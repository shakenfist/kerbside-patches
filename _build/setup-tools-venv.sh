#!/bin/bash -e

# Create (or update) the shared tools venv at
# /srv/shakenfist/kerbside-patches-tools, install the requested pip
# packages into it, and symlink its console scripts into /usr/local/bin.
#
# All Python CLI tooling used by the build and test scripts is installed
# here rather than into the system Python. pip cannot upgrade
# distro-owned packages (they have no RECORD file), so system installs
# turn every distro upgrade into a new pip-vs-package-manager fight --
# the venv ends that permanently.
#
# The build scripts find these tools two ways: _build/common.sh (and
# friends) activate this venv when it exists, and the symlinks cover
# anything invoked outside that convention (e.g. clingwrap in workflow
# steps).
#
# Usage: setup-tools-venv.sh <pip-install-args...>
#
# Safe to run repeatedly; later callers add packages to the same venv.

if [ $# -eq 0 ]; then
    echo "Usage: $0 <pip-install-args...>" >&2
    exit 1
fi

venv=/srv/shakenfist/kerbside-patches-tools

sudo mkdir -p /srv/shakenfist
sudo chown "$(whoami)":"$(id -gn)" /srv/shakenfist

python3 -mvenv "${venv}"
"${venv}/bin/pip3" install -U pip
"${venv}/bin/pip3" install -U "$@"

for tool in tox yq xq virtualenv occystrap clingwrap flake8; do
    if [ -e "${venv}/bin/${tool}" ]; then
        sudo ln -sf "${venv}/bin/${tool}" "/usr/local/bin/${tool}"
    fi
done
