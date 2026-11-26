# `ignore_damage_clips` is sticky: the bug, and what sending it involves

Working notes for submitting `0001-drm-atomic-state-helper-Clear-ignore_damage_clips-on.patch`.
The patch's own commit message and `Notes:` section are the authoritative
technical record and are written for the mailing list; this file is the part
that does not belong in a commit message -- the decisions still open, what to
re-check before sending, and what each upstream policy requires.

Nothing here has been sent anywhere. Staged only.

Every fact here about somebody else's tree was last verified on **2026-11-26**
against mainline `4dce97c6c265` (v7.4-rc4+), `drm-misc-next` `479e381e77dc`,
`drm-misc-fixes` `1c986bc5918c` and all sixteen live `linux-*.y` stable
branches, plus a lore.kernel.org search for pending work. See "Before sending,
re-check these" for each question and how to ask it again.

## The bug

`__drm_atomic_helper_plane_duplicate_state()` copies a plane's state and then
clears the fields that describe one commit only -- `fb_damage_clips`,
`color_mgmt_changed`, the fence, the commit. `ignore_damage_clips` was added
beside `fb_damage_clips` as another per-commit hint, but it is never cleared,
so once a driver sets it, every later state duplicated from that one inherits
it.

| | |
|---|---|
| Introduced by | `35ed38d58257` ("drm: Allow drivers to indicate the damage helpers to ignore damage clips"), v6.8 |
| Affected drivers | `vmwgfx` on every branch, including current mainline; `virtio-gpu` before v7.4, hence on every stable branch |
| Affected versions | v6.8 onwards, hence the patch's `Cc: stable # v6.8+` |
| Fix | one line in the helper, plus kernel-doc saying the flag is per-commit |

The two affected drivers set the flag only to `true`, and only when the
framebuffer attached to the plane changes. On most systems the first such
change is the initial modeset, so from that moment every commit on the plane
ignores its damage clips and `drm_atomic_helper_damage_iter_init()` reports
the whole plane as damaged -- whether or not the framebuffer changed again.

Four other drivers touch the flag, and none of them is hurt by the
stickiness, which matters because the patch's claim that no driver relies on
the flag persisting has to hold for all of them:

| Driver | Use | Exposure |
|---|---|---|
| `gud` | sets `true` from the per-device `GUD_DISPLAY_FLAG_FULL_UPDATE` flag, on every check | none: the device flag does not change, so the value is the same every commit |
| `virtio-gpu` (v7.4+) | assigns `fb changed && bo->dumb` on every check | none: reassigned every commit |
| `drm/ms91xx` (on the list, v2 2026-11-04) | assigns `fb changed \|\| needs modeset` on every check | none, same reason -- a new driver that got it right |
| `amdgpu` | honours the flag (`ac11060c6d49`, backported to 6.6/6.12/6.18/7.1) | none: it never sets it |

An i915 patch to honour the flag was resent to the list on 2026-11-24. More
consumers honouring the flag is the main thing that has changed since this
note was written: the flag's lifetime matters to more code than it did, which
is the argument for the kernel-doc half of the patch.

## Why we care

Kerbside relays SPICE from hypervisor guests, and a guest reporting full-plane
damage is the difference between sending a cursor-sized update and sending the
screen. On `virtio-gpu` the flag turns every `DRM_IOCTL_MODE_DIRTYFB` on a
dumb buffer into a full-plane transfer and `RESOURCE_FLUSH`: with a 1280x800
buffer and a client dirtying a 480x360 region per frame, qemu receives a
1280x800 flush every frame instead of a 480x360 one. That is what the guests
we actually relay from -- stable kernels -- still do.

This is the kernel half of a wider display-path investigation; the qemu half
is the series in `../../../qemu/`, which is carried downstream rather than
submitted.

## What upstream already has, and why this patch is still worth sending

`a9cc9905ddb7` ("drm/virtio: Recalculate ignore_damage_clips on every
update") and its prerequisite `730f8554f35c` landed in **v7.4-rc1**. They
recompute the flag on every atomic check in `virtio-gpu` alone, and carry no
`Fixes:` tag and no stable tag. So:

- `virtio-gpu` is fixed in mainline from v7.4;
- every stable branch still has the sticky flag -- 6.12.y (v6.12.119),
  6.18.y, 7.2.y and 7.3.y (v7.3.6) all still assign the flag inside
  `if (old_plane_state->fb != new_plane_state->fb)`, and nothing resembling a
  backport of either commit has been posted for stable review;
- `vmwgfx` is not fixed anywhere, including current mainline and
  `drm-misc-next`, and nothing is pending for it on the list, so it is now the
  whole of the *mainline* case for this patch;
- the next driver to use the flag inherits the same trap, which is what the
  kernel-doc half of this patch is for -- though the last two drivers to use
  it (`gud`, `drm/ms91xx`) both wrote code that is immune to it.

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
comparison.

Two limits on this evidence, both of which the submission should state
plainly:

- **`vmwgfx` is build-tested only** -- we have no vmwgfx hardware or guest,
  as the patch's `Notes:` says.
- **The virtio-gpu demonstration cannot be reproduced on current mainline**,
  because v7.4 reassigns the flag in the driver on every check. It reproduces
  on any stable branch, 6.12.y through 7.3.y, which is where it matters to us;
  on v7.4+ the remaining exposure is vmwgfx, which we cannot measure. A
  reviewer who asks "does this still happen on a recent kernel?" needs that
  answer, not the 6.12 numbers alone.

The patch was re-verified on 2026-11-26 against `drm-misc-next`: `git am`
clean, `allmodconfig` x86-64 build of `drivers/gpu/drm/` warning-free (so
`vmwgfx` still compiles with the kernel-doc change), `scripts/kernel-doc` clean
on `include/drm/drm_plane.h`, and `checkpatch.pl --strict` clean apart from the
deliberately absent `Signed-off-by`.

## The three ways to send this

A decision for us, not something to settle in the patch as drafted:

1. **The core helper fix, as drafted.** Fixes every driver and stable branch
   at once, documents the flag's lifetime, and is one line for stable. The
   risk is scope: a maintainer may not want a core helper changed for one
   remaining mainline driver, now that `virtio-gpu` is fixed in v7.4.
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

**The `Assisted-by:` tag no longer exists.** `1e035297ffda` ("docs: remove
Assisted-by references", v7.4-rc1, 2026-10-14) removed it from
`coding-assistants.rst` and `submitting-patches.rst`, dropped its handling
from `checkpatch.pl`, and removed it from the translations. The reasoning in
that commit: about 10% of 7.3 commits carried the tag while actual LLM use was
far wider, so its presence or absence told a maintainer nothing. The Maintainer
Summit's view was that how a patch was made belongs in the **cover letter**,
where it can be described, rather than in a trailer.

What remains in force:

- `Documentation/process/coding-assistants.rst` still requires a human
  `Signed-off-by` and full human responsibility for the patch.
- `Documentation/process/generated-content.rst` (unchanged) asks for
  transparency in cover letters and changelogs: which tools, the prompts or a
  summary of them, which parts of the content the tool produced, and how the
  result was tested. It also states plainly that maintainers may reject
  tool-generated work outright or demand extra scrutiny.

The patch therefore carries **no `Assisted-by:` trailer**, and the disclosure
lives in the `Notes:` block below the `---`, which reviewers see and the git
history does not.

That reverses the decision recorded in [`../../../README.md`](../../../README.md)'s
"Per-project status", which saw the same removal commit, read the tag as
surviving convention, and kept `Assisted-by: LLM`. Two facts decided it the
other way on 2026-11-26, and both are worth re-checking, because the call is
close:

- `checkpatch.pl --strict` now reports `WARNING: Non-standard signature:
  Assisted-by:` **and** `ERROR: Unrecognized email address: 'LLM'` for the
  tag, because `1e035297ffda` removed its special handling. A patch that
  trips a checkpatch error invites a bot reply before a human reads it.
- The convention is real but shrinking and no longer uniform: 427 of 4680
  non-merge mainline commits since 2026-10-20 carry the tag (240 of them as
  exactly `Assisted-by: LLM`, the rest naming a model or tool), and 34 of 517
  `drivers/gpu/drm` commits in `drm-misc-next`. It is a minority practice
  with no documented form, against a documented request for prose disclosure.

If the tag goes back in, put it back as `Assisted-by: LLM` -- still the
commonest form -- and expect the two checkpatch complaints. This is a single patch with no cover letter; if it grows one, that
paragraph moves there. **Read that paragraph before sending** -- it says "I
reviewed the change and the measurement", which is Michael's statement to
make, not ours.

The patch also **deliberately carries no `Signed-off-by`**. That is not an
oversight and should not be "fixed" by any later automated pass: the sign-off
is Michael's attestation that he wrote or reviewed the change and has the
right to submit it under the kernel's licence, so it is added by hand
immediately before sending. Its absence is also what stops a staged patch
being mailable by accident, and it is the only thing `checkpatch.pl --strict`
now complains about.

## Before sending, re-check these

Everything in this section is a fact about other people's trees and will go
stale. Each answer below is as of **2026-11-26**, with the check that produced
it.

- **Has `a9cc9905ddb7` picked up a stable tag, or been backported?** No. It
  landed in v7.4-rc1 with no `Fixes:` and no stable tag, no stable-review
  posting exists for it or for `730f8554f35c`, and no `linux-*.y` branch
  contains either. Re-check with
  `git show $b:drivers/gpu/drm/virtio/virtgpu_plane.c | grep -B2 ignore_damage_clips`
  for each live stable branch, plus a lore search for the two subjects.
- **Has `vmwgfx` been fixed separately?** No.
  `vmwgfx_kms.c`'s `if (old_fb != new_fb) new_state->ignore_damage_clips = true;`
  is identical in mainline, `drm-misc-next` and `drm-misc-fixes`, and a lore
  search for `ignore_damage_clips AND vmwgfx` turns up no patch. If this
  changes, only the kernel-doc and future-proofing half of this patch
  survives, which is probably not worth a submission on its own.
- **Do the recorded base SHAs still apply?** Yes. `config.yaml` now records
  `drm-misc-next` `479e381e77dc` plus `drm-misc-fixes` `1c986bc5918c`,
  mainline `4dce97c6c265` and stable `v6.12.119`; the patch `git am`s onto all
  four without fuzz. The original base `2302669bb4b5` is still an ancestor of
  `drm-misc-next`, so that branch has not been rewound. Re-verify against
  current `drm-misc-next`, since that is where DRM core patches go.
- **Who are the recipients?** `scripts/get_maintainer.pl` on the patch against
  `drm-misc-next` gives: Maarten Lankhorst, Maxime Ripard, Thomas Zimmermann,
  David Airlie `<airlied@gmail.com>`, Simona Vetter, Javier Martinez Canillas
  (author of the `Fixes:` commit), Zack Rusin (vmwgfx, via `blamed_fixes`),
  `dri-devel@lists.freedesktop.org` and `linux-kernel@vger.kernel.org`. It
  does *not* produce the virtio-gpu maintainers, because the patch touches no
  virtio file, nor `stable@vger.kernel.org`, which the `Cc:` trailer covers.
  Worth adding by hand: Gerd Hoffmann, Dmitry Osipenko, David Airlie
  `<airlied@redhat.com>`, `virtualization@lists.linux.dev`, and the authors of
  the virtio-gpu fix (Christian Hergert, Lyude Paul). Re-run it rather than
  reusing this list.
- **Does the measurement still reproduce on a current kernel?** On stable,
  yes -- the driver code that produces it is unchanged on every live branch.
  On current mainline, no: see the second limit under "Evidence". If a fresh
  number is wanted, run the rig against a current 6.12.y or 7.3.y guest
  rather than a current mainline one.
- **Has the kernel's policy on AI assistance changed again?** It changed once
  already (see the section above). `coding-assistants.rst`,
  `generated-content.rst` and `submitting-patches.rst` are the files to read
  at send time.

## Status

`config.yaml` records `disposition: submit`, which means this series is
*intended* for upstream and is not carried in any build. It has not been
sent. Submission is Michael's, by hand, and we work through it together.
