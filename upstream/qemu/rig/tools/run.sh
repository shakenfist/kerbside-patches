#!/bin/bash
# Usage: run.sh <label> <qemu-binary> <streaming-video> [fps]
#
# Boots the single-head animation guest (../guest/init.c, built by
# ../guest/build.sh into guest/initrd.gz and guest/initrd-fixed.gz),
# connects ryll headless with --capture for RYLL_SECS seconds, and
# collects pcaps, spice debug log, qemu trace and qemu CPU time into
# results/<label>/ (a scratch directory local to this rig checkout --
# curated summaries of past runs live in ../../results/, not here).
#
# Inputs, all resolved relative to this rig checkout (../), or override
# with the env vars below:
#   - a qemu binary, built from this series or stock, passed as $2
#   - ../guest/vmlinuz and ../guest/initrd*.gz (see ../guest/build.sh)
#   - a ryll binary at ../ryll/target/release/ryll, or RYLL=<path>
# Env overrides: INITRD (default initrd.gz), SPICE_EXTRA (appended to
# -spice), RYLL_ARGS (default --capture cap), RYLL_SECS (default 28),
# RYLL (default $W/ryll/target/release/ryll).
set -u
W=$(cd "$(dirname "$0")/.." && pwd)
RYLL=${RYLL:-$W/ryll/target/release/ryll}
LABEL=$1
QEMU=$2
SV=$3
FPS=${4:-30}
RYLL_SECS=${RYLL_SECS:-28}
PORT=5930
OUT=$W/results/$LABEL
rm -rf "$OUT"
mkdir -p "$OUT"
cd "$OUT"

G_MESSAGES_DEBUG=all "$QEMU" -enable-kvm -m 512 -smp 2 \
    -kernel "$W/guest/vmlinuz" -initrd "$W/guest/${INITRD:-initrd.gz}" \
    -append "console=ttyS0 loglevel=4 rdinit=/init anim.delay=6 anim.fps=$FPS" \
    -vga none -device virtio-vga \
    -spice port=$PORT,addr=127.0.0.1,disable-ticketing=on,streaming-video=$SV${SPICE_EXTRA:-} \
    -serial file:serial.log -display none \
    -trace qemu_spice_display_update -trace qemu_spice_create_update \
    -trace qemu_spice_display_refresh \
    -D trace.log -msg timestamp=on > qemu.log 2>&1 &
QPID=$!
sleep 2
cpu() { awk '{print $14 + $15}' /proc/$QPID/stat; }
C0=$(cpu)
timeout -s INT "$RYLL_SECS" "$RYLL" \
    --direct 127.0.0.1:$PORT --headless ${RYLL_ARGS:---capture cap} > ryll.out 2> ryll.err
C1=$(cpu 2>/dev/null || echo NA)
kill $QPID 2>/dev/null
wait $QPID
echo "{\"qemu_cpu_ticks\": \"$C0 $C1\", \"qemu_rc\": $?}" > run.json
grep -q 'Segmentation\|SIGSEGV' qemu.log && echo "QEMU CRASHED" >> run.json
cat run.json
