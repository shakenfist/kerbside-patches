#!/usr/bin/env python3
"""Tests for check-ansible-template-vars.py.

The checks run against small fixture trees built in a temporary directory, so
none of them need an assembled src/. One test pins the case this tool was
written for: a variable referenced only inside a branch no deployment reaches
is still reported.

Usage:
    ./test-check-ansible-template-vars.py
"""

import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = SourceFileLoader('check_ansible_template_vars',
                           os.path.join(HERE, 'check-ansible-template-vars.py'))
catv = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(catv)


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


class TreeFixture:
    """A source tree with one role, one template and one defaults file."""

    def __init__(self, tmpdir, template, defaults='---\ndefined_name: 1\n'):
        self.tree = os.path.join(tmpdir, 'kolla-ansible')
        self.relative = os.path.join('ansible', 'roles', 'demo', 'templates', 'demo.conf.j2')
        write(os.path.join(self.tree, self.relative), template)
        write(os.path.join(self.tree, 'ansible', 'roles', 'demo', 'defaults', 'main.yml'), defaults)
        write(os.path.join(self.tree, 'etc', 'kolla', 'passwords.yml'), '---\ndemo_password: secret\n')

    def check(self, baseline=frozenset()):
        return catv.check(self.tree, [self.relative], set(baseline))


class CheckTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_defined_variable_passes(self):
        fixture = TreeFixture(self.tmpdir, 'value = {{ defined_name }}\n')
        findings, stale = fixture.check()
        self.assertEqual([], findings)
        self.assertEqual([], stale)

    def test_undefined_variable_is_reported(self):
        fixture = TreeFixture(self.tmpdir, 'value = {{ absent_name }}\n')
        findings, _stale = fixture.check()
        self.assertEqual([(fixture.relative, 'absent_name')], findings)

    def test_undefined_variable_in_unreachable_branch_is_reported(self):
        """The bug this tool exists for: a reference behind a branch nothing takes."""
        template = ('{% if defined_name == "never" %}\n'
                    'workers = {{ absent_name }}\n'
                    '{% endif %}\n')
        fixture = TreeFixture(self.tmpdir, template)
        findings, _stale = fixture.check()
        self.assertEqual([(fixture.relative, 'absent_name')], findings)

    def test_password_file_counts_as_a_definition(self):
        fixture = TreeFixture(self.tmpdir, 'password = {{ demo_password }}\n')
        self.assertEqual([], fixture.check()[0])

    def test_vars_block_and_set_fact_count_as_definitions(self):
        fixture = TreeFixture(self.tmpdir, 'a = {{ block_var }}\nb = {{ fact_var }}\n')
        write(os.path.join(fixture.tree, 'ansible', 'roles', 'demo', 'tasks', 'main.yml'),
              '---\n'
              '- name: Render\n'
              '  vars:\n'
              '    block_var: 1\n'
              '  ansible.builtin.set_fact:\n'
              '    fact_var: 2\n')
        self.assertEqual([], fixture.check()[0])

    def test_registered_result_counts_as_a_definition(self):
        fixture = TreeFixture(self.tmpdir, 'a = {{ probe_result }}\n')
        write(os.path.join(fixture.tree, 'ansible', 'roles', 'demo', 'tasks', 'main.yml'),
              '---\n- name: Probe\n  ansible.builtin.command: true\n  register: probe_result\n')
        self.assertEqual([], fixture.check()[0])

    def test_magic_variables_need_no_definition(self):
        fixture = TreeFixture(self.tmpdir, 'host = {{ inventory_hostname }}\n')
        self.assertEqual([], fixture.check()[0])

    def test_unknown_filters_do_not_break_parsing(self):
        """Ansible's filters are not loaded here, and must not look like errors."""
        fixture = TreeFixture(self.tmpdir,
                              'a = {{ defined_name | put_address_in_context("url") }}\n'
                              'b = {{ defined_name | kolla_url("https", 443) }}\n')
        self.assertEqual([], fixture.check()[0])

    def test_unknown_tests_do_not_break_parsing(self):
        fixture = TreeFixture(self.tmpdir,
                              '{% if defined_name is service_enabled_and_mapped_to_host %}\n'
                              'a = 1\n{% endif %}\n')
        self.assertEqual([], fixture.check()[0])

    def test_template_local_set_is_not_a_requirement(self):
        template = ('{% if defined_name %}\n'
                    '{%   set local = "a" %}\n'
                    '{% else %}\n'
                    '{%   set local = "b" %}\n'
                    '{% endif %}\n'
                    'value = {{ local }}\n')
        fixture = TreeFixture(self.tmpdir, template)
        self.assertEqual([], fixture.check()[0])

    def test_default_filter_marks_a_name_optional(self):
        fixture = TreeFixture(self.tmpdir, 'value = {{ absent_name | default("x") }}\n')
        self.assertEqual([], fixture.check()[0])

    def test_is_defined_marks_a_name_optional(self):
        template = ('{% if absent_name is defined %}\n'
                    'value = {{ absent_name }}\n'
                    '{% endif %}\n')
        fixture = TreeFixture(self.tmpdir, template)
        self.assertEqual([], fixture.check()[0])

    def test_baseline_suppresses_a_finding(self):
        fixture = TreeFixture(self.tmpdir, 'value = {{ absent_name }}\n')
        findings, stale = fixture.check(baseline={'absent_name'})
        self.assertEqual([], findings)
        self.assertEqual([], stale)

    def test_baseline_entry_that_is_now_defined_is_stale(self):
        fixture = TreeFixture(self.tmpdir, 'value = {{ defined_name }}\n')
        _findings, stale = fixture.check(baseline={'defined_name'})
        self.assertEqual(['defined_name'], stale)

    def test_unparseable_template_is_reported(self):
        fixture = TreeFixture(self.tmpdir, '{% if unclosed %}\n')
        findings, _stale = fixture.check()
        self.assertEqual(1, len(findings))
        self.assertIn('does not parse', findings[0][1])


class SelectionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_patched_templates_reads_patch_targets(self):
        write(os.path.join(self.tmpdir, 'patch001-demo.patch'),
              'diff --git a/ansible/roles/demo/templates/a.j2 b/ansible/roles/demo/templates/a.j2\n'
              '--- a/ansible/roles/demo/templates/a.j2\n'
              '+++ b/ansible/roles/demo/templates/a.j2\n'
              '@@ -0,0 +1 @@\n'
              '+hello\n'
              '--- a/ansible/roles/demo/tasks/main.yml\n'
              '+++ b/ansible/roles/demo/tasks/main.yml\n')
        self.assertEqual({'ansible/roles/demo/templates/a.j2'},
                         catv.patched_templates(self.tmpdir))

    def test_patched_templates_ignores_a_missing_directory(self):
        self.assertEqual(set(), catv.patched_templates(os.path.join(self.tmpdir, 'absent')))

    def test_select_templates_drops_paths_absent_from_the_tree(self):
        """A patch for another project names templates this tree does not hold."""
        tree = os.path.join(self.tmpdir, 'kolla-ansible')
        write(os.path.join(tree, 'ansible', 'here.j2'), 'x\n')
        self.assertEqual(['ansible/here.j2'],
                         catv.select_templates(tree, {'ansible/here.j2', 'ansible/elsewhere.j2'}))

    def test_select_templates_excludes_zuul_job_templates(self):
        tree = os.path.join(self.tmpdir, 'kolla-ansible')
        write(os.path.join(tree, 'tests', 'templates', 'globals.j2'), 'x\n')
        write(os.path.join(tree, 'ansible', 'here.j2'), 'x\n')
        self.assertEqual(['ansible/here.j2'],
                         catv.select_templates(tree, {'ansible/here.j2',
                                                      'tests/templates/globals.j2'}))


class BaselineFileTestCase(unittest.TestCase):
    def test_real_baseline_parses_and_holds_only_bare_names(self):
        names = catv.load_baseline(os.path.join(HERE, 'ansible-template-vars-baseline.txt'))
        self.assertTrue(names, 'the baseline file should not be empty')
        for name in names:
            self.assertRegex(name, r'^[a-z][a-z0-9_]*$')

    def test_comments_and_blank_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'baseline.txt')
            write(path, '# a comment\n\nkept_name\nalso_kept  # trailing comment\n')
            self.assertEqual({'kept_name', 'also_kept'}, catv.load_baseline(path))

    def test_absent_baseline_is_empty(self):
        self.assertEqual(set(), catv.load_baseline('/nonexistent/baseline.txt'))


if __name__ == '__main__':
    unittest.main()
