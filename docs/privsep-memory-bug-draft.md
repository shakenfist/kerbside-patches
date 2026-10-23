# Draft Launchpad bug: oslo.privsep memory in devstack

This is the bug that Phase 1 of the [OpenStack memory plan](plans/privsep-memory.md)
calls for. It was drafted on 2026-10-23 and is **deliberately not filed yet.**
Whether privsep memory is a problem worth fixing is still being argued
upstream. The plan is to land the proposed fixes first, so the measured
savings make the case, and to file this once the community is ready to
track the work. Mikal decides when that is.

## Before filing

The draft is a snapshot. Check these against the current state first:

- **Status of the proposed changes.** Every change listed was open on
  2026-10-23. Describe merged ones as merged, quote their measured
  savings from `performance.json` rather than the projections, and add
  any changes proposed since.
- **The figures.** The CI numbers come from 1011787's patchset 2 builds,
  which were linked as [3] and [4]. Once the Phase 2 baseline exists,
  cite it instead. Zuul build logs expire, so links to old builds will
  stop working.
- **1010664.** Check whether the change that removes eventlet from
  oslo.privsep has merged or been abandoned, and update the oslo.privsep
  paragraph to match.
- **The "Not privsep, but related" paragraph.** Phase 5's findings are
  meant to be filed against their own projects. If any of those bugs
  exist by then, link them here instead of describing the findings.
- **After filing.** Add `Related-Bug:` to every patch in the plan's
  series. Repush pushes a new revision of every change, so check first
  whether the Gerrit votes survive a revision that only changes the
  commit message. Then mark Phase 1 done in the plan.

## The draft

Paste the title into Launchpad's summary field and everything after the
`---` into the description.

```text
Project: oslo.privsep   (then "Also affects project": neutron)
Tags: none (this is not a gate failure yet)
Title: privsep-helper daemons use 35-120 MB each in devstack, mostly from imports they do not need

---

At the TC meeting on 2026-10-20 [1], privsep's memory use on devstack CI
nodes was described as a major source of CI instability through
swapping. There was no bug for it, so this records what the memory
actually is, how it was measured, and the changes proposed so far.

Summary
=======

- The size is real. On an 8 GB devstack node the privsep daemons peak
  at about 0.25-0.6 GB together on OVN jobs, and about 1.1 GB on an
  ML2/OVS controller. That is 4-14% of the summed RSS on the node.
- Most of it is imported code the daemon never runs. An idle daemon
  costs about 35 MB of anonymous memory, and most of that comes from
  oslo.privsep itself. A neutron daemon grows to about 118 MB on its
  first call, because of what neutron/privileged/ imports.
- The causation is not proven. The pages are cold and are among the
  first to be swapped out, so privsep adds to swap volume. No evidence
  yet ties it to swap-in thrash or to job failures.

How the daemons start
=====================

Every service in devstack uses the ROOTWRAP start method
(sudo <rootwrap> privsep-helper --privsep_context ...). The daemons are
exec'd, not forked: they run with ppid 1 and do not inherit the parent
service's memory. Each daemon therefore pays for its own imports.

Measurements
============

CI telemetry. devstack change 1011787 adds peak RSS (VmHWM), anonymous
RSS and swap per process to performance.json [2]. Before it, the file
recorded RSS once, at the end of the job, after swap had emptied the
daemons. From that change's own builds:

  job                                   daemons  peak sum  end-of-job RSS  swapped at end
  tempest-full-py3 [3]                        8   566 MiB         111 MiB         452 MiB
  devstack-platform-ubuntu-noble-ovs [4]     14  1129 MiB         388 MiB         738 MiB

Per daemon, the warm peaks are about 115 MB for each neutron context,
about 51 MB for vif_plug_ovs, 44 MB for nova's sys_admin_pctxt and
about 40 MB for os_brick and cinder.

Container measurement, with nova 34.0.0, neutron 29.0.0 and
oslo.privsep 3.12.0, starting privsep-helper for each context and
reading /proc/PID/status:

- Idle daemon: about 35 MB anonymous. oslo_privsep/daemon.py imports
  eventlet at module level, which pulls in dnspython, cryptography and
  pyOpenSSL: 446 modules and about 19 MB. oslo.config and oslo.log add
  about 13 MB more. The context module itself adds under 1 MB for
  nova, os-brick, os-vif and cinder, and about 3 MB for neutron.
- First call: Daemon._process_cmd imports each entrypoint's module the
  first time it is called. neutron/privileged/agent/linux/ip_lib.py
  imports neutron.common.utils, which brings in openstacksdk,
  sqlalchemy, pecan, os_ken and keystoneauth: about 1600 modules and
  +71 MB. A warm neutron daemon is therefore about 118 MB, against
  about 60 MB for nova and 55 MB for os-vif.
- Count: neutron starts default, link_cmd and namespace_cmd per agent.
  On an ML2/OVS controller, q-agt, q-dhcp and q-l3 together give 11-13
  daemons. The OVN agent starts three.
- Threads are cheap: a full 16-thread pool adds about 1.6 MB, so
  thread_pool_size=1 (devstack change 988806) saves little.
- Under memory pressure a neutron daemon falls from about 118 MB to
  10-25 MB of RSS, with the rest in swap.

Proposed changes
================

oslo.privsep
  1011795 [5] Only inspect eventlet if it is already imported. Takes
  idle nova, os-brick, os-vif and ceilometer daemons from 39 to 26 MB
  of RSS. It does not change behaviour, so it can be backported. Open
  change 1010664, which removes eventlet support entirely, gives the
  same saving on master but is not backportable.

  Neither helps neutron or cinder daemons on its own:
  neutron/__init__.py imports oslo.service's threading backend, which
  imports futurist, and futurist/_utils.py imports eventlet at module
  level; cinder/__init__.py imports eventlet itself.

neutron
  1011796 [6] privileged: Do not import neutron.common.utils. Warm
  neutron.privileged.default: 118 -> 90 MB RSS, 2382 -> 1438 modules.
  1011797 [7] privileged: Do not import neutron_lib. 90 -> 57 MB RSS,
  999 modules. The underlying cause is that neutron_lib/__init__.py
  imports neutron_lib.db.api (sqlalchemy, oslo.db, pecan) on every
  import, deliberately, for bug 1802369. Fixing that in neutron-lib
  would be the root-cause fix, but it changes behaviour there.
  conntrack_cmd still loads neutron_lib through netlink_lib and stays
  at about 88 MB.

devstack (telemetry, so the effect of each change can be seen)
  1011786 tolerate zombie processes in get-stats.py, 1011787 the
  memory fields above, 1011794 performance.json on CentOS and Rocky.

Projected per-node totals, warm RSS / anonymous MB:

  scenario                          today        neutron fixes  + oslo.privsep fix
  OVN all-in-one, 8 daemons         584 / 521    400 / 347      358 / 302
  ML2/OVS controller, 12 daemons    1134 / 1022  704 / 617      688 / 601

The plan is to land these one at a time and compare performance.json
before and after each, so the savings can be told apart.

Not privsep, but related
========================

A follow-up scan of the services themselves found more of the same.
They are large packages imported but never called: futurist's eager
eventlet import in every threaded service, neutron_lib's db.api import
in every neutron agent, and module-level imports of openstacksdk in
neutron, pysaml2 in keystone and alembic in placement. Together these
cost about 0.5 GB on an OVN node and 0.85 GB on an ML2/OVS controller.
They will be filed against the projects concerned rather than here.

[1] https://meetings.opendev.org/meetings/tc/2026/tc.2026-10-20-17.00.log.html
[2] https://review.opendev.org/c/openstack/devstack/+/1011787
[3] https://zuul.opendev.org/t/openstack/build/8e3bfd425736459e81a0d3972348a651
[4] https://zuul.opendev.org/t/openstack/build/fb330af810cc4841943f24d573a95ae9
[5] https://review.opendev.org/c/openstack/oslo.privsep/+/1011795
[6] https://review.opendev.org/c/openstack/neutron/+/1011796
[7] https://review.opendev.org/c/openstack/neutron/+/1011797
```
