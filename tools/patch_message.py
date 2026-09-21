"""The commit message embedded in a patch file.

A patch in `_patches/` is `git show` output, so its commit message sits between
the header and the first `diff --git`, indented by four spaces:

    commit 57f69c143e51642546a2aea46b39c5b1bc0f4a34
    Author: ...
    Date:   ...
    (blank)
        Subject line.
    (blank)
        Body, footers.
    (blank)
    diff --git ...

`tools/extract-commit-message` writes that message out to `<patch>-message` so
git can commit with it, and `tools/check-depends-on.py` reads it to find the
Depends-On footers. They share this module so the two can never disagree about
what a patch's message is -- a disagreement of exactly that kind is why the
Depends-On checker exists.
"""

import re

DEPENDS_ON = re.compile(r'^Depends-On:\s*(\S.*?)\s*$')


def extract(path):
    """Return the commit message embedded in the patch at *path*.

    The result has no trailing newline, matching what `git commit --file`
    expects and what `<patch>-message` holds on disk.
    """
    with open(path, 'r') as f:
        # Skip over the header.
        while line := f.readline():
            if len(line.rstrip()) == 0:
                break

        commit = []
        while line := f.readline():
            if line.startswith('diff --git '):
                break
            commit.append(line.rstrip())

    # Remove the four space indent git show adds to the message.
    cleaned = []
    for line in commit:
        if line.startswith('    '):
            cleaned.append(line[4:])
        else:
            cleaned.append(line)

    # Remove the blank line git show leaves before the diff.
    if cleaned and len(cleaned[-1].rstrip()) == 0:
        cleaned = cleaned[:-1]

    return '\n'.join(cleaned)


def subject(message):
    """Return the subject line of a commit message."""
    return message.split('\n', 1)[0].strip()


def depends_on(message):
    """Yield (line number, value) for each Depends-On footer in *message*.

    Line numbers are one based and relative to the message, not the patch
    file, because that is the form a human can act on: the message is what
    they edit in the patch's indented block.
    """
    for number, line in enumerate(message.split('\n'), start=1):
        match = DEPENDS_ON.match(line)
        if match:
            yield number, match.group(1)
