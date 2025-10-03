#!/bin/bash -e

# Run from the top directory.
. _build/common.sh

banner "Archiving artifacts from previous stages"

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

        echo -e "${H3}       Individual image extraction${Color_Off}"
        rm -f "${image}-${target}-${CI_COMMIT_SHORT_SHA}.tar"
        docker save "kolla/${image}:${target}-${CI_COMMIT_SHORT_SHA}" > \
            "${image}-${target}-${CI_COMMIT_SHORT_SHA}.tar"

        echo -e "${H3}       SBOM generation${Color_Off}"
        docker run --rm --env SYFT_CHECK_FOR_APP_UPDATE=false \
            --volume $(pwd):/tmp/imgs \
            anchore/syft:latest \
            "docker-archive:/tmp/imgs/${image}-${target}-${CI_COMMIT_SHORT_SHA}.tar" \
            -o syft-json="/tmp/imgs/${image}-${target}-${CI_COMMIT_SHORT_SHA}.syft.json" \
            -o syft-table="/tmp/imgs/${image}-${target}-${CI_COMMIT_SHORT_SHA}.syft.txt" \
            -o cyclonedx-json="/tmp/imgs/${image}-${target}-${CI_COMMIT_SHORT_SHA}.cyclonedx.json" \
            -o spdx-json="/tmp/imgs/${image}-${target}-${CI_COMMIT_SHORT_SHA}.spdx.json"

        echo -e "${H3}       Cleanup${Color_Off}"
        rm -f "${image}-${target}-${CI_COMMIT_SHORT_SHA}.tar"
        tar rf "${target}-${CI_COMMIT_SHORT_SHA}-sbom.tar" \
            ${image}-${target}-${CI_COMMIT_SHORT_SHA}.*
        rm -f ${image}-${target}-${CI_COMMIT_SHORT_SHA}.*

        images+=("kolla/${image}:${target}-${CI_COMMIT_SHORT_SHA}")
    done

    echo
    echo -e "${H3}Saving images to a single tarball${Color_Off}"
    echo "${images[@]}"
    docker save ${images[@]} > "${target}-${CI_COMMIT_SHORT_SHA}.tar"
done

gzip "${target}-${CI_COMMIT_SHORT_SHA}.tar"
gzip "${target}-${CI_COMMIT_SHORT_SHA}-sbom.tar"

trap - EXIT

banner "All artifacts exported correctly for ${CI_COMMIT_SHORT_SHA}."