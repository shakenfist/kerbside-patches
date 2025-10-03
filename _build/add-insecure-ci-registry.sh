#!/bin/bash -e

# Run from the top directory.
. _build/common.sh

banner "Setup HTTP CI registry"

sudo mkdir -p /etc/docker/
cat - <<EOF | sudo tee /etc/docker/daemon.json
{
    "insecure-registries" : [ "192.168.1.5:4000" ]
}
EOF
sudo systemctl restart docker

trap - EXIT

banner "HTTP CI registry setup"