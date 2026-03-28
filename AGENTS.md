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

### Working with Layer Data

Layer metadata is collected during CI builds via occystrap.
In proxy mode, a single JSONL file captures all images. In
sequential mode, per-image per-stage JSONL files are produced
via occystrap's inspect filter. Data flows:

1. JSONL files written during push
2. Files packaged into `layers.tar.gz` tarball
3. Tarballs stored in `data/` via automated PR
4. `tools/summarize_layers.py` analyzes the tarballs

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
- `trigger-downstream.yml` -- triggers kerbside CI on push
  to develop (e.g. after daily rebase PR merges)
- `local-container-builds.yml` -- tests local builds work
- `daily-rebase-checks.yml` -- daily upstream rebase with
  Claude-assisted patch fixing

**Runner types and constraints:**

- `vm` runners -- ephemeral VMs with full sudo access
- `metal` runners -- persistent bare-metal machines without
  passwordless sudo for the CI user
- `static` runners -- persistent machines without passwordless
  sudo for the CI user

When adding steps that need elevated privileges, guard them
with `if: "!contains(matrix.test.infrastructure, 'metal')"`
or use user-level alternatives (e.g. `~/.config/pip/pip.conf`
instead of `/etc/pip.conf`).

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
- **Analyze layer optimization**: `tools/summarize_layers.py
  -d data/ --compare-stages`
