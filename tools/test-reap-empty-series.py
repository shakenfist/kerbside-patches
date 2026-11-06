#!/usr/bin/env python3
"""Tests for reap-empty-series.py.

Each test builds a small repository in a temporary directory: series
directories holding ORDER and config.yaml, and a hand formatted series.yaml
naming them, with the comments and grouping the real index has. The
rewritten index is compared byte for byte, because keeping the comments and
the grouping is the whole reason the index is edited as text.

Usage:
    ./test-reap-empty-series.py
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

_loader = SourceFileLoader('reap_empty_series', os.path.join(HERE, 'reap-empty-series.py'))
reap = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(reap)

# A hand formatted index for the tree make_tree() builds: a header comment, a
# pinned series with no patches, a project with one series, a project whose
# series are grouped by comments, and after links written in flow style.
INDEX = """\
# The patch series in this repository. tools/series.py documents the format.
#
# There is more than one "patch epic" expressed here.

ansible-collection-kolla:
  ansible-collection-kolla:
    status: carry
    pin: true

devstack:
  devstack-privsep-memory-001:
    description: Fix memory usage measurements
    status: carry

kolla:
  kolla:
    status: carry

  # Features
  kolla-deployer:
    description: Containerized kolla-ansible deployer
    status: push
    after: [kolla]

  # Fixes
  kolla-magnum-setuptools-pin:
    status: push

neutron:
  neutron-import-memory-007:
    status: push
    after: [devstack-privsep-memory-001, neutron-opts-014]
  neutron-opts-014:
    status: push
    after: [devstack-privsep-memory-001]
"""

DIRECTORIES = {
    'ansible-collection-kolla': 'ansible-collection-kolla',
    'devstack-privsep-memory-001': 'devstack',
    'kolla': 'kolla',
    'kolla-deployer': 'kolla',
    'kolla-magnum-setuptools-pin': 'kolla',
    'neutron-import-memory-007': 'neutron',
    'neutron-opts-014': 'neutron',
}

# The pinned series is meant to carry nothing.
EMPTY = ('ansible-collection-kolla',)


class Repository(unittest.TestCase):
    """A test case with a fixture repository in self.root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.make_tree()

    def make_series(self, name, directory, patches=('patch001',)):
        os.makedirs(os.path.join(self.root, name), exist_ok=True)
        with open(os.path.join(self.root, name, 'config.yaml'), 'w') as f:
            f.write(f'repo: https://opendev.org/openstack/{directory}\nsource_branch: master\n'
                    f'destination_branch: master-patches\ndirectory: {directory}\n')
        with open(os.path.join(self.root, name, 'ORDER'), 'w') as f:
            f.write(''.join(f'../_patches/{patch}-{name}.patch\n' for patch in patches))

    def make_tree(self, index=INDEX):
        for name, directory in DIRECTORIES.items():
            self.make_series(name, directory, patches=() if name in EMPTY else ('patch001',))
        self.write_index(index)

    def write_index(self, text):
        with open(os.path.join(self.root, 'series.yaml'), 'w') as f:
            f.write(text)

    def read_index(self):
        with open(os.path.join(self.root, 'series.yaml')) as f:
            return f.read()

    def empty(self, *names, keep_comment=False):
        """Leave *names* with an ORDER that lists no patches."""
        for name in names:
            with open(os.path.join(self.root, name, 'ORDER'), 'w') as f:
                f.write('# every patch merged upstream\n\n' if keep_comment else '')

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = reap.main(['--root', self.root, *argv])
        return code, out.getvalue()


##############################################################################
# Finding the series with nothing left                                       #
##############################################################################

class TestEmptySeries(Repository):

    def found(self):
        return reap.empty_series(series.load(self.root), self.root)

    def test_a_tree_with_patches_everywhere_has_nothing_to_reap(self):
        self.assertEqual(self.found(), [])

    def test_an_empty_order_is_found(self):
        self.empty('devstack-privsep-memory-001')
        self.assertEqual(self.found(), ['devstack-privsep-memory-001'])

    def test_an_order_of_comments_and_blank_lines_is_empty(self):
        self.empty('devstack-privsep-memory-001', keep_comment=True)
        self.assertEqual(self.found(), ['devstack-privsep-memory-001'])

    def test_a_pinned_series_is_never_reaped(self):
        self.assertEqual(series.order_patches(self.root, 'ansible-collection-kolla'), [])
        self.assertEqual(self.found(), [])

    def test_a_series_with_no_directory_is_left_to_the_index_check(self):
        for name in ('ORDER', 'config.yaml'):
            os.unlink(os.path.join(self.root, 'devstack-privsep-memory-001', name))
        os.rmdir(os.path.join(self.root, 'devstack-privsep-memory-001'))
        self.assertEqual(self.found(), [])

    def test_several_are_returned_sorted(self):
        self.empty('neutron-opts-014', 'devstack-privsep-memory-001')
        self.assertEqual(self.found(), ['devstack-privsep-memory-001', 'neutron-opts-014'])

    def test_waiting_on_skips_the_series_being_reaped(self):
        index = series.load(self.root)
        self.assertEqual(reap.waiting_on(index, 'devstack-privsep-memory-001'),
                         ['neutron-import-memory-007', 'neutron-opts-014'])
        self.assertEqual(reap.waiting_on(index, 'devstack-privsep-memory-001',
                                         reaped=['devstack-privsep-memory-001', 'neutron-opts-014']),
                         ['neutron-import-memory-007'])


##############################################################################
# Rewriting the index                                                        #
##############################################################################

class TestRemoveEntries(Repository):

    def test_the_only_series_of_a_project_takes_the_project_with_it(self):
        self.assertEqual(reap.remove_entries(INDEX, ['devstack-privsep-memory-001']), """\
# The patch series in this repository. tools/series.py documents the format.
#
# There is more than one "patch epic" expressed here.

ansible-collection-kolla:
  ansible-collection-kolla:
    status: carry
    pin: true

kolla:
  kolla:
    status: carry

  # Features
  kolla-deployer:
    description: Containerized kolla-ansible deployer
    status: push
    after: [kolla]

  # Fixes
  kolla-magnum-setuptools-pin:
    status: push

neutron:
  neutron-import-memory-007:
    status: push
    after: [neutron-opts-014]
  neutron-opts-014:
    status: push
""")

    def test_a_project_with_other_series_survives(self):
        rewritten = reap.remove_entries(INDEX, ['kolla'])
        self.assertIn('kolla:\n  # Features\n  kolla-deployer:\n', rewritten)
        self.assertNotIn('after: [kolla]', rewritten)
        self.assertNotIn('  kolla:\n', rewritten)

    def test_the_last_series_of_a_group_takes_its_heading_with_it(self):
        rewritten = reap.remove_entries(INDEX, ['kolla-deployer'])
        self.assertNotIn('# Features', rewritten)
        self.assertIn('  # Fixes\n  kolla-magnum-setuptools-pin:\n', rewritten)
        self.assertIn('  kolla:\n    status: carry\n\n  # Fixes\n', rewritten)

    def test_an_after_with_other_series_keeps_them(self):
        rewritten = reap.remove_entries(INDEX, ['neutron-opts-014'])
        self.assertEqual(rewritten.count('    after: [devstack-privsep-memory-001]\n'), 1)
        self.assertNotIn('neutron-opts-014', rewritten)

    def test_the_header_comment_survives_an_empty_first_project(self):
        rewritten = reap.remove_entries(INDEX, ['ansible-collection-kolla'])
        self.assertTrue(rewritten.startswith('# The patch series in this repository.'))
        self.assertNotIn('ansible-collection-kolla', rewritten)
        self.assertIn('\n\ndevstack:\n', rewritten)

    def test_the_file_ends_in_exactly_one_newline(self):
        for names in (['neutron-import-memory-007'], ['neutron-import-memory-007', 'neutron-opts-014']):
            rewritten = reap.remove_entries(INDEX, names)
            self.assertTrue(rewritten.endswith('\n'))
            self.assertFalse(rewritten.endswith('\n\n'), names)

    def test_a_block_style_after_is_refused_rather_than_mangled(self):
        index = INDEX.replace('    after: [devstack-privsep-memory-001, neutron-opts-014]\n',
                              '    after:\n      - devstack-privsep-memory-001\n      - neutron-opts-014\n')
        with self.assertRaises(reap.ReapError) as caught:
            reap.remove_entries(index, ['devstack-privsep-memory-001'])
        self.assertIn('by hand', str(caught.exception))
        # An untouched block style after is left alone.
        self.assertIn('      - devstack-privsep-memory-001\n', reap.remove_entries(index, ['kolla']))

    def test_check_rewrite_rejects_a_rewrite_that_kept_the_series(self):
        with self.assertRaises(reap.ReapError) as caught:
            reap.check_rewrite(INDEX, ['kolla'], series.load(self.root))
        self.assertIn('keep kolla', str(caught.exception))

    def test_check_rewrite_rejects_an_invalid_rewrite(self):
        # The entry is gone but an after still names it, which series.validate
        # catches and this must not write out.
        dangling = INDEX.replace('  kolla:\n    status: carry\n', '')
        with self.assertRaises(reap.ReapError) as caught:
            reap.check_rewrite(dangling, ['kolla'], series.load(self.root))
        self.assertIn('is invalid', str(caught.exception))
        self.assertIn('after names kolla', str(caught.exception))


##############################################################################
# Doing it                                                                   #
##############################################################################

class TestApply(Repository):

    def test_nothing_to_reap_says_so_and_changes_nothing(self):
        before = self.read_index()
        code, out = self.run_main('--apply')
        self.assertEqual(code, 0)
        self.assertIn('nothing to reap', out)
        self.assertEqual(self.read_index(), before)

    def test_reporting_exits_one_and_changes_nothing(self):
        self.empty('devstack-privsep-memory-001')
        before = self.read_index()
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn('devstack-privsep-memory-001 (devstack) has no patches left', out)
        self.assertIn('2 series wait on it in after', out)
        self.assertIn('--apply', out)
        self.assertEqual(self.read_index(), before)
        self.assertTrue(os.path.isdir(os.path.join(self.root, 'devstack-privsep-memory-001')))

    def test_list_prints_only_the_names(self):
        self.empty('devstack-privsep-memory-001', 'neutron-opts-014')
        code, out = self.run_main('--list')
        self.assertEqual((code, out), (0, 'devstack-privsep-memory-001\nneutron-opts-014\n'))

    def test_apply_deletes_the_directory_and_the_entry(self):
        self.empty('devstack-privsep-memory-001')
        code, out = self.run_main('--apply')
        self.assertEqual(code, 0)
        self.assertIn('Reaped 1 series', out)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'devstack-privsep-memory-001')))
        self.assertEqual(self.read_index(), reap.remove_entries(INDEX, ['devstack-privsep-memory-001']))
        self.assertEqual(sorted(series.load(self.root)), sorted(set(DIRECTORIES) - {'devstack-privsep-memory-001'}))

    def test_the_tree_is_still_consistent_afterwards(self):
        self.empty('devstack-privsep-memory-001', 'neutron-opts-014')
        self.assertEqual(self.run_main('--apply')[0], 0)
        index = series.load(self.root)
        self.assertEqual(index['neutron-import-memory-007'].after, ())
        for name in index:
            self.assertTrue(os.path.isfile(os.path.join(self.root, name, 'ORDER')), name)

    def test_a_refused_rewrite_leaves_everything_alone(self):
        self.write_index(INDEX.replace('    after: [devstack-privsep-memory-001, neutron-opts-014]\n',
                                       '    after:\n      - devstack-privsep-memory-001\n'
                                       '      - neutron-opts-014\n'))
        before = self.read_index()
        self.empty('devstack-privsep-memory-001')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self.run_main('--apply')
        self.assertEqual(code, 2)
        self.assertIn('by hand', err.getvalue())
        self.assertEqual(self.read_index(), before)
        self.assertTrue(os.path.isdir(os.path.join(self.root, 'devstack-privsep-memory-001')))

    def test_an_invalid_index_is_refused(self):
        self.write_index(INDEX.replace('    status: carry\n    pin: true\n', '    pin: maybe\n'))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self.run_main('--apply')
        self.assertEqual(code, 2)
        self.assertIn('pin must be true or false', err.getvalue())

    def test_run_as_a_script(self):
        self.empty('neutron-opts-014')
        result = subprocess.run([sys.executable, os.path.join(HERE, 'reap-empty-series.py'),
                                 '--root', self.root, '--list'], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, 'neutron-opts-014\n', ''))


if __name__ == '__main__':
    unittest.main()
