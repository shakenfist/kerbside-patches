#!/bin/bash
# v2 matrix: virtio-vga, full-plane (initrd.gz) and clip-honouring
# (initrd-fixed.gz) workloads, streaming-video=filter and off, across
# v2 patch-3's pacing variants (see the "list/diff/pace" naming below).
#
# Inputs (not provided by this import, see ../../README.md): three qemu
# builds under $W/build-v2/, each a checkout with series/v2 applied up
# to a different patch, renamed qemu-v2-list (patch 1, the damage
# list), qemu-v2-diff (patches 1-2, + the mirror diff) and qemu-v2-pace
# (patches 1-3, + the promptness pacing timer); pace60 reuses the
# qemu-v2-pace binary with max-refresh-rate=60 on the command line
# rather than a separate build (this naming is inferred from the
# variant labels below and the series' own patch order, not stated
# anywhere in the source dir). $W/guest must hold vmlinuz/initrd.gz/
# initrd-fixed.gz, built by ../guest/build.sh. SVS and VARIANTS
# restrict which streaming-video modes / pacing variants run.
W=$(cd "$(dirname "$0")/.." && pwd)
B=$W/build-v2
for wl in full rect; do
  if [ $wl = rect ]; then export INITRD=initrd-fixed.gz; else export INITRD=initrd.gz; fi
  for sv in ${SVS:-filter off}; do
    for v in ${VARIANTS:-list diff pace pace60}; do
      q=$B/qemu-v2-$v; extra=
      [ $v = pace60 ] && q=$B/qemu-v2-pace && extra=,max-refresh-rate=60
      echo "== v2$v-$wl-$sv"
      SPICE_EXTRA=$extra "$W/tools/run.sh" "v2$v-$wl-$sv" "$q" $sv
    done
  done
done
