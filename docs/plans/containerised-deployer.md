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
| Deployer image, build-manifest support (Kolla) | openstack/kolla | Patches in `_patches/`, listed in `kolla/ORDER` |
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
| 0. A CI entry for the prototype | Complete | #1796 (`725cf1e8a`), #1799 (`190daeeca`); shakenfist/actions#121 (`7eaa1e534`) |
| 1. Deployer image | Complete | #1803 (`507fbea62`) |
| 2. Launcher | Complete | #1812 (`b97361853`) |
| 3. The localhost connection | Complete | #1831 (`f2dd038ab`) |
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
  simply mean "no venv".
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
  post-deploy` (Phase 0). The OpenStack clients are linked out of the
  venv by `tools/install-openstack-clients`, on the target and also
  on the CI runner (`functional-tests.yml:874-890`).
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
| 3b | medium | sonnet | none | Read-only on this host, apart from `/tmp` and a scratch directory. Build the deployer image locally as Phase 1 step 1e did: `_build/assemble-source.sh --no-tarball master`, then `_build/imagebuild.sh` for `^(base\|openstack-base\|kolla-ansible)$` with tag `local`. Install `deployer/launcher` (with 3a) into a scratch venv, and run the probe below as the current user, reporting its full output. Then run it once more with the `ansible_remote_tmp` line removed from the inventory, and report whether it fails with the `/root/.ansible/tmp` error the survey predicts. Do not use `sudo`. `become` is not tested here. Commit nothing; the results go into this plan in step 3e. |
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

Fall back to (a) or (b) only if (d) fails, and record why. Bootstrap
here is also where the venv finally goes, so this phase gives the
OpenStack clients that `tools/install-openstack-clients` links out
of it a home of their own, on the target and on the CI runner
(`functional-tests.yml:874-890`). That was Phase 2's until its
survey found the venv still in place there. Also
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

Phase 1's patch199 does not apply to pristine Kolla. Its
`sources.yaml` hunk has our downstream `kerbside-base` entry as
context. Before a push, rebase it onto the upstream `source_sha`
alone, and check its `zuul.d/base.yaml` `override-checkout: master`
for the collection, which has to change when Kolla branches.

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
