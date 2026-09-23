# Non-OpenStack upstream series

This tree carries patch series against upstreams outside OpenStack --
today, qemu and the Linux kernel -- that came out of building
[Kerbside](https://github.com/shakenfist/kerbside). See
[docs/plans/upstream-series.md](../docs/plans/upstream-series.md) for
the plan this was built from and its current phase.

## How this differs from `_patches/`

`_patches/` holds OpenStack patches as individually numbered files
(`patchNNN-description.patch`, tracked in each project's `ORDER` file)
that the build pipeline applies and rewraps at build time. A series
here is different in kind: it is developed and reviewed as a unit --
v1, v2 and so on, with a cover letter -- and mailed or carried whole,
the way `git format-patch` produces it and `git am` expects it. Splitting
it into numbered files would lose that shape for no benefit, since
none of the OpenStack build tooling should be touching these series
anyway.

That tooling finds projects with `find . -maxdepth 2 -name config.yaml`
(`_build/assemble-source.sh`, `_build/bump-source-shas.sh`,
`_build/test-patches-for-ci.sh`, `_build/imagebuild.sh`) or a
`*/config.yaml` glob (`tools/rebase-with-claude.sh`), and matches patch
hooks on `^_patches/`. Everything under `upstream/` sits one level
deeper than that discovery expects (`upstream/<project>/config.yaml`,
not `./<project>/config.yaml`), so none of it is visible to those
tools. Run `find . -maxdepth 2 -name config.yaml` after adding a new
project here to confirm the set of depth-2 projects has not changed.

## Layout

```
upstream/
  README.md            this file
  <project>/
    config.yaml         repo, branch, base_sha, disposition, ...
    series/vN/          git format-patch output, cover letter included
    rig/                guest sources, run scripts, analysis tools
    results/            summary tables (curated, small; not raw captures)
```

`series/` keeps every mailed version (v1, v2, ...), not just the
current one, as a record of how the series developed; `config.yaml`'s
`current_series` says which one is live. `results/` holds only
summary tables generated from a run, in the tens of KB at most -- raw
captures (qemu traces, pcaps, serial logs) run to hundreds of MB per
run and are not committed; the rig regenerates them. `rig/` scripts
resolve their own inputs relative to themselves (or via env var
overrides, documented in each script's header) rather than any
absolute or scratch path, so a checkout of this repository is
sufficient to know what a script needs.

## `config.yaml` keys

| Key | Meaning |
|-----|---------|
| `repo` | The canonical upstream repository URL. |
| `branch` | The branch this series is developed against. May be a `repo#branch` pair when it differs from `repo`, e.g. Linux's `drm-misc-next` living in a separate tree from Torvalds' `linux.git`. |
| `base_sha` | The full 40-character commit SHA the current series was last verified to `git am` cleanly onto. |
| `current_series` | Which `series/vN/` directory is the one to apply. |
| `disposition` | `carry` (kept downstream, not submitted upstream) or `submit` (mailed upstream; see the project's own notes below). |
| `also_applies_to` | (optional) other refs the series was confirmed to also apply to cleanly, each with the base SHA tested. Informational only. |

## Per-project status

### qemu -- carried downstream

qemu's [`docs/devel/code-provenance.rst`](https://www.qemu.org/docs/master/devel/code-provenance.html)
declines contributions believed to include or derive from AI-generated
content, and names Claude specifically. This series was developed with
Claude's help (see the `Co-Authored-By` trailers in
`qemu/series/v2/*.patch`), so it is not submitted upstream. It is
carried downstream (`disposition: carry`) while Michael gets more
familiar with qemu's ui/ code and can consider rewriting it by hand.
Whether that is ever worth doing also depends on kerbside's
son-of-SPICE spike (a Rust SPICE server on qemu's D-Bus display, which
would bypass `ui/spice-display.c` entirely for Kerbside's own use,
though Nova and oVirt deployments would still run in-qemu SPICE).

### linux -- to be submitted by Michael

The kernel's
[`Documentation/process/coding-assistants.rst`](https://docs.kernel.org/process/coding-assistants.html)
accepts AI-assisted patches carrying an `Assisted-by:` tag, provided a
human takes responsibility for the change with their own
`Signed-off-by`. `linux/series/v1/0001-*.patch` carries `Assisted-by:
LLM` and deliberately **no** `Signed-off-by` -- only Michael can add
that, by hand, immediately before sending, since it is his attestation
that he wrote or reviewed the patch and has the right to submit it
under the kernel's license. Recipients (dri-devel, the DRM misc
maintainers, the `Fixes:` commit's author, and the vmwgfx/virtio-gpu
maintainers) come from `get_maintainer.pl` and are recorded for
submission time, not baked into the patch. `drm-misc-next` already
carries a narrower, virtio-gpu-only fix for the same underlying bug
(`a9cc9905ddb7`, undated and without a stable tag); the cover note
should say so and let maintainers choose between the two.

## How to verify a series applies

```
git clone --shared <repo> /tmp/verify-<project>
cd /tmp/verify-<project>
git fetch origin <base_sha>
git checkout <base_sha>
git am /path/to/upstream/<project>/series/<current_series>/*.patch
# excluding the 0000-*cover-letter*.patch, which git am will otherwise
# choke on -- it has no diff.
```

`--shared` avoids re-fetching the whole history when a local mirror or
`/srv/src-reference` clone is available; drop it to work from a
throwaway clone instead. A clean `git am` of every patch in order is
the acceptance bar; phase 2 of
[docs/plans/upstream-series.md](../docs/plans/upstream-series.md) adds
a script and a scheduled check that automates this against the
project's moving branch.
