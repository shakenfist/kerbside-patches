#!/bin/bash

# Create the pull request for an automated data branch, tolerating a
# GitHub GraphQL timeout.
#
# "gh pr create" issues the createPullRequest GraphQL mutation. GitHub
# terminates any GraphQL request that takes longer than ten seconds and
# returns "HTTP 502: 502 Bad Gateway" -- but the mutation has already
# taken effect by then, so the pull request exists and only the response
# was lost. Comparing the PR's createdAt against the 502 in the job log
# shows exactly ten seconds every time.
#
# Data PRs are close to that limit because each one appends a record to
# every per-image time series file at once: a few hundred files whose
# added lines are tens of kilobytes each. As the series grow, the
# mutation crosses ten seconds and the job fails even though the PR is
# fine, which is what happened to every layer data run from
# 2026-08-28 onwards.
#
# So a create that reports failure is not conclusive. This script asks
# GitHub what actually happened: if a pull request now exists for the
# head branch, that is a success no matter what the client was told, and
# only a branch with no pull request is worth retrying.
#
# Once the pull request exists, however it came to, it is handed to
# data-pr-auto-merge.sh, which puts it in the merge queue if that is safe.
# The helper is found beside this script rather than in tools/: the layer
# data job runs a copy of this script from $RUNNER_TEMP, after switching the
# working tree to develop, and copies the helper alongside it.
#
# Usage: tools/create-data-pr.sh --head BRANCH --title TITLE \
#            --body-file FILE [--assignee USER]
#
# Requires an authenticated gh with push access. Run from the repository
# root.

set -e

head_branch=
title=
body_file=
assignee=

while [ $# -gt 0 ]; do
    case "$1" in
        --head) head_branch="$2"; shift 2 ;;
        --title) title="$2"; shift 2 ;;
        --body-file) body_file="$2"; shift 2 ;;
        --assignee) assignee="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$head_branch" ] || [ -z "$title" ] || [ -z "$body_file" ]; then
    echo "Usage: $0 --head BRANCH --title TITLE --body-file FILE [--assignee USER]" >&2
    exit 1
fi

if [ ! -f "$body_file" ]; then
    echo "Body file $body_file does not exist." >&2
    exit 1
fi

repo="${GITHUB_REPOSITORY:-shakenfist/kerbside-patches}"
auto_merge="$(dirname "$0")/data-pr-auto-merge.sh"

create_args=(--repo "$repo" --head "$head_branch" --title "$title" --body-file "$body_file")
if [ -n "$assignee" ]; then
    create_args+=(--assignee "$assignee")
fi

# Report the pull request number for the head branch, if there is one.
# Both a timed-out create and a create this script already made on an
# earlier attempt land here.
existing_pr() {
    gh pr list --repo "$repo" --head "$head_branch" --state all \
        --json number --jq '.[0].number // empty' 2>/dev/null
}

# Queue the pull request if that is safe. The helper treats every refusal
# as a warning, and so does this: the pull request exists, which is what
# this script is for, so nothing here may fail it.
queue() {
    "$auto_merge" "$1" || echo "Warning: ${auto_merge} failed for #$1." >&2
}

# A pull request that already exists is the answer regardless of how it
# got there, so check before creating anything: a re-run of the job for
# a branch that was pushed earlier must not fail on "already exists".
pr=$(existing_pr)
if [ -n "$pr" ]; then
    echo "Pull request #${pr} already exists for ${head_branch}."
    queue "$pr"
    exit 0
fi

attempts=3
for attempt in $(seq 1 "$attempts"); do
    if url=$(gh pr create "${create_args[@]}"); then
        echo "Pull request created: ${url}"
        # gh prints the new pull request's URL. Should that ever not end
        # in its number, ask GitHub instead.
        pr="${url##*/}"
        if ! [[ "$pr" =~ ^[0-9]+$ ]]; then
            pr=$(existing_pr || true)
        fi
        if [ -n "$pr" ]; then
            queue "$pr"
        else
            echo "Warning: could not find the number of the new pull request; not queueing it." >&2
        fi
        exit 0
    fi

    echo "gh pr create failed on attempt ${attempt} of ${attempts}." >&2

    # The mutation may well have succeeded server-side. Give GitHub a
    # moment to make the pull request visible to the list API before
    # concluding that it did not.
    for _ in 1 2 3; do
        sleep 5
        pr=$(existing_pr)
        if [ -n "$pr" ]; then
            echo "Pull request #${pr} exists despite the error; the create did take effect."
            queue "$pr"
            exit 0
        fi
    done

    echo "No pull request exists for ${head_branch} yet." >&2
done

echo "Could not create a pull request for ${head_branch} after ${attempts} attempts." >&2
exit 1
