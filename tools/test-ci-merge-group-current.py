#!/usr/bin/env python3
"""Tests for ci-merge-group-current.sh.

The script stops a superseded merge group run cancelling the live one. Its
two failure directions are not equally bad -- calling a live run superseded
ejects a pull request untested -- so which way each case falls is tested
rather than read off the source. The script is run for real with `git` and
`gh` replaced by stubs on PATH.

Usage:
    ./test-ci-merge-group-current.py
"""

import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'ci-merge-group-current.sh')

BASE_REF = 'refs/heads/develop'
BASE_SHA = '80292df08060be0cfcbc26a98470151df0f9c63d'
NEWER_SHA = 'deb25fc78e1b0f6f7c9a6f1a2b3c4d5e6f708192'
RUN_ID = '37264092929'

# Records its arguments, then answers ls-remote from fixtures: the line to
# print, and the exit code to print it with.
GIT_STUB = """#!/bin/bash
echo "$@" >> "${STUB_LOG}/git.log"
if [ -e "${STUB_LOG}/ls-remote.out" ]; then
    cat "${STUB_LOG}/ls-remote.out"
fi
exit "$(cat "${STUB_LOG}/ls-remote.rc" 2>/dev/null || echo 0)"
"""

GH_STUB = """#!/bin/bash
echo "$@" >> "${STUB_LOG}/gh.log"
exit "$(cat "${STUB_LOG}/gh.rc" 2>/dev/null || echo 0)"
"""


class MergeGroupCurrentScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.bin = os.path.join(self.tempdir, 'bin')
        self.log = os.path.join(self.tempdir, 'log')
        os.makedirs(self.bin)
        os.makedirs(self.log)
        for name, content in [('git', GIT_STUB), ('gh', GH_STUB)]:
            path = os.path.join(self.bin, name)
            with open(path, 'w') as f:
                f.write(content)
            os.chmod(path, 0o755)

    def _fixture(self, name, content):
        with open(os.path.join(self.log, name), 'w') as f:
            f.write(content)

    def _read(self, name):
        path = os.path.join(self.log, name)
        if not os.path.exists(path):
            return ''
        with open(path) as f:
            return f.read()

    def remote_head(self, sha, rc=0):
        if sha is not None:
            self._fixture('ls-remote.out', '%s\t%s\n' % (sha, BASE_REF))
        self._fixture('ls-remote.rc', str(rc))

    def run_check(self, **overrides):
        environment = dict(os.environ)
        environment.update({
            'PATH': '%s:%s' % (self.bin, os.environ['PATH']),
            'STUB_LOG': self.log,
            'BASE_REF': BASE_REF,
            'BASE_SHA': BASE_SHA,
            'RUN_ID': RUN_ID,
            'CANCEL_WAIT_SECONDS': '0',
        })
        environment.update(overrides)
        for name in [k for k, v in environment.items() if v is None]:
            del environment[name]
        return subprocess.run(
            [SCRIPT], capture_output=True, text=True, env=environment)

    def test_a_current_merge_group_carries_on(self):
        self.remote_head(BASE_SHA)
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual('', self._read('gh.log'))

    def test_the_base_ref_from_the_event_is_what_is_read(self):
        self.remote_head(BASE_SHA)
        self.run_check()
        self.assertEqual(
            'ls-remote --exit-code origin %s\n' % BASE_REF,
            self._read('git.log'))

    def test_a_superseded_merge_group_cancels_its_own_run(self):
        self.remote_head(NEWER_SHA)
        proc = self.run_check()
        self.assertEqual('run cancel %s\n' % RUN_ID, self._read('gh.log'))
        self.assertIn('superseded', proc.stdout)

    def test_a_superseded_run_still_running_after_cancelling_fails(self):
        # Exiting zero here would let every queue job start on a stale base.
        self.remote_head(NEWER_SHA)
        proc = self.run_check()
        self.assertNotEqual(0, proc.returncode)

    def test_a_superseded_run_that_cannot_cancel_fails(self):
        self.remote_head(NEWER_SHA)
        self._fixture('gh.rc', '1')
        proc = self.run_check()
        self.assertNotEqual(0, proc.returncode)
        self.assertIn('Could not cancel', proc.stdout)

    def test_an_unreadable_base_branch_is_assumed_current(self):
        # Calling a live run superseded is the bug, so not knowing must not
        # cancel anything.
        self.remote_head(None, rc=128)
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual('', self._read('gh.log'))
        self.assertIn('::warning::', proc.stdout)

    def test_a_missing_base_branch_is_assumed_current(self):
        # ls-remote --exit-code exits 2, printing nothing, for a ref which
        # does not exist.
        self.remote_head(None, rc=2)
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual('', self._read('gh.log'))

    def test_an_empty_answer_is_assumed_current(self):
        self._fixture('ls-remote.out', '')
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual('', self._read('gh.log'))

    def test_missing_environment_variables_fail_without_side_effects(self):
        self.remote_head(NEWER_SHA)
        for name in ['BASE_REF', 'BASE_SHA', 'RUN_ID']:
            proc = self.run_check(**{name: None})
            self.assertNotEqual(0, proc.returncode, name)
            self.assertIn('%s must be set' % name, proc.stderr)
        self.assertEqual('', self._read('git.log'))
        self.assertEqual('', self._read('gh.log'))


if __name__ == '__main__':
    unittest.main()
