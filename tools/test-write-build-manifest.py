#!/usr/bin/env python3
"""Tests for write-build-manifest.

Every HTTP exchange is answered by a fake session, so this runs offline and
needs no registry credentials. The fake behaves like the GitLab container
registry: an anonymous request gets a 401 naming a token realm, and only a
request carrying the bearer token that realm issued for the right scope is
answered.

Usage:
    ./test-write-build-manifest.py
"""

import base64
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from unittest import mock

import requests
from click.testing import CliRunner
from requests.structures import CaseInsensitiveDict

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = SourceFileLoader('write_build_manifest', os.path.join(HERE, 'write-build-manifest'))
wbm = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(wbm)

REGISTRY = 'https://registry.example:5050'
REALM = 'https://gitlab.example/jwt/auth'
PROJECT = 'openstack/kolla-images'
TAG = 'master-debian-trixie-abc123'
USERNAME = 'kolla-imgs'
SECRET = 'not-a-real-token'


def digest_of(name):
    """A stable, well formed digest for a test image."""
    return 'sha256:' + (name.encode().hex() * 64)[:64]


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = CaseInsensitiveDict(headers or {})

    def json(self):
        if self._body is None:
            raise ValueError('no body')
        return self._body


class FakeRegistrySession:
    """Registry v2 fake, keyed by URL."""

    def __init__(self, tagged, missing_manifests=(), labels=None, deployer=True, prefix=''):
        # tagged: image names (without prefix) carrying TAG.
        self.tagged = [prefix + n for n in tagged]
        self.missing_manifests = {prefix + n for n in missing_manifests}
        self.labels = labels
        self.deployer = deployer
        self.calls = []
        self.token_requests = []

    @staticmethod
    def _scope(repo):
        return f'repository:{repo}:pull'

    def _token_for(self, scope):
        return f'bearer-for-{scope}'

    # requests.Session's interface, as far as the tool uses it.
    def get(self, url, headers=None, params=None, auth=None, timeout=None):
        return self.request('GET', url, headers=headers, params=params, auth=auth, timeout=timeout)

    def request(self, method, url, headers=None, params=None, auth=None, timeout=None):
        headers = dict(headers or {})
        self.calls.append((method, url, headers, params, auth))
        if url == REALM:
            return self._realm(params, auth)
        if url.startswith(f'{REGISTRY}/v2/'):
            return self._registry(method, url[len(f'{REGISTRY}/v2/'):], headers)
        raise AssertionError(f'unexpected request {method} {url}')

    def _realm(self, params, auth):
        self.token_requests.append(params['scope'])
        if auth != (USERNAME, SECRET) or params.get('service') != 'container_registry':
            return FakeResponse(401)
        return FakeResponse(200, {'token': self._token_for(params['scope'])})

    def _registry(self, method, path, headers):
        repo, sep, rest = path.partition('/manifests/')
        kind = 'manifests'
        if not sep:
            repo, sep, rest = path.partition('/blobs/')
            kind = 'blobs'
        scope = self._scope(repo)
        if headers.get('Authorization') != f'Bearer {self._token_for(scope)}':
            challenge = f'Bearer realm="{REALM}",service="container_registry",scope="{scope}"'
            return FakeResponse(401, headers={'WWW-Authenticate': challenge})

        name = repo[len(PROJECT) + 1:]
        if kind == 'manifests':
            assert headers.get('Accept') == wbm.SCHEMA2_MEDIA_TYPE
            known = self.tagged + ([wbm.DEPLOYER_IMAGE] if self.deployer else [])
            if rest != TAG or name not in known or name in self.missing_manifests:
                return FakeResponse(404, {'errors': [{'code': 'MANIFEST_UNKNOWN'}]})
            body = {'schemaVersion': 2, 'mediaType': wbm.SCHEMA2_MEDIA_TYPE,
                    'config': {'digest': digest_of(f'{name}-config')}, 'layers': []}
            return FakeResponse(200, body if method == 'GET' else None,
                                {'Content-Type': wbm.SCHEMA2_MEDIA_TYPE, 'Docker-Content-Digest': digest_of(name)})

        assert name == wbm.DEPLOYER_IMAGE and rest == digest_of(f'{name}-config')
        config = {'config': {'User': 'kolla'}}
        if self.labels is not None:
            config['config']['Labels'] = self.labels
        return FakeResponse(200, config)


def make_registry(session):
    return wbm.Registry(session, REGISTRY, USERNAME, SECRET)


class TokenHandshakeTests(unittest.TestCase):
    def test_bearer_token_is_fetched_with_basic_auth_and_cached_per_scope(self):
        session = FakeRegistrySession(['nova-api'])
        registry = make_registry(session)

        self.assertEqual(digest_of('nova-api'), registry.manifest_digest(f'{PROJECT}/nova-api', TAG))
        self.assertEqual(digest_of('nova-api'), registry.manifest_digest(f'{PROJECT}/nova-api', TAG))

        self.assertEqual([f'repository:{PROJECT}/nova-api:pull'], session.token_requests)
        realm_calls = [c for c in session.calls if c[1] == REALM]
        self.assertEqual((USERNAME, SECRET), realm_calls[0][4])
        self.assertEqual({'service': 'container_registry', 'scope': f'repository:{PROJECT}/nova-api:pull'},
                         realm_calls[0][3])

    def test_a_refused_token_request_fails(self):
        session = FakeRegistrySession(['nova-api'])
        registry = wbm.Registry(session, REGISTRY, USERNAME, 'wrong')
        with self.assertRaisesRegex(wbm.ManifestError, 'refused'):
            registry.manifest_digest(f'{PROJECT}/nova-api', TAG)

    def test_the_token_never_appears_in_an_error(self):
        session = FakeRegistrySession(['nova-api'])
        registry = wbm.Registry(session, REGISTRY, USERNAME, 'wrong')
        try:
            registry.manifest_digest(f'{PROJECT}/nova-api', TAG)
        except wbm.ManifestError as e:
            self.assertNotIn('wrong', str(e))
            self.assertNotIn(base64.b64encode(b'kolla-imgs:wrong').decode(), str(e))

    def test_parse_challenge(self):
        scheme, params = wbm.parse_challenge('Bearer realm="https://r/jwt/auth",service="svc",scope="a:b:pull"')
        self.assertEqual('bearer', scheme)
        self.assertEqual({'realm': 'https://r/jwt/auth', 'service': 'svc', 'scope': 'a:b:pull'}, params)
        self.assertEqual((None, {}), wbm.parse_challenge(None))


class DigestReadTests(unittest.TestCase):
    def test_head_uses_the_schema2_accept_header_and_returns_docker_content_digest(self):
        session = FakeRegistrySession(['glance-api'])
        registry = make_registry(session)
        self.assertEqual(digest_of('glance-api'), registry.manifest_digest(f'{PROJECT}/glance-api', TAG))
        heads = [c for c in session.calls if c[0] == 'HEAD']
        self.assertTrue(heads)
        for call in heads:
            self.assertEqual(wbm.SCHEMA2_MEDIA_TYPE, call[2]['Accept'])

    def test_a_missing_tag_is_none(self):
        registry = make_registry(FakeRegistrySession(['glance-api']))
        self.assertIsNone(registry.manifest_digest(f'{PROJECT}/glance-api', 'other-tag'))

    def test_a_manifest_of_another_media_type_fails(self):
        session = mock.Mock()
        session.request.return_value = FakeResponse(
            200, headers={'Content-Type': 'application/vnd.oci.image.index.v1+json',
                          'Docker-Content-Digest': digest_of('x')})
        registry = wbm.Registry(session, REGISTRY, USERNAME, SECRET)
        with self.assertRaisesRegex(wbm.ManifestError, 'not a Docker v2 schema 2 manifest'):
            registry.manifest_digest(f'{PROJECT}/x', TAG)

    def test_a_response_without_a_digest_fails(self):
        session = mock.Mock()
        session.request.return_value = FakeResponse(200, headers={'Content-Type': wbm.SCHEMA2_MEDIA_TYPE})
        registry = wbm.Registry(session, REGISTRY, USERNAME, SECRET)
        with self.assertRaisesRegex(wbm.ManifestError, 'Docker-Content-Digest'):
            registry.manifest_digest(f'{PROJECT}/x', TAG)

    def test_a_server_error_fails(self):
        session = mock.Mock()
        session.request.return_value = FakeResponse(503)
        registry = wbm.Registry(session, REGISTRY, USERNAME, SECRET)
        with self.assertRaisesRegex(wbm.ManifestError, 'HTTP 503'):
            registry.manifest_digest(f'{PROJECT}/x', TAG)

    def test_a_connection_failure_fails(self):
        session = mock.Mock()
        session.request.side_effect = requests.ConnectionError('boom')
        registry = wbm.Registry(session, REGISTRY, USERNAME, SECRET)
        with self.assertRaisesRegex(wbm.ManifestError, 'ConnectionError'):
            registry.manifest_digest(f'{PROJECT}/x', TAG)


def write_images_file(directory, content):
    path = os.path.join(directory, 'images')
    with open(path, 'w') as f:
        f.write(content)
    return path


class ImagesFileTests(unittest.TestCase):
    def read(self, content):
        with tempfile.TemporaryDirectory() as d:
            return wbm.read_images_file(write_images_file(d, content))

    def test_blank_lines_duplicates_and_the_deployer_are_dropped_and_names_sorted(self):
        names = self.read('nova-api\n\n  \nbase\nnova-api\nkolla-ansible\nmariadb-server\n')
        self.assertEqual(['base', 'mariadb-server', 'nova-api'], names)

    def test_a_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(wbm.ManifestError, 'could not be read'):
                wbm.read_images_file(os.path.join(d, 'nope'))

    def test_an_empty_file_fails(self):
        with self.assertRaisesRegex(wbm.ManifestError, 'names no images'):
            self.read('')
        with self.assertRaisesRegex(wbm.ManifestError, 'names no images'):
            self.read('\n\nkolla-ansible\n')


class BuildManifestTests(unittest.TestCase):
    def test_manifest_shape(self):
        session = FakeRegistrySession(['nova-api', 'base', 'mariadb-server'])
        manifest = wbm.build_manifest(make_registry(session), PROJECT, TAG, ['base', 'mariadb-server', 'nova-api'])

        self.assertEqual({'format_version', 'tag', 'images'}, set(manifest))
        self.assertEqual(1, manifest['format_version'])
        self.assertEqual(TAG, manifest['tag'])
        self.assertEqual({'base': digest_of('base'), 'mariadb-server': digest_of('mariadb-server'),
                          'nova-api': digest_of('nova-api')}, manifest['images'])
        self.assertEqual(sorted(manifest['images']), list(manifest['images']))

    def test_only_listed_images_are_read(self):
        session = FakeRegistrySession(['nova-api', 'glance-api'])
        manifest = wbm.build_manifest(make_registry(session), PROJECT, TAG, ['nova-api'])
        self.assertEqual(['nova-api'], list(manifest['images']))
        self.assertFalse([c for c in session.calls if 'glance-api' in c[1]])

    def test_prefix_is_applied_to_the_repository_and_the_key(self):
        session = FakeRegistrySession(['nova-api'], prefix='pre-')
        manifest = wbm.build_manifest(make_registry(session), PROJECT, TAG, ['nova-api'], prefix='pre-')
        self.assertEqual({'pre-nova-api': digest_of('pre-nova-api')}, manifest['images'])
        self.assertTrue([c for c in session.calls if f'/v2/{PROJECT}/pre-nova-api/manifests/' in c[1]])

    def test_a_listed_image_with_no_manifest_fails(self):
        session = FakeRegistrySession(['nova-api', 'glance-api'], missing_manifests=['glance-api'])
        with self.assertRaisesRegex(wbm.ManifestError, 'glance-api'):
            wbm.build_manifest(make_registry(session), PROJECT, TAG, ['glance-api', 'nova-api'])

    def test_a_registry_error_fails_rather_than_skipping(self):
        session = mock.Mock()
        session.request.return_value = FakeResponse(503)
        with self.assertRaisesRegex(wbm.ManifestError, 'HTTP 503'):
            wbm.build_manifest(make_registry(session), PROJECT, TAG, ['nova-api'])


class DeployerStateTests(unittest.TestCase):
    def test_missing(self):
        registry = make_registry(FakeRegistrySession(['nova-api'], deployer=False))
        self.assertEqual('missing', wbm.deployer_state(registry, PROJECT, TAG))

    def test_manifested(self):
        registry = make_registry(FakeRegistrySession(['nova-api'], labels={wbm.MANIFEST_LABEL: '1'}))
        self.assertEqual('manifested', wbm.deployer_state(registry, PROJECT, TAG))

    def test_unmanifested_returns_the_deployer_digest(self):
        for labels in (None, {}, {'other': 'x'}):
            registry = make_registry(FakeRegistrySession(['nova-api'], labels=labels))
            self.assertEqual(digest_of(wbm.DEPLOYER_IMAGE), wbm.deployer_state(registry, PROJECT, TAG))


class CommandLineTests(unittest.TestCase):
    def run_cli(self, session, args, prefix=None):
        runner = CliRunner()
        extra = ['--image-prefix', prefix] if prefix else []
        with mock.patch.object(wbm, 'make_session', return_value=session):
            return runner.invoke(wbm.cli, ['--registry', REGISTRY, '--project', PROJECT,
                                           '--username', USERNAME] + extra + args, env={'REGISTRY_TOKEN': SECRET})

    def test_write(self):
        with tempfile.TemporaryDirectory() as d:
            output = os.path.join(d, 'manifest.json')
            images = write_images_file(d, 'nova-api\n\nkeystone\nnova-api\nkolla-ansible\n')
            result = self.run_cli(FakeRegistrySession(['nova-api', 'keystone']),
                                  ['write', TAG, output, '--images-file', images])
            self.assertEqual(0, result.exit_code, result.output)
            with open(output) as f:
                manifest = json.load(f)
            self.assertEqual({'keystone', 'nova-api'}, set(manifest['images']))
            self.assertEqual(0o644, os.stat(output).st_mode & 0o777)
            self.assertNotIn(SECRET, result.output)

    def test_write_with_a_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            output = os.path.join(d, 'manifest.json')
            images = write_images_file(d, 'nova-api\n')
            result = self.run_cli(FakeRegistrySession(['nova-api'], prefix='pre-'),
                                  ['write', TAG, output, '--images-file', images], prefix='pre-')
            self.assertEqual(0, result.exit_code, result.output)
            with open(output) as f:
                self.assertEqual(['pre-nova-api'], list(json.load(f)['images']))

    def test_write_requires_the_images_file(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.run_cli(FakeRegistrySession(['nova-api']), ['write', TAG, os.path.join(d, 'm.json')])
            self.assertNotEqual(0, result.exit_code)
            self.assertEqual([], os.listdir(d))

    def test_write_with_a_missing_or_empty_images_file_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            output = os.path.join(d, 'manifest.json')
            for images in (os.path.join(d, 'absent'), write_images_file(d, '\n')):
                result = self.run_cli(FakeRegistrySession(['nova-api']),
                                      ['write', TAG, output, '--images-file', images])
                self.assertEqual(1, result.exit_code)
                self.assertIn('ERROR:', result.output)
                self.assertFalse(os.path.exists(output))

    def test_write_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            output = os.path.join(d, 'manifest.json')
            images = write_images_file(d, 'nova-api\nkeystone\n')
            session = FakeRegistrySession(['nova-api', 'keystone'], missing_manifests=['keystone'])
            result = self.run_cli(session, ['write', TAG, output, '--images-file', images])
            self.assertEqual(1, result.exit_code)
            self.assertIn('ERROR:', result.output)
            self.assertFalse(os.path.exists(output))

    def test_deployer_state(self):
        result = self.run_cli(FakeRegistrySession(['nova-api'], labels={wbm.MANIFEST_LABEL: '1'}),
                              ['deployer-state', TAG])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual('manifested\n', result.output)


if __name__ == '__main__':
    unittest.main()
