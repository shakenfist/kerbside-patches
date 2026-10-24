#!/usr/bin/env python3
"""Rank processes on a devstack node by peak RSS, from its memory_tracker log.

Usage:
    memory-tracker-peaks.py screen-memory_tracker.txt [--top N] [--grep REGEX]
    memory-tracker-peaks.py screen-memory_tracker.txt --group

devstack's memory_tracker service logs `ps` output every few seconds as
controller/logs/screen-memory_tracker.txt. This keeps the largest RSS seen for
each pid. --group sums Python service processes per project, which gives the
process counts needed to turn a per-process saving into a per-node one.

The figures are RSS, so pages a forked worker shares with its parent are counted
in both, and ps truncates the command line at 100 characters.
"""
import argparse
import collections
import re

PS_LINE = re.compile(r'memory_tracker\.sh\[\d+\]:\s+(\d+)\s+[\d.]+\s+(\d+)\s+(\d+)\s+\S+\s+(\d+)\s+\S+\s+(.*)$')

# First match wins, so the more specific patterns come first.
GROUPS = [
    ('privsep', r'privsep-helper'),
    ('tempest', r'tempest|stestr'),
    ('manage', r'-manage|config-generator|openstack-cli|/bin/openstack '),
    ('neutron-agent', r'neutron-(openvswitch|dhcp|l3|metadata|metering|ovn)-agent|ovn-metadata'),
    ('neutron-server', r'neutron-apiuWSGI|neutron-server|neutron-(rpc|periodic|ovn-maint)'),
    ('nova', r'nova-'),
    ('cinder', r'cinder-'),
    ('glance', r'glance'),
    ('keystone', r'keystone'),
    ('placement', r'placement'),
    ('swift', r'swift-'),
]


def peaks(path):
    out = {}
    with open(path, errors='replace') as f:
        for line in f:
            m = PS_LINE.search(line)
            if not m:
                continue
            pid, rss, ppid, _nlwp, args = m.groups()
            rss = int(rss)
            if pid not in out or rss > out[pid][0]:
                out[pid] = (rss, ppid, args.strip())
    return out


def is_python_service(args):
    return any(s in args for s in ('python', 'WSGI', 'worker', 'agent'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('log')
    parser.add_argument('--top', type=int, default=60)
    parser.add_argument('--grep', help='only show processes whose command line matches this regex')
    parser.add_argument('--group', action='store_true', help='sum Python service processes per project')
    args = parser.parse_args()

    procs = peaks(args.log)

    if args.group:
        groups = collections.defaultdict(list)
        for rss, _ppid, cmd in procs.values():
            if not is_python_service(cmd):
                continue
            for name, rx in GROUPS:
                if re.search(rx, cmd):
                    groups[name].append(rss >> 10)
                    break
        for name, values in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
            values.sort()
            print('%-15s n=%3d  sum of peak RSS %5d MB  median %4d MB'
                  % (name, len(values), sum(values), values[len(values) // 2]))
        return

    shown = 0
    for pid, (rss, ppid, cmd) in sorted(procs.items(), key=lambda kv: -kv[1][0]):
        if args.grep and not re.search(args.grep, cmd):
            continue
        print('%6d MB  pid %8s  ppid %8s  %s' % (rss >> 10, pid, ppid, cmd[:110]))
        shown += 1
        if shown >= args.top:
            break


if __name__ == '__main__':
    main()
