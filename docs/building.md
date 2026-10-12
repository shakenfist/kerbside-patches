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

Without overrides, the defaults in `_build/common.sh` apply: targets
`2023.1 2023.2 2024.1 master`, distro `debian`, and images `nova-compute
nova-libvirt nova-api kerbside`.

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

The `globals.yml` at the top of this repository is the one the CI test
deployments use; the settings that matter for Kerbside are
`kolla_base_distro: "debian"`, `nova_console: "spice"` and
`enable_kerbside: "yes"`.

You'll need a `globals.yml` in `/etc/kolla`, but that's site specific so
I don't want to include much detail here. Refer to the Kolla-Ansible
documentation for more details. Then I deploy like this:

```
sudo kolla-ansible -i /etc/kolla/inventory deploy
```

## The build manifest

When a build pushes to the CI registry (`--use-ci-registry`), the last
thing `build-containers.sh` does, once every push has landed, is record
which image digests that build produced. `tools/write-build-manifest`
takes the image names `kolla-build` recorded as built (`archive/images`),
and reads each one's digest from the registry with a
`HEAD /v2/<repo>/manifests/<tag>`; an image that was built but is missing
from the registry fails the build. It does not use the GitLab API, which the
CI registry token cannot be relied on for. The names equal the repository
names only for a build with no image prefix, as in CI; otherwise pass
`--image-prefix`. The digests come from the
registry, not from `kolla-build`, because the push filters rewrite every
image on its way in.

The result is a `manifest.json`:

```
{"format_version": 1, "tag": "<build tag>",
 "images": {"nova-api": "sha256:...", "keystone": "sha256:...", ...}}
```

It names every image the build pushed except the deployer
(`kolla-ansible`), which cannot name its own digest. It is copied into a
thin image built `FROM` the deployer's digest, at
`/etc/kolla-ansible/manifest.json`, with the label
`kolla_build_manifest="1"`, and pushed with `occystrap process` back to
`kolla-ansible:<tag>`. A deployer pinned by tag or by digest therefore pins
every image of its build. A copy of the manifest is left at
`archive/manifest-<tag>.json`, and CI keeps it with the build's artifacts.

The step:

* does nothing when no CI registry is in use, as in a local build;
* does nothing when `kolla-ansible:<tag>` is not in the registry, because
  the build did not include the deployer;
* does nothing when `kolla-ansible:<tag>` already carries the label, so a
  rerun that skipped the build because it is already built is a no-op; and
* fails the build if any image's digest cannot be read, rather than
  writing a partial manifest, and if the label is not there after the
  push.

The design is Phase 6 of
[the containerised deployer plan](plans/containerised-deployer.md).

## Skipping a tag that is already built

A CI image tag is `<release>-<distro>-<version>-<hash>`, where the hash
covers the patched source, `_build`, `tools` and the date. A rerun, or a
second run on the same day with the same patches, computes the same tag,
and `build-containers.sh` skips the build when that tag is already built.

"Already built" is recorded explicitly. The last thing a successful build
does, after every push has landed, the deployer has its manifest and the
`debsecan` scan has passed, is push a marker image,
`kolla-build-complete:<tag>`. It is a single-file image (the digest
manifest of everything built) with three labels:

* `kolla_build_complete="1"`;
* `kolla_build_selection`, a SHA-256 of the `--build-images` selection,
  because the tag does not cover which images were asked for; and
* `kolla_build_images`, the comma separated image names the build made.

Before building a tag, `tools/write-build-manifest build-state` reads the
marker's labels from the registry (a manifest and a config blob, through
the registry's bearer-token handshake, which works with the CI registry
token where the GitLab REST API does not). The build is skipped only when
the marker exists for the same selection. A build that failed part way
pushed no marker and is built again, as is a tag built for another
selection. If the registry cannot be asked at all, the job fails rather
than guessing, since the push that follows needs the same registry.

When the build is skipped, CI (`--dont-fetch-images`) pulls nothing. A
manual run without that flag pulls every image the marker names and tags
it locally as `openstack/kolla-images/<image>:<tag>`. Without registry
credentials there is no check, and the tag is always built.

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
