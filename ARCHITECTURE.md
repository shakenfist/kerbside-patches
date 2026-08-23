# Architecture

Kerbside-patches is a patch management and container build
system for OpenStack components. It maintains patches that
enable native SPICE console support and builds Kolla
container images with those patches applied.

This document is the map. Building the images is
[docs/building.md](docs/building.md), the helper scripts are
catalogued in [docs/script-reference.md](docs/script-reference.md), the
CI data pipeline is [docs/ci-data.md](docs/ci-data.md), and
[docs/index.md](docs/index.md) indexes the rest.

## Directory Structure

```
kerbside-patches/
    .github/
        workflows/
            functional-tests.yml  # CI: build, deploy, test
            daily-rebase-checks.yml  # Daily upstream rebase
            trigger-downstream.yml  # Trigger kerbside CI on develop push
            local-container-builds.yml  # Test local builds work
            heal-data-prs.yml  # Auto-resolve conflicted data PRs
    _build/                  # Build and CI scripts
        common.sh            # Shared functions and CLI parsing
        assemble-source.sh   # Clone repos, apply patches
        build-containers.sh  # Build + push images via occystrap
        imagebuild.sh        # Run kolla-build
        imagearchive.sh      # Archive images with SBOMs
        debsecan-report.sh   # Vulnerability scan with debsecan
        test-apply.sh        # Test patch application
        test-patches-for-ci.sh  # CI-friendly patch testing
        rebase-with-claude.sh   # Automated rebase with Claude
        bump-source-shas.sh    # Update upstream SHAs
    _patches/                # All patch files (shared pool)
        patchNNN-desc.patch  # Patch files
        patchNNN-desc.patch-message  # Optional commit messages
    data/                    # Per-image layer time series from CI
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
    inspect (as-built)
        -> normalize-timestamps
    inspect (post-normalize)
        -> exclude .git
    inspect (post-exclude)
        -> registry push
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

Layer metadata is collected during image pushes by occystrap
inspect filters at three pipeline stages: as-built,
post-normalize and post-exclude. In proxy mode all images
append to combined per-stage JSONL files (each line records
which image it describes). In sequential mode per-image
per-stage files are produced.

Data flow:
1. JSONL files written during push
2. Files are tarred into `layers.tar.gz`
3. CI uploads as build artifact
4. `collect_layer_data` job runs `tools/collect-layer-data.py`
   for each matrix build, which merges the three stages into
   one record per image
5. Each record is appended to the per-image time series file
   `data/layers/<build-name>/<image>.jsonl` and committed via
   automated PR

The time series structure is deliberate: one file per image
per build variant, one line per build run. That makes the two
questions the data exists to answer cheap to compute -- image
size over time (and which layer grew) is a single-file read,
and layer reuse between builds is a digest comparison between
adjacent lines.

### Security Vulnerability Scanning

After images are built, `debsecan-report.sh` scans each
image for known Debian/Ubuntu CVEs without modifying the
images:

```
docker create image → docker cp dpkg/status → debsecan
    |                                            |
    v                                            v
container removed              archive/debsecan/ reports
(image unchanged)              (detail, simple, fixable, summary)
```

A temporary scanner image is built from the same base distro
(`${distro}:${distro_version}`) so debsecan has the correct
vulnerability data source. Non-Debian-derived images are
detected via `/etc/os-release` and skipped.

The scan produces both human-readable (`summary.txt`) and
machine-parseable (`summary.json`) output. With
`--debsecan-fail-on-fixable`, the build fails if any
packages have available security fixes.

### Analysis

`tools/summarize_layers.py` processes the time series in
`data/layers/`:

- `--report growth` -- image size over time, attributing
  growth to specific layers (matched across runs by their
  CreatedBy command)
- `--report reuse` -- layer reuse between builds, based on
  the post-exclude digests actually pushed to the registry
- `--report stages` -- per-stage comparison showing how each
  filter affects layer count and size
- `--build` / `--image` -- narrow the analysis to one build
  variant or one image

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
3. **collect_layer_data** -- appends each build's layer
   records to the per-image time series in `data/layers/`
   and proposes a PR. Runs even when some builds fail (uses
   `!cancelled()`) so that layer data from successful builds
   is still collected.

Because each data PR appends a line to the same per-image
files, data PRs open concurrently conflict as soon as one of
them merges. The `heal-data-prs.yml` workflow runs on every
push to develop and re-resolves those conflicts with git's
`union` merge driver (configured in `.gitattributes`), which
keeps both sides' appended lines. The result is verified to be
append-only and well formed before it is pushed back to the PR
branch (`tools/heal-data-prs.sh` and
`tools/verify-data-merge.py`). GitHub's own conflict detection
ignores merge drivers, which is why the merge has to be redone
and pushed rather than just configured.

### Rebase Tests Workflow

The `rebase-tests.yml` workflow runs on PRs and includes:

1. **lint_check** -- lightweight job that applies patches
   and runs `tox -elinters` for kolla-ansible projects.
   Runs in parallel with the full test matrix.
2. **functional_matrix** -- deep tests that apply patches
   with full test suites on rocky and debian runners
3. **automated_lint_fixer** -- triggers when `lint_check`
   fails and last commit is not from the bot. Uses Claude
   Code to fix ansible-lint errors in patch files.
4. **check_bot_commit** -- prevents infinite loops by
   skipping automated fixers when the last commit was
   from the bot (same pattern as shakenfist).

### Security and Repository Automation

Four lanes run alongside the test workflows and are independent of
them:

- `secret-scanning.yml` -- scans every commit reachable from `HEAD`
  for leaked credentials via `tools/gitleaks-scan.sh`. Deliberately
  its own workflow with no path filter, because a credential pasted
  into documentation is still a credential.
- `codeql-analysis.yml` -- CodeQL analysis on pushes and pull
  requests against develop, plus weekly.
- `export-repo-config.yml` -- exports the repository's GitHub
  settings daily and opens a PR when they drift, so configuration
  changes are reviewable rather than invisible.
- `pr-re-review.yml` and `pr-retest.yml` -- respond to
  `@shakenfist-bot please re-review` and `@shakenfist-bot please
  retest` from authorised commenters. Useful here because bot commits
  do not trigger CI. Both reach the trigger handling through
  `shakenfist/actions/pr-bot-trigger@main`, which refuses fork pull
  requests.

See `docs/security-scanning.md` for the scanning half.

### Daily Rebase

`daily-rebase-checks.yml` runs daily to keep patches current
with upstream. It updates source SHAs, tests patches, and
uses Claude Code to auto-fix failures when possible. The
resulting PR includes a summary of upstream commits pulled
in for each project (short hashes and oneline messages).

### CI Reliability Reporting

`ci-reporting.yml` (on demand, with a dropdown selecting the
report) refreshes datasets tracking upstream OpenDev CI failure
modes we have fixes in flight for. `tools/count_ci_log_errors.py`
is the shared engine: it walks the Zuul build history for a
project, locates a named log file in each build via
zuul-manifest.json, counts occurrences of a target string, and
renders a per-day chart (hits stacked by distro, plus hit rate)
with an optional fix-merged boundary marker. The per-report
configuration (target string, log paths, job filter, data file
names) lives in `tools/ci-report.sh`. State is committed under
`data/ci-reporting/` with per-report checkpoints so runs are
incremental and OpenDev is never re-scraped.
