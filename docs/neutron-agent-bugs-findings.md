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
