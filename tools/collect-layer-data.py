#!/usr/bin/env python3
"""Append per-image layer records from a build's layers.tar.gz to the data/ time series.

The container build pipeline collects layer metadata with occystrap inspect filters at
three stages: as-built (before any filtering), post-normalize (after timestamp
normalization) and post-exclude (after path exclusions). Depending on the build path the
JSONL files in the tarball are either combined per-stage files written by the occystrap
proxy (proxy-as-built.jsonl, ...) or per-image files written by sequential pushes
(kolla-nova-api-as-built.jsonl, ...). Either way, every JSONL line records the image it
describes in its "name" field.

This script merges the three stages into a single record per image and appends that
record to <data-dir>/<build-name>/<image>.jsonl. Each of those files is a time series,
one line per build run, structured to answer:

  - Are images getting bigger over time, and if so which layer? Compare the "sizes" of
    corresponding layers (matched by command) in the as-built stage across lines.
  - Is occystrap increasing layer reuse between builds? Compare post-exclude layer
    digests (the content actually pushed to the registry) across lines: digests present
    in the previous line's record were reused, new digests were re-uploaded.

Records are written in the version 2 format, which exists because the version 1 format
was overwhelmingly repetition. A version 1 record inlined a full "docker history" dump
for each of the three stages, and the three stages differ only in each layer's digest
and size: every layer's CreatedBy command was therefore stored three times per record,
and again in full on every subsequent run. Measured on the neutron-server series that
was 71% of each 94KB record, to express 46 distinct commands across 102 runs of a 9MB
file.

Version 2 removes both axes of that repetition:

  - The fields that are identical across the three stages (the command, comment,
    creation time and tags) are stored once per layer, with only the per-stage digest
    and size kept per stage.
  - Command text is replaced by a short hash referring to <build-name>/commands.jsonl,
    a dictionary that gains a line only when a command is seen for the first time.
    Commands are near-static between runs, so a run's marginal cost for them falls to
    a hash per layer.

Together those take a record from roughly 94KB to roughly 21KB. Version 1 records
remain readable by tools/summarize_layers.py and age out of the series naturally.

Series length is capped at --max-records, oldest first, so the data is bounded rather
than growing without limit. Trimming the oldest records is compatible with the union
merge driver in .gitattributes: every branch trims the same leading records because
each is cut from the same develop, so the trims agree and only the appends need
merging.

Usage:
    collect-layer-data.py --tarball /tmp/build-artifacts/build-debian-trixie-master/layers.tar.gz \\
        --build-name build-debian-trixie-master --data-dir data/layers \\
        --run-id 12345 --run-attempt 1 --datestamp 20260704-0700
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile


STAGES = ['as-built', 'post-normalize', 'post-exclude']

# The record format written by this script. Version 1 inlined a full layer dump per
# stage; version 2 hoists the fields that do not vary between stages and refers to
# command text by hash. Readers must handle both, because version 1 records stay in
# the series until they age out.
RECORD_VERSION = 2

# Name of the per-build command dictionary, alongside the per-image series files.
COMMANDS_FILE = 'commands.jsonl'

# Length of the command hash. Truncated sha256, so 48 bits: ample for the low
# thousands of distinct commands a build variant ever produces, and a collision would
# be caught by the dictionary consistency check in tools/verify-data-merge.py.
COMMAND_HASH_LENGTH = 12

# Layer digests are recorded without their constant "sha256:" prefix.
DIGEST_PREFIX = 'sha256:'

# How many runs of history to keep in each series file. At roughly 21KB per version 2
# record and 239 series files this bounds data/layers to a few hundred megabytes,
# where the uncapped series had reached 1.6GB and was growing by 20MB per run. Daily
# builds make this about three months of history.
DEFAULT_MAX_RECORDS = 90


def stage_from_filename(filename):
    """Determine the pipeline stage from a JSONL filename, or None if unrecognized."""
    base = os.path.basename(filename)
    for stage in STAGES:
        if base.endswith('-%s.jsonl' % stage):
            return stage
    return None


def parse_jsonl(path):
    """Yield parsed records from a JSONL file, skipping corrupt lines.

    The occystrap proxy processes images concurrently and appends to shared per-stage
    files, so a rare interleaved (and therefore unparseable) line is possible. Losing
    one image's record for one stage of one run is preferable to failing the run.
    """
    with open(path) as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print('WARNING: skipping corrupt line %d of %s: %s' % (lineno, path, e),
                      file=sys.stderr)


def sanitize(name):
    """Make an image name safe for use as a filename component."""
    return re.sub(r'[^a-zA-Z0-9._-]', '-', name)


def collect_records(layers_dir):
    """Read all stage JSONL files and merge them into one record per image.

    Returns a dict of repository name (without tag) to
    {'name': image:tag, 'stages': {stage: layers}}.
    """
    images = {}

    for filename in sorted(os.listdir(layers_dir)):
        if not filename.endswith('.jsonl'):
            continue

        stage = stage_from_filename(filename)
        if stage is None:
            print('WARNING: cannot determine stage for %s, skipping' % filename, file=sys.stderr)
            continue

        for record in parse_jsonl(os.path.join(layers_dir, filename)):
            name = record.get('name', '')
            if not name:
                print('WARNING: record without a name in %s, skipping' % filename, file=sys.stderr)
                continue

            repo = name.rsplit(':', 1)[0]
            image = images.setdefault(repo, {'name': name, 'stages': {}})
            if stage in image['stages']:
                print('WARNING: duplicate %s record for %s, keeping the last one' % (stage, repo),
                      file=sys.stderr)
            image['stages'][stage] = record.get('layers', [])

    return images


def image_filenames(images):
    """Map each repository to a filename, using the repository basename where unique.

    Registry namespaces differ between the proxy and sequential push paths (e.g.
    openstack/kolla-images/nova-api versus kolla/nova-api), so the basename is the
    stable identity. If two repositories share a basename, both fall back to their
    full sanitized path.
    """
    basenames = {}
    for repo in images:
        basenames.setdefault(os.path.basename(repo), []).append(repo)

    filenames = {}
    for basename, repos in basenames.items():
        if len(repos) == 1:
            filenames[repos[0]] = sanitize(basename)
        else:
            for repo in repos:
                filenames[repo] = sanitize(repo)
    return filenames


def command_hash(command):
    """Return the dictionary key for a layer's CreatedBy command."""
    return hashlib.sha256(command.encode('utf-8')).hexdigest()[:COMMAND_HASH_LENGTH]


def load_commands(path):
    """Load the command dictionary.

    Returns a dict of hash to command text, and the entries as stored, in file order,
    so that they can be rewritten without losing the run that first recorded each.
    """
    commands = {}
    entries = []
    if not os.path.exists(path):
        return commands, entries

    for record in parse_jsonl(path):
        key = record.get('cmd')
        if key:
            commands[key] = record.get('command', '')
            entries.append(record)
    return commands, entries


def write_commands(path, entries, new_commands, run_id, run_attempt):
    """Write the dictionary: existing entries deduplicated, then newly seen ones.

    Each new entry carries the run that first observed the command so that
    tools/verify-data-merge.py can confirm the lines a data PR adds belong to that
    PR's run, the same check it applies to the series files.

    Existing entries are rewritten rather than simply appended to because the union
    merge driver duplicates them: two data PRs that both create the dictionary from
    scratch each contribute a full copy, and the merge keeps both. Duplicates are
    harmless to read, since every copy of a hash carries the same command, but they
    would otherwise accumulate on each heal. Rewriting in first-seen order keeps the
    diff to the removal of the duplicated lines.
    """
    if not new_commands and len(entries) == len({entry['cmd'] for entry in entries}):
        return

    seen = set()
    with open(path, 'w') as f:
        for entry in entries:
            if entry['cmd'] in seen:
                continue
            seen.add(entry['cmd'])
            f.write(json.dumps(entry, sort_keys=True) + '\n')
        for key in sorted(new_commands):
            f.write(json.dumps({'cmd': key, 'command': new_commands[key],
                                'run_id': run_id, 'run_attempt': run_attempt},
                               sort_keys=True) + '\n')


def short_digest(digest):
    """Strip the constant sha256: prefix from a layer digest."""
    if digest.startswith(DIGEST_PREFIX):
        return digest[len(DIGEST_PREFIX):]
    return digest


def stages_are_aligned(stages):
    """Whether the stages agree layer for layer on everything but digest and size.

    Hoisting the shared fields out of the per-stage lists is only correct if the
    lists describe the same layers in the same order. That has held for every record
    measured, but a future filter that drops a layer would break it, so it is checked
    rather than assumed.
    """
    present = [stages[stage] for stage in STAGES if stage in stages]
    if len(present) < 2:
        return True

    first = present[0]
    if any(len(layers) != len(first) for layers in present):
        return False

    for layers in present[1:]:
        for mine, theirs in zip(first, layers):
            for field in ('CreatedBy', 'Comment', 'Created', 'Tags'):
                if mine.get(field) != theirs.get(field):
                    return False
    return True


def build_layers(stages, commands):
    """Build the version 2 layer list, interning command text into commands.

    Returns the layer list. Fields that do not vary between stages are stored once
    per layer; only the digest and size are kept per stage.
    """
    template = None
    for stage in STAGES:
        if stage in stages:
            template = stages[stage]
            break

    layers = []
    for index, layer in enumerate(template):
        command = layer.get('CreatedBy', '')
        key = command_hash(command)
        commands.setdefault(key, command)

        entry = {'cmd': key, 'stages': {}}
        if layer.get('Comment'):
            entry['comment'] = layer['Comment']
        if layer.get('Created') is not None:
            entry['created'] = layer['Created']
        if layer.get('Tags'):
            entry['tags'] = layer['Tags']

        for stage in STAGES:
            if stage not in stages:
                continue
            observed = stages[stage][index]
            entry['stages'][stage] = {'id': short_digest(observed.get('Id', '')),
                                      'size': observed.get('Size', 0)}
        layers.append(entry)

    return layers


def record_key(record):
    """Chronological sort key for a record, matching tools/summarize_layers.py."""
    return (record.get('datestamp', ''), record.get('run_id', ''), record.get('run_attempt', ''))


def append_and_trim(path, record, max_records):
    """Append a record to a series file and drop the oldest records past the cap.

    Kept records stay in their existing file order so that a trim shows up as the
    removal of a run of leading lines rather than as a reordering of the whole file.
    Every branch trims from the same develop, so concurrent data PRs agree on what to
    drop and the union merge driver only has to merge their appends.
    """
    lines = []
    if os.path.exists(path):
        with open(path) as f:
            lines = [line for line in (line.strip() for line in f) if line]

    lines.append(json.dumps(record, sort_keys=True))

    surplus = len(lines) - max_records
    if surplus > 0:
        keys = []
        for index, line in enumerate(lines):
            try:
                keys.append((record_key(json.loads(line)), index))
            except json.JSONDecodeError:
                # An unparseable line has no age, so drop it first.
                keys.append((('', '', ''), index))
        keys.sort()
        dropped = {index for _, index in keys[:surplus]}
        lines = [line for index, line in enumerate(lines) if index not in dropped]

    with open(path, 'w') as f:
        for line in lines:
            f.write(line + '\n')

    return max(surplus, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tarball', required=True, help='Path to a layers.tar.gz build artifact')
    parser.add_argument('--build-name', required=True, help='Build variant name, e.g. build-debian-trixie-master')
    parser.add_argument('--data-dir', required=True, help='Root of the layer data time series, e.g. data/layers')
    parser.add_argument('--run-id', required=True, help='GitHub Actions run id')
    parser.add_argument('--run-attempt', required=True, help='GitHub Actions run attempt')
    parser.add_argument('--datestamp', required=True, help='Datestamp for this collection, e.g. 20260704-0700')
    parser.add_argument('--max-records', type=int, default=DEFAULT_MAX_RECORDS,
                        help='Runs of history to keep per series file (default: %d)' % DEFAULT_MAX_RECORDS)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as workdir:
        with tarfile.open(args.tarball) as tar:
            # The filter argument requires Python 3.12 (or a 3.11.4+ security backport),
            # but some CI runners are older than that.
            if hasattr(tarfile, 'data_filter'):
                tar.extractall(workdir, filter='data')
            else:
                tar.extractall(workdir)

        layers_dir = os.path.join(workdir, 'layers')
        if not os.path.isdir(layers_dir):
            print('WARNING: no layers/ directory in %s, nothing to collect' % args.tarball,
                  file=sys.stderr)
            print('0')
            return

        images = collect_records(layers_dir)

    filenames = image_filenames(images)
    outdir = os.path.join(args.data_dir, args.build_name)
    os.makedirs(outdir, exist_ok=True)

    commands_path = os.path.join(outdir, COMMANDS_FILE)
    commands, command_entries = load_commands(commands_path)
    known_commands = set(commands)

    trimmed = 0
    for repo in sorted(images):
        stages = images[repo]['stages']

        missing = [s for s in STAGES if s not in stages]
        if missing:
            print('WARNING: %s is missing stages: %s' % (repo, ', '.join(missing)), file=sys.stderr)

        record = {
            'datestamp': args.datestamp,
            'run_id': args.run_id,
            'run_attempt': args.run_attempt,
            'name': images[repo]['name'],
        }

        if stages and stages_are_aligned(stages):
            record['version'] = RECORD_VERSION
            record['layers'] = build_layers(stages, commands)
        else:
            # The stages describe different layers, so the shared fields cannot be
            # hoisted. Fall back to the version 1 shape rather than lose anything;
            # tools/summarize_layers.py reads both.
            print('WARNING: %s has stages that do not align, writing a version 1 record'
                  % repo, file=sys.stderr)
            record['stages'] = stages

        outpath = os.path.join(outdir, '%s.jsonl' % filenames[repo])
        trimmed += append_and_trim(outpath, record, args.max_records)
        print('Appended %s record to %s' % (repo, outpath), file=sys.stderr)

    new_commands = {k: v for k, v in commands.items() if k not in known_commands}
    write_commands(commands_path, command_entries, new_commands, args.run_id, args.run_attempt)
    print('Added %d new commands to %s (%d known)'
          % (len(new_commands), commands_path, len(commands)), file=sys.stderr)
    if trimmed:
        print('Trimmed %d records past the %d record cap'
              % (trimmed, args.max_records), file=sys.stderr)

    print('%d' % len(images))


if __name__ == '__main__':
    main()
