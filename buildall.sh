#!/bin/bash -e

# Note that our CI environment requires these packages to be installed.
#     From the OS: git moreutils python3-venv
#     From pypi: tox

topdir=$(pwd)
topsrcdir="${topdir}/src"

. buildconfig.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Build configuration${Color_Off}"
echo -e "${H1}    Targets: ${build_targets}${Color_Off}"
echo -e "${H1}    Images: ${build_images}${Color_Off}"
echo -e "${H1}    CI SHA: ${CI_COMMIT_SHORT_SHA}${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

rm -rf archive
mkdir -p archive

./imagebuild.sh --build-targets "${build_targets}" --build-images "${build_images}"
./imagearchive.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Shared archival steps${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

echo -e "${H2}Export patched source code to archive/src${Color_Off}"
cd "${topdir}"
mkdir -p "archive/src"

declare -a directories
directories+=(kerbside)

projects=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
for project in ${projects}; do
    echo -e "${H3}Considering ${project}${Color_Off}"
    release=$(yq -r .release ${project}/config.yaml)
    directory=$(yq -r .directory ${project}/config.yaml)

    echo "${project} is release ${release}"
    if [ "${release}" == "${target}" ]; then
        num_patches=$(cat ${project}/ORDER | wc -l)
        echo "...there are ${num_patches} queued patches"
        if [ ${num_patches} -lt 1 ]; then
            echo "...but there are are no active patches"
        else
            echo "...will be included"
            directories+=(${directory})
        fi
    fi
done

for directory in "${directories[@]}"; do
    if [ ! -e "archive/src/${directory}-${CI_COMMIT_SHORT_SHA}.tgz" ]; then
        echo -e "${H3}...${directory}-${CI_COMMIT_SHORT_SHA}.tgz${Color_Off}"
        cp ${topsrcdir}/${directory}.tgz "archive/src/${directory}-${CI_COMMIT_SHORT_SHA}.tgz"
    fi
done

trap - EXIT

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Archival complete.${Color_Off}"
echo -e "${H1}    Total archive size: "`du -sh archive`"${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"