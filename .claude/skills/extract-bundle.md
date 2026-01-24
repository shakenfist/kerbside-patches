# Extract Debug Bundle

Skill for extracting and analyzing CI debug bundles.

## Trigger
When user mentions: debug bundle, extract bundle, analyze bundle

## Process

1. **Locate the bundle** - typically in `~/Downloads/` with names like:
   - `bundle-master-debian12-multi*.zip`
   - `bundle-flamingo-debian12-single*.zip`

2. **Extract to temp directory**:
   ```bash
   BUNDLE_DIR=$(mktemp -d)
   unzip -d "$BUNDLE_DIR" "path/to/bundle.zip"
   ```

3. **Key directories in bundle**:
   ```
   host_debug_logs/
     <hostname>/
       kolla_logs/          # Container logs (*.log)
       ansible/             # Ansible facts and vars (*.yml)
       docker_info/         # Container status
       journal/             # systemd journal excerpts
   ```

4. **Priority files to check**:
   - `kolla_logs/kolla-ansible.log` - main deployment log
   - `kolla_logs/*-bootstrap*.log` - bootstrap container logs
   - `ansible/vars_and_facts.yml` - variable state at failure
   - `docker_info/docker_ps.txt` - running containers

5. **Search for errors**:
   ```bash
   grep -r "ERROR\|FAILED\|undefined" "$BUNDLE_DIR/host_debug_logs/"
   ```

## Cleanup

Remember to remove temp directory when done:
```bash
rm -rf "$BUNDLE_DIR"
```
