import os
import tempfile
import unittest

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import command
from kolla_ansible_launcher.mounts import Mount


class CollectMountsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.work = os.path.join(self.root, 'work')
        self.cfg = os.path.join(self.root, 'cfg')
        self.home = os.path.join(self.root, 'home')
        self.ssh = os.path.join(self.home, '.ssh')
        self.ansible = os.path.join(self.root, 'ansible')
        self.inventories = os.path.join(self.root, 'inventories')
        for d in (self.work, self.cfg, self.ssh, self.ansible, os.path.join(self.inventories, 'a'),
                  os.path.join(self.inventories, 'b'), os.path.join(self.root, 'logs')):
            os.makedirs(d)
        for f in ('ansible/ansible.cfg', 'ansible/vault-pass', 'ansible/deploy-key', 'home/.ssh/id_ed25519',
                  'inventories/a/hosts', 'inventories/b/hosts', 'cfg/globals.yml'):
            with open(os.path.join(self.root, f), 'w') as fh:
                fh.write('\n')

    def tearDown(self):
        self._tmp.cleanup()

    def collect(self, environ, configdir=None, argv=()):
        return command.collect_mounts('kolla-ansible', list(argv), self.work, environ, configdir or self.cfg,
                                      self.home)

    def added(self, environ):
        """Return the mounts that environ adds to a run with none of the variables set."""
        base = self.collect({})
        return [m for m in self.collect(environ) if m not in base]

    def test_files_ansible_reads_mount_their_directory_read_only(self):
        for name, path in (('ANSIBLE_CONFIG', 'ansible.cfg'), ('ANSIBLE_VAULT_PASSWORD_FILE', 'vault-pass'),
                           ('ANSIBLE_PRIVATE_KEY_FILE', 'deploy-key')):
            with self.subTest(name=name):
                self.assertEqual([Mount(self.ansible, readonly=True)],
                                 self.added({name: os.path.join(self.ansible, path)}))

    def test_configuration_directory(self):
        self.assertEqual([Mount(self.ansible, readonly=True)], self.added({'ANSIBLE_CONFIG': self.ansible}))

    def test_private_key_leaves_ssh_read_only(self):
        mounts = self.collect({'ANSIBLE_PRIVATE_KEY_FILE': os.path.join(self.ssh, 'id_ed25519')})
        self.assertIn(Mount(self.ssh, readonly=True), mounts)
        self.assertNotIn(Mount(self.ssh), mounts)

    def test_inventory_list(self):
        # Relative entries are against the current directory, and empty ones are skipped.
        value = '../inventories/a/hosts, %s,,' % os.path.join(self.inventories, 'b')
        self.assertEqual([Mount(os.path.join(self.inventories, 'a'), readonly=True),
                          Mount(os.path.join(self.inventories, 'b'), readonly=True)],
                         self.added({'ANSIBLE_INVENTORY': value}))

    def test_read_only_under_a_read_write_mount_is_dropped(self):
        self.assertEqual([], self.added({'ANSIBLE_CONFIG': os.path.join(self.cfg, 'globals.yml')}))

    def test_missing_file_ansible_reads_is_not_mounted(self):
        # Not even refused, though / is protected.
        for name in command.READ_PATH_VARIABLES:
            with self.subTest(name=name):
                self.assertEqual([], self.added({name: '/no-such-file-for-the-launcher-tests'}))

    def test_log_path_mounts_its_nearest_existing_directory(self):
        logs = os.path.join(self.root, 'logs')
        self.assertEqual([Mount(logs)], self.added({'ANSIBLE_LOG_PATH': os.path.join(logs, 'ansible.log')}))
        self.assertEqual([Mount(logs)], self.added({'ANSIBLE_LOG_PATH': os.path.join(logs, 'new', 'ansible.log')}))

    def test_unset_or_empty(self):
        base = self.collect({})
        for name in command.READ_PATH_VARIABLES + command.WRITE_PATH_VARIABLES:
            with self.subTest(name=name):
                self.assertEqual(base, self.collect({name: ''}))
        self.assertEqual(base, self.collect({'ANSIBLE_INVENTORY': ' , '}))

    def test_other_variables_are_not_mounted(self):
        self.assertEqual([], self.added({'ANSIBLE_ROLES_PATH': self.ansible, 'KOLLA_ANSIBLE_CFG': self.ansible}))

    def test_protected_path_is_refused_naming_the_variable(self):
        with self.assertRaises(LauncherError) as e:
            self.collect({'ANSIBLE_LOG_PATH': '/etc/no-such-dir-for-the-launcher-tests/ansible.log'})
        self.assertEqual('ANSIBLE_LOG_PATH: refusing to mount /etc over the deployer image, which '
                         '/etc/no-such-dir-for-the-launcher-tests/ansible.log needs; move it elsewhere',
                         str(e.exception))


if __name__ == '__main__':
    unittest.main()
