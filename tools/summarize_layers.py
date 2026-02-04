#!/usr/bin/env python3
"""Summarize the total size of unique layers in Docker image layer data.

Layer data is stored as tarballs containing per-image JSONL files,
organized by pipeline stage (as-built, post-normalize, post-exclude).

Supports chronological build progression analysis and per-stage
comparison to measure the effect of image optimization filters.
"""

import argparse
import json
import os
import re
import sys
import tarfile as tarfile_module
from collections import defaultdict
from itertools import combinations


KNOWN_STAGES = ['as-built', 'post-normalize', 'post-exclude']
DEFAULT_STAGE = 'post-exclude'


def format_size(size_bytes):
    """Format bytes into human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024.0:
            return f'{size_bytes:.2f} {unit}'
        size_bytes /= 1024.0
    return f'{size_bytes:.2f} PB'


def _extract_layers_from_images(images):
    """Extract unique layers from a list of image dicts.

    Returns a dict mapping layer_id to layer info.
    """
    unique_layers = {}
    for image in images:
        for layer in image.get('layers', []):
            layer_id = layer.get('Id')
            size = layer.get('Size', 0)
            if layer_id and layer_id not in unique_layers:
                unique_layers[layer_id] = {
                    'size': size,
                    'created_by': layer.get(
                        'CreatedBy', ''),
                    'tags': layer.get('Tags')
                }
    return unique_layers


def load_layers(tarball_path, stage=None):
    """Load unique layers from a tarball of JSONL files.

    The tarball contains per-image files like:
        layers/kolla-nova-api-as-built.jsonl
        layers/kolla-nova-api-post-normalize.jsonl
        layers/kolla-nova-api-post-exclude.jsonl

    Args:
        tarball_path: Path to the .tar.gz file.
        stage: Filter to files matching this stage.
            If None, loads all JSONL files.

    Returns:
        Dict mapping layer_id to layer info.
    """
    images = []
    with tarfile_module.open(tarball_path, 'r:gz') as tar:
        for member in tar.getmembers():
            if not member.name.endswith('.jsonl'):
                continue
            if stage and not member.name.endswith(
                    f'-{stage}.jsonl'):
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            for line in f:
                line = line.decode('utf-8').strip()
                if line:
                    images.append(json.loads(line))
    return _extract_layers_from_images(images)


def list_tarball_stages(tarball_path):
    """List the pipeline stages present in a tarball.

    Returns a list of stage name strings, ordered by
    pipeline position.
    """
    stages = set()
    with tarfile_module.open(tarball_path, 'r:gz') as tar:
        for member in tar.getmembers():
            if not member.name.endswith('.jsonl'):
                continue
            for known in KNOWN_STAGES:
                if member.name.endswith(
                        f'-{known}.jsonl'):
                    stages.add(known)
                    break
    return sorted(stages, key=lambda s: (
        KNOWN_STAGES.index(s)
        if s in KNOWN_STAGES
        else len(KNOWN_STAGES)
    ))


def parse_build_info(filename, prefix='layers'):
    """Parse build info from filename.

    Expected format:
    {prefix}-YYYYMMDD-HHMM-runNNNN-build-TYPE.tar.gz

    Returns a tuple of (datetime_str, run_id, build_type)
    or None if not parseable.
    """
    basename = os.path.basename(filename)
    pattern = (
        re.escape(prefix)
        + r'-(\d{8})-(\d{4})-(run\d+)-(.+)\.tar\.gz$'
    )
    match = re.match(pattern, basename)
    if match:
        date_str = match.group(1)
        time_str = match.group(2)
        run_id = match.group(3)
        build_type = match.group(4)
        datetime_str = f'{date_str}-{time_str}'
        return (datetime_str, run_id, build_type)
    return None


def summarize_single_file(unique_layers, filename,
                          verbose=False):
    """Print summary for a single file's layers."""
    total_size = sum(
        layer['size'] for layer in unique_layers.values())
    non_zero_layers = sum(
        1 for layer in unique_layers.values()
        if layer['size'] > 0)

    print(f'Total unique layers: {len(unique_layers)}')
    print(f'Layers with non-zero size: {non_zero_layers}')
    print(
        f'Total unique layer size: '
        f'{format_size(total_size)} '
        f'({total_size:,} bytes)')

    if verbose:
        print('\nLargest layers:')
        sorted_layers = sorted(
            unique_layers.items(),
            key=lambda x: x[1]['size'],
            reverse=True
        )
        for layer_id, info in sorted_layers[:20]:
            if info['size'] > 0:
                short_id = (
                    layer_id.split(':')[1][:12]
                    if ':' in layer_id
                    else layer_id[:12])
                created_by = info['created_by']
                if len(created_by) > 60:
                    created_by = created_by[:60] + '...'
                print(
                    f'  {short_id}: '
                    f'{format_size(info["size"]):>10}'
                    f'  {created_by}')


def summarize_tarball_stages(tarball_path, verbose=False):
    """Print per-stage summary for a tarball.

    Shows how each pipeline stage affects layer count and
    total size, including deltas between consecutive stages.
    """
    stages = list_tarball_stages(tarball_path)
    if not stages:
        print('No stages found in tarball.')
        return

    print(f'Pipeline stages: {", ".join(stages)}')
    print()

    prev_layers = None
    for stage in stages:
        layers = load_layers(tarball_path, stage)
        total_size = sum(
            l['size'] for l in layers.values())
        non_zero = sum(
            1 for l in layers.values() if l['size'] > 0)

        print(f'  {stage}:')
        print(f'    Unique layers: {len(layers)}')
        print(f'    Non-zero size: {non_zero}')
        print(
            f'    Total size: '
            f'{format_size(total_size)}')

        if prev_layers is not None:
            prev_size = sum(
                l['size'] for l in prev_layers.values())
            size_diff = total_size - prev_size
            shared = (
                set(layers.keys())
                & set(prev_layers.keys()))
            changed = (
                set(layers.keys())
                - set(prev_layers.keys()))
            print(
                f'    vs previous: '
                f'{len(shared)} shared, '
                f'{len(changed)} changed, '
                f'size delta '
                f'{format_size(size_diff)}')

        prev_layers = layers
        print()


def print_overlap_report(file_layers):
    """Print a report on layer overlap between files."""
    filenames = list(file_layers.keys())

    if len(filenames) < 2:
        return

    print('\n' + '=' * 70)
    print('LAYER OVERLAP REPORT')
    print('=' * 70)

    print(
        '\nPairwise overlap '
        '(shared layers between each pair):')
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
        print(
            f'  Shared size: '
            f'{format_size(shared_size)} '
            f'({shared_size:,} bytes)')
        print(
            f'  Only in {name1}: '
            f'{len(layers1 - layers2)}')
        print(
            f'  Only in {name2}: '
            f'{len(layers2 - layers1)}')

        if layers1 or layers2:
            union = layers1 | layers2
            overlap_pct = (
                (len(shared) / len(union)) * 100
                if union else 0)
            print(
                f'  Overlap percentage: '
                f'{overlap_pct:.1f}%')

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

        print(
            f'\nTotal unique layers across all files: '
            f'{len(all_layers)}')
        print(
            f'Layers common to ALL files: '
            f'{len(common_to_all)}')

        if common_to_all:
            first_file = filenames[0]
            common_size = sum(
                file_layers[first_file]['info']
                [lid]['size']
                for lid in common_to_all
                if lid in file_layers[first_file]['info']
            )
            print(
                f'Size of common layers: '
                f'{format_size(common_size)}')


def enumerate_builds(data_dir, prefix='layers'):
    """Enumerate all builds in a data directory.

    Returns a dict mapping (datetime_str, run_id) to list of
    files for that build, sorted chronologically.
    """
    builds = defaultdict(list)
    filter_prefix = prefix + '-'

    for filename in os.listdir(data_dir):
        if not filename.startswith(filter_prefix):
            continue
        if not filename.endswith('.tar.gz'):
            continue

        filepath = os.path.join(data_dir, filename)
        build_info = parse_build_info(filename, prefix)
        if build_info:
            datetime_str, run_id, build_type = build_info
            build_key = (datetime_str, run_id)
            builds[build_key].append({
                'path': filepath,
                'build_type': build_type
            })

    sorted_builds = dict(
        sorted(builds.items(), key=lambda x: x[0]))
    return sorted_builds


def analyze_build_progression(data_dir, prefix='layers',
                              stage=None, verbose=False):
    """Analyze layer reuse across builds chronologically.

    For each build, reports how many layers are new vs.
    recycled from previous builds.
    """
    if stage is None:
        stage = DEFAULT_STAGE

    builds = enumerate_builds(data_dir, prefix)

    if not builds:
        print(f'No builds found in {data_dir}')
        return

    print(f'Pipeline stage: {stage}')
    print()

    print('=' * 78)
    print('BUILD PROGRESSION ANALYSIS')
    print('=' * 78)
    print(
        f'\nFound {len(builds)} builds '
        f'in chronological order:\n')

    all_seen_layers = {}
    build_results = []

    for build_key, files in builds.items():
        datetime_str, run_id = build_key

        build_layers = {}
        for file_info in files:
            layers = load_layers(
                file_info['path'], stage)
            build_layers.update(layers)

        new_layers = {}
        recycled_layers = {}

        for layer_id, layer_info in build_layers.items():
            if layer_id in all_seen_layers:
                recycled_layers[layer_id] = layer_info
            else:
                new_layers[layer_id] = layer_info

        new_size = sum(
            layer['size']
            for layer in new_layers.values())
        recycled_size = sum(
            layer['size']
            for layer in recycled_layers.values())
        total_size = new_size + recycled_size

        total_layers = len(build_layers)
        new_pct = (
            (len(new_layers) / total_layers * 100)
            if total_layers else 0)
        recycled_pct = (
            (len(recycled_layers) / total_layers * 100)
            if total_layers else 0)

        result = {
            'datetime': datetime_str,
            'run_id': run_id,
            'total_layers': total_layers,
            'new_layers': len(new_layers),
            'recycled_layers': len(recycled_layers),
            'recycled_layers_info': recycled_layers,
            'new_size': new_size,
            'recycled_size': recycled_size,
            'total_size': total_size,
            'new_pct': new_pct,
            'recycled_pct': recycled_pct,
            'files': len(files)
        }
        build_results.append(result)

        all_seen_layers.update(build_layers)

    print(
        f'{"Build DateTime":<20} {"Run ID":<18} '
        f'{"Files":>5} {"Total":>6} '
        f'{"New":>6} {"Recyc":>6} '
        f'{"New %":>7} {"New Size":>12}')
    print('-' * 78)

    for r in build_results:
        print(
            f'{r["datetime"]:<20} {r["run_id"]:<18} '
            f'{r["files"]:>5} '
            f'{r["total_layers"]:>6} '
            f'{r["new_layers"]:>6} '
            f'{r["recycled_layers"]:>6} '
            f'{r["new_pct"]:>6.1f}% '
            f'{format_size(r["new_size"]):>12}')

    print('-' * 78)

    total_unique = len(all_seen_layers)
    total_unique_size = sum(
        layer['size']
        for layer in all_seen_layers.values())

    print(f'\nSummary:')
    print(
        f'  Total unique layers across all builds: '
        f'{total_unique}')
    print(
        f'  Total unique layer size: '
        f'{format_size(total_unique_size)}')

    if len(build_results) > 1:
        later_builds = build_results[1:]
        avg_new_pct = sum(
            r['new_pct']
            for r in later_builds) / len(later_builds)
        avg_recycled_pct = sum(
            r['recycled_pct']
            for r in later_builds) / len(later_builds)
        print(
            f'  Average new layers after first build: '
            f'{avg_new_pct:.1f}%')
        print(
            f'  Average recycled layers after first '
            f'build: {avg_recycled_pct:.1f}%')

    if verbose:
        print('\n' + '-' * 78)
        print('Per-build details:')
        print('-' * 78)
        for r in build_results:
            print(
                f'\n{r["datetime"]} '
                f'({r["run_id"]}):')
            print(
                f'  Files in build: {r["files"]}')
            print(
                f'  Total layers: '
                f'{r["total_layers"]}')
            print(
                f'  New layers: {r["new_layers"]} '
                f'({r["new_pct"]:.1f}%)')
            print(
                f'  Recycled layers: '
                f'{r["recycled_layers"]} '
                f'({r["recycled_pct"]:.1f}%)')
            print(
                f'  New layer size: '
                f'{format_size(r["new_size"])}')
            print(
                f'  Recycled layer size: '
                f'{format_size(r["recycled_size"])}')
            print(
                f'  Total layer size: '
                f'{format_size(r["total_size"])}')

            if r['recycled_layers_info']:
                print('  Recycled layer commands:')
                sorted_recycled = sorted(
                    r['recycled_layers_info'].items(),
                    key=lambda x: x[1]['size'],
                    reverse=True
                )
                for layer_id, info in sorted_recycled:
                    short_id = (
                        layer_id.split(':')[1][:12]
                        if ':' in layer_id
                        else layer_id[:12])
                    created_by = info['created_by']
                    if len(created_by) > 60:
                        created_by = (
                            created_by[:60] + '...')
                    print(
                        f'    {short_id}: '
                        f'{format_size(info["size"]):>10}'
                        f'  {created_by}')


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Summarize unique Docker image layers '
            'from layer data tarballs.')
    )
    parser.add_argument(
        'files',
        nargs='*',
        help='Path(s) to layer data .tar.gz files'
    )
    parser.add_argument(
        '-d', '--data-dir',
        help=(
            'Analyze all builds in a data directory '
            'chronologically')
    )
    parser.add_argument(
        '-p', '--prefix',
        default='layers',
        help=(
            'Filename prefix to filter on '
            '(default: layers)')
    )
    parser.add_argument(
        '-s', '--stage',
        default=None,
        help=(
            'Pipeline stage to analyze '
            f'({", ".join(KNOWN_STAGES)}). '
            f'Default for --data-dir: {DEFAULT_STAGE}')
    )
    parser.add_argument(
        '--compare-stages',
        action='store_true',
        help=(
            'Compare pipeline stages within each '
            'tarball')
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help=(
            'Show largest layers, per-build details, '
            'and recycled layer commands')
    )
    args = parser.parse_args()

    if args.data_dir:
        analyze_build_progression(
            args.data_dir, args.prefix,
            stage=args.stage, verbose=args.verbose
        )
        return

    if not args.files:
        script_dir = os.path.dirname(
            os.path.abspath(__file__))
        data_dir = os.path.join(
            os.path.dirname(script_dir), 'data')
        if os.path.isdir(data_dir):
            print(
                f'No files specified. '
                f'Use -d {data_dir} to analyze '
                f'all builds.')
            print(
                'Or specify individual .tar.gz '
                'files as arguments.')
            sys.exit(1)
        else:
            print(
                'Error: No files specified and '
                'no data directory found.')
            sys.exit(1)

    file_layers = {}

    for filepath in args.files:
        try:
            if args.compare_stages:
                print('=' * 70)
                print(f'FILE: {filepath}')
                print('=' * 70)
                summarize_tarball_stages(
                    filepath, args.verbose)
                print()
                continue

            unique_layers = load_layers(
                filepath, args.stage)
            file_layers[filepath] = {
                'ids': set(unique_layers.keys()),
                'info': unique_layers
            }

            print('=' * 70)
            print(f'FILE: {filepath}')
            if args.stage:
                print(f'STAGE: {args.stage}')
            print('=' * 70)
            summarize_single_file(
                unique_layers, filepath,
                args.verbose)
            print()

        except FileNotFoundError:
            print(
                f'Error: File not found: {filepath}',
                file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(
                f'Error: Invalid JSON in '
                f'{filepath}: {e}',
                file=sys.stderr)
            sys.exit(1)

    if len(file_layers) > 1:
        print_overlap_report(file_layers)


if __name__ == '__main__':
    main()
