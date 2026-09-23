#!/bin/bash
# Build and run the bench microbenchmark (bench.c), which compares the
# upstream 32px column diff against this series' damage diff at
# 1920x1080, for the "Diff cost" numbers quoted in the cover letters.
#
# bench.c includes "../qemu/include/ui/spice-damage.h" (a plain header,
# no qemu build dependency), so it expects a qemu checkout at ../qemu
# with series/v2 applied -- see ../../README.md for how to get one.
# ui/spice-damage.c itself pulls in "qemu/osdep.h", qemu's build glue,
# which bench.c cannot link against standalone. This script produces a
# freestanding copy of it the same way the session scratchpad's
# bench/sd.c did: swap the two qemu includes for <glib.h> plus the
# handful of macros (MIN/MAX/DIV_ROUND_UP) osdep.h would otherwise
# provide. sd.c itself is not imported (see ../../README.md) because it
# was a point-in-time snapshot, already stale against the applied
# series by the time the session ended; this script regenerates the
# equivalent from whatever ../qemu/ui/spice-damage.c actually contains,
# so it cannot drift the same way.
#
# Env overrides: QEMU_CHECKOUT (default: ../qemu, see ../../README.md).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
QEMU_CHECKOUT=${QEMU_CHECKOUT:-$HERE/../qemu}
SRC=$QEMU_CHECKOUT/ui/spice-damage.c

if [ ! -f "$SRC" ]; then
    echo "bench/build.sh: $SRC not found." >&2
    echo "Expected a qemu checkout with upstream/qemu/series/v2 applied at $QEMU_CHECKOUT." >&2
    exit 1
fi

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

sed \
    -e 's/#include "qemu\/osdep.h"/#include <glib.h>\n#define MIN(a, b) ((a) < (b) ? (a) : (b))\n#define MAX(a, b) ((a) > (b) ? (a) : (b))\n#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) \/ (d))/' \
    -e 's/#include "ui\/spice-damage.h"/#include "spice-damage.h"/' \
    "$SRC" > "$STAGE/sd.c"
cp "$QEMU_CHECKOUT/include/ui/spice-damage.h" "$STAGE/spice-damage.h"

gcc -O2 -Wall $(pkg-config --cflags glib-2.0) \
    -o "$HERE/bench" "$HERE/bench.c" "$STAGE/sd.c" \
    $(pkg-config --libs glib-2.0)

echo "Built $HERE/bench; run it directly (see bench.c's usage comment)."
