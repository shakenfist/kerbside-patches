import collections
import contextlib
import io
import json
import os
import signal
import stat
import sys
import tempfile
import unittest
from unittest import mock

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import bootstrap
from kolla_ansible_launcher import cli
from kolla_ansible_launcher import config as config_mod

from tests import fakes
from tests.test_cli import GOOD
from tests.test_cli import IMAGE
from tests.test_cli import Scene


IMAGE_ENV = ['PATH=/var/lib/kolla/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
             'ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections', 'LANG=en_US.UTF-8',
             'HOME=/var/lib/kolla-ansible']


class CapturingPopen(fakes.FakePopen):
    """Keeps the spec the helper would have read, since the run directory is deleted afterwards."""

    def __init__(self, statuses=None):
        super().__init__(statuses)
        self.spec = None
        self.spec_mode = None

    def __call__(self, command, **kwargs):
        if command[0] == 'unshare':
            with open(command[-1]) as f:
                self.spec = json.load(f)
            self.spec_mode = stat.S_IMODE(os.stat(command[-1]).st_mode)
        return super().__call__(command, **kwargs)


class BootstrapTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Scene(self._tmp.name)
        self.work_base = os.path.join(self._tmp.name, 'launcher')
        self.argv = ['bootstrap-servers', '-i', 'inventory', '-e', '@overrides.yml',
                     '-e', 'ansible_remote_tmp=/tmp/.ansible-kolla-root']
        self.environ = dict(self.s.environ, TERM='xterm-256color', ANSIBLE_VAULT_PASSWORD_FILE='/x',
                            SECRET='not forwarded')

    def tearDown(self):
        self._tmp.cleanup()

    def bootstrap(self, engine='docker', fake=None, popen=None, euid=0):
        fake = fake or fakes.FakeEngine(local={IMAGE: fakes.image(GOOD, env=IMAGE_ENV)})
        popen = popen or CapturingPopen()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = bootstrap.run(self.argv, self.environ, self.s.work, runner=fake, popen=popen, euid=euid,
                                   home=self.s.home, work_base=self.work_base, clock=iter([10.0, 42.5]).__next__,
                                   which=lambda name: '/usr/bin/' + name if name == engine else None)
        return status, fake, popen, stderr.getvalue()

    def run_dirs(self):
        return os.listdir(self.work_base) if os.path.isdir(self.work_base) else []

    def check_sequence(self, engine):
        status, fake, popen, stderr = self.bootstrap(engine)
        self.assertEqual(0, status, stderr)
        self.assertEqual([
            [engine, 'image', 'inspect', '--format', '{{json .Id}}', IMAGE],
            [engine, 'image', 'inspect', '--format', '{{json .Config.Labels}}', IMAGE],
            [engine, 'image', 'inspect', '--format', '{{json .Config.Env}}', IMAGE],
            [engine, 'create', IMAGE],
            [engine, 'rm', fakes.CONTAINER],
            ['du', '-sx', popen.spec['root']],
        ], fake.calls)

        rootfs = popen.spec['root']
        run_dir = os.path.dirname(rootfs)
        self.assertEqual(self.work_base, os.path.dirname(run_dir))
        self.assertEqual([
            [engine, 'export', fakes.CONTAINER],
            ['tar', '-x', '-p', '--numeric-owner', '-f', '-', '-C', rootfs],
            ['unshare', '--mount', '--propagation', 'private', '--', sys.executable, '-I', '-m',
             'kolla_ansible_launcher.chroot', os.path.join(run_dir, 'spec.json')],
        ], [c for c, _ in popen.calls])
        self.assertEqual(0o600, popen.spec_mode)
        self.assertIn('unpacked %s with %s into %s in 32.5s, 1680 MiB (du -sx)' % (IMAGE, engine, rootfs), stderr)
        # The run directory is gone, and the base directory is private.
        self.assertEqual([], self.run_dirs())
        self.assertEqual(0o700, stat.S_IMODE(os.stat(self.work_base).st_mode))

    def test_docker(self):
        self.check_sequence('docker')

    def test_podman(self):
        self.check_sequence('podman')

    def test_spec(self):
        _, _, popen, _ = self.bootstrap()
        s = self.s
        spec = popen.spec
        ssh = os.path.join(s.home, '.ssh')

        def entry(source, target=None, readonly=False, recursive=True, replace_symlink=False):
            return {'source': source, 'target': source if target is None else target, 'readonly': readonly,
                    'recursive': recursive, 'replace_symlink': replace_symlink}

        system = [entry('/proc'), entry('/sys'), entry('/dev')]
        system += [entry(f, readonly=True, recursive=False, replace_symlink=True)
                   for f in ('/etc/resolv.conf', '/etc/hosts') if os.path.exists(f)]
        same_path = sorted([entry(s.agent), entry(s.cfg), entry(ssh, readonly=True),
                            entry(ssh, '/var/lib/kolla-ansible/.ssh', readonly=True), entry(s.keys),
                            entry(s.work)], key=lambda m: m['target'])
        self.assertEqual(system + same_path, spec['mounts'])
        self.assertEqual(1, spec['version'])
        self.assertEqual(s.work, spec['cwd'])
        self.assertEqual(['kolla-ansible'] + self.argv, spec['argv'])
        self.assertEqual({
            'PATH': '/var/lib/kolla/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            # The image's, not the host's /x.
            'ANSIBLE_COLLECTIONS_PATH': '/usr/share/ansible/collections',
            'LANG': 'en_US.UTF-8',
            'HOME': '/root',
            'ANSIBLE_FORKS': '20',
            'ANSIBLE_VAULT_PASSWORD_FILE': '/x',
            'KOLLA_CONFIG_PATH': s.cfg,
            'SSH_AUTH_SOCK': s.agent,
            'TERM': 'xterm-256color',
        }, spec['env'])

    def test_needs_root(self):
        fake = fakes.FakeEngine(local={IMAGE: fakes.image(GOOD, env=IMAGE_ENV)})
        popen = CapturingPopen()
        with self.assertRaises(LauncherError) as e:
            self.bootstrap(fake=fake, popen=popen, euid=1000)
        self.assertIn('sudo', str(e.exception))
        self.assertEqual([], fake.calls)
        self.assertEqual([], popen.calls)
        self.assertFalse(os.path.exists(self.work_base))

    def test_exit_status(self):
        status, _, _, _ = self.bootstrap(popen=CapturingPopen({'unshare': 4}))
        self.assertEqual(4, status)

    def test_killed_by_a_signal(self):
        status, _, _, _ = self.bootstrap(popen=CapturingPopen({'unshare': -signal.SIGKILL}))
        self.assertEqual(128 + signal.SIGKILL, status)

    def test_tar_failure_still_removes_the_container(self):
        fake = fakes.FakeEngine(local={IMAGE: fakes.image(GOOD, env=IMAGE_ENV)})
        popen = CapturingPopen({'tar': 2})
        with self.assertRaises(LauncherError) as e:
            self.bootstrap(fake=fake, popen=popen)
        self.assertIn('tar exited 2', str(e.exception))
        self.assertIsNone(popen.spec)
        self.assertEqual(['docker', 'rm', fakes.CONTAINER], fake.calls[-1])
        # The exporter's end of the pipe was handed to tar alone.
        self.assertTrue(popen.calls[0][1]['stdout'] is not None)
        self.assertEqual([], self.run_dirs())

    def test_export_failure(self):
        fake = fakes.FakeEngine(local={IMAGE: fakes.image(GOOD, env=IMAGE_ENV)})
        with self.assertRaises(LauncherError) as e:
            self.bootstrap(fake=fake, popen=CapturingPopen({'export': 1}))
        self.assertIn('export', str(e.exception))
        self.assertIn(['docker', 'rm', fakes.CONTAINER], fake.calls)
        self.assertEqual([], self.run_dirs())

    def test_refused_path_costs_no_unpack(self):
        self.s.write_conf('[deployer]\nimage = %s\nmounts = /usr/lib\n' % IMAGE)
        fake = fakes.FakeEngine(local={IMAGE: fakes.image(GOOD, env=IMAGE_ENV)})
        popen = CapturingPopen()
        with self.assertRaises(LauncherError):
            self.bootstrap(fake=fake, popen=popen)
        self.assertEqual([], popen.calls)
        self.assertNotIn('create', [c[1] for c in fake.calls])
        self.assertEqual([], self.run_dirs())

    def test_helper_survives_sigint_and_gets_sigterm(self):
        handlers = {}

        class Process(fakes.FakeProcess):
            def wait(self):
                handlers['int'] = signal.getsignal(signal.SIGINT)
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                return 0

        before = signal.getsignal(signal.SIGINT)
        process = Process(['unshare'], 0)
        self.assertEqual(0, bootstrap.wait_for(process))
        self.assertEqual(signal.SIG_IGN, handlers['int'])
        self.assertEqual([signal.SIGTERM], process.signals)
        self.assertEqual(before, signal.getsignal(signal.SIGINT))


class CliRoutingTestCase(unittest.TestCase):
    def test_bootstrap_servers_is_routed(self):
        with mock.patch.object(cli, 'refuse_kolla_ansible_distribution'), \
                mock.patch.object(bootstrap, 'run', return_value=3) as run:
            status = cli.run('kolla-ansible', ['-v', 'bootstrap-servers', '-i', 'x'], environ={}, cwd='/w',
                             runner='runner', execvp=self.fail)
        self.assertEqual(3, status)
        run.assert_called_once_with(['-v', 'bootstrap-servers', '-i', 'x'], {}, '/w', 'runner')

    def test_root_refusal_is_one_line(self):
        stderr = io.StringIO()
        with mock.patch.object(cli, 'refuse_kolla_ansible_distribution'), \
                mock.patch.object(os, 'geteuid', return_value=1000), contextlib.redirect_stderr(stderr):
            status = cli.run('kolla-ansible', ['bootstrap-servers'], environ={}, cwd='/w', execvp=self.fail)
        self.assertEqual(1, status)
        self.assertIn('kolla-ansible: error: bootstrap-servers through the launcher needs root', stderr.getvalue())

    def test_other_subcommands_are_not_routed(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'deployer.conf'), 'w') as f:
                f.write('[deployer]\nimage = %s\n' % IMAGE)
            execs = []
            with mock.patch.object(cli, 'refuse_kolla_ansible_distribution'), \
                    mock.patch.object(config_mod, 'choose_engine', return_value='docker'), \
                    mock.patch.object(bootstrap, 'run') as run:
                cli.run('kolla-ansible', ['deploy'], environ={'KOLLA_CONFIG_PATH': d, 'XDG_RUNTIME_DIR': d}, cwd=d,
                        runner=fakes.FakeEngine(local={IMAGE: fakes.image(GOOD)}), execvp=lambda f, c: execs.append(c))
            run.assert_not_called()
            self.assertEqual(1, len(execs))


Stat = collections.namedtuple('Stat', 'st_mode st_dev')


class SafeRmtreeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.top = os.path.join(self._tmp.name, 'run')
        self.outside = os.path.join(self._tmp.name, 'outside')
        os.makedirs(os.path.join(self.top, 'rootfs', 'etc', 'kolla'))
        os.makedirs(os.path.join(self.top, 'rootfs', 'usr', 'bin'))
        os.makedirs(self.outside)
        for path in (os.path.join(self.top, 'spec.json'), os.path.join(self.top, 'rootfs', 'etc', 'kolla', 'g.yml'),
                     os.path.join(self.top, 'rootfs', 'usr', 'bin', 'python3'),
                     os.path.join(self.outside, 'precious')):
            with open(path, 'w') as f:
                f.write('x')
        os.symlink(self.outside, os.path.join(self.top, 'rootfs', 'etc', 'link'))

    def tearDown(self):
        self._tmp.cleanup()

    def test_deletes_without_following_symlinks(self):
        bootstrap.safe_rmtree(self.top)
        self.assertFalse(os.path.exists(self.top))
        self.assertTrue(os.path.exists(os.path.join(self.outside, 'precious')))

    def test_refuses_to_cross_a_device(self):
        mounted = os.path.join(self.top, 'rootfs', 'etc', 'kolla')

        def lstat(path):
            st = os.lstat(path)
            return Stat(st.st_mode, st.st_dev + 1 if path == mounted else st.st_dev)

        with self.assertRaises(bootstrap.CrossesMount) as e:
            bootstrap.safe_rmtree(self.top, lstat=lstat)
        self.assertIn(mounted, str(e.exception))
        # Nothing below the other device was touched, and the tree above it is still there.
        self.assertTrue(os.path.exists(os.path.join(mounted, 'g.yml')))
        self.assertTrue(os.path.isdir(self.top))

    def test_cleanup_leaves_a_crossed_tree_with_a_warning(self):
        mounted = os.path.join(self.top, 'rootfs', 'etc', 'kolla')

        def fake_lstat(p):
            real = os.lstat(p)
            return Stat(real.st_mode, 1 if p == mounted else real.st_dev)

        def rmtree(top):
            bootstrap.safe_rmtree(top, lstat=fake_lstat)

        warnings = []
        info = os.path.join(self._tmp.name, 'mountinfo')
        with open(info, 'w') as f:
            f.write('')
        self.assertFalse(bootstrap.cleanup(self.top, mountinfo=info, rmtree=rmtree, warn=warnings.append))
        self.assertTrue(os.path.exists(os.path.join(mounted, 'g.yml')))
        self.assertEqual(1, len(warnings))
        self.assertIn('left in place', warnings[0])


class CleanupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.top = os.path.join(self._tmp.name, 'run dir')
        os.makedirs(os.path.join(self.top, 'rootfs', 'proc'))
        self.info = os.path.join(self._tmp.name, 'mountinfo')

    def tearDown(self):
        self._tmp.cleanup()

    def write_info(self, *points):
        with open(self.info, 'w') as f:
            f.write('22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n')
            for i, point in enumerate(points):
                f.write('%d 22 0:5 / %s rw - proc proc rw\n' % (100 + i, point.replace(' ', '\\040')))

    def test_deletes(self):
        self.write_info(self.top + '-other/rootfs/proc')
        warnings = []
        self.assertTrue(bootstrap.cleanup(self.top, mountinfo=self.info, warn=warnings.append))
        self.assertFalse(os.path.exists(self.top))
        self.assertEqual([], warnings)

    def test_leaves_a_mounted_tree(self):
        point = os.path.join(self.top, 'rootfs', 'proc')
        self.write_info(point)
        warnings = []
        self.assertFalse(bootstrap.cleanup(self.top, mountinfo=self.info, warn=warnings.append))
        self.assertTrue(os.path.isdir(point))
        self.assertIn(point, warnings[0])
        self.assertIn(self.top + ' is left in place', warnings[0])

    def test_mounts_under(self):
        text = ('22 1 8:1 / / rw - ext4 /dev/sda1 rw\n'
                '30 22 0:5 / /var/lib/x/run\\040dir/rootfs/proc rw - proc proc rw\n'
                '31 22 0:5 / /var/lib/x/run\\040dir2 rw - proc proc rw\n'
                '32 22 0:5 / /var/lib/x/run\\040dir rw - tmpfs tmpfs rw\n')
        self.assertEqual(['/var/lib/x/run dir/rootfs/proc', '/var/lib/x/run dir'],
                         bootstrap.mounts_under('/var/lib/x/run dir', text))


if __name__ == '__main__':
    unittest.main()
