#!/bin/bash

# Bump source SHAs for all projects.
#
# Usage:
#   bump-source-shas.sh [N]           # Set to HEAD~(N-1), default N=1 (HEAD)
#   bump-source-shas.sh --forward N   # Step forward N commits from current SHA
#   bump-source-shas.sh --changelog F # Write per-project commit summary to F
#
# Examples:
#   bump-source-shas.sh               # Update to latest HEAD
#   bump-source-shas.sh 1             # Same as above (HEAD)
#   bump-source-shas.sh 5             # Update to HEAD~4 (5 back from tip)
#   bump-source-shas.sh --forward 1   # Step forward 1 commit from current
#   bump-source-shas.sh --forward 10  # Step forward 10 commits from current
#   bump-source-shas.sh --changelog /tmp/changes.md

mode="rewind"
count=1
changelog_path=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --forward|-f)
            mode="forward"
            count="$2"
            shift 2
            ;;
        --changelog|-c)
            changelog_path="$2"
            shift 2
            ;;
        --help|-h)
            head -17 "$0" | tail -15
            exit 0
            ;;
        *)
            # Legacy positional argument for rewind count
            count="$1"
            shift
            ;;
    esac
done

# Initialize changelog file if requested
if [ -n "${changelog_path}" ]; then
    true > "${changelog_path}"
fi

if [ "${mode}" = "rewind" ]; then
    echo "Mode: rewind - will set to HEAD~$((count - 1)) (${count} back from tip)"
else
    echo "Mode: forward - will step forward ${count} commits from current SHA"
fi

# Lock in new daily SHAs for each project we're patching
cwd=$(pwd)
# -maxdepth 2 limits us to top-level ./<project>/config.yaml and avoids the
# assembled src/ tree, where upstream repos ship their own config.yaml files.
projects=$(find . -maxdepth 2 -type f -name "config.yaml" | cut -f 2 -d "/")
for project in ${projects}; do
    mkdir -p src
    repo=$(yq -r .repo ${project}/config.yaml)
    source_branch=$(yq -r .source_branch ${project}/config.yaml)
    current_source_sha=$(yq -r .source_sha ${project}/config.yaml)
    directory=$(yq -r .directory ${project}/config.yaml)
    skip_rebase=$(yq -r '.skip_rebase // false' ${project}/config.yaml)

    echo
    echo "Processing ${project}..."
    echo "  Current SHA: ${current_source_sha}"

    git clone --quiet --branch ${source_branch} ${repo} src/${directory}
    cd src/${directory}

    if [ "${mode}" = "rewind" ]; then
        # Original behavior: count back from HEAD
        source_sha=$(git log --pretty=format:%H | head -${count} | tail -1)
    else
        # Forward mode: step forward N commits from current SHA
        # Get commits from current SHA to HEAD, reverse to chronological order,
        # then pick the Nth one
        commits_ahead=$(git log --pretty=format:%H ${current_source_sha}..HEAD | tac)

        # Count commits (handle empty case separately to avoid grep exit code issue)
        if [ -z "${commits_ahead}" ]; then
            commit_count=0
        else
            commit_count=$(echo "${commits_ahead}" | grep -c .)
        fi

        if [ "${commit_count}" -eq 0 ]; then
            echo "  Already at HEAD, no commits to step forward"
            source_sha="${current_source_sha}"
        elif [ "${count}" -ge "${commit_count}" ]; then
            echo "  Requested ${count} commits but only ${commit_count} ahead"
            echo "  Moving to HEAD instead"
            source_sha=$(git log --pretty=format:%H -1)
        else
            source_sha=$(echo "${commits_ahead}" | head -${count} | tail -1)
        fi
    fi

    # Generate changelog before deleting the clone (skip for directories
    # with skip_rebase=true to avoid duplicate entries from wave dirs)
    if [ -n "${changelog_path}" ] \
            && [ "${source_sha}" != "${current_source_sha}" ] \
            && [ "${skip_rebase}" != "true" ]; then
        short_old=$(echo "${current_source_sha}" | cut -c1-9)
        short_new=$(echo "${source_sha}" | cut -c1-9)
        {
            echo "${project} updated from ${short_old} to ${short_new}"
            git log --no-merges --oneline "${current_source_sha}..${source_sha}" \
                | sed 's/^/    /'
            echo
        } >> "${changelog_path}"
    fi

    cd ${cwd}
    rm -rf src

    echo "  New SHA: ${source_sha}"

    if [ "${source_sha}" != "${current_source_sha}" ]; then
        echo "  Updating config.yaml"
        yq -i -y ".source_sha = \"${source_sha}\"" ${project}/config.yaml
        yq -i -y ".previous_source_sha = \"${current_source_sha}\"" ${project}/config.yaml
    else
        echo "  SHA unchanged."
    fi

    echo
done
