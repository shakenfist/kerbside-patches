# Editing patch files by hand

The patches in `_patches/` are the source artifact for this repository, so
they get edited directly rather than being regenerated from a working tree.
That means keeping each `@@` hunk header in step with the body you just
changed, which is the fiddly part.

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

## Linter issues

Kolla-Ansible runs `tox -elinters`, which includes ansible-lint. The common
one is `name[missing]` — every Ansible task needs a `name:`. Adding that line
changes the hunk's new-side count, so recount afterwards.

Verify a patch really applies with:

```bash
./_build/test-apply.sh --skip-tests kolla-ansible
```
