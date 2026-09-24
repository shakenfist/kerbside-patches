# linux: ignore_damage_clips DRM core fix

See [../README.md](../README.md) for the layout and disposition
policy (this patch is meant for upstream submission -- see its
"Per-project status" section for the process and what still needs
Michael's own action), and
[../../docs/plans/upstream-series.md](../../docs/plans/upstream-series.md)
for the plan.

`__drm_atomic_helper_plane_duplicate_state()` clears the other
per-commit plane state fields but not `ignore_damage_clips`, so once a
driver sets it (vmwgfx, and virtio-gpu on stable kernels), every later
commit on that plane inherits it and reports full-plane damage even
when nothing changed. `series/v1/0001-*.patch` clears it on
duplication like the other per-commit fields. Full rationale and the
functional test result are in the patch's own commit message.

## Running the rig

The guest is the same single-head KMS guest qemu's series uses
(`../qemu/rig/guest/init.c`, built by `../qemu/rig/guest/build.sh`);
this project does not have its own guest sources.

1. Build the guest (`../qemu/rig/guest/build.sh`) and a stock qemu
   (`../qemu/README.md`'s "Building qemu" section).
2. `rig/build-module.sh` rebuilds `drm_kms_helper.ko` against the
   guest kernel's own ABI, once stock and once with the patch applied
   (see its header for what is reconstructed vs a documented, unverified
   best guess).
3. Build two initrds with `../qemu/rig/guest/build.sh`'s
   `make_initrd`, one with each rebuilt module swapped in for the
   stock `drm_kms_helper.ko.xz`.
4. `rig/run-trace.sh <label> <initrd>` boots each (plus, for the "A"
   baseline, the guest's own unmodified initrd) under stock qemu and
   traces `virtio_gpu_cmd_res_flush` / `virtio_gpu_cmd_res_xfer_toh_2d`.

## Results

`results/README.md` has the A (stock)/B (rebuilt, unpatched)/C
(rebuilt, patched) comparison: the patched module reports the actual
480x360 damaged region instead of the whole 1280x800 plane on every
flush, matching the numbers in the patch's own commit message.
