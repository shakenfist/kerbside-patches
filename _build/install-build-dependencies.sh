#!/bin/bash -e

. /etc/os-release
echo "Claimed OS name: ${NAME}"

echo
echo -e "${H2}Early bootstrapping${Color_Off}"
if [ "${NAME}" == "Rocky Linux" ]; then
    sudo dnf update -y
    sudo dnf install -y epel-release
    sudo dnf config-manager --set-enabled crb
    echo

    sudo dnf install -y git
else
    sudo apt-get update
    sudo apt-get dist-upgrade -y
    echo

    sudo apt-get install -y git
fi

# Run from the top directory.
. _build/common.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Installing build dependencies${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

echo
if [ "${NAME}" == "Rocky Linux" ]; then
    echo -e "${H2}Additional packages${Color_Off}"
    sudo dnf install -y moreutils pkg-config python3-lxml libxml2-devel \
        libxslt jq gcc python3-devel dbus-devel glib2-devel dbus-python-devel \
        netcat
    sudo dnf remove python3-virtualenv
    sudo pip3 install tox yq occystrap virtualenv MarkupSafe==2.1.5 clingwrap
    echo

    echo -e "${H2}Install a recent Docker${Color_Off}"
    sudo dnf config-manager --add-repo \
        https://download.docker.com/linux/centos/docker-ce.repo
    sudo dnf install -y docker-ce docker-ce-cli containerd.io
    sudo systemctl start docker
    echo
else
    echo -e "${H2}Additional packages${Color_Off}"
    sudo apt-get install -y moreutils pkg-config python3-lxml libxml2-dev \
        libxslt1-dev jq gcc python3-dev libdbus-1-dev libglib2.0-dev \
        python3-dbus python3-venv netcat-openbsd python3-dev \
        build-essential libpcre3-dev
    sudo apt-get remove -y python3-virtualenv
    sudo pip3 install --break-system-packages tox yq occystrap virtualenv \
        MarkupSafe==2.1.5 clingwrap
    echo

    echo -e "${H2}Install a recent Docker${Color_Off}"
    sudo apt-get update
    sudo apt-get install ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
        -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
        https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update

    sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    sudo systemctl start docker
    echo
fi

# Allow the current user to access docker
echo -e "${H2}Grant access to docker${Color_Off}"
sudo usermod -a -G docker $(whoami)
echo

# Setup a tools venv
echo -e "${H2}Setup a tools venv${Color_Off}"
sudo mkdir -p /srv/kerbside
sudo chown -R $(whoami):$(whoami) /srv/kerbside
python3 -mvenv /srv/kerbside/venv-tools
/srv/kerbside/venv-tools/bin/pip3 install click requests

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Build dependencies installed.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"