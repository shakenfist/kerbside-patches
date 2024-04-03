# Kerbside upstream patches

In order to provide native SPICE console functionality in OpenStack, a series
of patches against OpenStack are required. This repository maintains those
patches.

The majority of the patches are against Nova, although there are a fair few
against Kolla and Kolla-Ansible as my preferred deployment system too. Any
other OpenStack deployment system wishing to include Kerbside would need to make
similar modifications to their code.

The remainder of the patches are ancillary changes -- support for new Nova API
microversions in clients, things which helped me debug along the way, and that
sort of thing.

# Not for production use

These patches were developed while building the Kerbside proof of concept.

Because these patches add a Nova API microversion which is not seen upstream
they are not suitable for production use. Specifically, as upstream Nova adds
API microversions themselves, they consume the version number used by these
patches. This results in an unsafe upgrade path. Until these patches (or
equivalent) are landed upstream, Kerbside should be considered unsafe for a
production deployment.

The following microversions are used for Nova releases:

* 2023.1: v2.96
* 2023.2: v2.96
* 2024.1: v2.97
* master: v2.97 (subject to change)

# Versions

The original proof of concept was developed against OpenStack 2023.1, and that
is therefore the best tested version of these patches. Forward porting of
patches to 2023.2 and 2024.1 has been done, but because Kolla / Kolla-Ansible
does not yet support 2024.1 they are not as well tested. Bug reports are
welcomed.

# Kolla container operating system

Because RHEL 9 dropped support for SPICE in KVM / qemu, and the downstream
redistributions such as Rocky Linux followed suit, the only tested container
operating system for these patches is Debian. While it is technically feasible
to add back SPICE into Rocky with custom packages, that work has not been
attempted. Additionally, Kolla-Ansible does not support running a mix of
container operating systems for your deployment. Therefore, you need to use
Debian for all container images in a deployment using Kerbside, even though
only the Nova / LibVirt containers are customized by these patches.

# Container image build

Given I am using Kolla-Ansible for testing, this respository also contains
scripts to build container images suitable for use with Kolla-Ansible. Those
images are build like this (assuming you're using Debian 11 like I am):

```
# Basic build configuration
sudo apt-get update
sudo apt-get dist-upgrade -y

sudo apt-get install -y moreutils python3-venv pkg-config \
    libmariadb-dev-compat build-essential python3-dev python3-lxml \
    libxml2-dev libxslt1-dev jq git
sudo pip3 uninstall virtualenv
sudo apt purge -y python3-virtualenv
sudo pip3 install virtualenv tox yq

# Clone the Kerbside patches respository
git clone https://github.com/shakenfist/kerbside-patches
cd kerbside-patches

# Make a place to store the source for kerbside and patched OpenStack components
mkdir src
cd src

# Clone the kerbside source, needed for container image build later
git clone https://github.com/shakenfist/kerbside
tar cvf kerbside.tgz kerbside
cd ..

(to be completed)
```

This process is automated for gitlab users using the included `.gitlab-ci.yml`
configuration file, and for github users with the included github workflows
under `.github/workflow`.