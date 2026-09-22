# Draft: openstack-discuss post on the Magnum collation failure

This is a draft for the OpenStack development mailing list
(openstack-discuss@lists.openstack.org). It explains why new Magnum
deployments fail on MariaDB 11.5 and later, what we considered, and why
the change we are proposing edits a migration that has already been
released -- which is the part most likely to be argued about, and the
reason for socialising it on the list before pushing hard on review.

The body below is plain text wrapped for a mailing list. Paste it as-is.

The changes it refers to are carried here as patch193 and patch194
(`kolla-ansible-mariadb-collations`) and patch195
(`magnum-mariadb-collations`), and the supporting measurement is the
`mariadb-collation` report described in [ci-data.md](ci-data.md).

---

Subject: [magnum][kolla-ansible] MariaDB 11.5 collation default breaks
 new Magnum deployments

Hi all,

Short version: on MariaDB 11.5 and later, a brand new Magnum deployment
cannot complete its database migrations. It aborts partway through the
chain with "Illegal mix of collations", every time. Existing deployments
are fine. I have a small fix, but it edits a migration that has already
been released, so I would rather discuss it here than surprise people in
review.


What fails
----------

During "magnum-db-manage upgrade", on a fresh database:

  Running upgrade 47380964133d -> c04e925e65c2, nodegroups_v2
  UPDATE nodegroup INNER JOIN cluster ON nodegroup.cluster_id=cluster.uuid
  ...
  Illegal mix of collations (utf8mb3_general_ci,IMPLICIT)
                        and (utf8mb3_uca1400_ai_ci,IMPLICIT)
                        for operation '='

The two tables have ended up in different collations, so the join in
nodegroups_v2 cannot be evaluated.


Why the two tables differ
-------------------------

MariaDB resolves a table's collation three ways: if neither charset nor
collation is stated, the database default applies; if only a charset is
stated, the server's charset-to-collation mapping applies and the
database default is not consulted; if both are stated, they are used.

MDEV-25829, released in MariaDB 11.5.1, changed that middle case. It
gave the character_set_collations variable (added in 11.2) a non-empty
default mapping every Unicode charset to its uca1400 collation. So a
bare "CHARACTER SET utf8mb3" now yields utf8mb3_uca1400_ai_ci where it
used to yield utf8mb3_general_ci.

Magnum creates 13 tables. Ten of them declare
mysql_DEFAULT_CHARSET='UTF8'. Three do not: nodegroup, federation and
magnum_service. Before 11.5 that made no difference, because both routes
arrived at utf8mb3_general_ci. From 11.5 the two routes diverge, and
cluster (which declares a charset) and nodegroup (which does not) end up
in different collations.

Worth noting that magnum/db/sqlalchemy/models.py is unambiguous about
the intent: table_args() returns mysql_charset "utf8" for every model,
including NodeGroup. The three migrations simply omit what the models
ask for.


Why this is surfacing now
-------------------------

Kolla switched its Debian base image to Trixie in October 2025, and that
is in stable/2026.1. Kolla's repos.yaml pins the MariaDB 11.4 repository
for all three distros, but its Debian entry can only offer bookworm
builds -- there is an in-tree comment saying "11.4 does not have trixie
builds yet" -- so the Trixie image actually resolves to Debian's own
MariaDB 11.8 instead.

That is visible in CI: debian-trixie jobs run 11.8.6 and hit this, while
ubuntu-noble and rocky-10 run 11.4.13 and do not. So Trixie is not an
outlier, it is an accidental preview. Every distro gets the same
exposure as soon as that pin moves past 11.5, which it must eventually.


How common is it
----------------

I have been scanning OpenDev job logs for the signature. Over 30 days,
across 12,953 builds and 160 distinct job names, there were 82 hits.
All 82 were kolla-ansible-debian-trixie-magnum, all were job failures,
and every one had exactly the same number of matches -- it is entirely
deterministic, not a flake. 82 of the 83 builds of that job were hit.

Magnum appears to be alone in this today. No other service in Kolla's
deploy or upgrade path tripped it, including 548 builds of the Trixie
upgrade job. rocky-10-magnum and ubuntu-noble-magnum ran the same
migrations 166 times on 11.4.13 with zero hits, which brackets the cause
to the MariaDB version rather than anything about Magnum's code.

One caveat on that reassurance, though: CI cannot see the more
interesting case. Kolla's upgrade jobs run the same containers on both
sides of the upgrade, so every table in them was created under the new
MariaDB. A database created under an older MariaDB and later served by a
newer one has no coverage at all. Other projects may want to check
whether their own migrations are consistent about declaring charsets --
the pattern to look for is some create_table calls naming a charset and
others naming none.


What we considered
------------------

1. Add COLLATE to the join in nodegroups_v2.

   Fixes the migration and leaves nodegroup.cluster_id and cluster.uuid
   permanently in different collations, so the same problem returns at
   runtime for any query joining them. Rejected.

2. Rewrite that UPDATE ... JOIN as a Python loop, comparing against
   literals instead of across two columns.

   This would work, since a literal's coercibility adapts. But it still
   means editing nodegroups_v2, which is a released migration -- so it
   does not avoid the objection, it just spends it somewhere less
   useful.

3. Add a new migration that converts the tables to a common collation.

   This is the obvious answer and it does not work. A new migration is a
   new head, so it runs last, and a fresh deployment dies at
   nodegroups_v2 long before reaching it. Alembic's chain is linear, so
   a revision cannot be inserted in the middle without breaking every
   deployment already past that point. If I am wrong about this and
   there is a sanctioned way to repair mid-chain, I would much rather do
   that, and this is the main thing I am hoping someone corrects me on.

4. Fix it in the deployment tooling instead, by setting
   character_set_collations='' on the server so that the two declaration
   styles converge again.

   This does work, and it fixes the whole class rather than this one
   instance, so I am proposing it for Kolla-Ansible as well. But it only
   helps operators whose deployment tool does it. Asking every tool and
   every operator on MariaDB 11.5+ to set a server variable seems a
   worse ask than one line of table metadata in Magnum.

5. Declare the missing charset on the three tables that lack it.

   This is what I am proposing.


The proposed change
-------------------

Add mysql_DEFAULT_CHARSET='UTF8' to the create_table calls in
ac92cbae311c (nodegroup), 9a1539f1cd2c (federation) and 27ad304554e2
(magnum_service), so that all 13 tables declare what the other ten and
table_args() already declare. Five lines.

I am well aware this edits released migrations, and I would not normally
suggest it. Three things make me think it is defensible here:

- It is additive metadata on a create_table, and a no-op for anyone who
  has already applied that revision. Nothing re-runs, nothing is
  altered, no data is touched.

- There are no mixed-collation Magnum deployments in the wild to be
  inconsistent with. A cloud deployed before 11.5 has every table in
  general_ci and is self-consistent; a cloud deployed after 11.5 never
  finished deploying. The split only exists during a fresh chain run.

- Every option that actually fixes fresh deployments requires editing a
  released migration. Given that, the question is which edit, and
  changing a table's declared charset seems less invasive than changing
  what a migration does to data.

One residue this does not fix: all 13 tables would then declare a bare
charset, so a cloud whose tables were created before 11.5 and which
later gains a new table would still split across the two collations.
That is what the server-side character_set_collations pin addresses, and
it is why I think the two changes are complementary rather than
alternatives. If people would prefer Magnum declare an explicit
collation rather than a bare charset, that is a bigger change -- all 13
tables plus table_args() -- but I am happy to write it instead if that
is the consensus.

Finally, a warning for anyone reviewing this against CI: landing it will
not turn kolla-ansible-debian-trixie-magnum green. That job also fails
on rocky-10 and ubuntu-noble, with no collation errors at all, for some
unrelated reason. Fixing the collation will unmask whatever that is.

Happy to be told I have this wrong.

Thanks,
Michael

References:
  MDEV-25829  https://jira.mariadb.org/browse/MDEV-25829
