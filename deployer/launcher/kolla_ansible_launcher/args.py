"""Find the path-valued arguments of Kolla-Ansible and the password tools.

The launcher never parses the command line it passes on: it only scans it for
the options whose values name files or directories on the host, so that they
can be mounted into the container. The arguments themselves reach the
container unchanged, and relative paths keep working because the current
directory is mounted at the same path and used as the working directory.

Options are matched as argparse would accept them in three forms: separate
(``-i inv``), with ``=`` (``--inventory=inv`` or ``-i=inv``) and attached
(``-iinv``). Abbreviated long options (``--invent inv``) are deliberately not
recognised: they pass through and fail in the container with an error that
names the path.

The option tables follow kolla-ansible's ``kolla_ansible/ansible.py`` (``-e``
:53, ``-i`` :61, ``-p`` :95, ``--vault-id`` :102, the vault password files
:110, ``--configdir`` :129, ``--passwords`` :137), cliff's global
``--log-file``, and ``kolla_ansible/cmd/{genpwd,mergepwd,readpwd,writepwd}.py``.
"""

import os


DEFAULT_CONFIG_PATH = '/etc/kolla'
CONFIG_PATH_ENV = 'KOLLA_CONFIG_PATH'

# How the value of an option is interpreted.
PATH = 'path'              # The value is a path.
EXTRA_VARS = 'extra-vars'  # A path only when written @path.
VAULT_ID = 'vault-id'      # label@source or source, where source may be a path.
VALUE = 'value'            # Not a path, but it consumes the next argument.

KOLLA_ANSIBLE = 'kolla-ansible'
GENPWD = 'kolla-genpwd'
MERGEPWD = 'kolla-mergepwd'
READPWD = 'kolla-readpwd'
WRITEPWD = 'kolla-writepwd'

_VAULT_OPTIONS = {
    '-kv': VALUE, '--vault-mount-point': VALUE,
    '-kvp': VALUE, '--vault-kv-path': VALUE,
    '-n': VALUE, '--vault-namespace': VALUE,
    '-v': VALUE, '--vault-addr': VALUE,
    '-r': VALUE, '--vault-role-id': VALUE,
    '-s': VALUE, '--vault-secret-id': VALUE,
    '-t': VALUE, '--vault-token': VALUE,
    '-c': PATH, '--vault-cacert': PATH,
    '-p': PATH, '--passwords': PATH,
}

TOOL_OPTIONS = {
    KOLLA_ANSIBLE: {
        '-i': PATH, '--inventory': PATH,
        '-e': EXTRA_VARS, '--extra-vars': EXTRA_VARS,
        '-p': PATH, '--playbook': PATH,
        '--vault-id': VAULT_ID,
        '--vault-password-file': PATH, '--vault-pass-file': PATH,
        '--configdir': PATH,
        '--passwords': PATH,
        '--log-file': PATH,
        '-l': VALUE, '--limit': VALUE,
        '-t': VALUE, '--tags': VALUE,
        '--skip-tags': VALUE,
    },
    GENPWD: {'-p': PATH, '--passwords': PATH},
    MERGEPWD: {'--old': PATH, '--new': PATH, '--final': PATH},
    READPWD: _VAULT_OPTIONS,
    WRITEPWD: _VAULT_OPTIONS,
}

# The password file the tools fall back to when -p is not given. It is fixed in
# the tools, and does not follow KOLLA_CONFIG_PATH.
_DEFAULT_PASSWORDS = '/etc/kolla/passwords.yml'
_PASSWORDS_OPTIONS = ('-p', '--passwords')
TOOL_DEFAULT_PATHS = {
    GENPWD: [(_PASSWORDS_OPTIONS, _DEFAULT_PASSWORDS)],
    READPWD: [(_PASSWORDS_OPTIONS, _DEFAULT_PASSWORDS)],
    WRITEPWD: [(_PASSWORDS_OPTIONS, _DEFAULT_PASSWORDS)],
}

# cliff's global options that take a value, for finding the subcommand.
_GLOBAL_VALUE_OPTIONS = ('--log-file',)


def _match(arg, spec):
    """Match one argument against an option table.

    Returns (option, attached value or None), or (None, None) when the argument
    is not one of the options in the table.
    """
    if arg in spec:
        return arg, None
    if arg.startswith('-') and '=' in arg:
        option, value = arg.split('=', 1)
        if option in spec:
            return option, value
    if arg.startswith('-') and not arg.startswith('--') and len(arg) > 2:
        if arg[:2] in spec:
            return arg[:2], arg[2:]
    return None, None


def _paths_in_value(kind, value):
    """Return the paths named by one option value of the given kind."""
    if kind == PATH:
        return [value]
    if kind == EXTRA_VARS:
        return [value[1:]] if value.startswith('@') and len(value) > 1 else []
    if kind == VAULT_ID:
        source = value.split('@', 1)[1] if '@' in value else value
        return [] if source in ('', 'prompt') else [source]
    return []


def scan(tool, argv):
    """Return (option, path) for each path argument, as written.

    The option is the spelling that matched (``-i`` or ``--inventory``, never
    the attached form), and the path is not yet made absolute.
    """
    spec = TOOL_OPTIONS[tool]
    found = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--':
            break
        option, value = _match(arg, spec)
        i += 1
        if option is None:
            continue
        if value is None:
            if i >= len(argv):
                break
            value = argv[i]
            i += 1
        for path in _paths_in_value(spec[option], value):
            found.append((option, path))
    return found


def absolute(path, cwd):
    """Make a path absolute against cwd, without resolving symlinks."""
    return os.path.normpath(os.path.join(cwd, path))


def host_paths(tool, argv, cwd):
    """Return every host path the tool's arguments name, made absolute.

    This includes the path a password tool uses by default when its -p option
    is absent, since that is a path on the host too.
    """
    found = scan(tool, argv)
    paths = [absolute(p, cwd) for _, p in found]
    seen = {option for option, _ in found}
    for options, default in TOOL_DEFAULT_PATHS.get(tool, []):
        if not seen.intersection(options):
            paths.append(default)
    return paths


def configdir(tool, argv, environ, cwd):
    """Return the absolute configuration directory a run will use.

    For kolla-ansible this is the last --configdir argument, as argparse would
    take it; otherwise, and for the password tools, it is $KOLLA_CONFIG_PATH or
    /etc/kolla. deployer.conf is found here.
    """
    path = None
    if tool == KOLLA_ANSIBLE:
        for option, value in scan(tool, argv):
            if option == '--configdir':
                path = value
    if path is None:
        path = environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH
    return absolute(path, cwd)


def subcommand(argv):
    """Return (index, word) of kolla-ansible's subcommand, or (None, None).

    The subcommand is the first argument that is neither an option nor the
    value of one of cliff's global options.
    """
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _GLOBAL_VALUE_OPTIONS:
            i += 2
            continue
        if arg == '--':
            return (i + 1, argv[i + 1]) if i + 1 < len(argv) else (None, None)
        if not arg.startswith('-'):
            return i, arg
        i += 1
    return None, None
