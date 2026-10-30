#!/usr/bin/env python3
"""Summarise shaped-link runs of spice-server builds as Markdown tables.

Usage:
  summarise-spice.py [--preamble FILE] [--rule-floor MS] RUN[:LABEL] ... \\
      --compare RUN_NAME:CHANGE=BASE[:TITLE] ...

Each RUN is the WORKDIR of one kerbside tools/shaped-link/run-matrix.sh
invocation: one qemu, one codec and one max-refresh-rate, with several
DISPLAYS entries (one per spice-server build) interleaved inside each pass.
Its case directories are <profile>/<activity>/<display>-<config>-r<N>/.

For every display and cell (profile x activity) the first table gives:

- repeats good, and lost to a qemu exit (run-matrix.sh's qemu-died marker)
  or a setup failure;
- key-press-to-draw latency p50 and p95, pooled over the good repeats, and
  the range of the per-repeat p50s;
- qemu's CPU in cores over the key-press window (mean of the repeats) and
  its peak RSS over the whole case (largest of the repeats), from proc.txt;
- the display channel's relayed throughput, from the proxy's /metrics, or
  from the client-leg socket's bytes_acked in ss.txt when a scrape failed;
- the display channel messages relayed per second over the key-press
  window, from the proxy's ACK-window counters (ackwin.txt): with a stream
  running these are mostly its frames, so this bounds the frame rate the
  client actually received;
- the streams the client was sent (Ryll's stream_create log lines, mean per
  repeat), with their codec and most common size;
- when qemu ran with spice-server's debug log on (G_MESSAGES_DEBUG=Spice),
  the GStreamer pipelines it built, hardware (va/vaapi/msdk encoders) and
  software (x264enc and the like), how many times a hardware pipeline was
  given up for software (its "using software" messages), the median frame rate a stream was
  created with, and the median and largest of the periodic input-fps
  re-measurements.

A display name may carry a streaming-video suffix, NAME_all or NAME_filter.
A comparison CHANGE=BASE then applies within each suffix: CHANGE_all
against BASE_all, CHANGE_filter against BASE_filter.

The verdict tables compare CHANGE against BASE cell by cell on four
measures, each lower-is-better: latency p50, latency p95, qemu cores and
qemu peak RSS. For each measure the per-repeat values of both displays are
taken, and a difference counts as measurable only when both hold:

- separation: every repeat of one display is on the same side of every
  repeat of the other;
- size: the difference between the pooled figures is larger than the
  spread, the wider of the two displays' per-repeat ranges, and larger
  than a floor (--rule-floor, default 2 ms, for latency; 0.02 cores; 8 MiB).

A cell needs at least two good repeats of each display to be judged. It is
"helps" when some measure improves measurably and none regresses,
"regresses" for the reverse, "mixed" when both happen, and "no measurable
effect" otherwise.
"""

# audit-allow-print: this is a reporting CLI; its tables on stdout are the
# output.

import argparse
import collections
import json
import os
import re
import statistics

STREAM_RE = re.compile(r'stream_create: id=\d+, surface=\d+, codec=(\d+), (\d+x\d+)')
PIPELINE_RE = re.compile(r'GStreamer pipeline: .*?! (\w+) name=encoder')
CREATE_FPS_RE = re.compile(r'video-stream\.cpp:\d+:\w+: stream \d+ \d+x\d+ .* (\d+) fps')
INPUT_FPS_RE = re.compile(r'input-fps=(\d+)')
FALLBACK_RE = re.compile(r'hardware encoder.*using software|with a hardware encoder, using software')
HW_ENCODERS = ('va', 'vaapi', 'msdk', 'qsv', 'nv', 'amf')
CODECS = {'1': 'MJPEG', '2': 'VP8', '3': 'H.264', '4': 'VP9', '5': 'H.265'}


def pct(values, p):
    if not values:
        return float('nan')
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def read(path, default=None):
    try:
        with open(path, errors='replace') as f:
            return f.read()
    except OSError:
        return default


def is_hw(encoder):
    return any(encoder.startswith(prefix) for prefix in HW_ENCODERS) and encoder != 'x264enc'


def ss_mbit(d, t0, t1):
    """Display throughput from the client-leg socket's bytes_acked in ss.txt.

    The fallback when a /metrics scrape failed: the busiest socket on the
    proxy's client port (5900) is the display channel.
    """
    socks = collections.defaultdict(list)
    ts = head = None
    for line in read(os.path.join(d, 'ss.txt'), '').splitlines():
        if line.startswith('@ '):
            ts = float(line.split()[1])
        elif line[:1] not in ('\t', ' '):
            parts = line.split()
            head = parts[2] if len(parts) >= 4 and parts[2].endswith(':5900') else None
            peer = parts[3] if head else None
        elif head and ts is not None and t0 <= ts <= t1:
            m = re.search(r'\bbytes_acked:(\d+)', line)
            if m:
                socks[peer].append((ts, int(m.group(1))))
            head = None
    best = max(socks.values(), key=lambda v: v[-1][1] - v[0][1], default=[])
    if len(best) < 2 or best[-1][0] == best[0][0]:
        return float('nan')
    return (best[-1][1] - best[0][1]) * 8 / (best[-1][0] - best[0][0]) / 1e6


def repeat_stats(d):
    """Figures for one repeat directory, or a dict with only 'state'."""
    if os.path.exists(os.path.join(d, 'setup-failed')):
        return {'state': 'failed'}
    if os.path.exists(os.path.join(d, 'qemu-died')):
        return {'state': 'died'}
    try:
        lat = [float(x) * 1000 for x in read(os.path.join(d, 'keydraw.csv'), '').split() if x.strip()]
        kd = json.loads(read(os.path.join(d, 'keydraw.json')))
        t0 = float(read(os.path.join(d, 't0')))
        t1 = float(read(os.path.join(d, 't1')))
    except (TypeError, ValueError):
        return {'state': 'failed'}
    if not lat:
        return {'state': 'failed'}
    out = {'state': 'good', 'lat': lat, 'timeouts': kd.get('timeouts', 0),
           'p50': pct(lat, 50), 'p95': pct(lat, 95)}

    # qemu CPU over the key-press window, and peak RSS over the case.
    ta = t1 - kd.get('elapsed_s', t1 - t0)
    clk, ts, samples = 100, None, []
    for line in read(os.path.join(d, 'proc.txt'), '').splitlines():
        parts = line.split()
        if line.startswith('# clk_tck') and len(parts) == 3:
            clk = int(parts[2])
        elif line.startswith('@ '):
            ts = float(parts[1])
        elif ts is not None and len(parts) == 4 and parts[0] == 'qemu':
            samples.append((ts, int(parts[1]) + int(parts[2]), int(parts[3])))
    inside = [s for s in samples if ta <= s[0] <= t1]
    out['cores'] = float('nan')
    if len(inside) >= 2:
        out['cores'] = (inside[-1][1] - inside[0][1]) / clk / (inside[-1][0] - inside[0][0])
    out['rss'] = max((s[2] for s in samples), default=float('nan')) / 1024

    # Relayed display throughput, server to client, from the proxy's metrics.
    def relayed(name):
        text = read(os.path.join(d, name), '')
        for line in text.splitlines():
            if line.startswith('kerbside_proxy_bytes_relayed_total{') and 'server_to_client' in line:
                return int(float(line.split()[-1]))
        return 0 if 'kerbside_proxy_' in text else None
    m0, m1 = relayed('metrics-0.txt'), relayed('metrics-1.txt')
    out['mbit'] = (m1 - m0) * 8 / (t1 - t0) / 1e6 if m0 is not None and m1 is not None else ss_mbit(d, t0, t1)

    # Display channel messages relayed per second over the key-press window,
    # from the proxy's ACK-window counters: with a stream running, mostly its
    # frames, so an upper bound on the stream's frame rate on the wire.
    samples, cur = [], None
    for line in read(os.path.join(d, 'ackwin.txt'), '').splitlines():
        if line.startswith('@ '):
            cur = [float(line.split()[1]), 0.0]
            samples.append(cur)
        elif (cur is not None and line.startswith('kerbside_proxy_ack_window_messages_total{')
              and 'channel="display"' in line):
            cur[1] += float(line.split()[-1])
    samples = [x for x in samples if ta <= x[0] <= t1]
    out['msgs'] = float('nan')
    if len(samples) >= 2 and samples[-1][0] > samples[0][0]:
        out['msgs'] = (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0])

    # What the client was sent.
    streams = STREAM_RE.findall(read(os.path.join(d, 'ryll.out'), ''))
    out['streams'] = len(streams)
    out['codecs'] = collections.Counter(c for c, _ in streams)
    out['sizes'] = collections.Counter(s for _, s in streams)

    # spice-server's own account, when its debug log was on.
    qlog = read(os.path.join(d, 'qemu.log'), '')
    out['debug'] = 'info: Spice: ' in qlog
    encoders = PIPELINE_RE.findall(qlog)
    out['hw'] = sum(1 for e in encoders if is_hw(e))
    out['sw'] = len(encoders) - out['hw']
    out['fallback'] = len(FALLBACK_RE.findall(qlog))
    out['create_fps'] = [int(x) for x in CREATE_FPS_RE.findall(qlog)]
    out['input_fps'] = [int(x) for x in INPUT_FPS_RE.findall(qlog)]
    return out


def cell_stats(rep_dirs):
    reps = [repeat_stats(d) for d in sorted(rep_dirs)]
    good = [r for r in reps if r['state'] == 'good']
    out = {'good': len(good), 'died': sum(r['state'] == 'died' for r in reps),
           'failed': sum(r['state'] == 'failed' for r in reps), 'reps': good}
    if not good:
        return out
    lat = [x for r in good for x in r['lat']]
    cores = [r['cores'] for r in good if r['cores'] == r['cores']]
    codecs, sizes = collections.Counter(), collections.Counter()
    for r in good:
        codecs.update(r['codecs'])
        sizes.update(r['sizes'])
    create = [x for r in good for x in r['create_fps']]
    inputs = [x for r in good for x in r['input_fps']]
    out.update({
        'n': len(lat), 'timeouts': sum(r['timeouts'] for r in good),
        'p50': pct(lat, 50), 'p95': pct(lat, 95),
        'p50_lo': min(r['p50'] for r in good), 'p50_hi': max(r['p50'] for r in good),
        'cores': statistics.mean(cores) if cores else float('nan'),
        'rss': max(r['rss'] for r in good),
        'mbit': statistics.mean(r['mbit'] for r in good),
        'msgs': statistics.mean(r['msgs'] for r in good),
        'streams': statistics.mean(r['streams'] for r in good),
        'codec': '/'.join(CODECS.get(c, c) for c, _ in codecs.most_common()) or '-',
        'size': sizes.most_common(1)[0][0] if sizes else '-',
        'debug': any(r['debug'] for r in good),
        'hw': sum(r['hw'] for r in good), 'sw': sum(r['sw'] for r in good),
        'fallback': sum(r['fallback'] for r in good),
        'create_fps': statistics.median(create) if create else None,
        'input_fps': statistics.median(inputs) if inputs else None,
        'input_fps_max': max(inputs) if inputs else None,
    })
    return out


def load_run(root):
    """Return {(profile, activity): {display: cell stats}} for one run."""
    cells = collections.defaultdict(dict)
    for profile in sorted(os.listdir(root)):
        pdir = os.path.join(root, profile)
        if not os.path.isdir(pdir) or not re.match(r'\d+ms-\d+mbit$', profile):
            continue
        for activity in sorted(os.listdir(pdir)):
            adir = os.path.join(pdir, activity)
            by_display = collections.defaultdict(list)
            for rep in os.listdir(adir):
                m = re.match(r'(.+)-[^-]+-r\d+$', rep)
                if m:
                    by_display[m.group(1)].append(os.path.join(adir, rep))
            for display, dirs in by_display.items():
                cells[(profile, activity)][display] = cell_stats(dirs)
    return cells


def profile_key(profile):
    rtt, rate = re.match(r'(\d+)ms-(\d+)mbit', profile).groups()
    return (int(rtt), -int(rate))


def link(profile):
    rtt, rate = re.match(r'(\d+)ms-(\d+)mbit', profile).groups()
    return f'{rtt} ms / {rate} Mbit'


def fmt(v, spec='.0f'):
    if v is None or v != v:
        return '-'
    return format(v, spec)


def print_run(name, root, cells, displays):
    host = read(os.path.join(root, 'host.txt'), '')
    env = read(os.path.join(root, 'invocation-env.txt'), '')
    ryll_args = re.search(r'^RYLL_ARGS=(.*)$', env, re.M)
    repeats = re.search(r'^REPEATS=(.*)$', env, re.M)
    qemus = sorted({m for m in re.findall(r'^display \S+: (QEMU emulator version \S+)', host, re.M)})
    print(f'### {name}\n')
    print(f'- qemu: {", ".join(qemus) or "unknown"}')
    print(f'- Ryll arguments: {ryll_args.group(1) if ryll_args and ryll_args.group(1) else "none"}; '
          f'repeats: {repeats.group(1) if repeats else "?"}')
    print()
    debug = any(c.get('debug') for row in cells.values() for c in row.values())
    head = ('| Link | Activity | Display | Good (died, failed) | n (timeouts) | p50 ms | p95 ms | '
            'Repeat p50 range ms | qemu cores | qemu peak RSS MiB | Display Mbit/s | Display msgs/s | '
            'Streams per repeat (codec, size) |')
    rule = '|---|---|---|---|---|---|---|---|---|---|---|---|---|'
    if debug:
        head += ' GStreamer pipelines hw / sw (fallbacks) | Stream fps at creation | input-fps median (max) |'
        rule += '---|---|---|'
    print(head)
    print(rule)
    for profile, activity in sorted(cells, key=lambda k: (profile_key(k[0]), k[1])):
        row = cells[(profile, activity)]
        for display in [d for d in displays if d in row]:
            c = row[display]
            line = f'| {link(profile)} | {activity} | {display} | {c["good"]} ({c["died"]}, {c["failed"]}) |'
            if not c['good']:
                print(line + ' - |' * (9 + (3 if debug else 0)))
                continue
            line += (f' {c["n"]} ({c["timeouts"]}) | {fmt(c["p50"])} | {fmt(c["p95"])} | '
                     f'{fmt(c["p50_lo"])}-{fmt(c["p50_hi"])} | {fmt(c["cores"], ".2f")} | {fmt(c["rss"])} | '
                     f'{fmt(c["mbit"], ".1f")} | {fmt(c["msgs"])} | '
                     f'{fmt(c["streams"], ".1f")} ({c["codec"]}, {c["size"]}) |')
            if debug:
                inputs = '-' if c['input_fps'] is None else f'{fmt(c["input_fps"])} ({c["input_fps_max"]})'
                line += f' {c["hw"]} / {c["sw"]} ({c["fallback"]}) | {fmt(c["create_fps"])} | {inputs} |'
            print(line)
    print()


MEASURES = (('p50', 'p50', None), ('p95', 'p95', None), ('cores', 'cores', 0.02), ('rss', 'peak RSS', 8.0))


def judge(base, change, floor_ms):
    """Return (verdict, [notes]) for one cell."""
    better, worse, notes = [], [], []
    for key, label, floor in MEASURES:
        b = [r[key] for r in base['reps'] if r[key] == r[key]]
        c = [r[key] for r in change['reps'] if r[key] == r[key]]
        if not b or not c:
            continue
        if key in ('p50', 'p95'):
            pb, pc, floor = base[key], change[key], floor_ms
        elif key == 'cores':
            pb, pc = statistics.mean(b), statistics.mean(c)
        else:
            pb, pc = max(b), max(c)
        delta = pc - pb
        spread = max(max(b) - min(b), max(c) - min(c))
        separated = max(c) < min(b) or min(c) > max(b)
        if separated and abs(delta) > max(spread, floor):
            (better if delta < 0 else worse).append(label)
            spec = '+.0f' if key != 'cores' else '+.2f'
            notes.append(f'{label} {format(delta, spec)} (spread {format(spread, spec[1:])})')
    if better and worse:
        verdict = 'mixed'
    elif better:
        verdict = 'helps'
    elif worse:
        verdict = 'regresses'
    else:
        verdict = 'no measurable effect'
    return verdict, notes


def print_compare(title, cells, change, base, floor_ms):
    print(f'### {title}\n')
    print('| Link | Activity | Streaming | p50 ms base / change | p95 ms base / change | '
          'Repeat p50 range base / change | qemu cores base / change | Peak RSS MiB base / change | '
          'Verdict | Measurable differences |')
    print('|---|---|---|---|---|---|---|---|---|---|')
    counts = collections.Counter()
    for profile, activity in sorted(cells, key=lambda k: (profile_key(k[0]), k[1])):
        row = cells[(profile, activity)]
        pairs = []
        for display in sorted(row):
            if display == change:
                pairs.append(('-', change, base))
            elif display.startswith(change + '_'):
                suffix = display[len(change) + 1:]
                pairs.append((suffix, display, f'{base}_{suffix}'))
        for suffix, cd, bd in pairs:
            if bd not in row:
                continue
            b, c = row[bd], row[cd]
            if b['good'] < 2 or c['good'] < 2:
                print(f'| {link(profile)} | {activity} | {suffix} | - | - | - | - | - | '
                      f'not judged (good repeats {b["good"]} / {c["good"]}) | |')
                counts['not judged'] += 1
                continue
            verdict, notes = judge(b, c, floor_ms)
            counts[verdict] += 1
            print(f'| {link(profile)} | {activity} | {suffix} | {fmt(b["p50"])} / {fmt(c["p50"])} | '
                  f'{fmt(b["p95"])} / {fmt(c["p95"])} | '
                  f'{fmt(b["p50_lo"])}-{fmt(b["p50_hi"])} / {fmt(c["p50_lo"])}-{fmt(c["p50_hi"])} | '
                  f'{fmt(b["cores"], ".2f")} / {fmt(c["cores"], ".2f")} | {fmt(b["rss"])} / {fmt(c["rss"])} | '
                  f'{verdict} | {"; ".join(notes)} |')
    print()
    print('Cells: ' + ', '.join(f'{v} {k}' for k, v in sorted(counts.items())) + '.\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('runs', nargs='+', help='run-matrix.sh WORKDIRs, each RUN or RUN:LABEL')
    parser.add_argument('--compare', action='append', default=[],
                        help='RUN_NAME:CHANGE=BASE[:TITLE], RUN_NAME being the run label')
    parser.add_argument('--preamble', help='a Markdown file printed before the tables')
    parser.add_argument('--rule-floor', type=float, default=2.0,
                        help='the smallest latency difference, in ms, that can count as measurable')
    args = parser.parse_args()

    runs = {}
    order = []
    for spec in args.runs:
        path, _, label = spec.partition(':')
        label = label or os.path.basename(os.path.normpath(path))
        runs[label] = (path, load_run(path))
        order.append(label)

    if args.preamble:
        print(read(args.preamble).rstrip() + '\n')

    if args.compare:
        print('## Verdicts\n')
        print('Each table compares one display (the change) with another (the base) inside one run, so both '
              'saw the same qemu, codec, refresh rate and link, interleaved in every pass. A measure differs '
              'measurably only when every repeat of one display sits on the same side of every repeat of the '
              f'other, and the pooled difference exceeds both the wider per-repeat range ("spread") and a floor '
              f'of {args.rule_floor:g} ms, 0.02 cores or 8 MiB. Lower is better for all four measures. '
              'A cell with fewer than two good repeats of either display is not judged.\n')
        for spec in args.compare:
            run, _, rest = spec.partition(':')
            pair, _, title = rest.partition(':')
            change, _, base = pair.partition('=')
            print_compare(title or f'{run}: {change} against {base}', runs[run][1], change, base,
                          args.rule_floor)

    print('## Per-display figures\n')
    for label in order:
        path, cells = runs[label]
        displays = []
        for row in cells.values():
            for d in row:
                if d not in displays:
                    displays.append(d)
        env = read(os.path.join(path, 'invocation-env.txt'), '')
        m = re.search(r'^DISPLAYS=(.*)$', env, re.M)
        if m:
            named = [d.split(':')[0] for d in m.group(1).split()]
            displays = [d for d in named if d in displays] + [d for d in displays if d not in named]
        print_run(label, path, cells, displays)


if __name__ == '__main__':
    main()
