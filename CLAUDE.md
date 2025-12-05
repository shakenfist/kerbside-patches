# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kerbside-patches maintains patches against OpenStack components to enable native SPICE console functionality. The patches primarily target Nova, Kolla, and Kolla-Ansible. This repository also contains container image build infrastructure for Kolla-Ansible deployments.

**Important**: These patches are for proof-of-concept/development use. The only tested container OS is Debian (RHEL/Rocky dropped SPICE support).

## Build Commands

### Setup Build Environment (Debian)
```bash
./_build/setup-local-build-environment.sh
# May need to logout/login to pick up docker group membership
```

### Assemble Patched Source Tree
```bash
# Creates src/ directory with patched OpenStack components
_build/assemble-source.sh <release>   # e.g., master, 2024.1, 2024.2
```

### Build Container Images
```bash
./buildall.sh --build-targets "master"
# Override defaults:
./buildall.sh --build-targets "2024.1" --build-images "all"
```

### Apply Patches to a Single Project
```bash
_build/apply-patches-and-test.sh <project-directory>
# e.g., _build/apply-patches-and-test.sh kolla-ansible
```

### Run Tests
Tests run automatically during patch application. Control with flags:
- `--skip-tests`: Skip all testing
- `--defer-tests`: Defer testing until end
- Tests include: `tox -epy3`, `tox -epep8`, `tox -efunctional` (Nova only), `tox -elinters` (Kolla-Ansible)

## Repository Architecture

### Patch Organization
Each OpenStack component has its own directory (e.g., `kolla/`, `kolla-ansible/`, `nova-2025.1/`):
- `config.yaml`: Repository URL, branch, SHA, release version, dependencies
- `ORDER`: List of patch files to apply (from `_patches/`) in sequence
- `FORCE`: Optional file to force inclusion even with no patches

### Patch Files
Located in `_patches/`:
- `patchNNN-description.patch`: The actual patch file
- `patchNNN-description.patch-message`: Optional commit message override

### Build Scripts (`_build/`)
- `common.sh`: Shared functions, command-line parsing, color output helpers
- `assemble-source.sh`: Orchestrates cloning repos and applying patches
- `apply-patches-and-test.sh`: Applies patches from ORDER file, runs tests
- `build-containers.sh`: Builds Kolla container images
- `imagebuild.sh`: Container image build logic

### Tools (`tools/`)
- `find_images`: GitLab Container Registry image finder (Python/Click CLI)
- `bootstrap-kolla-ansible`: Kolla-Ansible deployment helper
- `test-console`: SPICE console testing utility

## Key Configuration

### Build Defaults (from `common.sh`)
- `build_targets`: "2023.1 2023.2 2024.1 master"
- `distro`: "debian"
- `build_images`: "nova-compute nova-libvirt nova-api kerbside"

### Globals.yml
Contains Kolla-Ansible configuration for test deployments. Key settings:
- `kolla_base_distro: "debian"`
- `nova_console: "spice"`
- `enable_kerbside: "yes"`

## Working with Patches

1. Patches reference upstream OpenStack repos at specific SHAs (in `config.yaml`)
2. The `source_sha` field pins the exact upstream commit
3. To update patches for a new upstream version, update `source_sha` and regenerate patches
4. Patch dependencies between projects are specified in `depends_on` field
