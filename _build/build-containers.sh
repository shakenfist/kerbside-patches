# Run from the top directory.
. _build/common.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Building container images${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

for target in ${build_targets}; do
    debian_codename="bookworm"
    image_tag="${target}-${CI_COMMIT_SHORT_SHA}-debian-${debian_codename}"

    echo
    echo -e "${H1}==================================================${Color_Off}"
    echo -e "${H1}Build configuration${Color_Off}"
    echo -e "${H1}    Target: ${target}${Color_Off}"
    echo -e "${H1}    Images: ${build_images}${Color_Off}"
    echo -e "${H1}    CI SHA: ${CI_COMMIT_SHORT_SHA}${Color_Off}"
    echo -e "${H1}    Image tag: ${image_tag}${Color_Off}"
    echo -e "${H1}==================================================${Color_Off}"

    have_images="false"
    if [ ${use_ci_registry} == "true" ]; then
        echo -e "${H2}Check if we already have built images${Color_Off}"
        images=$(/srv/kerbside/venv-tools/bin/python3 ${topdir}/tools/find_images \
            --registry http://192.168.1.5:4000 find ${image_tag} || true)

        if [ $(echo ${images} | grep -c kolla || true) -gt 0 ]; then
            echo "Found existing images. Pulling them."

            for image in $(echo ${images}); do
                echo -e "    ${image}..."
                docker pull 192.168.1.5:4000/${image}:${image_tag}

                echo -e "    ${image}:${image_tag} ${Arrow} ${image}:${target}-debian-${debian_codename}"
                docker image tag 192.168.1.5:4000/${image}:${image_tag} \
                    ${image}:${target}-debian-${debian_codename}
            done
            have_images="true"
        fi
    fi

    if [ ${have_images} == "false" ]; then
        echo "No existing images found. Building them."

        mkdir -p ${topdir}/archive/
        ./imagebuild.sh --build-targets "${target}" --build-images "${build_images}"

        echo
        echo -e "${H2}Built images${Color_Off}"
        docker image list

        echo
        echo -e "${H2}Pushing to the CI registry${Color_Off}"
        for image in $(docker image list --format json | \
            jq --slurp -r ".[] | select(.Tag == \"${target}-${CI_COMMIT_SHORT_SHA}\") | .Repository"); do
            echo -e "    ${image}:${target}-${CI_COMMIT_SHORT_SHA} ${Arrow} 192.168.1.5:4000/openstack.${image}:${target}-debian-${debian_codename}"
            docker image tag ${image}:${target}-${CI_COMMIT_SHORT_SHA} \
                192.168.1.5:4000/openstack${image}:${target}-debian-${debian_codename}
            docker image push \
                192.168.1.5:4000/openstack${image}:${target}-debian-${debian_codename}

            echo -e "    ${image}:${target}-${CI_COMMIT_SHORT_SHA} ${Arrow} 192.168.1.5:4000/openstack.${image}:${image_tag}"
            docker image tag ${image}:${target}-${CI_COMMIT_SHORT_SHA} \
                192.168.1.5:4000/openstack${image}:${image_tag}
            docker image push 192.168.1.5:4000/openstack${image}:${image_tag}
        done
    fi
done

echo
trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Container image build complete.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"