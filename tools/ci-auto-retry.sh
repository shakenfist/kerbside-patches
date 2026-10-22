#!/bin/bash
#
# Looks at a completed CI run and acts on how it failed.
#
# For a pull request run that failed for infrastructure reasons rather than
# because of the change under test, it comments on the pull request and
# re-runs the failed jobs.
#
# For a merge queue run, re-running cannot land anything, so it works on the
# queue entry instead. An entry ejected because every failed job matched
# INFRA_PATTERNS is put back in the queue, once per tested head, with
# REQUEUE_TOKEN. Any other ejection gets a comment naming the failed jobs. A
# merge queue run which passed after its entry had already been taken out of
# the queue (what the check timeout looks like) gets a comment too.
#
# Called by the "Auto-retry infrastructure failures" workflow, which never
# retries a failure that does not match INFRA_PATTERNS.
#
# Inputs come from the environment: GH_TOKEN, REPO, RUN_ID, RUN_URL and
# WORKFLOW_NAME always. EVENT is the run's triggering event; when it is
# merge_group, CONCLUSION, HEAD_BRANCH and HEAD_SHA must be set too, and
# REQUEUE_TOKEN is needed to re-queue. With --dry-run every read happens as
# normal, but each write (a pull request comment, a re-run, a re-queue) is
# printed prefixed "DRY RUN:" instead of being made.

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

# Any event other than merge_group takes the pull request path, which is
# also what a run with no EVENT at all gets.
MERGE_GROUP=0
if [ "${EVENT:-}" = merge_group ]; then
  MERGE_GROUP=1
  for NAME in CONCLUSION HEAD_BRANCH HEAD_SHA; do
    if [ -z "${!NAME:-}" ]; then
      echo "$NAME must be set for a merge_group run" >&2
      exit 2
    fi
  done
fi

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

# Set MATCHED to the first infrastructure pattern found in a job's
# annotations or, failing that, in the last 2000 lines of its log, or to
# nothing.
match_infra_pattern() {
  local JOB_ID="$1" ANNOTATIONS LOG_TEXT PATTERN
  local LOG_ARGS=()

  # Get annotations for this job
  ANNOTATIONS=$(gh api "repos/$REPO/check-runs/$JOB_ID/annotations" \
    --jq '.[].message' 2>/dev/null || echo "")

  MATCHED=""
  for PATTERN in "${INFRA_PATTERNS[@]}"; do
    if grep -qi -- "$PATTERN" <<< "$ANNOTATIONS"; then
      MATCHED="$PATTERN"
      return
    fi
  done

  # Annotations didn't match — check the job log for in-job
  # transient errors (e.g. opendev.org clone failures).
  # Tail to keep memory bounded. Newer gh refuses to print a
  # response containing terminal escape sequences, even into a
  # pipe, and almost every job log has ANSI colour in it; with
  # stderr discarded that made this fallback silently match
  # nothing (run 38478931745's sfcbr 507 went unretried on
  # 2026-10-14). Pass the flag when this gh knows it, and say
  # when the log could not be read rather than hiding it.
  #
  # Does this gh know the flag? Asked once, into a variable: piping
  # the help text into grep -q would let grep exit early and, under
  # pipefail, turn a match into a failed pipeline.
  if [ -z "${GH_HELP+set}" ]; then
    GH_HELP=$(gh api --help 2>/dev/null || true)
  fi
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
      MATCHED="$PATTERN"
      return
    fi
  done
}

# A merge queue run has no pull request of its own: the queue branch names
# the pull request, and the entry was ejected (or not) when the run's
# "Can merge" reported. Every path through here ends the script.
handle_merge_group() {
  local BRANCH_PATTERN='^gh-readonly-queue/.+/pr-([0-9]+)-[0-9a-f]+$'
  if ! [[ "$HEAD_BRANCH" =~ $BRANCH_PATTERN ]]; then
    echo "::warning::Merge queue branch '$HEAD_BRANCH' does not name a pull request, skipping"
    exit 0
  fi
  PR_NUMBER="${BASH_REMATCH[1]}"
  echo "Merge queue run for PR #$PR_NUMBER, conclusion $CONCLUSION"

  if [ "$CONCLUSION" != failure ] && [ "$CONCLUSION" != success ]; then
    echo "Nothing to do for a merge queue run which ended $CONCLUSION"
    exit 0
  fi

  # The SHA in the queue branch's name is the base the entry was built on,
  # not the pull request. The commit the run tested merges the pull
  # request's head into that base, so the head is its second parent.
  TESTED_HEAD=$(gh api "repos/$REPO/commits/$HEAD_SHA" --jq '.parents[1].sha // empty')
  if [ -z "$TESTED_HEAD" ]; then
    echo "::error::Merge queue commit $HEAD_SHA has no second parent, so the pull request head it tested is unknown" >&2
    exit 1
  fi
  echo "Tested head: $TESTED_HEAD"

  # Everything about the pull request, in one read: the node id the
  # re-queue needs, whether it is open, queued and still at the tested head,
  # and the last time it left the merge queue.
  local PR_FIELDS
  # shellcheck disable=SC2016 # GraphQL variables, not shell ones.
  PR_FIELDS=$(gh api graphql \
    -f query='query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          id state headRefOid isInMergeQueue
          timelineItems(itemTypes: [REMOVED_FROM_MERGE_QUEUE_EVENT], last: 1) {
            nodes { ... on RemovedFromMergeQueueEvent { createdAt reason } }
          }
        }
      }
    }' \
    -f owner="${REPO%%/*}" -f name="${REPO#*/}" -F number="$PR_NUMBER" \
    --jq '.data.repository.pullRequest | [.id, .state, .headRefOid, (.isInMergeQueue | tostring),
          (.timelineItems.nodes[0].createdAt // ""), (.timelineItems.nodes[0].reason // "")] | join("|")')
  local PR_ID PR_STATE PR_HEAD PR_QUEUED REMOVED_AT REMOVED_REASON
  IFS='|' read -r PR_ID PR_STATE PR_HEAD PR_QUEUED REMOVED_AT REMOVED_REASON <<< "$PR_FIELDS"
  echo "PR #$PR_NUMBER is $PR_STATE at $PR_HEAD, in the merge queue: $PR_QUEUED"
  if [ -n "$REMOVED_AT" ]; then
    echo "Last removed from the merge queue at $REMOVED_AT, reason: $REMOVED_REASON"
  fi

  # Classify before looking at the pull request's state, so that a dry run
  # against an entry which has since merged still shows the verdicts.
  # "Can merge" is left out: it fails whenever any other job does.
  local INFRA_JOBS="" GENUINE_JOBS="" FAILED_JOBS JOB_ID JOB_NAME JOB_STEP LINE
  if [ "$CONCLUSION" = failure ]; then
    FAILED_JOBS=$(gh api --paginate "repos/$REPO/actions/runs/$RUN_ID/jobs" \
      --jq '.jobs[] | select(.conclusion == "failure")
            | "\(.id)|\(.name)|\(first(.steps[]? | select(.conclusion == "failure") | .name) // "")"')
    while IFS='|' read -r JOB_ID JOB_NAME JOB_STEP; do
      [ -z "$JOB_ID" ] && continue
      if [ "$JOB_NAME" = "Can merge" ]; then
        echo "Not classifying job 'Can merge', which fails whenever another job does"
        continue
      fi
      echo "Checking job '$JOB_NAME' (id: $JOB_ID)..."
      LINE="- ${JOB_NAME}: failed in step \"${JOB_STEP:-(none recorded)}\""
      match_infra_pattern "$JOB_ID"
      if [ -n "$MATCHED" ]; then
        echo "  -> infrastructure, matching: $MATCHED"
        INFRA_JOBS="$INFRA_JOBS$LINE (infrastructure: $MATCHED)"$'\n'
      else
        echo "  -> not infrastructure"
        GENUINE_JOBS="$GENUINE_JOBS$LINE"$'\n'
      fi
    done <<< "$FAILED_JOBS"
  fi

  if [ "$PR_STATE" != OPEN ]; then
    echo "PR #$PR_NUMBER is $PR_STATE, nothing to do"
    exit 0
  fi
  if [ "$PR_QUEUED" = true ]; then
    echo "PR #$PR_NUMBER is already in the merge queue, nothing to do"
    exit 0
  fi
  if [ "$PR_HEAD" != "$TESTED_HEAD" ]; then
    echo "PR #$PR_NUMBER has moved on from the tested head to $PR_HEAD, nothing to do"
    exit 0
  fi

  local RUN_MARKER="<!-- auto-retry-merge-group: $RUN_ID -->"
  local REQUEUE_MARKER="<!-- auto-requeue-infra: $TESTED_HEAD -->"
  local MARKERS
  MARKERS=$(gh api --paginate "repos/$REPO/issues/$PR_NUMBER/comments" \
    --jq '.[].body | scan("<!-- auto-(?:requeue-infra|retry-merge-group): [0-9a-f]+ -->")')
  if grep -qxF -- "$RUN_MARKER" <<< "$MARKERS"; then
    echo "::warning::Already handled run $RUN_ID, not commenting again"
    exit 0
  fi

  local COMMENT_BODY
  if [ "$CONCLUSION" = success ]; then
    if [ -z "$REMOVED_AT" ]; then
      echo "PR #$PR_NUMBER has never left the merge queue, nothing to do"
      exit 0
    fi
    local RUN_TIMES STARTED_AT FINISHED_AT STARTED FINISHED REMOVED DURATION
    RUN_TIMES=$(gh api "repos/$REPO/actions/runs/$RUN_ID" --jq '"\(.run_started_at)|\(.updated_at)"')
    IFS='|' read -r STARTED_AT FINISHED_AT <<< "$RUN_TIMES"
    STARTED=$(date -u -d "$STARTED_AT" +%s)
    FINISHED=$(date -u -d "$FINISHED_AT" +%s)
    REMOVED=$(date -u -d "$REMOVED_AT" +%s)
    if [ "$REMOVED" -le "$STARTED" ]; then
      echo "PR #$PR_NUMBER last left the merge queue before run $RUN_ID started, nothing to do"
      exit 0
    fi
    DURATION="$(( (FINISHED - STARTED) / 3600 ))h $(( (FINISHED - STARTED) % 3600 / 60 ))m"

    COMMENT_BODY="${RUN_MARKER}
**Ejected from the merge queue while its run was still going**

The merge queue run of workflow **${WORKFLOW_NAME}** for \`${TESTED_HEAD}\` passed, but this pull request had already been removed from the queue at ${REMOVED_AT}, so it did not merge. GitHub gave the reason as \"${REMOVED_REASON}\". The run took ${DURATION} from ${STARTED_AT}, so this is most likely the 360 minute timeout on the required check, which ejects an entry whose check has not reported in time however healthy its jobs are. [View run](${RUN_URL})

A re-queue would very likely time out again, so it has not been re-queued automatically.

_This is an automated action._"

    echo "Commenting on PR #$PR_NUMBER..."
    write gh pr comment "$PR_NUMBER" --repo "$REPO" --body "$COMMENT_BODY"
    echo "Done! Reported run $RUN_ID"
    exit 0
  fi

  local SECOND_INFRA=0
  if [ -n "$INFRA_JOBS" ] && [ -z "$GENUINE_JOBS" ]; then
    if ! grep -qxF -- "$REQUEUE_MARKER" <<< "$MARKERS"; then
      # The re-queue comes before its comment, so that a re-queue which
      # fails leaves no marker behind and the next ejection tries again.
      if [ -z "${REQUEUE_TOKEN:-}" ]; then
        echo "::error::REQUEUE_TOKEN is not set, so PR #$PR_NUMBER cannot be re-queued. Nothing was posted." >&2
        exit 1
      fi
      # expectedHeadOid makes GitHub refuse the re-queue, rather than queue
      # untested code, if the pull request moved after the read above.
      # shellcheck disable=SC2016 # GraphQL variables, not shell ones.
      local ENQUEUE=(gh api graphql
        -f query='mutation($id: ID!, $head: GitObjectID!) {
          enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head}) {
            mergeQueueEntry { position }
          }
        }'
        -f id="$PR_ID" -f head="$TESTED_HEAD")
      echo "Re-queueing PR #$PR_NUMBER at $TESTED_HEAD..."
      if [ "$DRY_RUN" = 1 ]; then
        echo "DRY RUN: GH_TOKEN=\$REQUEUE_TOKEN ${ENQUEUE[*]}"
      elif ! GH_TOKEN="$REQUEUE_TOKEN" "${ENQUEUE[@]}"; then
        echo "::error::Re-queueing PR #$PR_NUMBER failed. Nothing was posted." >&2
        exit 1
      fi

      COMMENT_BODY="${REQUEUE_MARKER}
${RUN_MARKER}
**Re-queued after an infrastructure failure**

This pull request was ejected from the merge queue because the following job(s) in workflow **${WORKFLOW_NAME}** failed due to infrastructure issues (not test failures):

${INFRA_JOBS}
It has been put back in the merge queue at \`${TESTED_HEAD}\`, the head that was tested. [View run](${RUN_URL})

_This is an automated action, and happens once per head. If this head is ejected for infrastructure reasons again, it will be left out of the queue for a human._"

      echo "Commenting on PR #$PR_NUMBER..."
      write gh pr comment "$PR_NUMBER" --repo "$REPO" --body "$COMMENT_BODY"
      echo "Done! Re-queued PR #$PR_NUMBER"
      exit 0
    fi
    SECOND_INFRA=1
  fi

  local WHY
  if [ "$SECOND_INFRA" = 1 ]; then
    WHY="Every failed job matched an infrastructure pattern, but this is the second infrastructure failure of this head: it was already re-queued once, so it has not been re-queued again."
  elif [ -n "$INFRA_JOBS" ]; then
    WHY="Some failed jobs matched an infrastructure pattern and some did not, so it has not been re-queued automatically."
  elif [ -n "$GENUINE_JOBS" ]; then
    WHY="No failed job matched an infrastructure pattern, so it has not been re-queued automatically."
  else
    WHY="No job other than \"Can merge\" failed, so a lane may have been cancelled or skipped. It has not been re-queued automatically."
  fi

  COMMENT_BODY="${RUN_MARKER}
**Ejected from the merge queue**

The merge queue run of workflow **${WORKFLOW_NAME}** for \`${TESTED_HEAD}\` failed, so this pull request was removed from the queue. [View run](${RUN_URL})

${GENUINE_JOBS}${INFRA_JOBS}
${WHY}

Re-running the merge queue run cannot land this pull request. If the change is at fault, fix it on the pull request (which re-runs the smoke tier); either way, add it to the queue again when it is ready. The \`merge-ci-triage\` skill diagnoses a failed merge queue run. To reproduce a merge tier lane first, dispatch **Build test clouds** on this pull request's branch with \`tier=full\`.

_This is an automated action._"

  echo "Commenting on PR #$PR_NUMBER..."
  write gh pr comment "$PR_NUMBER" --repo "$REPO" --body "$COMMENT_BODY"
  echo "Done! Reported run $RUN_ID"
  exit 0
}

if [ "$MERGE_GROUP" = 1 ]; then
  handle_merge_group
fi

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

# Track which jobs have infrastructure failures
INFRA_FAILED_JOBS=""

while IFS='|' read -r JOB_ID JOB_NAME; do
  [ -z "$JOB_ID" ] && continue
  echo "Checking job '$JOB_NAME' (id: $JOB_ID)..."

  match_infra_pattern "$JOB_ID"

  if [ -n "$MATCHED" ]; then
    echo "  -> Found infrastructure failure matching: $MATCHED"
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
