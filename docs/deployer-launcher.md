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
must be able to use that engine. On a fresh host with neither,
`sudo kolla-ansible launcher install-engine` installs one (see
below). The launcher never logs in to a registry. If the image needs
credentials, run `docker login` (or `podman login`) as that user
first.

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

## Installing an engine

`kolla-ansible launcher install-engine` is for a deploy host that has
no container engine yet, such as a stock cloud image. If `docker` or
`podman` is already on `PATH`, it says which and does nothing.
Otherwise it needs root, reads `ID` and `ID_LIKE` from
`/etc/os-release`, and installs the distribution's own engine:

* On the Debian family (Debian, Ubuntu), `docker.io`, plus
  `docker-cli` where the distribution has split the client out
  (Debian 13 has, Debian 12 has not), with `apt-get` and
  `DEBIAN_FRONTEND=noninteractive`.
* On the Red Hat family (RHEL, Rocky, CentOS, Fedora), `podman` with
  `dnf`. These distributions do not ship Docker.

Any other distribution is refused: install docker or podman by hand.
It never replaces an existing engine. `bootstrap-servers` installs the
engine Kolla deploys with, and on Debian `docker-ce` replaces
`docker.io`. On Rocky, `docker-ce` installs beside podman, and the
deployer image is then pulled once by each.

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
  run writes belong to you. That also keeps your supplementary groups.
* With docker, or podman as root, `--group-add <gid>` for each of your
  supplementary groups (sorted, without your primary group), so host
  `sudo` rules that name a group still match tasks run through
  `nsenter`.

The launcher then replaces itself with the engine, so the exit status
and signals are the container's. `bootstrap-servers` is the
exception, and is described below.

## Running on the deploy host itself

When the deploy host is the machine running the launcher, tasks reach
it through the `community.docker.nsenter` connection plugin rather
than SSH. The container enters the host's namespaces with
`nsenter --target=1`, using the file capabilities on the image's
`/usr/bin/nsenter`, and runs each command as your UID and groups.
This puts requirements on the image, the engine and the inventory.

The image and engine:

* `/usr/bin/nsenter` in the image must keep its file capabilities
  (`cap_sys_admin` among them). Check with
  `docker run --rm --entrypoint getcap <image> /usr/bin/nsenter`.
  `tools/check-deployer-launcher` does this in CI.
* An image pushed with a tool that drops file capabilities breaks
  every task with `Operation not permitted`. Push through occystrap
  0.4.17 or later (shakenfist/occystrap#151).
* The container needs `--privileged --pid host`, which is the
  `host_namespaces` default. Docker must not set `no-new-privileges`.

The inventory:

* **The deploy host must not be named `localhost`.** Kolla-Ansible
  runs some tasks with `delegate_to: localhost` and `connection:
  local`, among them keystone's fernet cron generator, using paths
  that exist only in the container. An inventory host named
  `localhost` replaces the implicit one, and the host's
  `ansible_connection` would send those tasks to the host, where
  they fail. Every use of `localhost` must stay in the container.
* `ansible_remote_tmp` must be an absolute path, for example
  `/tmp/.ansible-kolla-<user>`. The plugin runs as `root` as far as
  Ansible is concerned, so `~` would expand through the container's
  `HOME` (`/var/lib/kolla-ansible`), which the host does not have.
* The plugin passes the container's whole environment (`PATH`,
  `HOME`, `KOLLA_*`) to commands on the host. Python interpreter
  discovery on the host therefore depends on the `PATH` of whatever
  ran Ansible.
* `bootstrap-servers` installs the Docker SDK into the Python that
  interpreter discovery finds on the host. Through the launcher it
  runs with the image's `PATH`, as the deploy does, so both find the
  same `/usr/bin/python3` and the interpreter does not need pinning.
  (A `bootstrap-servers` run from a venv on the host would find the
  venv's Python instead, and the deploy would then lack the SDK.)

## bootstrap-servers

`kolla-ansible bootstrap-servers` is the one subcommand that does not
run in a container. It installs the container engine Kolla deploys
with, and on Debian that removes the distribution's engine and every
container it runs, which would include the deployer. So the launcher
runs it from an unpacked copy of the image instead:

1. It pulls the image if need be, and checks its protocol label, as
   for any run.
2. It makes a fresh directory under `/var/lib/kolla-ansible-launcher/`
   (`bootstrap-<random>`, mode 0700), and unpacks the image's root
   filesystem into it with `<engine> create`, `<engine> export` piped
   into `tar -x -p --numeric-owner`, and `<engine> rm`. It prints how
   long that took and how much it wrote (`du -sx`). The image is
   about 1.7 GB unpacked.
3. In a private mount namespace (`unshare --mount --propagation
   private`), a helper bind-mounts the host's `/proc`, `/sys` and
   `/dev`, `/etc/resolv.conf` and `/etc/hosts` (read-only), and the
   same paths a container run would mount (see above), at the same
   paths. It then `chroot`s into the directory and runs the image's
   `kolla-ansible` with the original arguments.
4. When it exits, the launcher deletes the directory and exits with
   its status.

The unpack uses whichever engine is on `PATH`, docker first, or the
`engine` key of `deployer.conf`. On a fresh Rocky host that is the
podman `install-engine` installed. If `deployer.conf` names an engine
that is not installed yet, the run is refused, so leave the key out
until bootstrap has installed it.

The environment in the chroot is the image's own (`PATH` starts with
the image's venv), plus the variables a container run forwards and
`TERM`, with `HOME=/root`. There is no PID namespace and no
`dumb-init`: `kolla-ansible` is an ordinary process on the host,
running as root, and Ctrl-C reaches it directly.

**It needs root.** Unpacking with the image's ownership, mounting and
`chroot` all need it, so the launcher refuses to run
`bootstrap-servers` unless the effective UID is 0: run it with
`sudo`. That is a change from a venv install, where an operator runs
`bootstrap-servers` as their deploy user and Ansible's `become`
escalates each task. The launcher does not escalate on its own,
because that would hide the escalation from the operator. As root,
the targets are reached with root's `~/.ssh`, and with root's
`SSH_AUTH_SOCK` if `sudo` keeps one.

The unpacked directory is deleted after every run. There is no
cache, because `bootstrap-servers` is rare. The deletion never goes
through a mount:

* The mounts exist only in the helper's namespace, never in the
  launcher's.
* If anything is mounted under the directory in the launcher's
  namespace all the same, the launcher leaves the directory in place,
  with a warning naming it and the mount.
* The deletion does not follow symlinks, and stops, with the same
  warning, at any directory on a different device from the top.

A directory left this way, or by a launcher that was killed, stays in
`/var/lib/kolla-ansible-launcher/` until you delete it. The launcher
never deletes a run directory it did not create in the same run.

## What it refuses

* A `kolla-ansible` distribution in the same environment.
* `kolla-ansible install-deps`. The collections are already in the
  image.
* `kolla-ansible bootstrap-servers` without root, and a mount point
  that the image reaches through a symlink.
* `launcher install-engine` without root, or on a distribution
  outside the Debian and Red Hat families, when there is no engine.
* A missing or unparsable `deployer.conf`, a missing `image`, an
  `engine` that is not docker or podman, or a relative path in
  `mounts`.
* An image with no, or an unsupported, protocol label.
* A path that needs a protected directory mounted, a path containing
  a colon, and two different paths that would land at one mount point.

Each error is one line on stderr, prefixed with the tool name, and
exit status 1.
