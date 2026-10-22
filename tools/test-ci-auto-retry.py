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

A merge queue run is recovered by putting its pull request back in the queue,
not by re-running it, and that is done with a second, more powerful token. So
the merge queue cases also pin who is re-queued and at which head, and check
after every run that nothing was re-run or merged and that the second token
was used for the re-queue alone.

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

REQUEUE_TOKEN = 'requeue-token-for-the-test'

# A merge queue entry for PR_NUMBER. The queue branch is named for the base
# it was built on; the commit the run tested merges PR_HEAD into that base.
BASE_SHA = '3aa55390fc' + '0' * 30
MERGE_SHA = 'b3dc0c65da' + '1' * 30
PR_HEAD = '02d4959dd9' + '2' * 30
MOVED_HEAD = 'feedface' * 5
QUEUE_BRANCH = 'gh-readonly-queue/develop/pr-%s-%s' % (PR_NUMBER, BASE_SHA)
PR_NODE_ID = 'PR_kwDOtest4321'
CAN_MERGE_ID = '117274299999'
OTHER_JOB_ID = '117274288888'
OTHER_JOB_NAME = 'Images for 2025.2 on debian 13'

RECORD_END = '\x1e'
ARG_SEP = '\x1f'

# Records each call, then answers `gh api` from the fixture named for its
# API path (the argument which is neither a flag nor the value of --jq or a
# field), with every / turned into _. A GraphQL mutation is answered from
# graphql-mutation rather than graphql. A path with no fixture fails, as an
# API error would.
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
    all="$*"
    shift
    while [ $# -gt 0 ]; do
        case "$1" in
            --jq|-f|-F) shift ;;
            -*) ;;
            *) path="$1" ;;
        esac
        shift
    done
    if [ "$path" = graphql ] && [[ "$all" == *mutation* ]]; then
        path=graphql-mutation
    fi
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


class GhStubTestCase(unittest.TestCase):
    """The script run for real against a stub gh, starting from a failed pull request run."""

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
            # Present but never to be used on the pull request path, which
            # every call being made with TOKEN checks.
            'REQUEUE_TOKEN': REQUEUE_TOKEN,
            'EVENT': None,
            'CONCLUSION': None,
            'HEAD_BRANCH': None,
            'HEAD_SHA': None,
        })
        environment.update(overrides)
        for name in [k for k, v in environment.items() if v is None]:
            del environment[name]
        return subprocess.run([SCRIPT] + list(args), capture_output=True, text=True, env=environment)


class CiAutoRetryScriptTestCase(GhStubTestCase):
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

    def test_a_pull_request_event_takes_the_pull_request_path(self):
        self.annotations('runner is offline\n')
        self.assert_retried(self.run_script(EVENT='pull_request', CONCLUSION='failure',
                                            HEAD_BRANCH='some-branch', HEAD_SHA=PR_HEAD))


class CiAutoRetryMergeGroupTestCase(GhStubTestCase):
    """A merge queue run, whose recovery is a re-queue or a comment, never a re-run.

    Every run here is followed by the same safety checks on the stub's log:
    nothing re-runs or merges, and REQUEUE_TOKEN is used for the re-queue and
    for nothing else.
    """

    def setUp(self):
        super().setUp()
        # An entry for an open pull request, still at the head it was tested
        # at and out of the queue, whose run failed one infrastructure job
        # (once a test supplies the annotation) and therefore "Can merge".
        self.fixture('repos/%s/commits/%s' % (REPO, MERGE_SHA), PR_HEAD + '\n')
        self.pull_request()
        self.failed_jobs((JOB_ID, JOB_NAME, 'Setup test environment'),
                         (CAN_MERGE_ID, 'Can merge', 'Transform outcomes'))
        self.fixture('graphql-mutation', '{"data":{"enqueuePullRequest":{"mergeQueueEntry":{"position":1}}}}\n')

    def pull_request(self, state='OPEN', head=PR_HEAD, queued='false', removed_at='2026-10-22T09:00:00Z',
                     reason='CI failure'):
        self.fixture('graphql', '|'.join([PR_NODE_ID, state, head, queued, removed_at, reason]) + '\n')

    def failed_jobs(self, *jobs):
        self.fixture('repos/%s/actions/runs/%s/jobs' % (REPO, RUN_ID), ''.join('%s|%s|%s\n' % j for j in jobs))

    def markers(self, *markers):
        self.fixture('repos/%s/issues/%s/comments' % (REPO, PR_NUMBER), ''.join(m + '\n' for m in markers))

    def run_script(self, *args, **overrides):
        environment = {
            'EVENT': 'merge_group',
            'CONCLUSION': 'failure',
            'HEAD_BRANCH': QUEUE_BRANCH,
            'HEAD_SHA': MERGE_SHA,
        }
        environment.update(overrides)
        proc = super().run_script(*args, **environment)
        self.assert_safe(proc)
        return proc

    def is_enqueue(self, args):
        return args[:2] == ['api', 'graphql'] and any('enqueuePullRequest' in a for a in args)

    def enqueues(self):
        return [args for _, args in self.calls() if self.is_enqueue(args)]

    def assert_safe(self, proc):
        output = proc.stdout + proc.stderr
        self.assertNotIn(REQUEUE_TOKEN, output)
        self.assertLessEqual(len(self.enqueues()), 1)
        for token, args in self.calls():
            self.assertNotIn(args[:2], (['run', 'rerun'], ['pr', 'merge']), args)
            joined = ' '.join(args)
            self.assertNotIn('mergePullRequest', joined)
            self.assertNotIn('jump', joined)
            self.assertEqual(REQUEUE_TOKEN if self.is_enqueue(args) else TOKEN, token, args)

    def assert_no_writes(self, proc):
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.writes())
        self.assertEqual([], self.enqueues())

    def comment_body(self):
        writes = self.writes()
        self.assertEqual(1, len(writes), writes)
        self.assertEqual(['pr', 'comment', PR_NUMBER, '--repo', REPO, '--body'], writes[0][:-1])
        return writes[0][-1]

    def assert_ejection_comment(self, proc):
        """One comment for the run, and no re-queue."""
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.enqueues())
        body = self.comment_body()
        self.assertTrue(body.startswith('<!-- auto-retry-merge-group: %s -->\n' % RUN_ID), body)
        self.assertNotIn('auto-requeue-infra', body)
        self.assertIn('**Ejected from the merge queue**', body)
        self.assertIn('[View run](%s)' % RUN_URL, body)
        self.assertIn('`merge-ci-triage`', body)
        self.assertNotIn('merge-ci-triage](', body)
        self.assertIn('`tier=full`', body)
        return body

    def test_an_infrastructure_ejection_is_requeued_at_the_tested_head(self):
        self.annotations('The self-hosted runner lost communication with the server.\n')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        enqueues = self.enqueues()
        self.assertEqual(1, len(enqueues), self.calls())
        enqueue = enqueues[0]
        query = [a for a in enqueue if a.startswith('query=')]
        self.assertEqual(1, len(query), enqueue)
        self.assertIn('enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head})', query[0])
        self.assertIn('id=%s' % PR_NODE_ID, enqueue)
        # The head the merge group commit tested, not the base in the branch.
        self.assertIn('head=%s' % PR_HEAD, enqueue)

        body = self.comment_body()
        self.assertTrue(body.startswith('<!-- auto-requeue-infra: %s -->\n<!-- auto-retry-merge-group: %s -->\n'
                                        % (PR_HEAD, RUN_ID)), body)
        self.assertIn('**Re-queued after an infrastructure failure**', body)
        self.assertIn('- %s: failed in step "Setup test environment" (infrastructure: ' % JOB_NAME, body)
        self.assertNotIn('Can merge', body)
        self.assertIn('[View run](%s)' % RUN_URL, body)

        # The re-queue comes first, so a failed one leaves no marker.
        commands = [args for _, args in self.calls()]
        self.assertLess(commands.index(enqueue), commands.index(self.writes()[0]))

    def test_the_tested_head_comes_from_the_merge_commit_not_the_branch(self):
        self.annotations('runner is offline\n')
        proc = self.run_script()
        self.assertIn('PR #%s' % PR_NUMBER, proc.stdout)
        self.assertIn('Tested head: %s' % PR_HEAD, proc.stdout)
        commits = [args for _, args in self.calls() if 'repos/%s/commits/%s' % (REPO, MERGE_SHA) in args]
        self.assertEqual(1, len(commits), self.calls())
        self.assertIn('.parents[1].sha', commits[0][commits[0].index('--jq') + 1])

    def test_the_pull_request_is_read_in_one_graphql_query(self):
        self.annotations('runner is offline\n')
        self.run_script()
        reads = [args for _, args in self.calls() if args[:2] == ['api', 'graphql'] and not self.is_enqueue(args)]
        self.assertEqual(1, len(reads), reads)
        for field in ('id', 'state', 'headRefOid', 'isInMergeQueue', 'REMOVED_FROM_MERGE_QUEUE_EVENT',
                      'createdAt', 'reason'):
            self.assertIn(field, ' '.join(reads[0]))
        self.assertIn('number=%s' % PR_NUMBER, reads[0])

    def test_a_pattern_found_only_in_the_log_is_requeued(self):
        self.job_log(log_lines(2500, 1981, 'No nodes remaining at scheduling stage'))
        self.run_script()
        self.assertEqual(1, len(self.enqueues()))

    def test_a_failed_can_merge_is_not_classified(self):
        self.annotations('runner is offline\n')
        proc = self.run_script()
        self.assertEqual(1, len(self.enqueues()))
        self.assertNotIn(['api', 'repos/%s/check-runs/%s/annotations' % (REPO, CAN_MERGE_ID)],
                         [args[:2] for _, args in self.calls()])
        self.assertIn("Not classifying job 'Can merge'", proc.stdout)

    def test_can_merge_failing_alone_is_not_requeued(self):
        self.failed_jobs((CAN_MERGE_ID, 'Can merge', 'Transform outcomes'))
        body = self.assert_ejection_comment(self.run_script())
        self.assertIn('No job other than "Can merge" failed', body)

    def test_a_second_infrastructure_ejection_of_the_same_head_comments_instead(self):
        self.annotations('runner is offline\n')
        self.markers('<!-- auto-requeue-infra: %s -->' % PR_HEAD, '<!-- auto-retry-merge-group: 1234 -->')
        body = self.assert_ejection_comment(self.run_script())
        self.assertIn('second infrastructure failure of this head', body)
        self.assertIn('- %s: failed in step "Setup test environment" (infrastructure: ' % JOB_NAME, body)

    def test_a_requeue_of_another_head_does_not_count(self):
        self.annotations('runner is offline\n')
        self.markers('<!-- auto-requeue-infra: %s -->' % MOVED_HEAD)
        self.run_script()
        self.assertEqual(1, len(self.enqueues()))

    def test_a_mixed_failure_comments_and_is_not_requeued(self):
        self.failed_jobs((JOB_ID, JOB_NAME, 'Setup test environment'),
                         (OTHER_JOB_ID, OTHER_JOB_NAME, 'Build images'),
                         (CAN_MERGE_ID, 'Can merge', 'Transform outcomes'))
        self.annotations('runner is offline\n')
        self.job_log(log_lines(100, 50, 'ERROR: kolla-build failed'), job_id=OTHER_JOB_ID)
        proc = self.run_script()
        body = self.assert_ejection_comment(proc)
        self.assertIn('- %s: failed in step "Build images"\n' % OTHER_JOB_NAME, body)
        self.assertIn('- %s: failed in step "Setup test environment" (infrastructure: ' % JOB_NAME, body)
        self.assertIn('Some failed jobs matched an infrastructure pattern and some did not', body)
        self.assertIn('-> not infrastructure', proc.stdout)

    def test_a_genuine_failure_comments_and_is_not_requeued(self):
        self.job_log(log_lines(100, 50, 'AssertionError: test_something failed'))
        body = self.assert_ejection_comment(self.run_script())
        self.assertIn('No failed job matched an infrastructure pattern', body)
        self.assertIn('- %s: failed in step "Setup test environment"\n' % JOB_NAME, body)

    def test_a_job_with_no_failed_step_is_still_listed(self):
        self.failed_jobs((JOB_ID, JOB_NAME, ''))
        self.job_log('boring\n')
        body = self.assert_ejection_comment(self.run_script())
        self.assertIn('- %s: failed in step "(none recorded)"' % JOB_NAME, body)

    def test_a_head_that_moved_does_nothing(self):
        self.annotations('runner is offline\n')
        self.pull_request(head=MOVED_HEAD)
        proc = self.run_script()
        self.assert_no_writes(proc)
        self.assertIn('moved on from the tested head to %s' % MOVED_HEAD, proc.stdout)

    def test_a_closed_pull_request_does_nothing(self):
        self.annotations('runner is offline\n')
        self.pull_request(state='CLOSED')
        proc = self.run_script()
        self.assert_no_writes(proc)
        self.assertIn('is CLOSED, nothing to do', proc.stdout)

    def test_an_already_queued_pull_request_does_nothing(self):
        self.annotations('runner is offline\n')
        self.pull_request(queued='true')
        proc = self.run_script()
        self.assert_no_writes(proc)
        self.assertIn('already in the merge queue', proc.stdout)

    def test_a_genuine_failure_of_a_moved_head_does_nothing(self):
        self.job_log('boring\n')
        self.pull_request(head=MOVED_HEAD)
        self.assert_no_writes(self.run_script())

    def test_verdicts_are_logged_before_the_state_is_checked(self):
        self.annotations('runner is offline\n')
        self.pull_request(state='MERGED')
        proc = self.run_script('--dry-run')
        self.assert_no_writes(proc)
        self.assertIn('-> infrastructure, matching: runner is offline', proc.stdout)
        self.assertLess(proc.stdout.index('-> infrastructure'), proc.stdout.index('is MERGED, nothing to do'))

    def test_an_already_handled_run_does_nothing(self):
        self.annotations('runner is offline\n')
        self.markers('<!-- auto-retry-merge-group: %s -->' % RUN_ID)
        proc = self.run_script()
        self.assert_no_writes(proc)
        self.assertIn('Already handled run %s' % RUN_ID, proc.stdout)

    def test_an_unparseable_branch_warns_and_does_nothing(self):
        for branch in ('gh-readonly-queue/develop/some-branch', 'develop', 'gh-readonly-queue/develop/pr-12-XYZ'):
            proc = self.run_script(HEAD_BRANCH=branch)
            self.assert_no_writes(proc)
            self.assertIn('::warning::', proc.stdout)
        self.assertEqual([], self.calls())

    def test_a_missing_requeue_token_fails_and_posts_nothing(self):
        self.annotations('runner is offline\n')
        for token in (None, ''):
            proc = self.run_script(REQUEUE_TOKEN=token)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn('REQUEUE_TOKEN is not set', proc.stderr)
            self.assertEqual([], self.writes())
            self.assertEqual([], self.enqueues())

    def test_a_failed_requeue_fails_and_posts_nothing(self):
        self.annotations('runner is offline\n')
        os.unlink(os.path.join(self.fixtures, 'graphql-mutation'))
        proc = self.run_script()
        self.assertNotEqual(0, proc.returncode)
        self.assertIn('Re-queueing PR #%s failed' % PR_NUMBER, proc.stderr)
        self.assertEqual(1, len(self.enqueues()))
        self.assertEqual([], self.writes())

    def test_a_merge_commit_with_one_parent_fails(self):
        self.fixture('repos/%s/commits/%s' % (REPO, MERGE_SHA), '')
        self.annotations('runner is offline\n')
        proc = self.run_script()
        self.assertNotEqual(0, proc.returncode)
        self.assertIn('has no second parent', proc.stderr)
        self.assertEqual([], self.writes())
        self.assertEqual([], self.enqueues())

    def test_missing_merge_group_inputs_fail_without_side_effects(self):
        for name in ['CONCLUSION', 'HEAD_BRANCH', 'HEAD_SHA']:
            proc = self.run_script(**{name: None})
            self.assertEqual(2, proc.returncode, name)
            self.assertIn('%s must be set' % name, proc.stderr)
        self.assertEqual([], self.calls())

    def test_a_cancelled_run_does_nothing(self):
        self.annotations('runner is offline\n')
        self.assert_no_writes(self.run_script(CONCLUSION='cancelled'))

    def test_dry_run_prints_the_requeue_and_the_comment(self):
        self.annotations('runner is offline\n')
        proc = self.run_script('--dry-run')
        self.assert_no_writes(proc)
        self.assertIn('DRY RUN: GH_TOKEN=$REQUEUE_TOKEN gh api graphql -f query=mutation', proc.stdout)
        self.assertIn('-f id=%s -f head=%s' % (PR_NODE_ID, PR_HEAD), proc.stdout)
        self.assertIn('DRY RUN: gh pr comment %s --repo %s --body <!-- auto-requeue-infra: %s -->'
                      % (PR_NUMBER, REPO, PR_HEAD), proc.stdout)
        self.assertIn('-> infrastructure, matching: runner is offline', proc.stdout)

    def test_dry_run_prints_an_ejection_comment(self):
        self.job_log('boring\n')
        proc = self.run_script('--dry-run')
        self.assert_no_writes(proc)
        self.assertIn('DRY RUN: gh pr comment %s --repo %s --body <!-- auto-retry-merge-group: %s -->'
                      % (PR_NUMBER, REPO, RUN_ID), proc.stdout)

    # A merge queue run which passed.

    def green_run(self, started='2026-01-05T02:00:00Z', finished='2026-01-05T08:30:00Z'):
        self.fixture('repos/%s/actions/runs/%s' % (REPO, RUN_ID), '%s|%s\n' % (started, finished))
        # The reason is not documented, so it is quoted as given, separator
        # and all.
        self.pull_request(removed_at='2026-01-05T08:00:00Z', reason='Checks timed out | "Can merge"')

    def test_a_green_run_after_an_ejection_comments(self):
        self.green_run()
        proc = self.run_script(CONCLUSION='success')
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.enqueues())
        body = self.comment_body()
        self.assertTrue(body.startswith('<!-- auto-retry-merge-group: %s -->\n' % RUN_ID), body)
        self.assertIn('**Ejected from the merge queue while its run was still going**', body)
        self.assertIn('"Checks timed out | "Can merge""', body)
        self.assertIn('6h 30m', body)
        self.assertIn('360 minute', body)
        self.assertIn('[View run](%s)' % RUN_URL, body)
        # A green run has no failed jobs to classify.
        arguments = [a for _, args in self.calls() for a in args]
        self.assertNotIn('repos/%s/actions/runs/%s/jobs' % (REPO, RUN_ID), arguments)

    def test_a_green_run_that_merged_does_nothing(self):
        self.green_run()
        self.pull_request(state='MERGED')
        self.assert_no_writes(self.run_script(CONCLUSION='success'))

    def test_a_green_run_whose_pull_request_is_back_in_the_queue_does_nothing(self):
        self.green_run()
        self.pull_request(queued='true', removed_at='2026-01-05T08:00:00Z')
        self.assert_no_writes(self.run_script(CONCLUSION='success'))

    def test_a_green_run_whose_pull_request_moved_does_nothing(self):
        self.green_run()
        self.pull_request(head=MOVED_HEAD, removed_at='2026-01-05T08:00:00Z')
        self.assert_no_writes(self.run_script(CONCLUSION='success'))

    def test_a_green_run_with_an_older_ejection_does_nothing(self):
        self.green_run(started='2026-01-05T08:10:00Z', finished='2026-01-05T10:00:00Z')
        proc = self.run_script(CONCLUSION='success')
        self.assert_no_writes(proc)
        self.assertIn('before run %s started' % RUN_ID, proc.stdout)

    def test_a_green_run_never_ejected_does_nothing(self):
        self.green_run()
        self.pull_request(removed_at='', reason='')
        self.assert_no_writes(self.run_script(CONCLUSION='success'))

    def test_a_green_run_is_reported_once(self):
        self.green_run()
        self.markers('<!-- auto-retry-merge-group: %s -->' % RUN_ID)
        self.assert_no_writes(self.run_script(CONCLUSION='success'))


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
        self.assertEqual('${{ github.event.workflow_run.event }}', env['EVENT'])
        self.assertEqual('${{ github.event.workflow_run.conclusion }}', env['CONCLUSION'])
        self.assertEqual('${{ github.event.workflow_run.head_branch }}', env['HEAD_BRANCH'])
        self.assertEqual('${{ github.event.workflow_run.head_sha }}', env['HEAD_SHA'])

    def test_the_bot_token_reaches_the_script_as_requeue_token_alone(self):
        self.assertEqual('${{ secrets.DAILY_REBASE_TOKEN }}', self._step()['env']['REQUEUE_TOKEN'])
        with open(WORKFLOW) as f:
            self.assertEqual(1, f.read().count('secrets.DAILY_REBASE_TOKEN'))
        self.assertNotIn('env', self.job)
        self.assertNotIn('env', self.workflow)

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
        self.assertTrue(self.job['if'].startswith("github.event.workflow_run.conclusion == 'failure' || "),
                        self.job['if'])

    def test_the_job_also_fires_on_a_green_merge_queue_run(self):
        self.assertEqual("github.event.workflow_run.conclusion == 'failure' || "
                         "(github.event.workflow_run.conclusion == 'success' && "
                         "github.event.workflow_run.event == 'merge_group')", self.job['if'])


if __name__ == '__main__':
    unittest.main()
