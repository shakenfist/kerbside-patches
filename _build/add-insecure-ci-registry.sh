#!/bin/bash -e

# Some code from common.sh is copied here so that this script can be stand
# alone.

export Color_Off='\033[0m'       # Text Reset
export Red='\033[0;31m'          # Red
export Green='\033[0;32m'        # Green

export H1="${Green}"

# Make failures more obvious
function on_exit {
    echo
    echo -e "${Red}*** Failed ***${No_Color}"
    echo
    exit 1
    }
trap 'on_exit $?' EXIT

function banner {
    echo
    echo -e "${H1}**************************************************${Color_Off}"
    echo -e "${H1}${1}${Color_Off}"
    echo -e "${H1}**************************************************${Color_Off}"
    echo
}



banner "Setup HTTP CI registry"

sudo mkdir -p /etc/docker/
sudo rm -f /etc/docker/daemon.json
cat - <<EOF | sudo tee /etc/docker/daemon.json
{
    "insecure-registries" : [ "192.168.1.12:4000" ]
}
EOF
sudo systemctl restart docker

trap - EXIT

banner "HTTP CI registry setup"