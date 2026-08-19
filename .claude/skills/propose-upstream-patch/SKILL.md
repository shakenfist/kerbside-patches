---
name: propose-upstream-patch
description: Author and submit a change to an upstream OpenStack project (Kolla, Kolla-Ansible, Nova) via review.opendev.org. Use when a fix belongs upstream rather than in _patches/, when writing the commit message for a Gerrit change, or before pushing to Gerrit.
---

# Proposing a Patch Upstream

Use this when a fix belongs in an upstream OpenStack project rather than as
a local patch in `_patches/`. Typical cases: a CI or packaging fix that
affects everyone, a bug in a role that we happen to hit first, or a
capability we need that upstream would also want.

Read [docs/tactics.md](../../../docs/tactics.md) for the full rationale and
the data behind these rules. This skill is the procedure.

## Decide: upstream or local patch?

Upstream if the change stands on its own merits with no mention of
Kerbside. Local `_patches/` entry if it only makes sense with Kerbside
present, or if we need it before upstream could plausibly merge it.

If it is genuinely upstream-worthy, submit it upstream **and** carry it in
`_patches/` only if we cannot wait for the merge. Do not carry a permanent
local patch for something upstream would accept.

## The rules that cause the most friction

These two are the ones we keep getting review comments about. Get them
right the first time.

### Commit message: two short paragraphs, hard stop

- Subject 50 characters ideal, 72 maximum.
- Body is **at most two paragraphs**, **each at most 4 lines** wrapped at
  72 characters.
  - Paragraph 1: what is broken, and what this change does about it.
  - Paragraph 2 (optional): why this approach, not the obvious alternative.
- Footers (`Closes-Bug`, `Change-Id`, `Depends-On`, `Signed-off-by`,
  `Co-Authored-By`) do not count toward the two paragraphs.
- No bullet lists of hunks, no multi-line logs or tracebacks, no
  "background" section, no links to Kerbside repositories. A single log
  line that *is* the symptom is fine.
- Evidence and reasoning a reviewer might want goes in a **Gerrit comment
  on the change**, not in the commit message.

### Comments in the code: minimal without being negligent

- Default to no comment. Let the task `name:`, the variable name, or the
  function name carry the meaning.
- Comment only what the code cannot express: a workaround for a named
  upstream or distro bug (with a bug number or URL), a non-obvious ordering
  or timing constraint, or the provenance of a magic value.
- Never restate the following line.
- In Ansible, sharpen the task `name:` rather than adding a comment. The
  name is user-visible during a deploy; a comment is not.
- If the justification needs more than about two lines, it belongs in a
  release note or `doc/source/`, not in a comment.
- Nothing Kerbside-specific goes upstream, in code or in comments.

## Procedure

1. **Size the change.** Small patches (under 50 lines) merge in about a day
   and need one revision cycle; large ones need dozens. Split before
   submitting, not after a reviewer asks.
2. **Write the change** in a clone of the upstream project, not in
   `_patches/`. Generate the patch file for `_patches/` afterwards if we
   need to carry it.
3. **Add a release note** (`reno new <slug>`) for anything user-visible.
4. **Add a bug reference** (`Closes-Bug: #NNNNNNN`) for anything that is
   not a typo fix.
5. **Run the project's own linters** before pushing -- `tox -elinters` for
   Kolla-Ansible, `tox -epep8` for Nova.
6. **Run the pre-push linter** from this repository:

   ```bash
   cd /path/to/kolla-ansible
   /path/to/kerbside-patches/tools/gerrit-pre-push-lint --commit
   ```

   Fix every ERROR and WARNING. The commit body check enforces the two
   paragraph rule above.
7. **Time the push.** Thursday morning European time (roughly 08:00-10:00
   UTC) gets the fastest first review. Avoid Friday afternoon and
   weekends. See docs/tactics.md for the reviewer timezone table.
8. **Post supporting evidence as a Gerrit comment** immediately after
   pushing -- the CI log excerpt, the reproduction, the reasoning that did
   not fit in the two paragraphs.
9. **Respond fast.** Median time to first review is 1.6 hours; same-day
   iteration is normal and builds reviewer trust.

## Querying review state

See [docs/gerrit-api.md](../../../docs/gerrit-api.md) for the SSH and REST
APIs, including how to fetch inline comments on a change.
