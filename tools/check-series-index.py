#!/usr/bin/env python3
"""Check that series.yaml agrees with the series directories, offline.

A series directory is a top-level directory holding both an ORDER file and a
config.yaml. src/ (upstream clones, which ship config.yaml files of their own)
and upstream/ never count, and nor does anything below the top level. This
reports:

    - a series directory with no entry in series.yaml, or an entry with no
      series directory;
    - a series filed under a project other than its config.yaml `directory`;
    - a series listed under more than one project;
    - an entry that is not a mapping, has no status, has a status other than
      push, hold or carry, or has a key other than status, after and note;
    - an `after` that is not a list, names something that is not a series, or
      names the series itself;
    - `after` links that form a cycle, naming the series in it;
    - projects not sorted by name, or series not sorted by name within their
      project;
    - a config.yaml with a `repush` key, which `status` in series.yaml replaces.

Every problem is printed, not only the first. Checks that need Gerrit are not
made here.

Usage:
    tools/check-series-index.py [--root DIR]

--root names the repository to check; it defaults to the one holding this
script.

Exit status:
    0   series.yaml and the series directories agree
    1   at least one problem was found
"""

import argparse
import os
import sys

import yaml

import series

# Top-level directories that are never series, whatever they hold.
NOT_SERIES = ('src', 'upstream')


def configs(root):
    """Return {directory: parsed config.yaml} for every top-level directory holding a config.yaml.

    A config.yaml that does not parse is returned as the error message.
    """
    found = {}
    for entry in sorted(os.listdir(root)):
        config = os.path.join(root, entry, 'config.yaml')
        if entry in NOT_SERIES or not os.path.isfile(config):
            continue
        try:
            with open(config) as f:
                found[entry] = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            found[entry] = f'{entry}/config.yaml is not valid YAML: {e}'
    return found


def series_directories(root, found_configs):
    """Return the names of the top-level directories holding both ORDER and config.yaml."""
    return {name for name in found_configs if os.path.isfile(os.path.join(root, name, 'ORDER'))}


def unsorted(names, what):
    """Return a problem for each name in *names* that comes before the one after it."""
    names = [str(n) for n in names]
    return [f'{what}: {a} should come after {b}' for a, b in zip(names, names[1:]) if a > b]


def problems(root):
    """Return every problem found in the repository at *root*."""
    found = []
    try:
        raw = series.read(root)
    except series.SeriesError as e:
        found.append(str(e))
        raw = None

    found.extend(series.validate(raw))
    index = raw if isinstance(raw, dict) else {}
    project_of = {}
    for project, entries in index.items():
        if isinstance(entries, dict):
            for name in entries:
                project_of.setdefault(name, project)

    found.extend(unsorted(index, 'projects are not sorted by name'))
    for project, entries in index.items():
        if isinstance(entries, dict):
            found.extend(unsorted(entries, f'series under {project} are not sorted by name'))

    found_configs = configs(root)
    directories = series_directories(root, found_configs)
    for name in sorted(directories - set(project_of)):
        found.append(f'{name}: has ORDER and config.yaml but no entry in {series.INDEX}')
    for name in sorted(set(project_of) - directories, key=str):
        found.append(f'{name}: listed in {series.INDEX} but there is no {name}/ with ORDER and config.yaml')

    for name, config in found_configs.items():
        if isinstance(config, str):
            found.append(config)
            continue
        if not isinstance(config, dict):
            found.append(f'{name}/config.yaml must be a mapping')
            continue
        if 'repush' in config:
            found.append(f'{name}/config.yaml: has a repush key; set status in {series.INDEX} instead')
        if name in directories and name in project_of and config.get('directory') != project_of[name]:
            found.append(f'{name}: filed under {project_of[name]} in {series.INDEX}, '
                         f'but config.yaml says directory {config.get("directory")}')
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--root', default=series.TOPDIR,
                        help='the repository to check (default: %(default)s)')
    args = parser.parse_args(argv)

    found = problems(args.root)
    for problem in found:
        print(problem)
    if found:
        print(f'{series.INDEX}: {len(found)} problem{"s" if len(found) != 1 else ""}; '
              f'see tools/series.py for its format')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
