#!/bin/bash -e

# Ensure we fail even when piping output to ts
set -o pipefail

# Note that our CI environment requires these packages to be installed.
#     From the OS: git moreutils python3-venv
#     From pypi: tox

topdir=$(pwd)
topsrcdir="${topdir}/src"

# Color helpers, from https://stackoverflow.com/questions/5947742/
Color_Off='\033[0m'       # Text Reset
Red='\033[0;31m'          # Red
Green='\033[0;32m'        # Green
Yellow='\033[0;33m'       # Yellow
Blue='\033[0;34m'         # Blue
Purple='\033[0;35m'       # Purple
Cyan='\033[0;36m'         # Cyan
White='\033[0;37m'        # White

# And an arrow!
Arrow='\u2192'

H1="${Green}"
H2="${Blue}"
H3="${Arrow}${Purple}"

function on_exit {
    echo
    echo -e "${Red}*** Failed ***${No_Color}"
    echo
    }
trap 'on_exit $?' EXIT

. buildconfig.sh

rm -rf archive
mkdir -p archive

# Kolla-Ansible does not yet support 2024.1
./imagebuild.sh
./imagearchive.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Shared archival steps${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

echo -e "${H2}Prune local docker image cache${Color_Off}"
docker image prune -f

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