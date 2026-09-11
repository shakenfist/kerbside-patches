#!/usr/bin/env python3
"""Recompute the @@ hunk headers in a unified diff.

Editing a patch in _patches/ by hand means keeping the line counts in every
``@@ -old,N +new,M @@`` header in step with the body, which is fiddly and easy
to get wrong. This recomputes them from the body.

Usage:
    ./recount-patch.py _patches/patch008-use-routable-ip-for-spice-consoles.patch
    ./recount-patch.py --in-place _patches/patch008-*.patch
    ./recount-patch.py --check _patches/*.patch

Exit status:
    0   headers are correct (--check), or the recount succeeded
    1   headers are stale (--check), or a file could not be parsed

Why not patchutils' recountdiff? It solves the same problem, but scores
slightly worse on this repository's patches (164/175 vs 167/175 round-tripping
unchanged) and would add an apt dependency to CI. See docs/patch-editing.md.
"""

import argparse
import re
import sys

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$')

# Lines that end a hunk body even though they may start with a diff-ish
# character. A hunk runs until the next hunk or the next file's header.
FILE_START = ('--- ', 'diff --git ', 'index ', 'Index: ')

# git format-patch ends the diff with "-- " followed by the git version. That
# leading '-' is not a deleted line.
VERSION = re.compile(r'^\d+\.\d')


class ParseError(Exception):
    """The file is not a unified diff we understand."""


def _range(start, count):
    """Render one side of a hunk header.

    Unified diff elides the count when it is 1, so ``-1,1`` is written ``-1``.
    """
    return str(start) if count == 1 else f'{start},{count}'


def _is_signature(lines, i):
    """True if lines[i] is git format-patch's "-- " signature separator."""
    return lines[i] == '-- ' and i + 1 < len(lines) and VERSION.match(lines[i + 1])


def _body_extent(lines, body):
    """Find where the hunk body starting at `body` ends.

    Returns (end, counts) where counts maps a trailing-blank exclusion count k
    to the (old, new) totals that result from ignoring the last k
    whitespace-only lines. See _resolve_trailing_blanks for why k matters.
    """
    j, old_n, new_n = body, 0, 0
    per_line = []
    while j < len(lines):
        line = lines[j]
        if _is_signature(lines, j) or HUNK.match(line) or line.startswith(FILE_START):
            break
        if line.startswith('\\'):
            # "\ No newline at end of file" annotates the previous line and is
            # counted on neither side.
            j += 1
            continue
        marker = line[:1]
        if marker == '-':
            old_n += 1
            per_line.append((line, 1, 0))
        elif marker == '+':
            new_n += 1
            per_line.append((line, 0, 1))
        elif marker == ' ' or line == '':
            # Context. A bare empty line is a context line whose single leading
            # space was stripped, which some editors and mail paths do.
            old_n += 1
            new_n += 1
            per_line.append((line, 1, 1))
        else:
            # Prose after the final hunk (the commit trailer, say).
            break
        j += 1

    counts = {0: (old_n, new_n)}
    o, n = old_n, new_n
    for k, (line, do, dn) in enumerate(reversed(per_line), start=1):
        if line.strip():
            break
        o -= do
        n -= dn
        counts[k] = (o, n)
    return j, counts


def _resolve_trailing_blanks(counts, old_decl, new_decl):
    """Decide how many trailing whitespace-only lines fall outside the hunk.

    This is the one genuinely ambiguous part of recounting. git reads a hunk by
    consuming exactly as many lines as the header declares and ignoring
    whatever follows, so a blank line sitting between the last real body line
    and the next "diff --git" may or may not belong to the hunk -- the body
    alone cannot say. patchutils' recountdiff gets this wrong on the same
    patches we do.

    We break the tie with the header we were given: if ignoring k trailing
    blanks makes *both* sides agree with the declared counts, the original
    author's intent was to exclude them, so we preserve that. An edit inside
    the body perturbs one side or both, no k matches, and we fall back to
    counting every line -- which is the safe direction, since a header that is
    too large makes git reject the patch loudly rather than apply it wrongly.
    """
    for k, (old_n, new_n) in sorted(counts.items()):
        if old_n == old_decl and new_n == new_decl:
            return old_n, new_n
    return counts[0]


def recount(lines):
    """Return `lines` with every hunk header recomputed from its body."""
    out, i, delta = [], 0, 0
    while i < len(lines):
        match = HUNK.match(lines[i])
        if not match:
            if lines[i].startswith('--- '):
                # A new file restarts the running old->new line offset.
                delta = 0
            out.append(lines[i])
            i += 1
            continue

        old_start = int(match.group(1))
        old_decl = int(match.group(2)) if match.group(2) else 1
        new_decl = int(match.group(4)) if match.group(4) else 1

        body = i + 1
        end, counts = _body_extent(lines, body)
        old_n, new_n = _resolve_trailing_blanks(counts, old_decl, new_decl)

        new_start = old_start + delta
        if not new_n:
            # Deleting a file: the new side is empty and starts at 0.
            new_start = 0
        elif not new_start:
            # Creating a file: -0,0 +1,N.
            new_start = 1
        delta += new_n - old_n

        out.append(f'@@ -{_range(old_start, old_n)} +{_range(new_start, new_n)} @@{match.group(5)}')
        out.extend(lines[body:end])
        i = end

    return out


def recount_text(text):
    """Recount a patch held as a string, preserving its trailing newline."""
    trailing_nl = text.endswith('\n')
    lines = text.split('\n')
    if trailing_nl:
        # split() leaves a phantom empty element after the final newline, which
        # would otherwise be counted as a context line.
        lines.pop()
    if not any(HUNK.match(line) for line in lines):
        raise ParseError('no hunk headers found')
    return '\n'.join(recount(lines)) + ('\n' if trailing_nl else '')


def main():
    parser = argparse.ArgumentParser(description='Recompute @@ hunk headers in a unified diff.')
    parser.add_argument('patches', nargs='+', metavar='PATCH', help='patch files to recount')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-i', '--in-place', action='store_true', help='rewrite the files instead of printing them')
    group.add_argument('-c', '--check', action='store_true',
                       help='exit non-zero if any header is stale, changing nothing')
    args = parser.parse_args()

    if not args.in_place and not args.check and len(args.patches) > 1:
        parser.error('refusing to concatenate several patches to stdout; use --in-place or --check')

    stale, failed = [], []
    for path in args.patches:
        try:
            with open(path) as f:
                original = f.read()
            fixed = recount_text(original)
        except (OSError, ParseError) as e:
            print(f'{path}: {e}', file=sys.stderr)
            failed.append(path)
            continue

        if args.check:
            if fixed != original:
                stale.append(path)
                print(f'{path}: hunk headers are stale', file=sys.stderr)
        elif args.in_place:
            if fixed != original:
                with open(path, 'w') as f:
                    f.write(fixed)
                print(f'{path}: recounted')
        else:
            sys.stdout.write(fixed)

    return 1 if stale or failed else 0


if __name__ == '__main__':
    sys.exit(main())
