#!/bin/bash
# Rebuild drm_kms_helper.ko against the guest kernel's own ABI, once
# stock and once with series/v1's patch applied, for run-trace.sh's
# A (stock)/B (rebuilt, unpatched)/C (rebuilt, patched) comparison --
# see ../results/ for what that comparison found.
#
# Reconstructed from what the session scratchpad's kdebs/ (downloaded
# linux-headers-*/linux-kbuild-* .debs), ksys/ (them extracted) and
# build/ (an out-of-tree module build) directories showed had been
# done. Not re-run or verified in the session that wrote it, and one
# step is genuinely uncertain rather than just undemonstrated -- see
# the note before step 3.
#
# Env overrides:
#   DEBIAN_KERNEL_VERSION  Debian kernel package version the guest
#                          runs (default 6.12.101-1, matching
#                          upstream/qemu/rig/guest/build.sh's default
#                          and this project's linux base SHA -- see
#                          ../config.yaml).
#   LINUX_CHECKOUT         a linux.git checkout to build from (default:
#                          clone https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
#                          into ./.build/linux). Must be checked out at
#                          the v6.12.101 tag (see ../config.yaml's
#                          also_applies_to) for the module's vermagic to
#                          match the guest kernel.
#   WORK                   scratch directory (default: ./.build).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DEBIAN_KERNEL_VERSION=${DEBIAN_KERNEL_VERSION:-6.12.101-1}
WORK=${WORK:-$HERE/.build}
mkdir -p "$WORK"

# 1. Fetch the guest kernel's headers, kbuild and image packages: the
# headers/kbuild give a source tree that matches the running kernel's
# Kbuild version; the image package's config-* is the .config that
# actually produced the running vmlinuz, needed so the rebuilt module's
# vermagic and CONFIG_* symbol versions match it.
fetch() {
    local pkg=$1
    if apt-get download "$pkg" -o Dir::Cache::archives="$WORK" 2>/dev/null; then
        return
    fi
    local letter=${pkg:0:1}
    local url="https://snapshot.debian.org/archive/debian/latest/pool/main/${letter}/linux/${pkg}_${DEBIAN_KERNEL_VERSION}_amd64.deb"
    echo "apt-get download failed for $pkg; trying $url" >&2
    curl -fsSLo "$WORK/${pkg}.deb" "$url"
}
KVER=${DEBIAN_KERNEL_VERSION%-*}
fetch "linux-headers-${KVER}+deb13-amd64"
fetch "linux-headers-${KVER}+deb13-common"
fetch "linux-kbuild-${KVER}+deb13"
fetch "linux-image-${KVER}+deb13-amd64"

SYSROOT=$WORK/sysroot
mkdir -p "$SYSROOT"
for deb in "$WORK"/linux-headers-*.deb "$WORK"/linux-kbuild-*.deb "$WORK"/linux-image-*.deb; do
    dpkg-deb -x "$deb" "$SYSROOT"
done
KDIR=$SYSROOT/usr/src/linux-headers-${KVER}+deb13-amd64
CONFIG=$SYSROOT/boot/config-${KVER}+deb13-amd64

# 2. Get a matching linux.git checkout at the v6.12.101 tag (or
# whichever tag DEBIAN_KERNEL_VERSION corresponds to).
LINUX_CHECKOUT=${LINUX_CHECKOUT:-$WORK/linux}
if [ ! -d "$LINUX_CHECKOUT/.git" ]; then
    git clone https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git "$LINUX_CHECKOUT"
fi
git -C "$LINUX_CHECKOUT" checkout "v${KVER}"

# 3. Prepare the module build tree. This mirrors "make modules_prepare"
# against $KDIR's own scripts/Kbuild infrastructure, seeded with the
# guest's own .config so the resulting module's CONFIG_* and vermagic
# line up.
#
# UNCERTAIN: the scratchpad's build/.config (from the build-check.log
# sanity build) was generated against the linux/ clone's drm-misc-next
# based checkout (Linux/x86 7.3.0-rc4), not v6.12.101, and looks like a
# "does the patch still compile" check rather than the build that
# produced guest/drm_kms_helper-{stock,fix}.ko. The commands below are
# the standard way to build an out-of-tree-ABI-matching module against
# a target kernel's installed headers, but were not directly evidenced
# for this specific rebuild in the scratchpad, so treat them as a
# documented best guess, not a verified recipe.
build_module() {
    local label=$1
    local build=$WORK/build-$label
    rm -rf "$build"
    mkdir -p "$build"
    cp "$CONFIG" "$build/.config"
    make -C "$LINUX_CHECKOUT" O="$build" olddefconfig
    make -C "$LINUX_CHECKOUT" O="$build" modules_prepare
    make -C "$LINUX_CHECKOUT" O="$build" M=drivers/gpu/drm modules
    cp "$build/drivers/gpu/drm/drm_kms_helper.ko" "$HERE/drm_kms_helper-$label.ko"
}

git -C "$LINUX_CHECKOUT" checkout -B build-module-stock "v${KVER}"
build_module stock

git -C "$LINUX_CHECKOUT" checkout -B build-module-fix "v${KVER}"
git -C "$LINUX_CHECKOUT" am "$HERE/../series/v1/"*.patch
build_module fix

echo "Built $HERE/drm_kms_helper-stock.ko and $HERE/drm_kms_helper-fix.ko."
echo "Swap one in for the stock module (see upstream/qemu/rig/guest/build.sh's"
echo "make_initrd) to build the rebuilt-unfixed / rebuilt-fixed initrds run-trace.sh compares."
