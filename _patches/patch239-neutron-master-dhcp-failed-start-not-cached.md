# patch239: dhcp: Do not report ports of a network with no dnsmasq

- **Status:** draft
- **Change:** neutron, Change-Id `I8ef0d5cfcddf3b352eda3688dee14595648ae643`
- **Post:** as a reply to Rodolfo Alonso's patch set 3 comment, once patch
  set 4 is uploaded.

Everything below the line is the comment.

---

Thank you, that is a real hole and not a cosmetic one. Patch set 4 closes it
at the root rather than at that one call site.

**The path you describe.** Confirmed. `_port_update` calls
`reload_allocations`, which promotes a change to the agent port's IP
addresses to `driver_action = 'restart'`, and then discarded what
`call_driver` returned. With the previous patch set that restart could fail,
schedule a resync and return `False`, and the port was still added to
`dhcp_prio_ready_ports` and reported to the server as provisioned with no
dnsmasq running for it.

**A second instance, which the resync reached.** `sync_state` ends with
`self.dhcp_ready_ports |= set(self.cache.get_port_ids(only_nets))`,
regardless of whether `safe_configure_dhcp_for_network` succeeded. For a
network that was never cached this is harmless, since its ports are not in
`port_lookup`. But the `refresh_dhcp_helper` case the previous patch set did
fix leaves the stale network in the cache, so the resync it scheduled
reported those ports anyway a moment later. Gating `reload_allocations`
alone would not have been enough.

**What patch set 4 does.** `_call_driver` is now the single place that
decides a network has no DHCP service: on a `False` from `enable` or
`restart` it drops that network's ports from both pending sets, schedules
the resync, and returns `False`. The four callers that mark ports
DHCP-ready then honour that result:

| Caller | Before | Now |
|--------|--------|-----|
| `configure_dhcp_for_network` | skipped its body, told its caller nothing | returns whether the service is running |
| `refresh_dhcp_helper` | gated the `restart` branch only | gates both driver calls |
| `reload_allocations` | ignored the result | returns early, as you asked |
| `sync_state` | reported every port it knows | reports the ports of the networks it configured |

No port is stranded by this: the resync each failure schedules reports the
network's ports once its DHCP service is running.

**One deliberate widening.** These callers treat any falsy result as
failure, not just `False`, which is what `configure_dhcp_for_network` has
always done for `enable`. So a driver call that raised, which already meant
the network was not cached, now also means its ports are not reported. That
includes `exceptions.Conflict`, which schedules no resync on the grounds
that a network status event will follow; for those ports the next event or
periodic resync reports them. Say the word if you would rather this
gated strictly on `rv is False` and left the exception paths alone.

**Checks.** `neutron.tests.unit.agent.dhcp.test_agent` and
`neutron.tests.unit.agent.linux.test_dhcp` pass, with new tests for the
`_port_update` restart failure, the `sync_state` skip, the withdrawal of
pending ports, the `refresh_dhcp_helper` reload failure and the two return
values. Each of those guards was also checked by breaking the fix it covers
and confirming the test fails.
