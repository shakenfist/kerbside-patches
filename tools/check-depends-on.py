#!/usr/bin/env python3
"""Validate the Depends-On footers in the patches in _patches/.

A Depends-On footer points at a change on review.opendev.org that has to land
before this patch can. Nothing in the build reads it -- it is there for Zuul,
and for the human reading the patch -- so a wrong link is invisible until a
gate job behaves strangely or a reviewer follows it somewhere unrelated.

Checks, in two tiers:

Offline (no network, this is what pre-commit runs)
    * every Depends-On value is a review.opendev.org change URL
    * a committed <patch>-message matches the message embedded in <patch>,
      so the two copies of the footer cannot drift apart

Online (needs review.opendev.org)
    * the change exists
    * the project in the URL is the change's actual project
    * the change is not abandoned -- an abandoned change can never merge, so
      the dependency can never be satisfied. When it is, the open change with
      the same subject is suggested, which is usually the re-upload the link
      was meant to point at.
    * the change is not this patch's own change

Usage:
    tools/check-depends-on.py                        # every patch in _patches/
    tools/check-depends-on.py --offline _patches/*   # no network
    tools/check-depends-on.py kolla-ansible          # a project's ORDER, and
                                                     # its depends_on projects

Exit status:
    0   no problems found (or only Gerrit being unreachable)
    1   at least one patch has a bad Depends-On footer
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

from patch_message import depends_on, extract

GERRIT = 'https://review.opendev.org'
TIMEOUT = 30

# https://review.opendev.org/c/openstack/kolla-ansible/+/1005458, optionally
# with a trailing patchset number or slash, which is what the web UI copies.
CHANGE_URL = re.compile(r'^https://review\.opendev\.org/c/(?P<project>\S+?)/\+/(?P<number>\d+)(?:/\d+)?/?$')
CHANGE_ID = re.compile(r'^Change-Id:\s*(\S+)\s*$', re.MULTILINE)


class GerritUnavailable(Exception):
    """review.opendev.org could not be reached."""


class Problem:
    """One thing wrong with one patch."""

    def __init__(self, patch, line, message, hint=None):
        self.patch = patch
        self.line = line
        self.message = message
        self.hint = hint

    def report(self):
        print(f'{self.patch}:{self.line}: {self.message}')
        if self.hint:
            for line in self.hint.split('\n'):
                print(f'    {line}')


##############################################################################
# Gerrit                                                                     #
##############################################################################

_changes = {}


def _fetch(path):
    """GET a Gerrit REST path and decode the response, or None on a 404."""
    url = f'{GERRIT}/{path}'
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            raw = response.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise GerritUnavailable(f'{url}: {e}') from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GerritUnavailable(f'{url}: {e}') from e

    # Gerrit prefixes JSON responses with )]}' to defeat cross site script
    # inclusion. See docs/gerrit-api.md.
    if raw.startswith(")]}'"):
        raw = raw.split('\n', 1)[1]
    return json.loads(raw)


def change(number):
    """Return Gerrit's view of a change number, or None if there is no such change."""
    if number not in _changes:
        _changes[number] = _fetch(f'changes/{number}/')
    return _changes[number]


def open_change_with_subject(project, wanted):
    """Return the open change in *project* whose subject is *wanted*, if there is exactly one.

    Used to suggest a replacement when a Depends-On points at an abandoned
    change: abandoning and re-uploading keeps the subject but changes both the
    change number and the Change-Id, which is exactly the situation a stale
    link records.
    """
    query = f'project:{project} subject:"{wanted}" -status:abandoned'
    results = _fetch('changes/?q=' + urllib.parse.quote(query, safe='')) or []
    candidates = [c for c in results if c.get('subject') == wanted]
    if len(candidates) == 1:
        return candidates[0]
    return None


##############################################################################
# Finding patches                                                            #
##############################################################################

def patches_in_project(project, seen):
    """Return the patches a project applies, following its depends_on projects."""
    project = project.rstrip('/')
    if project in seen:
        return []
    seen.add(project)

    found = []
    for name in ('PREPATCH', 'ORDER', 'ADDITIONAL_FOR_CI'):
        listing = os.path.join(project, name)
        if not os.path.exists(listing):
            continue
        with open(listing, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                found.append(os.path.normpath(os.path.join(project, line)))

    config = os.path.join(project, 'config.yaml')
    if os.path.exists(config):
        with open(config, 'r') as f:
            for dependency in (yaml.safe_load(f).get('depends_on') or '').split():
                found = patches_in_project(dependency, seen) + found

    return found


def resolve(targets):
    """Turn command line targets into a de-duplicated, ordered list of patch files."""
    found = []
    seen = set()
    for target in targets:
        if os.path.isdir(target):
            found.extend(patches_in_project(target, seen))
        elif target.endswith('-message'):
            # pre-commit hands us whichever half of the pair was edited.
            found.append(target[:-len('-message')])
        else:
            found.append(target)

    ordered = []
    for patch in found:
        if patch not in ordered:
            ordered.append(patch)
    return ordered


##############################################################################
# Checks                                                                     #
##############################################################################

def check_message_file(patch, message):
    """The committed <patch>-message, if there is one, must match the patch."""
    path = f'{patch}-message'
    if not os.path.exists(path):
        return []

    with open(path, 'r') as f:
        committed = f.read()

    if committed == message:
        return []

    return [Problem(
        path, 1,
        'does not match the commit message in the patch, so the two copies of any '
        'Depends-On footer can disagree',
        f'Regenerate it with:\n    python3 tools/extract-commit-message {patch}\n'
        'or delete it -- the build regenerates it when applying the patch.')]


def check_url(patch, line, value):
    """A Depends-On must be a review.opendev.org change URL."""
    if CHANGE_URL.match(value):
        return None

    hint = None
    if not value.startswith('http'):
        hint = ('Zuul deprecated the bare Change-Id form of Depends-On. Use the\n'
                'change URL, which the Gerrit web UI copies for you.')
    return Problem(
        patch, line, f'Depends-On "{value}" is not a review.opendev.org change URL', hint)


def check_change(patch, line, value, own_change_id):
    """Ask Gerrit whether a Depends-On target is a change this patch can depend on."""
    match = CHANGE_URL.match(value)
    project, number = match.group('project'), match.group('number')

    info = change(number)
    if info is None:
        return Problem(patch, line, f'Depends-On {value} is not a change on {GERRIT}')

    if info['project'] != project:
        return Problem(
            patch, line,
            f'Depends-On {value} says {project} but change {number} is in {info["project"]}',
            f'{GERRIT}/c/{info["project"]}/+/{number}')

    if own_change_id and info['change_id'] == own_change_id:
        return Problem(
            patch, line,
            f'Depends-On {value} is the change this patch itself becomes ({own_change_id})')

    if info['status'] == 'ABANDONED':
        hint = f'"{info["subject"]}" was abandoned, so this dependency can never be satisfied.'
        replacement = open_change_with_subject(info['project'], info['subject'])
        if replacement:
            hint += (f'\nThe open change with that subject is {replacement["_number"]}:\n'
                     f'    {GERRIT}/c/{info["project"]}/+/{replacement["_number"]}')
        return Problem(patch, line, f'Depends-On {value} is ABANDONED', hint)

    return None


def check(patch, offline):
    """Return the problems with one patch file."""
    if not os.path.exists(patch):
        return [Problem(patch, 1, 'no such file')]

    message = extract(patch)
    problems = check_message_file(patch, message)

    own = CHANGE_ID.search(message)
    own_change_id = own.group(1) if own else None

    for line, value in depends_on(message):
        problem = check_url(patch, line, value)
        if problem:
            problems.append(problem)
        elif not offline:
            problem = check_change(patch, line, value, own_change_id)
            if problem:
                problems.append(problem)

    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--offline', action='store_true',
                        help='skip the checks that need review.opendev.org')
    parser.add_argument('targets', nargs='*',
                        help='patch files, or project directories whose ORDER to check '
                             '(default: every patch in _patches/)')
    args = parser.parse_args()

    targets = args.targets
    if not targets:
        targets = sorted(os.path.join('_patches', p) for p in os.listdir('_patches')
                         if p.endswith('.patch'))

    problems = []
    offline = args.offline
    for patch in resolve(targets):
        try:
            problems.extend(check(patch, offline))
        except GerritUnavailable as e:
            print(f'Warning: {GERRIT} is unreachable, running offline checks only ({e})',
                  file=sys.stderr)
            offline = True
            problems.extend(check(patch, offline))

    for problem in problems:
        problem.report()

    if problems:
        print(f'\n{len(problems)} problem(s) found.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
