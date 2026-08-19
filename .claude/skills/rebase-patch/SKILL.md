---
name: rebase-patch
description: Rebase a patch in _patches/ onto a newer upstream SHA when it stops applying, including deciding between editing in place and making a release-specific copy. Use when a patch fails to apply after an upstream bump or during the daily rebase.
---

# Rebasing a Patch

Patches are pinned to upstream by `source_sha` in each project directory's
`config.yaml`. When upstream moves, patches stop applying and the daily
rebase workflow reports it.

## Releases

Project directories use numeric versions (`kolla-ansible-2025.1`), patch
filenames use codenames. From `_build/release-names.yaml`: 2024.1 caracal,
2024.2 dalmatian, 2025.1 epoxy, 2025.2 flamingo, 2026.1 gazpacho, and
master.

## Process

1. **Reproduce the failure** and see the reject context:

   ```bash
   ./_build/test-apply.sh --test-patch patch115 --skip-tests kolla-ansible
   ```

2. **Decide the strategy before editing.** A patch may be shared by several
   project directories:

   ```bash
   ./_build/find-patch-usage.py _patches/patch115-....patch
   ```

   Used by one project, edit in place. Shared but only one release needs
   different code, make a release-specific copy: take the next number from
   `./_build/get-next-patch-number.py`, name it
   `patch<NNN>-<project>-<codename>-<description>.patch`, and update only
   the failing project's ORDER file. Leave the original alone so the other
   releases keep working.

3. **Look at what upstream changed** in the files the patch touches. The
   patched tree is under `src/<project>/` after an assemble, and is a real
   git checkout, so `git log` on the affected file explains the conflict.

4. **Edit the patch file.** Keep hunk headers in sync with content in the
   same edit: in `@@ -0,0 +1,54 @@` the second count is the new file's line
   count, so adding N lines means incrementing it by N.

5. **Re-test with the upstream suites**, not just application. Style and
   lint breakage only shows up there:

   ```bash
   ./_build/test-apply.sh kolla-ansible
   ```

6. **Check the sibling patches.** A fix for one release is usually needed
   in the others.

7. **Push to a branch and let CI confirm.** Never open the pull request
   without asking.

## Automation

`_build/rebase-with-claude.sh` drives this whole flow, and is what the
daily rebase workflow runs:

```bash
./_build/rebase-with-claude.sh kolla-ansible-2025.1   # test one project
./_build/rebase-with-claude.sh --bump-shas            # full daily rebase
./_build/rebase-with-claude.sh --no-claude            # test and report only
```
