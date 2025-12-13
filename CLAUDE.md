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
# Test patch application only (no tests, fast)
./_build/test-apply.sh --skip-tests <project-directory>
# e.g., ./_build/test-apply.sh --skip-tests kolla-ansible

# Full application with test suites (slow, but catches linter/style issues)
./_build/test-apply.sh <project-directory>
# e.g., ./_build/test-apply.sh kolla-ansible
```

### Run Tests
Tests run automatically during `test-apply.sh`. Control with flags:
- `--skip-tests`: Skip all testing (fast, only tests patch application)
- `--defer-tests`: Defer testing until all patches applied
- `--test-patch <name>`: Only run tests for patches matching `<name>` (substring match)
- Tests include: `tox -epy3`, `tox -epep8`, `tox -efunctional` (Nova only), `tox -elinters` (Kolla-Ansible)

**Testing a specific failing patch** (useful when CI identifies a failing patch):
```bash
# Test only patch115 without running tests for other patches or upstream
./_build/test-apply.sh --test-patch patch115 kolla-ansible
```

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

### Editing Patch Files Directly

When editing `.patch` files in `_patches/`, you must update both the content AND the diff header in a single edit:

1. **Diff headers** look like `@@ -0,0 +1,54 @@` where:
   - First pair (`-0,0`): line number and count in original file
   - Second pair (`+1,54`): line number and count in new file
   - If adding N lines to a hunk, increment the second count by N

2. **Always edit header and content together** to keep them synchronized

3. **Example**: Adding a `name:` line to an Ansible task requires:
   ```diff
   # Before: @@ -0,0 +1,2 @@
   # After:  @@ -0,0 +1,3 @@  (incremented by 1)
   ```

4. **Common linter issues**:
   - `ansible-lint name[missing]`: All tasks need a `name:` attribute
   - Kolla-Ansible runs `tox -elinters` which includes ansible-lint

5. **Verify changes** by running `test-apply.sh` without `--skip-tests`
