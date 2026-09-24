#!/bin/bash
# v1 matrix: runs a full comparison of the v1 series (stock, then
# patches 1-3 applied incrementally as p1/p2/p3) against the
# single-head guest, streaming-video=filter and off, via run.sh.
#
# Inputs (not provided by this import, see ../../README.md): qemu binaries
# at $W/build-stock/qemu-system-x86_64 (stock) and $W/build-p1/qemu-p1,
# qemu-p2, qemu-p3 (series/v1 applied one patch at a time, each build
# renamed to qemu-p<N>). $W/guest must hold vmlinuz/initrd.gz/
# initrd-fixed.gz, built by ../guest/build.sh. SVS restricts which
# streaming-video modes run (default: "filter off").
W=$(cd "$(dirname "$0")/.." && pwd)
declare -A Q=([stock]=$W/build-stock/qemu-system-x86_64 [p1]=$W/build-p1/qemu-p1 [p2]=$W/build-p1/qemu-p2 [p3]=$W/build-p1/qemu-p3)
for wl in full rect; do
  if [ $wl = rect ]; then export INITRD=initrd-fixed.gz; else export INITRD=initrd.gz; fi
  for v in stock p1 p2 p3; do
    for sv in ${SVS:-filter off}; do
      echo "== $v-$wl-$sv"; "$W/tools/run.sh" "$v-$wl-$sv" "${Q[$v]}" $sv
    done
  done
done
export INITRD=initrd-fixed.gz SPICE_EXTRA=,video-codecs=spice:mjpeg
for v in stock p3; do
  [ $v = stock ] && q=$W/build-stock/qemu-system-x86_64 || q=$W/build-p1/qemu-p3
  echo "== $v-rect-mjpeg"; "$W/tools/run.sh" "$v-rect-mjpeg" "$q" filter
done
