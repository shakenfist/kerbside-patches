import collections
import os
import stat
import tempfile
import unittest

from kolla_ansible_launcher import identity


User = collections.namedtuple('User', 'pw_name pw_passwd pw_uid pw_gid pw_gecos pw_dir pw_shell')
Group = collections.namedtuple('Group', 'gr_name gr_passwd gr_gid gr_mem')

OPERATOR = User('ansible', 'x', 1001, 1002, 'Deploy: account', '/home/ansible', '/usr/bin/zsh')


class IdentityTestCase(unittest.TestCase):
    def test_passwd(self):
        self.assertEqual('root:x:0:0:root:/root:/bin/sh\n'
                         'ansible:x:1001:1002:Deploy  account:/var/lib/kolla-ansible:/bin/sh\n',
                         identity.passwd_text(OPERATOR))

    def test_passwd_for_root(self):
        root = User('root', 'x', 0, 0, 'root', '/root', '/bin/bash')
        self.assertEqual('root:x:0:0:root:/root:/bin/sh\n', identity.passwd_text(root))
        self.assertEqual('root:x:0:\n', identity.group_text(root, None))

    def test_group(self):
        self.assertEqual('root:x:0:\ndeployers:x:1002:\n',
                         identity.group_text(OPERATOR, Group('deployers', 'x', 1002, [])))

    def test_group_without_a_name(self):
        self.assertEqual('root:x:0:\nansible:x:1002:\n', identity.group_text(OPERATOR, None))

    def test_state_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(os.path.join(d, 'kolla-ansible-launcher'),
                             identity.state_dir({'XDG_RUNTIME_DIR': d}, '/home/ansible'))
        self.assertEqual('/home/ansible/.cache/kolla-ansible-launcher', identity.state_dir({}, '/home/ansible'))
        self.assertEqual('/home/ansible/.cache/kolla-ansible-launcher',
                         identity.state_dir({'XDG_RUNTIME_DIR': '/no/such/dir'}, '/home/ansible'))

    def test_write_files(self):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, 'state')
            passwd_path, group_path = identity.write_files(state, OPERATOR, None)
            with open(passwd_path) as f:
                self.assertEqual(identity.passwd_text(OPERATOR), f.read())
            with open(group_path) as f:
                self.assertEqual(identity.group_text(OPERATOR, None), f.read())
            self.assertEqual(0o644, stat.S_IMODE(os.stat(passwd_path).st_mode))
            # Rewriting leaves no temporary files behind.
            identity.write_files(state, OPERATOR, None)
            self.assertEqual(['group', 'passwd'], sorted(os.listdir(state)))


if __name__ == '__main__':
    unittest.main()
