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
record to <data-dir>/<build-name>/<image>.jsonl. Each of those files is an append-only
time series, one line per build run, structured to answer:

  - Are images getting bigger over time, and if so which layer? Compare the "Size" of
    corresponding layers (matched by CreatedBy) in the as-built stage across lines.
  - Is occystrap increasing layer reuse between builds? Compare post-exclude layer
    digests (the content actually pushed to the registry) across lines: digests present
    in the previous line's record were reused, new digests were re-uploaded.

Usage:
    collect-layer-data.py --tarball /tmp/build-artifacts/build-debian-trixie-master/layers.tar.gz \\
        --build-name build-debian-trixie-master --data-dir data/layers \\
        --run-id 12345 --run-attempt 1 --datestamp 20260704-0700
"""

import argparse
import json
import os
import re
import sys
import tarfile
import tempfile


STAGES = ['as-built', 'post-normalize', 'post-exclude']


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tarball', required=True, help='Path to a layers.tar.gz build artifact')
    parser.add_argument('--build-name', required=True, help='Build variant name, e.g. build-debian-trixie-master')
    parser.add_argument('--data-dir', required=True, help='Root of the layer data time series, e.g. data/layers')
    parser.add_argument('--run-id', required=True, help='GitHub Actions run id')
    parser.add_argument('--run-attempt', required=True, help='GitHub Actions run attempt')
    parser.add_argument('--datestamp', required=True, help='Datestamp for this collection, e.g. 20260704-0700')
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as workdir:
        with tarfile.open(args.tarball) as tar:
            tar.extractall(workdir, filter='data')

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

    for repo in sorted(images):
        record = {
            'datestamp': args.datestamp,
            'run_id': args.run_id,
            'run_attempt': args.run_attempt,
            'name': images[repo]['name'],
            'stages': images[repo]['stages'],
        }

        missing = [s for s in STAGES if s not in record['stages']]
        if missing:
            print('WARNING: %s is missing stages: %s' % (repo, ', '.join(missing)), file=sys.stderr)

        outpath = os.path.join(outdir, '%s.jsonl' % filenames[repo])
        with open(outpath, 'a') as f:
            f.write(json.dumps(record, sort_keys=True) + '\n')
        print('Appended %s record to %s' % (repo, outpath), file=sys.stderr)

    print('%d' % len(images))


if __name__ == '__main__':
    main()
