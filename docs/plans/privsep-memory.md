# Plan: oslo.privsep memory in devstack CI

## Goal

Measure, and then reduce, the memory that oslo.privsep daemons use on
devstack CI nodes. Do it in an order that leaves a public record
OpenDev will accept: land telemetry in devstack first, record a
baseline, then land one fix at a time and show the change in the same
telemetry after each one.

The trigger was the TC meeting on 2026-10-20 (log:
<https://meetings.opendev.org/meetings/tc/2026/tc.2026-10-20-17.00.log.html>).
There clarkb said privsep's memory use is "a major source of CI
instability", through swapping. He also said he has raised this for
about five years without anyone acting, and he declined to file a bug.

## Status

| Phase | What | Status | Merged |
|-------|------|--------|--------|
| 1 | File the bug with the evidence below | Not started | |
| 2 | devstack telemetry in `get-stats.py`, plus a baseline | In review: [1011786](https://review.opendev.org/c/openstack/devstack/+/1011786), [1011787](https://review.opendev.org/c/openstack/devstack/+/1011787), [1011794](https://review.opendev.org/c/openstack/devstack/+/1011794) | |
| 3 | oslo.privsep: stop importing eventlet in the daemon | In review: [1011795](https://review.opendev.org/c/openstack/oslo.privsep/+/1011795) | |
| 4 | neutron: cut the privileged import chain | In review: [1011796](https://review.opendev.org/c/openstack/neutron/+/1011796), [1011797](https://review.opendev.org/c/openstack/neutron/+/1011797) | |
| 5 | Decide on structural changes (fewer contexts) | Not started | |
| 6 | Report back to the TC and openstack-discuss | Not started | |

## Situation

These are the findings of an investigation on 2026-10-22. The memory
figures come from devstack's `memory_tracker` logs in seven builds on
2026-10-21, and from privsep-helper daemons measured in a container
with nova 34.0.0, neutron 29.0.0 and oslo.privsep 3.12.0.

- **The size claim holds; the causation claim is unproven.** On an
  8 GB node, privsep daemons together peak at 0.3-0.45 GB in OVN jobs
  and about 1 GB on ML2/OVS controllers (nova-next,
  neutron-ovs-tempest-multinode-full). That is 4-14% of summed RSS.
  QEMU guests (2.3-3.9 GB) and the API, conductor and neutron-server
  workers are bigger. Six of the seven 8 GB nodes swapped 1.7-5.9 GB.
  Privsep pages are cold: under pressure a neutron daemon falls from
  about 118 MB to 10-25 MB of RSS. So privsep adds to swap use, but no
  evidence ties it to swap-in thrash.
- **There is no public record beyond IRC remarks.** oslo.privsep has
  no memory bug on Launchpad, and none of clarkb's 151 bug reports is
  about this. His record is a 2021 IRC estimate of "almost 1GB", which
  is about right, one sentence in a 2021 email, and remarks in 2024
  and 2026. JayF's 2024-02-29 openstack-discuss call for help named
  oslo.privsep and got no replies.
- **The daemons are exec'd, not forked.** Every service uses the
  ROOTWRAP start method: `sudo <rootwrap> privsep-helper
  --privsep_context ...`. The daemons run with ppid 1. The "fork with
  the whole service resident" explanation given in the meeting is
  wrong for devstack.
- **An idle daemon costs about 35 MB of anonymous memory.** Most of it
  comes from oslo.privsep itself, not the service. `daemon.py` imports
  eventlet at module level, which pulls in dnspython, cryptography and
  pyOpenSSL: 446 modules and about 19 MB. oslo.config and oslo.log add
  about 13 MB more. The context module adds under 1 MB for nova,
  os-brick, os-vif and cinder, and about 3 MB for neutron.
- **Daemons grow on first use.** `Daemon._process_cmd` imports each
  entrypoint's module the first time it is called. In neutron,
  `neutron/privileged/agent/linux/ip_lib.py` imports
  `neutron.common.utils`, which brings in openstacksdk, sqlalchemy,
  pecan, os_ken and keystoneauth: about 1600 modules and +71 MB. So
  a warm neutron daemon is about 118 MB, against about 60 MB for nova
  and 55 MB for os-vif.
- **Neutron runs many contexts, once per agent.** On an ML2/OVS
  controller, q-agt, q-dhcp and q-l3 each start their own `default`,
  `link_cmd` and `namespace_cmd` daemons as they need them, giving
  11-13 daemons per node. The OVN agent starts three.
- **Threads are cheap.** A full 16-thread pool adds about 1.6 MB. So
  devstack change 988806, which sets `thread_pool_size=1`, will not
  save much.
- **devstack already records privsep, but badly.** `tools/get-stats.py`
  lists `privsep` among its default processes and writes
  `performance.json` in every job. It only samples RSS once, at the end
  of the job, after swap has emptied the daemons, so the number it
  reports looks small.

## Patch series

There is one independent series per upstream repository. The numeric
suffix gives the order they are meant to land in:

| Series | Repository | Patches | Phase |
|--------|------------|---------|-------|
| `devstack-privsep-memory-001` | devstack | patch215 (tolerate zombies), patch216 (memory telemetry), patch220 (CentOS and Rocky) | 2 |
| `oslo.privsep-privsep-memory-002` | oslo.privsep | patch217 (only inspect eventlet if already imported) | 3 |
| `neutron-privsep-memory-003` | neutron | patch218 (no `neutron.common.utils`), patch219 (no `neutron_lib`) | 4 |

- **One Gerrit topic.** All three set `destination_branch:
  privsep-memory`, so the changes share that topic across the three
  repositories.
- **Pushed.** All six changes were pushed by hand on 2026-10-22,
  before the Phase 1 bug: devstack 1011786, 1011787 and 1011794
  (patch215, 216, 220), oslo.privsep 1011795 (patch217) and neutron
  1011796 and 1011797 (patch218, 219). All three series now have
  `repush: true`, so the daily rebase pushes new revisions.
- **Depends-On.** patch217 and patch218, the root patches of 002 and
  003, carry `Depends-On:` 1011787, the telemetry change. Its parent
  1011786 comes with it, so no other patch repeats it. Zuul then runs
  the changes' own devstack jobs with the telemetry applied:
  oslo.privsep's `tempest-full-py3` (from `lib-forward-testing-python3`)
  and neutron's tempest jobs. Grenade jobs do not run the
  `capture-performance-data` role, so they produce no telemetry.
  - The footers also stop 002 and 003 merging before 001, which is the
    order we want. Once 001 merges the footers are satisfied and do no
    harm.
- **Before numbers.** 001's own jobs give the baseline for jobs that
  devstack shares with the other repositories. For neutron-only jobs,
  push a DNM neutron change that carries only the Depends-On, as a
  control.
- **Bug reference.** When the Phase 1 bug exists, add `Related-Bug:` to
  every patch.

## Phase 1 --- file the bug

File an oslo.privsep bug, with a neutron task added, containing the
numbers above and the method behind them. Changes in later phases
reference it (`Related-Bug`/`Closes-Bug`), and the TC goal discussion
gets something to point at. Mikal files it; Claude drafts it.

## Phase 2 --- devstack telemetry and a baseline

There are three changes, carried as series `devstack-privsep-memory-001`.

1. **Tolerate zombie processes.** Today a zombie on the node makes
   `proc.cmdline()` raise `psutil.ZombieProcess`, which aborts the
   script and loses the whole of `performance.json`. This was seen
   when running the script locally.
2. **Record peak and cold memory.**
   - Per tracked process, add `rss_peak` (VmHWM), `rss_anon` (RssAnon)
     and `swap` (VmSwap) from `/proc/PID/status`. That file is
     world-readable, so this works for root's privsep-helper from the
     unprivileged collection role.
   - Add a top-level `swap` object with `pswpin`, `pswpout`, `zswpin`
     and `zswpout` from `/proc/vmstat`. clarkb's claim is about swap
     traffic, and zswap is enabled on CI nodes.

3. **Fix the empty file on CentOS Stream and Rocky.** The
   `capture-performance-data` role always calls
   `setup_devstack_virtualenv`, but `GLOBAL_VENV` is False on those
   distributions, so no venv exists and `performance.json` is empty in
   every build. Use the venv's python when it exists, and otherwise
   `python${PYTHON3_VERSION}`, where devstack installed psutil and
   PyMySQL.

1011787's own jobs confirmed changes 1 and 2 work: every Debian and
Ubuntu build has the new fields. A peak of 568 MB of privsep on
`tempest-full-py3` (8 daemons) and 1128 MB on
`devstack-platform-ubuntu-noble-ovs` (14 daemons), against an
end-of-job RSS of 140 and 402 MB.

clarkb's dstat change (devstack 1011544) gives a time series alongside
these; this phase does not duplicate it.

**Baseline.** Once the change merges, collect `performance.json` from
at least 20 successful builds each of `tempest-integrated-compute`,
`nova-next`, `tempest-full-py3` and `neutron-ovs-tempest-multinode-full`,
through the Zuul builds API. Summarise per privsep context: peak RSS,
anonymous memory plus swap, and the daemon count, along with the
node's swap counters. The collection script goes in `tools/` here.

## Phase 3 --- oslo.privsep: no eventlet in the daemon

Open change 1010664 removes eventlet from oslo.privsep. In the
prototype it took idle nova, os-brick, os-vif and ceilometer daemons
from 39 to 26 MB of RSS, and warm os-brick from 41 to 28 MB. It does
nothing for two services:

- **neutron:** `neutron/__init__.py` imports oslo.service's threading
  backend, which imports futurist, which tries `import eventlet` in
  `futurist/_utils.py`.
- **cinder:** `cinder/__init__.py` imports eventlet itself.

1010664 removes eventlet support, so it is a breaking change and will
not backport. Series 002 carries the alternative: import eventlet only
when it is already in `sys.modules`. It gives the same numbers, does
not change behaviour, and can be backported, so it can be offered as
the stable-branch fix alongside 1010664. Whether to push it while
1010664 is open is still undecided.

A follow-up would make oslo.service import its backend lazily. That
would save about 12 MB more per neutron daemon: a warm neutron daemon
would be 45 MB rather than 57, simulated by blocking the import. A
`sys.modules` guard in futurist is not equivalent, because whether
`GreenThreadPoolExecutor` is available depends on that import.

Watch about a week of builds before the next fix lands, so the
savings can be told apart.

## Phase 4 --- neutron: cut the privileged import chain

The prototype cuts two import edges from `neutron/privileged/`. The
figures are for a warm `neutron.privileged.default` daemon;
`namespace_cmd` and `link_cmd` move by the same amounts.

| Change | Warm RSS / anon MB | Modules |
|---|---|---|
| neutron master | 118 / 107 | 2382 |
| Change 1 (patch218): move `read_file` and the two `cidr_broadcast_address*` helpers into a new `neutron/common/privsep_safe_utils.py`, which `neutron.common.utils` re-exports | 90 / 81 | 1438 |
| Change 2 (patch219): also drop the `neutron_lib` imports from privileged `ip_lib`, `utils` and `tc_lib` | 57 / 50 | 999 |

- **Change 1 should not be controversial.** It also adds a test that
  imports every privileged module in a fresh interpreter and fails if
  `neutron.common.utils` is loaded.
- **Change 2 needs a judgement call.** It copies `IP_VERSION_4`,
  `IP_VERSION_6`, `DEVICE_NAME_MAX_LEN` and `safe_decode_utf8` out of
  neutron_lib, with a test that checks the copies still match.
  - The real cause is upstream: `neutron_lib/__init__.py` imports
    `neutron_lib.db.api` (sqlalchemy, oslo.db, pecan) on every
    neutron_lib import, deliberately, because of LP #1802369.
  - Fixing that in neutron-lib is the better root-cause fix, but it
    changes behaviour there. Ask neutron reviewers which they prefer
    before pushing change 2.
- **`conntrack_cmd` is unaffected.** It still loads neutron_lib
  through `netlink_lib` and stays at about 88 MB.
- **Tests:** 336 of 336 pass in the touched unit test areas, and
  flake8 with neutron's hacking checks passes. The full suite, pylint,
  mypy and the root-only functional tests have not been run.

Projected per-node totals (warm RSS / anon MB):

| Scenario | Baseline | Neutron change 2 | Change 2 + Phase 3 |
|---|---|---|---|
| devstack all-in-one, OVN, 8 daemons | 584 / 521 | 400 / 347 | 358 / 302 |
| ML2/OVS controller, 12 daemons | 1134 / 1022 | 704 / 617 | 688 / 601 |

The ML2/OVS daemon count comes from reading the code, not from a real
node. Phase 2's telemetry should confirm it.

Phase 4 does more than Phase 3 and does not depend on it, so it may
land first if neutron reviews faster.

## Phase 5 --- structural changes, decided on the numbers

Decide only once phases 3 and 4 have landed and been measured. The
candidates are fewer neutron contexts per agent (each was added in
2021 for least privilege, so this is a security trade-off neutron must
agree to), or sharing one daemon between agents. Record the decision
here, including a decision not to proceed.

## Phase 6 --- report back

Reply on openstack-discuss and at a TC meeting with the baseline and
the before/after telemetry. Be plain about what the data shows. If
swap traffic falls but job failure rates do not, say so.

## Open questions

- Is `performance.json` ingested anywhere queryable, such as the
  OpenSearch log index? If it is, Phase 2's baseline does not need a
  collection script. The comment in `get-stats.py` about Elasticsearch
  mapping explosion suggests it once was. Unverified.
- Should adding fields bump `performance.json`'s `version` (2 today)?
  The additions are backwards compatible; leave this to the devstack
  reviewers.
