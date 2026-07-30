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

These patches last successfully applied via CI on 30 July 2026. When this occurs,
the SHAs the patches were applied to for each project are recorded in the
relevant config.yaml file, and will be used for patch applications until
updated.

## Not for production use

These patches were developed while building the Kerbside proof of concept. While
the core API patches have now landed in Nova, there is no client or deployer
support yet, and Kerbside itself needs to be updated to match what landed in
Nova. Reach out if you want more details.

## Kolla container operating system

Because RHEL 9 dropped support for SPICE in KVM / qemu, and the downstream
redistributions such as Rocky Linux followed suit, the only tested container
operating system for these patches is Debian. While it is technically feasible
to add back SPICE into Rocky with custom packages, that work has not been
attempted. Additionally, Kolla-Ansible does not support running a mix of
container operating systems for your deployment. Therefore, you need to use
Debian for all container images in a deployment using Kerbside, even though
only the Nova / LibVirt containers are customized by these patches.

## Building patched container images

The short version, on a prepared build host:

```
_build/assemble-source.sh master   # clone upstreams, apply patches
./buildall.sh --build-targets "master"
```

See
[docs/building.md](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/building.md)
for host setup on Debian and Rocky, the full build walkthrough,
deploying the resulting images with Kolla-Ansible, and the debsecan
vulnerability scan that runs after each build.

## Documentation

In the [docs/](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/index.md)
directory:

- [Documentation Index](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/index.md) - Where to start
- [Building](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/building.md) - Host setup, the build flow, deployment, and vulnerability scanning
- [Script Reference](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/script-reference.md) - Every helper script in `_build/` and `tools/`
- [CI Data](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/ci-data.md) - Layer data collection and OpenDev CI reliability reporting
- [Kolla-Ansible Tempest Jobs](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/kolla-ansible-tempest-jobs.md) - Adding Kerbside-enabled Zuul jobs upstream
- [Devmode](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/kolla-devmode.md) - Rapid kerbside iteration in a deployed environment
- [Gerrit API](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/gerrit-api.md) - Querying review.opendev.org
- [Tactics](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/tactics.md) - Getting Kolla patches reviewed quickly

Project reference files:

- [ARCHITECTURE.md](https://github.com/shakenfist/kerbside-patches/blob/develop/ARCHITECTURE.md) - How the patch archive and build tooling fit together
- [AGENTS.md](https://github.com/shakenfist/kerbside-patches/blob/develop/AGENTS.md) - Guide for AI coding assistants
