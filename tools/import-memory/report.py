#!/usr/bin/env python3
"""Summarise probe.py output.

Usage:
    report.py PROBE.jsonl                      per-role summary and package table
    report.py PROBE.jsonl --packages a,b,c     the table for just these packages
    report.py PROBE.jsonl --chain PACKAGE      which import loaded PACKAGE in each role

The package table has one column per role and shows how many modules of each
top-level package the role loaded, or '.' for none. A package loaded by a role
that has no reason to call it is a candidate; confirm its cost with marginal.py.
"""
import argparse
import json


def short(role):
    for prefix, abbrev in (('neutron-', 'q-'), ('nova-', 'n-'), ('cinder-', 'c-')):
        if role.startswith(prefix):
            role = abbrev + role[len(prefix):]
    return role[:6]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('probe')
    parser.add_argument('--packages', help='comma separated top-level packages for the table')
    parser.add_argument('--min-modules', type=int, default=20,
                        help='without --packages, show packages that load at least this many modules in some role')
    parser.add_argument('--chain', help='print the import chain for this top-level package')
    args = parser.parse_args()

    with open(args.probe) as f:
        rows = [json.loads(line) for line in f if line.strip()]

    if args.chain:
        for r in rows:
            if args.chain in r['chains']:
                print('%-26s %s' % (r['role'], ' <- '.join(r['chains'][args.chain])))
        return

    for r in rows:
        errors = list(r['errors'].values())
        print('%-26s anon %4d MB  rss %4d MB  modules %5d  %s'
              % (r['role'], r['after']['RssAnon'], r['after']['VmRSS'], r['nmodules'],
                 ('ERROR ' + errors[0][:60]) if errors else ''))
    print()

    if args.packages:
        packages = args.packages.split(',')
    else:
        packages = sorted({p for r in rows for p, n in r['packages'].items() if n >= args.min_modules})
    print('%-20s' % 'package' + ''.join('%7s' % short(r['role']) for r in rows))
    for p in packages:
        counts = [r['packages'].get(p, 0) for r in rows]
        if any(counts):
            print('%-20s' % p[:20] + ''.join('%7s' % (n or '.') for n in counts))


if __name__ == '__main__':
    main()
