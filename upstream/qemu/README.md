# qemu: SPICE damage-path series

See [../README.md](../README.md) for the layout and disposition
policy (this series is carried downstream, not submitted -- see its
"Per-project status" section for why), and
[../../docs/plans/upstream-series.md](../../docs/plans/upstream-series.md)
for the plan.

qemu's non-GL SPICE display (`ui/spice-display.c`) unions damage into
one box, diffs it against a mirror in 32-pixel columns, and only
pushes on the 30ms refresh timer. That starves spice-server's video
stream detection into many small streams instead of one. `series/v3/`
(current; `v1` and `v2` are kept for history, see the `v1` vs `v2`
diffs in `results/`) fixes this in five patches: a bounded list of
damage rectangles, sending each as one draw, pushing promptly (paced by
`max-refresh-rate`), splitting on unchanged columns too, and capping the
bytes one draw may carry. Full measurement method and results are in
`series/v3/v3-0000-cover-letter.patch` and `results/v2-summary.md`.

The fifth patch answers the slow-link regression `results/v2-summary.md`
records: the server's ACK window counts messages rather than bytes, so
one whole-region drawable lets a far larger backlog build than the
32-pixel columns it replaced. It bands damage above a byte budget,
`SPICE_DISPLAY_MAX_DRAW_BYTES`, overridable at build time.

`results/v3-summary.md` measures what that costs. Banding does not
defeat stream detection, which was the design's open risk: spice-server
forms one stream per band, three at the 256 KB default, against one for
v2 and twenty-odd for stock. The default carries 7% more bytes than v2
while cutting the largest message from about 691 KB to about 230 KB.

**The default is still not settled**, and that page says why: the cap
exists to bound the backlog, and on that axis a smaller budget is
better, so the arm that measures worst on streams may measure best on
latency. Settling it needs kerbside's shaped-link rig per arm, which is
the next thing this series needs.

> **Do not build this series into an image.** The diff helpers added in
> v2-0002, which v3 carries unchanged, have unbounded byte loops, so a
> guest that writes its framebuffer while a diff is in progress can
> drive a read past the end of the buffer
> ([kerbside-patches#2032](https://github.com/shakenfist/kerbside-patches/issues/2032),
> rated high by the spice-performance phase 6 push audit). Every series
> here is for the measurement rig only until that is fixed. This bars the
> `_patches/` route and any nova-libvirt image, not local rig builds.

## Building qemu

The rig needs a locally built qemu with SPICE support, and without
root on a CI runner or a host you would rather not touch permanently.
`rig/sysroot/build.sh` extracts `libspice-protocol-dev` and
`libspice-server-dev` into `rig/sysroot/sysroot/` for that:

```
rig/sysroot/build.sh
git clone https://gitlab.com/qemu-project/qemu.git rig/qemu
cd rig/qemu
git checkout 30e8a06b64aa58a3990ba39cb5d09531e7d265e0   # config.yaml's base_sha
git am ../../series/v3/v3-000{1,2,3,4,5}*.patch          # or v2, v1, or none
SYS=$(pwd)/../sysroot/sysroot
PKG_CONFIG_PATH="$SYS/usr/lib/x86_64-linux-gnu/pkgconfig:$SYS/usr/share/pkgconfig" \
    ./configure --target-list=x86_64-softmmu --enable-spice --enable-kvm
ninja -C build
```

qemu finds spice through pkg-config, so `PKG_CONFIG_PATH` is what makes
the dependency check succeed; the `--extra-cflags`/`--extra-ldflags`
pair this section used to give instead does not, and meson fails with
`Dependency "spice-server" not found`. Do not set
`PKG_CONFIG_SYSROOT_DIR` to fix that: it rewrites every package's paths,
not just spice's, and qemu's configure then fails on glib with
`sizeof(size_t) doesn't match GLIB_SIZEOF_SIZE_T`. `rig/sysroot/build.sh`
rewrites the `prefix=` in the two spice `.pc` files instead, which is
why only `PKG_CONFIG_PATH` is needed here.

A shallow fetch of the base commit is enough if a full clone is
unwelcome: `git init`, `git remote add`, then
`git fetch --depth 1 origin <base_sha>` and `git checkout FETCH_HEAD`.

`rig/tools/matrix.sh`, `matrix2.sh` and `coverage.sh` each expect
several such builds side by side (stock, and the series applied to
different points), renamed as their header comments describe -- they
were built this way once per session in the scratchpad that produced
this import (`build-stock/`, `build-p1/`, `build-v2/`), not
automated; automating it is
[phase 3](../../docs/plans/upstream-series.md#phase-3--build-and-measure-in-ci)
of the plan.

## Running the rig

1. `rig/guest/build.sh` builds the guest kernel image and initrds (see
   its header for what it reconstructs and what remains a documented
   manual step).
2. Build one or more qemu binaries as above.
3. `rig/tools/run.sh <label> <qemu-binary> <streaming-video>` runs one
   measurement; `matrix.sh`, `matrix2.sh` and `coverage.sh` run a
   whole sweep. Each writes to `rig/results/<label>/` (a local,
   gitignored scratch directory -- not `results/`, which holds only
   the curated summaries checked in here).
4. `rig/tools/summary.py <label>...` turns a sweep's raw output into
   the markdown tables in `results/`.
5. `rig/bench/build.sh` builds and runs the standalone diff
   microbenchmark used for the "Diff cost" numbers in the cover
   letter and `results/v2-summary.md`.

## Results

- `results/v1-summary.md` -- the v1 series' per-patch comparison.
- `results/v2-summary.md` -- the v2 series' results (mostly a copy of
  the cover letter's tables, plus the device coverage matrix and
  patch-3 pacing-variant matrix, which the cover letter does not
  carry).
