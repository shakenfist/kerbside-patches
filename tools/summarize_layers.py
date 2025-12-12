#!/usr/bin/env python3
"""Summarize the total size of unique layers in a Docker image layers JSON file."""

import argparse
import json
import sys


def format_size(size_bytes):
    """Format bytes into human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024.0:
            return f'{size_bytes:.2f} {unit}'
        size_bytes /= 1024.0
    return f'{size_bytes:.2f} PB'


def summarize_layers(json_file, verbose=False):
    """Summarize unique layers from a JSON file."""
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
                short_id = layer_id.split(':')[1][:12] if ':' in layer_id else layer_id[:12]
                created_by = info['created_by'][:60] + '...' if len(info['created_by']) > 60 else info['created_by']
                print(f'  {short_id}: {format_size(info["size"]):>10}  {created_by}')


def main():
    parser = argparse.ArgumentParser(
        description='Summarize unique Docker image layers from a JSON file.'
    )
    parser.add_argument(
        'json_file',
        nargs='?',
        default='layers-before.json',
        help='Path to the JSON file (default: layers-before.json)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show details of largest layers'
    )
    args = parser.parse_args()

    try:
        summarize_layers(args.json_file, args.verbose)
    except FileNotFoundError:
        print(f'Error: File not found: {args.json_file}', file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f'Error: Invalid JSON: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
