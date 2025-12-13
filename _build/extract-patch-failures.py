#!/usr/bin/env python3
"""Extract patch failure details from test-patches-for-ci.sh JSON output.

Reads JSON from stdin and outputs human-readable failure details to stdout.
Also writes a simplified failure summary suitable for Claude Code prompts.

Usage:
    cat patch-test-results.json | ./_build/extract-patch-failures.py

Output format:
    Project: kolla
    Patch: _patches/patch112-kolla-layer-data.patch
    Error: error: patch failed: kolla/common/config.py:271
    ---
"""

import json
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f'Error parsing JSON: {e}', file=sys.stderr)
        sys.exit(1)

    failures = data.get('failures', [])

    if not failures:
        print('No failures found.')
        sys.exit(0)

    for failure in failures:
        project = failure.get('project', 'unknown')
        patch = failure.get('patch', 'unknown')
        error = failure.get('error', 'no error message')

        print(f'Project: {project}')
        print(f'Patch: {patch}')
        print(f'Error: {error}')
        print('---')


if __name__ == '__main__':
    main()
