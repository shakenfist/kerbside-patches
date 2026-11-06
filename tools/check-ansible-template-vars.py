#!/usr/bin/env python3

"""Check that every variable an Ansible template references is defined somewhere.

A Jinja2 template in a kolla-ansible role may reference a variable that nothing
defines. Ansible only notices when it renders the template, so a reference
inside a branch no deployment reaches -- a non-default WSGI provider, a backend
nobody enables -- survives review and CI indefinitely. ansible-lint does not
check this.

This tool reads each template's syntax tree rather than rendering it, so every
branch is examined whether or not it is reachable, and no host facts, inventory
or custom filter plugins are needed. For each template it collects the names the
template needs and subtracts the names the tree defines:

  * keys in any YAML file under ansible/ -- role defaults and vars, group_vars,
    and the vars: blocks and set_fact: mappings inside task files
  * values of register: in those same files
  * keys in etc/kolla/passwords.yml
  * Ansible's own magic variables, listed in MAGIC_VARIABLES below
  * names the template itself assigns with {% set %}

Whatever is left is reported. Names that upstream genuinely leaves undefined,
because an operator supplies them only when enabling an optional backend, are
listed in the baseline file; an entry there that the tree has since defined is
reported too, so the baseline cannot quietly rot.

By default only templates touched by _patches/ are checked, since those are the
ones this repository is responsible for. Pass --all to check every template in
the tree, which is useful when exploring but reports a great deal that upstream
owns.

Usage:
  tools/check-ansible-template-vars.py [--tree src/kolla-ansible] [--all]

Exit codes:
  0 - no unresolved references (or the source tree is not assembled)
  1 - unresolved references, or a stale baseline entry
"""

import argparse
import os
import re
import sys

import jinja2
import jinja2.meta


YAML_KEY = re.compile(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:')
REGISTERED = re.compile(r'^\s*register:\s*([a-zA-Z_][a-zA-Z0-9_]*)')
PATCH_TARGET = re.compile(r'^\+\+\+ b/(.*\.j2)$', re.MULTILINE)

# Ansible supplies these at render time and nothing in the tree declares them.
MAGIC_VARIABLES = {
    'ansible_check_mode',
    'ansible_facts',
    'ansible_managed',
    'ansible_parent_role_names',
    'ansible_play_hosts',
    'ansible_version',
    'group_names',
    'groups',
    'hostvars',
    'inventory_dir',
    'inventory_hostname',
    'inventory_hostname_short',
    'item',
    'lookup',
    'omit',
    'outer_item',
    'playbook_dir',
    'query',
    'role_name',
    'role_path',
}

# tests/ holds Zuul job templates, whose variables come from zuul.d/ rather than
# from the Ansible variable precedence chain this tool models.
EXCLUDED_PREFIXES = ('tests/',)

# Directories searched for variable definitions, relative to the source tree.
DEFINITION_ROOTS = ('ansible', os.path.join('etc', 'kolla'))


class PermissiveLookup(dict):
    """A filter or test table that resolves any name to a no-op.

    jinja2.meta.find_undeclared_variables runs the code generator, which rejects
    a filter it does not know. Ansible's filters -- and kolla-ansible's own, such
    as put_address_in_context -- are not loaded here, and loading them would
    drag in Ansible itself. Resolving every name sidesteps that; the generated
    code is never executed, so what the filter would have done does not matter.

    The compiler reaches the table through .get(), not through indexing, so that
    has to be overridden as well as __missing__.
    """

    def __contains__(self, key):
        return True

    def __missing__(self, key):
        return lambda value, *args, **kwargs: value

    def get(self, key, default=None):
        return self[key]


def make_environment():
    """Return a Jinja2 environment that can parse any Ansible template."""
    env = jinja2.Environment()
    env.filters = PermissiveLookup(env.filters)
    env.tests = PermissiveLookup(env.tests)
    return env


def assigned_names(ast):
    """Return the names a template assigns to itself with {% set %}.

    find_undeclared_variables reports a name assigned inside a conditional
    branch and used outside it, because it cannot know the branch was taken.
    Those names are the template's own and are not variables it needs supplied.
    """
    names = set()
    for node in ast.find_all((jinja2.nodes.Assign, jinja2.nodes.AssignBlock)):
        targets = node.target.items if isinstance(node.target, jinja2.nodes.Tuple) else [node.target]
        for target in targets:
            if isinstance(target, jinja2.nodes.Name):
                names.add(target.name)
    return names


def guarded_names(ast):
    """Return the names the template itself declares optional.

    A reference written as {{ foo | default('x') }}, or reached only under
    {% if foo is defined %}, renders without error whether or not anything
    defines foo, and says the author meant it to be optional. A name used both
    with a guard and without one is treated as guarded, which is the single
    unsound case here and much the rarer mistake.
    """
    names = set()
    for node in ast.find_all(jinja2.nodes.Filter):
        if node.name == 'default' and isinstance(node.node, jinja2.nodes.Name):
            names.add(node.node.name)
    for node in ast.find_all(jinja2.nodes.Test):
        if node.name in ('defined', 'undefined') and isinstance(node.node, jinja2.nodes.Name):
            names.add(node.node.name)
    return names


def template_requirements(env, path):
    """Return the variable names a template needs, or raise jinja2.TemplateError."""
    with open(path, encoding='utf-8', errors='replace') as f:
        ast = env.parse(f.read())
    return jinja2.meta.find_undeclared_variables(ast) - assigned_names(ast) - guarded_names(ast)


def defined_names(tree):
    """Return every name the tree defines, from YAML keys and register: values."""
    names = set()
    for relative in DEFINITION_ROOTS:
        for base, _dirs, files in os.walk(os.path.join(tree, relative)):
            for name in files:
                if not name.endswith(('.yml', '.yaml')):
                    continue
                with open(os.path.join(base, name), encoding='utf-8', errors='replace') as f:
                    for line in f:
                        key = YAML_KEY.match(line)
                        if key:
                            names.add(key.group(1))
                        registered = REGISTERED.match(line)
                        if registered:
                            names.add(registered.group(1))
    return names


def patched_templates(patch_dir):
    """Return the template paths our patches create or modify.

    A patch for another project names paths that do not exist in this tree; the
    caller drops those, which is simpler than mapping each patch to its project.
    """
    paths = set()
    if not os.path.isdir(patch_dir):
        return paths
    for name in sorted(os.listdir(patch_dir)):
        if not name.endswith('.patch'):
            continue
        with open(os.path.join(patch_dir, name), encoding='utf-8', errors='replace') as f:
            paths.update(PATCH_TARGET.findall(f.read()))
    return paths


def all_templates(tree):
    """Return every template in the tree, as paths relative to it."""
    paths = set()
    for base, _dirs, files in os.walk(tree):
        if '.git' in base.split(os.sep):
            continue
        for name in files:
            if name.endswith('.j2'):
                paths.add(os.path.relpath(os.path.join(base, name), tree))
    return paths


def select_templates(tree, candidates):
    """Return the candidates that exist in the tree and are not excluded."""
    selected = []
    for path in sorted(candidates):
        if path.startswith(EXCLUDED_PREFIXES):
            continue
        if os.path.isfile(os.path.join(tree, path)):
            selected.append(path)
    return selected


def load_baseline(path):
    """Return the names the baseline file exempts. Blank lines and # are ignored."""
    names = set()
    if not os.path.isfile(path):
        return names
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.split('#', 1)[0].strip()
            if line:
                names.add(line)
    return names


def check(tree, templates, baseline):
    """Return (findings, stale), where findings are (path, name) pairs.

    A baseline entry is stale once the tree defines the name, which is how an
    exemption for an upstream variable outlives the reason it was granted. An
    entry no template happens to reference is not reported, because which
    templates are in scope depends on the arguments this ran with.
    """
    env = make_environment()
    known = defined_names(tree) | MAGIC_VARIABLES
    findings = []

    for path in templates:
        try:
            required = template_requirements(env, os.path.join(tree, path))
        except jinja2.TemplateError as e:
            findings.append((path, f'template does not parse: {e}'))
            continue
        findings.extend((path, name) for name in sorted(required - known - baseline))

    return findings, sorted(baseline & known)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    topdir = os.path.dirname(here)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--tree', default=os.path.join(topdir, 'src', 'kolla-ansible'),
                        help='assembled source tree to check (default: src/kolla-ansible)')
    parser.add_argument('--patches', default=os.path.join(topdir, '_patches'),
                        help='directory of patches naming the templates we own')
    parser.add_argument('--baseline', default=os.path.join(here, 'ansible-template-vars-baseline.txt'),
                        help='file listing names upstream leaves undefined')
    parser.add_argument('--all', action='store_true',
                        help='check every template in the tree, not just the ones our patches touch')
    args = parser.parse_args()

    if not os.path.isdir(args.tree):
        print(f'{args.tree} is not assembled, skipping the template variable check.')
        print('Run _build/test-apply.sh --skip-tests kolla-ansible-kerbside to assemble it.')
        return 0

    candidates = all_templates(args.tree) if args.all else patched_templates(args.patches)
    templates = select_templates(args.tree, candidates)
    if not templates:
        print('No templates to check.')
        return 0

    findings, stale = check(args.tree, templates, load_baseline(args.baseline))

    for path, name in findings:
        print(f'{path}: {name} is not defined anywhere in {os.path.basename(args.tree)}')
    for name in stale:
        print(f'{args.baseline}: {name} is no longer undefined, remove this entry')

    print(f'\nChecked {len(templates)} template(s): '
          f'{len(findings)} unresolved reference(s), {len(stale)} stale baseline entr(ies).')
    return 1 if findings or stale else 0


if __name__ == '__main__':
    sys.exit(main())
