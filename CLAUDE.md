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
- `test-patches-for-ci.sh`: CI-friendly patch testing with JSON output
- `extract-patch-failures.py`: Parse JSON failures into human-readable format
- `bump-source-shas.sh`: Update source SHAs to latest upstream commits

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

## CI/CD Automation

### Daily Rebase Workflow

The `daily-rebase-checks.yml` workflow runs daily to:
1. Update source SHAs to latest upstream commits
2. Test if patches still apply cleanly
3. If patches fail, invoke Claude Code to attempt automatic fixes
4. Create a PR with the updated SHAs (and any auto-fixed patches)
5. Create an issue if patches cannot be fixed automatically

### Required Secrets

- `DAILY_REBASE_TOKEN`: GitHub token for creating PRs and issues

### Claude Code CLI

The workflow uses the Claude Code CLI (`claude`) in headless mode for auto-fixing
patches. This requires a dedicated runner with the `claude-code` label that has:

1. Claude Code CLI pre-installed (`npm install -g @anthropic-ai/claude-code`)
2. Pre-authenticated via `claude login` (uses Claude Max subscription)

The runner must have valid credentials in `~/.claude/` for the user running jobs.

### CI Scripts for Patch Testing

```bash
# Test all projects and output JSON results
./_build/test-patches-for-ci.sh

# Test specific projects
./_build/test-patches-for-ci.sh kolla kolla-ansible

# Parse failure output for human reading
cat patch-results.json | ./_build/extract-patch-failures.py
```

The JSON output format:
```json
{
  "success": false,
  "projects_tested": ["kolla", "kolla-ansible"],
  "failures": [
    {
      "project": "kolla",
      "patch": "_patches/patch112-kolla-layer-data.patch",
      "error": "error: patch failed: kolla/common/config.py:271..."
    }
  ]
}
```

### Shared Patch Handling

Patches in `_patches/` may be referenced by multiple ORDER files (e.g., the same
patch used by `kolla-ansible`, `kolla-ansible-2024.1`, and `kolla-ansible-2025.1`).
When a patch fails during daily rebase, the fix strategy depends on whether the
patch is shared:

**Check patch usage:**
```bash
./_build/find-patch-usage.py _patches/patch008-use-routable-ip.patch
# Output: {"patch": "...", "used_by": ["kolla-ansible", "kolla-ansible-2025.1"]}
```

**Fix strategies:**

1. **modify_in_place**: If the patch is only used by ONE project (or only the
   failing project needs different code), edit the patch directly.

2. **create_copy**: If the patch is shared across multiple releases and only one
   release needs changes, create a release-specific copy:
   - Use the next available patch number (see below)
   - Use the codename in the filename (e.g., `epoxy` for 2025.1)
   - Update the ORDER file for the failing project to use the new patch
   - Leave the original patch unchanged for other releases

**Naming convention for release-specific patches:**
```
patch{number:03d}-{project}-{codename}-{description}.patch
Example: patch118-kolla-ansible-epoxy-compressed-zstd.patch
```

**Release name mappings** (see `_build/release-names.yaml`):
- 2024.1 = caracal
- 2024.2 = dalmatian
- 2025.1 = epoxy
- 2025.2 = flamingo
- 2026.1 = gazpacho
- master = master

**Get the next available patch number:**
```bash
./_build/get-next-patch-number.py
# Output: 118
```

This script checks both existing files in `_patches/` AND open GitHub PRs to
avoid conflicts with patches being added by other work.

**Analyze failing patches for fix strategy:**
```bash
./_build/analyze-shared-patches.py patch-test-results.json
```

Output includes recommended strategy, suggested names, and next patch number:
```json
{
  "failures": [
    {
      "patch": "_patches/patch008.patch",
      "failed_in": "kolla-ansible-2025.1",
      "also_used_by": ["kolla-ansible", "kolla-ansible-2024.2"],
      "strategy": "create_copy",
      "suggested_name": "_patches/patch118-kolla-ansible-epoxy-use-routable-ip.patch"
    }
  ],
  "next_patch_number": 119
}
