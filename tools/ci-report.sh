#!/bin/bash

# Refresh a CI reliability dataset and chart. This is the per-report
# configuration wrapper around tools/count_ci_log_errors.py: each report names
# a target string, the log file(s) to scan for it, and where the committed
# state lives.
#
# Usage: tools/ci-report.sh <report>|all
#
# Reports:
#   libvirt-limit    - libvirtd "Client hit max requests limit" occurrences
#                      (the kolla-ansible 995171 fix; should stay at zero)
#   mariadb-ist      - Galera "IST didn't contain all write sets" failures
#                      during mariadb scenario jobs (MDEV-36621 / MDEV-33089;
#                      terminal for the joining node under the current
#                      mariadb_recovery flow)
#   wsrep-sync-fatal - hard failures of the "Wait for first MariaDB service
#                      to sync WSREP" bootstrap handler (the kolla-ansible
#                      989612 fix); the target string is the handler's until
#                      expression, which only appears in logs when the
#                      conditional errors out
#   scheduler-unhealthy
#                    - nova_scheduler flagged unhealthy by kolla-ansible's
#                      post-deploy/post-reconfigure check-failure.sh. Its
#                      healthcheck (healthcheck_port nova-scheduler 5672,
#                      30s interval x 3 retries = 90s) cannot survive the
#                      ~163s window in which cotyledon has SIGTERMed the
#                      scheduler workers and not yet respawned them, so any
#                      sanity check landing in that window fails
#   ovs-create-tap   - os-vif refusing to pre-create a TAP device for a
#                      hybrid-plugged OVS port. Neutron's ML2/OVS driver
#                      started advertising ovs_create_tap on 2026-07-31
#                      without checking OVS_HYBRID_PLUG, nova copies it onto
#                      the shared port profile before choosing VIFBridge, and
#                      os-vif then rejects the plug so no instance boots. We
#                      hit this before upstream did because we rebuild from
#                      master daily; this report watches for Kolla CI picking
#                      up a new enough neutron to start failing too
#   fluentd-missing-logs
#                    - check-logs.sh's check_fluentd_missing_logs() finding a
#                      log file under /var/log/kolla that fluentd never
#                      started tailing ("no match for <file>"). Treated as
#                      critical, so it fails the job outright, and the check
#                      never waits: fluentd's in_tail only discovers new files
#                      on its refresh_interval (Kolla leaves it at the 60s
#                      default), so a service that starts logging within a
#                      minute of the check is reported missing. Exposure
#                      therefore tracks deploy order -- site.yml applies
#                      masakari second to last -- rather than the service
#                      itself. A genuine gap (logs fluentd is not configured
#                      to collect) produces the same string but repeats every
#                      run
#
# Note on the fluentd-missing-logs denominator: fluentd-error.txt is only
# published by builds that already failed this check, so on its own it would
# chart a constant 100% hit rate. The second log suffix, fluentd/fluentd.txt,
# is the denominator -- it is published by exactly the builds where
# check-logs.sh's fluentd section runs, and can never contain the target
# string, so those builds are recorded as misses.
#
# State (the CSV, its checkpoint, and the chart) lives in data/ci-reporting/
# and is committed to this repository, so each run only fetches logs for
# builds newer than the checkpoint. This deliberately minimises load on the
# OpenDev Zuul API and log servers: Zuul itself is listed once per run (a
# serial, rate-limited sweep), and previously examined builds are never
# refetched. The Zuul build listing cache is transient (committing it would
# freeze the scan window) and is kept outside the data directory.

set -e -o pipefail

DATA_DIR="data/ci-reporting"

report="$1"
if [ -z "${report}" ]; then
    echo "Usage: $0 <report>|all" >&2
    echo "Reports: libvirt-limit mariadb-ist wsrep-sync-fatal ovs-create-tap scheduler-unhealthy" >&2
    echo "         fluentd-missing-logs" >&2
    exit 1
fi

venv=$(mktemp -d)
trap 'rm -rf "${venv}"' EXIT

python3 -m venv "${venv}/venv"
# shellcheck source=/dev/null
source "${venv}/venv/bin/activate"
pip install --quiet requests matplotlib

run_report() {
    local name="$1"

    # Per-report configuration. --fix-merged is the merge date of the fix the
    # report tracks; leave it empty until the fix actually merges.
    local target projects job_filter suffixes basename chart chart_title fix_merged
    case "${name}" in
        libvirt-limit)
            target='Client hit max requests limit'
            projects='openstack/kolla openstack/kolla-ansible'
            job_filter=''
            suffixes='kolla/libvirt/libvirtd.txt'
            basename='kolla_libvirt_errors'
            chart='kolla_libvirt_chart.png'
            chart_title='libvirtd connection limit failures on master CI'
            fix_merged='2026-06-28'
            ;;
        mariadb-ist)
            target="IST didn't contain all write sets"
            projects='openstack/kolla-ansible'
            job_filter='-mariadb'
            suffixes='kolla/mariadb/mariadb.txt'
            basename='kolla_mariadb_ist_errors'
            chart='kolla_mariadb_ist_chart.png'
            chart_title='MariaDB Galera IST failures (node restart required) on master CI'
            fix_merged=''
            ;;
        wsrep-sync-fatal)
            target="result.query_result[0][0]"
            projects='openstack/kolla-ansible'
            job_filter=''
            suffixes='logs/ansible/deploy logs/ansible/upgrade'
            basename='kolla_wsrep_sync_fatal_errors'
            chart='kolla_wsrep_sync_fatal_chart.png'
            chart_title='MariaDB WSREP sync wait hard failures on master CI'
            fix_merged=''
            ;;
        scheduler-unhealthy)
            target='Discovered unhealthy container: nova_scheduler'
            projects='openstack/kolla openstack/kolla-ansible'
            job_filter='^(kolla-ansible|kayobe)-'
            suffixes='job-output.txt'
            basename='kolla_scheduler_unhealthy_errors'
            chart='kolla_scheduler_unhealthy_chart.png'
            chart_title='nova_scheduler flagged unhealthy by post-deploy sanity checks on master CI'
            # Change 999789 raised nova_scheduler_healthcheck_retries to 8.
            fix_merged='2026-08-19'
            ;;
        ovs-create-tap)
            target='create_tap is only supported for VIFOpenVSwitch'
            projects='openstack/kolla openstack/kolla-ansible'
            job_filter=''
            suffixes='kolla/nova/nova-compute.txt'
            basename='kolla_ovs_create_tap_errors'
            chart='kolla_ovs_create_tap_chart.png'
            chart_title='os-vif TAP pre-creation refused on hybrid-plug OVS ports on master CI'
            fix_merged=''
            ;;
        fluentd-missing-logs)
            target='no match for /var/log/kolla/'
            projects='openstack/kolla openstack/kolla-ansible'
            job_filter=''
            # fluentd-error.txt carries the signature; fluentd/fluentd.txt is
            # the denominator (see the note at the top of this file).
            suffixes='kolla/fluentd-error.txt kolla/fluentd/fluentd.txt'
            basename='kolla_fluentd_missing_logs_errors'
            chart='kolla_fluentd_missing_logs_chart.png'
            chart_title='fluentd log files never tailed, failing check-logs.sh on master CI'
            fix_merged=''
            ;;
        *)
            echo "Unknown report: ${name}" >&2
            exit 1
            ;;
    esac

    local args=(
        --days 30
        --target-string "${target}"
        --output "${DATA_DIR}/${basename}.csv"
        --checkpoint "${DATA_DIR}/${basename}.csv.checkpoint"
        --builds-cache "${venv}/${name}-builds.json"
        --chart "${DATA_DIR}/${chart}"
        --chart-title "${chart_title}"
    )
    # shellcheck disable=SC2086
    for suffix in ${suffixes}; do
        args+=(--log-suffix "${suffix}")
    done
    # shellcheck disable=SC2086
    args+=(--projects ${projects})
    if [ -n "${job_filter}" ]; then
        # The = form so a leading dash in the pattern is not mistaken for an
        # option by argparse.
        args+=("--job-filter=${job_filter}")
    fi
    if [ -n "${fix_merged}" ]; then
        args+=(--fix-merged "${fix_merged}")
    fi

    echo "=== refreshing report: ${name} ===" >&2
    python3 tools/count_ci_log_errors.py "${args[@]}"
}

if [ "${report}" = "all" ]; then
    for name in libvirt-limit mariadb-ist wsrep-sync-fatal ovs-create-tap scheduler-unhealthy \
                fluentd-missing-logs; do
        run_report "${name}"
    done
else
    run_report "${report}"
fi
