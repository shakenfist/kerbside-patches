#!/bin/bash

# Test patch application for all projects and output JSON results.
# This script is designed for CI use - it captures failures in a structured
# format that can be passed to Claude Code for auto-fixing.
#
# Usage: _build/test-patches-for-ci.sh [project1 project2 ...]
#
# If no projects specified, tests all projects with config.yaml files.
# Outputs JSON to stdout with structure:
# {
#   "success": true/false,
#   "projects_tested": [...],
#   "failures": [
#     {
#       "project": "kolla",
#       "patch": "_patches/patch112-kolla-layer-data.patch",
#       "error": "error: patch failed: kolla/common/config.py:271..."
#     }
#   ]
# }

set -o pipefail

topdir=$(pwd)
topsrcdir="${topdir}/src"

# Activate venv if available
if [ -e /srv/shakenfist/kerbside-patches-tools/bin/activate ]; then
    . /srv/shakenfist/kerbside-patches-tools/bin/activate
fi

# Determine which projects to test
if [ $# -gt 0 ]; then
    projects="$@"
else
    projects=$(find . -maxdepth 2 -name "config.yaml" -type f | \
               cut -f 2 -d "/" | sort)
fi

# Initialize result tracking
declare -a projects_tested=()
declare -a failures=()
overall_success=true

# Helper to escape JSON strings
json_escape() {
    python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))"
}

for project in ${projects}; do
    # Skip if no config.yaml
    if [ ! -e "${project}/config.yaml" ]; then
        continue
    fi

    skip_rebase=$(yq -r .skip_rebase ${project}/config.yaml)
    if [ "${skip_rebase}" == "true" ]; then
        continue
    fi

    projects_tested+=("\"${project}\"")

    # Clean up any previous source directory
    rm -rf "${topsrcdir}" 2>/dev/null || true

    # Capture output from test-apply.sh
    error_output=""
    failed_patch=""

    # Run test-apply.sh and capture output
    output=$(./_build/apply-patches-and-test.sh --skip-tests "${project}" 2>&1)
    exit_code=$?

    if [ ${exit_code} -ne 0 ]; then
        overall_success=false

        # Extract the failing patch from output
        # Look for lines like "Applying master ../_patches/patch112-kolla-layer-data.patch"
        # followed by failure
        failed_patch=$(echo "${output}" | \
            grep -E "Applying.*_patches/.*\.patch" | \
            tail -1 | \
            sed -E 's/.*(_patches\/[^ ]+\.patch).*/\1/' | \
            sed 's/\x1b\[[0-9;]*m//g')

        # Extract error message (git apply output)
        error_output=$(echo "${output}" | \
            grep -A 20 "error: patch failed\|error: .*: patch does not apply" | \
            head -25 | \
            sed 's/\x1b\[[0-9;]*m//g')

        # If no specific error found, get the last 30 lines
        if [ -z "${error_output}" ]; then
            error_output=$(echo "${output}" | tail -30 | sed 's/\x1b\[[0-9;]*m//g')
        fi

        # Escape for JSON
        escaped_error=$(echo "${error_output}" | json_escape)
        escaped_patch=$(echo "${failed_patch}" | tr -d '\n')

        failures+=("{\"project\": \"${project}\", \"patch\": \"${escaped_patch}\", \"error\": ${escaped_error}}")
    fi

    # Clean up source directory to save space
    rm -rf "${topsrcdir}" 2>/dev/null || true
done

# Build JSON output
projects_json=$(IFS=,; echo "${projects_tested[*]}")
failures_json=$(IFS=,; echo "${failures[*]}")

cat <<EOF
{
  "success": ${overall_success},
  "projects_tested": [${projects_json}],
  "failures": [${failures_json}]
}
EOF

# Exit with appropriate code
if [ "${overall_success}" = true ]; then
    exit 0
else
    exit 1
fi
