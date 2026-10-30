#!/usr/bin/env python3
"""Tests for analyze-shared-patches.py.

The analyzer decides, for a patch that failed to apply, whether the daily
rebase edits it in place or forks a renumbered copy for one release. Both
mistakes cost something: an unneeded copy breaks the one-copy-per-patch rule
and drifts from the original, while an in-place edit of a patch another
release still needs breaks that release. So which way each case falls is
tested rather than read off the source.

Each test builds a throwaway tree of series directories, each with the
config.yaml and ORDER the analyzer reads, and runs the analyzer's own
analyze_failures() in that tree with the GitHub lookup of patch numbers
replaced by a constant.

Usage:
    ./test-analyze-shared-patches.py
"""

import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(TOOLS_DIR)
ANALYZER = os.path.join(REPO_DIR, '_build', 'analyze-shared-patches.py')


def load_analyzer():
    spec = importlib.util.spec_from_file_location('analyze_shared_patches', ANALYZER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyzer = load_analyzer()


class TestFailure(Exception):
    pass


def check(condition, message):
    if not condition:
        raise TestFailure(message)


class Tree:
    """A throwaway checkout: series directories with config.yaml and ORDER."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix='analyze-shared-patches-')
        os.makedirs(os.path.join(self.root, '_patches'))

    def cleanup(self):
        shutil.rmtree(self.root)

    def patch(self, name):
        path = os.path.join(self.root, '_patches', name)
        with open(path, 'w') as f:
            f.write('--- a/x\n+++ b/x\n')
        return f'_patches/{name}'

    def series(self, name, patches, repo='https://opendev.org/openstack/kolla-ansible', source_branch='master',
               config=True):
        os.makedirs(os.path.join(self.root, name))
        if config:
            with open(os.path.join(self.root, name, 'config.yaml'), 'w') as f:
                f.write(f'repo: {repo}\nsource_branch: {source_branch}\nrelease: whatever\n')
        with open(os.path.join(self.root, name, 'ORDER'), 'w') as f:
            for patch in patches:
                f.write(f'../{patch}\n')

    def analyze(self, project, patch):
        results = {'failures': [{'project': project, 'patch': patch, 'error': 'patch failed: x:1'}]}
        cwd = os.getcwd()
        saved = analyzer.get_next_patch_number
        analyzer.get_next_patch_number = lambda: 300
        try:
            os.chdir(self.root)
            analysis = analyzer.analyze_failures(results)
        finally:
            os.chdir(cwd)
            analyzer.get_next_patch_number = saved
        check(len(analysis['failures']) == 1, f'expected one analyzed failure, got {analysis}')
        return analysis['failures'][0]


def test_single_user_is_modified_in_place():
    tree = Tree()
    try:
        patch = tree.patch('patch200-kolla-ansible-master-thing.patch')
        tree.series('kolla-ansible', [patch])
        result = tree.analyze('kolla-ansible', patch)
        check(result['strategy'] == 'modify_in_place', result)
        check(result['also_used_by'] == [], result)
        check(result['copy_for'] == [], result)
        check(result['suggested_name'] is None, result)
    finally:
        tree.cleanup()


def test_same_branch_users_are_modified_in_place():
    """The issue's case: the main master series and the deployer series share a patch."""
    tree = Tree()
    try:
        patch = tree.patch('patch206-kolla-ansible-master-thing.patch')
        tree.series('kolla-ansible', [patch])
        tree.series('kolla-ansible-deployer', [patch])
        tree.series('kolla-ansible-kerbside', [patch])
        result = tree.analyze('kolla-ansible', patch)
        check(result['strategy'] == 'modify_in_place',
              f'same-branch users must not fork a copy: {result}')
        check(result['same_source_users'] == ['kolla-ansible-deployer', 'kolla-ansible-kerbside'], result)
        check(result['other_release_users'] == [], result)
        check(result['suggested_name'] is None, result)
        check(result['copy_for'] == [], result)
    finally:
        tree.cleanup()


def test_failure_in_a_side_series_is_modified_in_place_too():
    tree = Tree()
    try:
        patch = tree.patch('patch199-kolla-master-thing.patch')
        tree.series('kolla', [patch], repo='https://opendev.org/openstack/kolla')
        tree.series('kolla-deployer', [patch], repo='https://opendev.org/openstack/kolla')
        result = tree.analyze('kolla-deployer', patch)
        check(result['strategy'] == 'modify_in_place', result)
        check(result['same_source_users'] == ['kolla'], result)
    finally:
        tree.cleanup()


def test_other_release_user_gets_a_copy():
    tree = Tree()
    try:
        patch = tree.patch('patch008-use-routable-ip.patch')
        tree.series('kolla-ansible', [patch])
        tree.series('kolla-ansible-2025.1', [patch], source_branch='stable/2025.1')
        result = tree.analyze('kolla-ansible-2025.1', patch)
        check(result['strategy'] == 'create_copy', result)
        check(result['other_release_users'] == ['kolla-ansible'], result)
        check(result['same_source_users'] == [], result)
        check(result['copy_for'] == ['kolla-ansible-2025.1'], result)
        check(result['suggested_name'] == '_patches/patch300-kolla-ansible-epoxy-use-routable-ip.patch', result)
    finally:
        tree.cleanup()


def test_copy_covers_every_series_on_the_failing_branch():
    """Master and its deployer series both fail; a stable series keeps the original."""
    tree = Tree()
    try:
        patch = tree.patch('patch008-use-routable-ip.patch')
        tree.series('kolla-ansible', [patch])
        tree.series('kolla-ansible-deployer', [patch])
        tree.series('kolla-ansible-2025.1', [patch], source_branch='stable/2025.1')
        result = tree.analyze('kolla-ansible', patch)
        check(result['strategy'] == 'create_copy', result)
        check(result['same_source_users'] == ['kolla-ansible-deployer'], result)
        check(result['other_release_users'] == ['kolla-ansible-2025.1'], result)
        check(result['copy_for'] == ['kolla-ansible', 'kolla-ansible-deployer'], result)
        check(result['suggested_name'] == '_patches/patch300-kolla-ansible-master-use-routable-ip.patch', result)
    finally:
        tree.cleanup()


def test_same_branch_of_a_different_repo_is_a_different_source():
    tree = Tree()
    try:
        patch = tree.patch('patch050-shared-thing.patch')
        tree.series('kolla', [patch], repo='https://opendev.org/openstack/kolla')
        tree.series('kolla-ansible', [patch])
        result = tree.analyze('kolla', patch)
        check(result['strategy'] == 'create_copy', result)
        check(result['other_release_users'] == ['kolla-ansible'], result)
    finally:
        tree.cleanup()


def test_series_without_config_falls_back_to_its_name():
    tree = Tree()
    try:
        patch = tree.patch('patch008-use-routable-ip.patch')
        tree.series('kolla-ansible', [patch], config=False)
        tree.series('kolla-ansible-2025.1', [patch], config=False)
        tree.series('kolla-ansible-deployer', [patch], config=False)
        result = tree.analyze('kolla-ansible', patch)
        check(result['strategy'] == 'create_copy', result)
        check(result['same_source_users'] == ['kolla-ansible-deployer'], result)
        check(result['other_release_users'] == ['kolla-ansible-2025.1'], result)
    finally:
        tree.cleanup()


def test_two_failures_get_distinct_numbers():
    tree = Tree()
    try:
        a = tree.patch('patch008-use-routable-ip.patch')
        b = tree.patch('patch009-other-thing.patch')
        tree.series('kolla-ansible', [a, b])
        tree.series('kolla-ansible-2025.1', [a, b], source_branch='stable/2025.1')
        results = {'failures': [
            {'project': 'kolla-ansible-2025.1', 'patch': a, 'error': 'x'},
            {'project': 'kolla-ansible-2025.1', 'patch': b, 'error': 'y'},
        ]}
        cwd = os.getcwd()
        saved = analyzer.get_next_patch_number
        analyzer.get_next_patch_number = lambda: 300
        try:
            os.chdir(tree.root)
            analysis = analyzer.analyze_failures(results)
        finally:
            os.chdir(cwd)
            analyzer.get_next_patch_number = saved
        names = [f['suggested_name'] for f in analysis['failures']]
        check(names == ['_patches/patch300-kolla-ansible-epoxy-use-routable-ip.patch',
                        '_patches/patch301-kolla-ansible-epoxy-other-thing.patch'], names)
        check(analysis['next_patch_number'] == 302, analysis)
    finally:
        tree.cleanup()


def test_cli_prints_json_for_the_real_tree():
    """The script runs end to end against this checkout's own series directories.

    `gh` is replaced on PATH by a stub that fails, so the patch number comes
    from the filesystem alone and the test never reaches GitHub. The
    assertion is on the shape of the output and on patch206, one of the
    master-to-master shares the issue names, being edited in place.
    """
    patch206 = glob.glob(os.path.join(REPO_DIR, '_patches', 'patch206-kolla-ansible-*.patch'))
    check(len(patch206) == 1, f'expected exactly one kolla-ansible patch206 in _patches/, found {patch206}')
    patch = '_patches/' + os.path.basename(patch206[0])
    results = {'failures': [{'project': 'kolla-ansible', 'patch': patch, 'error': 'e'}]}
    stub_dir = tempfile.mkdtemp(prefix='analyze-shared-patches-gh-')
    with open(os.path.join(stub_dir, 'gh'), 'w') as f:
        f.write('#!/bin/sh\nexit 1\n')
    os.chmod(os.path.join(stub_dir, 'gh'), 0o755)
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(results, f)
        path = f.name
    try:
        env = dict(os.environ)
        env['PATH'] = stub_dir + ':' + env.get('PATH', '')
        proc = subprocess.run([sys.executable, ANALYZER, path], cwd=REPO_DIR, capture_output=True, text=True,
                              timeout=120, env=env)
        check(proc.returncode == 0, proc.stderr)
        analysis = json.loads(proc.stdout)
        failure = analysis['failures'][0]
        check(failure['strategy'] == 'modify_in_place', failure)
        check('kolla-ansible-deployer' in failure['same_source_users'], failure)
        check(failure['also_used_by'] == failure['same_source_users'], failure)
        check(failure['other_release_users'] == [], failure)
    finally:
        os.unlink(path)
        shutil.rmtree(stub_dir)


TESTS = [
    test_single_user_is_modified_in_place,
    test_same_branch_users_are_modified_in_place,
    test_failure_in_a_side_series_is_modified_in_place_too,
    test_other_release_user_gets_a_copy,
    test_copy_covers_every_series_on_the_failing_branch,
    test_same_branch_of_a_different_repo_is_a_different_source,
    test_series_without_config_falls_back_to_its_name,
    test_two_failures_get_distinct_numbers,
    test_cli_prints_json_for_the_real_tree,
]


def main():
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f'PASS {test.__name__}')
        except TestFailure as e:
            failed += 1
            print(f'FAIL {test.__name__}: {e}')
        except Exception as e:
            failed += 1
            print(f'ERROR {test.__name__}: {type(e).__name__}: {e}')
    print()
    print(f'{len(TESTS) - failed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
