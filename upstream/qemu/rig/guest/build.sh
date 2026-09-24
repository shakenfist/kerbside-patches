#!/bin/bash
# Build the static PID-1 KMS guests (init.c, init2.c) into the initrds
# and kernel image run.sh / run2.sh / run-trace.sh expect in this
# directory: vmlinuz, initrd.gz, initrd-fixed.gz, initrd2.gz,
# initrd2-fixed.gz.
#
# Reconstructed from what the session scratchpad's guest/, kmod/,
# ksys/, debs/ and kdebs/ directories showed had been done, since the
# built artefacts themselves (initrd*.gz, vmlinuz, *.ko) are not
# imported -- see ../../README.md. Not re-run or verified in the
# session that wrote it; treat it as a documented recipe and expect to
# fix it up the first time it is actually run. In particular the
# "fixed" modules step (3) rebuilds virtio-gpu.ko with a local,
# virtio-gpu-only version of the fix this project's linux series
# carries in the DRM core (../../../linux/series/v1/); that duplication
# is deliberate; see step 3.
#
# Env overrides:
#   DEBIAN_KERNEL_VERSION  Debian kernel package version providing the
#                          guest vmlinuz and stock modules (default:
#                          6.12.101-1, matching the deb13 kernel this
#                          rig was built and measured against).
#   WORK                   scratch directory for downloaded .debs and
#                          extracted sysroots (default: ./.build).
#
# Requires on the host: gcc (with static libc and Linux DRM headers --
# Debian's libc6-dev + libdrm-dev cover both), cpio, gzip, dpkg-deb,
# and either apt-get download or network access to snapshot.debian.org
# for step 2.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DEBIAN_KERNEL_VERSION=${DEBIAN_KERNEL_VERSION:-6.12.101-1}
WORK=${WORK:-$HERE/.build}
mkdir -p "$WORK"

# 1. Compile the two static PID-1 programs. Each becomes the /init of
# its own initrd; init.c is the single-head guest, init2.c adds the
# vesafb (VGA / qxl-vga) and two-head paths matrix2.sh's coverage
# matrix needs.
build_init() {
    local src=$1 out=$2
    gcc -O2 -static -Wall -o "$WORK/$out" "$HERE/$src" -lm
}
build_init init.c init
build_init init2.c init2

# 2. Fetch the matching Debian guest kernel package (linux-image, not
# just linux-headers/linux-kbuild, which is what kdebs/ in the source
# scratchpad held -- those are for step 3, rebuilding a module, and do
# not carry vmlinuz or the stock .ko.xz files). `apt-get download`
# needs the package available in the host's apt sources; snapshot.debian.org
# is the fallback used here so this step also works on a host that
# does not carry a deb13 apt source.
fetch_kernel_image() {
    local pkg="linux-image-${DEBIAN_KERNEL_VERSION%-*}+deb13-amd64"
    if apt-get download "$pkg" -o Dir::Cache::archives="$WORK" 2>/dev/null; then
        :
    else
        local url="https://snapshot.debian.org/archive/debian/latest/pool/main/l/linux/${pkg}_${DEBIAN_KERNEL_VERSION}_amd64.deb"
        echo "apt-get download failed; trying $url" >&2
        curl -fsSLo "$WORK/${pkg}.deb" "$url"
    fi
    mkdir -p "$WORK/kernel-sysroot"
    dpkg-deb -x "$WORK/${pkg}"*.deb "$WORK/kernel-sysroot"
}
fetch_kernel_image

VMLINUZ=$(find "$WORK/kernel-sysroot/boot" -name 'vmlinuz-*' | head -1)
cp "$VMLINUZ" "$HERE/vmlinuz"

MODDIR=$(find "$WORK/kernel-sysroot/lib/modules" -maxdepth 1 -mindepth 1 -type d | head -1)
STOCK_MODS="virtio_dma_buf.ko drm.ko drm_kms_helper.ko drm_shmem_helper.ko virtio-gpu.ko"

collect_stock_modules() {
    local dest=$1
    mkdir -p "$dest"
    local mod
    for mod in $STOCK_MODS; do
        local path
        path=$(find "$MODDIR" -name "${mod}*" | head -1)
        [ -n "$path" ] || { echo "module $mod not found under $MODDIR" >&2; exit 1; }
        case $path in
            *.xz) cp "$path" "$dest/${mod}.xz" ;;
            *) xz -c "$path" > "$dest/${mod}.xz" ;;
        esac
    done
}

# 3. Rebuild virtio-gpu.ko with ignore_damage_clips recalculated on
# every atomic_check, matching the fix upstream later took for
# virtio-gpu alone (drm-misc-next a9cc9905ddb7). This is a *different*,
# narrower fix than the DRM-core one this project carries in
# upstream/linux/series/v1/ (which fixes every driver, not just
# virtio-gpu, by clearing the flag in
# __drm_atomic_helper_plane_duplicate_state()); the qemu rig's
# "-fixed" initrds predate that series and were the first prototype of
# the same underlying bug. Keeping both is deliberate: this one
# isolates the qemu-side measurement from the kernel series' own
# build, and upstream/linux/rig/build-module.sh rebuilds the DRM-core
# fix separately for the A/B/C comparison in
# upstream/linux/results/.
#
# The source scratchpad's kdebs/ (linux-headers + linux-kbuild for the
# same Debian kernel version) and ksys/ (those debs extracted) show
# this was done by building an out-of-tree module against the
# running kernel's headers. Reconstructing the exact one-line diff to
# virtio-gpu's atomic_check was not attempted here -- the diff was not
# preserved in the scratchpad in a form distinct from the DRM-core
# patch already carried in upstream/linux/series/v1/, so this step is
# left as a documented manual one rather than guessed at:
#
#   1. apt-get download / snapshot.debian.org the matching
#      linux-headers-<ver>+deb13-amd64, linux-headers-<ver>+deb13-common
#      and linux-kbuild-<ver>+deb13 packages, dpkg-deb -x each into a
#      sysroot (e.g. $WORK/kernel-headers-sysroot).
#   2. Check out the matching kernel source (see
#      upstream/linux/config.yaml for the base SHA), edit
#      drivers/gpu/drm/virtio/virtio_plane.c's atomic_check so it
#      recalculates ignore_damage_clips every time instead of only on
#      an fb change (this is what a9cc9905ddb7 does upstream; use it
#      as the reference once available on the target branch).
#   3. make -C "$WORK/kernel-headers-sysroot/usr/src/linux-headers-<ver>+deb13-amd64" \
#        M=$(pwd)/drivers/gpu/drm/virtio modules
#   4. Copy the resulting virtio-gpu.ko in place of the stock one
#      before running collect_stock_modules for the "-fixed" initrds.
echo "guest/build.sh: step 3 (rebuilding a fixed virtio-gpu.ko) is a documented manual step, see the comment above it" >&2

# 4. Assemble each initrd: /init plus /m/<module>.ko.xz.
make_initrd() {
    local init=$1 moddir=$2 out=$3
    local stage
    stage=$(mktemp -d)
    cp "$WORK/$init" "$stage/init"
    chmod 755 "$stage/init"
    mkdir -p "$stage/m"
    cp "$moddir"/*.ko.xz "$stage/m/"
    (cd "$stage" && find . | cpio -o -H newc 2>/dev/null | gzip -9) > "$HERE/$out"
    rm -rf "$stage"
}

collect_stock_modules "$WORK/mods-stock"
make_initrd init "$WORK/mods-stock" initrd.gz
make_initrd init2 "$WORK/mods-stock" initrd2.gz

if [ -d "$WORK/mods-fixed" ]; then
    make_initrd init "$WORK/mods-fixed" initrd-fixed.gz
    make_initrd init2 "$WORK/mods-fixed" initrd2-fixed.gz
else
    echo "guest/build.sh: $WORK/mods-fixed not populated (see step 3); skipping initrd-fixed.gz / initrd2-fixed.gz" >&2
fi
