# Continuous integration

One workflow, `Build test clouds` (`.github/workflows/functional-tests.yml`),
carries every result that decides whether a change may land. The patch
tests and the local container build are reusable workflows that it
calls, so their results sit in the same run as the image builds and the
test clouds, and two jobs, `Can enqueue` and `Can merge`, summarise the
lot. The rationale, and what is still to come, is in
[the CI tiers plan](plans/ci-tiers.md); this document describes what
the workflows do now.

## The two tiers

Every lane belongs to one or more tiers:

* **Smoke** runs on every pull request. It is meant to give a first
  answer quickly.
* **Merge** is meant to run on a merge queue entry, on the commit that
  will actually land rather than the pull request's head.

Today every lane is in both tiers, so a pull request tests exactly what
it always did. The merge tier is wired up (the workflow has a
`merge_group` trigger and a `Can merge` job) but the repository has no
merge queue yet, so nothing runs it on its own. When the queue is
enabled the lanes that are too expensive for every push will move from
smoke to merge only; the change will be an edit to the lane file
described below, not to the workflow.

The lanes are:

| Kind | Lanes |
|------|-------|
| Lint | The pre-commit run, and the `Ansible lint check` inside the patch tests |
| Patch tests | One job on Debian 13 (kolla, kolla-ansible and both deployer series) and one on Rocky 9 (kolla and kolla-ansible) |
| Image builds | Debian trixie, Ubuntu noble and Rocky 10 images of master |
| Test clouds | Eight installs of master: Debian and Ubuntu images on a Debian 13 host, Debian and Rocky images on a Rocky 10 host, the containerised deployer on each host OS, an install without Kerbside, and a multinode install |
| Local build | `Local build` builds every image the way a user would on their own machine, without the CI registry |

`collect_layer_data` is not a lane and is not in either tier. It runs
on pull requests only, and is skipped when the path filter skipped the
builds; see [CI data collection and reporting](ci-data.md).

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
* `Collect layer data and create PR` runs after the builds on pull
  requests.
* `Can enqueue` and `Can merge` gate on all of the above.

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

* `builds`: image builds, one entry per release and container distro.
* `installs`: test clouds. Each names the build it needs by release,
  `container_distro` and `container_distro_version`.
* `patch_tests`: one entry per host OS, with the list of `projects`
  it tests.
* `local_build`: a single object, since the local build is one job.

Every entry carries `"tiers": ["smoke", "merge"]` or a subset. The
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
  requests and dispatches. On a dispatch it reports on the head
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

Nothing requires either check yet, and the merge queue is not
enabled. Once it is, an administrator who bypasses the queue to merge
directly skips the merge tier entirely.

## Running the full tier by hand

A dispatch of `Build test clouds` takes a `tier` input, `smoke` (the
default) or `full`. `full` selects the lanes of the merge tier. Use it
to reproduce a merge-tier failure on a branch before re-queueing:

```
gh workflow run functional-tests.yml --ref my-branch -f tier=full
```

Today the two tiers select the same lanes, so the two are equivalent.

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
a dispatch on `develop`, which shares its `github.ref`.

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

## The lint fixer's token

`Automated lint fixer` pushes to the pull request's branch with
`DAILY_REBASE_TOKEN`, so that the push triggers CI. A called workflow
sees no secrets unless the caller passes them. `Patch tests` passes
that one by name, and `rebase-tests.yml` declares it as an optional
`workflow_call` secret. Do not change this to `secrets: inherit`: the
fleet's reusable-workflow-secrets audit rejects it, and it would give
the called workflow every secret.

<!-- Throwaway docs-only CI check; do not merge. -->
