#!/usr/bin/env python3
"""Tests for the attribution rules in patch_message and the tools that use them.

Covers patch_message's LLM Co-Authored-By detection and standard Assisted-By
form, check-llm-attribution.py --fix, and gerrit-pre-push-lint's errors.

Usage:
    ./test-patch-message-attribution.py
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from importlib import import_module

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

check = import_module('check-llm-attribution')
import patch_message  # noqa: E402  (after the sys.path fiddling above)


def load_linter():
    """Import gerrit-pre-push-lint, which has no .py extension."""
    loader = importlib.machinery.SourceFileLoader('gerrit_pre_push_lint', os.path.join(TOOLS, 'gerrit-pre-push-lint'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


linter = load_linter()

PATCH = """commit 57f69c143e51642546a2aea46b39c5b1bc0f4a34
Author: Michael Still <mikal@stillhq.com>
Date:   Thu Sep 17 18:51:45 2026 +1000

    Do a thing.
    %(trailers)s
    Change-Id: Id69511ea84bd9d4cb005c39da471fc2d576686ac

diff --git a/f b/f
--- a/f
+++ b/f
@@ -1 +1 @@
-Co-Authored-By: Claude Opus <noreply@anthropic.com>
+Assisted-By: Claude Opus (1M context) <noreply@anthropic.com>
"""


class TestDetection(unittest.TestCase):
    def matches(self, line):
        return patch_message.llm_co_authors(f'Subject\n\n{line}\nChange-Id: I1')

    def test_claude(self):
        line = 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'
        self.assertEqual([line], self.matches(line))

    def test_mixed_case_key_and_name(self):
        self.assertEqual(1, len(self.matches('co-authored-by: CLAUDE Opus <x@example.com>')))
        self.assertEqual(1, len(self.matches('CO-AUTHORED-BY: copilot <x@example.com>')))
        self.assertEqual(1, len(self.matches('Co-Authored-By: gpt-4 <x@example.com>')))

    def test_unstripped(self):
        line = 'Co-Authored-By: Copilot <c@github.com> '
        self.assertEqual([line], self.matches(line))

    def test_person(self):
        self.assertEqual([], self.matches('Co-Authored-By: Michal Nasiadka <mnasiadka@gmail.com>'))

    def test_noreply_person(self):
        self.assertEqual([], self.matches('Co-Authored-By: A Person <1234+a@users.noreply.github.com>'))

    def test_domain_under_other_name(self):
        self.assertEqual(1, len(self.matches('Co-Authored-By: Bot <noreply@anthropic.com>')))
        self.assertEqual(1, len(self.matches('Co-Authored-By: Bot <noreply@openai.com>')))

    def test_assisted_by_not_matched(self):
        self.assertEqual([], self.matches('Assisted-By: Claude Opus 5.5 <noreply@anthropic.com>'))


class TestStandardForm(unittest.TestCase):
    def standard(self, value):
        return patch_message.standard_assisted_by(value)

    def test_drops_email_and_context(self):
        self.assertEqual('Assisted-By: Claude Opus 5.5 (low effort)',
                         self.standard(' Claude Opus 5.5 (1M context, low effort) <noreply@anthropic.com>'))

    def test_effort_is_optional(self):
        self.assertEqual('Assisted-By: Claude Opus 4.5', self.standard(' Claude Opus 4.5 <noreply@anthropic.com>'))
        self.assertEqual('Assisted-By: Claude Code', self.standard(' Claude Code'))
        self.assertEqual('Assisted-By: Claude Opus 5', self.standard(' Claude Opus 5 (1M context)'))

    def test_other_settings_dropped(self):
        self.assertEqual('Assisted-By: Claude Opus 5.5 (high effort)',
                         self.standard(' Claude Opus 5.5 (1M context, High Effort, fast mode)'))

    def test_problems(self):
        message = ('Subject\n\nSigned-off-by: A <a@example.com>\n'
                   'Assisted-By: Claude Opus 5 (1M context, high effort) <noreply@anthropic.com>\n'
                   'Co-Authored-By: Claude Opus 5 (1M context, high effort) <noreply@anthropic.com>\n'
                   'Co-Authored-By: Michal Nasiadka <mnasiadka@gmail.com>')
        self.assertEqual([
            ('Assisted-By: Claude Opus 5 (1M context, high effort) <noreply@anthropic.com>',
             'Assisted-By: Claude Opus 5 (high effort)'),
            ('Co-Authored-By: Claude Opus 5 (1M context, high effort) <noreply@anthropic.com>', None),
        ], list(patch_message.attribution_problems(message)))

    def test_standard_is_clean(self):
        message = 'Subject\n\nAssisted-By: Claude Opus 5.5 (high effort)\nAssisted-By: Claude Code'
        self.assertEqual([], list(patch_message.attribution_problems(message)))

    def test_converts_without_assisted_by(self):
        message = 'Subject\n\nCo-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>'
        self.assertEqual([('Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>', 'Assisted-By: Claude Opus 4.5')],
                         list(patch_message.attribution_problems(message)))


class TestFix(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name, content):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def patch(self, trailers):
        return self.write('p.patch', PATCH % {'trailers': trailers})

    def read(self, path):
        with open(path) as f:
            return f.read()

    def run_check(self, path, fix=True):
        with contextlib.redirect_stdout(io.StringIO()):
            return check.process(path, fix)

    def test_existing_assisted_by_removes_line(self):
        path = self.patch('Assisted-By: Claude X (1M context, high effort) <noreply@anthropic.com>\n'
                          '    Co-Authored-By: Claude X (1M context, high effort) <noreply@anthropic.com>')
        self.assertEqual(2, self.run_check(path))
        message = patch_message.extract(path)
        self.assertEqual(1, message.count('Claude X'))
        self.assertIn('\nAssisted-By: Claude X (high effort)\n', message)
        self.assertEqual([], list(patch_message.attribution_problems(message)))

    def test_no_assisted_by_converts_in_place(self):
        path = self.patch('Signed-off-by: A <a@example.com>\n'
                          '    Co-Authored-By: Claude X (200K context, medium effort) <noreply@anthropic.com>')
        self.run_check(path)
        lines = patch_message.extract(path).split('\n')
        self.assertEqual('Signed-off-by: A <a@example.com>', lines[1])
        self.assertEqual('Assisted-By: Claude X (medium effort)', lines[2])
        self.assertTrue(lines[3].startswith('Change-Id'))

    def test_repeat_after_standardising_dropped(self):
        path = self.patch('Assisted-By: Claude X (1M context, low effort) <noreply@anthropic.com>\n'
                          '    Assisted-By: Claude X (low effort)')
        self.run_check(path)
        self.assertEqual(1, patch_message.extract(path).count('Assisted-By'))

    def test_duplicate_co_authors_keep_one_assisted_by(self):
        path = self.patch('Co-Authored-By: Claude X <noreply@anthropic.com>\n'
                          '    Co-Authored-By: Claude X <noreply@anthropic.com>')
        self.run_check(path)
        message = patch_message.extract(path)
        self.assertEqual(1, message.count('Assisted-By: Claude X'))
        self.assertNotIn('Co-Authored-By', message)

    def test_diff_body_untouched(self):
        path = self.patch('Co-Authored-By: Claude X <noreply@anthropic.com>')
        diff = self.read(path).split('diff --git', 1)[1]
        self.run_check(path)
        self.assertEqual(diff, self.read(path).split('diff --git', 1)[1])

    def test_indent_kept(self):
        path = self.patch('Co-Authored-By: Claude X <noreply@anthropic.com>')
        self.run_check(path)
        self.assertIn('\n    Assisted-By: Claude X\n', self.read(path))

    def test_report_only_does_not_edit(self):
        path = self.patch('Assisted-By: Claude X <noreply@anthropic.com>')
        before = self.read(path)
        self.assertEqual(1, self.run_check(path, fix=False))
        self.assertEqual(before, self.read(path))

    def test_clean_untouched(self):
        path = self.patch('Co-Authored-By: Michal Nasiadka <mnasiadka@gmail.com>\n'
                          '    Assisted-By: Claude Opus 5.5 (high effort)')
        before = self.read(path)
        self.assertEqual(0, self.run_check(path))
        self.assertEqual(before, self.read(path))

    def test_message_file(self):
        path = self.write('p.patch-message',
                          'Subject\n\nCo-Authored-By: Claude X <noreply@anthropic.com>\nChange-Id: I1')
        self.run_check(path)
        self.assertEqual('Subject\n\nAssisted-By: Claude X\nChange-Id: I1', self.read(path))

    def test_message_file_removal_leaves_no_blank_pair(self):
        path = self.write('p.patch-message', 'Subject\n\nAssisted-By: Claude X <a@b.c>\n'
                          'Co-Authored-By: Claude X <noreply@anthropic.com>')
        self.run_check(path)
        self.assertEqual('Subject\n\nAssisted-By: Claude X', self.read(path))


class TestLinter(unittest.TestCase):
    def errors(self, trailers):
        lint = linter.GerritLinter.__new__(linter.GerritLinter)
        lint.issues = []
        lint.project_name = 'nova'
        lint.check_commit_message(f'Do a thing.\n\nBody.\n\n{trailers}\nChange-Id: I1\n')
        return [i for i in lint.issues if i.severity == linter.Severity.ERROR]

    def test_co_author_converted(self):
        errors = self.errors('Co-Authored-By: Claude Opus 5.5 (1M context, high effort) <noreply@anthropic.com>')
        self.assertEqual(1, len(errors))
        self.assertEqual(5, errors[0].line)
        self.assertIn('reserves it for people', errors[0].message)
        self.assertIn('"Assisted-By: Claude Opus 5.5 (high effort)"', errors[0].suggestion)

    def test_co_author_removed(self):
        errors = self.errors('Assisted-By: Claude Opus 5.5 (high effort)\n'
                             'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
        self.assertEqual(1, len(errors))
        self.assertIn('Remove it', errors[0].suggestion)

    def test_assisted_by_form(self):
        errors = self.errors('Assisted-By: Claude Opus 5.5 (1M context, high effort) <noreply@anthropic.com>')
        self.assertEqual(1, len(errors))
        self.assertIn('standard form', errors[0].message)
        self.assertIn('"Assisted-By: Claude Opus 5.5 (high effort)"', errors[0].suggestion)

    def test_clean(self):
        self.assertEqual([], self.errors('Co-Authored-By: Michal Nasiadka <mnasiadka@gmail.com>\n'
                                         'Assisted-By: Claude Opus 5.5 (high effort)'))


if __name__ == '__main__':
    unittest.main()
