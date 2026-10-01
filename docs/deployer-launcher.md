# The deployer launcher

`kolla-ansible-launcher` runs Kolla-Ansible from a container image
instead of from a Python virtual environment on the deploy host. It
installs a `kolla-ansible` command, and the four password tools
`kolla-genpwd`, `kolla-mergepwd`, `kolla-readpwd` and
`kolla-writepwd`. Each runs the same command inside the deployer
image, as the invoking user, with the files it names mounted in.

This is a prototype. The design and the phases still to come are in
[the containerised deployer plan](plans/containerised-deployer.md).
The source is in `deployer/launcher/`. It uses only the Python
standard library.

## Installing

Install it with pip into a virtualenv of its own:

```
python3 -m venv ~/launcher-venv
~/launcher-venv/bin/pip install deployer/launcher
```

The launcher and the real `kolla-ansible` package both install a
script called `kolla-ansible`. The launcher checks at every run, and
refuses to start if the `kolla-ansible` distribution is installed in
the same environment. Do not share one.

It needs docker or podman on the deploy host, and the user running it
must be able to use that engine. The launcher never logs in to a
registry. If the image needs credentials, run `docker login` (or
`podman login`) as that user first.

## deployer.conf

The launcher reads `deployer.conf` from the Kolla configuration
directory: `--configdir`, else `$KOLLA_CONFIG_PATH`, else `/etc/kolla`.
It cannot read `globals.yml`, so the image is named here and changes
when you decide it should. The file is INI, with one `[deployer]`
section:

```
[deployer]
image = registry.example.com/kolla/kolla-ansible@sha256:...
engine = docker
host_namespaces = true
mounts = /srv/github /srv/other
```

| Key | Meaning |
|-----|---------|
| `image` | Required. The deployer image. Should be a digest reference. A tag works, with a warning each run. |
| `engine` | `docker` or `podman`. Default: the first of the two on `PATH`. |
| `host_namespaces` | Default `true`. Runs the container with `--privileged --pid host`, which the deploy host needs for `nsenter`. |
| `mounts` | Absolute paths, separated by whitespace, to mount in addition to the rest. See below. |

Unknown keys are ignored with a warning.

## Pinning and showing

`kolla-ansible launcher pin <image:tag>` pulls the image and writes
its digest to the `image` key. The rest of the file, comments
included, is left alone, and the file is created if it is missing. An
image that only exists on this host has no registry digest. It is
pinned by its local image ID, with a warning that the ID means
nothing on another host.

`kolla-ansible launcher show` prints the file, whether the image is
pinned, the engine, and the image's labels. It exits non-zero if the
image speaks a launcher protocol this launcher does not.

Every run checks the image's `kolla_ansible_launcher_protocol` label
and refuses an image with no label or an unsupported value. The
label is how an old launcher and a new image notice that they no
longer fit.

## What it mounts

Everything is mounted at the same path inside the container as on the
host, so absolute paths in `globals.yml` and the inventory keep
working. The launcher does not parse the command line. It only looks
for the options whose values are paths, and passes the arguments on
unchanged.

* The current directory, which is also the working directory, so
  relative paths work.
* The configuration directory.
* Every path named by an option, such as `-i`, `-e @file`,
  `--passwords` or `--vault-password-file`.
* For a file, or a path that does not exist yet, its nearest existing
  parent directory, not the file alone. Ansible reads `group_vars/`
  and `host_vars/` from beside an inventory, and `@file` variable
  files often include their neighbours.
* The user's `~/.ssh`, read-only, at its own path and also at
  `/var/lib/kolla-ansible/.ssh`, which is where OpenSSH looks.
* The SSH agent socket named by `SSH_AUTH_SOCK`. It is the socket
  itself, not its directory, which is often all of `/run/user/<uid>`.
* The paths in the `mounts` key.

Parents are deduplicated: a mount inside another read-write mount is
dropped.

The `mounts` key is for paths that only an inventory names, for
example the directory holding an `ansible_ssh_private_key_file`. The
launcher cannot see those on the command line. Each must exist.

### Protected directories

Mounting `/etc` over the image would replace the image's own `/etc`.
So these are never mounted as directories: `/`, `/etc`, `/var`,
`/var/lib`, `/run`, `/opt`, and everything at or below `/usr`, `/bin`,
`/sbin`, `/lib` and its variants, `/boot`, `/dev`, `/proc`, `/sys`,
`/var/lib/kolla` and `/var/lib/kolla-ansible`. A single file in one of
them, such as a vault password in `/etc`, is mounted alone. Any other
path there is refused with a message saying to move it. The current
directory is silently not mounted if it is protected.

## The identity in the container

The container runs as your UID and GID, which the image does not
know, and OpenSSH refuses to run as a UID with no passwd entry. So
each run writes a `passwd` and a `group` file, with root and you, and
mounts them read-only over `/etc/passwd` and `/etc/group`. They go in
`$XDG_RUNTIME_DIR/kolla-ansible-launcher`, or
`~/.cache/kolla-ansible-launcher` if that is not usable. Your entry
comes from the host's user database, so LDAP and sssd users work.

The home directory in the entry is `/var/lib/kolla-ansible`, the
image's own `HOME`, and not your real one. Ansible expands `~` from
the passwd entry, and your real home is not writable in the
container. That is why `~/.ssh` is mounted at both places.

## What it forwards

* The environment variables `ANSIBLE_*`, `KOLLA_*` and
  `SSH_AUTH_SOCK`, by name.
* Not `ANSIBLE_COLLECTIONS_PATH` or `ANSIBLE_COLLECTIONS_PATHS`. The
  image sets its own, and a stale host value would hide the
  collections it carries.
* `--network host`, and `--privileged --pid host` unless
  `host_namespaces` is false.
* `-t` only when stdin is a terminal.
* With podman as a non-root user, `--userns=keep-id`, so files the
  run writes belong to you.

The launcher then replaces itself with the engine, so the exit status
and signals are the container's.

## What it refuses

* A `kolla-ansible` distribution in the same environment.
* `kolla-ansible install-deps`. The collections are already in the
  image.
* A missing or unparsable `deployer.conf`, a missing `image`, an
  `engine` that is not docker or podman, or a relative path in
  `mounts`.
* An image with no, or an unsupported, protocol label.
* A path that needs a protected directory mounted, a path containing
  a colon, and two different paths that would land at one mount point.

Each error is one line on stderr, prefixed with the tool name, and
exit status 1.
