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
    complete_image_tag="${target}-${image_tag}"
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
                --gitlab_url ${ci_gitlab} --username ${registry_username} --token ${registry_token} \
                find ${complete_image_tag} || true)
        else
            images=$(/srv/kerbside/venv-tools/bin/python3 ${topdir}/tools/find_images \
                --gitlab_url ${ci_gitlab} find ${complete_image_tag} || true)
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

                    echo -e "    ${image}:${complete_image_tag} ${Arrow} ${image}:${target}-debian-bookworm"
                    docker image tag ${ci_registry}/${image}:${complete_image_tag} \
                        ${image}:${target}-debian-bookworm
                done
                have_images="true"
            fi
        fi
    fi

    if [ ${have_images} == "false" ]; then
        echo "No existing images found. Building them."

        mkdir -p ${topdir}/archive/
        ./_build/imagebuild.sh --build-targets "${target}" --build-images "${build_images}"

        echo
        echo -e "${H2}Built images${Color_Off}"
        docker image list

        if [ ${use_ci_registry} == "true" ]; then
            echo
            echo -e "${H2}Pushing to the CI registry${Color_Off}"
            for image in $(docker image list --format json | \
                jq --slurp -r ".[] | select(.Tag == \"${target}-${CI_COMMIT_SHORT_SHA}\") | .Repository"); do
                echo -e "    ${image}:${target}-${CI_COMMIT_SHORT_SHA} ${Arrow} ${ci_registry}/${registry_project}/${image}:${target}-debian-bookworm"
                docker image tag ${image}:${target}-${CI_COMMIT_SHORT_SHA} \
                    ${ci_registry}/${registry_project}/${image}:${target}-debian-bookworm
                docker image push \
                    ${ci_registry}/${registry_project}/${image}:${target}-debian-bookworm

                echo -e "    ${image}:${target}-${CI_COMMIT_SHORT_SHA} ${Arrow} ${ci_registry}/${registry_project}/${image}:${complete_image_tag}"
                docker image tag ${image}:${target}-${CI_COMMIT_SHORT_SHA} \
                    ${ci_registry}/${registry_project}/${image}:${complete_image_tag}
                docker image push ${ci_registry}/${registry_project}/${image}:${complete_image_tag}
            done
        fi
    fi
done

echo
trap - EXIT

banner "Container image build complete."