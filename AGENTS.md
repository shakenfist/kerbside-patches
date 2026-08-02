# Agents Guide

This document provides guidance for AI agents working on
the kerbside-patches codebase.

## Project Overview

Kerbside-patches maintains patches against OpenStack
components (Nova, Kolla, Kolla-Ansible) to enable native
SPICE console functionality. It also contains container image
build infrastructure for Kolla-Ansible deployments.

## Key Patterns

### Adding a New Patch

1. Create `_patches/patchNNN-description.patch`
2. Add it to the appropriate project's `ORDER` file
3. Optionally create a `.patch-message` file for commit
   message override
4. Use `_build/get-next-patch-number.py` to find the next
   available number (checks both files and open PRs)

### Modifying Build Pipeline

Build scripts live in `_build/`. Key files:

- `common.sh` -- shared functions and CLI argument parsing
- `build-containers.sh` -- orchestrates image builds and
  pushes via occystrap (proxy or sequential mode)
- `imagebuild.sh` -- runs kolla-build (supports `--push`
  and `--registry` for proxy mode)
- `imagearchive.sh` -- archives images with SBOMs
- `debsecan-report.sh` -- scans built images for known
  CVEs using debsecan (non-destructive, images unchanged)

In CI, `--use-proxy` starts an occystrap filtering proxy
on localhost:5050 before building. kolla-build pushes
images directly to the proxy, which filters and forwards
to the CI registry. If the proxy fails to start, the
build falls back to the sequential `occystrap process`
push loop.

### The Assembled Source Tree

`_build/assemble-source.sh` clones the upstream OpenStack
repos into `src/` and applies our patch stack to them. That
tree is generated output, not source: it is regenerated from
scratch on every build and is listed in `.gitignore`.

- Never edit `src/` to change patched behaviour, and never
  commit from it. Edits belong in `_patches/`; regenerate
  with `_build/test-apply.sh` to see them applied.
- Each subdirectory (`src/kolla`, `src/kolla-ansible`, ...)
  is a separate git repository with its own `.git`. The
  `git add -A .` and commit calls in
  `_build/apply-patches-and-test.sh` run inside those repos,
  so this repository's `.gitignore` does not affect them.
- Because `src/` is ignored, a finished build worktree can be
  torn down with a plain `git worktree remove`, with no
  `--force` needed.

### Working with Layer Data

Layer metadata is collected during CI builds via occystrap
inspect filters at three pipeline stages (as-built,
post-normalize, post-exclude). In proxy mode, all images
append to combined per-stage JSONL files. In sequential mode,
per-image per-stage JSONL files are produced. Data flows:

1. JSONL files written during push
2. Files packaged into `layers.tar.gz` tarball
3. CI uploads the tarball as a build artifact
4. `tools/collect-layer-data.py` merges the stages into one
   record per image and appends it to the time series file
   `data/layers/<build-name>/<image>.jsonl` via automated PR
5. `tools/summarize_layers.py` analyzes the time series
   (growth, reuse and stage-comparison reports)

### Modifying GitHub Actions Workflows

The main workflow is `.github/workflows/functional-tests.yml`.
It is large and has several jobs:

- `build_images` -- builds container images (matrix strategy)
- `test_installs` -- deploys with kolla-ansible and runs tests
- `collect_layer_data` -- aggregates layer data from builds

The deploy steps (bootstrap, prechecks, pull, deploy,
install-clients, post-install) are shared with kerbside's
CI via the `shakenfist/actions/deploy-kolla-ansible` composite
action. Changes to the deploy flow should be made in that
action, not inlined in the workflow.

Other workflows:
- `rebase-tests.yml` -- applies the patch stack and runs the
  test suites (`_build/test-apply.sh`) for kolla and
  kolla-ansible on both debian-13 and rocky-9 VM runners
- `trigger-downstream.yml` -- triggers kerbside CI on push
  to develop (e.g. after daily rebase PR merges)
- `local-container-builds.yml` -- tests local builds work
- `daily-rebase-checks.yml` -- daily upstream rebase with
  Claude-assisted patch fixing
- `ci-reporting.yml` -- on-demand refresh of the OpenDev CI
  reliability data in `data/ci-reporting/`. A workflow_dispatch
  dropdown picks the report; the report catalogue is in
  `tools/ci-report.sh` (mariadb-ist, wsrep-sync-fatal,
  libvirt-limit) and the shared scan/chart engine is
  `tools/count_ci_log_errors.py`. Incremental via committed
  per-report checkpoints so OpenDev is never re-scraped; uploads
  the refreshed data as a workflow artifact and proposes a
  data-update PR. The scan runs on a vm runner but the PR step
  runs on a static runner, because `gh` is only installed on
  static runners (same split as the layer data flow)
- `heal-data-prs.yml` -- runs on every push to develop and
  union-merges develop into any automated data PR that GitHub
  reports as conflicted. Data PRs append records to shared
  time-series files under `data/`, so concurrent PRs always
  conflict once one merges; the union of both sides' appended
  lines is always the correct resolution. The merge is verified
  append-only and well formed (`tools/verify-data-merge.py`)
  before being pushed (`tools/heal-data-prs.sh`)

**Runner types and constraints:**

- `vm` runners -- ephemeral VMs with full sudo access,
  provisioned on demand by the conductor (shakenfist/private-ci)
  per `runs-on` labels `[self-hosted, vm, <os>, <size>]`. OS
  labels are `debian-13` and `rocky-9` (`debian-12` images still
  exist but kolla-ansible master no longer supports bookworm, so
  new jobs should use `debian-13`); sizes are `s`, `m`, `xl`.
- `static` runners -- persistent machines without passwordless
  sudo for the CI user
- `claude-code` -- persistent runner with the Claude Code CLI,
  used by the automated fixers

All test jobs run directly on a `vm` runner of the OS under test
(there are no longer any bare-metal runners or nested Shakenfist
VMs). The runners have passwordless sudo, so privileged steps are
fine; `tools/ci-install-test-deps.sh` installs the apt-vs-dnf
dependencies and, on rocky-9, upgrades Python to 3.12 via
`tools/upgrade-python`.

Python CLI tooling (tox, yq, occystrap, clingwrap, ...) is never
installed into the system Python: pip cannot upgrade distro-owned
packages, so system installs break on every distro upgrade. Instead
`_build/setup-tools-venv.sh` maintains a shared venv at
`/srv/shakenfist/kerbside-patches-tools` and symlinks its console
scripts into `/usr/local/bin`. `_build/common.sh` (and the lint/test
entry points) activate that venv when it exists; the symlinks cover
everything else. Add new Python CLI tools via that helper, not pip.

### Documentation

- **Do not edit README.md directly.** Edit `README.md.tmpl`
  instead. The daily rebase workflow regenerates `README.md`
  from the template, replacing `%%date%%` with the current
  date. Direct edits to `README.md` will be lost during
  regeneration.
- Keep both files in sync when making manual changes.

## Testing

- **Patch application**: `./_build/test-apply.sh --skip-tests
  <project>`
- **Full test suite**: `./_build/test-apply.sh <project>`
- **Pre-commit**: `pre-commit run --all-files` (runs
  actionlint and shellcheck)
- **CI patch testing**: `./_build/test-patches-for-ci.sh`

## Common Tasks

- **Rebase patches on new upstream**: Use
  `_build/rebase-with-claude.sh --bump-shas`
- **Test a specific patch**: `_build/test-apply.sh
  --test-patch patchNNN <project>`
- **Check shared patch usage**: `_build/find-patch-usage.py
  _patches/patchNNN.patch`
- **Analyze layer optimization**: `tools/summarize_layers.py`
  (add `--report growth`, `--report reuse` or
  `--report stages` to narrow the output)
