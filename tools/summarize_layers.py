#!/usr/bin/env python3
"""Analyze the per-image layer data time series collected from CI builds.

Layer data lives in data/layers/<build-name>/<image>.jsonl. Each file is a time series
with one line per build run, written by tools/collect-layer-data.py. Each record
contains the layers observed at three pipeline stages: as-built (before any filtering),
post-normalize (after timestamp normalization) and post-exclude (after path exclusions
-- the content actually pushed to the registry).

Records come in two formats and both are read here. Version 1 inlines a full layer dump
per stage. Version 2 stores the fields that are identical across the three stages once
per layer and refers to command text by hash, resolved through the build's
commands.jsonl dictionary; it is roughly a fifth of the size. Version 2 records are
expanded into the version 1 shape on load, so the reports below do not care which they
came from.

The reports answer the questions the data was collected for:

  growth:  Are images getting bigger over time, and if so which layer is responsible?
           Uses the as-built stage, matching layers across runs by their CreatedBy
           command (digests change between builds, commands mostly do not).

  reuse:   Is the occystrap pipeline increasing layer reuse between builds? Uses the
           post-exclude stage, comparing pushed layer digests against previous runs:
           a digest seen before did not need to be re-uploaded.

  stages:  What does each pipeline stage buy us? Compares layer counts and sizes
           across the three stages for the most recent run.
"""

import argparse
import json
import os
import sys
from collections import defaultdict


STAGES = ['as-built', 'post-normalize', 'post-exclude']
GROWTH_STAGE = 'as-built'
REUSE_STAGE = 'post-exclude'

# The per-build command dictionary written by tools/collect-layer-data.py. It sits
# alongside the per-image series files but is not one of them.
COMMANDS_FILE = 'commands.jsonl'

# Layer digests are stored without their constant sha256: prefix from version 2
# onwards, and restored on load so that records either side of the format change
# compare equal.
DIGEST_PREFIX = 'sha256:'


def format_size(size_bytes):
    """Format bytes into human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024.0:
            return f'{size_bytes:.2f} {unit}'
        size_bytes /= 1024.0
    return f'{size_bytes:.2f} PB'


def run_key(record):
    """Chronological sort key for a record."""
    return (record.get('datestamp', ''), record.get('run_id', ''), record.get('run_attempt', ''))


def run_label(record):
    """Human-readable run identifier for a record."""
    return '%s run %s' % (record.get('datestamp', '?'), record.get('run_id', '?'))


def read_jsonl(path):
    """Read a JSONL file into a list of records, skipping corrupt lines."""
    records = []
    with open(path) as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print('WARNING: skipping corrupt line %d of %s: %s' % (lineno, path, e),
                      file=sys.stderr)
    return records


def load_commands(build_dir):
    """Load a build's command dictionary, returning a dict of hash to command text."""
    path = os.path.join(build_dir, COMMANDS_FILE)
    if not os.path.exists(path):
        return {}

    commands = {}
    for record in read_jsonl(path):
        key = record.get('cmd')
        if key:
            commands[key] = record.get('command', '')
    return commands


def normalize_record(record, commands):
    """Return a record in the version 1 shape, whatever version it was written in.

    The reports below all work in terms of per-stage lists of layers carrying
    CreatedBy, Id and Size. Version 2 records store the fields that are common to the
    three stages once per layer and refer to command text by hash, so they are
    expanded here and the reports stay version agnostic.
    """
    if record.get('version', 1) < 2:
        return record

    stages = {}
    for layer in record.get('layers', []):
        key = layer.get('cmd', '')
        # An unresolvable hash keeps its own identity rather than collapsing every
        # unknown layer into one, so the growth report still tracks them separately.
        command = commands.get(key, '<unknown command %s>' % key)

        for stage, observed in layer.get('stages', {}).items():
            digest = observed.get('id', '')
            entry = {
                'CreatedBy': command,
                'Id': DIGEST_PREFIX + digest if digest else '',
                'Size': observed.get('size', 0),
            }
            if 'comment' in layer:
                entry['Comment'] = layer['comment']
            if 'created' in layer:
                entry['Created'] = layer['created']
            if 'tags' in layer:
                entry['Tags'] = layer['tags']
            stages.setdefault(stage, []).append(entry)

    normalized = {k: v for k, v in record.items() if k not in ('layers', 'version')}
    normalized['stages'] = stages
    return normalized


def load_series(data_dir, build=None, image=None):
    """Load the layer data time series.

    Returns a dict of build name to a dict of image name to a chronologically sorted
    list of records.
    """
    series = {}

    if not os.path.isdir(data_dir):
        return series

    for build_name in sorted(os.listdir(data_dir)):
        build_dir = os.path.join(data_dir, build_name)
        if not os.path.isdir(build_dir):
            continue
        if build and build_name != build:
            continue

        commands = load_commands(build_dir)

        for filename in sorted(os.listdir(build_dir)):
            if not filename.endswith('.jsonl') or filename == COMMANDS_FILE:
                continue
            image_name = filename[:-len('.jsonl')]
            if image and image_name != image:
                continue

            path = os.path.join(build_dir, filename)
            records = [normalize_record(r, commands) for r in read_jsonl(path)]

            if records:
                records.sort(key=run_key)
                series.setdefault(build_name, {})[image_name] = records

    return series


def stage_layers(record, stage):
    """Return the layer list for a stage of a record, or an empty list."""
    return record.get('stages', {}).get(stage, [])


def total_size(layers):
    """Total size of a list of layer entries."""
    return sum(layer.get('Size', 0) for layer in layers)


def sizes_by_command(layers):
    """Aggregate layer sizes by their CreatedBy command.

    Digests change between builds even when the Dockerfile step does not, so the
    CreatedBy command is the stable identity for tracking a layer across runs.
    """
    sizes = defaultdict(int)
    for layer in layers:
        sizes[layer.get('CreatedBy', '')] += layer.get('Size', 0)
    return sizes


def truncate(command, width=70):
    command = command.replace('\n', ' ')
    if len(command) > width:
        return command[:width] + '...'
    return command


def print_layer_deltas(old_layers, new_layers, limit=None):
    """Print per-layer size changes between two runs, largest change first."""
    old_sizes = sizes_by_command(old_layers)
    new_sizes = sizes_by_command(new_layers)

    deltas = []
    for command in set(old_sizes) | set(new_sizes):
        delta = new_sizes.get(command, 0) - old_sizes.get(command, 0)
        if delta != 0:
            deltas.append((delta, command))
    # The command breaks ties, because the set union above iterates in an order that
    # varies between interpreter runs and two layers often change by the same amount.
    deltas.sort(key=lambda d: (-abs(d[0]), d[1]))

    if limit:
        deltas = deltas[:limit]

    for delta, command in deltas:
        marker = '+' if delta > 0 else '-'
        note = ''
        if command not in old_sizes:
            note = ' (new layer)'
        elif command not in new_sizes:
            note = ' (removed layer)'
        print('      %s%10s  %s%s' % (marker, format_size(abs(delta)), truncate(command), note))


def report_growth(series, stage, verbose=False):
    """Report image size over time, attributing growth to specific layers."""
    print('=' * 78)
    print('IMAGE GROWTH (stage: %s)' % stage)
    print('=' * 78)

    for build_name, images in series.items():
        print()
        print(build_name)
        print('-' * 78)
        print('%-30s %5s %12s %12s %12s' % ('Image', 'Runs', 'First', 'Latest', 'Delta'))

        rows = []
        for image_name, records in images.items():
            first_size = total_size(stage_layers(records[0], stage))
            latest_size = total_size(stage_layers(records[-1], stage))
            rows.append((latest_size - first_size, image_name, records, first_size, latest_size))
        rows.sort(key=lambda r: r[0], reverse=True)

        for delta, image_name, records, first_size, latest_size in rows:
            print('%-30s %5d %12s %12s %12s' % (
                image_name, len(records), format_size(first_size), format_size(latest_size),
                ('+' if delta >= 0 else '-') + format_size(abs(delta))))

        # Attribute growth to layers by comparing the two most recent runs of each
        # image that changed size
        for delta, image_name, records, first_size, latest_size in rows:
            if len(records) < 2:
                continue
            prev_layers = stage_layers(records[-2], stage)
            last_layers = stage_layers(records[-1], stage)
            run_delta = total_size(last_layers) - total_size(prev_layers)
            if run_delta == 0 and not verbose:
                continue

            print()
            print('    %s: %s%s between %s and %s' % (
                image_name, '+' if run_delta >= 0 else '-', format_size(abs(run_delta)),
                run_label(records[-2]), run_label(records[-1])))
            print_layer_deltas(prev_layers, last_layers, limit=None if verbose else 5)


def report_reuse(series, stage, verbose=False):
    """Report layer reuse across chronological runs of each build variant."""
    print('=' * 78)
    print('LAYER REUSE BETWEEN BUILDS (stage: %s)' % stage)
    print('=' * 78)

    for build_name, images in series.items():
        # Regroup records by run so we can walk runs chronologically with the union
        # of all images' layers in each run
        runs = defaultdict(dict)  # run key -> digest -> size
        run_records = {}
        for records in images.values():
            for record in records:
                key = run_key(record)
                run_records[key] = record
                for layer in stage_layers(record, stage):
                    digest = layer.get('Id')
                    if digest:
                        runs[key][digest] = layer.get('Size', 0)

        print()
        print(build_name)
        print('-' * 78)
        print('%-28s %7s %7s %7s %8s %14s' % ('Run', 'Layers', 'New', 'Reused', 'Reused%', 'Upload size'))

        seen = set()
        for key in sorted(runs):
            digests = runs[key]
            new = {d for d in digests if d not in seen}
            reused = set(digests) - new
            reused_pct = (len(reused) / len(digests) * 100) if digests else 0
            upload_size = sum(digests[d] for d in new)

            print('%-28s %7d %7d %7d %7.1f%% %14s' % (
                run_label(run_records[key]), len(digests), len(new), len(reused), reused_pct,
                format_size(upload_size)))
            seen |= set(digests)

        if verbose and len(runs) > 1:
            print()
            print('    A reused layer has a digest already pushed by an earlier run, so it')
            print('    did not need to be re-uploaded to the registry.')


def report_stages(series, verbose=False):
    """Report the effect of each pipeline stage on the most recent run."""
    print('=' * 78)
    print('PIPELINE STAGE COMPARISON (most recent run)')
    print('=' * 78)

    for build_name, images in series.items():
        # Unique layers per stage across all images in the latest run of each image
        stage_digests = {stage: {} for stage in STAGES}
        for records in images.values():
            record = records[-1]
            for stage in STAGES:
                for layer in stage_layers(record, stage):
                    digest = layer.get('Id')
                    if digest:
                        stage_digests[stage][digest] = layer.get('Size', 0)

        print()
        print(build_name)
        print('-' * 78)

        prev_total = None
        for stage in STAGES:
            digests = stage_digests[stage]
            stage_total = sum(digests.values())
            line = '%-16s %6d unique layers %14s' % (stage, len(digests), format_size(stage_total))
            if prev_total is not None:
                delta = stage_total - prev_total
                line += '   (%s%s vs previous stage)' % ('+' if delta >= 0 else '-', format_size(abs(delta)))
            print(line)
            prev_total = stage_total


def main():
    default_data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'layers')

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-d', '--data-dir', default=default_data_dir,
                        help='Layer data directory (default: %s)' % default_data_dir)
    parser.add_argument('-b', '--build', help='Only analyze one build variant, e.g. build-debian-trixie-master')
    parser.add_argument('-i', '--image', help='Only analyze one image, e.g. nova-api')
    parser.add_argument('-r', '--report', choices=['growth', 'reuse', 'stages', 'all'], default='all',
                        help='Which report to produce (default: all)')
    parser.add_argument('-s', '--stage', choices=STAGES,
                        help='Override the pipeline stage used by the growth and reuse reports')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show all per-layer deltas and extra explanation')
    args = parser.parse_args()

    series = load_series(args.data_dir, build=args.build, image=args.image)
    if not series:
        print('No layer data found in %s' % args.data_dir, file=sys.stderr)
        sys.exit(1)

    if args.report in ('growth', 'all'):
        report_growth(series, args.stage or GROWTH_STAGE, verbose=args.verbose)
        print()
    if args.report in ('reuse', 'all'):
        report_reuse(series, args.stage or REUSE_STAGE, verbose=args.verbose)
        print()
    if args.report in ('stages', 'all'):
        report_stages(series, verbose=args.verbose)


if __name__ == '__main__':
    main()
