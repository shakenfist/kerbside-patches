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

These patches last successfully applied via CI on 19 January 2026. When this occurs,
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

# Container image build

Given I am using Kolla-Ansible for testing, this respository also contains
scripts to build container images suitable for use with Kolla-Ansible.

This process is automated for gitlab users using the included `.gitlab-ci.yml`
configuration file, and for github users with the included github workflows
under `.github/workflow`.

## Debian host OS setup

On Debian, you can build patched container images by running
`./_build/setup-local-build-environment.sh`.

Note that you might need to logout / in to pick up the group change associated
with installing docker. Now continue to the shared steps below.

## Rocky host OS setup

On Rocky, you can build patched container images like this:

```
# Basic build configuration
sudo dnf update -y
sudo dnf install -y epel-release
sudo dnf config-manager --set-enabled crb

sudo dnf install -y moreutils pkg-config python3-lxml libxml2-devel libxslt jq
sudo dnf remove python3-virtualenv
sudo pip3 install tox yq occystrap virtualenv

# Install a recent Docker
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install docker-ce docker-ce-cli containerd.io
sudo systemctl start docker

# Allow the current user to access docker
sudo usermod -a -G docker $(whoami)

# Note that you might need to logout / in to pick up the group change
```

Now continue to the shared steps below.

## Shared build steps

Now run these commands, regardless of host OS:

```
# Clone the Kerbside patches repository
git clone https://github.com/shakenfist/kerbside-patches
cd kerbside-patches

# Make a place to store the source for kerbside and patched OpenStack components
mkdir src
cd src

# Clone the kerbside source, needed for container image build later
git clone https://github.com/shakenfist/kerbside
tar cvf kerbside.tgz kerbside
cd ..

# Apply patches to upstream projects for your chosen release. Supported
# releases here at the moment are 2024.1, 2024.2, and master. However, the
# patches against 2024.1 and 2024.2 have been dropped, so you'll just be
# building a local version of the pure upstream containers with those.
_build/assemble-source.sh master

# At the end you should see this:
#
# ==================================================
# All patches applied correctly.
# ==================================================

# Now we can build images. Note that you can use --build-targets and
# --build-images to override the default behaviour. So for example this
# would build _all_ container images for 2024.1, but not for any other release:
#     ./buildall.sh --build-targets "2024.1" --build-images "all"

./buildall.sh --build-targets "2024.1"
```

At the end you should see output like this:

```
Export patched source code to archive/src
→...kerbside-e1632d4.tgz
→...kolla-ansible-e1632d4.tgz
→...nova-e1632d4.tgz
→...openstacksdk-e1632d4.tgz
→...oslo.config-e1632d4.tgz
→...python-novaclient-e1632d4.tgz
→...python-openstackclient-e1632d4.tgz
→...kolla-e1632d4.tgz

==================================================
Archival complete.
    Total archive size: 67G archive
==================================================
```

# To actually deploy Kolla-Ansible

In order to deploy Kolla-Ansible with our newly built images, we need to import
those images into our local docker registry so that Kolla-Ansible can use them
for a deploy. Note that the SHA variable below is set to match the value at the
end of the filename in the output above. Note that my docker registry in this
example is running on the same host I did the build on, and is on port 4000.
Now we can do a tag and push:

```
sha="e1632d4"
release="2024.1"
debian_codename="bookworm"
for name in $(docker image list | grep ${sha} | cut -f 1 -d " " | cut -f 2 -d "/"); do
   sudo docker image tag kolla/${name}:${release}-${sha} \
       127.0.0.1:4000/openstack.kolla/${name}:${release}-debian-${debian_codename}
   sudo docker image push \
       127.0.0.1:4000/openstack.kolla/${name}:${release}-debian-${debian_codename}
done
```

Now we need to have a patched version of Kolla-Ansible installed somewhere so
we can do the deployment. I do it this way, although you might want to use a venv
for this:

```
cd src/kolla-ansible
sudo python3 setup.py develop
```

You'll need a `globals.yml` in `/etc/kolla`, but that's site specific so I don't
want to include much detail here. Refer to the Kolla-Ansible documentation for
more details. Then I deploy like this:

```
sudo kolla-ansible -i /etc/kolla/inventory deploy
```

# Script Reference

This repository contains numerous helper scripts in the `_build/` and `tools/`
directories. This section documents each script and its purpose.

## Build Scripts (`_build/`)

### Core Build Scripts

| Script | Description |
|--------|-------------|
| `assemble-source.sh <release>` | Clones upstream OpenStack repositories and applies patches for the specified release (e.g., `master`, `2024.1`, `2024.2`). Creates the `src/` directory with patched source trees. |
| `build-containers.sh` | Builds Kolla container images using the patched source. Handles registry authentication and multi-release builds. Called by `buildall.sh`. |
| `imagebuild.sh` | Core container image build logic. Prepares artifacts, runs `kolla-build`, and manages image tagging. |
| `imagearchive.sh` | Archives built container images to `archive/imgs/` with SBOM generation. Exports patched source to `archive/src/`. |
| `common.sh` | Shared functions, command-line argument parsing, color output helpers, and default build configuration used by all other scripts. |

### Patch Testing Scripts

| Script | Description |
|--------|-------------|
| `apply-patches-and-test.sh <project>` | Applies patches from a project's ORDER file and optionally runs test suites (`tox -epy3`, `tox -epep8`, etc.). |
| `test-apply.sh [--skip-tests] <project>` | Wrapper for `apply-patches-and-test.sh`. Use `--skip-tests` for fast patch-only testing without running test suites. |
| `test-patches-for-ci.sh [projects...]` | CI-friendly patch testing that outputs JSON results. Tests all projects if none specified. Used by automated rebase workflows. |
| `extract-patch-failures.py` | Parses JSON output from `test-patches-for-ci.sh` into human-readable failure details. |

### Automated Rebase Scripts

| Script | Description |
|--------|-------------|
| `rebase-with-claude.sh [options] [projects...]` | Unified rebase helper for both CI and CLI use. Tests patches, analyzes failures, and invokes Claude Code to auto-fix. Options: `--bump-shas` (update to HEAD), `--step-forward N` (advance N commits from current), `--no-claude`, `--interactive`, `--ci`. |
| `bump-source-shas.sh [N]` | Updates `source_sha` in all project `config.yaml` files. With a positional arg N, sets to the Nth most recent upstream commit (default: 1 = HEAD). With `--forward N`, steps forward N commits from the current SHA. |
| `analyze-shared-patches.py <results.json>` | Analyzes failing patches to determine fix strategy (`modify_in_place` vs `create_copy`) based on whether patches are shared across releases. |
| `find-patch-usage.py <patch-file>` | Finds all ORDER files that reference a given patch. Returns JSON with list of projects using the patch. |
| `get-next-patch-number.py` | Returns the next available patch number by checking both existing files in `_patches/` and open GitHub PRs. |
| `release-names.yaml` | Maps OpenStack numeric release versions to codenames (e.g., `2025.1` → `epoxy`). |

### Setup and Configuration Scripts

| Script | Description |
|--------|-------------|
| `setup-local-build-environment.sh` | Installs build dependencies on Debian systems (Docker, Python packages, etc.). |
| `install-build-dependencies.sh` | Installs Python build dependencies in the current environment. |
| `add-insecure-ci-registry.sh` | Configures Docker to allow insecure registry access for CI environments. |
| `calculate-container-hash.sh <dirs...>` | Calculates a unique hash for container builds based on source content and date. Used to avoid redundant rebuilds in CI. |

## Deployment Tools (`tools/`)

### Kolla-Ansible Deployment

| Script | Description |
|--------|-------------|
| `bootstrap-kolla-ansible` | Bootstraps Kolla-Ansible into a virtualenv and prepares for deployment. |
| `postinstall-kolla-ansible` | Post-installation tasks after Kolla-Ansible deployment. |
| `install-openstack-clients` | Installs patched OpenStack clients (openstacksdk, python-openstackclient) from source. |
| `ka` | Shortcut wrapper for running kolla-ansible commands. |

### Testing and Debugging

| Script | Description |
|--------|-------------|
| `test-console` | Tests SPICE console functionality by creating a VM and verifying console access. Downloads test images and configures Glance. |
| `inspect-kerbside` | Inspects the state of Kerbside deployment (containers, databases, logs). |
| `gather-logs` | Collects logs from Kolla containers for debugging. |
| `spice-connect.py` | Python script for testing SPICE console connections programmatically. |

### Certificate and Network Configuration

| Script | Description |
|--------|-------------|
| `add-ca-certificate <cert-file>` | Adds a CA certificate to the system trust store. Works on Debian and RHEL-based systems. |
| `set-ca-path` | Sets the CA certificate path for OpenStack client connections. |
| `add_nodes_to_etc_hosts` | Adds cluster nodes to `/etc/hosts` for name resolution. |

### Container Registry Tools

| Script | Description |
|--------|-------------|
| `find_images` | Python CLI tool for finding images in GitLab Container Registry. Searches by tag patterns and lists available images. |

### Git Hooks and Utilities

| Script | Description |
|--------|-------------|
| `commit-msg.hook` | Git commit-msg hook for validating commit message format. |
| `extract-commit-message` | Extracts commit messages from patch files. |
| `find_changes` | Lists changed files for review. |
| `upgrade-python` | Helper for upgrading Python dependencies. |
