# Plan: JSON logging for oslo.log services in Kolla-Ansible

## Goal

Let operators switch every oslo.log-based OpenStack service in Kolla-Ansible to
structured JSON logging with a single global variable, and have fluentd parse
it correctly, reusing the JSON parser introduced for Kerbside.

This is proposed upstream as its own Gerrit series, in a new directory here,
built on top of the Kerbside work in `kolla-ansible-wave-1`.

## Why this is possible now

Before the Kerbside work, Kolla-Ansible had no JSON parser anywhere in its
fluentd configuration -- every input used a `multiline` or `regexp` parser.
`_patches/patch147` added the first one, and `_patches/patch181` showed it
generalises by reusing it for etcd. This series is the third use, and the one
that makes the capability general.

## Dependency modelling

The new directory's patches apply to **pristine upstream**, because
`_build/apply-patches-and-test.sh` creates each project's destination branch
from `source_sha` rather than from a dependency's branch --- `depends_on:` in
`config.yaml` only means "build that project first", it does not stack
branches.

The dependency on the Kerbside JSON parser is therefore expressed the Gerrit
way, in the first patch's commit message:

```
Depends-On: I73a7a0b521085fd4023b08ab6f8a137d53a8712a
```

That is `patch147`, the change that introduces the JSON parser.

Two caveats to settle before the first push:

- `patch181` (etcd) has no `Change-Id` yet. If we would rather depend on the
  etcd change --- it is the last one to touch `02-parser.conf.j2`, so it is the
  more accurate textual ancestor --- it has to be pushed to Gerrit first and
  its minted `Change-Id` captured back into
  `_patches/patch181-...patch-message`.
- `tools/gerrit-pre-push-lint` warns when `Depends-On` references the same
  repository. That warning is expected here and should be ignored: the two
  series live on different branches, so the parent-commit relationship Gerrit
  would normally use is not available.

## The two traps found while scoping

### check-logs.sh goes blind, it does not fail loudly

`tests/check-logs.sh` finds errors with:

```bash
sudo grep -E " $2 " $1        # " ERROR ", " CRITICAL ", " WARNING "
```

A text-format oslo line is `2026-09-11 07:41:03.696 123 ERROR nova.compute...`,
which matches. The JSON equivalent is `{"levelname": "ERROR", ...}`, which does
**not**. Turning on JSON logging without touching this script does not fail the
job --- it silently stops detecting ERROR and CRITICAL messages in every
converted service.

This is the single most important thing in the series and it must land in the
structural patch, before any service is converted and well before a CI scenario
turns the toggle on.

### asctime has no sub-second resolution

oslo's `JSONFormatter` builds `asctime` with the configured `log_date_format`,
which defaults to `%Y-%m-%d %H:%M:%S` --- no milliseconds. Parsing event time
from `asctime` would quietly lose precision the current text parser keeps.

Use `created` (an epoch float, full precision) with `time_type float` instead.

## Field mapping

oslo `JSONFormatter` emits:

```json
{"message": "...", "asctime": "2026-09-11 07:41:03", "name": "nova.compute.manager",
 "levelname": "INFO", "levelno": 20, "pathname": "...", "filename": "...",
 "module": "...", "lineno": 42, "funcname": "spawn", "created": 1789076463.6965241,
 "msecs": 696.0, "relative_created": 115.4, "thread": 140635406628736,
 "thread_name": "MainThread", "process_name": "MainProcess", "process": 1265076,
 "traceback": null, "hostname": "kasm", "error_summary": "", "context": {}, "extra": {}}
```

Mapped onto the names the rest of the pipeline uses:

| oslo field  | pipeline field  | notes |
|-------------|-----------------|-------|
| `message`   | `Payload`       | |
| `levelname` | `log_level`     | |
| `name`      | `python_module` | matches what the text regex captures |
| `hostname`  | `Hostname`      | already set by the `infra.*` transformer, prefer oslo's |
| `created`   | event time      | `time_type float`, not `asctime` |
| `context`   | request fields  | explodes into `request_id`, `tenant_id`, `user_id`, ... |

The `context` mapping is a strict improvement on today's behaviour: the text
regex only captures those fields when a line carries a `[req-...]` prefix in
exactly the expected shape, whereas the JSON context is always structured.

## Patch breakdown

Small and reviewable first, bulk conversion after the mechanism is agreed.

### Phase 1 --- structure, no service converted

**Patch 1: teach check-logs.sh the JSON format.**
Make the level checks recognise both formats. Standalone and independently
correct --- it is a no-op until something logs JSON, so it can land first and
alone. Carries the `Depends-On`.

**Patch 2: add the fluentd JSON path for oslo services.**
A `{% if %}` in `00-global.conf.j2` selecting the `json` parser over the
multiline one, and an `<filter>` in `02-parser.conf.j2` doing the rename above.
Gated on a new `openstack_logging_json` global, default `"False"`, added to
`group_vars/all/common.yml` and `etc/kolla/globals.yml`.

All 25 oslo services share the single source in `00-global.conf.j2`, so this is
one conditional, not 25. Nothing downstream breaks: the `openstack_python` tag
from `01-rewrite.conf.j2` is not consumed by any other filter or output, and
the fields the current source extracts are produced but never read anywhere in
the fluentd config.

**Patch 3: convert one service (keystone) as the demonstration.**
Adds `keystone_logging_json: "{{ openstack_logging_json }}"` and
`use_json = {{ keystone_logging_json }}` beside the existing
`debug = {{ keystone_logging_debug }}`. Keystone because every job deploys it
and its template is small.

This is the patch that proves the mechanism end to end and gives reviewers
something concrete to argue with before we touch 27 more files.

### Phase 2 --- bulk conversion, one patch per block

Same mechanical change per service, following the established
`openstack_logging_debug` pattern exactly: 28 template files, 32 insertion
sites, ~25 `defaults/main.yml` entries.

| Patch | Block | Services |
|-------|-------|----------|
| 4 | Core | glance, placement |
| 5 | Compute | nova, nova-cell, ironic, cyborg, masakari |
| 6 | Network | neutron, octavia, designate |
| 7 | Storage | cinder, manila |
| 8 | Telemetry | ceilometer, aodh, gnocchi, cloudkitty |
| 9 | Orchestration and the rest | heat, magnum, tacker, trove, blazar, mistral, watcher, barbican |

### Phase 3 --- CI

**Patch 10: a scenario that turns the toggle on.**
Without this the JSON path is dead code that no job exercises. Model it on
`zuul.d/scenarios/kerbside.yaml` from `patch156`: a scenario name, the
`globals-default.j2` stanza setting `openstack_logging_json: "yes"`, and the
`zuul.d/project.yaml` entry.

Open question for review: a dedicated `json-logging` scenario, or flip an
existing scenario over? A dedicated one is more honest about what it tests but
costs another job; flipping an existing one gets coverage free but changes what
that job was there to test.

## Estimate

Roughly half a day of work. The Ansible side is tedious rather than hard and
follows a pattern already in the tree; the fluentd side is small because of the
single shared source. The genuine work is Patch 1 and the CI scenario, which is
where the risk lives.

## Verification for each patch

- `_build/test-apply.sh --skip-tests kolla-ansible-json-logging`
- `tools/check-kolla-ansible-lint.sh` for the ansible-lint and j2lint gates
- for Patches 1--3, run the rendered templates under fluentd 1.16 against real
  oslo JSON output, the same way the Kerbside and etcd parsers were checked
  (produce sample lines with `oslo_log.formatters.JSONFormatter` in a venv)
- for Patch 1, verify both formats are detected by feeding it a text log and a
  JSON log containing an ERROR

## Unresolved, needs a decision

1. Depend on `patch147` (as written above) or on `patch181` once it has a
   `Change-Id`?
2. Dedicated CI scenario or convert an existing one?
3. Does `openstack_logging_json` want to be per-service overridable
   (`<svc>_logging_json`) from the start? The plan assumes yes, matching
   `openstack_logging_debug`, but it doubles the defaults churn.
