#!/usr/bin/env python3
"""Find the next available patch number.

Checks both:
1. Existing patch files in _patches/
2. Open GitHub PRs (via gh CLI) for patch numbers in titles, bodies, and files

Usage:
    ./get-next-patch-number.py

Output:
    118
"""

import glob
import json
import re
import subprocess
import sys


def get_patch_numbers_from_filesystem():
    """Get all patch numbers from existing files in _patches/."""
    numbers = set()

    for patch_file in glob.glob('_patches/patch*.patch'):
        match = re.search(r'patch(\d+)', patch_file)
        if match:
            numbers.add(int(match.group(1)))

    return numbers


def get_patch_numbers_from_open_prs():
    """Get patch numbers mentioned in open GitHub PRs."""
    numbers = set()

    try:
        # Query open PRs using gh CLI
        result = subprocess.run(
            ['gh', 'pr', 'list', '--state', 'open', '--json',
             'title,body,files', '--limit', '100'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            print(f'Warning: gh pr list failed: {result.stderr}',
                  file=sys.stderr)
            return numbers

        prs = json.loads(result.stdout)

        for pr in prs:
            # Check title for patch numbers
            title = pr.get('title', '')
            for match in re.finditer(r'patch(\d+)', title, re.IGNORECASE):
                numbers.add(int(match.group(1)))

            # Check body for patch numbers
            body = pr.get('body', '') or ''
            for match in re.finditer(r'patch(\d+)', body, re.IGNORECASE):
                numbers.add(int(match.group(1)))

            # Check changed files for patch numbers
            for file_info in pr.get('files', []):
                path = file_info.get('path', '')
                match = re.search(r'patch(\d+)', path)
                if match:
                    numbers.add(int(match.group(1)))

    except subprocess.TimeoutExpired:
        print('Warning: gh pr list timed out', file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f'Warning: Failed to parse gh output: {e}', file=sys.stderr)
    except FileNotFoundError:
        print('Warning: gh CLI not found', file=sys.stderr)
    except Exception as e:
        print(f'Warning: Error querying GitHub PRs: {e}', file=sys.stderr)

    return numbers


def get_next_patch_number():
    """Get the next available patch number."""
    # Combine numbers from filesystem and open PRs
    all_numbers = get_patch_numbers_from_filesystem()
    all_numbers |= get_patch_numbers_from_open_prs()

    if not all_numbers:
        return 1

    return max(all_numbers) + 1


def main():
    next_number = get_next_patch_number()
    print(next_number)


if __name__ == '__main__':
    main()
