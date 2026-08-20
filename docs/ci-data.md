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
- `scheduler-unhealthy` -- how often kolla-ansible's post-deploy and
  post-reconfigure `check-failure.sh` finds `nova_scheduler`
  unhealthy. The container healthcheck is `healthcheck_port
  nova-scheduler 5672`, which asserts an established AMQP connection,
  at a 30 second interval with 3 retries -- 90 seconds of tolerance.
  Every kolla-ansible deploy and reconfigure SIGTERMs the scheduler's
  cotyledon workers and takes a very consistent ~163 seconds to
  respawn them, during which no process holds a connection and the
  probe correctly reports unhealthy. Whether a job fails is therefore
  down to where its sanity check lands relative to that window.
  Measured over 30 days this hits 328 builds and is fatal to 118 of
  them, spread evenly across rocky-10, ubuntu-noble and debian-trixie
  on OpenDev's own nodes, so it is an upstream defaults mismatch
  rather than anything about our hardware. Raising
  `nova_scheduler_healthcheck_retries` past the reload window is the
  workaround this report justifies.
- `ovs-create-tap` -- os-vif refusing to pre-create a TAP device for a
  hybrid-plugged OVS port, logged by nova-compute as `create_tap is
  only supported for VIFOpenVSwitch`. Neutron's ML2/OVS mechanism
  driver began advertising `ovs_create_tap` on 2026-07-31 without
  consulting `OVS_HYBRID_PLUG`; nova copies the flag onto a shared
  port profile before it chooses `VIFBridge`, and os-vif then rejects
  the plug, so no instance boots at all. Kolla-Ansible hardcodes the
  hybrid firewall driver, so Kolla CI is exposed as soon as it picks
  up a new enough neutron. We tripped it first because we rebuild
  from master daily -- our patch178 opts out locally, and this report
  watches for the same failure reaching upstream.
- `fluentd-missing-logs` -- how often `check-logs.sh` aborts a job
  because fluentd never started tailing a log file under
  `/var/log/kolla`, logged as `no match for <file>`. Its
  `check_fluentd_missing_logs()` requires every non-exempt log file to
  have a matching `following tail of <file>` line in `fluentd.log` and
  treats a gap as critical, but it never waits for one to appear.
  fluentd's `in_tail` discovers files created after it started only on
  its `refresh_interval`, which Kolla leaves at the 60 second default
  (every `following tail of` line in a build lands on the same
  once-a-minute tick), so any service whose first log line arrives
  within a minute of the check is reported missing even though nothing
  is wrong. That makes the exposure a function of deploy order rather
  than of the service: `ansible/site.yml` applies masakari second to
  last and skyline last, and heat and horizon are also in the tail, so
  those are the names that show up. A second, rarer path reaches the
  same signature -- fluentd only refreshes while its output plugin is
  healthy, so an OpenSearch stall (`Could not communicate to
  OpenSearch`) stretches the window, and the script's guard loop for
  that greps only the last five lines of `fluentd.log`, so it stops
  waiting when the warning scrolls out of view rather than when fluentd
  has caught up. A genuine gap -- a service fluentd is not configured
  to collect at all -- looks identical in the chart but reproduces on
  every run of that scenario. The `log_url` column in the CSV points at
  the `fluentd-error.txt` naming the files, which is what tells the
  cases apart.

Each report is a target string plus the log file(s) to scan for it; the
shared scan/aggregate/chart engine is `tools/count_ci_log_errors.py`
(generalized from the earlier `count_libvirt_errors.py`, which was
prototyped in a separate repository). The chart marks the fix-merge
boundary, once a fix has merged, so the before/after effect stays
visible over time.

A report's denominator is implicit: it is the set of builds that
published any of its log files, because a build with no matching file is
not recorded at all. That is usually what you want, but it breaks for a
signature whose log file only exists when the check has already failed.
`fluentd-missing-logs` is such a case -- `fluentd-error.txt` is written
only when `check-logs.sh` finds something critical, and the job then
fails, so on its own the report would chart a constant 100% hit rate
over a handful of builds. It therefore names a second log suffix,
`kolla/fluentd/fluentd.txt`, purely as a denominator: fluentd's own log
is published by exactly the builds where the check runs, and can never
contain the target string, so those builds are recorded as misses and
the hit rate becomes a real per-build failure rate. Use the same trick
for any future signature that is only published on failure.

State lives in `data/ci-reporting/` (per-report CSVs, their
checkpoints, and the charts) and is committed to the repository. Runs
are incremental: the committed checkpoint means only builds newer than
the previous run are fetched, and the Zuul API is listed exactly once
per run, keeping load on OpenDev's infrastructure to a minimum. Each
run uploads the refreshed data as a workflow artifact and proposes a PR
updating the committed data.
