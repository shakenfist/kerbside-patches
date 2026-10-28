# Neutron agent bugs: drafts and findings

This file holds the Launchpad bug drafts and the not-yet-addressed findings
for the plan `docs/plans/neutron-agent-bugs.md`. Each section is written to be
pasted into Launchpad by a reader who has not seen the plan.

## Configuration options read at import time

**Title:** Config options read at import time ignore the config file in
tools that import neutron before parsing it

**Affects:** neutron master (all supported branches)

`neutron/objects/base.py`, `neutron/extensions/quotasv2.py` and
`neutron/extensions/quotasv2_detail.py` read `cfg.CONF` options (`debug` and
`quota_driver`) at module level, when the module is imported. The value
captured is whatever the option holds at that moment, which is the default
unless the configuration has already been parsed.

Tools such as `neutron-ovn-db-sync-util` import these modules before they
parse their configuration, so they see the defaults instead of the operator's
settings. In `quotasv2` the effect also depended on import order: the run-time
quota driver was compared with the import-time value to decide whether the
extension description said "per project".

The fix reads `debug` and `quota_driver` where they are used, always
registers the ORM hook in `objects/base.py`, and has `quotasv2` check the
driver against a new `QUOTA_DB_DRIVERS` tuple in `neutron.conf.quota`. A new
hacking check, N351, reports any `cfg.CONF` option read outside a function so
this cannot recur unnoticed.

Fix:

* Read config options where they are used, not at import
* hacking: Add N351 for config reads at import time

## Floating IP namespace created twice for one external network

**Title:** L3 agent can build two FipNamespace objects for one external
network and give two routers the same link-local pair

**Affects:** neutron master (2025.1 and later, since the L3 agent moved from
eventlet to threads)

`get_fip_ns` in the DVR code looks up the `FipNamespace` for an external
network, builds one if it is missing and stores it, with no lock around the
three steps. Under eventlet the lookup and store could not be interleaved
with another worker, so this was safe. Since the agent moved to real threads
it is not.

`FipNamespace.__init__` is slow: it takes 64 ms or more, because it sets up
40,000 rule priorities, 8,192 link-local pairs and reads two state files. During
a startup fullsync, two of the 32 pool workers handling routers on the same
external network can both find no namespace and both build one. Each has
allocators seeded from the same state files, so `set.pop()` on two equal sets
hands the same link-local pair to two routers. A unit test that holds two
threads inside the constructor reproduces this.

The fix holds one agent-wide lock across get, build and store. The first
fullsync for an external network is serialised by the constructor cost, once;
later calls only hold the lock for a dictionary lookup.

Fix:

* l3: Serialise FipNamespace creation per external network

## ItemAllocator has no lock and rewrites its state file in place

**Title:** L3 agent ItemAllocator is not thread safe and its state file
writes are not atomic

**Affects:** neutron master (2025.1 and later, since the L3 agent moved from
eventlet to threads)

`ItemAllocator` hands out the floating IP link-local pairs and rule
priorities, and one instance is shared by every router on the agent. It had
no lock, so concurrent workers could allocate the same item or lose an update.
Its `_write` method also truncated the state file and rewrote it, so a
concurrent writer, or a crash part way through, could leave a corrupt file
for the next restart to read.

A test with 64 threads allocating distinct keys failed in 14 of 20 runs on the
old code.

The fix takes a lock in each allocator operation and writes the state to a
temporary file that is then renamed over the old one, so readers see either
the old or the new content.

Separate observation, not fixed by these patches: both allocators use one
agent-global state path, shared by every external network, rather than a
path per network.

Fix:

* l3: Lock ItemAllocator and write its state atomically

## DHCP namespace deleted while other segments still run

**Title:** DHCP agent deletes the network namespace on every segment disable
of a multi-segment network

**Affects:** neutron master (2023.2 and later, since dfe29e6760)

`DhcpLocalProcess._has_running_interfaces` is meant to say whether any
segment of a network still has a running DHCP interface, so that the network
namespace is only removed when the last one goes. The method computes
`bool(cls._interfaces.get(network_id))` but never returns it, so it always
returns `None` and the caller always takes the "nothing is running" branch.

On a multi-segment network, disabling DHCP for one segment therefore deletes
the namespace while the dnsmasq processes of the other segments are still
running inside it. Those segments lose DHCP until something next restarts
them. The fix adds the missing `return`, and a unit test covers both the
running and not-running cases.

Fix:

* dhcp: Return the result of _has_running_interfaces

## DHCP resync throttle never throttles

**Title:** DHCP agent resync_throttle has never delayed a resync

**Affects:** neutron master and every supported stable branch (the code dates
from 2018)

`resync_throttle` is meant to space out consecutive resync passes by at least
that many seconds. `_periodic_resync_helper` builds a new `utils.throttler`
each time round its loop. The throttler keeps its last-call time in a closure,
so a freshly built one has never been called, its `LockWithTimer` starts at
timestamp 0 and it never waits. The option has not had any effect since it
was introduced.

The fix builds the throttled callable once in `__init__` and reuses it in the
loop. A unit test drives two resync events 3 seconds apart with a 10 second
throttle and checks that only the second one sleeps, for the remaining 7
seconds. A release note records that the option now works, which operators
with a small `resync_throttle` and frequent resync reasons may notice.

Fix:

* dhcp: Build the resync throttler once

## Network cached although dnsmasq failed to start

**Title:** DHCP agent marks a network as configured when dnsmasq did not
start

**Affects:** neutron master and every supported stable branch

`DhcpLocalProcess.enable` waits up to 300 seconds for the dnsmasq process to
start. On timeout it logs an error and returns `None`, which is also what it
returns on success. `_call_driver` therefore reported success, and the agent
put the network in its cache and marked the network's ports as DHCP-ready,
even though nothing was serving DHCP. Because the network is cached, nothing
retries it until the next full resync, if there is one.

`enable` now returns `True` or `False`. It returns `False` instead of
raising so that callers which ignore the result, such as `DhcpBase.restart`,
behave as before. `_call_driver` returns `False` for a failed `enable` or
`restart` and schedules a resync for that network, so the start is retried.
Unit tests cover the driver return values and the agent's handling of them.

Fix:

* dhcp: Do not cache a network whose dnsmasq failed to start

## Multi-segment network keeps only the last segment's subnets

**Title:** DHCP agent cached network loses all but the last segment's subnets

**Affects:** neutron master (2024.1 and later, since 06bc855dcf)

For a network whose subnets sit on several segments, `call_driver` runs the
driver once per segment. To do that it assigned `network.subnets` to each
segment's subnets in turn, on the network object it was passed, which is
often the one held in the agent's cache. It never restored the original list,
so after the call the cached network held only the final segment's subnets.
Later code that reads the cache, such as port updates and resyncs, then
works from an incomplete network.

Each segment's driver call now receives a shallow copy of the network
holding only that segment's subnets, and the caller's network is left alone.
The copy deliberately still shares the original `ports` list. `DeviceManager.setup`
appends each segment's DHCP port to that list, and the next segment's stale
device cleanup must see the previous segment's port, or it removes a device
that is in use (bug #2051729). Copying the ports would reintroduce that bug.

Fix:

* dhcp: Keep every segment's subnets on the cached network

## Router whose type changed is swapped for an uninitialized object

**Title:** L3 agent replaces a router whose ha or distributed flag changed
with an object that is never initialized

**Affects:** neutron master (Wallaby and later, since 489e0ead72)

The fix for bug #1892846 made `_process_updated_router` notice when a
router's `ha` or `distributed` flag no longer matches the router the agent
has cached. It then builds a router of the new class with `_create_router`,
stores it in `router_info` and processes it. It never calls `initialize()`
on the new object, so its `radvd` monitor and `process_monitor` stay `None`.
It never deletes the old object either, so the old router's keepalived,
radvd and keepalived-state-change processes keep running.

A router with an IPv6 internal subnet then fails in `enable_radvd` with
`AttributeError: 'NoneType' object has no attribute 'enable'`. The agent
resyncs it, but its ports are cached by then, so the retry does not enable
radvd and the router runs on without router advertisements. A unit test that
uses the real `_create_router` reproduces the AttributeError. The existing
test mocks `_create_router`, so it cannot see it.

The fix deletes the stale router and adds it again, as the DVR HA branch of
the same method already does. The new object is initialized before it is
processed, and the old router's processes are stopped.

Fix:

* l3: Re-create a router whose type changed instead of swapping it

## HA router left half-deleted after a failed initialize

**Title:** L3 agent stops deleting an HA router whose initialize failed
before it created the keepalived manager

**Affects:** neutron master (2025.1 and later, since b164d67f45)

`HaRouter.initialize()` sets the HA port and then builds the conntrackd
manager before the keepalived manager. The conntrackd manager is built even
when conntrackd is disabled, and it indexes the HA port's first fixed IP. An
HA port with no fixed IP therefore makes `initialize()` raise `IndexError`
with `ha_port` set and `keepalived_manager` still `None`.

The agent's `_router_added` then calls `_cleanup_failed_router`, which calls
`delete()`. `destroy_state_change_monitor` only checks for the HA port, so it
builds the monitor's command line, which reads the keepalived instance from
the missing manager and fails with `AttributeError: 'NoneType' object has no
attribute 'config'`. `_cleanup_failed_router` logs it, but the rest of
`delete()` is skipped, including disabling radvd. A unit test that sends an
HA port with no fixed IPs through `_router_added` reproduces it.

The fix returns early from `destroy_state_change_monitor` when there is no
keepalived manager, since the monitor is only spawned after the manager
exists, so the rest of the router is deleted.

Fix:

* l3: Finish deleting an HA router that failed to initialize

## Found but not addressed

These came from the same reading and from two read-only sweeps of the L3
and DHCP agents. Most are races in pure-Python sections shorter than
CPython's 5 ms switch interval, so they cannot fire on a GIL build today but
will under free-threaded Python or if the sections grow. A few are plain
robustness gaps. Each is tagged "latent" (cannot fire today), "real" (can
fire today) or "robustness" (missing error handling or cleanup).

* `neutron/agent/common/resource_processing_queue.py`
  `ExclusiveResourceProcessor.__init__`: unlocked check-then-set on the
  class-level `_primaries` dict, in both agents. Latent.
* `neutron/agent/l3/agent.py` `L3NATAgentWithStateReport._report_state` and
  `neutron/agent/dhcp/agent.py` `DhcpAgentWithStateReport._report_state`,
  via `NetworkCache.get_state`: they iterate the live router or network dict
  from the heartbeat thread while workers add and remove entries, and a
  `RuntimeError` stops the oslo.service looping call for good. Latent, as it
  needs a loop longer than 5 ms, so thousands of entries. The DHCP handler
  additionally treats any `AttributeError` as "server does not support
  report_state", stops the heartbeat and calls `self.run()` again, starting
  duplicate resync and worker threads. That is a robustness bug worth fixing
  on its own.
* `neutron/agent/l3/agent.py` `network_update` and `stop()`, and
  `neutron/agent/l3/l3_agent_extension_api.py`: iterate `router_info.values()`
  on RPC threads while workers modify it. Latent.
* `neutron/agent/dhcp/agent.py` `_periodic_resync_helper` and
  `schedule_resync`: the `needs_resync_reasons` swap is unlocked against
  appends from five thread types. A lost append drops a resync silently, and
  an append during the `items()` loop kills the resync thread, because
  `exception_logger` re-raises. Latent.
* `neutron/agent/dhcp/agent.py` `_reload_bulk_allocations`: the same swap on
  `_network_bulk_allocations`, with the comment "No need to lock ...
  greenthreads multi-task co-operatively" that is no longer true, and the
  only driver call made outside `_SYNC_STATE_LOCK`. Latent; the stale comment
  is real.
* `neutron/agent/linux/external_process.py`
  `ProcessMonitor._check_child_processes` / `_respawn_action`: `.get(service_id)`
  then `[service_id]` with pid-file and psutil I/O in between, so an
  unregister in between raises `KeyError` and skips the rest of that round. A
  concurrent `enable()` from the monitor and the agent on one uuid can also
  double-spawn or orphan a dnsmasq or haproxy. Real.
* `neutron/agent/l3/ha.py` `_enqueue_state_change`: the apply phases of two
  state-change threads for one router (a backup to primary flap) can overlap
  around subprocess waits, leaving a primary without its metadata proxy or
  radvd until the ProcessMonitor respawn. The dedup in
  `enqueue_state_change` assumes the pending thread has popped before the
  next notification arrives. Real but timing-dependent, and pre-existing.
* `neutron/agent/linux/keepalived.py` `KeepalivedInstance` `vips`: rebuilt
  and reassigned by the worker while the port-forwarding extension appends
  from an RPC thread. Latent.
* `neutron/agent/l3/dvr_local_router.py` `_update_arp_entry` callers:
  `_pending_arp_set` is iterated with netlink I/O in the body while ARP RPCs
  add to it, giving a `RuntimeError` and so a resync. Real, pre-existing.
* `neutron/agent/l3/agent.py` `fullsync` flag: set by the heartbeat and RPC
  threads and cleared by the periodic thread after a sync that may predate
  the request. Real, minor.
* `neutron/agent/l3/agent.py` `_process_routers_loop` and
  `neutron/agent/dhcp/agent.py` `_process_loop`: worker futures from
  `pool.submit` are discarded, so an exception escaping the worker function
  vanishes without a log line. For example a haproxy `WaitTimeout` escaping
  `spawn_monitored_metadata_proxy` before `cache.put` leaves a network
  unconfigured with no resync. Robustness.
* `neutron/agent/dhcp/agent.py` `stop()`: pool workers parked in
  `PriorityQueue.get()` are never woken and the pool is never shut down. The
  `yield None, None` sentinel in `each_update_to_next_resource` would raise
  `AttributeError` in a worker that did wake. Robustness.
* `neutron/agent/l3/dvr_fip_ns.py`: both allocators share one agent-global
  state path for every external network (also noted under ItemAllocator
  above). Real, pre-existing.
* `neutron/agent/l3/agent.py` `_router_added`: the router is stored in
  `router_info` before `initialize()` runs, so other threads can find an
  HA router whose `keepalived_manager` is still `None`.
  keepalived-state-change daemons outlive an agent restart, so a
  notification can arrive during the startup sync. ha.py
  `_enqueue_state_change` then sets `ri.ha_state`, whose setter writes to
  `ha_state_path`. That is `None` without the manager, so `open(None)`
  raises a `TypeError` that the setter's `except OSError` does not catch,
  and the state change thread dies before it updates radvd, the metadata
  proxy or the server. The port forwarding extension's push RPC
  (`_process_create`, `_sync_and_remove_fip`) can reach `enable_keepalived`
  and `_add_vip` in the same window. Real but timing-dependent, as the
  window is the namespace setup in `initialize()`. Separately,
  `HaRouter._get_ha_port_fixed_ip_with_subnet` has no callers in tree.
  The ndp_proxy extension is another path in for DVR routers: its RPC
  handler `_handle_notification` in `ndp_proxy.py` reads `ri.fip_ns`
  directly, and again through `_get_resource_by_router`, after checking
  only `get_ex_gw_port()`. `fip_ns` is `None` until the router's first
  `process()`, so an ndp_proxy event for a router not yet processed fails
  with an `AttributeError`.
* `neutron/agent/metadata/driver_base.py` `destroy_monitored_metadata_proxy`
  reads `cfg.CONF.state_path` where everything else uses the passed conf.
  Cosmetic.
