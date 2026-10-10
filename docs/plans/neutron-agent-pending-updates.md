# Plan: neutron L3 agent work queue visibility

## Goal

Let operators see how far behind a neutron L3 agent is. The agent
queues router and network updates in a `ResourceProcessingQueue` and
works through them with a pool of 32 threads, but nothing reports the
length of that queue, how long updates wait in it, or how often they
are retried. Today that can only be recovered by scraping INFO log
lines.

Start with the smallest change upstream is likely to accept, and see
how reviewers react before building anything larger.

## Status

| Phase | What | Status | Merged |
|-------|------|--------|--------|
| 1 | Propose `pending_updates` in the L3 and DHCP agent heartbeats upstream | In progress | |
| 2 | Fallback: out-of-tree metrics extension | Proposed | |
| 3 | Export `pending_updates` from openstack-exporter | Blocked | |
| 4 | Push audit | Not started | |

## Situation

These findings come from reading `neutron/agent/l3/agent.py` at
upstream `dd92e47ab1`.

- **Neutron has no metrics library.** Nothing in the tree imports
  oslo.metrics, prometheus_client or statsd. The L3 agent's only
  structured output is the `report_state` heartbeat, whose
  `configurations` carry `routers`, `ex_gw_ports`, `interfaces` and
  `floating_ips`, and osprofiler traces (`@profiler.trace_cls`).
- **The queue length is already exposed, just not used.**
  `ResourceProcessingQueue.qsize` was added in 2024 for the DHCP
  agent, which logs it at debug (LP #2073490). The L3 agent uses the
  same class and never reads it.
- **This is a known gap, and upstream was open to closing it.** LP
  #1512864 ("Application Metrics for Neutron", 2015) asked for a
  statsd-style library. It was closed Won't Fix because the tooling
  should come from oslo or osprofiler first, after which neutron would
  take instrumentation patches "on a case by case basis". oslo.metrics
  arrived in Ussuri, but only oslo.messaging uses it, and the
  case-by-case patches never followed.
- **The heartbeat costs nothing extra to extend.**
  `AgentDbMixin.create_or_update_agent` (`neutron/db/agents_db.py`)
  rewrites the whole `configurations` JSON on every heartbeat, so a
  value that changes each report adds no DB writes.

## Phase 1 --- `pending_updates` in the heartbeat

Series `neutron-agent-pending-updates/`, patch206, Gerrit topic
`agent-pending-updates`, closing LP #2170465 (filed 2026-10-11 as a
plain bug, not an RFE, citing LP #1512864), and uploaded as
[1009917](https://review.opendev.org/c/openstack/neutron/+/1009917).
One line in
`L3NATAgentWithStateReport._report_state` adds
`configurations['pending_updates'] = self._queue.qsize`, with a unit
test and a release note. Operators can read it with
`openstack network agent show`, and exporters that already scrape the
agent list can pick it up.

patch207 makes the same change in `DhcpAgentWithStateReport._report_state`,
which uses the same queue class, with `Related-Bug: #2170465`, uploaded
as [1009918](https://review.opendev.org/c/openstack/neutron/+/1009918). It is
stacked on patch206 so reviewers see the L3 change first; if they want
the two as one change, squash them.

What it counts: updates in the priority queue that no worker has taken
yet. Once a worker takes an update for a router that another worker is
already processing, the update is handed to the primary worker
(`ExclusiveResourceProcessor`) and drops out of the count. Several
updates for the same router are counted separately. That makes it a
backlog gauge, not a count of routers needing work, and the commit
message should not claim more than that.

How to judge the reaction:

- **Accepted, or accepted with changes:** follow both patches to merge,
  and start phase 3 once a core reviewer has +2'd the approach.
- **Rejected because the heartbeat is the wrong place:** for example,
  "configurations is for configuration, not runtime state". Ask where
  reviewers would put it. If the answer is oslo.metrics or nowhere, go
  to phase 2.
- **No response:** chase it once in the neutron IRC meeting or on
  openstack-discuss, citing LP #1512864's case-by-case promise.

## Phase 2 --- fallback: out-of-tree metrics extension

Only if phase 1 is rejected. This needs no neutron patch, but it reads
private agent state, so it is likely to break across releases.

An L3 agent extension, packaged out of tree and registered in the
`neutron.agent.l3.extensions` stevedore namespace (the in-tree list is
in neutron's `pyproject.toml`), enabled with `[agent] extensions` in
`l3_agent.ini`:

1. In `initialize()`, subscribe to `resources.ROUTER` callbacks for
   `BEFORE_`/`AFTER_` `CREATE`, `UPDATE` and `DELETE`. The agent
   publishes them with `trigger=self` (around `agent.py:451`, `:509`,
   `:523`, `:632`, `:686` and `:693`), so the callback receives the
   agent object.
2. Pair `BEFORE_UPDATE` with `AFTER_UPDATE` to time `ri.process()` for
   each router.
3. Keep the agent reference from the first callback. A background
   thread samples `agent._queue.qsize` (private) and serves a
   prometheus_client endpoint. The agent is a single process with
   native threads since the eventlet removal, so the default registry
   works, and prometheus_client metric types are thread-safe, which
   matters because callbacks fire from the 32 pool workers.

To also get queue wait time, retries and update priority, add a
logging handler configured via `log_config_append`. It reads
`record.args` from the INFO lines "Starting processing update" (id,
action, priority, wait time), "Finished a router update/delete"
(elapsed time) and "Hit retry limit". The callbacks don't carry the
`ResourceUpdate`, so the extension can't see these itself.

Known limits:

- The agent reference only appears after the first router event, so
  on a node with no routers the gauge stays empty.
- `_queue` and the log message arguments are not a contract.

Rejected alternatives:

- **Subclassing the agent.** `neutron.agent.l3_agent.main(manager=...)`
  makes this possible and would see everything, but it needs a custom
  binary and overrides private methods.
- **Wrapping RouterInfo via `L3AgentExtensionAPI.register_router`.**
  Router classes depend on the agent mode, and only one extension can
  hold each slot.
- **Wrapping the interface driver.** It only sees plug and unplug,
  below the queue.

## Phase 3 --- export `pending_updates` from openstack-exporter

Blocked until phase 1 has a core +2 on the approach. A key that
reviewers might rename or move is not worth exporting.

No client change is needed to *see* the new key. neutron-server stores
`configurations` as a JSON blob and returns it unvalidated. openstacksdk
maps it as `configuration = resource.Body('configurations')` with no
schema, and openstackclient prints the dict whole in
`network agent show` and `network agent list --long`. The gap is
Prometheus. openstack-exporter's neutron collector only exports
`openstack_neutron_agent_state` (up/down, `exporters/neutron.go`) and
never reads `configurations`.

The change, a GitHub PR against openstack-exporter/openstack-exporter:

- Add a gauge, e.g. `openstack_neutron_agent_pending_updates`, with the
  same labels as `agent_state` (`id`, `hostname`, `service`,
  `adminState`, `availability_zone`), filled from the agent list it
  already fetches.
- Emit it only for agents whose configurations carry the key, so older
  agents and OVN agents produce no series rather than a misleading 0.
- Cite the neutron change, or the release it lands in, in the PR, along
  with the exporter's existing tests for `agent_state` as the pattern.

Merged: record it as `openstack-exporter <sha> (#pr)`. It is audited
in that repository as part of its own PR.

An aside found while checking OSC: `network_agent.py` keys its
`DictColumn` formatter as `'configurations'`, but the column it fetches
is `'configuration'`. osc-lib matches formatters by exact field name,
so the formatter probably never runs. This is unverified and separate
from this plan.

## Open questions

- **Is the key name right?** `pending_updates` matches the DHCP log
  message's wording ("Pending events"). Default: keep it unless
  reviewers object.

## Phase 4 --- push audit

This repository has no `PUSH-AUDIT.md` yet. Audit the accumulated diff
of phases 1 and 2 against `develop` before closing the plan, using the
`Merged` column above to scope it. Phase 3 lands in openstack-exporter
and is audited there, so this phase cites that audit rather than
repeating it.
