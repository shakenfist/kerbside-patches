#!/usr/bin/env python3
"""Summarize the total size of unique layers in Docker image layers JSON files.

Supports multiple files with overlap analysis between them.
"""

import argparse
from itertools import combinations
import json
import os
import sys


def format_size(size_bytes):
    """Format bytes into human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024.0:
            return f'{size_bytes:.2f} {unit}'
        size_bytes /= 1024.0
    return f'{size_bytes:.2f} PB'


def load_layers(json_file):
    """Load and extract unique layers from a JSON file.

    Returns a dict mapping layer_id to layer info.
    """
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


def main():
    parser = argparse.ArgumentParser(
        description='Summarize unique Docker image layers from JSON files.'
    )
    parser.add_argument(
        'json_files',
        nargs='*',
        default=['layers-before.json'],
        help='Path(s) to JSON file(s) (default: layers-before.json)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show details of largest layers'
    )
    args = parser.parse_args()

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
