#!/usr/bin/env python3
"""Tests for series.py and check-series-index.py.

Each test builds a small repository in a temporary directory: series
directories holding ORDER and config.yaml, and a series.yaml naming them.

Usage:
    ./test-series-index.py
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import series  # noqa: E402

_loader = SourceFileLoader('check_series_index', os.path.join(HERE, 'check-series-index.py'))
check = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(check)

# A clean index for the tree make_tree() builds by default.
CLEAN = """\
devstack:
  devstack-privsep-memory-001:
    status: push
kolla:
  kolla:
    status: carry
  kolla-deployer:
    status: hold
    after: [kolla]
    note: Waiting for a reviewer.
neutron:
  neutron-import-memory-007:
    status: push
    after: [devstack-privsep-memory-001, neutron-opts-014]
  neutron-opts-014:
    status: push
"""

DIRECTORIES = {
    'devstack-privsep-memory-001': 'devstack',
    'kolla': 'kolla',
    'kolla-deployer': 'kolla',
    'neutron-import-memory-007': 'neutron',
    'neutron-opts-014': 'neutron',
}


class Repository(unittest.TestCase):
    """A test case with a fixture repository in self.root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def make_series(self, name, directory, extra='', order=True):
        os.makedirs(os.path.join(self.root, name), exist_ok=True)
        with open(os.path.join(self.root, name, 'config.yaml'), 'w') as f:
            f.write(f'repo: https://opendev.org/openstack/{directory}\nsource_branch: master\n'
                    f'destination_branch: master-patches\ndirectory: {directory}\n{extra}')
        if order:
            with open(os.path.join(self.root, name, 'ORDER'), 'w') as f:
                f.write(f'../_patches/patch001-{name}.patch\n')

    def make_tree(self, index=CLEAN, directories=None):
        for name, directory in (directories or DIRECTORIES).items():
            self.make_series(name, directory)
        if index is not None:
            self.write_index(index)

    def write_index(self, text):
        with open(os.path.join(self.root, 'series.yaml'), 'w') as f:
            f.write(text)


##############################################################################
# check-series-index.py                                                      #
##############################################################################

class TestCheck(Repository):

    def assertProblem(self, fragment, count=1):
        found = check.problems(self.root)
        matching = [p for p in found if fragment in p]
        self.assertEqual(len(matching), count, f'expected {count} problem(s) with {fragment!r}, got {found}')
        return found

    def test_clean_tree_passes(self):
        self.make_tree()
        self.assertEqual(check.problems(self.root), [])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(check.main(['--root', self.root]), 0)
        self.assertEqual(out.getvalue(), '')

    def test_every_problem_is_printed(self):
        self.make_tree(CLEAN.replace('status: carry', 'status: maybe')
                            .replace('    note: Waiting', '    colour: blue\n    note: Waiting'))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(check.main(['--root', self.root]), 1)
        self.assertIn("status 'maybe'", out.getvalue())
        self.assertIn('unknown key colour', out.getvalue())
        self.assertIn('2 problems', out.getvalue())

    def test_missing_index(self):
        self.make_tree(index=None)
        found = self.assertProblem('series.yaml does not exist')
        self.assertEqual(len([p for p in found if 'no entry in series.yaml' in p]), len(DIRECTORIES))

    def test_directory_without_entry(self):
        self.make_tree()
        self.make_series('neutron-new-015', 'neutron')
        self.assertProblem('neutron-new-015: has ORDER and config.yaml but no entry')

    def test_directory_without_order_is_not_a_series(self):
        self.make_tree()
        self.make_series('neutron-half', 'neutron', order=False)
        self.assertEqual(check.problems(self.root), [])

    def test_entry_without_directory(self):
        self.make_tree(CLEAN + '  neutron-zz-gone:\n    status: push\n')
        self.assertProblem('neutron-zz-gone: listed in series.yaml but there is no neutron-zz-gone/')

    def test_entry_whose_directory_has_no_order(self):
        directories = dict(DIRECTORIES)
        del directories['neutron-opts-014']
        self.make_tree(directories=directories)
        self.make_series('neutron-opts-014', 'neutron', order=False)
        self.assertProblem('neutron-opts-014: listed in series.yaml but there is no')

    def test_wrong_project(self):
        # Move kolla-deployer from kolla to neutron.
        self.make_tree(CLEAN.replace('  kolla-deployer:\n    status: hold\n    after: [kolla]\n'
                                     '    note: Waiting for a reviewer.\n', '')
                       .replace('neutron:\n', 'neutron:\n  kolla-deployer:\n    status: hold\n'))
        self.assertProblem('kolla-deployer: filed under neutron in series.yaml, but config.yaml says directory kolla')

    def test_listed_under_two_projects(self):
        self.make_tree(CLEAN.replace('neutron:\n', 'neutron:\n  kolla:\n    status: carry\n'))
        self.assertProblem('kolla: listed under more than one project (kolla, neutron)')

    def test_unknown_after(self):
        self.make_tree(CLEAN.replace('after: [kolla]', 'after: [kolla, nova-nothing]'))
        self.assertProblem('kolla-deployer: after names nova-nothing, which is not a series')

    def test_after_must_be_a_list(self):
        self.make_tree(CLEAN.replace('after: [kolla]', 'after: kolla'))
        self.assertProblem('kolla-deployer: after must be a list')

    def test_after_names_itself(self):
        self.make_tree(CLEAN.replace('after: [kolla]', 'after: [kolla-deployer]'))
        found = self.assertProblem('kolla-deployer: after names the series itself')
        self.assertEqual([p for p in found if 'cycle' in p], [])

    def test_cycle(self):
        self.make_tree(CLEAN.replace('  neutron-opts-014:\n    status: push\n',
                                     '  neutron-opts-014:\n    status: push\n    after: [neutron-import-memory-007]\n'))
        self.assertProblem('the after links form a cycle: neutron-import-memory-007, neutron-opts-014')

    def test_cycle_of_three(self):
        # kolla-deployer is already after kolla; close the loop through devstack.
        self.make_tree(CLEAN.replace('    status: push\nkolla:',
                                     '    status: push\n    after: [kolla-deployer]\nkolla:')
                       .replace('  kolla:\n    status: carry\n',
                                '  kolla:\n    status: carry\n    after: [devstack-privsep-memory-001]\n'))
        self.assertProblem('the after links form a cycle: devstack-privsep-memory-001, kolla, kolla-deployer')

    def test_bad_status(self):
        self.make_tree(CLEAN.replace('status: carry', 'status: merged'))
        self.assertProblem("kolla: status 'merged' is not one of push, hold, carry")

    def test_missing_status(self):
        self.make_tree(CLEAN.replace('    status: carry\n', '    note: no status\n'))
        self.assertProblem('kolla: has no status')

    def test_entry_not_a_mapping(self):
        self.make_tree(CLEAN.replace('  kolla:\n    status: carry\n', '  kolla:\n'))
        self.assertProblem('kolla: must be a mapping')

    def test_unknown_key(self):
        self.make_tree(CLEAN.replace('status: carry', 'status: carry\n    repush: true'))
        self.assertProblem('kolla: unknown key repush')

    def test_note_must_be_a_string(self):
        self.make_tree(CLEAN.replace('note: Waiting for a reviewer.', 'note: [a, b]'))
        self.assertProblem('kolla-deployer: note must be a string')

    def test_repush_in_config(self):
        self.make_tree()
        self.make_series('kolla', 'kolla', extra='repush: false\n')
        self.assertProblem('kolla/config.yaml: has a repush key')

    def test_repush_in_config_without_order(self):
        self.make_tree()
        self.make_series('kolla-half', 'kolla', extra='repush: true\n', order=False)
        self.assertProblem('kolla-half/config.yaml: has a repush key')

    def test_series_not_sorted(self):
        self.make_tree(CLEAN.replace('  neutron-import-memory-007:\n    status: push\n'
                                     '    after: [devstack-privsep-memory-001, neutron-opts-014]\n', '')
                       + '  neutron-import-memory-007:\n    status: push\n')
        self.assertProblem('series under neutron are not sorted by name: neutron-opts-014 should come after '
                           'neutron-import-memory-007')

    def test_projects_not_sorted(self):
        self.make_tree(CLEAN.replace('devstack:\n  devstack-privsep-memory-001:\n    status: push\n', '')
                       + 'devstack:\n  devstack-privsep-memory-001:\n    status: push\n')
        self.assertProblem('projects are not sorted by name: neutron should come after devstack')

    def test_duplicate_key(self):
        self.make_tree(CLEAN + 'kolla:\n  kolla-extra:\n    status: push\n')
        self.assertProblem("duplicate key 'kolla'")

    def test_src_and_upstream_do_not_count(self):
        self.make_tree()
        self.make_series('src', 'neutron')
        self.make_series('upstream', 'qemu', extra='repush: true\n')
        os.makedirs(os.path.join(self.root, 'src', 'neutron'))
        with open(os.path.join(self.root, 'src', 'neutron', 'config.yaml'), 'w') as f:
            f.write('repush: true\n')
        self.make_series(os.path.join('upstream', 'qemu'), 'qemu', extra='repush: true\n')
        self.assertEqual(check.problems(self.root), [])

    def test_run_as_a_script(self):
        self.make_tree()
        self.make_series('neutron-new-015', 'neutron')
        result = subprocess.run([sys.executable, os.path.join(HERE, 'check-series-index.py'), '--root', self.root],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('neutron-new-015: has ORDER and config.yaml but no entry', result.stdout)


##############################################################################
# series.py                                                                  #
##############################################################################

class TestSeries(Repository):

    def setUp(self):
        super().setUp()
        self.make_tree()

    def run_cli(self, *args):
        """Run series.main() and return (exit status, stdout lines, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = series.main(['--root', self.root, *args])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_load(self):
        index = series.load(self.root)
        self.assertEqual(index['kolla-deployer'],
                         series.Series(name='kolla-deployer', project='kolla', status='hold', after=('kolla',),
                                       note='Waiting for a reviewer.'))
        self.assertEqual(index['kolla'].after, ())
        self.assertIsNone(index['kolla'].note)

    def test_load_rejects_an_invalid_index(self):
        self.write_index(CLEAN.replace('after: [kolla]', 'after: [nova-nothing]'))
        with self.assertRaisesRegex(series.SeriesError, 'nova-nothing, which is not a series'):
            series.load(self.root)

    def test_list_sorted_by_project_then_name(self):
        self.assertEqual(self.run_cli('list'), (0, ['devstack-privsep-memory-001', 'kolla', 'kolla-deployer',
                                                    'neutron-import-memory-007', 'neutron-opts-014'], ''))

    def test_list_by_project(self):
        self.assertEqual(self.run_cli('list', '--project', 'neutron'),
                         (0, ['neutron-import-memory-007', 'neutron-opts-014'], ''))
        self.assertEqual(self.run_cli('list', '--project', 'nova'), (0, [], ''))

    def test_list_by_status(self):
        self.assertEqual(self.run_cli('list', '--status', 'carry'), (0, ['kolla'], ''))
        both = ['kolla', 'kolla-deployer']
        self.assertEqual(self.run_cli('list', '--status', 'carry', '--status', 'hold'), (0, both, ''))
        self.assertEqual(self.run_cli('list', '--status', 'carry,hold'), (0, both, ''))
        self.assertEqual(self.run_cli('list', '--status', 'push', '--project', 'devstack'),
                         (0, ['devstack-privsep-memory-001'], ''))

    def test_list_bad_status(self):
        code, lines, err = self.run_cli('list', '--status', 'merged')
        self.assertEqual((code, lines), (2, []))
        self.assertIn("'merged' is not one of push, hold, carry", err)

    def test_ordered(self):
        # neutron-import-memory-007 sorts before neutron-opts-014, but is after it.
        self.assertEqual(self.run_cli('list', '--ordered'),
                         (0, ['devstack-privsep-memory-001', 'kolla', 'kolla-deployer', 'neutron-opts-014',
                              'neutron-import-memory-007'], ''))

    def test_ordered_ties_break_by_project_then_name(self):
        self.write_index('a-project:\n  zz-first:\n    status: push\n    after: [zz-late]\n'
                         'b-project:\n  b-one:\n    status: push\n  b-two:\n    status: push\n'
                         'c-project:\n  a-c:\n    status: push\n  zz-late:\n    status: push\n')
        # No link holds b-one, b-two or a-c back, so they come in project then
        # name order; zz-first waits for zz-late, though its project sorts first.
        index = {s.name: s for s in [
            series.Series('zz-first', 'a-project', 'push', ('zz-late',)),
            series.Series('b-two', 'b-project', 'push'),
            series.Series('b-one', 'b-project', 'push'),
            series.Series('zz-late', 'c-project', 'push'),
            series.Series('a-c', 'c-project', 'push'),
        ]}
        self.assertEqual([s.name for s in series.ordered(index)], ['b-one', 'b-two', 'a-c', 'zz-late', 'zz-first'])
        # The same through the command line, which reads the file instead.
        self.assertEqual(self.run_cli('list', '--ordered')[1], ['b-one', 'b-two', 'a-c', 'zz-late', 'zz-first'])

    def test_ordered_filters_after_ordering(self):
        index = {s.name: s for s in [
            series.Series('a', 'p', 'push', ('m',)),
            series.Series('m', 'p', 'hold', ('z',)),
            series.Series('z', 'p', 'push'),
        ]}
        # a follows z through m, even though m is filtered out.
        self.assertEqual([s.name for s in series.ordered(index, statuses=['push'])], ['z', 'a'])
        self.assertEqual(self.run_cli('list', '--ordered', '--status', 'push', '--project', 'neutron')[1],
                         ['neutron-opts-014', 'neutron-import-memory-007'])

    def test_ordered_cycle(self):
        index = {s.name: s for s in [
            series.Series('a', 'p', 'push', ('b',)),
            series.Series('b', 'p', 'push', ('c',)),
            series.Series('c', 'p', 'push', ('a',)),
            series.Series('d', 'p', 'push'),
        ]}
        with self.assertRaises(series.CycleError) as raised:
            series.ordered(index)
        self.assertEqual(raised.exception.cycles, [['a', 'b', 'c']])
        self.assertIn('the after links form a cycle: a, b, c', str(raised.exception))

    def test_ordered_self_link_is_a_cycle(self):
        index = {'a': series.Series('a', 'p', 'push', ('a',))}
        with self.assertRaises(series.CycleError):
            series.ordered(index)

    def test_cli_cycle(self):
        self.write_index(CLEAN.replace('  neutron-opts-014:\n    status: push\n',
                                       '  neutron-opts-014:\n    status: push\n'
                                       '    after: [neutron-import-memory-007]\n'))
        code, lines, err = self.run_cli('list', '--ordered')
        self.assertEqual((code, lines), (1, []))
        self.assertIn('the after links form a cycle: neutron-import-memory-007, neutron-opts-014', err)

    def test_after(self):
        self.assertEqual(self.run_cli('after', 'neutron-import-memory-007'),
                         (0, ['devstack-privsep-memory-001', 'neutron-opts-014'], ''))
        self.assertEqual(self.run_cli('after', 'kolla'), (0, [], ''))
        self.assertEqual(self.run_cli('after', 'kolla-deployer/'), (0, ['kolla'], ''))

    def test_project(self):
        self.assertEqual(self.run_cli('project', 'kolla-deployer'), (0, ['kolla'], ''))
        self.assertEqual(self.run_cli('project', 'neutron-opts-014'), (0, ['neutron'], ''))

    def test_unknown_series(self):
        for command in ('after', 'project'):
            code, lines, err = self.run_cli(command, 'nova-nothing')
            self.assertEqual((code, lines), (1, []))
            self.assertIn('nova-nothing is not a series in series.yaml', err)

    def test_missing_index(self):
        os.unlink(os.path.join(self.root, 'series.yaml'))
        code, lines, err = self.run_cli('list')
        self.assertEqual((code, lines), (1, []))
        self.assertIn('series.yaml does not exist', err)

    def test_run_as_a_script(self):
        result = subprocess.run([sys.executable, os.path.join(HERE, 'series.py'), '--root', self.root,
                                 'list', '--status', 'hold'], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, 'kolla-deployer\n', ''))


if __name__ == '__main__':
    unittest.main()
