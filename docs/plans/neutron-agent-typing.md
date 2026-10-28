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
| 1 | Enable `check_untyped_defs` for the L3 and DHCP agent packages, with the modules that fail listed as exceptions (`neutron-agent-typing-base`) | Complete | |
| 2 | DHCP agent: driver return annotations on the `dhcp-agent-fixes` chain, body checks on the `agent-typing` chain, and two fixes to that chain the survey found | Complete | |
| 3 | L3 agent, the router and namespace classes (`neutron-agent-typing-l3`) | Not started | |
| 4 | L3 agent, the two agent mixins (`neutron-agent-typing-l3-mixins`) | Proposed | |
| 5 | Push audit | Not started | |

Phase 1 is planned in detail below. Phases 2 to 4 are sized from the
tox measurement taken while planning phase 1 (see Situation) and each
one is re-planned from the previous phase's `tox -e pep8` output
before its steps run.

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
- **The backlog is small and lopsided.** Measured in neutron's own
  `tox -e mypy` environment (mypy 2.4.0, every requirement installed)
  with `check_untyped_defs` turned on for `neutron.agent.*`, the agent
  tree reports 221 errors and nothing outside `neutron/agent` changes.
  In `agent/l3` and `agent/dhcp` it is 101 errors in 13 of 32
  modules; the other 19 are already clean. The two largest router
  classes hold 46 (`dvr_local_router.py` 24, `ha_router.py` 22, all
  `union-attr`, `index` and `override`); the two agent mixins hold 29
  (`ha.py` 22, `dvr.py` 7), 27 of them mixin-shaped; the other eight
  L3 files hold 23, none above 5; the DHCP agent holds 3. Outside
  those packages the DHCP driver, `linux/dhcp.py`, holds 4, and the
  rest of `neutron/agent` fails in 34 of 114 files. An earlier
  scratch venv without neutron's requirements gave higher counts
  (130 for the agent tree), because unresolved imports turn into
  `Any | None` unions; the tox figures replace them.
- **Two populations of error.** About half are `union-attr` and
  `index` on values that came from `dict.get()` or an attribute that
  may be `None` (`Item "None" of "Any | None" has no attribute
  "get_name"`, `Value of type "Any | None" is not indexable`). Each
  one is either a real latent crash or a missing narrowing, and
  deciding which is the useful work. The other large block is
  `attr-defined` and `call-arg` from the mixin style, and it is
  confined to the two agent mixins, `ha.py` and `dvr.py`:
  `AgentMixin.__init__` calling `super().__init__(host)` and reading
  `self.conf` it never defines. Those are false positives for the
  code as written and need either a `Protocol` describing what the
  mixin expects of its host or a targeted ignore, and either choice
  is a style discussion with the core team. The `override` errors are
  real signature drift: `Namespace.create` versus
  `DvrSnatNamespace`, `FipNamespace` and `HaRouterNamespace`, and
  `BaseRouterInfo.delete` and `process` versus five router classes.
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
the plan must say so for each series before Mikal repushes. Phase 2's
survey found that the way to express that is git parentage, not
`Depends-On:` footers:

- **Stack on the series you need; do not Depends-On it.** A typing
  patch that touches lines an in-flight change also touches cannot be
  parented on master, and one that edits phase 1's `pyproject.toml`
  block cannot be parented on anything without patch 246. A
  `Depends-On:` footer does not change the git parent, so neither case
  is solved by one. Within this repository the series `depends_on`
  field does not solve it either: `_build/repush-openstack.sh` checks
  that the commits above `source_sha` are exactly the series' own
  `ORDER`, so a series applied on top of another fails that check and
  is not pushed. A patch that needs another series is therefore
  appended to that series' `ORDER`, and becomes the next change in its
  Gerrit chain and topic.
- **Phase 2 splits along that line.** The driver return annotations
  conflict textually with patch 239 and only pass mypy with patches
  237 and 239, so they are appended to `neutron-dhcp-agent-fixes` as
  patch 247. The body checks and their configuration need patch 246's
  `pyproject.toml` block and nothing from the DHCP fixes, so they are
  appended to `neutron-agent-typing-base` as patch 248. Each passes
  mypy on its own chain, and together in either merge order.
- **Phase 4 overlaps 1012621 textually.** Patch 241 rewrites
  `dvr.py:21-43` (`get_fip_ns` and the lock), and every one of
  `dvr.py`'s seven errors sits at lines 24, 36 to 38, 44 and 48,
  inside or beside those hunks. All seven are `AgentMixin` attribute
  reads, so `dvr.py` belongs to phase 4, which is already held until
  the `dvr-fip-ns-locking` topic merges. If it has not merged, the
  `dvr.py` patch is appended to `neutron-dvr-fip-ns-locking`, under
  the rule above. `item_allocator.py` (1012622) is already clean, and
  phase 3's files are touched by neither 241 nor 242.
- **Both agents' `agent.py` are touched by the import-memory topic**
  (1012169, 1012170, 1012182, tracked in
  [privsep-memory](privsep-memory.md)) and by the merged
  `pending_updates` changes. The import changes alter import blocks,
  not function bodies, so a conflict is unlikely but is checked at
  authoring time by applying the phase's series over those changes'
  current revisions; if it conflicts, the same stacking rule
  applies.
- **Phase 1 depends on nothing, and nothing depends on it.** It
  changes `pyproject.toml` only. Of the files the nine agent-bugs
  changes touch, only `item_allocator.py` (1012622) becomes checked,
  and with all nine applied the phase 1 configuration still passes,
  so the two topics can merge in either order.

If a `Depends-On:` footer is ever needed, for a dependency in another
project, `tools/check-depends-on.py` validates it. It runs offline in
pre-commit and online by hand. Run it online once before each repush,
so an abandoned or re-uploaded dependency is caught.

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

- **Override versus allowlist.** Settled in phase 1's Decision 1:
  turn `check_untyped_defs` on by package wildcard and list the
  failing modules in a second override that turns it off again. An
  explicit `files` list, as nova has, would narrow what is checked
  today and is the wrong direction for a tree that already runs mypy
  everywhere.
- **Mixins.** Default: phase 4 is `Proposed` until phases 1 to 3 have
  had review. If reviewers are warm, offer a `Protocol` for the agent
  attributes the two L3 agent mixins read; if not, leave `ha.py` and
  `dvr.py` on the exception list with a comment and stop there. Do not open the
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
- A patch that needs changes from another series in review is appended
  to that series' `ORDER`, as the Dependencies section explains. It
  does not get a `Depends-On:` footer.
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

## Phase 1 --- check untyped bodies in the L3 and DHCP agents

Series `neutron-agent-typing-base/`, one patch (246), Gerrit topic
`agent-typing`, against neutron `61f6db5ac9`. It lives on branch
`neutron-agent-bugs`, in worktree `kerbside-patches-wt-neutron-agent-bugs`,
beside this plan and patches 237 to 245.

Planning effort: high. The patch is a configuration change, but its
shape decides how every later phase reads to upstream reviewers. The
steps themselves are low and medium effort because the configuration
below is final and verified.

### What the survey found

Surveyed 2026-10-28 in the management session. The master plan's
Situation, Dependencies and phase 2 to 4 sections were corrected at
source in the same commit as this section, so later steps need not
redo it.

- **Upstream has not moved.** `upstream/master` is still `61f6db5ac9`.
- **Baseline passes.** `tox -e mypy` at `61f6db5ac9`: `Success: no
  issues found in 1196 source files`, mypy 2.4.0, about 6 s of mypy
  time once the environment exists. It also prints one note, at
  `router_info.py:65`, "By default the bodies of untyped functions are
  not checked, consider using --check-untyped-defs
  [annotation-unchecked]". mypy is not pinned: upper-constraints
  carries only `mypy_extensions`, so every pep8 run takes the latest
  release.
- **The tox counts replace the scratch counts.** With
  `check_untyped_defs` on for `neutron.agent.*` in the tox
  environment: 101 errors in 13 of the 32 modules in `agent/l3` and
  `agent/dhcp`. The scratch venv the master plan was first written
  from overstated them. The figures and the corrected phase 3/4 split
  are in the master plan's Situation.
- **The override shape works, and is verified by script.** A wildcard
  override turning `check_untyped_defs` on for both packages, plus a
  concrete-name override turning it off for the failing modules,
  passes. mypy's documented rule that concrete module names beat
  wildcards holds in practice. A probe added to an untyped body fails
  in `item_allocator.py`, `extensions/qos/fip.py`,
  `agent/dhcp/__init__.py` and in a new module, and passes in `ha.py`
  (excepted) and `linux/utils.py` (out of scope).
- **The exception list is 12 modules, not 13.**
  `dvr_edge_ha_router.py` reports one error when everything is
  checked, but passes once `ha_router.py` is excepted. Its error comes
  from an attribute type mypy infers only by checking `ha_router.py`'s
  bodies. So removing a module from the list can surface errors in
  another module, and each later removal patch has to run the whole
  of mypy, not just look at its own file.
- **The in-flight agent-bugs changes do not interact.** With all nine
  applied (237 to 245) and the final configuration, mypy passes.
  `item_allocator.py`, which 1012622 changes, becomes checked and
  stays clean.
- **Upstream is already looking at this code's typing.** Brian Haley's
  open change 1011933 ("Tweak BaseRouterInfo mypy annotation",
  2026-10-22, his own Review-Priority+1, no code review yet) moves
  `internal_ports: list[dict]` to class level to silence the
  `annotation-unchecked` note. It touches only `router_info.py`, which
  is on the exception list, so there is no conflict, and the note
  stays until either 1011933 merges or phase 3 clears `router_info.py`.
  Stephen Finucane moved mypy into tox (982004, 517a51bb30). Those two
  are the natural reviewers.
- **`pyproject.toml` churns, but not here.** About thirty merged
  changes touched it in the last year, mostly entry points and ruff
  configuration. Patch 246 inserts between the end of `[tool.mypy]`
  and the `[tool.ruff]` header, and only the `[tool.ruff]` and
  `line-length` lines of its trailing context are near anyone else's
  edits. The daily rebase handles a context shift.

### Decisions

1. **Wildcard on, exceptions off.** This departs from the master
   plan's first outline, which listed the clean modules. Patch 246
   enables `check_untyped_defs` for `neutron.agent.dhcp.*` and
   `neutron.agent.l3.*` and turns it off again for the twelve modules
   that fail. A new module in either package is checked from its
   first commit, which an allowlist cannot do. The exception list is
   the to-do list, and every later patch in this plan shows as a
   deleted line in it. It is also the shorter list (12 against 20).
   This is the decision a reviewer is most likely to argue with, from
   either side: "use an allowlist like nova" or "why not all of
   `neutron.agent`". The answer to the first is the new-module point.
   The second is a 34-entry exception list, offered as a follow-up if
   asked, not pre-empted.
2. **Scope is the two packages.** `neutron/agent/linux/dhcp.py`, the
   DHCP driver, is phase 2's because it sits in `neutron.agent.linux`,
   whose other 30-odd failing modules are out of this plan's scope.
   Phase 2 adds it by name to the first override.
3. **No code changes in patch 246, not even the one-error modules.**
   `dvr_snat_ns.py` and `dvr_router_base.py` each have one `override`
   error that is a real signature mismatch with its base class. Fixing
   them here would turn a one-minute configuration review into a
   design review. They are phase 3's first patches.
4. **The configuration is final.** It is the block below, verified by
   the script below at `61f6db5ac9`. It goes directly after the
   `exclude = "(?x)(^neutron/tests/$)"` line that ends `[tool.mypy]`,
   with one blank line before it and one before `[tool.ruff]`:

   ```toml
   # Check the bodies of unannotated functions in the L3 and DHCP agents.
   # Modules are removed from the second list as they are made to pass;
   # new modules in these packages are checked from the start.
   [[tool.mypy.overrides]]
   module = ["neutron.agent.dhcp.*", "neutron.agent.l3.*"]
   check_untyped_defs = true

   [[tool.mypy.overrides]]
   module = [
       "neutron.agent.dhcp.agent",
       "neutron.agent.l3.agent",
       "neutron.agent.l3.dvr",
       "neutron.agent.l3.dvr_edge_router",
       "neutron.agent.l3.dvr_fip_ns",
       "neutron.agent.l3.dvr_local_router",
       "neutron.agent.l3.dvr_router_base",
       "neutron.agent.l3.dvr_snat_ns",
       "neutron.agent.l3.ha",
       "neutron.agent.l3.ha_router",
       "neutron.agent.l3.l3_agent_extensions_manager",
       "neutron.agent.l3.router_info",
   ]
   check_untyped_defs = false
   ```

5. **The commit message is fixed too.** It cites no bug, carries no
   Depends-On and needs no release note, because it changes developer
   tooling only:

   ```
   mypy: Check untyped function bodies in the L3 and DHCP agents

   mypy does not look inside a function that has no annotations, and
   almost nothing in the L3 and DHCP agents has any, so mypy checks very
   little of either agent today.

   Turn on check_untyped_defs for both packages, so that new modules are
   checked from the start, and list the twelve modules that do not pass
   yet as exceptions, to be removed as their errors are fixed.

   Assisted-By: Claude Sonnet 5.5 (medium effort)
   Change-Id: <from tools/commit-msg.hook>
   Signed-off-by: Michael Still <mikal@stillhq.com>
   ```

6. **Publishing.** Patch 246 has no dependencies and can be pushed as
   soon as Mikal has reviewed it, alongside the agent-bugs topics.
   Adding Brian Haley and Stephen Finucane as reviewers is Mikal's
   call at upload. If a reviewer asks a design question, answer it in
   a comment. Do not mark the change WIP.

### Verification script

Run from the neutron worktree after `tox -e mypy` has built
`.tox/mypy`. It leaves the tree as it found it, exits 0 only if every
expectation holds, and takes about two minutes. It was run against the
configuration above while planning and passed. The authoring step
saves it as `verify-typing-p1.sh` beside the worktree, not inside it.

```bash
#!/bin/bash
# Phase 1 verification for neutron-agent-typing.
set -u
MYPY=".tox/mypy/bin/mypy --cache-dir=.tox/mypy/mypy_cache --no-pretty"
fail=0
passes() { $MYPY > /dev/null 2>&1; }

passes || { echo "FAIL: mypy does not pass on the unmodified tree"; exit 1; }
echo "ok: clean tree passes"

# 1. A probe in an untyped body must fail in checked modules and a new
#    module, and pass in an excepted module and an out-of-scope one.
probe='\n\ndef _mutation_probe(x):\n    y = None\n    return y.missing_attribute\n'
expect() {  # expect <fail|pass> <file>
    local want=$1 f=$2 new=0
    [ -e "$f" ] || new=1
    [ $new = 1 ] || cp "$f" "$f.verify-bak"
    printf "$probe" >> "$f"
    if passes; then got=pass; else got=fail; fi
    if [ $new = 1 ]; then rm -f "$f"; else mv "$f.verify-bak" "$f"; fi
    if [ "$got" = "$want" ]; then echo "ok: probe in $f -> $got"
    else echo "FAIL: probe in $f -> $got, wanted $want"; fail=1; fi
}
expect fail neutron/agent/l3/item_allocator.py
expect fail neutron/agent/l3/extensions/qos/fip.py
expect fail neutron/agent/dhcp/__init__.py
expect fail neutron/agent/l3/verify_new_module_probe.py
expect pass neutron/agent/l3/ha.py
expect pass neutron/agent/linux/utils.py

# 2. Every module on the exception list must still need to be there:
#    dropping it alone makes mypy fail.
cp pyproject.toml pyproject.toml.verify-bak
for m in $(python3 -c '
import tomllib
for o in tomllib.load(open("pyproject.toml", "rb"))["tool"]["mypy"].get("overrides", []):
    if o.get("check_untyped_defs") is False:
        print(*o["module"])'); do
    grep -v "^    \"$m\",\$" pyproject.toml.verify-bak > pyproject.toml
    if passes; then echo "FAIL: $m is excepted but passes"; fail=1
    else echo "ok: $m still needs its exception"; fi
done
mv pyproject.toml.verify-bak pyproject.toml
git status --short | grep -q . && { echo "FAIL: tree left modified"; git status --short; fail=1; }
exit $fail
```

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Author patch 246 in a neutron worktree at `61f6db5ac9`. Use `/tmp/claude-1000/-srv-kasm-profiles-mikal-vscode-src-reviews-neutron/01456ce4-0983-482a-85e0-d244c0f7cd34/scratchpad/neutron-typing-base` if it still exists (detached at `61f6db5ac9`, clean, `.tox/mypy` built). Otherwise create one with `git -C /srv/kasm_profiles/mikal/vscode/src/reviews/neutron worktree add --detach <path> 61f6db5ac91caa9e5d83e71dc1b3ec31d0611e8e` and run `tox -e mypy`. A tox environment does not survive `git worktree move`; rebuild it with `rm -rf .tox && tox -e mypy`. First check `git -C /srv/kasm_profiles/mikal/vscode/src/reviews/neutron fetch upstream` and report if `upstream/master` has moved past `61f6db5ac9` and touched `pyproject.toml` or `neutron/agent/l3`/`dhcp`. If it has, stop and report rather than adapting. Insert Decision 4's block verbatim into `pyproject.toml` after the `exclude = ...` line. Run `tox -e mypy` and expect `Success: no issues found in 1196 source files`. Write Decision 5's message to a file, run the kerbside-patches `tools/commit-msg.hook` over it to add the Change-Id, keep the footer order shown, and `git commit -F` it as Michael Still <mikal@stillhq.com>. Only then save the plan's verification script beside the worktree and run it from inside the worktree, expecting exit 0. It fails on a dirty tree by design. Run `tox -e pep8` once and expect it to pass. Report the sha, the last lines of the two tox runs verbatim, and the full verification script output. |
| 1b | low | sonnet | none | Package patch 246 in the kerbside-patches worktree that holds this plan. Run `git -C <neutron worktree> show <1a sha> > _patches/patch246-neutron-master-mypy-check-untyped-agent-defs.patch`, then `python3 tools/extract-commit-message _patches/patch246-...patch`. Create `neutron-agent-typing-base/config.yaml` copied from `neutron-test-switch-interval/config.yaml`, with `destination_branch: agent-typing` and `release: master-neutron-agent-typing-base`, keeping both SHAs at `61f6db5ac91caa9e5d83e71dc1b3ec31d0611e8e`, `depends_on: ''` and `repush: true`. Create `neutron-agent-typing-base/ORDER` with the single line `../_patches/patch246-neutron-master-mypy-check-untyped-agent-defs.patch`, matching `neutron-test-switch-interval/ORDER`. Run `tools/recount-patch.py --check`, `tools/check-depends-on.py --offline` and `tools/check-llm-attribution.py` on the new files. Run `_build/test-apply.sh --skip-tests neutron-agent-typing-base`, `tools/gerrit-pre-push-lint --commit` from inside the neutron worktree, and `pre-commit run --all-files` here. Leave everything uncommitted. Report each command's result. |

The management session then reviews the diff, records the execution
note (patch sha, verification output, any upstream drift) and commits
it as "Add the neutron agent mypy base series." Phase 1's Status row
moves to `Complete` with that commit, because nothing here lands
until Mikal's repush, and its upstream fate is tracked in the
`Merged` cell.

### Risks and mitigations

- **A new mypy release flags a newly checked module.** mypy floats
  with every pep8 run, so this would break pep8 for every neutron
  change, not just ours. The likelihood is low: the 20 newly checked
  modules are small and pass under 2.4.0. The fix is adding one line
  to the exception list, which the comment above it explains, so
  anyone can write it and the impact stays small. Mitigation: after patch 246
  merges, the next agent-typing patch runs the verification script,
  and Mikal's routine Zuul triage would see a pep8 break on his other
  neutron changes.
- **Upstream moves before upload.** A neutron change could make an
  excepted module pass or a checked one fail. Step 1a stops if the
  relevant files moved. Before the repush, Mikal or the session reruns
  the verification script against current master, and part 2 of it
  catches a stale exception.
- **Reviewers want a different shape.** Decision 1 gives the answer
  for both directions. The other shape is a five-minute revision,
  with the same script, which reads its list from `pyproject.toml`.
- **1011933 merges first.** Nothing changes for patch 246:
  `router_info.py` stays excepted. The note disappears, which only
  makes the patch's commit message slightly less obviously motivated.

### Definition of done

- [ ] `tox -e mypy` at patch 246 prints `Success: no issues found in 1196 source files` (or the then-current count, explained in the execution note).
- [ ] The verification script exits 0 at patch 246, and its output is in the execution note.
- [ ] `tox -e pep8` passes at patch 246.
- [ ] The patch changes `pyproject.toml` only: `grep '^+++ ' _patches/patch246-*.patch` shows exactly one file.
- [ ] `_build/test-apply.sh --skip-tests neutron-agent-typing-base` passes.
- [ ] `tools/gerrit-pre-push-lint --commit` in the neutron worktree reports no ERROR or WARNING.
- [ ] `pre-commit run --all-files` passes in this repository.

### Back brief

No gate. The configuration and the message are fixed above, so
reviewing this plan is the design review. If Mikal wants the
allowlist shape or the agent-wide scope instead, that is a change to
Decision 1 before step 1a runs, not during it.

## Phase 2 --- DHCP agent

No series of its own. Two patches are appended to existing series, and
two patches already in review are revised:

| Patch | Series (topic) | Change |
|-------|----------------|--------|
| 237 | `neutron-dhcp-agent-fixes` (`dhcp-agent-fixes`) | Revised: reset the running-interface registry per test |
| 239 | `neutron-dhcp-agent-fixes` (`dhcp-agent-fixes`) | Revised: `restart` returns `enable`'s result |
| 247 | `neutron-dhcp-agent-fixes` (`dhcp-agent-fixes`) | New: driver return annotations |
| 248 | `neutron-agent-typing-base` (`agent-typing`) | New: check the DHCP agent and driver bodies |

Work stays on branch `neutron-agent-bugs`. The neutron authoring
worktree is the session scratch tree used for phase 1, which also has
`.tox/py3` built.

Planning effort: high. Two of the four changes revise patches already
in review on Gerrit, and the survey changed the phase's shape.

### What the survey found

Surveyed 2026-10-28, against neutron `61f6db5ac9` with patches 246 and
237 to 240 applied as scratch commits. Line numbers are at that tree.
Corrections to the master plan's Dependencies section, conventions and
this section were made in the same commit.

- **Phase 1 is complete in this repository but not upstream.** Patch
  246 is committed and verified, and is held with the rest of this
  branch for Mikal's review; it is not on Gerrit yet. The
  `dhcp-agent-fixes` changes (1012616 to 1012619) have no code review
  yet.
- **The annotations demonstrate exactly what the plan claimed.** On
  master plus 246, `-> bool` on `DhcpLocalProcess.enable` and
  `_has_running_interfaces` makes mypy report `Missing return
  statement` at `dhcp.py:312` and `:276`, and nothing else. With
  patches 237 and 239 under them, both errors go away.
- **No Depends-On is needed, but stacking is.** Adding `-> bool` to
  `enable` touches the line beside patch 239's new docstring, so the
  annotations cannot be parented on master. The body fixes touch none
  of the lines patches 237 to 240 change. Prototyped, the body fixes
  and configuration pass mypy on 246 alone and on 246 plus 237 to 240.
  The annotations pass on master plus 237 to 240 without 246.
  `_build/repush-openstack.sh` cannot publish a series stacked on
  another series (the Dependencies section has the detail). So each
  half is appended to the series it needs.
- **`call_driver` and `_call_driver` should not be annotated.** They
  dispatch on an action string. They return `None` for a missing
  network and on the exception paths, the driver's string for
  `get_metadata_bind_interface`, and a bool otherwise. The honest
  annotation, `bool | str | None`, would catch nothing, so the master
  plan's first outline was wrong to list them.
- **`restart` is a real bug that patch 239 misses.**
  `DhcpBase.restart` (`dhcp.py:195`) calls `self.enable()` and drops
  the result, so it always returns `None`. Patch 239's check
  `action in ('enable', 'restart') and rv is False` therefore never
  fires for `restart`. When the subnet CIDRs change and dnsmasq fails
  to restart, `refresh_dhcp_helper` (`agent.py:569`) caches the
  network and marks its ports ready, which is the bug patch 239 fixes
  for `enable`. Annotating `restart -> bool` makes mypy require the
  fix.
- **Patch 237 made an existing test order-dependent.** 1012618's py311
  job failed `TestDhcpLocalProcess.test_enable_disable_two_networks`,
  which dates from dfe29e6760 (2023). The py314 and cover jobs of the
  same buildset passed. `DhcpLocalProcess._interfaces` is a class-level
  registry. Until patch 237, `_has_running_interfaces` returned `None`
  whatever it held, so tests never noticed what earlier tests left
  there. Run pairwise, 9 of the 18 other tests in the class make that
  test fail when they run first. Two of them,
  `test_enable_returns_false_when_start_times_out` and
  `test_enable_returns_true_when_started`, were added by patch 239.
  Patching the registry to a fresh `collections.defaultdict(set)` in
  `TestBase.setUp` leaves none, and the 307 tests in
  `neutron.tests.unit.agent.linux.test_dhcp` and
  `neutron.tests.unit.agent.dhcp` pass. Patch 237's own tests clear
  the registry with `addCleanup`, which is why 1012616's run passed.
  That cleanup becomes redundant.
- **The seven body errors are narrowing, not bugs.** In `dhcp.py`:
  - The `DictModel` storage dict needs a value annotation (`:107`).
  - `DictModel.__getattr__` calls `super().__getattr__`, which no
    superclass has (`:114`). That still raises `AttributeError`, the
    exception the code wants, but only by accident.
  - `_format_option`'s regex has only optional groups and so always
    matches, but mypy cannot know that (`:1410`, `:1411`).

  In `agent.py`:
  - `_report_state` uses `.get('configurations')` on a key that is
    always present (`:1172`, `:1173`).
  - `DhcpPluginApi.get_ports` drops the base class's `context`
    parameter (`:987`). That is deliberate: every method on that class
    binds its own context. Its only caller, `_get_ovn_metadata_port_ip`,
    already handles the `None` it returns for no ports.

### Decisions

1. **Two halves, each on the chain it needs.** Patch 247 (annotations)
   is appended to `neutron-dhcp-agent-fixes` and becomes the fifth
   change of the `dhcp-agent-fixes` topic. Patch 248 (body checks) is
   appended to `neutron-agent-typing-base` after 246. This is the
   decision most likely to be argued with, because a reviewer of the
   DHCP bug fixes gets a typing change in the same topic. That
   placement is also its best argument: it sits directly after the two
   fixes it would have caught, and its commit message says so. If
   reviewers want it moved, it moves to `agent-typing` once the DHCP
   fixes merge.
2. **Fold the `restart` fix into patch 239**, not a new patch. It is
   the same bug through the other entry point, and 239's
   `('enable', 'restart')` check is dead code without it. A reviewer
   reading 239 would ask for it. `DhcpBase.restart` becomes
   `self.disable(retain_port=True, block=True)` then
   `return self.enable()`. 239 gains a driver test (`restart` returns
   what `enable` returned) and an agent test: a failed restart on a
   CIDR change neither caches the network nor marks its ports ready,
   and schedules a resync. Its release note gains one sentence. Gated
   in the back brief because it creates new revisions of 1012618 and
   1012619.
3. **Fold the registry reset into patch 237.** 237 is what made the
   registry matter, so 237 is where tests stop sharing it. Add the
   `mock.patch.object` reset to `TestBase.setUp` in
   `neutron/tests/unit/agent/linux/test_dhcp.py`, with a two-line
   comment and an `import collections`. Drop 237's own two
   `self.addCleanup(LocalChild._interfaces.clear)` lines. Gated in the
   back brief with Decision 2, since both go out in one repush of the
   chain.
4. **Patch 247 annotates the driver interface and nothing else:**
   - `DhcpBase`: `enable -> bool`,
     `disable(...) -> None`, `restart -> bool`, `active -> bool`, and
     `reload_allocations -> None`.
   - `DhcpLocalProcess`: `enable -> bool`, `disable(...) -> None`,
     `active -> bool`, and `_has_running_interfaces -> bool`.
   - Nothing in `agent.py`.

   It touches no configuration: annotated functions are checked
   whatever `check_untyped_defs` says. Subject: "dhcp: Annotate the
   DHCP driver's return types". Its message says that mypy only checks
   return statements in functions that declare a return type, and that
   without the two preceding fixes this change makes mypy report both
   bugs.
5. **Patch 248 is the prototype, verbatim in effect:**
   - In `pyproject.toml`, `neutron.agent.linux.dhcp` joins the first
     override's `module` list, which is reformatted one module per
     line, and `neutron.agent.dhcp.agent` leaves the exception list.
   - `DictModel`'s storage gets `dict[str, typing.Any]`, which adds
     `import typing`.
   - `DictModel.__getattr__` raises `AttributeError(name)` directly.
   - `_format_option` raises `ValueError(f'Malformed DHCP option
     {option!r}')` if the match is `None`. Ruff's S101 forbids
     `assert` outside tests.
   - `_report_state` indexes `self.agent_state['configurations']`.
   - `DhcpPluginApi.get_ports` keeps its signature with
     `# type: ignore[override]` and a one-line comment saying the DHCP
     API binds its own context.

   That ignore departs from the master plan's "narrowing, not ignores".
   Fixing the signature means changing either the RPC base class or
   the one caller and its tests, for no behaviour change, and the
   ignore documents a deliberate difference. Subject: "mypy: Check the
   DHCP agent and driver function bodies".
6. **Publishing.** One repush of `neutron-dhcp-agent-fixes` carries
   the revised 237 and 239, a replayed 238 and 240, and the new 247.
   Patch 248 goes up with 246 whenever Mikal pushes the typing series.
   Neither waits for the other.

### Verification scripts

Both run from the neutron worktree after `tox -e py3 --notest` and
`tox -e mypy` have built their environments, and both exit non-zero on
failure.

`check-dhcp-registry-isolation.sh`, run at patch 237 and at 247:

```bash
#!/bin/bash
# No test may change the outcome of the namespace-deletion test by
# running before it.
.tox/py3/bin/python - <<'EOF'
import sys
import unittest

from neutron.tests.unit.agent.linux import test_dhcp as t

target = 'test_enable_disable_two_networks'
bad = []
for name in unittest.TestLoader().getTestCaseNames(t.TestDhcpLocalProcess):
    if name == target:
        continue
    suite = unittest.TestSuite([t.TestDhcpLocalProcess(name),
                                t.TestDhcpLocalProcess(target)])
    result = unittest.TextTestRunner(stream=open('/dev/null', 'w')).run(suite)
    if result.failures or result.errors:
        bad.append(name)
print(f'tests that break {target} when run first: {bad}')
sys.exit(1 if bad else 0)
EOF
```

`check-annotations-catch-bugs.sh`, run at patch 247. It undoes the
return fixes of 237 and 239 in a copy of the tree and expects mypy to
report both:

```bash
#!/bin/bash
set -eu
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp -a neutron pyproject.toml "$work"/
cd "$work"
python3 - <<'EOF'
p = 'neutron/agent/linux/dhcp.py'
s = open(p).read()
old = '        return bool(cls._interfaces.get(network_id))\n'
assert s.count(old) == 1
s = s.replace(old, '        bool(cls._interfaces.get(network_id))\n')
old = '            return False\n        return True\n\n    def _enable(self):'
assert s.count(old) == 1
s = s.replace(old, '            pass\n\n    def _enable(self):')
open(p, 'w').write(s)
EOF
out=$("$OLDPWD/.tox/mypy/bin/mypy" --no-pretty --cache-dir=/dev/null neutron/agent/linux/dhcp.py 2>&1 || true)
echo "$out" | grep -E 'error' || true
n=$(echo "$out" | grep -c 'Missing return statement' || true)
[ "$n" -eq 2 ] && echo "ok: both bugs reported" || { echo "FAIL: expected 2 missing-return errors, got $n"; exit 1; }
```

Phase 1's `verify-typing-p1.sh` is rerun at patch 248. Its part 2
proves that each remaining exception is still needed.

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Revise the `dhcp-agent-fixes` chain per Decisions 2 and 3. In the neutron worktree, rebuild the chain from 61f6db5ac9. For each of patches 237 to 240, `git apply` the patch file and commit with its `-message` file as Michael Still, which keeps the Change-Ids. Amend 237 per Decision 3 and replay the rest. Amend 239 per Decision 2, adding the two tests and the release-note sentence, and replay 240. Run `check-dhcp-registry-isolation.sh` at 237 and at 240, `tox -e py3 -- neutron.tests.unit.agent.dhcp neutron.tests.unit.agent.linux.test_dhcp` at each of the four commits, and `tox -e pep8` at 240. Regenerate the four patch and message files in kerbside-patches. Check with `grep Change-Id` that the Change-Ids are unchanged. Add an execution note to `neutron-agent-bugs.md` saying what changed in 237 and 239 and why. Leave kerbside-patches uncommitted. |
| 2b | medium | sonnet | none | Patch 247 per Decision 4, on top of 2a's 240 in the same worktree. Run `tox -e mypy` and expect Success. Run `check-annotations-catch-bugs.sh` and expect "ok". Run the DHCP unit tests as in 2a, `check-dhcp-registry-isolation.sh`, and `tox -e pep8`. Write the message under the conventions, add the Change-Id with the hook, and commit. Create `_patches/patch247-...` and its message file, and append it to `neutron-dhcp-agent-fixes/ORDER`. |
| 2c | medium | sonnet | none | Patch 248 per Decision 5, on top of patch 246 (`a4ef6ca9c5`) in a separate branch of the worktree. Run `tox -e mypy` and expect Success. Run `verify-typing-p1.sh` and expect exit 0; its probes and exception check still apply. Probe `neutron/agent/linux/dhcp.py` by hand the same way and expect a failure. Run the DHCP unit tests and `tox -e pep8`. Commit, then create `_patches/patch248-...` and its message file, and append it to `neutron-agent-typing-base/ORDER`. Finally, cherry-pick 248 onto 2b's 247 in a throwaway branch with 246 also applied, and confirm mypy still passes. That proves the merge-order independence claimed in Decision 6. |
| 2d | low | sonnet | none | Packaging checks across both series. Run `tools/recount-patch.py --check`, `tools/check-depends-on.py --offline` and `tools/check-llm-attribution.py` on every new or changed patch file. Run `_build/test-apply.sh --skip-tests` for `neutron-dhcp-agent-fixes` and `neutron-agent-typing-base`, and `tools/gerrit-pre-push-lint --stack 5` and `--stack 2` on the two neutron branches. Run `pre-commit run --all-files` here. Report each result; fix nothing by hand. |

Steps run in order. 2b sits on 2a's output. 2c is independent of both
but shares the worktree and its tox environments, so it runs after.

### Risks and mitigations

- **New revisions of 1012616 to 1012619 restart their review.** None
  has a code review yet, so the cost now is only a Zuul rerun. The
  cost grows once reviews arrive, which is why Decisions 2 and 3 are
  gated for an early answer.
- **The `DictModel` error message changes.** An `AttributeError` for
  the storage attribute now names the attribute instead of saying
  `'super' object has no attribute '__getattr__'`. `copy` and
  `hasattr` depend only on the exception type. The DHCP unit tests,
  which copy `NetModel` objects since patch 240, must pass in 2c.
- **The `ValueError` guard is unreachable**, and a reviewer may ask
  for `typing.cast` instead. Either is a one-line change at review
  time. The guard is preferred because it states the assumption.
- **mypy floats.** As in phase 1, a new release could flag a newly
  checked module. 2c's run and the first CI run show it.

### Definition of done

- [ ] `check-dhcp-registry-isolation.sh` exits 0 at patch 237 and at patch 247.
- [ ] The DHCP unit tests pass at each of patches 237, 238, 239, 240 and 247, and at 248.
- [ ] Patch 239's new agent test fails with `restart` returning `None` (2a shows the revert) and passes with the fix.
- [ ] `tox -e mypy` passes at 247 without 246, and at 248 without 237 to 240, and with all of 237 to 240 and 246 to 248 applied.
- [ ] `check-annotations-catch-bugs.sh` reports both missing returns at 247.
- [ ] `verify-typing-p1.sh` exits 0 at 248.
- [ ] `tox -e pep8` passes at 247 and at 248.
- [ ] The Change-Ids in patch files 237 to 240 are the ones listed in `neutron-agent-bugs.md`'s Gerrit table.
- [ ] `_build/test-apply.sh --skip-tests` passes for both series, `gerrit-pre-push-lint` reports no ERROR or WARNING on either stack, and `pre-commit run --all-files` passes.

### Back brief

Two gates before step 2a, both because they revise changes already on
Gerrit:

1. Fold the `restart` fix into 239 (Decision 2), or make it a separate
   patch between 240 and 247.
2. Fold the registry reset into 237 (Decision 3), or make it a
   separate test-only patch at the head of the chain.

Steps 2c and 2d do not depend on either answer.

Answered 2026-10-28: both folds approved as planned (Decisions 2 and
3 stand).

## Phase 3 --- L3 agent, the router and namespace classes

Series `neutron-agent-typing-l3/`, one patch per file or small group,
smallest first from the tox measurement: `dvr_snat_ns.py` (1),
`dvr_router_base.py` (1), `dvr_edge_ha_router.py` (1),
`router_info.py` (3), `dvr_fip_ns.py` (3),
`l3_agent_extensions_manager.py` (4), `agent.py` (5),
`dvr_edge_router.py` (5), `ha_router.py` (22) and
`dvr_local_router.py` (24). The `override` fixes to `Namespace.create`
and `BaseRouterInfo.delete` and `process` cut across several files and
go first as their own patches. Each patch removes its modules from
phase 1's exception list. Published in batches of at most five with
the next batch held until the previous has merged.

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

## Phase 4 --- L3 agent, the two agent mixins

`ha.py` (`AgentMixin` for HA, 22 errors, 20 mixin-shaped) and
`dvr.py` (`AgentMixin` for DVR, 7 errors, all mixin-shaped). Proposed
only. The open question above decides whether this becomes a
`Protocol` series or a permanent place on the exception list with a
comment, and it is not planned until the core team has reviewed at
least one of phases 2 and 3. Either way the series is authored after
the `dvr-fip-ns-locking` topic has merged, because 1012621 rewrites
the lines of `dvr.py` that carry its errors.

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
- 2026-10-28 (AEDT): Phase 1 planned in detail after a survey in
  neutron's own tox environment, done in the planning session rather
  than as a step because the patch's content depends on it entirely.
  The tox counts replaced the scratch ones in Situation. The survey
  moved `ha_router.py` and `dvr_local_router.py` from phase 4 to
  phase 3, because their errors are ordinary narrowing and override
  errors, and moved `dvr.py` from phase 3 to phase 4, because all its
  errors are mixin attribute reads. It also replaced phase 1's
  allowlist with a wildcard and an exception list (phase 1, Decision
  1). Survey worktree:
  `/tmp/claude-1000/-srv-kasm-profiles-mikal-vscode-src-reviews-neutron/01456ce4-0983-482a-85e0-d244c0f7cd34/scratchpad/neutron-typing-base`
  (session scratch, may not survive the session).
- 2026-10-28 (AEDT): Phase 1 implemented as planned, with no
  departures. Step 1a (Sonnet, medium) committed patch 246 as neutron
  `a4ef6ca9c5` on `61f6db5ac9`, Change-Id
  `I2af9c68d9175aa4634f3d9a2d205ebc55be0ae4b`; upstream had not moved.
  `tox -e mypy` and `tox -e pep8` passed, with 1196 source files, and
  the verification script exited 0. It was rerun in review and passed
  again, with all twelve exceptions still needed. Step 1b (Sonnet, low)
  packaged it as `neutron-agent-typing-base/`. `test-apply`,
  `gerrit-pre-push-lint --commit` (0 errors, 0 warnings), and the
  offline Depends-On and attribution checks passed. The patch waits
  for Mikal's hand review and repush. The neutron worktree stays in the
  session scratchpad for later phases.

- 2026-10-28 (AEDT): At Mikal's request, phase 1's branch
  (`neutron-agent-typing-phase-1`, worktree
  `kerbside-patches-wt-agent-typing-p1`) was fast-forwarded into
  `neutron-agent-bugs` and removed, so one unpushed branch holds both
  plans. Later phases of this plan work on that branch too, unless it
  has merged by then.
- 2026-10-28 (AEDT): Phase 2 planned. The survey prototyped both
  halves in the session's neutron scratch tree, so the claims in the
  phase 2 section were run, not inferred. That covers the annotations
  catching both bugs, each half passing on its own chain, the registry
  leak and its fix, and the two verification scripts. It replaced
  phase 2's Depends-On approach with stacking, because repush cannot
  publish a series stacked on another. It dropped the `call_driver`
  annotations, and found two defects in the `dhcp-agent-fixes` chain
  (`restart` in 239, test isolation exposed by 237), which phase 2's
  step 2a fixes if the back brief agrees.
- 2026-10-28 (AEDT): Phase 2 step 2b. Patch 247 is neutron `aa8f056ce4`
  on the rebuilt chain. `gerrit-pre-push-lint --stack 5` gives one
  WARNING on it, "no release note found", where the definition of done
  asks for none. It is accepted, not fixed: the change has no
  user-visible effect, and none of neutron's own typing changes
  (3d42164244, 3f29ba5cdf, e163bb5b6c, 517a51bb30) carries a release
  note.
- 2026-10-28 (AEDT): Phase 2 step 2c. Patch 248 is neutron `0eb3009f56`
  on 246. The review rewrote its commit message, which had left out
  the `__getattr__` change. Its Change-Id is unchanged. The release-note
  warning is accepted as for 247. 296 DHCP unit tests pass on 246 plus
  248, and `verify-typing-p1.sh` passes with 11 exceptions. 246 and 248
  cherry-picked onto the DHCP chain with 247 also pass: mypy succeeds
  and 310 tests pass.
- 2026-10-28 (AEDT): Phase 2 complete. Step 2d's read-only checks all
  passed. Every patch file matches its neutron commit, and the
  Change-Ids of 237 to 240 still resolve to 1012616 to 1012619. The
  only lint warnings are the two accepted release-note warnings. The
  revised 237 to 240 and the new 247 need Mikal's repush of
  `neutron-dhcp-agent-fixes`. 248 goes up with 246 in
  `neutron-agent-typing-base`.
