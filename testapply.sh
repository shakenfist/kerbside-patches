#!/bin/bash -e

# Note that our CI environment requires these packages to be installed.
#     From the OS: git moreutils jq
#     From pypi: tox yq

# All positional args are consumed as project names to test. If none are
# specified, all projects are tested. We also optionally take --defer-tests
# to not test in between patch applications and --skip-tests to skip tests
# completely.

. buildconfig.sh


function run_tests {
    # $1 is the name of the branch

    if [ "${skip_tests}" == "true" ]; then
        echo -e "${H3}Skipping tests${Color_Off}"
        return
    fi

    echo -e "${H3}Working in ${topsrcdir}/${directory} on branch ${1}${Color_Off}"
    if [ ! -e tox.ini ]
    then
        echo "${Red}No test configuration found!${Colour_off}"
    else
        if [ $(tox -a | grep -c py3) -gt 0 ]
        then
            echo -e "${H3}tox -epy3${Color_Off}"
            tox -epy3 | ts "%b %d %H:%M:%S ${1} ${shortpatch} py3"
            if [ $? -gt 0 ]; then
                echo -e "${H3}tox -epy3 failed!${Color_Off}"
                exit 1
            fi
        fi

        # Nova has both fast8 and pep8, but runs pep8 in their CI so that
        # should be our gold standard.
        if [ $(tox -a | grep -c pep8) -gt 0 ]
        then
            echo -e "${H3}tox -epep8${Color_Off}"
            tox -epep8 | ts "%b %d %H:%M:%S ${1} ${shortpatch} pep8"
            if [ $? -gt 0 ]; then
                echo -e "${H3}tox -pep8 failed!${Color_Off}"
                exit 1
            fi
        elif [ $(tox -a | grep -c flake8) -gt 0 ]
        then
            echo -e "${H3}tox -eflake8${Color_Off}"
            tox -eflake8 | ts "%b %d %H:%M:%S ${1} ${shortpatch} flake8"
            if [ $? -gt 0 ]; then
                echo -e "${H3}tox -eflake8 failed!${Color_Off}"
                exit 1
            fi
        fi
    fi

    echo -e "${H2}${ARROW}Tests complete${Color_Off}"
}


topdir=$(pwd)
topsrcdir="${topdir}/src"

if [ "${positional_args}" == "" ]; then
    positional_args=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
fi

for project in ${positional_args}; do
    echo
    echo -e "${H1}==================================================${Color_Off}"
    echo -e "${H1}${project}${Color_Off}"
    echo -e "${H1}==================================================${Color_Off}"

    repo=$(yq -r .repo ${project}/config.yaml)
    branch=$(yq -r .branch ${project}/config.yaml)
    shallow_clone=$(yq -r .shallow_clone ${project}/config.yaml)
    directory=$(yq -r .directory ${project}/config.yaml)

    echo -e "${H2}${Arrow}Repository: ${repo}${Color_Off}"
    echo -e "${H2}${Arrow}Branch: ${branch}${Color_Off}"
    echo -e "${H2}${Arrow}Shallow cloning: ${shallow_clone}${Color_Off}"
    echo

    mkdir -p "${topsrcdir}"

    if [ ! -e ${topsrcdir}/${directory} ]; then
        echo -e "${H2}${Arrow}Cloning repo${Color_Off}"
        if [ "${shallow_clone}" == "true" ]; then
            echo -e "${H2}Shallow cloning ${repo} branch ${branch}${Color_Off}"
            git clone --depth 1 --branch ${branch} ${repo} ${topsrcdir}/${directory}
        else
            echo -e "${H2}Full depth cloning ${repo} branch ${branch}${Color_Off}"
            git clone --branch ${branch} ${repo} ${topsrcdir}/${directory}
        fi
    else
        echo -e "${H2}${Arrow}Reusing existing repo${Color_Off}"
        if [ "${shallow_clone}" == "true" ]; then
            echo -e "${H2}Adding remote ${branch} to a pre-existing shallow clone${Color_Off}"
            cd ${topsrcdir}/${directory}
            git remote set-branches origin '*'
            git fetch -v --depth=1
            git checkout ${branch}
        else
            echo -e "${H2}Checking out ${branch} from pre-existing deep clone${Color_Off}"
            cd ${topsrcdir}/${directory}
            git checkout ${branch}
        fi
    fi

    cd ${topsrcdir}/${directory}
    git checkout -b ${branch}-patches
    echo -e "${H2}Working in branch ${branch}-patches${Color_Off}"
    cd ${topdir}

    for patch in $(cat ${project}/ORDER)
    do
        echo
        shortpatch=$(echo $patch | cut -f 2 -d "/" | cut -f 1 -d "-")

        echo -e "${H3}Applying ${branch} ${project}/${patch}${Color_Off}"
        git -C ${topsrcdir}/${directory} apply -v ${topdir}/${project}/${patch}
        if [ $? -gt 0 ]; then
            echo -e "${H3}Applying ${branch} ${project}/${patch} failed!${Color_Off}"
            exit 1
        fi

        pushd ${topsrcdir}/${directory}

        if [ "${defer_tests}" != "true" ]; then
            run_tests ${branch}
        fi

        echo -e "${H3}Commiting ${branch} ${patch}${Color_Off}"
        git add -A .
        git status
        git commit -a -m "${patch}"
        echo
        popd
    done

    pushd ${topsrcdir}/${directory}
    if [ "${defer_tests}" == "true" ]; then
        run_tests ${branch}
    fi
    popd

    # Cleanup built elements which take a lot of disk
    for target in .tox .stestr; do
        if [ -e ${topsrcdir}/${directory}/${target} ]; then
            echo -e "${H2}Cleanup ${target}${Color_Off}"
            rm -rf ${topsrcdir}/${directory}/${target}
        fi
    done

    # Compress for later stages. Its important we use relative paths here or it
    # gets fiddly to extract later...
    pushd ${topsrcdir}
    tar czf ${directory}.tgz ${directory}
    ls -lrth ${topsrcdir}/${directory}.tgz

    echo -e "${H2}Success for branch ${branch}!${Color_Off}"
    echo ""
    popd
done

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}All patches applied correctly.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"
