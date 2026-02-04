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
  pushes via occystrap
- `imagebuild.sh` -- runs kolla-build
- `imagearchive.sh` -- archives images with SBOMs

The image push pipeline in `build-containers.sh` uses
occystrap with inspect filters between optimization filters
to collect layer metadata.

### Working with Layer Data

Layer metadata is collected during CI builds via occystrap's
inspect filter. Data flows:

1. Per-image JSONL files written at each pipeline stage
2. Files packaged into `layers.tar.gz` tarball
3. Tarballs stored in `data/` via automated PR
4. `tools/summarize_layers.py` analyzes the tarballs

Pipeline stages: `as-built`, `post-normalize`, `post-exclude`

### Modifying GitHub Actions Workflows

The main workflow is `.github/workflows/functional-tests.yml`.
It is large and has several jobs:

- `build` -- builds container images (matrix strategy)
- `deploy` -- deploys with kolla-ansible and runs tests
- `collect_layer_data` -- aggregates layer data from builds

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
