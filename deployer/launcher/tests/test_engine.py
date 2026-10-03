import unittest

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import engine

from tests import fakes


DIGEST = 'sha256:' + 'c' * 64
GOOD = {'kolla_ansible_launcher_protocol': '1', 'name': 'kolla-ansible'}


class ReferenceTestCase(unittest.TestCase):
    def test_split_reference(self):
        self.assertEqual(('kolla/kolla-ansible', 'master', None), engine.split_reference('kolla/kolla-ansible:master'))
        self.assertEqual(('reg:5050/a/b', None, None), engine.split_reference('reg:5050/a/b'))
        self.assertEqual(('reg:5050/a/b', 't', DIGEST), engine.split_reference('reg:5050/a/b:t@' + DIGEST))

    def test_normalise_repository(self):
        self.assertEqual('docker.io/library/debian', engine.normalise_repository('debian'))
        self.assertEqual('docker.io/kolla/kolla-ansible', engine.normalise_repository('kolla/kolla-ansible'))
        self.assertEqual('docker.io/kolla/kolla-ansible',
                         engine.normalise_repository('index.docker.io/kolla/kolla-ansible'))
        self.assertEqual('quay.io/openstack.kolla/kolla-ansible',
                         engine.normalise_repository('quay.io/openstack.kolla/kolla-ansible'))
        self.assertEqual('localhost/kolla-ansible', engine.normalise_repository('localhost/kolla-ansible'))
        self.assertEqual('reg:5050/a/b', engine.normalise_repository('reg:5050/a/b'))

    def test_choose_digest(self):
        digests = ['other/repo@sha256:' + 'd' * 64, 'docker.io/kolla/kolla-ansible@' + DIGEST]
        self.assertEqual(DIGEST, engine.choose_digest('kolla/kolla-ansible:master', digests))
        self.assertIsNone(engine.choose_digest('kolla/kolla-ansible:master', ['x/y@' + DIGEST]))
        self.assertIsNone(engine.choose_digest('kolla/kolla-ansible:master', None))


class ProtocolTestCase(unittest.TestCase):
    def test_supported(self):
        engine.check_protocol('img', GOOD)

    def test_refused(self):
        self.assertRaises(LauncherError, engine.check_protocol, 'img', {'kolla_ansible_launcher_protocol': '2'})
        self.assertRaises(LauncherError, engine.check_protocol, 'img', {})

    def test_labels_pull_when_absent(self):
        fake = fakes.FakeEngine(remote={'reg/k:t': fakes.image(GOOD)})
        self.assertEqual(GOOD, engine.labels('docker', 'reg/k:t', fake))
        self.assertEqual(['docker', 'pull', 'reg/k:t'], fake.calls[1])

    def test_labels_no_pull_when_present(self):
        fake = fakes.FakeEngine(local={'reg/k:t': fakes.image(GOOD)})
        engine.labels('podman', 'reg/k:t', fake)
        self.assertNotIn('pull', [c[1] for c in fake.calls])
        self.assertEqual(['podman', 'image', 'inspect', '--format', '{{json .Config.Labels}}', 'reg/k:t'],
                         fake.calls[-1])

    def test_labels_pull_failure(self):
        self.assertRaises(LauncherError, engine.labels, 'docker', 'reg/k:t', fakes.FakeEngine())


class ResolveTestCase(unittest.TestCase):
    def test_digest_from_repo_digests(self):
        fake = fakes.FakeEngine(remote={'reg:5050/k/kolla-ansible:t': fakes.image(
            GOOD, ['reg:5050/k/kolla-ansible@' + DIGEST])})
        self.assertEqual(('reg:5050/k/kolla-ansible@' + DIGEST, []),
                         engine.resolve('docker', 'reg:5050/k/kolla-ansible:t', fake))

    def test_digest_reference_is_kept(self):
        ref = 'reg/k@' + DIGEST
        fake = fakes.FakeEngine(remote={ref: fakes.image(GOOD)})
        self.assertEqual((ref, []), engine.resolve('docker', ref, fake))

    def test_local_only_image_is_pinned_by_id(self):
        fake = fakes.FakeEngine(local={'kolla/kolla-ansible:local': fakes.image(GOOD, image_id=DIGEST)})
        pinned, warnings = engine.resolve('docker', 'kolla/kolla-ansible:local', fake)
        self.assertEqual(DIGEST, pinned)
        self.assertEqual(2, len(warnings))

    def test_podman_image_id_gains_its_prefix(self):
        fake = fakes.FakeEngine(local={'localhost/k:local': fakes.image(GOOD, image_id='c' * 64)})
        self.assertEqual(DIGEST, engine.resolve('podman', 'localhost/k:local', fake)[0])

    def test_missing_image(self):
        self.assertRaises(LauncherError, engine.resolve, 'docker', 'reg/k:t', fakes.FakeEngine())


if __name__ == '__main__':
    unittest.main()
