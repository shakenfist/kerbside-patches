#!/bin/bash

# Install the system and Python dependencies needed to run the kerbside
# patch test suites (_build/test-apply.sh) directly on a CI VM runner.
#
# The rebase tests used to run on bare-metal runners (or by spinning up a
# nested Shakenfist VM); they now run straight on the conductor-provisioned
# VM runner, which is the right OS already. This script papers over the
# apt-vs-dnf differences so the workflow stays simple.
#
# On rocky 9 the system Python is too old for current kolla-ansible, so
# tools/upgrade-python is used to move to Python 3.12 (a no-op elsewhere).

set -ex

here="$(cd "$(dirname "$0")" && pwd)"

if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y \
        python3-pip python3-dev build-essential \
        moreutils libpq-dev libpcre2-dev
    # -U matters: the runner image may have an older tox baked in, and
    # without it this install is a "requirement already satisfied" no-op.
    # Old tox (v3) cannot test projects using PEP 639 metadata.
    sudo pip3 install --break-system-packages -U yq tox

elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y dnf-plugins-core epel-release
    sudo dnf config-manager --set-enabled crb
    sudo dnf install -y \
        python3-pip python3-devel gcc \
        moreutils libpq-devel pcre-devel
    # kolla / kolla-ansible master need a newer Python than rocky 9 ships.
    # upgrade-python relinks python3 -> 3.12 on rocky 9 and is a no-op on
    # rocky 10 and other distros, so run it before the final pip install.
    sudo "${here}/upgrade-python"
    sudo pip3 install -U yq tox flake8

else
    echo "Unsupported runner: neither apt-get nor dnf found" >&2
    exit 1
fi
