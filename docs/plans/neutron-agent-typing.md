# Plan: incremental mypy coverage for the neutron L3 and DHCP agents

## Goal

Make mypy actually check the neutron L3 and DHCP agent code, one module
at a time, so that the class of bug where a function returns `None`
where a caller expects a value (patches 237 and 239 of
[neutron-agent-bugs](neutron-agent-bugs.md)) is caught in the pep8 job
instead of by someone reading the code. Do it as a sequence of small
changes that each pass `tox -e pep8` on their own, with no more than a
handful in review at once, so the neutron team sees a ratchet rather
than a flood.

## Status

| Phase | What | Status | Merged |
|-------|------|--------|--------|
| 1 | Measure in tox, then enable `check_untyped_defs` for the modules that already pass (`neutron-agent-typing-base`) | Not started | |
| 2 | DHCP agent: interface return annotations and the remaining errors (`neutron-agent-typing-dhcp`) | Not started | |
| 3 | L3 agent, the single-inheritance files (`neutron-agent-typing-l3`) | Not started | |
| 4 | L3 agent, the mixin files (`neutron-agent-typing-l3-mixins`) | Proposed | |
| 5 | Push audit | Not started | |

Phase 1 is the only one with a fixed shape. Phases 2 to 4 are sized
from a scratch measurement (see Situation) and each one is re-planned
from the previous phase's `tox -e pep8` output before its steps run.

## Situation

Measured against upstream neutron master `61f6db5ac9` (2026-10-27),
the same base as [neutron-agent-bugs](neutron-agent-bugs.md), on
2026-10-28.

- **mypy already runs, and already votes.** It was added as a
  non-voting job in September 2024 (943d26da20), made voting in
  October 2024 (fb52de507d), folded into the pep8 job (919b4f6d68) and
  since March 2026 runs from `tox -e pep8` (517a51bb30). The
  configuration in `pyproject.toml` is `files = "neutron"` with the
  tests excluded, `warn_unused_ignores`, and `import-untyped` and
  `var-annotated` disabled "gradually as progress is made". There are
  no `[[tool.mypy.overrides]]` sections and no strictness flags.
- **It checks almost nothing in the agents.** mypy skips the body of
  any function without annotations unless `check_untyped_defs` is on.
  Across the non-test tree 54 of 9,140 function definitions carry an
  annotation; in `neutron/agent/l3` and `neutron/agent/dhcp` it is 0
  of 682. Run as configured over the L3 and DHCP agent files, mypy
  reports nothing.
- **Two of the nine neutron-agent-bugs patches are typing-shaped.**
  Adding only `-> bool` to `DhcpLocalProcess.enable` and
  `_has_running_interfaces` in the unpatched `dhcp.py` makes mypy
  report `Missing return statement` at both (`dhcp.py:276` and
  `:312`). The other seven (the throttler, the segment copy, the two
  DVR races, the config-at-import check and the switch interval) are
  not things a type checker sees. The claim this plan makes upstream
  is therefore narrow: return annotations on the driver and router
  interfaces, plus `check_untyped_defs`, would have caught two real
  bugs in code that has had no annotations for a decade.
- **The backlog is small and lopsided.** With `check_untyped_defs`
  on, `import-untyped` and `var-annotated` still disabled, and a
  scratch venv that has mypy, oslo.config, oslo.utils and neutron-lib
  but not the rest of neutron's requirements, the agent tree reports
  130 errors in 30 files; 53 files are already clean, 19 of them in
  `agent/l3` and `agent/dhcp`. The three mixin-heavy L3 files hold
  126 of the errors when run alone (`ha_router.py` 51,
  `dvr_local_router.py` 41, `ha.py` 34); the DHCP agent and its
  driver hold 15 (`agent/dhcp/agent.py` 8, `linux/dhcp.py` 7); the
  rest of `agent/l3` holds under 70 spread over nine files, none above
  13. The counts move with which modules are analysed together and
  with which dependencies resolve, so phase 1 re-measures inside the
  tox environment before anything is written.
- **Two populations of error.** About half are `union-attr` and
  `index` on values that came from `dict.get()` or an attribute that
  may be `None` (`Item "None" of "Any | None" has no attribute
  "get_name"`, `Value of type "Any | None" is not indexable`). Each
  one is either a real latent crash or a missing narrowing, and
  deciding which is the useful work. The other large block is
  `attr-defined` and `call-arg` from the mixin style:
  `AgentMixin.__init__` calling `super().__init__(host)` and reading
  `self.conf` it never defines. Those are false positives for the
  code as written and need either a `Protocol` describing what the
  mixin expects of its host or a targeted ignore, and either choice
  is a style discussion with the core team. The `override` errors are
  real signature drift (`Namespace.create` versus `DvrSnatNamespace`,
  `FipNamespace` and `HaRouterNamespace`; `BaseRouterInfo.delete`
  versus `RouterInfo.delete`).
- **Precedents.** Nova runs `check_untyped_defs = true` over an
  explicit file list in `pyproject.toml` and grows the list. Neutron
  already points mypy at the whole tree, so its equivalent is a
  per-module override, which is also what the "gradually enable"
  comment in `pyproject.toml` anticipates. Ihar's 2024 enabling series
  was eight changes, the largest touching 22 files, and it landed; the
  OVN agent got its first annotations in June 2026 (3d42164244) in a
  two-file change. That is the size of change this team has accepted.
- **Dependencies ship little typing.** oslo.config and oslo.utils
  carry `py.typed`; neutron-lib does not, so everything that crosses
  into neutron-lib is `Any` and `import-untyped` must stay disabled.
  That caps how much a strict mode could find and is a reason to stop
  at `check_untyped_defs` plus interface annotations rather than
  chase `disallow_untyped_defs`.

## Dependencies on changes already in flight

The typing changes cannot all be published against bare master, and
the plan must say so for each series before Mikal repushes:

- **Phase 2 depends on 1012616 and 1012618** (patches 237 and 239 of
  the `dhcp-agent-fixes` topic). `-> bool` on `_has_running_interfaces`
  and on `DhcpBase.enable` and its subclasses is exactly the
  annotation that fails mypy on master today, because on master those
  functions do return `None`. If those two changes have merged when
  phase 2 is published, nothing is needed. If they have not, the
  phase 2 series is authored on top of them and its root patch carries
  `Depends-On:` footers for both change URLs; only the root, because
  the chained patches inherit it through their git parent and
  repeating it has confused reviewers before. The `config.yaml`
  `depends_on` field stays empty: that
  field names another series in this repository, not a Gerrit change.
- **Phase 3 overlaps 1012621 textually.** Patch 241 rewrites
  `dvr.py:21-43` (`get_fip_ns` and the lock) and the scratch run's
  `dvr.py` errors sit at lines 24, 36 to 38, 44 and 48, inside or
  beside those hunks. A phase 3 patch for `dvr.py` written against
  master would conflict with 241 in Gerrit, so it is authored on top
  of 1012621 and the phase 3 root carries `Depends-On:` for it unless
  it has merged. `item_allocator.py` (1012622) is already clean and is
  not touched. `dvr_fip_ns.py` is touched by neither 241 nor 242.
- **Both agents' `agent.py` are touched by the import-memory topic**
  (1012169, 1012170, 1012182, tracked in
  [privsep-memory](privsep-memory.md)) and by the merged
  `pending_updates` changes. The import changes alter import blocks,
  not function bodies, so a conflict is unlikely but is checked at
  authoring time by applying the phase's series over those changes'
  current revisions; if it conflicts, the same Depends-On rule
  applies.
- **Phase 1 depends on nothing.** It changes `pyproject.toml` only
  and names modules the in-flight changes do not touch, which is one
  reason it goes first and can be pushed while the agent-bugs topics
  are still in review.

`tools/check-depends-on.py` (offline in pre-commit, online by hand)
validates the footers; run it online once before each repush so an
abandoned or re-uploaded dependency is caught.

## Mission and problem statement

Produce up to four short series in `_patches/`, each applying to the
pinned `source_sha` (or to the in-flight change it depends on, as
above), each passing `tox -e pep8` at every patch, so that Mikal can
review them by hand and run `_build/repush-openstack.sh`. Each series
is published only when the previous one has merged or is clearly
converging, and never more than five typing changes are in review at
once. Any real bug the annotations expose becomes its own fix patch
with a bug draft in `docs/neutron-agent-bugs-findings.md`, placed
before the annotation patch that found it, so the typing series is
visibly paying for itself.

Not covered: `disallow_untyped_defs` or any strict mode; annotating
neutron-lib; the OVN agent and the OVS agent (`agent/l2`,
`agent/ovn`), which have their own owners; the test tree.

## Open questions

- **Override versus allowlist.** Default: `[[tool.mypy.overrides]]`
  blocks with `module = [...]` and `check_untyped_defs = true`, one
  block, modules listed alphabetically, next to the existing
  "gradually enable" comment. An explicit `files` list, as nova has,
  would narrow what is checked today and is the wrong direction for a
  tree that already runs mypy everywhere.
- **Mixins.** Default: phase 4 is `Proposed` until phases 1 to 3 have
  had review. If reviewers are warm, offer a `Protocol` for the agent
  attributes the L3 mixins read; if not, exclude the three files by
  name in the override comment and stop there. Do not open the
  question on Gerrit before there is a merged phase to point at.
- **How much to annotate.** Default: return types on the base-class
  and driver interfaces (`DhcpBase`, `Namespace`, `BaseRouterInfo`,
  the L3 extension hooks) and nothing on bodies unless mypy needs a
  narrowing. Reviewers have accepted interface annotations
  (3d42164244) and have not been offered body annotations; keep it
  that way until asked.

## Conventions every step follows

- Work in the neutron worktree named in the brief, never in `src/` or
  in `_patches/` directly. The worktree is at `source_sha`, or at the
  in-flight change the phase depends on, fetched from Gerrit by its
  current revision and recorded in the execution notes.
- One commit per patch. Message: subject 50 characters ideal, 72
  maximum, prefixed by the area (`mypy:`, `dhcp:`, `l3:`); body at
  most two paragraphs of at most four 72-column lines; footers
  `Assisted-By: <model> (<effort> effort)`, `Signed-off-by: Michael
  Still <mikal@stillhq.com>`, and `Change-Id` produced by running
  `tools/commit-msg.hook` from this repository over the message file
  before `git commit -F`. Never `Co-Authored-By` for an LLM. No
  `Prompt:` paragraph.
- `Depends-On: https://review.opendev.org/c/openstack/neutron/+/NNNNNNN`
  on the series root only, when the Dependencies section says so and
  the change has not merged at authoring time; re-check before the
  repush.
- Every patch passes `tox -e pep8` on its own, not just the series
  tip, because each one is a self-contained ratchet step and a
  reviewer may merge them out of order.
- `tox -e py3 -- <test module>` for any patch that changes behaviour
  (a fix patch, or a narrowing that changes a code path). Pure
  annotation and configuration patches need pep8 only.
- Patch file: `git show <sha> > _patches/patchNNN-neutron-master-
  <slug>.patch`, then `python3 tools/extract-commit-message`,
  `tools/recount-patch.py --check`, `tools/check-depends-on.py
  --offline`, and `tools/check-llm-attribution.py` over the new files.
- Series directory: `config.yaml` copied from
  `neutron-dhcp-agent-fixes/` with `destination_branch` (the Gerrit
  topic, `agent-typing` for every phase), `release`
  (`master-<series>`), `source_sha` and `previous_source_sha` set, and
  an `ORDER`.
- `_build/test-apply.sh --skip-tests <series>` must pass before the
  step reports.
- Leave the work uncommitted in this repository; the management
  session reviews and commits each step.

## Phase 1 --- measure, then enable what already passes

Series `neutron-agent-typing-base/`, one patch (246), neutron worktree
`neutron-typing-base` at `source_sha`.

### Decisions

1. **Measure inside tox first.** `tox -e pep8` with a temporary
   override enabling `check_untyped_defs` for all of `neutron.agent.*`
   gives the real per-file counts with neutron's dependencies
   installed. The step records the counts in the execution notes and
   replaces the scratch numbers in this plan's Situation if they
   differ by more than a handful per file. This is the input phases 2
   to 4 are planned from.
2. **Patch 246 enables the clean modules and nothing else.** One
   `[[tool.mypy.overrides]]` block listing every `neutron.agent.l3.*`
   and `neutron.agent.dhcp.*` module (and `neutron.agent.l3.extensions.*`)
   that reports zero errors in the tox measurement, with
   `check_untyped_defs = true`. No code change. The commit message
   says what the override does, that the listed modules already pass,
   and that later changes will add modules as they are made to pass.
   Subject: "mypy: Check untyped function bodies in the clean agent
   modules".
3. **Scope stays at `agent/l3` and `agent/dhcp`** for this patch even
   though 34 other `agent/` modules are also clean; adding them later
   is a one-line change each and keeps this patch about the two
   agents the series is for.

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | In `neutron-typing-base`, add a temporary override enabling `check_untyped_defs` for `neutron.agent.*`, run `tox -e pep8`, and report errors per file and per error code for `agent/l3`, `agent/dhcp` and `agent/linux/dhcp.py`. Save the raw output under the plan's scratch area. Revert the temporary override. |
| 1b | low | sonnet | none | Patch 246 per Decision 2 using 1a's zero-error list. `tox -e pep8` must pass. Series directory, patch file, checks per Conventions. |

### Definition of done

- [ ] 1a's counts are in the execution notes and the Situation is corrected if needed.
- [ ] `tox -e pep8` passes at patch 246.
- [ ] `_build/test-apply.sh --skip-tests neutron-agent-typing-base` passes.
- [ ] `tools/gerrit-pre-push-lint --commit` in the worktree reports no ERROR or WARNING.
- [ ] `pre-commit run --all-files` passes in this repository.

## Phase 2 --- DHCP agent

Series `neutron-agent-typing-dhcp/`, two or three patches, worktree
`neutron-typing-dhcp` at `source_sha` or at the 1012618 revision per
the Dependencies section. Re-planned from 1a's counts before it runs.

### Decisions

1. **Interface first.** Patch one annotates the return types of
   `DhcpBase` and `DhcpLocalProcess` (`enable -> bool`,
   `disable -> None`, `restart`, `active -> bool`,
   `_has_running_interfaces -> bool`) and of `DhcpAgent._call_driver`
   and `call_driver`. With 237 and 239 in place this passes; without
   them it is the demonstration that the check works, which the
   commit message says in one sentence, citing the two changes.
2. **Then the bodies.** Patch two fixes whatever `check_untyped_defs`
   reports in `agent/dhcp/agent.py` and `agent/linux/dhcp.py` (8 and 7
   in the scratch run, mostly `union-attr` on `self.conf` and `.get()`
   results) by narrowing, not by ignores, and adds both modules to the
   override. A finding that is a real bug becomes its own patch ahead
   of this one, with a bug draft.
3. **Gating.** Not published until the `dhcp-agent-fixes` topic has
   merged or carries the Depends-On footers above.

### Steps

To be written when phase 1 completes, in the same table form; expected
two steps for the patches plus one for the series directory.

### Definition of done

- [ ] `tox -e pep8` passes at every patch in the series, applied over the base the Dependencies section requires.
- [ ] `tox -e py3 -- neutron.tests.unit.agent.dhcp neutron.tests.unit.agent.linux.test_dhcp` passes at the tip.
- [ ] Any fix patch has a failing-then-passing test and a bug draft.
- [ ] `_build/test-apply.sh --skip-tests neutron-agent-typing-dhcp` passes; `tools/check-depends-on.py` run online reports nothing.
- [ ] `pre-commit run --all-files` passes in this repository.

## Phase 3 --- L3 agent, single-inheritance files

Series `neutron-agent-typing-l3/`, one patch per file or small group,
smallest first from 1a's counts: `router_info.py`, `dvr_fip_ns.py`,
`dvr_snat_ns.py`, `dvr_router_base.py`, `l3_agent_extensions_manager.py`,
`agent.py`, `dvr.py` (on top of 1012621), `dvr_edge_router.py`.
Published in batches of at most five with the next batch held until
the previous has merged.

### Decisions

1. **`override` errors are fixed by making the signatures agree**, not
   by loosening the base class: `Namespace.create` versus the three
   subclasses that take extra arguments, and `BaseRouterInfo.delete`.
   Where a subclass genuinely needs more, the base gains the
   parameter with a default and a sentence in the commit message.
2. **`union-attr` and `index` errors are fixed by narrowing** at the
   point the value is produced (an early return or a raise when the
   port or namespace is absent), and each one is read as a possible
   bug first: a `None` that can reach that line in production is a
   fix patch with a test and a bug draft, before the typing patch.
3. **The extension manager's `Any | None`** (`add_router`,
   `update_router`, `delete_router`, `ha_state_change` on a lookup
   that may fail) is the first candidate for a real fix in this
   phase; the step reports its reading before writing a patch.

### Steps and definition of done

As phase 2, written when phase 2 is in review; the definition of done
adds `tox -e py3 -- neutron.tests.unit.agent.l3` at the tip and
`tox -e functional -- neutron.tests.functional.agent.l3` for any fix
patch that touches namespace or router lifecycle.

## Phase 4 --- L3 agent, the mixin files

`ha.py`, `ha_router.py`, `dvr_local_router.py` and
`dvr_edge_ha_router.py`. Proposed only. The open question above
decides whether this becomes a `Protocol` series or an explicit
exclusion with a comment, and it is not planned until the core team
has reviewed at least one of phases 2 and 3. Either way the series is
authored after the `dvr-fip-ns-locking` topic has merged, because
`dvr_local_router.py` is where `get_fip_ns` is called.

## Phase 5 --- push audit

Audit the accumulated diff of phases 1 to 4 against `develop` using
the `Merged` column, as [neutron-agent-bugs](neutron-agent-bugs.md)
does. The upstream fate of each series is tracked in the Status table
as the changes are uploaded and reviewed.

## Execution notes

- 2026-10-28 (AEDT): Plan written from a scratch measurement, not a
  tox run: a venv with mypy 2.4.0, oslo.config, oslo.utils, oslo.log,
  oslo.service, oslo.concurrency and neutron-lib, run with
  `--ignore-missing-imports --follow-imports=silent
  --check-untyped-defs` and the two disabled codes from `pyproject.toml`.
  Counts shifted between a run over the L3/DHCP files alone (106
  errors) and one over all of `neutron/agent` (130), which is why
  phase 1 measures again in tox before anything is written.
