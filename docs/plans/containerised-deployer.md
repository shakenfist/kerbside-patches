# Containerised Kolla-Ansible deployer

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly, and the three
upstream trees this plan reaches into: `kolla` (image definitions
and `kolla-build`), `kolla-ansible` (the CLI in
`kolla_ansible/cli/commands.py` and `kolla_ansible/ansible.py`, the
playbooks and roles under `ansible/`), and
`ansible-collection-kolla` (the `baremetal`, `docker` and `podman`
roles that `bootstrap-servers` runs). Read them at the `source_sha`
this repository pins, not at upstream `HEAD`, and ground answers in
what they do rather than what their documentation says. Where a
question turns on Ansible's own behaviour -- connection plugins,
`delegate_to`, fact caching, `ansible-galaxy` -- read the
`ansible-core` and `community.docker` sources. Flag any uncertainty
explicitly rather than guessing.

This plan is a prototype aimed at an upstream proposal. Every
decision should be judged by two questions: does it work in the
demo, and would an upstream reviewer accept it. Where those
disagree, say so in the plan rather than quietly optimising for
the demo.

## Situation

Kolla-Ansible deploys every OpenStack service in a container,
except the thing that deploys them. The deployer is a Python
package installed into a virtualenv on the deploy host, together
with its own pinned Ansible and a set of Galaxy collections
fetched at run time. Nothing ties that virtualenv to the images it
deploys.

What that looks like, measured in this repository and at the
pinned upstream (`kolla-ansible` `c8c7a0f6d`,
`ansible-collection-kolla` `11fa4c2`):

* **Our own CI hand-builds the venv every run.**
  `tools/bootstrap-kolla-ansible` makes
  `/srv/kolla-ansible/venv` with `--system-site-packages`,
  pip-installs `ansible-core`, `ansible`, `dbus-python`, `selinux`
  and `docker`, installs the patched tree editable
  (`pip install -e`), then runs `kolla-ansible install-deps`.
  `tools/ka` is a two-line wrapper that activates that venv. The
  deploy steps in `shakenfist/actions/deploy-kolla-ansible` call
  `tools/ka` over SSH, once per action.
* **The deployer's Python is dictated by Ansible, not by the
  deploy host.** `requirements.txt` pins
  `ansible-core>=2.20,<2.22`, and ansible-core 2.20 needs Python
  3.12. Every Debian 12 deploy host broke on master on 2026-09-07
  for that reason alone, although the images being deployed did
  not care what Python the deploy host ran.
* **Part of the deployer is not versioned with the rest at all.**
  `install-deps` installs `requirements.yml`, which on master
  names `ansible-collection-kolla` as `type: git, version:
  master`. That collection carries the `baremetal` and `docker`
  roles `bootstrap-servers` runs, so the code that configures the
  host's container engine is whatever the collection's master
  branch held on the day the venv was built. Stable branches pin
  a branch (`stable/2025.1`), not a commit. `requirements-core.yml`
  fetches seven more collections from Galaxy at run time, which
  also makes Galaxy an availability dependency of every deploy
  (the "Failed to install Ansible collections" flake in upstream
  Zuul is exactly this).
* **Nothing checks that deployer and images agree.** The images
  are chosen by `openstack_tag`, which defaults to
  `{{ openstack_release }}-{{ kolla_base_distro }}-{{ kolla_base_distro_version }}{{ openstack_tag_suffix }}`
  in `ansible/group_vars/all/common.yml`. An operator can run any
  Kolla-Ansible against any tag, and nothing notices until a role
  and an image disagree about a config file or a bootstrap step.
* **The CLI is already a thin argument builder.**
  `kolla_ansible/ansible.py:build_args()` turns the cliff options
  into an `ansible-playbook` command line. The controller-side
  paths it handles are `--configdir` (default `/etc/kolla`, or
  `$KOLLA_CONFIG_PATH`), `--passwords`, `-i`/`--inventory`
  (repeatable), `--vault-password-file`, the `globals.d`
  directory, and any `-e @file` extra vars. It passes
  `CONFIG_DIR` through to the playbooks, where it becomes
  `node_config` and `node_custom_config`.
* **"localhost" is load-bearing.** The shipped all-in-one
  inventory puts `localhost ansible_connection=local` in every
  group, and there are 85 uses of `delegate_to: localhost`,
  `hosts: localhost` or `connection: local` across 40 playbooks
  and role task files. Most are `stat`s and lookups of operator
  overrides under `node_custom_config`, which run on the
  controller.
* **Bootstrap restarts the engine it would be running on.** The
  `docker` role in `ansible-collection-kolla` installs `docker-ce`
  from Docker's own repository (`docker_apt_package: docker-ce`),
  masks the unit first on Debian, writes `/etc/docker/daemon.json`,
  and has a `Restart docker` handler that restarts (or, with
  `docker_systemd_reload`, reloads) the daemon. A package upgrade
  also restarts it; the role snapshots running containers first
  and restarts them afterwards. `docker_custom_config` is merged
  into `daemon.json`, so an operator can already set
  `live-restore`.
* **No published artifact ties one build's images together.**
  Measured on quay.io on 2026-09-29: every active tag on
  `openstack.kolla/keystone` is a `<branch>-<distro>` tag that
  each publish overwrites, with no per-build tag beside it. One
  day's build lands over a window of minutes, not at once:
  `keystone:master-debian-trixie` at 02:38, `nova-compute` at
  02:39, `kolla-toolbox` at 02:55. A pull inside that window can
  mix two days' builds, and so can a node added to a cloud months
  later. Tags that are no longer published stay in place looking
  current: `master-debian-bookworm` was last written on
  2025-12-20.

## Mission and problem statement

Build a working demonstration of Kolla-Ansible running from a
container image. The image is built and tagged alongside the
service images, and a small host-side launcher makes it look like
the `kolla-ansible` command operators already use. The demonstration
is the argument: Kolla-Ansible has had no working specs process for
some years, and a demo that deploys a real cloud surfaces the
awkward cases before any proposal does. Those cases are the ones
this plan exists to find. The work proceeds as patches here and a
prototype launcher, proven by switching one CI topology to the new
path. It ends with a written proposal ready to take upstream.

Out of scope: changing how the service containers are deployed;
Kayobe, which would consume this but is not part of it; Bifrost;
and landing anything upstream. The upstream conversation starts
from this plan's output, not inside it.

## How this work is carried

The plan produces four kinds of change, and each is carried the way
this repository already carries that kind. The rule of thumb:
anything bound for an OpenStack repository is a patch, and anything
that is ours is committed directly where it lives.

| Change | Destination | Carried as |
|--------|-------------|------------|
| Deployer image, build-manifest support (Kolla) | openstack/kolla | Patches in `_patches/`, listed in `kolla/ORDER` |
| nsenter inventory, version safeguards, pulling by digest (Kolla-Ansible) | openstack/kolla-ansible | Patches in `_patches/`, listed in `kolla-ansible/ORDER` |
| Bootstrap changes to the collection, only if Phase 4 falls back from shape (d) | openstack/ansible-collection-kolla | Patches, in the project directory Phase 1 adds |
| The launcher | Undecided upstream (Decision 8) | New code in this repository, under `deployer/launcher/` |
| Hash, `kolla-build.conf.in` source, digest read-back, CI entries | This repository | Direct commits under `_build/`, `etc/`, `.github/` |
| The `deployer` input to the deploy action | shakenfist/actions | Direct pull requests there, landed first |

**The upstream-bound patches go in the main `kolla/` and
`kolla-ansible/` `ORDER` files, not only in a series directory.**
This departs from how the JSON-logging series is carried: its
patches live only in `kolla-ansible-json-logging/`, so our own CI
never runs them. Here the prototype CI entry must build and deploy
them, so they go on the main stack. That has a useful side effect:
the unchanged venv entries then run the patched Kolla-Ansible too,
which proves every patch still works for a deployer that is not in
a container. A patch that cannot live alongside the venv path is
one upstream would refuse anyway. The costs, accepted:

* every build, kerbside's downstream CI included, builds one more
  image;
* the patches are rebased by the daily rebase like any other; and
* they are master-only, and not listed in the per-release
  directories.

A separate series directory for each upstream project
(`kolla-deployer/`, `kolla-ansible-deployer/`, with `repush: true`
and `skip_rebase: true`, following `kolla-ansible-json-logging/`)
is only created in Phase 8, when there is something to push to
Gerrit. It lists the same patch files, so there is still one copy
of each.

**The build manifest has two implementations.** The prototype
reads digests back in our own `_build/` pipeline, because that is
where our images are pushed, through occystrap. Upstream publishes
from Zuul jobs defined in Kolla's own repository, so the upstream
version is a separate change to those jobs. Phase 8 drafts it; it
is not prototyped here.

## Decisions

Settled in review on 2026-09-29. Each one records why, so that a
later reader can tell a decision from a default.

1. **The deployer is a Kolla image**, defined by a
   `docker/kolla-ansible/Dockerfile.j2` in Kolla and built by
   `kolla-build` like every other image. Any other home would be
   confusing later. It also gets the same tag, registry, base
   distro and pipeline as the service images for free. The cost,
   accepted, is a cross-repository dependency: Kolla builds an
   image whose content is Kolla-Ansible.

2. **Same-path bind mounts.** Configuration is mounted into the
   container at the path it has on the host, so every absolute
   path in `globals.yml`, the inventory and operator overrides
   keeps working unchanged.

3. **No assumption about the container engine.** Docker and
   podman are both supported for running the deployer. Debian in
   particular is largely on Docker.

4. **The launcher is `kolla-ansible-launcher`**, a minimal,
   standard-library-only, pip-installable package whose console
   script is `kolla-ansible`. It handles argument pass-through,
   mounts, image selection and the part of bootstrap that cannot
   run inside a container.

   It must not share an environment with the real `kolla-ansible`
   distribution, because the two would overwrite each other's
   script and uninstalling either would break the other. pip cannot
   enforce that on its own. It has no negative dependencies and
   ignores `Obsoletes-Dist` and `Provides-Dist`. The only way to
   make the resolver refuse is for both packages to depend on a
   third, trivial package with pins that cannot both be satisfied.
   That needs Kolla-Ansible to take a new requirement through
   openstack/requirements, which is too much ceremony to ask for
   before the idea has landed. So the launcher checks at start-up
   and refuses to run if the `kolla-ansible` distribution is
   installed beside it. If the real package is installed second,
   its script simply replaces the launcher's and runs natively,
   which is confusing but safe. The marker-package trick is listed
   under Future work.

5. **`localhost` uses `community.docker.nsenter`.** It has been in
   `community.docker` since 1.9.0, `requirements-core.yml` already
   allows `<6`, and it depends on no daemon being configured
   correctly, unlike SSH to `127.0.0.1`. It runs tasks in the host's
   namespaces from a container started with `--privileged
   --pid=host`. It is a connection plugin, not a `become` method:
   `become` escalates privilege on top of a connection and cannot
   change where a task runs. It only reaches the machine the
   deployer runs on, so every other host keeps using SSH, and the
   only configuration is the `localhost` inventory entry. It needs
   a rootful engine, because a rootless engine's PID 1 is not the
   host's.

6. **The deployer runs as the invoking user**, typically a shared
   "ansible" account. It needs no access to the engine's socket,
   because it reaches hosts through connection plugins, not the
   local daemon. This conflicts with Decision 5: `nsenter` into
   PID 1 needs `CAP_SYS_ADMIN`, and a non-root process in a
   container has no effective capabilities even under
   `--privileged`. The proposed resolution, to verify in Phase 3,
   is to give the image's `nsenter` binary file capabilities
   (`setcap cap_sys_admin,cap_sys_ptrace,cap_sys_chroot+ep`).
   `--privileged` leaves them in the bounding set, so Ansible runs
   unprivileged and only the connection step escalates. This
   depends on the engine not setting `no-new-privileges`.

7. **Lockstep between the deployer and the images is enforced**,
   not merely documented (Phases 5 and 6).

8. **The launcher lives in this repository, under
   `deployer/launcher/`, for the life of the prototype.** It is a
   new program with no upstream file to patch, and it will change
   on nearly every commit while young; carried as a patch that
   creates files, every edit would be a regenerated patch. A new
   shakenfist repository was rejected for now because it takes on
   the fleet's release and consistency machinery before we know
   the launcher survives the prototype. This repository therefore
   holds a program as well as patches: `ARCHITECTURE.md` says so
   in Phase 2, and Python linting and tests join `pre-commit`. Its
   eventual upstream home is a question for the proposal (Phase
   8). One option is a directory in the Kolla-Ansible repository,
   which is awkward because OpenStack's pbr tooling expects one
   distribution per repository. The other is a new opendev
   repository, which needs a governance change through
   project-config.

## Open questions

Each has a default the prototype will take unless someone decides
otherwise. Several exist to be answered by the prototype itself.

1. **How does the launcher choose an image?** It cannot parse
   `globals.yml`: the standard library has no YAML parser, and the
   effective values depend on `globals.d`, Jinja defaults and
   possibly vault. Nor can it trust a tag, because Kolla's tags
   move every day. *Default: a small INI file beside the
   configuration (`<configdir>/deployer.conf`, read with
   `configparser`) naming the deployer image **by digest**.* A
   launcher subcommand resolves a tag to a digest once and writes
   the file, and the file changes deliberately at upgrade time.
   Once the deployer carries a build manifest (Phase 6), pinning
   the deployer pins the entire build. The container, which can
   evaluate the configuration fully, checks that the image and
   the configuration agree (Phase 5). The launcher stays dumb, and
   the one place that understands the configuration does the
   verifying.

2. **How is the deploy host itself bootstrapped?** This is the
   hard one. The deployer needs a container engine before it can
   run at all. `bootstrap-servers` on a deploy host that is also a
   target will install, reconfigure and restart that same engine
   underneath the running deployer, and will try to replace a
   distribution `docker.io` with `docker-ce`. Four shapes are on
   the table:
   * **(a) Deferred restart.** A patch to `ansible-collection-kolla`
     teaches the `docker` role which host the deployer runs on. On
     that host it writes `daemon.json` but neither restarts the
     daemon nor changes the package. It records what is pending in
     a marker in the mounted configuration directory, and the
     launcher applies it after the container exits.
   * **(b) Render and apply.** The container renders a host
     preparation (repository, packages, `daemon.json`) for the
     deploy host into the mounted directory and exits. The
     launcher applies it natively, with nothing running on the
     engine.
   * **(c) Precondition.** The deploy host's engine is the
     operator's responsibility. `bootstrap-servers` checks it on
     that host but does not manage it.
   * **(d) Run the deployer outside the engine.** Unpack the
     deployer image's root filesystem to a directory, then run
     `kolla-ansible bootstrap-servers` from it under `chroot`,
     inside a private mount namespace (`unshare -m`) with the
     same-path bind mounts, `/proc`, `/dev`, `/sys`,
     `resolv.conf` and the SSH agent socket set up by hand. The
     deployer is then not a process of the engine at all, so
     bootstrap can install, reconfigure, restart or even replace
     the engine underneath it, and **the collection needs no
     special-casing**. That is the strongest argument upstream.
     Images are layer tarballs, not filesystem images, so this
     means unpacking, not loop-mounting. The cheap way to unpack
     is `create` then `export` with the engine the launcher's first
     step installed, before bootstrap touches it. A stdlib registry
     client (manifest lists, auth, whiteouts) is the fallback if
     that proves awkward. It runs as root, which bootstrap needs
     anyway, and nsenter still works from inside the chroot, so the
     connection story does not fork.

   *Default: the launcher's first step only ensures that some
   engine exists, using the distribution package. Phase 4
   prototypes (d) first, and (a) or (b) only if (d) fails.* (c) is
   the fallback if everything proves fragile. (d) is used for
   `bootstrap-servers` only; every other action runs through the
   engine. A native Python reimplementation of `bootstrap-servers`
   is not on the table: the `docker` role derives `daemon.json`
   from a dozen variables, and a second copy would drift within a
   release.

3. **Credentials.** *Default: forward `SSH_AUTH_SOCK` when it is
   set, otherwise mount the invoking user's `~/.ssh` read-only.
   Vault password files are mounted at the same path like any
   other path argument.*

4. **Where does the build manifest's digest list come from?** It
   has to be read back from the registry after the push, not taken
   from `kolla-build`. Our occystrap proxy normalises timestamps on
   the way through, so the digest `kolla-build` sees is not the one
   that lands, and upstream could add a filtering step for the
   same reason. *Default: query the registry for each pushed tag's
   digest once every service image is pushed, then build the
   deployer's final layer from that list.* In upstream's publish
   jobs, check whether anything else can retag between the push
   and the query.

## Execution

Phases are sections of this plan, following
`docs/plans/index.md`. Every phase lands as its own pull request
against `develop`.

| Phase | Status | Merged |
|-------|--------|--------|
| 0. A CI entry for the prototype | Not started | |
| 1. Deployer image | Not started | |
| 2. Launcher | Not started | |
| 3. The localhost connection | Not started | |
| 4. Bootstrapping the deploy host | Not started | |
| 5. Version safeguards | Not started | |
| 6. Build manifest | Not started | |
| 7. Make the prototype entry voting | Not started | |
| 8. Documentation and upstream proposal | Not started | |
| 9. Push audit | Not started | |

The order is deliberate. Phase 0 comes first so that every later
phase is proven in CI as it lands, rather than CI arriving at the
end. Phases 1 and 2 give a deployer that
works against a remote, already-bootstrapped host, which is the
easy case and proves the plumbing. Phases 3 and 4 are the
all-in-one cases where the container and its target share a
kernel, and they are where the design can fail. Phases 5 and 6
are only worth building once the thing they protect exists.
Phase 7 makes it continuously true rather than true once.

### Phase 0: A CI entry for the prototype

Planning effort: high. It changes `shakenfist/actions`, which
kerbside's CI shares.

Add a new `test_installs` entry to `functional-tests.yml` that
becomes the prototype install, rather than converting an existing
one: the old path has to stay proven while the new one is built.
The new entry is `master-h-debian13-c-debian13-aio` with the
deployer switched and nothing else changed, so the existing entry
of that name is its control and a failure in one but not the
other is attributable.

Add a `deployer` input (`venv` or `launcher`, default `venv`) to
`shakenfist/actions/deploy-kolla-ansible`, so kerbside's CI and
every existing entry are unaffected. The new entry passes
`launcher`, which at this phase still runs the venv path: the
input is plumbing that each later phase fills in as it lands.

The entry is non-voting until Phase 7. Before adding it, check
whether branch protection or the merge queue lists required
checks by job name, because a new required check would block
every daily rebase pull request until the prototype works.

One entry, not two. The host OS is where this plan's risk lies:
the launcher installs the distribution's engine (`docker.io` on
Debian, podman on Rocky, whose base repositories have no Docker),
and SELinux is exactly the sort of thing that affects `nsenter`,
bind mounts and an unpacked chroot. But Phases 1 and 2 are
host-agnostic plumbing, and a second entry before then doubles the
nested-cloud load on every pull request without finding anything
the first would not. A Rocky 10 host entry is added in the first
commit of Phase 3.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 0a | medium | sonnet | none | Report which checks are required on `develop` (`gh api repos/shakenfist/kerbside-patches/branches/develop/protection` and the merge queue ruleset), so the new entry's name can be chosen not to collide. No changes. |
| 0b | high | opus | worktree | In shakenfist/actions, add the `deployer` input to `deploy-kolla-ansible/action.yml`, defaulting to `venv`, with `launcher` currently taking the same path; land it there first. |
| 0c | medium | sonnet | none | Add the `master-h-debian13-c-debian13-aio-launcher` entry to `test_installs` in `.github/workflows/functional-tests.yml`, copying `master-h-debian13-c-debian13-aio` and adding `'deployer': 'launcher'`, pass `matrix.test.deployer` (defaulting to `venv`) to the deploy action, and make that entry `continue-on-error`. |

Exit: the new entry runs green on a pull request here, the
existing entries are unchanged, and kerbside's CI is unaffected.

### Phase 1: Deployer image

Planning effort: high. It sets the contract the launcher is
written against.

Produce the Kolla image from Decision 1. It contains the patched
Kolla-Ansible, an `ansible-core` inside its supported range, and
every collection from `requirements.yml` and
`requirements-core.yml`, installed at build time.
`ansible-collection-kolla` is pinned to a commit recorded in the
image, not a branch. Nothing is fetched from Galaxy or git when
the image runs. The image's `nsenter` carries the file
capabilities from Decision 6, and the image tolerates running as
an arbitrary UID.

The image carries labels the launcher and the in-container
prechecks will read: the Kolla-Ansible version and git SHA, the
`ansible-collection-kolla` SHA, the OpenStack release, and a
launcher protocol version (starting at `1`). Its entrypoint is the
real `kolla-ansible`, so `docker run <image> --help` works with
no launcher at all.

Things this phase has to get right that are easy to miss:

* `kolla-build` needs Kolla-Ansible as a source. Our build
  already feeds patched trees from `src/` to `kolla-build`, so the
  same mechanism should serve, but check it in
  `_build/imagebuild.sh` rather than assuming.
* The image must enter the CI image hash.
  `_build/calculate-container-hash.sh` deliberately excludes
  `kolla-ansible` from the `src` term, on the grounds that its
  code does not change image content. Once there is a deployer
  image, it does. See also "Bugs fixed during this work": the
  `src` term currently hashes nothing at all.
* `ansible-collection-kolla` is not yet a project this repository
  patches. It may need to be if Phase 4 falls back from shape (d),
  and it needs a pinned SHA either way, so this phase adds the
  project directory (`config.yaml`, empty `ORDER`) even though it
  carries no patches yet.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | worktree | Read `_build/imagebuild.sh`, `_build/assemble-source.sh` and the kolla source-override mechanism at the pinned kolla SHA, and write up (in this plan) exactly how a Kolla image definition would receive the patched Kolla-Ansible tree and a pinned `ansible-collection-kolla`. No code; the output is the recipe 1b follows. |
| 1b | high | opus | worktree | Add a kolla patch defining the `kolla-ansible` deployer image per 1a, list it in `kolla/ORDER`, recount hunks with `tools/recount-patch.py --in-place`, and prove `_build/test-apply.sh --skip-tests kolla`. |
| 1c | medium | sonnet | none | Add an `ansible-collection-kolla/` project directory (`config.yaml` pinned to the SHA 1a chose, empty `ORDER`), mirroring an existing project directory, and teach `_build/assemble-source.sh` to clone it. |
| 1d | medium | sonnet | none | Include the deployer image's inputs in `_build/calculate-container-hash.sh`, and fix the `src` hashing bug recorded below in the same change, since both edit the same term. |

Exit: an image built by our pipeline, runnable as
`docker run --rm <image> --version`, with labels present and no
network access needed at run time. Check the last point with
`--network none`.

### Phase 2: Launcher

Planning effort: high.

Write `kolla-ansible-launcher` (Decision 4) in this repository
under `deployer/launcher/`, with its own `pyproject.toml`
(Decision 8).

It must:

* Refuse to run if the `kolla-ansible` distribution is installed
  in the same environment, and say why.
* Read `deployer.conf` for the image digest (Open question 1), and
  provide a subcommand that resolves a tag to a digest and writes
  the file.
* Detect `docker` or `podman` on `PATH`, preferring whichever
  `deployer.conf` names and otherwise whichever is present, and
  fail clearly when neither is.
* Mount at the same path: the config directory, every path-valued
  argument (`--configdir`, `--passwords`, each `-i`, each
  `--vault-password-file`, each `-e @file`), and the current
  working directory, so that relative paths resolve. Parse these
  from the argument list; `kolla_ansible/ansible.py` is the
  authority on which arguments are paths.
* Forward `ANSIBLE_*`, `KOLLA_*` and `SSH_AUTH_SOCK` (mounting the
  socket), allocate a TTY only when stdin is one, run with `--init`
  so that Ctrl-C reaches `ansible-playbook`, and return the
  container's exit code unchanged.
* Always run with `--network host`, `--rm`, and `--user` set to
  the invoking UID and GID. Add `--privileged --pid=host` by
  default for nsenter (Decision 5), with a `deployer.conf` switch
  to drop them when the deploy host is not a target.
* Check the image's launcher protocol label and refuse a version
  it does not speak.

Exit: `kolla-ansible prechecks`, `deploy` and `post-deploy` run
through the launcher against a remote host bootstrapped the old
way, as a non-root shared account. Files the run writes under
`/etc/kolla` (`passwords.yml` from `genpwd`, `admin-openrc.sh`,
`clouds.yaml`) are owned by that account.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Write `deployer/launcher/` as above, with unit tests for argument-to-mount translation covering relative paths, repeated `-i`, `-e @file` and `--configdir` given by environment variable. |
| 2c | medium | sonnet | none | Add flake8 and the launcher's unit tests to `.pre-commit-config.yaml` (scoped to `deployer/`), and add the launcher to `ARCHITECTURE.md`'s directory structure and a short section saying this repository now holds one program alongside its patches, linking to the launcher's doc page. |
| 2b | medium | sonnet | none | Add a `tools/ka`-equivalent entry point that runs through the launcher, and make the `launcher` value of the Phase 0 `deployer` input in shakenfist/actions use it for every step after bootstrap (bootstrap itself moves in Phase 4). |

### Phase 3: The localhost connection

Planning effort: high.

Start by adding the Rocky 10 host entry to CI (see Phase 0), as
`master-h-rocky10-c-debian-13-aio` with the deployer switched, and
non-voting like the first.

Make Decisions 5 and 6 real. Deploy all-in-one from the container
with `localhost` on `community.docker.nsenter`, as the unprivileged
shared account, relying on `nsenter`'s file capabilities. Check
file transfer, `become` and fact gathering explicitly, since
those are where a connection plugin is most likely to fall short.
Record in this plan which of the 85 `localhost` uses behave
differently, if any. Most are controller-side `stat`s of
`node_custom_config`, which the same-path mount should make a
non-event, but that is exactly the kind of claim a demo must
check rather than assert.

If the file-capabilities approach fails, record why here. The
fallback is to run the container as root and have the launcher
restore ownership of what it wrote under the config directory.

The deliverable is an all-in-one inventory that works from the
container. Upstream would ship it beside the existing one, not
instead of it. Any role that turns out to assume controller and
target share a filesystem is a patch against Kolla-Ansible,
recorded here.

Exit: an all-in-one deploy through the launcher on a host that
was bootstrapped the old way.

### Phase 4: Bootstrapping the deploy host

Planning effort: high. This is the phase most likely to reshape
the design.

Resolve Open question 2. Start from a clean Debian 13 host with
no container engine. The launcher's first step installs the
distribution's engine, then runs `bootstrap-servers` using shape
(d): unpack the deployer image with that engine, then run it from
a chroot in a private mount namespace. Record in this plan:

* whether bootstrap completed, including when it replaced
  `docker.io` with `docker-ce`;
* what state the host's engine ended in (package, `daemon.json`,
  running containers);
* how long the unpack takes, and how much disk it uses; and
* what the chroot had to have set up by hand that a container
  runtime would have done for us.

Fall back to (a) or (b) only if (d) fails, and record why. Also
cover the case where the deploy host is not a target, so that only
the first step applies and nothing needs special-casing.

Exit: a clean host goes from "no engine" to a working all-in-one
cloud through the launcher alone.

### Phase 5: Version safeguards

Planning effort: medium, once Phases 1 to 4 have fixed the
contracts.

Two guards, both evaluated inside the container, where the
configuration can be read properly:

* **Deployer against configuration.** A precheck compares the
  image's own release label with the effective `openstack_release`
  and `openstack_tag`, and refuses a mismatch. It runs at the
  start of every action, not only `prechecks`, because the
  foot-gun fires on `deploy` and `reconfigure` too.
* **Deployer against deployment.** Each successful `deploy` or
  `upgrade` records the deployed release and Kolla-Ansible SHA on
  the targets. A deployer older than that record refuses to run.
  `upgrade` may move forward only by the steps upstream supports,
  including skip-level (SLURP) upgrades, and an explicit
  `--allow-downgrade` exists for rollback. It prints what it is
  overriding.

Where the record lives on the targets is decided in this phase.
The default is a file under `node_config_directory`, which
already exists on every target.

Exit: each guard demonstrated both firing and not firing, in CI.

### Phase 6: Build manifest

Planning effort: high. It changes how Kolla publishes and how
Kolla-Ansible chooses images, and is the part of this plan most
useful even to someone who never runs the deployer in a
container.

The deployer is built last. Once every service image is pushed,
their digests are read back from the registry (Open question 4)
and written to a `manifest.json` (image name to digest) in a thin
final layer of the deployer image. When the manifest is present,
Kolla-Ansible pulls every service image by digest, using the
existing per-service `<svc>_image_full` variables. When it is
absent, as in local builds that were never pushed, it falls back
to tags and warns.

This changes three things, and the upstream proposal should lead
with the second and third:

* Pinning the deployer by digest (Open question 1) pins every
  image in the build.
* A pull can no longer mix builds, whether during a publish
  window or when a node is added months later.
* A tag that has stopped being published cannot be deployed by
  accident, because nothing resolves images by tag any more.

Exit: a deploy with the manifest present pulls every image by
digest (checked on the target with `docker inspect`), and the
same deploy without it still works.

### Phase 7: Make the prototype entry voting

Planning effort: medium.

By now both prototype entries have been exercising each phase as
it landed. Take the measurement worth recording here: the time
and failure rate of bootstrap with and without run-time Galaxy
fetches, comparing each prototype entry with its control. Then
make the entries voting. Keep the venv entries: the old path
stays supported for as long as upstream supports it, and this
plan does not change that.

Exit: both prototype entries green for a week of daily rebases,
then voting.

### Phase 8: Documentation and upstream proposal

Planning effort: medium.

Write `docs/containerised-deployer.md`: how to build and run the
deployer, and what the prototype learned, including the cases
that did not work. Add it to the index in `AGENTS.md` only if a
convention changed. Draft the upstream proposal: a mailing-list
post or PTG topic that links the demo, what we measured, and the
series of changes it would take, saying which repository each
change belongs in. This is prose here, not a Gerrit push. Pushing
is a separate decision.

### Phase 9: Push audit

Planning effort: medium.

This repository has no `PUSH-AUDIT.md` yet. This phase runs what
`PLAN-TEMPLATE.md` specifies in its place over the accumulated
diff of Phases 0 to 8 against `origin/develop`, using the ranges
recorded in the `Merged` column:

* a `/code-review` of that diff;
* `_build/test-apply.sh` for every project directory the plan
  touched; and
* a read of every documentation page the plan changed.

Phase 0's changes in `shakenfist/actions` are audited in that
repository, as part of the pull request that lands them, and are
cited here rather than re-run. Findings land as their own pull
request.

## Agent guidance

Follow `PLAN-TEMPLATE.md`. Implementation is done by sub-agents,
reviewed in the management session before any commit. The step
tables above are a first draft. Each phase's table is refined in
the first commit of that phase, when the previous phase's findings
are known.

## Administration and logistics

### Success criteria

We will know this plan has succeeded when the following are true:

* A clean Debian 13 host, with no container engine and no Python
  beyond what the distribution ships, reaches a working all-in-one
  cloud using only `pip install` of the launcher and the
  `kolla-ansible` commands it provides.
* Running a deployer whose release does not match the
  configuration or the deployment fails before it changes
  anything, with a message saying why.
* The deployer image runs with no network access to Galaxy or
  opendev.org.
* A deploy with a build manifest pulls every service image by
  digest.
* A Debian 13 host entry and a Rocky 10 host entry deploy
  through the launcher and vote, beside unchanged venv entries.
* Every patch applies to pristine upstream and is written in
  upstream's style. `pre-commit run --all-files` passes, and the
  launcher is standard-library only and wrapped at 120
  characters.
* `docs/containerised-deployer.md` exists and says what did not
  work as plainly as what did.

### Future work

* Kayobe wraps Kolla-Ansible and would be the obvious first
  consumer. It may already have its own container story (not
  checked); ask its maintainers before the upstream proposal.
* A Debian package of the launcher, since it has no dependencies
  to package.
* Resolver-level mutual exclusion between `kolla-ansible` and
  `kolla-ansible-launcher`, via a shared marker package with
  incompatible pins (Decision 4). Raise it upstream only if the
  runtime check turns out not to be enough.
* Rootless podman as the deployer's engine. nsenter rules it out
  for a deploy host that is also a target, but it should work
  where the deploy host is not.

### Bugs fixed during this work

* **`_build/calculate-container-hash.sh` hashes nothing for
  `src`.** The command is `find . -type f -name "*.py" -path
  "./kolla-ansible" -prune -exec cat {} \;`. A file cannot both
  match `-name "*.py"` and have the path `./kolla-ansible`, so the
  `-exec` never runs and the term is the hash of empty input,
  whatever `src/` contains. This was checked against a scratch
  tree. The date and the per-patch hashes still vary, so the
  practical effect is limited: an upstream `source_sha` bump with
  unchanged patches, on the same day, reuses the previous build's
  hash. Not yet fixed; step 1d fixes it, because that step edits
  the same term.

### Back brief

Before executing any step of this plan, back brief the operator on
your understanding of the plan and how the work you intend to do
aligns with it.
