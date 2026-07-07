#!/bin/bash

# Heal automated data PRs that have merge conflicts with develop.
#
# Automated data collection (layers data, CI reporting data) appends
# records to shared time-series files under data/. When more than one
# data PR is open at once, whichever merges first causes GitHub to mark
# the others as conflicted: both sides appended different lines at the
# same end-of-file position. The correct resolution is always the union
# of both sides' appended lines, which is what git's built-in "union"
# merge driver produces (see .gitattributes).
#
# GitHub's PR conflict detection does not honour merge drivers, so this
# script re-does the merge locally for each conflicted data PR, verifies
# with tools/verify-data-merge.py that the result is append-only and
# well formed, and pushes the merge commit back to the PR branch.
#
# Usage: tools/heal-data-prs.sh [--dry-run]
#
# Requires an authenticated gh (GH_TOKEN with push access) and a full
# clone with an "origin" remote. Run from the repository root.

set -e

dry_run=0
if [ "$1" == "--dry-run" ]; then
    dry_run=1
fi

repo="${GITHUB_REPOSITORY:-shakenfist/kerbside-patches}"
bot_identity=(-c user.name=shakenfist-bot -c user.email=bot@shakenfist.com)
failure_marker='<!-- heal-data-prs: could-not-resolve -->'

git fetch origin develop

# Data PR branches look like <prefix>-data-<datestamp>-run<runid>, for
# example layers-data-20260706-1359-run28792754633.
prs=$(gh pr list --repo "$repo" --state open \
    --json number,headRefName,author \
    --jq '.[] | select(.author.login == "shakenfist-bot")
              | select(.headRefName | test("-data-[0-9]{8}-[0-9]{4}-run[0-9]+$"))
              | "\(.number)|\(.headRefName)"')

if [ -z "$prs" ]; then
    echo "No open data PRs found."
    exit 0
fi

# The union merge driver is configured in .gitattributes, but data PR
# branches may have been cut before that file existed and merge reads
# attributes from the branch being merged INTO. Write repo-local
# attributes so the driver applies regardless of branch content.
attributes_file=$(git rev-parse --git-path info/attributes)
mkdir -p "$(dirname "$attributes_file")"
if ! grep -q 'merge=union' "$attributes_file" 2>/dev/null; then
    grep 'merge=union' .gitattributes >> "$attributes_file"
fi

original_ref=$(git rev-parse --abbrev-ref HEAD)
healed=0
skipped=0
failed=0

report_failure() {
    local pr_number="$1"
    local reason="$2"

    failed=$((failed + 1))
    echo "PR #${pr_number}: ${reason}"

    if [ "$dry_run" == "1" ]; then
        return
    fi

    # Only comment once per PR: this runs on every push to develop, and
    # a PR that cannot be healed will fail the same way every time.
    local existing
    existing=$(gh pr view "$pr_number" --repo "$repo" \
        --json comments --jq ".comments[].body" | grep -cF "$failure_marker" || true)
    if [ "$existing" == "0" ]; then
        gh pr comment "$pr_number" --repo "$repo" --body "${failure_marker}
**Could not automatically resolve the merge conflict with develop**

${reason}

The heal-data-prs workflow only pushes union merges it can verify are
append-only and well formed. This PR needs manual conflict resolution."
    fi
}

while IFS='|' read -r pr_number branch; do
    [ -z "$pr_number" ] && continue

    # GitHub computes mergeability lazily, so poll until it settles.
    mergeable="UNKNOWN"
    for _ in $(seq 1 10); do
        mergeable=$(gh pr view "$pr_number" --repo "$repo" \
            --json mergeable --jq .mergeable)
        [ "$mergeable" != "UNKNOWN" ] && break
        sleep 5
    done

    if [ "$mergeable" != "CONFLICTING" ]; then
        echo "PR #${pr_number} (${branch}): mergeable=${mergeable}, nothing to do."
        skipped=$((skipped + 1))
        continue
    fi

    echo "PR #${pr_number} (${branch}): conflicted, attempting union merge."
    run_id=$(echo "$branch" | sed 's/.*-run\([0-9]*\)$/\1/')

    git fetch origin "$branch"
    git checkout --quiet -B "$branch" "origin/$branch"

    if ! git "${bot_identity[@]}" merge --no-edit origin/develop; then
        # The union driver only handles text appends; a file regenerated
        # on both sides (e.g. a chart PNG) still conflicts.
        conflicts=$(git diff --name-only --diff-filter=U | tr '\n' ' ')
        git merge --abort || true
        report_failure "$pr_number" \
            "The union merge driver could not resolve: ${conflicts}"
        continue
    fi

    if ! ./tools/verify-data-merge.py --base origin/develop --run-id "$run_id"; then
        git reset --hard "origin/$branch"
        report_failure "$pr_number" \
            "The union merge succeeded but the result failed verification (see the workflow log)."
        continue
    fi

    if [ "$dry_run" == "1" ]; then
        echo "PR #${pr_number}: verified union merge, would push (dry run)."
        git reset --hard "origin/$branch"
        continue
    fi

    git push origin "$branch"
    gh pr comment "$pr_number" --repo "$repo" --body \
"**Auto-resolved the merge conflict with develop**

Union-merged develop ($(git rev-parse --short origin/develop)) into this
branch and verified the result only appends well-formed records.

_This is an automated action by the heal-data-prs workflow._"
    healed=$((healed + 1))
done <<< "$prs"

git checkout --quiet "$original_ref"
echo "Done: ${healed} healed, ${skipped} already clean, ${failed} need manual attention."
