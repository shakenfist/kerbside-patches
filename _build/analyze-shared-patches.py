#!/usr/bin/env python3
"""Analyze failing patches to determine fix strategy for shared patches.

Takes patch test results JSON as input and outputs analysis including:
- Which other projects use each failing patch
- Recommended fix strategy (modify_in_place vs create_copy)
- Suggested new patch names for release-specific copies
- Next available patch number

A patch shared between series that track the same upstream branch (the
main `kolla-ansible/` series and the `kolla-ansible-deployer/` or
`kolla-ansible-kerbside/` series, say, all of which have
`source_branch: master` in their config.yaml) fails identically in every
one of them, so the fix is one in-place edit and never a copy. A copy is
only right when the other users track a different upstream branch, which
is what makes the same patch need different content.

Usage:
    ./analyze-shared-patches.py <patch-test-results.json>

Output (JSON):
    {
        "failures": [...],
        "release_mappings": {...},
        "next_patch_number": 118
    }
"""

import glob
import json
import os
import re
import subprocess
import sys

import yaml


def load_release_mappings():
    """Load release name mappings from YAML file."""
    yaml_path = os.path.join(os.path.dirname(__file__), 'release-names.yaml')

    try:
        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f)
            return data.get('releases', {})
    except Exception as e:
        print(f'Warning: Could not load release-names.yaml: {e}',
              file=sys.stderr)
        # Return default mappings
        return {
            '2024.1': 'caracal',
            '2024.2': 'dalmatian',
            '2025.1': 'epoxy',
            '2025.2': 'flamingo',
            '2026.1': 'gazpacho',
            'master': 'master',
        }


def find_patch_usage(patch_path):
    """Find all projects that use a given patch in their ORDER file."""
    patch_filename = os.path.basename(patch_path)

    patch_ref_patterns = [
        patch_filename,
        f'../_patches/{patch_filename}',
        f'_patches/{patch_filename}',
    ]

    used_by = []

    for order_file in glob.glob('*/ORDER'):
        project = os.path.dirname(order_file)

        with open(order_file, 'r') as f:
            content = f.read()

        for pattern in patch_ref_patterns:
            if pattern in content:
                used_by.append(project)
                break

    return sorted(used_by)


def load_series_source(project):
    """Return what upstream a series directory tracks, as (repo, source_branch).

    Read from the series' config.yaml. Every series directory has one; a
    directory without one (or without the key) falls back to the branch
    implied by its name, so that `kolla-ansible-2025.1` and `kolla-ansible`
    still count as different sources and `kolla-ansible` and
    `kolla-ansible-deployer` do not. The repo cannot be guessed from a
    name and stays None in that case.
    """
    repo = None
    source_branch = None

    try:
        with open(os.path.join(project, 'config.yaml'), 'r') as f:
            config = yaml.safe_load(f) or {}
        repo = config.get('repo')
        source_branch = config.get('source_branch')
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f'Warning: Could not read {project}/config.yaml: {e}',
              file=sys.stderr)

    if not source_branch:
        release = extract_release_from_project(project)
        source_branch = 'master' if release == 'master' else f'stable/{release}'

    return (repo, source_branch)


def get_next_patch_number():
    """Get the next available patch number."""
    numbers = set()

    # From filesystem
    for patch_file in glob.glob('_patches/patch*.patch'):
        match = re.search(r'patch(\d+)', patch_file)
        if match:
            numbers.add(int(match.group(1)))

    # From open PRs
    try:
        result = subprocess.run(
            ['gh', 'pr', 'list', '--state', 'open', '--json',
             'title,body,files', '--limit', '100'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            prs = json.loads(result.stdout)
            for pr in prs:
                for text in [pr.get('title', ''), pr.get('body', '') or '']:
                    for match in re.finditer(r'patch(\d+)', text, re.I):
                        numbers.add(int(match.group(1)))
                for file_info in pr.get('files', []):
                    match = re.search(r'patch(\d+)', file_info.get('path', ''))
                    if match:
                        numbers.add(int(match.group(1)))
    except Exception:
        pass

    return max(numbers) + 1 if numbers else 1


def extract_release_from_project(project_name):
    """Extract release version from project name.

    Examples:
        kolla-ansible-2025.1 -> 2025.1
        kolla-ansible -> master
        nova-2025.1 -> 2025.1
    """
    match = re.search(r'(\d{4}\.\d)', project_name)
    if match:
        return match.group(1)
    return 'master'


def extract_base_project(project_name):
    """Extract base project name without release version.

    Examples:
        kolla-ansible-2025.1 -> kolla-ansible
        kolla-ansible -> kolla-ansible
        nova-2025.1 -> nova
    """
    return re.sub(r'-\d{4}\.\d$', '', project_name)


def generate_patch_name(original_patch, project, release_mappings, patch_num):
    """Generate a release-specific patch name.

    Example:
        patch008-use-routable-ip.patch + kolla-ansible-2025.2
        -> patch118-kolla-ansible-flamingo-use-routable-ip.patch
    """
    # Extract the description part from original patch name
    original_basename = os.path.basename(original_patch)
    match = re.match(r'patch\d+-(.+)\.patch$', original_basename)
    if not match:
        # Fallback: just append release
        description = original_basename.replace('.patch', '')
    else:
        description = match.group(1)

    # Get release info
    release = extract_release_from_project(project)
    codename = release_mappings.get(release, release)
    base_project = extract_base_project(project)

    # Build new patch name
    new_name = f'patch{patch_num:03d}-{base_project}-{codename}-{description}.patch'
    return f'_patches/{new_name}'


def analyze_failures(test_results):
    """Analyze test failures and determine fix strategies."""
    release_mappings = load_release_mappings()
    next_patch_num = get_next_patch_number()

    failures = test_results.get('failures', [])
    analyzed = []

    for failure in failures:
        project = failure.get('project', '')
        patch = failure.get('patch', '')
        error = failure.get('error', '')

        if not patch:
            continue

        # Find all projects using this patch, and split them by the
        # upstream they track. Series on the same upstream branch see the
        # same failure and want the same edit.
        all_users = find_patch_usage(patch)
        other_users = [p for p in all_users if p != project]

        source = load_series_source(project)
        same_source_users = [p for p in other_users if load_series_source(p) == source]
        other_release_users = [p for p in other_users if load_series_source(p) != source]

        # Determine strategy
        if not other_release_users:
            # Every user tracks the same upstream branch - fix the one patch
            # in place, and it is fixed for all of them.
            strategy = 'modify_in_place'
            suggested_name = None
            copy_for = []
        else:
            # Some users track a different upstream branch and must keep the
            # patch as it is - give this branch its own copy, and point every
            # series on this branch at it.
            strategy = 'create_copy'
            suggested_name = generate_patch_name(
                patch, project, release_mappings, next_patch_num
            )
            next_patch_num += 1
            copy_for = [project] + same_source_users

        analyzed.append({
            'patch': patch,
            'failed_in': project,
            'also_used_by': other_users,
            'same_source_users': same_source_users,
            'other_release_users': other_release_users,
            'strategy': strategy,
            'suggested_name': suggested_name,
            'copy_for': copy_for,
            'error': error,
        })

    return {
        'failures': analyzed,
        'release_mappings': release_mappings,
        'next_patch_number': next_patch_num,
    }


def main():
    if len(sys.argv) != 2:
        print('Usage: analyze-shared-patches.py <patch-test-results.json>',
              file=sys.stderr)
        sys.exit(1)

    results_file = sys.argv[1]

    try:
        with open(results_file, 'r') as f:
            test_results = json.load(f)
    except Exception as e:
        print(f'Error reading {results_file}: {e}', file=sys.stderr)
        sys.exit(1)

    analysis = analyze_failures(test_results)
    print(json.dumps(analysis, indent=2))


if __name__ == '__main__':
    main()
