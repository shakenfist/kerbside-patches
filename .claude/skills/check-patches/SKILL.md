---
name: check-patches
description: Validate that the patches in _patches/ still apply and are consistent across releases, using test-apply.sh and the CI patch testing scripts. Use when asked to check, validate, or test patches, or when a patch fails to apply.
---

# Checking Patches

Validates the patches in `_patches/` against the upstream trees named in each
project directory's `config.yaml`.

## Patch and project layout

- Patches live in `_patches/`, named
  `patch<NNN>-<description>.patch`, with an optional
  `patch<NNN>-<description>.patch-message` holding a commit message override.
- Release-specific copies carry the codename:
  `patch<NNN>-<project>-<codename>-<description>.patch`.
- Codenames come from `_build/release-names.yaml`: 2024.1 caracal,
  2024.2 dalmatian, 2025.1 epoxy, 2025.2 flamingo, 2026.1 gazpacho, and
  master.
- Project directories (`kolla/`, `kolla-ansible/`, `kolla-ansible-2025.1/`
  and so on) each hold a `config.yaml` (upstream repo, branch, `source_sha`,
  `depends_on`) and an `ORDER` file listing the patches to apply in sequence.

## Validating

Do not hand-roll `patch --dry-run`. The repository has tooling that clones
the right upstream at the right SHA and applies the ORDER file:

```bash
# Fast: only test that patches apply
./_build/test-apply.sh --skip-tests kolla-ansible

# Full: also run the upstream test suites, which catches linter and style
# breakage that patch application alone will not
./_build/test-apply.sh kolla-ansible

# Only test one patch, for when CI has already named the failure
./_build/test-apply.sh --test-patch patch115 kolla-ansible

# All projects, JSON output
./_build/test-patches-for-ci.sh
cat patch-results.json | ./_build/extract-patch-failures.py
```

## Before changing a failing patch

A patch may be shared by several project directories. Check before editing:

```bash
./_build/find-patch-usage.py _patches/patch008-use-routable-ip.patch
```

- Used by one project: edit the patch in place.
- Shared, and only one release needs different code: make a
  release-specific copy rather than breaking the other releases. Get the
  next free number with `./_build/get-next-patch-number.py` (it also checks
  open PRs), name it with the codename, and update only the failing
  project's ORDER file.

`./_build/analyze-shared-patches.py patch-test-results.json` does this
analysis over a whole failing run and recommends a strategy per patch.

## Editing patch files directly

The diff headers must stay in sync with the content. `@@ -0,0 +1,54 @@` means
line 0 count 0 in the original, line 1 count 54 in the new file: adding N
lines to a hunk means incrementing the second count by N, in the same edit
as the content change.

## Common issues

- **Ansible `name[missing]`**: every task needs a `name:`. Kolla-Ansible
  runs `tox -elinters`, which includes ansible-lint.
- **Variable scope**: `set_fact` only defines a variable on the hosts the
  task actually ran on.
- **Host group mismatches**: a template rendered on a host that never ran
  the `set_fact` will fail on an undefined variable.
- **Release drift**: when a patch exists for several releases, a fix to one
  usually belongs in the others too. Check the sibling patches.
