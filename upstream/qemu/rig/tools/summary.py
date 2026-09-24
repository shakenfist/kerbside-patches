#!/usr/bin/env python3
"""One table row per results/<label>/ directory.

Reads the raw per-run captures that run.sh/run2.sh leave in the rig's
own (gitignored) results/ scratch directory -- not the curated summary
tables in ../../results/, which this script's output is meant to feed.

Env overrides: RIG_DIR (default: this rig checkout, one level up from
tools/), RESULTS_DIR (default: $RIG_DIR/results, the scratch directory
run.sh/run2.sh write to).
"""
import json
import os
import re
import subprocess
import sys

W = os.environ.get('RIG_DIR', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS = os.environ.get('RESULTS_DIR', os.path.join(W, 'results'))


def run(tool, arg):
    out = subprocess.run([sys.executable, os.path.join(W, 'tools', tool), arg],
                         capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else {}


def row(label):
    d = os.path.join(RESULTS, label)
    r = run('ryllana.py', os.path.join(d, 'ryll.out'))
    try:
        t = run('traceana.py', os.path.join(d, 'trace.log'))
    except Exception:
        t = {}
    qlog = open(os.path.join(d, 'qemu.log'), errors='replace').read()
    created = re.findall(r'display_channel_create_stream: stream \d+ (\d+x\d+)', qlog)
    rj = open(os.path.join(d, 'run.json')).read()
    ticks = re.search(r'"(\d+) (\d+)"', rj)
    cpu = (int(ticks.group(2)) - int(ticks.group(1))) / 28.0 if ticks else float('nan')
    geo = list(r.get('geoms', {}).items())[:2]
    return {
        'run': label,
        'draw_copy': r.get('draw_copy'),
        'top draw geoms': ' '.join('%s:%d' % g for g in geo),
        'n_geoms': r.get('n_geoms'),
        'srv stream creates': '%d %s' % (len(created), ','.join(sorted(set(created)))),
        'client streams': ' '.join('%s:%d' % kv for kv in r.get('stream_kinds', {}).items()),
        'stream_data': r.get('stream_data'),
        'MB total': r.get('total_MB'),
        'MB draw': r.get('draw_copy_MB'),
        'MB stream': r.get('stream_MB'),
        'decode_fail': r.get('decode_fail'),
        'batches/s': t.get('update_batches_per_s'),
        'lat p50': t.get('damage_to_cmd_ms_p50'),
        'lat p95': t.get('damage_to_cmd_ms_p95'),
        'qemu cpu%': cpu if cpu != cpu else round(cpu),
        'crash': 'CRASH' in rj or 'Segmentation' in qlog,
    }


rows = [row(label) for label in sys.argv[1:]]
keys = list(rows[0].keys())
print('| ' + ' | '.join(keys) + ' |')
print('|' + '---|' * len(keys))
for r in rows:
    print('| ' + ' | '.join(str(r[k]) for k in keys) + ' |')
