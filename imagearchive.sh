#!/bin/bash -e

# Ensure we fail even when piping output to ts
set -o pipefail

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

echo -e "${H2}Export images${Color_Off}"
for target in ${build_targets}; do
    for image in nova-compute nova-api nova-libvirt kerbside; do
        echo -e "${H3}...${image}${Color_Off}"
        rm -f "${image}-${target}-${CI_COMMIT_SHA}.tar"
        rm -f "${image}-${target}-${CI_COMMIT_SHA}.tar"

        docker save kolla/${image}:${target}-${CI_COMMIT_SHORT_SHA} > "${image}-${target}-${CI_COMMIT_SHORT_SHA}.tar"
        gzip "${image}-${target}-${CI_COMMIT_SHORT_SHA}.tar"
        ls -lrth "${image}-${target}-${CI_COMMIT_SHORT_SHA}.tar.gz"
        echo
    done
done

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}All artifacts exported correctly for ${CI_COMMIT_SHORT_SHA}.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"
