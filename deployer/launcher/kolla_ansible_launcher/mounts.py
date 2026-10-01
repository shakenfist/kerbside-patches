"""Turn host paths into same-path bind mounts.

Every path is mounted at the path it has on the host (top-level Decision 2),
so absolute paths in globals.yml and the inventory keep working. A directory is
mounted itself; for a file, or a path that does not exist yet, the nearest
existing directory above it is mounted, because Ansible reads group_vars/ and
host_vars/ from beside an inventory file, and @file vars commonly include
their neighbours.

A directory the image itself depends on (/, /etc, /usr and so on) is never
mounted over the image. A file in one is mounted alone instead; anything else
there is refused. This guard is the launcher's own addition to the plan: a
vault password file in /etc would otherwise replace the container's /etc.
"""

import os

from kolla_ansible_launcher import LauncherError


# Mounting any of these directories themselves would hide the image's own.
_PROTECTED_DIRS = frozenset(('/', '/etc', '/var', '/var/lib', '/run', '/opt'))

# Nothing at or below these is mounted as a directory.
_PROTECTED_TREES = ('/bin', '/boot', '/dev', '/lib', '/lib32', '/lib64', '/libx32', '/proc', '/sbin', '/sys', '/usr',
                    '/var/lib/kolla', '/var/lib/kolla-ansible')


class Mount:
    """A bind mount of source on the host at target in the container."""

    def __init__(self, source, target=None, readonly=False):
        self.source = source
        self.target = source if target is None else target
        self.readonly = readonly

    def volume(self):
        """Return the value for the engine's --volume option."""
        for path in (self.source, self.target):
            if ':' in path:
                raise LauncherError('cannot mount %s: the path contains a colon' % path)
        return '%s:%s%s' % (self.source, self.target, ':ro' if self.readonly else '')

    def __eq__(self, other):
        return (isinstance(other, Mount) and
                (self.source, self.target, self.readonly) == (other.source, other.target, other.readonly))

    def __repr__(self):
        return 'Mount(%r, %r, readonly=%r)' % (self.source, self.target, self.readonly)


def is_protected(directory):
    """Return True when a directory must not be mounted over the image."""
    if directory in _PROTECTED_DIRS:
        return True
    return any(directory == tree or directory.startswith(tree + '/') for tree in _PROTECTED_TREES)


def mount_for(path):
    """Return the same-path Mount that makes an absolute host path visible."""
    if os.path.isdir(path):
        directory = path
    else:
        directory = os.path.dirname(path)
        while directory != '/' and not os.path.isdir(directory):
            directory = os.path.dirname(directory)

    if not is_protected(directory):
        return Mount(directory)
    if os.path.isfile(path) and not is_protected(path):
        return Mount(path)
    raise LauncherError('refusing to mount %s over the deployer image, which %s needs; move it elsewhere'
                        % (directory, path))


def _under(path, parent):
    return parent == '/' or path.startswith(parent + '/')


def deduplicate(mounts):
    """Drop mounts that another mount already provides.

    Mounts at the same target collapse into one, read-write if any of them is.
    A same-path mount under another same-path mount is dropped when the outer
    one is read-write, or both are read-only. A read-write mount under a
    read-only one is kept, so that it stays writable. Mounts whose source and
    target differ (the synthesised passwd and group files) are never dropped.
    The result is sorted by target, so that parents come before children.
    """
    by_target = {}
    for m in mounts:
        existing = by_target.get(m.target)
        if existing is None:
            by_target[m.target] = Mount(m.source, m.target, m.readonly)
        elif existing.source != m.source:
            raise LauncherError('two different paths are mounted at %s: %s and %s'
                                % (m.target, existing.source, m.source))
        else:
            existing.readonly = existing.readonly and m.readonly

    kept = []
    for m in sorted(by_target.values(), key=lambda m: m.target):
        if m.source == m.target:
            covered = any(k.source == k.target and _under(m.target, k.target) and
                          (not k.readonly or m.readonly) for k in kept)
            if covered:
                continue
        kept.append(m)
    return kept
