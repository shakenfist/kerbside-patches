#!/bin/bash -e

# Run from the top directory.
. _build/common.sh

project="${1}"
if [ -z ${project} ]; then
    echo "Please specify a project."
    exit 1
fi

banner ${project}

repo=$(yq -r .repo ${project}/config.yaml)
source_branch=$(yq -r .source_branch ${project}/config.yaml)
source_sha=$(yq -r .source_sha ${project}/config.yaml)
destination_branch=$(yq -r .destination_branch ${project}/config.yaml)
shallow_clone=$(yq -r .shallow_clone ${project}/config.yaml)
directory=$(yq -r .directory ${project}/config.yaml)
depends_on=$(yq -r .depends_on ${project}/config.yaml)

echo -e "${H2}${Arrow}Repository: ${repo}${Color_Off}"
echo -e "${H2}${Arrow}Source branch: ${source_branch}${Color_Off}"
echo -e "${H2}${Arrow}Destination branch: ${destination_branch}${Color_Off}"
echo -e "${H2}${Arrow}Shallow cloning: ${shallow_clone}${Color_Off}"
echo -e "${H2}${Arrow}Dependencies: ${depends_on}${Color_Off}"
echo
echo -e "${H2}${Arrow}Skip tests: ${skip_tests}${Color_Off}"
echo -e "${H2}${Arrow}Test patch: ${test_patch:-all}${Color_Off}"
echo -e "${H2}${Arrow}Use CI registry: ${use_ci_registry}${Color_Off}"
echo

# Do this thing, but for our dependencies...
for dependency in ${depends_on}; do
    banner "Entering ${dependency}"

    extra=""
    if [ "${skip_tests}" == "true" ]; then
        extra="--skip-tests"
    fi
    $0 ${extra} ${dependency}

    banner "Finished with ${dependency}"
done

echo
echo -e "${H2}${Arrow}Dependency processing complete${Color_Off}"
echo

mkdir -p "${topsrcdir}"

if [ ! -e ${topsrcdir}/${directory} ]; then
    echo -e "${H2}${Arrow}Cloning repo${Color_Off}"
    if [[ ${source_sha} != "null" ]]; then
        # Using source SHAs requires special casing because git lacks a fast path.
        # Shallow cloning is also not possible, so that flag is ignored in this case.
        echo -e "${H2}Full depth cloning ${repo}...${Color_Off}"
        git clone ${repo} ${topsrcdir}/${directory}

        echo -e "${H2}Checking out ${source_sha}...${Color_Off}"
        cd ${topsrcdir}/${directory}
        git checkout ${source_sha}
    elif [ "${shallow_clone}" == "true" ]; then
        echo -e "${H2}Shallow cloning ${repo} branch ${source_branch}${Color_Off}"
        git clone --depth 1 --branch ${source_branch} ${repo} ${topsrcdir}/${directory}
    else
        echo -e "${H2}Full depth cloning ${repo} branch ${source_branch}${Color_Off}"
        git clone --branch ${source_branch} ${repo} ${topsrcdir}/${directory}
    fi

    # Sprinkle in the git review changeid hook
    echo
    echo -e "${H2}${Arrow}Install commit hook to ${topsrcdir}/${directory}/.git/hooks/commit-msg${Color_Off}"
    echo
    cp ${topdir}/tools/commit-msg.hook ${topsrcdir}/${directory}/.git/hooks/commit-msg
    ls -lrth ${topsrcdir}/${directory}/.git/hooks/commit-msg
    echo
else
    echo -e "${H2}${Arrow}Reusing existing repo${Color_Off}"
    if [[ ${source_sha} != "null" ]]; then
        # Using source SHAs requires special casing because git lacks a fast path.
        # Shallow cloning is also not possible, so that flag is ignored in this case.
        echo -e "${H2}Checking out ${source_sha}...${Color_Off}"
        cd ${topsrcdir}/${directory}
        git fetch --unshallow || true
        git checkout ${source_sha}
    elif [ "${shallow_clone}" == "true" ]; then
        echo -e "${H2}Adding remote ${source_branch} to a pre-existing shallow clone${Color_Off}"
        cd ${topsrcdir}/${directory}
        git remote set-branches origin '*'
        git fetch -v --depth=1
        git checkout ${source_branch}
    else
        echo -e "${H2}Checking out ${source_branch} from pre-existing deep clone${Color_Off}"
        cd ${topsrcdir}/${directory}
        git checkout ${source_branch}
    fi
fi

if [ ! -e ${topsrcdir}/${directory}/.git/hooks/commit-msg ]; then
    echo "Commit message hook missing!"
    exit 1
else
    echo "Commit message hook is at ${topsrcdir}/${directory}/.git/hooks/commit-msg..."
fi

cd "${topsrcdir}/${directory}"

# If we already have the branch, then this is a reused dependency
if [ $(git branch | grep -c ${destination_branch} || true) -gt 0 ]; then
    echo "We already have a branch called ${destination_branch}, assuming this is a reused dependency."
    trap - EXIT
    exit 0
fi

git checkout -b ${destination_branch}
echo -e "${H2}Working in branch ${destination_branch}${Color_Off}"
cd ${topdir}

if [ -e ${project}/PREPATCH ]; then
    for patch in $(cat ${project}/PREPATCH)
    do
        echo

        echo -e "${H3}Applying ${source_branch} ${project}/${patch}${Color_Off}"
        ls -l ${topdir}/${project}
        git -C ${topsrcdir}/${directory} apply -v ${topdir}/${project}/${patch}
        if [ $? -gt 0 ]; then
            echo -e "${H3}Applying ${source_branch} ${project}/${patch} failed!${Color_Off}"
            exit 1
        fi

        pushd ${topsrcdir}/${directory} > /dev/null
        echo -e "${H3}Committing ${source_branch} ${patch}${Color_Off}"
        git add -A .

        if [ $(git status | grep -c "Untracked files:" || true) -gt 0 ]; then
            echo "Untracked files!"
            exit 1
        fi

        echo -e "${H3}Extracting commit message from ${topdir}/${project}/${patch}${Color_Off}"
        python3 ${topdir}/tools/extract-commit-message ${topdir}/${project}/${patch}

        git commit -s -a --file ${topdir}/${project}/${patch}-message
        echo

        if [ "${update_patches}" == "true" ]; then
            echo -e "${H3}Update patch with what was applied${Color_Off}"
            git show > ${topdir}/${project}/${patch}
        fi

        popd > /dev/null
    done
fi

echo -e "${H3}Ensure tests pass on a clean ${project} ${source_branch} branch${Color_Off}"
cd ${topsrcdir}/${directory}
if [ "${defer_tests}" != "true" ] && [ -z "${test_patch}" ]; then
    run_tests ${repo} ${source_branch} "upstream"
elif [ -n "${test_patch}" ]; then
    echo -e "${H3}Skipping upstream tests (--test-patch specified)${Color_Off}"
fi
echo

cd ${topdir}

for patch in $(cat ${project}/ORDER)
do
    echo
    shortpatch=$(echo ${patch} | sed -e 's|.*/||' -e 's/.patch$//')

    echo -e "${H3}Applying ${source_branch} ${project}/${patch}${Color_Off}"
    git -C ${topsrcdir}/${directory} apply -v ${topdir}/${project}/${patch}
    if [ $? -gt 0 ]; then
        echo -e "${H3}Applying ${source_branch} ${project}/${patch} failed!${Color_Off}"
        exit 1
    fi

    pushd ${topsrcdir}/${directory} > /dev/null
    echo -e "${H3}Committing ${source_branch} ${patch}${Color_Off}"
    git add -A .

    if [ $(git status | grep -c "Untracked files:" || true) -gt 0 ]; then
        echo "Untracked files!"
        exit 1
    fi

    echo -e "${H3}Extracting commit message from ${topdir}/${project}/${patch}${Color_Off}"
    python3 ${topdir}/tools/extract-commit-message ${topdir}/${project}/${patch}

    git commit -s -a --file ${topdir}/${project}/${patch}-message
    echo

    # Determine whether to run tests for this patch
    should_test="false"
    if [ "${defer_tests}" != "true" ]; then
        if [ -z "${test_patch}" ]; then
            # No specific patch requested, test all
            should_test="true"
        elif [[ "${shortpatch}" == *"${test_patch}"* ]]; then
            # This patch matches the requested test_patch
            should_test="true"
            echo -e "${H3}Running tests for requested patch: ${shortpatch}${Color_Off}"
        else
            echo -e "${H3}Skipping tests for ${shortpatch} (--test-patch ${test_patch})${Color_Off}"
        fi
    fi

    if [ "${should_test}" == "true" ]; then
        run_tests ${repo} ${source_branch} ${shortpatch}
    fi

    if [ "${update_patches}" == "true" ]; then
        echo -e "${H3}Update patch with what was applied${Color_Off}"
        git show > ${topdir}/${project}/${patch}
    fi

    popd > /dev/null
done

if [ ${use_ci_patches} == "true" ]; then
    if [ -e ${project}/ADDITIONAL_FOR_CI ]; then
        for patch in $(cat ${project}/ADDITIONAL_FOR_CI)
        do
            echo
            shortpatch=$(echo ${patch} | sed -e 's|.*/||' -e 's/.patch$//')

            echo -e "${H3}Applying ${source_branch} ${project}/${patch}${Color_Off}"
            git -C ${topsrcdir}/${directory} apply -v ${topdir}/${project}/${patch}
            if [ $? -gt 0 ]; then
                echo -e "${H3}Applying ${source_branch} ${project}/${patch} failed!${Color_Off}"
                exit 1
            fi

            pushd ${topsrcdir}/${directory} > /dev/null
            echo -e "${H3}Committing ${source_branch} ${patch}${Color_Off}"
            git add -A .

            if [ $(git status | grep -c "Untracked files:" || true) -gt 0 ]; then
                echo "Untracked files!"
                exit 1
            fi

            echo -e "${H3}Extracting commit message from ${topdir}/${project}/${patch}${Color_Off}"
            python3 ${topdir}/tools/extract-commit-message ${topdir}/${project}/${patch}

            git commit -s -a --file ${topdir}/${project}/${patch}-message
            echo

            # Determine whether to run tests for this patch
            should_test="false"
            if [ "${defer_tests}" != "true" ]; then
                if [ -z "${test_patch}" ]; then
                    # No specific patch requested, test all
                    should_test="true"
                elif [[ "${shortpatch}" == *"${test_patch}"* ]]; then
                    # This patch matches the requested test_patch
                    should_test="true"
                    echo -e "${H3}Running tests for requested patch: ${shortpatch}${Color_Off}"
                else
                    echo -e "${H3}Skipping tests for ${shortpatch} (--test-patch ${test_patch})${Color_Off}"
                fi
            fi

            if [ "${should_test}" == "true" ]; then
                run_tests ${repo} ${source_branch} ${shortpatch}
            fi

            if [ "${update_patches}" == "true" ]; then
                echo -e "${H3}Update patch with what was applied${Color_Off}"
                git show > ${topdir}/${project}/${patch}
            fi

            popd > /dev/null
        done
    fi
fi

pushd ${topsrcdir}/${directory} > /dev/null
if [ "${defer_tests}" == "true" ] && [ -z "${test_patch}" ]; then
    run_tests ${repo} ${source_branch} "final"
elif [ "${defer_tests}" == "true" ] && [ -n "${test_patch}" ]; then
    echo -e "${H3}Skipping final deferred tests (--test-patch specified)${Color_Off}"
fi
popd > /dev/null

# Cleanup built elements which take a lot of disk
for target in .tox .stestr; do
    if [ -e ${topsrcdir}/${directory}/${target} ]; then
        echo -e "${H2}Cleanup ${target}${Color_Off}"
        rm -rf ${topsrcdir}/${directory}/${target}
    fi
done

# Compress for later stages. Its important we use relative paths here or it
# gets fiddly to extract later...
pushd ${topsrcdir} > /dev/null
tar czf ${directory}.tgz ${directory}
ls -lrth ${topsrcdir}/${directory}.tgz

# Drop a CLAUDE.md into the local directory for development, if one exists
if [ -e ${topdir}/${directory}/_CLAUDE.md ]; then
    cp ${topdir}/${directory}/_CLAUDE.md ${topsrcdir}/${directory}/CLAUDE.md
fi

echo -e "${H2}Success for branch ${source_branch}!${Color_Off}"
echo ""
popd > /dev/null

trap - EXIT