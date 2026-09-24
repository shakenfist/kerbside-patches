#!/usr/bin/env python3
"""Summarise display-channel traffic from a `ryll -v` log.

Usage: ryllana.py <ryll.out path>; prints one JSON object to stdout.
Fed run.sh/run2.sh's ryll.out (RYLL_ARGS must include -v). Called by
summary.py, which also needs a matching qemu.log and trace.log.

Only messages received after the animation starts (first stream_data or
first draw_copy after the initial full-screen one + 3 s) are counted.
"""
import collections
import datetime
import json
import re
import sys

ANSI = re.compile(r'\x1b\[[0-9;]*m')
RECV = re.compile(r'^(\S+)Z\s+DEBUG display received (\d+) byte opcode (\d+) (\w+)')
RECT = re.compile(r'\.\.\. surface=\d+, rect=\((\d+),(\d+)\) to \((\d+),(\d+)\)')
SCREATE = re.compile(r'stream_create: id=(\d+), surface=\d+, codec=(\d+), (\d+x\d+)')
CODECS = {'1': 'MJPEG', '2': 'VP8', '3': 'H264', '4': 'VP9', '5': 'H265'}


def main(path):
    counts = collections.Counter()
    nbytes = collections.Counter()
    geoms = collections.Counter()
    creates = []
    decode_fail = 0
    t0 = t1 = None
    last = None
    for line in open(path, errors='replace'):
        line = ANSI.sub('', line)
        m = RECV.match(line)
        if m:
            t = datetime.datetime.fromisoformat(m.group(1)).timestamp()
            t0 = t0 or t
            t1 = t
            last = m.group(4)
            counts[last] += 1
            nbytes[last] += int(m.group(2))
            continue
        m = RECT.search(line)
        if m and last == 'draw_copy':
            x0, y0, x1, y1 = map(int, m.groups())
            geoms['%dx%d' % (x1 - x0, y1 - y0)] += 1
            last = None
            continue
        m = SCREATE.search(line)
        if m:
            creates.append('%s:%s' % (CODECS.get(m.group(2), m.group(2)), m.group(3)))
        if 'decode failed' in line:
            decode_fail += 1
    total = sum(nbytes.values())
    print(json.dumps({
        'window_s': round(t1 - t0, 1),
        'total_MB': round(total / 1e6, 2),
        'draw_copy': counts['draw_copy'],
        'draw_copy_MB': round(nbytes['draw_copy'] / 1e6, 2),
        'geoms': dict(geoms.most_common(4)),
        'n_geoms': len(geoms),
        'stream_create': len(creates),
        'stream_kinds': dict(collections.Counter(creates)),
        'stream_data': counts['stream_data'] + counts['stream_data_sized'],
        'stream_MB': round((nbytes['stream_data'] + nbytes['stream_data_sized']) / 1e6, 2),
        'stream_destroy': counts['stream_destroy'],
        'stream_clip': counts['stream_clip'],
        'decode_fail': decode_fail,
    }, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
