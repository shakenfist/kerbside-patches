# Architecture

Kerbside-patches is a patch management and container build
system for OpenStack components. It maintains patches that
enable native SPICE console support and builds Kolla
container images with those patches applied.

## Directory Structure

```
kerbside-patches/
    .github/
        workflows/
            functional-tests.yml  # CI: build, deploy, test
            daily-rebase-checks.yml  # Daily upstream rebase
    _build/                  # Build and CI scripts
        common.sh            # Shared functions and CLI parsing
        assemble-source.sh   # Clone repos, apply patches
        build-containers.sh  # Build + push images via occystrap
        imagebuild.sh        # Run kolla-build
        imagearchive.sh      # Archive images with SBOMs
        test-apply.sh        # Test patch application
        test-patches-for-ci.sh  # CI-friendly patch testing
        rebase-with-claude.sh   # Automated rebase with Claude
        bump-source-shas.sh    # Update upstream SHAs
    _patches/                # All patch files (shared pool)
        patchNNN-desc.patch  # Patch files
        patchNNN-desc.patch-message  # Optional commit messages
    data/                    # Layer data tarballs from CI
    docs/                    # Additional documentation
    tools/                   # Utility scripts
        summarize_layers.py  # Layer data analysis
        find_images           # GitLab registry image finder
        gerrit-pre-push-lint  # Gerrit pre-push linter
    kolla/                   # Kolla master patches
    kolla-2025.1/            # Kolla epoxy patches
    kolla-2025.2/            # Kolla flamingo patches
    kolla-ansible/           # Kolla-Ansible master patches
    kolla-ansible-2025.1/    # Kolla-Ansible epoxy patches
    kolla-ansible-2025.2/    # Kolla-Ansible flamingo patches
    nova-2025.1/             # Nova epoxy patches
    globals.yml              # Kolla-Ansible deployment config
    README.md.tmpl           # README template (edit this)
    README.md                # Generated README (do not edit)
```

## Patch System

### Project Directories

Each `<project>[-<version>]/` directory contains:

- `config.yaml` -- repo URL, branch, source SHA, release
  name, dependencies
- `ORDER` -- ordered list of patches to apply from
  `_patches/`
- `FORCE` (optional) -- forces inclusion even with no
  patches

### Patch Application Flow

```
config.yaml (repo + SHA)
    |
    v
git clone + checkout SHA
    |
    v
ORDER file (list of patches)
    |
    v
git apply each patch in order
    |
    v
optionally run test suites
```

### Shared Patches

Patches in `_patches/` can be referenced by multiple ORDER
files across releases. When a patch needs release-specific
changes, a copy is created with a release codename in the
filename.

## Container Build Pipeline

### Build Flow

```
assemble-source.sh (clone + patch)
    |
    v
imagebuild.sh (kolla-build)
    |
    v
build-containers.sh (push via occystrap)
    |
    v
occystrap pipeline:
    inspect (as-built)
        -> normalize-timestamps
    inspect (post-normalize)
        -> exclude .git
    inspect (post-exclude)
        -> registry push
```

### Layer Data Collection

The occystrap inspect filter records layer metadata (digest,
size, history) at each pipeline stage. This produces per-image
JSONL files that are packaged into a tarball.

Data flow:
1. occystrap writes per-image JSONL files during push
2. Files are tarred into `layers.tar.gz`
3. CI uploads as build artifact
4. `collect_layer_data` job aggregates across matrix builds
5. Tarballs are committed to `data/` via automated PR

### Analysis

`tools/summarize_layers.py` processes the tarballs:

- `--data-dir` -- chronological build progression, tracking
  layer reuse across builds
- `--compare-stages` -- per-stage comparison showing how
  each filter affects layer count and size
- `--stage` -- select a specific pipeline stage for analysis

## CI/CD

### Functional Tests Workflow

The main CI workflow (`functional-tests.yml`) runs on PRs:

1. **build** (matrix) -- builds container images for each
   target release and distro combination
2. **deploy** -- deploys OpenStack via kolla-ansible and
   runs functional tests (VM creation, console access)
3. **collect_layer_data** -- aggregates layer tarballs and
   proposes a PR to store them in `data/`

### Daily Rebase

`daily-rebase-checks.yml` runs daily to keep patches current
with upstream. It updates source SHAs, tests patches, and
uses Claude Code to auto-fix failures when possible. The
resulting PR includes a summary of upstream commits pulled
in for each project (short hashes and oneline messages).
