import os
import tempfile
import unittest

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import config


DIGEST = 'sha256:' + 'a' * 64


class ParseTestCase(unittest.TestCase):
    def test_full(self):
        c = config.parse('[deployer]\n'
                         'image = registry.example.com/kolla/kolla-ansible@%s\n'
                         'engine = podman\n'
                         'host_namespaces = no\n'
                         'mounts = /srv/github\n'
                         '    /srv/keys/\n' % DIGEST)
        self.assertEqual('registry.example.com/kolla/kolla-ansible@' + DIGEST, c.image)
        self.assertEqual('podman', c.engine)
        self.assertFalse(c.host_namespaces)
        self.assertEqual(['/srv/github', '/srv/keys'], c.mounts)
        self.assertEqual([], c.warnings)

    def test_defaults(self):
        c = config.parse('[deployer]\nimage = kolla/kolla-ansible@%s\n' % DIGEST)
        self.assertIsNone(c.engine)
        self.assertTrue(c.host_namespaces)
        self.assertEqual([], c.mounts)

    def test_tag_is_accepted_with_a_warning(self):
        c = config.parse('[deployer]\nimage = kolla/kolla-ansible:local\n')
        self.assertEqual('kolla/kolla-ansible:local', c.image)
        self.assertEqual(1, len(c.warnings))
        self.assertIn('not pinned by digest', c.warnings[0])

    def test_image_id_is_pinned(self):
        self.assertEqual([], config.parse('[deployer]\nimage = %s\n' % DIGEST).warnings)

    def test_unknown_key_warns(self):
        c = config.parse('[deployer]\nimage = x@%s\nimgae = y\n' % DIGEST)
        self.assertEqual(['deployer.conf: ignoring unknown key imgae'], c.warnings)

    def test_errors(self):
        for text in ('', '[other]\nimage = x\n', '[deployer]\nengine = docker\n', '[deployer]\nimage =\n',
                     '[deployer]\nimage = x\nengine = lxc\n', '[deployer]\nimage = x\nhost_namespaces = maybe\n',
                     '[deployer]\nimage = x\nmounts = relative/path\n', 'not ini'):
            self.assertRaises(LauncherError, config.parse, text)

    def test_load_names_the_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LauncherError) as cm:
                config.load(d)
            self.assertIn(os.path.join(d, 'deployer.conf'), str(cm.exception))

    def test_choose_engine(self):
        c = config.DeployerConfig('deployer.conf', 'x')
        self.assertEqual('docker', config.choose_engine(c, which=lambda e: '/usr/bin/' + e))
        self.assertEqual('podman', config.choose_engine(c, which=lambda e: e == 'podman'))
        self.assertRaises(LauncherError, config.choose_engine, c, which=lambda e: None)
        c.engine = 'podman'
        self.assertEqual('podman', config.choose_engine(c, which=lambda e: True))
        self.assertRaises(LauncherError, config.choose_engine, c, which=lambda e: e == 'docker')


class PinTestCase(unittest.TestCase):
    def test_replaces_image_and_keeps_everything_else(self):
        text = ('# Deployer for the lab\n'
                '[deployer]\n'
                '# the image\n'
                'image = old:tag\n'
                'engine = docker\n'
                'mounts = /srv/github\n'
                '\n'
                '[other]\n'
                'image = untouched\n')
        new = config.set_image(text, 'new@' + DIGEST)
        self.assertEqual(text.replace('old:tag', 'new@' + DIGEST), new)
        c = config.parse(new)
        self.assertEqual('docker', c.engine)
        self.assertEqual(['/srv/github'], c.mounts)

    def test_replaces_multiline_value(self):
        text = '[deployer]\nimage = old\n  continued\nengine = podman\n'
        self.assertEqual('[deployer]\nimage = new\nengine = podman\n', config.set_image(text, 'new'))

    def test_adds_image_to_section(self):
        text = '[deployer]\nmounts = /srv/github\n'
        self.assertEqual('[deployer]\nimage = new\nmounts = /srv/github\n', config.set_image(text, 'new'))

    def test_adds_section(self):
        self.assertEqual('[deployer]\nimage = new\n', config.set_image('', 'new'))
        self.assertEqual('# hello\n\n[deployer]\nimage = new\n', config.set_image('# hello\n', 'new'))

    def test_write_image(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'deployer.conf')
            with open(path, 'w') as f:
                f.write('[deployer]\nimage = old\nhost_namespaces = false\n')
            config.write_image(d, 'new@' + DIGEST)
            c = config.load(d)
            self.assertEqual('new@' + DIGEST, c.image)
            self.assertFalse(c.host_namespaces)
            self.assertEqual(['deployer.conf'], os.listdir(d))

    def test_write_image_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            config.write_image(d, 'new@' + DIGEST)
            self.assertEqual('new@' + DIGEST, config.load(d).image)


if __name__ == '__main__':
    unittest.main()
