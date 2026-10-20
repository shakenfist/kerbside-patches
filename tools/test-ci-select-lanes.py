#!/usr/bin/env python3
"""Tests for ci-select-lanes.py.

The selection rules are exercised against small fixture files; one test also
runs the real .github/ci-lanes.json through every event and tier, so a lanes
file that does not validate fails here rather than in a workflow run.

Usage:
    ./test-ci-select-lanes.py
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = SourceFileLoader('ci_select_lanes', os.path.join(HERE, 'ci-select-lanes.py'))
cisl = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(cisl)

REAL_LANES = os.path.join(HERE, '..', '.github', 'ci-lanes.json')


def build(name, tiers):
    return {'name': name, 'release': 'master', 'container_distro': 'debian',
            'container_distro_version': 'trixie', 'tiers': tiers}


def install(name, tiers, distro='debian', version='trixie'):
    return {'name': name, 'release': 'master', 'container_distro': distro,
            'container_distro_version': version, 'tiers': tiers}


def patch_test(name, tiers):
    return {'name': name, 'runs_on': 'debian-13', 'projects': ['kolla'], 'tiers': tiers}


def fixture():
    return {
        'builds': [build('b-both', ['smoke', 'merge'])],
        'installs': [install('i-both', ['smoke', 'merge']), install('i-merge', ['merge'])],
        'patch_tests': [patch_test('p-smoke', ['smoke']), patch_test('p-both', ['smoke', 'merge'])],
        'local_build': {'tiers': ['merge']},
    }


class SelectLanesTestCase(unittest.TestCase):
    def run_main(self, lanes, *args):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'lanes.json')
            with open(path, 'w') as f:
                json.dump(lanes, f)
            return self.run_main_path(path, *args)

    def run_main_path(self, path, *args):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cisl.main(['--lanes', path] + list(args))
        return code, out.getvalue(), err.getvalue()

    def select(self, lanes, *args):
        code, out, err = self.run_main(lanes, *args)
        self.assertEqual(0, code, err)
        result = {}
        for line in out.splitlines():
            key, value = line.split('=', 1)
            result[key] = value
        return result

    def names(self, result, kind):
        return [lane['name'] for lane in json.loads(result[kind])['test']]


class SelectionTest(SelectLanesTestCase):
    def test_pull_request_selects_smoke(self):
        r = self.select(fixture(), '--event', 'pull_request', '--code-changed', 'true')
        self.assertEqual(['b-both'], self.names(r, 'builds'))
        self.assertEqual(['i-both'], self.names(r, 'installs'))
        self.assertEqual(['p-smoke', 'p-both'], self.names(r, 'patch_tests'))
        self.assertEqual('true', r['run_builds'])
        self.assertEqual('true', r['run_installs'])
        self.assertEqual('true', r['run_patch_tests'])
        self.assertEqual('false', r['run_local_build'])

    def test_merge_group_selects_merge(self):
        r = self.select(fixture(), '--event', 'merge_group', '--code-changed', 'true')
        self.assertEqual(['i-both', 'i-merge'], self.names(r, 'installs'))
        self.assertEqual(['p-both'], self.names(r, 'patch_tests'))
        self.assertEqual('true', r['run_local_build'])

    def test_tier_is_ignored_outside_dispatch(self):
        r = self.select(fixture(), '--event', 'pull_request', '--tier', 'full', '--code-changed', 'true')
        self.assertEqual(['i-both'], self.names(r, 'installs'))

    def test_dispatch_full_selects_merge(self):
        r = self.select(fixture(), '--event', 'workflow_dispatch', '--tier', 'full', '--code-changed', 'true')
        self.assertEqual(['i-both', 'i-merge'], self.names(r, 'installs'))
        self.assertEqual('true', r['run_local_build'])

    def test_dispatch_smoke_selects_smoke(self):
        r = self.select(fixture(), '--event', 'workflow_dispatch', '--tier', 'smoke', '--code-changed', 'true')
        self.assertEqual(['i-both'], self.names(r, 'installs'))
        self.assertEqual('false', r['run_local_build'])

    def test_dispatch_empty_tier_selects_smoke(self):
        r = self.select(fixture(), '--event', 'workflow_dispatch', '--tier', '', '--code-changed', 'true')
        self.assertEqual(['i-both'], self.names(r, 'installs'))
        r = self.select(fixture(), '--event', 'workflow_dispatch', '--code-changed', 'true')
        self.assertEqual(['i-both'], self.names(r, 'installs'))

    def test_tiers_key_is_removed(self):
        r = self.select(fixture(), '--event', 'merge_group', '--code-changed', 'true')
        for kind in ('builds', 'installs', 'patch_tests'):
            for lane in json.loads(r[kind])['test']:
                self.assertNotIn('tiers', lane)

    def test_output_is_compact_json(self):
        r = self.select(fixture(), '--event', 'pull_request', '--code-changed', 'true')
        self.assertNotIn(' ', r['builds'])
        self.assertTrue(r['builds'].startswith('{"test":['))

    def test_code_changed_false_selects_nothing(self):
        for event in ('pull_request', 'merge_group', 'workflow_dispatch'):
            r = self.select(fixture(), '--event', event, '--tier', 'full', '--code-changed', 'false')
            for kind in ('builds', 'installs', 'patch_tests'):
                self.assertEqual('{"test":[]}', r[kind])
                self.assertEqual('false', r['run_' + kind])
            self.assertEqual('false', r['run_local_build'])

    def test_empty_list_is_not_run(self):
        lanes = fixture()
        lanes['patch_tests'] = [patch_test('p-merge', ['merge'])]
        r = self.select(lanes, '--event', 'pull_request', '--code-changed', 'true')
        self.assertEqual('false', r['run_patch_tests'])
        self.assertEqual('true', r['run_installs'])


class ErrorTest(SelectLanesTestCase):
    def test_unknown_event(self):
        code, out, err = self.run_main(fixture(), '--event', 'push', '--code-changed', 'true')
        self.assertEqual(1, code)
        self.assertEqual('', out)
        self.assertIn('unknown event', err)

    def test_unknown_tier(self):
        code, out, err = self.run_main(
            fixture(), '--event', 'workflow_dispatch', '--tier', 'nightly', '--code-changed', 'true')
        self.assertEqual(1, code)
        self.assertIn('unknown tier', err)

    def test_install_without_build(self):
        lanes = fixture()
        lanes['installs'].append(install('i-rocky', ['smoke'], distro='rocky', version='10'))
        code, out, err = self.run_main(lanes, '--event', 'pull_request', '--code-changed', 'true')
        self.assertEqual(1, code)
        self.assertIn('i-rocky', err)
        self.assertIn('container_distro=rocky', err)

    def test_install_whose_build_is_in_another_tier(self):
        lanes = fixture()
        lanes['builds'][0]['tiers'] = ['merge']
        code, out, err = self.run_main(lanes, '--event', 'pull_request', '--code-changed', 'true')
        self.assertEqual(1, code)
        self.assertIn('i-both', err)

    def test_duplicate_name(self):
        for kind, lane in (('builds', build('b-both', ['smoke'])),
                           ('installs', install('i-both', ['smoke'])),
                           ('patch_tests', patch_test('p-both', ['smoke']))):
            lanes = fixture()
            lanes[kind].append(lane)
            code, out, err = self.run_main(lanes, '--event', 'pull_request', '--code-changed', 'true')
            self.assertEqual(1, code, kind)
            self.assertIn('duplicate', err)

    def test_missing_file(self):
        code, out, err = self.run_main_path('/nonexistent/lanes.json', '--event', 'pull_request',
                                            '--code-changed', 'true')
        self.assertEqual(1, code)
        self.assertIn('ERROR:', err)


class RealLanesTest(SelectLanesTestCase):
    def test_every_event_and_tier_validates(self):
        for event, tier in (('pull_request', ''), ('merge_group', ''), ('workflow_dispatch', ''),
                            ('workflow_dispatch', 'smoke'), ('workflow_dispatch', 'full')):
            for changed in ('true', 'false'):
                code, out, err = self.run_main_path(
                    REAL_LANES, '--event', event, '--tier', tier, '--code-changed', changed)
                self.assertEqual(0, code, '%s %s %s: %s' % (event, tier, changed, err))

    def test_pull_request_selection(self):
        code, out, err = self.run_main_path(REAL_LANES, '--event', 'pull_request', '--code-changed', 'true')
        self.assertEqual(0, code, err)
        r = dict(line.split('=', 1) for line in out.splitlines())
        self.assertEqual(3, len(json.loads(r['builds'])['test']))
        self.assertEqual(8, len(json.loads(r['installs'])['test']))
        self.assertEqual(2, len(json.loads(r['patch_tests'])['test']))


if __name__ == '__main__':
    unittest.main()
