# Plans index

Every planning document in this repository, oldest first. Plans here
describe work on the patch series themselves and on the tooling that
keeps them applying, and each one outlives the pull request that
implemented it because the upstream half -- pushing to Gerrit, chasing
reviews, landing -- runs long after the patches land here.

None of these plans has separate phase files. Where a plan is phased,
its phases are sections inside it, tracked in the plan's own table;
this page carries only the one-line status.

Status cells use the shared vocabulary from
`templates/shared-blocks/plan-status-vocabulary.md` in
[shakenfist/development](https://github.com/shakenfist/development),
the same list the `plan-index` audit enforces across the fleet: one of
`Proposed`, `Not started`, `In progress`, `Blocked`, `Complete`,
`Abandoned` or `Superseded`. A status says whether the plan still
wants attention and nothing else -- the dates, the phase arithmetic
and the summary of what happened live in the plan.

| Date | Plan | Intent | Status |
|------|------|--------|--------|
| 2026-09-11 | [JSON logging for oslo.log services](oslo-json-logging.md) | Switch every oslo.log-based Kolla-Ansible service to structured JSON logging behind one global variable, reusing the fluentd JSON parser Kerbside introduced | In progress |
| 2026-09-24 | [Non-OpenStack upstream series (qemu, Linux)](upstream-series.md) | Keep patch series against upstreams outside OpenStack, starting with kerbside's SPICE qemu damage-path series (carried downstream) and a DRM `ignore_damage_clips` kernel fix (to submit), under an `upstream/` tree the OpenStack tooling cannot see, with their measurement rigs, rebase checks and a mail submission path | In progress |
| 2026-09-29 | [Containerised Kolla-Ansible deployer](containerised-deployer.md) | Prototype Kolla-Ansible running from an image built and tagged with the service images, behind a stdlib-only host launcher, with enforced deployer/image lockstep and a per-build digest manifest, as the demo an upstream proposal would start from | Complete |
| 2026-10-10 | [Neutron L3 agent work queue visibility](neutron-agent-pending-updates.md) | Propose reporting the L3 agent's update backlog as `pending_updates` in its heartbeat upstream, with an out-of-tree metrics extension held in reserve if reviewers reject it | In progress |
| 2026-10-20 | [CI tiers](ci-tiers.md) | Split CI into a pull request smoke tier (patch tests, one image build, the Debian control and launcher installs) and a serial merge queue tier running the full matrix on the merged tree, with automatic re-queueing on infrastructure failure and bot data pull requests queued automatically | In progress |
| 2026-10-22 | [OpenStack memory in devstack CI](privsep-memory.md) | Land per-process memory and swap telemetry in devstack, baseline it, then land the oslo.privsep eventlet and neutron import-chain fixes and the service-side unneeded-import fixes one at a time and report the measured change to the TC | In progress |
| 2026-10-28 | [Neutron agent bugs from the L3/DHCP reading](neutron-agent-bugs.md) | Turn the bugs found reading the L3 and DHCP agents into five upstream series: four DHCP agent logic fixes, locking for the DVR fip namespace races the eventlet removal made real, a hacking check for config read at import, a static import-closure test for the agents, and a shorter thread switch interval under test | In progress |
| 2026-10-28 | [Incremental mypy coverage for the L3 and DHCP agents](neutron-agent-typing.md) | Make mypy check the agent code by turning `check_untyped_defs` on for the L3 and DHCP agent packages with the twelve failing modules as a shrinking exception list, then clearing that list and annotating the driver and router interfaces in series small enough to land one at a time, with Depends-On footers where a typing change needs an agent-bugs fix that has not merged | Not started |
