# Script reference

This repository contains numerous helper scripts in the `_build/` and `tools/`
directories. This page documents each script and its purpose.

## Build Scripts (`_build/`)

### Core Build Scripts

| Script | Description |
|--------|-------------|
| `assemble-source.sh <release>` | Clones upstream OpenStack repositories and applies patches for the specified release (e.g., `master`, `2024.1`, `2024.2`). Creates the `src/` directory with patched source trees. |
| `build-containers.sh` | Builds Kolla container images using the patched source. Handles registry authentication and multi-release builds. Called by `buildall.sh`. |
| `imagebuild.sh` | Core container image build logic. Prepares artifacts, runs `kolla-build`, and manages image tagging. |
| `imagearchive.sh` | Archives built container images to `archive/imgs/` with SBOM generation. Exports patched source to `archive/src/`. |
| `debsecan-report.sh` | Scans built container images for known security vulnerabilities using `debsecan`. Extracts the dpkg database from each image and scans externally -- the pushed images are never modified. Supports Debian and Ubuntu; skips non-Debian-derived images. Reports saved to `archive/debsecan/`. |
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
| `bump-source-shas.sh [N]` | Updates `source_sha` in all project `config.yaml` files. With a positional arg N, sets to the Nth most recent upstream commit (default: 1 = HEAD). With `--forward N`, steps forward N commits from the current SHA. With `--changelog <path>`, writes a per-project summary of new upstream commits (short hashes + oneline messages) to the specified file. |
| `analyze-shared-patches.py <results.json>` | Analyzes failing patches to determine fix strategy (`modify_in_place` vs `create_copy`) based on whether patches are shared across releases. |
| `find-patch-usage.py <patch-file>` | Finds all ORDER files that reference a given patch. Returns JSON with list of projects using the patch. |
| `get-next-patch-number.py` | Returns the next available patch number by checking both existing files in `_patches/` and open GitHub PRs. |
| `release-names.yaml` | Maps OpenStack numeric release versions to codenames (e.g., `2025.1` → `epoxy`). |

### Setup and Configuration Scripts

| Script | Description |
|--------|-------------|
| `setup-local-build-environment.sh` | Installs build dependencies on Debian systems (Docker, Python packages, etc.). |
| `install-build-dependencies.sh` | Installs Python build dependencies in the current environment. |
| `add-insecure-ci-registry.sh` | No-op kept for compatibility. The CI registry is now served over HTTPS. |
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

### Layer Analysis Tools

| Script | Description |
|--------|-------------|
| `summarize_layers.py` | Analyzes the per-image layer data time series in `data/layers/`. The `growth` report tracks image size over time and attributes growth to specific layers, the `reuse` report tracks layer reuse between builds, and the `stages` report compares the effect of each occystrap pipeline stage. Use `--build` and `--image` to narrow the analysis. |
| `collect-layer-data.py` | Used by the `collect_layer_data` CI job to convert a build's `layers.tar.gz` artifact into appends to the per-image time series files in `data/layers/`. |

### Automated Lint Fixing

| Script | Description |
|--------|-------------|
| `check-kolla-ansible-lint.sh [projects...]` | Lightweight lint checker that applies kolla-ansible patches (without tests) and runs `tox -elinters`. Used by the `lint_check` CI job. Exits 0 if clean, 1 if errors found. |
| `fix-lint-with-claude.sh [options] [projects...]` | Auto-fixes ansible-lint errors in patch files using Claude Code. Applies patches, runs linters, builds a prompt that explains the patch-file indirection, runs Claude, verifies the fix, and commits/pushes. Options: `--ci`, `--interactive`, `--no-push`, `--no-commit`, `--max-turns N`. |

### Pre-Push Linting for Gerrit

| Script | Description |
|--------|-------------|
| `gerrit-pre-push-lint` | Pre-push linter for OpenStack Gerrit submissions. Checks for common issues that reviewers flag during code review (missing release notes, bug references, YAML line length, Jinja2 issues, etc.). Supports checking stacked patches with `--stack N` or `--range origin/master..HEAD`. |
| `analyze-gerrit-review-times` | Analyzes Gerrit review timestamps to find optimal posting times. Fetches recent merged reviews and shows when reviewers are most active by hour and day of week. |
| `analyze-gerrit-patch-size` | Analyzes correlation between patch size, series length, and review response. Shows how patch complexity affects time to review, merge, and number of revision cycles needed. |
| `analyze-gerrit-new-roles` | Analyzes historical patterns for new Ansible role additions. Shows successful strategies, series vs standalone patterns, and recommendations for getting new roles merged. |

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

### Security Scanning

| Script | Description |
|--------|-------------|
| `gitleaks-scan.sh` | Scans every commit reachable from `HEAD` for leaked credentials, against `.gitleaks.toml`. Runs a positive control first -- it plants an AWS access key id and an SSH private key and fails if gitleaks does not report both -- so a clean scan means "found nothing" rather than "did nothing". Refuses to run against a shallow clone. Takes `--gitleaks PATH` to use a specific binary. See [security-scanning.md](security-scanning.md). |

### Pre-commit Hooks

This repository uses [pre-commit](https://pre-commit.com/) to validate files
before commits. To set up pre-commit hooks locally:

```
pip install pre-commit
pre-commit install
```

These checks also run automatically in CI on pull requests, so even if you don't
install the hooks locally, your PR will be checked.

The configured hooks include:

| Hook | Description |
|------|-------------|
| `actionlint` | Lints GitHub Actions workflow files for syntax errors, invalid expressions, and other issues. Custom runner labels are configured in `.github/actionlint.yaml`. |
| `shellcheck` | Static analysis tool for shell scripts. Checks scripts in `_build/` and `tools/` directories for common issues. Configuration is in `.shellcheckrc`. |
| `skillsaw` | Lints the agent context in this repository -- `CLAUDE.md`, `AGENTS.md` and the skills under `.claude/` -- for malformed skills, smuggled unicode and pasted credentials. The pin is kept current by renovate's pre-commit manager. |

### Git Hooks and Utilities

| Script | Description |
|--------|-------------|
| `commit-msg.hook` | Git commit-msg hook for validating commit message format. |
| `extract-commit-message` | Extracts commit messages from patch files. |
| `find_changes` | Lists changed files for review. |
| `upgrade-python` | Helper for upgrading Python dependencies. |

