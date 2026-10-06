"""The console scripts: kolla-ansible and the four kolla-*pwd tools.

Each script refuses what it must, works out the mounts and the command line,
and then execs the container engine, so the exit status and signals are the
container's own. The launcher takes over one word of Kolla-Ansible's
subcommand namespace, "launcher", for "launcher pin" and "launcher show".
"""

import argparse
import importlib.metadata
import os
import subprocess
import sys

from kolla_ansible_launcher import __version__
from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher import args as args_mod
from kolla_ansible_launcher import command as command_mod
from kolla_ansible_launcher import config as config_mod
from kolla_ansible_launcher import engine as engine_mod
from kolla_ansible_launcher import identity


def refuse_kolla_ansible_distribution(find=importlib.metadata.distribution):
    """Refuse to run when the real kolla-ansible is installed in this environment.

    The two distributions both install a kolla-ansible script, and would
    overwrite each other's; pip cannot be told that they conflict.
    """
    for name in ('kolla-ansible', 'kolla_ansible'):
        try:
            dist = find(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        raise LauncherError('the kolla-ansible distribution %s is installed in the same environment as '
                            'kolla-ansible-launcher (%s). They cannot share an environment: install the launcher '
                            'in a virtualenv of its own' % (dist.version, sys.prefix))


def refuse_subcommand(word):
    """Refuse the kolla-ansible subcommands that make no sense in the container."""
    if word == 'install-deps':
        raise LauncherError('install-deps is not needed: the Ansible collections Kolla-Ansible depends on are '
                            'already in the deployer image')


def _warn(tool, messages):
    for message in messages:
        print('%s: warning: %s' % (tool, message), file=sys.stderr)


def plan(tool, argv, environ, cwd, config, engine, user, group, state_dir, tty, uid, gid, groups, euid):
    """Return the engine command line for one run, writing the identity files.

    Everything this needs is passed in, so tests can call it with no engine
    and no real user.
    """
    passwd_path, group_path = identity.write_files(state_dir, user, group)
    configdir = os.path.dirname(config.path)
    mounts = command_mod.identity_mounts(passwd_path, group_path)
    mounts += command_mod.collect_mounts(tool, argv, cwd, environ, configdir, user.pw_dir, config.mounts)
    return command_mod.build(engine, config.image, tool, argv, mounts, uid, gid, groups, cwd,
                             command_mod.forwarded_env(environ), tty, config.host_namespaces,
                             rootless=euid != 0)


def run(tool, argv, environ=None, cwd=None, runner=subprocess.run, execvp=os.execvp):
    """Run one tool through the deployer image. Returns an exit status only on failure."""
    environ = os.environ if environ is None else environ
    cwd = os.getcwd() if cwd is None else cwd
    try:
        refuse_kolla_ansible_distribution()
        if tool == args_mod.KOLLA_ANSIBLE:
            index, word = args_mod.subcommand(argv)
            if word == 'launcher':
                return launcher(argv[index + 1:], environ, cwd, runner)
            refuse_subcommand(word)

        configdir = args_mod.configdir(tool, argv, environ, cwd)
        config = config_mod.load(configdir)
        _warn(tool, config.warnings)
        engine = config_mod.choose_engine(config)
        engine_mod.check_protocol(config.image, engine_mod.labels(engine, config.image, runner))

        uid, gid = os.getuid(), os.getgid()
        user, group = identity.lookup(uid, gid)
        command = plan(tool, argv, environ, cwd, config, engine, user, group,
                       identity.state_dir(environ, user.pw_dir), sys.stdin.isatty(), uid, gid, os.getgroups(),
                       os.geteuid())
    except LauncherError as e:
        print('%s: error: %s' % (tool, e), file=sys.stderr)
        return 1

    sys.stdout.flush()
    sys.stderr.flush()
    execvp(command[0], command)


def _launcher_parser():
    parser = argparse.ArgumentParser(
        prog='kolla-ansible launcher',
        description='Manage the deployer image that kolla-ansible-launcher %s runs.' % __version__)
    parser.add_argument('--configdir', help='the Kolla configuration directory (default: $%s or %s)'
                        % (args_mod.CONFIG_PATH_ENV, args_mod.DEFAULT_CONFIG_PATH))
    actions = parser.add_subparsers(dest='action', metavar='{pin,show}')
    pin = actions.add_parser('pin', help='pull an image and write it to deployer.conf by digest')
    pin.add_argument('reference', help='the deployer image, for example registry/kolla/kolla-ansible:tag')
    actions.add_parser('show', help='print deployer.conf and the image\'s labels')
    return parser


def launcher(argv, environ, cwd, runner):
    """The launcher subcommand: "launcher pin <image>" and "launcher show"."""
    parser = _launcher_parser()
    parsed = parser.parse_args(argv)
    configdir = args_mod.absolute(parsed.configdir or environ.get(args_mod.CONFIG_PATH_ENV) or
                                  args_mod.DEFAULT_CONFIG_PATH, cwd)
    if parsed.action == 'pin':
        return _pin(configdir, parsed.reference, runner)
    if parsed.action == 'show':
        return _show(configdir, runner)
    parser.print_help()
    return 2


def _pin(configdir, reference, runner):
    try:
        existing = config_mod.load(configdir)
    except LauncherError:
        # No file yet, or one without an image; the engine falls back to PATH.
        existing = config_mod.DeployerConfig(config_mod.path_in(configdir), reference)
    engine = config_mod.choose_engine(existing)
    pinned, warnings = engine_mod.resolve(engine, reference, runner)
    _warn('kolla-ansible launcher', warnings)
    engine_mod.check_protocol(reference, engine_mod.labels(engine, pinned, runner))
    path = config_mod.write_image(configdir, pinned)
    print('%s: image = %s' % (path, pinned))
    return 0


def _show(configdir, runner):
    config = config_mod.load(configdir)
    _warn('kolla-ansible launcher', config.warnings)
    engine = config_mod.choose_engine(config)
    print('launcher: kolla-ansible-launcher %s' % __version__)
    print('config: %s' % config.path)
    print('image: %s' % config.image)
    print('pinned: %s' % ('yes' if config_mod.is_digest_reference(config.image) else 'no'))
    print('engine: %s%s' % (engine, '' if config.engine else ' (from PATH)'))
    print('host_namespaces: %s' % ('true' if config.host_namespaces else 'false'))
    print('mounts: %s' % (' '.join(config.mounts) or '(none)'))
    image_labels = engine_mod.labels(engine, config.image, runner)
    print('labels:')
    for key in sorted(image_labels):
        print('  %s = %s' % (key, image_labels[key]))
    protocol = image_labels.get(engine_mod.PROTOCOL_LABEL)
    supported = protocol in engine_mod.SUPPORTED_PROTOCOLS
    print('protocol: %s (%s)' % (protocol, 'supported' if supported else 'NOT supported by this launcher'))
    return 0 if supported else 1


def main_kolla_ansible():
    return run(args_mod.KOLLA_ANSIBLE, sys.argv[1:])


def main_genpwd():
    return run(args_mod.GENPWD, sys.argv[1:])


def main_mergepwd():
    return run(args_mod.MERGEPWD, sys.argv[1:])


def main_readpwd():
    return run(args_mod.READPWD, sys.argv[1:])


def main_writepwd():
    return run(args_mod.WRITEPWD, sys.argv[1:])
