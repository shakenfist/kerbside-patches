#!/usr/bin/env python3
"""Import one service role's modules and report its memory, modules and import chains.

Usage: probe.py ROLE MODULE [MODULE ...]

Each MODULE is imported in order. A MODULE of the form call:package.module.function
imports package.module and calls function() with no arguments, for setup such as
cinder.objects.register_all. Import errors are recorded rather than fatal, so a
role whose last plugin fails still reports what it loaded before the failure.

Prints one JSON object: anonymous and total RSS before and after, the number of
modules loaded, the module count per top-level package, and for each top-level
package the chain of importers that first loaded it, innermost first.

Run it with python -I in a fresh interpreter per role; anything imported before
the probe starts is not attributed.
"""
import builtins
import collections
import importlib
import json
import sys


def status():
    out = {}
    with open('/proc/self/status') as f:
        for line in f:
            key, _, value = line.partition(':')
            if key in ('VmRSS', 'RssAnon'):
                out[key] = int(value.split()[0]) // 1024
    return out


# Map each module to the module whose import statement first loaded it. Nested
# imports finish first, so the innermost importer claims each new module.
parent = {}
_orig_import = builtins.__import__


def _import(name, globals=None, locals=None, fromlist=(), level=0):
    before = set(sys.modules)
    try:
        return _orig_import(name, globals, locals, fromlist, level)
    finally:
        if len(sys.modules) != len(before):
            importer = (globals or {}).get('__name__', '?')
            for m in set(sys.modules) - before:
                parent.setdefault(m, importer)


def chain(module, limit=12):
    out = [module]
    seen = set()
    while out[-1] in parent and out[-1] not in seen and len(out) < limit:
        seen.add(out[-1])
        out.append(parent[out[-1]])
    return out


def main():
    role, modules = sys.argv[1], sys.argv[2:]
    builtins.__import__ = _import

    base = status()
    errors = {}
    for m in modules:
        try:
            if m.startswith('call:'):
                mod, _, fn = m[len('call:'):].rpartition('.')
                getattr(importlib.import_module(mod), fn)()
            else:
                importlib.import_module(m)
        except Exception as e:
            errors[m] = '%s: %s' % (type(e).__name__, e)
    after = status()

    tops = collections.Counter(m.split('.')[0] for m in sys.modules)
    chains = {}
    for top in tops:
        firsts = [m for m in sys.modules if m.split('.')[0] == top and m in parent]
        if firsts:
            # The shortest name is usually the package itself.
            chains[top] = chain(min(firsts, key=len))

    print(json.dumps({'role': role, 'base': base, 'after': after, 'errors': errors,
                      'nmodules': len(sys.modules), 'packages': dict(tops), 'chains': chains}))


if __name__ == '__main__':
    main()
