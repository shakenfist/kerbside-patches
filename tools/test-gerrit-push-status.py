#!/usr/bin/env python3
"""Tests for gerrit-push-status.py.

Gerrit's REST API is replaced by a stub, and opendev.org by a local repository
that holds the uploaded patchsets under refs/changes/, so this runs offline.

Usage:
    ./test-gerrit-push-status.py
"""

import os
import subprocess
import sys
import tempfile
from importlib import import_module

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

status_tool = import_module('gerrit-push-status')

PROJECT = 'openstack/demo'
IDS = ['I' + str(n) * 40 for n in range(1, 4)]


class TestFailure(Exception):
    pass


def git(repo, *args, stdin=None):
    env = status_tool._clean_env(GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.com',
                                 GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.com')
    return subprocess.run(['git', '-C', repo, *args], input=stdin, env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


class World:
    """An upstream repository, the Gerrit changes uploaded to it, and a kerbside series."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.upstream = os.path.join(tmp, 'upstream', PROJECT)
        os.makedirs(self.upstream)
        git(self.upstream, 'init', '-q', '-b', 'master')
        with open(os.path.join(self.upstream, 'file.txt'), 'w') as f:
            f.write(''.join(f'line {n}\n' for n in range(1, 41)))
        git(self.upstream, 'add', 'file.txt')
        git(self.upstream, 'commit', '-q', '-m', 'Base.')
        self.base = git(self.upstream, 'rev-parse', 'HEAD')
        self.changes = {}
        self.series = os.path.join(tmp, 'kerbside', 'demo-series')
        os.makedirs(self.series)
        os.makedirs(os.path.join(tmp, 'kerbside', '_patches'))

    def commit(self, parent, line, text, subject, change_id):
        """Commit one edit to file.txt on top of *parent* and return its sha."""
        git(self.upstream, 'checkout', '-q', '--detach', parent)
        path = os.path.join(self.upstream, 'file.txt')
        with open(path) as f:
            lines = f.readlines()
        lines[line] = text + '\n'
        with open(path, 'w') as f:
            f.writelines(lines)
        git(self.upstream, 'commit', '-q', '-a', '-m', f'{subject}\n\nChange-Id: {change_id}')
        return git(self.upstream, 'rev-parse', 'HEAD')

    def upload(self, number, sha):
        """Make *sha* patchset 1 of change *number*, as Gerrit would hold it."""
        ref = f'refs/changes/{number % 100:02d}/{number}/1'
        git(self.upstream, 'update-ref', ref, sha)
        message = git(self.upstream, 'log', '-1', '--format=%B', sha)
        parent = git(self.upstream, 'rev-parse', f'{sha}^')
        change_id = message.split('Change-Id: ')[1].strip()
        self.changes[change_id] = {
            '_number': number, 'status': 'NEW', 'current_revision': sha,
            'revisions': {sha: {'_number': 1, 'ref': ref,
                                'commit': {'message': message + '\n', 'parents': [{'commit': parent}]}}}}

    def write_patch(self, name, sha, *show_args):
        with open(os.path.join(self.tmp, 'kerbside', '_patches', name), 'w') as f:
            f.write(git(self.upstream, 'show', *show_args, sha) + '\n')

    def write_series(self, order, source_sha=None):
        with open(os.path.join(self.series, 'config.yaml'), 'w') as f:
            f.write(f'repo: https://opendev.org/{PROJECT}\nsource_branch: master\n'
                    f'destination_branch: demo\nsource_sha: {source_sha or self.base}\n')
        with open(os.path.join(self.series, 'ORDER'), 'w') as f:
            f.write(''.join(line + '\n' for line in order))

    def run(self):
        status_tool.find_change = lambda project, branch, change_id: self.changes.get(change_id)
        status_tool.GIT = 'file://' + os.path.join(self.tmp, 'upstream')
        cache = status_tool.Cache(os.path.join(self.tmp, 'cache'))
        _, rows = status_tool.status(self.series, cache)
        return [(r['patch'].split('-')[0], r['state']) for r in rows]


def check(name, world, expected):
    got = world.run()
    if got != expected:
        raise TestFailure(f'{name}: expected {expected}, got {got}')
    print(f'ok   {name}')


def scenario(build):
    with tempfile.TemporaryDirectory() as tmp:
        world = World(tmp)
        one = world.commit(world.base, 5, 'first change', 'First.', IDS[0])
        two = world.commit(one, 30, 'second change', 'Second.', IDS[1])
        world.upload(1001, one)
        world.upload(1002, two)
        build(world, one, two)


def test_unchanged_series_is_current():
    def build(world, one, two):
        world.write_patch('patch001-demo.patch', one)
        world.write_patch('patch002-demo.patch', two)
        world.write_series(['../_patches/patch001-demo.patch', '../_patches/patch002-demo.patch'])
        check('unchanged series is current', world, [('patch001', 'current'), ('patch002', 'current')])
    scenario(build)


def test_diff_text_does_not_matter():
    def build(world, one, two):
        # The same change with more context lines is different diff text.
        world.write_patch('patch001-demo.patch', one, '-U8')
        world.write_patch('patch002-demo.patch', two, '-U1')
        world.write_series(['../_patches/patch001-demo.patch', '../_patches/patch002-demo.patch'])
        check('diff text does not matter', world, [('patch001', 'current'), ('patch002', 'current')])
    scenario(build)


def test_changed_diff_rebases_the_rest():
    def build(world, one, two):
        edited = world.commit(world.base, 5, 'first change, revised', 'First.', IDS[0])
        world.write_patch('patch001-demo.patch', edited)
        world.write_patch('patch002-demo.patch', world.commit(edited, 30, 'second change', 'Second.', IDS[1]))
        world.write_series(['../_patches/patch001-demo.patch', '../_patches/patch002-demo.patch'])
        check('changed diff rebases the rest', world, [('patch001', 'changed'), ('patch002', 'rebase')])
    scenario(build)


def test_changed_message():
    def build(world, one, two):
        world.write_patch('patch001-demo.patch', one)
        world.write_patch('patch002-demo.patch', world.commit(one, 30, 'second change', 'Second, reworded.', IDS[1]))
        world.write_series(['../_patches/patch001-demo.patch', '../_patches/patch002-demo.patch'])
        check('changed message', world, [('patch001', 'current'), ('patch002', 'changed')])
    scenario(build)


def test_new_patch_rebases_the_rest():
    def build(world, one, two):
        new = world.commit(world.base, 15, 'new change', 'New.', IDS[2])
        world.write_patch('patch000-demo.patch', new)
        world.write_patch('patch001-demo.patch', world.commit(new, 5, 'first change', 'First.', IDS[0]))
        world.write_series(['../_patches/patch000-demo.patch', '../_patches/patch001-demo.patch'])
        check('new patch rebases the rest', world, [('patch000', 'new'), ('patch001', 'rebase')])
    scenario(build)


def test_held_patch_does_not_rebase_the_rest():
    def build(world, one, two):
        world.write_patch('patch001-demo.patch', one)
        world.write_patch('patch002-demo.patch', two)
        held = world.commit(two, 15, 'held change', 'Held.', IDS[2])
        world.write_patch('patch003-demo.patch', held)
        world.write_series(['../_patches/patch001-demo.patch', '# ../_patches/patch003-demo.patch',
                            '../_patches/patch002-demo.patch'])
        check('held patch does not rebase the rest', world,
              [('patch001', 'current'), ('patch003', 'new'), ('patch002', 'current')])
    scenario(build)


def test_rerun_with_a_warm_cache():
    def build(world, one, two):
        three = world.commit(two, 15, 'third change', 'Third.', IDS[2])
        world.upload(1003, three)
        names = []
        for n, sha in enumerate((one, two, three), 1):
            names.append(f'patch00{n}-demo.patch')
            world.write_patch(names[-1], sha)
        world.write_series([f'../_patches/{name}' for name in names])
        expected = [('patch001', 'current'), ('patch002', 'current'), ('patch003', 'current')]
        check('first run with a cold cache', world, expected)
        # Fetching the third change at depth 2 made the second a shallow
        # boundary in the cache, hiding the parent its patch applies to.
        check('rerun with a warm cache', world, expected)
    scenario(build)


def test_moved_source_sha_rebases():
    def build(world, one, two):
        world.write_patch('patch001-demo.patch', one)
        world.write_series(['../_patches/patch001-demo.patch'], source_sha='0' * 40)
        check('moved source_sha rebases', world, [('patch001', 'rebase')])
    scenario(build)


def test_all_selects_status_push():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, 'series.yaml'), 'w') as f:
            f.write('demo:\n'
                    '  demo-carried:\n    status: carry\n'
                    '  demo-held:\n    status: hold\n'
                    '  demo-pushed:\n    status: push\n'
                    'other:\n'
                    '  another-pushed:\n    status: push\n    after: [demo-pushed]\n')
        topdir = status_tool.TOPDIR
        status_tool.TOPDIR = tmp
        try:
            got = status_tool.repush_series()
        finally:
            status_tool.TOPDIR = topdir
    expected = ['another-pushed', 'demo-pushed']
    if got != expected:
        raise TestFailure(f'--all selects status push: expected {expected}, got {got}')
    print('ok   --all selects status push')


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    with tempfile.TemporaryDirectory() as decoy:
        # Run as a git hook would, with git's variables naming another
        # repository, to prove that neither this suite nor the tool uses it.
        decoy_index = os.path.join(decoy, 'index')
        os.environ.update(GIT_DIR=os.path.join(decoy, 'git'), GIT_INDEX_FILE=decoy_index)
        for test in tests:
            try:
                test()
            except TestFailure as e:
                print(f'FAIL {e}')
                failed += 1
        if os.path.exists(decoy_index):
            print('FAIL the git environment of the calling repository was used')
            failed += 1
    print(f'{len(tests) - failed} of {len(tests)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
