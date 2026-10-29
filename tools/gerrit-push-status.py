#!/usr/bin/env python3
"""Say which patches in a series need pushing to Gerrit, and why.

For every patch an ORDER file lists, including lines commented out to hold a
patch back, this finds the Gerrit change with the patch's Change-Id and
compares it with the latest patchset:

    new        no change with this Change-Id exists, so it has never been
               pushed
    changed    the diff or the commit message differs from the latest
               patchset
    rebase     diff and message match, but a repush would still upload a new
               patchset because the commit underneath it changes: an earlier
               patch in the series is new, changed or rebased, or the series'
               source_sha is not the parent Gerrit has
    current    a repush would upload nothing for this patch
    merged /   reported as Gerrit's status; a repush would fail for these
    abandoned

Diffs are compared by fetching the latest patchset and its parent from
opendev into a cache repository, applying the local patch to that parent and
comparing the resulting tree with the patchset's. Comparing diff text does not
work: Gerrit and git can align the hunks of one change differently. A patch
that no longer applies to the old parent counts as changed. A held patch is
compared too, but it is not pushed and does not affect the patches after it.

Usage:
    tools/gerrit-push-status.py neutron-dhcp-agent-fixes [series...]
    tools/gerrit-push-status.py --all     # every series with repush: true

The cache defaults to ~/.cache/kerbside-patches/gerrit-push-status, or
$XDG_CACHE_HOME in place of ~/.cache; --cache-dir moves it.

Exit status:
    0   a repush would upload nothing
    1   at least one series has a patch to push
    2   review.opendev.org or opendev.org could not be reached
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import yaml

from patch_message import extract

GERRIT = 'https://review.opendev.org'
GIT = 'https://opendev.org'
TIMEOUT = 30
TOPDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHANGE_ID = re.compile(r'^Change-Id:\s*(I[0-9a-f]{40})\s*$', re.MULTILINE)
HELD = re.compile(r'^#\s*')

# The states that mean a repush would upload a new patchset.
TO_PUSH = ('new', 'changed', 'rebase')


class GerritUnavailable(Exception):
    """review.opendev.org or opendev.org could not be reached."""


##############################################################################
# Gerrit                                                                     #
##############################################################################

def fetch_json(path):
    """GET a Gerrit REST path and decode its JSON."""
    url = f'{GERRIT}/{path}'
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            raw = response.read().decode('utf-8')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GerritUnavailable(f'{url}: {e}') from e

    # Gerrit prefixes JSON responses with )]}' to defeat cross site script
    # inclusion. See docs/gerrit-api.md.
    if raw.startswith(")]}'"):
        raw = raw.split('\n', 1)[1]
    return json.loads(raw)


def find_change(project, branch, change_id):
    """Return Gerrit's view of a change with its latest revision, or None if there is none."""
    query = urllib.parse.quote(f'change:{change_id} project:{project} branch:{branch}')
    changes = fetch_json(f'changes/?q={query}&o=CURRENT_REVISION&o=CURRENT_COMMIT')
    return changes[0] if changes else None


##############################################################################
# Comparison                                                                 #
##############################################################################

def _clean_env(**extra):
    """The environment without GIT_* variables, plus *extra*.

    git sets GIT_DIR, GIT_INDEX_FILE and others for hooks, and these would
    point the cache's git commands at the repository running the hook.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(extra)
    return env


def _git(repo, *args, env=None, stdin=None):
    return subprocess.run(['git', '-C', repo, *args], input=stdin, env=env or _clean_env(),
                          capture_output=True, text=True)


class Cache:
    """Bare repositories holding the patchsets being compared, one per project."""

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir

    def repo(self, project):
        path = os.path.join(self.cache_dir, project.replace('/', '_') + '.git')
        if not os.path.exists(path):
            os.makedirs(self.cache_dir, exist_ok=True)
            subprocess.run(['git', 'init', '-q', '--bare', path], env=_clean_env(), check=True)
        return path

    def same_diff(self, project, ref, revision, patch_path):
        """Whether the patch, applied to the revision's parent, gives the revision's tree."""
        repo = self.repo(project)
        # The parent is needed too, as the base the patch is applied to. Check
        # for it rather than for the revision: fetching a later change at
        # depth 2 can make this revision a shallow boundary, which hides its
        # parent, and fetching the revision again restores it.
        if _git(repo, 'rev-parse', '-q', '--verify', f'{revision}^{{commit}}^').returncode != 0:
            fetched = _git(repo, 'fetch', '-q', '--depth=2', f'{GIT}/{project}', ref)
            if fetched.returncode != 0:
                raise GerritUnavailable(f'fetching {ref} from {GIT}/{project}: {fetched.stderr.strip()}')
            if _git(repo, 'rev-parse', '-q', '--verify', f'{revision}^').returncode != 0:
                raise GerritUnavailable(f'{ref} from {GIT}/{project} arrived without its parent')

        with tempfile.TemporaryDirectory() as tmp:
            env = _clean_env(GIT_INDEX_FILE=os.path.join(tmp, 'index'))
            if _git(repo, 'read-tree', f'{revision}^', env=env).returncode != 0:
                return False
            with open(patch_path) as f:
                if _git(repo, 'apply', '--cached', env=env, stdin=f.read()).returncode != 0:
                    return False
            tree = _git(repo, 'write-tree', env=env).stdout.strip()
        return tree == _git(repo, 'rev-parse', f'{revision}^{{tree}}').stdout.strip()


def series_patches(series_dir):
    """Yield (patch path, held) for each patch the series' ORDER file lists."""
    with open(os.path.join(series_dir, 'ORDER')) as f:
        for line in f:
            line = line.strip()
            held = bool(HELD.match(line))
            line = HELD.sub('', line)
            if not line.endswith('.patch'):
                continue
            yield os.path.normpath(os.path.join(series_dir, line)), held


def status(series_dir, cache):
    """Return the series' config and one dict per patch describing its push state."""
    with open(os.path.join(series_dir, 'config.yaml')) as f:
        config = yaml.safe_load(f)
    project = urllib.parse.urlparse(config['repo']).path.strip('/')
    branch = config.get('source_branch', 'master')
    expected_parent = config.get('source_sha') or None

    rows = []
    for path, held in series_patches(series_dir):
        message = extract(path)
        row = {'patch': os.path.basename(path), 'held': held, 'change': None,
               'patchset': None, 'why': ''}
        rows.append(row)

        found = CHANGE_ID.search(message)
        change = find_change(project, branch, found.group(1)) if found else None
        if change is None:
            row['state'] = 'new'
            if not held:
                expected_parent = None
            continue

        revision_sha = change['current_revision']
        revision = change['revisions'][revision_sha]
        row['change'] = change['_number']
        row['patchset'] = revision['_number']
        if change['status'] in ('MERGED', 'ABANDONED'):
            row['state'] = change['status'].lower()
            continue

        differs = []
        if not cache.same_diff(project, revision['ref'], revision_sha, path):
            differs.append('diff')
        if message.strip() != revision['commit']['message'].strip():
            differs.append('message')
        if differs:
            row['state'] = 'changed'
            row['why'] = ' and '.join(differs) + ' differ'
        else:
            parent = revision['commit']['parents'][0]['commit']
            if held or (expected_parent and parent == expected_parent):
                row['state'] = 'current'
            else:
                row['state'] = 'rebase'
                row['why'] = 'parent changes' if expected_parent else 'an earlier patch changes'

        if not held:
            expected_parent = revision_sha if row['state'] == 'current' else None
    return config, rows


##############################################################################
# Output                                                                     #
##############################################################################

def report(series, config, rows):
    """Print one series' table and return whether a repush would upload anything."""
    print(f'{series} (topic {config.get("destination_branch", "")})')
    for row in rows:
        change = f'{row["change"]} ps{row["patchset"]}' if row['change'] else '-'
        notes = ', '.join(n for n in (row['why'], 'held in ORDER' if row['held'] else '') if n)
        print(f'  {row["patch"].split("-")[0]:<9} {change:<14} {row["state"]:<9} {notes}'.rstrip())
    pushed = [r for r in rows if not r['held'] and r['state'] in TO_PUSH]
    counts = ', '.join(f'{sum(1 for r in pushed if r["state"] == s)} {s}'
                       for s in TO_PUSH if any(r['state'] == s for r in pushed))
    print(f'  repush: {"yes (" + counts + ")" if pushed else "nothing to upload"}')
    return bool(pushed)


def repush_series():
    """Return every series directory whose config says repush: true."""
    found = []
    for entry in sorted(os.listdir(TOPDIR)):
        config = os.path.join(TOPDIR, entry, 'config.yaml')
        if os.path.exists(os.path.join(TOPDIR, entry, 'ORDER')) and os.path.exists(config):
            with open(config) as f:
                if (yaml.safe_load(f) or {}).get('repush'):
                    found.append(entry)
    return found


def default_cache_dir():
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, 'kerbside-patches', 'gerrit-push-status')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('series', nargs='*', help='series directories, e.g. neutron-dhcp-agent-fixes')
    parser.add_argument('--all', action='store_true', help='every series with repush: true')
    parser.add_argument('--cache-dir', default=default_cache_dir(),
                        help='where to keep the fetched patchsets (default: %(default)s)')
    args = parser.parse_args()

    series = repush_series() if args.all else [s.rstrip('/') for s in args.series]
    if not series:
        parser.error('name at least one series, or pass --all')

    cache = Cache(args.cache_dir)
    to_push = False
    try:
        for name in series:
            config, rows = status(os.path.join(TOPDIR, name), cache)
            to_push = report(name, config, rows) or to_push
            print()
    except GerritUnavailable as e:
        print(f'Gerrit is unavailable: {e}', file=sys.stderr)
        return 2
    return 1 if to_push else 0


if __name__ == '__main__':
    sys.exit(main())
