#!/bin/bash
# Build a sysroot with the SPICE development headers/libraries qemu's
# ./configure --enable-spice needs, without installing them system-wide
# (so a CI runner without sudo, or a host that should not gain -dev
# packages permanently, can still build the patched qemu).
#
# Reconstructed from the session scratchpad's debs/ (the two .debs
# fetched) and sysroot/ (them extracted) -- the source dir showed
# `libspice-protocol-dev` and `libspice-server-dev` were downloaded and
# unpacked with dpkg-deb -x, one on top of the other, into a single
# tree passed to qemu's configure via --extra-cflags/--extra-ldflags
# (see the "Building qemu" section of ../../README.md). Not re-run or
# verified in the session that wrote it.
#
# Env overrides:
#   SPICE_PROTOCOL_VERSION  default 0.14.3-1
#   SPICE_SERVER_VERSION    default 0.15.2-1+b1
#   WORK                    scratch directory for downloaded .debs
#                           (default: ./.build)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SPICE_PROTOCOL_VERSION=${SPICE_PROTOCOL_VERSION:-0.14.3-1}
SPICE_SERVER_VERSION=${SPICE_SERVER_VERSION:-0.15.2-1+b1}
WORK=${WORK:-$HERE/.build}
SYSROOT=$HERE/sysroot
mkdir -p "$WORK"
rm -rf "$SYSROOT"
mkdir -p "$SYSROOT"

fetch() {
    local pkg=$1 ver=$2
    if apt-get download "${pkg}=${ver}" -o Dir::Cache::archives="$WORK" 2>/dev/null; then
        return
    fi
    echo "apt-get download failed for ${pkg}=${ver}; falling back to snapshot.debian.org" >&2
    local letter=${pkg:0:1}
    local url="https://snapshot.debian.org/archive/debian/latest/pool/main/${letter}/${pkg%-dev}/${pkg}_${ver}_amd64.deb"
    curl -fsSLo "$WORK/${pkg}.deb" "$url"
}

fetch libspice-protocol-dev "$SPICE_PROTOCOL_VERSION"
fetch libspice-server-dev "$SPICE_SERVER_VERSION"

for deb in "$WORK"/libspice-protocol-dev*.deb "$WORK"/libspice-server-dev*.deb; do
    dpkg-deb -x "$deb" "$SYSROOT"
done

cat <<EOF
Sysroot built at $SYSROOT.

Configure qemu against it with, e.g.:
  ./configure --enable-spice \\
      --extra-cflags="-I$SYSROOT/usr/include" \\
      --extra-ldflags="-L$SYSROOT/usr/lib/x86_64-linux-gnu"
EOF
