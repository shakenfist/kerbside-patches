#!/bin/bash
# Usage: run-trace.sh <label> <initrd>; boots the KMS dumb-buffer guest
# (../../qemu/rig/guest/init.c) under stock qemu and records virtio-gpu
# flush/transfer traces, to compare a stock drm_kms_helper.ko against
# one rebuilt with this series' patch (see build-module.sh) inside an
# initrd built with the rebuilt module swapped in.
#
# Inputs, resolved relative to the qemu rig (override with QEMU_RIG):
#   - $QEMU_RIG/build-stock/qemu-system-x86_64 (a stock qemu build)
#   - $QEMU_RIG/guest/vmlinuz (the guest kernel image; see
#     ../../qemu/rig/guest/build.sh)
#   - <initrd>, a path to an initrd built by build-module.sh with
#     either the stock or the rebuilt drm_kms_helper.ko
# Writes to results/<label>/ here, a scratch directory local to this
# rig checkout -- curated summaries of past runs live in ../results/.
set -u
K=$(cd "$(dirname "$0")" && pwd)
QEMU_RIG=${QEMU_RIG:-$K/../../qemu/rig}
Q=$QEMU_RIG
OUT=$K/results/$1
rm -rf "$OUT"; mkdir -p "$OUT"; cd "$OUT"
timeout -s KILL ${SECS:-15} "$Q/build-stock/qemu-system-x86_64" -enable-kvm -m 512 -smp 2 \
    -kernel "$Q/guest/vmlinuz" -initrd "$2" \
    -append "console=ttyS0 loglevel=4 rdinit=/init anim.delay=2 anim.fps=30" \
    -vga none -device virtio-vga -display none -serial file:serial.log \
    -trace virtio_gpu_cmd_res_flush -trace virtio_gpu_cmd_res_xfer_toh_2d \
    -D trace.log -msg timestamp=on > qemu.log 2>&1
echo "== $1"
grep -a 'ANIM\|taint\|virtio' serial.log | head -5
for t in res_flush res_xfer_toh_2d; do
  echo "-- $t: $(grep -c $t trace.log) total; top geometries:"
  grep $t trace.log | sed 's/.*res 0x[0-9a-f]*, //' | sort | uniq -c | sort -rn | head -4
done
