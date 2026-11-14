# `ignore_damage_clips` is sticky: the bug, and what sending it involves

Working notes for submitting `0001-drm-atomic-state-helper-Clear-ignore_damage_clips-on.patch`.
The patch's own commit message and `Notes:` section are the authoritative
technical record and are written for the mailing list; this file is the part
that does not belong in a commit message -- the decisions still open, what to
re-check before sending, and what each upstream policy requires.

Nothing here has been sent anywhere. Staged only.

## The bug

`__drm_atomic_helper_plane_duplicate_state()` copies a plane's state and then
clears the fields that describe one commit only -- `fb_damage_clips`,
`color_mgmt_changed`, the fence, the commit. `ignore_damage_clips` was added
beside `fb_damage_clips` as another per-commit hint, but it is never cleared,
so once a driver sets it, every later state duplicated from that one inherits
it.

Both drivers that set the flag set it only to `true`, and only when the
framebuffer attached to the plane changes. On most systems the first such
change is the initial modeset, so from that moment every commit on the plane
ignores its damage clips and `drm_atomic_helper_damage_iter_init()` reports
the whole plane as damaged -- whether or not the framebuffer changed again.

| | |
|---|---|
| Introduced by | `35ed38d58257` ("drm: Allow drivers to indicate the damage helpers to ignore damage clips"), v6.8 |
| Affected drivers | `vmwgfx` on every branch; `virtio-gpu` on any kernel without `a9cc9905ddb7` |
| Affected versions | v6.8 onwards, hence the patch's `Cc: stable # v6.8+` |
| Fix | one line in the helper, plus kernel-doc saying the flag is per-commit |

## Why we care

Kerbside relays SPICE from hypervisor guests, and a guest reporting full-plane
damage is the difference between sending a cursor-sized update and sending the
screen. On `virtio-gpu` the flag turns every `DRM_IOCTL_MODE_DIRTYFB` on a
dumb buffer into a full-plane transfer and `RESOURCE_FLUSH`: with a 1280x800
buffer and a client dirtying a 480x360 region per frame, qemu receives a
1280x800 flush every frame instead of a 480x360 one.

This is the kernel half of a wider display-path investigation; the qemu half
is the series in `../../../qemu/`, which is carried downstream rather than
submitted.

## What upstream already has, and why this patch is still worth sending

`drm-misc-next` carries `a9cc9905ddb7` ("drm/virtio: Recalculate
ignore_damage_clips on every update"), which recomputes the flag on every
atomic check in `virtio-gpu` alone. It has no `Fixes:` tag and no stable tag,
and it depends on `730f8554f35c`. So:

- `virtio-gpu` is fixed on `drm-misc-next` and will be fixed in mainline from
  whichever release takes those two commits;
- every stable kernel from v6.8 up to that release still has the sticky flag;
- `vmwgfx` is not fixed anywhere, because it still assigns the flag only on a
  framebuffer change;
- the next driver to use the flag inherits the same trap, which is what the
  kernel-doc half of this patch is for.

A one-line change in the shared helper covers all four points at once, and is
a safer thing to ask stable to take than a two-commit driver series.

## Evidence

Measured on a Debian 6.12.101 guest whose `virtgpu_plane.c` still sets the
flag only on framebuffer change, with `drm_kms_helper.ko` rebuilt against the
guest kernel's own ABI and `virtio-gpu.ko` left stock. A static test program
drew into a 1280x800 dumb buffer and called `DRM_IOCTL_MODE_DIRTYFB` on a
480x360 region at 30 fps, plus an 80x28 region once a second. Over about
twelve seconds of qemu `virtio_gpu_cmd_res_flush` traces:

| Module | Flushes | Shape |
|---|---|---|
| Unpatched | 380 | every one 1280x800+0+0 |
| Patched | 381 | 362 x 480x360+437+241, 13 x 80x28+24+740, 5 x 1280x800 (modeset and initial full dirty), 1 x 8x16 |

`../../rig/` rebuilds the module and runs the trace; `../../results/README.md`
has the full A (stock) / B (rebuilt, unpatched) / C (rebuilt, patched)
comparison. **`vmwgfx` was build-tested only** -- we have no vmwgfx hardware
or guest, and the patch should say so to the list, as its `Notes:` does.

## The three ways to send this

A decision for us, not something to settle in the patch as drafted:

1. **The core helper fix, as drafted.** Fixes every driver and stable branch
   at once, documents the flag's lifetime, and is one line for stable. The
   risk is scope: a maintainer may not want a core helper changed to fix two
   drivers, particularly with `virtio-gpu` already handled in `drm-misc-next`.
2. **A `vmwgfx`-only change**, mirroring what `a9cc9905ddb7` did for
   `virtio-gpu`, plus a separate request that the `drm-misc-next` pair be
   backported to stable. Narrower and likely the easier sell, but it leaves
   the helper's trap in place for the next user of the flag, and asks stable
   for two commits instead of one.
3. **Neither patch: just ask for the backport.** Cheapest, fixes only
   `virtio-gpu`, leaves `vmwgfx` broken. Mentioned for completeness; the
   `vmwgfx` exposure is real, so this is probably not enough.

The patch as drafted takes option 1 and names option 2 in its `Notes:`, so
the list can push back. That seems the right opening move: it is easier to
narrow a submission after review than to widen one.

## What the kernel asks of an AI-assisted patch

`Documentation/process/coding-assistants.rst` accepts AI-assisted patches that
carry an `Assisted-by:` tag, provided a human takes responsibility with their
own `Signed-off-by`.

The patch therefore carries `Assisted-by: LLM` and **deliberately no
`Signed-off-by`**. That is not an oversight and should not be "fixed" by any
later automated pass: the sign-off is Michael's attestation that he wrote or
reviewed the change and has the right to submit it under the kernel's licence,
so it is added by hand immediately before sending. Its absence is also what
stops a staged patch being mailable by accident.

Two things to confirm against the document itself at send time rather than
from these notes: the exact value the tag is expected to carry (whether a bare
`LLM` is right, or whether it should name the model and tool, as Open vSwitch
asks for), and whether anything about the policy has changed since the patch
was written.

## Before sending, re-check these

Everything in this section is a fact about other people's trees and will go
stale:

- Has `a9cc9905ddb7` picked up a stable tag, or been backported? If it has,
  argument 2 above weakens and `vmwgfx` becomes the whole case.
- Has `vmwgfx` been fixed separately? If so, only the kernel-doc and
  future-proofing half of this patch survives, which is probably not worth a
  submission on its own.
- Do the recorded base SHAs still apply? `config.yaml` has `drm-misc-next`
  `2302669bb4b5` plus the mainline, `drm-misc-fixes` and v6.12.101 refs the
  patch was confirmed to `git am` onto. Re-verify against current
  `drm-misc-next`, since that is where DRM core patches go.
- Re-run `scripts/get_maintainer.pl` on the patch for the current recipient
  set, rather than reusing one captured now. Expect dri-devel, the DRM misc
  maintainers, the `Fixes:` commit's author, and the `vmwgfx` and
  `virtio-gpu` maintainers.
- Does the measurement still reproduce on a current kernel? The numbers above
  are from 6.12.101, and a reviewer may reasonably ask about a recent one.

## Status

`config.yaml` records `disposition: submit`, which means this series is
*intended* for upstream and is not carried in any build. It has not been
sent. Submission is Michael's, by hand, and we work through it together.
