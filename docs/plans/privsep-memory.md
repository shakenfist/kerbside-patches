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
| 5 | Credit LLMs with `Assisted-By` only, in one standard form, in the linter and every in-flight patch | Complete | b461cb072 |
| 6 | Services: stop importing what they do not use | In progress | |
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

| # | Cause | Per process, measured | Fix lives in |
|---|---|---|---|
| 1 | `futurist/_utils.py` imports eventlet at module level. Every service already on threads loads it anyway: neutron and glance. keystone and placement get it through oslo.messaging's import of oslo.service's eventlet backend. (nova-api and nova-api-meta were listed here first, but their entry points refuse eventlet; the probe missed that until step 6q) | No single change saves anything, so there is no figure for #1 alone. glance-api, where eventlet is the only change, falls 10 MB (92 to 82). The whole chain is in "Measured savings" below. | futurist; keystone and placement (see the survey) |
| 2 | `neutron_lib/__init__.py` imports `neutron_lib.db.api` (oslo.db, sqlalchemy, pecan, mako, pygments) in every agent. This is LP #1802369 again, the root cause behind patch219 | 4 to 6 MB on the OVN, DHCP, L3 and metadata agents, on top of neutron's three DB API patches. 0 in the OVS agent, neutron-api and neutron-rpc-server. Alone it saves nothing (6o). The estimate was +25 MB; sqlalchemy and oslo.db stay loaded through `neutron_lib.context` (see "Three more findings" below) | neutron-lib |
| 3 | `neutron/api/extensions.py` imports `openstack.connection` at module level, for the optional `ProjectIdMiddleware` alone. Agents load it through `neutron.common.utils` -> `neutron.api.api_common` | 8 to 13 MB in the agents (series 007 alone: OVN 8, L3 11, metadata 11, OVS 11, DHCP 13). 19 MB in neutron-api and neutron-rpc-server together with #5 | neutron |
| 4 | `neutron_lib/api/validators/__init__.py` imports `os_ken.lib.packet.ether_types`, which loads the whole os_ken packet library for one constants table. It hits every neutron process except the OVS agent | 7 to 10 MB (series 005 alone: API 7, RPC 7, OVN 8, L3 7, metadata 8, DHCP 10). 0 in the OVS agent, which uses os_ken itself | neutron-lib |
| 5 | `neutron/db/models/agent.py` imports `neutron.agent.common.utils` for `is_agent_down`, so every server process loads pyroute2 and `neutron.privileged` | Not separable from #3: series 007 saves 19 MB in each of neutron-api and neutron-rpc-server, and pyroute2 is gone from both. The agents still load pyroute2 for their own use | neutron |
| 6 | `keystone/api/_shared/saml.py` imports `keystone.federation.idp`, and with it pysaml2, unconditionally | 13 MB in keystone (95 to 82; series 008 alone) | keystone |
| 7 | `placement/deploy.py` imports alembic at module level, though it only uses it when `sync_on_startup` is set, which is off by default | 8 MB in placement (82 to 74; series 009 alone) | placement |
| 8 | nova-compute loads oslo.db and sqlalchemy through `nova.context`, but never uses a database | Not measured: out of scope for this phase. Estimated +17 MB, 17 MB per compute node | nova |

The first scan summed these estimates to roughly 0.5 GB per OVN node
and 0.85 GB per ML2/OVS controller. Those totals are withdrawn: the
whole chain saves less per process than the sum of its estimates (see
"Measured savings" below), and step 6j did not count processes per
node. Multiply a role's saving by its count from
`memory-tracker-peaks.py --group`, and remember that the figure counts
shared copy-on-write pages in both parent and worker. These pages are
as cold as privsep's, and the same caution about swap volume versus
swap thrash applies.

Checked and not counted, because the process does use them: networkx
in cinder (taskflow runs its flows), openstacksdk in nova (the placement
client), paramiko in nova (keypairs). Not import problems, but worth
noting for Phase 7: devstack sets `lazy-apps = true` for every uWSGI
service, so each worker imports the app separately and shares nothing
with the others.

### Phase 6 plan

Planned on 2026-10-23, on branch `privsep-memory-phase-6`. The branch is
stacked on `privsep-memory-plan` (PR #1953), because this plan file is
not on `develop` yet. Phase 5 landed on that branch too, and must merge
first (see below), so rebase this branch onto `develop` once #1953 has
merged.

**Planning effort: high.** Five upstream projects are involved, and
how reviewers receive the neutron-lib and futurist changes matters more
than their size. Review effort: high for steps 6c and 6e, medium for
the rest.

#### Scope

In scope: findings #1 to #7 in the table above, each as a change in
`_patches/` in a series of its own per repository. Also in scope: the
`get-stats.py` follow-up that records the service processes, the probe
support needed to measure patched trees, and the measurements.

Out of scope:

- **#8, nova-compute's database stack.** `nova.context` imports
  enginefacade for the cell database context managers that every nova
  service uses. Making that lazy is a nova design change, and it saves
  17 MB on one process per compute node. Recorded under Future work.
- **uWSGI `lazy-apps`.** This is a structural question for Phase 7, not
  an import.
- **Pushing to Gerrit.** Every step stops at a local commit. Mikal
  pushes each new series by hand, then sets `repush: true`, as was done
  for series 001 to 003.

#### What the survey found

Every claim in the findings table was rechecked against upstream
master on 2026-10-23, by reading the files from opendev.org. All seven
import sites are still there. Gerrit has no open change that addresses
any of them. Three things differ from what the findings and the
original order said, and the findings table above has been corrected
where they appear:

1. **keystone and placement need their own fix, not one in
   oslo.service.** `oslo_messaging/server.py` subclasses
   `service.ServiceBase`, and `oslo_service/service.py` resolves every
   component with `get_component()` at import time. So whichever
   process first imports oslo.messaging fixes the backend there and
   then, using `DEFAULT_BACKEND_TYPE`, which is eventlet. Making
   oslo.service lazy would not help. glance already solves this with
   `register_backend_default_hook(lambda: BackendType.THREADING)` in
   `glance/common/config.py`. keystone and placement can do the same.
   The threading backend still imports futurist, so the saving needs #1
   as well.
2. **The futurist change is a refactor, not a one-line guard.**
   `futurist/_utils.py` sets `EVENTLET_AVAILABLE` by importing eventlet,
   and `futurist/_green.py` imports five eventlet modules when
   `_green.py` itself is imported. `GreenThreadPoolExecutor` also binds
   `threading = _green.threading` as a class attribute, which runs at
   class-definition time. Codesearch finds no user of
   `futurist._green` or `futurist._utils` outside futurist, so the
   private surface can change freely. futurist deprecated its green
   executors in 939361 (2025-01), so the change is in line with where
   futurist is heading. Fixing it also removes eventlet from neutron's
   privsep daemons, which Phase 3 could not reach.
3. **#5 has seven call sites, not one.** `is_agent_down` is called
   from `neutron/db/agents_db.py`, `neutron/db/agentschedulers_db.py`,
   `neutron/db/l3_agentschedulers_db.py`, `neutron/db/models/agent.py`,
   `neutron/objects/agent.py` and `neutron/scheduler/dhcp_agent_scheduler.py`,
   all through `neutron.agent.common.utils`. Out of tree,
   neutron-dynamic-routing's `bgp_dragent_scheduler.py` uses the same
   path. `neutron/tests/unit/db/test_l3_hamode_db.py` mocks it with
   `mock.patch.object(agent_utils, 'is_agent_down', ...)`.
   keystone's #6 likewise has two module-level importers of
   `keystone.federation.idp`: `keystone/api/_shared/saml.py` and
   `keystone/api/auth.py`.

Two more things were found:

- **The plan's status cells broke the shared vocabulary, and the plan
  had no push-audit phase.** The planning commit before this one fixed
  both.
- **The `import-memory:latest` image was gone from the host** within
  hours of being built, so something prunes images. Every step that
  measures starts with `tools/import-memory/scan.sh build`.

#### Decisions

1. **One series per repository, with a new Gerrit topic.** The new
   series set `destination_branch: import-memory`, so they share that
   topic, not `privsep-memory`. A keystone reviewer would read a
   `privsep-memory` topic as misleading. The telemetry patch is the
   exception: it extends 1011787 and goes in series 001, in the
   `privsep-memory` topic. The series are:

   | Series | Patches | Findings |
   |---|---|---|
   | `devstack-privsep-memory-001` (existing) | patch221, appended after patch220 | telemetry |
   | `futurist-import-memory-004` | patch222 | #1 |
   | `neutron-lib-import-memory-005` | patch223 | #4 |
   | `neutron-lib-db-api-import-memory-006` | patch224 | #2 |
   | `neutron-import-memory-007` | patch225, patch226 | #3, #5 |
   | `keystone-import-memory-008` | patch227, patch228 | #6, eventlet |
   | `placement-import-memory-009` | patch229, patch230 | #7, eventlet |

   The numbers are provisional. Each step takes its number from
   `_build/get-next-patch-number.py` when it runs, because open pull
   requests may claim numbers first.

2. **#2 is its own series, pushed for review with the question asked
   on the change.** It changes when neutron-lib registers its ORM event
   listeners, which is the behaviour LP #1802369 deliberately added. As
   a separate series it can start that discussion without holding up
   #4, which needs no discussion. It also sits beside patch219's open
   question with neutron reviewers. It was first planned as work in
   progress, on an estimate of 25 MB per agent. On 2026-10-24 Mikal
   removed the WIP flag, because Gerrit's WIP means "not ready for
   review", so no reviewer would answer the question. Once it is
   uploaded he comments on the change to ask whether registering the
   listeners on first DB use is acceptable. 6j measured it at 4 to
   6 MB per agent, not 25.

3. **Lazy imports, not copied constants, for #4.** The `validate_ethertype`
   function can import `os_ken.lib.packet.ether_types` inside the
   `sg_filter_ethertypes` branch, its only user. Copying the constants
   would need a drift test, as patch219's copies do. A lazy import is
   smaller and needs no such test.

4. **Measure with Depends-On footers, as series 002 and 003 do.** The
   root patch of each new series (patch222, patch223, patch224,
   patch225, patch227 and patch229) carries a `Depends-On:` on
   patch221's Gerrit change. Each change's own devstack jobs then run
   with the service-process telemetry applied, and record its saving in
   `performance.json`. Gerrit follows patch221's parents, so this brings
   in 1011786, 1011787 and 1011794 too, and no patch repeats them.
   - The footers also stop every Phase 6 change merging before the
     telemetry does. Mikal chose that deliberately: the improvement
     cannot be shown without the telemetry, and holding the fixes behind
     it gives the devstack team a reason to review it.
   - "Before" figures come from patch221's own builds, for the jobs
     devstack shares with each project. For a job that only one project
     runs, push a `DNM` change to that project that carries only the
     footer, as Phase 2 does for neutron.
   - The probe still measures each patch in a container. Those figures
     are deterministic, and they are what the commit messages quote.
   - A DNM devstack change stacked on patch221, with a `Depends-On:` for
     every Phase 6 change, was considered and rejected. It would let the
     fixes merge before the telemetry, but take away the leverage.

5. **No week between Phase 6 landings.** Phase 3's rule exists to tell
   savings apart in CI totals. Here each change's own builds show its
   saving, and the container measures each one individually. Because of
   Decision 4, the changes may merge close together once patch221 does,
   and that is fine.

6. **keystone and placement register the backend hook in their
   package `__init__.py`.** The hook must be registered before anything
   imports oslo.messaging. In placement, that import comes through
   `placement.deploy` and then keystonemiddleware's audit middleware. A
   package `__init__` is the earliest point, and it is where neutron
   calls `init_backend`. Use glance's API, `register_backend_default_hook`:
   it sets a default, unlike `init_backend`, so an operator or a test
   that chooses a backend explicitly still wins.

7. **patch221 is pushed by hand, before the rest of the phase.**
   Decision 4 needs its Gerrit number for the footers, and
   `tools/check-depends-on.py` rejects a footer whose change does not
   exist. So after step 6b, Mikal pushes patch221 by hand, as a new
   change stacked on 1011794, and its number goes into this section.
   Series 001 already has `repush: true`, so `repush-openstack.sh`
   pushes it whenever Mikal runs it. Nothing pushes to Gerrit
   automatically. The new series start with `repush: false`, so the
   script skips them until Mikal decides to push them.
   - patch221 is
     [1011992](https://review.opendev.org/c/openstack/devstack/+/1011992),
     pushed by Mikal on 2026-10-23. Every new series root carries
     `Depends-On: https://review.opendev.org/c/openstack/devstack/+/1011992`.

8. **Release notes only where behaviour changes.** patch228 and
   patch230 change which oslo.service backend keystone and placement
   use, so each gets a reno note. The lazy imports change nothing a user
   can see, so they get no note, which is how those projects treat
   import-only changes.

#### Steps

Every step that adds a patch follows the same mechanics, which the
briefs do not repeat:

- Take the number from `_build/get-next-patch-number.py`.
- Create the series directory as a copy of
  `oslo.privsep-privsep-memory-002/config.yaml`, changing `repo`,
  `directory`, `release` (`master-<series>`), `destination_branch`
  (`import-memory`) and `repush` (`false`). Set `source_sha` and
  `previous_source_sha` to `git ls-remote <repo> master`.
- Write the patch as a `git format-patch` against that SHA, authored
  by Michael Still <mikal@stillhq.com>, with upstream's commit style
  and a `Change-Id`. Credit Claude with one `Assisted-By: <model>
  (<effort> effort)` line only, such as `Assisted-By: Claude Opus 5.5
  (high effort)`, with no email address or context window, and never
  `Co-Authored-By:` (Phase 5, Decision 7). Run `tools/gerrit-pre-push-lint
  --commit` in `src/<directory>` and fix every error it reports. Phase
  5's `llm-attribution` pre-commit hook rejects the patch otherwise. This
  branch is stacked on Phase 5, so the linter and the hook are there to
  enforce it (see the back brief).
- List the patch in the series' `ORDER`, then run
  `tools/recount-patch.py --in-place` and `tools/extract-commit-message`.
- Prove it with `_build/test-apply.sh <series>`, which runs that
  project's py3 and pep8 tox environments.
- Measure it with `tools/import-memory/scan.sh probe --with
  src/<directory>` (added in step 6a), and quote the before and after
  figures in the commit message.
- The root patch of each new series carries `Depends-On:
  https://review.opendev.org/c/openstack/devstack/+/<patch221>`
  (Decision 4), with the number recorded under Decision 7. Only the
  root has it; the other patches in a series do not repeat it.
- Every lazy-import patch adds a unit test that runs a fresh
  interpreter (`subprocess.run([sys.executable, '-c', ...])`), imports
  the module, and asserts that the dropped package is not in
  `sys.modules`. Mirror patch218's `PrivilegedImportsTestCase`.
- If the probe shows that the package still loads through another
  import chain, do not chase it. Report the chain
  (`report.py --chain`) and stop.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Extend `tools/import-memory/scan.sh` so it can measure patched source trees. `probe` gains repeatable `--with DIR` (a source tree such as `src/neutron`) and `--roles REGEX` (only roles whose name matches). For each `--with`, mount the directory read-only at `/with/<basename>`. Inside the container, before probing, copy each one to `/tmp/<basename>` (pbr writes `*.egg-info` into the tree, so a read-only mount cannot be installed directly) and run `pip install --no-deps --no-build-isolation -q /tmp/<basename>`. Keep each role in its own `python -I` interpreter. `marginal` gains the same `--with`. Keep the script shellcheck-clean and the usage text at the top accurate. Document both options in `docs/import-memory-scans.md`: add a section "Measuring a patch" after step 4 of "How to run a scan", showing `_build/test-apply.sh --skip-tests <series>` and then `scan.sh probe --with src/<directory> --roles '<regex>'`, and say the figures for a commit message come from comparing that against a run without `--with`. Verify: `scan.sh build`, then `scan.sh probe --roles placement` with and without `--with` on an unmodified `src/placement` clone, giving the same numbers to within 1 MB. Commit subject: "Let the import probe measure patched trees." |
| 6b | high | opus | none | Add patch221 to series `devstack-privsep-memory-001`, after patch220 in `ORDER`, against that series' `source_sha`, on top of patch215, patch216 and patch220. Run `_build/test-apply.sh devstack-privsep-memory-001` so it applies on top of them. In `tools/get-stats.py`, `process_defaults` is `['privsep', 'mysqld', 'erlang', 'etcd']`, and each entry is a regex matched against the joined cmdline. Add entries that match the OpenStack service processes and nothing else: uWSGI workers (cmdlines such as `nova-apiuWSGI worker 1`), oslo.service parents and workers (`nova-conductor: ServiceWrapper worker(0)`, `neutron-server: rpc worker (...)`, `neutron-dhcp-agent: ServiceWrapper worker(0)`), and plain service processes such as `/opt/stack/data/venv/bin/python3.12 /opt/stack/data/venv/bin/nova-compute ...`. They must not match tempest, stestr, the `*-manage` tools or `oslo-config-generator`. Prove the regexes offline with a small script run against the cmdlines in the two memory_tracker logs, `controller/logs/screen-memory_tracker.txt` of Zuul builds 8e3bfd425736459e81a0d3972348a651 and fb330af810cc4841943f24d573a95ae9 (fetch them with `zuul-result <uuid> log <path>`). `tools/import-memory/memory-tracker-peaks.py` already parses them. Report what matches and what does not. The commit message, in devstack's style, says why: today `performance.json` cannot show a service's memory, and its `services` figures are cgroup totals that include page cache. Do not touch the `privsep` entry. Commit subject: "Record service process memory in get-stats.py." |
| 6c | xhigh | opus | worktree | patch222 for futurist (series `futurist-import-memory-004`): import eventlet only when a green executor or green helper is first used. Read `futurist/_utils.py`, `futurist/_green.py`, `futurist/_futures.py` (`GreenThreadPoolExecutor`, the `green` argument check, `GreenFuture`, `GreenDelayedExecutorMixin`) and `futurist/periodics.py` first. Keep `EVENTLET_AVAILABLE` as a bool, computed with `importlib.util.find_spec('eventlet') is not None`, which imports nothing. Turn `_green`'s module-level eventlet imports and aliases (`Pool`, `Queue`, `is_monkey_patched`, `spawn`, `threading`) into lazily resolved ones; a module `__getattr__` is one way. `is_monkey_patched` must return False without importing eventlet when `'eventlet' not in sys.modules`, since nothing can be patched before eventlet is imported. Replace the `GreenThreadPoolExecutor.threading = _green.threading` class attribute with something resolved on first use. Behaviour must not change when eventlet is installed: the existing green tests must pass unmodified. Add a test that runs a fresh interpreter, imports `futurist` and `futurist.periodics`, and asserts that `eventlet` is not in `sys.modules`, plus one that constructs a `GreenThreadPoolExecutor` in a fresh interpreter and checks that it works. Run futurist's full `tox -e py3` and `pep8`, not only `test-apply`. The saving is about 11 MB per threaded process; measure it with `scan.sh probe --with src/futurist --roles 'neutron-api\|nova-api$\|glance'`. Commit subject: "Do not import eventlet until it is needed." |
| 6d | medium | sonnet | none | patch223 for neutron-lib (series `neutron-lib-import-memory-005`): in `neutron_lib/api/validators/__init__.py`, remove the module-level `from os_ken.lib.packet import ether_types` and import it inside `validate_ethertype`, within the `if cfg.CONF.sg_filter_ethertypes:` branch that is its only use. Add the fresh-interpreter test that `neutron_lib.api.validators` does not load `os_ken`, placed beside the existing validator tests in `neutron_lib/tests/unit/api/validators/`. The saving is about 8.5 MB per neutron process except the OVS agent; measure it with `--roles 'neutron-(api\|ovn-agent\|l3-agent)'`. Commit subject: "Import os_ken only when validating an ethertype." |
| 6e | high | opus | worktree | patch224 for neutron-lib (series `neutron-lib-db-api-import-memory-006`, with `wip: true` in its `config.yaml`): stop `neutron_lib/__init__.py` importing `neutron_lib.db.api` unconditionally. First read LP #1802369 and the change that added the import (`git log -S 'from neutron_lib.db import api' -- neutron_lib/__init__.py` in `src/neutron-lib`). Find what it protects: which ORM event listeners `neutron_lib.db.api` registers, and which import paths have to see them. The likely shape moves the import into `neutron_lib/db/__init__.py`, so that anything using `neutron_lib.db.*` (model_base, model_query and so on) still registers the listeners, while `neutron_lib.constants`, `neutron_lib.api.*` and the agent-side modules do not pull in sqlalchemy, oslo.db or pecan. Confirm that every neutron DB path imports `neutron_lib.db` before it uses the ORM. If you find one that does not, say so and stop: that is the case the bug was about. The commit message must explain the trade-off and cite the bug. Measure with `--with src/neutron-lib --roles 'neutron-(ovn-agent\|l3-agent\|dhcp-agent)'`; expect about 25 MB per agent. Commit subject: "Load the DB API only for DB users." |
| 6f | medium | sonnet | none | patch225 for neutron (new series `neutron-import-memory-007`): in `neutron/api/extensions.py`, remove the module-level `from openstack import connection` and import it inside `ProjectIdMiddleware.__call__`, where `_NOVA_CONNECTION` is first built, which is its only use. Add a fresh-interpreter test that `neutron.common.utils` does not load `openstack`, beside the existing tests for `neutron/api/extensions.py`. Measure with `--roles 'neutron-(ovn-agent\|l3-agent\|rpc-server)'`; expect about 13 MB per process. Commit subject: "Import openstacksdk only for ProjectIdMiddleware." |
| 6g | high | opus | none | patch226 for neutron, the same series after patch225: move `is_agent_down` out of `neutron/agent/common/utils.py`, which imports `neutron.agent.linux.utils` and through it `neutron.privileged` and pyroute2. Its new home is a new module, `neutron/common/agent_status.py`, which imports only `oslo_config`, `oslo_utils.timeutils` and `neutron.conf.agent.database.agents_db`, and calls `agents_db.register_db_agents_opts()` itself, because `agent_down_time` is registered there today. `neutron.agent.common.utils` keeps `is_agent_down = agent_status.is_agent_down` for out-of-tree users such as neutron-dynamic-routing. Change the in-tree callers to import the new module: `neutron/db/agents_db.py`, `neutron/db/agentschedulers_db.py`, `neutron/db/l3_agentschedulers_db.py`, `neutron/db/models/agent.py`, `neutron/objects/agent.py` and `neutron/scheduler/dhcp_agent_scheduler.py`. Then fix the tests that patch the old path, starting with the `mock.patch.object(agent_utils, 'is_agent_down', ...)` calls in `neutron/tests/unit/db/test_l3_hamode_db.py`; grep `neutron/tests` for `is_agent_down` to find the rest. Add a fresh-interpreter test that `neutron.db.models.agent` does not load `pyroute2` or `neutron.privileged`. Measure with `--roles 'neutron-(api\|rpc-server)'`; expect about 10 MB per server process. If pyroute2 still loads through another chain, report it and stop. Commit subject: "Keep is_agent_down out of the agent utilities." |
| 6h | medium | sonnet | none | Series `keystone-import-memory-008`, two patches. patch227: `keystone/api/_shared/saml.py` and `keystone/api/auth.py` both import `keystone.federation.idp` at module level, which loads pysaml2 (about 15 MB). Import it inside the functions that use it: `keystone_idp.SAMLGenerator()` in `_shared/saml.py`, and `keystone_idp.ECPGenerator()` in `auth.py`. Leave `keystone/cmd/cli.py` alone, because it is not the server. Add a fresh-interpreter test that `keystone.server.flask.application` does not load `saml2`. patch228: in `keystone/__init__.py`, register `oslo_service.backend.register_backend_default_hook(lambda: BackendType.THREADING)`, with a comment like glance's in `glance/common/config.py`, and a reno note under `releasenotes/notes/`. The eventlet saving needs patch222 as well, so measure patch228 with `--with src/keystone --with src/futurist`, and say so in the commit message. Commit subjects: "Import the SAML IdP only when it is used." and "Prefer the threading oslo.service backend." |
| 6i | medium | sonnet | none | Series `placement-import-memory-009`, two patches. patch229: `placement/deploy.py` imports `placement.db.sqlalchemy.migration` at module level, which loads alembic (about 11 MB), but `update_database()` calls `migration.upgrade('head')` only when `conf.placement_database.sync_on_startup` is set, which is off by default. Import it inside that branch, and check with `grep -rn "deploy.migration" placement/tests` for tests that mock `placement.deploy.migration`. Add a fresh-interpreter test that `placement.deploy` does not load `alembic`. patch230: as patch228, in `placement/__init__.py`, which is empty today, with a reno note, measured with `--with src/futurist` too. Commit subjects: "Import migrations only when syncing on startup." and "Prefer the threading oslo.service backend." |
| 6j | medium | sonnet | none | Measure the whole phase. Run `_build/test-apply.sh --skip-tests` for every Phase 6 series. Then run `scan.sh probe` with no `--with` and save the output as `before.jsonl`. Run it again with `--with` for every tree the chain fixes touch, and save that as `after.jsonl`: futurist (004), neutron-lib (005 and 006 in one tree), neutron (007 and 013 in one tree), keystone (008), placement (009), oslo.utils (010), oslo.messaging (011), oslo.service (012), and python-ovs (the `ovs` sdist at the image's version with `upstream/openvswitch/series/v1` applied). Two series that share a directory are combined by applying the second's patches over the first's tree in a scratch copy. Run `report.py` on both with `--packages eventlet,os_ken,openstack,pyroute2,saml2,alembic,sqlalchemy,pecan`. In Phase 6's findings table in `docs/plans/privsep-memory.md`, replace each estimate with the measured figure, and add a per-role before and after table of anonymous RSS. If #2 (patch224) is excluded because it is WIP, give the figures with and without it. Commit subject: "Record Phase 6's measured savings." |

#### Chain fixes for eventlet and `neutron_lib.db.api`

Added on 2026-10-24, after steps 6c and 6e. The findings table sized #1
and #2 with `marginal.py`, which measures what a package costs on top of
a base set. That is the cost of the whole package, not what removing one
import site saves. Both packages load through several independent
chains, so patch222 (futurist) and the neutron-lib change each save
nothing alone. Mikal chose to add the remaining chain fixes to this
phase, rather than defer them.

What tracing found so far, with each fix applied in a scratch copy in
turn:

| Package | Roles | Import sites, in the order they appear |
|---|---|---|
| eventlet | neutron-api, neutron-rpc-server, glance-api | futurist (patch222); `oslo_utils.eventletutils`, a deprecated module imported by at least four oslo.messaging modules; `oslo_messaging/_utils.py`'s own `try_import('eventlet')`; `oslo_service.wsgi`, imported by `neutron.common.config` |
| eventlet | keystone, placement, nova-api | futurist; oslo.service's default backend when no hook chooses threading (6h and 6i add the hooks; nova-api is out of scope); then presumably the oslo.messaging chain |
| eventlet | neutron agents | futurist; python-ovs `ovs/poller.py`, which is Open vSwitch, not OpenStack |
| `neutron_lib.db.api` | neutron agents | `neutron_lib/__init__.py`; `neutron_lib.context` and `neutron_lib.db.utils` (the option (a) design, written and tested in neutron-lib; diff kept as `step-6e/patch224-src.diff` in the session scratchpad); then neutron's `neutron.common.utils`, `neutron.service` and `neutron.common.ovn.utils`; then agents importing DB models (`neutron.db.models_v2`, and `neutron_lib.db.standard_attr` through `neutron.extensions.revisions`) |

The table is incomplete: tracing stopped at each layer. So the chain
fixes start with a survey, and the patch steps it produces are planned
from its results, not here.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6k | high | opus | none | Map every import chain that loads eventlet in each role in `tools/import-memory/roles.txt`, and every chain that loads `neutron_lib.db.api` in each neutron agent role. Work only in scratch copies of the source trees (clone each project with `git clone --depth 1` into the scratch directory, starting from patch222's futurist and the option (a) neutron-lib diff). Repeat: probe with every scratch tree as `--with`; trace the first importer of the package with a `sys.meta_path` hook that ignores `importlib.util.find_spec` lookups (Python 3.12 shows those frames as `<frozen importlib.util>`); make the smallest scratch edit that removes that site; until the role no longer loads the package or the next site is outside OpenStack. Scratch edits may be crude, but they must keep each module importable; note any that would not be a valid fix. Output, for the plan: one row per import site (project, file, what it uses the package for, the proposed real fix and whether it changes behaviour), and a per-role table of anonymous RSS for the full set against `import-memory:phase6` with no `--with`. Also say which sites are shared by many roles, so the patch order puts the widest saving first. Commit subject: "Map the eventlet and DB API import chains." |

6k ran on 2026-10-24. Every eventlet chain ends at an OpenStack fix or
at python-ovs. With every fix applied in scratch copies, every neutron
role, glance-api, keystone, placement and the privsep daemon run without
eventlet, and the OVN, DHCP, L3 and metadata agents without
`neutron_lib.db.api`. "Every fix" includes the two eventlet rows below
that Phase 6 does not patch: oslo.privsep's patch217 and ovsdbapp
3.0.0. Without them every neutron role still loads eventlet; see
"Measured savings" below. No row saves anything alone, except neutron's
three DB API patches: 6o found that they save 4 to 12 MB together
without patch224 (see below).

| Package | Project | Site | Uses it for | Roles | Fix | Behaviour change |
|---|---|---|---|---|---|---|
| eventlet | futurist | `_utils.py` | `EVENTLET_AVAILABLE`, green executors | neutron (7 roles), glance-api, keystone, placement | patch222 | none |
| eventlet | oslo.utils | `eventletutils.py` | module-level `try_import`; seven oslo.messaging modules import it | the same 10 roles | look eventlet up in `sys.modules` when called; `EVENTLET_AVAILABLE` from `find_spec`, catching `ImportError` | none in the public API |
| eventlet | oslo.messaging | `_utils.py` | its own `try_import`, to find unpatched threading and queue | the same 10 roles | look it up in `sys.modules` | none |
| eventlet | keystone, placement | package `__init__.py` | oslo.service's default backend | keystone, placement | the backend hook (patch228, patch230) | threading backend; reno note |
| eventlet | oslo.service | `wsgi.py` | eventlet, `eventlet.wsgi` and greenlet, used only by the deprecated `Server`; neutron imports the module for `Loader`, `Request` and `register_opts` | neutron (7 roles) | import inside `Server`'s methods | `protocol` defaults to `None` and resolves to `HttpProtocol` |
| eventlet | oslo.privsep | `daemon.py` | monkey-patch check | the privsep daemon; neutron through `neutron.conf.agent.common` | patch217, in review as 1011795 | none |
| eventlet | ovsdbapp | `backend/ovs_idl/vlog.py` | patcher check for Windows logging | neutron-api, neutron-rpc-server, the OVN, OVS and DHCP agents | already gone in ovsdbapp 3.0.0; upper-constraints pins 2.19.0 until requirements 1011342 merges | none |
| eventlet | python-ovs (Open vSwitch) | `ovs/poller.py` | module-level patcher import for green `select` | the same 5 roles | look it up when called, sent to ovs-dev | none |
| db.api | neutron-lib | `__init__.py`, `context.py`, `db/utils.py` | ORM listeners | every agent | option (a), patch224 | when the listeners are registered |
| db.api | neutron | `common/utils.py` | `MAX_RETRIES` and `is_session_active` in two decorators | 5 agents | import inside the two functions | none |
| db.api | neutron | `service.py` | `dispose_pool()` before forking | DHCP, L3, OVN | import inside | none |
| db.api | neutron | `api/api_common.py` | `model_base.BASEV2` in filter validation | agents through `neutron.common.utils` | import inside | none |
| db.api | neutron | `common/ovn/utils.py` | `models_v2` and `ports_obj` for one `isinstance`; also pecan | OVN, DHCP | import inside | none |
| db.api | neutron | `agent/rpc.py` | `resource_cache` and `callbacks.resources`, which loads every OVO and model; used only by L2 agents | DHCP, L3, metadata | import when the cache is made | none |
| db.api | neutron | `ovsdb_monitor.py` | server-side hash ring and router handlers; the OVN agent imports the module for its IDL classes | OVN | import inside | none |
| db.api | neutron | `extensions/stdattrs_common.py` | `standard_attr`, inside one function; agents reach it through `agent/l3/ha_router.py` | DHCP, L3 | import inside | none |

Anonymous RSS in MB, against `import-memory:phase6`. "Control" is
unedited master trees, so library drift is not counted as saving.
Expect 2 MB of noise.

| Role | Control | All OpenStack fixes | Plus python-ovs | Saving |
|---|---:|---:|---:|---:|
| privsep daemon (idle) | 33 | 19 | 19 | 14 |
| neutron-api | 127 | 127 | 114 | 13 |
| neutron-rpc-server | 126 | 124 | 115 | 11 |
| neutron-ovn-agent | 116 | 104 | 93 | 23 |
| neutron-openvswitch-agent | 127 | 124 | 113 | 14 |
| neutron-dhcp-agent | 119 | 103 | 92 | 27 |
| neutron-l3-agent | 117 | 90 | 92 | 25 |
| neutron-metadata-agent | 115 | 87 | 89 | 26 |
| glance-api | 92 | 82 | 82 | 10 |
| keystone | 93 | 87 | 87 | 6 |
| placement | 84 | 73 | 73 | 11 |

Three more findings:

- **nova-api and nova-api-meta were a probe artefact.** Their entry
  points call `monkey_patch.patch(backend='threading')` first, which
  refuses eventlet, and the probe skipped that call. Finding #1's nova
  figures come out; step 6q fixed the probe, after which the two roles
  measure about 106 and 93 MB, 8 MB less than before, with no eventlet.
- **Any `find_spec('eventlet')` must catch `ImportError`**, because
  nova's hook raises it from `find_spec`. patch222 does; the oslo.utils
  fix must too.
- **sqlalchemy and oslo.db still load in every neutron agent** after
  the db.api chain is cut, through `neutron_lib.context`'s
  `enginefacade.transaction_context_provider` decorator: 19.3 MB more.
  That is Future work. The OVS agent cannot drop `db.api` without a
  design change, because it registers the OVO classes at runtime.

Back briefed on 2026-10-24: Mikal agreed to one series per project,
neutron-lib staying WIP, the neutron DB sites as three patches grouped
by the agents they free, and nothing pushed until the whole chain is
written and measured. python-ovs goes in `upstream/openvswitch/`, the
non-OpenStack series tree, for Mikal to send to ovs-dev. The probe is
fixed for nova.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6e | high | opus | own worktree | Finish patch224 (option (a)) from the diff 6e left, in series `neutron-lib-db-api-import-memory-006`, `wip: true`. The commit message says the agents gain nothing until the neutron patches in 6o land, and names them. |
| 6h | medium | sonnet | own worktree | As the row above, patch227 and patch228. patch228's commit message names the rest of its chain (futurist, oslo.utils, oslo.messaging). |
| 6i | medium | sonnet | own worktree | As the row above, patch229 and patch230, likewise. |
| 6l | high | opus | own worktree | patch231, series `oslo.utils-import-memory-010`: `oslo_utils/eventletutils.py` stops importing eventlet at module level, as the table says. `EVENTLET_AVAILABLE` from `importlib.util.find_spec`, guarded for `ImportError`; every function that uses eventlet looks it up in `sys.modules` (nothing can be patched before eventlet is imported) or imports it on first use. The module is deprecated; keep its behaviour exactly. Commit subject: "Do not import eventlet in eventletutils until used." |
| 6m | medium | sonnet | own worktree | patch232, series `oslo.messaging-import-memory-011`: `oslo_messaging/_utils.py` looks eventlet up in `sys.modules` instead of `try_import`. Commit subject: "Find patched threading without importing eventlet." |
| 6n | high | opus | own worktree | patch233, series `oslo.service-import-memory-012`: `oslo_service/wsgi.py` imports eventlet, `eventlet.wsgi` and greenlet only inside `Server`. The `protocol` default changes to `None`, resolved to `eventlet.wsgi.HttpProtocol` when the server is built; keep every behaviour of `Server` and fix the tests that mock `wsgi.eventlet`. A reno note, since an argument default changes. Commit subject: "Import eventlet only for the WSGI server." |
| 6o | high | opus | own worktree | Series `neutron-db-api-import-memory-013`, three patches. patch234: `neutron/common/utils.py`, `neutron/service.py` and `neutron/api/api_common.py` import the DB API inside the functions that use it. patch235: `neutron/common/ovn/utils.py` (`models_v2`, `ports_obj`, pecan) and `ovsdb_monitor.py`'s server-side imports. patch236: `neutron/agent/rpc.py`'s resource cache and `extensions/stdattrs_common.py`. Each has a fresh-interpreter test for the agents it frees, which can only pass with patch224's neutron-lib, so mark that test to skip when `neutron_lib.db.api` is loaded by neutron-lib itself and say so. |
| 6p | medium | opus | own worktree | python-ovs: `ovs/poller.py` imports eventlet's patcher only when it is needed. Add `upstream/openvswitch/` in the layout `upstream/README.md` describes (`config.yaml` with `repo: https://github.com/openvswitch/ovs`, `branch: main`, `base_sha`, `current_series: v1`, `disposition: submit`; `series/v1/0001-*.patch` from `git format-patch`; a README), following Open vSwitch's `Documentation/internals/contributing/submitting-patches.rst`, including whatever it says about tool-assisted patches. Test with python-ovs's own tests. Confirm `find . -maxdepth 2 -name config.yaml` is unchanged. |
| 6q | low | sonnet | own worktree | Let `roles.txt` call an entry function with literal arguments, so nova-api and nova-api-meta run `nova.monkey_patch.patch(backend='threading')` first, as their entry points do. Correct finding #1's nova figures in this plan, and document the case in `docs/import-memory-scans.md`. |

6o ran on 2026-10-24 and corrected this section: the three neutron
patches save memory without patch224, because the agents stop loading
neutron's own objects and models. Anonymous RSS in MB, with 6e's
neutron-lib as "patch224":

| Role | no `--with` | patch224 | patch224, 234 | + 235 | + 236 | 234 to 236 alone |
|---|---|---|---|---|---|---|
| neutron-ovn-agent | 119 | 121 | 119 | 109 | 109 | 115 |
| neutron-openvswitch-agent | 129 | 127 | 129 | 129 | 124 | 124 |
| neutron-dhcp-agent | 119 | 119 | 119 | 119 | 103 | 109 |
| neutron-l3-agent | 119 | 117 | 117 | 117 | 102 | 107 |
| neutron-metadata-agent | 115 | 115 | 115 | 115 | 99 | 105 |

The OVS agent's 5 MB is not a real saving: moving `neutron.agent.rpc`'s
resource cache import also moves the `register_objects()` it ran at
import, and the OVS agent runs that anyway when it builds its cache.
That move is also patch236's one risk: out-of-tree code that relied on
importing `neutron.agent.rpc` to register the objects must call
`objects.register_objects()` itself. Nobody has searched codesearch for
such code yet. The management session rebased the commit messages'
"before" figures onto the no-`--with` column.

6j then measures the whole set, with every tree as `--with`, and records
it here. After 6j, Mikal pushes the chain together.

The management session planned one patch step per project from
its table, in the form of the rows above, with a back brief to Mikal
before any of them starts. Each patch goes in a series of its own in
the `import-memory` topic, and the commit messages say that the saving
needs the rest of the chain, naming the other changes. patch222 and
the neutron-lib db.api change are held until then, so they are pushed
with the rest of their chain. python-ovs is Open vSwitch, reviewed on
the ovs-dev mailing list, so a fix there is prepared for Mikal to send
rather than added to `_patches/`.

6h and 6i were split while 6k ran; with the chain planned, both go
ahead whole.

Between step 6b and step 6c there is a gate, which is the management
session's job, not a sub-agent's. Mikal pushes patch221 by hand
(Decision 7), and the session records its change number under
Decision 7. Steps 6c to 6i need that number for their footers, so none
of them starts before it is recorded. After step 6j, Mikal pushes each
new series by hand. Where a project's only devstack job is its own, the
session prepares the DNM control change that Decision 4 describes, for
Mikal to push.

#### Risks and mitigations

- **Lazy imports hide import errors until first use.** A typo in a
  moved import fails only on the code path that uses it. Mitigation:
  every patch runs its project's unit tests through `test-apply`, and
  the management session checks that a test covers each moved import's
  call site. Where none does, the step adds one.
- **futurist behaviour changes under eventlet.** Mitigation: step 6c
  runs futurist's full test suite in a worktree, and the management
  session reviews the diff against the four places `_green` is used.
  futurist's gate also runs `lib-forward-testing-python3`, which is a
  devstack tempest job with futurist installed from source.
- **neutron-lib reviewers reject #2.** It is in a series of
  its own, so the rest of the phase does not depend on it. If it is
  rejected, record that in Future work, along with the per-agent cost
  that remains.
- **An is_agent_down mock path is missed in an out-of-tree project.**
  The re-export keeps `neutron.agent.common.utils.is_agent_down`
  working, but a mock of the old path no longer affects the in-tree
  callers. Mitigation: codesearch found no out-of-tree mocks, and the
  commit message says what changed.
- **The probe misleads.** It imports modules; it does not run
  services. Mitigation: each change's own devstack jobs give the CI
  figure (Decision 4), and Phase 8 reports CI figures, not probe
  figures.
- **devstack is slow to review patch221.** Every Phase 6 change
  waits on it, by design. Mitigation: raise it with the devstack team
  when the Phase 6 changes are pushed, pointing at the fixes waiting
  on it. If it stalls for a month, take the footers out of the
  uncontroversial changes rather than leave them stuck.

#### Measured savings

Step 6j ran on 2026-10-24 against `import-memory:phase6`. "Before" is
the image with no `--with`. "After" mounts every Phase 6 tree:
futurist (004), neutron-lib (005 and patch224), neutron (007 and 013),
keystone (008), placement (009), oslo.utils (010), oslo.messaging (011),
oslo.service (012) and python-ovs 4.0.0 (the image's version) with
`upstream/openvswitch/series/v1` applied. "After without patch224" is
the same with neutron-lib at 005 only. "Full chain" adds the two
eventlet sites in the 6k table that Phase 6 does not patch: oslo.privsep
with patch217 (series 002, in review as 1011795) and ovsdbapp 3.0.0
from PyPI, which upper-constraints pins at 2.19.0 until requirements
1011342 merges. The management session ran that column. neutron's
series 003 from Phase 4 is left out of every column. Anonymous RSS in
MB. Treat a difference of 2 MB or less as noise: the unchanged nova and
cinder roles move by that much.

| Role | Before | After | After without patch224 | Saving | Full chain | Full saving | Still loaded after |
|---|---:|---:|---:|---:|---:|---:|---|
| privsep-idle | 33 | 33 | 33 | 0 | 19 | 14 | none |
| nova-compute | 120 | 118 | 120 | 2 | 118 | 2 | openstack, pyroute2, sqlalchemy |
| nova-conductor | 91 | 91 | 91 | 0 | 91 | 0 | openstack, sqlalchemy |
| nova-scheduler | 90 | 92 | 90 | -2 | 90 | 0 | openstack, sqlalchemy |
| nova-novncproxy | 97 | 97 | 97 | 0 | 99 | -2 | openstack, sqlalchemy |
| nova-api | 106 | 106 | 108 | 0 | 108 | -2 | openstack, sqlalchemy |
| nova-api-meta | 93 | 95 | 93 | -2 | 95 | -2 | openstack, sqlalchemy |
| neutron-api | 127 | 98 | 97 | 29 | 88 | 39 | sqlalchemy, pecan |
| neutron-rpc-server | 126 | 97 | 97 | 29 | 85 | 41 | sqlalchemy, pecan |
| neutron-ovn-agent | 116 | 89 | 95 | 27 | 76 | 40 | pyroute2, sqlalchemy |
| neutron-openvswitch-agent | 127 | 113 | 112 | 14 | 103 | 24 | os_ken, pyroute2, sqlalchemy, pecan |
| neutron-dhcp-agent | 121 | 83 | 89 | 38 | 72 | 49 | pyroute2, sqlalchemy |
| neutron-l3-agent | 117 | 83 | 87 | 34 | 72 | 45 | pyroute2, sqlalchemy |
| neutron-metadata-agent | 115 | 81 | 85 | 34 | 70 | 45 | pyroute2, sqlalchemy |
| cinder-volume | 95 | 94 | 94 | 1 | 96 | -1 | eventlet, sqlalchemy |
| cinder-scheduler | 93 | 93 | 93 | 0 | 93 | 0 | eventlet, sqlalchemy |
| cinder-backup | 81 | 83 | 81 | -2 | 83 | -2 | eventlet, sqlalchemy |
| cinder-api | 97 | 95 | 95 | 2 | 95 | 2 | eventlet, sqlalchemy |
| glance-api | 92 | 82 | 82 | 10 | 82 | 10 | openstack, sqlalchemy |
| keystone | 95 | 71 | 71 | 24 | 71 | 24 | sqlalchemy |
| placement | 82 | 62 | 62 | 20 | 62 | 20 | sqlalchemy |

The nova and cinder rows are noise: no Phase 6 change touches them.
Findings #2 to #7 were also measured one series at a time; those
figures are in the findings table.

Of the packages the plan names, after the chain:

- **Gone as the Definition of done asks:** os_ken in every neutron role
  but the OVS agent; openstack in every neutron role (more than the
  agents, which was all that was asked); pyroute2 in neutron-api and
  neutron-rpc-server; saml2 in keystone; alembic in placement.
- **eventlet is gone with the full chain** from every neutron role,
  glance-api, keystone, placement and the privsep daemon, which drops
  from 33 to 19 MB. The "Still loaded after" column is for the full
  chain.
- **Without patch217 and ovsdbapp 3.0.0, eventlet is still loaded, in
  30 modules, by every neutron role.** neutron-api and
  neutron-rpc-server load it through `ovsdbapp.backend.ovs_idl.vlog`
  (ovsdbapp 2.19.0, pinned by upper-constraints until requirements
  1011342 merges). The five agents load it through
  `oslo_privsep.daemon`, via `neutron.conf.agent.common`; that is
  patch217, in series 002. glance-api, keystone and placement load none.
  The cinder roles load it from `cinder/__init__.py`, as expected.
  Removing it saves another 10 to 13 MB in each neutron role, so
  Phase 6's neutron savings depend on patch217 merging and on the
  upper-constraints bump.
- **`neutron_lib.db.api` is gone from the OVN, DHCP, L3 and metadata
  agents,** and still loaded in neutron-api, neutron-rpc-server and the
  OVS agent, as 6k expected. Without patch224 every neutron role still
  loads it. Checked in a fresh interpreter per role, not from the
  package counts.
- **pecan** is gone from the OVN, DHCP, L3 and metadata agents with
  patch224, and loaded in all seven neutron roles without it. With
  patch224 it is still loaded in neutron-api, neutron-rpc-server and
  the OVS agent.
- **sqlalchemy is loaded in every role.** In the neutron agents it
  comes through `neutron_lib.context`; see "Three more findings".

#### Definition of done

- 6k's chain table is in this section, and every row has either a
  patch step or a recorded reason for none.

- `pre-commit run --all-files` passes, and `_build/test-apply.sh
  <series>` passes for series 001 and 004 to 013.
- In the full-chain probe from step 6j, `report.py --packages
  eventlet,os_ken,openstack,pyroute2,saml2,alembic` shows `.` for:
  - eventlet, in the neutron server roles and agents, glance-api,
    keystone and placement (nova-api and nova-api-meta never load it,
    and the probe shows `.` for them since step 6q);
  - os_ken, in every neutron role except the OVS agent;
  - openstack, in every neutron agent;
  - pyroute2, in neutron-api and neutron-rpc-server;
  - saml2, in keystone;
  - alembic, in placement.

  Any cell that still shows a number is listed in the plan with the
  chain that loads it.
- Every new patch has a fresh-interpreter test that fails without the
  patch. The management session checks this for at least two patches
  by reverting the source change and running the test.
- Phase 6's findings table carries measured figures, not estimates.
- patch221's change number is recorded under Decision 7, and every
  new series root carries a footer naming it. `tools/check-depends-on.py`
  run online over those series passes.
- Phase 6's status is `In progress` until every change in it has
  merged upstream or been abandoned.

#### Back brief

Before step 6a, back brief Mikal on the plan and get his agreement to
Decisions 2, 4 and 7. Those are the ones that commit him to something
outward-facing: a WIP neutron-lib discussion, Phase 6 changes held
behind devstack's review of the telemetry, and a hand push of patch221
before the rest of the work. Before step 6e, back
brief again with what LP #1802369 actually protects, because the shape
of patch224 depends on it.

Back briefed on 2026-10-23. Mikal agreed to Decisions 2, 4 and 7.
Rodolfo Alonso's comment on 1011796, that it "does not depend on this
devstack patch", does not change Decision 4: the footers stay, and the
comment gets no reply. Mikal also lifted the rule that no step starts
before Phase 5 merges. That rule was there so the attribution linter
and hook would be in place, and this branch is stacked on #1953, which
has them, so steps start now. Pushing anything to Gerrit is still
Mikal's own act, and patch221's hand push still waits for his go.

#### Execution notes

- 2026-10-23 (AEDT). 6a and 6b ran in order, since both write to the
  worktree. 6a was committed with one review change: a failed install
  of a `--with` tree now stops the run, because the probe's stderr was
  discarded and the run would otherwise have reported unpatched figures
  as patched ones. 6b's regexes were proved against both memory_tracker
  logs. devstack has no unit test environment, so its test-apply runs no
  Python tests. rootwrap-daemon is deliberately not tracked.
- Decision 7 said the daily rebase would push series 001. Nothing
  pushes to Gerrit automatically; Mikal runs `repush-openstack.sh`
  himself. Corrected at the source.
- The gate after 6b: Mikal pushed patch221 as 1011992, recorded under
  Decision 7.
- 2026-10-24. 6c, 6d and 6e ran in parallel, 6c and 6e in worktrees of
  their own made from this branch (`kerbside-patches-wt-p6-6c` and
  `-6e`), since the Agent tool's isolation would have branched from the
  session's other worktree. Patch numbers were assigned in advance so
  they could not collide. Every measurement uses the image tagged
  `import-memory:phase6`, kept from pruning by a stopped container,
  `import-memory-phase6-pin`, to be removed when the phase is done.
- 6d (patch223) measured 8 MB in neutron-api, the OVN agent and the L3
  agent. Its release note was removed in review (Decision 8).
- 6c (patch222) and 6e saved nothing alone; see "Chain fixes" above.
  The 6e back brief chose option (a) (register the listeners from
  `model_base`, lazy `db_api` in `context.py` and `db/utils.py`), and
  Mikal then chose to add the chain fixes to this phase.
- 2026-10-24. After 6j, the step worktrees and the
  `import-memory-phase6-pin` container were removed; the image is no
  longer pinned. Mikal is reading each series and pushing it by hand.

| Patch | Series | Change |
|---|---|---|
| patch221 | `devstack-privsep-memory-001` | [1011992](https://review.opendev.org/c/openstack/devstack/+/1011992) |
| patch222 | `futurist-import-memory-004` | [1012147](https://review.opendev.org/c/openstack/futurist/+/1012147) |
| patch223 | `neutron-lib-import-memory-005` | [1012151](https://review.opendev.org/c/openstack/neutron-lib/+/1012151) |
| patch224 | `neutron-lib-db-api-import-memory-006` | [1012152](https://review.opendev.org/c/openstack/neutron-lib/+/1012152) |
| patch225 | `neutron-import-memory-007` | [1012169](https://review.opendev.org/c/openstack/neutron/+/1012169) |
| patch226 | `neutron-import-memory-007` | [1012170](https://review.opendev.org/c/openstack/neutron/+/1012170) |
| patch227 | `keystone-import-memory-008` | [1012171](https://review.opendev.org/c/openstack/keystone/+/1012171) |
| patch228 | `keystone-import-memory-008` | [1012172](https://review.opendev.org/c/openstack/keystone/+/1012172) |
| patch229 | `placement-import-memory-009` | [1012173](https://review.opendev.org/c/openstack/placement/+/1012173) |
| patch230 | `placement-import-memory-009` | [1012174](https://review.opendev.org/c/openstack/placement/+/1012174) |
| patch231 | `oslo.utils-import-memory-010` | [1012175](https://review.opendev.org/c/openstack/oslo.utils/+/1012175) |

#### Future work

- **nova-compute's database stack** (#8): about 17 MB per compute node.
  Raise it with nova reviewers once the rest of Phase 6 has been
  received.
- **uWSGI `lazy-apps`**: input to Phase 7.
- **conntrack_cmd** still loads neutron_lib through `netlink_lib`,
  which Phase 4 left at about 88 MB. #2 would fix that too. If #2 is
  rejected, look for a smaller cut.

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
