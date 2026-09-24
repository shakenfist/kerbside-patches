#!/usr/bin/env python3
"""Damage -> QXL command latency and update stats from a qemu trace log.

Usage: traceana.py <trace.log path>; prints one JSON object to stdout.
Fed run.sh/run2.sh's trace.log (qemu -trace qemu_spice_display_update
-trace qemu_spice_create_update -trace qemu_spice_display_refresh -D
trace.log -msg timestamp=on). Called by summary.py.

Latency is measured for the video rect damage (the most frequent
display_update geometry): time from each qemu_spice_display_update to
the first qemu_spice_create_update that follows it. Only the
steady-state window (after the first 200 video damage events) is used.
"""
import collections
import datetime
import json
import re
import statistics
import sys

TS = re.compile(r'^(\S+)Z (\S+) (.*)$')


def ts(s):
    return datetime.datetime.fromisoformat(s).timestamp()


def main(path):
    events = []
    for line in open(path):
        m = TS.match(line)
        if m and m.group(2).startswith('qemu_spice_'):
            events.append((ts(m.group(1)), m.group(2), m.group(3)))
    geoms = collections.Counter(e[2] for e in events if e[1] == 'qemu_spice_display_update')
    video = geoms.most_common(1)[0][0]
    pending = []
    lat = []
    creates = 0
    batches = 0
    last_create = 0
    seen = 0
    first = last = None
    for t, name, arg in events:
        if name == 'qemu_spice_display_update' and arg == video:
            seen += 1
            if seen > 200:
                pending.append(t)
                first = first or t
                last = t
        elif name == 'qemu_spice_create_update':
            if first:
                creates += 1
                if t - last_create > 0.002:
                    batches += 1
            last_create = t
            for p in pending:
                lat.append((t - p) * 1000)
            pending = []
    lat.sort()
    dur = last - first
    q = statistics.quantiles(lat, n=20)
    print(json.dumps({
        'video_damage': video,
        'window_s': round(dur, 1),
        'video_damage_per_s': round((seen - 200) / dur, 1),
        'draw_copies_per_s': round(creates / dur, 1),
        'update_batches_per_s': round(batches / dur, 1),
        'damage_to_cmd_ms_p50': round(statistics.median(lat), 1),
        'damage_to_cmd_ms_p95': round(q[18], 1),
        'damage_to_cmd_ms_max': round(lat[-1], 1),
    }, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
