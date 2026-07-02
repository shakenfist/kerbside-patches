#!/bin/bash

# Refresh the Kolla libvirt "max requests limit" dataset and chart.
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
FIX_MERGED="2026-06-28"

venv=$(mktemp -d)
trap 'rm -rf "${venv}"' EXIT

python3 -m venv "${venv}/venv"
# shellcheck source=/dev/null
source "${venv}/venv/bin/activate"
pip install --quiet requests matplotlib

python3 tools/count_libvirt_errors.py \
    --days 30 \
    --output "${DATA_DIR}/kolla_libvirt_errors.csv" \
    --checkpoint "${DATA_DIR}/kolla_libvirt_errors.csv.checkpoint" \
    --builds-cache "${venv}/builds.json" \
    --chart "${DATA_DIR}/kolla_libvirt_chart.png" \
    --fix-merged "${FIX_MERGED}"
