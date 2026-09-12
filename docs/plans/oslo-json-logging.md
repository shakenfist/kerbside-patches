# Plan: JSON logging for oslo.log services in Kolla-Ansible

## Goal

Let operators switch every oslo.log-based OpenStack service in Kolla-Ansible to
structured JSON logging with a single global variable, and have fluentd parse
it correctly, reusing the JSON parser introduced for Kerbside.

This is proposed upstream as its own Gerrit series, from the
`kolla-ansible-json-logging` directory here, built on top of the Kerbside work
in `kolla-ansible-wave-1`.

## Status

All three phases are written and verified. Eleven patches, in
`kolla-ansible-json-logging/ORDER`:

| Patch | What | Files |
|-------|------|-------|
| patch182 | `check-logs.sh` understands JSON | 1 |
| patch183 | fluentd JSON path, `openstack_logging_json` | 5 |
| patch184 | Keystone, the demonstration conversion | 1 |
| patch185 | core batch: glance, placement | 3 |
| patch186 | compute batch: nova, nova-cell, ironic, cyborg, masakari | 6 |
| patch187 | network batch: neutron, octavia, designate | 3 |
| patch188 | storage batch: cinder, manila | 2 |
| patch189 | telemetry batch: ceilometer, aodh, gnocchi, cloudkitty | 4 |
| patch190 | heat, magnum, tacker, trove, blazar, mistral, watcher, barbican | 9 |
| patch191 | turn the toggle on in the telemetry CI scenario | 1 |
| patch192 | document the option in the central logging guide | 1 |

All eleven apply to pristine upstream; ansible-lint, bashate, j2lint and doc8
pass.

Nothing has been pushed to Gerrit, so none of these have a `Change-Id` yet.
When they are pushed, every minted `Change-Id` has to be captured back into
the corresponding patch file's commit header --- see the warning below.

## Why this is possible now

Before the Kerbside work, Kolla-Ansible had no JSON parser anywhere in its
fluentd configuration -- every input used a `multiline` or `regexp` parser.
`_patches/patch147` added the first one, and `_patches/patch181` showed it
generalises by reusing it for etcd. This series is the third use, and the one
that makes the capability general.

## Dependency modelling

The directory's patches apply to **pristine upstream**, because
`_build/apply-patches-and-test.sh` creates each project's destination branch
from `source_sha` rather than from a dependency's branch --- `depends_on:` in
`config.yaml` only means "build that project first", it does not stack
branches.

The dependency on the Kerbside JSON parser is therefore expressed the Gerrit
way, in patch182's commit message:

```
Depends-On: https://review.opendev.org/c/openstack/kolla-ansible/+/1005370
```

That is change 1005370, the etcd change, which is the last one to touch
`02-parser.conf.j2` and so the accurate textual ancestor. Its `Change-Id` is
`I1f8c22590904dc94fe3695f7550a9aee56c30a39`, now recorded in patch181 itself.

**Where a Change-Id has to live.** In the patch file's own commit header, not
in the `-message` file. `apply-patches-and-test.sh` regenerates `-message`
from the patch header on every build, so a Change-Id written only into the
message file is silently overwritten and the next push mints a second review
for the same patch. This bit us once already.

One expected warning: `tools/gerrit-pre-push-lint` flags `Depends-On` when it
references the same repository. Ignore it here --- the two series live on
different branches, so the parent-commit relationship Gerrit would normally use
is not available to us.

## What the implementation established

### The toggle has to be global

All ~25 oslo.log services are tailed by **one** `<source>` in
`conf/input/00-global.conf.j2`, whose `path` is a comma-joined list of every
service's log directory. One source means one `<parse>` block. A per-service
`<svc>_logging_json` override, matching how `<svc>_logging_debug` works, would
therefore let an operator put two formats under a single parser, and the
service that disagreed with the parser would simply not be collected.

So the service templates reference `openstack_logging_json` directly. This
settles what was previously an open question in this plan, and it is worth
saying explicitly in review, because the asymmetry with `_logging_debug` is
the first thing a reviewer will query.

### Event time comes from `created`, not `asctime`

oslo's `JSONFormatter` builds `asctime` with `log_date_format`, which defaults
to `%Y-%m-%d %H:%M:%S` --- no sub-second component at all. `created` is an
epoch float with full resolution. Parsing time from `asctime` would quietly
lose precision the existing text parser keeps, so the input uses
`time_key created` with `time_type float`.

### The request context is safe to keep whole

`JSONFormatter` builds its `context` member from
`RequestContext.get_logging_values()`, which redacts `auth_token` to `***`.
Verified directly rather than assumed, because the alternative was writing
bearer tokens into the log store. The context is kept intact, and the four
fields the text parser used to extract are lifted back to the top level.

### check-logs.sh goes blind, it does not fail loudly

`tests/check-logs.sh` finds errors with `sudo grep -E " $2 "`, which matches a
text line like `... 123 ERROR nova.compute...` but not `"levelname": "ERROR"`.
Turning on JSON logging without fixing this does not fail the job --- it
silently stops detecting ERROR and CRITICAL messages in every converted
service. That is why patch182 lands first and alone.

Measured, before and after, against real `JSONFormatter` output:

| | text log | JSON log |
|--|--|--|
| before patch182 | ERROR found | **nothing found at any level** |
| after patch182 | ERROR found | ERROR, CRITICAL, WARNING, INFO all found |

Note that patch181 also touches `check-logs.sh`, removing the
`/var/log/kolla/etcd/etcd.log` exemption, but in a different part of the file,
so the two do not collide.

### j2lint wants nesting inside the delimiter

A nested jinja statement must be written `{%     if ... %}`, with the extra
four spaces *after* `{%`, not as an indented `    {% if ... %}`. Indenting the
delimiter fails `jinja-statements-indentation` at every indentation width, and
the error text ("expected 5, got 1") does not make the distinction obvious.
`conf/output/03-opensearch.conf.j2` is the upstream example to copy. The style
also keeps stray whitespace out of the rendered config.

## Field mapping

Confirmed against real output, not documentation:

| oslo field | pipeline field | notes |
|------------|----------------|-------|
| `message` | `Payload` | `msg` and `args` duplicate it and are dropped |
| `levelname` | `log_level` | |
| `name` | `python_module` | matches what the text regex captures |
| `process` | `Pid` | |
| `created` | event time | `time_type float`; **not** `asctime` |
| `context.request_id` | `request_id` | |
| `context.global_request_id` | `global_request_id` | |
| `context.user` | `user_id` | the key is `user`, not `user_id` |
| `context.project_id` | `tenant_id` | |

Everything else oslo emits --- `asctime`, `levelno`, `pathname`, `filename`,
`module`, `lineno`, `funcname`, `msecs`, `thread_name`, `traceback`,
`error_summary`, the full `context` and `extra` --- is left in place. Keeping
it is the point of logging JSON.

`Hostname` stays as the fluentd transformer sets it, for consistency with
every other input; oslo's own lowercase `hostname` is left alongside it.

## Phase 2 --- bulk conversion, batched by project

27 template files across 24 roles remain. Batching by project rather than by
file keeps each patch reviewable and, in OpenStack, puts each one in front of
the cores who know that project.

| Patch | Batch | Roles | Files |
|-------|-------|-------|-------|
| 185 | Core | glance, placement | 3 |
| 186 | Compute | nova, nova-cell, ironic, cyborg, masakari | 6 |
| 187 | Network | neutron, octavia, designate | 3 |
| 188 | Storage | cinder, manila | 2 |
| 189 | Telemetry | ceilometer, aodh, gnocchi, cloudkitty | 4 |
| 190 | Orchestration and the rest | heat, magnum, tacker, trove, blazar, mistral, watcher, barbican | 9 |

Each is the same one-line addition per template:

```
use_json = {{ openstack_logging_json }}
```

beside the existing `debug = {{ <svc>_logging_debug }}`. No `defaults/main.yml`
changes, since there is no per-service variable.

## Phase 3 --- CI

**patch191 turns the toggle on in the existing `telemetry` scenario** rather
than adding one. That is a single line in
`tests/templates/globals-default.j2`.

The telemetry scenario deploys the OpenStack core --- Keystone, Glance, Nova,
Neutron --- as well as Ceilometer, Aodh and Gnocchi, so one job exercises four
of the six conversion batches against services that really emit oslo.log
output. It adds no job, where a dedicated scenario would add six.

**`prometheus-opensearch` is the trap here, and the first attempt fell into
it.** It looks like the obvious home, being the only scenario that sets
`enable_central_logging`. But `tests/run.yml` reads

```yaml
openstack_core_enabled: "{{ scenario not in
    ['bifrost', 'mariadb', 'prometheus-opensearch'] }}"
```

so that scenario deploys no OpenStack service at all --- its
`scenario_images_core` list has no Keystone, Nova, Glance, Neutron or Cinder.
Setting the option there sets a flag in a deployment with nothing to log.

Central logging is not needed for the assertions that matter. `check-logs.sh`
runs in every job and already treats fluentd's `pattern not matched` warnings
and error-level messages as critical, so a service whose format the parser
cannot read fails the job. Shipping to OpenSearch would add only the further
claim that records arrive with the expected field names.

**Known gap.** The conversion patches touch role templates that are not in the
scenario's file matcher, so they do not trigger those jobs themselves. A
conversion that went wrong would not be caught until something else ran the
scenario. Widening the matcher to 24 role directories would make those jobs
run on most Kolla-Ansible changes, which is a worse trade.

## Phase 4 --- documentation

**patch192 adds a "Log format" section to the central logging guide.** The
guide already tells operators to search on `Hostname`, `Payload` and
`programname`, so the section leads with those being unchanged, and records
the two constraints worth knowing in advance: the setting covers every
oslo.log service at once, and services that do not use oslo.log ignore it.

### Horizon is the one service not converted

`fluentd_input_openstack_services` tails 24 services and this series converts
23. Horizon is the exception, because it logs through Django rather than
oslo.log, and a non-JSON line in the shared input does produce
`pattern not matched`, which `check-logs.sh` treats as critical.

It is not a defect, because Horizon writes nothing to the files that input
reads. Two independent checks say so: `check-logs.sh` grants Horizon no
exemption and fails a job for any log file fluentd did not tail, and in the
*current* text format a Django-formatted line already trips
`got incomplete line before first line`, also critical. If Horizon were
writing there, the gate would be red today. The residual risk is an operator
who configures Horizon to log to a file in that directory, which patch192
warns about.

## Verification for each patch

- `_build/test-apply.sh --skip-tests kolla-ansible-json-logging`
- `tools/check-kolla-ansible-lint.sh kolla-ansible-json-logging` for the
  ansible-lint, bashate and j2lint gates
- for the fluentd patches, render the real templates and run them under
  `fluent/fluentd:v1.16-1` against output from `oslo_log`'s own
  `JSONFormatter`, the same way the Kerbside and etcd parsers were checked

## Decided

- **Depend on `patch181`, not `patch147`**, and by URL rather than bare
  Change-Id, matching how patch147 writes its own `Depends-On`.
- **No per-service `<svc>_logging_json`.** One shared fluentd input makes it
  unimplementable rather than merely redundant.
- **Six conversion batches grouped by project**, not one patch per role and
  not a single bulk commit.
- **Reuse the `telemetry` scenario** rather than adding a dedicated one, and
  not `prometheus-opensearch`, which deploys no OpenStack services.
- **Do not special-case Horizon.** It cannot reach the JSON parser, so
  excluding it would be complexity guarding against nothing.

## Unresolved

Nothing blocking. The series is ready to push.
