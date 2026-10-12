#!/bin/bash -e

# Run from the top directory.
. _build/common.sh

banner "Building container images"

if [ ! -z ${registry_username} ]; then
    echo
    echo -e "${H1}==================================================${Color_Off}"
    echo -e "${H1}Registry configuration${Color_Off}"
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

    # Layer metadata is collected at each pipeline stage via inspect
    # filters. All images pushed through the proxy append to the same
    # combined per-stage files -- each JSONL line records which image
    # it describes, and tools/collect-layer-data.py separates them
    # per image later in the CI pipeline.
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
        -f "inspect:file=${layers_dir}/proxy-as-built.jsonl" \
        -f normalize-timestamps \
        -f "inspect:file=${layers_dir}/proxy-post-normalize.jsonl" \
        -f "exclude:pattern=**/.git" \
        -f "inspect:file=${layers_dir}/proxy-post-exclude.jsonl" \
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
# Registry helper functions                                                  #
##############################################################################

# Run tools/write-build-manifest with this build's registry settings. The
# token goes in the environment, not on the command line, and is never
# printed.
manifest_tool() {
    REGISTRY_TOKEN="${registry_token}" /srv/kerbside/venv-tools/bin/python3 \
        "${topdir}/tools/write-build-manifest" \
        --registry "${ci_registry}" \
        --project "${registry_project}" \
        --username "${registry_username}" \
        "$@"
}

# The image tag covers the source and the date, but not which images were
# asked for, so the build marker records a hash of the selection as well.
build_selection=$(printf '%s' "${build_images}" | sha256sum | cut -d ' ' -f 1)

# Push one local image to the CI registry with the same transforming filters
# as the sequential push below. The inspect filters are left out: they record
# layer data for kolla-build's images, which is already packaged by now.
push_image() {
    local local_image="${1}"
    local remote="${2}"

    if [ ! -x /tmp/occystrap/bin/occystrap ]; then
        echo -e "${H3}Install occystrap for registry push${Color_Off}"
        python3 -mvenv /tmp/occystrap
        /tmp/occystrap/bin/pip3 install occystrap
    fi

    echo -e "    ${local_image} ${Arrow} occystrap ${Arrow} ${remote}"
    http_proxy='' HTTP_PROXY='' https_proxy='' HTTPS_PROXY='' all_proxy='' ALL_PROXY='' /tmp/occystrap/bin/occystrap \
        --parallel 8 \
        --compression zstd \
        --username "${registry_username}" \
        --password "${registry_token}" \
        --layer-cache "${topdir}/archive/occystrap-layer-cache.json" \
        process \
        "dockerpush://${local_image}" \
        "registry://${remote}" \
        -f normalize-timestamps \
        -f "exclude:pattern=**/.git"
}

# Pull every image of a finished build, as recorded by its build marker, and
# tag each locally as ${registry_project}/<image>:<tag>.
pull_built_images() {
    local tag="${1}"
    local images
    local image
    local remote

    images=$(manifest_tool built-images "${tag}")
    for image in ${images}; do
        remote="${ci_registry}/${registry_project}/${image}:${tag}"
        echo -e "    ${image}..."
        docker pull "${remote}"

        echo -e "    ${remote} ${Arrow} ${registry_project}/${image}:${tag}"
        docker image tag "${remote}" "${registry_project}/${image}:${tag}"
    done
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

# The tags this run built, rather than found already built, and so must mark
# as built once everything else has succeeded.
built_tags=""

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
    if [ "${use_ci_registry}" == "true" ] && [ -z "${registry_token}" ]; then
        echo -e "${H2}No registry credentials, so cannot check for an earlier build of this tag${Color_Off}"
    elif [ "${use_ci_registry}" == "true" ]; then
        echo -e "${H2}Check if we already have built images${Color_Off}"

        # A tag counts as built only when its build marker (see "Build
        # marker" below) is in the registry for this image selection. A
        # registry that cannot be asked stops the build rather than being
        # taken for one with nothing in it: the push needs it too.
        if ! build_state=$(manifest_tool build-state --selection "${build_selection}" "${complete_image_tag}"); then
            echo "Could not ask ${ci_registry} whether ${complete_image_tag} is already built."
            exit 1
        fi

        case "${build_state}" in
            complete)
                have_images="true"
                if [ "${dont_fetch_images}" == "true" ]; then
                    echo "${complete_image_tag} is already built, but configured not to pull it."
                else
                    echo "${complete_image_tag} is already built. Pulling it."
                    pull_built_images "${complete_image_tag}"
                fi
                ;;
            stale)
                echo "${complete_image_tag} was built for a different image selection."
                ;;
            missing)
                echo "${complete_image_tag} has not been built before."
                ;;
            *)
                echo "Unexpected build state '${build_state}' for ${complete_image_tag}."
                exit 1
                ;;
        esac
    fi

    if [ ${have_images} == "false" ]; then
        echo "Building ${complete_image_tag}."

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

        # archive/images is reset for each target, so keep this target's list
        # for the manifest and marker steps after the loop.
        cp ${topdir}/archive/images ${topdir}/archive/images-${complete_image_tag}
        built_tags="${built_tags} ${complete_image_tag}"

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

##############################################################################
# Build manifest                                                             #
##############################################################################

# Phase 6 of docs/plans/containerised-deployer.md. Once every image of a
# build has landed in the registry, read each one's digest back into a
# manifest.json, and push that as a thin layer on top of the deployer under
# the deployer's own tag. This has to wait for the proxy to drain: the digest
# kolla-build saw is not the one the proxy's filters wrote to the registry.

manifest_label="kolla_build_manifest"

# Build and push the manifest image for one complete image tag. Do not call
# this from a condition (if, ||, &&): errexit is what makes a failure here
# fail the build.
write_build_manifest() {
    local tag="${1}"
    local deployer_repo="${ci_registry}/${registry_project}/kolla-ansible"
    local workdir="${topdir}/archive/build-manifest-${tag}"
    local local_image="kolla-build-manifest/kolla-ansible:${tag}"
    local images_file="${topdir}/archive/images-${tag}"
    local state

    echo
    echo -e "${H2}Build manifest for ${tag}${Color_Off}"

    # Prints "missing", "manifested", or the digest of kolla-ansible:<tag>
    # as it is now, before the manifest layer.
    state=$(manifest_tool deployer-state "${tag}")
    case "${state}" in
        missing)
            echo -e "${H3}kolla-ansible:${tag} is not in the registry, so this build has no deployer to carry a manifest. Skipping.${Color_Off}"
            return 0
            ;;
        manifested)
            echo -e "${H3}kolla-ansible:${tag} already carries a manifest. Nothing to do.${Color_Off}"
            return 0
            ;;
        sha256:*)
            echo -e "${H3}kolla-ansible:${tag} is ${state}, without a manifest${Color_Off}"
            ;;
        *)
            echo "Unexpected deployer state '${state}' for kolla-ansible:${tag}."
            exit 1
            ;;
    esac

    # The image list is kolla-build's record of what this run built. Without
    # it (a run that skipped the build) there is nothing to enumerate from.
    if [ ! -s "${images_file}" ]; then
        echo "Cannot build the manifest for ${tag}: this run built no images, so there is no image list"
        echo "(archive/images-${tag}), and kolla-ansible:${tag} carries no manifest."
        exit 1
    fi

    rm -rf "${workdir}"
    mkdir -p "${workdir}"
    rm -f "${topdir}/archive/manifest-${tag}.json"

    echo -e "${H3}Reading image digests from ${ci_registry}${Color_Off}"
    manifest_tool write "${tag}" "${workdir}/manifest.json" --images-file "${images_file}"
    cp "${workdir}/manifest.json" "${topdir}/archive/manifest-${tag}.json"

    cat > "${workdir}/Dockerfile" <<EOF
FROM ${deployer_repo}@${state}
COPY manifest.json /etc/kolla-ansible/manifest.json
LABEL ${manifest_label}="1"
EOF

    echo -e "${H3}Building ${local_image} from ${deployer_repo}@${state}${Color_Off}"
    docker build --provenance=false -t "${local_image}" "${workdir}"

    push_image "${local_image}" "${deployer_repo}:${tag}"

    # Keep the debsecan scan below, which lists local images by tag, off
    # this image: it is the deployer again, plus one JSON file.
    docker image rm "${local_image}"

    # Prove the label survived the push.
    state=$(manifest_tool deployer-state "${tag}")
    if [ "${state}" != "manifested" ]; then
        echo "kolla-ansible:${tag} has no ${manifest_label} label after the push (state '${state}')."
        exit 1
    fi
    echo -e "${H3}kolla-ansible:${tag} now carries the build manifest${Color_Off}"
}

if [ "${use_ci_registry}" != "true" ]; then
    echo
    echo -e "${H2}No CI registry in use, so no build manifest${Color_Off}"
elif [ -z "${registry_token}" ]; then
    echo
    echo -e "${H2}No registry credentials, so no build manifest${Color_Off}"
else
    for target in ${build_targets}; do
        write_build_manifest "${target}-${distro}-${distro_version}-${image_tag}"
    done
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

##############################################################################
# Build marker                                                               #
##############################################################################

# The last thing a build does, once every image has landed, the deployer
# carries its manifest and the scan above has passed, is say that it
# finished: it pushes kolla-build-complete:<tag>, a one file image (the
# digest manifest of everything built) labelled with the image names and a
# hash of the image selection. The check at the top of the build loop skips
# a tag only when that marker exists for the same selection, so a build that
# failed part way, or was asked for different images, is built again. The
# marker does not depend on the deployer image.

# Build and push the marker for one complete image tag. Do not call this from
# a condition (if, ||, &&): errexit is what makes a failure here fail the
# build.
write_build_marker() {
    local tag="${1}"
    local marker_repo="${ci_registry}/${registry_project}/kolla-build-complete"
    local workdir="${topdir}/archive/build-marker-${tag}"
    local local_image="kolla-build-marker/kolla-build-complete:${tag}"
    local images_file="${topdir}/archive/images-${tag}"
    local images
    local state

    echo
    echo -e "${H2}Build marker for ${tag}${Color_Off}"

    rm -rf "${workdir}"
    mkdir -p "${workdir}"

    # Fails, and so fails the build, if any image this run built is not in
    # the registry under the tag.
    echo -e "${H3}Checking every built image is in ${ci_registry}${Color_Off}"
    manifest_tool write "${tag}" "${workdir}/manifest.json" --images-file "${images_file}"
    images=$(sort -u "${images_file}" | paste -sd, -)

    cat > "${workdir}/Dockerfile" <<EOF
FROM scratch
COPY manifest.json /manifest.json
LABEL kolla_build_complete="1" \\
      kolla_build_selection="${build_selection}" \\
      kolla_build_images="${images}"
EOF

    docker build --provenance=false -t "${local_image}" "${workdir}"
    push_image "${local_image}" "${marker_repo}:${tag}"
    docker image rm "${local_image}"

    state=$(manifest_tool build-state --selection "${build_selection}" "${tag}")
    if [ "${state}" != "complete" ]; then
        echo "kolla-build-complete:${tag} does not mark this build after the push (state '${state}')."
        exit 1
    fi
    echo -e "${H3}${tag} is marked as built${Color_Off}"
}

if [ "${use_ci_registry}" == "true" ] && [ -n "${registry_token}" ]; then
    for tag in ${built_tags}; do
        write_build_marker "${tag}"
    done
fi

echo
trap - EXIT

image_count=$(docker image ls | wc -l)
image_count=$(( ${image_count} - 1 ))
banner "Container image build complete (${image_count} images built)."