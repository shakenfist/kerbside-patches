"""Read deployer.conf, and rewrite its image key.

The file is <configdir>/deployer.conf, in INI format, with one [deployer]
section:

    [deployer]
    # Required. Should be a digest reference; written by `launcher pin`.
    image = registry.example.com/kolla/kolla-ansible@sha256:...
    # Optional: docker or podman. Defaults to whichever is on PATH, docker first.
    engine = docker
    # Optional, default true: run with --privileged --pid=host.
    host_namespaces = true
    # Optional: further host paths to mount at the same path, whitespace separated.
    mounts = /srv/github

The launcher cannot parse globals.yml (no YAML in the standard library, and
the effective values need Jinja and possibly vault), so the image is named
here instead, and changes deliberately at upgrade time.
"""

import configparser
import os
import re
import shutil

from kolla_ansible_launcher import LauncherError


FILENAME = 'deployer.conf'
SECTION = 'deployer'
ENGINES = ('docker', 'podman')
_KEYS = ('image', 'engine', 'host_namespaces', 'mounts')


class DeployerConfig:
    """The parsed contents of deployer.conf."""

    def __init__(self, path, image, engine=None, host_namespaces=True, mounts=(), warnings=()):
        self.path = path
        self.image = image
        self.engine = engine
        self.host_namespaces = host_namespaces
        self.mounts = list(mounts)
        self.warnings = list(warnings)


def path_in(configdir):
    return os.path.join(configdir, FILENAME)


def is_digest_reference(image):
    """Return True when an image reference cannot move: a digest or an image ID."""
    return '@sha256:' in image or image.startswith('sha256:')


def parse(text, path='deployer.conf'):
    """Parse the text of a deployer.conf. path is used only in messages."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text, source=path)
    except configparser.Error as e:
        raise LauncherError('cannot parse %s: %s' % (path, e))
    if not parser.has_section(SECTION):
        raise LauncherError('%s has no [%s] section' % (path, SECTION))
    section = parser[SECTION]

    image = section.get('image', '').strip()
    if not image:
        raise LauncherError('%s does not name an image; run "kolla-ansible launcher pin <image:tag>"' % path)

    engine = section.get('engine', '').strip() or None
    if engine is not None and engine not in ENGINES:
        raise LauncherError('%s: engine must be one of %s, not %s' % (path, ', '.join(ENGINES), engine))

    try:
        host_namespaces = section.getboolean('host_namespaces', fallback=True)
    except ValueError:
        raise LauncherError('%s: host_namespaces must be a boolean, not %s' % (path, section.get('host_namespaces')))

    mounts = section.get('mounts', '').split()
    for m in mounts:
        if not os.path.isabs(m):
            raise LauncherError('%s: mounts must be absolute paths, not %s' % (path, m))

    warnings = ['%s: ignoring unknown key %s' % (path, key) for key in section if key not in _KEYS]
    if not is_digest_reference(image):
        warnings.append('%s: image %s is not pinned by digest; run "kolla-ansible launcher pin" to pin it'
                        % (path, image))
    return DeployerConfig(path, image, engine=engine, host_namespaces=host_namespaces,
                          mounts=[os.path.normpath(m) for m in mounts], warnings=warnings)


def load(configdir):
    """Read and parse <configdir>/deployer.conf."""
    path = path_in(configdir)
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        raise LauncherError('%s does not exist. The launcher reads the deployer image from it; create it with '
                            '"kolla-ansible launcher pin <image:tag>", or set KOLLA_CONFIG_PATH or --configdir '
                            'if the configuration is elsewhere' % path)
    except OSError as e:
        raise LauncherError('cannot read %s: %s' % (path, e))
    return parse(text, path)


def choose_engine(config, which=shutil.which):
    """Return the engine named in the config, or the first one on PATH."""
    if config.engine:
        if not which(config.engine):
            raise LauncherError('%s names engine %s, which is not on PATH' % (config.path, config.engine))
        return config.engine
    for engine in ENGINES:
        if which(engine):
            return engine
    raise LauncherError('neither docker nor podman is on PATH')


_SECTION_RE = re.compile(r'^\s*\[([^\]]*)\]')
_IMAGE_RE = re.compile(r'^image\s*[=:]', re.IGNORECASE)


def set_image(text, image):
    """Return deployer.conf text with its image key set, keeping everything else.

    This edits lines rather than round-tripping through configparser, so that
    comments, the order of keys and other sections survive a pin.
    """
    new_line = 'image = %s' % image
    out = []
    section = None
    header = None
    replaced = False
    skipping = False
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            skipping = False
            out.append(line)
            if section == SECTION and header is None:
                header = len(out) - 1
            continue
        if skipping and line[:1] in (' ', '\t') and line.strip():
            # A continuation line of the multi-line value being replaced.
            continue
        skipping = False
        if section == SECTION and _IMAGE_RE.match(line):
            skipping = True
            if not replaced:
                out.append(new_line)
                replaced = True
            continue
        out.append(line)

    if not replaced:
        if header is not None:
            out.insert(header + 1, new_line)
        else:
            if out and out[-1].strip():
                out.append('')
            out.extend(['[%s]' % SECTION, new_line])
    return '\n'.join(out) + '\n'


def write_image(configdir, image):
    """Set image in <configdir>/deployer.conf, creating the file if need be."""
    path = path_in(configdir)
    if not os.path.isdir(configdir):
        raise LauncherError('configuration directory %s does not exist' % configdir)
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        text = ''
    new = set_image(text, image)
    parse(new, path)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(new)
    os.replace(tmp, path)
    return path
