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
            trigger-downstream.yml  # Trigger kerbside CI on develop push
            local-container-builds.yml  # Test local builds work
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

The build pipeline supports two modes for pushing images to
the CI registry:

**Proxy mode** (`--use-proxy`, default in CI):

```
assemble-source.sh (clone + patch)
    |
    v
build-containers.sh starts occystrap proxy (background)
    |
    v
imagebuild.sh (kolla-build --push --registry 127.0.0.1:5050)
    images pushed to proxy as they finish building
    |
    v
occystrap proxy filters + forwards to CI registry:
    normalize-timestamps -> exclude .git -> registry push
    |
    v
build-containers.sh stops proxy (SIGTERM, graceful drain)
```

Build and push overlap: kolla-build pushes each image to the
local proxy as it finishes, and the proxy filters and forwards
to the CI registry concurrently. The proxy's layer cache
provides cross-image deduplication.

When using the proxy, kolla-build's `--namespace` is set to
the CI registry project path (`openstack/kolla-images`) so
that images land at the correct path in the downstream
registry.

**Sequential mode** (fallback):

```
assemble-source.sh (clone + patch)
    |
    v
imagebuild.sh (kolla-build, push=false)
    |
    v
build-containers.sh pushes each image via occystrap process:
    inspect (as-built)
        -> normalize-timestamps
    inspect (post-normalize)
        -> exclude .git
    inspect (post-exclude)
        -> registry push
```

### Layer Data Collection

Layer metadata is collected during image pushes. In proxy
mode, a single JSONL file captures all images. In sequential
mode, per-image per-stage JSONL files are produced via
occystrap's inspect filter.

Data flow:
1. JSONL files written during push
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
   target release and distro combination. After the build,
   clingwrap collects Docker daemon diagnostics (using the
   `openstack-kolla-ansible` target) to help debug build
   failures such as Docker daemon hangs. The diagnostics
   bundle is included in the build artifact.
2. **deploy** -- deploys OpenStack via kolla-ansible and
   runs functional tests (VM creation, console access)
3. **collect_layer_data** -- aggregates layer tarballs and
   proposes a PR to store them in `data/`. Runs even when
   some builds fail (uses `!cancelled()`) so that layer
   data from successful builds is still collected.

### Daily Rebase

`daily-rebase-checks.yml` runs daily to keep patches current
with upstream. It updates source SHAs, tests patches, and
uses Claude Code to auto-fix failures when possible. The
resulting PR includes a summary of upstream commits pulled
in for each project (short hashes and oneline messages).
