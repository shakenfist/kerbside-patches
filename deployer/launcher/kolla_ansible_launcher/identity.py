"""Synthesise the invoking user's identity for the container.

The container runs as the invoking user's UID and GID, which the image knows
nothing about. OpenSSH refuses to run as a UID with no passwd entry. So the
launcher writes a passwd file with root and the user, and a matching group
file, and mounts both read-only over /etc/passwd and /etc/group. The user's
entry comes from getpwuid(), so LDAP and sssd users work too.

The entry's home directory is the image's HOME, /var/lib/kolla-ansible, not
the user's own. Ansible expands ~ on its local connection as ~<user>, from the
passwd entry rather than $HOME, so with the real home directory it tries to
create ~/.ansible/tmp somewhere the container cannot write. The user's ~/.ssh
is mounted read-only at <image home>/.ssh, where OpenSSH looks for it, and also
at its own path, for configuration that names it absolutely (see command.py).

The files are rewritten on every run because the launcher execs the engine and
cannot clean up after it. Each is replaced atomically, so a container that is
still running keeps the copy it started with.
"""

import grp
import os
import pwd


ROOT_PASSWD = 'root:x:0:0:root:/root:/bin/sh'
# The image's HOME, which it makes world-writable; part of launcher protocol 1.
CONTAINER_HOME = '/var/lib/kolla-ansible'
ROOT_GROUP = 'root:x:0:'
# The user's own shell may not exist in the image, and nothing logs in with it.
SHELL = '/bin/sh'


def _field(value):
    """Make a value safe for a colon-separated line."""
    return str(value).replace(':', ' ').replace('\n', ' ')


def passwd_text(user):
    """Return the passwd file for a pwd.struct_passwd-like user."""
    lines = [ROOT_PASSWD]
    if user.pw_uid != 0:
        lines.append('%s:x:%d:%d:%s:%s:%s' % (_field(user.pw_name), user.pw_uid, user.pw_gid,
                                              _field(user.pw_gecos), CONTAINER_HOME, SHELL))
    return '\n'.join(lines) + '\n'


def group_text(user, group):
    """Return the group file for a user and their primary group, which may be None.

    When the primary GID has no name on the host, the group takes the user's
    name.
    """
    lines = [ROOT_GROUP]
    if user.pw_gid != 0:
        name = group.gr_name if group is not None else user.pw_name
        lines.append('%s:x:%d:' % (_field(name), user.pw_gid))
    return '\n'.join(lines) + '\n'


def lookup(uid, gid):
    """Return (user, group) for the invoking user. group may be None."""
    user = pwd.getpwuid(uid)
    try:
        group = grp.getgrgid(gid)
    except KeyError:
        group = None
    return user, group


def state_dir(environ, home):
    """Return the directory the files are written to.

    ${XDG_RUNTIME_DIR}/kolla-ansible-launcher when that is set and writable,
    else <home>/.cache/kolla-ansible-launcher. home is the passwd home
    directory rather than $HOME, which sudo may have left as another user's.
    """
    runtime = environ.get('XDG_RUNTIME_DIR')
    if runtime and os.path.isdir(runtime) and os.access(runtime, os.W_OK):
        base = runtime
    else:
        base = os.path.join(home, '.cache')
    return os.path.join(base, 'kolla-ansible-launcher')


def _write(path, text):
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with open(tmp, 'w') as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def write_files(directory, user, group):
    """Write passwd and group under directory; return their paths."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    passwd_path = os.path.join(directory, 'passwd')
    group_path = os.path.join(directory, 'group')
    _write(passwd_path, passwd_text(user))
    _write(group_path, group_text(user, group))
    return passwd_path, group_path
