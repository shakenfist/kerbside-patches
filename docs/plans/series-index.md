# Plan: a series index, push order and shared clones

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

Consult `AGENTS.md` for the conventions and `ARCHITECTURE.md` for
the shape of the system. `docs/patch-editing.md` (section
"Independent patch series") describes how series directories are
laid out today, and `docs/script-reference.md` lists every script
this plan touches.

## Situation

Surveyed on 2026-10-31 (AEDT) against `origin/develop` 76ca05d47.

**There is no list of series.** `develop` has 36 series directories,
11 of them for neutron. The unmerged `neutron-agent-bugs` branch adds
two more. A series is any top-level directory holding
a `config.yaml`, and eight scripts find them independently:

| Script | How it finds series | What it filters on |
|--------|---------------------|--------------------|
| `_build/assemble-source.sh:42` | `find . -maxdepth 2 -name config.yaml` | `release` |
| `_build/imagebuild.sh:27` | the same `find` | `release` |
| `_build/bump-source-shas.sh:66` | the same `find` | none |
| `_build/test-patches-for-ci.sh:37` | the same `find`, sorted | `skip_rebase` |
| `_build/repush-openstack.sh:28` | `*/config.yaml` glob | `repush` |
| `tools/gerrit-push-status.py:241` | `os.listdir` plus `ORDER` and `config.yaml` | `repush` (with `--all`) |
| `tools/check-kolla-ansible-lint.sh:34` | `kolla-ansible*/` glob | `skip_rebase` |
| `tools/fix-lint-with-claude.sh:147` | `kolla-ansible*/` glob | `skip_rebase` |

The `-maxdepth 2` in three of them is a comment-documented guard
against `src/` clones that ship their own `config.yaml`. The
`upstream-series` plan has to keep its `upstream/` tree out of all
eight.

**The push order exists only in footers and in memory.** Each
series' `Depends-On` footers record the order. The naming
convention in `docs/patch-editing.md` (`<repository>-<topic>-<NNN>`,
"where `NNN` gives the landing order") is meant to record it too,
and it was wrong this week:

- `neutron-agent-opts-import-memory-014` has to be pushed before
  `neutron-import-memory-007` and `neutron-db-api-import-memory-013`
  can name it in a `Depends-On`.
- Nothing recorded that ordering except a report in a Claude
  session.

The order is not a line either. Today's 28 footers resolve to about
20 open links between series:

- Eleven series wait on `devstack-privsep-memory-001`.
- The kolla and kolla-ansible links form a small graph of their own.
- Most neutron series have no relationship to each other.

**A patch can belong to several series.** The main `kolla` and
`kolla-ansible` series list patches that the smaller
`kolla-*` and `kolla-ansible-*` series also list. So deriving links
from footers has to map one patch to all the series that list it.

**Push state is already available.** `tools/gerrit-push-status.py`
reports, for each patch, whether it is `new`, `changed`, `rebase`,
`current`, `merged` or `abandoned`, by comparing the patch with
Gerrit's latest patchset. It keeps a cache repository under
`~/.cache/kerbside-patches/gerrit-push-status`.

**Big projects are cloned over and over.**

- Every series sets `source_sha`, and with one set,
  `apply-patches-and-test.sh:69` makes a full-depth clone and
  ignores `shallow_clone`.
- `repush-openstack.sh` runs `rm -rf src` before each series, so one
  repush makes 11 full clones of neutron.
- `bump-source-shas.sh` clones each of the 36 series and deletes the
  clone each time, in CI every day. Only 13 upstream repositories are
  involved.
- An existing clone is reused without a fetch
  (`apply-patches-and-test.sh:94-100`). Its `git fetch --unshallow`
  fetches nothing from a full clone. So a clone older than a newly
  bumped `source_sha` fails with `unable to read tree`, which
  happened on 2026-10-30.

## Mission and problem statement

Give the repository one explicit, checked list of its series. The
list records which project each series patches, which series must
be pushed before which, and what we intend to do with each one. Then
build two things on that list:

- a status command that answers "what do I push next, and what is
  waiting on what", combining the list with Gerrit's live state;
- a shared clone cache, so that applying a series costs a local
  clone rather than a network one.

Out of scope:

- **Push history.** The index does not record it. Gerrit is the
  source of truth, and copying it into a file that every worktree
  edits would go stale and cause merge conflicts. This was decided
  on 2026-10-31.
- **The `upstream/` series** (qemu, Linux). The `upstream-series`
  plan keeps them out of the OpenStack tooling on purpose.
- **Changes to how series are built into images.** `release` stays
  in `config.yaml` and keeps its meaning.

## Decisions

1. **The index is `series.yaml` at the repository root.**
   - It holds one entry per series, grouped by project and sorted by
     name within each project.
   - It is not called `index.yml`, because `docs/plans/index.md`
     already exists and the two would be easy to confuse.
   - Example:

     ```yaml
     neutron:
       neutron-agent-opts-import-memory-014:
         status: push
       neutron-import-memory-007:
         status: push
         after: [devstack-privsep-memory-001, neutron-agent-opts-import-memory-014]
         note: Rodolfo CR-1 on 1012183; reply drafted in patch235's .md file.
     kolla:
       kolla:
         status: carry
     ```

2. **`config.yaml` stays in each series directory**, but only for
   building: `repo`, the branches, the shas, `directory`,
   `shallow_clone`, `release`, `skip_rebase`, `skip_claude`, `wip`
   and `depends_on`.
   - Membership, project grouping, ordering and intent move to the
     index.
   - A series' build settings stay next to its `ORDER` file, where
     `test-apply.sh` and the daily rebase bot already read and write
     them. So the bot never touches `series.yaml`.

3. **`status` replaces `repush`.**

   | Status | Meaning |
   |--------|---------|
   | `push` | Keep it on Gerrit. Today's `repush: true`. |
   | `hold` | Pushable, but not now. The `note` says why. |
   | `carry` | Downstream only, never pushed. Today's `repush: false`, which is `kolla`, `kolla-ansible` and `ansible-collection-kolla`. |

   - `merged` and `abandoned` are not statuses. They come from Gerrit
     (decision 5).
   - `repush:` is removed from every `config.yaml` (33 set it today) in the same
     change that moves its two readers over, so there is never a
     second copy to disagree with.

4. **The order is a list of `after` links, not position in the
   file.**
   - `after` names the series whose changes must be on Gerrit
     before this one is pushed. Where they are open changes, this
     series' patches must carry a `Depends-On` for them.
   - Tools sort the links into push order, grouping by project where
     the links allow, so that consecutive series reuse one clone.
   - A cycle is an error.
   - The `NNN` suffix stays in names, because renaming a series
     breaks history and plan references. `patch-editing.md` will say
     that it names a series and does not order it.
   - `after` is not the same as `config.yaml`'s `depends_on`.
     `depends_on` stacks another series' patches into the same tree.
     No series sets it today, but `apply-patches-and-test.sh` and
     `check-depends-on.py` support it.

5. **Everything that can be computed is computed.** `series-status`
   works out per series:
   - **To push:** what `gerrit-push-status` reports as `new`,
     `changed` or `rebase`.
   - **Merged** or **abandoned:** what Gerrit reports.
   - **Waiting on:** an `after` series that is not yet on Gerrit.
   - **Needs a footer:** an `after` series that is on Gerrit, open,
     and that no `Depends-On` in this series names. This is the
     014 case.
   - **Undeclared order:** a footer that points at another series'
     change with no matching `after` link.

   `note` is the only free text, for the next steps a person decides
   that Gerrit cannot show.

6. **A pre-commit check keeps the index and the directories in step,
   offline.** The check fails when:
   - a top-level directory has an `ORDER` and a `config.yaml` but no
     index entry, or the reverse;
   - a series is filed under a project other than its `config.yaml`
     `directory`;
   - an `after` link names an unknown series;
   - the links form a cycle;
   - a status is not in the vocabulary above;
   - any `config.yaml` still has a `repush:` key.

   Checks that need Gerrit live in `series-status` and
   `check-depends-on.py`, not in pre-commit.

7. **Shell scripts read the index through one helper,**
   `tools/series.py`. Its subcommands are:
   - `list [--project P] [--status S] [--ordered]`, printing one
     series per line;
   - `after SERIES`;
   - `project SERIES`.

   Every one of the eight discovery sites above calls it rather than
   running `find`, a glob or `yq` over `series.yaml`. The
   `-maxdepth 2` guards and their comments go away with the `find`.

8. **The clone cache is a bare mirror per upstream repository,
   outside the worktree.**
   - It lives at `~/.cache/kerbside-patches/mirrors/<host>/<path>.git`,
     next to `gerrit-push-status`'s cache, so that all worktrees
     share it.
   - A per-worktree cache would multiply the neutron clone by six on
     this machine.
   - A shared helper takes `flock` on the mirror, fetches it once per
     process, and makes `src/<directory>` a local clone from it.
   - It then points `origin` back at the upstream URL, so that
     `git review` and later fetches behave as before.
   - The reuse path in `apply-patches-and-test.sh` fetches from the
     mirror before checking out, which fixes the stale-clone failure.
   - In CI the cache starts empty, so each job pays for one clone per
     repository rather than one per series. `bump-source-shas.sh`
     drops from 36 clones to 13.
   - Keeping the cache between CI runs with `actions/cache` is future
     work.

9. **The initial `series.yaml` is generated, then reviewed.**
   - A one-off script makes one entry per series. It sets `status`
     from `repush`, and derives `after` links from today's footers,
     mapping each patch to every series that lists it.
   - Links that exist only in memory today are added by hand from
     the plans, for example 007 and 013 coming after 014.
   - The script is not kept. The index is the source from then on.

## Open questions

Each one has the default this plan will take if nobody answers.

- **Retiring a series** once all its changes have merged. By
  default, `series-status` reports it as "all merged, retire" and
  nothing is removed automatically. Retiring means deleting the
  directory and its index entry in one commit, by hand.
- **Merge conflicts** on `series.yaml` from parallel worktrees.
  - Two worktrees adding series to the same project both edit the
    same block. The conflict is trivial (keep both entries), and
    sorting by name makes it rarer.
  - By default nothing more is done. If it becomes a nuisance,
    `heal-data-prs.yml` already shows how to union-merge.
- **Whether `series-status` replaces `gerrit-push-status --all`.**
  By default it calls into `gerrit-push-status` as a library, and
  `--all` stays as a thin alias.

## Execution

| Phase | What | Status | Merged |
|-------|------|--------|--------|
| 1. The index | `series.yaml`, `tools/series.py`, the pre-commit check, switching the eight discovery sites over, removing `repush:` | Not started | |
| 2. Push order and status | `tools/series-status.py`, and making `repush-openstack.sh` follow the index's order and offer only what needs pushing | Not started | |
| 3. Shared clones | the mirror helper, used by `apply-patches-and-test.sh`, `bump-source-shas.sh`, `check-agent-startup.py` and `gerrit-push-status.py` | Not started | |
| 4. Push audit | the checklist below, over all three phases | Not started | |

The phases depend on each other as follows:

- Phase 2 needs phase 1.
- Phase 3 needs neither, but goes last, because
  `tools/check-agent-startup.py` is still on the unmerged
  `neutron-agent-bugs` branch. If that branch merges first, phase 3
  can run at any point.
- Phase 1 rewrites the scripts that the `ci-tiers` plan's workflows
  call. It changes only how they find series, not their interfaces,
  so the two plans should not collide. Check before starting.

### Phase 1: the index

Planning effort: high. This phase changes scripts that CI runs
(`assemble-source.sh` in `functional-tests.yml` and
`local-container-builds.yml`; `bump-source-shas.sh` and
`test-patches-for-ci.sh` in `daily-rebase-checks.yml`).

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Write `tools/series.py` (decision 7) and `tools/check-series-index.py` (decision 6), stdlib plus PyYAML as the other tools use, with a test file in the style of `tools/test-gerrit-push-status.py` covering each failure the check reports. Register the check and the test in `.pre-commit-config.yaml`, triggered on `series.yaml`, `*/config.yaml` and `*/ORDER`. Sorting into push order is a topological sort that breaks ties by project, then by name, so the output is deterministic. |
| 1b | medium | opus | none | Generate `series.yaml` as decision 9 describes. The one-off script goes in the step's scratch directory, not the repository. Map each patch to every series whose `ORDER` lists it, commented-out lines included. Resolve each `Depends-On` number to a Change-Id through `https://review.opendev.org/changes/N`, and skip merged and abandoned targets. Add the links the footers do not yet hold (007 and 013 after 014), and report every link added by hand. Remove `repush:` from every `config.yaml`. `check-series-index.py` must pass. |
| 1c | high | sonnet | none | Switch the eight discovery sites in the Situation table to `tools/series.py list`, keeping each script's own filters (`release`, `skip_rebase`). `repush-openstack.sh` and `gerrit-push-status.py --all` use `--status push`. Remove the `-maxdepth 2` comments along with the `find`. Before and after, record each script's discovered set (running only the discovery part, with nothing cloned) and show the sets are identical. `test-gerrit-push-status.py` must still pass. |
| 1d | medium | sonnet | none | Update the docs. In `docs/patch-editing.md`, a new series is added to `series.yaml`, `repush` becomes `status`, and `NNN` is a name, not an order. Also update `docs/script-reference.md`, and AGENTS.md's "Adding a New Patch" step that creates a series directory. In ARCHITECTURE.md, mention that the index exists, in one line. |

### Phase 2: push order and status

Planning effort: high.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Write `tools/series-status.py [SERIES...]`, default all `push` and `hold` series. Print one table in push order, with columns series, project, status, Gerrit state (from `gerrit-push-status` as a library: counts of new, changed, rebase, current and merged), and next action, which is one of: push, waiting on X, add Depends-On for X, undeclared order, retire, or nothing. Finish with the `note`. Exit status: 0 when nothing needs doing, 1 when something does, 2 when Gerrit is unreachable, matching `gerrit-push-status.py`. Test it against a fixture Gerrit as `test-gerrit-push-status.py` does. |
| 2b | high | opus | none | Make `repush-openstack.sh` take its series from `series.py list --status push --ordered`. Before prompting, call `series-status` and offer only the series whose next action is push, listing the others with their reason. A series whose `after` dependency is not yet on Gerrit is listed but not offered. `--all` keeps today's behaviour of offering every `push` series. Keep the per-series prompt and the agent start-up check. |
| 2c | medium | sonnet | none | Document `series-status` in `docs/script-reference.md` and in `docs/patch-editing.md`'s Gerrit section. Explain how to read its next-action column. |

### Phase 3: shared clones

Planning effort: high. It changes how every build gets its source,
including in CI.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | worktree | Write the mirror helper (decision 8) as a shell function in `_build/common.sh` and a Python function in `tools/` with the same behaviour. Its inputs are the upstream URL and the destination; it outputs a full clone with `origin` set to the upstream URL. Take `flock` on `<mirror>.lock`. Fetch at most once per process, tracked with an environment variable holding the fetched mirrors. Fall back to a direct clone if the cache directory cannot be created. Test it with two concurrent callers, a missing mirror, a stale mirror and a sha newer than the mirror. |
| 3b | high | opus | worktree | Use the helper in `apply-patches-and-test.sh` for both the clone and the reuse path, and keep the commit-msg hook install. Use it in `bump-source-shas.sh` and `check-agent-startup.py`, and point `gerrit-push-status.py`'s cache at the mirrors where its layout allows. Show by timing a repush dry run (apply only) of the neutron series, before and after. Show that `_build/test-apply.sh --skip-tests` succeeds for every series. |
| 3c | medium | sonnet | none | Document the cache: where it lives, how to clear it, and that it is safe to delete. Put it in `docs/building.md` and `docs/script-reference.md`. |

### Phase 4: push audit

This repository has no `PUSH-AUDIT.md`, so this phase applies the
template's floor checklist to the recorded ranges. It adds:

- a `/code-review` of the ranges;
- `_build/test-apply.sh --skip-tests` for every series in the index;
- a read of every doc page the plan changed;
- a grep across this repository, kerbside and `shakenfist/actions`
  for any reader of `repush:` or of the removed `find` that was
  missed.

## Risks and mitigations

- **A discovery site silently finds a different set of series,**
  for example dropping one from image builds.
  - Step 1c records each script's discovered set before and after,
    and the two must be identical.
  - The management session re-runs that comparison before
    committing.
- **The daily rebase bot and `series.yaml`.** The bot edits
  `config.yaml` and patches, never the index, so it cannot conflict
  with it. A series the bot cannot see because it is missing from
  the index fails the pre-commit check, so it fails CI rather than
  disappearing.
- **The mirror goes wrong in a way that is hard to spot,** for
  example `origin` left pointing at the mirror, so that `git review`
  pushes to the wrong place.
  - Step 3a's tests assert the remote URL.
  - Step 3b runs `git review --dry-run` style checks on one series,
    and pushes nothing.
- **Generated `after` links are wrong** when one patch is in several
  series. Step 1b reports every link it derived and every one added
  by hand, and Mikal reviews that list before the commit.

## Definition of done

- `tools/check-series-index.py` passes, and removing any entry
  from `series.yaml` makes it fail.
- `grep -rn -E 'find \. -maxdepth 2.*config\.yaml|\*/config\.yaml|repush'`
  over `_build/` and `tools/` matches nothing outside the index
  tools and their tests.
- Each of the eight scripts discovers exactly the set it discovered
  before phase 1, shown by step 1c's comparison.
- `tools/series-status.py` on the tree as of this plan lists 007
  and 013 as waiting on, or needing a footer for, 014.
- A second `_build/test-apply.sh --skip-tests` of a neutron series,
  with the mirror warm, makes no network clone. The trace shows
  only a mirror fetch.
- `pre-commit run --all-files` passes.

## Future work

- Keep the mirror cache between CI runs with `actions/cache`.
- Teach `rebase-with-claude.sh` to rebase in push order, so a series
  is rebased after the series it depends on.

## Bugs fixed during this work

- The reuse path in `apply-patches-and-test.sh` checks out a
  `source_sha` without fetching, so a clone older than the sha
  fails (phase 3).

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
