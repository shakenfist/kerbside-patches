"""The chroot helper: python -m kolla_ansible_launcher.chroot <spec.json>.

bootstrap-servers runs this inside "unshare --mount --propagation private", so
every mount it makes lives only in its own mount namespace and goes away with
it. It does no planning: the launcher writes the spec (see bootstrap.py), and
this makes the mounts the spec lists, chroots into the unpacked image, sets the
spec's environment and execs kolla-ansible. There is no PID namespace, so
nothing needs dumb-init: kolla-ansible replaces this process.

The spec is a JSON object:

    {"version": 1,
     "root": "/var/lib/kolla-ansible-launcher/bootstrap-.../rootfs",
     "mounts": [{"source": "/proc", "target": "/proc", "readonly": false,
                 "recursive": true, "replace_symlink": false}, ...],
     "env": {"PATH": "...", "HOME": "/root"},
     "cwd": "/home/debian",
     "argv": ["kolla-ansible", "bootstrap-servers", ...]}

It is a file rather than arguments because the environment may hold secrets,
which would be visible to every user in the process list, and because stdin
belongs to kolla-ansible.

A mount point inside the image is never reached through a symlink: mount
follows symlinks against the host's root, not the image's, so one would put
the mount somewhere else entirely. A path through a symlink is refused, except
where the spec allows the last component to be replaced (/etc/resolv.conf and
/etc/hosts, which some images make symlinks).
"""

import json
import os
import stat
import subprocess
import sys

from kolla_ansible_launcher import LauncherError


SPEC_VERSION = 1
PROGRAM = 'kolla-ansible'


def load(path):
    try:
        with open(path) as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        raise LauncherError('cannot read the chroot spec %s: %s' % (path, e))
    if spec.get('version') != SPEC_VERSION:
        raise LauncherError('%s is chroot spec version %s, not %d' % (path, spec.get('version'), SPEC_VERSION))
    return spec


def in_root(root, target):
    """Return the path of an absolute target inside root."""
    return os.path.join(root, target.lstrip('/'))


def commands_for(root, mount):
    """Return the mount commands for one spec entry."""
    target = in_root(root, mount['target'])
    command = [['mount', '--rbind' if mount.get('recursive', True) else '--bind', mount['source'], target]]
    if mount.get('readonly'):
        command.append(['mount', '-o', 'remount,bind,ro', target])
    return command


def mount_commands(spec):
    """Return every mount command for a spec, in order."""
    commands = []
    for mount in spec['mounts']:
        commands += commands_for(spec['root'], mount)
    return commands


def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def prepare_target(root, target, is_dir, replace_symlink=False):
    """Make sure the mount point for target exists in root, and is not reached through a symlink.

    Missing directories are created, and a missing last component is created as
    a directory or an empty file to match the source.
    """
    parts = [p for p in target.split('/') if p]
    path = root
    for i, part in enumerate(parts):
        path = os.path.join(path, part)
        last = i == len(parts) - 1
        st = _lstat(path)
        if st is not None and stat.S_ISLNK(st.st_mode):
            if not (last and replace_symlink):
                raise LauncherError('cannot mount %s: %s is a symlink in the deployer image' % (target, path))
            os.unlink(path)
            st = None
        if st is None:
            if last and not is_dir:
                with open(path, 'w'):
                    pass
            else:
                os.mkdir(path, 0o755)
        elif not last and not stat.S_ISDIR(st.st_mode):
            raise LauncherError('cannot mount %s: %s is not a directory in the deployer image' % (target, path))
    return path


def mount_all(spec, run=subprocess.run, isdir=os.path.isdir):
    for mount in spec['mounts']:
        prepare_target(spec['root'], mount['target'], isdir(mount['source']), mount.get('replace_symlink', False))
        for command in commands_for(spec['root'], mount):
            result = run(command)
            if result.returncode != 0:
                raise LauncherError('%s failed with exit status %d' % (' '.join(command), result.returncode))


def enter(spec, chroot=os.chroot, chdir=os.chdir, warn=None):
    """chroot into the spec's root and change to its working directory, or / if that is missing."""
    chroot(spec['root'])
    try:
        chdir(spec['cwd'])
    except OSError:
        if warn:
            warn('%s does not exist in the deployer image; running in /' % spec['cwd'])
        chdir('/')


def _warn(message):
    print('%s: warning: %s' % (PROGRAM, message), file=sys.stderr)


def main(argv=None, run=subprocess.run, chroot=os.chroot, chdir=os.chdir, execvpe=os.execvpe):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 1:
            raise LauncherError('usage: python -m kolla_ansible_launcher.chroot <spec.json>')
        spec = load(argv[0])
        mount_all(spec, run)
        enter(spec, chroot, chdir, _warn)
    except (LauncherError, OSError) as e:
        print('%s: error: %s' % (PROGRAM, e), file=sys.stderr)
        return 1
    sys.stdout.flush()
    sys.stderr.flush()
    # The image's PATH, from the spec's environment, finds the image's kolla-ansible.
    try:
        execvpe(PROGRAM, spec['argv'], spec['env'])
    except OSError as e:
        print('%s: error: cannot run %s in the deployer image: %s' % (PROGRAM, PROGRAM, e), file=sys.stderr)
        return 127


if __name__ == '__main__':
    sys.exit(main())
