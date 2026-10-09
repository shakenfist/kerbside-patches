"""Run Kolla-Ansible from a containerised deployer image.

The launcher installs a ``kolla-ansible`` command and the four ``kolla-*pwd``
password tools. Each runs the same command inside the deployer image named by
``<configdir>/deployer.conf``, with every path it can see in the arguments
bind-mounted at the same path, as the invoking user. It is standard library
only, so that it can be installed with pip on any deploy host.

The modules divide the work so that everything up to the final ``exec``, or up
to the mount and chroot calls, can be tested without a container engine or
root:

* ``args``: find the path-valued arguments of each tool;
* ``mounts``: turn those paths into deduplicated same-path bind mounts;
* ``config``: read ``deployer.conf`` and rewrite its ``image`` key;
* ``identity``: synthesise ``/etc/passwd`` and ``/etc/group`` for the user;
* ``command``: build the engine's command line;
* ``engine``: the few calls to the engine that the launcher makes itself;
* ``bootstrap``: run bootstrap-servers from an unpacked image, and clean up;
* ``chroot``: the helper that mounts, chroots and execs for ``bootstrap``;
* ``install``: ``launcher install-engine``;
* ``cli``: the console scripts, the refusals, and the ``exec``.
"""

__version__ = '0.1.0'


class LauncherError(Exception):
    """An error the launcher reports in one line and exits non-zero for."""
