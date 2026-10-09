import os
import subprocess
import tempfile
import unittest

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import install


DEBIAN_13 = '''PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
NAME="Debian GNU/Linux"
VERSION_ID="13"
VERSION_CODENAME=trixie
ID=debian
HOME_URL="https://www.debian.org/"
'''

DEBIAN_12 = DEBIAN_13.replace('13 (trixie)', '12 (bookworm)').replace('"13"', '"12"')

ROCKY_10 = '''NAME="Rocky Linux"
VERSION="10.0 (Red Quartz)"
ID="rocky"
ID_LIKE="rhel centos fedora"
VERSION_ID="10.0"
PRETTY_NAME="Rocky Linux 10.0 (Red Quartz)"
'''

UBUNTU = '''# A comment
NAME="Ubuntu"
ID=ubuntu
ID_LIKE=debian
PRETTY_NAME="Ubuntu 24.04 LTS"
'''

ALPINE = '''NAME="Alpine Linux"
ID=alpine
PRETTY_NAME="Alpine Linux v3.20"
'''


class FakeHost:
    """A runner and a which() for a host with no engine until something installs one."""

    def __init__(self, has_docker_cli=True, installs=('docker', 'podman'), engines=()):
        self.has_docker_cli = has_docker_cli
        self.installs = installs
        self.engines = set(engines)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs.get('env')))
        if command[:2] == ['apt-cache', 'show']:
            if self.has_docker_cli:
                return subprocess.CompletedProcess(command, 0, stdout='Package: docker-cli\n')
            return subprocess.CompletedProcess(command, 100, stdout='')
        if command[:2] in (['apt-get', 'install'], ['dnf', 'install']):
            if 'docker.io' in command and 'docker' in self.installs:
                self.engines.add('docker')
            if 'podman' in command and 'podman' in self.installs:
                self.engines.add('podman')
        return subprocess.CompletedProcess(command, 0)

    def which(self, name):
        return '/usr/bin/%s' % name if name in self.engines else None


class OsReleaseTestCase(unittest.TestCase):
    def test_parse(self):
        fields = install.parse_os_release(ROCKY_10)
        self.assertEqual('rocky', fields['ID'])
        self.assertEqual('rhel centos fedora', fields['ID_LIKE'])
        self.assertEqual('Rocky Linux 10.0 (Red Quartz)', fields['PRETTY_NAME'])

    def test_parse_skips_comments(self):
        self.assertEqual({'NAME': 'Ubuntu', 'ID': 'ubuntu', 'ID_LIKE': 'debian', 'PRETTY_NAME': 'Ubuntu 24.04 LTS'},
                         install.parse_os_release(UBUNTU))

    def test_family(self):
        for text, expected in ((DEBIAN_13, install.DEBIAN), (UBUNTU, install.DEBIAN), (ROCKY_10, install.REDHAT),
                               ('ID=fedora\n', install.REDHAT), (ALPINE, None)):
            self.assertEqual(expected, install.family(install.parse_os_release(text)), text)

    def test_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertRaises(LauncherError, install.read_os_release, os.path.join(d, 'os-release'))


class InstallEngineTestCase(unittest.TestCase):
    def install(self, host, text, euid=0):
        out = []
        status = install.install_engine(run=host, which=host.which, euid=euid,
                                        os_release=lambda: install.parse_os_release(text), environ={'PATH': '/bin'},
                                        out=out.append)
        return status, out

    def test_debian_13(self):
        host = FakeHost(has_docker_cli=True)
        status, out = self.install(host, DEBIAN_13)
        self.assertEqual(0, status)
        env = {'PATH': '/bin', 'DEBIAN_FRONTEND': 'noninteractive'}
        self.assertEqual([(['apt-get', 'update'], env),
                          (['apt-cache', 'show', 'docker-cli'], env),
                          (['apt-get', 'install', '-y', 'docker.io', 'docker-cli'], env)], host.calls)
        self.assertEqual('docker is installed (/usr/bin/docker)', out[-1])

    def test_debian_12_has_no_docker_cli(self):
        host = FakeHost(has_docker_cli=False)
        status, _ = self.install(host, DEBIAN_12)
        self.assertEqual(0, status)
        self.assertEqual(['apt-get', 'install', '-y', 'docker.io'], host.calls[-1][0])

    def test_rocky_10(self):
        host = FakeHost()
        status, out = self.install(host, ROCKY_10)
        self.assertEqual(0, status)
        self.assertEqual([(['dnf', 'install', '-y', 'podman'], None)], host.calls)
        self.assertEqual('podman is installed (/usr/bin/podman)', out[-1])

    def test_existing_engine(self):
        for engine in ('docker', 'podman'):
            host = FakeHost(engines=[engine])
            # Not root, and on an unknown distribution: neither matters when there is nothing to do.
            status, out = self.install(host, ALPINE, euid=1000)
            self.assertEqual(0, status)
            self.assertEqual([], host.calls)
            self.assertEqual(['%s is already installed (/usr/bin/%s); nothing to do' % (engine, engine)], out)

    def test_unknown_distribution(self):
        host = FakeHost()
        with self.assertRaises(LauncherError) as e:
            self.install(host, ALPINE)
        self.assertIn('docker or podman', str(e.exception))
        self.assertIn('ID=alpine', str(e.exception))
        self.assertEqual([], host.calls)

    def test_needs_root(self):
        host = FakeHost()
        with self.assertRaises(LauncherError) as e:
            self.install(host, DEBIAN_13, euid=1000)
        self.assertIn('sudo', str(e.exception))
        self.assertEqual([], host.calls)

    def test_install_that_provides_no_engine(self):
        host = FakeHost(installs=())
        self.assertRaises(LauncherError, self.install, host, ROCKY_10)

    def test_failed_install(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1)
        host = FakeHost()
        with self.assertRaises(LauncherError):
            install.install_engine(run=run, which=host.which, euid=0,
                                   os_release=lambda: install.parse_os_release(ROCKY_10), environ={},
                                   out=lambda message: None)


if __name__ == '__main__':
    unittest.main()
