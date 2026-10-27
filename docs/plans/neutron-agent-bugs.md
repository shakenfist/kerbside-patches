# Plan: neutron agent bugs found while reading the L3 and DHCP agents

## Goal

Turn the bugs found while reading the neutron L3 and DHCP agent code
(the reading clone at `src/reviews/neutron`) into upstream changes, one
independent series per class of problem, each ready for Mikal's hand
review and his repush to Gerrit. Four are plain logic errors in the
DHCP agent, two are races that the eventlet removal made real in the
DVR floating IP namespace, and three are tooling changes that would
have caught the classes of bug this reading and the privsep-memory
work keep finding.

## Status

| Phase | What | Status | Merged |
|-------|------|--------|--------|
| 1 | DHCP agent logic fixes (`neutron-dhcp-agent-fixes`) | Complete | |
| 2 | DVR fip namespace locking (`neutron-dvr-fip-ns-locking`) | Complete | |
| 3 | Hacking check for config read at import (`neutron-import-time-config-check`) | Complete | |
| 4 | Static import-closure test for the agents (`neutron-import-closure-test`) | Abandoned | |
| 5 | Thread switch interval in the test base class (`neutron-test-switch-interval`) | Complete | |
| 6 | Push audit | Not started | |

## Situation

Findings are against upstream neutron master `61f6db5ac9`
(2026-10-27), which every series here pins as `source_sha`. The
reading was done at `dceca0b9fd`; none of the files below changed
between the two except `agent.py` in both agents, where the
`pending_updates` heartbeat changes (1009917, 1009918) merged.

Verified by reading the code in this session, with line numbers at
`61f6db5ac9`:

- **`neutron/agent/linux/dhcp.py:276`** `_has_running_interfaces` has
  no `return`, so it always returns `None`. Its only caller,
  `_destroy_namespace_and_port` (`:378`), therefore deletes the
  network namespace on every segment's disable, while other segments'
  dnsmasq processes still run in it. Introduced by dfe29e6760 (2023).
  No test references the method.
- **`neutron/agent/dhcp/agent.py:441-446`** builds
  `utils.throttler(self.conf.resync_throttle)(...)` afresh on every
  pass of `_periodic_resync_helper`. Each `LockWithTimer` starts with
  `timestamp = 0` (`neutron/common/utils.py:82`), so `time_to_wait` is
  always negative and `resync_throttle` has never delayed anything.
  Shape unchanged since 2018.
- **`neutron/agent/linux/dhcp.py:312-318` and
  `neutron/agent/dhcp/agent.py:295-300`**: `DhcpLocalProcess.enable`
  catches the 300 s `WaitTimeout`, logs, and returns `None`;
  `_call_driver` returns `True` for every action other than
  `get_metadata_bind_interface` whatever the driver did. So
  `configure_dhcp_for_network` (`:498-513`) caches the network and
  reports all its ports DHCP-ready with no dnsmasq behind them, the
  process is never registered with `ProcessMonitor`, and nothing
  schedules a resync.
- **`neutron/agent/dhcp/agent.py:265-271`**: for a routed network
  `call_driver` assigns `network.subnets = <one segment's subnets>` on
  the cached `NetModel` for each segment and never restores it, so the
  cache keeps only the last segment's subnets.
- **`neutron/agent/l3/dvr.py:26-41`** `get_fip_ns` is get, construct,
  store with no lock. `FipNamespace.__init__`
  (`neutron/agent/l3/dvr_fip_ns.py:51-72`) builds a
  `FipRulePriorityAllocator` over 40,000 priorities and a
  `LinkLocalAllocator` over the 8,192 /31s of `169.254.64.0/18`, and
  reads both state files, so it runs for tens of milliseconds and
  releases the GIL on the file reads. Callers are pool workers
  processing different routers (`dvr_local_router.py:71,926`); on a
  startup fullsync 32 of them hit one external network together. Two
  `FipNamespace` objects then exist for one network, each with
  allocators seeded from the same files, and `set.pop()` on equal sets
  hands two routers the same link-local pair.
- **`neutron/agent/l3/item_allocator.py:113-131`**: the allocators are
  shared by every DVR router on the agent and have no lock. `_write`
  truncates and rewrites the state file; two workers writing together
  leave a mixed file, which the next restart partly discards.
- **Import-time configuration reads**: besides the ovsdb partial fixed
  by 1009883, `neutron/objects/base.py:67` reads `cfg.CONF.debug` at
  import to decide whether to register the no-transaction ORM warning,
  and `neutron/extensions/quotasv2.py:44` and
  `quotasv2_detail.py:35` read `cfg.CONF.QUOTAS.quota_driver` at
  import. `neutron-ovn-db-sync-util` and the cleanup tools import
  these before parsing their configuration, so they see defaults.
  `quotasv2.py:190` compares the import-time value with the run-time
  value, which is a tautology when the config was parsed first and
  wrong otherwise. Nothing in `tox -e pep8` (ruff `E,F,W,S,UP,C90,G`,
  hacking N3xx, pylint 3.3, mypy) looks for this.
- **Import closures.** A static walk of module-level imports from each
  console script suggested `neutron-l3-agent`, `neutron-dhcp-agent`
  and `neutron-openvswitch-agent` reach neither `neutron.db` nor
  `neutron.objects`. Step 4a showed that is an artefact: those scripts
  load their agent class by name at run time (`importutils.import_class`
  in `neutron/service.py`, `AppManager.run_apps` for OVS), so the walk
  stops at the launcher. Walked from the agent modules, or imported in
  a fresh interpreter, all three load about 60 `neutron.db` and 67
  `neutron.objects` modules and SQLAlchemy, through `neutron.agent.rpc`
  and `api.rpc.callbacks.resources`. That is the chain the in-flight
  import-memory series (1012182 and its siblings) removes, and those
  changes carry their own fresh-interpreter tests, so a static test
  here would duplicate a guard that already exists in review.
- **Thread preemption.** CPython hands the GIL to a waiting thread
  only after that thread has waited a full switch interval (5 ms), so
  a pure-Python section shorter than that is never preempted. A model
  of `_report_state` iterating a 20,000-entry dict against a mutating
  thread never raised in 60 s at 5 ms and raised at once at 10 µs
  (`sys.setswitchinterval(1e-5)`). The agents' unit and functional
  tests set no switch interval, so the latent races the reading found
  (`ExclusiveResourceProcessor.__init__`, the `_report_state` loops,
  the DHCP agent's dict swaps that still say "greenthreads multi-task
  co-operatively" at `agent.py:230`) can never show up in CI.

Already in flight and not repeated here: 1009882 (timecost call id),
1009883 (ovsdb manager options, merged), 1009917 and 1009918
(`pending_updates`, merged), 1011796 and 1011797 (privileged import
chain), 1012169, 1012170 and 1012182 (import memory).

## Mission and problem statement

Produce the five series below in `_patches/`, each applying to
`61f6db5ac9`, with unit tests, release notes where the change is
user-visible, and commit messages in the upstream form, so that Mikal
can review each patch by hand and run `_build/repush-openstack.sh`.
Launchpad bugs are his to file; each step drafts the bug text for him
and the patch carries `Closes-Bug:` once he has a number.

Not covered: the latent races (window under 5 ms), the DHCP agent's
`_report_state` exception handler, `ProcessMonitor`'s `.get()`/`[]`
mismatch, the HA state-change overlap, and the other sweep findings
listed in the session. They are recorded in `docs/neutron-agent-bugs-
findings.md` (phase 1 step 1e) for later plans.

## Open questions

- **Bug numbers.** Default: every patch is written with a
  `Closes-Bug: #NNNNNNN` placeholder line commented out of the message
  (that is, omitted), and the bug drafts sit in the findings document.
  Mikal files the bugs, then the footers are added before the repush.
- **Switch interval value and cost.** Default: `1e-5`, applied in
  `DietTestCase.setUp` with a cleanup restoring the previous value,
  kept only if the unit suite's wall time grows by less than 5%. If it
  grows more, the step tries `1e-4` and reports both.
- **Where the import-closure test's contract list lives.** Default: a
  tuple in the test module naming the three lean entry points and the
  two forbidden packages, with a comment saying the list is meant to
  grow as agents shed imports.

## Conventions every step follows

- Work in the neutron worktree named in the brief (one per series,
  all at `61f6db5ac9`), never in `src/` or in `_patches/` directly.
- One commit per patch. Message: subject 50 characters ideal, 72
  maximum, prefixed by the area (`dhcp:`, `l3:`, `hacking:`, `tests:`);
  body at most two paragraphs of at most four 72-column lines; footers
  `Assisted-By: <model> (<effort> effort)`, `Signed-off-by: Michael
  Still <mikal@stillhq.com>`, and `Change-Id` produced by running
  `tools/commit-msg.hook` from this repository over the message file
  before `git commit -F`. Never `Co-Authored-By` for an LLM.
- A `Prompt:` paragraph is not part of the upstream form; leave it
  out of these patches.
- Tests: `tox -e py3 -- <test module>` for the touched area, and
  `tox -e pep8` once per series. Record the counts.
- Patch file: `git show <sha> > _patches/patchNNN-neutron-master-
  <slug>.patch`, then `python3 tools/extract-commit-message
  _patches/patchNNN-...patch`, `tools/recount-patch.py --check`,
  `tools/check-depends-on.py --offline`, and
  `tools/check-llm-attribution.py` over the new files.
- Series directory: `config.yaml` copied from `neutron-timecost-fix/`
  with `destination_branch`, `release` (`master-<series>`),
  `source_sha` and `previous_source_sha` (both `61f6db5ac9...` in full)
  set, and an `ORDER` listing the patches in order.
- `_build/test-apply.sh --skip-tests <series>` must pass before the
  step reports.
- Leave the work uncommitted in this repository; the management
  session reviews and commits each step.

## Phase 1 --- DHCP agent logic fixes

Series `neutron-dhcp-agent-fixes/`, Gerrit topic `dhcp-agent-fixes`,
patches 237 to 240, neutron worktree `neutron-dhcp`. Four independent
fixes in the same two files, chained so they apply in order; if a
reviewer wants them squashed or reordered, that is a repush.

### Decisions

1. **Patch 237, the missing `return`,** goes first because it is the
   smallest and the easiest +2. Its test covers both branches of
   `_destroy_namespace_and_port`: namespace kept while another segment
   has an interface, deleted when the last one goes.
2. **Patch 238, the throttler,** builds the throttled callable once
   per agent (in `__init__`, next to `_periodic_resync_event`) and
   calls it from the loop. The test proves a delay by mocking
   `time.sleep` and `time.time`: two resync events closer together
   than `resync_throttle` must sleep on the second.
3. **Patch 239, start failure,** makes `DhcpLocalProcess.enable`
   return `False` on `WaitTimeout` and `True` otherwise, and makes
   `_call_driver` return the driver's result for `enable` and
   `restart` (still `True` for actions that return `None`). On
   `False`, `configure_dhcp_for_network` skips the cache update and
   the ready-ports update and calls `schedule_resync`, the same path
   as the exception branch at `agent.py:300-318`. The test covers: a
   failed enable caches nothing, marks no port ready, and schedules a
   resync. Release note: `fixes:` section.
4. **Patch 240, segment subnets,** gives each segment's driver call a
   shallow copy of the `NetModel` with `subnets` replaced, and leaves
   the cached object alone. `NetModel` is a `dict` subclass with
   attribute access (`neutron/agent/linux/dhcp.py`), so `copy.copy`
   plus one attribute assignment is enough; check that nothing
   downstream relies on identity. Test: after `call_driver` on a
   two-segment network, `network.subnets` still has both.
5. **Bug drafts** go in `docs/neutron-agent-bugs-findings.md`, one
   section per patch, with the Launchpad title, the affected
   versions (all supported, for 237 since 2023.2 and 238 since Rocky)
   and the reproduction.

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | low | sonnet | none | Patch 237 per Decision 1, in `neutron-dhcp`. Subject "dhcp: Return the result of _has_running_interfaces". Unit test in `neutron/tests/unit/agent/linux/test_dhcp.py`. |
| 1b | medium | sonnet | none | Patch 238 per Decision 2, on top of 1a. Subject "dhcp: Build the resync throttler once". Test in `neutron/tests/unit/agent/dhcp/test_agent.py`. Release note. |
| 1c | high | opus | none | Patch 239 per Decision 3, on top of 1b. Subject "dhcp: Do not cache a network whose dnsmasq failed to start". Trace every caller of `_call_driver`'s return value first and list them in the report. Tests in both test files. Release note. |
| 1d | high | opus | none | Patch 240 per Decision 4, on top of 1c. Subject "dhcp: Keep every segment's subnets on the cached network". Test in `test_agent.py`. Release note. |
| 1e | medium | sonnet | none | Series directory `neutron-dhcp-agent-fixes/`, the four patch files, and `docs/neutron-agent-bugs-findings.md` with the four bug drafts plus a "not addressed" section listing the latent races and other sweep findings from the plan's Situation. Run `_build/test-apply.sh --skip-tests neutron-dhcp-agent-fixes`. |

### Definition of done

- [ ] `tox -e py3 -- neutron.tests.unit.agent.dhcp neutron.tests.unit.agent.linux.test_dhcp` passes in `neutron-dhcp` at the series tip, and each new test fails when its fix is reverted (one revert per patch, restored afterwards).
- [ ] `tox -e pep8` passes at the tip.
- [ ] `_build/test-apply.sh --skip-tests neutron-dhcp-agent-fixes` passes.
- [ ] `tools/gerrit-pre-push-lint --stack 4` in `neutron-dhcp` reports no ERROR or WARNING.
- [ ] `pre-commit run --all-files` passes in this repository.

## Phase 2 --- DVR fip namespace locking

Series `neutron-dvr-fip-ns-locking/`, topic `dvr-fip-ns-locking`,
patches 241 and 242, worktree `neutron-dvr`.

### Decisions

1. **Confirm before fixing.** Step 2a reproduces the duplicate
   `FipNamespace` with a unit test that runs two threads through
   `get_fip_ns` with the constructor slowed by a `threading.Event`,
   and times the real constructor against a tmp `state_path` to
   record how wide the window is. The test becomes the regression
   test for 2b. If 2a cannot make two objects appear, stop and
   report; nothing else in the phase proceeds.
2. **Patch 241 locks `get_fip_ns`** with one `threading.Lock` on the
   `AgentMixin`, held across get, construct and store. Construction
   under the lock serialises the first fullsync for an external
   network by the constructor's cost, once; that is acceptable and
   the commit message says so.
3. **Patch 242 locks `ItemAllocator`** with a per-instance
   `threading.RLock` around `lookup`, `allocate` and `release`, and
   writes the state file through a temporary file in the same
   directory and `os.replace`. The test runs concurrent `allocate`
   calls for distinct keys and checks no duplicate value and a
   well-formed file. The agent-global state path shared by every
   `FipNamespace` is reported in the bug draft but not changed here.

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Reproduce per Decision 1 in `neutron-dvr`: the failing test, and a timing of `FipNamespace.__init__` with mocked `IptablesManager`. Report the numbers and the test's path; leave the test uncommitted. |
| 2b | high | opus | none | Patch 241 per Decision 2, including 2a's test. Subject "l3: Serialise FipNamespace creation per external network". Release note. |
| 2c | high | opus | none | Patch 242 per Decision 3, on top of 2b. Subject "l3: Lock ItemAllocator and write its state atomically". Tests in `neutron/tests/unit/agent/l3/test_item_allocator.py`. Release note. |
| 2d | medium | sonnet | none | Series directory, patch files, bug drafts appended to the findings document, `_build/test-apply.sh --skip-tests neutron-dvr-fip-ns-locking`. |

### Definition of done

- [ ] 2a's test fails at `61f6db5ac9` and passes at the tip.
- [ ] `tox -e py3 -- neutron.tests.unit.agent.l3` passes at the tip; `tox -e pep8` passes.
- [ ] `_build/test-apply.sh --skip-tests neutron-dvr-fip-ns-locking` passes.
- [ ] `tools/gerrit-pre-push-lint --stack 2` reports no ERROR or WARNING.

## Phase 3 --- hacking check for configuration read at import

Series `neutron-import-time-config-check/`, topic `import-time-config`
(shared with 1009883), patches 243 and 244, worktree
`neutron-confcheck`.

### Decisions

1. **Patch 243 fixes the three in-tree sites first,** so the check
   lands green. `objects/base.py`: always register `do_orm_execute`
   and have it return early unless `cfg.CONF.debug`, dropping the
   module-level `_debug`. `quotasv2.py`: drop `DB_QUOTA_DRIVER` and
   compare the run-time driver against the DB driver class paths
   (the tuple `quotasv2_detail.DB_QUOTA_DRIVERS` already lists them;
   move it somewhere both can import without a cycle, or duplicate
   the two strings with a comment, whichever the implementer finds
   cleaner). `quotasv2_detail.py`: read `cfg.CONF.QUOTAS.quota_driver`
   inside the lambda.
2. **Patch 244 adds N351** to `neutron/hacking/checks.py`: a
   logical-line check that fires when `indent_level == 0`, the line
   is not in `neutron/tests/`, and it reads an option from `CONF` or
   `cfg.CONF` (an attribute that is not one of `register_opts`,
   `register_opt`, `register_cli_opts`, `register_group`,
   `import_opt`, `import_group`, `set_default`, `set_override`, and
   is not a call on `CONF` itself). Message: "N351: Do not read
   configuration options at import time; read them where they are
   used." Tests in `neutron/tests/unit/hacking/test_checks.py` for a
   hit, a `register_opts` miss, an indented miss and a tests-path
   miss. Add the line to `HACKING.rst` and register the check in
   `tox.ini`'s `[flake8:local-plugins]`. `tox -e pep8` must pass with
   no other hit; if it finds one the session's AST scan missed, fix
   it in patch 243 and say so.

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Patch 243 per Decision 1 in `neutron-confcheck`. Subject "Read config options where they are used, not at import". Unit tests for the ORM listener (debug on and off) and for the quota extension description. |
| 3b | high | opus | none | Patch 244 per Decision 2, on top of 3a. Subject "hacking: Add N351 for config reads at import time". |
| 3c | medium | sonnet | none | Series directory, patch files, bug draft, `_build/test-apply.sh --skip-tests neutron-import-time-config-check`. |

### Definition of done

- [ ] `tox -e pep8` passes at the tip and fails, with N351, when the `_debug` line is restored in `objects/base.py`.
- [ ] `tox -e py3 -- neutron.tests.unit.hacking neutron.tests.unit.objects.test_base neutron.tests.unit.extensions.test_quotasv2` passes.
- [ ] `_build/test-apply.sh --skip-tests neutron-import-time-config-check` passes.

## Phase 4 --- static import-closure test

Series `neutron-import-closure-test/`, topic `import-memory`, patch
245, worktree `neutron-closure`.

### Decisions

1. **One unit test module**, `neutron/tests/unit/test_import_closure.py`,
   that parses each listed console script's module and, recursively,
   every `neutron.*` module it imports at module or class level (not
   inside functions, which run after configuration is parsed), and
   asserts that no module under `neutron.db` or `neutron.objects` is
   reached. Entry points come from `importlib.metadata` so the list of
   scripts is not duplicated. Contracts: `neutron-l3-agent`,
   `neutron-dhcp-agent`, `neutron-openvswitch-agent`. A comment says
   the list is meant to grow as the metadata, metering, SR-IOV and
   macvtap agents shed `neutron.agent.rpc`'s resource cache.
2. **The failure message names the import path**, `a <- b <- c`, so
   a regression is diagnosable from the test output alone.
3. **Static, not `sys.modules`.** The in-flight tests in 1011796 and
   1012169 spawn a fresh interpreter and inspect `sys.modules`; that
   catches dynamic imports but is slow and per-module. This test is a
   cheap whole-tree invariant and complements them; the commit message
   says so without naming the changes.

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Patch 245 per the decisions in `neutron-closure`. Subject "tests: Keep the DB and objects out of the agents' imports". Prove the test fails by adding `from neutron.objects import ports` to `neutron/agent/l3/agent.py` and reverting. |
| 4b | low | sonnet | none | Series directory, patch file, `_build/test-apply.sh --skip-tests neutron-import-closure-test`. |

### Definition of done

- [ ] The test passes at `61f6db5ac9` and fails with a named path when an agent imports `neutron.objects`.
- [ ] It runs in under two seconds.
- [ ] `tox -e pep8` passes; `_build/test-apply.sh --skip-tests neutron-import-closure-test` passes.

## Phase 5 --- thread switch interval in tests

Series `neutron-test-switch-interval/`, topic `thread-safety`, patch
245 (phase 4's number, freed when it was abandoned), worktree
`neutron-switch`.

### Decisions

1. **`DietTestCase.setUp`** records `sys.getswitchinterval()`, adds a
   cleanup that restores it, and sets `1e-5`.
2. **Measure before claiming.** Run `tox -e py3 --
   neutron.tests.unit.agent` three times at `61f6db5ac9` and three
   times with the change, and report the median wall times. Keep the
   change only if the growth is under 5% (Open questions); otherwise
   try `1e-4`.
3. **No race fix rides with it.** If the suite turns up a flaky test
   under the shorter interval, report it with the traceback rather
   than fixing it here; that is a finding, and a separate patch.

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | opus | none | Patch 246 per the decisions in `neutron-switch`. Subject "tests: Shorten the thread switch interval under test". Report the six timings and any new failures. |
| 5b | low | sonnet | none | Series directory, patch file, `_build/test-apply.sh --skip-tests neutron-test-switch-interval`. |

### Definition of done

- [ ] Timings recorded in the plan's execution notes; growth under the threshold.
- [ ] `tox -e py3 -- neutron.tests.unit.agent` passes with the change, or every new failure is listed.

## Phase 6 --- push audit

Audit the accumulated diff of phases 1 to 5 against `develop` using
the `Merged` column. Phases 1 to 5 land here as patch files; their
upstream fate is tracked in the Status table's phase rows as the
changes are uploaded and reviewed, the same way
`neutron-agent-pending-updates.md` does.

## Execution notes

- 2026-10-28 (AEDT): Phase 4 abandoned before any commit. Step 4a's
  required proof failed: adding `from neutron.objects import ports` to
  `neutron/agent/l3/agent.py` did not fail the test, because the
  console scripts import their agent class at run time and the static
  walk never reaches it. Walked from the agent modules the contract is
  false today and becomes true only once the import-memory series
  lands, which already tests it. The Situation bullet is corrected
  above; the worktree `neutron-closure` was discarded.
- 2026-10-28 (AEDT): Step 2a reproduced the duplicate `FipNamespace`
  with two threads held inside the constructor (`MismatchError: is
  not`), and timed the real constructor at a 64 ms minimum against a
  5 ms switch interval, so phase 2 proceeded.
- 2026-10-28 (AEDT): Phase 5 timings, `tox -e py3 --
  neutron.tests.unit.agent` on a host at load 22-63 of 16 cores, six
  interleaved pairs (base, then `1e-5`): base 61.9, 58.3, 37.3, 84.0,
  125.8, 35.5 s; changed 63.7, 39.3, 42.9, 95.7, 76.6, 47.4 s. Medians
  60.1 s and 55.6 s; CPU time over the last three pairs 335.7 s and
  321.5 s. The noise swamps any cost, so `1e-5` stands and `1e-4` was
  not tried. 2080 tests passed in all twelve runs; nothing flaked.
- 2026-10-28 (AEDT): Phase 3's N351 departs from Decision 2: it tracks
  enclosing `def` lines through flake8's `checker_state` instead of
  testing `indent_level == 0`, because the original `_debug` read sat
  inside a module-level `try:` at indent 4 and the simpler rule missed
  it. Class bodies and default arguments are caught too; lambdas are
  not. With the old block restored, flake8 reports
  `neutron/objects/base.py:75:14: N351`. The `QUOTA_DB_DRIVERS` tuple
  went into `neutron/conf/quota.py`, where the two class-path strings
  already live and nothing from neutron is imported.
- 2026-10-28 (AEDT): Phase 2's `_write` departs from Decision 3's
  `tempfile`: it opens `<state_file>.<uuid>.tmp` with mode `'x'` and
  `os.replace`s it, which keeps the umask-derived file mode that
  `open(path, 'w')` gave; `neutron_lib.utils.file.replace_file` was
  tried first but the DVR test fixtures mock it globally and it forces
  0o644. The concurrency test (64 threads, distinct keys) failed 14 of
  20 runs on the old code and passed 80 of 80 with the lock.
- 2026-10-28 (AEDT): Phase 1 departures. Patch 239 schedules the
  resync in `_call_driver`, not `configure_dhcp_for_network`: for a
  routed network `call_driver` returns `all(ret)`, which would have
  turned the deliberate no-resync exception paths into resyncs.
  `DhcpBase.restart` still returns `None`, so no in-tree restart path
  changes. Patch 240's bug dates from 06bc855dcf (2024, bug #2051729),
  not the 2022 change the Situation bullet implied; that change moved
  from `deepcopy` to editing the shared object so the next segment's
  stale-device cleanup sees the previous segment's DHCP port, so the
  fix copies the network but shares its `ports` list. After the stack
  lint, release notes were added to patches 237 and 243 (both
  user-visible) by amending those commits and replaying the rest of
  each series, which changed their SHAs but not their Change-Ids.
- 2026-10-28 (AEDT): Mikal uploaded all nine patches; found on Gerrit
  by Change-Id afterwards. Bug numbers are still to come.

  | Patch | Change | Topic |
  |-------|--------|-------|
  | 237 | [1012616](https://review.opendev.org/c/openstack/neutron/+/1012616) | dhcp-agent-fixes |
  | 238 | [1012617](https://review.opendev.org/c/openstack/neutron/+/1012617) | dhcp-agent-fixes |
  | 239 | [1012618](https://review.opendev.org/c/openstack/neutron/+/1012618) | dhcp-agent-fixes |
  | 240 | [1012619](https://review.opendev.org/c/openstack/neutron/+/1012619) | dhcp-agent-fixes |
  | 241 | [1012621](https://review.opendev.org/c/openstack/neutron/+/1012621) | dvr-fip-ns-locking |
  | 242 | [1012622](https://review.opendev.org/c/openstack/neutron/+/1012622) | dvr-fip-ns-locking |
  | 243 | [1012623](https://review.opendev.org/c/openstack/neutron/+/1012623) | import-time-config |
  | 244 | [1012624](https://review.opendev.org/c/openstack/neutron/+/1012624) | import-time-config |
  | 245 | [1012625](https://review.opendev.org/c/openstack/neutron/+/1012625) | thread-safety |
