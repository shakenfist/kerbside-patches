"""Install the distribution's container engine on a host that has none.

"kolla-ansible launcher install-engine" is for a fresh deploy host: the launcher
needs an engine to pull and unpack the deployer image before bootstrap-servers
has installed the one Kolla-Ansible deploys with. It never touches an existing
engine, because replacing engines is bootstrap's job.

On the Debian family it installs docker.io, plus docker-cli where the
distribution has split the client out (Debian 13 has, Debian 12 has not). On
the Red Hat family, which has no Docker in its repositories, it installs
podman. Anything else is refused.
"""

import os
import shlex
import shutil
import subprocess

from kolla_ansible_launcher import LauncherError
from kolla_ansible_launcher.config import ENGINES


OS_RELEASE = '/etc/os-release'
DEBIAN = 'debian'
REDHAT = 'redhat'
_REDHAT_IDS = ('rhel', 'fedora', 'centos')
APT_ENV = {'DEBIAN_FRONTEND': 'noninteractive'}


def parse_os_release(text):
    """Return the KEY=value pairs of an os-release file as a dict, unquoted."""
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        try:
            words = shlex.split(value)
        except ValueError:
            words = [value]
        fields[key.strip()] = ' '.join(words)
    return fields


def read_os_release(path=OS_RELEASE):
    try:
        with open(path) as f:
            return parse_os_release(f.read())
    except FileNotFoundError:
        raise LauncherError('%s does not exist, so the distribution cannot be identified; install docker or '
                            'podman by hand' % path)
    except OSError as e:
        raise LauncherError('cannot read %s: %s' % (path, e))


def family(fields):
    """Return DEBIAN, REDHAT or None for an os-release dict, from ID and ID_LIKE."""
    ids = [fields.get('ID', '').lower()] + fields.get('ID_LIKE', '').lower().split()
    if DEBIAN in ids:
        return DEBIAN
    if any(i in ids for i in _REDHAT_IDS):
        return REDHAT
    return None


def existing_engine(which=shutil.which):
    """Return (engine, path) for the first engine on PATH, or (None, None)."""
    for engine in ENGINES:
        path = which(engine)
        if path:
            return engine, path
    return None, None


def debian_commands(has_docker_cli):
    """Return the apt-get install command for the Debian family."""
    packages = ['docker.io'] + (['docker-cli'] if has_docker_cli else [])
    return ['apt-get', 'install', '-y'] + packages


def redhat_commands():
    return ['dnf', 'install', '-y', 'podman']


def _apt_environ(environ):
    env = dict(environ)
    env.update(APT_ENV)
    return env


def _check(result, command):
    if result.returncode != 0:
        raise LauncherError('%s failed with exit status %d' % (' '.join(command), result.returncode))


def has_package(name, run, environ):
    """Return True when apt knows a real package called name."""
    result = run(['apt-cache', 'show', name], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                 universal_newlines=True, env=_apt_environ(environ))
    return result.returncode == 0 and bool((result.stdout or '').strip())


def install_engine(run=subprocess.run, which=shutil.which, euid=None, os_release=read_os_release, environ=None,
                   out=print):
    """Install an engine if there is none. Return the exit status."""
    engine, path = existing_engine(which)
    if engine:
        out('%s is already installed (%s); nothing to do' % (engine, path))
        return 0

    euid = os.geteuid() if euid is None else euid
    if euid != 0:
        raise LauncherError('installing a container engine needs root; run "sudo kolla-ansible launcher '
                            'install-engine"')

    fields = os_release()
    environ = os.environ if environ is None else environ
    kind = family(fields)
    if kind == DEBIAN:
        update = ['apt-get', 'update']
        out('installing docker.io with apt-get')
        _check(run(update, env=_apt_environ(environ)), update)
        command = debian_commands(has_package('docker-cli', run, environ))
        _check(run(command, env=_apt_environ(environ)), command)
    elif kind == REDHAT:
        command = redhat_commands()
        out('installing podman with dnf')
        _check(run(command), command)
    else:
        raise LauncherError('cannot install a container engine on %s (ID=%s, ID_LIKE=%s): only the Debian and Red '
                            'Hat families are known. Install docker or podman by hand'
                            % (fields.get('PRETTY_NAME') or 'this distribution', fields.get('ID', ''),
                               fields.get('ID_LIKE', '')))

    engine, path = existing_engine(which)
    if not engine:
        raise LauncherError('the install finished, but neither docker nor podman is on PATH')
    out('%s is installed (%s)' % (engine, path))
    return 0
