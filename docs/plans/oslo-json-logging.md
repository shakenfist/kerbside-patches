# Plan: JSON logging for oslo.log services in Kolla-Ansible

## Goal

Let operators switch every oslo.log-based OpenStack service in Kolla-Ansible to
structured JSON logging with a single global variable, and have fluentd parse
it correctly, reusing the JSON parser introduced for Kerbside.

This is proposed upstream as its own Gerrit series, from the
`kolla-ansible-json-logging` directory here, built on top of the Kerbside work
in `kolla-ansible-wave-1`.

## Status

Phase 1 is written and verified. Phase 2 and 3 are not started.

| Patch | What | State |
|-------|------|-------|
| patch182 | `check-logs.sh` understands JSON | done |
| patch183 | fluentd JSON path, `openstack_logging_json` | done |
| patch184 | Keystone conversion, the demonstration | done |
| patch185-190 | bulk conversion, six batches | not started |
| patch191 | CI scenario | not started |

Nothing has been pushed to Gerrit, so none of these have a `Change-Id` yet.

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

**patch191: a scenario that turns the toggle on.** Without this the JSON path
is dead code that no job exercises. Model it on `zuul.d/scenarios/kerbside.yaml`
from `patch156`: a scenario name, the `globals-default.j2` stanza setting
`openstack_logging_json: "yes"`, and the `zuul.d/project.yaml` entry.

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

## Unresolved, needs a decision

1. Dedicated `json-logging` CI scenario, or flip an existing scenario over? A
   dedicated one is more honest about what it tests but costs another job;
   flipping an existing one gets coverage free but changes what that job was
   there to test.
