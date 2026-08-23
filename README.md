# Kerbside upstream patches

In order to provide native SPICE console functionality in OpenStack, a series
of patches against OpenStack are required. This repository maintains those
patches.

The majority of the patches are against Nova, although there are a fair few
against Kolla and Kolla-Ansible as my preferred deployment system too. Any
other OpenStack deployment system wishing to include Kerbside would need to make
similar modifications to their code.

The remainder of the patches are ancillary changes -- support for new Nova API
microversions in clients, things which helped me debug along the way, and that
sort of thing.

These patches last successfully applied via CI on 22 August 2026. When this occurs,
the SHAs the patches were applied to for each project are recorded in the
relevant config.yaml file, and will be used for patch applications until
updated.

# Not for production use

These patches were developed while building the Kerbside proof of concept. While
the core API patches have now landed in Nova, there is no client or deployer
support yet, and Kerbside itself needs to be updated to match what landed in
Nova. Reach out if you want more details.

# Kolla container operating system

Because RHEL 9 dropped support for SPICE in KVM / qemu, and the downstream
redistributions such as Rocky Linux followed suit, the only tested container
operating system for these patches is Debian. While it is technically feasible
to add back SPICE into Rocky with custom packages, that work has not been
attempted. Additionally, Kolla-Ansible does not support running a mix of
container operating systems for your deployment. Therefore, you need to use
Debian for all container images in a deployment using Kerbside, even though
only the Nova / LibVirt containers are customized by these patches.

# Building container images

This repository also contains the scripts used to build Kolla container images
from the patched source. On a Debian host, the short version is:

```
# Install docker and the other build dependencies. You may need to log out
# and back in afterwards to pick up the docker group change.
./_build/setup-local-build-environment.sh

# Clone the upstream projects and apply the patches for a release
_build/assemble-source.sh master

# Build the container images
./buildall.sh --build-targets "master"
```

Supported releases are 2024.1, 2024.2 and master, although the patches against
2024.1 and 2024.2 have been dropped, so those targets now build essentially
upstream containers.

See [Building patched container images][building] for Rocky host setup, the
image registry and Kolla-Ansible deployment steps, and the `debsecan`
vulnerability scan that runs over the built images.

# Documentation

The detailed documentation lives in [docs/][docs]:

- [Building patched container images][building] -- host setup, the
  assemble-source and buildall flow, deploying with Kolla-Ansible, and the
  vulnerability scan.
- [Script reference][scripts] -- every helper script in `_build/` and
  `tools/`, including the automated rebase and lint-fix tooling, the Gerrit
  helpers, and the pre-commit hooks.
- [CI data collection and reporting][ci-data] -- the container layer data time
  series, and the reliability reports for the upstream OpenDev CI jobs this
  repository depends on.
- [Kolla-Ansible Zuul and Tempest CI][zuul] -- notes for adding
  Kerbside-enabled Zuul jobs to Kolla-Ansible.
- [Kerbside development mode][devmode] -- rapid iteration on the Kerbside proxy
  inside a deployed Kolla-Ansible environment.
- [Gerrit API notes][gerrit] -- querying review.opendev.org over SSH and REST.
- [Tactics][tactics] -- advice for getting Kolla and Kolla-Ansible patches
  reviewed quickly, based on reviewer activity analysis.

# Claude Code skills

This repository ships [Claude Code][claude-code] skills in `.claude/skills/`,
which Claude will use automatically when a task matches:

- `check-patches` -- validate that the patches in `_patches/` still apply and
  stay consistent across releases.
- `debug-ci` -- diagnose a failed CI run here: which job, which step, and
  whether the patches or the environment are at fault.
- `extract-bundle` -- fetch and navigate a clingwrap debug bundle from a
  `Build test clouds` job.
- `propose-upstream-patch` -- author and submit a change to an upstream
  OpenStack project via review.opendev.org.
- `rebase-patch` -- rebase a patch onto a newer upstream SHA when it stops
  applying.

[docs]: https://github.com/shakenfist/kerbside-patches/tree/develop/docs
[building]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/building.md
[scripts]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/script-reference.md
[ci-data]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/ci-data.md
[zuul]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/kolla-ansible-tempest-jobs.md
[devmode]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/kolla-devmode.md
[gerrit]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/gerrit-api.md
[tactics]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/tactics.md
[claude-code]: https://claude.com/claude-code
