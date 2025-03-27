# Intended to be sourced by all build scripts. This is command line parsing,
# helper functions, etc etc.

topdir=$(pwd)
topsrcdir="${topdir}/src"

if [ -e /srv/shakenfist/kerbside-patches-tools/bin/activate ]; then
    . /srv/shakenfist/kerbside-patches-tools/bin/activate
fi

##############################################################################
# Command line parsing                                                       #
##############################################################################

default_build_targets="2023.1 2023.2 2024.1 master"

# Which images to build. Kerbside only requires customized nova-compute,
# nova-libvirt, nova-api, and kerbside container images but it can make sense
# to build all the container images at the same time to keep them consistent.
default_build_images="nova-compute nova-libvirt nova-api kerbside"

# Should we only test once at the end?
defer_tests="false"

# Should we only run pep8 tests?
skip_unit_tests="false"

# Should we skip tests entirely?
skip_tests="false"

# Should we use the CI environment's OCI registry to avoid rebuilding images?
use_ci_registry="false"

# Should we build a compact archive using occystrap?
compact_archive="false"

# Ensure we have a git commit sha
if [ -z ${CI_COMMIT_SHORT_SHA} ]; then
    export CI_COMMIT_SHORT_SHA=$(git rev-parse --short HEAD)
fi

# Parse command line
export build_targets=${default_build_targets}
export build_images=${default_build_images}
export positional_args=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --compact-archive)
            export skip_tests="true"
            echo "Will create a compact archive."
            shift
            ;;
        --defer-tests)
            export defer_tests="true"
            echo "Will defer testing."
            shift
            ;;
        --build-images)
            export build_images="$2"
            echo "Setting build images to ${build_images}."
            shift; shift
            ;;
        --build-targets)
            export build_targets="$2"
            echo "Setting build targets to ${build_targets}."
            shift; shift
            ;;
        --skip-tests)
            export skip_tests="true"
            echo "Will skip testing."
            shift
            ;;
        --use-ci-registry)
            export use_ci_registry="true"
            echo "Will use the CI environment's OCI registry."
            shift
            ;;
        -*|--*)
            echo "Unknown option $1"
            exit 1
            ;;
        *)
            positional_args+=("$1")
            shift
            ;;
    esac
done

# You can't export bash arrays, so we dance instead
export positional_args=$(printf "%s\n" "${positional_args[@]}")
if [ -z ${positional_args} ]; then
    export positional_args=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
fi

# Ensure we fail even when piping output to ts
set -o pipefail

##############################################################################
# Ensure our required dependencies are installed                             #
##############################################################################

# for command in git ts jq tox yq; do
#     which ${command} > /dev/null
#     if [ ${?} -gt 0 ]; then
#         echo "${command} appears to be missing, please install it!"
#         exit 1
#     fi
# done

##############################################################################
# Pretty output helpers                                                      #
##############################################################################

# Color helpers, from https://stackoverflow.com/questions/5947742/
export Color_Off='\033[0m'       # Text Reset
export Red='\033[0;31m'          # Red
export Green='\033[0;32m'        # Green
export Yellow='\033[0;33m'       # Yellow
export Blue='\033[0;34m'         # Blue
export Purple='\033[0;35m'       # Purple
export Cyan='\033[0;36m'         # Cyan
export White='\033[0;37m'        # White

# And an arrow!
export Arrow='\u2192 '

export H1="${Green}"
export H2="${Blue}"
export H3="${Arrow}${Purple}"

# Make failures more obvious
function on_exit {
    echo
    echo -e "${Red}*** Failed ***${No_Color}"
    echo
    exit 1
    }
trap 'on_exit $?' EXIT

##############################################################################
# Helpers                                                                    #
##############################################################################

function run_tests {
    # $1 is the repo
    # $2 is the name of the branch
    # $3 is the short name of the patch

    if [ "${skip_tests}" == "true" ]; then
        echo -e "${H3}Skipping tests${Color_Off}"
        return
    fi

    echo -e "${H3}Working in ${topsrcdir}/${directory} on branch ${1}${Color_Off}"

    if [ ! -e tox.ini ]
    then
        echo "${Red}No test configuration found!${Colour_off}"
    else
        if [ "${skip_unit_tests}" == "false" ]; then
            if [ $(tox -a | grep -c py3) -gt 0 ]
            then
                echo -e "${H3}tox -epy3${Color_Off}"
                tox -epy3 | ts "%b %d %H:%M:%S ${2} ${3} py3"
                if [ $? -gt 0 ]; then
                    echo -e "${H3}tox -epy3 failed!${Color_Off}"
                    exit 1
                fi
            fi
        fi

        # Nova has both fast8 and pep8, but runs pep8 in their CI so that
        # should be our gold standard.
        if [ $(tox -a | grep -c pep8) -gt 0 ]
        then
            echo -e "${H3}tox -epep8${Color_Off}"
            tox -epep8 | ts "%b %d %H:%M:%S ${2} ${3} pep8"
            if [ $? -gt 0 ]; then
                echo -e "${H3}tox -epep8 failed!${Color_Off}"
                exit 1
            fi
        elif [ $(tox -a | grep -c flake8) -gt 0 ]
        then
            echo -e "${H3}tox -eflake8${Color_Off}"
            tox -eflake8 | ts "%b %d %H:%M:%S ${2} ${3} flake8"
            if [ $? -gt 0 ]; then
                echo -e "${H3}tox -eflake8 failed!${Color_Off}"
                exit 1
            fi
        fi

        # Nova has functional tests which do not require devstack. Other projects
        # require devstack, which we don't do right now.
        if [ $(echo ${repo} | grep -c "/nova" || true) -gt 0 ]; then
            if [ $(tox -a | egrep -c "functional$") -gt 0 ]
            then
                echo -e "${H3}tox -efunctional${Color_Off}"
                tox -efunctional | ts "%b %d %H:%M:%S ${2} ${3} functional"
                if [ $? -gt 0 ]; then
                    echo -e "${H3}tox -efunctional failed!${Color_Off}"
                    exit 1
                fi
            fi
        fi

        # Try building docs too
        if [ "${skip_unit_tests}" == "false" ]; then
            if [ $(tox -a | grep -c docs) -gt 0 ]
            then
                echo -e "${H3}tox -edocs${Color_Off}"
                tox -edocs | ts "%b %d %H:%M:%S ${2} ${3} docs"
                if [ $? -gt 0 ]; then
                    echo -e "${H3}tox -edocs failed!${Color_Off}"
                    rm -rf doc/build/html
                    exit 1
                fi
                rm -rf doc/build/html
            fi
        fi
    fi

    echo -e "${H2}${ARROW}Tests complete${Color_Off}"
}

function apply_patches_and_test_one {
    # $1 is the project
    project="${1}"

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
        $0 ${dependency} ${extra}

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

            pushd ${topsrcdir}/${directory}
            echo -e "${H3}Committing ${source_branch} ${patch}${Color_Off}"
            git add -A .

            if [ $(git status | grep -c "Untracked files:" || true) -gt 0 ]; then
                echo "Untracked files!"
                exit 1
            fi

            echo -e "${H3}Extracting commit message from ${topdir}/${project}/${patch}${Color_Off}"
            python3 ${topdir}/tools/extract-commit-message ${topdir}/${project}/${patch}

            git commit -a --file ${topdir}/${project}/${patch}-message
            echo

            popd
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

        pushd ${topsrcdir}/${directory}
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

        git commit -a --file ${topdir}/${project}/${patch}-message
        echo

        if [ "${defer_tests}" != "true" ]; then
            run_tests ${repo} ${source_branch} ${shortpatch}
        fi

        popd
    done

    pushd ${topsrcdir}/${directory}
    if [ "${defer_tests}" == "true" ]; then
        run_tests ${repo} ${source_branch} "final"
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

    echo -e "${H2}Success for branch ${source_branch}!${Color_Off}"
    echo ""
    popd
}