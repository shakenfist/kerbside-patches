"""A stand-in for subprocess.run that plays the part of a container engine."""

import json
import subprocess


class FakeEngine:
    """Answers image inspect and pull for a set of images.

    local and remote map references to images made by image(); a pull copies
    from remote to local. An image is also found by its ID or a RepoDigests
    entry.
    """

    def __init__(self, local=None, remote=None):
        self.local = dict(local or {})
        self.remote = dict(remote or {})
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if command[1:3] == ['image', 'inspect']:
            image = self._find(command[-1])
            if image is None:
                return subprocess.CompletedProcess(command, 1, stdout='')
            field = command[4][len('{{json .'):-len('}}')]
            value = image
            for part in field.split('.'):
                value = value.get(part)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(value) + '\n')
        if command[1] == 'pull':
            image = self.remote.get(command[2])
            if image is None:
                return subprocess.CompletedProcess(command, 1)
            self.local[command[2]] = image
            return subprocess.CompletedProcess(command, 0)
        raise AssertionError('unexpected command %s' % command)

    def _find(self, reference):
        if reference in self.local:
            return self.local[reference]
        for image in self.local.values():
            if reference == image['Id'] or reference in image['RepoDigests']:
                return image
        return None


def image(labels=None, repo_digests=None, image_id='sha256:' + 'b' * 64):
    return {'Config': {'Labels': labels}, 'RepoDigests': repo_digests or [], 'Id': image_id}
