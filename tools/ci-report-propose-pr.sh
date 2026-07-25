#!/bin/bash

# Commit refreshed CI report data and propose a PR for review. Used by the
# ci-reporting workflow; expects RUN_ID and REPORT (which report was
# refreshed, see tools/ci-report.sh) in the environment and a GITHUB_TOKEN
# able to push branches and create PRs.

set -e -o pipefail

DATA_DIR="data/ci-reporting"
REPORT="${REPORT:-unknown}"

if [ -z "$(git status --porcelain "${DATA_DIR}")" ]; then
    echo "No data changes to commit."
    exit 0
fi

DATESTAMP=$(date -u +%Y%m%d-%H%M)
branch_name="ci-reporting-data-${DATESTAMP}-run${RUN_ID}"

git config --global user.name "shakenfist-bot"
git config --global user.email "bot@shakenfist.com"

git checkout -b "${branch_name}"
git add "${DATA_DIR}"
git commit -m "Update CI reliability report data (${REPORT}).

This commit refreshes the '${REPORT}' CI reliability dataset and chart
with builds that completed since the previous run. Only new builds were
fetched; the committed checkpoint prevents re-scraping. See
tools/ci-report.sh for what each report tracks.

Run date: ${DATESTAMP}
Run ID: ${RUN_ID}

Assisted-By: CI Automation
Co-Authored-By: shakenfist-bot <bot@shakenfist.com>"

git push -f origin "${branch_name}"
sleep 5

gh pr create \
    --assignee mikalstill \
    --title "CI reliability report data (${REPORT}) from ${DATESTAMP} (run ${RUN_ID})" \
    --body "This PR refreshes the \`${REPORT}\` CI reliability dataset and
chart in \`data/ci-reporting/\` with builds that completed since the
previous run. See \`tools/ci-report.sh\` for what each report tracks. The
updated chart is also attached to the workflow run as an artifact.

**Report:** ${REPORT}
**Run date:** ${DATESTAMP}
**Run ID:** ${RUN_ID}" \
    --head "${branch_name}"

echo "Pull request created."
