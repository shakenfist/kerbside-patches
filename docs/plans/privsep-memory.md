# Plan: OpenStack memory in devstack CI

## Goal

Measure, and then reduce, the memory that oslo.privsep daemons use on
devstack CI nodes. Do it in an order that leaves a public record
OpenDev will accept: land telemetry in devstack first, record a
baseline, then land one fix at a time and show the change in the same
telemetry after each one.

The plan began with oslo.privsep. A follow-up on 2026-10-23 found that
unneeded imports in the services themselves cost as much again, so
Phase 6 covers those with the same telemetry and the same report.

The trigger was the TC meeting on 2026-10-20 (log:
<https://meetings.opendev.org/meetings/tc/2026/tc.2026-10-20-17.00.log.html>).
There clarkb said privsep's memory use is "a major source of CI
instability", through swapping. He also said he has raised this for
about five years without anyone acting, and he declined to file a bug.

## Status

| Phase | What | Status | Merged |
|-------|------|--------|--------|
| 1 | File the bug with the evidence below; [draft](../privsep-memory-bug-draft.md) held until upstream is ready | Blocked | |
| 2 | devstack telemetry in `get-stats.py`, plus a baseline; in review as [1011786](https://review.opendev.org/c/openstack/devstack/+/1011786), [1011787](https://review.opendev.org/c/openstack/devstack/+/1011787), [1011794](https://review.opendev.org/c/openstack/devstack/+/1011794) | In progress | |
| 3 | oslo.privsep: stop importing eventlet in the daemon; in review as [1011795](https://review.opendev.org/c/openstack/oslo.privsep/+/1011795) | In progress | |
| 4 | neutron: cut the privileged import chain; in review as [1011796](https://review.opendev.org/c/openstack/neutron/+/1011796), [1011797](https://review.opendev.org/c/openstack/neutron/+/1011797) | In progress | |
| 5 | Credit LLMs with `Assisted-By` only, in one standard form, in the linter and every in-flight patch | In progress | |
| 6 | Services: stop importing what they do not use | Not started | |
| 7 | Decide on structural changes (fewer contexts) | Not started | |
| 8 | Report back to the TC and openstack-discuss | Not started | |
| 9 | Push audit | Not started | |

Status cells hold only the plan status vocabulary. A phase whose
changes are proposed upstream is `In progress` until they merge. Phase
1 is `Blocked` on a decision recorded in its section. Each phase's
`Merged` cell records where it landed: this repository's merge commit
for the `_patches/` side, and `<repo> <sha>` for each upstream change.

## Situation

These are the findings of an investigation on 2026-10-22. The memory
figures come from devstack's `memory_tracker` logs in seven builds on
2026-10-21, and from privsep-helper daemons measured in a container
with nova 34.0.0, neutron 29.0.0 and oslo.privsep 3.12.0.

- **The size claim holds; the causation claim is unproven.** On an
  8 GB node, privsep daemons together peak at 0.3-0.45 GB in OVN jobs
  and about 1 GB on ML2/OVS controllers (nova-next,
  neutron-ovs-tempest-multinode-full). That is 4-14% of summed RSS.
  QEMU guests (2.3-3.9 GB) and the API, conductor and neutron-server
  workers are bigger. Six of the seven 8 GB nodes swapped 1.7-5.9 GB.
  Privsep pages are cold: under pressure a neutron daemon falls from
  about 118 MB to 10-25 MB of RSS. So privsep adds to swap use, but no
  evidence ties it to swap-in thrash.
- **There is no public record beyond IRC remarks.** oslo.privsep has
  no memory bug on Launchpad, and none of clarkb's 151 bug reports is
  about this. His record is a 2021 IRC estimate of "almost 1GB", which
  is about right, one sentence in a 2021 email, and remarks in 2024
  and 2026. JayF's 2024-02-29 openstack-discuss call for help named
  oslo.privsep and got no replies.
- **The daemons are exec'd, not forked.** Every service uses the
  ROOTWRAP start method: `sudo <rootwrap> privsep-helper
  --privsep_context ...`. The daemons run with ppid 1. The "fork with
  the whole service resident" explanation given in the meeting is
  wrong for devstack.
- **An idle daemon costs about 35 MB of anonymous memory.** Most of it
  comes from oslo.privsep itself, not the service. `daemon.py` imports
  eventlet at module level, which pulls in dnspython, cryptography and
  pyOpenSSL: 446 modules and about 19 MB. oslo.config and oslo.log add
  about 13 MB more. The context module adds under 1 MB for nova,
  os-brick, os-vif and cinder, and about 3 MB for neutron.
- **Daemons grow on first use.** `Daemon._process_cmd` imports each
  entrypoint's module the first time it is called. In neutron,
  `neutron/privileged/agent/linux/ip_lib.py` imports
  `neutron.common.utils`, which brings in openstacksdk, sqlalchemy,
  pecan, os_ken and keystoneauth: about 1600 modules and +71 MB. So
  a warm neutron daemon is about 118 MB, against about 60 MB for nova
  and 55 MB for os-vif.
- **Neutron runs many contexts, once per agent.** On an ML2/OVS
  controller, q-agt, q-dhcp and q-l3 each start their own `default`,
  `link_cmd` and `namespace_cmd` daemons as they need them, giving
  11-13 daemons per node. The OVN agent starts three.
- **Threads are cheap.** A full 16-thread pool adds about 1.6 MB. So
  devstack change 988806, which sets `thread_pool_size=1`, will not
  save much.
- **devstack already records privsep, but badly.** `tools/get-stats.py`
  lists `privsep` among its default processes and writes
  `performance.json` in every job. It only samples RSS once, at the end
  of the job, after swap has emptied the daemons, so the number it
  reports looks small.

## Patch series

There is one independent series per upstream repository. The numeric
suffix gives the order they are meant to land in:

| Series | Repository | Patches | Phase |
|--------|------------|---------|-------|
| `devstack-privsep-memory-001` | devstack | patch215 (tolerate zombies), patch216 (memory telemetry), patch220 (CentOS and Rocky) | 2 |
| `oslo.privsep-privsep-memory-002` | oslo.privsep | patch217 (only inspect eventlet if already imported) | 3 |
| `neutron-privsep-memory-003` | neutron | patch218 (no `neutron.common.utils`), patch219 (no `neutron_lib`) | 4 |

- **One Gerrit topic.** All three set `destination_branch:
  privsep-memory`, so the changes share that topic across the three
  repositories.
- **Pushed.** All six changes were pushed by hand on 2026-10-22,
  before the Phase 1 bug: devstack 1011786, 1011787 and 1011794
  (patch215, 216, 220), oslo.privsep 1011795 (patch217) and neutron
  1011796 and 1011797 (patch218, 219). All three series now have
  `repush: true`, so `_build/repush-openstack.sh` pushes new revisions
  when Mikal runs it. Nothing pushes to Gerrit automatically: the daily
  workflow only rebases the patches.
- **Depends-On.** patch217 and patch218, the root patches of 002 and
  003, carry `Depends-On:` 1011787, the telemetry change. Its parent
  1011786 comes with it, so no other patch repeats it. Zuul then runs
  the changes' own devstack jobs with the telemetry applied:
  oslo.privsep's `tempest-full-py3` (from `lib-forward-testing-python3`)
  and neutron's tempest jobs. Grenade jobs do not run the
  `capture-performance-data` role, so they produce no telemetry.
  - The footers also stop 002 and 003 merging before 001, which is the
    order we want. Once 001 merges the footers are satisfied and do no
    harm.
- **Before numbers.** 001's own jobs give the baseline for jobs that
  devstack shares with the other repositories. For neutron-only jobs,
  push a DNM neutron change that carries only the Depends-On, as a
  control.
- **Bug reference.** When the Phase 1 bug exists, add `Related-Bug:` to
  every patch. It is held for now (see Phase 1), so the changes go
  ahead without it.

## Phase 1 --- file the bug

File an oslo.privsep bug, with a neutron task added, containing the
numbers above and the method behind them. Changes in later phases
reference it (`Related-Bug`/`Closes-Bug`), and the TC goal discussion
gets something to point at. Mikal files it; Claude drafts it.

**Held, 2026-10-23.** The draft is in
[../privsep-memory-bug-draft.md](../privsep-memory-bug-draft.md),
together with a checklist for refreshing it before it is filed. Mikal
chose not to file it yet, because whether this is a problem is still
being argued upstream. The fixes land first, so their measured savings
make the case, and the bug is filed once the community is ready to
track it. Until then the patches carry no `Related-Bug:` footer, and
nothing waits on this phase.

## Phase 2 --- devstack telemetry and a baseline

There are three changes, carried as series `devstack-privsep-memory-001`.

1. **Tolerate zombie processes.** Today a zombie on the node makes
   `proc.cmdline()` raise `psutil.ZombieProcess`, which aborts the
   script and loses the whole of `performance.json`. This was seen
   when running the script locally.
2. **Record peak and cold memory.**
   - Per tracked process, add `rss_peak` (VmHWM), `rss_anon` (RssAnon)
     and `swap` (VmSwap) from `/proc/PID/status`. That file is
     world-readable, so this works for root's privsep-helper from the
     unprivileged collection role.
   - Add a top-level `swap` object with `pswpin`, `pswpout`, `zswpin`
     and `zswpout` from `/proc/vmstat`. clarkb's claim is about swap
     traffic, and zswap is enabled on CI nodes.

3. **Fix the empty file on CentOS Stream and Rocky.** The
   `capture-performance-data` role always calls
   `setup_devstack_virtualenv`, but `GLOBAL_VENV` is False on those
   distributions, so no venv exists and `performance.json` is empty in
   every build. Use the venv's python when it exists, and otherwise
   `python${PYTHON3_VERSION}`, where devstack installed psutil and
   PyMySQL.

1011787's own jobs confirmed changes 1 and 2 work: every Debian and
Ubuntu build has the new fields. A peak of 568 MB of privsep on
`tempest-full-py3` (8 daemons) and 1128 MB on
`devstack-platform-ubuntu-noble-ovs` (14 daemons), against an
end-of-job RSS of 140 and 402 MB.

clarkb's dstat change (devstack 1011544) gives a time series alongside
these; this phase does not duplicate it.

**Baseline.** Once the change merges, collect `performance.json` from
at least 20 successful builds each of `tempest-integrated-compute`,
`nova-next`, `tempest-full-py3` and `neutron-ovs-tempest-multinode-full`,
through the Zuul builds API. Summarise per privsep context: peak RSS,
anonymous memory plus swap, and the daemon count, along with the
node's swap counters. The collection script goes in `tools/` here.

## Phase 3 --- oslo.privsep: no eventlet in the daemon

Open change 1010664 removes eventlet from oslo.privsep. In the
prototype it took idle nova, os-brick, os-vif and ceilometer daemons
from 39 to 26 MB of RSS, and warm os-brick from 41 to 28 MB. It does
nothing for two services:

- **neutron:** `neutron/__init__.py` imports oslo.service's threading
  backend, which imports futurist, which tries `import eventlet` in
  `futurist/_utils.py`.
- **cinder:** `cinder/__init__.py` imports eventlet itself.

1010664 removes eventlet support, so it is a breaking change and will
not backport. Series 002 carries the alternative: import eventlet only
when it is already in `sys.modules`. It gives the same numbers, does
not change behaviour, and can be backported, so it can be offered as
the stable-branch fix alongside 1010664. Whether to push it while
1010664 is open is still undecided.

A follow-up would make oslo.service import its backend lazily. That
would save about 12 MB more per neutron daemon: a warm neutron daemon
would be 45 MB rather than 57, simulated by blocking the import. A
`sys.modules` guard in futurist is not equivalent, because whether
`GreenThreadPoolExecutor` is available depends on that import.

Watch about a week of builds before the next fix lands, so the
savings can be told apart.

## Phase 4 --- neutron: cut the privileged import chain

The prototype cuts two import edges from `neutron/privileged/`. The
figures are for a warm `neutron.privileged.default` daemon;
`namespace_cmd` and `link_cmd` move by the same amounts.

| Change | Warm RSS / anon MB | Modules |
|---|---|---|
| neutron master | 118 / 107 | 2382 |
| Change 1 (patch218): move `read_file` and the two `cidr_broadcast_address*` helpers into a new `neutron/common/privsep_safe_utils.py`, which `neutron.common.utils` re-exports | 90 / 81 | 1438 |
| Change 2 (patch219): also drop the `neutron_lib` imports from privileged `ip_lib`, `utils` and `tc_lib` | 57 / 50 | 999 |

- **Change 1 should not be controversial.** It also adds a test that
  imports every privileged module in a fresh interpreter and fails if
  `neutron.common.utils` is loaded.
- **Change 2 needs a judgement call.** It copies `IP_VERSION_4`,
  `IP_VERSION_6`, `DEVICE_NAME_MAX_LEN` and `safe_decode_utf8` out of
  neutron_lib, with a test that checks the copies still match.
  - The real cause is upstream: `neutron_lib/__init__.py` imports
    `neutron_lib.db.api` (sqlalchemy, oslo.db, pecan) on every
    neutron_lib import, deliberately, because of LP #1802369.
  - Fixing that in neutron-lib is the better root-cause fix, but it
    changes behaviour there. Ask neutron reviewers which they prefer
    before pushing change 2.
- **`conntrack_cmd` is unaffected.** It still loads neutron_lib
  through `netlink_lib` and stays at about 88 MB.
- **Tests:** 336 of 336 pass in the touched unit test areas, and
  flake8 with neutron's hacking checks passes. The full suite, pylint,
  mypy and the root-only functional tests have not been run.

Projected per-node totals (warm RSS / anon MB):

| Scenario | Baseline | Neutron change 2 | Change 2 + Phase 3 |
|---|---|---|---|
| devstack all-in-one, OVN, 8 daemons | 584 / 521 | 400 / 347 | 358 / 302 |
| ML2/OVS controller, 12 daemons | 1134 / 1022 | 704 / 617 | 688 / 601 |

The ML2/OVS daemon count comes from reading the code, not from a real
node. Phase 2's telemetry should confirm it.

Phase 4 does more than Phase 3 and does not depend on it, so it may
land first if neutron reviews faster.

## Phase 5 --- credit LLMs with Assisted-By only

Added on 2026-10-23. The changes this plan pushed drew pushback from
the OpenStack community about crediting an LLM with `Co-Authored-By:`.
The community wants `Assisted-By:` to be the only trailer used for that,
and wants `Co-Authored-By:` kept for people. That applies to every
change this repository sends upstream, not just this plan's, so the fix
covers all of `_patches/`. It comes before Phase 6, so the patches that
phase writes follow the rule from the start, and the linter enforces it
for them.

Reviewing the first sweep the same day, Mikal also asked for one form
of `Assisted-By:` line, `Assisted-By: <model> (<effort> effort)`, with
the effort level whenever it is known and never an email address. The
sweep had copied the whole `Co-Authored-By:` value, context window and
email included. Decision 7 records the form, and steps 5a and 5b were
redone to it.

**Planning effort: medium.** It is a mechanical sweep plus a small
linter check. The judgement calls are listed under Decisions.

### What the survey found

- **42 patches credit Claude with `Co-Authored-By:`**, across seven
  model and effort variants (`Claude Opus 5`, `Claude Opus 5.5`,
  `Claude Opus 4.7`, `Claude Fable 5` and others). 40 of them also carry
  an `Assisted-By:` line. patch135 and patch136 do not.
  - 34 of the 42 are listed in some `ORDER` file. 22 of those are in
    series with `repush: true`, which together are 10 series:
    kolla-ansible-json-logging (5), kolla-ansible-kerbside (4),
    devstack-privsep-memory-001 (3), kolla-ansible-mariadb-collations
    (2), neutron-agent-pending-updates (2), neutron-privsep-memory-003
    (2), and one each in kolla-ansible-fluentd-patience,
    neutron-timecost-fix, neutron-ovsdb-helpers-fix and
    oslo.privsep-privsep-memory-002.
  - The other 8 are listed in no `ORDER` file: patch135, 136, 152,
    155, 159, 176, 179 and 195.
- **Three `Co-Authored-By:` lines credit people** (Michal Nasiadka,
  Doug Szumski and Michael Still). Each appears in a `.patch` and its
  `.patch-message`, so six lines in all. They stay.
- **54 patches carry an `Assisted-By:` line, in ten variants.** Most
  include the context window and `<noreply@anthropic.com>`. Seven say
  only `Claude Code`; six of those (patch152, 154, 155, 176, 196 and
  198) also have a `Co-Authored-By:` that names the model, and patch147
  names none.
- **Three of the 42 have no `.patch-message` file:** patch135, 136 and
  155. The rest have one, generated by `tools/extract-commit-message`,
  so it must be regenerated after each edit.
- **Nothing enforces attribution today.** `tools/gerrit-pre-push-lint`
  only treats `Co-Authored-By` and `Assisted-By` as footers, when it
  counts the commit message's paragraph budget. It has no test file.
  `tools/patch_message.py` is the shared reader for a patch's embedded
  commit message, and `extract-commit-message`, `extract-commit-author.py`
  and `check-depends-on.py` already use it. No pre-commit hook looks at
  attribution.
- **The rule is written down as the opposite in three places.**
  - `docs/tactics.md` lists `Co-Authored-By` among the trailers this
    repository adds.
  - `.claude/skills/propose-upstream-patch/SKILL.md` mentions it as a
    footer.
  - Most important, the global commit hook,
    `~/.claude/hooks/commit-guidelines.md`, tells every session to
    attribute Claude with `Co-Authored-By`. That file is outside this
    repository, and it applies to commits made in `src/<project>` for
    upstream as well as to this repository's own commits.
- **Whether Gerrit keeps votes on a message-only revision could not be
  checked.** OpenDev's label configuration needs authentication to read.
  Gerrit's default `copyCondition` for Code-Review does not include
  `changekind:NO_CODE_CHANGE`, so assume votes are lost. The one vote at
  risk in this plan is the +2 on 1011796. Other plans' changes in the
  22 may hold votes too.

### Decisions

1. **Convert, do not just delete.** Remove a `Co-Authored-By:` line
   that credits an LLM. If the message then has no `Assisted-By:` line,
   add one in the Decision 7 form, as for patch135 and patch136.
   OpenStack's AI policy asks for the disclosure, so removing it
   entirely would be the wrong fix.
2. **Every patch in `_patches/`, including the 8 in no `ORDER` file.**
   One invariant over the whole directory is simpler to enforce with a
   pre-commit hook than one that depends on which `ORDER` files list a
   patch. Editing a patch nobody applies costs nothing.
3. **The LLM test lives in `tools/patch_message.py`,** as
   `llm_co_authors(message)`. It returns the `Co-Authored-By:` lines
   (key matched without regard to case) whose name matches
   `Claude|ChatGPT|GPT-\d|Copilot|Gemini|Codex` or whose email is in
   `anthropic.com` or `openai.com`. Both the linter and the new
   pre-commit hook use it, so they cannot disagree. Matching known names
   misses an unknown tool, but it never flags a person.
4. **The linter reports it as an error,** with the replacement
   `Assisted-By:` line as its suggestion. An error, not a warning,
   because the community has asked for this explicitly.
5. **Accept the Gerrit churn now.** Confirmed by Mikal on 2026-10-23:
   the churn is unavoidable, since the community is insisting on the
   change. Mikal's repush sends a new revision of 25 changes in 13
   `repush: true` series whose only change is the commit message
   (Decision 7 reaches more changes than the 22 the survey counted),
   and some votes will drop. Waiting until each
   change next needs a real revision would leave the trailer the
   community objected to on display for weeks. Mikal re-asks for the
   lost votes, and 1011796's +2 first.
6. **The global hook is Mikal's to change.** The repository's own rules
   (the skill, the docs, `AGENTS.md`) change in this phase. The global
   hook is his own configuration. At his request it was changed in his
   llm-setup repository, in 83e4819 (Assisted-By, not Co-Authored-By,
   for OpenStack) and 85eca01 (the Decision 7 form).
7. **One form of `Assisted-By:` line:** `Assisted-By: <model> (<effort>
   effort)`, such as `Assisted-By: Claude Opus 5.5 (high effort)`, or
   just the model when the effort level is not known. The effort level
   stays because it changes what the model produces. The email address,
   context window and any other settings go. `--fix` rewrites every
   `Assisted-By:` line in this form and drops one that then repeats
   another. Where an `Assisted-By:` names only the tool (`Claude Code`)
   and the `Co-Authored-By:` being removed names the model, the model is
   carried across by hand: that is the six patches the survey found.
   The checker does not do this itself, because replacing one value
   with another automatically could lose information in a future
   commit. `Claude Code` alone still passes, since a bare name is the
   right form when the model is not known.

### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Add `llm_co_authors(message)` to `tools/patch_message.py` as Decision 3 of Phase 5 in `docs/plans/privsep-memory.md` specifies. It returns the matching trailer lines, unstripped, in order. Keep the patterns in one module-level tuple, with a comment saying why it matches names and not "any noreply address". In `tools/gerrit-pre-push-lint`'s `check_commit_message`, call it and add one `Severity.ERROR` issue per line: the message says OpenStack reserves `Co-Authored-By` for people, and the suggestion is the same value as `Assisted-By:`, or "remove it" when an `Assisted-By:` line already exists. The linter is run from other directories (`/path/to/kerbside-patches/tools/gerrit-pre-push-lint --commit` inside `src/<project>`), so import `patch_message` by putting the script's own directory on `sys.path`, as a comment there explains. Add `tools/test-patch-message-attribution.py` (unittest, stdlib only), mirroring the layout of `tools/test-check-depends-on.py`. It covers Claude, Copilot and GPT trailers in any case, a person (`Co-Authored-By: Michal Nasiadka <mnasiadka@gmail.com>`) not matching, an `anthropic.com` email under another name, and the linter's error with both suggestions, by calling `GerritLinter.check_commit_message` directly (load the extensionless script with `importlib.machinery.SourceFileLoader`). Add a pre-commit hook for the test, modelled on `write-build-manifest-tests`, and add the test to the `flake8` hook's `files`. Do not add the `_patches/` hook yet; step 5b does, once the sweep makes it pass. Document the new check in `gerrit-pre-push-lint`'s row in `docs/script-reference.md`. Commit subject: "Reject LLM Co-Authored-By trailers in the linter." |
| 5b | medium | sonnet | none | Sweep `_patches/`. Write `tools/check-llm-attribution.py`: it takes patch and `.patch-message` file paths, reads each message with `patch_message.extract`, and exits 1, listing `file: line`, for any line `llm_co_authors` returns. Give it a `--fix` mode that applies Decision 1: remove each such line, and when the message then has no `Assisted-By:` line, put one with the same value where the first removed line was. `--fix` edits only the commit message part of a `.patch` file, the part before the first `---` line or `diff --git`; diff bodies are never touched. After fixing a `.patch`, regenerate its `.patch-message` with `tools/extract-commit-message`, where one exists. Run `--fix` over all of `_patches/`. Expect 42 `.patch` files and 39 `.patch-message` files to change, and nothing else. patch135, 136 and 155 have no message file. Confirm that the six `Co-Authored-By` lines crediting people survive: `grep -h Co-Authored-By _patches/*` should show only Michal Nasiadka, Doug Szumski and Michael Still. Then add a pre-commit hook `llm-attribution`, with `entry: tools/check-llm-attribution.py`, `files: ^_patches/.*\.patch(-message)?$` and `language: python`, add the script to `flake8`'s `files`, and give it a row in `docs/script-reference.md`. Run `_build/test-apply.sh --skip-tests` for every series directory whose `ORDER` lists an edited patch (`_build/find-patch-usage.py <patch>` lists them), to show every patch still applies. Run `pre-commit run --all-files`. Commit subject: "Credit Claude with Assisted-By in every patch." |
| 5c | low | sonnet | none | Bring the repository's written rules in line. In `.claude/skills/propose-upstream-patch/SKILL.md`, say that an LLM is credited with `Assisted-By:` only, never `Co-Authored-By:`, because OpenStack reserves that for people; that `gerrit-pre-push-lint` rejects it; and that a session's global commit guidance saying otherwise does not apply to upstream patches. In `docs/tactics.md`, take `Co-Authored-By` out of the list of trailers this repository adds, and say the same in one sentence there. In `AGENTS.md`, add one line under "Adding a New Patch": "Upstream patches credit an LLM with `Assisted-By:` only; `Co-Authored-By:` is for people, and pre-commit enforces it." Then, in the management session and not by sub-agent, draft the matching edit to `~/.claude/hooks/commit-guidelines.md`: commits in an OpenStack tree under `src/`, or for a patch in `_patches/`, use `Assisted-By:` alone. Show it to Mikal to apply. Commit subject: "Document Assisted-By for upstream patches." |

Steps 5a and 5b landed as one commit, "Standardise how every patch
credits an LLM.", because redoing them to Decision 7 put the rules for
both in `patch_message.attribution_problems()`. Where the 5a and 5b
briefs above say to copy the `Co-Authored-By:` value, Decision 7
overrides them. Step 5c landed as
"Document Assisted-By for upstream patches.". All of Phase 5 is on
branch `privsep-memory-plan` (pull request #1953), alongside Phases 2
to 4, not on a phase branch of its own. Mikal asked for that.

Mikal merges this phase before Phase 6 pushes anything. After he runs
the repush, the management session lists the changes that lost votes,
from each change's Gerrit page, so he can ask for them again.

### Definition of done

- [x] `tools/check-llm-attribution.py _patches/*` exits 0, and
  `grep -l "Co-Authored-By: Claude" _patches/*` prints nothing.
- [x] `grep -h "Co-Authored-By" _patches/*` shows only the six lines
  crediting people.
- [x] Every patch that credits Claude has exactly one `Assisted-By:`
  line, in the Decision 7 form, including patch135 and patch136.
  `grep -h -i "^ *Assisted-By" _patches/*.patch | grep -E "<|context"`
  prints nothing.
- [x] `tools/gerrit-pre-push-lint --commit`, run on a scratch commit
  with `Assisted-By:` and `Co-Authored-By:` lines carrying the context
  window and email, exits non-zero with an error for each, suggesting
  `Assisted-By: Claude Opus 5.5 (high effort)` and removal.
- [x] `pre-commit run --all-files` passes with the `llm-attribution`
  hook registered. The sweep commit's diff in `_patches/` changes only
  `Co-Authored-By:` and `Assisted-By:` lines, and all 20 series using an
  edited patch pass `_build/test-apply.sh --skip-tests`.
- [x] Mikal has applied the global hook edit: llm-setup 83e4819 and
  85eca01.
- [x] The changes that lost votes are listed for Mikal. He ran the
  repush himself on 2026-10-23, before the merge, so the list is below.

Votes lost to the 2026-10-23 repush, read from the "Outdated Votes"
part of each new revision's upload message. Every project involved
copies Code-Review only on `changekind:TRIVIAL_REBASE OR is:MIN`, so a
commit message change drops every vote except a -2. Changes whose
revision left the message alone kept their votes. Mikal re-asks for
these:

| Change | Lost |
|--------|------|
| neutron [1011796](https://review.opendev.org/c/openstack/neutron/+/1011796) | Brian Haley +2, Jay Faulkner +1, Rodolfo Alonso -1 |
| neutron [1011797](https://review.opendev.org/c/openstack/neutron/+/1011797) | Jay Faulkner +1, Rodolfo Alonso -1 |
| neutron [1009882](https://review.opendev.org/c/openstack/neutron/+/1009882), [1009883](https://review.opendev.org/c/openstack/neutron/+/1009883) | Lajos Katona +2 |
| neutron [1009917](https://review.opendev.org/c/openstack/neutron/+/1009917), [1009918](https://review.opendev.org/c/openstack/neutron/+/1009918) | Lajos Katona +2, Lucas Henry +1 |
| oslo.privsep [1011795](https://review.opendev.org/c/openstack/oslo.privsep/+/1011795) | Jay Faulkner +1 |
| devstack [1011786](https://review.opendev.org/c/openstack/devstack/+/1011786), [1011787](https://review.opendev.org/c/openstack/devstack/+/1011787), [1011794](https://review.opendev.org/c/openstack/devstack/+/1011794) | Jay Faulkner +1 |
| kolla-ansible [1005469](https://review.opendev.org/c/openstack/kolla-ansible/+/1005469) | Alma Maillet-Contoz +1, Franciszek Przewoźny +1 |

Rodolfo's -1s on 1011796 and 1011797, and Jay Faulkner's comment on
1011796, were the `Co-Authored-By:` objection that started this phase;
the new revision answers them, but the threads are still unresolved
and need a reply. Rodolfo's other comment on 1011796, that it "does not
depend on this devstack patch", questions the `Depends-On:` design in
the Patch series section and is still open.

### Back brief

Before step 5b, confirm Decision 5 with Mikal. It is the outward-facing
one: merging this phase pushes new revisions of 25 changes, some of them
in other plans, and may drop the +2 on 1011796. Confirmed on 2026-10-23.

## Phase 6 --- services: stop importing what they do not use

Found on 2026-10-23, after 1011787's builds showed which processes
are large. A service's steady-state memory is mostly the code it
imports: the import-only RSS measured below is within about 10% of the
CI peak for the scheduler, novncproxy and the neutron agents. So
packages that a process loads but never calls cost real memory, in
every worker and parent process.

**Method.** A container built from master installs nova, neutron,
cinder, glance, keystone and placement. For each service it imports
the entry point and the plugins devstack configures, then records
anonymous RSS, `sys.modules`, and the importer chain that first pulled
in each package. The cost of a suspect package is the anonymous RSS it
adds on top of a common base (oslo.config, oslo.log, requests,
cryptography, keystoneauth, webob). tracemalloc is no use here,
because it charges code objects to `importlib`, not to the package.
The scripts are in `tools/import-memory/`, and
[../import-memory-scans.md](../import-memory-scans.md) explains how to
run a scan like this one again.

**Findings.** All of these are still on master. Process counts come
from `tempest-full-py3` (OVN all-in-one) and
`devstack-platform-ubuntu-noble-ovs` (ML2/OVS) in 1011787's builds.

| # | Cause | Per process | Per node (OVN / ML2-OVS) | Fix lives in |
|---|---|---|---|---|
| 1 | `futurist/_utils.py` imports eventlet at module level. Every service already on threads loads it anyway: neutron, nova-api, nova-api-meta and glance. keystone and placement get it through oslo.messaging's import of oslo.service's eventlet backend | +11 MB | ~190 / ~240 MB | futurist, oslo.service |
| 2 | `neutron_lib/__init__.py` imports `neutron_lib.db.api` (oslo.db, sqlalchemy, pecan, mako, pygments) in every agent. This is LP #1802369 again, the root cause behind patch219 | +25 MB | ~50 / ~200 MB | neutron-lib |
| 3 | `neutron/api/extensions.py` imports `openstack.connection` at module level, for the optional `ProjectIdMiddleware` alone. Agents load it through `neutron.common.utils` -> `neutron.api.api_common` | +13 MB | ~65 / ~155 MB | neutron |
| 4 | `neutron_lib/api/validators/__init__.py` imports `os_ken.lib.packet.ether_types`, which loads the whole os_ken packet library for one constants table. It hits every neutron process except the OVS agent | +8.5 MB | ~70 / ~120 MB | neutron-lib |
| 5 | `neutron/db/models/agent.py` imports `neutron.agent.common.utils` for `is_agent_down`, so every server process loads pyroute2 and `neutron.privileged` | +10 MB | ~60 MB | neutron |
| 6 | `keystone/api/_shared/saml.py` imports `keystone.federation.idp`, and with it pysaml2, unconditionally | +15 MB | 30 MB | keystone |
| 7 | `placement/deploy.py` imports alembic at module level, though it only uses it when `sync_on_startup` is set, which is off by default | +11 MB | 22 MB | placement |
| 8 | nova-compute loads oslo.db and sqlalchemy through `nova.context`, but never uses a database | +17 MB | 17 MB per compute node | nova |

That is roughly 0.5 GB per OVN node and 0.85 GB per ML2/OVS
controller, comparable to phases 3 and 4. The totals add up
per-package costs that share dependencies, and they count shared
copy-on-write pages in both parent and worker, so treat them as upper
bounds. These pages are as cold as privsep's, and the same caution
about swap volume versus swap thrash applies.

Checked and not counted, because the process does use them: networkx
in cinder (taskflow runs its flows), openstacksdk in nova (the placement
client), paramiko in nova (keypairs). Not import problems, but worth
noting for Phase 7: devstack sets `lazy-apps = true` for every uWSGI
service, so each worker imports the app separately and shares nothing
with the others.

**Order.** Cheapest and least controversial first, one series per
repository, each with a test that imports the module in a fresh
interpreter and fails if the dropped package is loaded (the pattern
patch218 uses):

1. #3, #6 and #7: move one import into the function that uses it.
2. #4 and #5: small refactors.
3. #1: futurist must keep `GreenThreadPoolExecutor` working when
   eventlet is installed but not yet imported, so the detection has
   to move to construction time. This is the largest single saving.
   Fixing oslo.service's default backend for keystone and placement
   is separate work.
4. #2: the neutron-lib root-cause fix. Raise it with neutron
   reviewers alongside patch219's judgement call, because it changes
   when ORM event listeners are registered.
5. #8: only if nova reviewers want it, because `nova.context` is
   central.

**Telemetry.** `performance.json` cannot show these changes yet,
because `get-stats.py`'s default process list leaves out the OpenStack
services, and its `services` figures are end-of-job cgroup totals that
include page cache. Either add the service processes to the default
list in a follow-up to 1011787, which gives them `rss_peak` and
`rss_anon`, or build the baseline from `memory_tracker` per-process
peaks. The first is better, because it is the same record the TC
report already uses.

## Phase 7 --- structural changes, decided on the numbers

Decide only once phases 3, 4 and 6 have landed and been measured. The
candidates are fewer neutron contexts per agent (each was added in
2021 for least privilege, so this is a security trade-off neutron must
agree to), or sharing one daemon between agents. Record the decision
here, including a decision not to proceed.

## Phase 8 --- report back

Reply on openstack-discuss and at a TC meeting with the baseline and
the before/after telemetry. Be plain about what the data shows. If
swap traffic falls but job failure rates do not, say so.

## Phase 9 --- push audit

This repository has no `PUSH-AUDIT.md` yet. So this phase applies the
shared push-audit checklist from `PLAN-TEMPLATE.md` to the ranges
recorded in the Status table's `Merged` column. On top of that it runs
this repository's own checks: a `/code-review` of those ranges,
`_build/test-apply.sh` for every series directory the plan added, and
a read of every doc page the plan changed. Most of this plan lands in
other repositories, through Gerrit. For those changes the audit cites
upstream's own review and records `<repo> <sha>`, and does not
re-review them. Diff against `origin/develop` after a fetch.

## Open questions

- Is `performance.json` ingested anywhere queryable, such as the
  OpenSearch log index? If it is, Phase 2's baseline does not need a
  collection script. The comment in `get-stats.py` about Elasticsearch
  mapping explosion suggests it once was. Unverified.
- Should adding fields bump `performance.json`'s `version` (2 today)?
  The additions are backwards compatible; leave this to the devstack
  reviewers.
