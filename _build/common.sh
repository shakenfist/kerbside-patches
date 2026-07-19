# shellcheck shell=bash
# Intended to be sourced by all build scripts. This is command line parsing,
# helper functions, etc etc.

topdir=$(pwd)
topsrcdir="${topdir}/src"

if [ -e /srv/shakenfist/kerbside-patches-tools/bin/activate ]; then
    . /srv/shakenfist/kerbside-patches-tools/bin/activate
fi

# Improve pip reliability for CI environments with flaky
# connectivity to PyPI.
export PIP_RETRIES=${PIP_RETRIES:-5}
export PIP_DEFAULT_TIMEOUT=${PIP_DEFAULT_TIMEOUT:-120}

# Debian 13 mounts /tmp as a small tmpfs by default. kolla-build work
# directories and occystrap temporary layer blobs are much bigger than
# it, so send temporary files to the disk-backed /var/tmp instead.
if [ -z "${TMPDIR:-}" ] && [ "$(findmnt -no FSTYPE /tmp 2>/dev/null)" == "tmpfs" ]; then
    export TMPDIR=/var/tmp
fi

##############################################################################
# Command line parsing                                                       #
##############################################################################

# Which OpenStack releases to build images for.
build_targets="2023.1 2023.2 2024.1 master"

# Which base distribution to use for container images.
distro="debian"

# Which version of the base distribution to use for container images.
distro_version="bookworm"

# Which images to build. Kerbside only requires customized nova-compute,
# nova-libvirt, nova-api, and kerbside container images but it can make sense
# to build all the container images at the same time to keep them consistent.
build_images="nova-compute nova-libvirt nova-api kerbside"

# What tag to use to identify this set of containers.
image_tag="undefined"

# Should we only test once at the end?
defer_tests="false"

# Should we only run pep8 tests?
skip_unit_tests="false"

# Should we skip tests entirely?
skip_tests="false"

# Should we only test a specific patch? (empty means test all)
test_patch=""

# Should we rewrite the patch file to match what was applied?
update_patches="false"

# Should we use the CI environment's OCI registry to avoid rebuilding images?
use_ci_patches="false"
use_ci_registry="false"
ci_gitlab="http://192.168.1.12"
ci_registry="gitlab.home.stillhq.com:5050"
registry_project="openstack/kolla-images"
registry_username=""
registry_token=""

# Should we enable kerbside?
enable_kerbside="true"
# Should we skip tarballing the source directories?
no_tarball="false"

# Should we build a compact archive using occystrap?
compact_archive="false"

# Sometimes we're only building images and don't want to fetch them just
# to ignore them
dont_fetch_images="false"

# Should we use the occystrap filtering proxy for image pushes?
use_proxy="false"

# Should we skip the debsecan vulnerability scan?
skip_debsecan="false"

# Should we fail the build if fixable CVEs are found?
debsecan_fail_on_fixable="false"

# If we are building a cloud, what inventory should we use?
topology="all-in-one"

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
        --no-tarball)
            export no_tarball="true"
            echo "Will not tar up source directories."
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
        --distro)
            export distro="$2"
            echo "Setting base distribution to ${distro}."
            shift; shift
            found_arg=1
            ;;
        --distro-version)
            export distro_version="$2"
            echo "Setting base distribution version to ${distro_version}."
            shift; shift
            found_arg=1
            ;;
        --image-tag)
            export image_tag="$2"
            echo "Setting image tag to ${image_tag}."
            shift; shift;
            found_arg=1
            ;;
        --disable-kerbside)
            export enable_kerbside="false"
            echo "Will disable kerbside."
            shift
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
        --test-patch)
            export test_patch="$2"
            echo "Will only test patch: ${test_patch}"
            shift; shift
            found_arg=1
            ;;
        --ci-gitlab)
            export ci_gitlab="$2"
            echo "Set CI gitlab to ${ci_gitlab}"
            shift; shift
            found_arg=1
            ;;
        --use-ci-patches)
            export use_ci_patches="true"
            echo "Will use additonal patches for CI."
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
       --registry-username)
            export registry_username="$2"
            echo "Set CI registry username to ${registry_username}."
            shift; shift
            found_arg=1
	        ;;
       --registry-token)
            export registry_token="$2"
            echo "Set CI registry password."
            shift; shift
            found_arg=1
	        ;;
       --topology)
            export topology="$2"
            echo "Set topology to ${topology}."
            shift; shift
            found_arg=1
	        ;;
        --use-proxy)
            export use_proxy="true"
            echo "Will use occystrap proxy for image pushes."
            shift
            found_arg=1
            ;;
        --update-patches)
            export update_patches="true"
            echo "Will update patches to match what was applied."
            shift
            found_arg=1
            ;;
        --skip-debsecan)
            export skip_debsecan="true"
            echo "Will skip debsecan vulnerability scan."
            shift
            found_arg=1
            ;;
        --debsecan-fail-on-fixable)
            export debsecan_fail_on_fixable="true"
            echo "Will fail build if fixable CVEs are found."
            shift
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
    echo -e "${Red}*** Failed ***${Color_Off}"
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


function install_test_environment {
    # $1 is the name of the environment
    echo -e "${H3}Build tox -e${1} environment${Color_Off}"

    echo -e "    ${Arrow} Attempt #1${Color_Off}"
    if tox -e${1} --notest; then
        return
    fi

    sleep 60

    echo -e "    ${Arrow} Attempt #2${Color_Off}"
    if tox -e${1} --notest; then
        return
    fi

    sleep 60

    echo -e "    ${Arrow} Attempt #3${Color_Off}"
    if tox -e${1} --notest; then
        return
    fi

    echo -e "    ${Arrow} Failed!${Color_Off}"
    exit 1
}


function run_tests {
    # $1 is the repo
    # $2 is the name of the branch
    # $3 is the short name of the patch

    if [ "${skip_tests}" == "true" ]; then
        echo -e "${H3}Skipping tests${Color_Off}"
        return
    fi

    # Use local constraints file to avoid transient DNS failures when
    # tox fetches the remote constraints URL. Projects that depend on
    # "requirements" already have a local copy; for others we download
    # it with retries.
    if [ -z "${TOX_CONSTRAINTS_FILE}" ]; then
        local_constraints="${topsrcdir}/requirements/upper-constraints.txt"
        if [ -e "${local_constraints}" ]; then
            export TOX_CONSTRAINTS_FILE="${local_constraints}"
            echo -e "${H3}Using local constraints: ${local_constraints}${Color_Off}"
        else
            # Extract the release from the branch (e.g. stable/2025.1 -> 2025.1)
            release_name="${2##*/}"
            constraints_url="https://releases.openstack.org/constraints/upper/${release_name}"
            downloaded="${topsrcdir}/upper-constraints-${release_name}.txt"

            echo -e "${H3}Downloading constraints from ${constraints_url}${Color_Off}"
            for attempt in 1 2 3; do
                if curl -fsSL --retry 3 --retry-delay 5 \
                        "${constraints_url}" -o "${downloaded}" 2>/dev/null; then
                    export TOX_CONSTRAINTS_FILE="${downloaded}"
                    echo -e "${H3}Using downloaded constraints: ${downloaded}${Color_Off}"
                    break
                fi
                echo -e "${H3}Download attempt ${attempt} failed, retrying in 10s...${Color_Off}"
                sleep 10
            done

            if [ -z "${TOX_CONSTRAINTS_FILE}" ]; then
                echo -e "${YELLOW}WARNING: Could not download constraints, tox will fetch remotely${Color_Off}"
            fi
        fi
    fi

    echo -e "${H3}Working in ${topsrcdir}/${directory} on branch ${1}${Color_Off}"

    if [ ! -e tox.ini ]; then
        echo "${Red}No test configuration found!${Color_Off}"
    else
        if [ "${skip_unit_tests}" == "false" ]; then
            if [ $(tox -a | grep -c py3) -gt 0 ]; then
                install_test_environment py3
                echo
                echo -e "${H3}tox -epy3${Color_Off}"
                tox -epy3 | ts "%b %d %H:%M:%S ${2} ${3} py3"
            fi
        fi

        # Nova has both fast8 and pep8, but runs pep8 in their CI so that
        # should be our gold standard.
        if [ $(tox -a | grep -c pep8) -gt 0 ]; then
            install_test_environment pep8
            echo
            echo -e "${H3}tox -epep8${Color_Off}"
            tox -epep8 | ts "%b %d %H:%M:%S ${2} ${3} pep8"
        elif [ $(tox -a | grep -c flake8) -gt 0 ]; then
            install_test_environment flake8
            echo
            echo -e "${H3}tox -eflake8${Color_Off}"
            tox -eflake8 | ts "%b %d %H:%M:%S ${2} ${3} flake8"
    fi

        # Nova has functional tests which do not require devstack. Other projects
        # require devstack, which we don't do right now.
        if [ $(echo ${repo} | grep -c "/nova" || true) -gt 0 ]; then
            if [ $(tox -a | egrep -c "functional$") -gt 0 ]; then
                install_test_environment functional
                echo
                echo -e "${H3}tox -efunctiona${Color_Off}"
                tox -efunctional | ts "%b %d %H:%M:%S ${2} ${3} functional"
            fi
        fi

        # Kolla-Ansible also has additional linters as well as a pep8
        if [ $(tox -a | grep -c linters) -gt 0 ]; then
            install_test_environment linters
            echo
            echo -e "${H3}tox -elinters${Color_Off}"
            # ansible-lint clones ansible-collection-kolla from opendev.org,
            # which is intermittently unreachable. Retry on transient
            # network errors before failing the run. The caller has
            # errexit + pipefail on, so we disable errexit around the
            # pipeline to let the loop observe PIPESTATUS instead of
            # exiting on the first failure.
            linters_tmp=$(mktemp)
            linters_attempts=3
            linters_delay=30
            linters_rc=0
            set +e
            for (( linters_try=1; linters_try<=linters_attempts; linters_try++ )); do
                tox -elinters 2>&1 \
                    | tee "${linters_tmp}" \
                    | ts "%b %d %H:%M:%S ${2} ${3} linters"
                linters_rc=${PIPESTATUS[0]}
                if [ "${linters_rc}" -eq 0 ]; then
                    break
                fi
                if ! grep -qE \
                        "Failed to connect to opendev\.org|unable to access 'https?://opendev\.org|Could not resolve host: opendev\.org" \
                        "${linters_tmp}"; then
                    break
                fi
                if [ "${linters_try}" -lt "${linters_attempts}" ]; then
                    echo -e "${Yellow}WARNING: transient opendev.org network error during tox -elinters; retrying in ${linters_delay}s (attempt $((linters_try + 1))/${linters_attempts})${Color_Off}"
                    sleep "${linters_delay}"
                    linters_delay=$((linters_delay * 2))
                fi
            done
            set -e
            rm -f "${linters_tmp}"
            if [ "${linters_rc}" -ne 0 ]; then
                false
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

    echo -e "${H2}${Arrow}Tests complete${Color_Off}"
}
