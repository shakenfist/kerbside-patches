#!/bin/bash
# Coverage: std VGA and qxl-vga (VGA mode, vesafb guest), and two-head
# virtio-vga, stock qemu vs the v2 series, via the two-head/vesafb
# guest (../guest/init2.c) and run2.sh.
#
# Inputs (not provided by this import, see ../../README.md): qemu binaries
# at $W/build-stock/qemu-system-x86_64 (stock) and $W/build-v2/
# qemu-v2-cols (series/v2 applied in full, i.e. through patch 4, the
# column split). $W/guest must hold vmlinuz/initrd2.gz/
# initrd2-fixed.gz, built by ../guest/build.sh.
W=$(cd "$(dirname "$0")/.." && pwd)
export RYLL_ARGS=-v
declare -A Q=([stock]=$W/build-stock/qemu-system-x86_64 [v2]=$W/build-v2/qemu-v2-cols)
for dev in vga qxlvga twohead; do
  case $dev in
    vga) D="-vga none -device VGA"; A="anim.fbdev=1 vga=0x344"; I=initrd2.gz;;
    qxlvga) D="-vga none -device qxl-vga"; A="anim.fbdev=1 vga=0x344"; I=initrd2.gz;;
    twohead) D="-vga none -device virtio-vga,max_outputs=2"; A="anim.heads=2"; I=initrd2-fixed.gz;;
  esac
  for v in stock v2; do
    for sv in filter off; do
      echo "== cov-$dev-$v-$sv"
      DEVICE="$D" APPEND_EXTRA="$A" INITRD=$I "$W/tools/run2.sh" "cov-$dev-$v-$sv" "${Q[$v]}" $sv
    done
  done
done
