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
| `test-apply.sh [--skip-tests\|--defer-tests] [--test-patch NAME] <project>` | Wrapper for `apply-patches-and-test.sh`. `--skip-tests` only checks that the patches apply (fast); `--defer-tests` runs the test suites once, after every patch is applied; `--test-patch NAME` runs tests only for patches whose name contains `NAME`, which is the quick way to reproduce one patch's CI failure. The suites are `tox -epy3` and `tox -epep8`, plus `tox -efunctional` for Nova and `tox -elinters` for Kolla-Ansible. |
| `test-patches-for-ci.sh [projects...]` | CI-friendly patch testing that outputs JSON results: `success`, `projects_tested`, and a `failures` list of `{project, patch, error}`. Tests all projects if none specified. Used by automated rebase workflows. |
| `extract-patch-failures.py` | Parses JSON output from `test-patches-for-ci.sh` into human-readable failure details. |
| `../tools/recount-patch.py [--in-place\|--check] <patch...>` | Recomputes the `@@` hunk headers in a patch from its body, for when a patch in `_patches/` has been hand edited. See [patch-editing.md](patch-editing.md). |
| `../tools/check-depends-on.py [--offline] [targets...]` | Validates the `Depends-On` footers in `_patches/`: the URL is a review.opendev.org change, the change exists, is in the project the URL names, and has not been abandoned. Targets are patch files or project directories (whose `ORDER` and `depends_on` chain are expanded); with none, every patch in `_patches/`. `--offline` skips the Gerrit lookups. Run by `test-apply.sh` and, in `--offline` form, by pre-commit. See [patch-editing.md](patch-editing.md). |
| `../tools/test-check-depends-on.py` | Test suite for `check-depends-on.py`, run against a stubbed Gerrit so it needs no network. |
| `../tools/patch_message.py` | The commit message and `Author:` header embedded in a patch file. Shared by `extract-commit-message`, `extract-commit-author.py` and `check-depends-on.py` so they cannot disagree about what a patch says. |
| `../tools/test-recount-patch.py` | Test suite for `recount-patch.py`, using git itself as the oracle: it generates canonical patches, damages them, and requires the recounted result to match git's output or apply cleanly. |

### Automated Rebase Scripts

| Script | Description |
|--------|-------------|
| `rebase-with-claude.sh [options] [projects...]` | Unified rebase helper for both CI and CLI use. Tests patches, analyzes failures, and invokes Claude Code to auto-fix. Options: `--bump-shas` (update to HEAD), `--step-forward N` (advance N commits from current), `--no-claude`, `--interactive`, `--ci`, `--max-turns N` (default 50), `--output-dir DIR`. |
| `bump-source-shas.sh [N]` | Updates `source_sha` in all project `config.yaml` files. With a positional arg N, sets to the Nth most recent upstream commit (default: 1 = HEAD). With `--forward N`, steps forward N commits from the current SHA. With `--changelog <path>`, writes a summary of new upstream commits (short hashes + oneline messages) to the specified file, listing each repository and range once even when several series patch that repository. It moves every series' SHA, including those with `skip_rebase: true`. |
| `analyze-shared-patches.py <results.json>` | Analyzes failing patches to determine fix strategy (`modify_in_place` vs `create_copy`) based on whether patches are shared across releases. |
| `find-patch-usage.py <patch-file>` | Finds all ORDER files that reference a given patch. Returns JSON with list of projects using the patch. |
| `get-next-patch-number.py` | Returns the next available patch number by checking both existing files in `_patches/` and open GitHub PRs. |
| `release-names.yaml` | Maps OpenStack numeric release versions to codenames (e.g., `2025.1` → `epoxy`). |

The Claude-assisted scripts run in CI on a runner labelled `claude-code`,
which needs the Claude Code CLI installed and authenticated (`claude
login`) for the user that runs jobs. The daily rebase workflow creates its
PRs and issues with the `DAILY_REBASE_TOKEN` secret.

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
| `bootstrap-kolla-ansible` | Bootstraps Kolla-Ansible into a virtualenv and prepares for deployment. With the `launcher` deployer it builds no Kolla-Ansible virtualenv: it installs [the deployer launcher](deployer-launcher.md) into a venv of its own, installs a container engine if there is none, and pins the deployer image. |
| `postinstall-kolla-ansible` | Post-installation tasks after Kolla-Ansible deployment. |
| `install-openstack-clients` | Installs patched OpenStack clients (openstacksdk, python-openstackclient) from source. |
| `ka` | Shortcut wrapper for running kolla-ansible commands. |
| `check-launcher-inventory` | Fails if `etc/inventory-all-in-one-launcher-master` differs from `etc/inventory-all-in-one-master` by anything but its leading comment block and the `ansible_connection` settings. Run by pre-commit. |
| `check-deployer-launcher` | Asserts that a deploy really went through the launcher: the deployer marker and call log, the pinned image, the nsenter connection, the file capability on the image's `nsenter`, who owns the openrc files, and that bootstrap built no Kolla-Ansible venv. Run on the target by `functional-tests.yml` on the launcher entries. |
| `check-deployer-guards` | Asserts that the [deployer checks](deployer-launcher.md#deployer-checks) work on a deployed cloud: the record exists, a tag mismatch and a newer record are refused, and `--override-deployer-checks` overrides the refusal. It restores the record on exit. Run on the target by `functional-tests.yml` on the launcher entries. |
| `check-deployer-manifest` | Asserts that the [build manifest](deployer-launcher.md#the-build-manifest) pins every Kolla container to its digest, that `deploy-containers` then recreates nothing, and that a missing manifest warns once. Run on the target by `functional-tests.yml` on the launcher entries. |

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
| `collect-layer-data.py` | Used by the `collect_layer_data` CI job to convert a build's `layers.tar.gz` artifact into appends to the per-image time series files in `data/layers/`. Writes the compact version 2 record format, maintains the per-build `commands.jsonl` dictionary, and caps each series at `--max-records` runs (90 by default). |

### Automated Data PRs

| Script | Description |
|--------|-------------|
| `create-data-pr.sh --head BRANCH --title TITLE --body-file FILE [--assignee USER]` | Creates the pull request for an automated data branch, tolerating the GitHub GraphQL timeout that makes `gh pr create` report a 502 for a pull request it has in fact created. Used by the `collect_layer_data` CI job and by `ci-report-propose-pr.sh`. |

### Automated Lint Fixing

| Script | Description |
|--------|-------------|
| `check-kolla-ansible-lint.sh [projects...]` | Lightweight lint checker that applies kolla-ansible patches (without tests) and runs `tox -elinters`. Used by the `lint_check` CI job. Exits 0 if clean, 1 if errors found. |
| `fix-lint-with-claude.sh [options] [projects...]` | Auto-fixes ansible-lint errors in patch files using Claude Code. Applies patches, runs linters, builds a prompt that explains the patch-file indirection, runs Claude, verifies the fix, and commits/pushes. Options: `--ci`, `--interactive`, `--no-push`, `--no-commit`, `--max-turns N`. |

### Pre-Push Linting for Gerrit

| Script | Description |
|--------|-------------|
| `gerrit-pre-push-lint` | Pre-push linter for OpenStack Gerrit submissions. Checks for common issues that reviewers flag during code review (missing release notes, bug references, YAML line length, Jinja2 issues, etc.). It also learns subject conventions from the last 200 commits of the project: when at least 40% carry an area prefix (`ovn: `, `[OVN] `) it warns about a subject without one, and when at most 10% end with a period it warns about a subject that does. A shallow clone skips this check. Supports checking stacked patches with `--stack N` or `--range origin/master..HEAD`. |
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
| `write-build-manifest` | Reads back the registry digest of every image a build pushed and writes `manifest.json`; also reports whether `kolla-ansible:<tag>` already carries one, and reads the `kolla-build-complete:<tag>` marker that decides whether a build can be skipped. Run by `build-containers.sh`. See [building.md](building.md#the-build-manifest) and [skipping a built tag](building.md#skipping-a-tag-that-is-already-built). |
| `test-write-build-manifest.py` | Test suite for `write-build-manifest`, against a fake registry, so it needs no network or credentials. Run by pre-commit. |
| `ci-select-lanes.py` | Chooses which CI lanes run for a workflow event, from `.github/ci-lanes.json`, where each image build, install test and patch test lists the tiers it runs in. `--event` is `pull_request` (smoke tier), `merge_group` (merge tier) or `workflow_dispatch`, where `--tier full` means the merge tier and anything else the smoke tier. Prints `key=value` lines for `$GITHUB_OUTPUT`: `builds`, `installs` and `patch_tests` as compact `{"test": [...]}` JSON, and `run_builds`, `run_installs`, `run_patch_tests` and `run_local_build` as `true` or `false`. `--code-changed false` selects nothing. Exits 1 on an unknown event or tier, a duplicate lane name, or an install with no matching selected image build. |
| `test-ci-select-lanes.py` | Test suite for `ci-select-lanes.py`, on small fixture files, plus a check that the real `.github/ci-lanes.json` validates for every event and tier. Run by pre-commit. |
| `ci-merge-group-current.sh` | Run as the last step of `check_paths` on a merge group. If GitHub has rebuilt the queue entry onto a newer base, so `BASE_SHA` is no longer the head of `BASE_REF` on origin, it cancels its own run (`RUN_ID`, with `GH_TOKEN` holding `actions: write`) before any job enters a concurrency group, so a superseded run cannot cancel the live one. An unreadable base branch is treated as current; a run that cannot cancel itself, or is still running after `CANCEL_WAIT_SECONDS` (default 120), exits 1. Copied from shakenfist/shakenfist; keep it in step with that copy. |
| `test-ci-merge-group-current.py` | Test suite for `ci-merge-group-current.sh`, run for real with stub `git` and `gh` commands on `PATH`, so it needs no network. Run by pre-commit. |
| `ci-auto-retry.sh` | Run by the "Auto-retry infrastructure failures" workflow when a "Build test clouds" run fails, or when a merge queue run of it passes. For a pull request run, it checks each failed job's annotations and then the last 2000 lines of its log against a list of infrastructure failure patterns (a lost runner, a full disk, opendev.org unreachable, sfcbr unable to place a test cloud, and so on). If any job matches and the run has not already been retried, it comments on the pull request with an `auto-retry-infra` marker and runs `gh run rerun --failed`. A run with no pull request, or no match, writes nothing. For a merge queue run it never re-runs or merges anything. It takes the pull request from the queue branch's name and the tested head from the merge commit's second parent, and acts only while the pull request is open, out of the queue and still at that head. If every failed job except `Can merge` matched a pattern, it puts the pull request back in the queue, requiring the head it tested, then comments with an `auto-requeue-infra` marker, once per head. Any other ejection, including a second infrastructure one of the same head, gets one comment (an `auto-retry-merge-group` marker) listing the failed jobs and their failed steps and naming the `merge-ci-triage` skill and a `tier=full` dispatch. A merge queue run which passed after its entry had left the queue gets one comment giving the run's duration and GitHub's reason, which is what the check timeout looks like. Reads `GH_TOKEN`, `REPO`, `RUN_ID`, `RUN_URL` and `WORKFLOW_NAME` from the environment, plus `EVENT`; when that is `merge_group`, `CONCLUSION`, `HEAD_BRANCH` and `HEAD_SHA` are required, and `REQUEUE_TOKEN` (the workflow passes `DAILY_REBASE_TOKEN`) is used for the re-queue and nothing else. Without it, or if the re-queue fails, it exits non-zero and posts nothing. `--dry-run` does every read but prints each write prefixed `DRY RUN:` instead of making it. |
| `test-ci-auto-retry.py` | Test suite for `ci-auto-retry.sh`, run for real with a stub `gh` on `PATH` that answers from fixture files, covering pull request and merge queue runs, plus checks on how the workflow calls the script. Run by pre-commit. |
| `measure-deployer-bootstrap` | Measures bootstrap from the "Build test clouds" job logs, through the `gh` CLI. `timings --since DATE` pairs each containerised-deployer entry with its venv control in the same run and attempt and prints median and range of the "Deploy Kolla-Ansible" step, its nine composite sub-steps (bootstrap, prechecks, pull, deploy, ...), the sections of `bootstrap-kolla-ansible`, the deployer image pull and unpack, so the time the launcher saves in bootstrap can be followed to where it is spent. `galaxy-failures --since DATE` counts venv jobs whose bootstrap failed fetching Ansible collections, and separately those that recovered by retrying. `record --since D --until D` applies the rules for the launcher entries to become voting (every launcher job in the window, on any branch but `deployer-phase-*`, passes or fails for a reason that is not the launcher's; each entry passed in at least `--min-runs` runs, default 5, and `--min-rebases` of them, default 2, are daily rebases) and prints MET or NOT MET, exiting 1 when not met. `timings --logs-dir DIR` reads already downloaded job logs instead of GitHub; `--cache-dir` keeps downloaded zips. Durations are noisy. See [Bootstrap cost](deployer-launcher.md#bootstrap-cost), and the measurement in [the containerised deployer plan](plans/containerised-deployer.md). |
| `test-measure-deployer-bootstrap.py` | Test suite for `measure-deployer-bootstrap`, on logs built in memory and a fake `gh`, so it needs no network. Run by pre-commit. |

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
| `recount-patch` | Checks that every `@@` hunk header in `_patches/` matches its body. A too-large count makes git reject the patch; a too-small one makes git silently truncate the hunk and apply the wrong content. |
| `check-depends-on` | Runs the offline half of `check-depends-on.py`: every `Depends-On` value is a review.opendev.org change URL, and a committed `<patch>-message` still matches the message in its patch. The Gerrit-backed checks (does the change exist, is it abandoned) need the network, so they run in `test-apply.sh` instead. |
| `flake8` | Lints the Python under `deployer/`, `tools/write-build-manifest`, `tools/measure-deployer-bootstrap`, `tools/ci-select-lanes.py`, `tools/test-ci-merge-group-current.py` and `tools/test-ci-auto-retry.py`. |
| `deployer-launcher-tests`, `write-build-manifest-tests`, `measure-deployer-bootstrap-tests`, `ci-select-lanes-tests`, `ci-merge-group-current-tests`, `ci-auto-retry-tests` | Run the unit tests of the deployer launcher, of `write-build-manifest`, of `measure-deployer-bootstrap`, of `ci-select-lanes.py`, of `ci-merge-group-current.sh` and of `ci-auto-retry.sh`. |
| `skillsaw` | Lints the agent context in this repository -- `AGENTS.md` and the skills under `.claude/` -- for malformed skills, smuggled unicode and pasted credentials. The pin is kept current by renovate's pre-commit manager. |

### Git Hooks and Utilities

| Script | Description |
|--------|-------------|
| `commit-msg.hook` | Git commit-msg hook for validating commit message format. |
| `extract-commit-message` | Extracts commit messages from patch files. |
| `extract-commit-author.py` | Prints a patch file's `Author:` header. `apply-patches-and-test.sh` commits each patch as that author, so a change carried from someone else's review stays attributed to them when `repush-openstack.sh` pushes it back as a new patchset. |
| `find_changes` | Lists changed files for review. |
| `upgrade-python` | Helper for upgrading Python dependencies. |

