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

for target in ${build_targets}; do
    if [ ${distro} == "debian" ]; then
        # NOTE(mikal): the trixie changes have not yet merged upstream
        # if [ ${target} == "master" ]; then
        #     distro_version="trixie"
        # else
        distro_version="bookworm"
        # fi
    elif [ ${distro} == "ubuntu" ]; then
        distro_version="noble"
    else
        echo "Unknown distro: ${distro}!"
        exit 1
    fi
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

        ./_build/imagebuild.sh --build-targets "${target}" \
                --build-images "${build_images}" \
                --image-tag "${complete_image_tag}" || true

        if [ $(grep -c "kolla-build failed!" ${topdir}/archive/build.log || true) -gt 0 ]; then
            echo
            echo
            echo -e "${H2}Retry build once.${Color_Off}"
            ./_build/imagebuild.sh --build-targets "${target}" \
                --build-images "${build_images}" \
                --image-tag "${complete_image_tag}"
        fi

        cat ${topdir}/archive/images | sort | uniq > ${topdir}/archive/images.uniq
        mv ${topdir}/archive/images.uniq ${topdir}/archive/images

        echo
        echo -e "${H2}Built images${Color_Off}"
        docker image list

        if [ ${use_ci_registry} == "true" ]; then
            echo
            echo -e "${H2}Install occystrap for registry push${Color_Off}"
            python3 -mvenv /tmp/occystrap
            /tmp/occystrap/bip/pip3 install occystrap

            echo
            echo -e "${H2}Pushing to the CI registry${Color_Off}"
            filters=""
            # -f normalize-timestamps
            # -f "exclude:pattern=**/.git"

            for image in $(docker image list --format json | \
                    jq --slurp -r ".[] | select(.Tag == \"${complete_image_tag}\") | .Repository"); do
                registry_image=$(echo ${image} | sed 's/^kolla\///')
                echo -e "    ${image}:${complete_image_tag} ${Arrow} occystrap${filters} ${Arrow} ${ci_registry}/${registry_project}/${image}:${complete_image_tag}"
                /tmp/occystrap/bip/occystrap \
                    docker://${image}:${complete_image_tag} \
                    registry://${ci_registry}/${registry_project}/${registry_image}:${complete_image_tag} \
                    ${filters}
            done
        fi
    fi
done

echo
trap - EXIT

banner "Container image build complete."