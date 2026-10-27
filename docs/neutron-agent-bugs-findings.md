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
