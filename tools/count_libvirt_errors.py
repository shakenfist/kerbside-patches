#!/usr/bin/env python3
"""Count 'Client hit max requests limit' hits in Kolla libvirtd.txt logs.

This is a copy of the tool prototyped in the (private) openstack_zuul_tools
repository, which remains the canonical source; it was copied here (rather
than referenced) because it is stable, self-contained, and CI wants a pinned
copy. It was the tool behind kolla-ansible change 995171 ("Tune libvirtd
connection limits for OpenStack"); the ci-reporting workflow now runs it
periodically to track that fix.

This walks every OpenStack Kolla build recorded by the OpenDev Zuul over a
trailing window (default 30 days), locates the ``kolla/libvirt/libvirtd.txt``
log file(s) produced by each build, and classifies each file as a 'hit' (it
contains the string ``Client hit max requests limit``) or a 'miss' (the file
exists but does not contain the string).

The OpenStack ElasticSearch (logstash) deployment does not index libvirtd.txt,
which is why we have to walk the build logs in object storage directly.

Results are written incrementally to a CSV file, one row per libvirtd.txt file
found. Builds that do not produce a libvirtd.txt (unit test jobs, doc builds,
node failures, and so on) are not written to the CSV but are tallied in the
run summary. A checkpoint file records which build UUIDs have already been
processed so the run can be interrupted and resumed without redoing work.

Multinode jobs (for example the cells jobs) produce several libvirtd.txt files,
one per node directory (primary, secondary1, secondary2, ...). Each of those is
recorded as its own CSV row. We discover them from the build's
zuul-manifest.json rather than guessing node directory names, so any node
layout is handled correctly.
"""

import argparse
import csv
import datetime
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


ZUUL_API = 'https://zuul.opendev.org/api/tenant/openstack'
TARGET_STRING = 'Client hit max requests limit'
# Path suffixes of libvirt daemon logs, by deployment tooling. Kolla publishes
# the daemon log as kolla/libvirt/libvirtd.txt. Devstack-based jobs (Nova,
# Tempest, ...) publish it as <node>/logs/libvirt/libvirt/libvirtd_log.txt,
# except grenade jobs which use <node>/logs/libvirt/libvirtd_log.txt, so match
# on the bare filename.
LIBVIRTD_SUFFIXES = {
    'kolla': ['kolla/libvirt/libvirtd.txt'],
    'devstack': ['/libvirtd_log.txt'],
}
DEFAULT_PROJECTS = ['openstack/kolla', 'openstack/kolla-ansible']

CSV_FIELDS = [
    'build_uuid', 'project', 'job_name', 'branch', 'pipeline', 'result',
    'end_time', 'node', 'status', 'match_count', 'libvirtd_url',
]


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_zuul_time(value):
    """Parse a Zuul timestamp (naive UTC ISO string) into an aware datetime."""
    if not value:
        return None
    return datetime.datetime.fromisoformat(value).replace(tzinfo=datetime.timezone.utc)


def make_session():
    session = requests.Session()
    session.headers.update({'User-Agent': 'kolla-libvirt-error-counter/1.0 (mikal@stillhq.com)'})
    return session


# HTTP statuses worth retrying: object storage / CDN backends occasionally
# return these transiently under load. 404 is NOT here -- a missing file is a
# real answer, not a failure.
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5


def http_get(session, url, timeout):
    """GET ``url`` with bounded retry/backoff on transient failures.

    Returns the final ``requests.Response`` (the caller inspects status_code,
    e.g. for 404). Raises ``requests.RequestException`` only after exhausting
    retries on a transient error, or immediately on a non-retryable client
    error. This is what keeps a single flaky request from wedging the run.
    """
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
        else:
            if resp.status_code not in RETRY_STATUSES:
                return resp
            last_exc = requests.HTTPError(f'{resp.status_code} for {url}', response=resp)
        # Exponential backoff: 1, 2, 4, 8, 16s. The last attempt does not sleep.
        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
    raise last_exc


def fetch_builds(session, project, cutoff, page_size, list_delay, verbose):
    """Yield build dicts for ``project`` newer than ``cutoff``, newest first.

    This is the only part of the run that talks to zuul.opendev.org itself (the
    per-build log fetches go to object storage). It is deliberately serial and
    sleeps ``list_delay`` seconds between pages to stay gentle on Zuul.
    """
    skip = 0
    while True:
        url = f'{ZUUL_API}/builds'
        params = {'project': project, 'limit': page_size, 'skip': skip}
        resp = session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            return

        stop = False
        for build in batch:
            stamp = parse_zuul_time(build.get('end_time')) or parse_zuul_time(build.get('start_time'))
            if stamp is not None and stamp < cutoff:
                stop = True
                break
            yield build

        if verbose:
            print(f'  {project}: fetched {skip + len(batch)} builds '
                  f'(oldest in page {batch[-1].get("end_time")})', file=sys.stderr)

        if stop or len(batch) < page_size:
            return
        skip += page_size
        if list_delay:
            time.sleep(list_delay)


def walk_manifest(nodes, prefix=''):
    """Yield full path strings for every file leaf in a zuul manifest tree."""
    for node in nodes:
        name = node.get('name', '')
        path = f'{prefix}{name}' if not prefix else f'{prefix}/{name}'
        children = node.get('children')
        if children:
            yield from walk_manifest(children, path)
        else:
            yield path


def find_libvirtd_paths(session, log_url, suffixes):
    """Return relative paths of every libvirt daemon log in a build's manifest.

    A missing manifest (404) means no logs were published -> empty list. Any
    other failure is raised after retries, for the caller to treat as a build
    that could not be processed (so it is retried on the next run).
    """
    manifest_url = log_url.rstrip('/') + '/zuul-manifest.json'
    resp = http_get(session, manifest_url, timeout=60)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    tree = resp.json().get('tree', [])
    return [p for p in walk_manifest(tree)
            if any(p.endswith(suffix) for suffix in suffixes)]


def classify_file(session, file_url):
    """Return (status, match_count) for a single libvirtd.txt URL.

    requests transparently decompresses the gzip-stored object, so a plain
    text search works. status is 'hit', 'miss', or 'missing' (404).
    """
    resp = http_get(session, file_url, timeout=120)
    if resp.status_code == 404:
        return 'missing', 0
    resp.raise_for_status()
    count = resp.text.count(TARGET_STRING)
    return ('hit' if count else 'miss'), count


def node_from_path(path):
    """The leading node directory, for example 'primary' or 'secondary1'."""
    return path.split('/', 1)[0] if '/' in path else path


def process_build(session, build, suffixes):
    """Process one build's libvirt daemon log files.

    Returns ``(rows, ok)``. ``ok`` is False when the build could not be fully
    processed because of a transient fetch failure that survived all retries;
    such a build is left out of the checkpoint so it is retried on the next
    run, and its (partial) rows are discarded to keep the work atomic -- a
    build is either fully recorded or not recorded at all, never half. ``rows``
    is empty for builds that simply have no libvirtd.txt.
    """
    log_url = build.get('log_url')
    if not log_url:
        return [], True

    try:
        rel_paths = find_libvirtd_paths(session, log_url, suffixes)
        rows = []
        for rel_path in rel_paths:
            file_url = log_url.rstrip('/') + '/' + rel_path
            status, count = classify_file(session, file_url)
            if status == 'missing':
                # In the manifest but unfetchable as a 404; nothing to record.
                continue
            ref = build.get('ref', {})
            rows.append({
                'build_uuid': build.get('uuid', ''),
                'project': ref.get('project', ''),
                'job_name': build.get('job_name', ''),
                'branch': ref.get('branch', ''),
                'pipeline': build.get('pipeline', ''),
                'result': build.get('result', ''),
                'end_time': build.get('end_time', ''),
                'node': node_from_path(rel_path),
                'status': status,
                'match_count': count,
                'libvirtd_url': file_url,
            })
        return rows, True
    except Exception as exc:
        # Any failure (HTTP after retries, malformed manifest JSON, ...) leaves
        # the build unprocessed so it is retried, rather than killing the run.
        print(f'    build {build.get("uuid", "")} failed (will retry next run): '
              f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return [], False


def load_checkpoint(path):
    if not os.path.exists(path):
        return set()
    with open(path, encoding='utf-8') as handle:
        return {line.strip() for line in handle if line.strip()}


# Distro detection from the job name, in priority order. Every Kolla CI job
# name embeds exactly one of these tokens (kolla-ansible-ubuntu-noble-...,
# kolla-ansible-centos-10s-..., and so on), so this is an exact partition.
DISTRO_TOKENS = [('debian', 'Debian'), ('ubuntu', 'Ubuntu'),
                 ('rocky', 'Rocky'), ('centos', 'CentOS')]
DISTRO_ORDER = ['Debian', 'Ubuntu', 'Rocky', 'CentOS', 'Other']
DISTRO_COLORS = {
    'Debian': '#1f77b4', 'Ubuntu': '#ff7f0e', 'Rocky': '#7f7f7f',
    'CentOS': '#ffd700', 'Other': '#c0c0c0',
}


def distro_of(job_name):
    """Map a Kolla CI job name to its base distro label."""
    lowered = job_name.lower()
    for token, label in DISTRO_TOKENS:
        if token in lowered:
            return label
    return 'Other'


def aggregate_for_chart(csv_path, branch):
    """Aggregate the CSV into per-day series for the chart.

    Aggregation is per *build* (not per libvirtd.txt row): a build counts once,
    and 'hits' if any of its files contain the target string. Only rows on
    ``branch`` are considered. Returns ``(days, hits_by_distro, totals, rates)``
    where ``days`` is a contiguous list of ``datetime.date`` from the first to
    the last day seen, ``hits_by_distro[label]`` is a per-day hit-count list
    aligned to ``days``, ``totals`` is per-day build count, and ``rates`` is the
    per-day hit rate as a percentage (0 when there were no builds that day).
    """
    builds = {}
    with open(csv_path, newline='', encoding='utf-8') as handle:
        for row in csv.DictReader(handle):
            if branch and row.get('branch') != branch:
                continue
            uuid = row.get('build_uuid', '')
            end_time = row.get('end_time', '')
            if not uuid or not end_time:
                continue
            entry = builds.get(uuid)
            if entry is None:
                day = datetime.date.fromisoformat(end_time[:10])
                entry = builds[uuid] = {'day': day,
                                        'distro': distro_of(row.get('job_name', '')),
                                        'hit': False}
            if row.get('status') == 'hit':
                entry['hit'] = True

    if not builds:
        return [], {}, [], []

    total_by_day = {}
    hit_by_day_distro = {}
    for entry in builds.values():
        day = entry['day']
        total_by_day[day] = total_by_day.get(day, 0) + 1
        if entry['hit']:
            per = hit_by_day_distro.setdefault(day, {})
            per[entry['distro']] = per.get(entry['distro'], 0) + 1

    first, last = min(total_by_day), max(total_by_day)
    days = [first + datetime.timedelta(days=i) for i in range((last - first).days + 1)]

    hits_by_distro = {label: [] for label in DISTRO_ORDER}
    totals = []
    rates = []
    for day in days:
        total = total_by_day.get(day, 0)
        per = hit_by_day_distro.get(day, {})
        day_hits = sum(per.values())
        totals.append(total)
        rates.append(100.0 * day_hits / total if total else 0.0)
        for label in DISTRO_ORDER:
            hits_by_distro[label].append(per.get(label, 0))

    # Drop distro series that never appear so the legend stays tidy.
    hits_by_distro = {label: counts for label, counts in hits_by_distro.items()
                      if any(counts)}
    return days, hits_by_distro, totals, rates


def build_chart(csv_path, out_path, branch='master', title=None, fix_merged=None):
    """Render the per-day libvirt chart from ``csv_path`` to ``out_path``.

    matplotlib is imported lazily so the scrape path never needs it. The figure
    has two stacked panels sharing the date axis: the top shows per-day hit
    counts stacked by distro with the remaining (non-hit) jobs stacked on top in
    grey, so each bar's full height is that day's total jobs on a single axis;
    the bottom shows the per-day hit rate as a percentage.

    ``fix_merged`` is an optional date/ISO-datetime string for when a fix landed.
    The merge day itself has mixed before/after data, so the chart marks the
    boundary at the start of the *next* day (the first complete post-merge day):
    a dashed line on both panels, faint shading over the post-merge region, and
    a label, so before/after is easy to read off.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    days, hits_by_distro, totals, rates = aggregate_for_chart(csv_path, branch)
    if not days:
        raise SystemExit(f'no chartable rows in {csv_path} for branch {branch!r}')

    # The first complete post-merge day is the day after the (mixed) merge day.
    first_full_day = None
    if fix_merged:
        first_full_day = datetime.date.fromisoformat(fix_merged[:10]) + datetime.timedelta(days=1)

    if title is None:
        scope = f'{branch} CI' if branch else 'CI'
        title = f'libvirtd connection limit failures on {scope}'

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True, height_ratios=[2, 1],
        gridspec_kw={'hspace': 0.2})

    # Top: per-day hit counts stacked by distro, then the remaining (non-hit)
    # jobs stacked on top in neutral grey so each bar's full height is that
    # day's total jobs -- all on one axis, no secondary scale.
    bottom = [0] * len(days)
    for label, counts in hits_by_distro.items():
        ax_top.bar(days, counts, bottom=bottom, label=label,
                   color=DISTRO_COLORS.get(label, '#c0c0c0'), width=0.8)
        bottom = [b + c for b, c in zip(bottom, counts)]
    remaining = [total - hits for total, hits in zip(totals, bottom)]
    ax_top.bar(days, remaining, bottom=bottom, label='Jobs without a hit',
               color='#bfe3bf', width=0.8)
    ax_top.set_ylabel('Jobs/day (hits highlighted)')
    ax_top.set_ylim(bottom=0)
    ax_top.set_title(title)
    ax_top.legend(ncol=6, loc='upper center', bbox_to_anchor=(0.5, -0.02),
                  frameon=False)

    # Bottom: per-day hit rate as a percentage, as bars for consistency.
    ax_bot.bar(days, rates, color='#f0a0a0', width=0.8)
    ax_bot.set_ylabel('Hit rate (%)')
    ax_bot.set_ylim(bottom=0)
    ax_bot.grid(axis='y', linestyle=':', alpha=0.5)

    # Mark the pre/post-fix boundary at the start of the first complete day
    # after the merge (the merge day itself is mixed, so it stays on the "before"
    # side). The boundary sits halfway between that day's bar and the prior one.
    if first_full_day is not None and days[0] <= first_full_day <= days[-1]:
        boundary = mdates.date2num(first_full_day) - 0.5
        right_edge = mdates.date2num(days[-1]) + 0.5
        for ax in (ax_top, ax_bot):
            ax.axvspan(boundary, right_edge, color='#1f77b4', alpha=0.06, zorder=0)
            ax.axvline(boundary, color='#444444', linestyle='--', linewidth=1.2, zorder=5)
        ax_top.text(boundary, 0.97, f' fix merged — first full day {first_full_day:%d-%b} →',
                    transform=ax_top.get_xaxis_transform(), ha='left', va='top',
                    fontsize=9, color='#444444')

    # Clamp the x-range to the data so the default axis margin does not draw
    # empty leading/trailing day columns (shared axis, so this covers both).
    ax_bot.set_xlim(mdates.date2num(days[0]) - 0.6, mdates.date2num(days[-1]) + 0.6)
    ax_bot.xaxis.set_major_formatter(mdates.DateFormatter('%d-%b'))
    ax_bot.xaxis.set_major_locator(mdates.DayLocator())
    fig.autofmt_xdate(rotation=90)

    fig.savefig(out_path, dpi=110, bbox_inches='tight')
    plt.close(fig)
    print(f'chart written to            : {out_path}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--days', type=int, default=30,
                        help='trailing window in days (default 30)')
    parser.add_argument('--projects', nargs='+', default=DEFAULT_PROJECTS,
                        help=f'Zuul projects to scan (default: {" ".join(DEFAULT_PROJECTS)})')
    parser.add_argument('--log-layout', choices=sorted(LIBVIRTD_SUFFIXES), default='kolla',
                        help='which libvirt daemon log path layout to look for: '
                             "'kolla' (kolla/libvirt/libvirtd.txt) or 'devstack' "
                             '(libvirt/libvirt/libvirtd_log.txt, used by Nova/Tempest '
                             'devstack jobs) (default kolla)')
    parser.add_argument('--output', default='kolla_libvirt_errors.csv',
                        help='CSV output path (default kolla_libvirt_errors.csv)')
    parser.add_argument('--checkpoint', default=None,
                        help='checkpoint file (default <output>.checkpoint)')
    parser.add_argument('--builds-cache', default=None,
                        help='build-list cache JSON (default <output>.builds.json); '
                             'reused on resume so Zuul is listed only once. Delete to refresh.')
    parser.add_argument('--workers', type=int, default=6,
                        help='concurrent build workers (default 6). These hit object '
                             'storage/CDN, not Zuul itself.')
    parser.add_argument('--page-size', type=int, default=500,
                        help='Zuul builds API page size (default 500)')
    parser.add_argument('--list-delay', type=float, default=0.5,
                        help='seconds to sleep between Zuul build-list pages (default 0.5)')
    parser.add_argument('--verbose', action='store_true', help='chatty progress')
    parser.add_argument('--chart', nargs='?', const='kolla_libvirt_chart.png', default=None,
                        metavar='PATH',
                        help='after the run, render the per-day chart to PATH '
                             '(default kolla_libvirt_chart.png when given without a value)')
    parser.add_argument('--chart-only', action='store_true',
                        help='skip scraping; just (re)build the chart from the existing '
                             '--output CSV, then exit')
    parser.add_argument('--chart-branch', default='master',
                        help="branch to chart, or '' for all branches (default master)")
    parser.add_argument('--fix-merged', default=None, metavar='DATE',
                        help='date/ISO-datetime a fix merged; the chart marks the first '
                             'complete day after it (the merge day is mixed) as the '
                             'pre/post-fix boundary')
    args = parser.parse_args()

    if args.chart_only:
        out_path = args.chart or 'kolla_libvirt_chart.png'
        build_chart(args.output, out_path, branch=args.chart_branch, fix_merged=args.fix_merged)
        return

    checkpoint_path = args.checkpoint or (args.output + '.checkpoint')
    builds_cache_path = args.builds_cache or (args.output + '.builds.json')
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.days)
    done = load_checkpoint(checkpoint_path)
    print(f'cutoff: {cutoff.isoformat()}  projects: {", ".join(args.projects)}', file=sys.stderr)
    if done:
        print(f'resuming: {len(done)} builds already processed', file=sys.stderr)

    # Collect the build list up front so we know how much work there is. The
    # listing is the only Zuul-facing step, so cache it: a resume reuses the
    # cached list (and its frozen window) rather than re-hitting Zuul.
    if os.path.exists(builds_cache_path):
        with open(builds_cache_path, encoding='utf-8') as handle:
            builds = json.load(handle)
        print(f'build list loaded from cache {builds_cache_path} '
              f'({len(builds)} builds)', file=sys.stderr)
    else:
        listing_session = make_session()
        builds = []
        seen = set()
        for project in args.projects:
            for build in fetch_builds(listing_session, project, cutoff, args.page_size,
                                      args.list_delay, args.verbose):
                uuid = build.get('uuid')
                if uuid and uuid not in seen:
                    seen.add(uuid)
                    builds.append(build)
        tmp_path = builds_cache_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as handle:
            json.dump(builds, handle)
        os.replace(tmp_path, builds_cache_path)
        print(f'build list cached to {builds_cache_path}', file=sys.stderr)

    todo = [b for b in builds if b.get('uuid') not in done]
    print(f'builds in window: {len(builds)}  to process: {len(todo)}', file=sys.stderr)

    csv_exists = os.path.exists(args.output)
    csv_handle = open(args.output, 'a', newline='', encoding='utf-8')
    writer = csv.DictWriter(csv_handle, fieldnames=CSV_FIELDS)
    if not csv_exists:
        writer.writeheader()
    cp_handle = open(checkpoint_path, 'a', encoding='utf-8')

    write_lock = threading.Lock()
    tally = {'hit': 0, 'miss': 0}
    builds_with_file = 0
    builds_without_file = 0
    builds_failed = 0
    processed = 0

    thread_local = threading.local()

    def session_for_thread():
        if not hasattr(thread_local, 'session'):
            thread_local.session = make_session()
        return thread_local.session

    suffixes = LIBVIRTD_SUFFIXES[args.log_layout]

    def worker(build):
        return build, process_build(session_for_thread(), build, suffixes)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, b) for b in todo]
        for future in as_completed(futures):
            try:
                build, (rows, ok) = future.result()
            except Exception as exc:  # defensive: a worker should never raise
                builds_failed += 1
                print(f'    worker raised, build skipped: {type(exc).__name__}: {exc}',
                      file=sys.stderr)
                continue

            if not ok:
                # Build left unprocessed and uncheckpointed -> retried next run.
                builds_failed += 1
            else:
                with write_lock:
                    for row in rows:
                        writer.writerow(row)
                        tally[row['status']] = tally.get(row['status'], 0) + 1
                    cp_handle.write(build.get('uuid', '') + '\n')
                    csv_handle.flush()
                    cp_handle.flush()
                if rows:
                    builds_with_file += 1
                else:
                    builds_without_file += 1

            processed += 1
            if processed % 100 == 0 or args.verbose:
                print(f'  processed {processed}/{len(todo)}  '
                      f'hits={tally["hit"]} misses={tally["miss"]} '
                      f'failed={builds_failed}', file=sys.stderr)

    csv_handle.close()
    cp_handle.close()

    total_files = tally['hit'] + tally['miss']
    print('\n=== summary ===')
    print(f'builds processed this run : {processed}')
    print(f'builds with libvirtd.txt  : {builds_with_file}')
    print(f'builds without libvirtd   : {builds_without_file}')
    print(f'builds failed (retry next): {builds_failed}')
    print(f'libvirtd.txt files (rows) : {total_files}')
    print(f'  hits   : {tally["hit"]}')
    print(f'  misses : {tally["miss"]}')
    if total_files:
        print(f'hit rate (files)          : {tally["hit"] / total_files:.1%}')
    print(f'CSV written to            : {args.output}')

    if args.chart is not None:
        build_chart(args.output, args.chart, branch=args.chart_branch, fix_merged=args.fix_merged)


if __name__ == '__main__':
    main()
