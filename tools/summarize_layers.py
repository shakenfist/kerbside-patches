#!/usr/bin/env python3
"""Summarize the total size of unique layers in Docker image layers JSON files.

Supports multiple files with overlap analysis between them, including
chronological build analysis to track layer reuse over time.
"""

import argparse
import gzip
import json
import os
import re
import sys
from collections import defaultdict
from itertools import combinations


def format_size(size_bytes):
    """Format bytes into human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024.0:
            return f'{size_bytes:.2f} {unit}'
        size_bytes /= 1024.0
    return f'{size_bytes:.2f} PB'


def load_layers(json_file):
    """Load and extract unique layers from a JSON file.

    Supports both plain JSON and gzip-compressed JSON files.
    Returns a dict mapping layer_id to layer info.
    """
    if json_file.endswith('.gz'):
        with gzip.open(json_file, 'rt', encoding='utf-8') as f:
            data = json.load(f)
    else:
        with open(json_file, 'r') as f:
            data = json.load(f)

    unique_layers = {}

    for image in data:
        for layer in image.get('layers', []):
            layer_id = layer.get('Id')
            size = layer.get('Size', 0)
            if layer_id and layer_id not in unique_layers:
                unique_layers[layer_id] = {
                    'size': size,
                    'created_by': layer.get('CreatedBy', ''),
                    'tags': layer.get('Tags')
                }

    return unique_layers


def parse_build_info(filename):
    """Parse build info from filename.

    Expected format: layers-YYYYMMDD-HHMM-runNNNN-build-DISTRO-VERSION.json[.gz]

    Returns a tuple of (datetime_str, run_id, build_type) or None if not parseable.
    """
    basename = os.path.basename(filename)
    pattern = r'layers-(\d{8})-(\d{4})-(run\d+)-(.+)\.json(?:\.gz)?$'
    match = re.match(pattern, basename)
    if match:
        date_str = match.group(1)
        time_str = match.group(2)
        run_id = match.group(3)
        build_type = match.group(4)
        datetime_str = f'{date_str}-{time_str}'
        return (datetime_str, run_id, build_type)
    return None


def summarize_single_file(unique_layers, filename, verbose=False):
    """Print summary for a single file's layers."""
    total_size = sum(layer['size'] for layer in unique_layers.values())
    non_zero_layers = sum(1 for layer in unique_layers.values() if layer['size'] > 0)

    print(f'Total unique layers: {len(unique_layers)}')
    print(f'Layers with non-zero size: {non_zero_layers}')
    print(f'Total unique layer size: {format_size(total_size)} ({total_size:,} bytes)')

    if verbose:
        print('\nLargest layers:')
        sorted_layers = sorted(
            unique_layers.items(),
            key=lambda x: x[1]['size'],
            reverse=True
        )
        for layer_id, info in sorted_layers[:20]:
            if info['size'] > 0:
                short_id = (layer_id.split(':')[1][:12]
                            if ':' in layer_id else layer_id[:12])
                created_by = info['created_by']
                if len(created_by) > 60:
                    created_by = created_by[:60] + '...'
                print(f'  {short_id}: {format_size(info["size"]):>10}  {created_by}')


def print_overlap_report(file_layers):
    """Print a report on layer overlap between files.

    Args:
        file_layers: dict mapping filename to set of layer IDs
    """
    filenames = list(file_layers.keys())

    if len(filenames) < 2:
        return

    print('\n' + '=' * 70)
    print('LAYER OVERLAP REPORT')
    print('=' * 70)

    # Pairwise overlap
    print('\nPairwise overlap (shared layers between each pair of files):')
    print('-' * 70)

    for file1, file2 in combinations(filenames, 2):
        layers1 = file_layers[file1]['ids']
        layers2 = file_layers[file2]['ids']
        shared = layers1 & layers2
        shared_size = sum(
            file_layers[file1]['info'][lid]['size']
            for lid in shared
        )

        name1 = os.path.basename(file1)
        name2 = os.path.basename(file2)

        print(f'\n{name1} <-> {name2}:')
        print(f'  Shared layers: {len(shared)}')
        print(f'  Shared size: {format_size(shared_size)} ({shared_size:,} bytes)')
        print(f'  Only in {name1}: {len(layers1 - layers2)}')
        print(f'  Only in {name2}: {len(layers2 - layers1)}')

        if layers1 or layers2:
            union = layers1 | layers2
            overlap_pct = (len(shared) / len(union)) * 100 if union else 0
            print(f'  Overlap percentage: {overlap_pct:.1f}%')

    # Summary across all files
    if len(filenames) > 2:
        print('\n' + '-' * 70)
        print('Multi-file summary:')
        print('-' * 70)

        all_layers = set()
        for data in file_layers.values():
            all_layers |= data['ids']

        common_to_all = set.intersection(
            *[data['ids'] for data in file_layers.values()]
        )

        print(f'\nTotal unique layers across all files: {len(all_layers)}')
        print(f'Layers common to ALL files: {len(common_to_all)}')

        if common_to_all:
            # Use first file's info for size calculation
            first_file = filenames[0]
            common_size = sum(
                file_layers[first_file]['info'][lid]['size']
                for lid in common_to_all
                if lid in file_layers[first_file]['info']
            )
            print(f'Size of common layers: {format_size(common_size)}')


def enumerate_builds(data_dir):
    """Enumerate all builds in a data directory.

    Returns a dict mapping (datetime_str, run_id) to list of files for that build,
    sorted chronologically.
    """
    builds = defaultdict(list)

    for filename in os.listdir(data_dir):
        if not filename.startswith('layers-'):
            continue
        if not (filename.endswith('.json') or filename.endswith('.json.gz')):
            continue

        filepath = os.path.join(data_dir, filename)
        build_info = parse_build_info(filename)
        if build_info:
            datetime_str, run_id, build_type = build_info
            build_key = (datetime_str, run_id)
            builds[build_key].append({
                'path': filepath,
                'build_type': build_type
            })

    # Sort by datetime (keys are already sortable as YYYYMMDD-HHMM)
    sorted_builds = dict(sorted(builds.items(), key=lambda x: x[0]))
    return sorted_builds


def analyze_build_progression(data_dir, verbose=False):
    """Analyze layer reuse across builds in chronological order.

    For each build, reports how many layers are new vs. recycled from
    previous builds.
    """
    builds = enumerate_builds(data_dir)

    if not builds:
        print(f'No builds found in {data_dir}')
        return

    print('=' * 78)
    print('BUILD PROGRESSION ANALYSIS')
    print('=' * 78)
    print(f'\nFound {len(builds)} builds in chronological order:\n')

    # Track all layers seen across builds
    all_seen_layers = {}  # layer_id -> layer_info (including size)
    build_results = []

    for build_key, files in builds.items():
        datetime_str, run_id = build_key

        # Load all layers for this build
        build_layers = {}
        for file_info in files:
            layers = load_layers(file_info['path'])
            build_layers.update(layers)

        # Calculate new vs recycled layers
        new_layers = {}
        recycled_layers = {}

        for layer_id, layer_info in build_layers.items():
            if layer_id in all_seen_layers:
                recycled_layers[layer_id] = layer_info
            else:
                new_layers[layer_id] = layer_info

        # Calculate sizes
        new_size = sum(layer['size'] for layer in new_layers.values())
        recycled_size = sum(layer['size'] for layer in recycled_layers.values())
        total_size = new_size + recycled_size

        # Calculate percentages
        total_layers = len(build_layers)
        new_pct = (len(new_layers) / total_layers * 100) if total_layers else 0
        recycled_pct = (len(recycled_layers) / total_layers * 100) if total_layers else 0

        # Store results
        result = {
            'datetime': datetime_str,
            'run_id': run_id,
            'total_layers': total_layers,
            'new_layers': len(new_layers),
            'recycled_layers': len(recycled_layers),
            'new_size': new_size,
            'recycled_size': recycled_size,
            'total_size': total_size,
            'new_pct': new_pct,
            'recycled_pct': recycled_pct,
            'files': len(files)
        }
        build_results.append(result)

        # Update seen layers for next iteration
        all_seen_layers.update(build_layers)

    # Print results as a table
    print(f'{"Build DateTime":<20} {"Run ID":<18} {"Files":>5} {"Total":>6} '
          f'{"New":>6} {"Recyc":>6} {"New %":>7} {"New Size":>12}')
    print('-' * 78)

    for r in build_results:
        print(f'{r["datetime"]:<20} {r["run_id"]:<18} {r["files"]:>5} '
              f'{r["total_layers"]:>6} {r["new_layers"]:>6} '
              f'{r["recycled_layers"]:>6} {r["new_pct"]:>6.1f}% '
              f'{format_size(r["new_size"]):>12}')

    print('-' * 78)

    # Summary statistics
    total_unique = len(all_seen_layers)
    total_unique_size = sum(layer['size'] for layer in all_seen_layers.values())

    print(f'\nSummary:')
    print(f'  Total unique layers across all builds: {total_unique}')
    print(f'  Total unique layer size: {format_size(total_unique_size)}')

    if len(build_results) > 1:
        # Calculate average reuse after first build
        later_builds = build_results[1:]
        avg_new_pct = sum(r['new_pct'] for r in later_builds) / len(later_builds)
        avg_recycled_pct = sum(r['recycled_pct'] for r in later_builds) / len(later_builds)
        print(f'  Average new layers after first build: {avg_new_pct:.1f}%')
        print(f'  Average recycled layers after first build: {avg_recycled_pct:.1f}%')

    if verbose:
        print('\n' + '-' * 78)
        print('Per-build details:')
        print('-' * 78)
        for r in build_results:
            print(f'\n{r["datetime"]} ({r["run_id"]}):')
            print(f'  Files in build: {r["files"]}')
            print(f'  Total layers: {r["total_layers"]}')
            print(f'  New layers: {r["new_layers"]} ({r["new_pct"]:.1f}%)')
            print(f'  Recycled layers: {r["recycled_layers"]} ({r["recycled_pct"]:.1f}%)')
            print(f'  New layer size: {format_size(r["new_size"])}')
            print(f'  Recycled layer size: {format_size(r["recycled_size"])}')
            print(f'  Total layer size: {format_size(r["total_size"])}')


def main():
    parser = argparse.ArgumentParser(
        description='Summarize unique Docker image layers from JSON files.'
    )
    parser.add_argument(
        'json_files',
        nargs='*',
        help='Path(s) to JSON file(s) to analyze individually'
    )
    parser.add_argument(
        '-d', '--data-dir',
        help='Analyze all builds in a data directory chronologically'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show details of largest layers or per-build details'
    )
    args = parser.parse_args()

    # If data directory specified, run build progression analysis
    if args.data_dir:
        analyze_build_progression(args.data_dir, args.verbose)
        return

    # Otherwise, analyze individual files
    if not args.json_files:
        # Default to data directory if it exists
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(os.path.dirname(script_dir), 'data')
        if os.path.isdir(data_dir):
            print(f'No files specified. Use -d {data_dir} to analyze all builds.')
            print('Or specify individual files as arguments.')
            sys.exit(1)
        else:
            print('Error: No files specified and no data directory found.')
            sys.exit(1)

    file_layers = {}

    for json_file in args.json_files:
        try:
            unique_layers = load_layers(json_file)
            file_layers[json_file] = {
                'ids': set(unique_layers.keys()),
                'info': unique_layers
            }

            print('=' * 70)
            print(f'FILE: {json_file}')
            print('=' * 70)
            summarize_single_file(unique_layers, json_file, args.verbose)
            print()

        except FileNotFoundError:
            print(f'Error: File not found: {json_file}', file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f'Error: Invalid JSON in {json_file}: {e}', file=sys.stderr)
            sys.exit(1)

    # Print overlap report if multiple files
    if len(args.json_files) > 1:
        print_overlap_report(file_layers)


if __name__ == '__main__':
    main()
