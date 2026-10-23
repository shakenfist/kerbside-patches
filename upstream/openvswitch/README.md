# openvswitch: python-ovs poller without eventlet

See [../README.md](../README.md) for the layout and disposition policy,
and [../../docs/plans/privsep-memory.md](../../docs/plans/privsep-memory.md)
(step 6p) for the plan this came from.

## What the patch is

`python/ovs/poller.py` imported `eventlet.patcher` at module level, only
to ask whether `select` had been monkey patched. That import loads all of
eventlet, greenlet, dnspython and asyncio in every program that imports
the poller, whenever eventlet is installed. `series/v1/0001-*.patch`
looks the patcher up in `sys.modules` when it is needed instead:
nothing can be monkey patched before eventlet is imported, so behaviour
is unchanged when eventlet is imported and patched, in either order
relative to the poller. It also adds `python/ovs/tests/test_poller.py`.
The `gevent` import a few lines below has the same shape and is left
alone; neutron does not install gevent.

## Why

Neutron loads the poller through ovsdbapp in neutron-api,
neutron-rpc-server and the OVN, Open vSwitch and DHCP agents. python-ovs
is the last eventlet importer in those roles once the rest of the
OpenStack `import-memory` chain lands (futurist, oslo.utils,
oslo.messaging, oslo.service, neutron-lib, neutron). Measured in step 6k
(anonymous RSS, MB, against `import-memory:phase6`, about 2 MB of noise):

| Role | All OpenStack fixes | Plus python-ovs | Saving |
|---|---:|---:|---:|
| neutron-api | 127 | 114 | 13 |
| neutron-rpc-server | 124 | 115 | 9 |
| neutron-ovn-agent | 104 | 93 | 11 |
| neutron-openvswitch-agent | 124 | 113 | 11 |
| neutron-dhcp-agent | 103 | 92 | 11 |

So about 9 to 13 MB per process. Alone, before the OpenStack chain
lands, it saves nothing in neutron, because other imports still load
eventlet.

## How it was tested

Against `base_sha`, in a venv with `python/test_requirements.txt`,
`eventlet` and `flake8`, after generating `python/ovs/dirs.py` from its
template and `python/ovs/flow/ofp_fields.py` with
`build-aux/gen_ofp_field_decoders` (both are build products):

- `python3 -m pytest python/ovs` (what `tests/pytest.at` runs): 159
  passed, 46 skipped.
- `python3 -m pytest python/ovs/tests/test_poller.py`: 4 passed. With
  the old `poller.py` restored, the "import does not load eventlet"
  test fails; with the `sys.modules` lookup stubbed to return `None`,
  both patched-eventlet tests fail.
- `flake8` on both files with Makefile.am's `FLAKE8_IGNORE` list: clean.
- `utilities/checkpatch.py -1`: clean.
- A full build in a `debian:13` container and
  `make check TESTSUITEFLAGS='-k python'` (the autotest suites that drive
  the Python IDL, jsonrpc, unixctl and vlog code through the poller):
  367 passed, 1 skipped (`tests/pytest.at`, whose pytest unit tests were
  run in the venv instead, above).

## How to send it

Open vSwitch takes patches on the ovs-dev list, one patch per email
(`Documentation/internals/contributing/submitting-patches.rst`). The
patch already carries `Signed-off-by: Michael Still <mikal@stillhq.com>`
(the DCO) and the `Assisted-by:` tag OVS asks for on AI-assisted patches
(model name, then tool name, no email address). Review it, then:

```
git clone https://github.com/openvswitch/ovs && cd ovs
git checkout 6fcb20dc6b0291bd033741debfc9de68317b742e
git am /path/to/upstream/openvswitch/series/v1/0001-*.patch
git send-email --to=dev@openvswitch.org -1
```

Rebase onto current `main` first if it has moved, and update `base_sha`.
A v2 goes in `series/v2/` with `[PATCH v2]` in the subject and a note of
the changes below the `---` line.
