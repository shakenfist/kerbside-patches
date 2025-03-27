sudo apt-get update
sudo apt-get dist-upgrade -y

sudo apt-get install -y moreutils python3-venv pkg-config \
    libmariadb-dev-compat build-essential python3-dev python3-lxml \
    libxml2-dev libxslt1-dev jq ca-certificates curl git libpq-dev
sudo apt purge -y python3-virtualenv

sudo mkdir /srv/shakenfist
sudo chown $(whoami):$(whoami) /srv/shakenfist

python3 -mvenv /srv/shakenfist/kerbside-patches-tools
/srv/shakenfist/kerbside-patches-tools/bin/pip3 install virtualenv tox yq occystrap

# These build scripts require a more recent version of Docker than that packaged
# by Debian, so we use the Docker repositories instead.
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
    sudo apt-get remove -y ${pkg} || true
done
sudo apt-get -y autoremove

if [ ! -e /etc/apt/keyrings/docker.asc ]; then
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update
fi

sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin

# Allow the current user to access docker
sudo usermod -a -G docker $(whoami)