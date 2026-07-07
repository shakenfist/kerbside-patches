#!/usr/bin/env python3
"""Verify that a healed data PR merge only appends well-formed records.

After tools/heal-data-prs.sh union-merges develop into a data PR branch, this
script compares the result (HEAD) against the base ref and checks that:

  - every modified text file only gains lines (the append-only invariant --
    binary files such as regenerated charts are exempt, since line counts do
    not apply to them);
  - every line added to a .jsonl file is valid JSON and carries the run id
    the PR branch name promises;
  - no .jsonl file ends up with duplicate (run_id, run_attempt) records.

Exits non-zero if any check fails, in which case the caller must not push
the merge.

Usage:
    verify-data-merge.py --base origin/develop --run-id 28792754633
"""

import argparse
import collections
import json
import subprocess
import sys


def git_output(args):
    return subprocess.run(['git'] + args, capture_output=True, text=True, check=True).stdout


def main():
    parser = argparse.ArgumentParser(description='Verify a data PR merge result is append-only and well formed.')
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
        if removed != '0':
            errors.append('%s: removes %s lines, but data merges must be append-only' % (path, removed))
        if path.endswith('.jsonl'):
            jsonl_files.append(path)

    for path in jsonl_files:
        # Every line added relative to the base must be a record from this
        # PR's run: anything else means the merge resurrected or mangled
        # existing data.
        for line in git_output(['diff', '%s..HEAD' % args.base, '--', path]).splitlines():
            if not line.startswith('+') or line.startswith('+++'):
                continue
            try:
                record = json.loads(line[1:])
            except json.JSONDecodeError:
                errors.append('%s: added line is not valid JSON' % path)
                continue
            if record.get('run_id') != args.run_id:
                errors.append('%s: added record has run_id %s, expected %s'
                              % (path, record.get('run_id'), args.run_id))

        # The whole merged file must still parse, one record per run.
        seen = collections.Counter()
        with open(path) as datafile:
            for lineno, line in enumerate(datafile, 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    errors.append('%s:%d: line is not valid JSON' % (path, lineno))
                    continue
                seen[(record.get('run_id'), record.get('run_attempt'))] += 1
        for (run_id, run_attempt), count in seen.items():
            if count > 1:
                errors.append('%s: %d records for run %s attempt %s, expected at most 1'
                              % (path, count, run_id, run_attempt))

    if errors:
        for error in errors:
            print('ERROR: %s' % error, file=sys.stderr)
        sys.exit(1)

    print('Verified %d changed text files (%d jsonl) against %s.' % (text_files, len(jsonl_files), args.base))


if __name__ == '__main__':
    main()
