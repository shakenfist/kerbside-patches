# Check Patches

Skill for validating patches in the kerbside-patches repository.

## Trigger
When user mentions: check patches, validate patches, patch syntax, patch consistency

## Patch Structure

Patches are in `_patches/` directory with naming convention:
- `patch<NNN>-<component>-<release>-compressed.patch`
- Releases: master, flamingo (2024.2), epoxy (2024.1)

## Validation Steps

1. **Check patch syntax**:
   ```bash
   for patch in _patches/*.patch; do
     echo "Checking $patch"
     patch --dry-run -p1 < "$patch" 2>&1 | head -20
   done
   ```

2. **Verify consistency across releases** - similar patches should have matching logic across master/flamingo/epoxy versions

3. **Check Jinja2 template syntax** in any templates added by patches:
   - Variable definitions match usage
   - Filters are valid
   - Conditionals are properly structured

4. **Verify Ansible task syntax** in patch content:
   - `when:` conditions reference valid groups
   - `set_fact` tasks run on correct host groups
   - Template rendering happens on hosts that have the required variables

## Common Issues

- **Variable scope**: `set_fact` only defines variables on hosts where the task runs
- **Host group mismatches**: Templates rendered on different hosts than where variables are defined
- **Missing conditionals**: Tasks that should be conditional on service enablement
