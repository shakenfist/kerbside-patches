"""A stand-in for subprocess.run that plays the part of a container engine."""

import json
import subprocess


class FakeEngine:
    """Answers image inspect and pull for a set of images.

    local and remote map references to images made by image(); a pull copies
    from remote to local. An image is also found by its ID or a RepoDigests
    entry.
    """

    def __init__(self, local=None, remote=None, du='1720320\t/x\n', rm_status=0):
        self.local = dict(local or {})
        self.remote = dict(remote or {})
        self.calls = []
        self.du = du
        self.rm_status = rm_status

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
        if command[1] == 'create':
            if self._find(command[2]) is None:
                return subprocess.CompletedProcess(command, 125, stdout='')
            return subprocess.CompletedProcess(command, 0, stdout=CONTAINER + '\n')
        if command[1] == 'rm':
            return subprocess.CompletedProcess(command, self.rm_status)
        if command[0] == 'du':
            return subprocess.CompletedProcess(command, 0, stdout=self.du)
        raise AssertionError('unexpected command %s' % command)

    def _find(self, reference):
        if reference in self.local:
            return self.local[reference]
        for image in self.local.values():
            if reference == image['Id'] or reference in image['RepoDigests']:
                return image
        return None


def image(labels=None, repo_digests=None, image_id='sha256:' + 'b' * 64, env=None):
    return {'Config': {'Labels': labels, 'Env': env}, 'RepoDigests': repo_digests or [], 'Id': image_id}


CONTAINER = 'c' * 64


class FakeProcess:
    """What FakePopen returns: a process that has already finished."""

    def __init__(self, command, status, stdout=None):
        self.command = command
        self.status = status
        self.stdout = stdout
        self.signals = []

    def wait(self):
        return self.status

    def kill(self):
        pass

    def send_signal(self, signum):
        self.signals.append(signum)


class FakeStream:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakePopen:
    """A stand-in for subprocess.Popen; statuses maps a command's first word to its exit status."""

    def __init__(self, statuses=None):
        self.statuses = dict(statuses or {})
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        stdout = FakeStream() if kwargs.get('stdout') == subprocess.PIPE else None
        word = 'unshare' if command[0] == 'unshare' else ('tar' if command[0] == 'tar' else 'export')
        return FakeProcess(command, self.statuses.get(word, 0), stdout)
