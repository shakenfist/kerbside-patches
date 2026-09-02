#!/usr/bin/env python3
"""Verify that a healed data PR merge only makes the changes a data PR is allowed to.

After tools/heal-data-prs.sh union-merges develop into a data PR branch, this
script compares the result (HEAD) against the base ref and checks that:

  - every modified text file other than a .jsonl series only gains lines (binary
    files such as regenerated charts are exempt, since line counts do not apply
    to them);
  - every record a .jsonl file gains carries the run id the PR branch name
    promises;
  - a .jsonl series only ever loses its oldest records, which is what the
    retention cap in tools/collect-layer-data.py does. Losing a record that is
    newer than one still present means the merge dropped live data rather than
    trimming history;
  - no .jsonl series ends up with duplicate (run_id, run_attempt) records;
  - the command dictionaries never map one hash to two different commands, and
    every hash a record refers to can still be resolved in them.

Exits non-zero if any check fails, in which case the caller must not push
the merge.

Usage:
    verify-data-merge.py --base origin/develop --run-id 28792754633
"""

import argparse
import collections
import json
import os
import subprocess
import sys


# Written by tools/collect-layer-data.py alongside the per-image series files.
COMMANDS_FILE = 'commands.jsonl'


def git_output(args):
    return subprocess.run(['git'] + args, capture_output=True, text=True, check=True).stdout


def parse_records(text, path, errors):
    """Parse JSONL text into records, recording a problem for any unparseable line."""
    records = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            errors.append('%s:%d: line is not valid JSON' % (path, lineno))
    return records


def show(ref, path):
    """Return a file's contents at a ref, or an empty string if it did not exist."""
    result = subprocess.run(['git', 'show', '%s:%s' % (ref, path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return ''
    return result.stdout


def record_key(record):
    """Chronological identity of a series record."""
    return (record.get('datestamp', ''), record.get('run_id', ''), record.get('run_attempt', ''))


def check_series(path, base_records, head_records, run_id, errors):
    """Check a per-image series file: appends belong to this run, losses are trims."""
    base_keys = {record_key(r) for r in base_records}
    head_keys = {record_key(r) for r in head_records}

    for key in head_keys - base_keys:
        if key[1] != run_id:
            errors.append('%s: added record has run_id %s, expected %s' % (path, key[1], run_id))

    # The retention cap drops the oldest records, so a lost record is only
    # acceptable if everything still present is newer than it.
    removed = base_keys - head_keys
    if removed and head_keys:
        newest_removed = max(removed)
        oldest_retained = min(head_keys)
        if newest_removed > oldest_retained:
            errors.append('%s: dropped record %s is newer than retained record %s, which is '
                          'data loss rather than a retention trim'
                          % (path, newest_removed, oldest_retained))

    seen = collections.Counter()
    for record in head_records:
        seen[(record.get('run_id'), record.get('run_attempt'))] += 1
    for (seen_run_id, run_attempt), count in seen.items():
        if count > 1:
            errors.append('%s: %d records for run %s attempt %s, expected at most 1'
                          % (path, count, seen_run_id, run_attempt))


def check_commands(path, base_records, head_records, run_id, errors):
    """Check a command dictionary: this run's additions, and one text per hash.

    The dictionary is not checked for removals. A union merge duplicates its entries
    whenever two data PRs both create it from scratch, and the next run rewrites the
    file without those duplicates, so lines legitimately disappear. What matters is
    that no hash a record refers to becomes unresolvable, which check_references
    covers, and that a hash never changes meaning.
    """
    head = {}
    for record in head_records:
        key = record.get('cmd')
        if not key:
            errors.append('%s: entry without a cmd hash' % path)
            continue
        if key in head and head[key] != record.get('command', ''):
            errors.append('%s: hash %s maps to two different commands' % (path, key))
        head[key] = record.get('command', '')

    base = {r.get('cmd') for r in base_records if r.get('cmd')}

    for record in head_records:
        key = record.get('cmd')
        if key and key not in base and record.get('run_id') != run_id:
            errors.append('%s: added entry %s has run_id %s, expected %s'
                          % (path, key, record.get('run_id'), run_id))


def check_references(path, head_records, commands, errors):
    """Check that every command hash a series record refers to can be resolved."""
    for record in head_records:
        for layer in record.get('layers', []):
            key = layer.get('cmd')
            if key and key not in commands:
                errors.append('%s: record %s refers to command %s, which is not in the '
                              'dictionary' % (path, record.get('run_id'), key))
                return


def main():
    parser = argparse.ArgumentParser(description='Verify a data PR merge result is well formed.')
    parser.add_argument('--base', default='origin/develop', help='ref to compare HEAD against')
    parser.add_argument('--run-id', required=True, help='CI run id whose records this PR is expected to add')
    args = parser.parse_args()

    errors = []
    jsonl_files = []
    text_files = 0

    for line in git_output(['diff', '--numstat', '%s..HEAD' % args.base]).splitlines():
        added, removed, path = line.split('\t', 2)
        if added == '-':
            # A binary file, for example a regenerated chart.
            continue
        text_files += 1
        if path.endswith('.jsonl'):
            jsonl_files.append(path)
        elif removed != '0':
            # Only the series files have a retention policy; everything else a
            # data PR touches is still strictly append-only.
            errors.append('%s: removes %s lines, but data merges must be append-only' % (path, removed))

    dictionaries = {}
    for path in jsonl_files:
        # Both sides are read from git rather than from the working tree, so that
        # what is checked is exactly what a push would publish.
        base_records = parse_records(show(args.base, path), '%s (%s)' % (path, args.base), errors)
        head_records = parse_records(show('HEAD', path), path, errors)

        if path.endswith('/' + COMMANDS_FILE):
            check_commands(path, base_records, head_records, args.run_id, errors)
        else:
            check_series(path, base_records, head_records, args.run_id, errors)

            build_dir = os.path.dirname(path)
            if build_dir not in dictionaries:
                commands_path = os.path.join(build_dir, COMMANDS_FILE)
                dictionaries[build_dir] = {
                    r.get('cmd') for r in parse_records(show('HEAD', commands_path),
                                                        commands_path, errors)}
            check_references(path, head_records, dictionaries[build_dir], errors)

    if errors:
        for error in errors:
            print('ERROR: %s' % error, file=sys.stderr)
        sys.exit(1)

    print('Verified %d changed text files (%d jsonl) against %s.' % (text_files, len(jsonl_files), args.base))


if __name__ == '__main__':
    main()
