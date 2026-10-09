import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import chroot


ROOT = '/var/lib/kolla-ansible-launcher/bootstrap-x/rootfs'


def entry(source, target=None, readonly=False, recursive=True, replace_symlink=False):
    return {'source': source, 'target': source if target is None else target, 'readonly': readonly,
            'recursive': recursive, 'replace_symlink': replace_symlink}


class MountCommandsTestCase(unittest.TestCase):
    def test_commands(self):
        spec = {'root': ROOT, 'mounts': [
            entry('/proc'), entry('/sys'), entry('/dev'),
            entry('/etc/resolv.conf', readonly=True, recursive=False, replace_symlink=True),
            entry('/etc/kolla'),
            entry('/root/.ssh', readonly=True),
            entry('/root/.ssh', '/var/lib/kolla-ansible/.ssh', readonly=True)]}
        self.assertEqual([
            ['mount', '--rbind', '/proc', ROOT + '/proc'],
            ['mount', '--rbind', '/sys', ROOT + '/sys'],
            ['mount', '--rbind', '/dev', ROOT + '/dev'],
            ['mount', '--bind', '/etc/resolv.conf', ROOT + '/etc/resolv.conf'],
            ['mount', '-o', 'remount,bind,ro', ROOT + '/etc/resolv.conf'],
            ['mount', '--rbind', '/etc/kolla', ROOT + '/etc/kolla'],
            ['mount', '--rbind', '/root/.ssh', ROOT + '/root/.ssh'],
            ['mount', '-o', 'remount,bind,ro', ROOT + '/root/.ssh'],
            ['mount', '--rbind', '/root/.ssh', ROOT + '/var/lib/kolla-ansible/.ssh'],
            ['mount', '-o', 'remount,bind,ro', ROOT + '/var/lib/kolla-ansible/.ssh'],
        ], chroot.mount_commands(spec))


class PrepareTargetTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, 'etc'))

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_directories(self):
        path = chroot.prepare_target(self.root, '/srv/github/keys', is_dir=True)
        self.assertEqual(os.path.join(self.root, 'srv/github/keys'), path)
        self.assertTrue(os.path.isdir(path))

    def test_creates_a_file_for_a_file(self):
        path = chroot.prepare_target(self.root, '/run/user/0/agent.sock', is_dir=False)
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(os.path.isdir(os.path.dirname(path)))

    def test_existing_target_is_kept(self):
        with open(os.path.join(self.root, 'etc', 'hosts'), 'w') as f:
            f.write('image\n')
        chroot.prepare_target(self.root, '/etc/hosts', is_dir=False, replace_symlink=True)
        with open(os.path.join(self.root, 'etc', 'hosts')) as f:
            self.assertEqual('image\n', f.read())

    def test_refuses_a_symlinked_directory(self):
        os.symlink('/run', os.path.join(self.root, 'var'))
        self.assertRaises(LauncherError, chroot.prepare_target, self.root, '/var/run/x', is_dir=True)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'run')))

    def test_refuses_a_symlinked_leaf(self):
        os.symlink('/elsewhere', os.path.join(self.root, 'etc', 'kolla'))
        self.assertRaises(LauncherError, chroot.prepare_target, self.root, '/etc/kolla', is_dir=True)

    def test_replaces_a_symlinked_resolv_conf(self):
        link = os.path.join(self.root, 'etc', 'resolv.conf')
        os.symlink('/run/systemd/resolve/stub-resolv.conf', link)
        chroot.prepare_target(self.root, '/etc/resolv.conf', is_dir=False, replace_symlink=True)
        self.assertFalse(os.path.islink(link))
        self.assertTrue(os.path.isfile(link))

    def test_refuses_a_file_in_the_path(self):
        with open(os.path.join(self.root, 'srv'), 'w'):
            pass
        self.assertRaises(LauncherError, chroot.prepare_target, self.root, '/srv/github', is_dir=True)


class MainTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, 'rootfs')
        self.source = os.path.join(self._tmp.name, 'kolla')
        os.makedirs(self.root)
        os.makedirs(self.source)
        self.spec = {'version': 1, 'root': self.root, 'mounts': [entry(self.source)],
                     'env': {'PATH': '/var/lib/kolla/venv/bin:/usr/bin', 'HOME': '/root'}, 'cwd': '/work',
                     'argv': ['kolla-ansible', 'bootstrap-servers', '-i', 'inventory']}
        self.spec_path = os.path.join(self._tmp.name, 'spec.json')
        with open(self.spec_path, 'w') as f:
            json.dump(self.spec, f)
        self.events = []

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, chdir=None, mount_status=0, exec_error=None):
        def run(command, **kwargs):
            self.events.append(('run', command))
            return subprocess.CompletedProcess(command, mount_status)

        def execvpe(file, args, env):
            self.events.append(('exec', file, args, env))
            if exec_error:
                raise exec_error

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = chroot.main([self.spec_path], run=run, chroot=lambda p: self.events.append(('chroot', p)),
                                 chdir=chdir or (lambda p: self.events.append(('chdir', p))), execvpe=execvpe)
        return status, stderr.getvalue()

    def test_mounts_chroots_and_execs(self):
        status, _ = self.run_main()
        self.assertIsNone(status)
        target = self.root + self.source
        self.assertEqual([('run', ['mount', '--rbind', self.source, target]),
                          ('chroot', self.root),
                          ('chdir', '/work'),
                          ('exec', 'kolla-ansible', self.spec['argv'], self.spec['env'])], self.events)
        self.assertTrue(os.path.isdir(target))

    def test_missing_cwd_falls_back_to_root(self):
        def chdir(path):
            if path == '/work':
                raise FileNotFoundError(path)
            self.events.append(('chdir', path))
        status, stderr = self.run_main(chdir=chdir)
        self.assertIsNone(status)
        self.assertIn(('chdir', '/'), self.events)
        self.assertIn('/work does not exist', stderr)

    def test_failed_mount_stops_before_chroot(self):
        status, stderr = self.run_main(mount_status=32)
        self.assertEqual(1, status)
        self.assertEqual(['run'], [e[0] for e in self.events])
        self.assertIn('exit status 32', stderr)

    def test_failed_exec(self):
        status, stderr = self.run_main(exec_error=FileNotFoundError('kolla-ansible'))
        self.assertEqual(127, status)
        self.assertIn('cannot run kolla-ansible', stderr)

    def test_wrong_spec_version(self):
        self.spec['version'] = 2
        with open(self.spec_path, 'w') as f:
            json.dump(self.spec, f)
        status, stderr = self.run_main()
        self.assertEqual(1, status)
        self.assertEqual([], self.events)
        self.assertIn('version 2', stderr)


if __name__ == '__main__':
    unittest.main()
