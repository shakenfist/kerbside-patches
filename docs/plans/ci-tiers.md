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
  heavy job, and the aggregators pass on the skips. Unlike
  kerbside's copy, the job checks out on every event, dispatches
  included, because the lane selector and the superseded-run check
  both read the tree, and it carries job-level `permissions:` of
  `contents: read` and `actions: write`, because the workflow's own
  block is `contents: read` and the superseded-run check cancels its
  own run.
* `rebase-tests.yml` becomes a reusable (`workflow_call`) workflow,
  which `functional-tests.yml` calls with the tier and the lint
  fixer's token passed by name (it pushes with `DAILY_REBASE_TOKEN`;
  the fleet's reusable-workflow-secrets audit rejects
  `secrets: inherit`). Its result then sits in the aggregators'
  `needs`. `local-container-builds.yml` does the same, and keeps
  `workflow_dispatch`. Inside a called workflow `github.workflow` is
  the caller's name, so a called job's concurrency suffix must not
  repeat one of the caller's: `rebase-tests.yml`'s `-lint` would
  share a group with the pre-commit job and the two would cancel
  each other.
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
  `cancelled` fails. Each needs every gated job directly, not
  through another job, because a job skipped after its own `needs`
  failed reports `skipped`. `collect_layer_data` is in neither
  `needs` list. The automated lint fixer runs inside the patch-test
  call, so its result folds into the call's; it only runs after lint
  has already failed that call, so this adds no failure mode.
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
  and re-enqueues it as shakenfist-bot, but only when every failed
  job matched. The `<sha>` there is the base, not the pull request's
  head. The tested head is the merge group commit's second parent,
  and the re-queue happens only while the pull request is still at
  it. A marker comment keyed on that head stops it doing so twice. Since 2026-09-20, 17 of 89 first
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
| 1. One gated workflow | Complete | `6a0403851` |
| 2. Re-queue on infrastructure failure | In progress | |
| 3. Split the tiers and enable the queue | Not started | |
| 4. Soak and measure | Not started | |
| 5. Push audit | Not started | |

The order keeps every lane voting on pull requests until the queue
exists to run it. Phase 1 restructures with no change in coverage.
Phase 2 adds recovery code that lies dormant until there are merge
groups. Phase 3 is the one switch, and is small because the first
two did the work.

### Phase 1: One gated workflow

Planning effort: high. It restructures the workflow every other
phase edits, and it must not change what a pull request tests.
Planned in detail on 2026-10-20 against `develop` at `3bd67676e`.
Branch `ci-tiers-phase-1`.

#### Scope

In: restructure as Decisions 2 and 3 describe, with every lane in
both tiers (`"tiers": ["smoke", "merge"]` throughout
`ci-lanes.json`), so pull requests test exactly what they test
today. The two load reductions that do not depend on a queue land
here too: the Debian patch tests become one sequential job and the
Rocky ones another (6 xl lanes become 2), and `check_bot_commit`
and `aggregate_results` move to static runners. `merge_group:`
joins the triggers, and is inert until Phase 3 creates a ruleset.

Out: moving any lane out of the smoke tier (Phase 3), the merge
group re-queue (Phase 2), `collect_layer_data`'s trigger (Phase 3),
any ruleset or repository setting (3d), and anything in
`shakenfist/actions`.

#### What the survey found

Every factual claim in this section's original briefs was checked
against the tree, GitHub and the reference repositories. The line
ranges it cites (`build_images` at `functional-tests.yml:112-150`,
`test_installs` at `:418-540`) are right, as are the hash
directories in `build_images`' "Build container images" step
(`kolla kolla-ansible ansible-collection-kolla etc src _build
tools`, so `.github/` is not hashed), and the repository state under
Situation (no ruleset, no branch protection, `allow_auto_merge`
false, public, all actions allowed). The kerbside reference
(kerbside#582) is still open at `cc50cb9`, and shakenfist's
`tools/ci-merge-group-current.sh` at `8a89247de0` is byte-identical
to its `develop` (blob `561390c2aa`). These were wrong or missing,
and are corrected in the briefs below and, where a Decision stated
them, in Decisions 2 and 3 above:

1. **kerbside's `check_paths` skips its checkout on
   `workflow_dispatch`.** Ours cannot: `tools/ci-select-lanes.py`
   and `tools/ci-merge-group-current.sh` both run from the tree.
2. **The superseded-run check needs `actions: write`.** Its header
   says so, and shakenfist grants it on `check_paths` alone. This
   workflow's top-level block is `contents: read`, so the job needs
   its own `permissions:` (naming one scope sets every unnamed one to
   none, so it names `contents: read` too). dorny/paths-filter needs
   no `pull-requests: read` here because the repository is public,
   which is also why kerbside's copy works under `contents: read`.
3. **In a called workflow, `github.workflow` is the caller's
   name.** `rebase-tests.yml`'s `lint_check` keys its group
   `${{ github.workflow }}-${{ github.ref }}-lint`, which is
   character for character the pre-commit `lint` job's group in
   `functional-tests.yml`. Called as is, the two would cancel each
   other. Every called job needs a suffix of its own.
4. **The lint fixer needs its token passed.** It checks out with
   `DAILY_REBASE_TOKEN`, and a called workflow sees no secrets
   unless the caller passes them. It is passed by name, as an
   optional `workflow_call` secret, because the fleet's
   reusable-workflow-secrets audit rejects `secrets: inherit`.
5. **`rebase-tests.yml` also triggers on `v*-releases` branches.**
   None exist (`git ls-remote --heads origin 'v*-releases'` is
   empty), so dropping its `pull_request` trigger loses nothing.
6. **A sequential patch-test job stops at its first failing step.**
   Today's six lanes are independent, so a kolla failure still
   reports kolla-ansible. Each project step needs `!cancelled()` in
   its `if:` to keep that.
7. **A `fromJSON` matrix of an empty list fails the job** rather
   than skipping it, so the matrix jobs must be skipped by a job
   `if:` before the matrix is expanded. Comparing JSON strings in
   `if:` is fragile, so the selector emits booleans as well.
8. **The local build was to sit only in `Can merge`'s `needs`.**
   In this phase it runs on every pull request, and a failure there
   would not have failed `Can enqueue`. It goes in both: on a pull
   request after Phase 3 it is skipped, which passes.
9. **The lint fixer cannot be left out of `needs`**, as Decision 3
   said: it runs inside the patch-test call. That is harmless, and
   Decision 3 now says why.
10. **Missing from the original briefs:** `docs/script-reference.md`
    documents every script in `tools/`, so both new scripts and
    their tests go in it; the `flake8` hook in
    `.pre-commit-config.yaml` lists the Python it lints by name, so
    the new Python must be added; the `docs-line-references` hook
    rejects `file.yml:123` references anywhere in `docs/` except
    `docs/plans/`, so `docs/ci.md` names jobs and steps, not lines;
    `.claude/skills/debug-ci/SKILL.md` lists "Rebase tests" and
    "Ensure local container builds work" as workflows to look in,
    which stops being true; and shakenfist carries a test for the
    cancel script (`shakenfist/tests/test_merge_group_current.py`),
    which is worth porting with it.
11. **`SF_HEAD_SHA` in `local-container-builds.yml` has no
    consumer.** Nothing in that workflow reads it (its only reader
    is `shakenfist/actions/tools/clone_with_depends.py`, which this
    workflow does not run). kerbside's fallback,
    `github.event.pull_request.head.sha || github.sha`, keeps the
    pull request value and fills the rest. `daily-rebase-checks.yml`
    has the same empty-on-dispatch pattern; it is out of scope here.
12. **The local `../development` clone is 23 commits behind
    `origin/main`**, so the fleet audits must run from an up-to-date
    checkout of it, not the clone as it stands.
13. **client-python-k3s's `develop` does not yet carry the
    `run_attempt` concurrency rule.** The aggregators are copied
    from it; the concurrency key is Decision 3's, not k3s's.

Nothing else in the section was wrong.

#### Decisions

1. **The master plan lands with this phase.** It was never
   committed, so the branch carries two commits before any work: the
   plan as written (in place of a previous phase's close-out, since
   there is none), then this section and the corrections above. The
   containerised deployer landed its master plan as a pull request
   of its own (#1788) first. Doing that here would buy a review of
   the plan alone at the price of a round trip, and the plan's
   phases are only meaningful once Phase 1 is visible. If a separate
   review is wanted, the first commit pushes on its own.
2. **Strict parity on pull requests.** Every job that is skipped on
   a docs-only or data-only change today is skipped by `check_paths`
   instead, pre-commit included, although linting docs would be
   cheap. Phase 1's promise is "same tests"; linting docs-only pull
   requests is in Future work.
3. **Sequential patch tests keep going after a failure.** Each
   project is its own step with `if: ${{ !cancelled() &&
   contains(matrix.test.projects, '<project>') }}`, so the step list
   says which projects failed, as the six lanes did. The job's time
   is the sum (about 95 minutes on Debian, 70 on Rocky), still
   shorter than the build-then-deploy path beside it.
4. **The selector emits a `run_<name>` boolean beside each matrix**
   and the jobs gate on those. It also validates its own input: a
   selected install whose release and distribution have no selected
   build, or two lanes with the same `name`, is an error. A test
   runs the real `ci-lanes.json` through every event and tier, so an
   edit that breaks a tier fails in pre-commit, not after eight
   hours of CI.
5. **Reusable jobs key on a prefix of their own and on
   `inputs.tier`**: `patch-lint`, `patch-lint-fixer`,
   `patch-tests-<lane>` and `local-build-<release>`. That satisfies
   `merge-group-cancellation`'s rule for reusable workflows and
   avoids finding 3.
6. **The 2026.1 entries commented out of both matrices are not
   carried into the JSON**, which has no comments. `docs/ci.md` says
   how to add a lane back, and Future work already records them.
7. **The reusable workflows are converted before their caller is
   reworked.** Every commit then passes actionlint. The commits in
   between leave the patch tests triggered by nothing, which only
   matters if someone pushes the branch half done; the pull request
   is tested at its tip.
8. **The cancel script comes with its tests.** It is the guard
   against a superseded queue run cancelling the live one, and
   calling a live run superseded ejects a pull request untested. Its
   failure directions are worth tests, not a reading.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Create `.github/ci-lanes.json` with four keys: `builds`, `installs`, `patch_tests` (lists) and `local_build` (an object). Copy every field of the active matrix entries verbatim from `.github/workflows/functional-tests.yml` (`build_images`' three entries and `test_installs`' eight; drop the commented-out 2026.1 entries) and add `"tiers": ["smoke", "merge"]` to each. `patch_tests` is two entries replacing `rebase-tests.yml`'s six `functional_matrix` lanes: `{"name": "debian-13", "description": "debian 13", "runs_on": "debian-13", "projects": ["kolla", "kolla-ansible", "kolla-deployer", "kolla-ansible-deployer"], "tiers": [...]}` and the same for `rocky-9` with `["kolla", "kolla-ansible"]`. `local_build` is `{"tiers": ["smoke", "merge"]}`. Write `tools/ci-select-lanes.py`: Python 3, stdlib only, single-quoted strings, double-quoted docstrings, 120 columns, executable, `#!/usr/bin/env python3`, a module docstring saying why the lanes are data (one job definition per lane kind, and `.github/` is outside the image hash). Arguments `--lanes PATH`, `--event` (`pull_request`, `merge_group`, `workflow_dispatch`), `--tier` (`smoke`/`full`/empty; only read for `workflow_dispatch`, where empty means `smoke`) and `--code-changed` (`true`/`false`). `pull_request` selects tier `smoke`, `merge_group` selects `merge`, a dispatch with `full` selects `merge`. Print, one per line in `key=value` form for `$GITHUB_OUTPUT`: `builds`, `installs`, `patch_tests`, each compact JSON `{"test": [...]}` with the `tiers` key removed from every entry, so jobs keep reading `matrix.test.*`; `run_builds`, `run_installs`, `run_patch_tests` (`true` when the list is non-empty) and `run_local_build` (`true` when `local_build`'s tiers include the selected one). With `--code-changed false`, every list is empty and every `run_*` is `false`. Exit 1 with a clear message on an unknown event or tier, on a duplicate `name` within a list, or when a selected install's (`release`, `container_distro`, `container_distro_version`) matches no selected build. Add `tools/test-ci-select-lanes.py` (unittest, stdlib only), mirroring `tools/test-write-build-manifest.py`'s layout: each event, `full` versus `smoke` versus empty tier, `--code-changed false`, the missing-build and duplicate-name errors against small fixture files, and one test that runs the real `.github/ci-lanes.json` through every event and tier and asserts it validates; in this phase it also asserts that `pull_request` selects 3 builds, 8 installs and 2 patch tests. Add a pre-commit hook `ci-select-lanes-tests` modelled on `write-build-manifest-tests`, with `files: ^(tools/(ci-select-lanes\|test-ci-select-lanes)\.py\|\.github/ci-lanes\.json)$`, and add both Python files to the `flake8` hook's `files` regex. Add both to `docs/script-reference.md` beside `write-build-manifest`. Run `pre-commit run --all-files`. Commit subject: "Describe the CI lanes in a data file." |
| 1b | medium | sonnet | none | Copy `tools/ci-merge-group-current.sh` byte for byte from shakenfist/shakenfist at `8a89247de0` (`gh api 'repos/shakenfist/shakenfist/contents/tools/ci-merge-group-current.sh?ref=8a89247de0' -q .content \| base64 -d`), keeping its header comment, and add one comment line under the copyright saying it is copied from shakenfist/shakenfist `tools/ci-merge-group-current.sh` at `8a89247de0` and should be kept in step with it. Make it executable; it must pass the repository's shellcheck hooks. Port the script half of `shakenfist/tests/test_merge_group_current.py` (same ref) to `tools/test-ci-merge-group-current.py`: unittest instead of shakenfist's `base` test case, stdlib only, the same `git` and `gh` stubs on `PATH`, every script-behaviour case it has (current, superseded and cancelled, superseded and the cancel fails, `ls-remote` fails, missing environment variables), with `CANCEL_WAIT_SECONDS` small. Leave out its workflow-shape tests; step 1e adds those. Add a pre-commit hook `ci-merge-group-current-tests` (`language: python`, `pass_filenames: false`) with `files: ^(tools/(ci-merge-group-current\.sh\|test-ci-merge-group-current\.py)\|\.github/workflows/functional-tests\.yml)$`, add the test to the `flake8` hook's `files`, and add both files to `docs/script-reference.md`. Commit subject: "Copy the superseded merge group check." |
| 1c | medium | sonnet | none | Turn `.github/workflows/rebase-tests.yml` into a reusable workflow. Replace `on:` with `workflow_call` taking string inputs `tier` (required) and `patch_tests` (required, JSON); there is no `pull_request` trigger any more (its `v*-releases` branch filter matched no branch). Keep `name: Rebase tests`, the file's comments and `permissions:`. `check_bot_commit` moves to `[self-hosted, static]`. `lint_check` stays on `[self-hosted, vm, debian-13, m]`. `automated_lint_fixer`'s `if:` gains `github.event_name == 'pull_request'` (in a called workflow that is the caller's event). Replace `functional_matrix` with a job `patch_tests` named `"Patch tests on ${{ matrix.test.description }}"`, `strategy: {fail-fast: false, matrix: ${{ fromJSON(inputs.patch_tests) }}}`, `runs-on: [self-hosted, vm, '${{ matrix.test.runs_on }}', 'xl']`, `timeout-minutes: 150`, the same checkout, pip and test-dependency steps, and then one step per project, in the order kolla, kolla-ansible, kolla-deployer, kolla-ansible-deployer, each named "Deep test patches for <project>", each running `./_build/test-apply.sh <project>` from the checkout, each with `if: ${{ !cancelled() && contains(matrix.test.projects, '<project>') }}` so a failure in one project does not hide the next. Concurrency, with `cancel-in-progress: true` throughout: inside a called workflow `github.workflow` is the caller's name ("Build test clouds"), so every group needs a suffix the caller does not use, and must include `inputs.tier`. Use `${{ github.workflow }}-patch-lint-${{ inputs.tier }}-<ref part>`, `-patch-lint-fixer-`, and `-patch-tests-${{ matrix.test.name }}-${{ inputs.tier }}-<ref part>`, where `<ref part>` is Decision 3's expression in `docs/plans/ci-tiers.md` (`github.event_name == 'merge_group' && github.run_attempt == '1' && format('merge_group-{0}', github.event.merge_group.base_ref) \|\| github.ref`). Nothing calls this workflow until step 1e, which is expected. Run `pre-commit run --all-files`. Commit subject: "Make the patch tests a reusable workflow." |
| 1d | low | sonnet | none | In `.github/workflows/local-container-builds.yml`, replace the `pull_request` trigger with `workflow_call` taking a required string input `tier`, and keep `workflow_dispatch`. Keep `name:`, comments and `permissions:`. Set `SF_HEAD_SHA` to `${{ github.event.pull_request.head.sha \|\| github.sha }}` (kerbside's fallback; the value is empty outside `pull_request` today). Add `timeout-minutes: 120` to `local_build_matrix`. Its concurrency group becomes `${{ github.workflow }}-local-build-${{ matrix.test.release }}-${{ inputs.tier }}-<ref part>` with Decision 3's ref expression from `docs/plans/ci-tiers.md`, `cancel-in-progress: true`; on a direct dispatch `inputs.tier` is empty, which is fine. Run `pre-commit run --all-files`. Commit subject: "Make the local build a reusable workflow." |
| 1e | high | opus | worktree | Rework `.github/workflows/functional-tests.yml` per Decisions 2 and 3 and this phase's survey in `docs/plans/ci-tiers.md`; read both first. Keep `name: Build test clouds` (auto-retry matches on it). (1) Triggers: drop `paths-ignore`; add `merge_group:`; add a `workflow_dispatch` choice input `tier` (`smoke`, `full`, default `smoke`) beside `data_prefix`. (2) Add a `check_paths` job (name "Check paths") on `[self-hosted, static]`, `timeout-minutes: 10`, with job-level `permissions: {contents: read, actions: write}` and a comment saying `actions: write` is for the last step. Base it on shakenfist/kerbside's `check_paths` in `.github/workflows/functional-tests.yml` at `cc50cb96d5` (fetch it with `gh api`), keeping its explanatory comments where they still apply, with three differences: checkout always runs, dispatches included; the filter is `'**'`, `'!docs/**'`, `'!data/**'`, `'!README.md'` with `predicate-quantifier: 'every'` and `base: ${{ github.event.merge_group.base_sha \|\| 'develop' }}`, and still skipped on dispatch so `code_changed` defaults to `true`; and two steps follow the verdict. First, "Select lanes" runs `tools/ci-select-lanes.py --lanes .github/ci-lanes.json --event "${{ github.event_name }}" --tier "${{ inputs.tier }}" --code-changed "<code_changed>" >> "$GITHUB_OUTPUT"`, and the job exports `code_changed` plus all eight of its outputs. Last, "Cancel this run if the merge group is superseded", `if: github.event_name == 'merge_group'`, env `GH_TOKEN: ${{ github.token }}`, `BASE_REF`, `BASE_SHA` and `RUN_ID` as shakenfist's `functional-tests.yml` at `8a89247de0` passes them, running `tools/ci-merge-group-current.sh`. (3) `lint` gains `needs: check_paths` and `if: needs.check_paths.outputs.code_changed == 'true'`. `assemble_source` likewise; its one-entry matrix stays inline. `build_images` needs `[check_paths, assemble_source]`, `if: needs.check_paths.outputs.run_builds == 'true'`, `strategy: {fail-fast: false, matrix: ${{ fromJSON(needs.check_paths.outputs.builds) }}}`. `test_installs` needs `[check_paths, build_images, aggregate_results]`, `if: needs.check_paths.outputs.run_installs == 'true'`, matrix from `installs`. Keep `test_installs`' `runs-on` line exactly as it is: the `bind-` token uses `strategy.job-index`, which stays unique only while there is one install job. (4) Every job on a `vm` or `bound-` runner takes Decision 3's concurrency key, `${{ github.workflow }}-<suffix>-<matrix lane if any>-<ref part>`, keeping each job's current suffix (`lint`, `source-<release>`, the build and install `matrix.test.name`). (5) `aggregate_results` moves to `[self-hosted, static]` with `timeout-minutes: 15`. (6) Add `patch_tests` (name "Patch tests") calling `./.github/workflows/rebase-tests.yml` with `needs: check_paths`, `if: needs.check_paths.outputs.run_patch_tests == 'true'`, `with: {tier: <selected tier>, patch_tests: ${{ needs.check_paths.outputs.patch_tests }}}` and `secrets: {DAILY_REBASE_TOKEN: ${{ secrets.DAILY_REBASE_TOKEN }}}`, the token passed by name because the fleet's reusable-workflow-secrets audit rejects `secrets: inherit`; and `local_build` (name "Local build") calling `./.github/workflows/local-container-builds.yml` with `if: needs.check_paths.outputs.run_local_build == 'true'` and `tier`. The selected tier passed down is `merge_group` → `merge`, `pull_request` → `smoke`, dispatch → `inputs.tier`. (7) Add `can_enqueue` and `can_merge` exactly as shakenfist/client-python-k3s's `.github/workflows/functional-tests.yml` on `develop` writes them (`always()` and the event test, `permissions: {actions: read}`, static runner, the jq over `toJSON(needs)` that passes only `success` and `skipped`). Both need, directly, `check_paths`, `lint`, `assemble_source`, `build_images`, `aggregate_results`, `test_installs`, `patch_tests` and `local_build`. Leave `collect_layer_data` out of both, and leave its own trigger and `needs` as they are. (8) Add the workflow-shape tests to `tools/test-ci-merge-group-current.py` that shakenfist's `shakenfist/tests/test_merge_group_current.py` at `8a89247de0` has, adapted to this workflow (the cancel step is the last step of `check_paths`, `check_paths` grants `actions: write`, and every job with a `merge_group`-keyed concurrency group needs `check_paths`), and give its pre-commit hook `additional_dependencies: [pyyaml]`. Verify: `pre-commit run --all-files` (actionlint is in it); then run shakenfist/development's audits from an up-to-date checkout (`git -C ../development fetch origin`, then `git -C ../development worktree add <scratch dir> origin/main`, and `python3 <scratch dir>/scripts/audit-check.py --repo-path <this worktree> --repo-name kerbside-patches`), and report the `merge-group-cancellation` and `expensive-lane-path-filter` results from its JSON. Remove the scratch worktree afterwards. Commit subject: "Gate CI on Can enqueue and Can merge." |
| 1f | low | haiku | none | In `.github/workflows/auto-retry-infra-failures.yml`, remove `"Ensure local container builds work"` and `"Rebase tests"` from `on.workflow_run.workflows`, leaving `"Build test clouds"`: their jobs now run inside that workflow's runs. Change nothing else. Run `pre-commit run --all-files`. Commit subject: "Retry only the workflow that now runs everything." |
| 1g | medium | sonnet | none | Write `docs/ci.md`: the two tiers and which lanes sit in each (all lanes in both for now; say the merge tier is not enabled yet and Phase 3 of `docs/plans/ci-tiers.md` enables it); `check_paths` and why there is no trigger-level `paths-ignore` (required checks must always report); the lane file `.github/ci-lanes.json`, why it is under `.github/` (outside the image hash), how to add, remove or move a lane and that `tools/test-ci-select-lanes.py` checks it; `Can enqueue` and `Can merge`; `tier=full` dispatches; that "@shakenfist-bot please retest" runs the smoke tier; the concurrency key and why new jobs must use it. Name jobs and steps, never `file.yml:<line>` (the `docs-line-references` hook rejects it). Link it from `docs/index.md` and from the question table in `AGENTS.md`. In `AGENTS.md`'s "Modifying GitHub Actions Workflows", list the new jobs, and add three conventions: lanes live in `.github/ci-lanes.json`; the gated workflow never uses trigger-level `paths-ignore`; new VM jobs take the Decision 3 concurrency key, and a called workflow's suffix must not repeat its caller's. Correct its "Other workflows" entries for `rebase-tests.yml` and `local-container-builds.yml` (now called from `functional-tests.yml`). Rewrite `ARCHITECTURE.md`'s "CI/CD" section, its "Functional Tests Workflow" and "Rebase Tests Workflow" subsections, for the new shape, and fix the directory-tree comments for the two reusable workflows. In `.claude/skills/debug-ci/SKILL.md`, replace the three-workflow table with the jobs of "Build test clouds" (patch tests and the local build are now jobs there, as `Patch tests / ...` and `Local build / ...`). Run `pre-commit run --all-files`. Commit subject: "Document the CI tiers." |
| 1h | medium | opus | none | Management session, not a sub-agent: the exit checks below, after the branch is pushed with Michael's go-ahead. No commit unless a check fails. |

#### Risks

* **Two called jobs sharing a concurrency group cancel each other
  silently**, and the run looks merely cancelled. Mitigation: the
  suffixes in Decision 5, the `merge-group-cancellation` audit in
  1e, and the management session reading every rendered `group:`
  in the 1e diff before committing.
* **The bound install runners might not bind** if the conductor
  depends on anything beyond the job's resolved labels. The labels
  are unchanged, and dynamic matrices resolve before the job is
  queued, so this is not expected. The pull request's own run is the
  test: all eight installs must get a runner.
* **`check_paths` on the static pool becomes a single point of
  failure** for every job. `collect_layer_data` and auto-retry
  already depend on that pool, so this adds a dependency rather than
  creating one.
* **New files under `tools/` change the image hash**, so this pull
  request rebuilds every image once. Expected.
* **The exit checks cost two full matrix runs** (the pull request
  and the dispatch). The dispatch goes only after the pull request
  run is green, so a broken branch costs one.
* **A mistake in `if:` wiring can make every heavy job skip and
  both aggregators pass on the skips**, which looks like success.
  The exit check counts jobs and conclusions, not the aggregator's
  colour.

#### Definition of done

* `pre-commit run --all-files` passes, including
  `ci-select-lanes-tests` and `ci-merge-group-current-tests`.
* `rg -n 'paths-ignore' .github/workflows/functional-tests.yml`
  finds nothing, and `rebase-tests.yml` and
  `local-container-builds.yml` have no `pull_request:` trigger.
* Every `group:` in `rebase-tests.yml` and
  `local-container-builds.yml` starts
  `${{ github.workflow }}-patch-` or `${{ github.workflow }}-local-build-`
  and contains `inputs.tier`, so no called job can share a group with
  a caller job.
* shakenfist/development's `merge-group-cancellation` and
  `expensive-lane-path-filter` checks pass for this worktree.
* The pull request's own run, checked with the snippet below, shows
  3 builds, 8 installs, 2 patch-test jobs, 1 local build and
  `Can enqueue`, all `success`. A `tier=full` dispatch on the branch
  shows the same.
* A throwaway docs-only pull request (with Michael's go-ahead; it is
  outward-facing) shows `Check paths` and `Can enqueue` `success`,
  every other job `skipped`, and is then closed and its branch
  deleted.
* `docs/ci.md` exists, is linked from `docs/index.md` and
  `AGENTS.md`, and `rg -n 'Rebase tests|Ensure local container
  builds' AGENTS.md ARCHITECTURE.md .claude/skills docs --glob
  '!docs/plans/**'` finds no claim that either is a workflow of its
  own.

The run check, for a run id `R`:

```bash
gh run view "$R" -R shakenfist/kerbside-patches --json jobs --jq '
  .jobs as $j
  | {builds:   [$j[] | select(.name | startswith("Images for "))],
     installs: [$j[] | select(.name | test(" using .* containers$"))],
     patch:    [$j[] | select(.name | startswith("Patch tests / Patch tests on "))],
     local:    [$j[] | select(.name | startswith("Local build / "))],
     enqueue:  [$j[] | select(.name == "Can enqueue")]}
  | map_values({n: length, ok: all(.[]; .conclusion == "success")})'
```

Expected on a code run: `builds` 3, `installs` 8, `patch` 2,
`local` 1, `enqueue` 1, every `ok` true.

#### Execution notes

Steps 1a to 1g ran on 2026-10-20 (UTC), in table order, one at a time
because each runs pre-commit over the shared tree. Back brief answers:
this session may push and open the pull request; the `tier=full`
dispatch and the throwaway docs-only pull request each need their own
go-ahead when 1h reaches them.

Departures from the table, each reviewed in the management session:

* **The lint fixer's token is passed by name, not with
  `secrets: inherit`.** The fleet's reusable-workflow-secrets audit
  rejects inherit, so `rebase-tests.yml` declares `DAILY_REBASE_TOKEN`
  as an optional `workflow_call` secret and `Patch tests` passes that
  one. Decision 2, survey finding 4 and 1e's row are corrected at
  source.
* **`collect_layer_data` gained `needs.build_images.result !=
  'skipped'`** in review. Its `!cancelled()` would otherwise have run
  it on every docs-only or data-only pull request, now that the
  workflow runs on those; the 1e agent raised it.
* **1e passes the tier as `inputs.tier || 'smoke'` on a dispatch**, so
  the called workflows' required input is never empty, and its
  `Select lanes` step tees its output into the log.
* **Kept extras:** 1e's tests that both gates need every gated job
  directly and that no caller job shares a called workflow's
  concurrency prefix (dropping `local_build` from `Can merge`'s
  `needs` fails them), and the `ci-merge-group-current-tests` hook
  also running when the two called workflows change.
* **Mutation checks** on copies: the cancel script made to cancel a
  current run, or to fail where it should carry on, fails
  `tools/test-ci-merge-group-current.py`.
* **Fleet audits** (shakenfist/development `origin/main` at
  `4984807`): `merge-group-cancellation`,
  `expensive-lane-path-filter` and `reusable-workflow-secrets` pass.
  The checks that fail (`plan-phase-references` on
  `docs/containerised-deployer.md`, `renovate`,
  `ci-review-automation`) predate this phase and touch none of its
  files.

Exit checks (1h), recorded at close-out because the pull request
(#1935) merged as `6a0403851` on 2026-10-20 18:42 UTC, before the
last two had run. Both of those therefore ran against `develop` at
`291437298`, which carries the same workflow code as the branch.

| Check | Run | Result |
|-------|-----|--------|
| Pull request run | 39348729439 | builds 3, installs 8, patch 2, local 1, `Can enqueue` 1, all `success` |
| `tier=full` dispatch on `develop` | 39481681665 | the same counts, all `success`; `Can merge` skipped |
| Throwaway docs-only pull request #1944 | 39481760414 | `Check paths` and `Can enqueue` `success`, the other nine jobs `skipped`; closed and its branch deleted |

The dispatch took 24 hours of wall clock, 15 of them with its eight
installs queued for bound runners behind three earlier pull request
runs: fleet capacity, not this change. The `paths-ignore` check above
finds three comment lines in `functional-tests.yml` explaining why
the trigger has none; the trigger itself has none.

#### Back brief

Before 1a, back brief Michael on this section. Two gates on top of
that: the 1e diff is reviewed in the management session before its
commit, with every rendered concurrency group read, because a wrong
`if:` or group is cheap to write and costs an eight-hour run to
find; and nothing is pushed, and no throwaway pull request opened,
without his go-ahead.

### Phase 2: Re-queue on infrastructure failure

Planning effort: medium. The classification already exists, and the
new behaviour is narrow. Planned in detail on 2026-10-22 against
`develop` at `98b7b24c7`. Branch `ci-tiers-phase-2`.

#### Scope

In: move the inline script in `auto-retry-infra-failures.yml` to
`tools/ci-auto-retry.sh`, unchanged for pull request runs, with
tests; then teach it the `merge_group` case from Decision 4: re-queue
an entry ejected on infrastructure, once per tested head; comment on
any other ejection; and comment when a merge group passed after its
entry had already been ejected, which is what the 360-minute check
timeout looks like. Document it in `docs/ci.md`.

Out: any ruleset or repository setting (3d); a model-driven triage
verdict, which the fleet's shared triager will bring (finding 4);
retrying `workflow_dispatch` runs, which the inline script skips
today and this phase still skips (Bugs fixed during this work); and
new infrastructure patterns, since the existing ones already match
(finding 2).

#### What the survey found

Every factual claim in this section's original brief was checked
against the tree, GitHub, and the fleet's own merge queue tooling.
These were wrong, missing, or have changed since the plan was
written. The ones that a Decision stated are corrected in Decision 4
and the Risks above.

1. **Phase 1 left the workflow watching "Build test clouds" alone**,
   so patch tests and the local build reach this script as jobs of
   that run (`Patch tests / ...`, `Local build / ...`). The job has
   no checkout step today, because the script is inline. A script in
   `tools/` needs one. `workflow_run` always runs the default
   branch's copy of the workflow, so the checkout is `develop`, never
   the pull request's head.
2. **The existing patterns already match the three runs the brief
   names.** In attempt 1 of 38971535327, 38820775821 and 38792591006,
   every failed job failed `Setup test environment` with sfcbr's
   `No nodes remaining at scheduling stage`. That is on line 1981 of
   2459, 1978 of 2456 and 2101 of 2579 of the job logs checked
   (117274276492, 116788589228, 116683261774). The script reads only
   the last 2000 lines, so each match is 478 lines inside that
   window. No annotation matches (they say only `Process completed
   with exit code` and an empty artifact upload), so the log fallback
   is what matches. No new
   pattern is needed.
3. **The SHA in a queue branch name is the base, not the pull
   request's head.** shakenfist's merge group run 39676235178 ran on
   `gh-readonly-queue/develop/pr-4597-3aa55390fc…`. `3aa55390fc` is
   the previous entry's merge commit. The commit it tested,
   `b3dc0c65da`, has parents `3aa55390fc` and `02d4959dd9`. The
   second is #4597's head. So "once per PR head SHA" must take the
   head from the merge group commit's second parent, not from the
   branch name. Decision 4 is corrected.
4. **The fleet triages merge failures, but nothing in it re-queues.**
   `enqueuePullRequest` appears in no shakenfist repository except
   this plan. shakenfist's `merge-failure-triage.yml` asks Claude for
   a verdict on every ejection, posts it on the pull request, and the
   conductor ledgers it. private-ci's `PLAN-fleet-merge-triage.md`
   (Not started) will move that triager into `shakenfist/actions`
   and give every repository with a `merge_group` trigger a caller,
   with an audit to enforce it. This repository has had a
   `merge_group` trigger since Phase 1. Once that lands here, it
   would duplicate the plain failure comment this phase adds. The
   re-queue is this repository's alone.
5. **A run's `pull_requests` empties once its pull request merges or
   closes.** None of the three runs in finding 2 reports one now. The
   original exit check, re-running "a real failed pull request run",
   therefore needs an open pull request's run, and running the real
   script on it would really re-run it. A `--dry-run` mode replaces
   it (Decision 6).
6. **shakenfist-bot is an admin of this repository and a member of
   `sf-can-skip-merge-queue`** (team 11722172), the bypass actor the
   ruleset will copy. `enqueuePullRequest` queues regardless. A merge
   call from the bot's token could skip the queue, so the script must
   never make one.
7. **The master plan's Risks gave 2a the timeout case**: an entry
   whose `Can merge` never reports. The 2a brief did not say so. On
   timeout the entry is ejected but its run carries on, and may well
   finish green. A workflow that fires only on `failure` would never
   see it.
8. **The repository tests scripts in Python, not bash.** Phase 1's
   `tools/test-ci-merge-group-current.py` runs a shell script with
   `gh` and `git` stubbed on `PATH`, from a pre-commit hook. `bats`
   is not installed. The brief's `tools/test-ci-auto-retry.sh` becomes
   a `.py` on that model.
9. **`set -euo pipefail` would invert the log match.** The script
   matches with `echo "$LOG_TEXT" | grep -qi "$PATTERN"`. Under
   `pipefail`, `grep -q` exits at the first match, `echo` takes
   SIGPIPE on a 2000-line log, and the pipeline returns 141, so a
   match reads as a miss. It is harmless under today's `set -e`, and
   the move must switch to here-strings.
10. **`merge-ci-triage` is a user-level skill**, in Michael's
    `~/.claude/skills`, not in this repository. A comment can name
    it, and should not link it.

Nothing else in the original brief was wrong.

#### Decisions

1. **Move first, then change.** 2a moves the script, and its tests
   prove that pull request runs behave exactly as before. 2b adds the
   merge-group path on top. The move is the part that can silently
   break today's retries, so it gets a commit and a review of its own.
2. **Re-queue only when every failed job is infrastructure.** A pull
   request run re-runs only its failed jobs, so retrying a mixed
   failure costs little. A re-queue re-runs the whole merge tier, 2.5
   to 4 hours, so a mixed failure stays ejected. `Can merge` itself is
   left out of the classification, since it fails whenever anything
   else does.
3. **Re-queue the head that was tested, and nothing else.** The
   tested head is the merge group commit's second parent (finding 3).
   The script re-queues only if the pull request is open, not already
   queued, and its `headRefOid` still equals the tested head, and it
   passes that SHA as `expectedHeadOid`, so a push in between makes
   GitHub refuse rather than queue untested code. It never sets
   `jump`. The once-only marker is
   `<!-- auto-requeue-infra: <tested head> -->`. A second
   infrastructure ejection of the same head gets a comment instead.
4. **Enqueue with `DAILY_REBASE_TOKEN`; everything else with
   `GITHUB_TOKEN`.** The bot token creates the merge group so that it
   runs workflows, as the master plan says. The comments stay on
   `GITHUB_TOKEN` as today, and the workflow's `permissions:` do not
   change. The script makes exactly one mutation with the bot token,
   `enqueuePullRequest`, and a test pins that (finding 6). The enqueue
   happens before its comment, so a failed enqueue leaves no marker
   and fails the step where Michael sees it.
5. **Keep a plain failure comment for now.** It lists the failed jobs
   and their first failed step, links the run, and names the
   `merge-ci-triage` skill and `tier=full` dispatches. Without it,
   Phase 3 would go live with ejections that say nothing until the
   fleet triager arrives, and that plan has no date. When the triager
   reaches this repository, the comment goes and the re-queue stays.
   Future work records that. This is the decision most open to
   argument: it builds something the fleet will replace. It is about
   thirty lines with no model in it, and a gap between Phase 3 and the
   triager would cost every ejection a log read.
6. **`--dry-run` instead of a live exit check.** The script does all
   its reads, then prints each write (`gh pr comment`, `gh run rerun`,
   the GraphQL enqueue) prefixed `DRY RUN:` instead of making it. The
   environment names the repository and run, so the management
   session can point it at a real open pull request's failed run, and
   at a real failed merge group in shakenfist/shakenfist, with no
   write to either.
7. **Timeout ejections are reported, not re-queued.** The workflow
   also fires on a `success` from a `merge_group` run. If the pull
   request did not merge and has a `RemovedFromMergeQueueEvent` after
   the run started, the script comments once with the run's duration
   and the event's `reason`, quoted as given, because its values are
   not documented. A re-queue would very likely time out again, so
   that call is Michael's. Phase 4 counts these comments.

#### Steps

Run in order, one at a time, in this worktree. Each step runs
pre-commit over the shared tree, and 2b builds on 2a's tests.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Read Phase 2 of `docs/plans/ci-tiers.md` first (findings 1, 8 and 9 and Decisions 1 and 6). Move the `run:` body of `.github/workflows/auto-retry-infra-failures.yml` into `tools/ci-auto-retry.sh` (`#!/bin/bash`, `set -euo pipefail`, executable, shellcheck-clean), keeping its comments, `INFRA_PATTERNS` and its comment text exactly. Behaviour on every run must be unchanged. Inputs come from the environment: `GH_TOKEN`, `REPO`, `RUN_ID`, `RUN_URL` and `WORKFLOW_NAME`. Use a `mktemp` file where it used `$RUNNER_TEMP` if that is unset. Replace every `echo "$X" \| grep -q...` with `grep -q... -- "$PATTERN" <<< "$X"`, because under `pipefail` an early `grep -q` exit makes the pipeline fail and reads as no match. Add `--dry-run`: every read happens, and every write (`gh pr comment`, `gh run rerun`) is printed as `DRY RUN: <command>` instead of run. In the workflow, add an `actions/checkout@v7` step (no `ref`, so it checks out the default branch, with a comment saying never to check out the pull request) and replace the step's body with `run: tools/ci-auto-retry.sh`, passing the five values through `env:` from `github.event.workflow_run` and `github.repository`. Leave no `${{ }}` inside `run:`. Keep `permissions:`, the trigger and the job `if:`. Add `tools/test-ci-auto-retry.py` modelled on `tools/test-ci-merge-group-current.py`: unittest, the real script run with `gh` stubbed on `PATH`, the stub answering `gh api` paths from fixture files and logging every call with its `GH_TOKEN`. Cover these cases. An infrastructure annotation re-runs with `--failed` and posts one comment carrying the run's marker. A pattern found only in the log does the same. A pattern on the first line of a 2000-line log tail matches (the SIGPIPE case). A pattern above the last 2000 lines does not. A failure with no pattern writes nothing. An existing marker writes nothing. A run with no pull request exits 0 and writes nothing. `--dry-run` writes nothing and prints both planned writes. Add a workflow-shape test (pyyaml) that the step runs the script, contains no `${{`, and that the job checks out without a `ref`. Add a pre-commit hook `ci-auto-retry-tests` (`language: python`, `additional_dependencies: [pyyaml]`, `pass_filenames: false`, `files: ^(tools/(ci-auto-retry\.sh\|test-ci-auto-retry\.py)\|\.github/workflows/auto-retry-infra-failures\.yml)$`), add the test to the `flake8` hook's `files`, and add both files to `docs/script-reference.md`. Mutation-check on a copy of the script: restoring the `echo \| grep` form, or dropping `--failed`, must fail a test. Run `pre-commit run --all-files` after `git add -N` on new files. Leave everything uncommitted. Commit subject: "Move the infrastructure retry into a script." |
| 2b | high | opus | none | Read Phase 2 of `docs/plans/ci-tiers.md` first (findings 3, 4, 6 and 7, and Decisions 2 to 7, which define this step), and Decision 4 above it. Extend `tools/ci-auto-retry.sh` and its tests for `merge_group` runs, taking `EVENT`, `CONCLUSION`, `HEAD_BRANCH` and `HEAD_SHA` from `github.event.workflow_run` through `env:`, and `REQUEUE_TOKEN` from `secrets.DAILY_REBASE_TOKEN`. Widen the job's `if:` to `conclusion == 'failure' \|\| (conclusion == 'success' && event == 'merge_group')`. Keep the pull request path exactly as 2a left it. For a merge group: parse the PR number from `^gh-readonly-queue/.+/pr-([0-9]+)-[0-9a-f]+$`, and on no match warn and exit 0. Take the tested head from `gh api repos/$REPO/commits/$HEAD_SHA --jq '.parents[1].sha'`, never from the branch name. Read the PR's `id`, `state`, `headRefOid`, `isInMergeQueue` and its last `RemovedFromMergeQueueEvent` (`createdAt`, `reason`) in one `gh api graphql` call. On `failure`, classify every failed job except `Can merge` with `INFRA_PATTERNS`, logging the verdict per job before any state check, so `--dry-run` shows it. If the PR is not open, is already queued, or its head is no longer the tested head, log why and exit 0. If every classified job is infrastructure and there is no `<!-- auto-requeue-infra: <tested head> -->` comment, enqueue: `gh api graphql` with `enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head})`, with `GH_TOKEN` set to `REQUEUE_TOKEN` for that one call only. Then comment with that marker, naming the infrastructure jobs. If `REQUEUE_TOKEN` is empty, or the enqueue fails, exit non-zero with a clear message and post no marker. Otherwise (a mixed or genuine failure, or a second infrastructure failure of the same head), comment once, with marker `<!-- auto-retry-merge-group: <run id> -->`. List each failed job with its first failed step, link the run, say whether it was the second infrastructure failure, and name the `merge-ci-triage` skill (by name, no link) and a `tier=full` dispatch for reproducing a lane. On `success`: if the PR is open, not queued, still at the tested head, and has a `RemovedFromMergeQueueEvent` after the run's `run_started_at`, comment once (the same run-id marker) saying the entry was ejected while its run was still going, which is the 360-minute check timeout. Give the run's duration and quote the event's `reason` verbatim. Otherwise exit 0. Never call `gh run rerun`, `mergePullRequest`, `gh pr merge` or `jump` on this path. Add tests for: an all-infrastructure ejection enqueues once with `expectedHeadOid` and the `REQUEUE_TOKEN`; a second ejection of the same head comments and does not enqueue; a mixed failure comments and does not enqueue; a failed `Can merge` alone does not count as genuine; a head that moved, a closed PR and an already-queued PR do nothing; an unparseable branch warns and exits 0; a missing `REQUEUE_TOKEN` exits non-zero with no marker; a green run after an ejection comments, and a green run that merged does nothing; and, over every merge-group case, the stub log never shows `run rerun`, `mergePullRequest`, `pr merge` or `jump`, and shows `REQUEUE_TOKEN` used for the enqueue alone. Extend the workflow-shape test for the new `env:` and `if:`. Mutation-check on copies: taking the head from the branch name, or dropping `expectedHeadOid`, must fail a test. Run `pre-commit run --all-files`. Leave everything uncommitted. Commit subject: "Re-queue a merge group that failed on infrastructure." |
| 2c | low | sonnet | none | Read Phase 2 of `docs/plans/ci-tiers.md`, then `tools/ci-auto-retry.sh` as 2b left it. Add a "When a merge-tier lane fails" section to `docs/ci.md`, before any section on the merge tier going live. Cover: what is re-queued automatically (an entry whose every failed job matched an infrastructure pattern, once per tested head, as shakenfist-bot); what is left for a human and gets a comment instead (a mixed or genuine failure, or a second infrastructure failure); the timeout comment and what to do about it; re-queue, not re-run, and why; the `merge-ci-triage` skill; a `tier=full` dispatch to reproduce a lane on a branch; and that the plain comment is expected to give way to the fleet's shared merge triager. Say the queue is not yet enabled. Describe behaviour and name scripts, jobs and steps, but no `file:<line>` references (the `docs-line-references` hook rejects them), and no plan phase or decision numbers. Run `pre-commit run --all-files`. Leave it uncommitted. Commit subject: "Document merge queue failure handling." |
| 2d | medium | opus | none | Management session, not a sub-agent: the exit checks below. Read-only throughout. No commit unless a check fails. |

#### Risks

* **The enqueue cannot be exercised before Phase 3.** Without a
  ruleset there are no merge groups, and `enqueuePullRequest` with
  this token has never run here. Mitigation: the dry-run against a
  real shakenfist merge group covers everything up to the write. The
  write fails the job loudly rather than quietly. Phase 3's exit and
  Phase 4's soak watch for the first real re-queue.
* **The bot can bypass the queue** (finding 6). Mitigation:
  Decision 4's single mutation, pinned by 2b's tests and by the
  definition of done's `rg`.
* **A re-queue costs a whole entry**, 2.5 to 4 hours, and goes to
  the back of the queue. Mitigation: only all-infrastructure
  ejections, once per head. Phase 4 measures how often it fires.
* **The new workflow only runs once merged.** `workflow_run` uses
  `develop`'s copy, so this pull request's own CI never runs it.
  Mitigation: the dry-run, and the first real pull request retry
  after merge, recorded at close-out.
* **The sfcbr failure sits 478 lines inside the log window.** A
  later step that logs more could push the match out of the last
  2000 lines, and the retry would stop. Mitigation: 2a's test pins
  the window. Widening it is a one-line change if Phase 4 sees
  misses.
* **Two comments per ejection once the fleet triager arrives.**
  Mitigation: the Future work entry.

#### Definition of done

* `pre-commit run --all-files` passes, including
  `ci-auto-retry-tests`.
* The workflow's retry step is `run: tools/ci-auto-retry.sh`, with
  no `${{` inside `run:`, and its `permissions:` are unchanged:
  `git diff origin/develop -- .github/workflows/auto-retry-infra-failures.yml
  | rg '^[-+] *(actions|checks|contents|pull-requests):'` finds
  nothing.
* `rg -n 'mergePullRequest|pr merge|jump|--admin' tools/ci-auto-retry.sh`
  finds nothing, and `rg -c enqueuePullRequest tools/ci-auto-retry.sh`
  is 1.
* **Pull request path, dry run.** The management session takes the
  most recent failed `Build test clouds` run whose pull request is
  still open, and the `Auto-retry infrastructure failures` run that
  handled it. It then runs
  `REPO=shakenfist/kerbside-patches RUN_ID=<run> RUN_URL=<url>
  WORKFLOW_NAME='Build test clouds' tools/ci-auto-retry.sh --dry-run`.
  The jobs it calls infrastructure, and whether it would comment and
  re-run, match that handler's log. The run and the pull request show
  no new attempt or comment afterwards.
* **Merge group path, dry run.** The same, with `EVENT=merge_group`
  and the `HEAD_BRANCH`, `HEAD_SHA` and `CONCLUSION` of a recent
  failed shakenfist/shakenfist merge group
  (`gh run list -R shakenfist/shakenfist --event merge_group --status
  failure`), and `REPO=shakenfist/shakenfist`. It prints the pull
  request number, a tested head equal to that pull request's head,
  and a per-job verdict. It then stops at the pull request state
  (most will have merged), writing nothing.
* `docs/ci.md` has the section, and `docs/script-reference.md` lists
  `tools/ci-auto-retry.sh` and its test.

#### Back brief

Before 2a, back brief Michael on this section. 2d is read-only by
design, so this phase needs no live go-ahead beyond the push and the
pull request. Ask for those in the back brief.

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
  entry ejected on timeout looks like a failure, so 2b's comment says
  when an entry was ejected while its run was still going.
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
  only while there is one install job. 1e must not split it into
  smoke and merge copies.
* **A required check that never reports blocks forever.** Every gated
  job must stay reachable on both events, and both aggregators must
  stay `always()`. Phase 1's docs-only pull request is the test.
* **Auto-merge is repository-wide.** Turning it on lets anyone with
  write access set it on any pull request. Here that is Michael and
  the bot.
* **The bot can bypass the queue.** shakenfist-bot is an admin here
  and in `sf-can-skip-merge-queue`, the bypass team the ruleset
  copies. Anything that merges with `DAILY_REBASE_TOKEN` must enqueue
  (`enqueuePullRequest`, or `gh pr merge --auto` against a queue),
  never merge directly or pass `--admin`. 3b's auto-merge is checked
  for the same.
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
* Lint docs-only pull requests. Phase 1 keeps today's behaviour, where
  pre-commit does not run on a change confined to `docs/` or `data/`,
  so the `docs-line-references` hook only runs locally on those.
* `daily-rebase-checks.yml` reads `github.event.pull_request.head.sha`
  into `SF_HEAD_SHA` as `local-container-builds.yml` did, and gets the
  same empty value outside `pull_request`.
* Drop `tools/ci-auto-retry.sh`'s plain merge-group failure comment
  once the fleet's shared merge triager (shakenfist/private-ci
  `PLAN-fleet-merge-triage.md`, phase 4) reaches this repository, and
  keep the re-queue. That triager posts a verdict on every ejection.

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
