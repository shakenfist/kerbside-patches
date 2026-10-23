#!/bin/bash
#
# Put an automated data pull request into the merge queue, by setting
# auto-merge on it, but only where that is safe.
#
# With a merge queue on the base branch, "gh pr merge --auto" means "enqueue
# once the required checks pass". Without one it means "merge as soon as the
# pull request is mergeable", skipping every check the queue would have run.
# So this acts only when all of these hold, and checks them in this order:
#
#   * develop has a merge_queue rule;
#   * the pull request is open and its base is develop;
#   * it changes at least one file, and every file it changes is under
#     data/. The file list is paged: a data pull request touches hundreds of
#     files, and the first page stops at 100.
#
# It runs as shakenfist-bot, which can bypass the queue. So it never passes
# the admin flag, which would merge directly, nor a merge strategy, which a
# queue does not take because the queue's own method applies, and never
# merges by any other route.
#
# Every refusal, and a failed "gh pr merge", is logged and exits 0: the data
# is already safe in the pull request, and a human can still enqueue it.
#
# Usage: tools/data-pr-auto-merge.sh <pull request number>
#
# Requires an authenticated gh (GH_TOKEN or GITHUB_TOKEN). The repository is
# GITHUB_REPOSITORY, default shakenfist/kerbside-patches.

set -euo pipefail

if [ $# -ne 1 ] || ! [[ "$1" =~ ^[0-9]+$ ]]; then
    echo "Usage: $0 <pull request number>" >&2
    exit 1
fi

pr="$1"
repo="${GITHUB_REPOSITORY:-shakenfist/kerbside-patches}"

refuse() {
    echo "Not queueing PR #${pr}: $*"
    exit 0
}

if ! rule_types=$(gh api "repos/${repo}/rules/branches/develop" --jq '.[].type'); then
    refuse "could not read the rules for develop"
fi
if ! grep -qx 'merge_queue' <<< "${rule_types}"; then
    refuse "develop has no merge queue, and without one auto-merge would merge directly"
fi

if ! pr_state=$(gh pr view "${pr}" --repo "${repo}" --json baseRefName,state \
        --jq '"\(.baseRefName) \(.state)"'); then
    refuse "could not read the pull request"
fi
if [ "${pr_state}" != "develop OPEN" ]; then
    refuse "want base develop and state OPEN, found '${pr_state}'"
fi

if ! files=$(gh api --paginate "repos/${repo}/pulls/${pr}/files" --jq '.[].filename'); then
    refuse "could not list its files"
fi
if [ -z "${files}" ]; then
    refuse "it changes no files"
fi
outside=$(grep -v '^data/' <<< "${files}" || true)
if [ -n "${outside}" ]; then
    refuse "it changes files outside data/: $(tr '\n' ' ' <<< "${outside}")"
fi

if gh pr merge "${pr}" --repo "${repo}" --auto; then
    echo "Set auto-merge on PR #${pr}; it joins the merge queue once its checks pass."
else
    echo "Warning: could not set auto-merge on PR #${pr}; it needs enqueueing by hand." >&2
fi
exit 0
