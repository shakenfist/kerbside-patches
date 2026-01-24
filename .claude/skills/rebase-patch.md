# Rebase Patch

Skill for rebasing patches to new upstream releases.

## Trigger
When user mentions: rebase patch, update patch, new release, upstream update

## Context

Kerbside-patches maintains patches against multiple kolla-ansible releases:
- **master** - current development
- **flamingo** - OpenStack 2024.2
- **epoxy** - OpenStack 2024.1

## Process

1. **Identify the patch to rebase** from `_patches/` directory

2. **Understand the patch content**:
   ```bash
   # View patch summary
   head -100 _patches/patch<NNN>-<component>-<release>-compressed.patch
   ```

3. **Check for corresponding patches** in other releases - similar patches usually exist for all supported releases

4. **Apply the patch to a fresh checkout** to understand what it modifies:
   ```bash
   # In a temp kolla-ansible checkout
   git checkout <release-branch>
   patch -p1 --dry-run < path/to/patch.patch
   ```

5. **Identify conflicts** and resolve by:
   - Checking upstream changes that affect patched files
   - Adjusting line numbers and context
   - Updating any changed function signatures or APIs

6. **Regenerate the compressed patch**:
   - Apply changes to kolla-ansible checkout
   - Use `git diff` to generate new patch
   - Compress using the project's patch compression method

7. **Test with CI** - push to a PR branch and verify CI passes

## Consistency Check

When modifying one release's patch, check if the same change is needed in:
- `patch*-master-*.patch`
- `patch*-flamingo-*.patch`
- `patch*-epoxy-*.patch`
