# Run from the top directory.
. _build/common.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Archiving artifacts from previous stages${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

# Save images
echo
echo -e "${H3}Saving images to archive/imgs${Color_Off}"
cd "${topdir}"
mkdir -p "archive/imgs"
cd "archive/imgs"

for target in ${build_targets}; do
    # images=$(docker image list --format json | \
    #     jq --slurp -r ".[] | select(.Tag == \"${target}-${CI_COMMIT_SHORT_SHA}\") | .Repository")

    images=()
    for image in $( cat ${topdir}/archive/images ); do
        image=$(echo $image | sed 's/kolla\///')

        echo -e "${H3}...${image}${Color_Off}"
        rm -f "${image}-${target}-${CI_COMMIT_SHA}.tar"
        rm -f "${image}-${target}-${CI_COMMIT_SHA}.tar"

        images+=("kolla/${image}:${target}-${CI_COMMIT_SHORT_SHA}")
    done

    echo
    echo "${images[@]}"
    docker save ${images[@]} > "${target}-${CI_COMMIT_SHORT_SHA}.tar"
done

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}All artifacts exported correctly for ${CI_COMMIT_SHORT_SHA}.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"
