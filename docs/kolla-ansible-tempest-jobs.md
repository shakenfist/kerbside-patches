# Kolla-Ansible Zuul and Tempest CI: notes for adding Kerbside jobs

This document captures what is needed to add new Zuul jobs to Kolla-Ansible
that deploy with `enable_kerbside: true` and run Tempest. It is written from
a survey of the upstream `kolla-ansible` tree at the SHA pinned in this
patch set, so file paths refer to that source tree (which lands in
`src/kolla-ansible/` after `assemble-source.sh`).

## How Zuul jobs are organised

Zuul configuration lives in `zuul.d/`:

- `zuul.d/base.yaml` — the root job `kolla-ansible-base`. It wires up
  `tests/pre.yml`, `tests/run.yml`, `tests/post.yml`, declares
  `required-projects` (including `openstack/tempest`), and sets the
  cross-cutting vars: `tls_enabled: true`, `virt_type: qemu`,
  `scenario: core`, `kolla_ansible_tempest_regex: ['.*smoke.*']`,
  `base_distro: "{{ zuul.job.split('-').2 }}"` (so the distro is parsed
  out of the job name), the `scenario_images_core` list, etc.
- `zuul.d/nodesets.yaml` — nodesets per distro/size. Single-node 16GB
  nodesets exist for each distro we care about:
  `kolla-ansible-debian-trixie-16GB`,
  `kolla-ansible-ubuntu-noble-16GB`,
  `kolla-ansible-rocky-10-16GB`.
- `zuul.d/scenarios/*.yaml` — one file per scenario. Each defines a
  `kolla-ansible-<scenario>-base` job inheriting from
  `kolla-ansible-base`, sets `scenario: <name>` (and other vars), then
  declares one job per distro plus a `project-template` to bundle them.
- `zuul.d/scenarios/aio.yaml` — defines `kolla-ansible-aio-base` and the
  per-distro plain AIO jobs (`kolla-ansible-debian-trixie`,
  `kolla-ansible-ubuntu-noble`, `kolla-ansible-rocky-10`). These are the
  "scenario: core" jobs.
- `zuul.d/project.yaml` — pulls in the per-scenario project-templates.

## How `scenario` drives configuration

`scenario` is the single switch that lets one set of playbooks/templates
serve many CI jobs. Key consumers:

- `tests/templates/globals-default.j2` — the globals.yml used in CI.
  Most feature-enabling `enable_*: "yes"` lines are inside
  `{% if scenario == "<name>" %}` blocks. The kerbside jobs need a new
  block here that sets `enable_kerbside: "yes"` (and possibly other
  toggles such as `nova_console: spice`).
- `tests/templates/inventory.j2` — already includes a
  `[kerbside:children]\nnova` block from patch147, so no inventory
  changes are required for an AIO deployment.
- `tests/run.yml` — uses `scenario` to decide which post-deploy test
  script to run (`test-masakari.sh`, `test-magnum.sh`, ...). It also
  calls the `kolla-ansible-tempest` role unconditionally when
  `openstack_core_tested` is true. `openstack_core_tested` is true for
  scenarios `core`, `cephadm`, `cells`, `ovn`, `lets-encrypt`,
  `container-engine-migration`. A new `kerbside` scenario will need to
  be added to that list (in `tests/run.yml`) if we want Tempest to run.
  Alternatively, give the new jobs `scenario: core` and use job vars to
  enable kerbside — see "Two ways to wire it up" below.

## How Tempest is invoked

- The `kolla-ansible-tempest` role lives at
  `roles/kolla-ansible-tempest/`. It pip-installs `python-tempestconf`
  and `tempest`, runs `tempest init`, runs `discover-tempest-config`
  against `kolla-admin` (from `/etc/kolla/clouds.yaml`), and then runs
  `tempest run` with `--regex` from `kolla_ansible_tempest_regex` and
  `--exclude-regex` from `kolla_ansible_tempest_exclude_regex`.
- The default regex set in `zuul.d/base.yaml` is `['.*smoke.*']`. To
  add kerbside-specific Tempest tests later, extend that list (or set
  `kolla_ansible_tempest_packages_extra` to pull in a plugin and add a
  matching regex). The role re-runs `discover-tempest-config` each
  job, so a plugin shipped in the same venv will register itself.
- `tests/run.yml:451-457` is the call site:
  ```yaml
  - import_role:
      name: kolla-ansible-tempest
    vars:
      kolla_ansible_tempest_packages:
        - python-tempestconf
        - "{{ tempest_src_dir if (zuul.branch == 'master' and not is_upgrade) else 'tempest' }}"
    when: openstack_core_tested
  ```

## Two ways to wire kerbside into the jobs

1. **New `scenario: kerbside`** (mirrors masakari, ovn, telemetry, ...).
   Requires patching `tests/templates/globals-default.j2` to add a
   `{% if scenario == "kerbside" %} enable_kerbside: "yes" {% endif %}`
   block, and patching `tests/run.yml` so `openstack_core_tested` (or
   an equivalent flag) is true for the kerbside scenario so Tempest
   runs. Cleaner long-term; this is what gets folded back upstream.

2. **Job-var override on a `scenario: core` base.** Define the new jobs
   with `parent: kolla-ansible-aio-base` and set `enable_kerbside: yes`
   via a `host_vars`/extra-globals mechanism. This avoids patching the
   globals template but is messier because Kolla-Ansible CI does not
   currently support `enable_*` flags as zuul job vars — they live in
   the templated `globals.yml`. So in practice option 1 is required.

The masakari scenario is the closest existing precedent for what we
want (single feature gated by an `enable_<feature>` flag with extra
images and a small test script), so the patch should follow its shape.

## Recommended parent job to copy

**Copy `zuul.d/scenarios/masakari.yaml` as the structural template,
but use the AIO single-node 16GB nodesets (not masakari's 4-node
8GB nodesets).**

Reasons:

- Masakari is a small, self-contained scenario that adds a feature to
  an otherwise normal control plane. Its file structure (base job,
  three distro jobs, project-template) is exactly what we want.
- Kerbside is a single-host service in this deployment (the inventory
  patch puts `[kerbside:children] nova` and we are running AIO), so we
  do **not** need multi-node nodesets. Use:
  - `kolla-ansible-debian-trixie-16GB`
  - `kolla-ansible-ubuntu-noble-16GB`
  - `kolla-ansible-rocky-10-16GB`
- Inherit `kolla-ansible-base` (not `kolla-ansible-aio-base`); the AIO
  base adds AIO-specific `files:` filters, but kerbside CI should run
  whenever kerbside-relevant paths change, which is a different set.
- TLS is required for kerbside (`certificates_generate_kerbside`
  depends on `enable_kerbside`); the base already sets
  `tls_enabled: true`, so we get this for free.
- `virt_type` should stay at the default `qemu`. Real KVM (nested-virt)
  is not needed just to deploy kerbside, and nested-virt nodesets are
  a scarcer CI resource. If we later add console-attach tests that
  need a guest to actually boot a desktop, the `kvm` scenario pattern
  is the model.

The mirror-of-existing-jobs framing in the task brief is therefore:
the kerbside jobs are mirrors of the **plain AIO** jobs
(`kolla-ansible-debian-trixie`, `kolla-ansible-ubuntu-noble`,
`kolla-ansible-rocky-10`) rather than mirrors of any specialised
scenario. The masakari file is the right structural template; the
plain AIO jobs are the right behavioural template.

## Triggering the jobs only on kerbside / nova changes

Zuul runs a job when at least one changed file matches the job's `files:`
regex list. `files: !inherit` extends (rather than replaces) the parent's
list, so a child can add scenario-specific paths on top of the base set.

Masakari is the precedent here:

```yaml
files: !inherit
  - ^ansible/group_vars/all/(hacluster|masakari|valkey).yml
  - ^ansible/roles/(hacluster|masakari|valkey)/
  - ^tests/test-masakari.sh
```

The equivalent block for kerbside is:

```yaml
files: !inherit
  - ^ansible/group_vars/all/(kerbside|nova).yml
  - ^ansible/roles/(kerbside|nova|nova-cell)/
  - ^ansible/roles/certificates/tasks/generate-kerbside.yml
  - ^ansible/roles/certificates/templates/openssl-kolla-kerbside.cnf.j2
```

Confirmed in the tree: `ansible/roles/kerbside/`, `ansible/roles/nova/`
and `ansible/roles/nova-cell/` all exist; `ansible/group_vars/all/` has
`kerbside.yml` and `nova.yml` (no `nova-cell.yml`). The certificates
paths are kerbside-specific; the broader cert role is already covered
by the base job's `^ansible/roles/certificates/` filter.

Note: `^ansible/roles/nova/` is a wide trigger — every Nova-touching
change will fire the kerbside jobs. That is the intended behaviour
(we want to know when Nova changes break kerbside), but it does mean
the three new jobs run frequently. Acceptable while they are
`voting: false`; reconsider before promoting to voting.

The base job already covers `^tests/templates/(inventory|globals-default).j2`,
so the scenario block we add to `globals-default.j2` will trigger the
jobs without an extra entry.

## Sketch of what the new patch will need to touch

- `_patches/patchNNN-kolla-ansible-master-kerbside-ci-jobs.patch`:
  - **new** `zuul.d/scenarios/kerbside.yaml` (base + 3 jobs + template)
  - **edit** `zuul.d/project.yaml` to include
    `kolla-ansible-scenario-kerbside`
  - **edit** `tests/templates/globals-default.j2` to add
    `{% if scenario == "kerbside" %} enable_kerbside: "yes" {% endif %}`
  - **edit** `tests/run.yml` so the `kerbside` scenario triggers
    Tempest (extend `openstack_core_tested` or add a kerbside-specific
    branch alongside the existing scenario test scripts).

- `kolla-ansible/ORDER` (and the equivalent stable-branch ORDER files
  if we want kerbside CI on stable too) gains the new patch.

## Open questions for the implementer

- Do we want `voting: false` initially? The masakari, kvm, and other
  scenario-base jobs start non-voting; following suit makes failures
  in the kerbside jobs not block the gate while we develop the test
  set.
- Should the three jobs be added to `gate:` as well as `check:` in
  the project-template? Masakari is check-only. Until the jobs are
  trusted, check-only is the right call.
- `scenario_images_extra` should include a `^kerbside` (and likely
  `^nova-libvirt-spice`) regex so the relevant container images are
  built/pulled. Confirm the image names in the kolla side of this
  patch set.
