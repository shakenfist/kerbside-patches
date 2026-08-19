---
name: extract-bundle
description: Extract and navigate a clingwrap debug bundle from a Build test clouds CI job, which holds the deployed host's logs, config and command output. Use when handed a bundle zip or when a test cloud job needs its deployment logs inspected.
---

# Extracting a Debug Bundle

Every job in the `Build test clouds` workflow uploads a clingwrap bundle
artifact per test cloud, named
`bundle-<release>-h-<host os>-c-<container os>-<topology>`. It is the
richest source of information about a failed deployment.

## Fetching

```bash
# List what a run produced
gh api repos/shakenfist/kerbside-patches/actions/runs/<run-id>/artifacts \
    --paginate -q '.artifacts[].name'

# Download one bundle
mkdir -p /tmp/bundle && cd /tmp/bundle
gh run download <run-id> -R shakenfist/kerbside-patches \
    -n bundle-master-h-debian12-c-debian12-aio
unzip -q bundle.zip -d x
```

The user may also have downloaded one to `~/Downloads/` already.

## Layout

```
bundle/
  gather-<host>.log       # the collector's own log
  <host>/                 # one directory per host, usually "kolla"
    clingwrap.log
    _commands/            # ~127 command outputs, see below
    etc/                  # kolla, docker/daemon.json, os-release, hosts
    var/log/kolla/        # per service container logs
    var/log/syslog
  consoledata/            # SPICE console test artifacts
```

Useful `_commands/` entries:

| File | Holds |
|------|-------|
| `journalctl-docker` | Why dockerd failed to start, if it did |
| `journalctl-containerd` | containerd side of the same |
| `docker-ps-all`, `docker-info` | Container state, storage driver |
| `rpm`, `dpkg` | Installed packages, for "is the module package present" questions |
| `uname`, `hostnamectl` | Running kernel, which often differs from the installed one |
| `systemctl-status` | Unit failures |

Useful log paths:

| Path | Holds |
|------|-------|
| `var/log/kolla/ansible.log` | The main deployment log |
| `var/log/kolla/<service>/` | Per service container logs |
| `var/log/kolla/config-validate` | Config validation output |

A bundle from a job that failed early is sparse. If `var/log/kolla/` is
missing entirely, the deployment never started and the answer is in
`_commands/` or the job log instead.

## Searching

```bash
grep -rn "ERROR\|FAILED\|undefined\|Traceback" x/bundle/*/var/log/kolla/ | head -50
```

## Cleanup

Remove the extraction directory when finished; bundles run to tens of
megabytes.
