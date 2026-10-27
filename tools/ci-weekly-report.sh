#!/bin/bash

# Report a failed weekly run of "Build test clouds".
#
# The weekly tier runs every lane on develop, including the Ubuntu and
# Rocky lanes that no pull request or queue entry runs. A failure there has
# no pull request to comment on and no merge to block, so without this it
# would only be an email to whoever last edited the schedule. This script
# keeps one open issue, labelled weekly-ci-failure, for the weekly tier:
# the first failure opens it, and each later failure while it is open adds
# a comment rather than a second issue. Closing the issue is left to
# whoever fixes the lanes.
#
# Inputs come from the environment: GH_TOKEN, GH_REPO, RUN_ID and RUN_URL.

set -euo pipefail

for NAME in GH_TOKEN GH_REPO RUN_ID RUN_URL; do
  if [ -z "${!NAME:-}" ]; then
    echo "$NAME must be set" >&2
    exit 2
  fi
done

LABEL=weekly-ci-failure

# --force makes this idempotent: it updates the label if it exists.
gh label create "$LABEL" --force --color B60205 \
  --description 'The weekly run of Build test clouds failed'

# The gate jobs fail when anything else does, so they say nothing a reader
# needs; list only the lanes.
FAILED=$(gh api --paginate "repos/$GH_REPO/actions/runs/$RUN_ID/jobs?per_page=100" \
  --jq '.jobs[] | select(.conclusion == "failure" or .conclusion == "cancelled")
        | select(.name != "Can enqueue" and .name != "Can merge")
        | "- [\(.name)](\(.html_url)) (\(.conclusion))"')
if [ -z "$FAILED" ]; then
  FAILED="- No lane failed; see the run for the gate's own failure."
fi

BODY="The weekly run of \`Build test clouds\` failed: $RUN_URL

$FAILED

The weekly tier carries lanes that the merge queue does not run (see
docs/ci.md), so develop may have broken one of them at any point since the
last passing weekly run. A \`tier=weekly\` dispatch re-runs the whole
matrix on develop."

ISSUE=$(gh issue list --label "$LABEL" --state open --limit 1 --json number --jq '.[0].number // empty')
if [ -n "$ISSUE" ]; then
  echo "Adding to open issue #$ISSUE"
  gh issue comment "$ISSUE" --body "$BODY"
else
  echo "Opening a new issue"
  gh issue create --title 'Weekly CI: the full lane matrix failed on develop' \
    --label "$LABEL" --body "$BODY"
fi
