#!/bin/bash
#
# Looks at a failed CI run and, if it failed for infrastructure reasons
# rather than because of the change under test, comments on the pull
# request and re-runs the failed jobs. Called by the "Auto-retry
# infrastructure failures" workflow, which never retries a failure that
# does not match INFRA_PATTERNS.
#
# Inputs come from the environment: GH_TOKEN, REPO, RUN_ID, RUN_URL and
# WORKFLOW_NAME. With --dry-run every read happens as normal, but each
# write (a pull request comment, a re-run) is printed prefixed "DRY RUN:"
# instead of being made.

set -euo pipefail

DRY_RUN=0
for ARG in "$@"; do
  case "$ARG" in
    --dry-run) DRY_RUN=1 ;;
    *)
      echo "Unknown argument: $ARG" >&2
      exit 2
      ;;
  esac
done

for NAME in GH_TOKEN REPO RUN_ID RUN_URL WORKFLOW_NAME; do
  if [ -z "${!NAME:-}" ]; then
    echo "$NAME must be set" >&2
    exit 2
  fi
done

# Run a command which changes something on GitHub, or only say what it
# would have run.
write() {
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY RUN: $*"
  else
    "$@"
  fi
}

# Error output from fetching a job log goes here.
SCRATCH="${RUNNER_TEMP:-$(mktemp -d)}"

echo "Checking run $RUN_ID for infrastructure failures..."

# Infrastructure failure patterns to match in job annotations or
# in job logs. opendev.org git server flakes intermittently and
# the failure surfaces inside a job step (ansible-lint clones
# ansible-collection-kolla during tox -elinters), so we have to
# grep the logs for those — annotations only carry the generic
# "Process completed with exit code 1".
INFRA_PATTERNS=(
  "self-hosted runner lost communication"
  "runner has received a shutdown signal"
  "runner process exited"
  "No space left on device"
  "runner is offline"
  "Job cancelled because the runner went offline"
  "Failed to connect to opendev.org"
  "unable to access 'https://opendev.org"
  "Could not resolve host: opendev.org"
  # A truncated artifact download surfaces as a gzip/tar EOF when
  # the source tarball is extracted (actions/download-artifact can
  # silently deliver a partial file); seen 2026-07-03 where the
  # same artifact extracted fine on sibling runners.
  "gzip: stdin: unexpected end of file"
  "tar: Unexpected EOF in archive"
  # apt mirror/metadata failures at test-VM provision time; seen
  # 2026-07-03 failing identically on all seven multinode VMs at
  # the same instant.
  "No package matching 'git' is available"
  # A runner VM with failing storage; seen 2026-07-18 where even
  # /usr/bin/sleep returned EIO and the build produced garbage
  # (empty container hash, dead occystrap proxy).
  "Input/output error"
  # GitHub's own error when a job never reaches a runner. Seen
  # 2026-09-13 on run 34732771730, where all six "Build test
  # clouds" matrix jobs failed three minutes after starting with
  # runner_name empty, no steps recorded and no log to download.
  # The full sentence is matched rather than "internal error"
  # because the same list is grepped against job logs below, and
  # a bare "internal error" is a plausible thing for a test under
  # test to print. This one only ever appears as an annotation.
  "GitHub Actions has encountered an internal error"
  # The Shaken Fist cluster could not place a test cloud's
  # instance, either refusing the create outright (a 507 which
  # sfcbr itself marks "transient": true) or leaving it queued in
  # preflight until the wait timed out. Seen 2026-10-12 on runs
  # 38177402085 and 38237936810, both failing in "Setup test
  # environment" before any change under test had run.
  "No nodes remaining at scheduling stage"
  "second timeout (last state: preflight)"
)

# Get PR number if this was a PR-triggered run
PR_NUMBER=$(gh api "repos/$REPO/actions/runs/$RUN_ID" \
  --jq '.pull_requests[0].number // empty')

if [ -z "$PR_NUMBER" ]; then
  echo "Not a PR run, skipping (run_id: $RUN_ID)"
  exit 0
fi

echo "PR number: $PR_NUMBER"

# Get all failed jobs
FAILED_JOBS=$(gh api "repos/$REPO/actions/runs/$RUN_ID/jobs" \
  --jq '.jobs[] | select(.conclusion == "failure") | "\(.id)|\(.name)"')

if [ -z "$FAILED_JOBS" ]; then
  echo "No failed jobs found"
  exit 0
fi

# Does the output of this gh know a flag? Asked once, into a variable: piping
# the help text into grep -q would let grep exit early and, under pipefail,
# turn a match into a failed pipeline.
GH_HELP=$(gh api --help 2>/dev/null || true)

# Track which jobs have infrastructure failures
INFRA_FAILED_JOBS=""

while IFS='|' read -r JOB_ID JOB_NAME; do
  [ -z "$JOB_ID" ] && continue
  echo "Checking job '$JOB_NAME' (id: $JOB_ID)..."

  # Get annotations for this job
  ANNOTATIONS=$(gh api "repos/$REPO/check-runs/$JOB_ID/annotations" \
    --jq '.[].message' 2>/dev/null || echo "")

  matched=""
  for PATTERN in "${INFRA_PATTERNS[@]}"; do
    if grep -qi -- "$PATTERN" <<< "$ANNOTATIONS"; then
      matched="$PATTERN"
      break
    fi
  done

  if [ -z "$matched" ]; then
    # Annotations didn't match — check the job log for in-job
    # transient errors (e.g. opendev.org clone failures).
    # Tail to keep memory bounded. Newer gh refuses to print a
    # response containing terminal escape sequences, even into a
    # pipe, and almost every job log has ANSI colour in it; with
    # stderr discarded that made this fallback silently match
    # nothing (run 38478931745's sfcbr 507 went unretried on
    # 2026-10-14). Pass the flag when this gh knows it, and say
    # when the log could not be read rather than hiding it.
    LOG_ARGS=()
    if grep -q -- '--allow-escape-sequences' <<< "$GH_HELP"; then
      LOG_ARGS=(--allow-escape-sequences)
    fi
    LOG_TEXT=$(gh api "${LOG_ARGS[@]}" \
      "repos/$REPO/actions/jobs/$JOB_ID/logs" \
      2>"$SCRATCH/log-fetch.err" | tail -n 2000 || echo "")
    if [ -z "$LOG_TEXT" ]; then
      echo "  (could not read job log: $(head -c 300 "$SCRATCH/log-fetch.err"))"
    fi
    for PATTERN in "${INFRA_PATTERNS[@]}"; do
      if grep -qi -- "$PATTERN" <<< "$LOG_TEXT"; then
        matched="$PATTERN"
        break
      fi
    done
  fi

  if [ -n "$matched" ]; then
    echo "  -> Found infrastructure failure matching: $matched"
    INFRA_FAILED_JOBS="$INFRA_FAILED_JOBS$JOB_NAME\n"
  fi
done <<< "$FAILED_JOBS"

if [ -z "$INFRA_FAILED_JOBS" ]; then
  echo "No infrastructure failures detected, not retrying"
  exit 0
fi

# Check if we've already retried this run by looking for our comment
RETRY_MARKER="<!-- auto-retry-infra: $RUN_ID -->"
EXISTING_COMMENTS=$(gh api "repos/$REPO/issues/$PR_NUMBER/comments" \
  --jq ".[] | select(.body | contains(\"$RETRY_MARKER\")) | .id")
EXISTING_COMMENT=$(head -1 <<< "$EXISTING_COMMENTS")

if [ -n "$EXISTING_COMMENT" ]; then
  echo "::warning::Already retried run $RUN_ID, not retrying again"
  exit 0
fi

# Format the list of failed jobs for the comment
JOBS_LIST=$(echo -e "$INFRA_FAILED_JOBS" | sed '/^$/d' | sed 's/^/- /')

# Build comment body
COMMENT_BODY="${RETRY_MARKER}
**Auto-retry triggered for infrastructure failure**

The following job(s) in workflow **${WORKFLOW_NAME}** failed due to infrastructure issues (not test failures):

${JOBS_LIST}

Automatically retrying the failed jobs. [View run](${RUN_URL})

_This is an automated action. If the retry also fails with infrastructure issues, manual intervention may be needed._"

echo "Commenting on PR #$PR_NUMBER..."
write gh pr comment "$PR_NUMBER" --repo "$REPO" --body "$COMMENT_BODY"

# Trigger rerun of failed jobs only
echo "Triggering rerun of failed jobs..."
write gh run rerun "$RUN_ID" --failed --repo "$REPO"

echo "Done! Retried run $RUN_ID"
