#!/bin/bash -e

# Run from the top directory.
. _build/common.sh

project="${1}"
if [ -z ${project} ]; then
    echo "Please specify a project."
    exit 1
fi

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}${project}${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

repo=$(yq -r .repo ${project}/config.yaml)
source_branch=$(yq -r .source_branch ${project}/config.yaml)
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
echo

# Do this thing, but for our dependencies...
for dependency in ${depends_on}; do
    echo
    echo -e "${H1}**************************************************${Color_Off}"
    echo -e "${H1}Entering ${dependency}${Color_Off}"
    echo -e "${H1}**************************************************${Color_Off}"
    echo

    extra=""
    if [ "${skip_tests}" == "true" ]; then
        extra="--skip-tests"
    fi
    $0 ${extra} ${dependency}

    echo
    echo -e "${H1}**************************************************${Color_Off}"
    echo -e "${H1}Finished with ${dependency}${Color_Off}"
    echo -e "${H1}**************************************************${Color_Off}"
    echo
done

echo
echo -e "${H2}${Arrow}Dependency processing complete${Color_Off}"
echo

mkdir -p "${topsrcdir}"

if [ ! -e ${topsrcdir}/${directory} ]; then
    echo -e "${H2}${Arrow}Cloning repo${Color_Off}"
    if [ "${shallow_clone}" == "true" ]; then
        echo -e "${H2}Shallow cloning ${repo} branch ${source_branch}${Color_Off}"
        git clone --depth 1 --branch ${source_branch} ${repo} ${topsrcdir}/${directory}
    else
        echo -e "${H2}Full depth cloning ${repo} branch ${source_branch}${Color_Off}"
        git clone --branch ${source_branch} ${repo} ${topsrcdir}/${directory}
    fi
else
    echo -e "${H2}${Arrow}Reusing existing repo${Color_Off}"
    if [ "${shallow_clone}" == "true" ]; then
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

        popd > /dev/null
    done
fi

echo -e "${H3}Ensure tests pass on a clean ${project} ${source_branch} branch${Color_Off}"
cd ${topsrcdir}/${directory}
if [ "${defer_tests}" != "true" ]; then
    run_tests ${repo} ${source_branch} "upstream"
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

    if [ ! -e ${topdir}/${project}/${patch}-message ]; then
        echo -e "${H3}Extracting commit message from ${topdir}/${project}/${patch}${Color_Off}"
        python3 ${topdir}/tools/extract-commit-message ${topdir}/${project}/${patch}
    fi

    git commit -s -a --file ${topdir}/${project}/${patch}-message
    echo

    if [ "${defer_tests}" != "true" ]; then
        run_tests ${repo} ${source_branch} ${shortpatch}
    fi

    popd > /dev/null
done

pushd ${topsrcdir}/${directory} > /dev/null
if [ "${defer_tests}" == "true" ]; then
    run_tests ${repo} ${source_branch} "final"
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

echo -e "${H2}Success for branch ${source_branch}!${Color_Off}"
echo ""
popd > /dev/null

trap - EXIT