#!/usr/bin/env python3
"""Summarise a ryll display.pcap: message counts, DRAW_COPY rects, streams.

Usage: spicepcap.py <display.pcap path>; prints one JSON object to
stdout. Fed a capture from `ryll --headless --capture <dir>` (RYLL_ARGS
in run.sh/run2.sh). Not called by summary.py -- run separately when a
capture is needed. As of the 2026-09-23 session that produced this
rig, ryll's --capture had two known bugs (a startup panic, and message
framing that could drift mid-capture); see the ryll issue tracker
before relying on a capture's ordering.
"""
import collections
import json
import struct
import sys

NAMES = {
    1: 'MIGRATE', 2: 'MIGRATE_DATA', 3: 'SET_ACK', 4: 'PING', 5: 'WAIT_FOR_CHANNELS',
    6: 'DISCONNECTING', 7: 'NOTIFY', 101: 'MODE', 102: 'MARK', 103: 'RESET',
    104: 'COPY_BITS', 105: 'INVAL_LIST', 106: 'INVAL_ALL_PIXMAPS',
    107: 'INVAL_PALETTE', 108: 'INVAL_ALL_PALETTES', 122: 'STREAM_CREATE',
    123: 'STREAM_DATA', 124: 'STREAM_CLIP', 125: 'STREAM_DESTROY',
    126: 'STREAM_DESTROY_ALL', 302: 'DRAW_FILL', 303: 'DRAW_OPAQUE',
    304: 'DRAW_COPY', 305: 'DRAW_BLEND', 314: 'SURFACE_CREATE',
    315: 'SURFACE_DESTROY', 316: 'STREAM_DATA_SIZED', 317: 'MONITORS_CONFIG',
    318: 'DRAW_COMPOSITE', 319: 'STREAM_ACTIVATE_REPORT', 320: 'GL_SCANOUT_UNIX',
    321: 'GL_DRAW',
}
CODECS = {1: 'MJPEG', 2: 'VP8', 3: 'H264', 4: 'VP9', 5: 'H265'}


def frames(path):
    with open(path, 'rb') as f:
        data = f.read()
    off = 24
    self_endian = '>' if data[:4] == b'\xa1\xb2\xc3\xd4' else '<'
    while off + 16 <= len(data):
        sec, usec, incl, _ = struct.unpack_from(self_endian + 'IIII', data, off)
        off += 16
        pkt = data[off:off + incl]
        off += incl
        sport = struct.unpack_from('>H', pkt, 34)[0]
        yield sec + usec / 1e6, sport, pkt[54:]


def valid_chain(buf, off, n=6):
    for _ in range(n):
        if off + 6 > len(buf):
            return True
        mtype, size = struct.unpack_from('<HI', buf, off)
        if mtype not in NAMES or size > 5 * 1024 * 1024 or (mtype == 3 and size != 8) or (mtype == 4 and size < 12):
            return False
        off += 6 + size
    return True


def main(path):
    streams = collections.defaultdict(bytearray)
    times = collections.defaultdict(list)
    for t, sport, payload in frames(path):
        times[sport].append((len(streams[sport]), t))
        streams[sport] += payload
    # the server->client direction is the one with the most bytes
    sport = 5900  # ryll records the server side with source port 5900
    buf = bytes(streams[sport])
    tl = times[sport]
    counts = collections.Counter()
    nbytes = collections.Counter()
    rects = collections.Counter()
    creates = []
    first_t = tl[0][1] if tl else 0
    last_t = first_t
    ti = 0
    off = 0
    resyncs = 0
    skipped = 0
    while off + 6 <= len(buf):
        if not valid_chain(buf, off):
            start = off
            off += 1
            while off + 6 <= len(buf) and not valid_chain(buf, off):
                off += 1
            resyncs += 1
            skipped += off - start
            continue
        mtype, size = struct.unpack_from('<HI', buf, off)
        while ti + 1 < len(tl) and tl[ti + 1][0] <= off:
            ti += 1
        last_t = tl[ti][1]
        body = buf[off + 6:off + 6 + size]
        name = NAMES.get(mtype, str(mtype))
        counts[name] += 1
        nbytes[name] += size + 6
        if mtype in (302, 303, 304, 305):
            _, top, left, bottom, right = struct.unpack_from('<Iiiii', body, 0)
            rects['%dx%d' % (right - left, bottom - top)] += 1
        elif mtype == 122:
            sid, flags, codec = struct.unpack_from('<IBB', body, 4)
            sw, sh = struct.unpack_from('<II', body, 18)
            top, left, bottom, right = struct.unpack_from('<iiii', body, 34)
            creates.append({'id': sid, 'codec': CODECS.get(codec, codec), 'size': '%dx%d' % (sw, sh),
                            'dest': '%dx%d@%d,%d' % (right - left, bottom - top, left, top),
                            't': round(last_t - first_t, 2)})
        off += 6 + size
    total = sum(nbytes.values())
    out = {
        'duration_s': round(last_t - first_t, 2),
        'resyncs': resyncs,
        'skipped_bytes': skipped,
        'raw_bytes': len(buf),
        'total_bytes': total,
        'counts': dict(counts.most_common()),
        'bytes': dict(nbytes.most_common()),
        'draw_rects_top': dict(rects.most_common(12)),
        'distinct_draw_geometries': len(rects),
        'stream_creates': creates[:20],
    }
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
