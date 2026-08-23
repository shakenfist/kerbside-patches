# Kerbside patches documentation

This repository maintains the patches against OpenStack (mostly Nova,
Kolla, and Kolla-Ansible) needed for native SPICE console support via
[Kerbside](https://github.com/shakenfist/kerbside), plus the tooling
that keeps those patches applying, builds patched container images,
and tracks the health of the pipelines involved.

## Contents

- [Building patched container images](building.md) — host setup
  (Debian and Rocky), the assemble-source / buildall flow, deploying
  with Kolla-Ansible, and the debsecan vulnerability scan.
- [Script reference](script-reference.md) — every helper script in
  `_build/` and `tools/`, including the automated rebase and lint-fix
  tooling, Gerrit helpers, and pre-commit hooks.
- [CI data collection and reporting](ci-data.md) — the container layer
  data time series and the upstream OpenDev CI reliability reports.
- [Security scanning](security-scanning.md) — the gitleaks credential
  scan, CodeQL, the GitHub-side secret scanning settings, and how to
  accept a finding.
- [Kolla-Ansible Zuul and Tempest CI](kolla-ansible-tempest-jobs.md) —
  notes for adding Kerbside-enabled Zuul jobs to Kolla-Ansible.
- [Kerbside development mode](kolla-devmode.md) — rapid iteration on
  the kerbside proxy inside a deployed Kolla-Ansible environment.
- [Gerrit API notes](gerrit-api.md) — querying review.opendev.org over
  SSH and REST, with examples for batch-fetching reviews.
- [Tactics](tactics.md) — advice for getting Kolla/Kolla-Ansible
  patches reviewed quickly, based on reviewer activity analysis.
