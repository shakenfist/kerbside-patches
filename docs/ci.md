# Continuous integration

One workflow, `Build test clouds` (`.github/workflows/functional-tests.yml`),
carries every result that decides whether a change may land. The patch
tests and the local container build are reusable workflows that it
calls, so their results sit in the same run as the image builds and the
test clouds, and two jobs, `Can enqueue` and `Can merge`, summarise the
lot. The rationale is in [the CI tiers plan](plans/ci-tiers.md); this
document describes what the workflows do.

## The tiers

Every lane belongs to one or more tiers:

* **Smoke** runs on every pull request, and on every push to one. It
  answers "do the patches apply and pass their own tests, and do the
  Kerbside images build?" quickly, and runs no test cloud.
* **Merge** runs on a merge queue entry, on the commit that will
  actually land rather than the pull request's head. It builds every
  distribution's images and stands up the two Debian 13 clouds that
  matter most.
* **Weekly** runs once a week on `develop`, and holds every lane,
  including the six test clouds that no other tier runs.

A pull request is tested twice: its head on the smoke tier, then the
commit that would land, on the merge tier, when it goes through the
merge queue. Each tier is a superset of the one before it, so a lane
that ran on the pull request runs again on the merged tree, and the
weekly run is the whole matrix. The one lane outside that rule is the
smoke tier's Kerbside-only build, which the merge tier's full Debian
trixie build covers. Which lanes are in which tier is set in the lane
file described below, not in the workflow.

| Kind | Smoke | Merge | Weekly |
|------|-------|-------|--------|
| Lint | The pre-commit run, and the `Ansible lint check` inside the patch tests | The same | The same |
| Patch tests | Debian 13 (kolla, kolla-ansible and both deployer series) | The same | Adds Rocky 9 (kolla and kolla-ansible) |
| Image builds | Debian trixie, Kerbside images and their parents only | Debian trixie, Ubuntu noble and Rocky 10, the full CI selection | The same |
| Test clouds | None | Debian images on a Debian 13 host: the all-in-one install and the containerised deployer | All eight: adds Ubuntu images on a Debian 13 host, an install without Kerbside, a multinode install, and Debian images, Rocky images and the containerised deployer on a Rocky 10 host |
| Local build | Not run | `Local build` builds every image the way a user would on their own machine, without the CI registry | The same |

The split follows what each check costs and where its failure lands:

* **Test clouds** are the expensive part. Each holds an sfcbr cloud
  claim, the scarcest thing CI uses, for about an hour, and is where
  sfcbr flakes show. Smoke runs on every push, so it runs none; a
  change that breaks a deployment is found in the queue instead. Use a
  `tier=full` dispatch before queueing a change you expect to be risky.
  The merge queue is serial, so every cloud it runs is held by every
  entry behind it and is one more lane a flake can eject the entry
  from; it runs two. The multinode install, at 75 to 85 minutes the
  longest lane and the one most often starved of a runner, is weekly,
  which also takes it off the queue's serial path.
* **Image builds** for every distribution stay in the merge tier. They
  run in parallel on `xl` runners rather than cloud claims, rarely
  flake, and the trixie build is on the serial path anyway, so the
  other two add almost no time. They are also what catches a daily
  rebase that breaks one distribution's packages (#1973).
* **Patch tests** on Debian 13 run in every tier; Rocky 9's are
  weekly.

The trade is that a change which breaks only a weekly lane lands, and
the weekly run finds it; see "The weekly run" below.

`collect_layer_data` is not a lane and is not in any tier. It runs
on merge groups only, and is skipped when the path filter skipped the
builds; see [CI data collection and reporting](ci-data.md). A
docs-only or data-only change skips every lane in the smoke and merge
tiers. The weekly run has no change to filter, and always runs every
lane.

## What runs when

The jobs of `Build test clouds`, in dependency order:

* `Check paths` decides what runs. Everything else needs it.
* `Lint shell scripts and workflows` runs pre-commit on a VM, and
  `Assemble source for master` builds the patched source tree. Both
  skip when only documentation or data changed.
* `Images for ...` builds the images, one job per selected build lane,
  followed by `Aggregate results`, which combines the build results.
* `... using ... containers` installs and tests a cloud, one job per
  selected install lane.
* `Patch tests` calls `rebase-tests.yml`. Inside it, `Ansible lint
  check` applies the patches and runs `tox -elinters`; `Patch tests on
  ...` runs `_build/test-apply.sh` for each project of a lane, as
  steps of one job, so a failing project does not hide the result of
  the next; and `Automated lint fixer` runs Claude Code to repair
  lint failures, on pull requests only and never when the last commit
  was the bot's (`Check bot commit` guards that).
* `Local build` calls `local-container-builds.yml`.
* `Collect layer data and create PR` runs after the builds on merge
  groups. It is not part of either gate.
* `Can enqueue` and `Can merge` gate on all of the above.
* `Report weekly failures` runs on the weekly schedule only, when
  `Can enqueue` failed.

`Patch tests` and `Local build` are jobs of this workflow. Their jobs
show in a run as `Patch tests / Ansible lint check`,
`Local build / Ensure local builds for master work` and so on, and
neither workflow has runs of its own on pull requests.
`local-container-builds.yml` can still be dispatched by hand from the
Actions tab, which does start a run of its own.

## Check paths

`Check paths` runs on a static runner and does three things.

It decides whether any code changed. A change that touches only
`docs/`, `data/` or `README.md` skips every heavy job. This is a job
rather than a trigger-level `paths-ignore` because a required status
check in a workflow that `paths-ignore` filters out never reports, and
a required check that never reports blocks the merge forever. With a
job doing the filtering the workflow always runs, the heavy jobs skip,
and the two gate jobs pass on the skips. Do not add `paths-ignore` (or
`paths`) to the triggers of `functional-tests.yml`. On a merge queue
entry the filter compares against the base commit recorded in the
event, not against `develop` as it is now, so re-running an old queue
run cannot pass on skips having tested nothing. A manual dispatch
skips the filter and runs everything its tier selects.

It selects the lanes. `Select lanes` runs `tools/ci-select-lanes.py`
over `.github/ci-lanes.json` for the event, the tier and the verdict,
and the build, install and patch test jobs read their matrices from its
outputs. Each matrix has a matching `run_` flag that the job tests in
its `if:`, because a job whose matrix expands to nothing fails rather
than skips.

It cancels a superseded queue run. On a merge group, the last step,
`Cancel this run if the merge group is superseded`, runs
`tools/ci-merge-group-current.sh`. If GitHub has rebuilt the queue
entry onto a newer base, this run is stale and removes itself before
its jobs take their concurrency groups; the script explains why that
ordering matters. This is the one place the job needs `actions: write`,
and that is why the job grants its own permissions.

## The lane file

`.github/ci-lanes.json` lists the lanes and the tiers each runs in:

* `builds`: image builds, one entry per release and container distro
  in each tier. A build may carry `images`, a kolla-build regexp that
  narrows what it builds, as the smoke tier's Kerbside-only build does;
  without it a build takes the workflow's default CI selection. A
  narrowed build gets an image tag of its own (the content hash plus a
  hash of the regexp), so it never shares a tag, or a build marker,
  with a full build of the same tree. The build marker would already
  stop a full build reusing a subset; the separate tag also stops the
  two from displacing each other's marker and re-pushing shared images
  under a tag a test cloud is pulling.
* `installs`: test clouds. Each names the build it needs by release,
  `container_distro` and `container_distro_version`.
* `patch_tests`: one entry per host OS, with the list of `projects`
  it tests.
* `local_build`: a single object, since the local build is one job.

Every entry carries `"tiers": ["smoke", "merge", "weekly"]` or a subset. The
selector strips `tiers` before handing an entry to the workflow, so
the jobs see only the fields they use.

The file lives under `.github/` rather than `etc/` or `tools/`
because `_build/calculate-container-hash.sh` hashes `etc`, `_build`
and `tools` into the image tag. Moving a lane from one tier to another
should not rebuild every image.

To change the lanes, edit the file and nothing else:

* **Move a lane** by editing its `tiers`.
* **Add a lane** by copying a neighbouring entry. A name must be
  unique within its kind, and an install must have a selected build
  with the same release and distro in every tier it runs in; the
  selector rejects anything else. The `name` of a build or install is
  also part of its concurrency group and its artifact names. A lane
  that has been commented out of a workflow in the past (the 2026.1
  releases) is added back the same way.
* **Remove a lane** by deleting its entry.

`tools/test-ci-select-lanes.py` checks the selection rules against
small fixtures, and also runs the real lane file through every event
and tier, so an edit that breaks a tier fails in pre-commit rather than
in a run. Run `pre-commit run --all-files` after editing.

## Can enqueue and Can merge

These two jobs are the only checks a ruleset needs to require. Each
needs every gated job directly, and each passes only if every one of
them succeeded or was skipped:

* `Can enqueue` runs on every event except `merge_group`: pull
  requests, the weekly schedule and dispatches. On a dispatch it
  reports on the head
  commit, which makes a `tier=full` dispatch a way of asking "would
  this pass the merge tier?" before queueing.
* `Can merge` runs on `merge_group` only.

Each is skipped for the other kind of event, and GitHub treats a
skipped required check as satisfied. They run under `always()` rather
than `!cancelled()`, so a cancelled run fails the gate instead of
skipping it, and a job that was skipped only because something it
needed failed cannot hide that failure, which is why they list the
jobs directly rather than relying on one another. `collect_layer_data`
is in neither list: it publishes data rather than testing anything.
`Aggregate results` is in both, as the builds' summary.

The ruleset on `develop` requires both, and sends every merge through
the merge queue. The ruleset's bypass team lets an administrator merge
directly; a bypass merge skips the merge tier entirely, so it is for
emergencies, not for getting round a slow queue.

## Landing a pull request

A pull request lands in two steps. Its smoke run must pass
`Can enqueue`; then it is added to the merge queue, which builds the
merged tree and runs the merge tier on it. When `Can merge` passes,
GitHub merges it.

To enqueue, use the merge button on the pull request, or from the
command line:

```
gh pr merge <N> --auto
```

With a queue, `--auto` means "enqueue once the required checks
pass". Never pass `--admin`: it merges directly, skipping the merge
tier. Automation follows the same rule, and enqueues rather than
merges (see the data pull requests section below).

Expect roughly the following. These were measured over the queue's
first days (2026-10-23 to 10-27), and are not guarantees:

* A code entry takes a little over two hours when sfcbr is quiet:
  `Assemble source for master` (about 10 minutes), the slowest image
  build (35 to 45 minutes) and the slowest test cloud (45 to 85
  minutes, the multinode install the longest) run one after another,
  and every other job fits alongside them.
* When sfcbr is busy, each of those stages can wait one to three hours
  for a runner, and code entries took 5 to 6.5 hours. Four of the
  first seven reached the 360-minute timeout below without failing a
  test.
* Those figures are for the eight-cloud merge tier the queue started
  with. With the multinode install moved to the weekly run, the serial
  path ends with the slower of the all-in-one and containerised
  deployer clouds (45 to 60 minutes), so a quiet entry should take
  about 1h45. Under load, an entry now needs two cloud claims rather
  than eight; how much that shortens the waits is not yet measured.
* A docs-only or data-only entry takes a few minutes, because
  `Check paths` skips every heavy job and the gate passes on the
  skips.
* Merges are serial: one entry is built at a time, and entries queued
  behind it wait for it, so a busy day adds the waits together.
* An entry whose `Can merge` has not reported within 360 minutes is
  removed from the queue. See "When a merge-tier lane fails" for what
  happens next.

If you need to know whether a change would pass the merge tier before
spending a place in the queue, dispatch `tier=full` on the branch, as
described below.

## Data pull requests and the queue

The automated pull requests that add layer data and CI reports go
through the queue like any other. `tools/create-data-pr.sh` calls
`tools/data-pr-auto-merge.sh` on every path where the pull request
exists, which runs `gh pr merge <N> --auto`, and only when all of
these hold:

* `develop` has a merge queue rule, because without one `--auto`
  merges directly the moment the pull request is mergeable;
* the pull request is open and its base is `develop`;
* every file it changes, across all pages of the file list, is under
  `data/`, and it changes at least one.

The helper passes neither `--admin` nor a merge strategy, although
shakenfist-bot could bypass the queue. Every refusal, and a failed
merge, is a warning rather than an error: the data is safe in the
pull request, and a human can enqueue it.

Once one data pull request merges, the others conflict with
`develop`, and GitHub drops a conflicted one from the queue.
`tools/heal-data-prs.sh` re-resolves those conflicts when `develop`
moves, then calls the helper again for each pull request it healed,
after returning to the ref it started on, so the pull request is
queued again.

A data pull request is a data-only entry, so it takes a few minutes in
the queue. The layer data job itself runs on merge groups: see
[CI data collection and reporting](ci-data.md).

## Running the full tier by hand

A dispatch of `Build test clouds` takes a `tier` input, `smoke` (the
default), `full` or `weekly`. `full` selects the lanes of the merge
tier. Use it to reproduce a merge-tier failure on a branch before
re-queueing:

```
gh workflow run functional-tests.yml --ref my-branch -f tier=full
```

A `full` dispatch runs the same lanes as the merge queue would, on the
branch's head instead of the merged tree, and its `Can enqueue` reports
on the head commit.

`weekly` selects every lane. Use it on a branch that touches something
distribution-specific (a Rocky or Ubuntu package list, an image
template, a host OS quirk), before queueing it, since the queue will
not run those lanes:

```
gh workflow run functional-tests.yml --ref my-branch -f tier=weekly
```

## The weekly run

A schedule (`0 19 * * 6`, Sunday morning in Sydney) runs the weekly
tier on `develop`. The path filter is skipped, since there is no base
to compare against, so every lane runs. Its `Can enqueue` reports on
`develop`'s head commit, which no ruleset reads.

Nothing else watches this run: `auto-retry-infra-failures.yml` acts
only on pull request and merge queue runs, so an infrastructure
failure is not re-run either. So when `Can enqueue` fails,
`Report weekly failures` runs `tools/ci-weekly-report.sh`, which keeps
one open issue labelled `weekly-ci-failure`. The first failure opens
it, listing the failed lanes; each later failure while it is open adds
a comment. Close it by hand once a weekly run, or a `tier=weekly`
dispatch on `develop`, passes. A failure there means some change
since the last passing weekly run broke a lane the queue does not run;
`merge-ci-triage` and the lane's log are the place to start, the same
as for a merge-tier failure.

A scheduled run's `github.ref` is `develop`, so it shares concurrency
groups with a dispatch on `develop`; whichever starts second cancels
the other's jobs. Avoid dispatching on `develop` on Sunday mornings.

Commenting "@shakenfist-bot please retest" on a pull request
dispatches the workflow with its defaults, which is the smoke tier, so
a retest never costs more than a push does.

## Concurrency groups

Every job on a `vm` or `bound-` runner takes a concurrency group of
this shape:

```yaml
concurrency:
  group: >-
    ${{ github.workflow }}-<job>-<matrix lane if any>-${{
    github.event_name == 'merge_group' && github.run_attempt == '1'
    && format('merge_group-{0}', github.event.merge_group.base_ref)
    || github.ref }}
  cancel-in-progress: true
```

On a pull request this means a new push cancels the previous run's
jobs. On a merge group, GitHub names the queue branch afresh with a new
commit hash every time it rebuilds the entry, so keying on the ref
would put each rebuild in a group of its own and no superseded run
would ever be cancelled; the base branch is the key instead, so the
live run cancels the stale one. A re-run (attempt 2 onwards) keeps its
own queue branch so that it can neither cancel the live entry nor be
cancelled by it. The `merge_group-` prefix keeps a queue run apart from
a dispatch or the weekly run on `develop`, which share its
`github.ref`.

The cancellation only works as intended if the stale run has already
removed itself in `Check paths`, so every job with this key must
`need` `Check paths`. `tools/test-ci-merge-group-current.py` checks
that, and that `Check paths` ends with the cancel step and grants
`actions: write`.

Two rules for new jobs:

* A new VM job takes this key, with a suffix of its own.
* Inside a called workflow `github.workflow` is the caller's name, so
  a called job's suffix must not repeat one of the caller's. The called
  workflows use `patch-lint`, `patch-lint-fixer`, `patch-tests-<lane>`
  and `local-build-<release>`, each followed by the tier, and no job in
  `functional-tests.yml` may reuse those. Reusing one would put two
  jobs in the same group, and they would cancel each other.

## Automatic retries

`auto-retry-infra-failures.yml` watches `Build test clouds` for
failures and re-runs the failed jobs when the cause is the
infrastructure (a runner that lost its connection, for example) rather
than the change. Since the patch tests and the local build now run as
jobs of that workflow, it is the only workflow it needs to watch.

## When a merge-tier lane fails

`tools/ci-auto-retry.sh`, run by `auto-retry-infra-failures.yml`,
also handles `Build test clouds` runs of a `merge_group`.

A merge queue run has no pull request of its own, so re-running it
cannot land anything: GitHub has already taken the entry out of the
queue. The script therefore works on the queue entry. It finds the
pull request from the queue branch name, and the head that was tested
from the second parent of the merge group commit.

**Re-queued automatically.** If every failed job other than `Can
merge` matched an infrastructure pattern (the same list, in the same
annotations and job logs, that decides pull request retries), the
script puts the pull request back in the queue. `Can merge` is not
classified, since it fails whenever anything else does. A re-queue
runs the whole merge tier again, so a mixed failure is not re-queued.
It happens once per tested head, only while the pull request is open,
out of the queue, and still at the head that was tested, and it asks
GitHub to refuse if the head has moved, so untested code is never
queued. It never jumps the queue. The re-queue is the one thing done
with `DAILY_REBASE_TOKEN`, as shakenfist-bot, so that the new merge
group runs workflows; the comment that follows uses the workflow's own
token, like every other comment. The re-queue comes first, so one
that fails leaves no marker and the step fails.

**Left for a human, with a comment.** Any other ejection gets one
comment listing the failed jobs, the step each failed in, and which
matched an infrastructure pattern: a genuine failure, a mixed one, a
queue run with no failed job but `Can merge`, or a second
infrastructure failure of a head that was already re-queued once. If
the change is at fault, fix it on the pull request, which runs the
smoke tier again. Either way the pull request has to be added to the
queue again by hand.

**Timeouts.** A merge queue run which passed, but whose entry had
already been removed from the queue after the run started, is what
the check timeout looks like. It gets one comment giving the run's
duration and the reason GitHub recorded, quoted as given. It is not
re-queued, because a second attempt would very likely time out again;
whether to try is a human call.

Each comment carries a marker so that a repeat event for the same run
or head does nothing. To diagnose a failure, use the `merge-ci-triage`
skill. To reproduce a lane on a branch before re-queueing, dispatch
`Build test clouds` with `tier=full` as described above. The failure
comment is a stopgap: it is expected to give way to the fleet's shared
merge triager, and the re-queue will stay.

To see what the script would do against a real run without writing
anything, set `GH_TOKEN`, `REPO`, `RUN_ID`, `RUN_URL` and
`WORKFLOW_NAME` (and, for a merge queue run, `EVENT`, `CONCLUSION`,
`HEAD_BRANCH` and `HEAD_SHA`) and pass `--dry-run`: it reads
everything and prints each write as `DRY RUN: ...`. A dry run that
reaches a re-queue still checks that `REQUEUE_TOKEN` is set, so give
it any value; it is never used.

## The lint fixer's token

`Automated lint fixer` pushes to the pull request's branch with
`DAILY_REBASE_TOKEN`, so that the push triggers CI. A called workflow
sees no secrets unless the caller passes them. `Patch tests` passes
that one by name, and `rebase-tests.yml` declares it as an optional
`workflow_call` secret. Do not change this to `secrets: inherit`: the
fleet's reusable-workflow-secrets audit rejects it, and it would give
the called workflow every secret.
