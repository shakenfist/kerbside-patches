# CI tiers: a pull request smoke tier and a merge queue tier

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly, and read the
workflows it cites rather than this plan's summary of them:
`.github/workflows/functional-tests.yml`, `rebase-tests.yml`,
`local-container-builds.yml`, `auto-retry-infra-failures.yml`,
`daily-rebase-checks.yml`, `heal-data-prs.yml` and `pr-retest.yml`,
and the scripts they call in `tools/` and `_build/`. Where a
question turns on GitHub's merge queue, rulesets or Actions
semantics, check GitHub's documentation and the fleet's own record
of what it learned the hard way: shakenfist/development's
`docs/audits/merge-queue-config.md`,
`docs/audits/merge-group-cancellation.md` and
`docs/audits/expensive-lane-path-filter.md`, and the reference
workflows named under Situation. Flag any uncertainty explicitly
rather than guessing.

This plan changes CI, not patches. Nothing in it is aimed upstream,
and nothing in it changes the deploy flow in
`shakenfist/actions/deploy-kolla-ansible`. It does change how much
of the shared under-cloud this repository holds, which is the point.

## Situation

Measured on 2026-10-20 against `develop` at `3bd67676e`, from the
last 200 runs of each pull request workflow (2026-08-02 to
2026-10-19 for `functional-tests.yml`, 2026-08-14 onwards for the
other two), the job lists of every failed run and every earlier
attempt of a re-run run (85 run attempts), the job timings of 22
recent green runs, and the logs of eight lanes that failed while
their siblings passed. About 150 GitHub API calls in all.

There is no merge queue, no ruleset (the exported
`.github/exported-config/rulesets-summary.json` is `[]`), and
`allow_auto_merge` is off. Every pull request is merged by hand:
all 99 merged between 2026-10-03 and 2026-10-18 were merged by
mikalstill.

### What runs on every push to a pull request

Three workflows, all `pull_request` against `develop` with
`paths-ignore` for `data/**` and `docs/**` (and `README.md` for the
first):

| Workflow | Job | Lanes | Runner | Run time (median, max) | Tests |
|----------|-----|-------|--------|------------------------|-------|
| functional-tests | lint | 1 | vm s | 1, 2 min | `pre-commit run --all-files` |
| functional-tests | assemble_source | 1 | vm xl | 10, 12 min | patches apply to `source_sha`, source tarballs |
| functional-tests | build_images | 3 (debian trixie, ubuntu noble, rocky 10) | vm xl | 32-38, 49 min | a filtered image set builds and pushes |
| functional-tests | aggregate_results | 1 | vm m | 0 min | collects build tags |
| functional-tests | test_installs | 8 | bound s (cloud-building) | 47-73, 83 min | a real Kolla-Ansible deploy, post-deploy sanity, a console |
| functional-tests | collect_layer_data | 1 | static | 1 min | opens a `data/layers` pull request |
| rebase-tests | check_bot_commit | 1 | vm s | 0 min | |
| rebase-tests | lint_check | 1 | vm m | 27, 32 min | `tox -elinters` for Kolla-Ansible |
| rebase-tests | automated_lint_fixer | when lint fails | claude-code | | Claude fixes lint |
| rebase-tests | functional_matrix | 6 | vm xl | kolla 9-10, kolla-ansible 57-59, deployers 7 and 19 min | `_build/test-apply.sh` per project, debian 13 and rocky 9 |
| local-container-builds | local_build_matrix | 1 | vm xl | 42 min | every image builds without the CI registry |

The eight install lanes are: Debian 13 images on a Debian 13 host
(`master-h-debian13-c-debian13-aio`, the control), the same with
the containerised deployer (`-aio-launcher`), the same without
Kerbside (`-aio-nokerbside`), Ubuntu 24.04 images on Debian 13,
Debian images on a Rocky 10 host with the venv and with the
launcher, Rocky images on Rocky 10, and a Debian 13 multinode.

So one push asks for 23 VM jobs: 11 of them xl and 8 of them
building a nested cloud. The conductor runs at most 12 workers for
the whole fleet (held at 12 on 2026-10-17, private-ci note n41).

### What it costs

The run time of any one job is modest. The wall clock is not:

* Successful `functional-tests.yml` runs took a median 435 minutes
  from creation to completion (p90 915, max 1755). First-attempt
  green runs on 2026-10-14 and 10-15 took 504, 581, 582 and 978
  minutes, and their image builds did not start until 72 to 261
  minutes after the run was created. The time is queueing for xl
  runners and cloud capacity, not work.
* `rebase-tests.yml` successes took a median 238 minutes for jobs
  whose longest lane runs 59.
* 47 of 200 functional runs were re-run at least once (5 three
  times). In the 15 days to 2026-10-18 there were 54 runs and 75
  attempts across 34 branches.
* private-ci note n9 (2026-10-11) records the cross-fleet cost:
  while this repository's multinode job was pending, every
  debian-13/s runner claimed 44 vCPU under combination sizing, 102
  claim refusals in 13 hours, with 91 jobs queued fleet-wide.

### What it has caught

This is what decides which lanes can wait for the queue.

**Every genuine failure since 2026-09-20 failed a lane this plan
keeps on pull requests.** Since then the matrix has had its
current shape (Debian 13 hosts). Of 89 first attempts that were not
cancelled, 58 were clean, 17 failed only in infrastructure steps
(`Setup test environment`, `Set up job`, artifact transfer), 5
failed only in `collect_layer_data`, and 9 failed a deploy, build
or test step. Those nine were:

| Run | Branch | What failed | Verdict |
|-----|--------|-------------|---------|
| 35816827966, 35852191265, 35915692798 | mariadb-collation-fixes | every lane but Ubuntu, then every build | genuine; the Debian control and Debian build both failed |
| 38091129412 | deployer-phase-5 | both launcher lanes | genuine; the Debian launcher lane failed |
| 36542933592 | consistency-fixes | lint | genuine; lint |
| 37996529795 | deployer-phase-4-clean-host | Ubuntu deploy, twice | infrastructure: `No matching distribution found for openstacksdk` from the PyPI mirror |
| 37670483548, 36842787902 | deployer-phase-3, trigger-downstream | Rocky image build | flake; passed on re-run |
| 38533346103 | bind-test-clouds | Debian control console test | flake; passed on re-run |

**Lane-unique failures are almost all flakes.** Before
2026-09-20, the failures confined to one lane that this plan moves
to the queue were: the Rocky-host and Ubuntu post-deploy check
(runs 30862328691, 30775862036) on an unhealthy `nova_scheduler`,
the known race `ci-report.sh scheduler-unhealthy` tracks; a Rocky
host refusing a VIP that answered ping (35202102331); multinode
failing to reach Galaxy (`Temporary failure in name resolution`,
32833757983); and multinode deploys that passed on re-run
(31145402334, 34323897454). The two genuine ones were both on
2026-09-08 (34270270577), during the Debian 12 to 13 host
migration, which was a CI change rather than a patch change: the
Ubuntu lane's `bootstrap_kerbside` container failed to start
because `/etc/timezone` was not the type the bind mount expected,
and multinode failed with `Could not detect a supported package
manager`. A merge tier would still have caught both before they
landed. A pull request would just have learned of it later.

**The patch tests have never failed a pull request.**
`rebase-tests.yml` has 165 successes, 34 cancellations and no
failures in 200 runs. Two first attempts failed a deep test (both
`Rebase kolla on debian 13`, runs 33668968759 and 32869409461) and
passed on re-run. The Rocky 9 lanes failed only in `Set up job` and
`Install test dependencies`. The daily rebase runs the same tests
before it opens its pull request, and authors run them locally.

**`local-container-builds.yml` failed four times**, all while the
whole matrix was failing (the daily rebases of 2026-08-14 to 08-16
and mariadb-collation-fixes). It is still the only job that builds
every image rather than the filtered set, so it has coverage
nothing else has.

Across all 85 failed attempts, the step that failed was
`Deploy Kolla-Ansible` 60 times (mostly correlated whole-matrix
regressions), `Setup test environment` 41, `collect_layer_data` 13,
an image build 9 and `Set up job` 7. By lane, the Rocky-host lanes
failed most often (18 each), then Ubuntu and multinode.

### Bot pull requests

Of the 99 merges between 2026-10-03 and 2026-10-18, 57 were data
pull requests (`<prefix>-data-<date>-run<id>` branches, merged after
a median of an hour), 16 daily rebases (median 9 hours open), 23
human, 2 Renovate (which has `automerge: false`) and 1 consistency
fix. Non-data merges peaked at 5 a day.

* `collect_layer_data` opens a data pull request from every
  pull request run. Those touch only `data/**`, so today they run
  no CI at all.
* `heal-data-prs.yml` union-merges `develop` into any conflicted
  data pull request on every push to `develop`, and pushes the
  merge back with `DAILY_REBASE_TOKEN`.
* `daily-rebase-checks.yml` opens the daily rebase from a
  `claude-code` runner as shakenfist-bot, which triggers
  `pull_request` CI because the token is not `GITHUB_TOKEN`.
* `auto-retry-infra-failures.yml` re-runs failed jobs whose logs
  match an infrastructure pattern, once per run, for all three
  workflows. It finds the pull request from
  `.pull_requests[0]`, which a `merge_group` run does not have.
* `pr-retest.yml` ("@shakenfist-bot please retest") dispatches
  `functional-tests.yml` on the pull request's branch. It is a
  fleet template (`shakenfist/development`
  `templates/ci-review-automation/`).

### Reference designs

* **shakenfist/client-python-k3s** `functional-tests.yml`: a
  `check_paths` job using `dorny/paths-filter` with
  `predicate-quantifier: 'every'` instead of trigger-level
  `paths-ignore`; smoke jobs on every event; the merge tier
  (`cluster_deploy`) only on `merge_group` and `workflow_dispatch`;
  `Can enqueue` and `Can merge` aggregators under
  `always() && ...` whose jq passes only `success` and `skipped`.
  Its ruleset 20595671 is the template for ours: merge method
  `MERGE`, `max_entries_to_build: 1`, `min_entries_to_merge: 1`,
  `max_entries_to_merge: 5`, `ALLGREEN`,
  `check_response_timeout_minutes: 360`, required checks
  `Can enqueue` and `Can merge` from GitHub Actions
  (`integration_id` 15368), deletion and non-fast-forward rules,
  and one bypass team (11722172).
* **shakenfist/shakenfist** adds a last step to `check_paths`,
  `tools/ci-merge-group-current.sh`, which cancels a merge group
  run whose `base_sha` is no longer the head of `develop` before
  its jobs enter their concurrency groups (shakenfist#3998: a
  superseded run won the race and cancelled the live one).
* **The two newest fleet rules.** Merge group concurrency keys use
  `merge_group-<base_ref>` only when `github.run_attempt == '1'`, so
  that a re-run of an old queue run can neither cancel the live
  entry nor be cancelled by it; this is merged on
  shakenfist/client-python-ova's `develop` (`ci.yml`). And a
  `check_paths` that filters merge groups diffs against
  `github.event.merge_group.base_sha`, because a re-run diffed
  against `develop` would find nothing once the entry had merged and
  pass on skips; that one is only on open pull requests so far
  (kerbside#582 at `cc50cb9`, client-python-k3s#154,
  shakenfist#4581). shakenfist's release-gating branch
  also records "re-queue, not re-run, after a failed queue run"
  (`9b6a787af1`): a failed queue run ejects its entry, so re-running
  it cannot land anything.

## Mission and problem statement

Split this repository's CI into two tiers. Every push to a pull
request runs a smoke tier that answers "do the patches apply and
pass their tests, does an image build, and does a single-node cloud
deploy, with and without the containerised deployer", in about two
hours rather than eight. The full matrix runs once per pull request,
in a serial merge queue, on the commit that will actually land.

The goal is less load on the shared under-cloud per push, and a
faster first signal on a pull request. The cost, stated up front, is
that merges become serial and each one waits for a full-matrix run.

Out of scope: changing what any lane tests, the deploy flow in
`shakenfist/actions`, the conductor's scheduling in
`shakenfist/private-ci`, kerbside's downstream trigger (merges still
push to `develop`), and shrinking `functional-tests.yml` as a file
beyond what the restructure itself does.

## Decisions

### 1. Which lanes run where

| Lane | Today | Smoke (pull request) | Merge (queue) | Why |
|------|-------|----------------------|---------------|-----|
| Lint (`pre-commit`) | PR | yes | yes | seconds |
| Ansible lint check | PR | yes | yes | cheap; the automated fixer needs it |
| Automated lint fixer | PR | when lint fails | no | it pushes to the branch |
| Patch tests, Debian 13 (kolla, kolla-ansible, both deployer series) | 4 xl lanes | one xl job, sequential | yes | "patches apply and pass tests"; about 93 minutes, alongside the build-then-deploy path |
| Patch tests, Rocky 9 (kolla, kolla-ansible) | 2 xl lanes | no | one xl job | no genuine failure in 200 runs |
| Assemble source | PR | yes | yes | everything needs it |
| Images, Debian trixie | PR | yes | yes | "an image build"; the control lanes need it |
| Images, Ubuntu noble and Rocky 10 | PR | no | yes | only merge-tier lanes use them; Rocky's build is the flakiest |
| Install: Debian 13 control (`-aio`) | PR | yes | yes | caught mariadb-collation and the Debian 12 break |
| Install: Debian 13 launcher (`-aio-launcher`) | PR | yes | yes | Michael's sketch; caught deployer-phase-5 |
| Install: no Kerbside, Ubuntu images, both Rocky-host lanes, Rocky images, multinode | PR | no | yes | one genuine unique catch each for Ubuntu and multinode in 80 days, both during a CI migration; otherwise flakes |
| Local container build (every image) | PR | no | yes | the only unfiltered build; never failed alone |
| Collect layer data | PR | no | merge group, not gating | see Decision 5 |

The merge tier is the whole of today's coverage, not only the lanes
the smoke tier drops. It tests the merged tree rather than the pull
request's head, and it is the result a later release gate or audit
would trust. Running the two smoke installs again costs capacity but
no wall clock, because they run beside the others.

Per push, that is 23 VM jobs, 11 xl and 8 nested clouds today,
against 7 VM jobs, 3 xl and 2 nested clouds on the smoke tier
(moving `check_bot_commit` and `aggregate_results` to static
runners). The full matrix runs about once per pull request instead
of 2.2 attempts per branch.

No lane is dropped. Whether `-aio-nokerbside` and the Rocky-host
launcher lane earn their place is an open question for after the
soak.

### 2. One gated workflow, lanes in a data file

`functional-tests.yml` (name "Build test clouds", which
`auto-retry-infra-failures.yml` matches on) stays the single
workflow that the ruleset requires, and carries both aggregators.
Required checks have to come from somewhere that always reports, so:

* Trigger-level `paths-ignore` goes. A `check_paths` job decides
  instead, excluding `docs/**`, `data/**` and `README.md` with
  `predicate-quantifier: 'every'`, with
  `base: ${{ github.event.merge_group.base_sha || 'develop' }}`.
  A docs-only or data-only pull request or queue entry skips every
  heavy job, and the aggregators pass on the skips.
* `rebase-tests.yml` becomes a reusable (`workflow_call`) workflow,
  which `functional-tests.yml` calls with the tier. Its result then
  sits in the aggregators' `needs`. `local-container-builds.yml`
  does the same, and keeps `workflow_dispatch`.
* The lanes move into `.github/ci-lanes.json`, each carrying the
  tiers it runs in. A short `tools/ci-select-lanes.py`, run in
  `check_paths`, emits the build, install and patch-test matrices for
  the event, and the jobs read them with `fromJSON`. There is one
  definition of each job rather than smoke and merge copies of a
  600-line install job. The file is under `.github/`, not `etc/` or
  `tools/`, because `_build/calculate-container-hash.sh` hashes
  `etc`, `_build` and `tools` into the image tag. Moving a lane
  between tiers should not rebuild every image.
* `workflow_dispatch` gains a `tier` input, `smoke` or `full`,
  defaulting to `smoke`. "@shakenfist-bot please retest" then stays
  a smoke run with no change to the fleet template. A full run on a
  branch is a dispatch with `tier=full` from the Actions tab, and its
  `Can enqueue` reports on the head commit.

### 3. The merge queue, by the fleet's current rules

* `merge_group:` joins the triggers.
* `Can enqueue` (`if: always() && github.event_name != 'merge_group'`)
  and `Can merge` (`if: always() && github.event_name == 'merge_group'`)
  are written as in client-python-k3s. They use `always()`, not
  `!cancelled()`, so a cancelled run fails `Can merge` instead of
  skipping it. Their jq passes only `success` and `skipped`, so
  `cancelled` fails. `collect_layer_data` and the automated lint
  fixer are not in either `needs` list.
* Every job on a VM or bound runner keys its concurrency group as:

  ```yaml
  concurrency:
    group: >-
      ${{ github.workflow }}-<job>-<matrix lane if any>-${{
      github.event_name == 'merge_group' && github.run_attempt == '1'
      && format('merge_group-{0}', github.event.merge_group.base_ref)
      || github.ref }}
    cancel-in-progress: true
  ```

  The reusable workflows key on a `workflow_call` input as well,
  per `merge-group-cancellation.md`.
* `check_paths` ends with a copy of shakenfist's
  `tools/ci-merge-group-current.sh`, so a superseded merge group
  cancels itself before its jobs take a concurrency group. A
  superseded run here costs hours of under-cloud, not minutes.
* The ruleset is a copy of client-python-k3s's 20595671, including
  `max_entries_to_build: 1` and `check_response_timeout_minutes:
  360`. As far as we know, 360 is also the most the API accepts:
  check that when the ruleset is created.

### 4. A failing merge-tier lane, and the latency to expect

A failed lane fails `Can merge` and ejects the entry. Re-running the
run cannot land anything, so recovery is always a re-queue.

* **Infrastructure failures re-queue themselves, once.**
  `auto-retry-infra-failures.yml` already classifies failures by log
  pattern. For a `merge_group` run, it takes the pull request number
  from the queue branch (`gh-readonly-queue/develop/pr-<N>-<sha>`)
  and re-enqueues it as shakenfist-bot. A marker comment keyed on the
  head SHA stops it doing so twice. Since 2026-09-20, 17 of 89 first
  attempts (19%) failed only in infrastructure steps, so this alone
  should save most ejections.
* **Anything else stays ejected and gets a comment** naming the
  failed lanes and pointing at the `merge-ci-triage` skill. The fix
  goes on the pull request, which re-runs the smoke tier, and the
  pull request is re-queued. To reproduce a merge-tier lane before
  re-queueing, dispatch the branch with `tier=full`.
* **No lane is made advisory.** A lane that does not gate should not
  run in the queue. If a lane keeps ejecting entries for reasons
  that are not the change's fault, the fallback is to move it to a
  nightly full run on `develop` (Phase 4's gate), not to stop it
  voting.

**Expected latency.** The merge tier's critical path is assemble
(10) + the slowest build (38, max 49) + multinode (73, max 83), about
2h15m of work. The image build is a skip when the smoke tier already
pushed that tag the same day and `develop` has not changed the hashed
directories since (#1886's build marker). Queueing comes on top. It
has been 1-4 hours a run, and it should fall as per-push load drops
by about two thirds. Expect **2.5 to 4 hours per code entry**, and a
few minutes per docs or data entry. At 2.8 code merges a day on
average, the queue is busy 7 to 11 hours a day. On a 5-merge day, the
last pull request enqueued in a burst waits 10 to 20 hours, against
today's habit of merging five in an afternoon. That serialisation is
the main cost of this design, and `max_entries_to_build` must stay at
1 (`merge-queue-config.md`). It will not grow back.

Against that, today a pull request already waits a median 435
minutes for its first full result, and human pull requests stay open
a median 15.4 hours. The smoke tier should give a first result in
about two hours.

The hard limit is the check timeout. An entry whose `Can merge` has
not reported within 360 minutes is ejected, however healthy its
lanes. Phase 4 measures how close entries come to it.

### 5. Bot pull requests in the queue

* **Daily rebase**: queued like any other pull request. The smoke
  tier runs on the pull request, Michael reviews and enqueues, and
  the merge tier runs the full matrix on the new `source_sha`s. No
  bypass. Whether a rebase that needed no Claude fixes should
  enqueue itself is an open question.
* **Data pull requests** (layers and CI reporting): the bot sets
  auto-merge when it creates one (`gh pr merge --auto --merge`),
  which with a queue means "enqueue once `Can enqueue` passes". Both
  tiers skip on the path filter, so an entry takes minutes on static
  runners. `create-data-pr.sh` refuses to set auto-merge on a pull
  request that changes anything outside `data/`. This needs
  `allow_auto_merge: true` on the repository. It also ends the hand
  merging of about 4 data pull requests a day.
* **Conflicting data pull requests**: once one data entry merges, the
  next conflicts. GitHub drops it from the queue, which also disables
  its auto-merge. `heal-data-prs.sh` already heals it on the push to
  `develop`. After a heal it now sets auto-merge again.
* **Layer data comes from the merge tier**: `collect_layer_data` runs
  on `merge_group`, not on pull requests. The merge tier builds all
  three variants once per pull request, so the time series gets every
  variant, at one point per landed change rather than one per push,
  and with fewer conflicting data pull requests. The source pull
  request comes from the queue branch name. It stays outside
  `Can merge`'s `needs`, as it is outside the gate today.
* **Bypass**: the ruleset copies k3s's bypass team, so Michael can
  still merge directly in an emergency. A bypassed merge skips the
  merge tier, so `docs/ci.md` says so.

## Open questions

1. **Should a clean daily rebase enqueue itself?** Default: no.
   Michael keeps reviewing every rebase before it is queued.
2. **Do bot data pull requests need a human at all?** Decision 5
   assumes not. If Michael wants to eyeball them, drop the
   auto-merge half of Phase 3 and he enqueues them by hand.
3. **Should merge-group jobs get priority in the conductor?** It
   would cut queue latency more than anything here, but it is
   private-ci's call. Default: not pursued unless Phase 4 shows
   queueing, not work, dominates entry time.
4. **Do `-aio-nokerbside` and the Rocky-host launcher lane earn
   their place?** Neither has caught anything another lane missed.
   Default: keep both in the merge tier, and decide with Phase 4's
   data.

## Execution

Phases are sections of this plan, following `docs/plans/index.md`.
Every phase lands as its own pull request against `develop`.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. One gated workflow | Proposed | |
| 2. Re-queue on infrastructure failure | Proposed | |
| 3. Split the tiers and enable the queue | Proposed | |
| 4. Soak and measure | Proposed | |
| 5. Push audit | Proposed | |

The order keeps every lane voting on pull requests until the queue
exists to run it. Phase 1 restructures with no change in coverage.
Phase 2 adds recovery code that lies dormant until there are merge
groups. Phase 3 is the one switch, and is small because the first
two did the work.

### Phase 1: One gated workflow

Planning effort: high. It restructures the workflow every other
phase edits, and it must not change what a pull request tests.

Restructure as Decisions 2 and 3 describe, with every lane in both
tiers (`"tiers": ["smoke", "merge"]` throughout `ci-lanes.json`), so
pull requests test exactly what they test today. The two load
reductions that do not depend on a queue land here too: the Debian
patch tests become one sequential job and the Rocky ones another
(6 xl lanes become 2), and `check_bot_commit` and
`aggregate_results` move to static runners.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Create `.github/ci-lanes.json` with three lists, `builds`, `installs` and `patch_tests`. Copy every field of the current matrix entries from `functional-tests.yml` (`build_images` at :112-150, `test_installs` at :418-540; skip the commented-out 2026.1 entries) and `rebase-tests.yml` (`functional_matrix`), and add `"tiers": ["smoke", "merge"]` to each. Patch tests are two entries, one per runner OS, each with a `projects` list: debian-13 `[kolla, kolla-ansible, kolla-deployer, kolla-ansible-deployer]`, rocky-9 `[kolla, kolla-ansible]`. Write `tools/ci-select-lanes.py` (Python 3, stdlib only, single quotes, 120 columns, executable, with `#!/usr/bin/env python3`). Arguments: `--lanes`, `--event` (`pull_request`, `merge_group`, `workflow_dispatch`), `--tier` (the dispatch input, ignored otherwise) and `--code-changed` (`true`/`false`). `pull_request` selects `smoke`, `merge_group` selects `merge`, and dispatch selects whatever `--tier` says, with `full` meaning `merge`. Print `builds=<json>`, `installs=<json>` and `patch_tests=<json>` lines for `$GITHUB_OUTPUT`, each `{"test": [...]}` so the jobs keep reading `matrix.test.*`. Exit non-zero if a selected install's `release`/`container_distro`/`container_distro_version` has no selected build. When `--code-changed` is false, print empty lists. Add `tools/test-ci-select-lanes.py` (unittest) covering each event, `full` versus `smoke`, the missing-build error and code-changed false, and a pre-commit hook for it modelled on `write-build-manifest-tests` in `.pre-commit-config.yaml`. Commit subject: "Describe the CI lanes in a data file." |
| 1b | high | opus | worktree | Rework `.github/workflows/functional-tests.yml` per Decisions 2 and 3 of `docs/plans/ci-tiers.md`. (1) Triggers: drop `paths-ignore`, add `merge_group:`, add a `workflow_dispatch` input `tier` (choice `smoke`/`full`, default `smoke`) beside `data_prefix`. (2) Add `check_paths` on `[self-hosted, static]`, copied from shakenfist/kerbside's `functional-tests.yml:60-104` as kerbside#582 (`cc50cb9`) leaves it, with `base: ${{ github.event.merge_group.base_sha \|\| 'develop' }}`, `predicate-quantifier: 'every'` and filters `'**'`, `'!docs/**'`, `'!data/**'`, `'!README.md'`. Add a step running `tools/ci-select-lanes.py` that exposes `builds`, `installs` and `patch_tests` outputs. The last step is "Cancel this run if the merge group is superseded", copied with `tools/ci-merge-group-current.sh` from shakenfist/shakenfist at `8a89247de0`; keep its header comment and say where it came from. (3) Make `assemble_source`, `build_images` and `test_installs` `needs: check_paths`, skip when `code_changed == 'false'` or the matrix is empty, and use `strategy.matrix: ${{ fromJSON(needs.check_paths.outputs.<name>) }}`. Keep `test_installs`' bound `runs-on` exactly as it is. (4) Put every VM or bound job on the concurrency key in Decision 3, keeping each job's suffix and matrix lane. (5) Move `aggregate_results` to `[self-hosted, static]` and give it a `timeout-minutes`. (6) Call the reusable patch tests (step 1c) with `tier` and `patch_tests`, and the reusable local build (step 1d) when the event is `merge_group` or a `full` dispatch. In this phase that also means every `pull_request`: gate it on a `local_build` output from `ci-select-lanes.py`, added to `ci-lanes.json` as `{"tiers": [...]}`. (7) Add `can_enqueue` and `can_merge` exactly as client-python-k3s's `functional-tests.yml` writes them, with `needs` covering check_paths, lint, assemble_source, build_images, aggregate_results, test_installs, the patch tests and, for `can_merge`, the local build. Leave out `collect_layer_data` and the lint fixer. (8) Leave `collect_layer_data` on `pull_request` for now. Run `pre-commit run --all-files` (actionlint is in it). Then run shakenfist/development's consistency checks `merge-group-cancellation` and `expensive-lane-path-filter` against the worktree, using the local clone at `../development` (see its `scripts/` for how to run one check). Commit subject: "Gate CI on Can enqueue and Can merge." |
| 1c | medium | sonnet | none | Turn `.github/workflows/rebase-tests.yml` into a reusable workflow: `on: workflow_call` with inputs `tier` and `patch_tests` (JSON), and no `pull_request` trigger. `check_bot_commit` moves to `[self-hosted, static]`. `lint_check` stays on vm m. `automated_lint_fixer` also needs `github.event_name == 'pull_request'` in its `if:` (in a called workflow that is the caller's event). Replace `functional_matrix` with `patch_tests`: matrix `fromJSON(inputs.patch_tests)`, runs-on `[self-hosted, vm, '${{ matrix.test.runs_on }}', 'xl']`, `timeout-minutes: 150`, and one step per project, each running `./_build/test-apply.sh <project>`, so the log says which project failed. Steps for projects a lane does not list are skipped with an `if:` on `contains(matrix.test.projects, '<project>')`. Concurrency keys use the Decision 3 form and also include `inputs.tier`, per `merge-group-cancellation.md`'s rule for reusable workflows. Keep the file's name and `permissions:`. Commit subject: "Run the patch tests from the gated workflow." |
| 1d | low | sonnet | none | In `.github/workflows/local-container-builds.yml`, replace the `pull_request` trigger with `workflow_call` and keep `workflow_dispatch`. Set `SF_HEAD_SHA` from `github.sha` rather than `github.event.pull_request.head.sha`, which is empty outside `pull_request`. Concurrency key as in step 1c. Add `timeout-minutes: 120`. Commit subject: "Call the local build from the gated workflow." |
| 1e | low | sonnet | none | In `.github/workflows/auto-retry-infra-failures.yml`, drop "Ensure local container builds work" and "Rebase tests" from `workflow_run.workflows`, because their jobs now run inside "Build test clouds". Nothing else changes in this phase. Commit subject: "Retry only the workflow that now runs everything." |
| 1f | medium | sonnet | none | Write `docs/ci.md` covering both tiers, the lane file, how to move a lane between tiers, `tier=full` dispatches, and what "please retest" runs. Mark the merge tier as not yet enabled; later phases update the page. Link it from `docs/index.md` and from `AGENTS.md`'s question table. In `AGENTS.md`'s "Modifying GitHub Actions Workflows", add: lanes live in `.github/ci-lanes.json`; the gated workflow never uses trigger-level `paths-ignore`; new jobs take the Decision 3 concurrency key. Fix its "Other workflows" list. Rewrite `ARCHITECTURE.md`'s "CI/CD" section for the new shape. Commit subject: "Document the CI tiers." |

Exit: the phase's own pull request shows the same 8 installs, 3
builds and both patch-test OSes passing as before, and a green
`Can enqueue`. A `tier=full` dispatch on the branch also runs green.
A throwaway docs-only pull request reports `Can enqueue` green with
every heavy job skipped, and is then closed. `pre-commit` and both
fleet audits pass.

### Phase 2: Re-queue on infrastructure failure

Planning effort: medium. The classification already exists, and the
new behaviour is narrow.

Move the inline script from `auto-retry-infra-failures.yml` into a
shell script in `tools/`, as Michael prefers for long workflow steps,
and teach it the `merge_group` case from Decision 4. The new code
stays dormant until Phase 3 creates merge groups, so this phase
proves it with tests rather than live runs.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | worktree | Move the `run:` body of `.github/workflows/auto-retry-infra-failures.yml` to `tools/ci-auto-retry.sh` (shellcheck-clean, `set -euo pipefail`), unchanged for pull request runs. Add the merge-group path. Read the run's `event` and `head_branch`. When the event is `merge_group`, parse `pr-<N>-<sha>` out of `gh-readonly-queue/develop/pr-<N>-<sha>`, classify failed jobs with the same `INFRA_PATTERNS`, and on an infrastructure failure re-enqueue PR N. Use `gh api graphql` `enqueuePullRequest` with the pull request's node id, authenticated with `DAILY_REBASE_TOKEN`: events caused by `GITHUB_TOKEN` do not start workflows, and we want the merge group to run. Do this at most once per PR head SHA, recorded by a marker comment `<!-- auto-requeue-infra: <head sha> -->`, then comment that it was re-queued. On any other failure, comment once (marker keyed on the run id) listing the failed jobs and their first failed step, and pointing at the `merge-ci-triage` skill. Never call `gh run rerun` for a merge group. Pass `DAILY_REBASE_TOKEN` to the step as a second env var, and keep the workflow's `permissions:` as they are. Before writing any patterns, check that the existing ones match the `Setup test environment` failures of runs 38971535327, 38820775821 and 38792591006 (attempt 1). If they do not, add the narrowest pattern that does, and say which line matched. Add `tools/test-ci-auto-retry.sh` (bats is not installed; plain bash with a stubbed `gh` on `PATH`) covering: a pull request infra failure re-runs; a merge-group infra failure enqueues; a second merge-group failure on the same head SHA does not; a merge-group test failure comments and does not enqueue; and an unparseable head branch exits 0 with a warning. Commit subject: "Re-queue a merge group that failed on infrastructure." |
| 2b | low | sonnet | none | Add a "When a merge-tier lane fails" section to `docs/ci.md`: what is re-queued automatically and once, what is left for a human, the `merge-ci-triage` skill, and `tier=full` dispatches for reproducing a lane. Commit subject: "Document merge queue failure handling." |

Exit: the tests pass, and `tools/ci-auto-retry.sh` re-runs a real
failed pull request run, chosen by the management session, exactly
as the inline script did.

### Phase 3: Split the tiers and enable the queue

Planning effort: high. It is the switch, it needs admin changes
outside the repository, and it has to be ordered so that no lane goes
untested in between.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | low | sonnet | none | In `.github/ci-lanes.json`, set `"tiers": ["merge"]` on the Ubuntu and Rocky builds; on the `-aio-nokerbside`, Ubuntu, both Rocky-host and Rocky-image installs and the multinode install; on the rocky-9 patch tests; and on `local_build`. Leave everything else on both tiers. Update `tools/test-ci-select-lanes.py` so the `pull_request` case expects exactly 1 build, 2 installs (`-aio`, `-aio-launcher`) and 1 patch test. Commit subject: "Move the long lanes to the merge queue." |
| 3b | medium | sonnet | none | In `.github/workflows/functional-tests.yml`, make `collect_layer_data` run `if: github.event_name == 'merge_group' && !cancelled()` (`!cancelled()`, as today, not the aggregators' `always()`: a superseded run's partial builds are not data), and read the source pull request number from `github.event.merge_group.head_ref` (`gh-readonly-queue/develop/pr-<N>-<sha>`) instead of `github.event.pull_request`. Drop `PR_TITLE` from the commit and body if no title is available. It must stay out of both aggregators' `needs`. In `tools/create-data-pr.sh`, after the pull request exists, set auto-merge with `gh pr merge <N> --auto --merge`, but only if `git diff --name-only origin/develop...<head>` lists nothing outside `data/`; otherwise warn and leave it. In `tools/heal-data-prs.sh`, after a successful heal and push, set auto-merge again the same way. Commit subject: "Queue data pull requests automatically." |
| 3c | medium | sonnet | none | Update `docs/ci.md` (the merge tier is live; the bot pull request rules from Decision 5; that a bypass merge skips the merge tier), `docs/ci-data.md` (layer data now comes from merge groups) and `ARCHITECTURE.md`'s CI/CD section to match. Commit subject: "Document the merge queue." |
| 3d | medium | opus | none | Management session, with Michael's explicit go-ahead, after the 3a-3c pull request has merged: set `allow_auto_merge: true` (`gh api -X PATCH repos/shakenfist/kerbside-patches -F allow_auto_merge=true`). Create the ruleset by fetching client-python-k3s's ruleset 20595671, keeping `name`, `target`, `enforcement`, `conditions`, `rules` and `bypass_actors`, and `POST`ing it to `repos/shakenfist/kerbside-patches/rulesets`. Check that the response has `max_entries_to_build: 1` and `check_response_timeout_minutes: 360`. Run the `export-repo-config` workflow so `.github/exported-config/` records both changes in its own pull request. No commit from this step itself. |

The admin step comes last on purpose. Between 3a merging and 3d,
merge-tier lanes run on nothing. That window should be minutes, and
3d should happen while no other pull request is about to merge.

Exit: the first human pull request through the queue shows
`Can enqueue` from a two-install smoke run and `Can merge` from a
full merge group, and lands. A data pull request enqueues itself and
lands without a human. A daily rebase lands through the queue.

### Phase 4: Soak and measure

Planning effort: medium.

Run the queue for 14 days or 30 code entries, whichever comes later,
and record the following in this section:

* each entry's wall clock from enqueue to `Can merge`, with the p50,
  p90 and max;
* ejections, split into infrastructure (re-queued automatically),
  genuine and timeout;
* smoke-tier wall clock per push, against the 435-minute median
  recorded above;
* which merge-tier lane caused each ejection.

**Gate:** if the p90 entry exceeds 300 minutes, or more than 20% of
entries need a human after the automatic re-queue, move the multinode
and both Rocky-host install lanes to a nightly full run on `develop`.
That is a new `schedule` trigger which selects a `nightly` tier in
`ci-lanes.json`, failing into an issue the way the daily rebase does.
Then measure again. Otherwise record the result, settle Open
Question 4, and move on.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Read-only. From `gh run list -R shakenfist/kerbside-patches -w functional-tests.yml --event merge_group --limit 100 --json databaseId,createdAt,updatedAt,conclusion,headBranch,attempt`, and the pull request timeline events for enqueue times (`gh api graphql`, `AddedToMergeQueueEvent` and `RemovedFromMergeQueueEvent`), produce the four measurements above. Keep it under 150 API calls; fetch jobs only for failed runs. Report the numbers. Commit nothing. |
| 4b | medium | sonnet | none | Only if the gate trips: add the `nightly` tier and `schedule` trigger described above, with a failure issue labelled `nightly-failure`. Move the three lanes to it. Commit subject: "Run the slowest lanes nightly instead of in the queue." |

### Phase 5: Push audit

Planning effort: medium.

This repository has no `PUSH-AUDIT.md`. This phase runs what
`PLAN-TEMPLATE.md` specifies in its place over the accumulated diff
of Phases 1 to 4 against `origin/develop`, using the ranges recorded
in the `Merged` column: the five-item checklist, a `/code-review` of
that diff, and a read of every documentation page the plan changed.
`_build/test-apply.sh` does not apply, because the plan touches no
patches. The checklist's second item gets the most attention here:
the nightly run, if Phase 4 added one, and the bot's automatic
re-queues must each report a failure somewhere Michael sees. The
ruleset and repository setting changes from 3d are audited against
the exported configuration, not the diff. Findings land as their own
pull request.

## Risks

* **The 360-minute check timeout.** Today's full matrix takes 504 to
  978 minutes of wall clock on a first attempt. The merge tier only
  fits if per-push load falls enough to cut queueing, and that is a
  prediction, not a measurement. Phase 4's gate exists for this. An
  entry ejected on timeout looks like a failure, so 2a's comment says
  when `Can merge` never reported.
* **Merges become serial.** A burst of five merges that takes an
  afternoon today will take most of a day. This is the design's price
  and `docs/ci.md` says so. The tempting way out,
  `max_entries_to_build` above 1, is ruled out by the fleet audit and
  by shakenfist's experience of stacked rebuilds.
* **The lane file lives outside the hashed directories, the scripts
  do not.** `tools/ci-select-lanes.py` and `tools/ci-auto-retry.sh`
  change the image tag hash whenever they change, which forces a
  rebuild. The date in the hash already forces one daily, so the
  cost is small. The lane file sits under `.github/` so the frequent
  edit is free.
* **The bound-runner binding token.** `test_installs` binds with
  `build-test-clouds-${{ strategy.job-index }}`. That stays unique
  only while there is one install job. 1b must not split it into
  smoke and merge copies.
* **A required check that never reports blocks forever.** Every gated
  job must stay reachable on both events, and both aggregators must
  stay `always()`. Phase 1's docs-only pull request is the test.
* **Auto-merge is repository-wide.** Turning it on lets anyone with
  write access set it on any pull request. Here that is Michael and
  the bot.
* **A bot push to a queued pull request removes it from the queue.**
  The lint fixer only runs on `pull_request`, and the healer only
  touches conflicted data pull requests, which the queue has already
  dropped. A Claude fix pushed to a daily rebase while it is queued
  would dequeue it, which is the right outcome.
* **The patch tests stop running on Rocky 9 per push.** That lane has
  never failed except on infrastructure. The daily rebase still runs
  the patch tests before every rebase pull request.

## Agent guidance

Follow `PLAN-TEMPLATE.md`. Implementation is done by sub-agents and
reviewed in the management session before any commit. Step 3d is the
exception: it is an administrative change to the repository's
settings and ruleset, made only with Michael's explicit approval in
the session. Each phase's table is refined in the first commit of
that phase, once the previous phase's findings are known.

## Administration and logistics

### Success criteria

We will know this plan has succeeded when the following are true:

* A push to a code pull request starts at most 7 VM jobs, 3 of them
  xl and 2 of them building a nested cloud, and its smoke tier
  finishes in a median under three hours of wall clock.
* `develop` changes only through the merge queue (or a recorded
  bypass), and every code entry that landed has a green `Can merge`
  from a full-matrix merge group.
* The p90 queue entry finishes inside the 360-minute check timeout,
  or the nightly fallback is in place and measured.
* Data pull requests reach `develop` without a human merging them.
* `pre-commit run --all-files` passes, and shakenfist/development's
  `merge-group-cancellation`, `merge-queue-config` and
  `expensive-lane-path-filter` audits pass for this repository.
* `docs/ci.md` describes both tiers, the lane file and the failure
  handling. `AGENTS.md` and `ARCHITECTURE.md` say only what changed
  in convention and in shape.

### Future work

* Retry environment setup inside `setup-kerbside-environment` in
  `shakenfist/actions`. It is the largest single failure class (41
  failed lanes), and an in-job retry would beat a whole re-queue. It
  is shared with kerbside, so it is that repository's change.
* A `nightly` tier, if Phase 4 does not need one anyway, for the
  2026.1 entries still commented out in the matrix.
* Extract the 600-line install job into a reusable workflow, if the
  file's size still bothers anyone once the tiers exist.
* Conductor priority for merge-group jobs (Open Question 3).

### Bugs fixed during this work

None yet. Two found by the survey, which this plan does not fix:

* `auto-retry-infra-failures.yml` silently does nothing for any run
  without a pull request (`Not a PR run, skipping`). That includes
  `workflow_dispatch` retests. Phase 2 fixes the merge-group case
  only.
* `local-container-builds.yml` reads
  `github.event.pull_request.head.sha` into `SF_HEAD_SHA`, which is
  empty on its existing `workflow_dispatch` trigger. Step 1d fixes it
  in passing.

### Back brief

Before executing any step of this plan, back brief the operator on
your understanding of the plan and how the work you intend to do
aligns with it.
