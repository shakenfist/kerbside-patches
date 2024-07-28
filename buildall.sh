#!/bin/bash -e

# Note that our CI environment requires these packages to be installed.
#     From the OS: git moreutils python3-venv
#     From pypi: tox

topdir=$(pwd)
topsrcdir="${topdir}/src"

ARGS=$*
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

./imagebuild.sh
./imagearchive.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Shared archival steps${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

echo -e "${H2}Export patched source code to archive/src${Color_Off}"
cd "${topdir}"
mkdir -p "archive/src"

projects=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
declare -a directories
for project in kerbside ${projects}; do
    if [ ${project} == "kerbside" ]; then
        directory="kerbside"
    else
        directory=$(yq -r .directory ${project}/config.yaml)
    fi

    directories+=(${directory})
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