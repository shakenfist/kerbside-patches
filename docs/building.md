# Building patched container images

Given the project uses Kolla-Ansible for testing, this repository also
contains scripts to build container images suitable for use with
Kolla-Ansible.

This process is automated for gitlab users using the included
`.gitlab-ci.yml` configuration file, and for github users with the
included github workflows under `.github/workflow`.

## Debian host OS setup

On Debian, you can build patched container images by running
`./_build/setup-local-build-environment.sh`.

Note that you might need to logout / in to pick up the group change
associated with installing docker. Now continue to the shared steps
below.

## Rocky host OS setup

On Rocky, you can build patched container images like this:

```
# Basic build configuration
sudo dnf update -y
sudo dnf install -y epel-release
sudo dnf config-manager --set-enabled crb

sudo dnf install -y moreutils pkg-config python3-lxml libxml2-devel libxslt jq
sudo dnf remove python3-virtualenv
sudo pip3 install tox yq occystrap virtualenv

# Install a recent Docker. kernel-modules-extra provides the xt_addrtype
# netfilter module that dockerd needs to install its NAT rules.
sudo dnf install -y "kernel-modules-extra-$(uname -r)"
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install docker-ce docker-ce-cli containerd.io
sudo systemctl start docker

# Allow the current user to access docker
sudo usermod -a -G docker $(whoami)

# Note that you might need to logout / in to pick up the group change
```

Now continue to the shared steps below.

## Shared build steps

Now run these commands, regardless of host OS:

```
# Clone the Kerbside patches repository
git clone https://github.com/shakenfist/kerbside-patches
cd kerbside-patches

# Make a place to store the source for kerbside and patched OpenStack components
mkdir src
cd src

# Clone the kerbside source, needed for container image build later
git clone https://github.com/shakenfist/kerbside
tar cvf kerbside.tgz kerbside
cd ..

# Apply patches to upstream projects for your chosen release. Supported
# releases here at the moment are 2024.1, 2024.2, and master. However, the
# patches against 2024.1 and 2024.2 have been dropped, so you'll just be
# building a local version of the pure upstream containers with those.
_build/assemble-source.sh master

# At the end you should see this:
#
# ==================================================
# All patches applied correctly.
# ==================================================

# Now we can build images. Note that you can use --build-targets and
# --build-images to override the default behaviour. So for example this
# would build _all_ container images for 2024.1, but not for any other release:
#     ./buildall.sh --build-targets "2024.1" --build-images "all"

./buildall.sh --build-targets "2024.1"
```

At the end you should see output like this:

```
Export patched source code to archive/src
→...kerbside-e1632d4.tgz
→...kolla-ansible-e1632d4.tgz
→...nova-e1632d4.tgz
→...openstacksdk-e1632d4.tgz
→...oslo.config-e1632d4.tgz
→...python-novaclient-e1632d4.tgz
→...python-openstackclient-e1632d4.tgz
→...kolla-e1632d4.tgz

==================================================
Archival complete.
    Total archive size: 67G archive
==================================================
```

## To actually deploy Kolla-Ansible

In order to deploy Kolla-Ansible with our newly built images, we need to
import those images into our local docker registry so that Kolla-Ansible
can use them for a deploy. Note that the SHA variable below is set to
match the value at the end of the filename in the output above. Note
that my docker registry in this example is running on the same host I
did the build on, and is on port 4000. Now we can do a tag and push:

```
sha="e1632d4"
release="2024.1"
debian_codename="bookworm"
for name in $(docker image list | grep ${sha} | cut -f 1 -d " " | cut -f 2 -d "/"); do
   sudo docker image tag kolla/${name}:${release}-${sha} \
       127.0.0.1:4000/openstack.kolla/${name}:${release}-debian-${debian_codename}
   sudo docker image push \
       127.0.0.1:4000/openstack.kolla/${name}:${release}-debian-${debian_codename}
done
```

Now we need to have a patched version of Kolla-Ansible installed
somewhere so we can do the deployment. I do it this way, although you
might want to use a venv for this:

```
cd src/kolla-ansible
sudo python3 setup.py develop
```

You'll need a `globals.yml` in `/etc/kolla`, but that's site specific so
I don't want to include much detail here. Refer to the Kolla-Ansible
documentation for more details. Then I deploy like this:

```
sudo kolla-ansible -i /etc/kolla/inventory deploy
```

## Security vulnerability scanning

After container images are built, `debsecan` is run against each image
to report known Debian/Ubuntu security vulnerabilities. The scan
extracts the dpkg package database from each image and runs `debsecan`
externally, so the scanned images are never modified and `debsecan` is
not installed in the pushed container images.

The scan supports both Debian and Ubuntu base images. Non-Debian-derived
images are detected and skipped automatically.

Reports are saved to `archive/debsecan/` and include:

- Per-image detail reports (full CVE listing)
- Per-image fixable CVE reports (packages with available fixes)
- A combined summary (`summary.txt` and `summary.json`)

The summary report is included as a CI build artifact.

### Controlling the scan

```bash
# Skip the vulnerability scan entirely
./buildall.sh --build-targets "master" --skip-debsecan

# Fail the build if any fixable CVEs are found
./buildall.sh --build-targets "master" --debsecan-fail-on-fixable

# Run the scan standalone against already-built images
./_build/debsecan-report.sh --build-targets "master" \
    --distro debian --distro-version bookworm --image-tag local
```
