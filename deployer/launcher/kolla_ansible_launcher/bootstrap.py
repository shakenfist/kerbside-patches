"""Run kolla-ansible bootstrap-servers from an unpacked deployer image.

bootstrap-servers installs the container engine Kolla deploys with, and on
Debian that replaces the distribution's engine, killing every container it
runs. So the deployer cannot be one of them. Instead the launcher unpacks the
image's root filesystem into a fresh directory under
/var/lib/kolla-ansible-launcher, with "<engine> create", "<engine> export"
piped into tar, and "<engine> rm", and runs the image's kolla-ansible there,
chrooted, inside a private mount namespace:

    unshare --mount --propagation private -- <python> -I -m kolla_ansible_launcher.chroot <spec.json>

The helper (chroot.py) bind-mounts /proc, /sys and /dev, the host's
/etc/resolv.conf and /etc/hosts, and the same-path mounts a container run
would have, then chroots and execs kolla-ansible with the image's environment
and HOME=/root. The launcher waits for it, deletes the directory, and returns
its exit status.

The deletion never crosses a mount. The helper's mounts live in a namespace
the deletion does not run in, which is the real protection; in addition the
directory is left in place, with a warning, if the launcher's own namespace
shows anything mounted under it, and the deletion refuses to descend into any
directory on a different device from the top.

All of this needs root: unpacking with ownership, mounting and chroot.
"""

import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import args as args_mod
from kolla_ansible_launcher import command as command_mod
from kolla_ansible_launcher import config as config_mod
from kolla_ansible_launcher import engine as engine_mod


SUBCOMMAND = 'bootstrap-servers'
WORK_BASE = '/var/lib/kolla-ansible-launcher'
HELPER_MODULE = 'kolla_ansible_launcher.chroot'
ROOT_HOME = '/root'
MOUNTINFO = '/proc/self/mountinfo'
SYSTEM_DIRS = ('/proc', '/sys', '/dev')
SYSTEM_FILES = ('/etc/resolv.conf', '/etc/hosts')
EXTRA_FORWARDED = ('TERM',)


class CrossesMount(LauncherError):
    """The deletion met a directory on a different device from its top."""


def _warn(message):
    print('kolla-ansible: warning: %s' % message, file=sys.stderr)


def refuse_unless_root(euid):
    if euid != 0:
        raise LauncherError('bootstrap-servers through the launcher needs root, to unpack the deployer image and '
                            'chroot into it; run it with sudo')


def _entry(source, target=None, readonly=False, recursive=True, replace_symlink=False):
    return {'source': source, 'target': source if target is None else target, 'readonly': readonly,
            'recursive': recursive, 'replace_symlink': replace_symlink}


def system_mounts(exists=os.path.exists):
    """Return the mounts every chroot has: the kernel filesystems, and the host's resolver files read-only."""
    mounts = [_entry(d) for d in SYSTEM_DIRS]
    mounts += [_entry(f, readonly=True, recursive=False, replace_symlink=True) for f in SYSTEM_FILES if exists(f)]
    return mounts


def chroot_env(image_environment, environ):
    """Return the chroot's environment: the image's, the forwarded host variables, and HOME=/root."""
    env = dict(image_environment)
    for name in command_mod.forwarded_env(environ):
        env[name] = environ[name]
    for name in EXTRA_FORWARDED:
        if name in environ:
            env[name] = environ[name]
    env['HOME'] = ROOT_HOME
    return env


def build_spec(root, argv, environ, cwd, configdir, home, extra_mounts, image_environment, exists=os.path.exists):
    """Return the helper's spec for one run. collect_mounts may refuse a path."""
    mounts = system_mounts(exists)
    for m in command_mod.collect_mounts(args_mod.KOLLA_ANSIBLE, argv, cwd, environ, configdir, home, extra_mounts):
        mounts.append(_entry(m.source, m.target, readonly=m.readonly))
    return {'version': 1, 'root': root, 'mounts': mounts, 'env': chroot_env(image_environment, environ),
            'cwd': cwd, 'argv': [args_mod.KOLLA_ANSIBLE] + list(argv)}


def helper_command(spec_path, python=None):
    python = sys.executable if python is None else python
    return ['unshare', '--mount', '--propagation', 'private', '--', python, '-I', '-m', HELPER_MODULE, spec_path]


def tar_command(rootfs):
    return ['tar', '-x', '-p', '--numeric-owner', '-f', '-', '-C', rootfs]


def write_spec(path, spec):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(spec, f, indent=2, sort_keys=True)


def make_run_dir(base=WORK_BASE):
    """Make a fresh run directory, mode 0700, with an empty rootfs in it. Return (run_dir, rootfs)."""
    os.makedirs(base, mode=0o700, exist_ok=True)
    run_dir = tempfile.mkdtemp(prefix='bootstrap-', dir=base)
    rootfs = os.path.join(run_dir, 'rootfs')
    os.mkdir(rootfs, 0o755)
    return run_dir, rootfs


def export_into(engine, container, rootfs, popen):
    """Pipe "<engine> export" into tar. Raise unless both succeed."""
    exporter = popen(engine_mod.export_command(engine, container), stdout=subprocess.PIPE)
    try:
        tar = popen(tar_command(rootfs), stdin=exporter.stdout)
    except OSError:
        exporter.kill()
        exporter.wait()
        raise
    # Only tar holds the pipe now, so the exporter sees EPIPE if tar dies.
    exporter.stdout.close()
    tar_status = tar.wait()
    export_status = exporter.wait()
    if export_status != 0:
        raise LauncherError('%s export of the deployer image failed with exit status %d' % (engine, export_status))
    if tar_status != 0:
        raise LauncherError('unpacking the deployer image into %s failed: tar exited %d' % (rootfs, tar_status))


def unpack(engine, image, rootfs, run, popen):
    """Unpack image's filesystem into rootfs with create, export and rm."""
    container = engine_mod.create(engine, image, run)
    try:
        export_into(engine, container, rootfs, popen)
    finally:
        if not engine_mod.remove(engine, container, run):
            _warn('could not remove the container %s; remove it with "%s rm %s"' % (container, engine, container))


def disk_usage(path, run):
    """Return du -sx's size of path in KiB, or None."""
    result = run(['du', '-sx', path], stdout=subprocess.PIPE, universal_newlines=True)
    try:
        return int(result.stdout.split()[0]) if result.returncode == 0 else None
    except (IndexError, ValueError, AttributeError):
        return None


def wait_for(process):
    """Wait for the helper, leaving SIGINT to it and passing SIGTERM on. Return a shell-style status.

    The terminal sends SIGINT to the whole process group, so the launcher only
    has to survive it: otherwise its cleanup would delete the tree under a
    running Ansible. It is ignored only after the helper starts, because an
    ignored signal is inherited across exec.
    """
    previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
    previous_term = signal.signal(signal.SIGTERM, lambda signum, frame: process.send_signal(signum))
    try:
        status = process.wait()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    return 128 - status if status < 0 else status


def _unescape(field):
    """Undo mountinfo's octal escapes (\\040 for a space)."""
    out = []
    i = 0
    while i < len(field):
        digits = field[i + 1:i + 4]
        if field[i] == '\\' and len(digits) == 3 and digits.isdigit():
            out.append(chr(int(digits, 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return ''.join(out)


def mounts_under(path, mountinfo_text):
    """Return the mount points in a mountinfo text that are path or below it."""
    found = []
    for line in mountinfo_text.splitlines():
        fields = line.split(' ')
        if len(fields) < 5:
            continue
        point = _unescape(fields[4])
        if point == path or point.startswith(path.rstrip('/') + '/'):
            found.append(point)
    return found


def safe_rmtree(top, lstat=os.lstat, listdir=os.listdir, unlink=os.unlink, rmdir=os.rmdir):
    """Delete a tree without following symlinks or descending into another device.

    Raises CrossesMount, before touching anything below it, at the first
    directory whose st_dev differs from top's. What was deleted before then
    stays deleted; the rest is left.
    """
    device = lstat(top).st_dev

    def remove(directory):
        for name in listdir(directory):
            path = os.path.join(directory, name)
            st = lstat(path)
            if stat.S_ISDIR(st.st_mode):
                if st.st_dev != device:
                    raise CrossesMount('%s is on a different device from %s' % (path, top))
                remove(path)
            else:
                unlink(path)
        rmdir(directory)

    remove(top)


def cleanup(run_dir, mountinfo=MOUNTINFO, rmtree=safe_rmtree, warn=_warn):
    """Delete the run directory unless something is mounted in it. Return True when it is gone."""
    try:
        with open(mountinfo) as f:
            mounted = mounts_under(run_dir, f.read())
    except OSError as e:
        warn('cannot read %s (%s), so %s is left in place; delete it by hand' % (mountinfo, e, run_dir))
        return False
    if mounted:
        warn('%s is left in place, because %s is mounted in it; unmount it and delete the directory by hand'
             % (run_dir, ', '.join(mounted)))
        return False
    try:
        rmtree(run_dir)
    except CrossesMount as e:
        warn('stopped deleting %s, which is left in place: %s' % (run_dir, e))
        return False
    except OSError as e:
        warn('could not delete %s, which is left in place: %s' % (run_dir, e))
        return False
    return True


def run(argv, environ, cwd, runner=subprocess.run, popen=subprocess.Popen, euid=None, home=None,
        work_base=WORK_BASE, clock=time.monotonic, which=shutil.which):
    """Run kolla-ansible bootstrap-servers from an unpacked image. Return the exit status."""
    euid = os.geteuid() if euid is None else euid
    refuse_unless_root(euid)
    home = pwd.getpwuid(euid).pw_dir if home is None else home

    configdir = args_mod.configdir(args_mod.KOLLA_ANSIBLE, argv, environ, cwd)
    config = config_mod.load(configdir)
    for message in config.warnings:
        _warn(message)
    engine = config_mod.choose_engine(config, which)
    engine_mod.check_protocol(config.image, engine_mod.labels(engine, config.image, runner))
    image_environment = engine_mod.image_env(engine, config.image, runner)

    try:
        run_dir, rootfs = make_run_dir(work_base)
    except OSError as e:
        raise LauncherError('cannot make a directory to unpack the deployer image in under %s: %s' % (work_base, e))
    try:
        return _unpack_and_run(engine, config, argv, environ, cwd, configdir, home, image_environment, run_dir,
                               rootfs, runner, popen, clock)
    except OSError as e:
        raise LauncherError('bootstrap-servers failed in %s: %s' % (run_dir, e))
    finally:
        cleanup(run_dir)


def _unpack_and_run(engine, config, argv, environ, cwd, configdir, home, image_environment, run_dir, rootfs,
                    runner, popen, clock):
    # Planned before the unpack, so a refused path costs nothing.
    spec = build_spec(rootfs, argv, environ, cwd, configdir, home, config.mounts, image_environment)
    started = clock()
    unpack(engine, config.image, rootfs, runner, popen)
    size = disk_usage(rootfs, runner)
    print('kolla-ansible: unpacked %s with %s into %s in %.1fs, %s (du -sx)'
          % (config.image, engine, rootfs, clock() - started,
             'unknown size' if size is None else '%.0f MiB' % (size / 1024.0)), file=sys.stderr)

    spec_path = os.path.join(run_dir, 'spec.json')
    write_spec(spec_path, spec)
    sys.stdout.flush()
    sys.stderr.flush()
    return wait_for(popen(helper_command(spec_path)))
