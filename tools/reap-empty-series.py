#!/usr/bin/env python3
"""Reap the series with no patches left, offline.

A series' patches merge upstream one at a time, and the daily rebase drops
each merged patch from the series' ORDER as it goes. When the last one goes
the series is finished, and what is left is litter: an empty ORDER, a
config.yaml pinning a `source_sha` that nothing reads, and an entry in
series.yaml. Reaping it means

    - deleting the series directory;
    - deleting its series.yaml entry, and the project it was filed under
      when that project holds no other series;
    - dropping it from every `after` that names it. Those series were
      waiting for it to reach Gerrit, and it has merged.

A series with `pin: true` in series.yaml carries no patches by design: it
is there so the build pins a repository it does not patch. It is never
reaped, however empty its ORDER.

Nothing here touches git. `_build/rebase-with-claude.sh` runs this, and the
daily rebase workflow commits what it leaves behind.

Usage:
    tools/reap-empty-series.py [--root DIR] [--list | --apply]

With neither --list nor --apply this reports what would be reaped and what
waits on it, and changes nothing. --list prints just the series names, one
per line, for a shell to read. --apply reaps them.

--root names the repository to reap in; it defaults to the one holding this
script.

Exit status:
    0   nothing to reap, or --list or --apply did what was asked
    1   there is something to reap and neither --list nor --apply was given
    2   series.yaml is missing or invalid, or the reap could not be made
        safely, in which case nothing was changed
"""

import argparse
import os
import re
import shutil
import sys

import yaml

import series

# series.yaml is hand written: it carries comments, and its series are
# grouped by epic rather than sorted. A YAML round trip would throw all of
# that away, so entries are removed as lines of text. A project is a key at
# the left margin, a series is a key indented by two, and an entry's own
# keys are indented by four.
PROJECT = re.compile(r'^(\S[^:#]*):\s*$')
ENTRY = re.compile(r'^  (\S[^:#]*):\s*$')
FLOW_AFTER = re.compile(r'^(\s*)after:\s*\[(.*)\]\s*$')
BLOCK_AFTER = re.compile(r'^\s*after:\s*$')
BLOCK_ITEM = re.compile(r'^\s*-\s*(\S.*?)\s*$')


class ReapError(Exception):
    """The reap could not be made safely, so nothing was changed."""


##############################################################################
# What is finished                                                           #
##############################################################################

def empty_series(index, root):
    """Return the series in *index* whose ORDER lists no patches, sorted by name.

    A series with `pin: true` is meant to have none. A series with no
    directory at all is left to tools/check-series-index.py to report.
    """
    found = []
    for name, entry in sorted(index.items()):
        if entry.pin or not os.path.isfile(os.path.join(root, name, 'ORDER')):
            continue
        if not series.order_patches(root, name):
            found.append(name)
    return found


def waiting_on(index, name, reaped=()):
    """Return the surviving series whose `after` names *name*, sorted."""
    return sorted(other for other, entry in index.items() if name in entry.after and other not in reaped)


##############################################################################
# Rewriting series.yaml                                                      #
##############################################################################

def _entry_end(lines, start):
    """Return the index one past the entry that starts at lines[start].

    An entry runs to the first line that is not indented by at least four
    spaces, so a blank line, a comment between epics, or the next series
    ends it.
    """
    end = start + 1
    while end < len(lines) and lines[end].startswith('    ') and lines[end].strip():
        end += 1
    return end


def _project_end(lines, start):
    """Return the index one past the project block that starts at lines[start].

    The block runs to the next line beginning at the left margin, so it
    holds the project's series, the comments between them, and the blank
    line that separates it from the next project.
    """
    end = start + 1
    while end < len(lines) and not lines[end][:1].strip():
        end += 1
    return end


def _drop_entries(lines, names):
    """Return *lines* without the entries of the series in *names*."""
    kept, position = [], 0
    while position < len(lines):
        match = ENTRY.match(lines[position])
        if match and match.group(1) in names:
            position = _entry_end(lines, position)
            continue
        kept.append(lines[position])
        position += 1
    return kept


def _drop_after(lines, names):
    """Return *lines* with the series in *names* gone from every `after`.

    An `after` left with nothing in it loses its line, because an empty
    list says the same thing as no key at all and reads worse.
    """
    kept, position = [], 0
    while position < len(lines):
        line = lines[position]
        if BLOCK_AFTER.match(line):
            end = position + 1
            items = []
            while end < len(lines) and BLOCK_ITEM.match(lines[end]):
                items.append(BLOCK_ITEM.match(lines[end]).group(1))
                end += 1
            if set(items) & names:
                raise ReapError(f'{series.INDEX} writes an after as a list of indented lines, which this tool '
                                f'does not rewrite: drop {", ".join(sorted(set(items) & names))} from it by hand')
            kept.extend(lines[position:end])
            position = end
            continue

        match = FLOW_AFTER.match(line)
        if not match:
            kept.append(line)
            position += 1
            continue
        items = [item.strip() for item in match.group(2).split(',') if item.strip()]
        left = [item for item in items if item not in names]
        if left == items:
            kept.append(line)
        elif left:
            kept.append(f'{match.group(1)}after: [{", ".join(left)}]')
        position += 1
    return kept


def _drop_dangling_comments(lines):
    """Return *lines* without the comments heading a group that has no series left.

    Series are grouped inside a project by a comment -- "# Fixes", "# Not yet
    pushed" -- and a blank line. Reaping the last series of a group would
    otherwise leave its heading behind with nothing under it.
    """
    kept, position = [], 0
    while position < len(lines):
        if not lines[position].startswith(' ') or not lines[position].lstrip().startswith('#'):
            kept.append(lines[position])
            position += 1
            continue
        end = position
        while end < len(lines) and lines[end].startswith(' ') and lines[end].lstrip().startswith('#'):
            end += 1
        # The group these comments head runs to the next blank line, the next
        # comment, or the end of the project.
        group = end
        while group < len(lines) and lines[group].strip() and not lines[group].lstrip().startswith('#'):
            group += 1
        if any(ENTRY.match(line) for line in lines[end:group]):
            kept.extend(lines[position:end])
        elif group < len(lines) and not lines[group].strip():
            end = group + 1
        position = end
    return kept


def _drop_leading_blanks(lines):
    """Return *lines* without a blank line left directly under a project key.

    Reaping the first series of a project otherwise leaves the blank line
    that separated it from the group below.
    """
    kept = []
    for line in lines:
        if not line.strip() and kept and PROJECT.match(kept[-1]):
            continue
        kept.append(line)
    return kept


def _drop_empty_projects(lines):
    """Return *lines* without the projects that have no series left.

    The comments written immediately above such a project go with it; a
    blank line between them and the project key means they are not its.
    """
    kept, position = [], 0
    while position < len(lines):
        if not PROJECT.match(lines[position]):
            kept.append(lines[position])
            position += 1
            continue
        end = _project_end(lines, position)
        if any(ENTRY.match(line) for line in lines[position + 1:end]):
            kept.extend(lines[position:end])
        else:
            while kept and kept[-1].lstrip().startswith('#'):
                kept.pop()
        position = end
    return kept


def remove_entries(text, names):
    """Return series.yaml *text* with the series in *names* reaped from it."""
    names = set(names)
    lines = _drop_entries(text.splitlines(), names)
    lines = _drop_after(lines, names)
    lines = _drop_dangling_comments(lines)
    lines = _drop_leading_blanks(lines)
    lines = _drop_empty_projects(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines) + '\n'


def check_rewrite(text, names, index):
    """Raise ReapError unless *text* is *index* with exactly *names* reaped from it."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ReapError(f'the rewritten {series.INDEX} is not valid YAML, so nothing was changed: {e}')
    problems = series.validate(raw)
    if problems:
        raise ReapError(f'the rewritten {series.INDEX} is invalid, so nothing was changed:\n  '
                        + '\n  '.join(problems))
    left = {name for entries in (raw or {}).values() for name in entries}
    expected = set(index) - set(names)
    if left != expected:
        missing = sorted(expected - left)
        extra = sorted(left - expected)
        raise ReapError(f'the rewritten {series.INDEX} would lose {", ".join(missing) or "nothing"} and keep '
                        f'{", ".join(extra) or "nothing"} it should not, so nothing was changed')


##############################################################################
# Doing it                                                                   #
##############################################################################

def reap(root, names, index=None):
    """Reap *names*: rewrite series.yaml, then delete the series directories."""
    index = index if index is not None else series.load(root)
    with open(series.path(root)) as f:
        text = f.read()
    rewritten = remove_entries(text, names)
    check_rewrite(rewritten, names, index)
    with open(series.path(root), 'w') as f:
        f.write(rewritten)
    for name in names:
        shutil.rmtree(os.path.join(root, name))


def report(index, names):
    """Print what reaping *names* would do, a line or two per series."""
    for name in names:
        print(f'{name} ({index[name].project}) has no patches left in its ORDER.')
        waiting = waiting_on(index, name, reaped=names)
        if waiting:
            plural = 'series waits' if len(waiting) == 1 else 'series wait'
            print(f'  {len(waiting)} {plural} on it in after, and would lose that link: {", ".join(waiting)}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--root', default=series.TOPDIR,
                        help='the repository to reap in (default: %(default)s)')
    what = parser.add_mutually_exclusive_group()
    what.add_argument('--list', action='store_true', help='print only the series names, one per line')
    what.add_argument('--apply', action='store_true', help='reap them, rather than only reporting')
    args = parser.parse_args(argv)

    try:
        index = series.load(args.root)
    except series.SeriesError as e:
        print(e, file=sys.stderr)
        return 2

    names = empty_series(index, args.root)

    if args.list:
        for name in names:
            print(name)
        return 0

    if not names:
        print(f'Every series in {series.INDEX} still has patches to carry; nothing to reap.')
        return 0

    report(index, names)

    if not args.apply:
        print(f'Run tools/reap-empty-series.py --apply to delete {"them" if len(names) > 1 else "it"} '
              f'and {"their" if len(names) > 1 else "its"} {series.INDEX} entries.')
        return 1

    try:
        reap(args.root, names, index=index)
    except (ReapError, series.SeriesError, OSError) as e:
        print(e, file=sys.stderr)
        return 2

    print(f'Reaped {len(names)} series. Commit the deleted directories and {series.INDEX}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
