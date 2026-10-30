# spice: connect-crash fix and hardware encoding on any GPU

See [../README.md](../README.md) for the layout and disposition policy
(this series is meant for upstream submission, and Michael sends it; see
its "Per-project status" section). The work came out of
[Kerbside](https://github.com/shakenfist/kerbside)'s SPICE performance
plan, whose write-up is Kerbside's
`docs/performance/spice-server-patches.md`.

## What the series is

`series/v1/`, against spice-server master
`adeb620a04c43620416c5067b8bbfd1e4cf5eef5`:

| Patch | What it does |
|---|---|
| 0001 video-stream: skip streams a client has not been given | Fixes a crash on connect. A display client is on the channel's client list from `dcc_new()`, but gets the existing streams only at the end of `dcc_start()`. A `SPICE_MSGC_DISPLAY_PREFERRED_VIDEO_CODEC_TYPE` handled in that window stops every stream, queuing a `STREAM_CLIP` for a stream the client never had, whose destructor dereferences the agent's NULL `dcc`. |
| 0002 gstreamer-encoder: fall back to software when a hardware encoder fails | Probes a hardware element by taking it to READY, caches the result per codec, and rebuilds a pipeline with the software encoder when a hardware one cannot be built, started or encode, or cannot take the frame size. |
| 0003 gstreamer-encoder: probe hardware encoders on any GPU | Replaces the Intel-only udev check with a check for a DRM render node, so GStreamer's `va` plugin is tried on AMD and other VA-API GPUs. Drops the udev include. |

The cover letter, `series/v1/0000-cover-letter.patch`, summarises the
measurements for upstream. Each patch carries `Assisted-by: Claude Opus
5.5` and no `Signed-off-by`.

The results tables were made before the series was assembled, and number
the patches as they were drafted: their 0001 is v1's 0001, and their
0004 is v1's 0002 and 0003 together. Applied in order, v1 gives the same
source tree as drafts 0001 and 0004, so the measurements are of this
code.

## Results

`results/patch-matrix.md` has every table; its summary is the short
version.

- **The crash.** With `streaming-video=all` and a guest already
  streaming when the client connected, 0.15.2, 0.16.0 and master
  crashed in 20 of 20 connects. Master with 0001 crashed in 0 of 20.
  Only a client that sends its codec preference before, or in the same
  read as, `SPICE_MSGC_DISPLAY_INIT` triggers it: spice-gtk sends the
  preference only when an application asks it to, and remote-viewer
  never does.
- **Hardware encoding** (v2 qemu, client preferring H.264). vah264enc
  encoded every H.264 stream, with no fallbacks. qemu's CPU fell from
  0.15 to 0.16 cores to 0.13. At 80 ms / 10 Mbit with heavy noise,
  latency fell from 186 to 107 ms at p50 and from 1262 to 275 ms at
  p95; on the other links it did not change. qemu's peak RSS rose 56 to
  64 MiB, the VA driver loaded into it, but only once a GStreamer
  encoder is built: with MJPEG streams there was no change. This is
  kept under the rule that a change which costs somewhere is kept only
  if the cost is conditional.
- **Upgrading 0.15.2 to master** (with 0001, stock qemu, MJPEG). 9 of 15
  cells help, chiefly 0.03 to 0.04 fewer qemu cores and, with
  `streaming-video=all`, lower latency and a third or less of the
  display bandwidth; 2 cells regress by a margin just over their spread.

## Dropped

Kept unchanged in `dropped/` as a record. Neither made a measurable
difference.

- **`dropped/0002-*`, the stream trace ring from 8 to 64 entries.**
  Under stock qemu with MJPEG, 14 of 15 judged cells showed nothing,
  and every build created the same streams in every cell (21 per repeat
  with `all`, 19 with `filter`). The two flagged cells were single
  measures at the rig's false-positive rate: peak RSS -8 MiB (spread 6)
  and an idle p50 +5 ms (spread 4) where no drawable is streamable. The
  ring is searched only when many drawables leave the tree at once
  (`update_area`, `free_some`, memory pressure), so stock qemu's column
  slivers did not overflow it in a way that delayed a stream.
- **`dropped/0003-*`, `MAX_FPS` from 30 to 60.** On the v2 qemu at
  `max-refresh-rate=60`, spice-server's log showed the patch working
  (busy60 streams created at 60 fps rather than 30), but latency, qemu
  CPU, bandwidth and display messages per second did not change in any
  of 12 cells, with H.264 or MJPEG, and nothing regressed on the slow
  links. The client received 4 to 12 display messages a second whatever
  the ceiling, so spice-server's rate control, not the ceiling, sets the
  frame rate on these links.

## Building

`rig/build-spice.sh NAME REF [PATCH_DIR]` builds `libspice-server.so.1`
in a `debian:trixie` container with Debian's flags, runs spice-server's
`meson test`, and writes a `qemu-NAME.sh` wrapper that runs the host's
qemu with that library on `LD_LIBRARY_PATH`. Its header documents the
outputs and the environment overrides. To build the series:

```
mkdir /tmp/spice-v1
cp series/v1/000[1-9]-*.patch /tmp/spice-v1/
rig/build-spice.sh v1 adeb620a04c43620416c5067b8bbfd1e4cf5eef5 /tmp/spice-v1
```

(A `0000-*cover-letter*` patch in `PATCH_DIR` is skipped, so the series
directory itself also works.) Each patch was built in turn, 0001, then
0001 and 0002, then all three; `meson test` passed 25 of 25 each time.
The project's CI also runs `make syntax-check` from the autotools build,
which this script does not.

The assembled series also had one smoke run of each kind on the rig
below. On the v2 qemu with Ryll preferring H.264, spice-server's log
showed the 640x480 stream encoded by vah264enc. With `/dev/dri` hidden
from qemu, or holding only a render node bound to `/dev/null` with a
GStreamer registry that still listed the va elements, it was encoded by
x264enc. With the crash reproducer on stock qemu, 0 of 5 connects
crashed, each with 21 streams running before the client connected.

## Measuring

The measurements use Kerbside's shaped-link rig,
`tools/shaped-link/run-matrix.sh`: Ryll behind kerbside-proxy on a veth
link shaped by netem, a keydraw guest on virtio-vga, and latency from a
key press to the key box being drawn. Each spice-server build is a
`DISPLAYS` entry whose qemu binary is a wrapper from `build-spice.sh`,
so the builds are interleaved inside every pass of one run.
`rig/summarise-spice.py` turns the run directories into the tables in
`results/`; `results/patch-matrix.in.md` records the five runs, their
displays and the command that regenerates `patch-matrix.md`.

Two details matter for the encoder rows:

- Stock qemu's non-GL display cuts damage into 32-pixel columns,
  narrower than radeonsi's vah264enc accepts, so every stream falls back
  to x264enc. Those rows use the qemu built from
  `../qemu/series/v2/`, which sends each damaged region whole. It links
  spice-server dynamically, so `LD_LIBRARY_PATH` swaps the library
  under it in the same way.
- qemu cannot choose the codec, so the client does: those runs set
  `RYLL_ARGS=--experimental-h264`. Running qemu with
  `G_MESSAGES_DEBUG=Spice` puts spice-server's debug log in each case's
  `qemu.log`, which names the encoder each pipeline got and any fallback
  to software.

Delay the guest's activity past the connect (`ACTIVITY_DELAY=8`) when
measuring builds without 0001, or the crash ends the case. The crash
reproducer is the opposite: `ACTIVITY_DELAY=0` and
`streaming-video=all`, so streams exist before Ryll connects.

## How to send it

What spice-server's repository says about contributions:

- `README`'s "Communication" section names the spice-devel mailing list
  (`http://lists.freedesktop.org/mailman/listinfo/spice-devel`) "to
  communicate with the development team, or to post patches", and the
  GitLab project for bugs.
- `docs/how_to_release.md` sends release changes as a merge request on
  GitLab, and `.gitlab-ci.yml` runs builds, `meson test`, valgrind and
  `make syntax-check` on GitLab's CI.
- There is no CONTRIBUTING file and no written policy on AI-assisted
  patches. In practice, 292 of the last 300 commits on master carry a
  `Signed-off-by:` and 180 an `Acked-by:`.

So: review the patches, then add your `Signed-off-by` and send them as a
merge request on gitlab.freedesktop.org or to spice-devel:

```
git clone https://gitlab.freedesktop.org/spice/spice.git && cd spice
git checkout adeb620a04c43620416c5067b8bbfd1e4cf5eef5
git am --signoff /path/to/upstream/spice/series/v1/000[1-9]-*.patch
```

Rebase onto current master first if it has moved, rebuild with
`rig/build-spice.sh`, and update `base_sha`. For a merge request, the
cover letter's text is the description. For the list, regenerate the
patches with `git format-patch --cover-letter` and copy the cover
letter's text into the new one.

The trailers say `Assisted-by: Claude Opus 5.5`. spice-server's own
AI-assisted commits use a `Co-Authored-By: Claude <model>
<noreply@anthropic.com>` trailer instead (56 commits since 2026-02-09),
so convert it if that form is preferred. A v2 goes in `series/v2/` with
`[PATCH v2]` subjects and a note of the changes below each `---`.
