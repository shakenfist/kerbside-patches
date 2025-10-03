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

# Which OpenStack releases to build images for.
build_targets="2023.1 2023.2 2024.1 master"

# Which images to build. Kerbside only requires customized nova-compute,
# nova-libvirt, nova-api, and kerbside container images but it can make sense
# to build all the container images at the same time to keep them consistent.
build_images="nova-compute nova-libvirt nova-api kerbside"

# What tag to use to identify this set of containers.
image_tag="${CI_COMMIT_SHORT_SHA}-debian-bookworm"

# Should we only test once at the end?
defer_tests="false"

# Should we only run pep8 tests?
skip_unit_tests="false"

# Should we skip tests entirely?
skip_tests="false"

# Should we use the CI environment's OCI registry to avoid rebuilding images?
use_ci_registry="false"
ci_registry="192.168.1.5:4000"

# Should we build a compact archive using occystrap?
compact_archive="false"

# Sometimes we're only building images and don't want to fetch them just
# to ignore them
dont_fetch_images="false"

# Ensure we have a git commit sha
if [ -z ${CI_COMMIT_SHORT_SHA} ]; then
    export CI_COMMIT_SHORT_SHA=$(git rev-parse --short HEAD)
fi

# Parse command line
found_arg=1
while [[ ${found_arg} -gt 0 ]]; do
    found_arg=0
    case $1 in
        --compact-archive)
            export skip_tests="true"
            echo "Will create a compact archive."
            shift
            found_arg=1
            ;;
        --defer-tests)
            export defer_tests="true"
            echo "Will defer testing."
            shift
            found_arg=1
            ;;
        --build-images)
            export build_images="$2"
            echo "Setting build images to ${build_images}."
            shift; shift
            found_arg=1
            ;;
        --build-targets)
            export build_targets="$2"
            echo "Setting build targets to ${build_targets}."
            shift; shift
            found_arg=1
            ;;
        --image-tag)
            export image_tag="$2"
            echo "Setting image tag to ${image_tag}."
            shift; shift;
            found_arg=1
            ;;
        --dont-fetch-images)
            export dont_fetch_images="true"
            echo "Will not fetch pre-built images if they exist."
            shift
            found_arg=1
            ;;
        --skip-tests)
            export skip_tests="true"
            echo "Will skip testing."
            shift
            found_arg=1
            ;;
        --use-ci-registry)
            export use_ci_registry="true"
            echo "Will use the CI environment's OCI registry."
            shift
            found_arg=1
            ;;
       --ci-registry)
            export ci_registry="$2"
            echo "Set CI registry to ${ci_registry}."
            shift; shift
            found_arg=1
	   ;;
        -*|--*)
            echo "Unknown option $1"
            exit 1
            ;;
    esac
done

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

function banner {
    echo
    echo -e "${H1}**************************************************${Color_Off}"
    echo -e "${H1}${1}${Color_Off}"
    echo -e "${H1}**************************************************${Color_Off}"
    echo
}


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
        # if [ "${skip_unit_tests}" == "false" ]; then
        #     if [ $(tox -a | grep -c docs) -gt 0 ]
        #     then
        #         echo -e "${H3}tox -edocs${Color_Off}"
        #         tox -edocs | ts "%b %d %H:%M:%S ${2} ${3} docs"
        #         if [ $? -gt 0 ]; then
        #             echo -e "${H3}tox -edocs failed!${Color_Off}"
        #             rm -rf doc/build/html
        #             exit 1
        #         fi
        #         rm -rf doc/build/html
        #     fi
        # fi
    fi

    echo -e "${H2}${ARROW}Tests complete${Color_Off}"
}
