---
name: debug-ci
description: Diagnose a failed kerbside-patches CI run - which job failed, which step, and whether the cause is the patches or the environment. Use when a CI run, workflow, or PR check fails in this repository.
---

# Debugging a CI Failure

## Jobs

Pull requests run one workflow, `Build test clouds`. Look in its jobs:

| Job | What fails there |
|-----|------------------|
| `Check paths` | The path filter or lane selection (`tools/ci-select-lanes.py`); on a merge group, the superseded-run check |
| `Patch tests / ...` | Patches no longer apply, or upstream test suites break (and `Patch tests / Ansible lint check` for lint) |
| `Local build / ...` | Image builds without the CI registry |
| `Images for ...` | Image builds in CI |
| `... using ... containers` | Real Kolla-Ansible deployments on test VMs, plus the SPICE console test |
| `Can enqueue`, `Can merge` | Nothing of their own: they fail because a job they need failed or was cancelled |

See `docs/ci.md` for how the jobs fit together.

## Finding the failing step

Use the pre-approved read-only `ci-status` helper (see the global
`ci-debugging` skill for its full usage):

```bash
ci-status shakenfist/kerbside-patches runs --branch <branch>
ci-status shakenfist/kerbside-patches jobs <run-id>
ci-status shakenfist/kerbside-patches failures <run-id>
ci-status shakenfist/kerbside-patches logs <job-id>
```

Read the job list before the logs. Which jobs failed is itself the
diagnosis: if only the Rocky-hosted test clouds failed and the Debian ones
passed, the cause is the host OS, not the patches.

## Reading a `Build test clouds` failure

1. Note which step failed. Steps after a failure that still ran are marked
   `if: always()` and their failures are cascade noise, not the cause.
   `Setup test environment` failing means the test VM never got as far as
   deploying.
2. Pull the clingwrap bundle for that job and read the deployment logs. See
   the `extract-bundle` skill.
3. Compare against the last run where the same job passed. Kernel version,
   installed packages and image contents drift underneath us, and the
   difference between a passing and a failing bundle usually names the
   cause outright.

## Patch failures

```bash
cat patch-results.json | ./_build/extract-patch-failures.py
./_build/test-apply.sh --test-patch <patch> --skip-tests <project>
```

See the `check-patches` skill for the full patch validation flow, and
`_build/rebase-with-claude.sh` for the automated rebase path.

## Cascade failures

When an Ansible task fails on a host, that host is excluded from every
later task in the play. The visible error is often far downstream of the
real one, so read forward from the first failure, not backward from the
last.
