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
# (see the "Building qemu" section of ../../README.md).
#
# Re-run 2026-11-14, which is how the download-directory bug above was
# found: it had never been exercised. The snapshot.debian.org fallback
# still has not been, and its URL looks wrong for these two packages --
# it derives the pool directory from the binary package name, so it asks
# for pool/main/l/libspice-protocol/ where Debian has
# pool/main/s/spice-protocol/. It only fires when apt-get download
# fails.
#
# Three packages, not two: the -dev package ships
# libspice-server.so as a symlink to libspice-server.so.1, which lives in
# the runtime package libspice-server1. Without it the symlink dangles and
# the qemu link fails with "cannot find -lspice-server", even on a host that
# has the runtime installed -- ld needs the .so in a directory it searches,
# and the host has only the versioned file.
#
# Env overrides:
#   SPICE_PROTOCOL_VERSION  default 0.14.3-1
#   SPICE_SERVER_VERSION    default 0.15.2-1+b1 (-dev and runtime alike)
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
    # apt-get download writes into the current directory and has no option
    # to send it anywhere else: Dir::Cache::archives names the package cache,
    # not the download target, so passing it left the .deb in whatever
    # directory the caller happened to be in and the extraction below found
    # nothing. Run it from the scratch directory instead.
    if (cd "$WORK" && apt-get download "${pkg}=${ver}" 2>/dev/null); then
        return
    fi
    echo "apt-get download failed for ${pkg}=${ver}; falling back to snapshot.debian.org" >&2
    local letter=${pkg:0:1}
    local url="https://snapshot.debian.org/archive/debian/latest/pool/main/${letter}/${pkg%-dev}/${pkg}_${ver}_amd64.deb"
    curl -fsSLo "$WORK/${pkg}.deb" "$url"
}

fetch libspice-protocol-dev "$SPICE_PROTOCOL_VERSION"
fetch libspice-server-dev "$SPICE_SERVER_VERSION"
fetch libspice-server1 "$SPICE_SERVER_VERSION"

for deb in "$WORK"/libspice-protocol-dev*.deb "$WORK"/libspice-server-dev*.deb \
           "$WORK"/libspice-server1_*.deb; do
    dpkg-deb -x "$deb" "$SYSROOT"
done

# Point the extracted .pc files at the sysroot. They ship prefix=/usr, which
# pkg-config would resolve to the host's /usr/include and /usr/lib -- where
# these packages are precisely not installed, which is the whole reason for a
# sysroot. PKG_CONFIG_SYSROOT_DIR would fix that, but it prefixes *every*
# package's paths, so glib and pixman get looked for in here too and qemu's
# configure fails with "sizeof(size_t) doesn't match GLIB_SIZEOF_SIZE_T".
# Rewriting the prefix in just these two files keeps the system packages
# resolving normally.
for pc in "$SYSROOT"/usr/share/pkgconfig/*.pc \
          "$SYSROOT"/usr/lib/*/pkgconfig/*.pc; do
    [ -f "$pc" ] || continue
    sed -i "s|^prefix=/usr\$|prefix=$SYSROOT/usr|" "$pc"
done

cat <<EOF
Sysroot built at $SYSROOT.

Configure qemu against it with:
  PKG_CONFIG_PATH=$SYSROOT/usr/lib/x86_64-linux-gnu/pkgconfig:$SYSROOT/usr/share/pkgconfig \\
      ./configure --target-list=x86_64-softmmu --enable-spice --enable-kvm

qemu finds spice through pkg-config, so PKG_CONFIG_PATH is what matters;
--extra-cflags/--extra-ldflags alone do not make meson's dependency
check succeed. Do not set PKG_CONFIG_SYSROOT_DIR: see the note above.
EOF
