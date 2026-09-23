# qemu: SPICE damage-path series

See [../README.md](../README.md) for the layout and disposition
policy (this series is carried downstream, not submitted -- see its
"Per-project status" section for why), and
[../../docs/plans/upstream-series.md](../../docs/plans/upstream-series.md)
for the plan.

qemu's non-GL SPICE display (`ui/spice-display.c`) unions damage into
one box, diffs it against a mirror in 32-pixel columns, and only
pushes on the 30ms refresh timer. That starves spice-server's video
stream detection into many small streams instead of one. `series/v2/`
(current; `series/v1/` is kept for history, see `v1` vs `v2` diffs in
`results/`) fixes this in four patches: a bounded list of damage
rectangles, sending each as one draw, pushing promptly (paced by
`max-refresh-rate`), and splitting on unchanged columns too. Full
measurement method and results are in
`series/v2/v2-0000-cover-letter.patch` and `results/v2-summary.md`.

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
git am ../../series/v2/v2-000{1,2,3,4}*.patch            # or v1, or none for stock
./configure --target-list=x86_64-softmmu --enable-spice \
    --enable-kvm \
    --extra-cflags="-I$(pwd)/../sysroot/sysroot/usr/include" \
    --extra-ldflags="-L$(pwd)/../sysroot/sysroot/usr/lib/x86_64-linux-gnu"
ninja -C build
```

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
