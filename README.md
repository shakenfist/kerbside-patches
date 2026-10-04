# Kerbside upstream patches

Kerbside is a SPICE protocol proxy which gives cloud users native SPICE
consoles -- high resolution, multi-monitor, USB passthrough and audio --
instead of HTML5-transcoded ones. Making that work in OpenStack needed changes
to OpenStack itself, not just a proxy beside it. This repository is where
Kerbside's contributions to those projects are developed, carried as patches
while they are in review, and tested together until they land upstream. As
Kerbside needs changes in other projects, they will be tracked here too.

The centrepiece is Nova's `spice-direct` console type. I proposed it to Nova
from the Kerbside work, and it was refined and landed with the Nova team
through the [2025.1 specification][spec], shipping in Epoxy. It is
deliberately general -- any protocol-aware proxy could answer it -- but
Kerbside is the proxy it was designed alongside.

The contributions tracked here fall into three groups:

- **Nova**: `spice-direct` itself, which has landed, and follow-on work such
  as extra specs for the sound model and USB redirection.
- **Clients**: `spice-direct` support in openstacksdk and
  python-openstackclient, and other support for new Nova API microversions.
- **Deployment**: Kolla and Kolla-Ansible changes which deploy Kerbside as a
  component of the cloud. The Kolla image build has merged; the Kolla-Ansible
  deployment is in review under the [`spice-direct-consoles`][topic] topic.
  Any other deployment system wishing to include Kerbside would need similar
  changes.

The rest are ancillary changes -- things which helped me debug along the way,
and that sort of thing.

These patches last successfully applied via CI on 4 October 2026. When this occurs,
the SHAs the patches were applied to for each project are recorded in the
relevant config.yaml file, and will be used for patch applications until
updated.

# Status

The core Nova API has landed, and Kerbside's `/nova-console.vv` endpoint
implements the `spice-direct` token exchange against it. Kerbside's own CI
exercises that exchange on every merge against an all-in-one Kolla-Ansible
deployment built from this repository. Kerbside as a whole is still
experimental, and the patches here are carried against upstream branches while
they are in review, so treat them as a way to try Kerbside rather than a
production deployment path. Reach out if you want more details.

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

Supported releases are Nova 2025.1 (Epoxy) onwards, the first release with the
`spice-direct` console type.

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
- [Security scanning][security] -- the gitleaks credential scan, CodeQL, and
  how to accept a finding.
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

[spec]: https://specs.openstack.org/openstack/nova-specs/specs/2025.1/implemented/libvirt-spice-direct-consoles.html
[topic]: https://review.opendev.org/q/topic:spice-direct-consoles
[docs]: https://github.com/shakenfist/kerbside-patches/tree/develop/docs
[building]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/building.md
[scripts]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/script-reference.md
[ci-data]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/ci-data.md
[security]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/security-scanning.md
[zuul]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/kolla-ansible-tempest-jobs.md
[devmode]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/kolla-devmode.md
[gerrit]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/gerrit-api.md
[tactics]: https://github.com/shakenfist/kerbside-patches/blob/develop/docs/tactics.md
[claude-code]: https://claude.com/claude-code
