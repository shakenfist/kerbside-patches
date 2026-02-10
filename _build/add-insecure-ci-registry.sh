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
    echo -e "${Red}*** Failed ***${Color_Off}"
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
if [ -f /etc/docker/daemon.json ]; then
    sudo jq '. + {"insecure-registries": (."insecure-registries" // [] + ["192.168.1.12:4000"])}' \
        /etc/docker/daemon.json | sudo sponge /etc/docker/daemon.json
else
    echo '{"insecure-registries":["192.168.1.12:4000"]}' | \
        sudo tee /etc/docker/daemon.json > /dev/null
fi

sudo systemctl restart docker
sleep 10

trap - EXIT

banner "HTTP CI registry setup"