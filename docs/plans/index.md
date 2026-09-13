# Plans index

Every planning document in this repository, oldest first. Plans here
describe work on the patch series themselves and on the tooling that
keeps them applying, and each one outlives the pull request that
implemented it because the upstream half -- pushing to Gerrit, chasing
reviews, landing -- runs long after the patches land here.

None of these plans has separate phase files. Where a plan is phased,
its phases are sections inside it, tracked in the plan's own table;
this page carries only the one-line status.

Status cells use the shared vocabulary from
`templates/shared-blocks/plan-status-vocabulary.md` in
[shakenfist/development](https://github.com/shakenfist/development),
the same list the `plan-index` audit enforces across the fleet: one of
`Proposed`, `Not started`, `In progress`, `Blocked`, `Complete`,
`Abandoned` or `Superseded`. A status says whether the plan still
wants attention and nothing else -- the dates, the phase arithmetic
and the summary of what happened live in the plan.

| Date | Plan | Intent | Status |
|------|------|--------|--------|
| 2026-09-11 | [JSON logging for oslo.log services](oslo-json-logging.md) | Switch every oslo.log-based Kolla-Ansible service to structured JSON logging behind one global variable, reusing the fluentd JSON parser Kerbside introduced | In progress |
