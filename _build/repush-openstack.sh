#!/bin/bash -e

# Reapply every series whose config.yaml sets repush: true and push it to
# Gerrit with git review. Nothing runs this automatically; it is run by hand
# from a checkout that has Gerrit credentials.
#
# It first lists what will and will not be pushed and asks before starting.
# Then, for each series, it reapplies the patches into src/ (rewriting the
# patch files with --update-patches), checks the branch holds exactly the
# series' patches, shows them, and asks again before that series' git review.
# A series that fails to apply, fails a check or fails to push is reported at
# the end rather than stopping the series after it.

# C collation, so kolla sorts before kolla-ansible and the kolla-* series.
export LC_COLLATE=C

cd "$(dirname "$0")/.."
topdir=$(pwd)

# Patches in a series: the ORDER lines apply-patches-and-test.sh applies, which
# skips comments and blank lines.
count_patches() {
    grep -v -E '^#' "$1/ORDER" | grep -c -v '^[[:space:]]*$' || true
}

push=()
no_push=()
for config in */config.yaml; do
    project=$(dirname "${config}")
    if [ "$(yq -r '.repush // false' "${config}")" == "true" ]; then
        push+=("${project}")
    else
        no_push+=("${project}")
    fi
done

if [ ${#push[@]} -eq 0 ]; then
    echo "No series sets repush: true, nothing to push."
    exit 0
fi

# The patch files a series' ORDER lists, as paths from the top directory.
series_patches() {
    grep -v -E '^#' "$1/ORDER" | grep -v '^[[:space:]]*$' | sed "s|^|$1/|"
}

# Whether any patch in a series carries a Depends-On naming the repository of
# another series being pushed.
depends_on_pushed_series() {
    local project="$1" other repo
    for other in "${push[@]}"; do
        if [ "${other}" == "${project}" ]; then
            continue
        fi
        # https://opendev.org/openstack/kolla -> openstack/kolla, as a review
        # URL names it. The trailing /+/ keeps openstack/kolla from matching
        # openstack/kolla-ansible.
        repo=$(yq -r .repo "${other}/config.yaml")
        repo=${repo#https://opendev.org/}
        # shellcheck disable=SC2046
        if grep -q -E "^ *Depends-On: https://review.opendev.org/c/${repo}/\+/" $(series_patches "${project}"); then
            return 0
        fi
    done
    return 1
}

# A series whose patches depend on another series being pushed goes after it.
# Pushed first, the dependent is in Zuul's check pipeline when its dependency
# gets a new patchset, and Zuul dequeues it and every change behind it with
# "This change depends on a change that failed to merge". Glob order gets this
# wrong for kolla-ansible-deployer, which depends on kolla-deployer.
independent=()
dependent=()
for project in "${push[@]}"; do
    if depends_on_pushed_series "${project}"; then
        dependent+=("${project}")
    else
        independent+=("${project}")
    fi
done
push=("${independent[@]}" "${dependent[@]}")

total_patches=0
echo "Will push ${#push[@]} series to Gerrit:"
echo
printf '    %-40s %7s  %s\n' 'SERIES' 'PATCHES' 'TOPIC'
for project in "${push[@]}"; do
    patches=$(count_patches "${project}")
    total_patches=$(( total_patches + patches ))
    topic=$(yq -r .destination_branch "${project}/config.yaml")
    if [ "$(yq -r '.wip // false' "${project}/config.yaml")" == "true" ]; then
        topic="${topic} (wip)"
    fi
    printf '    %-40s %7d  %s\n' "${project}" "${patches}" "${topic}"
done
echo
echo "That is ${total_patches} patches in total."
echo
echo "Will not push ${#no_push[@]} series, as they do not set repush: true:"
echo
for project in "${no_push[@]}"; do
    printf '    %-40s %7d\n' "${project}" "$(count_patches "${project}")"
done
echo

read -r -p "Continue (y to start, anything else to stop)? " proceed || true
if [ "${proceed}" != "y" ]; then
    echo "Stopping, nothing was pushed."
    exit 0
fi

pushed=()
declined=()
failed=()

# Reapply, check and push one series. Returns 0 when pushed, 2 when the
# operator declined at the prompt, and 1 on any failure. Its caller tests the
# result, which turns -e off inside it, so every step checks its own result.
push_series() {
    local project="$1"
    local config="${project}/config.yaml"
    local source_branch destination_branch source_sha directory wip patches landed proceed
    source_branch=$(yq -r .source_branch "${config}")
    destination_branch=$(yq -r .destination_branch "${config}")
    source_sha=$(yq -r '.source_sha // ""' "${config}")
    directory=$(yq -r .directory "${config}")
    wip=$(yq -r '.wip // false' "${config}")
    patches=$(count_patches "${project}")

    echo
    echo "Processing ${project}..."
    rm -rf src

    echo "Reapply patch series into src/${directory}..."
    _build/test-apply.sh --skip-tests --update-patches "${project}" || return 1

    echo "Validating what landed..."
    echo
    cd "src/${directory}" || return 1

    echo "Project: ${project}"
    echo "Source branch: ${source_branch}"
    echo "Destination branch: ${destination_branch}"
    echo "Work in progress: ${wip}"
    echo "Expected number of patches: ${patches}"
    echo

    git status
    echo

    if [ "$(git branch --show-current)" != "${destination_branch}" ]; then
        echo "Incorrect destination branch!"
        return 1
    fi

    # git review pushes every commit since the upstream base, so anything
    # beyond the series' own patches (a CLAUDE.md commit, a PREPATCH) would
    # land in Gerrit as a review of its own.
    if [ -n "${source_sha}" ]; then
        landed=$(git rev-list --count "${source_sha}..HEAD") || return 1
        if [ "${landed}" -ne "${patches}" ]; then
            echo "Expected ${patches} commits since ${source_sha}, found ${landed}!"
            return 1
        fi
    fi

    git log --oneline | head -$(( patches + 2 ))
    echo

    read -r -p "Proceed (y to go, anything else to skip)? " proceed || true
    if [ "${proceed}" != "y" ]; then
        return 2
    fi

    local review_args=(--yes)
    if [ "${wip}" == "true" ]; then
        review_args+=(--wip)
    fi
    git review "${review_args[@]}" || return 1
}

for project in "${push[@]}"; do
    rc=0
    push_series "${project}" || rc=$?
    cd "${topdir}"
    case ${rc} in
        0) pushed+=("${project}") ;;
        2) declined+=("${project}") ;;
        *) failed+=("${project}") ;;
    esac
done

echo
echo "Pushed ${#pushed[@]}, skipped ${#declined[@]}, failed ${#failed[@]}."
for project in "${declined[@]}"; do
    echo "    skipped: ${project}"
done
for project in "${failed[@]}"; do
    echo "    failed:  ${project}"
done

if [ ${#failed[@]} -gt 0 ]; then
    exit 1
fi
