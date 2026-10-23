#!/bin/bash
#
# Collects the layer data from a functional test run's build artifacts,
# commits it to a new branch based on develop, pushes that and opens a pull
# request for it.
#
# Called by the "Collect layer data and prepare PR" step of the
# collect_layer_data job in functional-tests.yml, from the repository root.
#
# This script switches the working tree to develop part-way through, which
# replaces tools/ with develop's copy. So it must not be run from tools/: the
# workflow step copies this script, collect-layer-data.py and create-data-pr.sh
# to $RUNNER_TEMP and runs the copy, and the siblings are found next to
# whichever copy is running. That way a fix to any of them is exercised by the
# pull request that makes it. create-data-pr.sh in turn calls
# data-pr-auto-merge.sh beside itself, so that is copied too.
#
# It runs on merge groups. The pull request under test is taken from the
# queue branch's name (gh-readonly-queue/<base>/pr-<N>-<base sha>, with or
# without refs/heads/ in front), and its title is looked up, because the
# merge group payload carries none. A ref which does not parse, or a title
# which cannot be looked up, costs only the description: the data is still
# collected, and the run is named where the pull request would be.
#
# Inputs come from the environment:
#   GITHUB_TOKEN     required, used by create-data-pr.sh and the push
#   GITHUB_RUN_ID    required, the run the artifacts came from
#   QUEUE_REF        required, the merge group's head ref; it may be empty,
#                    which is treated like a ref that does not parse
#   CURRENT_ATTEMPT  required, the run attempt; artifacts from any other
#                    attempt are excluded
#   DATA_PREFIX      subdirectory of data/ and branch name prefix, default
#                    layers
#   ARTIFACTS_DIR    where the build-* artifacts were downloaded, default
#                    /tmp/build-artifacts
#   RUNNER_TEMP      scratch space, a new temporary directory if unset
#   GITHUB_REPOSITORY  where to look the title up, default
#                    shakenfist/kerbside-patches

set -euo pipefail

for NAME in GITHUB_TOKEN GITHUB_RUN_ID CURRENT_ATTEMPT; do
    if [ -z "${!NAME:-}" ]; then
        echo "${NAME} must be set" >&2
        exit 1
    fi
done

# Set, but possibly empty: an empty ref loses only the description.
if [ -z "${QUEUE_REF+set}" ]; then
    echo "QUEUE_REF must be set" >&2
    exit 1
fi

DATA_PREFIX="${DATA_PREFIX:-layers}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-/tmp/build-artifacts}"
RUN_ID="${GITHUB_RUN_ID}"
DATESTAMP=$(date '+%Y%m%d-%H%M')
SCRIPT_DIR=$(dirname "$0")
REPO="${GITHUB_REPOSITORY:-shakenfist/kerbside-patches}"

if [ -z "${RUNNER_TEMP:-}" ]; then
    RUNNER_TEMP=$(mktemp -d)
fi

# Name the source of the data: the pull request the queue entry is for if
# the ref says, the run otherwise.
queue_ref_pattern='^(refs/heads/)?gh-readonly-queue/.+/pr-([0-9]+)-[0-9a-f]+$'
if [[ "${QUEUE_REF}" =~ ${queue_ref_pattern} ]]; then
    pr_number="${BASH_REMATCH[2]}"
    commit_source="Source PR: #${pr_number}"
    if pr_title=$(gh pr view "${pr_number}" --repo "${REPO}" --json title --jq .title) \
        && [ -n "${pr_title}" ]; then
        body_source="PR #${pr_number} - ${pr_title}"
    else
        echo "Warning: could not look up the title of PR #${pr_number}; leaving it out" >&2
        body_source="PR #${pr_number}"
    fi
else
    echo "Warning: queue ref '${QUEUE_REF}' names no pull request; naming run ${RUN_ID} instead" >&2
    commit_source="Source: run ${RUN_ID}"
    body_source="run ${RUN_ID}"
fi
echo "Source: ${body_source}"
echo

echo "Available artifacts:"
ls -la "${ARTIFACTS_DIR}/" || echo "No artifacts directory"
echo
echo "Current run attempt: ${CURRENT_ATTEMPT}"

# Filter artifacts to only include those from the current run attempt.
# This prevents including stale layer data from previous attempts when
# a single matrix job is retried.
listing="${RUNNER_TEMP}/layers-listing.txt"
included_builds=""
excluded_builds=""
NL=$'\n'

for dir in "${ARTIFACTS_DIR}"/build-*/; do
    build_name=$(basename "${dir}")

    # Check if this artifact was generated in the current attempt
    if [ -f "${dir}run_attempt" ]; then
        artifact_attempt=$(cat "${dir}run_attempt")
        if [ "${artifact_attempt}" != "${CURRENT_ATTEMPT}" ]; then
            echo "Skipping ${build_name}: from attempt ${artifact_attempt}, current is ${CURRENT_ATTEMPT}"
            excluded_builds="${excluded_builds}- ${build_name} (attempt ${artifact_attempt})${NL}"
            continue
        fi
    else
        echo "Skipping ${build_name}: no run_attempt file (legacy artifact)"
        excluded_builds="${excluded_builds}- ${build_name} (no attempt marker)${NL}"
        continue
    fi

    # Only include builds whose tarball actually contains layer
    # data, not just an empty layers/ directory
    if [ -f "${dir}layers.tar.gz" ]; then
        # List to a file rather than piping into grep -q: grep exits at
        # the first match, and under pipefail tar then dies of SIGPIPE
        # on a long listing and the match reads as a miss. A tarball
        # which cannot be listed is one with no layer data.
        if tar tzf "${dir}layers.tar.gz" > "${listing}" 2> /dev/null \
            && grep -q '\.jsonl$' "${listing}"; then
            included_builds="${included_builds}- ${build_name}${NL}"
        else
            echo "Skipping ${build_name}: layers.tar.gz contains no layer data"
            excluded_builds="${excluded_builds}- ${build_name} (no layer data)${NL}"
        fi
    fi
done

# Check if we have any layers to include
if [ -z "${included_builds}" ]; then
    echo "No layers.tar.gz files from current attempt found, skipping PR creation"
    exit 0
fi

echo
echo "Builds to include:"
echo "${included_builds}"
if [ -n "${excluded_builds}" ]; then
    echo "Builds excluded (from previous attempts):"
    echo "${excluded_builds}"
fi

# Create a branch for the layer data (include run_id for uniqueness)
branch_name="${DATA_PREFIX}-data-${DATESTAMP}-run${RUN_ID}"
git checkout develop
git checkout -b "${branch_name}"

# Append layer records to the per-image time series files in
# data/ (only from the current attempt). Each image gets one
# JSONL file per build variant, with one line appended per run.
for dir in "${ARTIFACTS_DIR}"/build-*/; do
    # Re-check run_attempt filter
    if [ -f "${dir}run_attempt" ]; then
        artifact_attempt=$(cat "${dir}run_attempt")
        if [ "${artifact_attempt}" != "${CURRENT_ATTEMPT}" ]; then
            continue
        fi
    else
        continue
    fi

    if [ -f "${dir}layers.tar.gz" ]; then
        build_name=$(basename "${dir}")

        echo "Collecting layer data from ${build_name}"
        "${SCRIPT_DIR}/collect-layer-data.py" \
            --tarball "${dir}layers.tar.gz" \
            --build-name "${build_name}" \
            --data-dir "data/${DATA_PREFIX}" \
            --run-id "${RUN_ID}" \
            --run-attempt "${CURRENT_ATTEMPT}" \
            --datestamp "${DATESTAMP}"
    fi
done

# Check if there are changes to commit
if [ -z "$(git status --porcelain data/)" ]; then
    echo "No changes to commit"
    exit 0
fi

# Configure git and commit
git config --global user.name "shakenfist-bot"
git config --global user.email "bot@shakenfist.com"

git add data/
git commit -m "Add layer data from build run ${RUN_ID}.

This commit adds layer data from a successful functional
test build. This data is collected for analysis purposes.

${commit_source}
Build date: ${DATESTAMP}
Run ID: ${RUN_ID}
Run attempt: ${CURRENT_ATTEMPT}

Assisted-By: CI Automation
Co-Authored-By: shakenfist-bot <bot@shakenfist.com>"

# Push the branch
git push -f origin "${branch_name}"
sleep 5

# Build PR body with explanation of included/excluded builds
pr_body="This PR adds layer data collected from build run ${RUN_ID}.

**Source:** ${body_source}
**Build date:** ${DATESTAMP}
**Run ID:** ${RUN_ID}
**Run attempt:** ${CURRENT_ATTEMPT}

### Builds included

${included_builds}

These builds completed in the current workflow run attempt."

if [ -n "${excluded_builds}" ]; then
    pr_body="${pr_body}

### Builds excluded

${excluded_builds}

These builds were from previous run attempts (e.g., cached artifacts from
before a job retry). Their layer data was not included to avoid duplicating
data that was already collected in earlier PRs."
fi

printf '%s\n' "${pr_body}" > "${RUNNER_TEMP}/pr-body.md"

"${SCRIPT_DIR}/create-data-pr.sh" \
    --assignee mikalstill \
    --title "${DATA_PREFIX} data from ${DATESTAMP} (run ${RUN_ID}, attempt ${CURRENT_ATTEMPT})" \
    --body-file "${RUNNER_TEMP}/pr-body.md" \
    --head "${branch_name}"
