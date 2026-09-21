#!/usr/bin/env python3
"""Tests for check-depends-on.py.

The Gerrit half is tested against a stub rather than review.opendev.org, so
this runs offline and does not depend on the state of any real change. The
offline half is tested against both synthetic patches and the real corpus in
_patches/.

Usage:
    ./test-check-depends-on.py
"""

import os
import sys
import tempfile
import urllib.parse
from importlib import import_module

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

check = import_module('check-depends-on')
import patch_message  # noqa: E402  (after the sys.path fiddling above)

TOPDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A patch in the shape git show produces, with the commit message indented.
PATCH = """commit 57f69c143e51642546a2aea46b39c5b1bc0f4a34
Author: Michael Still <mikal@stillhq.com>
Date:   Thu Sep 17 18:51:45 2026 +1000

    Detect log levels in JSON formatted logs.
%s
    Depends-On: %s
    Change-Id: Id69511ea84bd9d4cb005c39da471fc2d576686ac

diff --git a/tools/check-logs.sh b/tools/check-logs.sh
index 1111111..2222222 100644
--- a/tools/check-logs.sh
+++ b/tools/check-logs.sh
@@ -1,2 +1,3 @@
 #!/bin/bash
+# new line
 exit 0
"""

# What the stub Gerrit knows about. Keyed by change number.
CHANGES = {
    '1005370': {'project': 'openstack/kolla-ansible', 'status': 'ABANDONED',
                'change_id': 'I1f8c22590904dc94fe3695f7550a9aee56c30a39',
                'subject': 'Collect etcd logs with Fluentd.'},
    '1005458': {'project': 'openstack/kolla-ansible', 'status': 'NEW', '_number': 1005458,
                'change_id': 'I9bcd92bc3e8f30c2407df1096d8cdff54e967244',
                'subject': 'Collect etcd logs with Fluentd.'},
    '993065': {'project': 'openstack/kolla', 'status': 'MERGED', '_number': 993065,
               'change_id': 'Ib62191e3022e5bcff781c12f662c9800fb210eb9',
               'subject': 'Install and start virtlogd in nova-libvirt.'},
    '924844': {'project': 'openstack/nova', 'status': 'MERGED', '_number': 924844,
               'change_id': 'Id69511ea84bd9d4cb005c39da471fc2d576686ac',
               'subject': 'libvirt: allow direct SPICE connections to qemu'},
}


def stub_fetch(path):
    """Stand in for the Gerrit REST API."""
    if path.startswith('changes/?q='):
        query = urllib.parse.unquote(path[len('changes/?q='):])
        wanted = query.split('subject:"', 1)[1].rsplit('"', 1)[0]
        project = query.split('project:', 1)[1].split(' ', 1)[0]
        return [c for c in CHANGES.values()
                if c['subject'] == wanted and c['project'] == project
                and c['status'] != 'ABANDONED']

    number = path.split('/')[1]
    info = CHANGES.get(number)
    return dict(info) if info else None


class TestFailure(Exception):
    pass


def write_patch(directory, name, url, body=''):
    """Write a synthetic patch and return its path."""
    path = os.path.join(directory, name)
    with open(path, 'w') as f:
        f.write(PATCH % (f'\n    {body}\n' if body else '', url))
    return path


def problems_for(url, body=''):
    """Check a synthetic patch carrying *url* as its Depends-On."""
    with tempfile.TemporaryDirectory() as directory:
        patch = write_patch(directory, 'patch999-test.patch', url, body)
        return check.check(patch, offline=False)


##############################################################################
# Cases                                                                      #
##############################################################################

def test_good_url():
    if problems_for('https://review.opendev.org/c/openstack/kolla/+/993065'):
        raise TestFailure('a merged change in the right project should be accepted')


def test_trailing_patchset_and_slash():
    for url in ('https://review.opendev.org/c/openstack/kolla/+/993065/3',
                'https://review.opendev.org/c/openstack/kolla/+/993065/'):
        if problems_for(url):
            raise TestFailure(f'{url} is a form the web UI copies and must be accepted')


def test_abandoned():
    problems = problems_for('https://review.opendev.org/c/openstack/kolla-ansible/+/1005370')
    if len(problems) != 1 or 'ABANDONED' not in problems[0].message:
        raise TestFailure('an abandoned change must be rejected')
    if '1005458' not in (problems[0].hint or ''):
        raise TestFailure('the open change with the same subject must be suggested')


def test_wrong_project():
    problems = problems_for('https://review.opendev.org/c/openstack/nova/+/993065')
    if len(problems) != 1 or 'openstack/kolla' not in problems[0].message:
        raise TestFailure('a URL naming the wrong project must be rejected')


def test_no_such_change():
    problems = problems_for('https://review.opendev.org/c/openstack/kolla/+/7')
    if len(problems) != 1 or 'not a change' not in problems[0].message:
        raise TestFailure('a change number that does not exist must be rejected')


def test_self_dependency():
    # 924844 carries the same Change-Id as the synthetic patch.
    problems = problems_for('https://review.opendev.org/c/openstack/nova/+/924844')
    if len(problems) != 1 or 'itself becomes' not in problems[0].message:
        raise TestFailure('depending on your own change must be rejected')


def test_bad_url_forms():
    for value in ('I9bcd92bc3e8f30c2407df1096d8cdff54e967244',
                  'https://review.example.com/c/openstack/kolla/+/993065',
                  'https://review.opendev.org/c/openstack/kolla/+/',
                  'see the etcd change'):
        problems = problems_for(value)
        if len(problems) != 1 or 'not a review.opendev.org change URL' not in problems[0].message:
            raise TestFailure(f'{value!r} is not a change URL and must be rejected')


def test_prose_is_not_a_footer():
    # patch156 discusses Depends-On in its body. Only a line that starts with
    # the footer is one.
    problems = problems_for('https://review.opendev.org/c/openstack/kolla/+/993065',
                            body='builds them locally. Combined with a Depends-On')
    if problems:
        raise TestFailure('prose mentioning Depends-On must not be parsed as a footer')


def test_message_file_drift():
    with tempfile.TemporaryDirectory() as directory:
        patch = write_patch(directory, 'patch999-test.patch',
                            'https://review.opendev.org/c/openstack/kolla/+/993065')
        with open(f'{patch}-message', 'w') as f:
            f.write(patch_message.extract(patch))
        if check.check(patch, offline=True):
            raise TestFailure('a message file matching the patch must be accepted')

        with open(f'{patch}-message', 'a') as f:
            f.write('\nDepends-On: https://review.opendev.org/c/openstack/kolla/+/1\n')
        problems = check.check(patch, offline=True)
        if len(problems) != 1 or 'does not match' not in problems[0].message:
            raise TestFailure('a message file that has drifted must be rejected')


def test_corpus_urls_parse():
    """Every Depends-On in _patches/ is a change URL the checker understands."""
    patches = sorted(p for p in os.listdir(os.path.join(TOPDIR, '_patches'))
                     if p.endswith('.patch'))
    seen = 0
    for name in patches:
        message = patch_message.extract(os.path.join(TOPDIR, '_patches', name))
        for _, value in patch_message.depends_on(message):
            seen += 1
            if not check.CHANGE_URL.match(value):
                raise TestFailure(f'{name}: {value} does not parse')
    if seen == 0:
        raise TestFailure('no Depends-On footers found in the corpus, the parser is broken')


def test_project_resolution():
    """A project directory resolves to the patches its ORDER lists."""
    cwd = os.getcwd()
    os.chdir(TOPDIR)
    try:
        patches = check.resolve(['kolla-ansible-json-logging'])
    finally:
        os.chdir(cwd)
    if not patches or not all(p.startswith('_patches/') for p in patches):
        raise TestFailure(f'ORDER entries did not resolve into _patches/: {patches[:3]}')


def main():
    check._fetch = stub_fetch

    results = []
    failures = []
    for name, test in sorted(globals().items()):
        if not name.startswith('test_'):
            continue
        check._changes.clear()
        try:
            test()
            results.append(f'  ok   {name}')
        except TestFailure as e:
            results.append(f'  FAIL {name}: {e}')
            failures.append(name)

    print('Testing check-depends-on.py')
    print('\n'.join(results))
    print(f'\n{len(results) - len(failures)} passed, {len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
