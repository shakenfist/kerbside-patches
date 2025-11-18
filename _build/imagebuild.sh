#!/bin/bash -e

# Run from the top directory.
. _build/common.sh

banner "State of build dependencies"
du -sh ${topsrcdir}/*

# Docker image build steps, which are pre target branch
for target in ${build_targets}; do
    banner "Preparing artifacts from previous stages"
    echo -e "${H2}Finding projects for release ${target}${Color_Off}"
    declare -a directories
    directories+=(kerbside)

    projects=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
    for project in ${projects}; do
        echo -e "${H3}Considering ${project}${Color_Off}"
        release=$(yq -r .release ${project}/config.yaml)
        directory=$(yq -r .directory ${project}/config.yaml)

        echo "${project} is release ${release}"
        if [ "${release}" == "${target}" ]; then
            num_patches=$(cat ${project}/ORDER | egrep -c "^" || true)
            echo "...there are ${num_patches} queued patches"
            if [ ${num_patches} -lt 1 ]; then
                echo "...but there are are no active patches"
            elif [ -e ${topsrcdir}/${directory} ]; then
                echo "...will be included, but archive already extracted"
            else
                echo "...will be included, extracting archive"
                tar xzf ${topsrcdir}/${directory}.tgz -C ${topsrcdir}/
                directories+=(${project})
            fi
        fi
    done

    banner "Building docker images for ${target}"

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

    # Install other required dependencies
    ${venvdir}/bin/pip install setuptools

    # Install kolla, docker and oslo
    if [ ! -f ${venvdir}/bin/kolla-build ]; then
        if [ $( echo "${directories[@]}" | grep -c "oslo.config" || true) -gt 0 ]; then
            # We need to override the version of oslo.config so that it doesn't
            # get clobbered by the Kolla install.
            echo -e "${H2}Overriding the default oslo.config with patched version${Color_Off}"
            export PBR_VERSION=10.0.0
            ${venvdir}/bin/pip install "${topsrcdir}/oslo.config"
            unset PBR_VERSION
        fi

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
    cat etc/kolla-build-${target}.conf.in | \
        sed -e "s|TOPSRCDIR|${topsrcdir}|g" -e "s|DISTRO|${distro}|g" > ${topdir}/archive/kolla-build.conf

    if [ $(wc -c ${topdir}/archive/kolla-build.conf | cut -f 1 -d " ") -lt 1 ]; then
        echo
        echo "Empty kolla-build.conf!"
        exit 1
    fi

    # Clear build cache
    echo -e "${H2}Clear build cache${Color_Off}"
    docker buildx prune -f

    # Build images
    echo
    echo -e "${H2}Build images${Color_Off}"
    cd ${topsrcdir}

    kolla_build_args=${build_images}
    if [ "${build_images}" == "all" ]; then
        kolla_build_args="^(?!skyline)(.*)"
    fi

    echo -e "${H3}${venvdir}/bin/kolla-build \\"
    echo -e "    --config-file \"${topdir}/archive/kolla-build.conf\" \\"
    echo -e "    --tag ${target}-${distro}-${CI_COMMIT_SHORT_SHA} \\"
    echo -e "    --namespace kolla ${kolla_build_args} 2>&1 | \\"
    echo -e "    tee --append ${topdir}/archive/build.log | \\"
    echo -e "    ts \"%b %d %H:%M:%S ${target}\""
    echo -e "${Color_Off}"

    ${venvdir}/bin/kolla-build \
        --config-file "${topdir}/archive/kolla-build.conf" \
        --tag ${target}-${distro}-${CI_COMMIT_SHORT_SHA} \
        --namespace kolla ${kolla_build_args} 2>&1 | \
        tee --append ${topdir}/archive/build.log | \
        ts "%b %d %H:%M:%S ${target}"

    echo
    echo -e "${H3}Exit code: ${?}"

    # Did we see any messages indicating failure?
    failed=0
    if [ $(grep -c "Failed with status" ${topdir}/archive/build.log || true) -gt 0 ]; then\
        echo "Image build failed..."
        grep "Failed with status" ${topdir}/archive/build.log
        echo
        failed = 1
    fi

    for img in $(tail -1 ${topdir}/archive/build.log | jq -r ".failed | .[] | .name"); do
        echo "${img} failed"
        failed = 1
    done
    if [ ${failed} == 1 ]; then
        echo
    fi

    for img in $(tail -1 ${topdir}/archive/build.log | jq -r ".unbuildable | .[] | .name"); do
        echo "${img} unbuildable"
        failed = 1
    done
    if [ ${failed} == 1 ]; then
        echo
    fi


    if [ ${failed} == 1 ]; then
        echo "kolla-build failed!"
        exit 1
    fi

    # Extract the list of build images and save it for later
    tail -1 ${topdir}/archive/build.log | jq -r ".built | .[] | .name" >> ${topdir}/archive/images

    cd ${topdir}
done

trap - EXIT

banner "All docker images built correctly."
