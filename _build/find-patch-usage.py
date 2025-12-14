#!/usr/bin/env python3
"""Find all ORDER files that reference a given patch.

Usage:
    ./find-patch-usage.py <patch-filename>
    ./find-patch-usage.py _patches/patch008-use-routable-ip-for-spice-consoles.patch

Output (JSON):
    {
        "patch": "_patches/patch008-use-routable-ip-for-spice-consoles.patch",
        "used_by": ["kolla-ansible", "kolla-ansible-2025.1", ...]
    }
"""

import glob
import json
import os
import sys


def find_patch_usage(patch_path):
    """Find all projects that use a given patch in their ORDER file."""
    # Normalize the patch path to just the filename for matching
    patch_filename = os.path.basename(patch_path)

    # Also handle the ../_patches/ prefix used in ORDER files
    patch_ref_patterns = [
        patch_filename,
        f'../_patches/{patch_filename}',
        f'_patches/{patch_filename}',
    ]

    used_by = []

    # Find all ORDER files
    for order_file in glob.glob('*/ORDER'):
        project = os.path.dirname(order_file)

        with open(order_file, 'r') as f:
            content = f.read()

        # Check if any of the patch reference patterns appear in the ORDER file
        for pattern in patch_ref_patterns:
            if pattern in content:
                used_by.append(project)
                break

    return {
        'patch': patch_path,
        'used_by': sorted(used_by)
    }


def main():
    if len(sys.argv) != 2:
        print('Usage: find-patch-usage.py <patch-filename>', file=sys.stderr)
        sys.exit(1)

    patch_path = sys.argv[1]
    result = find_patch_usage(patch_path)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
