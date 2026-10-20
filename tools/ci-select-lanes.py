#!/usr/bin/env python3
"""Select the CI lanes to run for a workflow event.

The lanes live in a data file, not in the workflow, so that each kind of lane
(image build, install test, patch test) has exactly one job definition and a
lane is added or moved between tiers by editing data. The file is under
.github/ rather than etc/ because .github/ is outside the image hash: changing
which lanes run must not invalidate the images that were already built.

Each lane carries the tiers it runs in. A pull request runs the smoke tier,
the merge queue runs the merge tier, and a manual dispatch runs smoke unless
it asks for full, which means the merge tier.

The output is one key=value per line, ready to append to $GITHUB_OUTPUT:
builds, installs and patch_tests are compact JSON of the form {"test": [...]}
(so jobs keep reading matrix.test.*), and run_builds, run_installs,
run_patch_tests and run_local_build say whether each kind of job should run.

Usage:
    ci-select-lanes.py --lanes .github/ci-lanes.json --event pull_request \
        --code-changed true
"""

import argparse
import json
import sys

EVENT_TIERS = {
    'pull_request': 'smoke',
    'merge_group': 'merge',
}
DISPATCH_TIERS = {
    '': 'smoke',
    'smoke': 'smoke',
    'full': 'merge',
}
LANE_KINDS = ('builds', 'installs', 'patch_tests')
INSTALL_BUILD_KEY = ('release', 'container_distro', 'container_distro_version')


class SelectionError(Exception):
    pass


def selected_tier(event, tier):
    if event == 'workflow_dispatch':
        if tier not in DISPATCH_TIERS:
            raise SelectionError('unknown tier %r, expected smoke, full or empty' % tier)
        return DISPATCH_TIERS[tier]
    if event not in EVENT_TIERS:
        raise SelectionError(
            'unknown event %r, expected pull_request, merge_group or workflow_dispatch' % event)
    return EVENT_TIERS[event]


def check_unique_names(lanes):
    for kind in LANE_KINDS:
        seen = set()
        for lane in lanes[kind]:
            if lane['name'] in seen:
                raise SelectionError('duplicate %s lane name %r' % (kind, lane['name']))
            seen.add(lane['name'])


def strip_tiers(lane):
    return {k: v for k, v in lane.items() if k != 'tiers'}


def select(lanes, event, tier, code_changed):
    """Return the output as an ordered dict of key to string value."""
    chosen = selected_tier(event, tier)
    check_unique_names(lanes)

    selected = {}
    for kind in LANE_KINDS:
        if code_changed:
            selected[kind] = [strip_tiers(lane) for lane in lanes[kind] if chosen in lane['tiers']]
        else:
            selected[kind] = []

    built = {tuple(build[k] for k in INSTALL_BUILD_KEY) for build in selected['builds']}
    for install in selected['installs']:
        key = tuple(install[k] for k in INSTALL_BUILD_KEY)
        if key not in built:
            raise SelectionError(
                'install %r needs an image build for %s, but none is selected' % (
                    install['name'], ', '.join('%s=%s' % pair for pair in zip(INSTALL_BUILD_KEY, key))))

    output = {}
    for kind in LANE_KINDS:
        output[kind] = json.dumps({'test': selected[kind]}, separators=(',', ':'))
    for kind in LANE_KINDS:
        output['run_' + kind] = 'true' if selected[kind] else 'false'
    local_build = code_changed and chosen in lanes['local_build']['tiers']
    output['run_local_build'] = 'true' if local_build else 'false'
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description='Select the CI lanes to run for a workflow event.')
    parser.add_argument('--lanes', required=True, help='Path to the lanes JSON file')
    parser.add_argument('--event', required=True, help='pull_request, merge_group or workflow_dispatch')
    parser.add_argument('--tier', default='', help='smoke, full or empty; only read for workflow_dispatch')
    parser.add_argument('--code-changed', required=True, choices=['true', 'false'],
                        help='Whether anything outside the docs-only paths changed')
    args = parser.parse_args(argv)

    try:
        with open(args.lanes) as f:
            lanes = json.load(f)
        output = select(lanes, args.event, args.tier, args.code_changed == 'true')
    except (SelectionError, OSError, ValueError, KeyError) as e:
        print('ERROR: %s' % (e if not isinstance(e, KeyError) else 'lanes file lacks %s' % e), file=sys.stderr)
        return 1

    for key, value in output.items():
        print('%s=%s' % (key, value))
    return 0


if __name__ == '__main__':
    sys.exit(main())
