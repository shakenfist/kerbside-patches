#!/bin/bash -e

# Note that our CI environment requires these packages to be installed.
#     From the OS: git moreutils python3-venv
#     From pypi: tox

topdir=$(pwd)
topsrcdir="${topdir}/src"

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Preparing artifacts from previous stages${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

projects=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
declare -a directories
mkdir -p ${topsrcdir}
for project in kerbside ${projects}; do
    if [ ${project} == "kerbside" ]; then
        directory="kerbside"
    else
        directory=$(yq -r .directory ${project}/config.yaml)
    fi

    if [ ! -e ${topsrcdir}/${directory} ]; then
        echo -e "${H2}Extract ${directory}.tgz for ${project} ${Color_Off}"
        tar xzf ${topsrcdir}/${directory}.tgz -C ${topsrcdir}/
        directories+=(${directory})
    else
        echo -e "${H2}${project} shares ${directory}${Color_Off}"
    fi
done

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}State of build dependencies${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"
du -sh ${topsrcdir}/*

# Docker image build steps, which are pre target branch
for target in ${build_targets}; do
    echo
    echo -e "${H1}==================================================${Color_Off}"
    echo -e "${H1}Building docker images for ${target}${Color_Off}"
    echo -e "${H1}==================================================${Color_Off}"

    if [ ${target} == "master" ]; then
        target_branch="master-patches"
    else
        target_branch="stable/${target}-patches"
    fi
    echo -e "${H2}Target branch is ${target_branch}${Color_Off}"

    # Checkout the target branch in all our directories. Kerbside is a special
    # case as it doesn't obey the OpenStack branch naming conventions.
    for directory in "${directories[@]}"; do
        if [ ${directory} == "kerbside" ]; then
            tb="develop"
        elif [ ${directory} == "nova-specs" ]; then
            tb="master"
        else
            tb="${target_branch}"
        fi

        echo -e "${H2}${Arrow}Checkout ${tb} in ${directory}${Color_Off}"
        pushd ${topsrcdir}/${directory}
        git checkout ${tb}
        popd
    done

    # Create a venv
    target_version=$(echo ${target_branch} | sed 's/stable\///')
    venvdir="${topdir}/venv-${target_version}"
    if [ ! -f ${venvdir}/bin/activate ]; then
        rm -rf ${venvdir}
        echo
        echo -e "${H2}Create build venv at ${venvdir}${Color_Off}"
        python3 -mvenv "${venvdir}"
    else
        echo -e "${H2}Using existing build venv ${venvdir}${Color_Off}"
    fi

    # Install kolla, docker and oslo
    if [ ! -f ${venvdir}/bin/kolla-build ]; then
        # We need to override the version of oslo.config so that it doesn't get clobbered
        # by the Kolla install
        export PBR_VERSION=10.0.0
        ${venvdir}/bin/pip install "${topsrcdir}/oslo.config"
        unset PBR_VERSION

        ${venvdir}/bin/pip install "${topsrcdir}/kolla"
        ${venvdir}/bin/pip install docker
    else
        echo -e "${H2}Using existing kolla install in ${venvdir}${Color_Off}"
    fi

    # Check for known broken versions of python requests
    # See https://github.com/docker/docker-py/pull/3257 for details
    echo
    requests_version=$(${venvdir}/bin/pip list 2> /dev/null | grep requests | tr -s " " | cut -f 2 -d " ")
    echo -e "${H2}Detected python requests version ${requests_version}${Color_Off}"
    if [ $(echo $requests_version | egrep -c "2\.32") -gt 0 ]; then
        echo -e "${H3}Buggy requests version detected. Downgrading.${Color_Off}"
	${venvdir}/bin/pip install requests==2.31.0
    fi

    # Customize the kolla-build.conf file
    echo
    echo -e "${H2}Customize build configuration${Color_Off}"
    cat kolla-build.conf.in | \
        sed "s|TOPSRCDIR|${topsrcdir}|g" \
        > kolla-build.conf

    # Clear build cache
    echo -e "${H2}Clear build cache${Color_Off}"
    docker buildx prune -f

    # Build images
    echo
    echo -e "${H2}Build images${Color_Off}"
    cd ${topsrcdir}

    kolla_build_args=${build_images}
    if [ "${build_images}" == "all" ]; then
        kolla_build_args=""
    fi

    echo -e "${H3}${venvdir}/bin/kolla-build \\"
    echo -e "    --config-file \"${topdir}/kolla-build.conf\" \\"
    echo -e "    --tag ${target}-${CI_COMMIT_SHORT_SHA} \\"
    echo -e "    --namespace kolla ${kolla_build_args} | ts \"%b %d %H:%M:%S ${target}\""
    echo -e "${Color_Off}"

    ${venvdir}/bin/kolla-build \
        --config-file "${topdir}/kolla-build.conf" \
        --tag ${target}-${CI_COMMIT_SHORT_SHA} \
        --namespace kolla ${kolla_build_args} | ts "%b %d %H:%M:%S ${target}"

    echo
    echo -e "${H3}Exit code: ${?}"
    cd ${topdir}
done

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}All docker images built correctly.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"
