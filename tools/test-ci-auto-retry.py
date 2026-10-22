#!/usr/bin/env python3
"""Tests for ci-auto-retry.sh and how the workflow calls it.

The script decides whether a failed CI run failed for infrastructure reasons
and, if so, comments on the pull request and re-runs the failed jobs. Both
mistakes cost something -- a missed infrastructure failure leaves a red pull
request for a human, and a retried genuine failure burns a runner for
nothing -- so which way each case falls is tested rather than read off the
source. The script is run for real with `gh` replaced by a stub on PATH. The
stub answers `gh api` from fixture files, one per API path, holding what the
script's own --jq would have printed, and records every call with the token
it was made with.

The workflow half pins that the retry step runs the script with no
expressions inside `run:`, and that the job checks out the default branch
rather than the pull request.

Usage:
    ./test-ci-auto-retry.py
"""

import os
import shutil
import subprocess
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'ci-auto-retry.sh')
WORKFLOW = os.path.join(os.path.dirname(HERE), '.github', 'workflows', 'auto-retry-infra-failures.yml')

REPO = 'shakenfist/kerbside-patches'
RUN_ID = '38971535327'
RUN_URL = 'https://github.com/%s/actions/runs/%s' % (REPO, RUN_ID)
WORKFLOW_NAME = 'Build test clouds'
PR_NUMBER = '4321'
TOKEN = 'github-token-for-the-test'
JOB_ID = '117274276492'
JOB_NAME = 'Patch tests / test (debian-13)'

RECORD_END = '\x1e'
ARG_SEP = '\x1f'

# Records each call, then answers `gh api` from the fixture named for its
# API path (the argument which is neither a flag nor the --jq expression),
# with every / turned into _. A path with no
# fixture fails, as an API error would.
GH_STUB = """#!/bin/bash
{
    printf '%s' "${GH_TOKEN-}"
    for arg in "$@"; do
        printf '\\x1f%s' "$arg"
    done
    printf '\\x1e'
} >> "${STUB_LOG}/gh.log"

if [ "$1" = api ] && [ "$2" = --help ]; then
    cat "${STUB_LOG}/fx/help" 2>/dev/null
    exit 0
fi

if [ "$1" = api ]; then
    path=""
    shift
    while [ $# -gt 0 ]; do
        case "$1" in
            --jq) shift ;;
            -*) ;;
            *) path="$1" ;;
        esac
        shift
    done
    fixture="${STUB_LOG}/fx/${path//\\//_}"
    if [ -e "$fixture" ]; then
        cat "$fixture"
        exit 0
    fi
    echo "stub gh: no fixture for $path" >&2
    exit 1
fi

exit 0
"""

FILLER = 'x' * 98 + '\n'


def log_lines(total, at=None, text=None):
    """A job log of total 100 character lines, with text on line number at."""
    lines = [FILLER] * total
    if at is not None:
        lines[at - 1] = text + '\n'
    return ''.join(lines)


class CiAutoRetryScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.bin = os.path.join(self.tempdir, 'bin')
        self.log = os.path.join(self.tempdir, 'log')
        self.fixtures = os.path.join(self.log, 'fx')
        os.makedirs(self.bin)
        os.makedirs(self.fixtures)
        path = os.path.join(self.bin, 'gh')
        with open(path, 'w') as f:
            f.write(GH_STUB)
        os.chmod(path, 0o755)

        # A failed pull request run with one failed job and nothing readable
        # about it, until a test says otherwise.
        self.fixture('help', '      --allow-escape-sequences   Do not escape\n')
        self.fixture('repos/%s/actions/runs/%s' % (REPO, RUN_ID), PR_NUMBER + '\n')
        self.fixture('repos/%s/actions/runs/%s/jobs' % (REPO, RUN_ID), '%s|%s\n' % (JOB_ID, JOB_NAME))
        self.fixture('repos/%s/issues/%s/comments' % (REPO, PR_NUMBER), '')

    def fixture(self, api_path, content):
        with open(os.path.join(self.fixtures, api_path.replace('/', '_')), 'w') as f:
            f.write(content)

    def annotations(self, text, job_id=JOB_ID):
        self.fixture('repos/%s/check-runs/%s/annotations' % (REPO, job_id), text)

    def job_log(self, text, job_id=JOB_ID):
        self.fixture('repos/%s/actions/jobs/%s/logs' % (REPO, job_id), text)

    def calls(self):
        """Every gh call as (token, [args])."""
        path = os.path.join(self.log, 'gh.log')
        if not os.path.exists(path):
            return []
        with open(path) as f:
            records = f.read().split(RECORD_END)[:-1]
        calls = []
        for record in records:
            fields = record.split(ARG_SEP)
            calls.append((fields[0], fields[1:]))
        return calls

    def writes(self):
        return [args for _, args in self.calls() if args[:2] in (['pr', 'comment'], ['run', 'rerun'])]

    def run_script(self, *args, **overrides):
        environment = dict(os.environ)
        environment.update({
            'PATH': '%s:%s' % (self.bin, os.environ['PATH']),
            'STUB_LOG': self.log,
            'GH_TOKEN': TOKEN,
            'REPO': REPO,
            'RUN_ID': RUN_ID,
            'RUN_URL': RUN_URL,
            'WORKFLOW_NAME': WORKFLOW_NAME,
            'RUNNER_TEMP': self.tempdir,
        })
        environment.update(overrides)
        for name in [k for k, v in environment.items() if v is None]:
            del environment[name]
        return subprocess.run([SCRIPT] + list(args), capture_output=True, text=True, env=environment)

    def assert_retried(self, proc):
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        writes = self.writes()
        self.assertEqual(2, len(writes), writes)
        comment, rerun = writes
        self.assertEqual(['pr', 'comment', PR_NUMBER, '--repo', REPO, '--body'], comment[:-1])
        body = comment[-1]
        self.assertTrue(body.startswith('<!-- auto-retry-infra: %s -->\n' % RUN_ID), body)
        self.assertIn('- %s\n' % JOB_NAME, body)
        self.assertIn('**%s**' % WORKFLOW_NAME, body)
        self.assertIn('[View run](%s)' % RUN_URL, body)
        self.assertIn('**Auto-retry triggered for infrastructure failure**', body)
        self.assertEqual(['run', 'rerun', RUN_ID, '--failed', '--repo', REPO], rerun)
        for token, args in self.calls():
            self.assertEqual(TOKEN, token, args)

    def test_an_infrastructure_annotation_retries_the_failed_jobs(self):
        self.annotations('The self-hosted runner lost communication with the server.\n')
        self.assert_retried(self.run_script())

    def test_an_annotation_pattern_is_matched_without_regard_to_case(self):
        self.annotations('RUNNER IS OFFLINE\n')
        self.assert_retried(self.run_script())

    def test_a_pattern_found_only_in_the_log_retries_the_failed_jobs(self):
        self.annotations('Process completed with exit code 1.\n')
        self.job_log(log_lines(300, 250, 'No nodes remaining at scheduling stage'))
        proc = self.run_script()
        self.assert_retried(proc)
        self.assertIn('matching: No nodes remaining at scheduling stage', proc.stdout)

    def test_a_log_pattern_is_matched_without_regard_to_case(self):
        self.job_log(log_lines(50, 25, 'NO SPACE LEFT ON DEVICE'))
        self.assert_retried(self.run_script())

    def test_the_log_is_fetched_with_the_flag_when_gh_knows_it(self):
        self.job_log(log_lines(10, 5, 'Input/output error'))
        self.run_script()
        fetches = [a for _, a in self.calls() if a[-1].endswith('/logs')]
        self.assertEqual([['api', '--allow-escape-sequences', 'repos/%s/actions/jobs/%s/logs' % (REPO, JOB_ID)]],
                         fetches)

    def test_the_log_is_fetched_without_the_flag_when_gh_does_not_know_it(self):
        self.fixture('help', '')
        self.job_log(log_lines(10, 5, 'Input/output error'))
        proc = self.run_script()
        self.assert_retried(proc)
        fetches = [a for _, a in self.calls() if a[-1].endswith('/logs')]
        self.assertEqual([['api', 'repos/%s/actions/jobs/%s/logs' % (REPO, JOB_ID)]], fetches)

    def test_a_pattern_on_the_first_line_of_the_log_window_matches(self):
        # The window is the last 2000 lines of 100 characters, far more than
        # a pipe holds. A grep -q on the far end of a pipe would exit at this
        # match and make the writer fail, and under pipefail a match would
        # read as a miss.
        self.job_log(log_lines(2500, 501, 'runner process exited'))
        self.assert_retried(self.run_script())

    def test_a_pattern_on_the_last_line_of_the_log_matches(self):
        self.job_log(log_lines(2500, 2500, 'runner process exited'))
        self.assert_retried(self.run_script())

    def test_a_pattern_above_the_log_window_does_not_match(self):
        self.job_log(log_lines(2500, 500, 'runner process exited'))
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.writes())
        self.assertIn('No infrastructure failures detected', proc.stdout)

    def test_a_failure_with_no_pattern_writes_nothing(self):
        self.annotations('Process completed with exit code 1.\n')
        self.job_log(log_lines(100, 50, 'AssertionError: test_something failed'))
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.writes())

    def test_an_unreadable_log_is_reported_and_writes_nothing(self):
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('could not read job log: stub gh: no fixture', proc.stdout)
        self.assertEqual([], self.writes())

    def test_only_the_infrastructure_jobs_are_listed(self):
        self.fixture('repos/%s/actions/runs/%s/jobs' % (REPO, RUN_ID), '1|Lint\n%s|%s\n' % (JOB_ID, JOB_NAME))
        self.job_log('boring\n', job_id='1')
        self.annotations('runner is offline\n')
        self.assert_retried(self.run_script())

    def test_an_existing_marker_writes_nothing(self):
        self.annotations('runner is offline\n')
        self.fixture('repos/%s/issues/%s/comments' % (REPO, PR_NUMBER), '987654\n')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.writes())
        self.assertIn('Already retried run %s' % RUN_ID, proc.stdout)

    def test_a_run_with_no_pull_request_writes_nothing(self):
        self.fixture('repos/%s/actions/runs/%s' % (REPO, RUN_ID), '')
        self.annotations('runner is offline\n')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.writes())
        self.assertIn('Not a PR run', proc.stdout)

    def test_a_run_with_no_failed_jobs_writes_nothing(self):
        self.fixture('repos/%s/actions/runs/%s/jobs' % (REPO, RUN_ID), '')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.writes())
        self.assertIn('No failed jobs found', proc.stdout)

    def test_dry_run_writes_nothing_and_prints_both_planned_writes(self):
        self.annotations('runner is offline\n')
        proc = self.run_script('--dry-run')
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.writes())
        self.assertIn('DRY RUN: gh pr comment %s --repo %s --body <!-- auto-retry-infra: %s -->'
                      % (PR_NUMBER, REPO, RUN_ID), proc.stdout)
        self.assertIn('DRY RUN: gh run rerun %s --failed --repo %s' % (RUN_ID, REPO), proc.stdout)

    def test_dry_run_still_reads_everything(self):
        self.annotations('runner is offline\n')
        self.run_script('--dry-run')
        paths = [a for _, args in self.calls() if args[0] == 'api' for a in args if a.startswith('repos/')]
        self.assertIn('repos/%s/issues/%s/comments' % (REPO, PR_NUMBER), paths)
        self.assertIn('repos/%s/check-runs/%s/annotations' % (REPO, JOB_ID), paths)

    def test_dry_run_with_nothing_to_retry_prints_no_writes(self):
        proc = self.run_script('--dry-run')
        self.assertNotIn('DRY RUN', proc.stdout)

    def test_an_unknown_argument_fails_without_side_effects(self):
        proc = self.run_script('--force')
        self.assertEqual(2, proc.returncode)
        self.assertIn('Unknown argument: --force', proc.stderr)
        self.assertEqual([], self.calls())

    def test_missing_environment_variables_fail_without_side_effects(self):
        for name in ['GH_TOKEN', 'REPO', 'RUN_ID', 'RUN_URL', 'WORKFLOW_NAME']:
            proc = self.run_script(**{name: None})
            self.assertNotEqual(0, proc.returncode, name)
            self.assertIn('%s must be set' % name, proc.stderr)
        self.assertEqual([], self.calls())

    def test_the_script_works_without_runner_temp(self):
        self.annotations('runner is offline\n')
        self.job_log('nothing\n')
        proc = self.run_script(RUNNER_TEMP=None)
        self.assert_retried(proc)


class CiAutoRetryWorkflowTestCase(unittest.TestCase):
    def setUp(self):
        with open(WORKFLOW) as f:
            self.workflow = yaml.safe_load(f)
        self.job = self.workflow['jobs']['check-and-retry']

    def _step(self):
        steps = [s for s in self.job['steps'] if 'run' in s]
        self.assertEqual(1, len(steps))
        return steps[0]

    def test_the_retry_step_runs_the_script(self):
        self.assertEqual('tools/ci-auto-retry.sh', self._step()['run'])

    def test_nothing_is_interpolated_into_the_shell(self):
        for step in self.job['steps']:
            self.assertNotIn('${{', step.get('run', ''))

    def test_the_script_inputs_come_from_the_event_through_the_environment(self):
        env = self._step()['env']
        self.assertEqual('${{ secrets.GITHUB_TOKEN }}', env['GH_TOKEN'])
        self.assertEqual('${{ github.repository }}', env['REPO'])
        self.assertEqual('${{ github.event.workflow_run.id }}', env['RUN_ID'])
        self.assertEqual('${{ github.event.workflow_run.html_url }}', env['RUN_URL'])
        self.assertEqual('${{ github.event.workflow_run.name }}', env['WORKFLOW_NAME'])

    def test_the_job_checks_out_the_default_branch_before_running_the_script(self):
        steps = self.job['steps']
        checkouts = [i for i, s in enumerate(steps) if s.get('uses', '').startswith('actions/checkout@')]
        self.assertEqual(1, len(checkouts))
        # With no ref the checkout is the default branch, never the head of
        # the pull request whose run failed.
        self.assertNotIn('ref', steps[checkouts[0]].get('with') or {})
        self.assertLess(checkouts[0], steps.index(self._step()))

    def test_the_job_still_fires_on_a_failed_build_test_clouds_run(self):
        trigger = self.workflow[True] if True in self.workflow else self.workflow['on']
        self.assertEqual(['Build test clouds'], trigger['workflow_run']['workflows'])
        self.assertEqual(['completed'], trigger['workflow_run']['types'])
        self.assertEqual("github.event.workflow_run.conclusion == 'failure'", self.job['if'])


if __name__ == '__main__':
    unittest.main()
