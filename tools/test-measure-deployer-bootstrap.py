#!/usr/bin/env python3
"""Tests for measure-deployer-bootstrap.

Logs are built in memory in the shape of a real "Build test clouds" job log,
and `gh` is replaced, so this runs offline.

Usage:
    ./test-measure-deployer-bootstrap.py
"""

import datetime
import io
import json
import os
import tempfile
import unittest
import zipfile
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from unittest import mock

from click.testing import CliRunner

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = SourceFileLoader('measure_deployer_bootstrap', os.path.join(HERE, 'measure-deployer-bootstrap'))
mdb = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(mdb)

BLUE = '\x1b[0;34m'
GREEN = '\x1b[0;32m'
OFF = '\x1b[0m'
VENV = 'master debian 13 images on rocky 10 all-in-one using debian containers'
LAUNCHER = 'master debian 13 images on rocky 10 all-in-one with the containerised deployer using debian containers'
SUBSTEP_NAMES = [display for _, display in mdb.SUBSTEPS]


class LogBuilder(object):
    """Writes a log with a clock the test advances."""

    def __init__(self, bom=True):
        self.now = datetime.datetime(2026, 10, 12, 13, 0, 0)
        self.lines = []
        self.bom = bom

    def line(self, text, after=0.0):
        self.now += datetime.timedelta(seconds=after)
        prefix = '﻿' if self.bom and not self.lines else ''
        self.lines.append('%s%s.%07dZ %s' % (prefix, self.now.strftime('%Y-%m-%dT%H:%M:%S'),
                                             self.now.microsecond * 10, text))

    def header(self, text, after=0.0, colour=BLUE):
        self.line('%s%s%s' % (colour, text, OFF), after)

    def substep_start(self, number, display):
        self.line('##[group]Run . $GITHUB_ENV')
        self.line('##[start-action display=%s;id=__shakenfist_actions_2.__run%s]'
                  % (display, '' if number == 1 else '_%d' % number))

    def substep_end(self, number, seconds, outcome='success'):
        self.now += datetime.timedelta(seconds=seconds)
        self.line('##[end-action id=__shakenfist_actions_2.__run%s;outcome=%s;conclusion=%s;duration_ms=%d]'
                  % ('' if number == 1 else '_%d' % number, outcome, outcome, round(seconds * 1000)))

    def text(self):
        return '\n'.join(self.lines) + '\n'


def venv_log(posix=85.0, deps=492.0, deps_lines=(), posix_lines=(), after=None, finish=True):
    log = LogBuilder()
    log.line('##[group]Run actions/checkout@v7')
    log.line('##[group]Run shakenfist/actions/deploy-kolla-ansible@main', 5)
    log.substep_start(1, 'Bootstrap Kolla-Ansible')
    log.header('Bootstrap Kolla-Ansible into a venv', 0.2, GREEN)
    log.header('Prepare for \'rocky\' distro')
    log.header('Make venv', 1.0)
    log.header('Install dependencies', 1.5)
    log.header('Upgrade the POSIX ansible collection', 28.0)
    for text in posix_lines:
        log.line(text, 1)
    log.header('Install docker python libraries', posix)
    log.header('Install patched Kolla-Ansible', 0.5)
    log.header('Install Kolla-Ansible dependencies', 7.8)
    for text in deps_lines:
        log.line(text, 1)
    log.header('Bootstrap inventory for topology all-in-one running master', deps)
    log.header('Make certificates', 4.0)
    log.line('[ERROR]: Task failed: Module failed: Timeout when waiting for search string MariaDB', 1)
    log.header('Bootstrap servers', 16.0)
    log.header('Kolla-Ansible bootstrapped.', 22.0, GREEN)
    if not finish:
        return log.text()
    log.substep_end(1, 0)
    for number, seconds in enumerate(after or [50, 100, 700, 120, 13, 400, 12, 36], start=2):
        log.substep_start(number, SUBSTEP_NAMES[number - 1])
        log.substep_end(number, seconds)
    return log.text()


def launcher_log(engine='docker'):
    log = LogBuilder()
    log.line('##[group]Run shakenfist/actions/deploy-kolla-ansible@main')
    log.substep_start(1, 'Bootstrap Kolla-Ansible')
    log.header('Bootstrap Kolla-Ansible into a venv', 0.2, GREEN)
    log.header('Install the launcher into its own venv', 0.1)
    log.header('Install a container engine if there is none', 4.0)
    log.header('Bootstrap inventory for topology all-in-one running master', 33.0)
    log.header('Pin registry.example/kolla-ansible:tag as root', 0.3)
    if engine == 'docker':
        log.line('tag: Pulling from openstack/kolla-ansible', 0.2)
        log.line('Status: Downloaded newer image for registry.example/kolla-ansible:tag', 13.0)
    else:
        log.line('Trying to pull registry.example/kolla-ansible:tag...', 0.2)
        log.line('Writing manifest to image destination', 14.0)
    log.header('Generate passwords', 0.1)
    log.header('Make certificates', 4.0)
    log.header('Bootstrap servers', 20.0)
    log.line('kolla-ansible: unpacked registry.example/kolla-ansible@sha256:abc with %s into /var/lib/x/rootfs in '
             '7.3s, 1172 MiB (du -sx)' % engine, 7.3)
    log.header('Let cloud-user use Docker', 50.0)
    log.header('Give cloud-user /etc/kolla and pin registry.example/kolla-ansible:tag', 0.1)
    log.line('tag: Pulling from openstack/kolla-ansible', 0.2)
    log.line('Status: Image is up to date for registry.example/kolla-ansible:tag', 0.1)
    log.header('Kolla-Ansible bootstrapped.', 0.1, GREEN)
    log.substep_end(1, 0)
    for number, seconds in enumerate([60, 120, 900, 180, 20, 590, 18, 56], start=2):
        log.substep_start(number, SUBSTEP_NAMES[number - 1])
        log.substep_end(number, seconds)
    return log.text()


def make_zip(files, truncate=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    data = buf.getvalue()
    return data[:len(data) // 2] if truncate else data


def job(name, conclusion='success', steps=None, status='completed', deploy='success'):
    steps = steps if steps is not None else [
        {'name': mdb.SETUP_STEP, 'conclusion': 'success'},
        {'name': mdb.DEPLOY_STEP, 'conclusion': deploy, 'started_at': '2026-10-12T13:35:15Z',
         'completed_at': '2026-10-12T14:00:25Z'}]
    return {'name': name, 'conclusion': conclusion, 'status': status, 'steps': steps}


class ParsingTest(unittest.TestCase):
    def test_bom_and_ansi_are_stripped(self):
        lines = mdb.parse_lines(venv_log())
        self.assertEqual('##[group]Run actions/checkout@v7', lines[0][2])
        self.assertEqual('Bootstrap Kolla-Ansible into a venv', lines[4][2])
        self.assertIn('\x1b[0;32m', lines[4][1])

    def test_when_forms(self):
        self.assertEqual('2026-10-13T10:13:00Z', mdb.api_time(mdb.parse_when('2026-10-13T10:13Z')))
        self.assertEqual('2026-10-13T00:00:00Z', mdb.api_time(mdb.parse_when('2026-10-13')))
        with self.assertRaises(Exception):
            mdb.parse_when('yesterday')

    def test_control_names(self):
        self.assertEqual(VENV, mdb.control_name(LAUNCHER))
        self.assertIsNone(mdb.control_name(VENV))


class TimingsTest(unittest.TestCase):
    def test_venv_sections_and_substeps(self):
        view = mdb.LogView(VENV, venv_log())
        self.assertTrue(view.complete())
        values = view.timings()
        self.assertAlmostEqual(85.0, values['posix'], 1)
        self.assertAlmostEqual(492.0, values['install-deps'], 1)
        self.assertAlmostEqual(7.8, values['install-patched'], 1)
        self.assertAlmostEqual(1.5, values['make-venv'], 1)
        self.assertAlmostEqual(17.0, values['certificates'], 1)
        # First header to the inventory header.
        self.assertAlmostEqual(1.0 + 1.5 + 28.0 + 85.0 + 0.5 + 7.8 + 492.0, values['to-inventory'], 1)
        self.assertAlmostEqual(1431.0, values['after-bootstrap'], 1)
        self.assertAlmostEqual(100.0, values['pull'], 1)
        self.assertNotIn('unpack', values)
        self.assertNotIn('install-launcher', values)

    def test_launcher_pull_and_unpack_docker(self):
        values = mdb.LogView(LAUNCHER, launcher_log('docker')).timings()
        self.assertAlmostEqual(13.2, values['pull-root'], 1)
        self.assertAlmostEqual(0.3, values['pull-user'], 1)
        self.assertEqual(7.3, values['unpack'])
        self.assertAlmostEqual(4.0, values['install-launcher'], 1)
        self.assertAlmostEqual(33.0 + 4.0 + 0.1, values['to-inventory'], 1)
        self.assertNotIn('posix', values)

    def test_launcher_pull_podman(self):
        values = mdb.LogView(LAUNCHER, launcher_log('podman')).timings()
        self.assertAlmostEqual(14.2, values['pull-root'], 1)

    def test_log_missing_its_headers_fails_naming_the_job(self):
        log = LogBuilder()
        log.line('##[group]Run shakenfist/actions/deploy-kolla-ansible@main')
        log.substep_start(1, 'Bootstrap Kolla-Ansible')
        log.line('Something else entirely', 3)
        log.substep_end(1, 3)
        with self.assertRaises(mdb.ToolError) as caught:
            mdb.LogView(VENV, log.text()).timings()
        self.assertIn(VENV, caught.exception.message)

    def test_deploy_step_that_never_ran_is_not_reached(self):
        log = LogBuilder()
        log.line('##[group]Run actions/checkout@v7')
        log.line('Setup test environment failed', 3)
        self.assertFalse(mdb.LogView(VENV, log.text()).reached)

    def test_unfinished_bootstrap_still_reports_what_it_reached(self):
        view = mdb.LogView(VENV, venv_log(finish=False))
        self.assertFalse(view.complete())
        self.assertAlmostEqual(85.0, view.timings()['posix'], 1)

    def test_pairs_use_the_jobs_api_step_duration_and_skip_failed_controls(self):
        logs = {LAUNCHER: launcher_log(), VENV: venv_log()}
        jobs = {LAUNCHER: job(LAUNCHER), VENV: job(VENV)}
        pairs = mdb.pair_timings(jobs, logs)
        self.assertEqual(1, len(pairs))
        self.assertEqual(LAUNCHER, pairs[0][0])
        self.assertEqual(25 * 60 + 10, pairs[0][1]['step'])
        jobs[VENV] = job(VENV, 'failure', deploy='failure')
        self.assertEqual([], mdb.pair_timings(jobs, logs))
        jobs[VENV] = job(VENV, 'failure', deploy='skipped')
        self.assertEqual([], mdb.pair_timings(jobs, logs))

    def test_offline_pairs(self):
        self.assertEqual(1, len(mdb.offline_pairs({LAUNCHER: launcher_log(), VENV: venv_log()})))
        self.assertEqual(0, len(mdb.offline_pairs({LAUNCHER: launcher_log()})))

    def test_summarise_and_format(self):
        self.assertIsNone(mdb.summarise([]))
        stat = mdb.summarise([3.0, 1.0, 2.0])
        self.assertEqual((3, 2.0, 1.0, 3.0), (stat['n'], stat['median'], stat['min'], stat['max']))
        self.assertEqual('2.0 (1.0-3.0)', mdb.cell(stat))
        pairs = mdb.offline_pairs({LAUNCHER: launcher_log(), VENV: venv_log()})
        text = mdb.format_timings({LAUNCHER: ([pairs[0][1]], [pairs[0][2]])})
        self.assertIn('kolla-ansible install-deps', text)
        self.assertIn('noisy', text)

    def test_cli_logs_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, (name, text) in enumerate([(VENV, venv_log()), (LAUNCHER, launcher_log())]):
                with open(os.path.join(tmp, '%d_%s.txt' % (index + 7, name)), 'w', encoding='utf-8') as f:
                    f.write(text)
            result = CliRunner().invoke(mdb.cli, ['timings', '--logs-dir', tmp])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn('pairs: 1', result.output)
            result = CliRunner().invoke(mdb.cli, ['timings', '--logs-dir', tmp, '--json'])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn(LAUNCHER, json.loads(result.output))


class GalaxyTest(unittest.TestCase):
    GIVE_UP = ('ERROR kolla_ansible.utils Failed to install Ansible collections from requirements-core.yml using '
               'Ansible Galaxy (error: Command returned non-zero exit status 1) after 5 retries')
    RETRY = ('WARNING Failed to install Ansible collections from requirements-core.yml using Ansible Galaxy '
             '(error: Command returned non-zero exit status 1) (retry: 2/5)')

    def test_clean_log_with_unrelated_errors_matches_nothing(self):
        found = mdb.LogView(VENV, venv_log()).galaxy()
        self.assertEqual({'hard': [], 'recovered': []}, found)

    def test_give_up_is_hard(self):
        found = mdb.LogView(VENV, venv_log(deps_lines=[self.RETRY, self.GIVE_UP], finish=False)).galaxy()
        self.assertEqual(1, len(found['hard']))

    def test_retry_that_recovered(self):
        found = mdb.LogView(VENV, venv_log(deps_lines=[self.RETRY])).galaxy()
        self.assertEqual([], found['hard'])
        self.assertEqual(1, len(found['recovered']))

    def test_galaxy_error_in_the_posix_step_is_hard(self):
        line = "[ERROR]: Unknown error when attempting to call Galaxy at 'https://galaxy.ansible.com/api/'"
        found = mdb.LogView(VENV, venv_log(posix_lines=[line])).galaxy()
        self.assertEqual(1, len(found['hard']))

    def test_galaxy_error_text_outside_the_galaxy_sections_is_ignored(self):
        log = venv_log().replace('Make certificates', 'Make certificates').replace(
            'Timeout when waiting for search string MariaDB', 'Error when getting available collection versions for x')
        self.assertEqual({'hard': [], 'recovered': []}, mdb.LogView(VENV, log).galaxy())


class DownloadTest(unittest.TestCase):
    def test_logs_are_keyed_by_name_not_index(self):
        data = make_zip({'0_%s.txt' % VENV: 'a', '5_%s.txt' % LAUNCHER: 'b', '%s/system.txt' % VENV: 'c'})
        self.assertEqual({VENV: 'a', LAUNCHER: 'b'}, mdb.read_logs_zip(data))

    def test_truncated_zip_is_rejected(self):
        with self.assertRaises(mdb.ToolError):
            mdb.read_logs_zip(make_zip({'0_x.txt': 'a' * 100000}, truncate=True))

    def test_download_retries_a_truncated_zip(self):
        good = make_zip({'0_x.txt': 'a'})
        bad = make_zip({'0_x.txt': 'a' * 100000}, truncate=True)
        with mock.patch.object(mdb, 'gh_api', side_effect=[bad, good]) as api:
            self.assertEqual({'x': 'a'}, mdb.download_logs('o/r', 1, 1, sleep=lambda s: None))
        self.assertEqual(2, api.call_count)
        self.assertIn('repos/o/r/actions/runs/1/attempts/1/logs', api.call_args[0][0])

    def test_download_gives_up_after_three_tries(self):
        bad = make_zip({'0_x.txt': 'a' * 100000}, truncate=True)
        with mock.patch.object(mdb, 'gh_api', return_value=bad) as api:
            with self.assertRaises(mdb.ToolError):
                mdb.download_logs('o/r', 1, 1, sleep=lambda s: None)
        self.assertEqual(3, api.call_count)

    def test_expired_logs(self):
        with mock.patch.object(mdb, 'gh_api', side_effect=mdb.ToolError('gh failed: HTTP 410: Gone')):
            with self.assertRaises(mdb.LogsExpired):
                mdb.download_logs('o/r', 1, 1, sleep=lambda s: None)

    def test_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = make_zip({'0_x.txt': 'a'})
            with mock.patch.object(mdb, 'gh_api', return_value=good) as api:
                mdb.download_logs('o/r', 1, 1, cache_dir=tmp)
                self.assertEqual({'x': 'a'}, mdb.download_logs('o/r', 1, 1, cache_dir=tmp))
            self.assertEqual(1, api.call_count)


FAILED_SETUP = [{'name': mdb.SETUP_STEP, 'conclusion': 'failure'}, {'name': 'Collect logs', 'conclusion': 'failure'}]
FAILED_DEPLOY = [{'name': mdb.SETUP_STEP, 'conclusion': 'success'}, {'name': mdb.DEPLOY_STEP, 'conclusion': 'failure'},
                 {'name': 'Collect logs', 'conclusion': 'failure'}]


class RecordTest(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual('PASS', mdb.classify_record(job(LAUNCHER), job(VENV))[0])
        self.assertEqual('CANCELLED', mdb.classify_record(job(LAUNCHER, 'cancelled'), None)[0])
        self.assertEqual('PENDING', mdb.classify_record(job(LAUNCHER, None, status='in_progress'), None)[0])
        # A failure in Setup is the cluster's, even when later always() steps also fail.
        self.assertEqual('IGNORED', mdb.classify_record(job(LAUNCHER, 'failure', FAILED_SETUP), job(VENV))[0])
        self.assertEqual('FAIL', mdb.classify_record(job(LAUNCHER, 'failure', FAILED_DEPLOY), job(VENV))[0])
        self.assertEqual('FAIL', mdb.classify_record(job(LAUNCHER, 'failure', FAILED_DEPLOY), None)[0])
        # The control failed the same step in the same attempt.
        self.assertEqual('IGNORED', mdb.classify_record(job(LAUNCHER, 'failure', FAILED_DEPLOY),
                                                        job(VENV, 'failure', FAILED_DEPLOY))[0])
        # The control failing somewhere else does not excuse the launcher.
        self.assertEqual('FAIL', mdb.classify_record(job(LAUNCHER, 'failure', FAILED_DEPLOY),
                                                     job(VENV, 'failure', FAILED_SETUP))[0])

    def rows(self, entry, passes, rebases, fails=0):
        rows = [{'entry': entry, 'run': i, 'verdict': 'PASS', 'rebase': i < rebases} for i in range(passes)]
        return rows + [{'entry': entry, 'run': 100 + i, 'verdict': 'FAIL', 'rebase': False} for i in range(fails)]

    def test_rules(self):
        rows = self.rows('a', 5, 2) + self.rows('b', 6, 3)
        entries, met = mdb.evaluate_record(rows, 5, 2)
        self.assertTrue(met)
        self.assertEqual(5, entries['a']['pass'])
        # Too few runs, too few rebases, any failure, and no jobs at all.
        self.assertFalse(mdb.evaluate_record(self.rows('a', 4, 2), 5, 2)[1])
        self.assertFalse(mdb.evaluate_record(self.rows('a', 5, 1), 5, 2)[1])
        self.assertFalse(mdb.evaluate_record(self.rows('a', 9, 4, fails=1), 5, 2)[1])
        self.assertFalse(mdb.evaluate_record([], 5, 2)[1])
        # One entry short is enough to fail the window.
        self.assertFalse(mdb.evaluate_record(self.rows('a', 5, 2) + self.rows('b', 2, 2), 5, 2)[1])

    def test_phase_branches_are_excluded(self):
        runs = [{'id': 1, 'run_attempt': 1, 'head_branch': 'deployer-phase-6'},
                {'id': 2, 'run_attempt': 2, 'head_branch': 'daily-rebase-20261011'}]
        with mock.patch.object(mdb, 'list_jobs', return_value=[job(LAUNCHER), job(VENV)]) as listing:
            rows = mdb.collect_rows('o/r', runs)
        # The same job listed again by the second attempt is one job.
        self.assertEqual(1, len(rows))
        self.assertEqual({2}, set(row['run'] for row in rows))
        self.assertTrue(all(row['rebase'] for row in rows))
        self.assertEqual(2, listing.call_count)

    def test_a_run_counts_once_however_many_attempts_passed(self):
        rows = [{'entry': 'a', 'run': 1, 'verdict': 'PASS', 'rebase': True},
                {'entry': 'a', 'run': 1, 'verdict': 'PASS', 'rebase': True},
                {'entry': 'a', 'run': 2, 'verdict': 'PASS', 'rebase': False}]
        entries, _ = mdb.evaluate_record(rows, 1, 1)
        self.assertEqual({'pass': 2, 'rebases': 1, 'fail': 0}, entries['a'])

    def test_a_failure_stands_when_a_rerun_passed(self):
        rows = self.rows('a', 9, 4) + [{'entry': 'a', 'run': 99, 'verdict': 'FAIL', 'rebase': False},
                                       {'entry': 'a', 'run': 99, 'verdict': 'PASS', 'rebase': False}]
        entries, met = mdb.evaluate_record(rows, 5, 2)
        self.assertEqual(1, entries['a']['fail'])
        self.assertFalse(met)

    def test_carried_over_jobs_are_collected_once(self):
        carried = dict(job(LAUNCHER), id=1, started_at='t1', completed_at='t2')
        rerun = dict(carried, id=2)
        fresh = dict(job(LAUNCHER, 'failure', FAILED_DEPLOY), id=3, started_at='t3', completed_at='t4')
        by_attempt = {1: [carried, job(VENV)], 2: [rerun, job(VENV)], 3: [fresh, job(VENV)]}
        runs = [{'id': 7, 'run_attempt': 3, 'head_branch': 'x'}]
        with mock.patch.object(mdb, 'list_jobs', side_effect=lambda repo, run, attempt: by_attempt[attempt]):
            rows = mdb.collect_rows('o/r', runs)
        self.assertEqual([(1, 'PASS'), (3, 'FAIL')], [(row['attempt'], row['verdict']) for row in rows])

    def test_carried_over_pairs_are_measured_once(self):
        logs = {LAUNCHER: launcher_log(), VENV: venv_log()}
        jobs = {LAUNCHER: dict(job(LAUNCHER), id=1), VENV: dict(job(VENV), id=2)}
        rerun = {LAUNCHER: dict(job(LAUNCHER), id=3), VENV: dict(job(VENV), id=4)}
        seen = set()
        self.assertEqual(1, len(mdb.pair_timings(jobs, logs, seen)))
        self.assertEqual(0, len(mdb.pair_timings(rerun, logs, seen)))

    def test_galaxy_failures_count_a_carried_over_job_once(self):
        carried = dict(job(VENV), id=1)
        by_attempt = {1: [carried], 2: [dict(carried, id=2)]}
        runs = [{'id': 7, 'run_attempt': 2, 'head_branch': 'x', 'created_at': '2026-10-12T00:00:00Z'}]
        with mock.patch.object(mdb, 'list_runs', return_value=runs), \
                mock.patch.object(mdb, 'list_jobs', side_effect=lambda repo, run, attempt: by_attempt[attempt]), \
                mock.patch.object(mdb, 'download_logs', return_value={VENV: venv_log()}) as download:
            result = CliRunner().invoke(mdb.cli, ['galaxy-failures', '--since', '2026-10-01', '--json'])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(1, json.loads(result.output)['venv_jobs_examined'])
        self.assertEqual(1, download.call_count)

    def test_cli_exit_status(self):
        runs = [{'id': i, 'run_attempt': 1, 'head_branch': 'daily-rebase-2026101%d' % i if i < 3 else 'x'}
                for i in range(1, 7)]
        with mock.patch.object(mdb, 'list_runs', return_value=runs), \
                mock.patch.object(mdb, 'list_jobs', return_value=[job(LAUNCHER), job(VENV)]):
            result = CliRunner().invoke(mdb.cli, ['record', '--since', '2026-10-13T10:13Z',
                                                  '--until', '2026-10-20T10:13Z'])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertTrue(result.output.rstrip().endswith('MET'))
        self.assertNotIn('NOT MET', result.output)
        with mock.patch.object(mdb, 'list_runs', return_value=runs[:3]), \
                mock.patch.object(mdb, 'list_jobs', return_value=[job(LAUNCHER), job(VENV)]):
            result = CliRunner().invoke(mdb.cli, ['record', '--since', '2026-10-13', '--until', '2026-10-20'])
        self.assertEqual(1, result.exit_code)
        self.assertIn('NOT MET', result.output)


if __name__ == '__main__':
    unittest.main()
