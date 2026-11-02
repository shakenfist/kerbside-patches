# horizonX: third-party Horizon performance patch

This is an archived copy of a patch somebody else wrote. It is kept as
reference material only. Nothing in the build reads this directory, no
`ORDER` file lists it, and it is not one of our patches.

## Provenance

| | |
|---|---|
| File | `horizonx-2025.1.patch` |
| Author | Enrico Kern, stackXperts (`enrico.kern@stackxperts.com`) |
| Published | 2026-10-08, in "How to make Openstack Horizon fly" |
| Post | https://www.thinknerd.de/how-to-make-openstack-horizon-fly/ |
| Download | https://www.thinknerd.de/content/files/2026/10/horizonx-2025.1.patch |
| Fetched | 2026-10-12 |
| sha256 | `0a6ac2623a3706ff10151170fc4b3ceefc69a25728b88f2d4b738c32f932b88c` |
| Licence | Apache 2.0, as stated in the post |

The download is a single `git format-patch` commit,
`cf9dc2065a3fcdbfe5e8e19292d7d9041394befa`. Its message has a subject
line and no body. It touches 15 files: it adds `horizon/utils/cached.py`
and changes `openstack_auth/policy.py`, `openstack_dashboard/api/` and
six dashboard views. The author also publishes a prebuilt image,
`registry.stackxperts.com/horizonx/horizon:2025.1-fast6`. We have not
inspected that image.

On a cloud with roughly 1,500 VMs, 1,300 images, 3,300 ports and 570
networks, the post reports server-side page times falling from 15–94 s
to 1–4 s.

## Status against upstream (checked 2026-10-12)

The patch applies cleanly to Horizon `stable/2025.1` (head 039850556).
It does not apply to master (head d406a0bc9). `_nova.py`, `neutron.py`,
`nova.py` and `placement.py` conflict, and master is 634 commits ahead
of 2025.1. Master Horizon pins openstacksdk 4.21.0; 2025.1 pins 4.4.1.

| # | Problem the post describes | State on master |
|---|---|---|
| 1 | SDK filters `tenant_id`/`id` client-side | Partly fixed (see below) |
| 2 | `retrieve_all=False` ignored by the SDK | Still present |
| 3a | Instances pages fetch the whole image catalogue | Still present |
| 3b | Volume and admin floating IP pages list every server | Still present |
| 3c | `network_list` fetches every subnet in the cloud | Still present |
| 3d | Floating IP list reads all ports despite `port_details` | Still present |
| 3e | Volume detail calls `server_get` once per attachment | Still present |
| 4a | Hypervisors page makes 4 Placement calls per provider | Partly fixed (see below) |
| 4b | Admin networks page makes one DHCP-agent call per network | Still present |
| 5 | No cross-request (memcached) cache of list calls | Still absent |
| 6 | Microversion and extension lookups repeat on every request | Still present |
| 7a | Kolla image leaves the cached template loader off | Still present (kolla) |
| 7b | oslo.policy re-stats policy files on every check | Still present |

Notes on the rows that are only partly fixed:

- **1, SDK filtering.** openstacksdk 4.21 now sends some of these
  filters to the server. `id` became a server-side filter for networks,
  subnets and routers in openstacksdk 53e19fe0a (2025-08). `tenant_id`
  became a server-side alias for `project_id` on ports in openstacksdk
  change 970135. On the Horizon side, change 970219 (master only)
  renamed `tenant_id` to `project_id` in `port_list` and in the floating
  IP manager. `tenant_id` is still client-side for subnets, subnet
  pools, routers and trunks. Horizon master still passes it in
  `router_list` from the project routers and network topology views, and
  in the `trunks()` call inside `port_list_with_trunk_types`.
  `network_list` also still loops over ids and makes one SDK call per
  id. That is now one server-side query per id, rather than one full
  listing per id, but it is still not a batched lookup.
- **4a, Placement.** Change 999208 (also on stable/2026.1 as 1000066)
  skips nested providers, drops the aggregates and traits calls, and
  defers the provider tab. The remaining inventory and usage calls per
  provider still run one after another.
- **7a, kolla template loader.** `openstack_dashboard/settings.py`
  chooses the template loader from `DEBUG` before it reads
  `local_settings.d`. Kolla's horizon image copies
  `local_settings.py.example`, which sets `DEBUG = True`, and only
  kolla-ansible's `_9998-kolla-settings.py` sets the real value. That
  layout dates from kolla 906339 and kolla-ansible 906347 (2024-02).

## Applying it

See the post for a thin-image Dockerfile that layers the patch onto an
existing kolla horizon image. Read the diff before deploying it. The
cross-request cache (row 5) changes how stale Horizon's data can be.
