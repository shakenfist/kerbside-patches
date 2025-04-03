# Run from the top directory.
. _build/common.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Setup HTTP CI registry${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

sudo mkdir -p /etc/docker/
cat - <<EOF | sudo tee /etc/docker/daemon.json
{
    "insecure-registries" : [ "192.168.1.5:4000" ]
}
EOF
sudo systemctl restart docker

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}HTTP CI registry setup${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"