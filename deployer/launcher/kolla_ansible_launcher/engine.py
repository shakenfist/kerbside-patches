"""The calls the launcher makes to the container engine itself.

These are image inspection and pulls, and the create, export and rm that
unpack an image for bootstrap-servers, which work the same way with docker and
podman. Each function takes the subprocess runner as an argument, so that
tests can stand in for the engine. The launcher never logs in to a registry:
that is the operator's job, as it is for any image.
"""

import json
import subprocess

from kolla_ansible_launcher import LauncherError


PROTOCOL_LABEL = 'kolla_ansible_launcher_protocol'
SUPPORTED_PROTOCOLS = ('1',)


def _inspect(engine, image, field, run):
    """Return the JSON value of one field of a local image, or None if it is absent."""
    result = run([engine, 'image', 'inspect', '--format', '{{json %s}}' % field, image],
                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip() or 'null')
    except ValueError:
        raise LauncherError('cannot parse %s image inspect output for %s: %s' % (engine, image, result.stdout))


def pull(engine, image, run):
    """Pull an image, with progress on stderr. Return True on success."""
    # File descriptor 2, so that progress stays off stdout, which "launcher show" uses.
    result = run([engine, 'pull', image], stdout=2)
    return result.returncode == 0


def is_local(engine, image, run):
    return _inspect(engine, image, '.Id', run) is not None


def labels(engine, image, run):
    """Return an image's labels, pulling it first if it is not present locally."""
    if not is_local(engine, image, run):
        if not pull(engine, image, run):
            raise LauncherError('cannot pull %s with %s. If the registry needs credentials, log in to it with '
                                '"%s login" as this user' % (image, engine, engine))
    value = _inspect(engine, image, '.Config.Labels', run)
    return value or {}


def image_env(engine, image, run):
    """Return an image's environment, from .Config.Env, as a dict."""
    env = {}
    for entry in _inspect(engine, image, '.Config.Env', run) or []:
        name, sep, value = entry.partition('=')
        if sep and name:
            env[name] = value
    return env


def create(engine, image, run):
    """Create, but do not start, a container from a local image. Return its ID."""
    result = run([engine, 'create', image], stdout=subprocess.PIPE, universal_newlines=True)
    container = (result.stdout or '').strip()
    if result.returncode != 0 or not container:
        raise LauncherError('cannot create a container from %s with %s' % (image, engine))
    return container


def export_command(engine, container):
    """Return the command that writes a container's filesystem as a tar stream on stdout."""
    return [engine, 'export', container]


def remove(engine, container, run):
    """Remove a container made by create(). Return True on success."""
    result = run([engine, 'rm', container], stdout=subprocess.DEVNULL)
    return result.returncode == 0


def check_protocol(image, image_labels):
    """Refuse an image whose launcher protocol this launcher does not speak."""
    protocol = image_labels.get(PROTOCOL_LABEL)
    if protocol is None:
        raise LauncherError('%s has no %s label, so it is not a deployer image this launcher can run'
                            % (image, PROTOCOL_LABEL))
    if protocol not in SUPPORTED_PROTOCOLS:
        raise LauncherError('%s speaks launcher protocol %s, but this launcher speaks only %s; install a launcher '
                            'that matches the image' % (image, protocol, ', '.join(SUPPORTED_PROTOCOLS)))


def split_reference(reference):
    """Split an image reference into (repository, tag, digest); tag and digest may be None."""
    name, _, digest = reference.partition('@')
    slash = name.rfind('/')
    colon = name.rfind(':')
    if colon > slash:
        return name[:colon], name[colon + 1:], digest or None
    return name, None, digest or None


def normalise_repository(repository):
    """Return a repository name in full, as docker.io/library/name for Docker Hub short names.

    Docker reports Docker Hub repositories by their short names and podman by
    their full ones, so the two spellings are compared in this form.
    """
    parts = repository.split('/')
    if len(parts) == 1 or not ('.' in parts[0] or ':' in parts[0] or parts[0] == 'localhost'):
        parts = ['docker.io'] + parts
    if parts[0] in ('index.docker.io', 'registry-1.docker.io'):
        parts[0] = 'docker.io'
    if parts[0] == 'docker.io' and len(parts) == 2:
        parts.insert(1, 'library')
    return '/'.join(parts)


def choose_digest(reference, repo_digests):
    """Return the digest in repo_digests for the reference's repository, or None."""
    wanted = normalise_repository(split_reference(reference)[0])
    for entry in repo_digests or []:
        repository, _, digest = entry.partition('@')
        if digest and normalise_repository(repository) == wanted:
            return digest
    return None


def resolve(engine, reference, run):
    """Pull reference and return (the reference to pin, warnings).

    A reference that already carries a digest is pinned as written. Otherwise
    the digest is taken from the image's RepoDigests entry for the same
    repository, and the pinned reference is the repository as written plus
    that digest.

    An image that only exists locally (built here and never pushed) has no
    registry digest. It is pinned by its image ID, with a warning: the ID is
    the only name that cannot move, but it means nothing on another host. A
    failed pull of an image that is present locally is a warning, not an
    error, for the same reason.
    """
    warnings = []
    if not pull(engine, reference, run):
        if not is_local(engine, reference, run):
            raise LauncherError('cannot pull %s with %s, and it is not present locally' % (reference, engine))
        warnings.append('could not pull %s; using the local copy' % reference)

    repository, _, digest = split_reference(reference)
    if digest:
        return reference, warnings

    digest = choose_digest(reference, _inspect(engine, reference, '.RepoDigests', run))
    if digest:
        return '%s@%s' % (repository, digest), warnings

    image_id = _inspect(engine, reference, '.Id', run)
    if not image_id:
        raise LauncherError('cannot inspect %s with %s after pulling it' % (reference, engine))
    if not image_id.startswith('sha256:'):
        image_id = 'sha256:' + image_id
    warnings.append('%s has no registry digest, so it is pinned by its local image ID %s, which only this host '
                    'knows' % (reference, image_id))
    return image_id, warnings
