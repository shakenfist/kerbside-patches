"""Build the container engine's command line for one run.

Nothing here runs anything: the functions take everything they need as
arguments, so the whole command line can be tested without an engine.

The container is run with --rm, -i, -t only when stdin is a terminal,
--network host, --user, --group-add for each supplementary group (not with
rootless podman, whose --userns=keep-id already keeps them), the mounts,
--workdir set to the current directory, --privileged --pid=host unless
host_namespaces is off, and the ANSIBLE_*, KOLLA_* and SSH_AUTH_SOCK variables
forwarded by name. The files that ANSIBLE_CONFIG, ANSIBLE_INVENTORY,
ANSIBLE_PRIVATE_KEY_FILE, ANSIBLE_VAULT_PASSWORD_FILE and ANSIBLE_LOG_PATH name
are mounted; no other variable's value is. There is no --init: the image's
entrypoint is already dumb-init --single-child.
"""

import os

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import args as args_mod
from kolla_ansible_launcher import identity
from kolla_ansible_launcher import mounts as mounts_mod
from kolla_ansible_launcher.mounts import Mount


FORWARD_PREFIXES = ('ANSIBLE_', 'KOLLA_')
FORWARD_NAMES = ('SSH_AUTH_SOCK',)
# The image sets ANSIBLE_COLLECTIONS_PATH to where its collections are; a host
# value, typically left over from a native install, would hide them.
NOT_FORWARDED = ('ANSIBLE_COLLECTIONS_PATH', 'ANSIBLE_COLLECTIONS_PATHS')

# The forwarded variables whose values name files, which are mounted like path arguments. Ansible only reads the
# first four, so they are mounted read-only and only when they exist; ANSIBLE_INVENTORY is a comma-separated list.
# The log file may not exist yet, so its nearest existing directory is mounted, as for --log-file.
READ_PATH_VARIABLES = ('ANSIBLE_CONFIG', 'ANSIBLE_INVENTORY', 'ANSIBLE_PRIVATE_KEY_FILE', 'ANSIBLE_VAULT_PASSWORD_FILE')
WRITE_PATH_VARIABLES = ('ANSIBLE_LOG_PATH',)
LIST_VARIABLES = ('ANSIBLE_INVENTORY',)

# The image's entrypoint runs kolla-ansible; the password tools replace it.
ENTRYPOINT = 'dumb-init'
ENTRYPOINT_ARGS = ('--single-child', '--')


def forwarded_env(environ):
    """Return the sorted names of the variables passed into the container."""
    names = [name for name in environ
             if (name.startswith(FORWARD_PREFIXES) or name in FORWARD_NAMES) and name not in NOT_FORWARDED]
    return sorted(names)


def variable_paths(environ, cwd):
    """Return (name, absolute path, readonly) for each file a path-valued variable names."""
    found = []
    for name in READ_PATH_VARIABLES + WRITE_PATH_VARIABLES:
        value = environ.get(name, '')
        values = value.split(',') if name in LIST_VARIABLES else [value]
        for path in (v.strip() for v in values):
            if path:
                found.append((name, args_mod.absolute(path, cwd), name in READ_PATH_VARIABLES))
    return found


def collect_mounts(tool, argv, cwd, environ, configdir, home, extra=()):
    """Return the same-path mounts a run needs, deduplicated.

    These are the current directory, the configuration directory, every path
    argument, the files the path-valued ANSIBLE_* variables name, the paths
    listed in deployer.conf, ~/.ssh (read-only, and also in the container's
    home directory) and the SSH agent socket. home is the user's home
    directory on the host.
    """
    mounts = []
    if not mounts_mod.is_protected(cwd):
        mounts.append(Mount(cwd))
    mounts.append(mounts_mod.mount_for(configdir))
    for path in args_mod.host_paths(tool, argv, cwd):
        mounts.append(mounts_mod.mount_for(path))

    for name, path, readonly in variable_paths(environ, cwd):
        if readonly and not os.path.exists(path):
            # Nothing to make visible; Ansible names the missing file itself.
            continue
        try:
            m = mounts_mod.mount_for(path)
        except LauncherError as e:
            raise LauncherError('%s: %s' % (name, e))
        mounts.append(Mount(m.source, readonly=readonly))

    for path in extra:
        if not os.path.exists(path):
            raise LauncherError('deployer.conf lists %s in mounts, but it does not exist' % path)
        if mounts_mod.is_protected(path):
            raise LauncherError('refusing to mount %s over the deployer image' % path)
        mounts.append(Mount(path))

    ssh_dir = os.path.join(home, '.ssh')
    if os.path.isdir(ssh_dir):
        # At its own path for configuration that names it absolutely, and in the
        # passwd home directory, where OpenSSH and ~ expansion look for it.
        mounts.append(Mount(ssh_dir, readonly=True))
        mounts.append(Mount(ssh_dir, os.path.join(identity.CONTAINER_HOME, '.ssh'), readonly=True))

    agent = environ.get('SSH_AUTH_SOCK')
    if agent:
        agent = args_mod.absolute(agent, cwd)
        if os.path.exists(agent):
            # The socket itself, not its directory, which is often a whole /run/user/<uid>.
            mounts.append(Mount(agent))

    return mounts_mod.deduplicate(mounts)


def identity_mounts(passwd_path, group_path):
    return [Mount(passwd_path, '/etc/passwd', readonly=True), Mount(group_path, '/etc/group', readonly=True)]


def build(engine, image, tool, argv, mounts, uid, gid, groups, cwd, env_names, tty, host_namespaces, rootless=False):
    """Return the engine command line, as a list, for running tool in image.

    rootless is for a non-root user running podman, which needs
    --userns=keep-id for the container's UID to be the user's on the host;
    without it, files the run writes would belong to a subordinate UID. That
    also keeps the user's supplementary groups, so --group-add is for every
    other case: through nsenter the container's identity is what host tasks
    run as, and sudo %group rules need the groups. groups is the user's
    supplementary GIDs, as from os.getgroups(); the primary GID is dropped.
    """
    command = [engine, 'run', '--rm', '-i']
    if tty:
        command.append('-t')
    command += ['--network', 'host', '--user', '%d:%d' % (uid, gid)]
    if engine == 'podman' and rootless:
        command.append('--userns=keep-id')
    else:
        for extra in sorted(set(groups) - {gid}):
            command += ['--group-add', str(extra)]
    if host_namespaces:
        command += ['--privileged', '--pid', 'host']
    for m in mounts:
        command += ['--volume', m.volume()]
    command += ['--workdir', cwd]
    for name in env_names:
        command += ['--env', name]
    if tool == args_mod.KOLLA_ANSIBLE:
        command.append(image)
    else:
        command += ['--entrypoint', ENTRYPOINT, image]
        command += list(ENTRYPOINT_ARGS) + [tool]
    return command + list(argv)
