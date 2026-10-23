#!/usr/bin/env python3
"""Tests for collect-layer-data-pr.sh and how the workflow calls it.

The script turns a run's downloaded build artifacts into a pull request of
layer data. Which artifacts it counts decides what data is recorded: counting
a stale one records layers twice, and missing a good one loses them for good,
so each case is tested rather than read off the source. The script is run for
real in a temporary clone of a temporary bare origin, from a copy beside stub
collect-layer-data.py and create-data-pr.sh scripts which record their
arguments, as the workflow runs it from $RUNNER_TEMP. `sleep` and `gh` are
stubbed on PATH, the latter answering the title lookup, and HOME and
GIT_CONFIG_GLOBAL point into the temporary directory so that the script's
`git config --global` never touches the developer's own git configuration
(and no identity is taken from it).

The workflow half pins that the job runs on merge groups, that the step runs
the copy from $RUNNER_TEMP, copies every script it needs, and has no
expressions inside `run:`.

Usage:
    ./test-collect-layer-data-pr.py
"""

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'collect-layer-data-pr.sh')
WORKFLOW = os.path.join(os.path.dirname(HERE), '.github', 'workflows', 'functional-tests.yml')

RUN_ID = '39876200072'
PR_NUMBER = '1966'
PR_TITLE = 'Fix the thing that was broken'
REPO = 'shakenfist/kerbside-patches'
BASE_SHA = '3aa55390fc' + '0' * 30
QUEUE_BRANCH = 'gh-readonly-queue/develop/pr-%s-%s' % (PR_NUMBER, BASE_SHA)
QUEUE_REF = 'refs/heads/' + QUEUE_BRANCH
ATTEMPT = '2'
TOKEN = 'token-for-the-test'

STUB_COLLECT = """#!/bin/bash
printf '%s\\n' "$*" >> "${STUB_LOG}/collect.log"
while [ $# -gt 0 ]; do
    case "$1" in
        --data-dir) data_dir="$2" ;;
        --build-name) build_name="$2" ;;
    esac
    shift
done
mkdir -p "${data_dir}"
echo "{\\"build\\": \\"${build_name}\\"}" >> "${data_dir}/${build_name}.jsonl"
"""

STUB_CREATE = """#!/bin/bash
printf '%s\\n' "$*" >> "${STUB_LOG}/create.log"
while [ $# -gt 0 ]; do
    case "$1" in
        --body-file) cp "$2" "${STUB_LOG}/body.md" ;;
    esac
    shift
done
"""

STUB_SLEEP = """#!/bin/bash
printf '%s\\n' "$*" >> "${STUB_LOG}/sleep.log"
"""

# Answers `gh pr view <N> ... --json title` from a title-<N> fixture, failing
# as an API error would when there is none.
STUB_GH = """#!/bin/bash
printf '%s\\n' "$*" >> "${STUB_LOG}/gh.log"
if [ "$1 $2" = 'pr view' ] && [ -e "${STUB_LOG}/title-$3" ]; then
    cat "${STUB_LOG}/title-$3"
    exit 0
fi
echo 'stub gh: no answer' >&2
exit 1
"""


def git(*args, cwd, env):
    return subprocess.run(['git'] + list(args), cwd=cwd, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def make_tarball(path, names):
    with tarfile.open(path, 'w:gz') as tar:
        for name in names:
            tar.addfile(tarfile.TarInfo(name), io.BytesIO(b''))


class CollectLayerDataPrTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.home = os.path.join(self.tempdir, 'home')
        self.bin = os.path.join(self.tempdir, 'bin')
        self.log = os.path.join(self.tempdir, 'log')
        self.scripts = os.path.join(self.tempdir, 'runner-temp')
        self.artifacts = os.path.join(self.tempdir, 'artifacts')
        self.origin = os.path.join(self.tempdir, 'origin.git')
        self.work = os.path.join(self.tempdir, 'work')
        for path in (self.home, self.bin, self.log, self.scripts, self.artifacts):
            os.makedirs(path)

        self.stub(os.path.join(self.bin, 'sleep'), STUB_SLEEP)
        self.stub(os.path.join(self.bin, 'gh'), STUB_GH)
        self.title(PR_NUMBER, PR_TITLE)
        self.stub(os.path.join(self.scripts, 'collect-layer-data.py'), STUB_COLLECT)
        self.stub(os.path.join(self.scripts, 'create-data-pr.sh'), STUB_CREATE)
        shutil.copy(SCRIPT, os.path.join(self.scripts, 'collect-layer-data-pr.sh'))

        # Drop every inherited GIT_ variable. Run from a git hook, as
        # pre-commit is on commit, GIT_INDEX_FILE and friends point at the
        # repository being committed to, and git commands here would act on
        # it instead of the temporary repositories.
        self.git_env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.git_env.update({
            'HOME': self.home,
            'GIT_CONFIG_GLOBAL': os.path.join(self.home, 'gitconfig'),
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_AUTHOR_NAME': 'Test Setup',
            'GIT_AUTHOR_EMAIL': 'setup@example.com',
            'GIT_COMMITTER_NAME': 'Test Setup',
            'GIT_COMMITTER_EMAIL': 'setup@example.com',
        })

        # A bare origin holding develop, and a clone of it on a pull request
        # branch, which is where the workflow's checkout leaves the script.
        git('init', '--bare', '--initial-branch=develop', self.origin, cwd=self.tempdir, env=self.git_env)
        git('clone', self.origin, self.work, cwd=self.tempdir, env=self.git_env)
        git('checkout', '-b', 'develop', cwd=self.work, env=self.git_env)
        os.makedirs(os.path.join(self.work, 'data'))
        with open(os.path.join(self.work, 'data', 'README'), 'w') as f:
            f.write('data\n')
        git('add', '.', cwd=self.work, env=self.git_env)
        git('commit', '-m', 'Develop', cwd=self.work, env=self.git_env)
        git('push', 'origin', 'develop', cwd=self.work, env=self.git_env)
        git('checkout', '-b', 'the-pull-request', cwd=self.work, env=self.git_env)
        with open(os.path.join(self.work, 'tools-change'), 'w') as f:
            f.write('a change under test\n')
        git('add', '.', cwd=self.work, env=self.git_env)
        git('commit', '-m', 'The change under test', cwd=self.work, env=self.git_env)
        self.develop = git('rev-parse', 'develop', cwd=self.work, env=self.git_env)

    def stub(self, path, content):
        with open(path, 'w') as f:
            f.write(content)
        os.chmod(path, 0o755)

    def title(self, number, text):
        with open(os.path.join(self.log, 'title-%s' % number), 'w') as f:
            f.write(text + '\n')

    def artifact(self, name, attempt=ATTEMPT, names=('layers/a.jsonl',), tarball=True):
        """A downloaded build-* artifact. attempt=None leaves out the run_attempt file."""
        path = os.path.join(self.artifacts, name)
        os.makedirs(path)
        if attempt is not None:
            with open(os.path.join(path, 'run_attempt'), 'w') as f:
                f.write(attempt + '\n')
        if tarball:
            make_tarball(os.path.join(path, 'layers.tar.gz'), names)
        return path

    def run_script(self, **overrides):
        environment = dict(self.git_env)
        environment.update({
            'PATH': '%s:%s' % (self.bin, os.environ['PATH']),
            'STUB_LOG': self.log,
            'RUNNER_TEMP': self.scripts,
            'ARTIFACTS_DIR': self.artifacts,
            'GITHUB_TOKEN': TOKEN,
            'GITHUB_RUN_ID': RUN_ID,
            'QUEUE_REF': QUEUE_REF,
            'GITHUB_REPOSITORY': REPO,
            'CURRENT_ATTEMPT': ATTEMPT,
            'DATA_PREFIX': None,
            # The setup identity must not leak in: the commit is made as
            # whoever the script configures.
            'GIT_AUTHOR_NAME': None,
            'GIT_AUTHOR_EMAIL': None,
            'GIT_COMMITTER_NAME': None,
            'GIT_COMMITTER_EMAIL': None,
        })
        environment.update(overrides)
        for name in [k for k, v in environment.items() if v is None]:
            del environment[name]
        return subprocess.run([os.path.join(self.scripts, 'collect-layer-data-pr.sh')], cwd=self.work,
                              capture_output=True, text=True, env=environment, timeout=120)

    def read_log(self, name):
        path = os.path.join(self.log, name)
        if not os.path.exists(path):
            return ''
        with open(path) as f:
            return f.read()

    def collected(self):
        """The build names the collection script was run for, in order."""
        return [line.split('--build-name ')[1].split(' ')[0] for line in self.read_log('collect.log').splitlines()]

    def origin_branches(self):
        out = git('for-each-ref', '--format=%(refname:short)', 'refs/heads', cwd=self.origin, env=self.git_env)
        return sorted(out.split())

    def assert_nothing_published(self, proc):
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(['develop'], self.origin_branches())
        self.assertEqual('', self.read_log('create.log'))
        self.assertEqual('', self.read_log('collect.log'))

    def test_a_good_artifact_is_pushed_on_develop_and_opened_as_a_pull_request(self):
        self.artifact('build-one')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

        branches = [b for b in self.origin_branches() if b != 'develop']
        self.assertEqual(1, len(branches), branches)
        branch = branches[0]
        self.assertRegex(branch, r'^layers-data-\d{8}-\d{4}-run%s$' % RUN_ID)

        # The data commit sits directly on develop, not on the pull request.
        self.assertEqual(self.develop, git('rev-parse', branch + '^', cwd=self.origin, env=self.git_env))
        files = git('show', '--name-only', '--format=', branch, cwd=self.origin, env=self.git_env)
        self.assertEqual('data/layers/build-one.jsonl', files)
        message = git('log', '-1', '--format=%B', branch, cwd=self.origin, env=self.git_env)
        self.assertIn('Add layer data from build run %s.' % RUN_ID, message)
        self.assertIn('\nSource PR: #%s\n' % PR_NUMBER, message)
        self.assertIn('Run attempt: %s' % ATTEMPT, message)
        self.assertIn('Co-Authored-By: shakenfist-bot <bot@shakenfist.com>', message)
        self.assertEqual('shakenfist-bot', git('log', '-1', '--format=%an', branch, cwd=self.origin,
                                               env=self.git_env))

        body = self.read_log('body.md')
        self.assertIn('This PR adds layer data collected from build run %s.' % RUN_ID, body)
        self.assertIn('\n**Source:** PR #%s - %s\n' % (PR_NUMBER, PR_TITLE), body)
        self.assertEqual(['pr view %s --repo %s --json title --jq .title' % (PR_NUMBER, REPO)],
                         self.read_log('gh.log').splitlines())
        self.assertIn('### Builds included\n\n- build-one\n', body)
        self.assertNotIn('### Builds excluded', body)

        create = self.read_log('create.log').splitlines()
        self.assertEqual(1, len(create))
        self.assertIn('--assignee mikalstill', create[0])
        self.assertIn('--head %s' % branch, create[0])
        self.assertIn('--body-file %s/pr-body.md' % self.scripts, create[0])
        self.assertIn('--title layers data from ', create[0])
        self.assertIn('(run %s, attempt %s)' % (RUN_ID, ATTEMPT), create[0])

        collect = self.read_log('collect.log').splitlines()
        self.assertEqual(1, len(collect))
        for expected in ('--build-name build-one', '--data-dir data/layers', '--run-id %s' % RUN_ID,
                         '--run-attempt %s' % ATTEMPT):
            self.assertIn(expected, collect[0])
        self.assertTrue(collect[0].startswith('--tarball %s/build-one/layers.tar.gz ' % self.artifacts))

    def source_lines(self):
        """The source lines of the pushed commit message and of the pull request body."""
        branch = [b for b in self.origin_branches() if b != 'develop'][0]
        message = git('log', '-1', '--format=%B', branch, cwd=self.origin, env=self.git_env)
        commit = [line for line in message.splitlines() if line.startswith('Source')]
        body = [line for line in self.read_log('body.md').splitlines() if line.startswith('**Source:**')]
        return commit, body

    def test_a_queue_branch_with_or_without_refs_heads_names_its_pull_request(self):
        for ref in (QUEUE_REF, QUEUE_BRANCH, 'refs/heads/gh-readonly-queue/stable/2026.1/pr-42-abc123',
                    'gh-readonly-queue/develop/pr-42-' + BASE_SHA):
            with self.subTest(ref):
                self.setUp()
                self.title('42', 'Another change')
                self.artifact('build-one')
                proc = self.run_script(QUEUE_REF=ref)
                self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
                number = '42' if '/pr-42-' in ref else PR_NUMBER
                title = 'Another change' if number == '42' else PR_TITLE
                self.assertEqual((['Source PR: #%s' % number], ['**Source:** PR #%s - %s' % (number, title)]),
                                 self.source_lines())
                self.assertNotIn('Warning', proc.stderr)

    def test_a_ref_which_names_no_pull_request_still_collects_and_names_the_run(self):
        for ref in ('', 'refs/heads/develop', 'refs/heads/gh-readonly-queue/develop/pr-12x-abc',
                    'gh-readonly-queue/develop/pr-12-NOTHEX', 'refs/heads/gh-readonly-queue/pr-12-abc',
                    'refs/pull/12/merge', 'x' + QUEUE_REF):
            with self.subTest(ref):
                self.setUp()
                self.artifact('build-one')
                proc = self.run_script(QUEUE_REF=ref)
                self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
                self.assertEqual(['build-one'], self.collected())
                self.assertEqual(1, len(self.read_log('create.log').splitlines()))
                self.assertEqual((['Source: run %s' % RUN_ID], ['**Source:** run %s' % RUN_ID]),
                                 self.source_lines())
                self.assertNotIn('#', self.read_log('body.md').split('### Builds included')[0])
                self.assertIn('names no pull request; naming run %s instead' % RUN_ID, proc.stderr)
                self.assertEqual('', self.read_log('gh.log'))

    def test_a_failed_title_lookup_still_collects_without_the_title(self):
        os.unlink(os.path.join(self.log, 'title-%s' % PR_NUMBER))
        self.artifact('build-one')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(['build-one'], self.collected())
        self.assertEqual(1, len(self.read_log('create.log').splitlines()))
        self.assertEqual((['Source PR: #%s' % PR_NUMBER], ['**Source:** PR #%s' % PR_NUMBER]), self.source_lines())
        self.assertIn('could not look up the title of PR #%s' % PR_NUMBER, proc.stderr)

    def test_an_empty_title_is_treated_as_a_failed_lookup(self):
        self.title(PR_NUMBER, '')
        self.artifact('build-one')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual((['Source PR: #%s' % PR_NUMBER], ['**Source:** PR #%s' % PR_NUMBER]), self.source_lines())

    def test_the_data_prefix_names_the_branch_and_the_directory(self):
        self.artifact('build-one')
        proc = self.run_script(DATA_PREFIX='other')
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertRegex(self.origin_branches()[-1], r'^other-data-')
        self.assertIn('--data-dir data/other', self.read_log('collect.log'))
        self.assertIn('--title other data from ', self.read_log('create.log'))

    def test_an_artifact_from_another_attempt_is_excluded_and_listed(self):
        self.artifact('build-current')
        self.artifact('build-stale', attempt='1')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

        self.assertEqual(['build-current'], self.collected())
        self.assertIn('Skipping build-stale: from attempt 1, current is %s' % ATTEMPT, proc.stdout)
        body = self.read_log('body.md')
        self.assertIn('### Builds included\n\n- build-current\n', body)
        self.assertIn('### Builds excluded\n\n- build-stale (attempt 1)\n', body)
        self.assertIn('Their layer data was not included to avoid duplicating', body)

    def test_an_artifact_with_no_run_attempt_is_excluded(self):
        self.artifact('build-current')
        self.artifact('build-legacy', attempt=None)
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

        self.assertEqual(['build-current'], self.collected())
        self.assertIn('Skipping build-legacy: no run_attempt file', proc.stdout)
        self.assertIn('- build-legacy (no attempt marker)\n', self.read_log('body.md'))

    def test_a_tarball_with_no_jsonl_is_excluded(self):
        self.artifact('build-current')
        self.artifact('build-empty', names=('layers/', 'layers/readme.txt'))
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

        # Only the body is pinned: the collection loop re-checks the attempt
        # but not the listing, so it still hands such a tarball to the
        # collection script, which finds nothing in it.
        self.assertIn('### Builds included\n\n- build-current\n', self.read_log('body.md'))
        self.assertIn('Skipping build-empty: layers.tar.gz contains no layer data', proc.stdout)
        self.assertIn('- build-empty (no layer data)\n', self.read_log('body.md'))

    def test_a_tarball_which_cannot_be_listed_is_excluded(self):
        self.artifact('build-current')
        path = self.artifact('build-broken', names=())
        with open(os.path.join(path, 'layers.tar.gz'), 'w') as f:
            f.write('this is not a tarball\n')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

        self.assertIn('### Builds included\n\n- build-current\n', self.read_log('body.md'))
        self.assertIn('- build-broken (no layer data)\n', self.read_log('body.md'))

    def test_a_long_listing_with_the_jsonl_first_still_counts(self):
        # grep -q exits at the first match. Read through a pipe under pipefail,
        # tar then dies of SIGPIPE on the rest of a long listing, and the
        # artifact reads as having no layer data.
        names = ['layers/first.jsonl'] + ['layers/a-long-directory-name/%s/file-%05d.txt' % ('x' * 40, i)
                                          for i in range(8000)]
        self.artifact('build-long', names=names)
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(['build-long'], self.collected())
        self.assertNotIn('contains no layer data', proc.stdout)

    def test_no_included_build_exits_cleanly_without_pushing(self):
        self.artifact('build-stale', attempt='1')
        self.artifact('build-legacy', attempt=None)
        self.artifact('build-empty', names=('layers/readme.txt',))
        proc = self.run_script()
        self.assert_nothing_published(proc)
        self.assertIn('No layers.tar.gz files from current attempt found, skipping PR creation', proc.stdout)

    def test_no_artifacts_at_all_exits_cleanly_without_pushing(self):
        proc = self.run_script()
        self.assert_nothing_published(proc)

    def test_nothing_to_commit_exits_cleanly_without_pushing(self):
        self.artifact('build-one')
        self.stub(os.path.join(self.scripts, 'collect-layer-data.py'), '#!/bin/bash\nexit 0\n')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('No changes to commit', proc.stdout)
        self.assertEqual(['develop'], self.origin_branches())
        self.assertEqual('', self.read_log('create.log'))

    def test_the_global_git_configuration_used_is_the_one_it_was_given(self):
        self.artifact('build-one')
        proc = self.run_script()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        config = git('config', '--file', self.git_env['GIT_CONFIG_GLOBAL'], '--list', cwd=self.tempdir,
                     env=self.git_env)
        self.assertIn('user.name=shakenfist-bot', config)
        self.assertIn('user.email=bot@shakenfist.com', config)

    def test_a_failing_collection_script_fails_the_step(self):
        self.artifact('build-one')
        self.stub(os.path.join(self.scripts, 'collect-layer-data.py'), '#!/bin/bash\nexit 3\n')
        proc = self.run_script()
        self.assertNotEqual(0, proc.returncode)
        self.assertEqual(['develop'], self.origin_branches())

    def test_a_required_input_which_is_missing_fails_before_anything_is_done(self):
        self.artifact('build-one')
        for name in ('GITHUB_TOKEN', 'GITHUB_RUN_ID', 'QUEUE_REF', 'CURRENT_ATTEMPT'):
            with self.subTest(name):
                proc = self.run_script(**{name: None})
                self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
                self.assertIn('%s must be set' % name, proc.stderr)
                self.assertEqual('', self.read_log('collect.log'))
                self.assertEqual(['develop'], self.origin_branches())

    def test_the_pull_request_inputs_are_no_longer_needed(self):
        self.artifact('build-one')
        proc = self.run_script(PR_NUMBER=None, PR_TITLE=None)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)


class CollectLayerDataWorkflowTestCase(unittest.TestCase):
    def setUp(self):
        with open(WORKFLOW) as f:
            self.workflow = yaml.safe_load(f)
        self.job = self.workflow['jobs']['collect_layer_data']
        steps = [s for s in self.job['steps'] if s.get('name') == 'Collect layer data and prepare PR']
        self.assertEqual(1, len(steps))
        self.step = steps[0]

    def test_the_job_runs_on_merge_groups_only_and_not_when_the_builds_skipped(self):
        condition = ' '.join(self.job['if'].split())
        self.assertEqual("github.event_name == 'merge_group' && !cancelled() "
                         "&& needs.build_images.result != 'skipped'", condition)

    def test_the_job_is_outside_both_gates(self):
        for gate in ('can_enqueue', 'can_merge'):
            self.assertNotIn('collect_layer_data', self.workflow['jobs'][gate]['needs'])

    def test_the_dead_data_prefix_input_is_gone(self):
        self.assertNotIn('data_prefix', self.workflow[True]['workflow_dispatch']['inputs'])
        self.assertNotIn('env', self.job)
        with open(WORKFLOW) as f:
            self.assertNotIn('data_prefix', f.read())

    def test_the_step_runs_the_copy_from_runner_temp(self):
        lines = [line.strip() for line in self.step['run'].replace('\\\n', ' ').splitlines() if line.strip()]
        self.assertEqual(2, len(lines), lines)
        self.assertTrue(lines[0].startswith('cp '), lines[0])
        self.assertEqual('"${RUNNER_TEMP}/collect-layer-data-pr.sh"', lines[1])

    def test_every_script_the_step_needs_is_copied(self):
        copy = self.step['run'].replace('\\\n', ' ').splitlines()[0]
        for script in ('collect-layer-data-pr.sh', 'collect-layer-data.py', 'create-data-pr.sh',
                       'data-pr-auto-merge.sh'):
            self.assertIn('tools/' + script, copy)
            self.assertTrue(os.path.exists(os.path.join(HERE, script)), script)
        self.assertTrue(copy.rstrip().endswith('"${RUNNER_TEMP}/"'), copy)

    def test_nothing_is_interpolated_into_the_shell(self):
        for step in self.job['steps']:
            self.assertNotIn('${{', step.get('run', ''))

    def test_the_script_inputs_come_through_the_environment(self):
        env = self.step['env']
        self.assertEqual('${{ secrets.DAILY_REBASE_TOKEN }}', env['GITHUB_TOKEN'])
        self.assertEqual('${{ github.event.merge_group.head_ref }}', env['QUEUE_REF'])
        self.assertEqual('${{ github.run_attempt }}', env['CURRENT_ATTEMPT'])
        self.assertEqual({'GITHUB_TOKEN', 'QUEUE_REF', 'CURRENT_ATTEMPT'}, set(env))
        # The pull_request payload is empty on a merge group.
        for step in self.job['steps']:
            self.assertNotIn('github.event.pull_request', yaml.safe_dump(step))

    def test_the_checkout_and_download_precede_the_step(self):
        names = [s.get('name') for s in self.job['steps']]
        self.assertEqual(['Checkout kerbside-patches', 'Download build artifacts',
                          'Collect layer data and prepare PR'], names)


if __name__ == '__main__':
    unittest.main()
