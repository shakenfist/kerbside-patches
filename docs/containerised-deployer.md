# The containerised deployer

The containerised deployer runs Kolla-Ansible from a container image
built by Kolla, instead of from a Python virtualenv on the deploy
host. A small launcher, `kolla-ansible-launcher`, installs a
`kolla-ansible` command that looks like the one operators already
use and runs the real one inside that image.

This page is for someone who wants to build and run it, and wants to
know what it does not do. It is a prototype: it deploys the two
all-in-one CI entries described below, and nothing else has been
shown to work. The launcher's own reference (its configuration file,
what it mounts and forwards, how `bootstrap-servers` runs, and what it
refuses) is [the deployer launcher](deployer-launcher.md), and is not
repeated here. How each part was designed, and the evidence behind
every number on this page, is in
[the containerised deployer plan](plans/containerised-deployer.md);
each number below names the phase of that plan it comes from.

A draft proposal for taking this upstream, written for Kolla and
Kolla-Ansible reviewers rather than for operators, is
[the containerised deployer proposal](containerised-deployer-proposal.md).

## What it is and why

Kolla-Ansible deploys every OpenStack service in a container, except
itself. Today the deployer is a virtualenv on the deploy host, with
its own pinned Ansible and a set of Ansible collections that
`kolla-ansible install-deps` fetches from Galaxy and git when the
virtualenv is built. Nothing ties that virtualenv to the images it
deploys:

* The `openstack.kolla` collection, which holds the roles that
  configure the host's container engine, is installed from a branch,
  so what runs is whatever that branch held on the day.
* The deploy host's Python is dictated by Ansible. Every Debian 12
  deploy host stopped working on master when ansible-core began
  requiring Python 3.12, although the images did not care.
* Galaxy is an availability dependency of every new deploy host.
* Nothing checks that the deployer and the images it deploys belong
  together, and an image tag is overwritten by every publish.

The containerised deployer has four parts:

* **A deployer image**, `kolla-ansible`, defined in Kolla and built by
  `kolla-build` like every service image, with the same tag, registry
  and base distribution. It holds Kolla-Ansible, ansible-core and every
  collection, installed at build time, and fetches nothing when it
  runs.
* **The launcher**, a standard-library-only Python package that runs
  that image with the configuration mounted at the same paths it has
  on the host, as the invoking user. It also runs `bootstrap-servers`,
  which cannot run in a container, from an unpacked copy of the image.
* **Version safeguards** in Kolla-Ansible, which refuse to run a
  deployer against a configuration or a deployment it does not match.
* **A build manifest** in the deployer image, naming the digest of
  every image built with it, so that pinning the deployer by digest
  pins every service image too.

The deployer reaches its own host through the
`community.docker.nsenter` connection plugin, from a container
started with `--privileged --pid host`, and every other host over SSH
as before. Inside the container it runs as the invoking user, not as
root: the image's `nsenter` carries file capabilities, so only the
step that enters the host's namespaces is privileged.

## Building the image

The image is defined by `kolla/docker/kolla-ansible/Dockerfile.j2`, a
Kolla patch carried here as `patch199`. It is built on
`openstack-base`, and takes two new Kolla sources: Kolla-Ansible
itself (upstream, the tarballs.opendev.org branch tarball) and the
`openstack.kolla` collection as a git plugin. At build time it:

* installs Kolla-Ansible into the image's venv with upper
  constraints;
* installs the collections of `requirements-core.yml`, and the
  `openstack.kolla` collection from the plugin, into
  `/usr/share/ansible/collections`, which `ANSIBLE_COLLECTIONS_PATH`
  makes the only path searched, so nothing in a user's
  `~/.ansible/collections` can shadow them;
* writes `/etc/kolla-ansible/deployer.json`: the Kolla-Ansible and
  ansible-core versions, the git commit of each source tree where one
  is known, the installed collections, and the tag and release the
  image was built with;
* labels the image `kolla_ansible_launcher_protocol`,
  `openstack_release` and `kolla_tag`;
* gives `/usr/bin/nsenter` `cap_sys_admin`, `cap_sys_ptrace` and
  `cap_sys_chroot` as file capabilities; and
* sets `HOME` to a world-writable, sticky `/var/lib/kolla-ansible`
  and installs an SSH client, so that it can run as any UID.

Its entrypoint is the real `kolla-ansible`, so
`docker run <image> --help` works without the launcher.

**In this repository's CI**, `_build/build-containers.sh` builds it
with every other image, from the patched Kolla-Ansible and the pinned
collection under `src/` (the `[kolla-ansible]` and
`[kolla-ansible-plugin-ansible-collection-kolla]` sections of
`etc/kolla-build-master.conf.in`), pushes it, and then adds the
build manifest (below). Only master is built: the deployer patches
are not in any per-release series.

**Locally**, without a registry, `build-containers.sh` is the wrong
tool, because it always pushes. Build the three images the deployer
needs instead:

```
_build/assemble-source.sh --no-tarball master
mkdir -p archive
_build/imagebuild.sh --build-targets master --distro debian \
    --distro-version trixie \
    --build-images '^(base|openstack-base|kolla-ansible)$' \
    --image-tag local
```

That gives `kolla/kolla-ansible:local`, about 1.7 GB (Phase 1 and
Phase 4). Without `archive/`, `imagebuild.sh` fails with
`archive/kolla-build.conf: No such file or directory`. A local image
has no registry digest, so `launcher pin` pins it by image ID with a
warning, and it has no build manifest, so every deploy warns and
pulls service images by tag.

**Pushing it.** The image is no use if the tool that pushes it drops
file capabilities, because then every task on the deploy host fails
with `Operation not permitted`. occystrap did, before 0.4.17
(shakenfist/occystrap#151, Phase 1); upstream's `kolla-build` push
keeps them. Check a pushed image with:

```
docker run --rm --entrypoint getcap <image> /usr/bin/nsenter
```

which should print `cap_sys_chroot,cap_sys_ptrace,cap_sys_admin=ep`.

## Installing and running it

Install the launcher into a virtualenv of its own, never beside the
real `kolla-ansible` package (it refuses to run if it finds one), and
then follow [the deployer launcher](deployer-launcher.md). In outline,
on a fresh all-in-one host, as a deploy user who can `sudo`:

1. `sudo kolla-ansible launcher install-engine`, if the host has no
   container engine. It installs the distribution's own: `docker.io`
   on the Debian family, `podman` on the Red Hat family.
2. Log in to the registry with that engine, as each user who will
   pull, if the registry needs credentials. The launcher never logs
   in.
3. Run `kolla-ansible launcher pin <registry>/<namespace>/kolla-ansible:<tag>`,
   which pulls the image, resolves the tag to a digest, and writes
   that to `/etc/kolla/deployer.conf`.
4. Write the inventory (below) and `globals.yml`, then run
   `kolla-genpwd` and `kolla-ansible certificates` through the
   launcher.
5. `sudo kolla-ansible bootstrap-servers -i <inventory>`. This one
   subcommand needs root: it runs from an unpacked copy of the image
   under `chroot`, so that it can replace the container engine
   underneath itself.
6. Give the deploy user the configuration directory and the engine
   (`bootstrap-servers` creates the `docker` group), pin again as that
   user if their engine has its own image store, and run `prechecks`,
   `pull`, `deploy` and `post-deploy` as usual.

`tools/bootstrap-kolla-ansible` does exactly this in launcher mode,
and is the worked example.

**The inventory is not upstream's all-in-one.** For the deploy host
itself it needs three things, which
[the deployer launcher](deployer-launcher.md) explains:

* the host must not be named `localhost`;
* `ansible_connection=community.docker.nsenter`; and
* an absolute `ansible_remote_tmp`, such as
  `/tmp/.ansible-kolla-<user>`.

`etc/inventory-all-in-one-launcher-master` is the one CI uses. It
carries Kerbside's groups and names the host `kolla`. patch214 adds
the upstream-style equivalent, `ansible/inventory/all-in-one-deployer`,
to Kolla-Ansible: upstream's `all-in-one` with the host renamed and
those two lines in `[all:vars]`. Nothing here deploys with it.

`validate-config` also needs two settings in `globals.yml`, from
`patch200`, because the container cannot use `sudo` on its own
`localhost` (see below):

```
service_config_validate_output_become: false
service_config_validate_output_dir: "/etc/kolla/config-validate"
```

## The version safeguards

A deployer image packages one version of Kolla-Ansible and is built
with one set of images, so Kolla-Ansible now checks the deployer
before it changes any host. The checks are in `patch206`, in
Kolla-Ansible rather than the launcher, because only Ansible can read
the effective configuration. They run only when
`/etc/kolla-ansible/deployer.json` exists, that is, from a deployer
image: a virtualenv cannot tell which release it is, since
`openstack_release` is the operator's to set.

* **Deployer against configuration.** A run is refused if
  `openstack_tag` or `openstack_release` differs from what the image
  was built with.
* **Deployer against deployment.** Every successful `deploy`,
  `deploy-containers`, `reconfigure`, `upgrade` and
  `migrate-container-engine` writes
  `/etc/kolla/kolla-ansible-deployment.json` on each host. A deployer
  older than that record is refused; a newer release may run only the
  actions an upgrade needs first, and `upgrade` may move one series,
  or from one `.1` (SLURP) series to the next.
* **`--override-deployer-checks`** turns each refusal into a warning
  that says what it overrode. `destroy` is never checked, so a
  mistake can always be cleaned up.

The record is written by virtualenv deploys too, so a deployment made
the old way can later be guarded by an image. The full rules are in
[the deployer launcher](deployer-launcher.md#deployer-checks).

In CI (Phase 5), `tools/check-deployer-guards` shows the configuration
check passing and refusing a different tag, the record check refusing
a record from a newer Kolla-Ansible, and the override. The guard play
cost a few seconds over a whole deploy. Most of the rules have never
run through Ansible here: see "What is not proven" below.

## The build manifest and digest pinning

After a build has pushed every image, `_build/build-containers.sh`
reads each image's digest back from the registry, writes a
`manifest.json` (image name to digest), and builds a thin image on
top of the pushed deployer with the manifest at
`/etc/kolla-ansible/manifest.json`. That image takes the deployer's
tag. The digests are read from the registry, not taken from
`kolla-build`, because a push path that filters images (occystrap does)
changes every digest on the way in. The details are in
[building patched container images](building.md#the-build-manifest).

When the manifest is present, every `<service>_image_full` resolves
through a new `kolla_image_full` lookup (`patch209`) to
`<image>@<digest>`; an overridden per-service tag, an image outside the
configured registry and namespace, and an image the manifest does not
name are still pulled by tag. Pulling by digest with Docker needed a
fix to the `kolla_container` module first (`patch208`): it could
neither pull a digest reference nor recognise an image pulled that way,
and on Docker's classic image store it would have recreated every such
container on every run.

What digest pinning changes, once the deployer is pinned by digest
in `deployer.conf`:

* **One pin pins the whole build.** The deployer's digest fixes its
  manifest, and the manifest fixes every service image.
* **A pull cannot mix builds**, whether during a publish window or
  on a node added months later. Tags are overwritten by every publish,
  and one day's publish lands over minutes, not at once.
* **A tag that has stopped being published cannot be deployed by
  accident**, because nothing resolves service images by tag.

And what it costs:

* **Switching a running deployment between tags and digests
  recreates every container once**, MariaDB and RabbitMQ included,
  because each container's image string changes. Do it at an
  upgrade. CI deploys fresh, so it has never shown this.
* A deployer image with no manifest warns once per action and pulls
  by tag. A manifest that cannot be read, or has an unknown
  `format_version`, fails closed, and the override does not change
  that.

In CI (Phase 6), on both launcher hosts, all 36 Kolla containers ran
`<name>@<digest>` matching the manifest, and a second
`deploy-containers --tags common,cron` left all 36 container IDs
unchanged. That second check re-examines only the `common` and `cron`
roles' containers. It passed on Docker's classic store (Debian) and
its containerd store (Rocky). The manifest names 85 images on Debian
and Ubuntu and 81 on Rocky, about 8 KiB (Phase 6).

## What CI shows

Two entries in `.github/workflows/functional-tests.yml` deploy
through the launcher, each beside an unchanged virtualenv entry that
is its control:

| Launcher entry | Control |
|----------------|---------|
| `master-h-debian13-c-debian13-aio-launcher` | `master-h-debian13-c-debian13-aio` |
| `master-h-rocky10-c-debian-13-aio-launcher` | `master-h-rocky10-c-debian-13-aio` |

Both start from a host with no container engine, have no
Kolla-Ansible virtualenv at any point, and run every Kolla-Ansible
call after bootstrap as the unprivileged base user (Phase 4). After
the deploy they run `tools/check-deployer-launcher`,
`tools/check-deployer-guards` and `tools/check-deployer-manifest`.
**Both have voted since #1919**: a workflow run fails if either does.
Before that, over a window from 2026-10-13 to 2026-10-15, each passed
in 11 runs, 3 of them daily rebases, and no failure was attributed to
the launcher (Phase 7).

What the timings support (Phase 7, 26 pairs of launcher and control
jobs from the same runs, 2026-10-10 to 2026-10-13):

* **The bootstrap section is shorter at the median, by about 130s on
  the Debian host and 95s on the Rocky host.** The launcher takes `ansible-galaxy` and
  `kolla-ansible install-deps` out of bootstrap. `install-deps` alone
  had a median of 107s on the Debian host and 131s on the Rocky host,
  and a range of 42s to 493s.
* **The deploy step as a whole is within noise** of its control on
  both hosts. Per-pair differences ranged from -947s to +777s.
* **Galaxy failures are rare here.** Of 982 virtualenv jobs over
  GitHub's 90 days of logs, four hit a Galaxy failure and one of them
  failed the job. The launcher entries cannot hit one. These numbers
  come from this CI's runners, registry and Python mirror; they say
  nothing about Galaxy failures in upstream's Zuul.

The launcher's own costs are small: about two seconds more per call
before Ansible starts, from one run, and 18s (Debian) to 27s (Rocky)
to pull and unpack the deployer image (Phase 7). The single-run
durations recorded in Phases 3 to 6 are noise and are not repeated
here.

`tools/measure-deployer-bootstrap` produced these numbers, and
[the deployer launcher](deployer-launcher.md#bootstrap-cost) says how
to run it.

## What did not work

Each of these was found by running the prototype, and each shaped
the design. Several are things an upstream version would have to
solve too.

**SSH to `127.0.0.1`.** Rejected as the way for the deployer to reach
its own host, because it depends on an SSH daemon, a key and
`authorized_keys` being right on the deploy host. Phase 2 used it as a
stopgap, and it showed a second problem: the key is named only in the
inventory, so the launcher could not see it to mount it, and
`deployer.conf` needed a `mounts` key for paths that only an inventory
names. That gap is general, and the key stays. Phase 3 replaced SSH
with `community.docker.nsenter`.

**Controller-side `become`.** The container cannot escalate on its own
`localhost`: the invoking user has no `sudo` inside it, and giving it
some would make a `--privileged --pid host` container root on the
host. `validate-config` failed this way on the first CI run through
the launcher ("sudo: a password is required", Phase 2). `patch200`
makes that `become` a variable. The only other controller-side
`become` in Kolla-Ansible is in `post-deploy`'s templates, which
escalate only when the configuration directory is not writable, so
the case is rare, but any new one breaks the containerised deployer.

**An inventory host named `localhost`.** Kolla-Ansible runs some tasks
on Ansible's implicit `localhost`, which under the launcher is the
container, using paths that exist only there (keystone's fernet cron
generator, for example). An inventory host called `localhost` replaces
the implicit one, and its `ansible_connection` sends those tasks to
the host, where they fail. Upstream's all-in-one inventory names its
host `localhost`, so a container all-in-one cannot be a one-line
change to it (Phase 3).

**The nsenter plugin assumes `remote_user` is root.**
`community.docker.nsenter` sets `remote_user` to `root` whatever the
caller, and documents that it always runs as root. Under the launcher
it runs as the invoking user, and Ansible's default remote temporary
directory then expands `~` through the container's `HOME`, a path the
host does not have, so every module fails. The workaround is an
absolute `ansible_remote_tmp` in the inventory; the fix belongs in
community.docker (Phase 3).

**The nsenter plugin leaks the caller's environment.** Commands on the
host get the container's `PATH`, `HOME` and `KOLLA_*` variables. So
Python interpreter discovery on the host depends on who ran Ansible.
That broke the Rocky entry: a `bootstrap-servers` run from a
virtualenv installed the Docker SDK into that virtualenv's Python, and
the deploy, run from the image, then found the system Python without
it. Pinning the interpreter in the inventory did not help, because
Kolla-Ansible overrides it, and pinning it on the command line broke
`certificates`. It took four rounds to fix (Phase 3). Running
`bootstrap-servers` from the unpacked image (Phase 4) gives both runs
the same `PATH`, which is why it now works without a pin; the leak is
still there.

**`bootstrap-servers` needs root.** Not a failure, but a change an
operator will notice: unpacking the image, mounting and `chroot` all
need root, so the whole command runs under `sudo`, where a virtualenv
install runs it as the deploy user and escalates per task. The
launcher does not escalate on its own, because that would hide it
(Phase 4).

**Podman is untested in CI.** The launcher supports podman, and its
unit tests cover the command lines it builds, but no CI host deploys
with it. On the Rocky entry podman only unpacks the image for
`bootstrap-servers`; the deploy runs on the `docker-ce` that
`bootstrap-servers` installs (Phase 4). Pulling by digest with podman
as the deploy engine has not been run either (Phase 6). Rootless
podman cannot work where the deploy host is also a target, because
its PID 1 is not the host's, so `nsenter` reaches the wrong namespaces.

**A Rocky slowdown after bootstrap is not established.** On the Rocky
host, the launcher entry's steps after bootstrap took a mean of 213s
longer than its control's over 13 pairs, about two standard errors,
with the jobs of a pair on different runners at different times
(Phase 7). That may be a real penalty of 100 to 200s, or host load.
Thirteen pairs cannot tell, so do not read it as a fact about the
launcher, in either direction.

**Along the way**, three things broke and were fixed, and are worth
knowing if you change the same code:

* occystrap stripped the file capabilities from every layer it
  rewrote, which would have broken `nsenter` on every pushed image
  (Phase 1; fixed in occystrap 0.4.17).
* The version safeguards, imported before Kolla-Ansible's own fact
  gathering, made it skip every host, because any module run on a
  host first fills in `ansible_facts`. They now run after it
  (Phase 5).
* The CI registry token cannot be relied on for the GitLab REST API,
  so the build manifest lists images from `kolla-build`'s own record
  and reads only the digests from the registry (Phase 6).

## What is not proven

These were not run, and nothing on this page claims them:

* A multinode deployment through the launcher, or a deploy host that
  is not also a target. The design covers both; no CI entry does.
* Upgrades. The upgrade and SLURP rules of the version safeguards are
  unit-tested in `patch206` but have never run through Ansible here,
  nor has a record newer by release, a host with no record under a
  deployer image, `bifrost`, or the MariaDB and RabbitMQ playbooks
  (Phase 5).
* Switching a running deployment from tags to digests, and so the
  one-time recreate (Phase 6).
* Whether SELinux was enforcing when the Rocky chroot ran. Nothing
  failed, and `restorecon -n` found no mislabelled files, but the job
  records neither `getenforce` nor the audit log (Phase 4).
* Anything outside this repository's CI: its runners, its private
  registry and its Python mirror are not upstream's.

## Where the changes are

| Change | Destination | Carried as |
|--------|-------------|------------|
| The deployer image | openstack/kolla | `patch199` |
| `validate-config` without controller `become` | openstack/kolla-ansible | `patch200` |
| `deployer.conf` in `tests/check-config.sh` | openstack/kolla-ansible | `patch201` |
| The version safeguards | openstack/kolla-ansible | `patch206` |
| Digest references in `kolla_container` | openstack/kolla-ansible | `patch208` |
| The build manifest lookup | openstack/kolla-ansible | `patch209` |
| Kerbside's images through the manifest | This repository only | `patch210` |
| An all-in-one inventory for a deployer image | openstack/kolla-ansible | `patch214` |
| The launcher | Undecided | `deployer/launcher/` |
| The manifest build step and CI checks | This repository | `_build/`, `tools/` |

All but `patch214` are on the main `kolla/` and `kolla-ansible/`
series, so every CI entry, virtualenv ones included, runs them.
`patch214` is only in `kolla-ansible-deployer/`, since CI deploys with
this repository's own launcher inventory instead. Nothing has been
proposed upstream yet; that is what
[the proposal](containerised-deployer-proposal.md) is for.
