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
pinned upstream on 2026-09-29 (`kolla-ansible` `c8c7a0f6d`,
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
| Deployer image (Kolla); the build manifest's publish step, drafted in Phase 8 | openstack/kolla | Patches in `_patches/`, listed in `kolla/ORDER` |
| nsenter inventory, version safeguards, pulling by digest (Kolla-Ansible) | openstack/kolla-ansible | Patches in `_patches/`, listed in `kolla-ansible/ORDER` |
| Bootstrap changes to the collection, only if Phase 4 falls back from shape (d) | openstack/ansible-collection-kolla | Patches, in the project directory Phase 1 adds |
| The launcher | Undecided upstream (Decision 8) | New code in this repository, under `deployer/launcher/` |
| Hash, `kolla-build.conf.in` source, digest read-back, CI entries | This repository | Direct commits under `_build/`, `etc/`, `.github/` |
| The `deployer` input to the deploy action | shakenfist/actions | One direct pull request there, in Phase 0 only |

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
and `skip_rebase: true`, like the `kolla-ansible-kerbside/` wave
directory: the main series already test the same patches)
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
   `kolla/docker/kolla-ansible/Dockerfile.j2` in Kolla and built by
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
| 0. A CI entry for the prototype | Complete | #1796 (`725cf1e8a`), #1799 (`190daeeca`); shakenfist/actions `7eaa1e534` (#121) |
| 1. Deployer image | Complete | #1803 (`507fbea62`) |
| 2. Launcher | Complete | #1812 (`b97361853`) |
| 3. The localhost connection | Complete | #1831 (`f2dd038ab`) |
| 4. Bootstrapping the deploy host | Complete | #1854 (`78c6e79c7`), #1857 (`af2474d4f`); shakenfist/actions `34b05f734` (#150) |
| 5. Version safeguards | Complete | #1867 (`b38293652`) |
| 6. Build manifest | Complete | #1881 (`784e95d9e`) |
| 7. Make the prototype entry voting | Complete | #1896 (`1af58c137`), #1919 (`3b8ff103d`) |
| 8. Documentation and upstream proposal | Complete | #1925 (`54b8bb270`) |
| 9. Push audit | Complete | #1933 (`30a2210ab`), #1936 (`61914728a`) |

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
kerbside's CI shares, and it fixes where every later phase plugs
in.

Add a new `test_installs` entry to `functional-tests.yml` that
becomes the prototype install, rather than converting an existing
one: the old path has to stay proven while the new one is built.
The new entry is `master-h-debian13-c-debian13-aio` with the
deployer switched and nothing else changed, so the existing entry
of that name is its control and a failure in one but not the
other is attributable.

One entry, not two. The host OS is where this plan's risk lies:
the launcher installs the distribution's engine (`docker.io` on
Debian, podman on Rocky, whose base repositories have no Docker),
and SELinux is exactly the sort of thing that affects `nsenter`,
bind mounts and an unpacked chroot. But Phases 1 and 2 are
host-agnostic plumbing, and a second entry before then doubles the
nested-cloud load on every pull request without finding anything
the first would not. A Rocky 10 host entry is added in the first
commit of Phase 3.

#### What the deploy path looks like today

Read while planning this phase (`shakenfist/actions` at `64e0e7e`,
this repository at `57c9496d3`), because the first draft of this
section put the switch in the wrong place:

* `shakenfist/actions/deploy-kolla-ansible` is a composite action
  that does nothing itself. Every step is an SSH to the target
  running a script from *this* repository: one call to
  `tools/bootstrap-kolla-ansible`, then six calls to `tools/ka`
  (`prechecks`, `pull`, `deploy`, `validate-config`, `check`,
  `reconfigure`), then `tools/install-openstack-clients` and
  `tools/postinstall-kolla-ansible`.
* `tools/ka` is `. /srv/kolla-ansible/venv/bin/activate;
  kolla-ansible $*`. It is the natural single dispatch point, but it
  is not yet the only one: `tools/postinstall-kolla-ansible` runs
  `kolla-ansible post-deploy` from the venv directly, and
  `tools/bootstrap-kolla-ansible` runs `install-deps`,
  `certificates` and `bootstrap-servers` the same way.
* The venv also supplies the OpenStack clients:
  `tools/install-openstack-clients` links them out of
  `/srv/kolla-ansible/venv/bin` into `/usr/local/bin`. A launcher
  deployment still needs somewhere for them to live. That is Phase
  2's problem, not this one, but it is why "launcher mode" can not
  simply mean "no venv". (Phase 4 gave them their own venv,
  `/srv/openstack-clients/venv`, in both modes.)
* Argument parsing is shared in `_build/common.sh`, and it rejects
  unknown flags. An action that always passed a new flag would
  break every caller running an older checkout of this repository.
* Kerbside's CI (`shakenfist/kerbside`, `functional-tests.yml`)
  runs `tools/bootstrap-kolla-ansible` itself, then calls the
  action with `skip_bootstrap: 'true'`. Both repositories call the
  action as `@main`, so a change merged there is live for both
  immediately.
* `develop` here has no branch protection and no rulesets
  (`gh api repos/shakenfist/kerbside-patches/branches/develop`
  reports `protected: false`; the rulesets list is empty), and
  nothing in `.github/` or `tools/` merges pull requests
  automatically. So no required check can be blocked by a new job
  name, and "non-voting" means only that a red prototype job does
  not turn the workflow run red. This answers what the first draft
  had as step 0a.

#### Design

**The switch lives in this repository, not in the action.** The
first draft had the action's `deployer` input grow a code path per
phase. Because the action only calls scripts from this repository,
it is enough for the action to pass the choice to bootstrap once.
Bootstrap records it, and `tools/ka` reads it on every later call.
Every later phase then changes `tools/` here, in the same pull
request as the code it tests, and `shakenfist/actions` is changed
exactly once, in this phase. That also keeps kerbside's CI off
the prototype's critical path for the rest of the plan.

Concretely:

* `_build/common.sh` gains `--deployer venv|launcher`, default
  `venv`, and rejects any other value.
* `tools/bootstrap-kolla-ansible` writes the value to
  `/srv/kolla-ansible/deployer`. This marker is private to our CI
  tooling. It is deliberately not the `<configdir>/deployer.conf`
  of Open question 1, whose format belongs to the launcher and
  should not be fixed early by a CI shim.
* `tools/ka` reads the marker. If it is absent it treats the value
  as `venv`, which is what keeps kerbside's CI and every existing
  entry unchanged. `venv` behaves as today. In this phase `launcher`
  prints one line on stderr, `deployer: launcher (not yet
  implemented, using venv)`, and then does the same. Any other value
  is an error, not a fallback.
* `tools/postinstall-kolla-ansible` calls `tools/ka post-deploy`
  instead of `kolla-ansible post-deploy`, so every Kolla-Ansible
  invocation after bootstrap goes through the one dispatch point.
* Bootstrap's own `install-deps`, `certificates` and
  `bootstrap-servers` stay on the venv with a comment saying so.
  `bootstrap-servers` is what installs the container engine, so
  routing it through a launcher that needs an engine is exactly
  the chicken-and-egg problem Phase 4 exists to solve. In launcher
  mode `install-deps` disappears altogether once the image carries
  the collections (Phase 1).
* `tools/ka` passes its arguments with `"$@"` rather than `$*`.
  See "Bugs fixed during this work".

**The action's input is passed through only when it is not the
default.** The `deployer` input defaults to `venv`, and the
bootstrap step adds `--deployer ${{ inputs.deployer }}` only when
the value is not `venv`. It is ignored when `skip_bootstrap` is
set: kerbside, the only such caller, bootstraps for itself and
would pass the flag to its own bootstrap call if it ever wanted
the prototype.

**The entry is non-voting through a matrix key.** Add
`'deployer': 'launcher'` and `'non_voting': 'true'` to the new
entry, and `continue-on-error: ${{ matrix.test.non_voting ==
'true' }}` to the `test_installs` job. A failing prototype job
still shows red on the pull request, but the run's conclusion stays
green. One side effect is accepted:
`auto-retry-infra-failures.yml` only fires on a failed run, so an
infrastructure flake that hits only the prototype job is not
retried automatically. Phase 7 removes the key, and with it the
side effect.

**The prototype job asserts that it is the prototype.** A step
that runs only when `matrix.test.deployer == 'launcher'`, straight
after the deploy, fails unless `/srv/kolla-ansible/deployer` on the
target reads `launcher`. Without it, a plumbing mistake that drops
the input would leave a "prototype" job that quietly tests the venv
path, and stays green while doing so. Phase 2 makes the assertion
stronger, by also checking that the venv's `kolla-ansible` was never
run.

#### Landing order

The flag is only sent when it is not the default, so neither
repository depends on the other being merged first for existing
callers. The order below is chosen so that each merge can be tested
before it lands:

1. **This repository, tools (0b).** Adds the flag, the marker and
   the dispatch. Nothing passes `--deployer` yet, so every CI entry
   exercises the default path, and a green run proves nothing
   changed.
2. **`shakenfist/actions` (0c).** Adds the input. For this pull
   request's test run, 0d's branch temporarily points its `uses:`
   at the actions branch (`shakenfist/actions/deploy-kolla-ansible@<branch>`),
   so that the prototype entry runs green against the unmerged
   action. Merge the actions pull request only after that.
3. **This repository, CI entry (0d).** Points `uses:` back at
   `@main` once the actions pull request has merged. It must not
   merge while it still points at a branch.

After step 2 merges, check the next kerbside `functional-tests`
run on `develop` and record its link in this phase's `Merged`
cell. Merging an actions change is the only moment in this plan
when kerbside's CI can be broken by it.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 0a | -- | -- | -- | Done while planning: `develop` is unprotected and has no rulesets, so no check is required by name. See above. |
| 0b | medium | sonnet | worktree | In this repository: add `--deployer` (values `venv`, `launcher`; default `venv`; anything else exits non-zero) to the argument parser in `_build/common.sh`, following the shape of `--topology`. In `tools/bootstrap-kolla-ansible`, write the value to `/srv/kolla-ansible/deployer` just after the venv is created, and add a comment on the `install-deps`, `certificates` and `bootstrap-servers` calls saying that they stay on the venv until Phase 4 of `docs/plans/containerised-deployer.md`. Rewrite `tools/ka` to read the marker (absent means `venv`), run the venv's `kolla-ansible "$@"` for `venv`, print `deployer: launcher (not yet implemented, using venv)` on stderr and then do the same for `launcher`, and exit non-zero naming the marker for any other value. Change `tools/postinstall-kolla-ansible` to call `./tools/ka post-deploy -i /etc/kolla/inventory`. All of it must be shellcheck-clean. Verify by running `tools/ka` against a scratch marker for each of the three cases, and by showing that `bash -n` passes on every file touched. |
| 0c | medium | sonnet | worktree | In shakenfist/actions, add a `deployer` input to `deploy-kolla-ansible/action.yml` (description: "Which Kolla-Ansible deployer bootstrap installs: venv (default) or launcher, the containerised deployer prototype in kerbside-patches"; default `venv`). In the bootstrap step, append `--deployer ${{ inputs.deployer }}` only when the value is not `venv`, following the existing `container_distro` conditional. Change nothing else. Read that repository's AGENTS.md first and follow its conventions for the pull request. |
| 0d | medium | sonnet | none | In `.github/workflows/functional-tests.yml`: copy the `master-h-debian13-c-debian13-aio` entry of `test_installs` to a new entry after it, named `master-h-debian13-c-debian13-aio-launcher`, described `master debian 13 images on debian 13 all-in-one with the containerised deployer`, and adding `'deployer': 'launcher'` and `'non_voting': 'true'`. Add `continue-on-error: ${{ matrix.test.non_voting == 'true' }}` to the job. Pass `deployer: ${{ matrix.test.deployer \|\| 'venv' }}` to the deploy step. Add a step after "Deploy Kolla-Ansible", conditional on `matrix.test.deployer == 'launcher'`, which ssh-es to the target in the same style as its neighbours and fails unless `cat /srv/kolla-ansible/deployer` prints `launcher`. For testing, point `uses:` at 0c's branch; the management session restores `@main` before merge. |

Exit criteria:

* The prototype entry has run green on 0d's pull request. Its log
  shows the `deployer: launcher` line from every `tools/ka` call,
  and the assertion step passed.
* Every other entry on the same run is green. If one is not, it
  fails in the same way on a `develop` run without these changes.
* The kerbside `functional-tests` run on `develop` after the
  actions merge is green, or is red for a reason visible on its
  previous run too. Its link is recorded.
* `functional-tests.yml` calls `@main` again.

#### Outcome

Completed on 2026-10-01. The prototype entry ran green on #1796,
with its marker assertion passing. Every other entry was green
too, once one multinode job had been re-run: its deploy had
passed, and it then stalled in an ssh inventory step until the
120-minute timeout.

Kerbside's first complete `functional-tests` run on `develop` after
shakenfist/actions#121 merged was green, including its deploy
through the changed action:
https://github.com/shakenfist/kerbside/actions/runs/36768693908.

One slip, worth remembering for the next cross-repository change.
#1796 and actions#121 were merged in the same minute, before the
commit restoring `@main` had reached #1796. Merging actions#121
deleted its branch, so `develop` briefly called an action ref that
no longer existed, and every `test_installs` job would have failed
to start. #1799 restored `@main`. Next time, merge the action
first, then push the restore to the dependent pull request, and
merge that only once the restored run is green. The pull request
checklist said this, but nothing enforced it.

### Phase 1: Deployer image

Planning effort: high. It sets the contract the launcher is
written against, and it changes how every image is tagged.

Produce the Kolla image from Decision 1. It contains the patched
Kolla-Ansible, an `ansible-core` inside Kolla-Ansible's supported
range, and every collection from `requirements.yml` and
`requirements-core.yml`, installed at build time. Nothing is
fetched from Galaxy or git when the image runs. The image's
`nsenter` carries the file capabilities from Decision 6, and the
image tolerates running as an arbitrary UID. Its entrypoint is the
real `kolla-ansible`, so `docker run <image> --help` works with no
launcher at all.

**In scope:** the Kolla patch that defines the image; the
`ansible-collection-kolla/` project directory and the build
configuration that feed it patched, pinned sources; the image
hash; and a local build that proves the exit criteria.

**Out of scope:**
* anything that runs the image: `tools/ka` dispatch and the
  launcher are Phase 2;
* solving SSH for an arbitrary UID: recorded below, and left to
  Phase 2, because the launcher decides which user runs the
  container;
* fixing occystrap if it strips file capabilities (step 1a): that
  is a fix in another repository, and Phase 3 is the first phase
  that needs the capability to survive a push.

#### What the survey found

Surveyed on 2026-10-01 against kolla `42ca15b70`, kolla-ansible
`beddefce7` and ansible-collection-kolla `11fa4c282`. The section
this replaces was written before Phase 0 ran, and several of its
claims were wrong. They have been corrected at their source in
this commit: Decision 1's path, the Situation SHAs, and this
section's own steps.

*Kolla.*
* Image definitions are under `kolla/docker/`, not `docker/`.
  Kolla moved them, so the new image is
  `kolla/docker/kolla-ansible/Dockerfile.j2`.
* Nothing in Kolla contains Kolla-Ansible or the collection
  today. The collection appears only as a source of Zuul roles
  (`zuul.d/base.yaml:12`).
* Sources are config groups. Defaults are in
  `kolla/common/sources.yaml`, and an image takes `[<image>]` as
  its own source, every `[<image>-plugin-*]` as a plugin, and
  every `[<image>-additions-*]` as an addition
  (`kolla/image/kolla_worker.py:792-820`).
  * A `type = local` directory is tarred whole, `.git` included,
    with its basename as the top level (`kolla/image/tasks.py:279-286`).
  * A `type = git` source is cloned and checked out at its
    `reference`, which can be a SHA (`tasks.py:250-276`).
  * Plugins arrive in `plugins-archive`, under `plugins/`.
    `mariadb-server/Dockerfile.j2:49-50` is the precedent for
    using a plugin as a plain file source.
* Kolla's pep8 runs `tools/validate-build-sources-overrides.py`
  (`tox.ini:55`). It fails unless every `sources.yaml` entry also
  has two companions:
  * a `kolla_build_sources` mapping in
    `roles/kolla-build-config/defaults/main.yml`;
  * a `required-projects` entry in `zuul.d/base.yaml`, and for a
    git source an `override-checkout` equal to its reference.

  `_build/test-apply.sh --skip-tests kolla` does not run pep8.
  `rebase-tests.yml` runs it in full.
* The Jinja context (`kolla_worker.py:369-395`) gives templates
  `openstack_release`, `kolla_version`, `image_name` and similar.
  Kolla adds the OCI labels itself (`kolla_worker.py:768-785`).
  Nothing exposes a *source's* git SHA to a template:
  `tasks.py:262` computes it and only logs it.
* `openstack_release` renders as `master` on master, not as a
  release name (`kolla/common/config.py:39`).
* `kolla-toolbox` is the nearest existing image, with its own
  `/opt/ansible` venv, `ansible-core==2.21.*` and six collections.
  It is a model rather than a parent. It carries RabbitMQ, Open
  vSwitch and a different collection set, and our patch154
  already changes it.
* `openstack-base` provides `/var/lib/kolla/venv`, made with
  `--system-site-packages` and already on `PATH`
  (`openstack-base/Dockerfile.j2:196-208`). It also provides upper
  constraints at `/requirements`, and a layer shared with every
  service image.
* Python is 3.13 on trixie and 3.12 on noble and Rocky 10. All of
  them satisfy Kolla-Ansible's `requires-python >=3.12` and
  ansible-core 2.21's own requirement.
* `nsenter` is in all three bases (util-linux). `setcap` is not on
  Debian or Ubuntu. The precedent for adding it is
  `prometheus-blackbox-exporter/Dockerfile.j2:12-30`, which
  installs `libcap2-bin` (deb) or `libcap` (rpm) and runs
  `setcap cap_net_raw+ep`.
* No parent image installs an SSH client on Debian or Ubuntu.

*Kolla-Ansible at `beddefce7`.*
* It requires `ansible-core>=2.20,<2.22` (`requirements.txt:14`).
* `requirements.yml` is one git entry: the collection at
  `version: master`.
* `requirements-core.yml` lists seven Galaxy collections
  (`ansible.netcommon<9`, `ansible.posix<3`, `ansible.utils<7`,
  `community.crypto<4`, `community.general<14`,
  `community.docker<6`, `containers.podman<2`). None of our patches
  touch either file.
* `install-deps` runs `ansible-galaxy collection install --force`
  with no `-p` (`kolla_ansible/utils.py:96-127`). Collections
  therefore land in `~/.ansible/collections`, which comes ahead of
  `/usr/share/ansible/collections` on the search path. Left alone,
  a user's home directory, or an `install-deps` run inside the
  container, would shadow the collections baked into the image,
  and lockstep would break without anyone noticing.
* A non-editable install finds its playbooks under
  `<prefix>/share/kolla-ansible`, by the last-`lib` heuristic at
  `kolla_ansible/utils.py:67-93`. That is read from the code, not
  tested, so the exit check tests it.
* The collection's `galaxy.yml` declares no dependencies.
  ansible-galaxy can install it from a local directory.

*This repository.*
* "Our build already feeds patched trees from `src/` to
  `kolla-build`" was half true. The `type = local` mechanism exists
  (`etc/kolla-build-master.conf.in:9-15`, with paths substituted
  by `_build/imagebuild.sh:141-145`). But it feeds kerbside and
  nova, and neither is patched here. The only patched tree a build
  uses today is Kolla itself, installed as the build tool. The
  mechanism serves; this is just its first use with a patched
  tree.
* `_build/assemble-source.sh:42-60` already honours a `FORCE` file,
  so a project directory with an empty `ORDER` needs no change
  there. That makes the old step 1c's "teach `assemble-source.sh`
  to clone it" wrong.
  * `_build/imagebuild.sh:27-46` does not honour `FORCE`. CI does
    not notice, because it unpacks every source tarball first;
    local builds would.
  * An empty `ORDER` breaks none of the tooling. The survey
    checked `apply-patches-and-test.sh`, the daily rebase,
    `repush-openstack.sh`, the Depends-On check and pre-commit.
* The daily `_build/bump-source-shas.sh` moves every project's
  `source_sha`, whether or not `skip_rebase` is set. The
  collection's pin will therefore follow master daily, like every
  other project's. That is the "pinned commit, not a branch" the
  original section asked for: the image records the SHA it was
  built from, and the SHA changes only through a reviewed bump.
* The image is built and pushed with no filter change. The
  `build_images` regex (`functional-tests.yml:259-267`) contains
  `kolla`, and Kolla matches with `re.search`, so `kolla-ansible`
  and `openstack-base` both match.
* The image hash is worse than the plan said.
  `_build/calculate-container-hash.sh` is called with `kolla
  kolla-2* nova nova-2* requirements requirements-2* etc src _build
  tools` (`functional-tests.yml:246-249`). Four things follow:
  * the `kolla-ansible` project directory is not passed at all;
  * the patch term hashes `ORDER`-listed patch files but never
    `config.yaml`, so a `source_sha` bump with unchanged patches
    changes nothing;
  * the `src` term hashes empty input (see "Bugs fixed");
  * even with that fixed, it hashes only `*.py`, which would miss
    every role, template and collection, since those are YAML.

  `_build/build-containers.sh:166-197` skips the whole build when
  any image with the tag already exists. A deployer image added
  without a hash change could therefore be skipped on the day it
  lands.
* **New risk.** Our push path may strip file capabilities. The
  occystrap proxy rewrites every layer
  (`_build/build-containers.sh:53-55`), and `occystrap/tarformat.py`
  chooses USTAR unless a name, size or uid needs PAX. It never
  checks `pax_headers`, which is where
  `SCHILY.xattr.security.capability` lives. Python's `tarfile`
  silently drops the xattr when writing USTAR; this was checked on
  2026-10-01 with a one-file archive. If the risk is real, it
  already affects `prometheus-blackbox-exporter`'s `cap_net_raw`,
  and step 1a checks exactly that.
* Not this plan's, but seen: `ARCHITECTURE.md` lists
  `kolla-2025.1/`, `kolla-ansible-2025.x/` and `nova-2025.1/`
  directories that do not exist.

#### Decisions

1. **Parent image: `openstack-base`.** It already has a venv on
   the right Python with upper constraints applied, so
   Kolla-Ansible installs with Kolla's standard `install_pip`
   macro, in the same way as every service. `kolla-toolbox` is
   copied from, not built on. Its Galaxy retry loop is reused.

2. **Sources.** Upstream, in `sources.yaml`:
   * Kolla-Ansible is a `url` source pointing at the
     tarballs.opendev.org branch tarball, like the OpenStack
     services.
   * The collection is a git plugin,
     `kolla-ansible-plugin-ansible-collection-kolla`, at `master`,
     because it has no tarball.

   Both get their `kolla_build_sources` and Zuul `required-projects`
   companions. Here, `etc/kolla-build-master.conf.in` overrides
   both with `type = local` sections pointing at `src/`. Our image
   is therefore built from the patched Kolla-Ansible and the pinned
   collection, while upstream's definition stays the ordinary one.

3. **Collections are baked into one fixed path, which is the only
   path searched.** Both requirements files are installed with
   `-p /usr/share/ansible/collections`. The collection is installed
   from the plugin directory, not by its git URL, and with the
   retry loop for Galaxy. The image sets
   `ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections`. A
   build-time `ansible-galaxy collection list --format json` is
   saved into the image as evidence of what was installed.

4. **Provenance goes in a file in the image, and labels carry only
   what the launcher needs before running anything.** This is the
   decision most likely to be argued with.
   * The labels are static: the launcher protocol version (`kolla_ansible_launcher_protocol=1`),
     `openstack_release` exactly as Kolla renders it (`master` on
     master), and Kolla's own OCI labels.
   * At build time, a `RUN` writes `/etc/kolla-ansible/deployer.json`.
     It holds:
     * Kolla-Ansible's version, from its installed metadata;
     * the git SHA of each source tree, read from its `.git`, which
       Kolla's local archive keeps, or `unknown` for a tarball
       source;
     * the collection list above.

   Labels cannot be set from `RUN` output. The alternatives were:
   * `build_args`, which work, but make every caller, upstream's
     Zuul included, compute SHAs outside the build;
   * a Kolla engine change to publish source SHAs as labels, which
     is generally useful, but is a second, larger upstream change
     riding on the first.

   The launcher's start-up check (Phase 5) needs only the protocol
   label. Everything else is for humans and for Phase 5's
   in-container checks, which can read a file. If Phase 6 wants
   source SHAs as labels after all, the engine change is a separate
   patch that does not disturb this one.

5. **Arbitrary UID.** The image sets `HOME` to a world-writable
   `/var/lib/kolla-ansible` (sticky, mode 1777). Ansible's local
   temporary directory and SSH control path then work for a UID
   with no passwd entry. The image's default `USER` is the existing
   `ansible` user (uid 42401). The image installs an SSH client
   (`openssh-client` on deb, `openssh-clients` on rpm).

   The remaining problem is deliberately left to Phase 2: OpenSSH
   refuses to run for a UID with no passwd entry, and it reads
   `~/.ssh` from that entry, not from `$HOME`. The fix depends on
   whose UID the launcher passes and what it mounts.

6. **`setcap` on `/usr/bin/nsenter` itself**, not on a copy.
   `community.docker.nsenter` runs `nsenter` from `PATH`, so a
   copy elsewhere would need configuration. This phase sets the
   capabilities and checks them on the locally built image;
   whether they survive our push is step 1a, and whether they
   work is Phase 3.

7. **The hash covers what is in the image.** The `src` term
   becomes one line per repository under `src/`: its name and
   `git rev-parse HEAD^{tree}`. That covers patched content,
   `source_sha` bumps, and both of the new sources. `kolla-ansible`
   and `ansible-collection-kolla` join the script's arguments, the
   nonexistent `nova` and `requirements` globs leave them, and the
   tag prefix moves from `v22` to `v23`, so that the first build
   after this lands cannot reuse a set with no deployer in it. The
   cost, accepted: a pull request that changes only Kolla-Ansible
   patches now rebuilds every image. Before this change, a
   Kolla-Ansible change never altered the image tag at all, and
   that was wrong as soon as an image contained Kolla-Ansible.

8. **`ansible-collection-kolla/` is a project directory with an
   empty `ORDER` and a `FORCE` file.** Its `config.yaml` follows
   `kolla-ansible/config.yaml` (`source_branch: master`,
   `destination_branch: master-patches`, `release: master`, and
   `source_sha` set to `11fa4c282fdde0dc60365c7890712325a226472d`).
   `imagebuild.sh` learns to honour `FORCE`, as `assemble-source.sh`
   already does.

#### Risks

* **Capabilities stripped on push** (step 1a). If confirmed,
  Phase 1 can still finish, because its exit check is run on the
  locally built image. File an occystrap issue, with the
  blackbox-exporter evidence, as a prerequisite of Phase 3. The
  management session checks that the issue exists before marking
  this phase complete.
* **Upstream pep8 rejects the source entries.** Mitigation: the
  step 1b brief lists all three companions, and the management
  session runs `_build/test-apply.sh kolla` without
  `--skip-tests` before committing the patch.
* **The non-editable data-files heuristic is wrong.** Mitigation:
  the exit check runs `kolla-ansible --version`, and lists
  `site.yml` from the installed share directory. If either fails,
  set `KOLLA_ANSIBLE_DATA_FILES_PATH` in the image, which
  Kolla-Ansible already honours first.
* **The hash change rebuilds more often.** This is accepted in
  Decision 7. Watch the first week of build times in the layer
  data; if they are painful, the answer is a separate deployer
  tag, not hashing less.
* **The daily rebase rewrites the new kolla patch.** As with every
  patch. The Dockerfile is a new file, so only the `sources.yaml`
  and Zuul hunks can conflict.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | low | sonnet | none | Read-only. Find the newest `prometheus-blackbox-exporter` image in the CI registry. Get the tag from the latest green `functional-tests.yml` run's `buildresult-master-debian-trixie` artifact `result.json`, `image_tag`. Get the registry and namespace from `_build/common.sh` and `etc/globals-master.yml`, and the token from the operator, since none is in the repository. Run `getcap` on its `blackbox_exporter` binary, via `docker run --rm --entrypoint getcap <image> <path>`, taking the path from the template. Then build the same template locally and run `getcap` on it, to show the capability exists before the push. Report both results, and read `occystrap/tarformat.py` and the filter path that `_build/build-containers.sh:53-55` uses, to say whether USTAR is what drops it. Commit subject: none; this is evidence for the plan. |
| 1b | high | opus | worktree | Create `_patches/patchNNN-kolla-ansible-deployer-image.patch` against kolla at the `source_sha` in `kolla/config.yaml`, taking NNN from `_build/get-next-patch-number.py`. Add `kolla/docker/kolla-ansible/Dockerfile.j2` per Decisions 1 and 3-6 of Phase 1 in `docs/plans/containerised-deployer.md`: `FROM` `openstack-base`; packages through `macros.install_packages`, using `libcap2-bin` and `openssh-client` on deb and `libcap` and `openssh-clients` on rpm, following `prometheus-blackbox-exporter`; `ADD kolla-ansible-archive` and `ADD plugins-archive /`, then `install_pip` of the Kolla-Ansible tree with constraints; the Galaxy install of `requirements-core.yml` into `/usr/share/ansible/collections`, with kolla-toolbox's retry loop; the collection installed from the plugin directory (glob `*ansible-collection-kolla*`); the `deployer.json` provenance file and the `setcap` on `/usr/bin/nsenter`; `ENV` for `HOME` and `ANSIBLE_COLLECTIONS_PATH`; `ENTRYPOINT ["dumb-init", "--single-child", "--", "kolla-ansible"]`, `CMD ["--help"]` and `USER ansible`. Add the `sources.yaml` entries from Decision 2, with their `kolla_build_sources` and `zuul.d/base.yaml` companions, which `tools/validate-build-sources-overrides.py` demands, and a release note in Kolla's style. List the patch in `kolla/ORDER` only. Recount it with `tools/recount-patch.py --in-place`, regenerate the message file with `tools/extract-commit-message`, and prove it with `_build/test-apply.sh kolla`, with tests and not `--skip-tests`, so that pep8 runs. Commit subject: "Add a Kolla-Ansible deployer image to Kolla." |
| 1c | medium | sonnet | none | Add `ansible-collection-kolla/`: a `config.yaml` modelled on `kolla-ansible/config.yaml`, with the values from Decision 8; an empty `ORDER`; and a `FORCE` file. Make `_build/imagebuild.sh:27-46` honour `FORCE` exactly as `_build/assemble-source.sh:49` does. Add `[kolla-ansible]` and `[kolla-ansible-plugin-ansible-collection-kolla]` `type = local` sections to `etc/kolla-build-master.conf.in`, using `TOPSRCDIR` like the existing two. Prove it with `_build/assemble-source.sh master`, showing `src/ansible-collection-kolla` at the pinned SHA, and with `_build/test-apply.sh --skip-tests ansible-collection-kolla`. Add the new project directory to `ARCHITECTURE.md`'s inventory, which changes the shape of the system. Commit subject: "Feed patched Kolla-Ansible and the collection to kolla-build." |
| 1d | medium | sonnet | none | Implement Decision 7 in `_build/calculate-container-hash.sh` and `.github/workflows/functional-tests.yml`. The `src` term becomes a sorted list of `<dir> <git -C <dir> rev-parse HEAD^{tree}>` lines for every directory under `src/`, then hashed. Rewrite its comment, which claims Kolla-Ansible does not affect images. Fix the argument list, and change the tag prefix to `v23`. Also check `local-container-builds.yml` and any other caller of the script. Prove it on a scratch `src/`: two runs give the same hash, and the hash changes after a commit in one repository, and after a `source_sha` change. Commit subject: "Hash the content of every source tree into image tags." |
| 1e | medium | sonnet | none | Build locally with `_build/build-containers.sh --build-targets master --distro debian --distro-version trixie --build-images "^(base\|openstack-base\|kolla-ansible)$" --image-tag local` (check the flags against `_build/common.sh`). Run the exit script below and report its full output. Commit nothing unless something fails and is fixed, in which case the fix goes back to the step that owns it. |

Exit: the script below passes against the locally built image,
1b's patch passes `_build/test-apply.sh kolla` with tests, and the
functional tests on the phase's pull request are green with the
new tag prefix. Step 1a's result is recorded here and, if the
capability is stripped, an occystrap issue exists.

```bash
#!/bin/bash -e
# Phase 1 exit check: run against the locally built deployer image.
img=${1:?usage: $0 <image>}
run() { docker run --rm --network none "$@"; }

run "${img}" --version
run --user 4242:4242 "${img}" --version
run --entrypoint ansible-galaxy "${img}" collection list \
    | grep -E 'openstack\.kolla|community\.docker|ansible\.posix'
run --entrypoint sh "${img}" -c \
    'ls "$(python3 -c "import sys; print(sys.prefix)")/share/kolla-ansible/ansible/site.yml"'
run --entrypoint getcap "${img}" /usr/bin/nsenter | grep cap_sys_admin
run --entrypoint cat "${img}" /etc/kolla-ansible/deployer.json
docker inspect "${img}" --format '{{json .Config.Labels}}' | grep -q launcher
echo "Phase 1 exit check passed."
```

**Step 1e result (2026-10-01): the exit check passed.**
* Built locally for Debian trixie: base, openstack-base and the
  deployer, about six minutes in all.
* `kolla-ansible 22.1.0.dev266`, ansible-core 2.21.4 and nine
  collections were installed. `nsenter` carries
  `cap_sys_chroot,cap_sys_ptrace,cap_sys_admin=ep`.
* `deployer.json` records real SHAs for both trees: the patched
  Kolla-Ansible, and the collection at `11fa4c282`.
* `ansible localhost -m ping` works as uid 4242.
* The image is 1.68 GB, against 1.46 GB for openstack-base.
* `build-containers.sh` has no local-only mode, because it always
  pushes. The local route is `_build/assemble-source.sh --no-tarball
  master`, then `_build/imagebuild.sh` with the same flags.

**Step 1a result (2026-10-01): the capability is stripped.**
* Running occystrap's real `normalize-timestamps` filter (develop
  `0b810de`) over a layer whose member carries
  `SCHILY.xattr.security.capability` produced the member with no
  PAX headers at all.
* The `exclude` filter uses the same format selection, and occystrap's
  tar outputs hard-code USTAR.
* Upstream's `prometheus-blackbox-exporter:master-debian-trixie` on
  quay.io still has `cap_net_raw=ep`, so `kolla-build` output is
  fine. The loss happens in our push path.
* The CI-registry image itself was not checked: this host has no
  credentials for `gitlab.home.stillhq.com:5050`.

This is filed as shakenfist/occystrap#151, a prerequisite of
Phase 3.

### Phase 2: Launcher

Planning effort: high. It fixes the launcher's command line and
`deployer.conf`, which every later phase builds on, and it is the
first phase in which the prototype CI entry stops running the venv.

Write `kolla-ansible-launcher` (Decision 4) in this repository,
under `deployer/launcher/`, with its own `pyproject.toml` (Decision
8). Then make the prototype CI entry run every Kolla-Ansible call
after bootstrap through the launcher, as the unprivileged `debian`
account rather than root.

**In scope:** the launcher, its unit tests and its lint; its
documentation page; `tools/ka` dispatch; the bootstrap and
inventory changes the prototype entry needs to run through it; and
a stronger CI assertion.

**Out of scope:**
* `localhost` over `community.docker.nsenter`. That is Phase 3. Until
  then the prototype entry reaches its own host over SSH (Decision
  2), which is a stopgap.
* Moving `kolla-genpwd`, `certificates` and `bootstrap-servers` off
  the venv. That is Phase 4.
* A home for the OpenStack clients outside the venv. The venv still
  exists in this phase, so nothing needs it yet; moved to Phase 4,
  which removes the venv.
* Podman in CI. The launcher supports it, and unit tests cover the
  command it builds. (This said Phase 3's Rocky 10 entry would be the
  first podman host. It is not: Rocky CI hosts run `docker-ce`, as
  Phase 3's survey found.)
* The multinode inventories' `[deployment] localhost` line, which
  would also run inside the container. No launcher entry is
  multinode.

#### What the survey found

Surveyed on 2026-10-02 against this repository at `13f4f34fc` and
kolla-ansible `54aab1476`. None of the eleven patches in
`kolla-ansible/ORDER` touch `kolla_ansible/` or `post-deploy.yml`.
The section this replaces was written before Phases 0 and 1 ran.
Its false claims are corrected below, and at their source in this
commit: the Phase 4 section now carries the OpenStack clients, and
the `--init` requirement is gone (Decision 6).

*The prototype entry cannot run through a container as it stands.*
* Its inventory, `etc/inventory-all-in-one-master:3-22`, puts
  `kolla ansible_connection=local` in every group. Inside a
  container, `local` is the container. So the original exit,
  "against a remote host bootstrapped the old way", has no remote
  host to run against: the CI entry is an all-in-one.
* Every CI node already accepts `/srv/github/id_ci` for the base
  user, including the deploy host itself
  (`ansible/kerbside-single-node.yml:7-8`). The key is on the
  deploy host, owned by `debian` (shakenfist/actions
  `setup-kerbside-environment`, action.yml:118-123). The multinode
  inventories already use it (`etc/inventory-multinode-master:2-6`).
* `tools/ka` runs as root. The action ssh-es in as `debian` and runs
  `sudo ./tools/ka ...`, so `SUDO_USER=debian`. The original exit
  asked for "a non-root shared account", so CI has to drop to
  `debian` itself.
* `debian` is already in the `docker` group
  (`_build/install-build-dependencies.sh:103`). Docker is installed
  and running before bootstrap.
* Nothing on the target runs `docker login`. Kolla-Ansible passes
  registry credentials per pull, so the deployer image has no
  credentials to be pulled with. The token reaches bootstrap as
  `registry_token` (`tools/bootstrap-kolla-ansible:206-210`).
* The deployer image is pushed as
  `gitlab.home.stillhq.com:5050/openstack/kolla-images/kolla-ansible:<image tag>`,
  beside the service images (`etc/globals-master.yml:100-106`).

*Kolla-Ansible's command line.*
* Path-valued options, all in `kolla_ansible/ansible.py`:
  * `-i/--inventory`, repeatable, a file or a directory (:61,
    :167-180). The default is under the config directory (:149).
  * `-e/--extra-vars`, repeatable; `@file` is any path (:53, :261).
  * `-p/--playbook`, repeatable (:95).
  * `--vault-password-file` and `--vault-pass-file`, repeatable
    (:110).
  * `--vault-id`, whose value may be `label@path` (:102).
  * `--configdir`, defaulting to `$KOLLA_CONFIG_PATH` or `/etc/kolla`
    (:23-25, :129). It implies `globals.yml`, `passwords.yml` and
    `globals.d/`.
  * `--passwords` (:137). It is only checked for readability (:191),
    and `build_args` always uses `<configdir>/passwords.yml`. That is
    an upstream bug, and not ours to fix here.
  * cliff's global `--log-file`.

  Every path is made absolute against the current directory. The
  parser is argparse, so `-iX`, `--inventory=X` and `-e@f` all
  parse, and so do unambiguous long-option prefixes.
* **`genpwd` is not a subcommand.** `kolla-genpwd`, `kolla-mergepwd`,
  `kolla-readpwd` and `kolla-writepwd` are separate console scripts
  (`pyproject.toml:33-37`). A launcher that provides only
  `kolla-ansible` cannot generate passwords, and the success
  criteria need it to.
* `install-deps` installs into `~/.ansible/collections`
  (`utils.py:96-127`). In the image that path is not searched (Phase
  1, Decision 3), so running it there is a confusing no-op.
* Several commands write on the controller, all under the
  configuration directory:
  * `post-deploy` writes `clouds.yaml` and the `*-openrc*.sh` files,
    mode 0600, owned by `ansible_facts.user_uid`. It uses `become`
    only when the directory is not writable (`post-deploy.yml:6-13`).
  * `certificates` and `octavia-certificates` write under `certificates/`
    and `node_custom_config`.
  * hacluster's bootstrap writes authkeys there too.

  Two writes are not under the configuration directory, and both
  are on `localhost`, which is the container:
  * `validate-config` uses `become` to write `.err` files to
    `/var/log/kolla/config-validate`;
  * `mariadb-recovery` writes to `/tmp`.
* No `ansible.cfg` ships with Kolla-Ansible, and the CLI reads no
  `ANSIBLE_*` variables itself. They reach `ansible-playbook` through
  the environment, which is why they must be forwarded.

*This repository.*
* `tools/postinstall-kolla-ansible` already calls `./tools/ka
  post-deploy` (Phase 0). The OpenStack clients were linked out of the
  bootstrap venv by `tools/install-openstack-clients`, on the target
  and also on the CI runner (`functional-tests.yml:874-890`). Phase 4
  moved them to `/srv/openstack-clients/venv`.
* There is no Python tooling: no `pyproject.toml`, `tox.ini` or
  flake8 configuration. Pre-commit runs actionlint, shellcheck,
  skillsaw and the two patch checks.
  `tools/test-recount-patch.py` and `tools/test-check-depends-on.py`
  exist, but nothing runs them. That is recorded here and not fixed.
* The local `kolla/kolla-ansible:local` image from Phase 1 step 1e
  is still on the build host, so the launcher can be exercised
  end to end without CI.

#### Decisions

1. **The launcher installs into its own venv, and CI drops to
   `debian` to run it.** Bootstrap creates
   `/srv/kolla-ansible/launcher-venv` and installs
   `deployer/launcher` into it. The venv that bootstrap still needs
   holds the real `kolla-ansible`, and Decision 4 forbids the two in
   one environment. In launcher mode, `tools/ka` runs `sudo -u
   "${SUDO_USER}" /srv/kolla-ansible/launcher-venv/bin/kolla-ansible
   "$@"`, after bootstrap has `chown -R`'d `/etc/kolla` to that
   user. Running it as root would have been easier and would have
   tested nothing that Phase 3 depends on: the identity of the
   unprivileged user, ownership of what the run writes, and SSH as a
   user who is not root.

2. **Until Phase 3, the prototype entry reaches its own host over
   SSH.** A new `etc/inventory-all-in-one-launcher-master` matches
   the all-in-one inventory except in its connection. `kolla` keeps
   its inventory name, so every host variable is unchanged, but its
   connection is `ansible_host=127.0.0.1 ansible_user=USER
   ansible_ssh_private_key_file=/srv/github/id_ci`, with the
   multinode file's `ansible_ssh_extra_args` plus
   `-o UserKnownHostsFile=/dev/null`. Bootstrap uses it when the
   deployer is `launcher` and the topology is all-in-one. **This is
   the decision most likely to be argued with.** Phase 0 promised
   the prototype entry would differ from its control only in the
   deployer, and for this one phase it also differs in the
   connection. The alternative was to wait for Phase 3's nsenter
   before the entry ran the launcher at all. That would leave
   Phase 2 untested in CI, and would make Phase 3 land a new program
   and a new connection plugin together, which is the attribution
   problem in a worse form. The top-level Decision 5 rejected SSH to
   `127.0.0.1` as the design, and this is not that: Phase 3 replaces
   the file's SSH connection with nsenter. (This said Phase 3 would
   delete the file. It cannot, because the launcher entries still
   need an inventory of their own; corrected in Phase 3's planning.)

3. **Identity inside the container is synthesised from the host.**
   The launcher runs with `--user <uid>:<gid>`. It writes a
   `passwd` file with two entries, `root` and the invoking user
   taken from `pwd.getpwuid()` (so LDAP and sssd users work), and a
   matching `group` file. Both are mounted read-only over
   `/etc/passwd` and `/etc/group`. The user's entry has the image's
   home directory, `/var/lib/kolla-ansible`, not their own: on a
   local connection Ansible expands `~` from the passwd entry, not
   `$HOME`, so a real home directory sent `~/.ansible/tmp` somewhere
   unwritable (found in step 2a). `<home>/.ssh` is mounted
   read-only both at `/var/lib/kolla-ansible/.ssh`, where OpenSSH
   looks, and at its own path, for configuration that names it
   absolutely. This answers the problem Phase 1 left over: OpenSSH
   refuses a UID with no passwd entry, and it reads `~/.ssh` from
   the passwd entry, not from `$HOME`. Rejected:
   * mounting the host's whole `/etc/passwd`, which misses NSS users;
   * a writable `/etc/passwd` in the image, which is the OpenShift
     pattern and would need a patch199 change;
   * `nss_wrapper`, which would need `LD_PRELOAD` in the image.

   The files are rewritten on every run under
   `${XDG_RUNTIME_DIR:-~/.cache}/kolla-ansible-launcher/`, because the
   launcher `exec`s the engine and cannot clean up afterwards.

4. **Mounts are directories, at the same path, read-write.** Every
   path argument is mounted at the same path. That covers the
   options listed in the survey, `KOLLA_CONFIG_PATH`, the current
   working directory, and the `SSH_AUTH_SOCK` socket.
   * For a file the launcher mounts its parent directory, and for a
     directory the directory itself. Ansible loads `group_vars/` and
     `host_vars/` from beside an inventory file, and `@file` vars
     commonly include their neighbours.
   * A mount under another mount is dropped.
   * Arguments are matched in their separate, `=` and attached forms.
     Abbreviated long options are not recognised. They pass through
     unchanged, and fail in the container with a "file not found"
     that names the path, which is a clear enough failure for a
     prototype.
   * Paths the launcher cannot see in the arguments (an inventory's
     `ansible_ssh_private_key_file`, a role's lookup of a host file)
     are listed in `deployer.conf` as `mounts`. In CI that is
     `/srv/github`. This gap is a real finding for the upstream
     proposal, not a CI detail: the launcher cannot know every path
     an inventory names.

5. **`deployer.conf` and the `launcher` subcommand.** The file is
   `<configdir>/deployer.conf`, in INI format, with one `[deployer]`
   section:
   * `image`: a digest reference, and required;
   * `engine`: `docker` or `podman`; optional, defaulting to
     whichever is on `PATH`, preferring docker;
   * `host_namespaces`: a boolean, default true, controlling
     `--privileged --pid=host`;
   * `mounts`: a whitespace-separated list.

   `kolla-ansible launcher pin <image:tag>` pulls the image, resolves
   it to a digest from `RepoDigests`, and writes `image`, keeping any
   other keys. `kolla-ansible launcher show` prints the configuration
   and the image's labels. `launcher` is the only word the launcher
   takes over in Kolla-Ansible's subcommand namespace.

   The launcher does not log in to registries. That is the operator's
   job, as it is for any image. In CI, bootstrap runs `docker login`
   as `debian` with the registry token.

6. **What the container is run with:** `--rm`, `-i`, `-t` only when
   stdin is a TTY, `--network host`, `--user`, the mounts,
   `--workdir` set to the current directory, and the
   `host_namespaces` flags. It also forwards `ANSIBLE_*`, `KOLLA_*`
   and `SSH_AUTH_SOCK`.
   * **There is no `--init`.** The image's entrypoint is already
     `dumb-init --single-child` (Phase 1), so `--init` would only put
     tini above it. The master plan's original requirement is
     withdrawn.
   * The launcher `exec`s the engine, so the exit status and signals
     are the container's own.

7. **The launcher refuses three things before it runs anything:**
   * the `kolla-ansible` distribution installed beside it
     (`importlib.metadata`);
   * an image whose `kolla_ansible_launcher_protocol` label it does
     not speak, which today means anything but `1`;
   * `install-deps`, because the collections are in the image. It
     says so, rather than running the confusing no-op the survey
     describes.

8. **The password tools come with it.** The package also installs
   `kolla-genpwd`, `kolla-mergepwd`, `kolla-readpwd` and
   `kolla-writepwd`. Each runs the same image with
   `--entrypoint dumb-init` and `--single-child -- <tool>`, and has
   its own path arguments translated. Without them, the success
   criterion of a cloud built "using only `pip install` of the
   launcher" fails at the first step. CI keeps using the venv's
   `kolla-genpwd` until Phase 4, so these are covered by unit tests
   and by step 2d, not by CI.

9. **The CI assertion counts launcher calls.** `tools/ka` appends one
   line per call, naming the deployer it used, to
   `/srv/kolla-ansible/ka.log`. A new `tools/check-deployer-launcher`
   is run on the target in place of the inline step. It checks:
   * the marker;
   * that every `ka.log` line says `launcher`, and there are at least
     seven of them;
   * that `deployer.conf` names an `@sha256:` image;
   * that `admin-openrc.sh` and `clouds.yaml` are owned by `debian`.

   This replaces Phase 0's step body, which is longer than the
   repository's limit for inline workflow scripts.

#### Risks

* **`validate-config` needs `become` on the container's
  `localhost`.** It did, and this risk materialised on #1812's first
  CI run. When keystone's validator reports anything, which it does
  on every entry, `service-config-validate/tasks/validate.yml:32`
  creates its output directory with `delegate_to: localhost` and
  `become: true`, and the synthesised user has no sudo in the
  container ("sudo: a password is required"). The mitigation planned
  here, making the step non-fatal, was not available: the step is in
  shakenfist/actions, which this plan changes only in Phase 0.
  Resolved by step 2e instead, a Kolla-Ansible patch. Running the
  container as root, or giving the user sudo inside it, would have
  made a `--privileged --pid=host` container root on the host,
  against top-level Decision 6. Every other task that runs on
  `localhost` with `become` was surveyed at kolla-ansible `54aab14`.
  The only ones are `post-deploy`'s openrc and `clouds.yaml`
  templates, including Octavia's, and their `become` applies only
  when `node_config` is not writable. So controller-side `become` is
  rare upstream, and is a case for the proposal (Phase 8): a
  containerised deployer cannot escalate on its own `localhost`.
* **SSH to `127.0.0.1` as `debian` hits something the multinode
  path does not**, such as a missing `authorized_keys` entry on the
  deploy host itself. Mitigation: before the first push, step 2c
  confirms that `ssh -i /srv/github/id_ci debian@127.0.0.1 true`
  works on a CI node, from the bootstrap log of a debug run, or
  makes bootstrap check it with a clear error.
* **`chown -R /etc/kolla` hides an ownership bug that root used to
  mask**, for example a later root-run step writing there. The
  assertion's ownership check catches the files that matter, and
  the step order means bootstrap is done writing before the chown.
* **Unit tests pass, but the real engine rejects the command
  line.** Step 2d runs the launcher against the real local image, as
  a non-root user, before anything is pushed.
* **The rebuild cost from Phase 1 Decision 7.** Nothing in this phase
  changes a hashed directory except `tools/` and `etc/`, and those
  already change the hash, so this phase adds no new rebuild cost.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Write `deployer/launcher/`: `pyproject.toml` (setuptools, distribution `kolla-ansible-launcher`, `requires-python >=3.9`, no dependencies, console scripts `kolla-ansible` and the four `kolla-*pwd` tools of Decision 8) and a package `kolla_ansible_launcher` that implements Decisions 3-8 of Phase 2 in `docs/plans/containerised-deployer.md`. Read that phase's survey first: the path-bearing options and their line numbers in kolla-ansible `kolla_ansible/ansible.py` are listed there, and the password tools' options are in `kolla_ansible/cmd/{genpwd,mergepwd,readpwd,writepwd}.py` at the `source_sha` in `kolla-ansible/config.yaml` (clone it to the scratchpad to read them). Standard library only; single quotes; 120 columns. Separate argument-to-mount translation and command construction from execution, so both can be tested without an engine. Write `unittest` tests (no pytest) under `deployer/launcher/tests/` covering: relative paths against a given cwd; repeated `-i` in separate, `=` and attached forms; `-e @file` and `-e@file`, with `-e key=value` left alone; `--vault-id label@path`; `--configdir` from `KOLLA_CONFIG_PATH`; nested mounts deduplicated; `deployer.conf` parsing and `pin` keeping other keys; the passwd and group synthesis; the refusals of Decision 7; and the full docker and podman command lines for one realistic invocation. Commit subject: "Add a launcher for containerised Kolla-Ansible." |
| 2b | medium | sonnet | none | Add to `.pre-commit-config.yaml`, scoped to `^deployer/`, flake8 from the upstream `pycqa/flake8` repository with `--max-line-length=120`, and a local hook running `python3 -m unittest discover -s deployer/launcher -t deployer/launcher`. Write `docs/deployer-launcher.md`: what the launcher is, how to install it, `deployer.conf`, `launcher pin` and `launcher show`, what it mounts and why, and what it refuses. Link it from `docs/index.md`. Add `deployer/launcher/` to `ARCHITECTURE.md`'s inventory, with a short paragraph saying this repository now holds one program alongside its patches, and why (top-level Decision 8), linking the doc page. Add one line to `AGENTS.md` saying that Python under `deployer/` is linted and tested by pre-commit, since that is a new convention. Commit subject: "Lint, test and document the deployer launcher." |
| 2c | medium | sonnet | none | Wire the prototype CI entry to the launcher, per Decisions 1, 2, 5 and 9 of Phase 2. Add `etc/inventory-all-in-one-launcher-master`. In `tools/bootstrap-kolla-ansible`, when the deployer is `launcher`: pick that inventory for the all-in-one topology; after `bootstrap-servers`, create `/srv/kolla-ansible/launcher-venv`, `pip install` `deployer/launcher` into it, run `docker login` as `${SUDO_USER}` with the registry token on stdin, `chown -R "${SUDO_USER}:"` `/etc/kolla`, run `launcher pin` as that user for `<docker_registry>/<docker_namespace>/kolla-ansible:<image tag>`, taking the values that bootstrap already substitutes into `globals.yml`, and add `mounts = /srv/github` to `deployer.conf`. Check `ssh -i /srv/github/id_ci -o BatchMode=yes ${SUDO_USER}@127.0.0.1 true`, and fail with a clear message if it does not work. In `tools/ka`, replace the `launcher` stub with the `sudo -u` call of Decision 1, and append the deployer used to `/srv/kolla-ansible/ka.log` for both modes. Write `tools/check-deployer-launcher` per Decision 9, and replace the body of the "Assert the containerised deployer prototype was selected" step in `.github/workflows/functional-tests.yml` with an ssh that runs it, in the same style as the neighbouring steps. Everything must be shellcheck-clean, and the venv path must be unchanged when the marker is absent or reads `venv`. Commit subject: "Run the prototype CI entry through the launcher." |
| 2d | medium | sonnet | none | Prove the launcher against the local `kolla/kolla-ansible:local` image before anything is pushed. Install it into a scratch venv, and run the exit script below as a non-root user. If `-p` does not replace `site.yml` for `deploy`, use whichever subcommand runs a given playbook, and say so. Report its full output. Commit nothing unless something fails and is fixed, in which case the fix goes back to the step that owns it. |
| 2e | medium | sonnet | none | Added after #1812's first CI run (see Risks). Write `_patches/patch200-kolla-ansible-master-validate-config-become.patch`, listed in `kolla-ansible/ORDER`. It adds a `service_config_validate_output_become` role default, `true`, used as `become` on the role's two `localhost` output tasks, with a release note. In launcher mode, bootstrap sets it to `false` in `globals.yml` and points `service_config_validate_output_dir` at `/etc/kolla/config-validate`, which is mounted and owned by the launcher's user. Prove it with `_build/test-apply.sh --test-patch patch200 kolla-ansible`. Commit subject: "Let validate-config run without controller sudo." |
| 2f | low | sonnet | none | Added after #1812's second CI run, which deployed fully through the launcher and then failed in upstream's `tests/check-config.sh`. That script requires every file under `/etc/kolla` to be `root:root` with mode 600, 660 or 770, except a list of deploy-host files (`globals.yml`, `passwords.yml`, `kolla-build.conf` and so on), and `deployer.conf` is a new deploy-host file. Write `_patches/patch201-kolla-ansible-master-check-config-deployer-conf.patch`, listed in `kolla-ansible/ORDER`, adding it to that list. Upstream would need the same line if it adopted the launcher. Commit subject: "Ignore deployer.conf in check-config.sh." |

Order: 2a, then 2b and 2c, which touch disjoint files, then 2d,
then push. 2e and 2f followed CI runs. The management session
reviews each step before its commit.

Exit:
* The script below passes locally.
* `pre-commit run --all-files` runs the launcher's tests and
  flake8, and passes.
* On the phase's pull request, the prototype entry is green, and
  `tools/check-deployer-launcher` has passed in it.
* Every other entry is green, or fails in the same way on a
  `develop` run without these changes.

```bash
#!/bin/bash -e
# Phase 2 exit check. Run as a non-root user in the docker group, with
# the launcher's venv on PATH and no kolla-ansible distribution in it.
img=${1:?usage: $0 <image>}
cfg=$(mktemp -d)
cd "${cfg}"
export KOLLA_CONFIG_PATH=${cfg}
printf '[deployer]\nimage = %s\n' "${img}" > deployer.conf
echo '{}' > globals.yml  # Ansible rejects an empty vars file

kolla-ansible --version
kolla-ansible launcher show | grep -q kolla_ansible_launcher_protocol

# The password tools, with a path argument the launcher must mount.
docker run --rm --entrypoint cat "${img}" \
    /var/lib/kolla/venv/share/kolla-ansible/etc_examples/kolla/passwords.yml \
    > passwords.yml
kolla-genpwd -p passwords.yml
grep -q '^keystone_admin_password: .' passwords.yml

# A relative inventory and playbook, and a write into the config
# directory from the container, which must come back owned by us.
printf '[all]\nlocalhost ansible_connection=local\n' > inv
cat > play.yml <<'PLAY'
- hosts: localhost
  gather_facts: true
  tasks:
    - copy:
        content: "{{ ansible_facts.user_id }}\n"
        dest: "{{ CONFIG_DIR }}/whoami"  # group_vars do not load for -p
PLAY
kolla-ansible deploy -i inv -p play.yml > /dev/null
test "$(stat -c %U whoami)" = "$(id -un)"
test "$(cat whoami)" = "$(id -un)"

if kolla-ansible install-deps > /dev/null 2>&1; then
    echo 'install-deps was not refused'
    exit 1
fi
echo "Phase 2 exit check passed."
```

### Phase 3: The localhost connection

Planning effort: high. It is the first phase where the deployer and
its target share a kernel. Decisions 5 and 6 at the top of this plan
either work here or the design changes.

Run the prototype CI entries' tasks on their own host through
`community.docker.nsenter`, as the unprivileged base user, using the
file capabilities on the image's `nsenter`. Add a Rocky 10 launcher
entry first, so that the new connection runs on two host operating
systems from its first CI run.

**In scope:**
* the Rocky 10 launcher entry;
* the nsenter inventory, replacing Phase 2's SSH stopgap;
* the launcher change that nsenter's identity model needs
  (Decision 4);
* a local probe of the connection before anything is pushed; and
* stronger CI checks, and the documentation of what was found.

**Out of scope:**
* Bootstrapping the deploy host from the container, which is Phase 4.
* A Kolla-Ansible patch adding an upstream all-in-one inventory for
  the container. See Decision 5.
* Fixing `community.docker.nsenter` upstream. Phase 8 proposes that
  (Decision 3).
* Podman. No CI host runs it (see the survey).
* The multinode inventories' `[deployment] localhost`. No launcher
  entry is multinode.

#### What the survey found

Surveyed on 2026-10-06 against this repository at `304ae30ba`,
kolla-ansible `f9148f101`, community.docker 5.4.0, ansible-core
2.21.5, and occystrap 0.4.17. The section this replaces was written
before Phases 0 to 2 ran. Its false claims are corrected in this
section and, where they appear elsewhere, at their source in this
commit: Phase 2's Decision 2 and its out-of-scope list.

*The prerequisite is met.*
* shakenfist/occystrap#151 is fixed by occystrap#156, which is in the
  `v0.4.17` tag. 0.4.17 is the latest release on PyPI.
* `_build/build-containers.sh:128` and `:251` install occystrap with
  no version pin, so the next image build uses 0.4.17.
* 0.4.17 also adds a rewrite version to occystrap's layer-cache key.
  Without it, a cached layer rewritten by an older release would
  still be served with its capability stripped.
* Whether the capability survives our push has not been checked on a
  pushed image. This host has no credentials for the CI registry.
  Step 3d's CI check is therefore the first real test.

*The section's own claims.*
* `master-h-rocky10-c-debian-13-aio` already exists. It is the venv
  entry, and it becomes the new entry's control, so the new entry is
  `master-h-rocky10-c-debian-13-aio-launcher`.
* Rocky CI hosts run Docker, not podman. `etc/globals-master.yml:92`
  leaves `kolla_container_engine` at its default, `docker`
  (`ansible/group_vars/all/common.yml:221`), so
  `bootstrap-servers` installs `docker-ce` on Rocky as it does on
  Debian. Phase 2 expected Rocky to be the first podman host. That
  claim is corrected.
* "85 uses of `localhost` across 40 files" is now 86 across 44 at
  `f9148f101`. The Situation section records the count measured on
  2026-09-29, and is left alone.
* Phase 2 did not delete a file in Phase 3's favour, as its
  Decision 2 claimed it would. `etc/inventory-all-in-one-launcher-master`
  stays, because the launcher entries still need an inventory whose
  connection differs from the venv entries'. Phase 3 rewrites it.

*Kolla-Ansible's `localhost` uses have already run in the container.*
* Every `delegate_to: localhost`, `hosts: localhost` and
  `connection: local` resolves to Ansible's implicit `localhost`.
  The CI inventories name the deploy host `kolla`, not `localhost`,
  so the implicit host is the deployer's own container.
* Phase 2's green run therefore executed all 86 uses inside the
  container. Only `validate-config`'s `become` broke (patch200).
  Changing the connection of the host `kolla` moves none of them.
* By module, the 86 tasks are:
  * 50 `stat` and 14 `find`, of operator overrides under
    `node_custom_config`;
  * 4 `command`;
  * 4 `file`, `copy` and `template`, two of them `validate-config`'s;
  * 2 in octavia's certificate expiry check;
  * the post-deploy, certificates and octavia-certificates plays,
    which are `hosts: localhost`.
* **The deploy host must not be named `localhost` in a container
  inventory.** keystone's fernet cron generator
  (`roles/keystone/tasks/config.yml:189-199`) runs
  `{{ ansible_playbook_python }} {{ role_path }}/files/...` with
  `delegate_to: localhost` and `connection: local`. Both paths exist
  only in the container. An inventory host named `localhost` replaces
  the implicit one, and a host variable `ansible_connection` beats
  the task keyword `connection: local`, so that task would run on the
  host and fail. Upstream's `ansible/inventory/all-in-one` names its
  host `localhost`. So a container all-in-one inventory cannot be a
  one-line change to it. That is a finding for Phase 8.
* Nothing in Kolla-Ansible uses `become_user`, `ansible_user`,
  `remote_user` or a `~` path on a target, apart from a commented
  example in `inventory/multinode`.

*How `community.docker.nsenter` actually behaves*
(`plugins/connection/nsenter.py` in 5.4.0).
* Each command becomes `nsenter --ipc --mount --net --pid --uts
  --preserve-credentials --target=1 -- <cmd>`, run through the
  container's `/bin/sh`. It does not enter a user namespace.
  * Commands therefore run on the host as the container process's
    own UID and GIDs. Under the launcher those are the invoking
    user's, not root's.
  * `nsenter`'s file capabilities are dropped when it `exec`s the
    command, because the host's shell has none. Only the namespace
    switch is privileged.
  * `become` then uses the host's `sudo`, with prompt handling. That
    is what Decision 6 wanted, and it is the opposite of Phase 2's
    finding on the container's own `localhost`.
* `put_file` pipes the file to `tee <path>` and `fetch_file` reads it
  with `cat`. There is no pipelining (`has_pipelining = False`). Our
  CI does not enable pipelining for SSH either, so the two are
  comparable.
* **The plugin sets `remote_user` to `root` unconditionally** in
  `_connect`, and documents that it "always runs as root". It has no
  `remote_user` option. ansible-core 2.21 makes its temporary
  directory under `remote_tmp`, default `~/.ansible/tmp`, and
  expands `~` as `~<remote_user>` (`plugins/action/__init__.py:530`
  and `:981`). As the unprivileged user, this was predicted to be
  `mkdir /root/.ansible/tmp`, failing for every module. **Step 3b
  showed the prediction was wrong in detail.** The failure is real,
  but `~` expanded through the container's `HOME`, so the path was
  under `/var/lib/kolla-ansible`, which the host lacks. See the
  Outcome.
* The process's supplementary groups are the container's, not the
  host user's. Docker gives a `--user uid:gid` process only the
  groups listed for it in the container's `/etc/group`, and the
  launcher's synthesised file lists none. That is read from Docker's
  documentation, not tested; step 3b's probe asserts the group list. On the host, `sudo`
  matches `%group` rules against the process's group list by
  default, so a deploy account allowed sudo through `%wheel` or
  `%sudo` would be refused. CI's base users have per-user `NOPASSWD`
  rules from cloud-init, which is why Phase 2's SSH path never
  showed this. SSH logins get the host's full group list.

*The local host can probe nsenter, though not `become`.* Docker here
sets no `no-new-privileges` and no user namespace remapping, so a
container started with `--privileged --pid host` can use file
capabilities. The local user has no passwordless sudo, so `become`
can only be tested in CI.

#### Decisions

1. **The deploy host keeps an inventory name that is not `localhost`,
   and only its connection changes.** The launcher inventory's
   `[all:vars]` becomes `ansible_connection=community.docker.nsenter`
   and an absolute `ansible_remote_tmp` (Decision 3). The SSH
   variables go: `ansible_host`, `ansible_user`, the key and the
   extra arguments. The 86 controller-side uses keep running in the
   container, as they did in Phase 2, and the only behaviour that
   moves is that of tasks on the host `kolla`. That keeps a failure
   attributable to the connection alone.

2. **Phase 2's stopgap goes entirely.** Bootstrap stops checking
   `ssh` to `127.0.0.1`, and `deployer.conf` stops mounting
   `/srv/github`, which only the SSH key needed. The launcher's
   `mounts` key stays, because the gap it covers is real. The
   `~/.ssh` mounts from Phase 2 Decision 3 also stay, because
   multinode needs them.

3. **`ansible_remote_tmp` is set to an absolute path, and the
   plugin's root assumption is recorded for upstream rather than
   patched.** The path is `/tmp/.ansible-kolla-<user>`, with
   `<user>` substituted by bootstrap as `USER` is today. Absolute
   paths skip `~` expansion, and `mkdtemp` still creates each task's
   directory with mode 0700 beneath it. The correct fix is in
   community.docker: the plugin should use the effective user, not
   root, when the controller is not root. Carrying a patch to a
   Galaxy collection would mean a new project directory, a new
   source, and a change to how Phase 1's image installs collections,
   all for one line of a plugin that documents root as its contract.
   Phase 8 raises it upstream with this plan as the evidence. **This
   is the decision most likely to be argued with.** It is a
   workaround in an inventory, which an upstream inventory would have
   to repeat until community.docker changes.

4. **The launcher gives the container the invoking user's
   supplementary groups**, with one `--group-add <gid>` for each GID
   in `os.getgroups()` other than the primary one. Through nsenter,
   the container's identity *is* the identity tasks run with on the
   host, so it should match what an SSH login would get. That makes
   `%group` sudo rules work. It also needs no change to the
   synthesised `/etc/group`, because `--group-add` takes numbers.
   This applies to docker and to rootful podman. Rootless podman with
   `--userns=keep-id` already keeps the user's groups.

5. **No upstream inventory patch in this phase.** "How this work is
   carried" lists an nsenter inventory as a Kolla-Ansible patch, and
   the old Phase 3 section said upstream would ship one beside the
   existing all-in-one. The survey shows that file cannot simply be
   upstream's all-in-one with a different connection, because the
   host has to be renamed. And no CI entry here would run it: our
   inventories under `etc/` carry Kerbside's groups and name the host
   `kolla`. A patch that nothing deploys is one that rots. Phase 8
   writes it, from the delta this phase proves: the host name rule
   and two `[all:vars]` lines.

6. **The Rocky entry lands first, still on SSH, and nsenter lands
   second.** Each is pushed separately. A failure in the first is
   about Rocky (`cloud-user`, `docker-ce` from Kolla, SELinux); a
   failure in the second is about the connection. Both entries are
   non-voting until Phase 7.

7. **The CI check proves the connection and the capability.**
   `tools/check-deployer-launcher` also checks that:
   * `/etc/kolla/inventory` sets `ansible_connection=community.docker.nsenter`
     and no `ansible_host`; and
   * `getcap /usr/bin/nsenter` in the pinned image reports
     `cap_sys_admin`, run as the base user, who is logged in to the
     registry. This is the first test of a capability that has been
     through our push.

   Where SELinux is enabled, it also prints `restorecon -n -v -R
   /etc/kolla` without failing, so any labels that tasks run from a
   container got wrong are visible.

#### Risks

* **The capability does not survive the push, even with 0.4.17.**
  Then every nsenter task fails with `Operation not permitted`.
  Step 3d's check names this cause rather than leaving it to be
  inferred from a failed deploy. If it fails, the fix is in
  occystrap, not here.
* **More of ansible-core assumes `remote_user` is the real user than
  the survey found.** Step 3b runs fact gathering, file transfer and
  modules locally, with and without the `remote_tmp` workaround.
  Whatever else it finds is added to the inventory or recorded here,
  before any push.
* **`HOME` on the host is the container's `/var/lib/kolla-ansible`.**
  The host does not have it. A task that writes under `$HOME`
  without `become` would fail. None was found, and sudo resets
  `HOME` for `become` tasks. Step 3b runs `env` on the host to
  record what tasks actually see.
* **`docker` on Rocky is not reachable by `cloud-user`.** Then
  bootstrap's `docker login` or `launcher pin` fails. Step 3c's CI
  run finds out while still on SSH. The fix belongs in bootstrap.
* **SELinux labels from a container process.** Kolla-Ansible's
  baremetal role sets SELinux to permissive by default, so CI will
  not fail on this. Decision 7's report makes it visible for the
  proposal.
* **Fallback.** If file capabilities cannot work at all, the
  recorded fallback stands: run the container as root and have the
  launcher restore ownership of what it wrote. That would put a root
  process with host namespaces behind every command, against top-level
  Decision 6, and it would be recorded here as the design failing,
  not as a detail.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Implement Decision 4 of Phase 3 in `docs/plans/containerised-deployer.md`. In `deployer/launcher/kolla_ansible_launcher/command.py`, add `--group-add <gid>` for each supplementary GID, sorted, excluding the primary GID. Take the GIDs as a parameter of the command builder, as the UID and GID already are. `cli.py` passes `os.getgroups()`. Do not add them for rootless podman with `--userns=keep-id`. Add unit tests for docker, rootful podman and rootless podman, and update `docs/deployer-launcher.md`'s list of engine flags. Standard library only; single quotes; 120 columns; `pre-commit run --all-files` must pass. Commit subject: "Give the deployer the user's supplementary groups." |
| 3b | medium | sonnet | none | Read-only on this host, apart from `/tmp` and a scratch directory. Build the deployer image locally as Phase 1 step 1e did: `_build/assemble-source.sh --no-tarball master`, then `mkdir archive` (`_build/imagebuild.sh` fails with `archive/kolla-build.conf: No such file or directory` without it), then `_build/imagebuild.sh` for `^(base\|openstack-base\|kolla-ansible)$` with tag `local`. Install `deployer/launcher` (with 3a) into a scratch venv, and run the probe below as the current user, reporting its full output. Then run it once more with the `ansible_remote_tmp` line removed from the inventory, and report whether it fails with the `/root/.ansible/tmp` error the survey predicts. Do not use `sudo`. `become` is not tested here. Commit nothing; the results go into this plan in step 3e. |
| 3c | low | sonnet | none | In `.github/workflows/functional-tests.yml`, copy `master-h-rocky10-c-debian-13-aio` to a new entry after it, named `master-h-rocky10-c-debian-13-aio-launcher` and described `master debian 13 images on rocky 10 all-in-one with the containerised deployer`, with `'deployer': 'launcher'` and `'non_voting': 'true'`, following `master-h-debian13-c-debian13-aio-launcher`. Change nothing else. This push still uses Phase 2's SSH inventory. Commit subject: "Add a Rocky 10 entry for the deployer prototype." |
| 3d | medium | sonnet | none | Implement Decisions 1, 2, 3 and 7 of Phase 3. Rewrite `etc/inventory-all-in-one-launcher-master`'s header and `[all:vars]` to `ansible_connection=community.docker.nsenter` and `ansible_remote_tmp=/tmp/.ansible-kolla-USER`. In `tools/bootstrap-kolla-ansible`, remove the ssh check to `127.0.0.1` and the `mounts = /srv/github` line, keep the `USER` substitution, and update the comments that mention SSH. Extend `tools/check-deployer-launcher` with the inventory, `getcap` and SELinux checks of Decision 7. The `getcap` check runs `docker run --rm --entrypoint getcap <image from deployer.conf> /usr/bin/nsenter`, and on failure says that the capability was lost, naming occystrap#151. Shellcheck-clean, and the venv entries must be unchanged. Commit subject: "Reach the deploy host through nsenter." |
| 3e | low | sonnet | none | After 3d's CI run, record in Phase 3 of `docs/plans/containerised-deployer.md` what 3b and the CI runs found: the probe output summary, deploy durations of each launcher entry against its control, the `restorecon` report, and anything that behaved differently. Update `docs/deployer-launcher.md` with what nsenter requires of the image, the engine and the inventory (a host not named `localhost`, an absolute `ansible_remote_tmp`), and add a line to Phase 8 listing the community.docker change and the upstream inventory to be proposed. Commit subject: "Record what the nsenter connection needed." |

Order: 3a, then 3b. Then push with 3c, and push 3d once that run
is in. 3e follows 3d's CI run. The management session reviews each
step before its commit.

Exit (status as of the CI run on `2b73d58d7`, run 37805828411):
* Met. The probe below passes locally, and its run without
  `ansible_remote_tmp` failed, though not with the predicted path.
  The Outcome records why.
* Met. On the phase's pull request, both launcher entries are green
  with the nsenter inventory, and `tools/check-deployer-launcher`
  passed in each, including its `getcap` check, at `2b73d58d7`.
  Rocky needed three fixes first (see the Outcome).
* Met. Every other entry in that run is green, so none needed a
  comparison with `develop`.
* Met. `grep -rn 'id_ci\|127.0.0.1' etc/inventory-all-in-one-launcher-master`
  finds nothing.
* Met. Step 3e's records landed in `7111be9d7`, and #1831 merged as
  `f2dd038ab`.

```bash
#!/bin/bash -e
# Phase 3 probe. Run as a non-root user in the docker group, with the
# launcher's venv on PATH. It runs tasks on THIS host through nsenter,
# writing only under /tmp.
img=${1:?usage: $0 <image>}
cfg=$(mktemp -d)
cd "${cfg}"
export KOLLA_CONFIG_PATH=${cfg}
printf '[deployer]\nimage = %s\n' "${img}" > deployer.conf
echo '{}' > globals.yml
echo '{}' > passwords.yml
out=/tmp/phase3-probe-$$

cat > inv <<INV
[all]
probe ansible_connection=community.docker.nsenter ansible_remote_tmp=/tmp/.ansible-kolla-$(id -un)
INV
cat > play.yml <<PLAY
- hosts: probe
  gather_facts: true
  tasks:
    - ansible.builtin.assert:
        that:
          - ansible_facts.hostname == '$(hostname -s)'
          - ansible_facts.user_id == '$(id -un)'
    - ansible.builtin.command: id -G
      register: id_groups
    - ansible.builtin.assert:
        that: id_groups.stdout.split() | sort == '$(id -G)'.split() | sort
    - ansible.builtin.command: env
      register: host_env
    - ansible.builtin.debug:
        var: host_env.stdout_lines
    - ansible.builtin.copy:
        content: "probe\n"
        dest: ${out}
    - ansible.builtin.template:
        src: ${cfg}/globals.yml
        dest: ${out}.template
    - ansible.builtin.fetch:
        src: ${out}
        dest: ${cfg}/fetched/
        flat: true
PLAY
kolla-ansible deploy -i inv -p play.yml
test "$(cat "${out}")" = probe          # written on the host
test "$(stat -c %U "${out}")" = "$(id -un)"
test -s fetched/"$(basename "${out}")"  # fetched back into the container
rm -f "${out}" "${out}.template"
echo "Phase 3 probe passed."
```

#### Outcome

Written after the CI run on `2b73d58d7` (run 37805828411, all jobs
green).

*The local probe (step 3b).*
* The probe as written failed first, because `kolla-ansible deploy`
  wants a `passwords.yml`. The script above now creates an empty one.
* With that fixed it passed: fact gathering, `command`, `copy`,
  `template` and `fetch` all worked through nsenter as the
  unprivileged user, files on the host were owned by that user, and
  the group list matched `id -G` (Decision 4 works).
* Without `ansible_remote_tmp` it failed, as predicted in kind but
  not in path. The survey expected `/root/.ansible/tmp`. In fact `~`
  was expanded through the container's `HOME`,
  `/var/lib/kolla-ansible`, giving a path the host does not have.
* **`community.docker.nsenter` passes the caller's whole environment
  to commands on the host**: `PATH`, `HOME` and the `KOLLA_*`
  variables all arrive as the container had them. Two consequences:
  * `ansible_remote_tmp` must be absolute, because `~` resolves
    against the wrong `HOME`.
  * Python interpreter discovery on the host depends on the
    caller's `PATH`, not on the host's. This is what broke Rocky.

*The Rocky 10 launcher failure and its fix.* The Debian launcher
entry passed first time. The Rocky one needed four rounds of fixes:
1. **Prechecks failed at "Checking docker SDK version"**, with no
   `docker` module in `/usr/bin/python3.12`. The cause is a chain.
   Bootstrap runs the host venv's `kolla-ansible`. nsenter leaked the
   venv's `PATH` to the host, so interpreter discovery found the
   venv's `python3.12`. The `openstack.kolla` `docker_sdk` role
   derives its `pip` from `ansible_facts.python.executable`, so it
   installed the SDK into the venv. The launcher's deploy later
   discovered `/usr/bin/python3.12`, which lacked it. Debian passes
   because the SDK there comes from the `python3-docker` package.
   Commit `70b6c25c7` moved bootstrap onto the nsenter inventory as
   part of this; that stays.
2. **Pinning `ansible_python_interpreter` in the inventory
   (`e0f81102a`) did not work.** kolla-ansible's `bootstrap-servers`
   passes `ansible_python_interpreter=auto_silent` as an extra var
   (`kolla_ansible/cli/commands.py`, about line 181), and
   `ansible/group_vars/baremetal/ansible-python-interpreter.yml`
   overrides inventory variables for the deploy. kolla-ansible only
   drops its override when the caller passes its own
   `-e ansible_python_interpreter=` (`commands.py`, about lines
   69-74).
3. **Passing that `-e` to both venv calls (`5c9e9da96`) broke
   `kolla-ansible certificates`.** It runs on the implicit
   `localhost` and needs the venv's `cryptography`, which the pin
   pointed away from.
4. **The fix is `2b73d58d7`.** `tools/bootstrap-kolla-ansible` passes
   `-e ansible_python_interpreter=/usr/bin/python3` to
   `bootstrap-servers` alone, and the inventory pin was removed.

One unrelated failure: a Rocky mirror (`ftp.swin.edu.au`) was
mid-sync and returned 404 for one image build. It was unrelated to this work.

*Debian evidence, from `check-deployer-launcher`.* The inventory
check reported the nsenter connection. `getcap /usr/bin/nsenter` in
the pushed image reported `cap_sys_chroot,cap_sys_ptrace,cap_sys_admin=ep`,
so the occystrap 0.4.17 fix holds through our push, which was the
risk this phase carried. `admin-openrc.sh` and `clouds.yaml` were
owned by the `debian` user. The Rocky run's checks were identical,
with `cloud-user` as owner.

*Deploy durations*, from the "Deploy Kolla-Ansible" step of each job
(`started_at` to `completed_at`):

| Host | Launcher entry | Control entry | Launcher | Control |
|------|----------------|---------------|----------|---------|
| debian 13 | `master-h-debian13-c-debian13-aio-launcher` (job 113445099316) | `master-h-debian13-c-debian13-aio` (job 113445099387) | 27m17s | 33m20s |
| rocky 10 | `master-h-rocky10-c-debian-13-aio-launcher` (job 113445099000) | `master-h-rocky10-c-debian-13-aio` (job 113445099561) | 28m55s | 40m59s |

The controls are the same host and container distributions with the
venv deployer. These numbers do **not** show the launcher to be
faster, and should not be read that way. Each pair ran on different
runners at different times (the launcher jobs started at about
17:40Z and the controls at about 18:45Z), and one other Debian
all-in-one job, with no Kerbside, took 28m05s, so the spread between
runs of the same shape is of the same order as the differences
above. What they do show is that the launcher and nsenter add no
cost large enough to stand out from that noise. A paired comparison
would need several runs of each.

*The `restorecon` report.* On the Rocky launcher job, Decision 7's
`restorecon -n -v -R /etc/kolla` printed `no relabels needed`. So
files written to `/etc/kolla` from the container carry correct
labels, at least with Kolla-Ansible's baremetal role having set
SELinux to permissive (the "Change state of selinux" task ran and
reported `changed`). That is weaker than it sounds: permissive mode
would not have failed the deploy in either case, but `restorecon -n`
reports mislabelled files regardless of mode, and found none. The
Debian job reported `SELinux is not enabled`, as expected.

### Phase 4: Bootstrapping the deploy host

Planning effort: high. This is the phase most likely to reshape
the design.

Resolve Open question 2 with shape (d). Both launcher entries start
from a stock cloud image with no container engine. The launcher
installs the distribution's engine, pulls and pins the deployer, and
then runs `bootstrap-servers` from the image's root filesystem,
unpacked to a directory and entered with `chroot` inside a private
mount namespace. The deployer is then not a process of any engine, so
bootstrap can replace the engine underneath it. After this phase the
launcher entries have no Kolla-Ansible virtualenv on the target at
all.

**In scope:**
* a `launcher install-engine` subcommand;
* running `bootstrap-servers` from an unpacked image, as root;
* bootstrap in launcher mode with no venv, and `genpwd` and
  `certificates` through the launcher;
* a home for the OpenStack clients that is not the bootstrap venv, on
  the target and on the CI runner;
* a local probe of the unpack and the chroot before anything is
  pushed; and
* CI checks that prove the venv is gone and record what happened to
  the engine.

**Out of scope:**
* Shapes (a), (b) and (c), unless (d) fails. Then this section is
  rewritten before any of them is started.
* A multinode launcher entry. Decision 6 covers the deploy host that
  is not a target by design, but no CI entry proves it.
* Podman as the deploy engine. On Rocky the launcher installs podman
  only to unpack the image; `kolla_container_engine` stays `docker`,
  as it was in Phase 3.
* Removing the venv path. The venv entries are the controls, and are
  unchanged apart from where the OpenStack clients live.

#### What the survey found

Surveyed on 2026-10-09 against this repository at `f2dd038ab`, and
ansible-collection-kolla `9fc1185` and kolla-ansible `6f2df72`, the
pinned `source_sha`s. The section this
replaces was written before Phases 0 to 3 ran. Its false claims are
corrected here and nowhere else needed it: the `index.md` row is one
line for the whole plan.

*The starting state.*
* The CI targets are stock `debian:13` and `rocky:10` cloud images
  (`setup-kerbside-environment` in shakenfist/actions, `base:` in the
  `test_installs` matrix). **This was wrong, and the first CI run
  did not catch it.** The setup action ran
  `_build/install-build-dependencies.sh` on the target, which
  installed `docker-ce` before `tools/bootstrap-kolla-ansible`, so
  neither host was a "no engine" host and `install-engine` was a
  no-op. #1857 fixed it with a `--no-docker` option on that script,
  an `install_docker` input to `setup-kerbside-environment`
  (shakenfist/actions#150) and bootstrap adding the base user to the
  `docker` group afterwards. The Outcome records the clean-host run.
  The old section named only Debian 13.
  Phase 3 added Rocky 10, and this phase covers both.
* In launcher mode, bootstrap still builds the full venv
  (`tools/bootstrap-kolla-ansible:40-90`) and runs four things from
  it: `install-deps`, `kolla-genpwd`, `certificates` and
  `bootstrap-servers`. The launcher already provides `kolla-genpwd`
  (Phase 2 Decision 8), refuses `install-deps` as unnecessary, and
  can run `certificates` like any other subcommand. Only
  `bootstrap-servers` needs this phase.

*What bootstrap does to each distribution's engine.* Checked in
throwaway containers on 2026-10-09, by installing the distribution's
engine and then simulating Kolla's `docker-ce` install.
* **Debian 13: `docker-ce` replaces the distribution's engine.**
  `apt-get install docker-ce` from Docker's repository removes
  `docker.io`, `containerd`, `docker-cli` and `runc`. Any container
  the distribution's engine is running dies with it, so the deployer
  cannot be one of them. This is the case shape (d) exists for.
* Debian 13 split the client out of `docker.io` into `docker-cli`
  (see `ansible/ci-image.yml` in shakenfist/actions), so installing
  the engine means both packages.
* **Rocky 10: the two coexist.** There is no Docker in Rocky's
  repositories. `podman` (5.8, with `crun`, not `runc`) installs from
  AppStream, and `docker-ce` then installs beside it with nothing
  removed. A deployer running in podman would survive bootstrap
  here, but one code path for both is worth more than the shortcut.
  Podman and Docker keep separate image stores, so the image is
  pulled twice on Rocky: once by podman, as root, to unpack, and once
  by Docker for the deploy.
* The collection's `docker` role (`roles/docker/tasks/install.yml`)
  masks `docker.service` on Debian when there is no unit file under
  `/etc/systemd/system`, which is true of `docker.io`'s. It then
  installs `docker-ce`, and `config.yml` unmasks and starts it. It
  snapshots running containers and restarts them after an upgrade,
  which does nothing for a container whose engine package has been
  removed.

*What the chroot inherits.*
* `ansible/kolla-host.yml`, the playbook `bootstrap-servers` runs, is
  one play on `hosts: baremetal`. Nothing in it runs on `localhost`.
* The deployer image's `PATH` starts with `/var/lib/kolla/venv/bin`,
  which the host does not have. Run from the chroot with the image's
  environment, the host's interpreter discovery therefore finds
  `/usr/bin/python3`, as the launcher's deploy does. **That removes
  the cause of Phase 3's Rocky failure**, so the
  `-e ansible_python_interpreter` workaround in
  `tools/bootstrap-kolla-ansible` goes. The `docker_sdk` role's `pip`
  then installs into the Python the deploy will discover.
* As root, nsenter's `remote_user` assumption is true for once. The
  inventory's `ansible_remote_tmp` still names the base user's
  directory, so bootstrap keeps passing
  `-e ansible_remote_tmp=/tmp/.ansible-kolla-root`.
* Phase 1 measured the image at 1.68 GB. That is roughly what an
  unpack writes.
* Python's `os.unshare` is 3.12 and later, and the launcher supports
  3.9. util-linux's `unshare` binary is on both distributions, and
  `os.chroot` is in every supported version.

*The OpenStack clients.*
* `tools/install-openstack-clients` activates the bootstrap venv,
  installs `openstacksdk` and `python-openstackclient<10.2` into it,
  and links their scripts into `/usr/local/bin`.
* `tools/postinstall-kolla-ansible` activates the same venv, for
  `init-runonce`'s `openstack` calls.
* The CI runner makes its own venv at the same path,
  `/srv/kolla-ansible/venv` (`functional-tests.yml:882-898`), only so
  that the same script works there. The old section's line numbers
  (874-890) have moved.

#### Decisions

1. **Shape (d), with the image unpacked by the engine.** The launcher
   runs `<engine> create`, then `<engine> export` piped into `tar -xp
   --numeric-owner`, into a fresh directory under
   `/var/lib/kolla-ansible-launcher/`, and then `<engine> rm`. It
   unpacks before every `bootstrap-servers` run and deletes the
   directory afterwards. There is no cache. `bootstrap-servers` is
   rare, and a cache would need invalidating whenever the pin
   changes. The stdlib registry client of Open question 2 stays the
   fallback, and is not built unless `export` proves awkward.

2. **The chroot is built by a helper, inside `unshare`.** The launcher
   runs `unshare --mount --propagation private --` on
   `sys.executable -m kolla_ansible_launcher.chroot`. That helper
   bind-mounts `/proc`, `/dev` and `/sys` (recursively), the host's
   `/etc/resolv.conf` and `/etc/hosts`, and every same-path mount the
   container path would make (`command.collect_mounts`, reused
   unchanged). It then calls `os.chroot`, sets the image's environment
   from `<engine> image inspect` `.Config.Env`, with `HOME=/root`,
   and execs the image's `kolla-ansible` with the original arguments.
   The launcher waits for it rather than exec'ing, so that it can
   delete the directory, and returns its exit status. The mounts live
   only in the helper's namespace, so they disappear with it.

3. **Deleting the unpacked tree must not be able to cross a mount.**
   That is the one way this phase could destroy an operator's
   configuration: an `rmtree` through a bind mount of `/etc/kolla`.
   The mounts are made in a namespace the deletion never runs in,
   which is the real protection. The deletion also refuses to descend
   into any directory whose `st_dev` differs from the top's, and
   unit tests prove that refusal with a fake tree. This is a
   belt-and-braces rule, and it is cheap.

4. **`bootstrap-servers` through the launcher requires root.** The
   launcher refuses it unless the effective UID is 0, saying to run
   it with `sudo`. Unpacking with ownership, mounting and `chroot` all
   need root, and bootstrap escalates on the host anyway. **This is
   the decision most likely to be argued with.** Today an operator
   runs `bootstrap-servers` as their deploy user and Ansible's
   `become` escalates per task. Here the whole command runs as root
   on the deploy host. The alternative, re-exec'ing through `sudo`
   from inside the launcher, hides the escalation from the operator,
   which is worse. The upstream proposal states the change plainly.

5. **`launcher install-engine` installs the distribution's engine if
   there is none.** If `docker` or `podman` is already on `PATH`, it
   says which and does nothing. Otherwise it reads `/etc/os-release`:
   on the Debian family it installs `docker.io`, plus `docker-cli`
   where `apt-cache show docker-cli` finds it; on the Red Hat family
   it installs `podman`. Anything else is refused with a message
   naming both engines. It needs root, and it never touches an
   existing engine, because replacing engines is bootstrap's job.

6. **There is no special case for a deploy host that is not a
   target.** `bootstrap-servers` always runs from the chroot. When the
   deploy host is not in `baremetal`, nothing removes its engine, and
   the chroot reaches the targets over SSH. Root's `~/.ssh` and
   `SSH_AUTH_SOCK` are then what reach them, which the existing mount
   rules already handle. No CI entry proves this, and the plan says
   so rather than claiming it.

7. **In launcher mode, bootstrap builds no Kolla-Ansible venv.** As
   root, `tools/bootstrap-kolla-ansible` installs the launcher, runs
   `launcher install-engine`, logs root in to the registry with the
   engine it now has, pins the image, and runs `kolla-genpwd`,
   `certificates` and `bootstrap-servers` through the launcher, in
   the order the venv path runs them. `genpwd` and `certificates`
   run in the container, on the engine that `install-engine`
   installed. Only `bootstrap-servers` uses the chroot. The existing
   `chown` and the base user's `docker login` then follow, as in
   Phase 3. The venv path is unchanged.

8. **The OpenStack clients get their own venv,
   `/srv/openstack-clients/venv`, in both modes.**
   `tools/install-openstack-clients` creates it if it is missing and
   links from it, and `tools/postinstall-kolla-ansible` stops
   activating the bootstrap venv, because the links in
   `/usr/local/bin` are on `PATH`. The runner step in
   `functional-tests.yml` makes the new path instead of the old one.
   The venv entries change only in this, which is the price of one
   code path.

9. **The CI check proves the venv is gone and records the engine.**
   `tools/check-deployer-launcher` fails if `/srv/kolla-ansible/venv`
   exists. It reports, without failing, the engine packages
   (`dpkg -l` or `rpm -q` for `docker.io`, `docker-ce`,
   `containerd.io` and `podman`), `docker info`'s server version,
   `/etc/docker/daemon.json`, and the running containers. The
   launcher prints how long the unpack took and how much it wrote
   (`du -sx`), so the job log carries the numbers this phase has to
   record.

#### Risks

* **The bind mounts outlive the helper**, for example if a process
  started in the chroot daemonises and keeps the namespace alive.
  This does not block deletion, as first written. Since Linux 3.18,
  unlinking a directory that is a mount point only in another mount
  namespace is allowed, and lazily detaches that mount. Deletion
  therefore succeeds, and the surviving process keeps its open
  inodes. The mountinfo check and Decision 3's `st_dev` rule are
  backstops against a propagation bug that let a mount leak into the
  launcher's own namespace, where `rmtree` could reach it; the
  launcher warns and leaves the directory in place if either
  trips. Step 4b checks that nothing is left mounted or on disk
  after a run.
* **On Rocky the unpacked tree carries the wrong SELinux labels.**
  `tar` creates files under `/var/lib/kolla-ansible-launcher`, so
  they take that directory's label (`var_lib_t`), not the image's.
  The chroot then runs `bootstrap-servers` as root through `sudo`,
  which is `unconfined_t` and expected to be allowed to execute and
  read them. If enforcing SELinux denies it, the failure would show
  as `Permission denied` from inside the chroot with an AVC in the
  audit log. The Rocky CI run shows what happened (see the Outcome).
* **`docker.io`'s removal on Debian takes the half-installed state of
  `docker-ce` with it**, for example if the masked unit stops
  `docker-ce`'s postinst from starting the daemon and something then
  depends on it. The role's own "Start and enable docker" task is
  meant to cover this. The clean-host Debian run showed it did (see
  the Outcome).
* **Root's registry login is a different file from the base user's.**
  On Rocky, rootful podman keeps its credentials in
  `/run/containers/0/auth.json`, and Docker's are per user. Bootstrap
  logs in each identity with its own engine, and the launcher pulls
  any image the current engine lacks, which it already does.
* **`docker-ce` 29 might not see `docker.io`'s images.** Then the
  launcher pulls again on its first run as the base user. That costs
  one pull, and is not a failure. On Debian the image was seen; on
  Rocky the two engines pull separately by design.
* **The interpreter goes wrong again.** If the chroot's `PATH` did not
  reach the host as expected, prechecks would fail at "Checking docker
  SDK version" as in Phase 3. Removing the workaround is deliberate,
  because the finding is that it is no longer needed. If CI shows
  otherwise, it comes back with a note saying why.
* **Shape (d) fails outright.** Then fall back to (a) or (b), as Open
  question 2 says, and rewrite this section first. Rocky's
  coexistence means a (b)-like fallback could start there.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Implement Decisions 1-5 of Phase 4 in `docs/plans/containerised-deployer.md`, in `deployer/launcher/`. Read Phases 2 and 3 of that plan and the existing package first: `cli.py` dispatches subcommands and the `launcher` word, `engine.py` wraps engine calls with an injectable runner, and `command.collect_mounts` computes the same-path mounts. Add `launcher install-engine` (Decision 5; `/etc/os-release` read with the standard library, `ID` and `ID_LIKE`; apt with `DEBIAN_FRONTEND=noninteractive`). Add a module for the unpack and the run (Decisions 1-3): `create`, `export` piped into `tar -xp --numeric-owner -C <dir>`, `rm`, and an image environment read from `.Config.Env`, then `unshare --mount --propagation private -- <sys.executable> -m kolla_ansible_launcher.chroot ...`. The helper does the bind mounts with the `mount` binary, `os.chroot`, sets the environment with `HOME=/root`, and execs `/var/lib/kolla/venv/bin/kolla-ansible` with the original arguments. Route the `bootstrap-servers` subcommand there, refusing it unless the effective UID is 0 (Decision 4). Print the unpack duration and `du -sx` size on stderr. The deletion must refuse to cross `st_dev` and must leave the directory with a warning if a mount is still present. Standard library only, Python 3.9, single quotes, 120 columns. Separate planning from execution as the package already does, and add `unittest` tests: the `install-engine` choices for Debian 13, Debian 12 (no `docker-cli`), Rocky 10, an existing engine and an unknown distribution; the full command lines for docker and podman; the helper's mount list for a realistic argument list; the root refusal; and the `st_dev` refusal on a fake tree. Update `docs/deployer-launcher.md`: the new subcommand, how `bootstrap-servers` runs and why it needs root, and remove the advice that the interpreter must be pinned for bootstrap. `pre-commit run --all-files` must pass. Commit subject: "Run bootstrap-servers from an unpacked deployer." |
| 4b | medium | sonnet | none | Read-only on this host apart from the scratchpad, and no `sudo`. Build the deployer image locally as Phase 3 step 3b did. Install `deployer/launcher` (with 4a) into a scratch venv. Inside `unshare --map-root-user --mount`, which gives a fake root, run the launcher's unpack and chroot path with a harmless command: `kolla-ansible bootstrap-servers --help`, and, through the same helper, `ansible --version` and a `cat` of a file in a same-path mount. Report the unpack time and size, whether the mounts were visible at the same paths in the chroot, and whether, afterwards, the directory is gone and `findmnt` shows nothing left behind. nsenter cannot work under a fake root, so nothing reaches the host. If the user namespace blocks a step (for example access to the Docker socket), report exactly which, and prove the rest separately. Commit nothing unless a fix is needed, in which case the fix goes back to 4a. |
| 4c | medium | sonnet | none | Implement Decisions 7-9 of Phase 4. In `tools/bootstrap-kolla-ansible`, launcher mode: no `/srv/kolla-ansible/venv` and nothing from it. Install the launcher venv first, run `launcher install-engine`, log root in with whichever of `docker` and `podman` is now on `PATH`, write `deployer.conf` and pin as root, then run `kolla-genpwd`, `certificates` and `bootstrap-servers` through the launcher with `-e ansible_remote_tmp=/tmp/.ansible-kolla-root`. Remove `bootstrap_args` and its comment, since the chroot makes the interpreter pin unnecessary (see the survey). Then `chown`, the base user's `docker login`, and a re-pin as the base user, so that Docker on Rocky has the image. Keep the marker file and everything the venv path does unchanged. Move the OpenStack clients to `/srv/openstack-clients/venv` in `tools/install-openstack-clients` (created if missing), drop the venv activation from `tools/postinstall-kolla-ansible`, and change the runner's venv path in `.github/workflows/functional-tests.yml` (about lines 882-884). Extend `tools/check-deployer-launcher` per Decision 9. Shellcheck-clean, no large inline scripts in the workflow. Commit subject: "Bootstrap the launcher entries without a venv." |
| 4d | low | sonnet | none | After 4c's CI run, add an Outcome to Phase 4 of `docs/plans/containerised-deployer.md`: whether bootstrap completed on each host; the engine's end state on each (packages, `daemon.json`, running containers) from `check-deployer-launcher`; the unpack duration and size from the launcher's output; deploy durations against the controls, labelled as noise as in Phase 3; what the chroot needed by hand; and anything that behaved differently from this section. Annotate the exit criteria. Commit subject: "Record what bootstrapping from a chroot found." |

Order: 4a, then the back brief below, then 4b. Push 4a with 4c once
4b passes. 4d follows that CI run. The management session reviews
each step before its commit.

**Back brief gate on 4a.** Before writing code, 4a's sub-agent
reports the module layout, the helper's argument protocol, and the
exact `unshare` and `mount` invocations. The management session
agrees them first. A chroot helper is cheap to describe and expensive
to rework once the tests are written around it.

Exit (status as of the clean-host CI run 37996529795 on `9a28da1a4`,
attempt 1):
* Met. Both launcher entries are green, and
  `tools/check-deployer-launcher` passed in each with no
  `/srv/kolla-ansible/venv` on the target ("absent" in both logs).
  Both ran with `Will not install Docker.` from the setup action, so
  the targets really were bare.
* Met. The Debian check shows `docker-ce` 5:29.9.0 and
  `containerd.io` 2.4.1 installed and `docker.io` in state `rc`
  (removed, configuration remains), so `docker.io` was installed and
  then replaced. The Rocky check shows `docker-ce`, `containerd.io`
  and `podman` 5.8.2 together. Both logs show the launcher installing
  the engine (`installing docker.io with apt-get`, `installing
  podman with dnf`) and unpacking the image (`with docker`, `with
  podman`).
* Partly met. The venv entries are green in the same run. Multinode
  deployed and passed its tests but the job failed in "Collect logs",
  and the Ubuntu all-in-one deployed with `failed=0` but failed in
  `tools/install-openstack-clients`. Neither touches Phase 4 (see the
  Outcome). Multinode passed when re-run (attempt 2), and every entry,
  Ubuntu included, was green on #1857's final head `26fad76ae`.
* Met. 4b's probe passed with nothing left mounted or on disk, except
  that ownership preservation was not exercised locally. The clean-host
  jobs unpack as real root and bootstrap completed.
* Met. `grep -n 'ansible_python_interpreter' tools/bootstrap-kolla-ansible`
  finds nothing (exit status 1), and the prechecks' "Checking docker
  SDK version" passed on both hosts.

#### Outcome

Written after the clean-host CI run 37996529795 (pull request #1857,
head `9a28da1a4`, attempt 1). It replaces an earlier draft written
from a run that did not test what it was meant to.

*History.*
* #1854 merged (merge `78c6e79c7`) with steps 4a and 4c, after a
  green run (37881878526, head `da9ef681c`). That run had Docker
  preinstalled, so it proved the unpack-and-chroot half of the design
  and that `install-engine` leaves an existing engine alone, and
  nothing about installing or replacing one. Its unpack numbers are a
  secondary data point: Debian 9.0s, Rocky 5.2s, 1169 MiB, both with
  docker.
* The survey's claim that the CI targets were stock images with no
  engine was wrong. `setup-kerbside-environment` ran
  `_build/install-build-dependencies.sh` on the target, which
  installed `docker-ce` before bootstrap, so `install-engine` printed
  "docker is already installed" on both hosts. #1857 (commit
  `150a7c138`, "Start the launcher entries with no engine.") fixed
  this with a `--no-docker` option on that script, an `install_docker`
  input on `setup-kerbside-environment` (shakenfist/actions#150,
  merged), and bootstrap adding the base user to the `docker` group
  after `bootstrap-servers`, because with no engine beforehand the
  `docker` group only exists once Kolla has installed `docker-ce`. `9a28da1a4` restored the
  action reference to `@main`.
* The evidence is run 37996529795, attempt 1.

*Bootstrap on a bare host.* Completed on both. Both jobs log `Will
not install Docker.` before bootstrap. The deployer image was the
same digest on both
(`sha256:eb20f4d19ee3ef0e4fcaff74473f8998b2641f9beb31628a3e3c13e8b69fa080`).

| Host | Launcher entry (job) | `install-engine` | Pull / unpack engine | `bootstrap-servers` recap |
|------|----------------------|------------------|----------------------|---------------------------|
| debian 13 | `master-h-debian13-c-debian13-aio-launcher` (114127096861) | `docker.io` and `docker-cli` (26.1.5), apt | docker | `ok=39 changed=13 failed=0` |
| rocky 10 | `master-h-rocky10-c-debian-13-aio-launcher` (114127096671) | `podman` 5.8.2, dnf | podman | `ok=39 changed=19 failed=0` |

The launcher's unpack lines:

```
kolla-ansible: unpacked <image>@sha256:eb20f4d1... with docker into /var/lib/kolla-ansible-launcher/bootstrap-at25ekou/rootfs in 1.9s, 1170 MiB (du -sx)
kolla-ansible: unpacked <image>@sha256:eb20f4d1... with podman into /var/lib/kolla-ansible-launcher/bootstrap-ysj_9j2x/rootfs in 3.3s, 1170 MiB (du -sx)
```

| Host | Engine | Unpack time | Size (`du -sx`) |
|------|--------|-------------|-----------------|
| debian 13 | docker (`docker.io` 26.1.5) | 1.9s | 1170 MiB |
| rocky 10 | podman 5.8.2 | 3.3s | 1170 MiB |

The size is within a MiB of the first run's 1169 MiB and of the local
probe. It is lower than Phase 1's 1.68 GB image size, which was
measured differently; I did not establish why. The times are well
under the first run's 9.0s and 5.2s. I did not establish why, and
one run each does not say. Neither log shows a
warning about a mount or a directory left in place.

On Rocky the image was pulled twice, as the plan expected: by
podman as root for the launcher, and later by Docker for the base
user once `docker-ce` existed. On Debian it was pulled once: root's
pin downloaded it with `docker.io` ("Downloaded newer image"), and
after `docker-ce` replaced `docker.io` the base user's pin reported
"Image is up to date". So `docker-ce` 29 saw the images `docker.io`
had stored, and the Risk about that did not materialise.

*The engine replacement on Debian.* This is the case shape (d)
exists for and the first run could not reach. `docker.io` 26.1.5 was
installed by `install-engine`, the launcher unpacked the pinned
digest with it, and `bootstrap-servers` then ran the Kolla `docker`
role from inside that unpacked deployer. The role installed
`docker-ce` and removed `docker.io` underneath the running chroot.
Afterwards `dpkg` shows `docker-ce` 5:29.9.0 and `containerd.io`
2.4.1 `ii` and `docker.io` `rc`. The role's "Start and enable docker"
task reported `ok`, bootstrap finished `failed=0`, and prechecks,
pull, deploy and the tests all passed. The Risk about `docker.io`'s
removal leaving `docker-ce` half-installed did not materialise.

*The end state*, from `tools/check-deployer-launcher` after the
deploy:

| | debian 13 | rocky 10 |
|---|-----------|----------|
| Packages | `docker-ce` 5:29.9.0, `containerd.io` 2.4.1; `docker.io` 26.1.5 `rc`; `podman` not installed | `docker-ce` 29.9.0, `containerd.io` 2.4.1, `podman` 5.8.2 |
| Docker server (`docker version`) | 29.9.0 | 29.9.0 |
| `/etc/docker/daemon.json` | `bridge: none`, `ip-forward: false`, `iptables: false`, log `max-size` 50m, `max-file` 5 | identical |
| Running containers | 36 lines: the deployed cloud (`mariadb` to `kerbside_proxy`) and neutron's `qdhcp` and `qrouter` helpers | 36 lines, same shape |
| Venv at `/srv/kolla-ansible/venv` | absent | absent |

The inventory check reported the nsenter connection; `getcap` on the
image's `nsenter` reported `cap_sys_chroot,cap_sys_ptrace,cap_sys_admin=ep`;
the `admin-openrc.sh` and `clouds.yaml` owners were `debian` and
`cloud-user`. On Rocky `podman` and `docker-ce` coexisting is what
the plan predicted, and nothing in the log shows one disturbing the
other.

*The interpreter.* "Checking docker SDK version" in the prechecks
passed on both hosts (`ok: [kolla]`) with
`ansible_python_interpreter` gone from `tools/bootstrap-kolla-ansible`
(`grep -n 'ansible_python_interpreter' tools/bootstrap-kolla-ansible`
exits 1). That is the check Phase 3's Rocky failure came from. This
time it is on bare hosts: on Rocky the `docker_sdk` role ran "Install
docker SDK for python using pip" and reported `changed`, so the SDK
was installed by the deploy and found by the check with no pin. On
Debian that task was skipped and the check still passed. The Risk
"The interpreter goes wrong again" did not materialise.

*Durations.* From the "Deploy Kolla-Ansible" step of each job
(`started_at` to `completed_at`), which includes bootstrap, and the
duration the job log reports for the "Bootstrap Kolla-Ansible" action.
Each launcher entry is against its venv control in the same run.

| Host | Entry | Job | Deploy step | Bootstrap |
|------|-------|-----|-------------|-----------|
| debian 13 | `master-h-debian13-c-debian13-aio-launcher` | 114127096861 | 23m58s | 1m26s |
| debian 13 | `master-h-debian13-c-debian13-aio` (control) | 114127096676 | 25m51s | 2m14s |
| rocky 10 | `master-h-rocky10-c-debian-13-aio-launcher` | 114127096671 | 28m38s | 1m49s |
| rocky 10 | `master-h-rocky10-c-debian-13-aio` (control) | 114127096649 | 27m41s | 2m13s |

The deploy-step differences (1m53s in the launcher's favour on
Debian, 57s against it on Rocky) are noise, for the same reason as in
Phase 3: they point in opposite directions, each pair ran on different
runners at different times, and Phase 3's controls ranged widely for
the same shapes. A paired comparison would need several runs of each.

The bootstrap sub-step is the comparison of interest, and the
launcher's now includes installing an engine (the controls' Docker
comes from the setup step, outside the timed bootstrap). Even so
the launcher was faster in both (48s on Debian, 24s on Rocky). One run
each, so this is labelled noise until repeated. The unpack is a
small share of the launcher's bootstrap (1.9s of 1m26s and 3.3s of
1m49s).

*The local probe (step 4b).*
* No local image existed. `_build/imagebuild.sh` failed with
  `archive/kolla-build.conf: No such file or directory` until
  `mkdir archive` was run. Phase 3 step 3b's recipe needs the same,
  and its row now says so.
* The image built as `kolla/kolla-ansible:local`, 1.67 GB.
* `unshare --map-auto` failed because `newuidmap` (package `uidmap`)
  is not installed. The probe used `--map-root-user --mount`, with a
  `tar` shim adding `--no-same-owner`, since a fake root cannot
  `chown`. **Ownership preservation was therefore not exercised
  locally.** The clean-host jobs, which unpack as real root, are the
  evidence for it: bootstrap completed on both.
* The launcher unpacked in 8.0s, 1169 MiB. `kolla-ansible
  bootstrap-servers --help`, `ansible --version` (ansible-core
  2.21.5, Python 3.13.5) and a `cat` through a same-path mount all
  exited 0.
* Afterwards the run directory was gone and `findmnt` showed nothing
  left behind. Docker was usable from inside the user namespace.

*What the chroot needed by hand.* Nothing, beyond Decision 2. In
CI the helper built the mounts, `bootstrap-servers` ran from the
image's environment as root, and no step needed a manual `mount`,
`chmod` or environment variable. Decision 4's root requirement held
(bootstrap runs under `sudo`). The remote tmp directory is the one
thing still passed by hand, `-e ansible_remote_tmp=/tmp/.ansible-kolla-root`,
as the survey said. What did need changing was outside the chroot:
the base user's `docker` group membership, which only exists once
Kolla has installed `docker-ce` (see History).

*SELinux on Rocky.* The Rocky job is green and `bootstrap-servers`
ran from the unpacked tree under `/var/lib/kolla-ansible-launcher`
(label `var_lib_t`, as the Risk predicts). What the log shows:
`tools/check-deployer-launcher` took its SELinux branch
(`selinuxenabled` succeeded, where Debian printed "SELinux is not
enabled") and `restorecon -n -v -R /etc/kolla` reported `no
relabels needed`. So SELinux was enabled on the host. The baremetal
role's "Change state of selinux" task, about 39 seconds into
`bootstrap-servers`, reported `changed`, which is consistent with it
having been enforcing before, Rocky's default. **This log cannot say
whether SELinux was enforcing when the chroot started.** There is no
`getenforce` and the audit log is not collected, so an AVC would not
have shown even if one had been logged. Nothing failed with a
permission error. If this needs settling, `getenforce` before
bootstrap and `ausearch -m avc` after would do it.

*Other failures in the run, unrelated to Phase 4.*
* **Multinode** (job 114127096781): the deploy and the tests passed,
  and "Collect logs" failed with exit status 127. `tools/gather-logs`
  installs clingwrap on each node from GitHub and pip, so a failure
  there would leave clingwrap missing on a node and give exactly this
  status. Separately, the step runs under `bash -e` and uses `wait
  ${pids}`, so a background job that exits non-zero aborts the step
  before the per-node "Warning: gather-logs on ... exited with code"
  code can run. That second part is a pre-existing workflow bug, not
  caused by this phase.
* **Ubuntu 24.04 all-in-one** (job 114127096765): the deploy passed
  (`failed=0`), then `tools/install-openstack-clients` failed with
  `Temporary failure in name resolution` for `devpi.home.stillhq.com`.
  This is the step Phase 4 changed (the clients' venv), but the
  error is a failure to resolve a name, not a path or venv problem,
  and the same index was reachable earlier in the same job.
* Both look like a DNS or network problem in the test cloud in the
  same window. The multinode job passed when re-run (attempt 2). The
  Ubuntu job was not re-run in that attempt.

*Plan corrections made by this phase's findings, at their source.*
* The Risk "The bind mounts outlive the helper" claimed that a
  surviving namespace blocks deletion. It does not: since Linux 3.18
  unlinking a directory that is a mount point only in another
  namespace succeeds and lazily detaches the mount. The Risk is
  rewritten, and the mountinfo check and `st_dev` rule are now
  described as backstops.
* A Risk is added for the SELinux labels of the unpacked tree on
  Rocky.
* The survey's claim that the CI targets have no engine is
  corrected and records how #1857 fixed it.
* Phase 3 step 3b's brief now includes `mkdir archive`.
* The two passages that said the OpenStack clients live in the
  bootstrap venv now say Phase 4 moved them to
  `/srv/openstack-clients/venv`.

### Phase 5: Version safeguards

Planning effort: medium, once Phases 1 to 4 have fixed the
contracts.

Two guards, both evaluated by Ansible inside the deployer, where the
configuration can be read properly:

* **Deployer against configuration.** The deployer knows the tag and
  release it was built with. The guard refuses an effective
  `openstack_tag` or `openstack_release` that differs.
* **Deployer against deployment.** Each successful `deploy` or
  `upgrade` records what deployed it on every target. The guard
  refuses a deployer older than that record, and refuses an upgrade
  path upstream does not support, unless the operator passes
  `--override-deployer-checks`, in which case it says what it is
  overriding. (Named `--allow-downgrade` when planned; renamed at the
  5b back brief, and corrected here in 5d.)

**In scope:**
* the build tag and release in the image's `deployer.json`;
* a Kolla-Ansible patch with both guards, the record, and
  `--override-deployer-checks`;
* a CI check that makes each guard fire and not fire, and shows the
  override; and
* the documentation of all three.

**Out of scope:**
* Guards for the venv deployer. It has no reliable notion of its own
  release (Decision 2), and the venv entries are the controls. The
  record is still written there, so a deployment made by the venv can
  later be guarded by an image.
* Per-service tag overrides (`<service>_tag`). The guard checks
  `openstack_tag` alone. Phase 6's manifest is what pins every image.
* Proving an upgrade or a skip-level upgrade end to end. No CI entry
  upgrades, and adding one is a separate cost. The path rules are
  unit-tested in the filter plugin instead (step 5b).

#### What the survey found

Surveyed on 2026-10-11 against this repository at `af2474d4f`,
Kolla `3fc91f2` and Kolla-Ansible `6f2df72`, the pinned
`source_sha`s.

*What the image already knows about itself.*
* patch199's `kolla-ansible/Dockerfile.j2` labels the image
  `openstack_release="{{ openstack_release }}"` and writes
  `/etc/kolla-ansible/deployer.json` with the Kolla-Ansible version,
  the ansible-core version and each source tree's git commit. It does
  not record the build tag.
* `kolla-build` passes `tag` to every template
  (`kolla/image/kolla_worker.py:387`), so the tag can be written into
  `deployer.json` at build time. The old section said only that the
  image carried a "release label", which cannot be compared with a
  tag.
* Kolla's own default tag is
  `{openstack_release}-{base}-{base_tag}`
  (`kolla/common/config.py:397`), and Kolla-Ansible's `openstack_tag`
  default is
  `{{ openstack_release }}-{{ kolla_base_distro }}-{{ kolla_base_distro_version }}{{ openstack_tag_suffix }}`
  (`ansible/group_vars/all/common.yml:311`), so upstream's two
  defaults agree. Our CI builds every image, the deployer included,
  with one content-hashed tag, and `tools/bootstrap-kolla-ansible`
  substitutes the same value for `openstack_tag`
  (`etc/globals-master.yml:52`).
* Labels are image metadata, which Ansible inside the container
  cannot read. `deployer.json` is a file in the image, and is
  present in the Phase 4 chroot too.

*Where a guard can run.*
* `ansible/gather-facts.yml` is imported first by `site.yml` and by
  `kolla-host.yml`, `certificates.yml`, `prune-images.yml`,
  `rabbitmq-upgrade.yml`, `nova-libvirt-cleanup.yml` and
  `migrate-container-engine.yml`. `site.yml` serves `deploy`,
  `deploy-containers`, `prechecks`, `pull`, `reconfigure`, `upgrade`,
  `stop`, `check`, `genconfig`, `validate-config` and `gather-facts`
  (`kolla_ansible/cli/commands.py:30-36`, where `site` is the default
  playbook).
* Not covered by `gather-facts.yml`: `post-deploy.yml`,
  `destroy.yml`, `mariadb-backup.yml`, `mariadb-recovery.yml`,
  `octavia-certificates.yml`, `bifrost.yml` and
  `rabbitmq-reset-state.yml`.
* Kolla-Ansible has no record of what deployed a host today. Nothing
  under `ansible/` writes a version file, and the only mention of
  skip-level upgrades is prose in
  `doc/source/user/operating-kolla.rst`.
* `node_config_directory` is `/etc/kolla`
  (`ansible/group_vars/all/common.yml:166`). On an all-in-one host it
  is also the operator's configuration directory, which upstream's
  `tests/check-config.sh` audits: every file there must be
  `root:root` with mode 600, 660 or 770, apart from a fixed list.

*The old section's claims.* It is otherwise accurate. "A precheck"
was the wrong word: it must run for every action, as the section
itself says, so it cannot live in the `prechecks` role. "The
image's own release label" is corrected above.

#### Decisions

1. **The image records its tag and release in `deployer.json`**, by
   amending patch199 rather than adding a patch: the image and its
   provenance file are one upstream change, which Phase 8 rebases
   anyway. The provenance script gains `--tag {{ tag }}` and
   `--openstack-release {{ openstack_release }}`, and the image gains
   a `kolla_tag` label beside its `openstack_release` one, so that
   `launcher show` can print both without running anything.

2. **The guards run only when `/etc/kolla-ansible/deployer.json`
   exists**, that is, when Kolla-Ansible runs from the deployer image.
   In a venv, Kolla-Ansible's own release is the
   `group_vars/all/common.yml` default of `openstack_release`, which
   an operator's `globals.yml` overrides, so the venv cannot tell what
   it is. Guarding on a value the operator can override would guard
   nothing. The venv entries are therefore untouched, as Phase 4
   left them.

3. **The guards are one play, in a new `ansible/deployer-checks.yml`,
   imported by every playbook except `destroy.yml`.** `destroy`
   must work whatever the versions, because it is how an operator
   cleans up a mistake. The configuration guard runs once for the run
   (`run_once`), and the deployment guard on each of the play's
   hosts, reading each target's record with `slurp`. A playbook that
   acts only on the deploy host, such as `certificates.yml`, gets the
   configuration guard alone.
   *Corrected in 5d.* The play is imported **at the end of
   `gather-facts.yml`**, which covers every playbook that imports it
   (so it runs once per action that gathers facts), and directly by
   `post-deploy.yml` and `octavia-certificates.yml` (localhost only,
   record check off), `bifrost.yml`, `mariadb-backup.yml`,
   `mariadb-recovery.yml` and `rabbitmq-reset-state.yml`. It must come
   after Kolla's own fact gathering, not first: any module run on a
   host before it puts `discovered_interpreter_python` into
   `ansible_facts`, and Kolla's `Gather facts` task
   (`when: not ansible_facts`) then skips the host. The first CI run
   failed this way (see the Outcome). `any_errors_fatal` sits on the
   two refusal tasks, not on the play, so that an unreachable host is
   not newly fatal.

4. **The record is `{{ node_config_directory }}/kolla-ansible-deployment.json`**,
   written at the end of a successful `deploy`, `deploy-containers` or
   `upgrade`, `root:root` mode 0600, on every host in the play. It
   holds the release, the Kolla-Ansible version and git commit, the
   deployer tag, the action and a UTC timestamp.
   *Corrected in 5d.* It is written by a final play of `site.yml` on
   `baremetal`, for `deploy`, `deploy-containers`, `reconfigure`,
   `upgrade` and `migrate-container-engine`, owned by
   `config_owner_user`. Its fields are `format_version`,
   `openstack_release`, `openstack_tag`, `kolla_ansible_version`,
   `kolla_ansible_git_sha`, `deployer_tag`, `kolla_action` and
   `timestamp`. Mode 0600 `root:root`
   satisfies `tests/check-config.sh` on an all-in-one host. It is
   written by venv deploys too, since writing costs nothing and a
   later image-based deployer can then guard it.

5. **The comparison rules.** Releases compare as series
   (`2025.1` < `2025.2` < `2026.1`), with `master` above every
   series. Kolla-Ansible versions compare as PEP 440 versions, which
   orders pbr's `.devN` versions correctly within a branch. Against a
   record:
   * an older release, or the same release with an older
     Kolla-Ansible version, is refused for every action;
   * a newer release is allowed only for the actions an upgrade
     needs before it lands (`bootstrap-servers`, `pull`,
     `prechecks`, `gather-facts`) and for `upgrade` itself. Anything
     else is refused, with a message saying to upgrade first;
   * `upgrade` may move to the next series, or, from a `.1` (SLURP)
     series, to the next `.1`. Anything further is refused. An
     upgrade to `master` is allowed from any series, with a warning,
     because `master`'s place in the series is not knowable from the
     deployer; and
   * `--override-deployer-checks`, a new Kolla-Ansible option passed
     through as an extra variable, turns each refusal, of the
     configuration guard as well as the record guard, into a warning
     that names the record and the deployer. (Planned as
     `--allow-downgrade`; corrected in 5d.)
   The rules live in a filter plugin, so that they are unit-tested
   in Kolla-Ansible's own test tree rather than through Ansible.
   **This is the decision most likely to be argued with.** Upstream
   has never enforced an upgrade path, and an operator who has
   upgraded by hand outside the rules would now need
   `--override-deployer-checks` to proceed. (The option was first
   called `--allow-downgrade`, the wrong name for that case, and was
   renamed for that reason.)
   *Corrected in 5d.* The rules compare `kolla_action` values
   (`precheck`, `config`, `config_validate`, ...), not the words an
   operator types. `kolla-ansible gather-facts` runs `gather-facts.yml`
   directly. The alternative, warning only, would not have stopped the
   foot-gun this plan exists to remove.

6. **CI demonstrates the guards on the launcher entries, cheaply.** A
   new `tools/check-deployer-guards`, run on the target after
   `check-deployer-launcher`, uses `kolla-ansible gather-facts`, the
   cheapest action that imports the guard play:
   * guard 1 does not fire: `gather-facts` succeeds;
   * guard 1 fires: `gather-facts -e openstack_tag=not-this-tag`
     fails, naming both tags;
   * guard 2 does not fire: the record exists after the deploy, and
     the CI run's own `reconfigure` succeeded against it;
   * guard 2 fires: with the record's Kolla-Ansible version replaced
     by a higher one, `gather-facts` fails, naming both versions; and
   * the override: the same with `--override-deployer-checks`
     succeeds and prints the warning. The original record is then restored.

#### Risks

* **pbr versions in the image differ from the venv's.** The image
  installs Kolla-Ansible from an archive without `.git`, so pbr may
  report a different version from a git checkout of the same commit.
  A venv deploy followed by an image deploy could then look like a
  downgrade. CI runs neither sequence. Step 5b reads the image's
  version from `deployer.json` and records the git commit beside it,
  and the Outcome records both versions from a real run.
* **The record breaks `tests/check-config.sh`.** Decision 4's owner and
  mode are chosen to pass it. CI runs that script on every all-in-one
  entry, so a mistake shows at once.
* **A guard that fires on the CI entries' own run.** For example, the
  record written by `deploy` followed by `reconfigure` from the same
  image must pass. That is exactly guard 2's "does not fire" case,
  and the existing CI run exercises it.
* **The guard slows every action.** One `slurp` per host and one
  `localhost` task. The Outcome compares deploy durations with Phase
  4's.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Amend `_patches/patch199-kolla-ansible-deployer-image.patch` per Decision 1 of Phase 5 in `docs/plans/containerised-deployer.md`: the provenance script takes `--tag` and `--openstack-release` and writes them into `deployer.json` as `tag` and `openstack_release`; the Dockerfile passes `{{ tag }}` and `{{ openstack_release }}`; the `LABEL` line gains `kolla_tag="{{ tag }}"`; the release note says so. Recount with `tools/recount-patch.py --in-place`, regenerate the message file with `tools/extract-commit-message`, and prove it with `_build/test-apply.sh kolla` with tests, so that pep8 runs. Then add `tag` and `openstack_release` to what `kolla-ansible launcher show` prints from the labels if they are not already printed (they are labels, so they may be), with a unit test. Commit subject: "Record the deployer image's tag and release." |
| 5b | high | opus | none | Write a new upstream-bound Kolla-Ansible patch implementing Decisions 2-5 of Phase 5, listed in `kolla-ansible/ORDER`, numbered by `_build/get-next-patch-number.py`. Read the survey first; it names the playbooks and lines. Contents: `ansible/deployer-checks.yml`, imported first by every playbook under `ansible/` except `destroy.yml`; a filter plugin under `ansible/filter_plugins/` (or `kolla_ansible/` where the existing filters live; follow the tree) with the series and version comparison and the action rules, and unit tests beside the existing filter tests; the record written at the end of `deploy`, `deploy-containers` and `upgrade` in `site.yml`; a `--allow-downgrade` option on the CLI (`kolla_ansible/cli/commands.py`), passed as the extra variable `kolla_allow_downgrade`; and a release note. Every refusal message names the record's values, the deployer's values and the option that overrides it. Prove it with `_build/test-apply.sh kolla-ansible` with tests. Commit subject: "Guard deployments against the wrong deployer." |
| 5c | medium | sonnet | none | Write `tools/check-deployer-guards` per Decision 6 of Phase 5, shellcheck-clean, run on the target as the base user through `tools/ka` so that the launcher runs it. It saves and restores the record with `sudo`, and must restore it even when a check fails (a `trap`). Add a workflow step after "Assert the containerised deployer prototype was selected", for launcher entries only, that ssh-es to the target and runs it, in the style of its neighbours. Commit subject: "Show the deployer guards firing in CI." |
| 5d | low | sonnet | none | After 5c's CI run, add an Outcome to Phase 5: each guard's firing and not-firing output from the job log, the record's content on each host, the image's and the record's Kolla-Ansible versions, deploy durations against Phase 4's, and anything that behaved differently from this section. Annotate the exit criteria, and update `docs/deployer-launcher.md` with the guards, the record and `--allow-downgrade`. Commit subject: "Record what the version safeguards did." |

Order: 5a, then 5b after its back brief, then 5c. Push all three
together. 5d follows that CI run. The management session reviews
each step before its commit.

**Back brief gate on 5b.** Before writing the patch, 5b's sub-agent
reports the filter plugin's interface, the exact refusal messages,
the playbook import list, and how `slurp` handles a host with no
record. The rules are cheap to change in prose and expensive once
tests encode them.

Note on the step table: the briefs above are kept as written. Where
they say `--allow-downgrade`, or that the guard play is imported
first by every playbook, the Decisions above carry the corrected
text (marked "Corrected in 5d"), and the Outcome below says why.

Exit (status as of CI run 38132378783 on `181c8540c`, attempt 1; the
earlier run 38091129412 on `66ee372f7` failed, see the Outcome):
* Met. Both launcher entries are green (jobs 114474727293, Debian
  13, and 114474727243, Rocky 10), and `tools/check-deployer-guards`
  passed in each: all six `PASS:` lines, covering the record, guard 1
  not firing and firing, guard 2 firing, the override, and the
  record's restoration. Guard 2 *not* firing is shown by the deploy
  and reconfigure runs themselves, not by that script (see the
  Outcome).
* Met for the venv entries and multinode, with a qualification. All
  six other entries are green (Debian 13 all-in-one, Rocky 10 with
  Debian images, Ubuntu 24.04, Rocky-on-Rocky, no-kerbside, and
  multinode), and their logs have no warning, refusal or override
  text. The guard play does
  appear in their logs, as nine `PLAY [Check the deployer ...]` blocks
  whose tasks all say `skipping`, because the play is imported
  unconditionally and its tasks are gated on `deployer.json`. "No
  guard output" is therefore true of warnings and failures, not of
  the play header. The venv entries also write the record (six
  `Record the deployment` plays each; on the Debian venv all-in-one
  and on multinode, two of them `changed` on every target, the
  `deploy` and the `reconfigure`, and the rest skipped).
* Met. `tests/check-config.sh` ran, and passed, in the "Validate
  deployed config" step of all eight all-in-one and multinode jobs
  (every job whose log shows the `tests/check-config.sh` group),
  with the record present: it is written before that step runs, by
  `deploy`. The record is `root:root` mode 0600 in the launcher
  entries; I did not separately print the mode in the venv entries.
* Met by the patch, not by this run. patch206 carries 41 unit tests
  (`kolla_ansible/tests/unit/test_deployer_checks_filters.py`) for the
  rules of Decision 5, and 5b's `test-apply.sh --test-patch patch206`
  run passed (523 unit tests). The CI run does not exercise the
  upgrade or SLURP rules at all, and no entry upgrades. That those
  rules work through Ansible, rather than in the filter, is unproven.
  Nor did any run exercise guard 1's release mismatch, a record
  older than the deployer, a host with no record on a deployer
  image, or multinode with the launcher (there is no such entry).

#### Outcome

Written after CI run 38132378783 (pull request #1867, head
`181c8540c`, attempt 1), which was green in all 15 jobs. An earlier
run, 38091129412 on `66ee372f7`, is used only for the first
deviation below.

*History.*
* 5a (`24672f5c2`) amended patch199 and 5b (`66ee372f7`) added
  patch206 with the guards, the record and the option. 5c
  (`ab6f0dd92`) added `tools/check-deployer-guards` and its workflow
  step.
* **The first CI run found a real bug in 5b.** Both launcher entries
  failed in `certificates`, on `kolla_address`: "Interface 'eth0' not
  present on host 'kolla'". `gather-facts.yml` had imported the
  guard play first. The guard's `stat` was the first module to run
  on the host, which triggered interpreter discovery and put
  `discovered_interpreter_python` into `ansible_facts`; Kolla's own
  `Gather facts` task is `when: not ansible_facts`, so it skipped the
  host, and nothing later had the interface facts. `181c8540c`
  moves the import to the end of `gather-facts.yml`. 5b's local
  probes missed it because none ran Kolla's fact gathering after the
  guard. The lesson is general: any module run on a host before
  Kolla's explicit fact gathering suppresses it.
* The other failure in that run (Rocky 10 non-launcher, a 503 from
  the test cloud when creating the instance) was infrastructure and
  unrelated.

*Where the guard runs.* patch206 imports `deployer-checks.yml` at the
end of `gather-facts.yml` and directly in `post-deploy.yml` and
`octavia-certificates.yml` (deploy host only, record check off),
`bifrost.yml`, `mariadb-backup.yml`, `mariadb-recovery.yml` and
`rabbitmq-reset-state.yml`. `destroy.yml` has none. In the Debian
launcher job the guard play ran nine times: `certificates`,
`bootstrap-servers`, `prechecks`, `pull`, `deploy`,
`validate-config`, `check`, `reconfigure` and `post-deploy`. The
guard ran on every action this CI run took.

*Guards not firing, in the deploy path.* In every one of those nine
runs, on both launcher hosts, the two warning tasks and the two
refusal tasks reported `skipping` and the record `stat` reported `ok`
(`skipping` for `post-deploy` on `localhost`, which has the record
check off). In the `certificates` run, for example:

```
PLAY [Check the deployer against the configuration and the deployment] ***
TASK [Warn about the deployer and the configuration] ***
skipping: [kolla]
TASK [Refuse a configuration this deployer was not built for] ***
skipping: [kolla]
TASK [Check whether there is a deployment record] ***
ok: [kolla]
TASK [Read the deployment record] ***
skipping: [kolla]
TASK [Warn about the deployer and the deployment record] ***
skipping: [kolla]
TASK [Refuse a deployment this deployer must not change] ***
skipping: [kolla]
```

Before `deploy` finishes there is no record, so in the first five
runs (`certificates`, `bootstrap-servers`, `prechecks`, `pull` and
`deploy`) "Read the deployment record" skipped. The next three
(`validate-config`, `check` and `reconfigure`) read the record that
`deploy` wrote and compared it with the deployer without refusing.
`reconfigure` against the `deploy` record from the same image is
guard 2 not firing in anger. `post-deploy` skips the record check by
design.

*`tools/check-deployer-guards`.* Identical on both hosts apart from
the timestamp. Each of the six checks printed `PASS:`.

```
Checking the deployment record /etc/kolla/kolla-ansible-deployment.json
PASS: /etc/kolla/kolla-ansible-deployment.json exists
Checking that guard 1 does not fire for the deployed tag
PASS: gather-facts passes the guards
Checking that guard 1 fires for a different tag
PASS: a mismatched openstack_tag is refused
Rewriting kolla_ansible_version in /etc/kolla/kolla-ansible-deployment.json to 9999.0.0
Checking that guard 2 fires for a record from a newer Kolla-Ansible
PASS: a newer recorded version is refused
Checking that --override-deployer-checks overrides the refusal
PASS: --override-deployer-checks overrides the refusal and warns
Restoring /etc/kolla/kolla-ansible-deployment.json
PASS: /etc/kolla/kolla-ansible-deployment.json was restored
```

The script prints only its verdicts when a check passes, so the job
log does not contain the refusal messages themselves. It asserts that
each refusal contains `This deployer was built with tag` or `is older
than`, and `Pass --override-deployer-checks to override this check.`,
and that the override prints `Overriding this refusal because
--override-deployer-checks was given`. The messages come from
patch206; the tag one reads "This deployer was built with tag *T*,
but openstack_tag is *U*. Use the deployer built with *U*, or set
openstack_tag to *T*." and the version one names the host, the
record's path, and the record's and the deployer's versions, release,
commit and tag. Printing the refusal on success would make this
section self-evident from the log, and is a cheap change if wanted.
The script took 21s on Rocky and 15s on Debian (step timestamps).

*The record on each host*, as `tools/check-deployer-guards` printed
it:

| Field | Debian 13 | Rocky 10 |
|-------|-----------|----------|
| `format_version` | 1 | 1 |
| `openstack_release` | `master` | `master` |
| `openstack_tag` | `master-debian-trixie-956ffea5` | same |
| `deployer_tag` | `master-debian-trixie-956ffea5` | same |
| `kolla_ansible_version` | `23.1.0.dev18` | `23.1.0.dev18` |
| `kolla_ansible_git_sha` | `a5f569044ddc3a7ae20bfcb945932852b2f35a6e` | same |
| `kolla_action` | `reconfigure` | `reconfigure` |
| `timestamp` | `2026-10-11T13:49:02Z` | `2026-10-11T14:58:33Z` |

`kolla_action` is `reconfigure` because the CI's idempotency
reconfigure ran after `deploy` and overwrote the record; the
`Record the deployment` play reported `changed` for `deploy` and
`reconfigure` and `skipping` for the other four runs.
`tests/check-config.sh` passed with the record present, so Decision 4's `config_owner_user`, mode
0600 held, at least as far as that script audits it. The venv
entries wrote the same record, and multinode wrote it on all seven
nodes.

*Versions.* The record's Kolla-Ansible version and commit come from
the image's `deployer.json`, so the image and the record agree by
construction: `23.1.0.dev18`, commit `a5f569044`. The log does not
print `deployer.json` itself. The Risk that a venv build and an image
build of one commit would report different pbr versions did not
materialise: the Debian venv job installed `kolla-ansible==23.1.0.dev18`
from the same source tree. This compares one commit's venv with one
commit's image, not a venv deploy followed by an image deploy,
which no entry does.

*Durations.* The "Deploy Kolla-Ansible" step (`started_at` to
`completed_at`, which includes bootstrap), against Phase 4's same
step, with the controls from the same run.

| Host | Entry | Job | Phase 5 | Phase 4 |
|------|-------|-----|---------|---------|
| debian 13 | launcher | 114474727293 | 24m06s | 23m58s |
| debian 13 | control | 114474727244 | 33m15s | 25m51s |
| rocky 10 | launcher | 114474727243 | 36m39s | 28m38s |
| rocky 10 | control | 114474727291 | 27m11s | 27m41s |

Per action, in seconds, from each action's `duration_ms`:

| Action | deb launcher | deb control | rocky launcher | rocky control |
|--------|-------------|-------------|----------------|---------------|
| Bootstrap | 84 | 165 | 156 | 133 |
| Pre-checks | 50 | 53 | 63 | 46 |
| Pull | 102 | 147 | 121 | 91 |
| Deploy | 618 | 876 | 975 | 750 |
| Validate config | 125 | 150 | 184 | 125 |
| Reconfigure | 401 | 526 | 602 | 419 |

The guard itself is not what moved these. Summing the time from each
`PLAY [Check the deployer ...]` header to the next play header (from the
log's timestamps, so roughly) gives 3.9s over nine runs on the Debian
launcher, 6.3s on the Rocky launcher, and 1.7s and 1.1s on the
controls, where every task skips. That is the cost of one `stat` per
run, a few seconds in 24 to 37 minutes.

The launcher-against-control differences are in opposite directions
on the two hosts (Debian launcher 9m faster, Rocky launcher 9m
slower), the Debian control was 7m slower than in Phase 4, and the
Rocky launcher was 8m slower than in Phase 4 with its bootstrap
(156s against 109s) and unpack (5.9s against 3.3s) also slower. That
is the same pattern as Phases 3 and 4: different runners at different
times. The Rocky launcher is slower than its control in every
action, which is why I would not call it noise without more runs, but
no guard cost I can find explains it, and one run does not say. On
Debian the launcher was faster in every action. The unpack lines were
`with docker ... in 3.3s, 1170 MiB` and `with podman ... in 5.9s,
1170 MiB`.

*Anything that behaved differently from this section.*
* The option is `--override-deployer-checks`, not `--allow-downgrade`.
  The 5b back brief renamed it, because "downgrade" is the wrong word
  for an upgrade done by hand outside the rules. It overrides both
  guards, so a tag mismatch is a warning too.
* The import point and the list of playbooks (Decision 3), the
  actions the record is written for (Decision 4), and the rule
  inputs (Decision 5) differ from the plan as first written, and are
  corrected at their source above. In particular the rules compare
  `kolla_action` values, so the pre-upgrade list is `bootstrap-servers`,
  `pull`, `precheck` and `gather-facts`, and the CLI words in the
  refusal text come from a separate list.
* `any_errors_fatal` is on the two refusal tasks only, so an
  unreachable host is not newly fatal. The `stat` has
  `ignore_unreachable`.
* 5a added `tag` and `openstack_release` to `deployer.json`, and
  `kolla_tag` to the image's labels.
* The record check is gated on `deployer.json` existing. Both hosts
  satisfied that, so there is no CI evidence of a deployer image
  without the file (which the plan does not intend to exist).
* Not exercised by CI: the upgrade and SLURP rules through Ansible,
  a record newer by release rather than by version, a host with no
  record under a deployer image, `bifrost`, the MariaDB and RabbitMQ
  playbooks, and multinode with the launcher.

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

*Corrected at planning (see the survey below).* The deployer is not
built last today: `kolla-build` builds and pushes it mid-run, beside
every other image. Phase 6 keeps that, and adds the manifest as a
second image built on top of the pushed deployer once the push proxy
has drained, which then takes the deployer's tag. Kolla-Ansible's
docker worker cannot pull by digest at all, so a fix to the
`kolla_container` module comes first. The prototype needs no Kolla
patch: the thin layer is built by `_build/`, and upstream's version is
a change to Kolla's Zuul publish jobs, which Phase 8 drafts.

This changes three things, and the upstream proposal should lead
with the second and third:

* Pinning the deployer by digest (Open question 1) pins every
  image in the build.
* A pull can no longer mix builds, whether during a publish
  window or when a node is added months later.
* A tag that has stopped being published cannot be deployed by
  accident, because nothing resolves images by tag any more.

**In scope:**
* digest references in Kolla-Ansible's `kolla_container` module, for
  docker and podman;
* a Kolla-Ansible patch that reads the manifest and resolves every
  `<svc>_image_full` through it, with a warning when a deployer image
  has no manifest;
* the read-back and the thin layer in `_build/`; and
* a CI check that every container on a launcher target runs the
  digest its manifest names, and that a second run recreates nothing.

**Out of scope:**
* Upstream's Zuul publish jobs (Phase 8 drafts the change).
* The venv entries. They have no deployer image, so no manifest, and
  keep pulling by tag; that is the "without it still works" half of
  the exit, and they run the patched code.
* Multi-arch images. occystrap rejects manifest lists, so every image
  here has exactly one manifest digest.
* Migrating a running deployment from tags to digests. Switching
  recreates every container once (Risks), which an operator does at
  an upgrade anyway; nothing here makes that cheaper.
* An occystrap option that reports the digest it wrote. It would
  replace the read-back for our pipeline only, and upstream has no
  occystrap.

#### What the survey found

Surveyed on 2026-10-12 against this repository at `32918a4e3`,
Kolla `4561a13`, Kolla-Ansible `e7c2b40`, and occystrap `c3e88e5`.
Three read-only sub-agents covered the build pipeline, Kolla-Ansible's
image references, and occystrap with the registry; the two library
claims that matter were checked by hand against docker-py 7.2.0 and
podman-py 5.8.0.

*How images are built and pushed.*
* The "Images for ..." job (`.github/workflows/functional-tests.yml:112-385`)
  runs `_build/build-containers.sh --use-ci-registry --use-proxy ...
  --build-images <regex> --image-tag <hash>` (`:268-278`). The tag is
  `<release>-<distro>-<version>-<hash>`, where the hash covers the
  date and the source trees (`_build/calculate-container-hash.sh:7`).
* `build-containers.sh:29-84` starts an occystrap proxy on
  `127.0.0.1:5050`, forwarding to `gitlab.home.stillhq.com:5050`
  (`_build/common.sh:66-68`) through `normalize-timestamps` and a
  `.git` exclusion, recompressing with zstd. `kolla-build --push
  --registry 127.0.0.1:5050` (`_build/imagebuild.sh:171-201`) pushes
  each image as soon as it is built. The proxy is stopped, and drains,
  only after every target is built (`build-containers.sh:295-296`),
  and `stop_occystrap_proxy` ignores the proxy's exit status (`:93`).
* **The deployer is not built last.** The regex's `kolla` alternative
  matches `kolla-ansible` (and `kolla-toolbox`), whose parent is
  `openstack-base`, so it is built and pushed mid-run. Nothing runs
  after the push.
* **The digest `kolla-build` sees is not the one that lands**, which
  confirms Open question 4. The proxy answers a manifest `PUT` with
  the digest of the manifest the client sent, and serves that
  manifest back to the client (`occystrap/proxy.py:99-115`,
  `:629-632`, `:727-744`), while the registry stores the filtered one.
  `normalize-timestamps` leaves the config's `created` and `history`
  alone, so a rebuild of identical layers still gets a new digest.
  occystrap writes no record of the digest it pushed.
* **Nothing reads a digest back.** There is no skopeo, crane or
  `/v2/.../manifests` query anywhere in `_build/`, `tools/` or the
  workflows. `tools/find_images` already authenticates to the GitLab
  API and asks for `.../registry/repositories/<id>/tags/<tag>`
  (`:118-134`), but keeps only the status. Registry reads need the
  `kolla-imgs` token (`functional-tests.yml:183-210`). The runners
  have `curl`, `jq`, docker with buildx, and a tools venv with
  `requests` and `python-gitlab` (`_build/install-build-dependencies.sh:115-119`).
* Every image is a single-arch Docker v2 schema 2 manifest; the proxy
  rejects manifest lists (`occystrap/proxy.py:645-654`).
* A build pushes 86 images on Debian and Ubuntu and 82 on Rocky
  (`data/layers/build-*-master/`). Many are never deployed: every
  `*-base`, prometheus, grafana, fluentd and the deployer itself.
* Deploy jobs pull from the same registry and namespace they push to
  (`etc/globals-master.yml:100-106`).
* **The launcher's `pin` already records a real registry digest**,
  by pulling and reading `RepoDigests` (`deployer/launcher/kolla_ansible_launcher/engine.py:120-165`).
  `tools/bootstrap-kolla-ansible:264-297` pins
  `kolla-ansible:<image_tag>` in launcher mode. So if the
  manifest-bearing image holds the deployer's tag when bootstrap runs,
  the pin picks it up with no launcher change.
* **Tags can move.** A rerun on the same day computes the same tag,
  the in-script retry re-pushes (`build-containers.sh:223-233`), and
  the concurrency group is per ref (`functional-tests.yml:154-156`),
  so two refs with identical inputs can push one tag at once. With
  `--dont-fetch-images`, a tag that already exists skips the build
  entirely (`build-containers.sh:179-183`), so a rerun after a
  half-finished attempt builds nothing.

*How Kolla-Ansible names images.*
* There is no central hook. 155 `*_image_full` variables in 51
  `roles/*/defaults/main.yml` files all follow
  `"{{ <p>_image }}:{{ <p>_tag }}"`, with `<p>_image` being
  `"{{ docker_image_url }}<kolla-build name>"`, and every consumer
  reads `service.image` (from the services dict) or uses the variable.
  Three of the 155 come from our patch147 (kerbside); nothing else
  downstream on the main stack adds one.
* The kolla-build name is everything after `docker_image_url`, and all
  147 distinct names match a directory in `kolla/docker/`. The
  exceptions are computed names: `rabbitmq-<version>` in
  `ansible/rabbitmq-upgrade.yml:23`, and patch147's
  `nova-libvirt-spice` or `nova-libvirt` choice. Several variables
  share an image (three neutron workers use `neutron-server`;
  `keystone_httpd` uses `httpd`), which a manifest keyed by image name
  handles for free.
* `kerbside_base_image_full` is built from `kerbside_api_image`
  (patch147, `roles/kerbside/defaults/main.yml:87`). That is an
  existing patch147 bug, recorded under Future work; a manifest keyed
  by name reproduces today's behaviour, so it is not this phase's.
* **The docker worker cannot pull by digest.** `parse_image`
  (`ansible/module_utils/kolla_container_worker.py:553-564`) splits
  `repo@sha256:<hex>` at its last `:`, and `pull_image`
  (`kolla_docker_worker.py:202-236`) calls
  `pull(repository='repo@sha256', tag='<hex>')`. docker-py's
  `parse_repository_tag` strips the `@sha256`, the explicit tag wins,
  and the daemon is asked for a tag named after the bare hex string.
  podman-py builds `f'{repository}:{tag}'`, which rebuilds the
  original reference, so podman pulls by accident.
* **The docker worker would also recreate every container on every
  run.** `check_image` (`kolla_docker_worker.py:71-80`) matches only
  `RepoTags`, and an image pulled by digest has only `RepoDigests`, so
  `compare_image` reports a difference and `check_container_differs`
  (`kolla_container_worker.py:89-123`) restarts everything. podman's
  `check_image` uses `images.get()`, which resolves digests.
  community.docker's `docker_image_info`, used by the
  `service-image-info` role, matches `RepoDigests` already.
* Nothing in Kolla-Ansible or the collection mentions image digests
  today. `group_vars/all/common.yml:317-318` (patch206) shows the shape
  for reading a file the deployer image carries.

*The old section's claims.* "The deployer is built last" and "a thin
final layer of the deployer image" are corrected above: it is a
second image on top of the pushed deployer. "Using the existing
`<svc>_image_full` variables" is right, but those variables cannot
take a digest on docker until the module is fixed. The table in "How
this work is carried" listed build-manifest support as a Kolla patch;
it is corrected there, at its source, in this planning commit.

#### Decisions

1. **Fix digest references in `kolla_container` first, as its own
   patch.** `pull_image` passes a digest as `tag='sha256:<hex>'`
   (the Engine API accepts a digest in the `tag` parameter), and
   `check_image` also matches `RepoDigests`. podman's argument shape
   must keep working, so the docker worker changes and the shared
   `parse_image` does not, or both workers stop splitting a digest
   reference. It is independently useful upstream (an operator can
   already write a digest into `<svc>_image_full`, and today it
   fails), so it goes first and does not depend on the manifest.

   *Corrected in 6e.* Built as written (patch208): the docker worker
   splits a digest reference at the `@` and passes the digest as
   the tag, and `check_image` also reads `RepoDigests`; the shared
   `parse_image` is unchanged. A tag given beside a digest is
   dropped. `get_image_id` needed nothing, because its
   `images(name=)` filter accepts a digest reference on both image
   stores. The deployer image installs no docker or podman library
   at all, so the versions that matter are the target's: the
   collection installs docker >= 7.0.0 and podman >= 4.7.0 there
   (7.2.0 and 5.8.0 today), and 6a tested against those.

2. **Every `<svc>_image_full` resolves through one lookup plugin**,
   `"{{ lookup('kolla_image_full', <p>_image, <p>_tag) }}"`, with its
   rules in `kolla_ansible/image_manifest.py` beside patch206's
   `deployer_checks.py`. A lookup, unlike a filter, sees the play's
   variables, so the 155 lines need not repeat `openstack_tag` and the
   manifest path. It returns `<image>@<digest>` when the manifest names
   the image and `<p>_tag` equals the manifest's tag, and
   `<image>:<tag>` otherwise. The tag comparison is what keeps an
   operator's per-service `<svc>_tag` override working: an overridden
   tag is not the build's, so it is used as written. It caches the
   parsed manifest by path and mtime, because group variables are
   re-templated on every use.
   **This is the decision most likely to be argued with.** It is a
   51-file patch that the daily rebase will have to carry through
   every upstream change to those defaults blocks. The alternative, an
   action plugin named `kolla_container` that rewrites `image` on the
   way into the module, is one file, but it hides the digest from
   every variable and log, and misses `service-image-info`, which
   would then silently skip the libvirt version check. A reviewer
   should be able to see what will be pulled by reading the variables.

   *Corrected in 6e.* The lookup reads `docker_registry`,
   `docker_namespace` and `kolla_image_manifest_path`, not
   `openstack_tag`: the tag it compares is the one passed to it, the
   service's own `<p>_tag`. An image outside `docker_registry` and
   `docker_namespace` is used as written. The rules are in
   `kolla_ansible/image_manifest.py`, and a new filter plugin,
   `ansible/filter_plugins/image_manifest.py`, serves the check in
   `deployer-checks.yml`. The rewrite is **50 files**, not 51, and
   152 upstream lines (the other 3 of the 155 are patch147's, in
   Decision 5). One line was folded with `>-` to stay inside
   ansible-lint's 160 columns. tox's `ANSIBLE_LOOKUP_PLUGINS` and the
   zuul all-in-one regex also had to learn about the plugin.

3. **The manifest is `/etc/kolla-ansible/manifest.json`**, beside
   `deployer.json`, read through a new `kolla_image_manifest_path` in
   `group_vars/all/common.yml`:
   `{"format_version": 1, "tag": "<build tag>", "images": {"<kolla-build name>": "sha256:<hex>", ...}}`.
   It lists every image the build pushed except the deployer, which
   cannot name its own digest. Keys are kolla-build names, not
   registries, because a digest is the same wherever the image is
   mirrored verbatim. A name missing from the manifest falls back to
   its tag, which covers the computed `rabbitmq-<version>` names.

   *Corrected in 6e.* The keys are the image's repository **within
   `docker_namespace`**: kolla-build's name plus any
   `docker_image_name_prefix`, derived as `image[len(base):]` with
   `base = registry/ + namespace/`. That is the property that lets a
   mirror keep a manifest valid. A real manifest has 85 entries on
   Debian and Ubuntu and 81 on Rocky and is 8 KiB (see the Outcome).
   A manifest which is malformed, or whose `format_version` is
   unknown, **fails closed**: the lookup raises an `AnsibleError`,
   and `deployer-checks.yml` refuses up front. Neither is bypassed by
   `--override-deployer-checks`, because a tag fallback is what the
   manifest exists to prevent; `kolla_image_manifest_path` pointing
   at a file that does not exist is the way to ask for tags.

4. **Warn once when a deployer image has no manifest**, in
   `ansible/deployer-checks.yml` (patch206's play, already run once
   per action): when `deployer.json` exists and the manifest does
   not. A venv, which has neither, stays silent, as in Phase 5. The
   lookup itself never warns, or every image on every host would.

   *Corrected in 6e.* The play also refuses a manifest whose tag
   differs from `deployer.json`'s, a mismatch `--override-deployer-checks`
   turns into a warning like the other mismatches, and the
   malformed-manifest refusal of Decision 3. The warning reads "This
   deployer image has no build manifest at *path*, so images will be
   pulled by tag".

5. **patch147's three kerbside lines are converted by a small
   downstream patch** listed after both in `kolla-ansible/ORDER` only.
   Amending patch147 would make the kerbside series depend on the
   manifest patch, which upstream has not seen. patch147's
   `nova_libvirt_image` choice needs nothing: the upstream
   `nova_libvirt_image_full` line wraps it, and the lookup resolves
   whichever name it holds. But that line's hunk has patch147's line
   as context, so the upstream patch will not apply to pristine
   Kolla-Ansible there; Phase 8 rebases it, as it does patch199.

   *Corrected in 6e.* Confirmed by 6b: exactly one hunk of patch209
   (`nova-cell`'s defaults, near line 300) has patch147's
   `nova_libvirt_image` line as context; every other rewrite applies
   to pristine upstream. patch210 is the downstream patch, after
   patch209 in the main `ORDER` only.

6. **The manifest is a second image, built after the proxy drains.**
   A new `tools/write-build-manifest` (Python, in the tools venv)
   lists the repositories in `openstack/kolla-images` carrying this
   build's tag through the GitLab API, as `tools/find_images` does,
   and reads each digest with a `HEAD /v2/<repo>/manifests/<tag>`
   using the v2 schema 2 `Accept` header, which is the digest a pull
   uses, rather than trusting GitLab's own field. It writes
   `manifest.json`. `build-containers.sh`, after
   `stop_occystrap_proxy`, then builds
   `FROM <registry>/<namespace>/kolla-ansible@<its digest>` with the
   manifest copied in and a `kolla_build_manifest="1"` label, and
   pushes it to `kolla-ansible:<tag>` with `occystrap process`, the
   path the fallback push already uses (`build-containers.sh:263-283`),
   so it goes through the same filters. The step is idempotent: if
   `kolla-ansible:<tag>` already has the label, it does nothing, which
   covers the rerun that skips the build. It fails the job if any
   digest cannot be read, rather than writing a partial manifest.
   Reading all 86 images after the build, not as each is pushed, is
   what makes a concurrent push of the same tag visible: the manifest
   is whatever the registry held when it was read, and both builds had
   identical inputs.

   *Corrected in 6e.* **There is no GitLab API in it.** The first
   CI run (38227709502, on `6bbfcb8a1`) failed in "Images for master
   on debian trixie": `write-build-manifest` could not read the
   GitLab project (HTTP 503 after its retries), and in the same job
   `tools/find_images` printed "GitLab authentication failed". The
   CI registry token works against the registry but cannot be relied
   on for the REST API. `4efc3dcc2` lists the images from
   kolla-build's own `archive/images` instead, and keeps the
   registry `HEAD` for each digest. `write-build-manifest` also gained
   `--image-prefix`. Only the listing changed: the digests are still
   read from the registry, which is what Open question 4 requires.
   The consequence is that `find_images` has never been able to
   authenticate in CI, so the "skip the build if the images exist"
   path never fires; see Future work.

7. **CI proves it on the launcher entries** with a new
   `tools/check-deployer-manifest`, run on the target after
   `check-deployer-guards`:
   * the image `bootstrap-kolla-ansible` pinned has the label and the
     manifest;
   * every running Kolla container's `Config.Image` is
     `<image>@<digest>` and that digest is the manifest's entry for its
     name (the computed rabbitmq name aside, which CI does not use);
   * a second run recreates nothing: container IDs are unchanged
     across `kolla-ansible deploy-containers --tags common` (the
     cheapest action that runs `service-check-containers`; the step
     confirms or replaces the tag); and
   * with `-e kolla_image_manifest_path=/nonexistent`, `gather-facts`
     prints the missing-manifest warning once.
   The venv entries are the "without it" half: their containers keep
   tag references, through the same patched lookup.

   *Corrected in 6e.* Check 3 runs `deploy-containers --tags
   common,cron`, not `--tags common`: `common` alone re-checks only
   `kolla_toolbox`, and `cron` is the other container of that role
   family. The ID comparison covers all 36 containers, but only those
   two roles are re-checked, so a recreate elsewhere would not be
   caught by it.

#### Risks

* **Every container is recreated once when a deployment switches
  between tags and digests**, Galera and RabbitMQ included, because the
  `Config.Image` string changes. CI deploys fresh, so it never sees
  this. The release note says so, and an operator meets it at an
  upgrade, which restarts everything anyway.
* **A recreate on every run, if Decision 1's `check_image` fix is
  wrong.** The deploy action's "Reconfigure (idempotency check)" only
  runs `reconfigure`; it does not assert that nothing changed. Decision
  7's container-ID check is what catches it, and step 6a's unit tests
  cover both workers.
* **Docker's containerd image store** reports `RepoTags` and
  `RepoDigests` differently from the classic store, and the collection
  can turn it on (`ansible-collection-kolla/roles/docker/tasks/config.yml:72`).
  Our build hosts turn it off; deploy targets use the collection's
  default. Step 6a checks which store the CI targets run, and the
  Outcome records it.

  *Corrected in 6e.* 6a could not check, because it ran before any CI;
  the clingwrap bundles do record it, and the Outcome has the answer:
  the Debian target uses the classic `overlay2` store and the Rocky
  target the containerd store (`overlayfs`), so both were exercised.
* **The lookup's cost.** 155 variables are templated many times per
  task. The cache in Decision 2 keeps that to a `stat`; the Outcome
  compares deploy durations with Phase 5's. *Corrected in 6e:* no
  cost is measurable (Outcome).
* **The deployer's tag moves.** Between the mid-run push and the
  manifest image, `kolla-ansible:<tag>` has no manifest. Nothing
  consumes it in that window in CI. An operator who resolved the tag
  then would get a deployer that warns and pulls by tag, which is the
  designed fallback.
* **A proxy that failed to drain** leaves images missing from the
  registry. `write-build-manifest` fails on any missing digest, so the
  job fails where today it would have failed later, at pull.
* **kerbside's downstream CI** runs `build-containers.sh` too, so it
  gains the manifest step. It is gated on the `kolla-ansible` image
  having been pushed in this run's registry, so a build without the
  deployer skips it.
* **docker-py and podman-py versions in the image** may differ from
  the 7.2.0 and 5.8.0 checked here. Step 6a reads the versions the
  deployer image installs and tests against those. *Corrected in
  6e:* the image installs neither library; see Decision 1.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | opus | none | Write an upstream-bound Kolla-Ansible patch, "Support image digests in kolla_container", per Decision 1 of Phase 6 in `docs/plans/containerised-deployer.md` (read its survey first; it names the lines). Number it with `_build/get-next-patch-number.py`, list it in `kolla-ansible/ORDER` after patch206 (master only, no per-release `ORDER`). In `ansible/module_utils/kolla_docker_worker.py`, `pull_image` must pull `repo@sha256:<hex>` (pass the digest as `tag='sha256:<hex>'`, or the whole reference with no tag; docker-py 7.2.0's `parse_repository_tag` splits on `@` first) and `check_image` must also match `RepoDigests`. Keep `kolla_podman_worker.py` working: podman-py builds `f'{repository}:{tag}'`, so do not change the shared `parse_image` in a way that gives podman `repo:sha256:<hex>`. Extend `tests/kolla_container_tests/test_docker_worker.py` and `test_podman_worker.py` for pull, `check_image` and `compare_image` with a digest reference, including the case where the image is present only by digest. Find the docker and podman library versions the deployer image installs and say which you tested against. Note, without fixing, whether `get_image_id`'s `images(name=...)` accepts a digest. Add a release note. Recount with `tools/recount-patch.py --in-place`, regenerate the message with `tools/extract-commit-message`, and prove it with `_build/test-apply.sh --test-patch patchNNN kolla-ansible` with tests. Commit subject: "Support image digests in kolla_container." |
| 6b | high | opus | none | Write an upstream-bound Kolla-Ansible patch implementing Decisions 2-4 of Phase 6, after 6a in `kolla-ansible/ORDER`, and a downstream patch implementing Decision 5 after it (main `ORDER` only). The upstream patch: `kolla_ansible/image_manifest.py` (rules, unit-tested beside `test_deployer_checks_filters.py`), `ansible/lookup_plugins/kolla_image_full.py` (thin wrapper reading `openstack_tag` and `kolla_image_manifest_path` from the variables, caching by path and mtime), `kolla_image_manifest_path` in `group_vars/all/common.yml` beside patch206's `kolla_deployer_info_path`, every upstream `<p>_image_full: "{{ <p>_image }}:{{ <p>_tag }}"` rewritten to `"{{ lookup('kolla_image_full', <p>_image, <p>_tag) }}"` by a script you keep in the scratchpad (prove the count: 152 upstream lines, none left in the old form), the missing-manifest warning in `ansible/deployer-checks.yml`, a release note (including the one-time recreate), and a section in `doc/source/user/operating-kolla.rst` beside patch206's "Deployer checks". The downstream patch converts patch147's three kerbside lines. Where the upstream patch's context includes a downstream patch's line (patch147's `nova_libvirt_image` beside `nova_libvirt_image_full`), say so in your report for Phase 8 rather than reordering. Check that `rabbitmq-upgrade.yml:23`'s computed name still resolves to a tag. Prove both with `_build/test-apply.sh --test-patch <n> kolla-ansible` with tests. Commit subjects: "Pull images by digest from a build manifest." and "Resolve the kerbside images through the manifest." |
| 6c | high | opus | none | Implement Decision 6 of Phase 6 in this repository: `tools/write-build-manifest` (Python, single quotes, 120 columns, runnable from the tools venv, which has `requests` and `python-gitlab`; reuse `tools/find_images`' GitLab API handling and add the registry bearer-token handshake for `HEAD /v2/<repo>/manifests/<tag>` with the v2 schema 2 `Accept` header), with unit tests the repo's pre-commit runs, and a function in `_build/build-containers.sh` called after `stop_occystrap_proxy` that is idempotent on the `kolla_build_manifest` label, builds the thin image `FROM` the deployer's digest, and pushes it with `occystrap process` as `build-containers.sh:263-283` does. It must skip cleanly when no registry is in use (`local-container-builds.yml`) and when this run did not push `kolla-ansible`, and fail the job on any unreadable digest. Update `docs/building.md`, and `ARCHITECTURE.md`'s build data flow (the shape of the system changes: a step after the push). Shellcheck-clean. Commit subject: "Build a manifest of image digests after the push." |
| 6d | medium | sonnet | none | Write `tools/check-deployer-manifest` per Decision 7 of Phase 6, shellcheck-clean, in the style of `tools/check-deployer-guards` (testable through `CHECK_*` variables, a trap that restores anything it changes), and add a workflow step after "Assert the containerised deployer guards work", for launcher entries only. Confirm that `deploy-containers --tags common` reaches `service-check-containers` for at least the `common` role's containers, or pick the cheapest action that does and say why. Commit subject: "Show the build manifest pinning every image." |
| 6e | low | sonnet | none | After the CI run of 6a-6d, add an Outcome to Phase 6: the manifest's size and a sample, whether every container ran by digest on each launcher host, the container-ID check, the warning, the containerd-store question from the Risks, the venv entries' references, deploy durations against Phase 5's, the build step's duration, and anything that behaved differently from this section. Annotate the exit criteria, and update `docs/deployer-launcher.md` with the manifest and the fallback. Commit subject: "Record what the build manifest did." |

Order: 6a, then 6b after its back brief, then 6c, then 6d. Push all
four together; 6e follows that CI run. The management session reviews
each step before its commit.

**Back brief gate on 6b.** Before rewriting the 152 lines, 6b's
sub-agent reports the lookup's interface and cache, the manifest
schema as the rules read it, the exact warning text, and the rewrite
script's before-and-after counts. Fifty-one files are cheap to propose
and expensive to redo.

Exit (status as of CI run 38237936810 on `4efc3dcc2`, attempt 1; the
earlier run 38227709502 on `6bbfcb8a1` failed, see the Outcome):
* Met with a qualification. On both launcher entries (jobs
  114827495594, Debian 13, and 114827495719, Rocky 10),
  `tools/check-deployer-manifest` passed all four checks: all 36
  Kolla containers run `<name>@<digest>` matching the manifest, and
  `deploy-containers --tags common,cron` left all 36 container IDs
  unchanged. The qualification is the one in Decision 7: only
  `kolla_toolbox` and `cron` were re-checked, so "a second run
  recreates no container" is shown for those two and not for the
  other 34. Both image stores were covered (Debian `overlay2`, Rocky
  containerd).
* Met. All six venv entries are green (Debian 13, Rocky 10 with
  Debian images, Rocky-on-Rocky, Ubuntu 24.04, no-kerbside, and
  multinode), with no digest reference and 937 (2106 on multinode)
  tag-reference lines in each log, and no manifest warning. The
  Debian 13 entry passed on a rerun (attempt 2, job 114943204196)
  after attempt 1 failed in "Setup test environment" (see the
  Outcome).
* Met. Check 4 passed on both launcher entries: with
  `kolla_image_manifest_path=/nonexistent`, `gather-facts` succeeded
  and printed the warning exactly once. The script prints only its
  verdict, so the log does not show the text (as with the guards).
  No other entry printed it.
* Met with a qualification. The image jobs' post-push check printed
  "now carries the build manifest" for all three distributions, and
  check 1 found the label and a usable manifest on the image each
  launcher host had pinned. The manifest names 85 images (Debian and
  Ubuntu) and 81 (Rocky), which is the 86 and 82 the survey counted
  as pushed, less the deployer. The qualification is that the list
  comes from kolla-build's own `archive/images` (Decision 6,
  corrected), so an image that was pushed but missing from that file
  would not be noticed. The registry was not enumerated
  independently, because the GitLab API is not usable in CI.

#### Outcome

Written after CI run 38237936810 (pull request #1881, head
`4efc3dcc2`, attempt 1). Fourteen of its fifteen jobs passed at
attempt 1; the fifteenth was infrastructure (below) and passed on a
rerun. An earlier run, 38227709502 on
`6bbfcb8a1`, is used only for the first deviation.

*History.*
* `9f66f2cfa` closed out Phase 5 and `fe3a6d545` planned this phase.
  6a (`bcd75a230`, patch208) fixed digest references in the docker
  worker. 6b (`69b16aa8a`, patch209; `14fa90194`, patch210) added the
  lookup, the manifest rules and the warning, and converted the
  kerbside lines. 6c (`cc620fe35`) builds the manifest image; it was
  done in a side worktree in parallel with 6a and cherry-picked. 6d
  (`6bbfcb8a1`) added `tools/check-deployer-manifest`, and
  `4efc3dcc2` fixed the CI failure below.
* **The first CI run failed in 6c.** "Images for master on debian
  trixie" failed because `write-build-manifest` could not read the
  GitLab project (HTTP 503 after its retries), and in the same job
  `tools/find_images` printed "GitLab authentication failed". The
  registry token cannot be relied on for the GitLab REST API.
  `4efc3dcc2` lists images from `archive/images` instead (Decision 6
  is corrected). Every image job in the second run still prints
  "GitLab authentication failed" once, from `find_images`, which means
  the "skip the build if the images exist" path has never fired in CI
  and every run rebuilds. That is pre-existing and not fixed here
  (Future work).
* **The one red job in the second run is not this change.** "master
  debian 13 images on debian 13 all-in-one using debian containers"
  (114827495789, the Debian venv control) failed in "Setup test
  environment": the test cloud left the instance in `preflight` until
  the 596s timeout. Its rerun (attempt 2, job 114943204196) passed,
  with 937 tag references, no digest reference and no warning; its
  deploy step took 37m01s against Phase 5's 33m15s. The timings below
  were taken before the rerun and leave it out.
* The 6b back brief changed Decisions 2 to 4 (corrected in place):
  the lookup's inputs, the manifest keys, failing closed on a bad
  manifest, a tag-mismatch refusal, the filter plugin, and the tox
  and zuul changes. 152 upstream lines were rewritten in 50 files.

*The manifest.* `archive/manifest-<tag>.json` in each build artifact:

| Distribution | Tag | Images | Size |
|--------------|-----|--------|------|
| Debian trixie | `master-debian-trixie-edb991f6` | 85 | 8415 B |
| Ubuntu noble | `master-ubuntu-noble-edb991f6` | 85 | 8414 B |
| Rocky 10 | `master-rocky-10-edb991f6` | 81 | 8033 B |

All have `format_version` 1 and none lists `kolla-ansible` (the
deployer cannot name its own digest). A sample from the Debian
manifest:

```
{
  "format_version": 1,
  "images": {
    "base": "sha256:795c9ea322b36c5590c02116719bac9e92ba5a80b31f9ce64e1a1ad8c6e462be",
    "cron": "sha256:55a249fd7833e10c13fa84515a8a3e633bd5b6060cbc1f1761fcba81f89617f9",
    "kerbside-api": "sha256:3280fa53f1c7dab4848c943eb16b7245ecdd1634279fde01c02e563feb0e04fc",
    "kolla-toolbox": "sha256:2c61ac9a5b7287bb6a2354b9bb48a1d30a05cf89446008e66f08b0fdd0d8beb3",
    "nova-api": "sha256:95e204d02a4a49b98c293113e5c2778903f242636d4a826c0662b745394bbf8b",
    ...
  },
  "tag": "master-debian-trixie-edb991f6"
}
```

It holds the `-base` images, `grafana` and the like that are never
deployed, and both `nova-libvirt` and `nova-libvirt-spice`. It has no
`ironic-neutron-agent`, which is not built, so that disabled service
keeps a tag reference on the launcher hosts (five `skipping` log lines
name it); that is the designed fallback for a name the manifest lacks.

*The build step.* "Build manifest for <tag>" in
`_build/build-containers.sh`, section header to "now carries the build
manifest", from the log timestamps:

| Job | Duration | Digest reads | Images | Docker build | occystrap push |
|-----|----------|--------------|--------|--------------|----------------|
| Debian trixie | 56s | 6s | 85 | about 20s | 29.1s |
| Rocky 10 | 53s | 6s | 81 | about 20s | 27.0s |
| Ubuntu noble | 69s | 6s | 85 | about 25s | 37.6s |

The Debian job's "Build container images" step took 22m43s, so the
step is about 4% of it. The push moved no layer bytes ("Summary: 0
layers, 0 B ... verified OK"), because the layer cache already held
all of them; the time is the fetch from the daemon, the filters and the
manifest `PUT`. The first line of the section says
`kolla-ansible:<tag> is sha256:404980f..., without a manifest`, and the
last says the tag "now carries the build manifest". The two digests
differ, as Open question 4 predicted: the deployer's tag moves. The
launcher hosts pinned `kolla-ansible@sha256:941aa0e0...`, the
manifest-bearing image, with no launcher change, so bootstrap ran
after the move.

*The checks, on each launcher host.* Identical on both apart from
timing:

```
Check 1: the pinned deployer image carries the build manifest
  /etc/kolla/deployer.conf pins '.../kolla-ansible@sha256:941aa0e0...' (docker)
PASS: ... has the kolla_build_manifest label
  manifest tag master-debian-trixie-edb991f6 lists 85 images
PASS: ... has a usable /etc/kolla-ansible/manifest.json
Check 2: every Kolla container runs by digest from the manifest
  36 Kolla containers checked, 0 wrong
PASS: every Kolla container runs <name>@<digest> matching the manifest
Check 3: deploy-containers recreates nothing
PASS: deploy-containers --tags common,cron left all 36 container IDs unchanged
Check 4: a missing manifest warns once
PASS: gather-facts succeeded and warned about the missing manifest once
OK: the build manifest pins every image.
```

The script took 11s on Debian and 17s on Rocky. Check 3 itself took 7s
and 11s. The lookup therefore works end to end: 36 containers, none of
them wrong, on both hosts.

*The containerd image store question.* Answered by the clingwrap
bundles (`_commands/docker-info`), not by the job logs, which record
nothing about the store (the collection's "Determine if Docker uses
containerd image store" task runs but its result is not printed).
Both targets ran Docker 29.9.0:

| Host | `Storage Driver` | Store |
|------|------------------|-------|
| Debian 13 | `overlay2` | classic |
| Rocky 10 | `overlayfs`, `driver-type: io.containerd.snapshotter.v1` | containerd |

So Check 2 and Check 3 passed on both stores. 6a found that on the
containerd store the old `check_image` matched by accident, and that
the recreate-every-run bug is real on the classic store, which is the
Debian host here.

*The venv entries.* Five entries run the patched Kolla-Ansible with no
deployer image: Rocky 10 with Debian images (114827495724),
Rocky-on-Rocky (114827495672), Ubuntu 24.04 (114827495812),
no-kerbside (114827495430) and multinode (114827495725). Each log
has 937 lines naming `kolla-images/<name>:master-...` (2106 on
multinode), none naming `kolla-images/<name>@sha256`, no "has no
build manifest" warning, and nine "Warn about the deployer's build
manifest" tasks per run that all report `skipping`. The launcher
entry for comparison has 906 `@sha256` lines and 11 tag lines (the
disabled `ironic-neutron-agent`, and `bootstrap-kolla-ansible`'s own
`pin` of the `kolla-ansible` tag, which resolves it to a digest).

*Durations.* The "Deploy Kolla-Ansible" step, against Phase 5's table.

| Host | Entry | Job | Phase 6 | Phase 5 |
|------|-------|-----|---------|---------|
| debian 13 | launcher | 114827495594 | 25m10s | 24m06s |
| debian 13 | control | 114827495789 | failed (infra) | 33m15s |
| rocky 10 | launcher | 114827495719 | 36m26s | 36m39s |
| rocky 10 | control | 114827495724 | 36m36s | 27m11s |

Per action, in seconds (Phase 5 in brackets):

| Action | deb launcher | rocky launcher | rocky control |
|--------|--------------|----------------|---------------|
| Bootstrap | 92 (84) | 156 (156) | 660 (133) |
| Pre-checks | 52 (50) | 59 (63) | 52 (46) |
| Pull | 116 (102) | 121 (121) | 101 (91) |
| Deploy | 644 (618) | 975 (975) | 764 (750) |
| Validate config | 129 (125) | 186 (184) | 129 (125) |
| Reconfigure | 405 (401) | 594 (602) | 428 (419) |

The lookup's cost is not measurable. Deploy, the action that templates
the most, is within 4% of Phase 5 on the Debian launcher and equal on
the Rocky launcher, and the venv control moved by the same couple of
percent. The one outlier is the Rocky control's bootstrap, 660s
against 133s, which is a venv install; the lookup does not run in
bootstrap. As in Phases 3 to 5 the whole-step differences (Rocky
control 9m slower than Phase 5, Debian launcher 1m slower) are different
runners at different times, so I would not read anything into them. No
Debian venv control is available in this run for a same-run
comparison; the other Debian venv entries (no-kerbside 36m09s,
Ubuntu 35m05s) are different configurations and only indicative. The
unpack lines were `with docker ... in 3.9s, 1172 MiB` and `with podman
... in 7.3s, 1172 MiB`, against Phase 5's 3.3s and 5.9s at 1170 MiB.

*Anything that behaved differently from this section.*
* The listing comes from `archive/images`, not the GitLab API
  (Decision 6).
* The manifest's keys, the lookup's inputs, failing closed and the
  tag-mismatch refusal (Decisions 2 to 4).
* Check 3 uses `--tags common,cron` (Decision 7).
* `_build/test-apply.sh` can report "All patches applied correctly"
  while applying and testing nothing, if the worktree's
  `src/kolla-ansible` has a stale `master-patches` branch checked out.
  6b ran its tests in a fresh clone because of this.
* 6a found the deployer image installs no docker or podman library.
* The containerd store is on the Rocky target and not the Debian one.

*Not exercised by CI.* The "label already present, do nothing" path
of the build step (every tag here was new). The skip for a local
build and for a run that did not push `kolla-ansible`. The failure on
an unreadable digest. A concurrent push of one tag. kerbside's
downstream CI, which runs the same script. Switching a running
deployment from tags to digests, so the one-time recreate in the
release note is not demonstrated: CI deploys fresh. A manifest with a
mismatched tag or an unknown `format_version` through Ansible (they
are unit-tested in patch209). Digest references on podman as the
*target* engine: the Rocky launcher unpacked the image with podman,
but its targets' containers ran under Docker.

### Phase 7: Make the prototype entry voting

Planning effort: medium. It changes no deploy code. The judgement is
in what counts as evidence: what the measurement compares, and what
"green for a week" means when a daily rebase does not always run the
tests.

By now both prototype entries have been exercising each phase as
it landed. Take the measurement worth recording here: the time
and failure rate of bootstrap with and without run-time Galaxy
fetches, comparing each prototype entry with its control. Then
make the entries voting. Keep the venv entries: the old path
stays supported for as long as upstream supports it, and this
plan does not change that.

*Corrected at planning (see the survey below).* "A week of daily
rebases" cannot be counted as seven runs: a daily rebase runs these
tests only when it changes a path the workflow does not ignore, so
some days have no run at all. Decision 3 replaces it with a record
counted over all runs in a dated window. The phase lands as two
pull requests, because the voting change cannot be made before the
window closes.

**In scope:**
* a committed tool that measures bootstrap from CI job logs, and
  checks the launcher entries' record over a window;
* the measurement, recorded in this phase's Outcome, including
  where the launcher spends the time it saves in bootstrap; and
* removing the non-voting mechanism from the workflow once the
  record is met.

**Out of scope:**
* The venv entries. They stay, unchanged, as the controls and as the
  supported path.
* A launcher variant of the multinode entry. Phase 2 covered the
  remote case; it is not one of the two prototype entries.
* Adding Galaxy or registry errors to `auto-retry-infra-failures.yml`'s
  patterns. Voting makes that workflow see launcher failures for the
  first time (Risks); whether it then needs new patterns is a
  question for the retries the window actually produces, not a guess
  made now.
* The always-rebuild bug (#1885). It inflates every run's image
  builds, the launcher entries' no more than the controls'.

#### What the survey found

Surveyed on 2026-10-13 against this repository at `784e95d9e`
(develop after #1881), using the jobs of the 38 "Build test clouds"
runs since 2026-10-03 and the logs of run 38237936810, attempt 1.

* **Non-voting is two matrix keys and one job key.**
  `functional-tests.yml:487` and `:513` set `'non_voting': 'true'` on
  `master-h-debian13-c-debian13-aio-launcher` and
  `master-h-rocky10-c-debian-13-aio-launcher`, and `:557` sets
  `continue-on-error: ${{ matrix.test.non_voting == 'true' }}` on
  `test_installs`, with a comment saying this phase removes it. No
  other entry uses the key.
* **"Voting" means the run's conclusion, and nothing else.**
  `develop` has no branch protection and no repository rulesets
  (`gh api .../branches/develop/protection` is 404; `rulesets` is
  empty). Daily rebases and phase pull requests are merged by hand.
  The one automated consumer of the run's conclusion is
  `auto-retry-infra-failures.yml`, which fires on
  `workflow_run.conclusion == 'failure'`. Today a launcher-only
  failure leaves the run green, so it is never retried; once voting,
  it is.
* **Daily rebases do not always run the tests.** The workflow has
  `paths-ignore`, and #1887 (20261012) changed one ignored file, so
  it ran only lint, CodeQL, secret scanning and rebase tests. Of the
  rebases since 2026-10-03, those for 10-03, 10-05, 10-06, 10-07,
  10-10 and 10-11 ran this workflow; 10-04, 10-08, 10-09 and 10-12
  did not.
* **The record so far is clean off the phase branches.** Every
  launcher failure since 2026-10-03 is on a `deployer-phase-*`
  branch, during development: four Rocky failures in Phase 3 (the
  `nsenter` work) and both entries once in Phase 5 (the guard bug).
  On every other branch, six daily rebases and eight other pull
  requests, each launcher job passed, except one Rocky job cancelled
  by its own superseded run (38169235756).
* **The Galaxy cost is large and visible in the logs.**
  `tools/bootstrap-kolla-ansible` prints a header before each
  section, and the job log timestamps every line. For the venv path,
  `ansible-galaxy collection install ansible.posix --upgrade` and
  `kolla-ansible install-deps` are the run-time Galaxy fetches. In
  run 38237936810, attempt 1:

  | Job (venv) | Bootstrap | `ansible.posix` | `install-deps` |
  |---|---|---|---|
  | Debian 13 images on Rocky 10 | 617s | 85s | 492s |
  | Ubuntu images on Debian 13 | 708s | 61s | 616s |
  | Debian 13 multinode | 804s | 96s | 667s |
  | Rocky on Rocky 10 | 741s | 72s | 631s |
  | Debian 13, no kerbside | 745s | 107s | 608s |

  The same section took 10s on the Debian launcher entry and 39s on
  the Rocky one, which install the launcher and an engine, and fetch
  nothing from Galaxy. `install-deps` downloads seven collections,
  so most of its eight to eleven minutes is Galaxy's API, not the
  bytes.

  *Corrected in 7b:* that table is one run, at the long tail. Over 13
  pairs `install-deps` is a median of 107s (Debian) and 131s (Rocky),
  range 42 to 493s, and the venv bootstrap as a whole 231s and 239s.
* **But the deploy step is not ten minutes shorter.** Across the
  runs since 2026-10-10, the launcher entries' "Deploy Kolla-Ansible"
  step took 24-38 minutes and the controls' 26-43. So the launcher
  spends most of what it saves somewhere else in that step, most
  likely the deployer image's pull and unpack and the chroot
  `bootstrap-servers`. The measurement has to find where, or the
  number it reports is misleading.

  *Corrected in 7b:* the saving in bootstrap is a median of 132s on
  Debian and 95s on Rocky, not ten minutes, so there is far less to
  find. The deployer image's pull and unpack are 18 to 27s and
  `bootstrap-servers` is 9 to 30s slower; the rest of the step's
  variation is run-to-run noise (Outcome).
* **No Galaxy failure in our own CI was found in this sample.**
  Upstream's Zuul has one (the `vexxhost-ca-ymq-1` "Failed to install
  Ansible collections" flake), but it is specific to that provider's
  IPv6. Whether ours ever fails is for 7a to count over the 90 days
  of logs GitHub keeps.

The master plan's own Phase 7 text above is corrected in place, in
the italic note, rather than rewritten; nothing else in it was
wrong.

#### Decisions

1. **The measurement is a committed tool, not a one-off script.**
   `tools/measure-deployer-bootstrap` reads job logs through `gh`,
   finds the bootstrap sections by their headers and timestamps, and
   prints a table. Phase 8's proposal will quote these numbers, and
   the first question a reviewer upstream asks is how they were
   produced. A committed tool, with its parsing unit tested on log
   fixtures, answers that; a scratchpad script does not survive the
   session.

2. **Compare each launcher entry with its own control, in the same
   run and attempt.** Debian 13 launcher against
   `master-h-debian13-c-debian13-aio`, and Rocky 10 launcher against
   `master-h-rocky10-c-debian-13-aio` (Debian images on a Rocky
   host, the same images as the launcher's). Pairing within a run
   holds the images, the cluster's load and Galaxy's
   responsiveness roughly constant. Durations are reported as
   medians and ranges over runs since
   2026-10-10 (Phase 4's `af2474d4f`, after which the launcher's
   bootstrap took its final shape), and labelled as noisy, as
   Phases 3 to 6 did. The failure count uses every retained venv
   job, all 90 days, because it does not depend on the launcher
   existing.

3. **"Green for a week" means a dated window, counted over all
   runs.** The window opens at #1881's merge (2026-10-13 10:13Z),
   because Phase 6 changed how the launcher entries pull every
   image; it closes seven days later. Every launcher job in it, on
   any branch except `deployer-phase-*`, must pass, or fail for a
   reason the run shows is not the launcher's: its control failed
   the same step in the same attempt, or it failed before the deploy
   in **Setup test environment** (cluster capacity, as on #1881).
   There must be at least five such runs of each entry, and at
   least two daily rebases among them. The tool's `record`
   subcommand prints this, so the check is run rather than read.

   *Amended 2026-10-16:* the window was closed early, at 2026-10-15
   22:00Z. `record` already printed MET with ten passing runs of each
   entry, two of them daily rebases, and no failure attributed to the
   launcher, twice the bar on every count. Michael chose to close it
   rather than wait four more days. What the shorter window does not
   cover is the daily rebases of 10-16 to 10-20; once the entries
   vote, a regression there fails the run instead of passing
   unnoticed, which is the point of voting.

4. **Two pull requests.** The first lands the tool and the
   measurement now (7a, 7b). The second, on a new branch after the
   window closes, removes the non-voting mechanism and records the
   window (7c, 7d). Holding one pull request open for a week would
   leave the measurement unmerged, and nothing in it depends on the
   window.

5. **Remove the mechanism, not just the two keys.** 7c deletes the
   `non_voting` keys and the `continue-on-error` line and its
   comment. No other entry uses them, and a future prototype can add
   two lines back. A key with nothing setting it is a puzzle for the
   next reader.

6. **If the record is not met, do not vote.** A launcher-attributable
   failure in the window restarts the window from that failure's
   fix, and 7c waits. The management session reports it and does
   not relax Decision 3's rules to fit.

#### Risks

* **Voting makes the auto-retry workflow see launcher failures.** A
  launcher-only infrastructure failure, a deployer image pull that
  times out, say, now turns the run red and is retried only if it
  matches an existing pattern. The window shows how often that
  happens, because its failures are classified; 7d records any that
  needed a manual rerun, and a pattern is added then if one recurs.
* **The pairing hides the image pull.** Comparing only the bootstrap
  section would credit the launcher with ten minutes it then spends
  pulling the deployer image. 7a must report the whole "Deploy
  Kolla-Ansible" step beside the sections, and 7b must account for
  the difference. The management session checks the sum.
* **Log formats move.** The section headers come from
  `tools/bootstrap-kolla-ansible`, and the launcher's own output from
  `deployer/launcher`. A renamed header would make the tool report
  nothing rather than fail. The tool must fail when a job it was
  asked about has none of the expected headers.
* **Ninety-day retention.** Logs older than that are gone; the
  failure count says which window it covers.
* **Few runs.** If the window holds fewer than five runs of an
  entry, Decision 3 is not met. The fix is to wait, or to ask
  Michael for a manual dispatch of the workflow; it is not to count
  a phase branch.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | Write `tools/measure-deployer-bootstrap` (Python, single quotes, 120 columns, `click`, shelling out to `gh api` for job lists and `gh api repos/OWNER/REPO/actions/runs/RUN/attempts/N/logs` for attempt-specific logs: `gh run view --job` returns the latest attempt's log, not the one asked for). Two subcommands. `timings --since DATE`: for each "Build test clouds" run and attempt since DATE with both a launcher entry and its control (Decision 2 of Phase 7 in `docs/plans/containerised-deployer.md` names the pairs), print per job the "Deploy Kolla-Ansible" step duration from the jobs API and these sections from the log, measured from header to next header: for venv jobs the `tools/bootstrap-kolla-ansible` headers "Bootstrap Kolla-Ansible into a venv", "Upgrade the POSIX ansible collection", "Install patched Kolla-Ansible", "Install Kolla-Ansible dependencies", "Bootstrap inventory for topology"; for launcher jobs "Install the launcher into its own venv", "Install a container engine if there is none", and the launcher's own report of the deployer image pull and unpack (find its text in `deployer/launcher`). Then medians and ranges per entry. Strip ANSI codes; a log line is `<ISO timestamp> <text>`. Fail, naming the job, when an asked-for job has none of the headers. `galaxy-failures --since DATE`: count venv jobs whose log contains Galaxy failure text ("Failed to install Ansible collections", "ERROR! Failed to", HTTP errors from galaxy.ansible.com; read `kolla-ansible install-deps`' and `ansible-galaxy`'s failure output to choose), over all runs since DATE. `record --since DATE --until DATE`: Decision 3's rules, printing each launcher job with its verdict and why, the per-entry counts, and MET or NOT MET. Unit tests in `tools/test-measure-deployer-bootstrap.py` on small log fixtures (a venv log, a launcher log, a log missing its headers, a Setup test environment failure) and on the record rules, with no network; wire them and flake8 into `.pre-commit-config.yaml` beside `write-build-manifest`'s hooks, and add the tool to the shellcheck hook's exclude list. Document it in `docs/script-reference.md`. Commit subject: "Measure deployer bootstrap from CI logs." |
| 7b | medium | sonnet | none | Run 7a's tool: `timings --since 2026-10-10`, `galaxy-failures` over all retained runs, and `record --since '2026-10-13T10:13Z'` as it stands. Add the first part of Phase 7's Outcome to `docs/plans/containerised-deployer.md`: the paired table (medians and ranges per entry and section, and the whole deploy step), the Galaxy failure count and the window it covers, and an account of where the launcher spends the bootstrap time it saves, with the sections that add up to the deploy-step difference. Label durations as noisy, as Phases 3-6 did. Say what the numbers would and would not support in Phase 8's proposal. Add a short "Bootstrap cost" paragraph to `docs/deployer-launcher.md` with the headline numbers and the command that produced them. Commit subject: "Record what bootstrap costs with and without Galaxy." |
| 7c | low | sonnet | none | On a new branch from develop, after 2026-10-20 10:13Z: run `tools/measure-deployer-bootstrap record --since '2026-10-13T10:13Z' --until '2026-10-20T10:13Z'`. If it prints NOT MET, stop and report each failing job; change nothing. If MET, delete both `'non_voting': 'true'` keys and the `continue-on-error` line with its two-line comment from `.github/workflows/functional-tests.yml` (and the trailing comma the keys leave behind, so the matrix stays valid JSON), and confirm with `actionlint` and the repo's pre-commit. Say in `docs/deployer-launcher.md` that the launcher entries vote. Commit subject: "Make the containerised deployer entries vote." |
| 7d | low | sonnet | none | After 7c's CI run, finish Phase 7's Outcome: the `record` output for the window, any failure it classified as not the launcher's and why, any run that needed a manual rerun (and whether a retry pattern is now warranted), and 7c's own run with both entries voting. Annotate the exit criteria. Commit subject: "Record the launcher entries' week." |

Order: 7a, then 7b, pushed together as the first pull request. 7c
and 7d, on a new branch after the window closes, are the second. The
management session reviews each step before its commit, and checks
7b's account against the deploy-step totals.

**Back brief gate on 7a.** Before writing the tool, 7a's sub-agent
reports the launcher's pull-and-unpack text it will parse, where in
the log the deployer image is first pulled, and the Galaxy failure
strings it chose and why. The tool is cheap to write and its numbers
are expensive to retract once Phase 8 quotes them.

Exit:
* `tools/measure-deployer-bootstrap timings` reproduces the survey's
  table for run 38237936810 within a second per section, and its
  unit tests run in pre-commit.
  Met, for the one job the tool pairs. The Debian 13 venv control on
  Rocky 10 comes out at 617.4s from first header to inventory, with
  `ansible.posix` 85.0s and `install-deps` 492.6s, against the survey's
  617, 85 and 492. The survey's other four venv rows have no launcher
  pair, so `timings` does not print them. The unit tests are a
  pre-commit hook (`measure-deployer-bootstrap-tests`).
* Phase 7's Outcome accounts for the launcher's whole deploy step,
  not only its bootstrap section.
  Met, with a limit. The Outcome's table of mean differences adds up
  to the step on both hosts (-173 and -49 make -222 on Debian; -148 and
  +213 make +65 on Rocky). What it cannot do is explain the part after
  bootstrap: 25 to 75s of it is small consistent per-call costs, and
  the rest is within the noise of 13 pairs a side, with a Rocky excess
  (+213s mean) at about two standard errors.
* `record` for 2026-10-13 10:13Z to 2026-10-20 10:13Z prints MET.
  Met, over the shortened window the amendment to Decision 3 allows
  (2026-10-13 10:13Z to 2026-10-15 22:00Z). Run on 2026-10-14 it printed
  "0 runs, 0 launcher jobs" and NOT MET. Run on 2026-10-16 it prints MET,
  with 11 passing runs of each entry, 3 of them daily rebases, and no
  failure attributed to the launcher. The window was not run to
  2026-10-20; the Outcome says what that leaves uncovered.
* `.github/workflows/functional-tests.yml` has no `non_voting` and no
  `continue-on-error`, and the first run after 7c lands is green
  with both launcher entries counting.
  Met. The file has neither key and no `continue-on-error`. Run
  38820775821 on #1919 is the first run with both launcher entries
  counting, and both passed (the Debian 13 entry in attempt 1, the Rocky
  10 entry in attempt 2). The run went green in attempt 3, after
  capacity misses on jobs that do not use the launcher; see the Outcome.

#### Outcome

Written after 7a (`453dd6e3f`, the tool) and its fix (`472f5b1a3`), as part
one: the measurement. 7d adds the window. Everything below was measured on
2026-10-14 with `tools/measure-deployer-bootstrap`, from the job logs
GitHub still holds. Durations are noisy, and Phases 3 to 6 said so for the
same reason: they depend on the cluster's load and on Galaxy's
responsiveness, and the two jobs of a pair run on different runners,
usually at different times.

*What was measured.* `tools/measure-deployer-bootstrap timings --since
2026-10-10`: 13 runs of "Build test clouds", created 2026-10-10 01:58Z to
2026-10-13 07:43Z, 14 run attempts, giving 26 pairs, 13 of the Debian 13
launcher against `master-h-debian13-c-debian13-aio` and 13 of the Rocky 10
launcher against `master-h-rocky10-c-debian-13-aio`. A launcher job and a
control that were carried over from an earlier attempt are not measured
twice, and a pair with one side carried over is skipped with a note on
stderr. Nine of the pairs (four Debian, five Rocky) come from
`deployer-phase-*` branches. Leaving them out leaves 9 and 8 pairs, and
does not change the picture: Debian's medians are the same (1583s against
1866s for the step, and no difference after bootstrap), and Rocky's median
per-pair difference after bootstrap is +135s against +144s with them. The
tables use all 26.

Medians and ranges, in seconds. "Diff" is the difference of the two
medians; the per-pair differences used further down are a different
quantity and are labelled as such.

| Debian 13 host | Launcher | Control | Diff |
|----------------|---------|---------|------|
| Deploy Kolla-Ansible step | 1583 (1446-2059) | 1866 (1549-2471) | -283 |
| bootstrap sub-step | 99 (82-137) | 231 (152-606) | -132 |
| - first header to inventory | 12 (2.4-19) | 155 (88-535) | -143 |
| - install the launcher | 2.4 (2.2-4.1) | - |  |
| - install a container engine | 9.1 (0.0-16) | - |  |
| - make venv | - | 1.5 (1.1-1.8) |  |
| - upgrade ansible.posix | - | 9.7 (6.0-83) |  |
| - install patched Kolla-Ansible | - | 6.8 (5.6-9.7) |  |
| - kolla-ansible install-deps | - | 107 (42-407) |  |
| - deployer image pull (pin as root) | 15 (8.2-26) | - |  |
| - certificates | 17 (16-23) | 16 (15-21) | +1.0 |
| - bootstrap-servers | 51 (41-70) | 42 (31-53) | +9.1 |
| - deployer image unpack | 2.9 (1.9-8.4) | - |  |
| - deployer image pull (pin as user) | 0.2 (0.2-8.4) | - |  |
| prechecks | 51 (50-64) | 46 (44-57) | +5.4 |
| pull images | 121 (84-161) | 112 (86-158) | +8.4 |
| deploy | 700 (618-888) | 738 (619-889) | -38 |
| validate-config | 134 (123-176) | 136 (110-203) | -2.3 |
| check | 15 (14-22) | 16 (13-22) | -1.0 |
| reconfigure | 413 (395-541) | 418 (396-648) | -4.3 |
| install clients | 13 (11-19) | 14 (11-17) | -1.8 |
| post-install | 40 (37-57) | 38 (35-52) | +1.7 |
| all sub-steps after bootstrap | 1468 (1362-1923) | 1465 (1322-2011) | +3.0 |

| Rocky 10 host | Launcher | Control | Diff |
|---------------|---------|---------|------|
| Deploy Kolla-Ansible step | 1875 (1675-2466) | 1841 (1593-2563) | +34 |
| bootstrap sub-step | 144 (83-190) | 239 (130-660) | -95 |
| - first header to inventory | 36 (5.5-51) | 184 (87-617) | -147 |
| - install the launcher | 3.4 (2.8-4.8) | - |  |
| - install a container engine | 31 (0.0-44) | - |  |
| - make venv | - | 1.5 (1.4-2.2) |  |
| - upgrade ansible.posix | - | 13 (4.7-85) |  |
| - install patched Kolla-Ansible | - | 7.5 (6.5-10) |  |
| - kolla-ansible install-deps | - | 131 (46-493) |  |
| - deployer image pull (pin as root) | 11 (7.1-25) | - |  |
| - certificates | 19 (16-23) | 16 (16-24) | +3.1 |
| - bootstrap-servers | 54 (36-67) | 25 (20-32) | +30 |
| - deployer image unpack | 5.3 (3.2-9.1) | - |  |
| - deployer image pull (pin as user) | 11 (8.3-18) | - |  |
| prechecks | 54 (51-66) | 48 (45-59) | +6.9 |
| pull images | 116 (88-148) | 100 (83-156) | +15 |
| deploy | 869 (791-1150) | 762 (691-965) | +107 |
| validate-config | 159 (136-248) | 129 (119-177) | +30 |
| check | 18 (15-26) | 14 (13-19) | +4.7 |
| reconfigure | 487 (431-636) | 439 (419-614) | +48 |
| install clients | 18 (13-20) | 15 (12-20) | +3.4 |
| post-install | 43 (38-58) | 39 (36-56) | +4.5 |
| all sub-steps after bootstrap | 1734 (1567-2316) | 1534 (1459-2012) | +200 |

(The rows with a leading "-" are parts of the bootstrap sub-step. On the
venv side, "first header to inventory" is the sum of "make venv",
`ansible.posix`, the patched Kolla-Ansible and `install-deps`. On the
launcher side it is the sum of "install the launcher" and "install a
container engine"; the pulls, the unpack, "certificates" and
"bootstrap-servers" all run after it. Medians do not add up exactly.)

*The headline is smaller than the survey's.* The survey's ten minutes came
from one run. Across 26 pairs the venv bootstrap's median is 231s (Debian)
and 239s (Rocky), of which `kolla-ansible install-deps` is 107s and 131s,
and the launcher's bootstrap sub-step is 99s and 144s. So the launcher
saves a median of about 130s on Debian and 95s on Rocky in bootstrap (the
median per-pair saving is 143s and 75s), not 500 to 600s. The Galaxy cost
is a long tail, not a constant: `install-deps` ranges from 42s to 493s, and
the survey's run sat near the top of that range. A phrase like "saves eight
to eleven minutes" must not appear in Phase 8.

*Where the saved time goes.* Over the whole deploy step the launcher saved
283s of median on Debian and cost 34s on Rocky. Neither can be told from
zero: the per-pair step differences have a standard deviation of 383s
(Debian) and 429s (Rocky), and range from -947s to +777s. Means of the
per-pair differences are additive, so these add up exactly (standard errors
are about 106s and 119s for the step):

| Mean of 13 pair differences | Debian | Rocky |
|-----------------------------|--------|-------|
| Deploy Kolla-Ansible step | -222 | +65 |
| = bootstrap sub-step | -173 | -148 |
| + all sub-steps after bootstrap | -49 | +213 |
| of which prechecks, check, clients, post-install | +6 | +16 |
| of which pull images, validate-config | +12 | +36 |
| of which deploy, reconfigure | -66 | +162 |

Inside the bootstrap sub-step the launcher also gives some time back:
`bootstrap-servers` is +11s (Debian) and +28s (Rocky, mean) slower than the
control's, because it includes the deployer image's pull and unpack and the
chroot. The Rocky launcher's engine install (median 31s, podman) is dearer
than Debian's (9s, docker), and Rocky pulls the deployer image twice (11s
as root, 11s as user) where Debian pulls it once (15s). The pull and unpack
together are about 27s on Rocky and 18s on Debian, so the survey's guess
that they explain the missing time is true of a few seconds, not minutes.

*Is the slowdown after bootstrap systematic?* In part. The small
costs are; the large one on Rocky is not established.

* **Small, consistent costs.** `bootstrap-servers` was slower than its
  control in 22 of 26 pairs (13 of 13 on Rocky). `prechecks` was slower in
  19 of 26, by 4 to 6s in the mean. On Rocky, `validate-config` (+30s, 10
  of 13) and `check` (+4s, 10 of 13) are slower too; on Debian they are
  not. Adding the steps that lean the same way gives about 25s on Debian
  and 75s on Rocky. A per-call cost of about two seconds fits the short
  ones: in run 38237936810 attempt 1, the Rocky launcher's seven calls took
  3.2 to 4.6s from the step starting to the first Ansible play, against 1.4
  to 2.8s for the control, which is about two seconds more per call and
  under a second more at the end. That is 15 to 20s over seven calls, from
  one run, not a measurement of the overhead.

* **The long steps on Rocky: borderline, and not separable from noise.**
  Deploy and reconfigure are 80% of the time after bootstrap. On Rocky the
  launcher is slower after bootstrap in 10 of 13 pairs (a two-sided sign
  test gives p of about 0.09), by a mean of +213s with a standard error of
  100s, which is 2.1 standard errors; deploy alone is +108s (2.5 standard
  errors, 10 of 13) and reconfigure +53s (1.5). The thirteenth pair (+782s)
  moved the whole-step figure from 1.8 to 2.1 standard errors, and I looked
  at about a dozen sub-steps, so one at 2.5 is less than it seems. On
  Debian the same measure is -49s with a standard error of 91s (6 of 13
  slower) and shows nothing. The per-pair deploy and reconfigure
  differences move together (correlation 0.96 on Rocky, 0.93 on Debian): a
  pair is slower or faster as a whole, which is what a shared cause such as
  host load looks like and not what a fixed per-call cost looks like. The
  two jobs of a pair ran at overlapping times in only 7 of the first 24
  pairs, and the gap between their starts does not predict the difference,
  so "load differed" can be neither confirmed nor excluded. A Rocky penalty
  of 100 to 200s is plausible and now at the edge of what 13 pairs can
  show; it is not established, and Phase 8 should not state it as a fact
  about the launcher.

* **The 494s of the 7a back brief is one draw from a wide distribution.**
  Run 38237936810 attempt 1 (deploy +211s, reconfigure +167s,
  validate-config +57s) is one of five Rocky pairs between +490s and +780s.
  Three other Rocky pairs are between -260s and -310s, and the remaining
  five between +80s and +170s. Debian swings as far the other way: five
  pairs between -295s and -470s, two between +465s and +505s. A swing of
  300 to 600s in either direction is ordinary for this measure, which is
  what "noisy" means here.

*Podman re-resolving the digest on every call.* Not supported. The Rocky
launcher log of that run has five `Trying to pull ...` lines, all inside
bootstrap, between 13:35:57 and 13:36:38: the tag pin as root, then digest
pulls for the pin as user and for the calls that generate passwords, make
certificates and run `bootstrap-servers`. There is none in the seven
launcher calls of the next 34 minutes (prechecks, pull, deploy,
validate-config, check, reconfigure, post-deploy). A digest pull whose
layers are present took 0.17s in the one case that could be timed
(13:36:12.19 to .36). So the registry is not where the per-call cost above
comes from, and the Rocky symptom 7a saw is a bootstrap one; nothing is
added to Future work for it.

*Galaxy failures.* `tools/measure-deployer-bootstrap galaxy-failures
--since 2026-07-16` counted 982 venv jobs whose bootstrap ran, in runs
created since 2026-07-16. The oldest retained log is 2026-07-16T21:02Z, so
this covers all of GitHub's 90 days: 2026-07-16 to 2026-10-13. Results:

| Outcome | Jobs | Where |
|---------|------|-------|
| Failed the job | 1 | run 32833757983 attempt 3 (a daily rebase, 2026-08-26), Debian 13 images on Debian 12 multinode. See below |
| Recovered by kolla-ansible's retry | 3 | run 33791915624 (a daily rebase, multinode), `The read operation timed out`; runs 36841853973 (deployer-phase-1, Rocky) and 38299246253 attempt 2 (Debian 13), both `KeyError: 'results'` |

That is four of 982 (0.4%), one of which failed a job. The launcher entries
cannot suffer any of it. "The old path is unreliable" is not what this data
says: the cost it shows is minutes, in a fraction of a percent of jobs.

The one job that failed did so because of the line the tool matched. In
"Upgrade the POSIX ansible collection", which is a bare `ansible-galaxy
collection install ansible.posix --upgrade` with no retry around it
(kolla-ansible's retry covers only `install-deps`), ansible-galaxy logged
`[WARNING]: Skipping Galaxy server https://galaxy.ansible.com. Got an
unexpected error when getting available versions of collection
ansible.posix`, then `[ERROR]: Unknown error when attempting to call Galaxy
at 'https://galaxy.ansible.com/api/': <urlopen error [Errno -3] Temporary
failure in name resolution>`, and `bootstrap-kolla-ansible` printed `***
Failed ***` and exited 1, 36.5s into the action. Every later step of the
job (pre-checks to post-install) shows as skipped. So the job ended there,
on a name that did not resolve from the test host, which is the host's
resolver and not Galaxy; what turned it into a failed job is that the venv
path fetches from the network at run time.

The `KeyError: 'results'` in run 38299246253 attempt 2 was a failure that
kolla-ansible retried, and the log shows it. In `kolla-ansible
install-deps` the first `ansible-galaxy collection install` of
`requirements-core.yml` ran from 15:01:22 to 15:04:46 (205s), and then
ansible-core crashed in `_call_galaxy` on `path_cache['results']`: Galaxy
returned a response for `ansible.utils` with no `results` key (from Galaxy
or from ansible's own cache of it; the log does not say which), which
ansible-core did not handle and reported as "Unexpected Exception, this is
probably a bug: 'results'". Kolla-ansible then printed `Failed to install
Ansible collections from ... requirements-core.yml using Ansible Galaxy
(error: ... exit status 250.) (retry: 1/5)`. The second attempt finished at
15:07:11, installing all seven collections, and the job went on to pass. So
`install-deps` took 351s (15:01:20 to 15:07:11) against a median of 107s on
Debian. The 7a back brief described this log as having "no kolla retry
line"; that was wrong, and the tool counts it as recovered because of that
line, which is right.

*What these numbers would and would not support in Phase 8.*

* They support: the launcher removes `ansible-galaxy` and `kolla-ansible
  install-deps` from bootstrap, which is about two minutes at the median
  (107s and 131s for `install-deps`) and up to eight at the top of the
  range; and removes a failure mode that is rare in our CI (0.4% of jobs,
  one job failed in 90 days) but real. Upstream's Zuul has a Galaxy flake
  specific to one provider (`vexxhost-ca-ymq-1`); this data says nothing
  about it.

* They do not support: a claim that a launcher deploy is faster. The whole
  step is within noise of its control on both hosts, and Phase 8 should say
  the bootstrap section is shorter and say no more about the step. They do
  not support a claim about the launcher's per-call overhead beyond "a few
  seconds per call, from one run", or a claim that the Rocky launcher is
  slower: that is about two standard errors, from 13 pairs, with runners
  that differ between the jobs of a pair.

* The numbers are from this CI's runners and registry (a cluster of
  test-cloud VMs, a private GitLab registry for the images, a devpi mirror
  for Python). A reviewer upstream will run on neither.

*The record.* `record --since '2026-10-13T10:13Z' --until
'2026-10-20T10:13Z'` run on 2026-10-14 prints "0 runs, 0 launcher jobs",
"No launcher jobs in the window" and `NOT MET`, exit 1. That is expected:
no run of the workflow has been created since #1881 merged. 7d records the
window when it closes.

#### The voting window (7c, 7d)

Written after the window closed and 7c's run. The numbers below are from
2026-10-16.

*The record.* `tools/measure-deployer-bootstrap record --since
'2026-10-13T10:13Z' --until '2026-10-15T22:00Z'`, run on 2026-10-16,
covered 12 runs and 26 launcher jobs, with `deployer-phase-*` branches
left out. It prints MET:

| Entry | Passing runs (need 5) | Daily rebases (need 2) | Failures |
|-------|-----------------------|------------------------|----------|
| Debian 13 launcher | 11 | 3 | 0 |
| Rocky 10 launcher | 11 | 3 | 0 |

An earlier run of the tool, with run 38792591006 still in progress,
printed 25 jobs and 10 passing runs of each entry with 2 daily rebases.
That run has since completed: its Debian 13 job passed, and its Rocky job
failed in Setup test environment in attempt 1 and passed in attempt 2. So
each count rose by one, the daily rebases rose from 2 to 3, and the
ignored jobs are as before plus that Rocky attempt. Nothing else changed.

*What the record ignored.* Two jobs, both Rocky 10 launcher entries and
both before the deploy:

| Run | Attempt | Branch | Why ignored |
|-----|---------|--------|-------------|
| 38647688356 | 1 | host-virtlogd | Failed in Setup test environment, before the deploy; passed in attempt 2 |
| 38792591006 | 1 | daily-rebase-20261015 | Failed in Setup test environment, before the deploy; passed in attempt 2 |

Decision 3 allows this because the launcher never ran. The tool
classifies by the step that failed, not by the message; the logs of both
jobs show the cause was capacity, sfcbr answering 507 "No nodes remaining
at scheduling stage sufficient_idle_memory" because no host had room for
the test cloud VM. No
job in the window failed in the deploy or later, so none was classified
as the launcher's, and none needed its control to have failed the same
step.

*Manual reruns, and whether a retry pattern is warranted.* One run needed
a manual rerun that the workflow should have done itself. Run
38478931745 (#1896, Phase 7's first pull request) had a Debian 13
no-kerbside job hit the sfcbr 507 on 2026-10-14; the management session
reran it at Michael's request.
That job is not a launcher entry, so `record` does not see it.

The pattern for that 507 already existed: #1884 added it to
auto-retry-infra-failures. It never matched, and the reason was not the
pattern. Current `gh api` refuses to print a job log that contains
terminal escape sequences, and the workflow discarded stderr, so every
pattern that matched on log text rather than on an annotation had silently
never matched. In the 200 auto-retry runs back to 2026-07-16, every match
was an annotation pattern. #1902 (merged 2026-10-14) added
`--allow-escape-sequences`. So no new pattern is warranted: the existing
one works now, and 7c's run is the first evidence that it does (below).
It also means the retry rate before 2026-10-14 understates how often a
log-text infrastructure failure happened; those were not retried.

*7c's own run.* Run 38820775821 on #1919 removed `non_voting` and
`continue-on-error`, so both launcher entries counted for the first time.
The run sat queued for several hours before any test cloud started: VM
runners were not being handed out to any repo run at the time, from about
2026-10-15 19:00Z to about 2026-10-16 03:00Z. That is a wait, not a
failure, and it is recorded so that the run's elapsed time is not read as
the cost of the change.

* Attempt 1. The Debian 13 launcher entry passed, as a voting job. Three
  Rocky 10 host jobs failed in Setup test environment with the sfcbr 507:
  the Rocky launcher entry, its venv control, and the Rocky-containers
  entry. Auto-retry started attempt 2 on its own. This is the first time
  it has retried on a log-only pattern, so #1902 is confirmed working in
  practice and not only in review.
* Attempt 2. The Rocky launcher entry and its control passed. The
  Rocky-containers entry, which is not a launcher entry, hit the 507
  again.

So both launcher entries passed as voting jobs, and the one job keeping
the run from green is a different entry hitting capacity, not the
launcher.

* Attempt 3. Auto-retry does not retry the same run twice, so the
  Rocky-containers job was rerun at Michael's request. It passed, and the
  run concluded green, with both launcher entries voting.

Phase 7 is done: the launcher entries vote, and the only failures in
their first voting run were capacity misses before the deploy, which
auto-retry and one manual rerun absorbed.

*What the shorter window does not cover.* Decision 3's amendment already
says it: the daily rebases of 2026-10-16 to 2026-10-20. A regression there
now fails the run. The record is eleven passing runs of each entry, three
of them daily rebases, over two and a half days; it is not seven days.

### Phase 8: Documentation and upstream proposal

Planning effort: medium. Review effort: high for 8d, because the
proposal is the one artefact of this plan that leaves the repository,
and a number quoted wrongly in it is expensive to retract.

Phase 1's patch199 and Phase 6's patch209 were expected to need a
rebase before they could apply to pristine upstream. *Corrected at
planning (see the survey below):* neither does. Upstream Kolla now
carries `kerbside-base` itself, and this repository applies patches
with `git apply -C1`, under which patch209's `nova-cell` hunk applies
to pristine Kolla-Ansible and produces the upstream line. What is
left is to prove that continuously, as two series directories, and
to write things down.

Raise these `community.docker.nsenter` problems found in Phase 3:
the plugin assumes `remote_user` is root, and it leaks the caller's
environment (`PATH`, `HOME`, `KOLLA_*`) to commands on the host. An
upstream all-in-one inventory for the container would need the
points in `docs/deployer-launcher.md`: a deploy host not named
`localhost`, an absolute `ansible_remote_tmp`, and a Docker SDK in
the Python the deploy will discover, which the inventory cannot pin.

Write `docs/containerised-deployer.md`: how to build and run the
deployer, and what the prototype learned, including the cases
that did not work. Add it to the index in `AGENTS.md` only if a
convention changed. Draft the upstream proposal: a mailing-list
post or PTG topic that links the demo, what we measured, and the
series of changes it would take, saying which repository each
change belongs in. This is prose here, not a Gerrit push. Pushing
is a separate decision.

**In scope:**
* the upstream all-in-one inventory for the container, as a
  Kolla-Ansible patch (Phase 3, Decision 5 deferred it here);
* two series directories, `kolla-deployer/` and
  `kolla-ansible-deployer/`, that apply this plan's upstream-bound
  patches to pristine upstream and run upstream's tests, without
  pushing anything;
* `docs/containerised-deployer.md`; and
* the proposal, with the community.docker issue and the Zuul publish
  job change drafted inside it.

**Out of scope:**
* Pushing to Gerrit, posting to openstack-discuss, or filing on
  ansible-collections/community.docker. Each is outward-facing, and
  each is Michael's decision once the drafts exist.
* Changing any patch's behaviour. If 8b's test run finds that a patch
  fails upstream's tests without the downstream series under it, that
  is a finding: record it, and fix it in the patch that owns it only
  if the fix is mechanical.
* Kayobe. The Future work entry stands; the proposal asks the
  question rather than answering it.

#### What the survey found

Checked against `origin/develop` at `3b8ff103d`, with Kolla at
`f738a5e56` and Kolla-Ansible at `24644b3ba` (the `source_sha` pins).

* **patch199 applies to pristine Kolla.** The old claim was that its
  `sources.yaml` hunk had our downstream `kerbside-base` entry as
  context. Upstream now has that entry (`kolla/common/sources.yaml:111`),
  so `git apply --check` passes on pristine Kolla. Its
  `zuul.d/base.yaml` hunk still pins `ansible-collection-kolla` with
  `override-checkout: master`, which upstream must change when Kolla
  branches; that stays a note for the proposal.
* **The deployer series applies to pristine Kolla-Ansible, in order,
  with the repository's own apply.** `_build/apply-patches-and-test.sh:205`
  uses `git apply -v -C1`. With `-C1`, patches 200, 201, 206, 208 and
  209 apply in that order to pristine Kolla-Ansible, and the
  `nova-cell` hunk of patch209 yields
  `nova_libvirt_image_full: "{{ lookup('kolla_image_full', ...) }}"`
  under upstream's own `nova_libvirt_image` line. With plain
  `git apply` (three lines of context) that hunk fails, which is what
  Phase 6's step 6e recorded; its note is true, and does not matter
  here. patch209 does depend on 206 and 208 (it adds to
  `deployer-checks.yml` and to the `common.yml` lines 208 changes),
  so the order is load-bearing.
* **The Phase 8 section named only patch199 and patch209.** The
  upstream-bound set is 199 (Kolla) and 200, 201, 206, 208 and 209
  (Kolla-Ansible). patch210 is downstream (Kerbside's images).
* **None of the six is on Gerrit.** A query of review.opendev.org for
  each `Change-Id` returns nothing.
* **The Change-Ids of patch200 and patch201 were typed, not
  generated.** `I7b3e5a9c2d4f60812a3b4c5d6e7f8091a2b3c4d5` and
  `I2f6a9d4c7e1b30586a9b0c1d2e3f4051627384a5` are runs of ascending
  hex. Gerrit would accept them, but they should be regenerated before
  anything is pushed.
* **Series directories are tested unless `skip_rebase` is set, and
  pushed only when `repush` is set.** `_build/repush-openstack.sh:11`
  reads `.repush // false`; `_build/test-patches-for-ci.sh:57` skips a
  directory whose `skip_rebase` is `true`. The Kerbside wave
  directories set both, because the main series already tests their
  patches. The deployer directories are different: the property they
  exist to check, that the patches apply and pass tests on pristine
  upstream, is exactly what the main series cannot check.
* **Upstream's publish jobs** are the `kolla-publish-*-quay` jobs in
  Kolla's `zuul.d/{debian,rocky,ubuntu}.yaml`, running
  `tests/playbooks/publish.yml`. That is where the manifest's upstream
  half would go.
* **Facts the documents must use, recorded in earlier phases:**
  controller-side `become` is rare upstream (Phase 2 Risks);
  upstream's all-in-one names its host `localhost`, so a container
  inventory cannot be a one-line change to it (Phase 3 survey); the
  nsenter plugin's root assumption and environment leak (Phase 3,
  Decision 3); digest pinning's three consequences (Phase 6); and what
  Phase 7's numbers would and would not support (Phase 7 Outcome).
* `docs/containerised-deployer.md` does not exist.
  `docs/deployer-launcher.md` already states the three inventory rules
  (lines 205-222).

I have corrected this section's opening at its source, above. The
index row describes the plan, not the phase, and needs no change.

#### Decisions

1. **No rebases.** The survey shows both patches apply to pristine
   upstream with the apply this repository uses. A second copy of any
   patch with different context would break the "one copy of each"
   rule of *How this work is carried*.

2. **The series directories are tested, and never pushed in this
   phase.** `kolla-deployer/` and `kolla-ansible-deployer/` follow the
   Kerbside wave directories' `config.yaml`, with `repush: false` and
   `skip_rebase: false`. Turning on `repush` is the push decision, and
   is one line when it is made. Until then, the daily rebase tells us
   the day an upstream change stops the series applying, which is the
   thing a proposal most needs to be true when it is sent.

3. **The upstream inventory is a patch, in the deployer series only.**
   Phase 3, Decision 5 said Phase 8 writes it. It goes in
   `kolla-ansible-deployer/ORDER`, not the main `kolla-ansible/ORDER`:
   our CI uses `etc/inventory-all-in-one-launcher-master`, which
   carries Kerbside's groups, so the main series would only carry the
   file, not use it. Its test is upstream's own lint, plus a check
   that it differs from `ansible/inventory/all-in-one` only in the host
   name and the two `[all:vars]` lines Phase 3 proved. That is weaker
   than a deploy, and the proposal says so.

4. **The two drafts that would leave the repository live inside the
   proposal.** The community.docker issue and the Zuul publish-job
   change are written as appendices to
   `docs/containerised-deployer-proposal.md`, not as files in
   `_patches/` or as issues. A publish-job patch that no CI here can
   run is the "patch nothing deploys" that Phase 3 refused to write.

5. **Two documents, not one.** `docs/containerised-deployer.md` is for
   someone running the deployer: how to build it, run it, and what does
   not work. The proposal is for upstream reviewers, and argues. Mixing
   them makes the how-to read like advocacy and the proposal read like
   a manual.

6. **The proposal quotes numbers only as Phase 7 allows.** The
   bootstrap section is shorter, by about two minutes at the median;
   the deploy step as a whole is within noise; Galaxy failures are
   rare here and say nothing about upstream's Zuul. 8e checks this
   with a grep rather than a read.

7. **Kayobe is a question in the proposal, not a dependency.** Asking
   its maintainers first, as Future work suggests, would block the
   draft on a conversation. The draft names the question; asking it is
   part of sending.

This plan's decision most likely to be argued with is 3: an upstream
inventory that nothing here deploys. The alternative is to describe
it in prose and let upstream write it, which is cheaper and honest.
The patch wins because the proposal's series table should name every
change, and because the delta against `all-in-one` is small and is what
the proposal describes, which a reviewer can read faster than a
paragraph describing it. The patch itself is a full new file, not a
twenty-line diff. *Corrected in 8e.*

#### Risks

* **Upstream's tests fail on the deployer series without our other
  patches** (for example a test that only passes with a downstream
  fixture). 8b runs `_build/test-apply.sh` with tests, not
  `--skip-tests`, on both directories, and the management session
  reads the output before 8c starts.
* **The proposal overstates.** It is the one place this plan's
  numbers leave the repository. Mitigation: Decision 6, the grep in
  8e, and the back brief gate on 8d's outline.
* **The documents restate facts the plan holds, and drift.** Every
  number in either document cites the phase Outcome it comes from,
  and 8e greps the two documents for the Phase 7 headline figures to
  check they match.
* **The daily rebase now tests two more directories.** Each is a
  short series; if the cost shows in the Rebase tests run time, set
  `skip_rebase: true` and record why.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| 8a | high | opus | none | Write the upstream all-in-one inventory for the containerised deployer as a Kolla-Ansible patch, per Decision 3 of Phase 8 in `docs/plans/containerised-deployer.md`. Read Phase 3's survey and Decisions 1, 2, 3 and 5 first, and `etc/inventory-all-in-one-launcher-master` and `docs/deployer-launcher.md` (lines 205-222). Clone kolla-ansible at the `source_sha` in `kolla-ansible/config.yaml` into the scratchpad. Copy `ansible/inventory/all-in-one` to a new file beside it (choose the name; say why), rename its host from `localhost` to a name that is not `localhost`, and add `ansible_connection=community.docker.nsenter` and an absolute `ansible_remote_tmp` to `[all:vars]`, with a header comment saying what it is for and pointing at the plugin's root assumption. Add a release note in Kolla-Ansible's style. Take the number from `_build/get-next-patch-number.py`, name it `patchNNN-kolla-ansible-master-deployer-all-in-one.patch`, write its message with `tools/extract-commit-message` conventions and a real `Change-Id` (generate one), and list it in no `ORDER` yet; 8b does that. Show `diff` of the new inventory against the original: only the host name and the `[all:vars]` lines may differ. Commit subject: "Add an all-in-one inventory for the deployer." |
| 8b | medium | sonnet | none | Create `kolla-deployer/` and `kolla-ansible-deployer/` per Decision 2 of Phase 8. Model each `config.yaml` on `kolla-ansible-kerbside/config.yaml` (the same `repo`, `source_branch`, `source_sha` and `previous_source_sha` as `kolla/config.yaml` and `kolla-ansible/config.yaml` respectively, a `destination_branch` of `containerised-deployer`, `skip_claude: true`, `skip_rebase: false`, and no `repush`). Check every key against `_build/common.sh`, `_build/apply-patches-and-test.sh`, `_build/test-patches-for-ci.sh` and `_build/repush-openstack.sh`, and against whatever enumerates series directories in `.github/workflows/`, so the new directories are tested and never pushed. `ORDER`: `kolla-deployer/` lists patch199; `kolla-ansible-deployer/` lists 200, 201, 206, 208, 209 and 8a's patch, in that order. Regenerate the `Change-Id` of patch200 and patch201 (the survey says they were typed), in both the `.patch` and `.patch-message` files. Prove both with `_build/test-apply.sh` with tests (not `--skip-tests`), and report the full summary. Add both directories to `ARCHITECTURE.md`'s inventory only if it lists series directories individually. Commit subject: "Test the deployer patches on pristine upstream." |
| 8c | high | opus | none | Write `docs/containerised-deployer.md` per Decision 5 of Phase 8: for someone who wants to run the deployer. Sources, all in the repository: `docs/plans/containerised-deployer.md` (every phase's survey, Decisions and Outcome; read them, do not summarise the plan's process), `docs/deployer-launcher.md` (link it; do not copy its reference material), `_patches/patch199*`, and the CI wiring in `tools/bootstrap-kolla-ansible` and `.github/workflows/functional-tests.yml`. Sections: what it is and why; building the image; installing and running the launcher (point at `docs/deployer-launcher.md`); the version safeguards; the build manifest and digest pinning; what CI shows; and **what did not work**, as plainly as what did (at least: SSH to `127.0.0.1`, controller-side `become`, an inventory host named `localhost`, the nsenter plugin's root assumption and environment leak, podman untested, the Rocky post-bootstrap excess that is not established). Every number cites the phase it comes from. Link it from `docs/index.md`. Do not touch `AGENTS.md` unless a convention changed, and say whether one did. Commit subject: "Document the containerised deployer." |
| 8d | high | opus | none | Write `docs/containerised-deployer-proposal.md`, the draft upstream proposal, per Decisions 4, 6 and 7 of Phase 8. **First write only an outline** (headings and one line each, plus the series table) and stop for the back brief gate below. Then the prose: a mailing-list post (openstack-discuss, `[kolla]` tag) that says what the deployer is, links the demo (this repository's launcher entries and their CI record), leads with digest pinning's two consequences from Phase 6 (a pull cannot mix builds; a tag that stopped being published cannot be deployed by accident), quotes Phase 7's numbers only as its Outcome's "would and would not support" list allows, and gives the series as a table: change, repository (`openstack/kolla`, `openstack/kolla-ansible`, `ansible-collections/community.docker`, and where the launcher itself should live, which is an open question), patch file, and status. Name the open questions: where the launcher lives, Kayobe, podman, the collection's `override-checkout: master`. Appendix A: the community.docker issue, as it would be filed (the root `remote_user` assumption and the environment leak, with the evidence from Phase 3). Appendix B: the change to Kolla's `kolla-publish-*-quay` jobs and `tests/playbooks/publish.yml` that would record digests after the push, as a sketch with the reasoning, not a patch. Link the proposal from `docs/containerised-deployer.md`. Commit subject: "Draft the containerised deployer proposal." |
| 8e | low | sonnet | none | Run the checks below and report their output. Then record Phase 8's Outcome in `docs/plans/containerised-deployer.md`: 8b's test results, what each document covers, any patch finding from 8b, and the decisions left to Michael (push, post, file). Annotate the exit criteria. Commit subject: "Record what Phase 8 produced." |

The 8e checks:

```bash
# No phrasing Phase 7 ruled out.
! grep -inE 'faster deploy|deploys? faster|eleven minutes|8-11 min' \
    docs/containerised-deployer*.md
# Both documents exist and link each other and the launcher page.
grep -q 'containerised-deployer-proposal.md' docs/containerised-deployer.md
grep -q 'deployer-launcher.md' docs/containerised-deployer.md
grep -q 'containerised-deployer.md' docs/index.md
# Neither series directory can push.
for d in kolla-deployer kolla-ansible-deployer; do
    test "$(yq -r '.repush // false' $d/config.yaml)" = false
done
# No typed Change-Ids remain in the deployer patches.
! grep -hE 'Change-Id: I(7b3e5a9c2d4f6081|2f6a9d4c7e1b3058)' \
    _patches/patch20[01]-*
pre-commit run --all-files
```

#### Back brief gate

Before 8d writes prose, the management session shows Michael the
outline and the series table. The table is the claim upstream will
check first, and the outline fixes what the post leads with; both are
cheap to change before the prose exists and costly after.

#### Exit criteria

* `_build/test-apply.sh` passes, with tests, for `kolla-deployer` and
  `kolla-ansible-deployer`, on pristine upstream.
  * Met. See 8b in the Outcome below: 172 tests for `kolla-deployer`,
    579 for `kolla-ansible-deployer`, with pep8 and linters clean.
* Neither directory sets `repush`, and both are picked up by the
  Rebase tests workflow.
  * Met for `repush`, which 8e's check confirms. Picked up: the matrix
    entries `debian-13-kolla-deployer` and
    `debian-13-kolla-ansible-deployer` are in `rebase-tests.yml`, but no
    run of the workflow has exercised them yet, so this is not yet
    observed in CI.
* The new inventory differs from `ansible/inventory/all-in-one` only
  in the host name and two `[all:vars]` lines.
  * Met with a qualification. The delta is an 18-line header, the two
    `[all:vars]` lines and six host renames, not one rename.
* `docs/containerised-deployer.md` has a section on what did not work,
  with at least the six cases in 8c's brief.
  * Met.
* The proposal's series table lists every upstream-bound patch, and
  8e's checks pass.
  * Met. 8e's checks pass, and `pre-commit run --all-files` is clean.
* Nothing has been pushed, posted or filed.
  * Met.

#### Outcome

Written by 8e, after 8a to 8d (`git log --oneline origin/develop..HEAD`:
`72f081d27`, `963419576`, `c1c20676c`, `a8b8fd0dc`). 8e's checks all
passed: no phrasing Phase 7 ruled out, the two documents link each other
and the launcher page, neither series directory sets `repush`, no typed
Change-Id remains in patch200 or patch201, and `pre-commit run
--all-files` is clean.

*8a, the inventory.* patch214,
`_patches/patch214-kolla-ansible-master-deployer-all-in-one.patch`, adds
`ansible/inventory/all-in-one-deployer`, with the host named `kolla`. Its
`[all:vars]` carries `ansible_connection=community.docker.nsenter` and
`ansible_remote_tmp=/tmp/.ansible-kolla`. The path is shared, unlike CI's
per-user one, because an upstream example has no bootstrap to substitute
a user name into it. The patch is a new file of about 600 lines. Its
delta against `all-in-one` is an 18-line header, the two `[all:vars]`
lines and six host renames.

*8b, the series directories.* `kolla-deployer/` and
`kolla-ansible-deployer/` have `release: containerised-deployer`, which
matches no build target, `skip_rebase: false`, and no `repush`.
`rebase-tests.yml`'s deep matrix is hand-written, so it gained a
debian-13 entry for each. Test-apply results: `kolla-deployer` passed
172 py3 tests and pep8 after patch199. `kolla-ansible-deployer` passed
py3, pep8 and linters after each of 200, 201, 206, 208, 209 and 214; the
last run was 579 tests, 577 passed and 2 skipped. The Change-Ids of
patch200 and patch201 were regenerated. No patch needed downstream
context, so there was no rebase and no second copy of any patch.

*8c, the how-to.* `docs/containerised-deployer.md`. Two stale facts were
fixed in passing: the launcher inventory's interpreter-pin comment, and
the 1.7 GB "unpacked" figure, which is 1170 MiB unpacked per Phase 4.

*8d, the proposal.* `docs/containerised-deployer-proposal.md`. Its outline
was approved at the back brief gate. It adds four open questions:
registry retention, no upstream CI job, the deployer tag being pushed
twice, and an inventory nothing deploys. Two things are not verified.
Appendix A's reproducer was not run in the form written, and Appendix B's
`RepoDigests` approach has not been checked against a real publish.

*Left to Michael.* Pushing to Gerrit, by setting `repush: true` on the
two series directories. Posting the proposal. Filing the community.docker
issue, after running Appendix A's reproducer first. Nothing has been
pushed, posted or filed.

### Phase 9: Push audit

Planning effort: medium. Review effort: high for 9b and 9c, because
they are the only read of the plan's code taken as a whole rather than
one phase at a time.

This repository has no `PUSH-AUDIT.md` yet. This phase runs what
`PLAN-TEMPLATE.md` specifies in its place over the accumulated diff of
Phases 0 to 8, using the ranges recorded in the `Merged` column: the
five-item checklist of the `plan-push-audit-phase` shared block, and on
top of it a code review of those ranges, `_build/test-apply.sh` for
every project directory the plan touched, and a read of every
documentation page the plan changed. *Corrected at planning (see the
survey below):* this section first named only the last three, and said
the `shakenfist/actions` changes were already audited there. They were
not.

**In scope:**
* the shared block's five checklist items, each with a verdict or a
  sentence saying why it does not apply;
* a code review of the twelve merges below, read together, in two
  halves: this repository's own code, and the upstream-bound patches;
* `_build/test-apply.sh`, with tests, for the five project directories
  the plan touched, in a fresh clone;
* a read of the eight documentation pages the plan changed, checked
  against the tree; and
* the audit of the two `shakenfist/actions` merges under that
  repository's own `PUSH-AUDIT.md`.

**Out of scope:**
* Fixing anything. Findings land as their own pull request after this
  phase merges, as the shared block requires. This phase's pull request
  carries the audit's results and nothing else.
* Re-raising what *Future work* already records. A known gap is not a
  finding; one that has become worse, or whose entry is now wrong, is.
* Pushing to Gerrit, posting the proposal, or filing on
  community.docker. Phase 8 left those to Michael, and the audit does
  not change that.
* Rewriting history. If item 3 finds something that should never have
  entered a public repository, the audit records it and Michael decides.

#### What the survey found

Checked against `origin/develop` at `3bd67676e`.

* **No `PUSH-AUDIT.md`**, as the section said. `PLAN-TEMPLATE.md`'s
  "In this project" note under the push-audit block is the substitute,
  and it adds the five-item checklist as a floor, which this section
  did not list. Corrected above.
* **Every recorded landing here is a two-parent merge commit on
  `develop`'s first-parent history**, so each diffs cleanly against its
  first parent:

  | Merge | PR | Phase | Files | +/- |
  |-------|----|-------|-------|-----|
  | `725cf1e8a` | #1796 | 0 | 6 | +246 -25 |
  | `190daeeca` | #1799 | 0 | 1 | +1 -2 |
  | `507fbea62` | #1803 | 1 | 12 | +806 -61 |
  | `b97361853` | #1812 | 2 | 34 | +3309 -59 |
  | `f2dd038ab` | #1831 | 3 | 9 | +642 -85 |
  | `78c6e79c7` | #1854 | 4 | 17 | +1935 -164 |
  | `af2474d4f` | #1857 | 4 | 5 | +344 -64 |
  | `b38293652` | #1867 | 5 | 9 | +2115 -34 |
  | `784e95d9e` | #1881 | 6 | 18 | +4413 -14 |
  | `1af58c137` | #1896 | 7 | 6 | +1872 -13 |
  | `3b8ff103d` | #1919 | 7 | 3 | +120 -9 |
  | `54b8bb270` | #1925 | 8 | 17 | +1869 -21 |

  Their union is 79 paths: 58 added, 21 modified, none deleted or
  renamed (`git diff --name-status -M`), and every one still exists.
  The list, for every step below:

  ```bash
  MERGES="725cf1e8a 190daeeca 507fbea62 b97361853 f2dd038ab 78c6e79c7
          af2474d4f b38293652 784e95d9e 1af58c137 3b8ff103d 54b8bb270"
  for m in ${MERGES}; do git diff --name-only ${m}^1 ${m}; done | sort -u
  ```

* **The `shakenfist/actions` changes were never audited.** The section
  said Phase 0's were "audited in that repository, as part of the pull
  request that lands them". Neither #121 (`7eaa1e534`, Phase 0) nor
  #150 (`34b05f734`, Phase 4) mentions an audit in its body or
  comments, and this section did not name #150 at all. Both are
  two-parent merges touching one file each
  (`deploy-kolla-ansible/action.yml`,
  `setup-kerbside-environment/action.yml`). That repository does have a
  `PUSH-AUDIT.md`, of two waves. Phase 4's `Merged` cell had no commit
  for #150; both cells now use the block's `<repo> <sha> (#pr)` form.
* **Five project directories were touched**: `kolla/` and
  `kolla-ansible/` (their `ORDER` files only), `ansible-collection-kolla/`
  (added in Phase 1 to pin the collection the deployer image installs:
  its `ORDER` is empty, and the empty `FORCE` file is what makes
  `assemble-source.sh` and `imagebuild.sh` include it, per Phase 1
  Decision 8. *Corrected in 9g.*), and `kolla-deployer/` and
  `kolla-ansible-deployer/` (Phase 8). No per-release directory was
  touched. The patches are 199, 200, 201, 206, 208, 209, 210 and 214.
* **The patches have moved since they merged.** The daily rebase
  rewrites patch files, so a merge's diff shows a patch as it landed,
  not as it is now. The same goes for code a later phase changed.
* **Phase 8's last open exit criterion is now observed.** Rebase tests
  runs `39056461955` (2026-10-17) and `39153391224` (2026-10-18) both
  passed, with the `debian-13-kolla-deployer` and
  `debian-13-kolla-ansible-deployer` jobs green in each.
* **Eight documentation pages were changed**, besides this plan:
  `AGENTS.md`, `ARCHITECTURE.md`, `docs/building.md`,
  `docs/containerised-deployer.md`,
  `docs/containerised-deployer-proposal.md`,
  `docs/deployer-launcher.md`, `docs/index.md` and
  `docs/script-reference.md`.
* **Leads for checklist item 3, not verdicts.** Both repositories are
  public. The plan's merges added `gitlab.home.stillhq.com:5050`
  (`507fbea62`, `b97361853`, and in `784e95d9e` as a `click` default in
  `tools/write-build-manifest`), `devpi.home.stillhq.com`
  (`af2474d4f`) and `sfcbr` (`3b8ff103d`). `gitlab.home.stillhq.com`
  was already in `_build/common.sh:66` before the plan started, so
  whether these "entered" the repository is a judgement for 9e.
  `10.0.2.0/24` addresses in `tools/` are the test cloud's network.
* **No secret scanner is installed on the host.** gitleaks runs from
  its container image.
* **`/code-review` takes one target**: a diff, a pull request, a branch
  or paths. The twelve merges are not contiguous, since unrelated work
  landed between them, so no single target is the plan's diff.
* **`_build/test-apply.sh` can report success while testing nothing**
  if `src/kolla-ansible` has a stale `master-patches` branch (Future
  work). The audit's run has to be in a fresh clone.

I have corrected this section's opening, and the two `shakenfist/actions`
cells of the Execution table, at their source. The index row describes
the plan, not the phase, and needs no change.

#### Decisions

1. **Read each merge as it landed, and judge the files as they are
   now.** The recorded ranges say what the plan changed; each merge's
   diff against its first parent says why. But the patches have been
   rebased since and later phases changed earlier phases' code, so a
   finding names the file and line in today's tree, and is a finding
   only if it is still true there.

2. **The code review is two sub-agents, not one `/code-review` run.**
   `/code-review` cannot take twelve separate merges as one target, and
   running it once per pull request is the one-phase-at-a-time audit
   the shared block exists to replace. 9b reads this repository's code
   (the launcher, `tools/`, `_build/`, `etc/` and the workflows); 9c
   reads the upstream-bound patches, judged against the plan's second
   question: would an upstream reviewer accept them. Both ask the
   cross-phase question explicitly. The management session may still
   run `/code-review` on a single pull request to settle a disputed
   finding.

3. **Audit the `shakenfist/actions` merges now, under that repository's
   runbook.** "Cite rather than re-run" assumed an audit that never
   happened, and skipping it would be the silent omission the shared
   block warns about. It is two one-file merges, so it is cheap. 9f
   runs `shakenfist/actions`' `PUSH-AUDIT.md`, not this repository's
   checklist (the block says not to borrow a runbook). Its findings
   become a pull request there, and its result is recorded here.

4. **`test-apply.sh` runs locally, in a fresh clone, for all five
   directories, with tests.** The green Rebase tests runs are
   corroboration, not a substitute: they run at a different SHA and
   their logs are not read by anyone. `ansible-collection-kolla/` is
   run too, to record what an empty `ORDER` does. Whether that empty
   directory should exist at all is a question for 9b. (9b: it
   should; see the Outcome.)

5. **Each finding is classified: fix, decline or Future work.** A fix
   lands in the follow-up pull request. A decline says why, in this
   plan. Future work is for a real gap that is not this plan's change.
   Declines are Michael's call (see the back brief gate).

6. **This phase closes itself out.** If nothing needs fixing, this
   pull request sets Phase 9 and the plan's index row to `Complete`,
   with no `Merged` cell. If something does, the follow-up pull request
   carries the close-out and records this phase's merge commit, as
   `plan-phase-landing` requires. A `shakenfist/actions` finding does
   not hold the close-out once its pull request there has merged.

The decision most likely to be argued with is 3. It widens the audit
into a second repository, with a different runbook, for a change that
is two files. The alternative is to record that the audit was never run
and move on, which is honest but leaves the shared block's rule broken
for this plan, and the next plan would copy that. Doing it costs one
sub-agent.

#### Risks

* **Reviewers re-find what Future work records**, and the findings
  list fills up with noise. Each brief names the entries; the
  management session drops duplicates before the gate.
* **The review turns into a redesign.** A finding must name a failure
  scenario, with inputs and the wrong result. A design opinion with no
  failure goes to Future work, if anywhere.
* **`test-apply.sh` fails for reasons outside the patches** (PyPI, Ansible
  Galaxy, a tox environment). 9a reruns once, and classifies a failure
  as the patch's or the environment's in the way the `check-patches`
  skill does. The green Rebase tests runs are the comparison.
* **Item 3 finds something that needs a history rewrite.** That is out
  of scope (see above): it is recorded, and Michael decides.
* **The audit itself goes stale** if the daily rebase moves the patches
  between 9a and the merge. 9a records the SHA it tested.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| 9a | medium | sonnet | none | Run the mechanical checks for Phase 9 of `docs/plans/containerised-deployer.md` and report output; change nothing and commit nothing. Clone this repository fresh into the scratchpad at `origin/develop` (not a worktree: a stale `master-patches` branch in `src/` makes `_build/test-apply.sh` pass while testing nothing). Record the SHA. Run `_build/test-apply.sh` with tests (no `--skip-tests`) for `kolla`, `kolla-ansible`, `ansible-collection-kolla`, `kolla-deployer` and `kolla-ansible-deployer`, one at a time, and report each summary, including test counts. Rerun a failure once, and say whether it looks like the patches or the environment. Then `pre-commit run --all-files` (it runs the launcher, `write-build-manifest` and `measure-deployer-bootstrap` tests). Then gitleaks from its container image over the twelve merges listed in the survey (each merge's first-parent diff), with a positive control: a scratch commit carrying a fake AWS key that it must find. Then the lines over 120 characters in Python the merges added. Report the commands as well as their output. |
| 9b | high | opus | none | Review this repository's own code from the twelve merges listed in Phase 9's survey: `deployer/launcher/`, `tools/` (`bootstrap-kolla-ansible`, `check-deployer-*`, `install-openstack-clients`, `ka`, `measure-deployer-bootstrap`, `postinstall-kolla-ansible`, `write-build-manifest` and their tests), `_build/`, `etc/`, `.github/workflows/` and `.pre-commit-config.yaml`. Read each merge's diff against its first parent for intent, and the file as it is now for the finding (Decision 1). Look hardest at what one phase did to another: a helper written twice, an assumption Phase 2 made that Phase 4 or 6 broke, a CI path no entry exercises. Also say whether `ansible-collection-kolla/` (empty `ORDER` and `FORCE`) should still exist. Every finding gives a file and line in today's tree, a failure scenario, and a suggested class (fix, decline, Future work). Do not re-raise *Future work* entries unless one is now wrong. Change nothing. |
| 9c | high | opus | none | Review the upstream-bound patches as an upstream reviewer would: `_patches/patch199*` (openstack/kolla) and 200, 201, 206, 208, 209 and 214 (openstack/kolla-ansible), and patch210 (downstream) for its interaction with 208 and 209. Read Phase 1, 5, 6 and 8's Decisions first. Apply them to pristine upstream at the `source_sha` pins in `kolla-deployer/config.yaml` and `kolla-ansible-deployer/config.yaml`, in a scratchpad clone, and read the result, not only the diffs. Check: upstream's style (release notes, docs, tests, commit messages); that each patch does one thing; that the series' order holds; that no patch relies on a downstream patch; and the cross-phase question (does 209's manifest still cover what 206's guards check, for example). Every finding gives a patch and hunk, a failure scenario or the reviewer comment it would draw, and a suggested class. Change nothing. |
| 9d | medium | opus | none | Read the eight documentation pages Phase 9's survey lists, and check every factual claim against the tree: a path, a command, a flag, a default, a line reference, a number. `docs/containerised-deployer.md` and the proposal cite phase Outcomes for their numbers; check each against the Outcome it names in `docs/plans/containerised-deployer.md`. Check that no fact is stated differently on two pages, and that `AGENTS.md` and `ARCHITECTURE.md` changed only as their own rules allow (a convention, the shape of the system). Report each error with file, line and the correct fact. Change nothing. |
| 9e | medium | sonnet | none | Give a verdict for items 2 to 5 of the `plan-push-audit-phase` checklist in `PLAN-TEMPLATE.md`, over the twelve merges in Phase 9's survey (item 1 is the survey's table). Item 2: anything that runs unattended, including the two deployer entries in `rebase-tests.yml`, and say where its failure is seen. Item 3: use 9a's gitleaks result, and judge the survey's hostname leads; say which were already in the repository before the plan. Item 4: nothing was deleted or renamed, but modified files lost lines (`tools/ka`, the workflows); grep this repository, `shakenfist/actions` and `shakenfist/kerbside` for consumers of anything removed, and state the greps. Item 5: what the repository publishes (images, the build manifest, patches to Gerrit through `repush`) and whether a broken change fails closed. Change nothing. |
| 9f | medium | sonnet | none | Audit `shakenfist/actions` merges `7eaa1e534` (#121) and `34b05f734` (#150) under that repository's own `PUSH-AUDIT.md`, per Decision 3 of Phase 9. Clone it fresh into the scratchpad. Scope its range to those two merges, each against its first parent, and run wave 1 and wave 2 as the runbook directs; say which checks you could not run and why. Report findings with file, line and failure scenario, or say in one sentence that there are none. Change nothing in either repository. |
| 9g | low | sonnet | none | After the back brief gate, record Phase 9's Outcome in `docs/plans/containerised-deployer.md`: 9a's SHA and results, a findings table (finding, source step, class, where it lands), each decline with its reason, 9e's verdicts, and 9f's result. Annotate the exit criteria. If nothing is classed fix, set Phase 9 and the plan's row in `docs/plans/index.md` to `Complete` (Decision 6). Run the checks below. Commit subject: "Record the deployer plan's push audit." |

The 9g checks:

```bash
git fetch origin
# Every recorded landing here is a two-parent merge on develop's
# first-parent history.
for m in 725cf1e8a 190daeeca 507fbea62 b97361853 f2dd038ab 78c6e79c7 \
         af2474d4f b38293652 784e95d9e 1af58c137 3b8ff103d 54b8bb270; do
    test "$(git rev-list --parents -n1 ${m} | wc -w)" = 3
    git rev-list --first-parent origin/develop | \
        grep -q "^$(git rev-parse ${m})"
done
# The audit changed nothing but the plan (and the index, at close-out).
test -z "$(git diff --name-only origin/develop...HEAD | \
    grep -vE '^docs/plans/(containerised-deployer|index)\.md$')"
pre-commit run --all-files
```

#### Back brief gate

Before 9g writes the Outcome, the management session shows Michael the
findings table with each finding's proposed class. Declining a finding
is a decision the plan records for good, and changing a class is cheap
before the Outcome is written and a second round of review after it.

#### Exit criteria

* Each of the shared block's five checklist items has a verdict, or a
  sentence saying why it does not apply.
  * Met. All five items have verdicts.

* `_build/test-apply.sh` passes, with tests, for all five project
  directories in a fresh clone, or each failure is classified.
  * Met with a qualification. Four of the five directories pass; the
    fifth, `ansible-collection-kolla/`, fails in the upstream baseline
    and is classified and declined (finding 18).

* Every 9b and 9c finding names a file and line (or patch and hunk) in
  today's tree, and has a class.
  * Met. Every finding names a location and has a class.

* All eight documentation pages were read against the tree, and each
  error found is listed.
  * Met. About 225 claims were checked and 12 errors found; they are
    folded into rows 12 to 17.

* The `shakenfist/actions` audit is recorded, with its pull request or
  a sentence saying it found nothing.
  * Met with a qualification. The audit is recorded; its pull request
    is part of the follow-up work.

* This pull request changes only the plan and, at close-out, the index.
  * Met. This pull request changes only the plan.

#### Outcome

Written in 9g, after 9a to 9f, on the tree at `3bd67676e`
(origin/develop). Michael classified every finding at the
back brief gate on 2026-10-20 and accepted the proposed classes.
Findings land in a follow-up pull request here and one in
shakenfist/actions, so Phase 9 stays In progress and the follow-up pull
request closes the plan out (Decision 6).

*9a, mechanical.* `_build/test-apply.sh` with tests, in a fresh clone
at `3bd67676e`. kolla passed (patches 047, 154, 175, 196 and 199; py3
172/172, pep8 clean). kolla-ansible passed (17 patches through 210;
the last py3 run was 579 ran, 577 passed, 2 skipped; pep8, linters and
bashate clean; 35 minutes). kolla-deployer passed (199; 172/172).
kolla-ansible-deployer passed (200, 201, 206, 208, 209 and 214; same
counts, linters clean). ansible-collection-kolla fails in the upstream
baseline before any patch is applied: upstream at `9fc1185` has no
`tests/` directory, so stestr cannot start. A rerun without deleting
`src/` "passed" by reusing the stale `master-patches` branch and
testing nothing, which is the Future work hazard seen live.
pre-commit is clean (11 hooks). gitleaks v8.18.4, from its container
image, over each of the twelve merges' first-parent ranges found no
leaks, and a positive control (a fake AWS key) was caught. No added
Python line is over 120 characters.

Findings, with the step that raised each, its class and where it
lands:

| # | Finding | Step | Class | Lands in |
|---|---|---|---|---|
| 1 | Launcher bind-mounts a host /etc/kolla-ansible over the image's, hiding deployer.json and manifest.json, which disables the version guards and digest pinning (`deployer/launcher/kolla_ansible_launcher/mounts.py:24`) | 9b | fix | follow-up PR |
| 2 | patch206's release note states the record rule backwards (refuses a record showing an older release; the code refuses an older deployer) | 9c | fix | follow-up |
| 3 | patch206 "Deployer checks" and patch209 "Build manifests" use `---`, nesting them under "Upgrade procedure" in operating-kolla.rst | 9c | fix | follow-up |
| 4 | patch200 and patch201 carry no Assisted-By line | 9c | fix | follow-up |
| 5 | All seven upstream commit messages exceed upstream's limits per tools/gerrit-pre-push-lint (body paragraphs, bullets, subject style); trim them before any push | 9c | fix | follow-up |
| 6 | patch214 adds a third sample inventory but doc/source/contributor/adding-a-new-service.rst still names only multinode and all-in-one | 9c | fix | follow-up |
| 7 | With 209, prune-images on Docker's classic image store deletes images pulled only by digest (reasoned from moby, not reproduced); one sentence in 209's notes and docs | 9c | fix | follow-up |
| 8 | Proposal: patch209 and patch147 must land upstream in a coordinated order (patch210's conversions ride with whichever is second); Open question 4 misses patch199's sources.yaml `reference: master`; patch201's series-table row should say it is needed only if upstream adopts the launcher | 9c | fix | follow-up |
| 9 | ANSIBLE_* variables are forwarded but the files they name are not mounted, so ANSIBLE_CONFIG outside the mounted paths is silently ignored (`command.py:24`) | 9b | fix | follow-up |
| 10 | Nothing keeps etc/inventory-all-in-one-launcher-master in step with etc/inventory-all-in-one-master | 9b | fix (pre-commit diff check) | follow-up |
| 11 | _build/calculate-container-hash.sh only warns and skips a src/ tree that is not a git repository, so a change there could reuse a stale tag; it should exit 1 as the empty case does | 9e | fix | follow-up |
| 12 | AGENTS.md:36-37 says there is no Python linting outside deployer/, but flake8 and tests cover tools/write-build-manifest and tools/measure-deployer-bootstrap | 9b, 9d | fix | follow-up |
| 13 | ARCHITECTURE.md's layout omits kolla-deployer/ and kolla-ansible-deployer/ | 9d | fix | follow-up |
| 14 | docs/containerised-deployer.md:452 says the patches are all on the main series; patch214 is only in kolla-ansible-deployer/ORDER, and the table above omits it | 9d | fix | follow-up |
| 15 | docs/deployer-launcher.md:449-451 gives per-pair differences as "up to 900 seconds" (Phase 7 Outcome: -947s to +777s) and "9 of 13 / 8 of 13" counts no Outcome holds | 9d | fix | follow-up |
| 16 | "About two minutes" for the bootstrap saving (containerised-deployer.md:302, proposal:99) overstates Rocky's 95s; say about 95 to 130s | 9d | fix | follow-up |
| 17 | Smaller doc errors: script-reference.md omits tools/check-deployer-{launcher,guards,manifest} and the launcher mode of bootstrap-kolla-ansible, links plans/containerised-deployer.md under a misleading name and says "jobs" for runs; archive/images should be archive/images-<tag> in ARCHITECTURE.md and building.md; building.md's hash description and the build-manifest step list; deployer-launcher.md's "phases still to come", `launcher show` wording and exit-status claim; the proposal's Kolla-Ansible SHA should read "at the time of writing" | 9d, 9e | fix | follow-up |
| 18 | ansible-collection-kolla test-apply fails (see 9a) | 9a | decline | — |
| 19 | patch214's ansible_remote_tmp=/tmp/.ansible-kolla is a predictable path in world-writable /tmp | 9c | Future work | this plan |
| 20 | patch199's kolla_ansible_launcher_protocol label has no upstream consumer | 9c | decline | — |
| 21 | _build/build-containers.sh:136 (Phase 6) passes the registry token on the occystrap command line, as the older lines 46 and 352 do | 9e | Future work | #1932 |
| A1 | shakenfist/actions docs/actions.md documents neither deploy-kolla-ansible's `deployer` input nor setup-kerbside-environment's `install_docker` | 9f | fix | shakenfist/actions PR |
| A2 | shakenfist/actions: an empty `deployer` emits a bare `--deployer` (action.yml:61), and `install_docker` treats any value but the exact string true as false (action.yml:160) | 9f | fix | shakenfist/actions PR |

*The declines.* Finding 18: the directory has no patches and upstream
has no unit tests. It exists for the collection pin in `config.yaml`
and the `FORCE` file, which makes `assemble-source.sh` and
`imagebuild.sh` clone it for the deployer image. Nothing runs
test-apply on it in CI, and testing it would test nothing of ours.
Finding 20: Phase 1 Decision 4 chose the label. It is harmless
upstream, and the proposal's open question on where the launcher lives
covers it.

*9e, the checklist.* Item 1: pass. The survey's table has the twelve
two-parent merges here, and the two actions merges were audited in 9f.
Item 2: pass. Nothing unattended was added. The two deployer matrix
entries in `rebase-tests.yml` run on pull requests (including the daily
rebase's) and fail as red checks, and Phase 7's removal of
`continue-on-error` made launcher failures visible. Item 3: pass for
secrets, as gitleaks is clean and the plan added no place that prints a
token. For hostnames, the CI registry and package-mirror hostnames were
already in the repository before the plan. The test-cloud's name appears
for the first time, as a bare name in plan prose only, which the audit
accepts. Item 4: pass. Nothing was deleted or renamed. The removed lines
(the deployer marker step, the old hash arguments and prefix, `tools/ka`'s
`$*`, the venv path in `functional-tests.yml`, the unconditional Docker
install) were grepped for across this repository, shakenfist/actions and
shakenfist/kerbside, and no stale consumer remains. Item 5: pass. No
touched `config.yaml` sets `repush`, and `write_build_manifest` and
`write_build_marker` fail the build rather than mark a partial one
complete. Finding 11 is the one gap.

*9f, shakenfist/actions.* Audited under that repository's
`PUSH-AUDIT.md` at main `3c9c6f5`. Wave 1 was clean: pre-commit, 762
unit tests, and gitleaks over all 599 commits with a positive control.
Wave 2 was read by the agent itself, given only 19 added lines. The
ansible syntax check was not run, since neither merge touches
`ansible/`. The findings are A1 and A2.

*Issues filed* for problems that predate the plan. #1931:
`_build/analyze-shared-patches.py` chooses `create_copy` for a patch
shared by master series, which would fork the deployer patches against
Phase 8 Decision 1; reproduced with a synthetic failure. #1932: the
registry token printed in the banners of `tools/bootstrap-kolla-ansible`
and `_build/build-containers.sh`, and passed on command lines.

*Corrected at source.* The survey's ansible-collection-kolla bullet,
which gave the wrong reason for the directory's existence.

#### Resolving the findings

Planning effort: medium. The audit merged as #1933 (`30a2210ab`). This
is the follow-up pull request Decision 6 names: it fixes findings 1 to
17 here, carries A1 and A2 as a pull request in shakenfist/actions, and
closes the plan out. Michael asked for it to be planned before it is
implemented.

**In scope:** the findings classed *fix* in the Outcome's table, and
nothing else. **Out of scope:** #1931 and #1932, which predate the plan
and have their own issues; the Future work entries; pushing to Gerrit,
which is still Michael's decision once the messages are trimmed.

*Decisions.*

1. **A protected `/etc/kolla-ansible` is refused, not skipped.** Adding
   it to `_PROTECTED_TREES` makes `mount_for` raise its existing "refusing
   to mount ... over the deployer image" error for a configuration
   directory, inventory or `-e @file` there, and mount a single file
   under it only where `is_protected` allows. A clear refusal is the
   launcher's existing behaviour for protected paths, and the operator's
   fix (move the directory) is one line in the error.

2. **Path-valued `ANSIBLE_*` variables are mounted from a fixed list.**
   `ANSIBLE_CONFIG`, `ANSIBLE_INVENTORY`, `ANSIBLE_VAULT_PASSWORD_FILE`,
   `ANSIBLE_PRIVATE_KEY_FILE` and `ANSIBLE_LOG_PATH`, through the same
   `mount_for` the arguments use (`ANSIBLE_INVENTORY` may be a
   comma-separated list). A fixed list is predictable and can be
   documented; guessing at every forwarded value would mount whatever
   happens to look like a path. Any other variable naming a file is
   documented as not mounted.

3. **The launcher inventory stays a checked copy, not a generated one.**
   A pre-commit hook (`tools/check-launcher-inventory`) fails when the two
   inventories differ by anything but the launcher file's header and its
   connection lines. Generating it would move the file CI reads into
   `tools/bootstrap-kolla-ansible`, where a reviewer cannot see it.

4. **Trimmed commit messages lose nothing.** Each of the seven messages
   is cut to `propose-upstream-patch`'s two paragraphs of four lines.
   Anything cut that is stated nowhere else (the patch's release note, its
   docs, the proposal) moves into the proposal's description of that
   patch, which is where the Gerrit comment would come from.
   `tools/gerrit-pre-push-lint --stack` over both series, applied in
   scratch clones, is the check.

5. **Patch edits are in place.** Every patch here is master-only, and
   Phase 8 Decision 1's one copy of each holds; `find-patch-usage.py`
   confirms the users before each edit, and both `ORDER` files that list
   a patch see the same change.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| R1 | high | opus | none | Fix findings 1 and 9 of the Phase 9 Outcome in `deployer/launcher/`, per Decisions 1 and 2 of *Resolving the findings*. 1: add `/etc/kolla-ansible` to `_PROTECTED_TREES` in `mounts.py`, with tests for a configuration directory and an inventory under it (refused) and the image paths untouched. 9: mount the five listed `ANSIBLE_*` variables' paths in `command.py`'s `collect_mounts`, through `mounts_mod.mount_for`, handling a comma-separated `ANSIBLE_INVENTORY` and an unset or empty value; tests for each. Update `docs/deployer-launcher.md`'s protected-path list and its account of forwarded variables, saying which are mounted. Standard library only, 120 columns. One commit per finding. Subjects: "Refuse to mount over the image's kolla-ansible config." and "Mount the files ANSIBLE_* variables name." |
| R2 | medium | sonnet | none | Fix findings 10 and 11. 10: write `tools/check-launcher-inventory`, a short shell script that strips the leading comment block and the `ansible_connection`/`ansible_remote_tmp` lines from `etc/inventory-all-in-one-launcher-master` and diffs the rest against `etc/inventory-all-in-one-master` (read both first and decide the exact normalisation from the real difference), and add it to `.pre-commit-config.yaml` for those two files and itself; prove it fails on a copy with a group added to one file only. 11: make `_build/calculate-container-hash.sh` exit 1, with its message, on a `src/` tree that is not a git repository, as it does when it finds no trees; update the comment above it and `docs/building.md`'s description of the hash (which still says it hashes each file by name and content). Shellcheck clean. One commit per finding. Subjects: "Check the launcher inventory matches the venv one." and "Fail the hash on a source tree that is not git." |
| R3 | high | opus | none | Fix findings 2, 3, 4, 6 and 7 in `_patches/`, per Decision 5. Read `.claude/skills/rebase-patch/SKILL.md` and `.claude/skills/propose-upstream-patch/SKILL.md` first. For each: apply the series to a scratch clone at the `source_sha` in `kolla-ansible-deployer/config.yaml` (and `kolla-deployer/` where relevant) with `git apply -C1`, make the change in the tree, and regenerate the patch so its hunks and counts are exact (`tools/recount-patch.py`; pre-commit's recount hook checks it). 2: patch206's second release note says it refuses a record showing an older release; the code refuses an older deployer than the record. 3: "Deployer checks" (206) and "Build manifests" (209) take `~~~` underlines, matching "Upgrade procedure"'s siblings in `doc/source/user/operating-kolla.rst`; check the rendered order still reads sensibly. 4: add `Assisted-By: Claude Code` to patch200 and patch201, in both the `.patch` header and `.patch-message`, matching the other five. 6: patch214 adds `all-in-one-deployer` to `doc/source/contributor/adding-a-new-service.rst`'s list of inventories to update. 7: one sentence in 209's release note and its `operating-kolla.rst` section that, on Docker's classic image store, `prune-images` removes images pulled only by digest that no container uses, so stage images with `pull` after pruning. Then `_build/test-apply.sh` with tests for `kolla-ansible-deployer` and `kolla-ansible`. One commit per finding, except that 3 touches two patches in one commit. |
| R4 | medium | opus | none | Fix finding 5, per Decision 4, after R3. Trim the commit messages of patches 199, 200, 201, 206, 208, 209 and 214 (both `.patch` header and `.patch-message`; keep them identical) to `propose-upstream-patch`'s rules; keep footers. Move anything cut and stated nowhere else into the matching patch's entry in `docs/containerised-deployer-proposal.md`. Prove with `tools/gerrit-pre-push-lint --stack N` in scratch clones with each series applied as commits (`git am` or apply-and-commit with the messages). Report each message's before and after paragraph counts. One commit. Subject: "Trim the deployer patches' commit messages." |
| R5 | medium | opus | none | Fix findings 8 and 12 to 17, after R1 to R4, against the tree they leave. 8: in the proposal, the coordinated landing order of patch209 and patch147 (patch210's three conversions ride with whichever lands second), Open question 4 to name patch199's `sources.yaml` `reference: master` beside `override-checkout`, and patch201's series row marked as needed only if upstream adopts the launcher. 12: `AGENTS.md:36-37`. 13: `ARCHITECTURE.md`'s layout gains the two deployer series directories. 14 to 17: the rows of the Outcome's table, each checked against the code before editing; Phase 9's 9d report is summarised in those rows, so re-verify each claim rather than trusting the row. No plan phase or decision citations in the docs. One commit per finding. |
| R6 | medium | sonnet | worktree in `../actions` | Fix A1 and A2 in shakenfist/actions, in a new worktree of `/srv/kasm_profiles/mikal/vscode/src/shakenfist/actions` on branch `deployer-inputs-docs` from `origin/main`. A1: document `deploy-kolla-ansible`'s `deployer` input and `setup-kerbside-environment`'s `install_docker` in `docs/actions.md`, with the precondition the latter's description states. A2: treat an empty `deployer` as `venv` (`-n` and `!= venv`), and accept only `true` or `false` for `install_docker`, failing on anything else. Run that repository's `pre-commit run --all-files` and unit tests. One commit per finding, following that repository's AGENTS.md. Do not push. |
| R7 | low | n/a | none | Management session. Run the checks below, record a short *Resolution* paragraph under this section (commits, test-apply counts, the actions pull request), and close out: Phase 9 `Complete` in the Execution table and the plan's row in `docs/plans/index.md` `Complete`. Commit subject: "Close out the containerised deployer plan." |

R1 to R5 run in sequence in this worktree, because each runs
pre-commit or test-apply over the shared tree, and R5's documents
describe what R1 and R3 changed. R6 runs alongside them in its own
repository.

The R7 checks:

```bash
# Finding 1: the image's config directory is protected.
grep -q "'/etc/kolla-ansible'" deployer/launcher/kolla_ansible_launcher/mounts.py
# Finding 4: every upstream-bound patch carries a disclosure.
for p in 199 200 201 206 208 209 214; do
    grep -q '^Assisted-By:' _patches/patch${p}-*kolla-ansible-*.patch-message
done
# Finding 2: the release note no longer says the record is refused for
# being older.
! grep -q 'record shows an older release' \
    _patches/patch206-kolla-ansible-*.patch
# Finding 3: no new section underlined with dashes.
! grep -A1 -E '^\+(Deployer checks|Build manifests)$' \
    _patches/patch20[69]-kolla-ansible-*.patch | grep -q -- '^+---'
# Finding 10: the inventory check exists and passes.
tools/check-launcher-inventory
./_build/test-apply.sh kolla-deployer kolla-ansible-deployer
pre-commit run --all-files
```

**Back brief gate.** Before R4 edits anything, the management session
shows Michael one trimmed message (patch209's, the longest) beside the
original. The other six follow its pattern, so agreeing it once is
cheap; rewriting seven after the fact is not.

*Resolution.* Done on 2026-10-20 (AEDT). R1 and R6 ran in parallel, and
R2 to R5 in sequence, as planned. Michael approved patch209's trimmed
message at the gate. Every finding classed *fix* is fixed, in one
commit each: 1 (`8ce132c6c`), 9 (`3ff820dde`), 10 (`826732347`), 11
(`f0aa0091d`), 4 (`cc8cfca2e`), 2 (`ebf0653ec`), 6 (`1874341c0`), 7
(`8cd3954cd`), 3 (`5b643a7e1`), 5 (`be4c35520`), 12 (`aa3fc8d35`), 13
(`9a4cdac50`), 14 (`82144f81c`), 15 (`1552bf293`), 16 (`e965ec2bf`), 8
(`f0f371922`) and 17 (`007d84dc6`). These are branch SHAs; the merge
commit of this pull request is the record. A1 and A2 are
shakenfist/actions#189, which should merge before this pull request.

Departures from the plan. R1 also applies the protected check to files
listed in `deployer.conf`'s mounts, which skipped it for files; that
closed the same hole by another route, so it was kept in finding 1's
commit. R3 gave patch200 and patch201 the model form of `Assisted-By`
the other five carry, not the bare `Claude Code` the brief named.
R4 kept the seven subjects, so `gerrit-pre-push-lint` still warns that
they end with a period and have no area prefix; the body warnings are
gone. Change the subjects before pushing to Gerrit if that matters.

Checks, on the branch before this commit: the R7 greps pass,
`tools/check-launcher-inventory` passes, `pre-commit run --all-files`
is clean, and `_build/test-apply.sh` with tests passes for
`kolla-deployer` (172 tests) and `kolla-ansible-deployer` (all six
patches; the last py3 run 577 passed, 0 failed; pep8 and linters
clean). R3 also ran `kolla-ansible` with tests, with the same counts.

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
* The "Collect logs" step in `functional-tests.yml` aborts on the
  first node whose `tools/gather-logs` fails: it runs under
  `bash -e`, and `wait ${pids}` returns the last job's status before
  the per-node warning code runs (Phase 4's Outcome). Not this
  plan's change; fix it in its own pull request.
* Whether SELinux is enforcing when the chroot of Phase 4 starts on
  Rocky. Nothing failed and `restorecon -n` found no mislabelled
  files, but the job captures neither `getenforce` nor the audit
  log. Add both to `tools/check-deployer-launcher` if it matters to
  the proposal.
* The Phase 5 rules that CI never ran through Ansible (Phase 5's
  Outcome): the upgrade and SLURP rules, a record newer by release,
  a host with no record under a deployer image, `bifrost`, the
  MariaDB and RabbitMQ playbooks, and multinode with the launcher.
  The rules are unit-tested in patch206; an upgrade CI entry and a
  multinode launcher entry would prove the rest, at a cost this plan
  did not take.
* patch147's `kerbside_base_image_full` is built from
  `kerbside_api_image`, so it names the `kerbside-api` image, not
  `kerbside-base` (`roles/kerbside/defaults/main.yml:87` in the
  patched tree). Found by the Phase 6 survey; a fix to patch147 in
  its own pull request.
* `tools/check-deployer-guards` prints only `PASS:` lines, so a job
  log never shows a refusal's text. Printing the matched message on
  success is a one-line change if the proposal wants it quoted.
  `tools/check-deployer-manifest` is the same for the missing-manifest
  warning.
* **`tools/find_images` cannot authenticate in CI** (Phase 6's
  Outcome). Every "Images for ..." job prints "GitLab authentication
  failed" once, so the `--dont-fetch-images` path that skips a build
  when its images already exist never fires, and every run rebuilds.
  Pre-existing, found when 6c's first CI run hit the same wall; the
  build manifest no longer depends on it. Filed as #1885, to be fixed
  in its own pull request.
* **A stale `master-patches` branch in a worktree's `src/kolla-ansible`
  makes `_build/test-apply.sh` report "All patches applied correctly"
  while applying and testing nothing.** Phase 6b ran its tests in a
  fresh clone. The script should refuse, or reset the branch.
* Both banners, `_build/build-containers.sh:15` and
  `tools/bootstrap-kolla-ansible:18`, echo the registry token. GitHub
  Actions masks it, so nothing leaks in CI; a local run prints it.
  Pre-existing. #1932 tracks it, with the uses of `--password` on a
  command line.
* **patch214's `ansible_remote_tmp=/tmp/.ansible-kolla` is a
  predictable path shared in world-writable `/tmp`.** A local user can
  win a symlink or rename race on it. The fix is a per-user or
  `mktemp` path, once upstream has a bootstrap to substitute it; Appendix
  A of the proposal already calls the workaround temporary.

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
  hash. Fixed in step 1d, which replaces the term with one line per
  tree under `src/`: its name and the git tree id of its `HEAD`.

* **`tools/ka` re-splits its arguments.** It runs `kolla-ansible $*`,
  so an argument containing a space, such as `-e 'foo=a b'`, reaches
  Kolla-Ansible as two words. No current caller passes one, which is
  why nothing has failed. Not yet fixed; step 0b rewrites the script
  and uses `"$@"`.

### Back brief

Before executing any step of this plan, back brief the operator on
your understanding of the plan and how the work you intend to do
aligns with it.
