# Editing patch files by hand

The patches in `_patches/` are the source artifact for this repository, so
they get edited directly rather than being regenerated from a working tree.
That means keeping each `@@` hunk header in step with the body you just
changed, which is the fiddly part, and keeping the commit message embedded in
the patch correct, because nothing in the build reads it.

## The hunk header

```
@@ -86,11 +86,12 @@ jpeg_compression = {{ nova_spice_jpeg_compression }}
     │   │    │  │
     │   │    │  └── number of lines this hunk occupies in the new file
     │   │    └───── first line number in the new file
     │   └────────── number of lines this hunk occupies in the old file
     └────────────── first line number in the old file
```

The old count is context lines plus `-` lines. The new count is context lines
plus `+` lines. Text after the closing `@@` is a function-name hint that git
adds for readability; it is not checked.

Two further rules matter in practice:

- A count of exactly 1 is elided, so `-1,1` is written `-1`.
- Creating a file gives `-0,0 +1,N`; deleting one gives `-1,N +0,0`.

## Use the tool

`tools/recount-patch.py` recomputes every header in a patch from its body:

```bash
# See what the corrected patch would look like
tools/recount-patch.py _patches/patch008-use-routable-ip-for-spice-consoles.patch

# Fix the files in place
tools/recount-patch.py --in-place _patches/patch1*.patch

# Fail if any header is stale (this is what pre-commit runs)
tools/recount-patch.py --check _patches/*.patch
```

A pre-commit hook runs `--check` over `_patches/`, so a stale header is caught
before it reaches CI. `tools/test-recount-patch.py` is the test suite; it
builds throwaway git repositories, has git generate canonical patches, damages
them, and requires the recounted result either to match git's own output byte
for byte or to apply cleanly.

## Depends-On footers

A patch's commit message may carry a `Depends-On:` footer pointing at a change
on review.opendev.org that has to land first:

```
Depends-On: https://review.opendev.org/c/openstack/kolla-ansible/+/1005458
```

Nothing in the build reads it. It is there for Zuul, when the patch is pushed
upstream, and for whoever reads the patch next -- which means a wrong link is
invisible here. It only surfaces later as a gate job that waits on the wrong
thing, or a reviewer following the link somewhere unrelated.

`tools/check-depends-on.py` validates them:

```bash
# Every patch, including the Gerrit lookups
tools/check-depends-on.py

# Just the patches one project applies, and the projects it depends on
tools/check-depends-on.py kolla-ansible

# No network (this is what pre-commit runs)
tools/check-depends-on.py --offline _patches/patch182-*.patch
```

`_build/test-apply.sh` runs it over the projects it is about to test, before
cloning anything, so a bad link fails in seconds rather than after a tox run.
`--skip-depends-on-check` turns that off.

The check that earns its keep is **abandoned**. Abandoning a change and
re-uploading it -- the normal response to a change that has gone stale -- keeps
the subject but issues a new change number *and* a new Change-Id, so a link
copied before the re-upload still resolves, still looks right, and can never be
satisfied. The tool reports the status and then finds the open change with the
same subject in the same project, which is almost always the one the footer
meant:

```
_patches/patch182-kolla-ansible-master-check-logs-json.patch:17: Depends-On
https://review.opendev.org/c/openstack/kolla-ansible/+/1005370 is ABANDONED
    "Collect etcd logs with Fluentd." was abandoned, so this dependency can
    never be satisfied.
    The open change with that subject is 1005458:
        https://review.opendev.org/c/openstack/kolla-ansible/+/1005458
```

It also rejects a value that is not a change URL (Zuul deprecated the bare
Change-Id form), a URL whose project is not the change's real project, a change
number that does not exist, and a patch depending on its own change.

## The two copies of a commit message

A patch's commit message lives in the patch itself, indented four spaces
between the header and the first `diff --git`. `tools/extract-commit-message`
writes it out to `<patch>-message` at apply time, because `git commit --file`
needs a file. Many of those generated files are also committed.

That makes two copies of every footer, and they drift: fixing a `Depends-On` in
the patch and forgetting the `-message` beside it leaves a stale link in the
file a human is most likely to read. The offline half of `check-depends-on.py`
compares them, so a committed `-message` has to match the patch it came from.
Regenerate it, or delete it -- the build recreates it either way:

```bash
python3 tools/extract-commit-message _patches/patchNNN-whatever.patch
```

Both tools parse the message through `tools/patch_message.py`, so they cannot
disagree about where the message starts and ends.

## Why getting this wrong is worse than it looks

A header that is **too large** makes git reject the patch, which is loud and
obvious:

```
error: corrupt patch at line 25
```

A header that is **too small** is the dangerous one. git reads exactly as many
lines as the header declares and ignores whatever follows, so the hunk is
silently truncated and the patch applies with the wrong content. Nothing warns
you. This is why the pre-commit check is worth having even though a broken
patch usually fails loudly.

Two patches in this repository were found in this state when the tool was
written: `patch137-horizon-requires-setuptools.patch` was outright corrupt and
`patch097-kolla-ansible-fixed-proxy-cert.patch` had a trailing context line git
was ignoring. Neither was referenced by an `ORDER` file, so neither was
breaking a build.

## The one ambiguous case

A blank line sitting between the last real body line and the next
`diff --git` may or may not belong to the hunk, and the body alone cannot say
— git resolves it by consuming exactly as many lines as the header declares.
patchutils' `recountdiff` guesses wrong here on several of this repository's
patches.

`recount-patch.py` breaks the tie using the header it was given: if ignoring
some trailing blanks makes both sides agree with the declared counts, that was
the original intent and it is preserved. Once you edit the body no
interpretation matches, and it falls back to counting every line — the safe
direction, since an over-large count fails loudly rather than truncating.

## Relationship to patchutils

`patchutils` solves the same problem and is worth knowing about:

```bash
recountdiff broken.patch > fixed.patch   # recompute counts and offsets
editdiff foo.patch                       # $EDITOR, with fixup on save
rediff orig.patch edited.patch           # uses the original as a reference
```

`rediff` can do something `recount-patch.py` cannot: because it sees the
patch before and after your edit, it can tell that you deleted a *context*
line, which is invisible to anything looking only at the final text.

We ship our own recounter anyway because it scores better on this
repository's corpus (175/175 round-tripping unchanged against
`recountdiff`'s 164/175, mostly the ambiguity above) and because it keeps
CI free of an apt dependency.

## Release-specific copies

The same patch is often listed in several `ORDER` files, for example
`kolla-ansible`, `kolla-ansible-2024.1` and `kolla-ansible-2025.1`.
`_build/find-patch-usage.py <patch>` lists them. When a patch fails in
one release, there are two ways to fix it:

* **Modify in place** when the patch is used by only one project, or
  every project using it needs the same change.
* **Create a copy** when only one release needs different content.
  Give the copy the next free number (`_build/get-next-patch-number.py`,
  which also checks open PRs), name it for the release's codename, point
  the failing project's `ORDER` file at it, and leave the original alone
  for the other releases.

Copies are named `patch{number:03d}-{project}-{codename}-{description}.patch`,
for example `patch118-kolla-ansible-epoxy-compressed-zstd.patch`. The
codenames come from `_build/release-names.yaml` (2025.1 is `epoxy`,
2026.1 is `gazpacho`, and `master` stays `master`).
`_build/analyze-shared-patches.py <results.json>` makes this decision
for a set of failures from `test-patches-for-ci.sh`, and suggests names.

## Linter issues

Kolla-Ansible runs `tox -elinters`, which includes ansible-lint. The common
one is `name[missing]` — every Ansible task needs a `name:`. Adding that line
changes the hunk's new-side count, so recount afterwards.

Verify a patch really applies with:

```bash
./_build/test-apply.sh --skip-tests kolla-ansible
```
