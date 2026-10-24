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
git can commit with it, `tools/check-depends-on.py` reads it to find the
Depends-On footers, and `tools/check-llm-attribution.py` reads it to check how
an LLM is credited. They share this module so they can never disagree about
what a patch's message is -- a disagreement of exactly that kind is why the
Depends-On checker exists. `tools/gerrit-pre-push-lint` uses the same
attribution rules on the commits it checks.
"""

import re

DEPENDS_ON = re.compile(r'^Depends-On:\s*(\S.*?)\s*$')
CO_AUTHORED_BY = re.compile(r'^Co-Authored-By:', re.IGNORECASE)

# Known LLM tool names and email domains. This deliberately does not match "any
# noreply address": a noreply address can belong to a person (GitHub's, for
# example). Matching known tools can miss one we have not heard of, but it never
# flags a person.
LLM_NAMES = re.compile(r'Claude|ChatGPT|GPT-\d|Copilot|Gemini|Codex', re.IGNORECASE)
LLM_DOMAINS = ('anthropic.com', 'openai.com')

ASSISTED_BY = re.compile(r'^Assisted-By:', re.IGNORECASE)
EFFORT = re.compile(r'^([a-z]+) effort$', re.IGNORECASE)


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


def author(path):
    """Return the Author header of the patch at *path*, or None if it has none.

    The value is in the `Name <email>` form that `git commit --author` takes.
    Committing with it keeps a patch carried from someone else's review
    attributed to them when we push an update to that review.
    """
    with open(path, 'r') as f:
        while line := f.readline():
            if len(line.rstrip()) == 0:
                break
            if line.startswith('Author: '):
                return line[len('Author: '):].strip()
    return None


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


def is_llm_co_author(line):
    """Return True if *line* is a Co-Authored-By trailer that credits an LLM.

    See LLM_NAMES and LLM_DOMAINS for what counts as an LLM.
    """
    if not CO_AUTHORED_BY.match(line):
        return False
    value = line.split(':', 1)[1]
    email = re.search(r'<[^>]*@([^>\s]+)>', value)
    domain = email.group(1).lower() if email else ''
    return bool(LLM_NAMES.search(value.split('<')[0])) or domain in LLM_DOMAINS


def llm_co_authors(message):
    """Return the Co-Authored-By lines of *message* that credit an LLM.

    Lines are returned unstripped and in order. The community convention is
    that an LLM is credited with Assisted-By, and Co-Authored-By is reserved
    for people.
    """
    return [line for line in message.split('\n') if is_llm_co_author(line)]


def standard_assisted_by(value):
    """Return the Assisted-By line for *value*, the value of an attribution trailer.

    The standard form is `Assisted-By: <model> (<effort> effort)`, or just the
    model when the effort level is not known. The effort level stays because it
    changes what the model produces; the email address, the context window and
    any other settings are dropped.
    """
    settings = [s.strip() for group in re.findall(r'\(([^)]*)\)', value) for s in group.split(',')]
    model = ' '.join(re.sub(r'\([^)]*\)|<[^>]*>', ' ', value).split())
    effort = next((m.group(1).lower() for s in settings if (m := EFFORT.match(s))), None)
    return f'Assisted-By: {model} ({effort} effort)' if effort else f'Assisted-By: {model}'


def attribution_problems(message):
    """Yield (line, fix) for each attribution trailer of *message* that breaks the rules.

    An LLM is credited with Assisted-By only, in the standard_assisted_by form.
    fix is the line to write instead, or None when the line should simply go: a
    Co-Authored-By for an LLM becomes an Assisted-By only when the message has
    none.
    """
    lines = message.split('\n')
    assisted = any(ASSISTED_BY.match(line) for line in lines)
    for line in lines:
        if ASSISTED_BY.match(line):
            fix = standard_assisted_by(line.split(':', 1)[1])
            if line != fix:
                yield line, fix
        elif is_llm_co_author(line):
            yield line, None if assisted else standard_assisted_by(line.split(':', 1)[1])
            assisted = True
