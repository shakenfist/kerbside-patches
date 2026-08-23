# Security scanning

Four things scan this repository, and they cover different ground.

| Scanner | Where it runs | What it looks for |
|---------|---------------|-------------------|
| gitleaks | `secret-scanning.yml` | Credentials anywhere in this repository's own history |
| GitHub secret scanning | GitHub, on push | Known third party credential formats |
| Push protection | GitHub, before a push lands | The same formats, rejected rather than reported |
| CodeQL | `codeql-analysis.yml` | Code-level security defects |

GitHub's secret scanning and gitleaks look redundant and are not.
GitHub's knows the shapes of third party credentials -- cloud keys,
provider tokens -- and needs GitHub Advanced Security before it can be
taught a custom pattern. gitleaks runs locally, costs nothing, and can
be taught this repository's own false positives, which matters here
because an archive of OpenStack patches is full of text that looks like
a credential and is not.

## Running the credential scan locally

```bash
# Uses whatever gitleaks is on your PATH
tools/gitleaks-scan.sh

# Or point it at a specific binary
tools/gitleaks-scan.sh --gitleaks /tmp/gitleaks
```

The script scans every commit reachable from `HEAD`, which on a pull
request means the whole of develop plus the branch under test. It
refuses to run against a shallow clone rather than reporting a clean
history it never looked at, so CI checks out with `fetch-depth: 0`.

The scan takes a few minutes here rather than the few seconds it takes
on a source tree, because the history carries large patch files.

### The positive control

Before the real scan, the script plants an AWS access key id and an SSH
private key in a scratch directory and fails if gitleaks does not report
both. A detector that reports nothing is indistinguishable from a
detector that is broken, and an allowlist wide enough to swallow the
control is wide enough to swallow a real credential. Green means
"scanned and found nothing", not "did nothing".

If the control fails, do not trust the clean scan above it. Check the
allowlists in `.gitleaks.toml` first.

### The pinned version

CI downloads gitleaks 8.16.0 with a pinned sha256 rather than installing
a package or using `gitleaks-action`, which refuses to run on
organization repositories without a paid licence. The version is pinned
because `.gitleaks.toml` is written against 8.16's schema: per-rule
allowlists became a repeatable `[[rules.allowlists]]` array in a later
release, while 8.16 takes a single `[rules.allowlist]` table.

Two other 8.16 details are worth knowing before editing the config.
Global allowlist regexes are matched against the *whole match* rather
than the secret alone, so the regexes in `.gitleaks.toml` are written
against the matched text and anchoring one with `^...$` silently stops
it matching. And a triple-quoted TOML literal whose body ends in a
single quote is a syntax error in gitleaks' Go parser even though
Python's `tomllib` accepts it, so no regex in that file ends with `'`.

To move the pin, run `tools/gitleaks-scan.sh` against the new version
locally first and check the positive control still passes.

## Accepting a finding

History cannot be rewritten to unpublish anything from a public
repository -- the objects survive in every fork and in GitHub's own
reflog -- so an accepted finding is a claim that the credential has been
dealt with *where it was trusted*, not that it has been tidied out of
sight. **Never suppress a finding for a credential that still
authorises something.** Rotate it first.

There are two mechanisms and they are not interchangeable.

**Content that recurs** -- a documentation placeholder, a test fixture,
an upstream default -- goes in the `[allowlist]` `regexes` list in
`.gitleaks.toml`, keyed on the text. Editing the code around a
placeholder produces a new finding in a new commit, so anything keyed on
a commit would need replacing every time. This is the right mechanism
for almost everything in this repository, because patch files are
rewritten wholesale on every upstream rebase.

Avoid `paths` for this. Blinding a whole file also blinds a real
credential added to it later, and a patch file is exactly the kind of
file that grows new content without anyone reading all of it.

**A specific historical event** goes in `.gitleaksignore` as a
`commit:path:rule-id:line` fingerprint, which forgives one occurrence
and nothing else -- the same secret in a new commit fails the scan
again. Put a comment on each entry saying what the credential was and
what was done about it; an undocumented entry is indistinguishable from
a mistake.

### What is currently allowlisted

All three entries are false positives from the `generic-api-key` rule
reading OpenStack patch content:

- **APT repository signing key fingerprints**, in the Kolla patches
  adding the RabbitMQ and Erlang archives. A GPG key id identifies a
  *public* key, published by the archive precisely so that everyone has
  it.
- **Nova's object version hash registry**, in the console token patches.
  These are hashes *of* an object's schema, used by
  `test_objects.test_versions` to catch unversioned changes to a
  versioned object. They authorise nothing.
- **Console URLs in Nova's unit tests and API samples**, where the token
  is a test fixture UUID. A real console token is minted per connection
  and lives for seconds.

## Repository settings

Secret scanning, push protection and Dependabot security updates are
enabled on the repository. Push protection is the one with a visible
effect on other people: a push containing something GitHub recognises as
a credential is rejected rather than accepted and reported afterwards.

These settings are exported daily by `export-repo-config.yml`, so a
change to them shows up as a pull request rather than vanishing into the
UI.
