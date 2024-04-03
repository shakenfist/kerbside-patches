#!/bin/bash -e

# Ensure we fail even when piping output to ts
set -o pipefail

# Note that our CI environment requires these packages to be installed.
#     From the OS: git moreutils jq
#     From pypi: tox yq

# All positional args are consumed as project names to test. If none are
# specified, all projects are tested. We also optionally take --defertests
# to not test in between patch applications and --skiptests to skip tests
# completely.
#
# These options can also be enabled by setting environment variables
#   KS_DEFERTESTS="true"
#   KS_SKIPTESTS="true"

POSITIONAL_ARGS=()

# Color helpers, from https://stackoverflow.com/questions/5947742/
Color_Off='\033[0m'       # Text Reset
Red='\033[0;31m'          # Red
Green='\033[0;32m'        # Green
Yellow='\033[0;33m'       # Yellow
Blue='\033[0;34m'         # Blue
Purple='\033[0;35m'       # Purple
Cyan='\033[0;36m'         # Cyan
White='\033[0;37m'        # White

# And an arrow!
Arrow='\u2192'

H1="${Green}"
H2="${Blue}"
H3="${Arrow}${Purple}"

function on_exit {
    echo
    echo -e "${Red}*** Failed ***${No_Color}"
    echo
    }
trap 'on_exit $?' EXIT

# Parse flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --defertests)
      KS_DEFERTESTS="true"
      echo -e "${H1}Will run tests once at the end (KS_DEFERTESTS='true')${Color_Off}"
      echo
      shift
      ;;
    --skiptests)
      KS_SKIPTESTS="true"
      echo -e "${H1}Will skip running tests completely (KS_SKIPTESTS='true')${Color_Off}"
      echo
      shift
      ;;
    -*|--*)
      echo "Unknown option $1"
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL_ARGS[@]}"


function run_tests {
    # $1 is the name of the branch

    if [ "${KS_SKIPTESTS}" == "true" ]; then
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
        fi

        # Nova has both fast8 and pep8, but runs pep8 in their CI so that
        # should be our gold standard.
        if [ $(tox -a | grep -c pep8) -gt 0 ]
        then
            echo -e "${H3}tox -epep8${Color_Off}"
            tox -epep8 | ts "%b %d %H:%M:%S ${1} ${shortpatch} pep8"
        elif [ $(tox -a | grep -c flake8) -gt 0 ]
        then
            echo -e "${H3}tox -eflake8${Color_Off}"
            tox -eflake8 | ts "%b %d %H:%M:%S ${1} ${shortpatch} flake8"
        fi
    fi

    echo -e "${H2}${ARROW}Tests complete${Color_Off}"
}


topdir=$(pwd)
topsrcdir="${topdir}/src"

projects="$@"
if [ "${projects}" == "" ]; then
    projects=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
fi

for project in ${projects}; do
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

        pushd ${topsrcdir}/${directory}

        if [ "${KS_DEFERTESTS}" != "true" ]; then
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
    if [ "${KS_DEFERTESTS}" == "true" ]; then
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
