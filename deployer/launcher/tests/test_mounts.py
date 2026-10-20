import contextlib
import os
import tempfile
import unittest
from unittest import mock

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import command
from kolla_ansible_launcher import mounts
from kolla_ansible_launcher.mounts import Mount


class MountForTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, 'inventory', 'group_vars'))
        with open(os.path.join(self.root, 'vars.yml'), 'w') as f:
            f.write('{}\n')

    def tearDown(self):
        self._tmp.cleanup()

    def test_directory_is_mounted_itself(self):
        path = os.path.join(self.root, 'inventory')
        self.assertEqual(Mount(path), mounts.mount_for(path))

    def test_file_mounts_its_directory(self):
        self.assertEqual(Mount(self.root), mounts.mount_for(os.path.join(self.root, 'vars.yml')))

    def test_missing_path_mounts_nearest_existing_directory(self):
        path = os.path.join(self.root, 'inventory', 'new', 'deeper', 'passwords.yml')
        self.assertEqual(Mount(os.path.join(self.root, 'inventory')), mounts.mount_for(path))

    def test_file_in_protected_directory_is_mounted_alone(self):
        self.assertEqual(Mount('/etc/hostname'), mounts.mount_for('/etc/hostname'))

    def test_protected_directory_is_refused(self):
        self.assertRaises(LauncherError, mounts.mount_for, '/etc')
        self.assertRaises(LauncherError, mounts.mount_for, '/usr/share')
        self.assertRaises(LauncherError, mounts.mount_for, '/etc/no-such-file-for-the-launcher-tests')

    def test_is_protected(self):
        self.assertTrue(mounts.is_protected('/'))
        self.assertTrue(mounts.is_protected('/usr/lib/python3'))
        self.assertTrue(mounts.is_protected('/var/lib/kolla/venv'))
        self.assertFalse(mounts.is_protected('/etc/kolla'))
        self.assertFalse(mounts.is_protected('/var/lib/kolla-other'))
        self.assertFalse(mounts.is_protected('/usrdata'))
        self.assertFalse(mounts.is_protected('/etc/kolla-ansible-old'))


class HostKollaAnsibleConfigTestCase(unittest.TestCase):
    """A host /etc/kolla-ansible must not hide the image's deployer.json and manifest.json."""

    DIRS = ('/', '/etc', '/etc/kolla-ansible', '/srv/kolla')
    FILES = ('/etc/kolla-ansible/inventory', '/etc/kolla-ansible/globals.yml')

    @contextlib.contextmanager
    def host(self):
        with mock.patch('os.path.isdir', side_effect=lambda p: p in self.DIRS), \
                mock.patch('os.path.isfile', side_effect=lambda p: p in self.FILES), \
                mock.patch('os.path.exists', side_effect=lambda p: p in self.DIRS + self.FILES):
            yield

    def mount_for(self, path):
        with self.host():
            return mounts.mount_for(path)

    def assert_refused(self, path):
        with self.assertRaises(LauncherError) as e:
            self.mount_for(path)
        self.assertEqual('refusing to mount /etc/kolla-ansible over the deployer image, which %s needs; move it '
                         'elsewhere' % path, str(e.exception))

    def test_configuration_directory_is_refused(self):
        self.assert_refused('/etc/kolla-ansible')

    def test_inventory_is_refused(self):
        # Unlike a file directly in /etc, which is mounted alone.
        self.assert_refused('/etc/kolla-ansible/inventory')

    def test_missing_file_is_refused(self):
        self.assert_refused('/etc/kolla-ansible/passwords.yml')

    def test_file_in_deployer_conf_mounts_is_refused(self):
        with self.host():
            for path in ('/etc/kolla-ansible', '/etc/kolla-ansible/globals.yml'):
                with self.subTest(path=path):
                    with self.assertRaises(LauncherError) as e:
                        command.collect_mounts('kolla-ansible', [], '/srv/kolla', {}, '/srv/kolla', '/home/op',
                                               [path])
                    self.assertEqual('refusing to mount %s over the deployer image' % path, str(e.exception))

    def test_image_files_are_protected(self):
        for path in ('/etc/kolla-ansible', '/etc/kolla-ansible/deployer.json', '/etc/kolla-ansible/manifest.json'):
            self.assertTrue(mounts.is_protected(path), path)


class DeduplicateTestCase(unittest.TestCase):
    def test_nested_mounts_are_dropped(self):
        result = mounts.deduplicate([
            Mount('/srv/kolla/inventory'),
            Mount('/srv/kolla'),
            Mount('/srv/kolla/a/b'),
            Mount('/srv/kollax'),
            Mount('/srv/kolla'),
        ])
        self.assertEqual([Mount('/srv/kolla'), Mount('/srv/kollax')], result)

    def test_read_write_wins_at_the_same_path(self):
        result = mounts.deduplicate([Mount('/home/op/.ssh', readonly=True), Mount('/home/op/.ssh')])
        self.assertEqual([Mount('/home/op/.ssh')], result)

    def test_read_only_under_read_write_is_dropped(self):
        result = mounts.deduplicate([Mount('/home/op/.ssh', readonly=True), Mount('/home/op')])
        self.assertEqual([Mount('/home/op')], result)

    def test_read_write_under_read_only_is_kept(self):
        result = mounts.deduplicate([Mount('/home/op/.ssh/vars'), Mount('/home/op/.ssh', readonly=True)])
        self.assertEqual([Mount('/home/op/.ssh', readonly=True), Mount('/home/op/.ssh/vars')], result)

    def test_mounts_at_other_targets_are_kept(self):
        passwd = Mount('/run/user/1000/kolla-ansible-launcher/passwd', '/etc/passwd', readonly=True)
        result = mounts.deduplicate([Mount('/run/user/1000'), passwd])
        self.assertEqual([passwd, Mount('/run/user/1000')], result)

    def test_conflicting_sources_are_refused(self):
        self.assertRaises(LauncherError, mounts.deduplicate,
                          [Mount('/a', '/etc/passwd'), Mount('/b', '/etc/passwd')])

    def test_volume(self):
        self.assertEqual('/srv/x:/srv/x', Mount('/srv/x').volume())
        self.assertEqual('/a:/etc/passwd:ro', Mount('/a', '/etc/passwd', readonly=True).volume())
        self.assertRaises(LauncherError, Mount('/srv/a:b').volume)


if __name__ == '__main__':
    unittest.main()
