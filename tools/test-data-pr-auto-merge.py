#!/usr/bin/env python3
"""Tests for data-pr-auto-merge.sh and the two scripts which call it.

The helper sets auto-merge on an automated data pull request. With a merge
queue on develop that enqueues it; without one it would merge it directly,
skipping every check, and it runs as a bot which can bypass the queue. So the
cases in which it must not act are tested one by one, and every case checks
that it never asks for a direct merge. The helper is run for real with `gh`
replaced by a stub on PATH which records every call and answers from fixture
files. `gh api` is answered from the fixture named for its API path, holding
what the helper's own --jq would have printed; with --paginate, a second page
fixture is appended, so a helper that stops paging sees only the first page.

create-data-pr.sh is run for real from a copy beside a stub helper, on each
path on which a pull request exists and on the one on which none does, which
also pins that it finds the helper beside itself rather than in tools/.

heal-data-prs.sh is run for real in a temporary clone of a temporary bare
origin, against a conflicted data branch. While it works, the working tree is
that branch merged with develop, whose tools/ need not hold the helper; here
neither does. Only the ref the healer started from does, so the pull request
is queued only if the healer calls the helper after it has returned there. It
must never do so in --dry-run.

Usage:
    ./test-data-pr-auto-merge.py
"""

import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, 'data-pr-auto-merge.sh')
CREATE = os.path.join(HERE, 'create-data-pr.sh')
HEAL = os.path.join(HERE, 'heal-data-prs.sh')

REPO = 'shakenfist/kerbside-patches'
PR_NUMBER = '1966'
TOKEN = 'token-for-the-test'
BRANCH = 'layers-data-20261023-0007-run39842433123'

RECORD_END = '\x1e'
ARG_SEP = '\x1f'

# Every argument any merge call must never carry, and the GraphQL mutation
# which merges directly.
FORBIDDEN = ('--admin', '--merge', '--squash', '--rebase', '-m', '-s', '-r', 'mergePullRequest')

# Records each call. `gh api` is answered from fx/api-<path>, every / in the
# path turned into _, plus fx/api-<path>.page2 when --paginate is given; a
# path with no fixture fails, as an API error would. `gh pr view <N> --json
# <fields>` is answered from fx/pr-view-<N>-<fields>, failing with none.
# `gh pr list` prints fx/pr-list. `gh pr create` records fx/pr-create-number
# as the pull request the list now shows and prints its URL, unless
# fx/pr-create-fails exists, in which case it fails either way (the create
# took effect only if there is a number). `gh pr merge` exits with
# fx/pr-merge-status, 0 by default.
GH_STUB = """#!/bin/bash
{
    for arg in "$@"; do
        printf '%s\\x1f' "$arg"
    done
    printf '\\x1e'
} >> "${STUB_LOG}/gh.log"
fx="${STUB_LOG}/fx"

if [ "$1" = api ]; then
    shift
    path=""
    paginate=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --jq|-f|-F) shift ;;
            --paginate) paginate=1 ;;
            -*) ;;
            *) path="$1" ;;
        esac
        shift
    done
    fixture="${fx}/api-${path//\\//_}"
    if [ ! -e "$fixture" ]; then
        echo "stub gh: no fixture for $path" >&2
        exit 1
    fi
    cat "$fixture"
    if [ "$paginate" = 1 ] && [ -e "${fixture}.page2" ]; then
        cat "${fixture}.page2"
    fi
    exit 0
fi

if [ "$1 $2" = 'pr view' ]; then
    number="$3"
    fields=""
    while [ $# -gt 0 ]; do
        if [ "$1" = --json ]; then
            fields="$2"
        fi
        shift
    done
    fixture="${fx}/pr-view-${number}-${fields}"
    if [ -e "$fixture" ]; then
        cat "$fixture"
        exit 0
    fi
    echo "stub gh: no pull request $number" >&2
    exit 1
fi

if [ "$1 $2" = 'pr list' ]; then
    cat "${fx}/pr-list" 2>/dev/null
    exit 0
fi

if [ "$1 $2" = 'pr create' ]; then
    if [ -e "${fx}/pr-create-number" ]; then
        cp "${fx}/pr-create-number" "${fx}/pr-list"
        created=$(cat "${fx}/pr-create-number")
    fi
    if [ -e "${fx}/pr-create-fails" ]; then
        echo 'HTTP 502: 502 Bad Gateway' >&2
        exit 1
    fi
    echo "https://github.com/shakenfist/kerbside-patches/pull/${created}"
    exit 0
fi

if [ "$1 $2" = 'pr merge' ]; then
    exit "$(cat "${fx}/pr-merge-status" 2>/dev/null || echo 0)"
fi

exit 0
"""

STUB_SLEEP = """#!/bin/bash
exit 0
"""

# Stands in for the helper beside the scripts which call it.
STUB_HELPER = """#!/bin/bash
printf '%s\\n' "$*" >> "${STUB_LOG}/helper.log"
exit "${HELPER_STATUS:-0}"
"""

STUB_VERIFY = """#!/bin/bash
printf '%s\\n' "$*" >> "${STUB_LOG}/verify.log"
"""


def write(path, content, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
    if mode:
        os.chmod(path, mode)


class GhStubTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.bin = os.path.join(self.tempdir, 'bin')
        self.log = os.path.join(self.tempdir, 'log')
        self.fixtures = os.path.join(self.log, 'fx')
        os.makedirs(self.fixtures)
        write(os.path.join(self.bin, 'gh'), GH_STUB, 0o755)
        write(os.path.join(self.bin, 'sleep'), STUB_SLEEP, 0o755)

    def fixture(self, name, content):
        write(os.path.join(self.fixtures, name.replace('/', '_')), content)

    def api(self, path, content):
        self.fixture('api-' + path, content)

    def environment(self, **overrides):
        environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        environment.update({
            'PATH': '%s:%s' % (self.bin, os.environ['PATH']),
            'STUB_LOG': self.log,
            'GH_TOKEN': TOKEN,
            'GITHUB_REPOSITORY': REPO,
        })
        environment.update(overrides)
        for name in [k for k, v in environment.items() if v is None]:
            del environment[name]
        return environment

    def calls(self):
        """Every gh call, as a list of its arguments."""
        path = os.path.join(self.log, 'gh.log')
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [record.split(ARG_SEP)[:-1] for record in f.read().split(RECORD_END)[:-1]]

    def merges(self):
        return [args for args in self.calls() if args[:2] == ['pr', 'merge']]

    def read_log(self, name):
        path = os.path.join(self.log, name)
        if not os.path.exists(path):
            return ''
        with open(path) as f:
            return f.read()

    def assert_never_merged_directly(self):
        for args in self.calls():
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, args, args)
                if forbidden.startswith('--') or forbidden == 'mergePullRequest':
                    self.assertFalse([a for a in args if forbidden in a], args)


class DataPrAutoMergeTestCase(GhStubTestCase):
    """The helper, starting from a data pull request it should queue."""

    def setUp(self):
        super().setUp()
        self.api('repos/%s/rules/branches/develop' % REPO, 'pull_request\nmerge_queue\nrequired_status_checks\n')
        self.fixture('pr-view-%s-baseRefName,state' % PR_NUMBER, 'develop OPEN\n')
        self.files(['data/layers/one.jsonl', 'data/layers/two.jsonl'], ['data/layers/three.jsonl'])

    def files(self, first, second=None):
        """The pull request's changed files, as two pages."""
        path = 'repos/%s/pulls/%s/files' % (REPO, PR_NUMBER)
        self.api(path, ''.join(name + '\n' for name in first))
        if second is not None:
            self.api(path + '.page2', ''.join(name + '\n' for name in second))

    def run_helper(self, *args, **overrides):
        args = args or (PR_NUMBER,)
        proc = subprocess.run([HELPER] + list(args), capture_output=True, text=True,
                              env=self.environment(**overrides), timeout=60)
        self.assert_never_merged_directly()
        return proc

    def assert_refused(self, proc, reason):
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.merges())
        self.assertIn('Not queueing PR #%s: ' % PR_NUMBER, proc.stdout)
        self.assertIn(reason, proc.stdout)

    def test_an_all_data_pull_request_is_queued(self):
        proc = self.run_helper()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([['pr', 'merge', PR_NUMBER, '--repo', REPO, '--auto']], self.merges())
        self.assertIn('Set auto-merge on PR #%s' % PR_NUMBER, proc.stdout)

    def test_the_checks_are_made_in_order_and_the_files_are_paged(self):
        self.run_helper()
        self.assertEqual([
            ['api', 'repos/%s/rules/branches/develop' % REPO, '--jq', '.[].type'],
            ['pr', 'view', PR_NUMBER, '--repo', REPO, '--json', 'baseRefName,state', '--jq',
             '"\\(.baseRefName) \\(.state)"'],
            ['api', '--paginate', 'repos/%s/pulls/%s/files' % (REPO, PR_NUMBER), '--jq', '.[].filename'],
            ['pr', 'merge', PR_NUMBER, '--repo', REPO, '--auto'],
        ], self.calls())

    def test_the_repository_defaults_to_kerbside_patches(self):
        proc = self.run_helper(GITHUB_REPOSITORY=None)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(self.merges()))

    def test_another_repository_is_used_throughout(self):
        other = 'someone/else'
        self.api('repos/%s/rules/branches/develop' % other, 'merge_queue\n')
        self.fixture('pr-view-%s-baseRefName,state' % PR_NUMBER, 'develop OPEN\n')
        self.api('repos/%s/pulls/%s/files' % (other, PR_NUMBER), 'data/x.jsonl\n')
        proc = self.run_helper(GITHUB_REPOSITORY=other)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([['pr', 'merge', PR_NUMBER, '--repo', other, '--auto']], self.merges())

    def test_no_merge_queue_means_no_merge(self):
        self.api('repos/%s/rules/branches/develop' % REPO, 'pull_request\nrequired_status_checks\n')
        proc = self.run_helper()
        self.assert_refused(proc, 'develop has no merge queue')
        self.assertEqual(1, len(self.calls()))

    def test_no_rules_at_all_means_no_merge(self):
        self.api('repos/%s/rules/branches/develop' % REPO, '')
        self.assert_refused(self.run_helper(), 'develop has no merge queue')

    def test_a_rule_merely_named_like_the_queue_is_not_one(self):
        self.api('repos/%s/rules/branches/develop' % REPO, 'not_merge_queue\nmerge_queue_like\n')
        self.assert_refused(self.run_helper(), 'develop has no merge queue')

    def test_unreadable_rules_mean_no_merge(self):
        os.unlink(os.path.join(self.fixtures, 'api-repos_%s_rules_branches_develop' % REPO.replace('/', '_')))
        self.assert_refused(self.run_helper(), 'could not read the rules for develop')

    def test_a_base_other_than_develop_means_no_merge(self):
        self.fixture('pr-view-%s-baseRefName,state' % PR_NUMBER, 'stable/2026.1 OPEN\n')
        self.assert_refused(self.run_helper(), "found 'stable/2026.1 OPEN'")

    def test_a_closed_pull_request_means_no_merge(self):
        for state in ('CLOSED', 'MERGED'):
            with self.subTest(state):
                self.fixture('pr-view-%s-baseRefName,state' % PR_NUMBER, 'develop %s\n' % state)
                self.assert_refused(self.run_helper(), "found 'develop %s'" % state)

    def test_an_unreadable_pull_request_means_no_merge(self):
        os.unlink(os.path.join(self.fixtures, 'pr-view-%s-baseRefName,state' % PR_NUMBER))
        self.assert_refused(self.run_helper(), 'could not read the pull request')

    def test_a_non_data_file_on_the_second_page_means_no_merge(self):
        self.files(['data/layers/%03d.jsonl' % i for i in range(100)], ['data/layers/last.jsonl', 'tools/evil.sh'])
        self.assert_refused(self.run_helper(), 'it changes files outside data/: tools/evil.sh')

    def test_a_non_data_file_on_the_first_page_means_no_merge(self):
        self.files(['README.md', 'data/layers/one.jsonl'])
        self.assert_refused(self.run_helper(), 'outside data/: README.md')

    def test_a_path_merely_starting_with_data_is_not_under_it(self):
        for name in ('database.py', 'data', 'docs/data/x', 'xdata/y'):
            with self.subTest(name):
                self.files(['data/one.jsonl', name])
                self.assert_refused(self.run_helper(), 'outside data/: %s' % name)

    def test_an_empty_file_list_means_no_merge(self):
        self.files([], [])
        self.assert_refused(self.run_helper(), 'it changes no files')

    def test_an_unreadable_file_list_means_no_merge(self):
        os.unlink(os.path.join(self.fixtures, 'api-repos_%s_pulls_%s_files' % (REPO.replace('/', '_'), PR_NUMBER)))
        self.assert_refused(self.run_helper(), 'could not list its files')

    def test_a_failing_merge_warns_and_exits_cleanly(self):
        self.fixture('pr-merge-status', '1\n')
        proc = self.run_helper()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(self.merges()))
        self.assertIn('Warning: could not set auto-merge on PR #%s' % PR_NUMBER, proc.stderr)

    def test_a_bad_argument_is_a_usage_error_with_no_calls(self):
        for args in ([], ['12', '13'], ['#12'], ['12; rm -rf /'], ['']):
            with self.subTest(args):
                proc = subprocess.run([HELPER] + args, capture_output=True, text=True, env=self.environment(),
                                      timeout=60)
                self.assertEqual(1, proc.returncode)
                self.assertIn('Usage:', proc.stderr)
                self.assertEqual([], self.calls())


class CreateDataPrTestCase(GhStubTestCase):
    """create-data-pr.sh, run from a copy beside a stub helper, as the layer data job runs it."""

    def setUp(self):
        super().setUp()
        self.scripts = os.path.join(self.tempdir, 'runner-temp')
        os.makedirs(self.scripts)
        shutil.copy(CREATE, os.path.join(self.scripts, 'create-data-pr.sh'))
        write(os.path.join(self.scripts, 'data-pr-auto-merge.sh'), STUB_HELPER, 0o755)
        self.body = os.path.join(self.tempdir, 'body.md')
        write(self.body, 'A body.\n')

    def run_create(self, **overrides):
        return subprocess.run(
            [os.path.join(self.scripts, 'create-data-pr.sh'), '--head', BRANCH, '--title', 'A title',
             '--body-file', self.body, '--assignee', 'mikalstill'],
            cwd=self.tempdir, capture_output=True, text=True, env=self.environment(**overrides), timeout=60)

    def helper_calls(self):
        return self.read_log('helper.log').splitlines()

    def test_an_existing_pull_request_is_queued(self):
        self.fixture('pr-list', '77\n')
        proc = self.run_create()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(['77'], self.helper_calls())
        self.assertFalse([c for c in self.calls() if c[:2] == ['pr', 'create']])

    def test_a_created_pull_request_is_queued(self):
        self.fixture('pr-create-number', '78\n')
        proc = self.run_create()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(['78'], self.helper_calls())
        self.assertIn('Pull request created: https://github.com/%s/pull/78' % REPO, proc.stdout)

    def test_a_pull_request_created_despite_an_error_is_queued(self):
        self.fixture('pr-create-number', '79\n')
        self.fixture('pr-create-fails', '')
        proc = self.run_create()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(['79'], self.helper_calls())
        self.assertIn('exists despite the error', proc.stdout)

    def test_no_pull_request_means_nothing_to_queue(self):
        self.fixture('pr-create-fails', '')
        proc = self.run_create()
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], self.helper_calls())

    def test_a_failing_helper_does_not_fail_the_create(self):
        for setup in (lambda: self.fixture('pr-list', '77\n'), lambda: self.fixture('pr-create-number', '78\n')):
            with self.subTest(setup):
                self.setUp()
                setup()
                proc = self.run_create(HELPER_STATUS='3')
                self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
                self.assertEqual(1, len(self.helper_calls()))
                self.assertIn('Warning: ', proc.stderr)

    def test_the_create_itself_never_merges(self):
        for name in ('pr-list', 'pr-create-number'):
            with self.subTest(name):
                self.setUp()
                self.fixture(name, '77\n')
                self.run_create()
                self.assertEqual([], self.merges())
                self.assert_never_merged_directly()


class HealDataPrsTestCase(GhStubTestCase):
    """heal-data-prs.sh, started from a ref holding the helper, against a conflicted data branch and a develop
    which do not."""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tempdir, 'home')
        self.origin = os.path.join(self.tempdir, 'origin.git')
        self.work = os.path.join(self.tempdir, 'work')
        os.makedirs(self.home)

        # Drop every inherited GIT_ variable. Run from a git hook, as
        # pre-commit is on commit, GIT_INDEX_FILE and friends point at the
        # repository being committed to, and git commands here would act on
        # it instead of the temporary repositories.
        self.git_env = self.environment(**{
            'HOME': self.home,
            'GIT_CONFIG_GLOBAL': os.path.join(self.home, 'gitconfig'),
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_AUTHOR_NAME': 'Test Setup',
            'GIT_AUTHOR_EMAIL': 'setup@example.com',
            'GIT_COMMITTER_NAME': 'Test Setup',
            'GIT_COMMITTER_EMAIL': 'setup@example.com',
        })

        self.git('init', '--bare', '--initial-branch=develop', self.origin, cwd=self.tempdir)
        self.git('clone', self.origin, self.work, cwd=self.tempdir)
        self.git('checkout', '-b', 'develop')

        # develop as it was when the data branch was cut: the healer and the
        # verifier.
        series = os.path.join(self.work, 'data', 'layers', 'image.jsonl')
        write(os.path.join(self.work, '.gitattributes'), 'data/** merge=union\n')
        write(series, '{"run": 1}\n')
        write(os.path.join(self.work, 'tools', 'verify-data-merge.py'), STUB_VERIFY, 0o755)
        shutil.copy(HEAL, os.path.join(self.work, 'tools', 'heal-data-prs.sh'))
        self.commit('Develop before the helper')

        self.git('checkout', '-b', BRANCH)
        with open(series, 'a') as f:
            f.write('{"run": 2}\n')
        self.commit('Layer data')
        self.git('push', 'origin', BRANCH)

        # develop since: another data merge appending to the same series,
        # which is the conflict.
        self.git('checkout', 'develop')
        with open(series, 'a') as f:
            f.write('{"run": 3}\n')
        self.commit('Other layer data')
        self.git('push', 'origin', 'develop')

        # The ref the healer starts from, one commit on: the helper, a stub
        # here. Neither develop nor the merged data branch has it.
        write(os.path.join(self.work, 'tools', 'data-pr-auto-merge.sh'), STUB_HELPER, 0o755)
        self.commit('Add the helper')

        self.fixture('pr-list', '%s|%s\n' % (PR_NUMBER, BRANCH))
        self.fixture('pr-view-%s-mergeable' % PR_NUMBER, 'CONFLICTING\n')
        self.fixture('pr-view-%s-comments' % PR_NUMBER, '')

    def git(self, *args, cwd=None):
        return subprocess.run(['git'] + list(args), cwd=cwd or self.work, env=self.git_env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-m', message)

    def run_heal(self, *args):
        environment = dict(self.git_env)
        for name in ('GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL'):
            del environment[name]
        return subprocess.run(['./tools/heal-data-prs.sh'] + list(args), cwd=self.work, capture_output=True,
                              text=True, env=environment, timeout=120)

    def helper_calls(self):
        return self.read_log('helper.log').splitlines()

    def test_a_healed_pull_request_is_queued_from_the_starting_ref(self):
        before = self.git('rev-parse', 'origin/' + BRANCH)
        proc = self.run_heal()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('Done: 1 healed', proc.stdout)

        # The heal was pushed, and the queueing came after it.
        self.git('fetch', 'origin')
        after = self.git('rev-parse', 'origin/' + BRANCH)
        self.assertNotEqual(before, after)
        self.assertEqual(before, self.git('rev-parse', after + '^1'))
        self.assertEqual([PR_NUMBER], self.helper_calls())
        self.assertEqual('develop', self.git('rev-parse', '--abbrev-ref', 'HEAD'))
        self.assertEqual([], self.merges())
        self.assert_never_merged_directly()

    def test_a_dry_run_never_queues(self):
        proc = self.run_heal('--dry-run')
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('would push (dry run)', proc.stdout)
        self.assertEqual([], self.helper_calls())

    def test_a_pull_request_with_no_conflict_is_left_alone(self):
        self.fixture('pr-view-%s-mergeable' % PR_NUMBER, 'MERGEABLE\n')
        proc = self.run_heal()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('Done: 0 healed, 1 already clean', proc.stdout)
        self.assertEqual([], self.helper_calls())

    def test_a_pull_request_which_fails_verification_is_not_queued(self):
        stub = STUB_VERIFY + 'exit 1\n'
        self.git('checkout', BRANCH)
        write(os.path.join(self.work, 'tools', 'verify-data-merge.py'), stub, 0o755)
        self.commit('A verifier which refuses')
        self.git('push', 'origin', BRANCH)
        self.git('checkout', 'develop')
        proc = self.run_heal()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('1 need manual attention', proc.stdout)
        self.assertEqual([], self.helper_calls())

    def test_a_failing_helper_does_not_fail_the_heal(self):
        self.git_env['HELPER_STATUS'] = '3'
        proc = self.run_heal()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([PR_NUMBER], self.helper_calls())
        self.assertIn('Warning: could not queue PR #%s' % PR_NUMBER, proc.stdout)


if __name__ == '__main__':
    unittest.main()
