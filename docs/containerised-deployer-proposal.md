# The containerised deployer: a draft upstream proposal

This is a draft of a post to openstack-discuss, for Kolla and
Kolla-Ansible reviewers. It has not been sent. Nothing it describes
is on Gerrit, and the community.docker issue in Appendix A has not
been filed; each of those is a separate decision. For how to build
and run the deployer today, see
[the containerised deployer](containerised-deployer.md); for the
evidence behind every number below, see
[the containerised deployer plan](plans/containerised-deployer.md).

The changes were checked against Kolla `f738a5e56` and Kolla-Ansible
`24644b3ba`.

**Subject:** `[kolla] Running Kolla-Ansible from a Kolla-built image, pinned by digest`

---

## What it is

We have a prototype that runs Kolla-Ansible from a container image
that Kolla builds, with the same tag, registry and base distribution
as every service image. A small launcher, with no dependencies beyond
Python's standard library, installs a `kolla-ansible` command that
runs the real one in that image as the invoking user. The image
carries a manifest naming the digest of every image built with it,
so that a deployment pins one image and gets the whole build. Two
all-in-one CI entries deploy this way, and both vote.

## One pin fixes the whole build

The operator pins the deployer image by digest, in a small file
beside the configuration. That digest fixes the deployer's manifest,
and the manifest fixes every service image: each
`<service>_image_full` resolves to `<image>@<digest>`, not to
`<image>:<tag>`. Two things follow that tags cannot give.

**A pull cannot mix builds.** Kolla's tags are overwritten by every
publish, and a day's publish lands over minutes, not at once. Today a
deploy that pulls during that window can get some images from one
build and some from the next, and a node added months later pulls
whatever the tag names that day. With digests, every node pulls the
images of the build the deployer was built with, whenever it pulls.

**A tag that has stopped being published cannot be deployed by
accident.** Nothing resolves service images by tag any more, so a
tag that has gone stale, or has been retired, is not a thing a
deployment can drift onto.

This has one cost, and it should be said here rather than later:
switching a running deployment between tags and digests recreates
every container once, MariaDB and RabbitMQ included, because each
container's image string changes. An operator would do it at an
upgrade, which restarts everything anyway. Our CI deploys fresh, so
it has never shown the recreate.

What CI has shown: on both launcher hosts, all 36 Kolla containers
ran `<name>@<digest>` matching the manifest, and a second
`deploy-containers --tags common,cron` left every container ID
unchanged. That passed on Docker's classic image store (Debian) and
its containerd store (Rocky). Pulling by digest needed a fix to the
`kolla_container` module first: it could neither pull a digest
reference nor recognise an image pulled that way, and on the classic
store it would have recreated every such container on every run.

A deployer image without a manifest, such as one built locally,
warns once per action and pulls by tag as today. A manifest that
cannot be read fails closed.

## What else a container gives

* **The collections are fixed at build time.** The image holds
  Kolla-Ansible, ansible-core and every collection, the
  `openstack.kolla` collection included, installed when it is built.
  It fetches nothing from Galaxy or git when it runs, and nothing in
  a user's `~/.ansible/collections` can shadow what it holds.
* **The deploy host's Python is no longer dictated by Ansible.**
  When ansible-core began requiring Python 3.12, every Debian 12
  deploy host stopped working on master, although the images it
  deployed did not care. In a container, ansible-core's Python is
  the image's.
* **The deployer can check that it belongs with what it deploys.**
  The image records the release and tag it was built with. New
  checks in Kolla-Ansible refuse a run whose `openstack_tag` or
  `openstack_release` differs from the image's, and refuse a deployer
  older than the one that last deployed a host, from a small record
  each deploy writes on each host. An upgrade may move one series, or
  from one SLURP release to the next. `--override-deployer-checks`
  turns each refusal into a warning, and `destroy` is never checked.
  The checks run only from a deployer image; a virtualenv deploy
  writes the record but is not checked.

## What we measured

These are from the CI described below, pairing each launcher job with
a virtualenv control from the same run.

Across 26 pairs of launcher and virtualenv jobs from the same CI runs
(2026-10-10 to 2026-10-13), the bootstrap section was shorter by
about two minutes at the median, because the launcher does not run
`ansible-galaxy` or `kolla-ansible install-deps`. `install-deps`
alone had a median of 107s on the Debian host and 131s on the Rocky
host, and ranged from 42s to 493s.

The deploy step as a whole was within noise of its control on both
hosts: per-pair differences ranged from -947s to +777s, so we make no
claim about the time a deploy takes.

Of 982 virtualenv jobs over the 90 days of logs GitHub keeps, four hit
a Galaxy failure and one of them failed the job; the launcher entries
cannot hit one. These are our runners, our registry and our Python
mirror, and say nothing about Galaxy failures in Zuul.

The launcher costs about two seconds more per call before Ansible
starts, from one run, and 18s (Debian) to 27s (Rocky) to pull and
unpack the deployer image.

On the Rocky host the steps after bootstrap took a mean of 213s longer
than the control's over 13 pairs, about two standard errors, with the
two jobs of a pair on different runners at different times. We cannot
tell that from host load, and do not claim it either way.

Both launcher entries now vote. Before they did, from 2026-10-13 to
2026-10-15, each passed in 11 runs, three of them daily rebases, and
no failure was attributed to the launcher.

## The demo

The prototype lives in
[shakenfist/kerbside-patches](https://github.com/shakenfist/kerbside-patches),
which carries patches to Kolla and Kolla-Ansible for Kerbside, a SPICE
console proxy, and builds and deploys them in its own CI. Two entries
in its
[functional tests workflow](https://github.com/shakenfist/kerbside-patches/blob/develop/.github/workflows/functional-tests.yml)
deploy through the launcher, each beside an unchanged virtualenv
entry that is its control:

| Launcher entry | Control |
|----------------|---------|
| `master-h-debian13-c-debian13-aio-launcher` | `master-h-debian13-c-debian13-aio` |
| `master-h-rocky10-c-debian-13-aio-launcher` | `master-h-rocky10-c-debian-13-aio` |

Both start from a host with no container engine and no Kolla-Ansible
virtualenv, install the launcher, pin the deployer, run
`bootstrap-servers`, and then run every later Kolla-Ansible action as
an unprivileged user. After the deploy they check the launcher, the
version checks and the digests. The first run in which both voted is
[run 38820775821](https://github.com/shakenfist/kerbside-patches/actions/runs/38820775821),
on [#1919](https://github.com/shakenfist/kerbside-patches/pull/1919).
[The containerised deployer](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/containerised-deployer.md)
says how to build and run it, and what did not work.

## What upstream would have to accept

None of these is hidden in the patches, but each is a cost a reviewer
should weigh.

* **The container is `--privileged --pid host`.** It reaches its own
  host through the `community.docker.nsenter` connection plugin, not
  SSH. It runs as the invoking user, and only the image's `nsenter`
  carries capabilities (as file capabilities), so only the namespace
  switch is privileged. Any tool that pushes the image must keep file
  capabilities; ours did not at first.
* **`bootstrap-servers` needs root.** It cannot run in a container on
  the engine it is about to install and restart, so the launcher
  unpacks the image and runs it under `chroot` in a private mount
  namespace. That needs root for the whole command, where a
  virtualenv install escalates per task.
* **Any new controller-side `become` breaks it.** The container cannot
  escalate on its own `localhost`. `validate-config` was the one case
  that mattered, and patch200 makes its `become` a variable; the only
  other is in `post-deploy`, and escalates only when the configuration
  directory is not writable.
* **The deploy host cannot be named `localhost` in the inventory.**
  Kolla-Ansible runs some tasks on Ansible's implicit `localhost`
  using paths that exist only in the container (keystone's fernet
  cron generator, for example). An inventory host called `localhost`
  replaces the implicit one and sends those tasks to the host. So the
  all-in-one inventory for the container is a new file, not a
  one-line change to `all-in-one`.
* **The nsenter plugin has two problems** (Appendix A): it assumes
  `remote_user` is root, and it passes the caller's environment to
  commands on the host. The inventory works around the first; the
  second cost us four rounds of fixes on Rocky.
* **What has not run.** Multinode through the launcher, a deploy host
  that is not also a target, upgrades (the upgrade rules of the
  version checks are unit-tested only), switching a running
  deployment from tags to digests, and podman as the deploy engine.
  And all of it ran on our CI's runners, private registry and Python
  mirror, not upstream's.

## The series

Each change is reviewable on its own. patch199 is useful without the
rest: it gives Kolla a deployer image that `docker run <image> --help`
can use with no launcher. The Kolla-Ansible patches apply in the
order listed.

| Change | Repository | Patch file | Status | Upstream tests |
|---|---|---|---|---|
| The `kolla-ansible` deployer image | `openstack/kolla` | `patch199-kolla-ansible-deployer-image.patch` | applies to pristine upstream with git apply -C1; not on Gerrit | py3 (172) and pep8 pass on pristine Kolla |
| `validate-config` without controller-side `become` | `openstack/kolla-ansible` | `patch200-kolla-ansible-master-validate-config-become.patch` | applies to pristine upstream with git apply -C1; not on Gerrit | py3, pep8 and linters pass on pristine Kolla-Ansible, applied in this order |
| Ignore `deployer.conf` in `tests/check-config.sh` | `openstack/kolla-ansible` | `patch201-kolla-ansible-master-check-config-deployer-conf.patch` | applies to pristine upstream with git apply -C1; not on Gerrit | py3, pep8 and linters pass on pristine Kolla-Ansible, applied in this order |
| Version safeguards (deployer against configuration and deployment) | `openstack/kolla-ansible` | `patch206-kolla-ansible-master-deployer-guards.patch` | applies to pristine upstream with git apply -C1; not on Gerrit | py3, pep8 and linters pass on pristine Kolla-Ansible, applied in this order |
| Digest references in `kolla_container` | `openstack/kolla-ansible` | `patch208-kolla-ansible-master-kolla-container-digests.patch` | applies to pristine upstream with git apply -C1; not on Gerrit | py3, pep8 and linters pass on pristine Kolla-Ansible, applied in this order |
| Pull by digest from a build manifest (`kolla_image_full`) | `openstack/kolla-ansible` | `patch209-kolla-ansible-master-image-build-manifest.patch` | applies to pristine upstream with git apply -C1, after 206 and 208; not on Gerrit | py3, pep8 and linters pass on pristine Kolla-Ansible, applied in this order |
| An all-in-one inventory for the deployer (new file, ~600 lines; nothing here deploys with it) | `openstack/kolla-ansible` | `patch214-kolla-ansible-master-deployer-all-in-one.patch` | applies to pristine upstream with git apply -C1; not on Gerrit | py3, pep8 and linters pass on pristine Kolla-Ansible, applied in this order |
| Record digests after the publish push | `openstack/kolla` | none (Appendix B, a sketch) | not written as a patch | n/a |
| nsenter `remote_user` and environment leak | `ansible-collections/community.docker` | none (Appendix A, an issue) | not filed | n/a |
| The launcher | open question | `deployer/launcher/` in shakenfist/kerbside-patches | runs in our CI; destination undecided | n/a |

patch214 is upstream's `all-in-one` copied with an 18-line header
comment, two `[all:vars]` lines (the nsenter connection and an
absolute `ansible_remote_tmp`), and the host renamed from `localhost`
to `kolla` on its six group lines. Nothing else differs.

## Open questions

1. **Where should the launcher live?** In Kolla-Ansible as a second
   package, in a repository of its own, or in Kolla beside the image
   it runs. It must never be installed beside the `kolla-ansible`
   package, and refuses to run if it finds one.
2. **Kayobe.** Would Kayobe consume the image, and does it already
   have a container story of its own? We have not asked yet, and this
   post is partly the asking.
3. **Podman.** The launcher supports it, and its unit tests cover the
   command lines it builds, but no CI host deploys with it, and
   pulling by digest with podman as the deploy engine has not run.
   Rootless podman cannot work where the deploy host is also a
   target, because its PID 1 is not the host's, so `nsenter` reaches
   the wrong namespaces.
4. **The collection's `override-checkout: master`.** patch199 adds
   `openstack/ansible-collection-kolla` to Kolla's jobs with
   `override-checkout: master` in `zuul.d/base.yaml`. That must
   change when Kolla branches.
5. **Registry retention.** A deployment pinned by digest needs the
   registry to keep manifests that no tag points to any more. How
   long does quay.io keep them for the `openstack.kolla`
   organisation, and is that long enough for a deployment that stays
   on one build for a release?
6. **An upstream CI job.** Nothing in Kolla-Ansible's Zuul would
   exercise the deployer; its jobs pull service images by tag. Which
   job should, and where does it get a deployer image from?
7. **May the deployer's tag be pushed twice?** In the sketch in
   Appendix B, the manifest is a layer added over the pushed deployer,
   under the same tag. For a few minutes the tag names a deployer with
   no manifest, which warns and pulls by tag. Is that acceptable, or
   should the manifest-carrying image have its own tag?
8. **An inventory that nothing deploys.** patch214 is proved only by
   lint and by its diff against `all-in-one`. Is that enough to merge
   it, or should it wait for question 6?

## What we are asking for

Whether Kolla and Kolla-Ansible want this, and if so in what order
the changes should go up. If it is easier to talk through than to review, we would be
glad to bring it to the PTG. Nothing is on Gerrit yet, and we would
rather hear objections before it is.

---

## Appendix A: the community.docker issue

As it would be filed on
[ansible-collections/community.docker](https://github.com/ansible-collections/community.docker).

**Title:** `nsenter connection: remote_user is forced to root, and the
controller's environment is passed to commands on the host`

**Versions:** community.docker 5.4.0, ansible-core 2.21.5.

**Summary.** The `community.docker.nsenter` connection plugin sets
`remote_user` to `root` unconditionally when it connects, and
documents that it always runs as root. It has no `remote_user`
option. But it does not need root: it runs each command as
`nsenter --ipc --mount --net --pid --uts --preserve-credentials
--target=1 -- <cmd>`, which enters no user namespace, so the command
runs on the host as the controller process's own UID and groups. When
that process is not root, which is the case when Ansible runs in a
container as an ordinary user whose `nsenter` carries file
capabilities, two things go wrong.

**1. The remote temporary directory.** ansible-core expands the
default `remote_tmp`, `~/.ansible/tmp`, through `remote_user`. We
expected that to give `/root/.ansible/tmp`, which an ordinary user
cannot create. In fact `~` expanded through the container's `HOME`,
`/var/lib/kolla-ansible`, giving a path the host does not have, and
every module failed. Setting `ansible_remote_tmp` to an absolute path
avoids it.

**2. The environment.** Commands on the host receive the controller's
whole environment: `PATH`, `HOME` and application variables all
arrive as the container had them. That is the reason for the `~`
expansion above, and it has a worse consequence: Python interpreter
discovery on the host follows the caller's `PATH`, not the host's.
In our case, a run from a host virtualenv leaked that virtualenv's
`PATH`, discovery found the virtualenv's `python3.12`, and a role
installed the Docker SDK into it. A later run from the container
discovered `/usr/bin/python3.12`, which lacked the SDK, and failed.
Pinning the interpreter did not help, because the application
overrides it. It took four rounds of fixes to find.

**Reproducer.** Adapted from the probe we ran, and not run in this
exact form. As an ordinary user in the `docker` group, on a host
whose Docker daemon does not set `no-new-privileges`:

```
docker run --rm -it --privileged --pid host \
    --user "$(id -u):$(id -g)" -e HOME=/nonexistent \
    <an image with ansible-core, community.docker, and
     setcap cap_sys_admin,cap_sys_ptrace,cap_sys_chroot+ep /usr/bin/nsenter> \
    ansible all -i 'probe,' -c community.docker.nsenter -m command -a env
```

The module fails on its temporary directory; with
`-e ansible_remote_tmp=/tmp/.ansible-$(id -un)` it succeeds, and its
output shows the container's `HOME` and `PATH`.

**What we would ask for.**

* Use the effective user rather than root when the controller is not
  root, or accept `remote_user`.
* Run commands on the host with a clean environment, or one derived
  from the host's, rather than the controller's.

The absolute `ansible_remote_tmp` is a workaround that every
inventory using the plugin this way has to repeat, which is why we
would rather not ship it as permanent advice.

## Appendix B: recording digests in Kolla's publish jobs

A sketch with its reasoning, not a patch: no CI we run can run
Kolla's publish jobs, and a patch nothing runs is one that rots.

**Where.** Kolla publishes from six jobs: `kolla-publish-*-quay` for
Debian Trixie, Ubuntu Noble and Rocky 10, each on x86 (in the
`periodic` pipeline) and arm64 (in `periodic-weekly`), defined in
`zuul.d/debian.yaml`, `zuul.d/ubuntu.yaml` and `zuul.d/rocky.yaml`.
Each runs `tests/playbooks/publish.yml` as `post-run`. That playbook
lists the `{{ kolla_namespace }}/*` images with
`community.docker.docker_host_info`, logs in to quay.io, and pushes
each image's `RepoTags.0` with `community.docker.docker_image`,
retrying up to five times. It records no digest. The tags are
`<branch>-<distro>-<version>`, with `-aarch64` appended on arm64, so
each architecture is a separate tag with a single-architecture image,
and gets its own manifest.

**The change.** After the push loop, in the same playbook:

1. Collect each pushed image's digest on quay.io. The simplest
   source is the image's `RepoDigests` after the push. **That
   `RepoDigests` after a `docker_image` push holds quay.io's digest
   is unverified**; we have not run it. The alternative is to read
   each digest back from the registry, which is what our pipeline
   does, because our push path rewrites images and changes their
   digests on the way in. Reading back is slower but stays correct if
   upstream ever adds such a step.
2. Write a `manifest.json` mapping each image name to its digest,
   with a `format_version`, leaving out the deployer image itself.
3. Build a thin image `FROM` the just-pushed `kolla-ansible` tag that
   adds the file as `/etc/kolla-ansible/manifest.json`, and push it
   under the same tag, last, and only once every other push has
   succeeded.

**Why there.** The push is the only point that knows which digests
landed together; reading tags back later can race the next publish.
Could anything retag between the push and the read-back? No: each tag
is unique to its branch, distribution and architecture, and only its
own job pushes it. What remains open is the registry's retention of
untagged manifests and the brief window in which the deployer's tag
has no manifest (open questions 5 and 7). The cost is one small image
build and push per publish.
