#!/usr/bin/env python3
"""Check how the patches in _patches/ credit an LLM.

The OpenStack community asks that an LLM be credited with Assisted-By only;
Co-Authored-By is reserved for people. Every Assisted-By line takes one form,
`Assisted-By: <model> (<effort> effort)`, or just the model when the effort
level is not known, with no email address. Takes .patch and .patch-message
files (anything else is ignored) and prints `file: line` for each offending
trailer. patch_message.attribution_problems holds the rules.

With --fix, each LLM Co-Authored-By line is removed, or converted to an
Assisted-By when the message has none, and each Assisted-By line is rewritten
in the standard form, dropping any that then repeat one above. In a .patch
only the commit message is edited, never the diff, and an existing
<patch>-message is regenerated with tools/extract-commit-message.

Exit status: 1 if any offending line was found (and, with --fix, fixed), else 0.
"""

import argparse
import os
import subprocess
import sys

# Pre-commit runs this by path, so make the sibling module importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import patch_message  # noqa: E402

INDENT = '    '


def region(lines, is_patch):
    """Return (start, end, indent) of the commit message within *lines*.

    Uses the same boundaries as patch_message.extract(): after the header's
    first blank line, up to the first `diff --git`.
    """
    if not is_patch:
        return 0, len(lines), ''
    start = next((i + 1 for i, line in enumerate(lines) if not line.rstrip()), len(lines))
    end = next((i for i in range(start, len(lines)) if lines[i].startswith('diff --git ')), len(lines))
    return start, end, INDENT


def fix_lines(lines, indent):
    """Return *lines* with their attribution trailers fixed."""
    bodies = [line[len(indent):] if line.startswith(indent) else line for line in lines]
    fixes = {}
    for line, fix in patch_message.attribution_problems('\n'.join(bodies)):
        # The first of two identical lines carries the conversion, if any.
        fixes.setdefault(line, fix)
    out = []
    seen = set()
    for i, (line, body) in enumerate(zip(lines, bodies)):
        if patch_message.ASSISTED_BY.match(body):
            body = fixes.get(body, body)
        elif body in fixes:
            body = fixes[body]
            if body is None:
                # Do not leave a doubled blank line where a line was removed.
                if out and not out[-1].strip() and (i + 1 == len(lines) or not lines[i + 1].strip()):
                    out.pop()
                continue
        else:
            out.append(line)
            continue
        if body not in seen:
            seen.add(body)
            out.append(indent + body)
    return out


def process(path, fix):
    is_patch = path.endswith('.patch')
    if is_patch:
        message = patch_message.extract(path)
    else:
        with open(path) as f:
            message = f.read()
    offenders = [line for line, _ in patch_message.attribution_problems(message)]
    if not offenders:
        return 0

    for number, line in enumerate(message.split('\n'), start=1):
        if line in offenders:
            print(f'{path}: {number}: {line}')
    if not fix:
        return len(offenders)

    with open(path) as f:
        lines = f.read().split('\n')
    start, end, indent = region(lines, is_patch)
    lines[start:end] = fix_lines(lines[start:end], indent)
    if not is_patch:
        while lines and not lines[-1].strip() and len(lines) > 1 and not lines[-2].strip():
            lines.pop()
    with open(path, 'w') as f:
        f.write('\n'.join(lines))

    if is_patch and os.path.exists(f'{path}-message'):
        extractor = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extract-commit-message')
        subprocess.run([sys.executable, extractor, path], check=True, stdout=subprocess.DEVNULL)
    return len(offenders)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--fix', action='store_true', help='fix the offending lines in place')
    parser.add_argument('files', nargs='*')
    args = parser.parse_args()

    found = 0
    for path in args.files:
        if path.endswith(('.patch', '.patch-message')):
            found += process(path, args.fix)
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
