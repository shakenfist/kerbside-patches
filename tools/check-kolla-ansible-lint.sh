#!/bin/bash

# Check kolla-ansible patches for ansible-lint errors.
#
# Applies patches (without running tests) and runs tox -elinters
# for each kolla-ansible project. This is a lightweight check
# intended to run in parallel with the full CI test matrix.
#
# Usage:
#   tools/check-kolla-ansible-lint.sh [project1 project2 ...]
#
# If no projects specified, finds all kolla-ansible-* projects
# that are not skipped for rebase.
#
# Exit codes:
#   0 - All linters passed
#   1 - Lint errors found

set -e

topdir=$(cd "$(dirname "$0")/.." && pwd)
cd "${topdir}"

# Activate venv if available (CI runner may pre-install one)
if [ -e /srv/shakenfist/kerbside-patches-tools/bin/activate ]; then
    . /srv/shakenfist/kerbside-patches-tools/bin/activate
fi

# Find kolla-ansible projects to check
if [ $# -gt 0 ]; then
    projects="$@"
else
    projects=""
    for dir in kolla-ansible*/; do
        dir="${dir%/}"
        if [ ! -e "${dir}/config.yaml" ]; then
            continue
        fi

        skip_rebase=$(yq -r '.skip_rebase // "false"' \
            "${dir}/config.yaml")
        if [ "${skip_rebase}" == "true" ]; then
            continue
        fi

        projects="${projects} ${dir}"
    done
fi

if [ -z "${projects}" ]; then
    echo "No kolla-ansible projects found to lint."
    exit 0
fi

echo "Projects to lint:${projects}"
echo

overall_exit=0
failed_projects=""

for project in ${projects}; do
    echo "============================================"
    echo "Linting ${project}"
    echo "============================================"

    directory=$(yq -r .directory "${project}/config.yaml")

    # Clean previous source tree
    rm -rf src/

    # Apply patches without running tests
    set +e
    ./_build/apply-patches-and-test.sh --skip-tests "${project}" \
        > /dev/null 2>&1
    apply_exit=$?
    set -e

    if [ ${apply_exit} -ne 0 ]; then
        echo "WARNING: Patches failed to apply for ${project}, skipping lint"
        echo
        continue
    fi

    # Run linters in the patched source tree
    pushd "src/${directory}" > /dev/null

    if ! tox -a 2>/dev/null | grep -q linters; then
        echo "No linters environment available, skipping"
        popd > /dev/null
        continue
    fi

    echo "Running tox -elinters..."
    # ansible-lint clones ansible-collection-kolla from opendev.org during
    # the run. opendev.org is intermittently unreachable, so retry on
    # transient network errors before declaring a real lint failure.
    set +e
    attempts=3
    delay=30
    for (( try=1; try<=attempts; try++ )); do
        tox -elinters 2>&1 | tee "${topdir}/lint-output-${project}.txt"
        lint_exit=${PIPESTATUS[0]}
        if [ "${lint_exit}" -eq 0 ]; then
            break
        fi
        if ! grep -qE \
                "Failed to connect to opendev\.org|unable to access 'https?://opendev\.org|Could not resolve host: opendev\.org" \
                "${topdir}/lint-output-${project}.txt"; then
            break
        fi
        if [ "${try}" -lt "${attempts}" ]; then
            echo
            echo "WARNING: transient opendev.org network error during tox -elinters; retrying in ${delay}s (attempt $((try + 1))/${attempts})"
            sleep "${delay}"
            delay=$((delay * 2))
        fi
    done
    set -e

    popd > /dev/null

    if [ ${lint_exit} -ne 0 ]; then
        echo
        echo "FAILED: Lint errors in ${project}"
        overall_exit=1
        failed_projects="${failed_projects} ${project}"
    else
        echo
        echo "PASSED: ${project}"
    fi

    # Clean up source tree to save space
    rm -rf src/
    echo
done

echo "============================================"
if [ ${overall_exit} -eq 0 ]; then
    echo "All kolla-ansible linters passed."
else
    echo "Lint failures in:${failed_projects}"
fi
echo "============================================"

exit ${overall_exit}
