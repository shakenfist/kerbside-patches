#!/usr/bin/env python3
"""Read series.yaml, the index of the patch series in this repository.

series.yaml maps each project (a series' config.yaml `directory`) to the
series that patch it, and each series to an entry:

    neutron:
      neutron-import-memory-007:
        status: push
        after: [devstack-privsep-memory-001]
        note: Waiting on a reply from the reviewer.

    status   push (keep it on Gerrit), hold (pushable, but not now) or
             carry (downstream only, never pushed); required
    after    the series whose changes must be on Gerrit before this one is
             pushed; optional
    note     free text; optional

This is both a module for other tools (`import series`) and a command for
shell scripts, which prints one series per line:

Usage:
    tools/series.py list [--project P] [--status S] [--ordered]
    tools/series.py after SERIES
    tools/series.py project SERIES

`list` prints every series sorted by project, then name. --status may be
repeated or given a comma separated list, and a series matching any of them is
printed. --ordered prints in push order instead: every series comes after the
series its `after` names, and ties are broken by project, then name. Filters
apply after ordering, so a series still follows one that was filtered out.

`after` prints the series named in SERIES' `after`, in the order written, and
`project` prints SERIES' project.

--root names the repository to read series.yaml from; it defaults to the one
holding this script. tools/check-series-index.py checks that the index agrees
with the series directories.

Exit status:
    0   success
    1   an unknown series, or series.yaml is missing or invalid
    2   a usage error
"""

import argparse
import dataclasses
import heapq
import os
import sys

import yaml

TOPDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = 'series.yaml'

STATUSES = ('push', 'hold', 'carry')
KEYS = ('status', 'after', 'note')


class SeriesError(Exception):
    """series.yaml is missing or invalid, or does not hold what was asked for."""


class UnknownSeries(SeriesError):
    """A series name is not in series.yaml."""


class CycleError(SeriesError):
    """The after links form at least one cycle, so there is no push order."""

    def __init__(self, cycles):
        self.cycles = cycles
        super().__init__('; '.join('the after links form a cycle: ' + ', '.join(c) for c in cycles))


@dataclasses.dataclass(frozen=True)
class Series:
    name: str
    project: str
    status: str
    after: tuple = ()
    note: str = None


##############################################################################
# Reading                                                                    #
##############################################################################

class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses a mapping with the same key twice.

    PyYAML otherwise keeps the last value silently, so a project left in the
    file twice by a merge would lose the first copy's series.
    """

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(None, None, f'duplicate key {key!r}', key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def path(root=None):
    return os.path.join(root or TOPDIR, INDEX)


def read(root=None):
    """Return series.yaml as parsed, without checking what it holds."""
    try:
        with open(path(root)) as f:
            return yaml.load(f, Loader=_UniqueKeyLoader)
    except FileNotFoundError:
        raise SeriesError(f'{path(root)} does not exist')
    except yaml.YAMLError as e:
        raise SeriesError(f'{path(root)} is not valid YAML: {e}')


def validate(raw):
    """Return a problem for everything in parsed series.yaml that leaves the index unusable.

    The order entries are written in is not checked here, nor whether the
    series exist on disk; tools/check-series-index.py does both.
    """
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return ['series.yaml must map projects to their series']

    problems = []
    projects_of = {}
    for project, entries in raw.items():
        if not isinstance(entries, dict):
            problems.append(f'project {project}: must map series names to their entries')
            continue
        for name in entries:
            projects_of.setdefault(name, []).append(project)
    for name, projects in projects_of.items():
        if len(projects) > 1:
            problems.append(f'{name}: listed under more than one project ({", ".join(map(str, projects))})')

    graph = {}
    for project, entries in raw.items():
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                problems.append(f'{name}: must be a mapping with at least a status')
                continue
            unknown = sorted(str(k) for k in entry if k not in KEYS)
            if unknown:
                problems.append(f'{name}: unknown key {", ".join(unknown)} (expected {", ".join(KEYS)})')
            if 'status' not in entry:
                problems.append(f'{name}: has no status')
            elif entry['status'] not in STATUSES:
                problems.append(f'{name}: status {entry["status"]!r} is not one of {", ".join(STATUSES)}')
            if 'note' in entry and not isinstance(entry['note'], str):
                problems.append(f'{name}: note must be a string')

            after = entry.get('after', [])
            if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
                problems.append(f'{name}: after must be a list of series names')
                continue
            for before in after:
                if before == name:
                    problems.append(f'{name}: after names the series itself')
                elif before not in projects_of:
                    problems.append(f'{name}: after names {before}, which is not a series')
            graph[name] = [a for a in after if a != name and a in projects_of]

    for cycle in cycles(graph):
        problems.append('the after links form a cycle: ' + ', '.join(cycle))
    return problems


def load(root=None):
    """Return series.yaml as {name: Series}, raising SeriesError if it is missing or invalid."""
    raw = read(root)
    problems = validate(raw)
    if problems:
        raise SeriesError(f'{path(root)} is invalid (tools/check-series-index.py lists every problem):\n  '
                          + '\n  '.join(problems))
    index = {}
    for project, entries in (raw or {}).items():
        for name, entry in entries.items():
            index[name] = Series(name=name, project=project, status=entry['status'],
                                 after=tuple(entry.get('after') or ()), note=entry.get('note'))
    return index


##############################################################################
# Queries                                                                    #
##############################################################################

def lookup(index, name):
    """Return the Series called *name*, raising UnknownSeries if there is none."""
    try:
        return index[name]
    except KeyError:
        raise UnknownSeries(f'{name} is not a series in {INDEX}')


def _matches(series, project, statuses):
    return (project is None or series.project == project) and (not statuses or series.status in statuses)


def select(index, project=None, statuses=None):
    """Return the series of *project* with one of *statuses*, sorted by project, then name.

    None for either means no filter.
    """
    return sorted((s for s in index.values() if _matches(s, project, statuses)), key=lambda s: (s.project, s.name))


def cycles(graph):
    """Return each cycle in *graph*, {node: [nodes it points at]}, as a sorted list of its nodes.

    A cycle is a strongly connected component of more than one node, or a
    node that points at itself. Nodes pointed at but not in *graph* are
    ignored. The result is sorted, so it is the same on every run.
    """
    found = []
    counter = [0]
    number = {}
    lowlink = {}
    stack = []
    on_stack = set()

    def visit(node):
        number[node] = lowlink[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(set(graph[node])):
            if target not in graph:
                continue
            if target not in number:
                visit(target)
                lowlink[node] = min(lowlink[node], lowlink[target])
            elif target in on_stack:
                lowlink[node] = min(lowlink[node], number[target])
        if lowlink[node] == number[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in graph[node]:
                found.append(sorted(component))

    for node in sorted(graph):
        if node not in number:
            visit(node)
    return sorted(found)


def ordered(index, project=None, statuses=None):
    """Return the series in push order, filtered as select() filters.

    Every series comes after the series its `after` names. Among the series
    that could come next, the one first by project, then name, does, so the
    order is the same on every run. The whole index is ordered before
    filtering, so a series still follows one that the filter leaves out.
    Raises CycleError if the after links form a cycle.
    """
    found = cycles({name: list(s.after) for name, s in index.items()})
    if found:
        raise CycleError(found)

    waiting_on = {name: set(s.after) & set(index) for name, s in index.items()}
    dependents = {}
    for name, s in index.items():
        for before in waiting_on[name]:
            dependents.setdefault(before, []).append(name)

    ready = [(s.project, name) for name, s in index.items() if not waiting_on[name]]
    heapq.heapify(ready)
    result = []
    while ready:
        _, name = heapq.heappop(ready)
        result.append(index[name])
        for dependent in dependents.get(name, []):
            waiting_on[dependent].discard(name)
            if not waiting_on[dependent]:
                heapq.heappush(ready, (index[dependent].project, dependent))
    return [s for s in result if _matches(s, project, statuses)]


##############################################################################
# Command line                                                               #
##############################################################################

def _statuses(parser, values):
    """Flatten repeated and comma separated --status values, rejecting unknown ones."""
    statuses = []
    for value in values or []:
        for status in value.split(','):
            status = status.strip()
            if status not in STATUSES:
                parser.error(f'--status {status!r} is not one of {", ".join(STATUSES)}')
            statuses.append(status)
    return statuses


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--root', default=TOPDIR,
                        help='the repository whose series.yaml to read (default: %(default)s)')
    commands = parser.add_subparsers(dest='command', required=True)

    list_parser = commands.add_parser('list', help='print series, one per line')
    list_parser.add_argument('--project', help='only the series of this project')
    list_parser.add_argument('--status', action='append',
                             help=f'only series with this status ({", ".join(STATUSES)}); '
                                  'repeat it or separate statuses with commas')
    list_parser.add_argument('--ordered', action='store_true', help='print in push order')

    after_parser = commands.add_parser('after', help='print the series this one is pushed after')
    after_parser.add_argument('series')

    project_parser = commands.add_parser('project', help="print this series' project")
    project_parser.add_argument('series')

    args = parser.parse_args(argv)
    statuses = _statuses(list_parser, args.status) if args.command == 'list' else None

    try:
        index = load(args.root)
        if args.command == 'list':
            pick = ordered if args.ordered else select
            lines = [s.name for s in pick(index, project=args.project, statuses=statuses)]
        elif args.command == 'after':
            lines = list(lookup(index, args.series.rstrip('/')).after)
        else:
            lines = [lookup(index, args.series.rstrip('/')).project]
    except SeriesError as e:
        print(e, file=sys.stderr)
        return 1

    for line in lines:
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
