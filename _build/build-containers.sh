#!/bin/bash -e

# Run from the top directory.
. _build/common.sh

banner "Building container images"

if [ ! -z ${registry_username} ]; then
    echo
    echo -e "${H1}==================================================${Color_Off}"
    echo -e "${H1}Registry configuration${Color_Off}"
    echo -e "${H1}    CI gitlab: ${ci_gitlab}"
    echo -e "${H1}    Use CI registry: ${use_ci_registry}"
    echo -e "${H1}    CI registry: ${ci_registry}"
    echo -e "${H1}    Registry username: ${registry_username}"
    echo -e "${H1}    Registry token: ${registry_token}"
    echo -e "${H1}==================================================${Color_Off}"

    echo ${registry_token} | docker login \
        ${ci_registry} --username ${registry_username} --password-stdin
fi

##############################################################################
# Proxy helper functions                                                     #
##############################################################################

PROXY_PID=""

start_occystrap_proxy() {
    local layers_dir="${1}"
    local cache_file="${2}"

    echo -e "${H2}Starting occystrap proxy${Color_Off}"
    echo -e "${H3}Downstream: ${ci_registry}${Color_Off}"
    echo -e "${H3}Layer cache: ${cache_file}${Color_Off}"

    http_proxy='' HTTP_PROXY='' https_proxy='' HTTPS_PROXY='' \
    all_proxy='' ALL_PROXY='' \
    /tmp/occystrap/bin/occystrap \
        --compression zstd \
        --username "${registry_username}" \
        --password "${registry_token}" \
        --layer-cache "${cache_file}" \
        proxy \
        --downstream "${ci_registry}" \
        --concurrency 4 \
        -f normalize-timestamps \
        -f "exclude:pattern=**/.git" \
        &
    PROXY_PID=$!

    # Wait for proxy to be ready
    local max_wait=30
    local waited=0
    while [ ${waited} -lt ${max_wait} ]; do
        if http_proxy='' HTTP_PROXY='' https_proxy='' \
            HTTPS_PROXY='' all_proxy='' ALL_PROXY='' \
            curl -sf http://127.0.0.1:5050/v2/ > /dev/null 2>&1
        then
            echo -e "${H3}Proxy is ready (PID ${PROXY_PID})${Color_Off}"
            return 0
        fi
        # Check if proxy process died
        if ! kill -0 ${PROXY_PID} 2>/dev/null; then
            echo "Proxy process died during startup"
            PROXY_PID=""
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    echo "Proxy failed to start within ${max_wait}s"
    kill ${PROXY_PID} 2>/dev/null || true
    PROXY_PID=""
    return 1
}

stop_occystrap_proxy() {
    if [ -n "${PROXY_PID}" ] \
        && kill -0 ${PROXY_PID} 2>/dev/null
    then
        echo
        echo -e "${H2}Stopping occystrap proxy (PID ${PROXY_PID})${Color_Off}"
        kill -TERM ${PROXY_PID}
        wait ${PROXY_PID} || true
        echo -e "${H3}Proxy stopped.${Color_Off}"
    fi
    PROXY_PID=""
}

##############################################################################
# Install occystrap and start proxy if requested                             #
##############################################################################

proxy_running="false"
if [ "${use_proxy}" == "true" ] \
    && [ "${use_ci_registry}" == "true" ]
then
    # Docker's containerd-snapshotter integration has its own push
    # resolver that ignores insecure-registries, containerd hosts.toml,
    # and CRI config_path. Disable the snapshotter so Docker's native
    # push path is used, which correctly honors insecure-registries.
    echo
    echo -e "${H2}Configure Docker for local proxy${Color_Off}"
    echo '{"insecure-registries": ["127.0.0.1:5050"], "features": {"containerd-snapshotter": false}}' | \
        sudo tee /etc/docker/daemon.json > /dev/null
    echo "daemon.json:"
    cat /etc/docker/daemon.json
    sudo systemctl restart docker
    echo -e "${H3}Docker restarted without containerd-snapshotter${Color_Off}"

    # Verify Docker loaded the insecure-registries config
    echo
    echo -e "${H3}Docker info (post-restart)${Color_Off}"
    docker info 2>&1 | grep -iA5 "Insecure\|Storage\|Snapshotter\|Server Version"

    echo
    echo -e "${H2}Install occystrap for proxy${Color_Off}"
    python3 -mvenv /tmp/occystrap
    /tmp/occystrap/bin/pip3 install occystrap

    layers_dir="${topdir}/archive/layers"
    mkdir -p "${layers_dir}"
    rm -f "${layers_dir}"/*.jsonl

    if start_occystrap_proxy \
            "${layers_dir}" \
            "${topdir}/archive/occystrap-layer-cache.json"
    then
        proxy_running="true"
        # Ensure proxy is stopped on exit
        original_trap=$(trap -p EXIT)
        trap 'stop_occystrap_proxy; on_exit' EXIT
    else
        echo -e "${Red}WARNING: Proxy failed to start."
        echo -e "Falling back to sequential push.${Color_Off}"
    fi
fi

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Contents of src directory${Color_Off}"
ls -lrth ${topsrcdir}
echo -e "${H1}==================================================${Color_Off}"

for target in ${build_targets}; do
    complete_image_tag="${target}-${distro}-${distro_version}-${image_tag}"

    echo
    echo -e "${H1}==================================================${Color_Off}"
    echo -e "${H1}Build configuration${Color_Off}"
    echo -e "${H1}    Target: ${target}${Color_Off}"
    echo -e "${H1}    Images: ${build_images}${Color_Off}"
    echo -e "${H1}    CI SHA: ${CI_COMMIT_SHORT_SHA}${Color_Off}"
    echo -e "${H1}    Image tag: ${complete_image_tag}${Color_Off}"
    echo -e "${H1}==================================================${Color_Off}"

    have_images="false"
    if [ ${use_ci_registry} == "true" ]; then
        echo -e "${H2}Check if we already have built images${Color_Off}"

        if [ ! -z ${registry_username} ]; then
            images=$(/srv/kerbside/venv-tools/bin/python3 ${topdir}/tools/find_images \
                --gitlab-url ${ci_gitlab} --username ${registry_username} --token ${registry_token} \
                find ${complete_image_tag} || true)
        else
            images=$(/srv/kerbside/venv-tools/bin/python3 ${topdir}/tools/find_images \
                --gitlab-url ${ci_gitlab} find ${complete_image_tag} || true)
        fi

        if [ $(echo ${images} | grep -c kolla || true) -gt 0 ]; then
            if [ ${dont_fetch_images} == "true" ]; then
                echo "Found existing images, but configured not to pull them."
                have_images="true"
            else
                echo "Found existing images. Pulling them."

                for image in $(echo ${images}); do
                    echo -e "    ${image}..."
                    docker pull ${ci_registry}/${image}:${complete_image_tag}

                    echo -e "    ${image}:${complete_image_tag} ${Arrow} ${image}:${complete_image_tag}"
                    docker image tag ${ci_registry}/${image}:${complete_image_tag} \
                        ${image}:${complete_image_tag}
                done
                have_images="true"
            fi
        fi
    fi

    if [ ${have_images} == "false" ]; then
        echo "No existing images found. Building them."

        mkdir -p ${topdir}/archive/
        rm -f ${topdir}/archive/images

        proxy_args=""
        if [ "${proxy_running}" == "true" ]; then
            proxy_args="--use-proxy"
        fi

        ./_build/imagebuild.sh --build-targets "${target}" \
                --build-images "${build_images}" \
                --distro "${distro}" \
                --distro-version "${distro_version}" \
                --image-tag "${complete_image_tag}" \
                ${proxy_args} || true

        # Guard against errors in base distro version selection
        if [ $(grep -c "${distro_version}" ${topdir}/archive/build.log || true) -eq 0 ]; then
            banner "No references to the correct distro version in build log. We likely did not use the correct base image version!"
            exit 1
        fi

        if [ $(grep -c "kolla-build failed!" ${topdir}/archive/build.log || true) -gt 0 ]; then
            echo
            echo
            echo -e "${H2}Retry build once.${Color_Off}"
            ./_build/imagebuild.sh --build-targets "${target}" \
                --build-images "${build_images}" \
                --distro "${distro}" \
                --distro-version "${distro_version}" \
                --image-tag "${complete_image_tag}" \
                ${proxy_args}
        fi

        cat ${topdir}/archive/images | sort | uniq > ${topdir}/archive/images.uniq
        mv ${topdir}/archive/images.uniq ${topdir}/archive/images

        echo
        echo -e "${H2}Built images${Color_Off}"
        docker image list

        # When using the proxy, kolla-build already pushed images
        # during the build. When not using the proxy, push images
        # sequentially via occystrap process (fallback path).
        if [ "${proxy_running}" != "true" ] \
            && [ "${use_ci_registry}" == "true" ]
        then
            echo
            echo -e "${H2}Install occystrap for registry push${Color_Off}"
            python3 -mvenv /tmp/occystrap
            /tmp/occystrap/bin/pip3 install occystrap

            echo
            echo -e "${H2}Pushing to the CI registry${Color_Off}"

            # Layer metadata is collected at each pipeline stage
            # via inspect filters. Per-image files allow comparing
            # the effect of each filter on individual images.
            layers_dir="${topdir}/archive/layers"
            mkdir -p "${layers_dir}"
            rm -f "${layers_dir}"/*.jsonl

            for image in $(docker image list --format json | \
                    jq --slurp -r ".[] | select(.Tag == \"${complete_image_tag}\") | .Repository"); do
                registry_image=$(echo ${image} | sed 's/^kolla\///')
                safe_name=$(echo ${image} | tr '/' '-')

                echo -e "    ${image}:${complete_image_tag} ${Arrow} occystrap ${Arrow} ${ci_registry}/${registry_project}/${image}:${complete_image_tag}"
                http_proxy='' HTTP_PROXY='' https_proxy='' HTTPS_PROXY='' all_proxy='' ALL_PROXY='' /tmp/occystrap/bin/occystrap \
                    --parallel 8 \
                    --compression zstd \
                    --username ${registry_username} \
                    --password ${registry_token} \
                    --layer-cache "${topdir}/archive/occystrap-layer-cache.json" \
                    process \
                    "dockerpush://${image}:${complete_image_tag}" \
                    "registry://${ci_registry}/${registry_project}/${registry_image}:${complete_image_tag}" \
                    -f "inspect:file=${layers_dir}/${safe_name}-as-built.jsonl" \
                    -f normalize-timestamps \
                    -f "inspect:file=${layers_dir}/${safe_name}-post-normalize.jsonl" \
                    -f "exclude:pattern=**/.git" \
                    -f "inspect:file=${layers_dir}/${safe_name}-post-exclude.jsonl"
            done

            # Package layer data for artifact collection
            echo
            echo -e "${H2}Packaging layer data${Color_Off}"
            tar czf "${topdir}/archive/layers.tar.gz" \
                -C "${topdir}/archive" layers/
        fi
    fi
done

# Stop the proxy after all targets are built and package layer data
if [ "${proxy_running}" == "true" ]; then
    stop_occystrap_proxy

    layers_dir="${topdir}/archive/layers"
    if [ -d "${layers_dir}" ]; then
        echo
        echo -e "${H2}Packaging layer data${Color_Off}"
        tar czf "${topdir}/archive/layers.tar.gz" \
            -C "${topdir}/archive" layers/
    fi
fi

# Run debsecan vulnerability scan on the built images. This extracts the
# dpkg database from each image and scans it externally, so the pushed
# images are never modified.
if [ "${skip_debsecan}" != "true" ]; then
    debsecan_args=""
    if [ "${debsecan_fail_on_fixable}" == "true" ]; then
        debsecan_args="--debsecan-fail-on-fixable"
    fi

    # When --debsecan-fail-on-fixable is set, let the exit code propagate
    # so the build fails. Otherwise, treat scan errors as non-fatal.
    if [ "${debsecan_fail_on_fixable}" == "true" ]; then
        ./_build/debsecan-report.sh \
            --build-targets "${build_targets}" \
            --distro "${distro}" \
            --distro-version "${distro_version}" \
            --image-tag "${image_tag}" \
            ${debsecan_args}
    else
        ./_build/debsecan-report.sh \
            --build-targets "${build_targets}" \
            --distro "${distro}" \
            --distro-version "${distro_version}" \
            --image-tag "${image_tag}" \
            ${debsecan_args} || true
    fi
fi

echo
trap - EXIT

image_count=$(docker image ls | wc -l)
image_count=$(( ${image_count} - 1 ))
banner "Container image build complete (${image_count} images built)."