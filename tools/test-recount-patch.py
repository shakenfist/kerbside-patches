#!/usr/bin/env python3
"""Tests for recount-patch.py, using git itself as the oracle.

Each test builds a throwaway git repository and has git generate a canonical
patch. That patch is then damaged in one of two ways and recounted:

  scramble  every @@ count is replaced with a wrong number, and the recounted
            patch must match git's own output byte for byte. This is the
            strongest assertion available -- it says we reproduce exactly the
            header git would have written.

  edit      a "+" line is added to the body, the way you would when hand
            editing a file in _patches/, leaving the headers stale. The
            recounted patch must then apply cleanly. git is strict about hunk
            line counts, so a clean apply means the headers were rebuilt right.

Usage:
    ./test-recount-patch.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib import import_module

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

recount_patch = import_module('recount-patch')

HUNK = recount_patch.HUNK


class TestFailure(Exception):
    pass


def git(repo, *args):
    return subprocess.run(['git', '-C', repo] + list(args), check=True, capture_output=True, text=True)


def make_repo():
    repo = tempfile.mkdtemp(prefix='recount-test-')
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.email', 'test@example.com')
    git(repo, 'config', 'user.name', 'Test')
    return repo


def commit_tree(repo, tree):
    """Write `tree` (a path -> content dict, None to delete) and commit it."""
    for path, content in tree.items():
        full = os.path.join(repo, path)
        if content is None:
            os.unlink(full)
            continue
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'change', '--allow-empty')


def canonical_patch(before, after):
    """Have git produce the canonical patch turning `before` into `after`."""
    repo = make_repo()
    try:
        commit_tree(repo, before)
        base = git(repo, 'rev-parse', 'HEAD').stdout.strip()
        commit_tree(repo, after)
        return git(repo, 'format-patch', '--stdout', f'{base}..HEAD').stdout, repo, base
    except Exception:
        shutil.rmtree(repo, ignore_errors=True)
        raise


def applies_cleanly(repo, base, patch):
    git(repo, 'checkout', '-q', base)
    proc = subprocess.run(['git', '-C', repo, 'apply', '--check', '-'],
                          input=patch, capture_output=True, text=True)
    return proc.returncode == 0, proc.stderr.strip()


def scramble(patch):
    """Replace every hunk count with a wrong one, leaving the body alone."""
    out = []
    for line in patch.split('\n'):
        m = HUNK.match(line)
        if m:
            old_start, new_start = m.group(1), m.group(3)
            # Deliberately absurd counts, and always the long form so we also
            # check that the ",1" elision gets restored where it belongs.
            line = f'@@ -{old_start},99 +{new_start},99 @@{m.group(5)}'
        out.append(line)
    return '\n'.join(out)


def add_line(needle, added):
    """Add `added` after the first body line containing `needle`."""
    def mutate(patch):
        out, done = [], False
        for line in patch.split('\n'):
            out.append(line)
            if not done and needle in line and line[:1] in ('+', ' '):
                out.append(added)
                done = True
        if not done:
            raise TestFailure(f'mutation target {needle!r} not found')
        return '\n'.join(out)
    return mutate


NUMBERS = ''.join(f'line {i}\n' for i in range(1, 21))

# name, before tree, after tree, optional body edit
CASES = [
    ('plain modification',
     {'a.txt': NUMBERS},
     {'a.txt': NUMBERS.replace('line 5\n', 'line 5 changed\n')},
     add_line('line 5 changed', '+line 5 extra')),

    ('new file',
     {'a.txt': 'keep\n'},
     {'a.txt': 'keep\n', 'new.txt': 'alpha\nbeta\n'},
     add_line('beta', '+gamma')),

    ('deleted file',
     {'a.txt': 'keep\n', 'gone.txt': 'one\ntwo\nthree\n'},
     {'a.txt': 'keep\n', 'gone.txt': None},
     None),

    ('single line file',
     {'one.txt': 'only\n'},
     {'one.txt': 'only changed\n'},
     add_line('only changed', '+second line')),

    ('two hunks, offset propagation',
     {'a.txt': NUMBERS},
     {'a.txt': NUMBERS.replace('line 3\n', 'line 3 changed\n').replace('line 18\n', 'line 18 changed\n')},
     add_line('line 3 changed', '+line 3 extra')),

    ('two files, offset reset',
     {'a.txt': NUMBERS, 'b.txt': NUMBERS},
     {'a.txt': NUMBERS.replace('line 2\n', 'line 2 changed\n'),
      'b.txt': NUMBERS.replace('line 15\n', 'line 15 changed\n')},
     add_line('line 2 changed', '+line 2 extra')),

    ('no newline at eof',
     {'a.txt': 'alpha\nbeta'},
     {'a.txt': 'alpha\nbeta changed'},
     None),

    ('blank context lines',
     {'a.txt': 'alpha\n\n\nbeta\n\ngamma\n'},
     {'a.txt': 'alpha\n\n\nbeta changed\n\ngamma\n'},
     None),

    ('file with trailing blank line',
     {'a.txt': 'alpha\nbeta\n\n'},
     {'a.txt': 'alpha\nbeta changed\n\n'},
     None),
]


def run_case(name, before, after, edit, results):
    patch, repo, base = canonical_patch(before, after)
    try:
        ok, err = applies_cleanly(repo, base, patch)
        if not ok:
            raise TestFailure(f'canonical patch does not apply, the test itself is broken: {err}')

        # 1. Scrambled headers must be restored to exactly git's own output.
        restored = recount_patch.recount_text(scramble(patch))
        if restored != patch:
            diff = '\n'.join(f'  got      {g}\n  expected {e}'
                             for g, e in zip(restored.split('\n'), patch.split('\n')) if g != e)
            raise TestFailure(f'scrambled headers not restored to git\'s output:\n{diff}')
        results.append(f'  ok  {name} [scramble]')

        # 2. A realistic body edit must leave a patch git will accept.
        if edit:
            broken = edit(patch)
            still_ok, _ = applies_cleanly(repo, base, broken)
            fixed = recount_patch.recount_text(broken)
            ok, err = applies_cleanly(repo, base, fixed)
            if not ok:
                sys.stderr.write(f'\n--- recounted patch that failed ---\n{fixed}\n')
                raise TestFailure(f'recounted patch rejected by git: {err}')
            warn = ' [WARNING: the edit did not actually stale the headers]' if still_ok else ''
            results.append(f'  ok  {name} [edit]{warn}')
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def corpus_round_trip(results):
    """The repository's own patches must round-trip byte identically.

    This guards against a regression that silently rewrites all of _patches/.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    directory = os.path.join(here, '_patches')
    names = sorted(p for p in os.listdir(directory) if p.endswith('.patch'))
    stale = []
    for name in names:
        with open(os.path.join(directory, name)) as f:
            original = f.read()
        try:
            if recount_patch.recount_text(original) != original:
                stale.append(name)
        except recount_patch.ParseError as e:
            stale.append(f'{name} ({e})')
    if stale:
        detail = '\n'.join(f'         {n}' for n in stale)
        raise TestFailure(f'{len(stale)} of {len(names)} patches changed:\n{detail}')
    results.append(f'  ok  corpus round-trip ({len(names)} patches unchanged)')


def main():
    results, failures = [], []

    for name, before, after, edit in CASES:
        try:
            run_case(name, before, after, edit, results)
        except (TestFailure, subprocess.CalledProcessError) as e:
            results.append(f'  FAIL {name}: {e}')
            failures.append(name)

    try:
        corpus_round_trip(results)
    except TestFailure as e:
        results.append(f'  FAIL corpus round-trip: {e}')
        failures.append('corpus round-trip')

    print('Testing recount-patch.py against git as oracle')
    print('\n'.join(results))
    passed = len([r for r in results if r.startswith('  ok')])
    print(f'\n{passed} passed, {len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
