import contextlib
import importlib.metadata
import io
import os
import tempfile
import unittest
from unittest import mock

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import cli
from kolla_ansible_launcher import config as config_mod

from tests import fakes
from tests.test_identity import Group
from tests.test_identity import User


DIGEST = 'sha256:' + 'e' * 64
IMAGE = 'registry.example.com:5050/kolla/kolla-ansible@' + DIGEST
GOOD = {'kolla_ansible_launcher_protocol': '1'}


class Scene:
    """A configuration directory, a working directory and a home on disk."""

    def __init__(self, root):
        self.root = root
        self.cfg = os.path.join(root, 'cfg')
        self.work = os.path.join(root, 'work')
        self.home = os.path.join(root, 'home', 'ansible')
        self.keys = os.path.join(root, 'keys')
        self.vault = os.path.join(root, 'vault')
        self.state = os.path.join(root, 'state')
        self.agent = os.path.join(root, 'agent', 'agent.sock')
        for d in (self.cfg, os.path.join(self.work, 'inventory', 'group_vars'), os.path.join(self.home, '.ssh'),
                  self.keys, self.vault, os.path.dirname(self.agent)):
            os.makedirs(d)
        for f in (os.path.join(self.work, 'overrides.yml'), os.path.join(self.vault, 'pass'), self.agent):
            with open(f, 'w') as fh:
                fh.write('\n')
        self.write_conf('[deployer]\nimage = %s\nmounts = %s\n' % (IMAGE, self.keys))
        self.user = User('ansible', 'x', 1001, 1002, '', self.home, '/bin/bash')
        self.group = Group('ansible', 'x', 1002, [])
        self.environ = {'KOLLA_CONFIG_PATH': self.cfg, 'ANSIBLE_FORKS': '20', 'ANSIBLE_COLLECTIONS_PATH': '/x',
                        'SSH_AUTH_SOCK': self.agent, 'PATH': '/usr/bin', 'HOME': self.home}

    def write_conf(self, text):
        with open(os.path.join(self.cfg, 'deployer.conf'), 'w') as f:
            f.write(text)

    def config(self):
        return config_mod.load(self.cfg)


class CommandLineTestCase(unittest.TestCase):
    """The full command line for one realistic invocation, for each engine."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Scene(self._tmp.name)
        self.argv = ['deploy', '-i', 'inventory', '-e', '@overrides.yml', '-e', 'openstack_release=master',
                     '--vault-password-file', os.path.join(self.s.vault, 'pass'), '--limit', 'control']

    def tearDown(self):
        self._tmp.cleanup()

    def expected_tail(self):
        s = self.s
        ssh = os.path.join(s.home, '.ssh')
        # Mounts after deduplication are in order of their target.
        mounts = sorted([(s.agent, s.agent, ''), (s.cfg, s.cfg, ''), (ssh, ssh, ':ro'),
                         (ssh, '/var/lib/kolla-ansible/.ssh', ':ro'), (s.keys, s.keys, ''),
                         (s.vault, s.vault, ''), (s.work, s.work, '')], key=lambda m: m[1])
        tail = ['--volume', '%s/passwd:/etc/passwd:ro' % s.state, '--volume', '%s/group:/etc/group:ro' % s.state]
        for source, target, mode in mounts:
            tail += ['--volume', '%s:%s%s' % (source, target, mode)]
        return tail + ['--workdir', s.work, '--env', 'ANSIBLE_FORKS', '--env', 'KOLLA_CONFIG_PATH',
                       '--env', 'SSH_AUTH_SOCK']

    def test_docker(self):
        s = self.s
        command = cli.plan('kolla-ansible', self.argv, s.environ, s.work, s.config(), 'docker', s.user, s.group,
                           s.state, tty=False, uid=1001, gid=1002, groups=[], euid=1001)
        self.assertEqual(
            ['docker', 'run', '--rm', '-i', '--network', 'host', '--user', '1001:1002', '--privileged', '--pid',
             'host'] + self.expected_tail() + [IMAGE] + self.argv,
            command)
        with open(os.path.join(s.state, 'passwd')) as f:
            self.assertIn('ansible:x:1001:1002::/var/lib/kolla-ansible:/bin/sh\n', f.read())

    def test_podman(self):
        s = self.s
        s.write_conf('[deployer]\nimage = %s\nengine = podman\nmounts = %s\n' % (IMAGE, s.keys))
        command = cli.plan('kolla-ansible', self.argv, s.environ, s.work, s.config(), 'podman', s.user, s.group,
                           s.state, tty=True, uid=1001, gid=1002, groups=[], euid=1001)
        self.assertEqual(
            ['podman', 'run', '--rm', '-i', '-t', '--network', 'host', '--user', '1001:1002', '--userns=keep-id',
             '--privileged', '--pid', 'host'] + self.expected_tail() + [IMAGE] + self.argv,
            command)

    def plan_with(self, engine, groups, euid):
        s = self.s
        return cli.plan('kolla-ansible', self.argv, s.environ, s.work, s.config(), engine, s.user, s.group,
                        s.state, tty=False, uid=1001, gid=1002, groups=groups, euid=euid)

    def test_docker_supplementary_groups(self):
        # Sorted numerically, deduplicated, and without the primary GID.
        command = self.plan_with('docker', [1002, 100, 27, 1002, 100, 1500], 1001)
        self.assertEqual(['--user', '1001:1002', '--group-add', '27', '--group-add', '100', '--group-add', '1500',
                          '--privileged'], command[command.index('--user'):command.index('--privileged') + 1])

    def test_rootful_podman_supplementary_groups(self):
        command = self.plan_with('podman', [1002, 10, 4], 0)
        self.assertNotIn('--userns=keep-id', command)
        self.assertEqual(['--user', '1001:1002', '--group-add', '4', '--group-add', '10', '--privileged'],
                         command[command.index('--user'):command.index('--privileged') + 1])

    def test_rootless_podman_has_no_group_add(self):
        command = self.plan_with('podman', [1002, 10, 4], 1001)
        self.assertIn('--userns=keep-id', command)
        self.assertNotIn('--group-add', command)

    def test_no_supplementary_groups(self):
        self.assertNotIn('--group-add', self.plan_with('docker', [1002], 1001))

    def test_without_host_namespaces(self):
        s = self.s
        s.write_conf('[deployer]\nimage = %s\nhost_namespaces = false\n' % IMAGE)
        command = cli.plan('kolla-ansible', ['--version'], {}, s.work, s.config(), 'docker', s.user, s.group,
                           s.state, tty=False, uid=1001, gid=1002, groups=[], euid=1001)
        self.assertNotIn('--privileged', command)
        self.assertNotIn('--pid', command)

    def test_password_tool(self):
        s = self.s
        argv = ['-p', 'passwords.yml']
        command = cli.plan('kolla-genpwd', argv, {'KOLLA_CONFIG_PATH': s.cfg}, s.work, s.config(), 'docker', s.user,
                           s.group, s.state, tty=False, uid=1001, gid=1002, groups=[], euid=1001)
        self.assertEqual(['--entrypoint', 'dumb-init', IMAGE, '--single-child', '--', 'kolla-genpwd'] + argv,
                         command[-8:])
        self.assertIn('%s:%s' % (s.work, s.work), command)


class RunTestCase(unittest.TestCase):
    """run(), up to the exec."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Scene(self._tmp.name)
        self.environ = {'KOLLA_CONFIG_PATH': self.s.cfg, 'XDG_RUNTIME_DIR': self.s.state}
        os.makedirs(self.s.state)
        self.execs = []
        patches = [mock.patch.object(cli, 'refuse_kolla_ansible_distribution'),
                   mock.patch.object(config_mod, 'choose_engine', return_value='docker')]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, tool, argv, fake):
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            status = cli.run(tool, argv, environ=self.environ, cwd=self.s.work, runner=fake,
                             execvp=lambda f, c: self.execs.append(c))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_runs(self):
        fake = fakes.FakeEngine(local={IMAGE: fakes.image(GOOD)})
        status, _, stderr = self.run_cli('kolla-ansible', ['deploy'], fake)
        self.assertIsNone(status, stderr)
        self.assertEqual(1, len(self.execs))
        self.assertEqual(['docker', 'run'], self.execs[0][:2])
        self.assertEqual([IMAGE, 'deploy'], self.execs[0][-2:])

    def test_unpinned_image_warns(self):
        self.s.write_conf('[deployer]\nimage = kolla/kolla-ansible:local\n')
        fake = fakes.FakeEngine(local={'kolla/kolla-ansible:local': fakes.image(GOOD)})
        status, _, stderr = self.run_cli('kolla-ansible', ['--version'], fake)
        self.assertIsNone(status)
        self.assertIn('not pinned by digest', stderr)

    def test_install_deps_is_refused(self):
        fake = fakes.FakeEngine(local={IMAGE: fakes.image(GOOD)})
        status, _, stderr = self.run_cli('kolla-ansible', ['-v', 'install-deps'], fake)
        self.assertEqual(1, status)
        self.assertIn('install-deps', stderr)
        self.assertEqual([], self.execs)
        self.assertEqual([], fake.calls)

    def test_unknown_protocol_is_refused(self):
        fake = fakes.FakeEngine(local={IMAGE: fakes.image({'kolla_ansible_launcher_protocol': '2'})})
        status, _, stderr = self.run_cli('kolla-ansible', ['deploy'], fake)
        self.assertEqual(1, status)
        self.assertIn('protocol 2', stderr)
        self.assertEqual([], self.execs)

    def test_image_without_protocol_is_refused(self):
        fake = fakes.FakeEngine(local={IMAGE: fakes.image({'name': 'nova-api'})})
        status, _, _ = self.run_cli('kolla-genpwd', [], fake)
        self.assertEqual(1, status)
        self.assertEqual([], self.execs)

    def test_missing_config_is_named(self):
        os.unlink(os.path.join(self.s.cfg, 'deployer.conf'))
        status, _, stderr = self.run_cli('kolla-ansible', ['--version'], fakes.FakeEngine())
        self.assertEqual(1, status)
        self.assertIn(os.path.join(self.s.cfg, 'deployer.conf'), stderr)

    def test_launcher_show(self):
        fake = fakes.FakeEngine(local={IMAGE: fakes.image(dict(GOOD, name='kolla-ansible'))})
        status, stdout, _ = self.run_cli('kolla-ansible', ['launcher', 'show'], fake)
        self.assertEqual(0, status)
        self.assertIn('  kolla_ansible_launcher_protocol = 1\n', stdout)
        self.assertIn('image: %s\n' % IMAGE, stdout)
        self.assertEqual([], self.execs)

    def test_launcher_pin_keeps_other_keys(self):
        ref = 'registry.example.com:5050/kolla/kolla-ansible:2026.2'
        self.s.write_conf('[deployer]\n# lab\nimage = old:tag\nmounts = %s\n' % self.s.keys)
        fake = fakes.FakeEngine(remote={ref: fakes.image(GOOD, [IMAGE])})
        status, stdout, stderr = self.run_cli('kolla-ansible', ['launcher', 'pin', ref], fake)
        self.assertEqual(0, status, stderr)
        with open(os.path.join(self.s.cfg, 'deployer.conf')) as f:
            self.assertEqual('[deployer]\n# lab\nimage = %s\nmounts = %s\n' % (IMAGE, self.s.keys), f.read())

    def test_launcher_pin_refuses_a_foreign_image(self):
        ref = 'registry.example.com:5050/kolla/nova-api:2026.2'
        fake = fakes.FakeEngine(remote={ref: fakes.image({}, ['registry.example.com:5050/kolla/nova-api@' + DIGEST])})
        status, _, _ = self.run_cli('kolla-ansible', ['launcher', '--configdir', self.s.cfg, 'pin', ref], fake)
        self.assertEqual(1, status)
        with open(os.path.join(self.s.cfg, 'deployer.conf')) as f:
            self.assertIn('image = %s' % IMAGE, f.read())


class DistributionTestCase(unittest.TestCase):
    def test_absent(self):
        def find(name):
            raise importlib.metadata.PackageNotFoundError(name)
        cli.refuse_kolla_ansible_distribution(find)

    def test_present(self):
        dist = mock.Mock(version='21.0.0')
        self.assertRaises(LauncherError, cli.refuse_kolla_ansible_distribution, lambda name: dist)


if __name__ == '__main__':
    unittest.main()
