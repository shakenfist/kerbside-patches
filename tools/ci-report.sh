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
    echo "Reports: libvirt-limit mariadb-ist wsrep-sync-fatal" >&2
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
    for name in libvirt-limit mariadb-ist wsrep-sync-fatal; do
        run_report "${name}"
    done
else
    run_report "${report}"
fi
