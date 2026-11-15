# v3 series: results

v3 is v2 plus one patch, which caps the pixel payload of a single
DRAW_COPY and sends larger damage as a stack of bands
(`SPICE_DISPLAY_MAX_DRAW_BYTES`, overridable at build time). The cap
exists to answer the slow-link regression in
[v2-summary.md](v2-summary.md): spice-server's ACK window counts
messages rather than bytes, so one whole-region drawable lets a far
larger backlog build than the 32-pixel columns it replaced.

**Read this file for what the cap costs, not for what it buys.** The
measurement below is the rig's own unshaped run, which answers whether
banding breaks video stream detection. It does not measure latency
behind a shaped link, which is the regression the cap is for. Those two
axes pull in opposite directions and only one of them has been
measured; see *What this does not settle*.

## Banding and stream detection, 28 s, virtio-vga, 480x360 at 30 fps

Each arm is the same qemu built from a different point of this series,
driven by `../rig/tools/run.sh <label> <qemu> <streaming-video> 30`
with `RYLL_ARGS=-v`, and summarised by `../rig/tools/ryllana.py`. The
window is 32.9 s in every arm.

### streaming-video=filter

  arm         draws  streams  display MB  stream geometry
  stock        8604       20       35.84  MJPEG 32x360 x20
  v2             50        1        7.76  MJPEG 480x360
  v3 @ 1 MB      50        1        7.70  MJPEG 480x360
  v3 @ 256 KB    90        3        8.32  MJPEG 480x136 x2, 480x88
  v3 @ 64 KB    255       11       11.00  MJPEG 480x34 x10, 480x20

### streaming-video=all

  arm         draws  streams  display MB  stream geometry
  stock        7521       27       30.79  MJPEG 32x360 x27
  v2             50        1        7.75  MJPEG 480x360
  v3 @ 1 MB      50        1        7.75  MJPEG 480x360
  v3 @ 256 KB    90        3        8.35  MJPEG 480x136 x2, 480x88
  v3 @ 64 KB    250       11       11.02  MJPEG 480x34 x10, 480x20

**Banding does not defeat stream detection.** This was the open risk in
the cap's design: splitting a region was how the unpatched code ended
up with twenty-odd 32-pixel streams, so a cap that split regions might
have reintroduced the problem it was added beside. It does not. Each
band repeats at a steady position and size, and spice-server forms one
stream per band -- three at the 256 KB default, eleven at 64 KB -- not
one per frame and not none.

**The cap's cost is bytes, and it is small at the default.** 256 KB
carries 7% more than v2 (8.32 against 7.76 MB) while cutting the
largest message from about 691 KB to about 230 KB. 64 KB carries 42%
more. Both remain far better than stock: a quarter of the bytes and
between a thirtieth and a hundredth of the draws.

**A budget above the region size is a no-op**, as intended: 1 MB
reproduces v2 exactly, since a 480x360x4 region is about 691 KB and is
never split. That is the control for the measurement -- it shows the
arms differ because of the cap and not because of anything else that
changed between builds.

The two streaming modes agree to within 0.03 MB on every v3 arm, so
nothing here depends on which one a deployment uses. stock fragments
differently between them (20 streams against 27), which is stock's
behaviour and not this series'.

## What this does not settle

The default is still chosen on reasoning rather than evidence, because
the evidence that would settle it is latency behind a constrained link
and that has not been measured.

The two axes disagree by construction. On this page, bigger budgets are
better: fewer streams, fewer bytes, and 1 MB is indistinguishable from
no cap at all. But the cap exists to bound the backlog the ACK window
permits, and on that axis smaller is better -- 64 KB comes closest to
the 60 KB messages the unpatched code sent, which is the backlog
behaviour the regression was measured against. **So the arm that looks
worst here may well be the one that looks best on latency, and this
table must not be read as choosing the default.**

What would settle it is kerbside's shaped-link rig
(`tools/shaped-link/run-matrix.sh` in the kerbside repository) run per
arm through its `DISPLAYS` variable, measuring keypress-to-draw at
80 ms / 10 Mbit with `streaming-video=filter` on content the server
will not stream -- the cell where v2 went from 2.1 s to 8.0 s. That
needs a built kerbside-proxy, kerbside's own guest and a mock control
plane in addition to what this rig builds.

## Reproducing this

The raw `ryllana.py` JSON for all ten runs, and the two ad-hoc scripts
that built the arms and drove them, were kept in a `v3-sweep-raw/`
directory beside this file. They were never imported and no longer
exist, so redoing the sweep means building the arms again first: five
qemu builds from this tree's `base_sha`, one unpatched, one with
`series/v2/`, and three with `series/v3/` and
`-DSPICE_DISPLAY_MAX_DRAW_BYTES` set to 1 MB, 256 KB and 64 KB -- the
constant is `#ifndef`-guarded in v3-0005 for exactly this. Each arm is
then driven by `../rig/tools/run.sh` for both `streaming-video` modes
and summarised by `../rig/tools/ryllana.py`, both of which are in this
tree. Three things cost time the first time round and are worth
knowing:

- qemu resolves its firmware relative to its build tree, so an arm
  binary copied elsewhere needs `-L <qemu tree>/pc-bios` or it dies
  with `could not load PC BIOS 'bios-256k.bin'`.
- the budget variants differ only by one constant, so confirm the arm
  binaries are actually distinct before trusting a sweep; identical
  binaries would produce a confident table measuring nothing.
- `../rig/tools/run.sh` now follows its `timeout -s INT` with a
  `SIGKILL`, because ryll does not exit on `SIGINT` in headless mode. A
  run meant to last 28 s ran for 676 s before that fix, which makes
  every per-second figure wrong and two arms incomparable.
