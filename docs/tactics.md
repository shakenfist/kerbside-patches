# Gerrit Review Tactics for Kolla / Kolla-Ansible

This document provides tactical advice for getting your patches reviewed quickly
in the Kolla and Kolla-Ansible projects. Based on analysis of 200 recent merged
reviews using the `analyze-gerrit-review-times` tool.

## When to Post Changes

### Peak Review Hours (UTC)

| Hour (UTC) | Activity Level | Notes |
|------------|----------------|-------|
| 09:00 | **Highest** (254 activities) | European morning |
| 12:00 | Very High (195) | European lunch / Asia evening |
| 10:00 | High (187) | |
| 14:00 | High (177) | European afternoon |
| 08:00 | High (173) | European start of day |

Review activity is minimal from 22:00-04:00 UTC.

### Peak Review Days

| Day | Activity Level | Recommendation |
|-----|----------------|----------------|
| Thursday | **Highest** (582 activities) | Best day to post |
| Friday | High (413) | Good, but less time for iteration |
| Monday | High (403) | Good |
| Wednesday | Moderate (374) | Acceptable |
| Tuesday | Moderate (358) | Acceptable |
| Saturday | Low (59) | Avoid |
| Sunday | Very Low (33) | Avoid |

### Key Reviewers and Their Timezones

The Kolla/Kolla-Ansible core reviewers are distributed globally:

| Reviewer | Reviews | Peak Hour (UTC) | Likely Timezone |
|----------|---------|-----------------|-----------------|
| Michal Nasiadka | 1291 | 08:00 | Central Europe (UTC+1) |
| Maksim Malchuk | 274 | 16:00 | UTC+7 (e.g., Bangkok) |
| Bartosz Bezak | 216 | 09:00 | Central Europe |
| Dr. Jens Harbott | 111 | 19:00 | UTC+10 (e.g., Sydney) |
| Bertrand Lanson | 91 | 20:00 | UTC+11 |

Michal Nasiadka is by far the most active reviewer, accounting for ~60% of all
review activity. Posting when he starts his day (08:00-09:00 UTC) is strategic.

## Time to First Review

- **Median**: 1.6 hours
- **25th percentile**: 0.3 hours (many reviews get quick attention)
- **75th percentile**: 8.5 hours
- **Average**: 64.8 hours (skewed by some long-waiting reviews)

Most patches get initial feedback within a few hours if posted during peak
activity times.

## Optimal Posting Strategy

### By Your Timezone

| Your Timezone | Optimal Local Time | Why |
|---------------|-------------------|-----|
| US Pacific (PST/PDT) | 01:00 (night before) | Catches European morning |
| US Eastern (EST/EDT) | 04:00-05:00 | Catches European morning |
| Europe/Berlin (CET) | 09:00-10:00 | Peak activity time |
| Australia/Sydney (AEST) | 19:00-20:00 | Catches European morning next day |

### General Strategy

1. **Post on Thursday morning (European time)** - This maximizes:
   - European reviewers see it during their workday
   - Asia-Pacific reviewers see it in their afternoon/evening
   - Full workweek remaining for iteration

2. **Respond to feedback quickly** - The 1.6 hour median time to first review
   means you can often iterate same-day if you're responsive

3. **Avoid weekends** - Activity drops by ~90%, your patch will sit

4. **Friday afternoon is risky** - You may get feedback but no time to address
   it before the weekend lull

## Writing the Change Itself

Reviewer time is the scarce resource. Everything a reviewer has to read but
does not need is friction, and friction costs revision cycles. Two rules
below came out of real review feedback on Kerbside CI fixes -- both were
raised as review comments, and both cost a round trip to fix.

### Commit Messages: Two Short Paragraphs, Hard Stop

Upstream reviewers do not want a long commit message. Long messages read as
a sign that the change is doing too much, and reviewers ask for them to be
cut down.

1. **Subject**: 50 characters ideal, 72 maximum.
2. **Body**: at most **two paragraphs**.
   - Paragraph one: what is broken, and what this change does about it.
   - Paragraph two (optional): why *this* approach -- the constraint that
     ruled out the obvious alternative.
   - There is no paragraph three.
3. **Each paragraph is at most 4 lines** wrapped at 72 characters, so 8 body
   lines total.
4. **Footers do not count** toward the two paragraphs: `Closes-Bug`,
   `Change-Id`, `Depends-On`, `Signed-off-by`, `Co-Authored-By`, and the
   `Assisted-By` and `Prompt` metadata this repository adds. `Prompt` is
   the only one that runs over several lines; it ends at the next blank
   line.

Do not put these in the commit message:

- Bullet lists enumerating the hunks in the diff.
- Multi-line logs, tracebacks, or command output. A single line that
  *is* the symptom is fine.
- A "background" or "history" section.
- Links to Kerbside repositories, or Kerbside-specific motivation.

Evidence and reasoning a reviewer might want goes in a **Gerrit comment on
the change**, not in the commit message. A comment is discussable and
disposable; the commit message is permanent and is read by everyone who
runs `git log` for the next decade.

Example of the shape that works, from Michal Nasiadka -- the most active
Kolla-Ansible core reviewer, so this is the standard being applied to us
(`7dc2b7f81` in kolla-ansible):

```
CI: Install kernel-modules-extra on Rocky

Started failing on Rocky 10 with:
Jul 18 09:28:34 localhost iptables.init[857]: Warning: Extension state is not supported, missing kernel module?

Change-Id: Ib7df1eaa8a3f75fe8040fd2d646cf7d02e0322cc
Signed-off-by: Michal Nasiadka <mnasiadka@gmail.com>
```

Subject, one two-line paragraph, footers. No second paragraph, because the
change needed no justification beyond the symptom.

### Comments in the Code: Minimal Without Being Negligent

The same review feedback applies to comments in the patch itself. A comment
that restates the code is noise a reviewer has to read and then ask you to
remove.

1. **Default to no comment.** The task `name:`, the variable name, or the
   function name should carry the meaning.
2. **Comment only what the code cannot express**: a workaround for a
   specific upstream or distro bug (name it, with a bug number or URL), a
   non-obvious ordering or timing constraint, or the provenance of a magic
   value.
3. **Never restate the following line.** `# Set the timeout to 30` above
   `timeout: 30` is exactly what gets flagged.
4. **One line where one line suffices.** No comment banners, no block
   headers above a task.
5. **In Ansible, sharpen the task `name:` instead of adding a comment.** The
   name is user-visible during a deploy, so reviewers treat it as the real
   documentation. A comment is not.
6. **If the justification needs more than about two lines, it is not a
   comment** -- it is a release note or a `doc/source/` change.
7. **Nothing Kerbside-specific goes upstream**, in code or in comments.

## Common Review Feedback (Pre-emptive Checklist)

Before posting, check these common issues (see `gerrit-pre-push-lint` tool):

- [ ] **Release notes** - Add for user-visible changes (`reno new <slug>`)
- [ ] **Bug reference** - Add `Closes-Bug: #NNNN` for non-trivial fixes
- [ ] **Commit message** - Subject ≤50 chars ideal, ≤72 max
- [ ] **Commit body** - At most two paragraphs, ≤4 lines each
- [ ] **Code comments** - Only where the code cannot explain itself
- [ ] **YAML line length** - Keep under 160 characters
- [ ] **Ansible changed_when** - Use `false` for read-only commands
- [ ] **Jinja2 spacing** - `{{ variable }}` not `{{variable}}`
- [ ] **TODO comments** - Include release target: `TODO(user): Remove in 2026.1`

## Building Reviewer Relationships

Some observations from the data:

1. **Consistent reviewers** - The same core team reviews most patches. Building
   rapport with them through quality submissions helps long-term.

2. **Quick iteration** - Reviewers appreciate authors who respond promptly to
   feedback. This builds trust for future reviews.

3. **Clean submissions** - Running linters and tests before posting shows
   respect for reviewer time.

## Patch Size and Series Length

Analysis of 300 reviews reveals how patch size and series length affect reviews.

### Patch Size Impact

| Size (lines) | Count | Med. Revisions | Med. Hrs to Review | Med. Hrs to Merge |
|--------------|-------|----------------|-------------------|-------------------|
| Small (11-50) | 205 | 1 | 1.5 | 26.0 |
| Medium (51-200) | 83 | 1 | 8.4 | 24.3 |
| Large (201-500) | 8 | 51 | 1.6 | 353.6 |
| Huge (500+) | 4 | 21 | 1.9 | 1851.8 |

**Key findings:**
- **Small patches merge fastest** (26 hours median)
- **Large patches take 14x longer to merge** despite getting reviewed quickly
- **Large patches need ~50 revision cycles** vs 1 for small patches

### Series vs Standalone Patches

| Type | Count | Avg. Revisions | Med. Hrs to Review | Med. Hrs to Merge |
|------|-------|----------------|-------------------|-------------------|
| Standalone | 273 | 4.0 | 6.8 | 25.1 |
| In a series | 27 | 18.4 | 0.7 | 119.0 |

**Key findings:**
- **Series get first review 6x FASTER** (0.7 vs 6.8 hours)
- **But series take 5x LONGER to merge** (119 vs 25 hours)
- **Series need 4.5x MORE revisions** on average

### Why Series Get Reviewed Faster

Series patches get quicker initial reviews likely because:
1. Reviewers see related context and understand the bigger picture
2. Topics help reviewers find all related patches
3. Active series indicate engaged contributors

### Why Series Take Longer to Merge

Despite faster initial reviews, series take longer because:
1. **All patches must be ready** - one blocker holds up the chain
2. **Iteration cycles compound** - changes ripple through the series
3. **Reviewer attention** - harder to get multiple +2s across all patches

### Recommendations: Size and Series

1. **Keep patches small** (under 50 lines ideal)
   - 1 revision cycle vs 50+ for large patches
   - 26 hours to merge vs 350+ hours

2. **Break large changes into series** but understand the tradeoff:
   - You'll get reviewed faster
   - But merging takes longer (all patches must pass together)

3. **If you must submit a large patch:**
   - Expect many revision cycles
   - Be very responsive to feedback
   - Consider if it can be broken up

4. **Series strategy:**
   - 2-3 patches is the sweet spot
   - Longer series (5+) have higher merge times
   - Make each patch independently reviewable if possible

5. **For urgent changes:**
   - Standalone small patches merge fastest (25 hours)
   - Avoid series if time-sensitive

## New Ansible Role Additions

Analysis of 186 new role/service additions reveals different patterns than
general patches.

### The Counterintuitive Finding

For new roles, **standalone patches are MORE successful than series**:

| Approach | Count | Median Revisions | Median Days to Merge |
|----------|-------|------------------|---------------------|
| Standalone | 156 | 2 | 9.0 |
| In a series | 30 | 17 | 60.6 |

Series take **7x longer** and need **8x more revisions** for new role additions!

### Why Standalone Works Better for New Roles

1. **Reviewers can see the complete picture** - A role is a cohesive unit
2. **No coordination overhead** - Series require all patches to be ready
3. **Simpler iteration** - Fix feedback in one place, not across patches
4. **Atomic functionality** - Either the role works or it doesn't

### Successful Large Additions (Real Examples)

These 200+ line additions merged quickly as standalone patches:

| Change | Lines | Revisions | Days |
|--------|-------|-----------|------|
| rabbitmq: Add stream queues support | 201 | 2 | 5.1 |
| Add docker_image_name_prefix support | 354 | 3 | 32.9 |

### When Series DO Make Sense for Roles

Series work when patches are **genuinely independent**:

1. **Cross-project dependencies** (Kolla image + Kolla-Ansible role)
2. **Shared infrastructure** (Add base capability, then use it in role)
3. **Phased rollout** (Add disabled feature, then enable in follow-up)

Example: `kolla_uwsgi` series (16 patches) added uWSGI support to multiple
services - each service was independent, so parallel review worked.

### Recommended Approach for New Roles

1. **Submit as a single cohesive patch** (even if 200-400 lines)
2. **Follow existing role structure exactly** - Reviewers spot deviations fast
3. **Include everything in one patch:**
   - Role tasks, handlers, templates, defaults
   - Documentation updates
   - Release note
4. **Expect 3-8 revision cycles** - This is normal for new roles
5. **Be very responsive** to feedback - Quick iteration builds trust

### Pre-Submission Checklist for New Roles

- [ ] Matches structure of similar existing roles
- [ ] Comprehensive defaults in `defaults/main.yml`
- [ ] Documentation in `doc/source/reference/`
- [ ] Release note added
- [ ] Tested with actual deployment (if possible)
- [ ] Bug/blueprint reference in commit message
- [ ] Run `tox -elinters` locally first

## Running Your Own Analysis

To analyze current patterns (data changes over time):

```bash
# Review timing patterns
./tools/analyze-gerrit-review-times

# Patch size and series analysis
./tools/analyze-gerrit-patch-size

# More data for better accuracy
./tools/analyze-gerrit-review-times --limit 200
./tools/analyze-gerrit-patch-size --limit 200

# Analyze a different project
./tools/analyze-gerrit-review-times --project openstack/nova --limit 100
```
