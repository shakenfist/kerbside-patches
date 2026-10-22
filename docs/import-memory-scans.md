# Scanning OpenStack services for unneeded imports

Most of an OpenStack Python service's steady-state memory is the code it
imported, not the data it holds. So a package that a process loads but never
calls costs real memory, in every worker and parent process on every node.
`tools/import-memory/` finds such packages, shows which import pulls each one
in, and measures what removing it would save.

The tools were written for the devstack CI memory work in
[plans/privsep-memory.md](plans/privsep-memory.md). Phase 5 of that plan
records the first scan, made on 2026-10-23, which found about 0.5 GB per OVN
all-in-one node and 0.85 GB per ML2/OVS controller.

## The tools

| File | Purpose |
|------|---------|
| `scan.sh` | Driver. `build` builds a container image of the services from master; `probe` runs `probe.py` for every role; `marginal` runs `marginal.py`; `shell` opens a shell in the image's `site-packages`, to read installed source. |
| `Dockerfile` | python:3.12 with nova, neutron, cinder, glance, keystone and placement installed from git master under master upper-constraints. Build arguments `PROJECTS` and `EXTRAS` change what is installed. |
| `roles.txt` | One line per service role: its entry point plus the plugins devstack configures. |
| `probe.py` | Imports one role in a fresh interpreter. It reports the role's anonymous and total RSS and its module count per top-level package. For each package it also records the chain of importers that first loaded it. |
| `report.py` | Summarises the probe output: one row per role, a table of package by role, and `--chain PACKAGE` to show the import site in each role. |
| `marginal.py` | The anonymous RSS that importing some modules adds on top of a base set. This is the per-process saving from not importing them. |
| `memory-tracker-peaks.py` | Ranks the processes on a devstack CI node by peak RSS, from its memory_tracker log. With `--group` it gives process counts and totals per project. |

## How to run a scan

### 1. See what is big in CI

Begin from a real job, not from the code. devstack's
`controller/logs/screen-memory_tracker.txt` logs `ps` every few seconds.
Fetch it with `zuul-result`, then rank it:

```bash
zuul-result <build-uuid> log controller/logs/screen-memory_tracker.txt > mt.txt
tools/import-memory/memory-tracker-peaks.py mt.txt --top 60
tools/import-memory/memory-tracker-peaks.py mt.txt --grep neutron
tools/import-memory/memory-tracker-peaks.py mt.txt --group
```

`performance.json` does not help here yet. Its `processes` list only
covers mysqld, rabbit, privsep and etcd by default. Its `services`
figures are cgroup totals per systemd unit: one sample at the end of the
job, including page cache, taken after swap has emptied the processes.

### 2. Probe the roles

```bash
tools/import-memory/scan.sh build        # about ten minutes the first time
tools/import-memory/scan.sh probe > probe.jsonl
tools/import-memory/report.py probe.jsonl
```

The summary rows give each role's import-only RSS. Compare that with the CI
peak from step 1. When the two are close, the process's memory is mostly
imports and a scan is worth doing. On 2026-10-23 they were within about 10%
for the nova scheduler, novncproxy and every neutron agent. When the CI peak
is much higher, as for nova-compute, the memory goes on runtime state and
caches instead, and this scan will not find it.

A role whose plugin fails to import shows `ERROR` in its row. Fix the line
in `roles.txt` (a renamed module, or a missing `call:` setup step) before
trusting that row. The scripts are mounted into the container, so editing
`roles.txt` needs no rebuild.

### 3. Find candidates

The package table shows, for each role, how many modules of each top-level
package it loaded. Look for a package in a role that has no reason to call
it. Kinds that have paid off:

- **Agents loading a database or web stack:** sqlalchemy, oslo.db,
  alembic, pecan, mako. In neutron this comes in through `neutron_lib`
  itself.
- **Server or API processes loading host-level libraries:** pyroute2,
  os_ken, os_brick, `*.privileged`.
- **Optional features imported unconditionally:** SAML federation in
  keystone, database migrations in placement, a single optional
  middleware in neutron.
- **eventlet in a service that has moved to threads,** usually brought in
  by a library rather than by the service. `report.py --packages eventlet`
  shows where.
- **A whole library loaded for a few constants,** for example os_ken's
  packet library for ethertype values.

To see the import site:

```bash
tools/import-memory/report.py probe.jsonl --chain sqlalchemy
```

Each chain reads innermost first: the package, then the module that
imported it, and so on back to the role's entry point. Next read the
module that imports the package (`scan.sh shell`). Check whether the
process could ever reach the code that uses it. A package that the
process does use at runtime does not count, however expensive it is.
networkx in cinder is one: taskflow uses it to run every flow.

### 4. Cost each candidate

```bash
B=oslo_config.cfg,oslo_log.log,requests,cryptography.x509,keystoneauth1.session,webob
tools/import-memory/scan.sh marginal $B eventlet
tools/import-memory/scan.sh marginal $B,oslo_db.sqlalchemy.enginefacade pecan
```

The base set matters. It should hold what the process loads anyway, so that
the figure is the saving, not the cost of shared dependencies. Imported on
its own, eventlet measures 22.6 MB. On top of cryptography and requests,
which every service loads, it measures 11 MB, and 11 MB is the real saving.

Multiply by the number of processes from `--group`, or from counting
`--grep` output, to get a per-node figure.

### 5. Fix it, and guard the fix

The usual fix is to move the import into the function that uses it, or to
move a small helper out of a heavy module. Land each fix with a unit test
that imports the module in a fresh interpreter and fails if the dropped
package appears in `sys.modules`. neutron patch218 shows the pattern.
Without such a test, the next unrelated import brings the package back.

## Pitfalls

- **tracemalloc cannot attribute this memory.** It charges code objects
  to `importlib`, which accounts for about half of everything an import
  allocates, not to the package that was imported. Use `marginal.py`.
- **Runtime plugins must be in `roles.txt`.** Services load their drivers
  through stevedore or importutils after start-up. An entry point alone
  misses them, for example the libvirt driver in nova-compute.
- **The uWSGI `wsgi` modules build their application at import time,**
  and that needs config files. The API roles therefore import the modules
  the app factory loads, not the `wsgi` module.
- **Per-package savings overlap,** because packages share dependencies, so
  adding them up overstates the total. Present such sums as upper bounds.
- **RSS counts copy-on-write pages in both parent and forked worker,** so
  summing RSS over the processes in a `--group` overstates the shared
  part.
- **Saved pages are mostly cold.** Under memory pressure they are swapped
  out first. Removing them cuts swap volume, but that alone does not prove
  it cuts swap thrash. Say so when reporting.
- **Always use `python -I`, with a fresh interpreter per role.** Anything
  imported before the probe starts is not attributed.
