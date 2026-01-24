# Debug CI Failure

Skill for debugging CI failures in kerbside-patches.

## Trigger
When user mentions: CI failure, CI failing, debug CI, CI run failed, GitHub Actions failed

## Process

1. **Get the CI run URL** from the user if not provided

2. **Fetch CI logs** using:
   ```bash
   gh run view <run-id> --log-failed --repo shakenfist/kerbside-patches
   ```

3. **Check for clingwrap log bundles** - CI workflows attach clingwrap long bundles as artifacts:
   ```bash
   gh run download <run-id> --repo shakenfist/kerbside-patches
   ```
   These log bundles contain comprehensive deployment logs and are often the most useful source of debugging information.

4. **Look for debug bundles** - user may have downloaded one to `~/Downloads/`

5. **Extract and analyze bundles**:
   ```bash
   unzip -d /tmp/debug-bundle "path/to/bundle.zip"
   ```
   Key files to examine:
   - `host_debug_logs/*/kolla_logs/*.log` - container logs
   - `host_debug_logs/*/ansible/*.yml` - ansible variables
   - `host_debug_logs/*/docker_info/` - container status

6. **Common failure patterns**:
   - **Template undefined variable**: Check if a variable is only defined for certain host groups
   - **Config missing**: Check if a previous ansible task failed, excluding hosts from later tasks
   - **Bootstrap container failures**: Often cascade from earlier config failures
   - **Host group mismatches**: Compare inventory groups between kerbside-api, kerbside-proxy, control, network, compute

7. **Check inventory files** at `etc/inventory-*` to understand host group relationships

8. **Examine patches** in `_patches/` to find where the failing component is configured

## Key Insight

When an Ansible task fails on a host, that host is excluded from all subsequent tasks in the playbook. This creates cascade failures where the root cause may be much earlier than the visible error.
