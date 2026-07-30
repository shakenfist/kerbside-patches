# CI data collection and reporting

The CI pipelines collect two kinds of data over time: container layer
metadata from the image builds, and reliability statistics for the
upstream OpenDev CI jobs this repository depends on.

## Layer data collection

The CI workflow collects layer metadata during the occystrap
image push pipeline and proposes a PR to add this data to the
`data/layers/` directory. This allows tracking of container
layer optimization over time.

In CI, the build uses an occystrap filtering proxy
(`--use-proxy`). kolla-build pushes images directly to the
local proxy as they finish building, and the proxy applies
filters (normalize-timestamps, exclude `.git`) before
forwarding to the CI registry. This overlaps build and push
for faster CI runs. Inspect filters in the proxy capture layer
metadata at each stage of that pipeline (as-built,
post-normalize and post-exclude).

The `collect_layer_data` CI job merges the per-stage records
into one record per image and appends it to a per-image time
series file, `data/layers/<build-name>/<image>.jsonl`. One line
is appended per build run, so the files are structured to
answer two questions: are images getting bigger over time (and
if so, which layer grew), and is the occystrap pipeline
increasing layer reuse between builds?

Use `tools/summarize_layers.py` to analyze the collected data.
It produces `growth`, `reuse` and `stages` reports; see
`--report`.

The workflow is configured to skip functional tests when only files in
`data/` are changed, preventing infinite loops when layer data PRs are
merged.

Because every run appends a line to the same per-image files, data PRs
that are open at the same time conflict as soon as one of them merges.
The `heal-data-prs.yml` workflow runs on each push to develop and
re-resolves those conflicts automatically: it union-merges develop into
the conflicted PR branch (git's `union` merge driver, configured in
`.gitattributes`, keeps both sides' appended lines), verifies the
result is append-only and well formed with
`tools/verify-data-merge.py`, and pushes the merge back to the PR
branch. Record order within a file does not matter because
`tools/summarize_layers.py` sorts records by their embedded datestamp.

## CI reliability reporting

The `CI reliability reporting` workflow (`ci-reporting.yml`, run on
demand via workflow_dispatch) tracks the health of the upstream OpenDev
CI jobs that this repository depends on. The workflow_dispatch form has
a dropdown selecting which report to refresh; the report catalogue
lives in `tools/ci-report.sh` and currently covers:

- `mariadb-ist` -- how often mariadb scenario jobs log the Galera error
  `IST didn't contain all write sets`, which is terminal for the
  joining node under the current `mariadb_recovery` flow (upstream
  MariaDB MDEV-36621 / MDEV-33089).
- `wsrep-sync-fatal` -- hard failures of the `Wait for first MariaDB
  service to sync WSREP` bootstrap handler, the failure mode fixed by
  kolla-ansible change 989612.
- `libvirt-limit` -- how often Kolla CI builds log the libvirt message
  `Client hit max requests limit`, the failure mode fixed by
  kolla-ansible change 995171. Kept to confirm the fix holds; it
  should stay at zero.

Each report is a target string plus the log file(s) to scan for it; the
shared scan/aggregate/chart engine is `tools/count_ci_log_errors.py`
(generalized from the earlier `count_libvirt_errors.py`, which was
prototyped in a separate repository). The chart marks the fix-merge
boundary, once a fix has merged, so the before/after effect stays
visible over time.

State lives in `data/ci-reporting/` (per-report CSVs, their
checkpoints, and the charts) and is committed to the repository. Runs
are incremental: the committed checkpoint means only builds newer than
the previous run are fetched, and the Zuul API is listed exactly once
per run, keeping load on OpenDev's infrastructure to a minimum. Each
run uploads the refreshed data as a workflow artifact and proposes a PR
updating the committed data.
