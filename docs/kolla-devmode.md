# Kerbside Development Mode (devmode) for Kolla-Ansible

This document explains how to enable and use development mode for rapid
iteration on the kerbside proxy in a deployed Kolla-Ansible environment.

## What is Devmode?

Devmode is a Kolla-Ansible feature that bind-mounts source code from the host
into containers. This allows you to make code changes without rebuilding
container images - changes take effect after a container restart.

**Warning**: Devmode is intended for development only. Do not use in
production environments.

## How Devmode Works

When devmode is enabled for kerbside, the following volume mount is added to
kerbside containers:

```
/opt/stack/kerbside:/dev-mode/kerbside
```

The container's entrypoint script detects the presence of `/dev-mode/kerbside`
and installs the code from there instead of using the code baked into the
image.

## Enabling Devmode

### Option 1: Enable Globally (All Hosts)

Add to `/etc/kolla/globals.yml`:

```yaml
kerbside_dev_mode: true
```

**Important**: This affects all hosts running kerbside containers. Every such
host must have the kerbside source code cloned to `/opt/stack/kerbside`, or
the containers will fail to start.

### Option 2: Enable for a Single Host (Recommended for Development)

To enable devmode on just one host, use host-specific variables in your
inventory file:

```ini
[kerbside:children]
control

[control]
node1
node2
node3 kerbside_dev_mode=true
```

Or create a `host_vars/<hostname>.yml` file:

```yaml
# host_vars/node3.yml
kerbside_dev_mode: true
```

This way only the specified host gets devmode enabled, and only that host
needs the source code cloned.

## Development Workflow

### Initial Setup

1. **Clone the kerbside source** to the target host(s):

   ```bash
   sudo mkdir -p /opt/stack
   sudo chown $USER:$USER /opt/stack
   git clone https://github.com/shakenfist/kerbside.git /opt/stack/kerbside
   ```

2. **Enable devmode** using one of the methods above.

3. **Deploy** (or reconfigure) with Kolla-Ansible:

   ```bash
   kolla-ansible -i inventory deploy
   # or for just kerbside:
   kolla-ansible -i inventory reconfigure --tags kerbside
   ```

### Iterating on Code Changes

1. **Edit code** in `/opt/stack/kerbside` on the host.

2. **Restart the container** to pick up changes:

   ```bash
   docker restart kerbside_proxy
   # or
   docker restart kerbside_api
   ```

   The container will reinstall the package from the mounted source on
   startup.

3. **Check logs** if needed:

   ```bash
   docker logs -f kerbside_proxy
   ```

### Debugging with remote_pdb

For interactive debugging, you can use `remote_pdb`:

1. **Install remote_pdb** in the container:

   ```bash
   docker exec -it -u root kerbside_proxy pip install remote_pdb
   ```

2. **Add a breakpoint** in your code:

   ```python
   from remote_pdb import RemotePdb
   RemotePdb('127.0.0.1', 4444).set_trace()
   ```

3. **Connect** when the breakpoint is hit:

   ```bash
   socat readline tcp:127.0.0.1:4444
   ```

## Key Paths

| Location | Purpose |
|----------|---------|
| `/opt/stack/kerbside/` | Host source directory (where you edit code) |
| `/dev-mode/kerbside` | Mount point inside the container |
| `/etc/kolla/globals.yml` | Global Kolla-Ansible configuration |

## Configuration Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `kolla_dev_mode` | `false` | Enable devmode globally for all services |
| `kerbside_dev_mode` | `{{ kolla_dev_mode }}` | Enable devmode for kerbside specifically |
| `kolla_dev_repos_directory` | `/opt/stack/` | Host directory where source is expected |

## Common Issues

### Container fails to start with devmode enabled

Ensure the source directory exists on the host:

```bash
ls -la /opt/stack/kerbside
```

If it doesn't exist, clone the repository as shown in the setup steps above.

### Changes not taking effect

- Ensure you restarted the container after making changes.
- Check that the volume mount is present:

  ```bash
  docker inspect kerbside_proxy | grep -A5 Mounts
  ```

- Verify devmode is enabled in the container's configuration:

  ```bash
  grep dev-mode /etc/kolla/kerbside-proxy/config.json
  ```

### Devmode enabled on wrong hosts

If you enabled devmode globally but only have source on one host, either:

- Clone the source to all affected hosts, or
- Switch to per-host configuration as described in Option 2 above.
