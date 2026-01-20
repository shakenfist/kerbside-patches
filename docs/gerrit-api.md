# Gerrit API Reference for OpenStack Development

This document describes how to interact with the OpenStack Gerrit instance at
`review.opendev.org` using both SSH and REST APIs. This information was gathered
while building the `gerrit-pre-push-lint` tool.

## SSH API

The SSH API provides access to Gerrit commands over SSH on port 29418.

### Authentication

You need an SSH key registered with your Gerrit account. Register keys at:
https://review.opendev.org/settings/#SSHKeys

### Basic Connection

```bash
ssh -p 29418 review.opendev.org gerrit --help
```

### Available Commands

Key commands for review analysis:

| Command | Description |
|---------|-------------|
| `query` | Query the change database |
| `review` | Apply reviews to patch sets |
| `ls-projects` | List visible projects |
| `stream-events` | Monitor events in real-time |

### Query Command

The `query` command is the most useful for gathering review data.

```bash
ssh -p 29418 review.opendev.org gerrit query [OPTIONS] QUERY
```

#### Query Options

| Option | Description |
|--------|-------------|
| `--format [TEXT\|JSON]` | Output format (JSON recommended for parsing) |
| `--comments` | Include patch set and inline comments |
| `--current-patch-set` | Include current patch set info |
| `--all-approvals` | Include all patch set approvals |
| `--all-reviewers` | Include all reviewers |
| `--commit-message` | Include full commit message |
| `--files` | Include file list on patch sets |
| `--dependencies` | Include depends-on/needed-by info |
| `--patch-sets` | Include all patch set info |
| `--submit-records` | Include submit and label status |
| `--no-limit` | Return all results (default limit applies otherwise) |
| `--start N` | Skip first N results (for pagination) |

#### Query Syntax

Queries use Gerrit's search syntax:

```bash
# Find merged changes in a project
project:openstack/kolla status:merged

# Find open changes by author
project:openstack/kolla-ansible owner:username status:open

# Find changes with specific topic
topic:debian-add-spice-package

# Find changes modified after a date
project:openstack/kolla after:2024-01-01

# Combine with limit
project:openstack/kolla status:merged limit:50
```

#### Example: Fetch Recent Merged Reviews with Comments

```bash
ssh -p 29418 review.opendev.org gerrit query \
    --format=JSON \
    --comments \
    --current-patch-set \
    'project:openstack/kolla status:merged limit:20'
```

#### JSON Output Structure

Each change is output as a single JSON line:

```json
{
  "project": "openstack/kolla",
  "branch": "master",
  "id": "Ib4ced4a04353b9c11c6f0abb7cfa91001b239e53",
  "number": 972441,
  "subject": "Re-enable SPICE support on Debian.",
  "owner": {
    "name": "Michael Still",
    "email": "mikal@stillhq.com",
    "username": "mikalstill"
  },
  "url": "https://review.opendev.org/c/openstack/kolla/+/972441",
  "status": "MERGED",
  "comments": [
    {
      "timestamp": 1767779873,
      "reviewer": {"name": "...", "username": "..."},
      "message": "Uploaded patch set 1."
    }
  ],
  "currentPatchSet": {
    "number": 4,
    "approvals": [
      {"type": "Code-Review", "value": "2", "by": {...}}
    ]
  }
}
```

The final line contains statistics:

```json
{"type": "stats", "rowCount": 20, "runTimeMilliseconds": 45, "moreChanges": true}
```

**Note**: The `comments` field from the SSH API contains patch-set-level messages
(uploads, votes, general comments) but NOT inline code comments. For inline
comments, use the REST API.

## REST API

The REST API provides more detailed data, particularly inline code comments.

### Base URL

```
https://review.opendev.org/
```

### Response Format

REST API responses are prefixed with `)]}'` to prevent JSON hijacking. Strip
this before parsing:

```bash
curl -s 'https://review.opendev.org/changes/...' | tail -c +5 | jq .
```

Or in Python:

```python
import requests
import json

response = requests.get('https://review.opendev.org/changes/...')
data = json.loads(response.text[4:])  # Skip ")]}'" prefix
```

### Endpoints

#### List Changes

```
GET /changes/?q=QUERY&n=LIMIT&o=OPTIONS
```

Query parameters:
- `q`: Search query (same syntax as SSH)
- `n`: Number of results
- `o`: Additional data to include (can be repeated)

Option values for `o`:
- `CURRENT_REVISION` - Include current revision info
- `ALL_REVISIONS` - Include all revisions
- `MESSAGES` - Include change messages
- `DETAILED_LABELS` - Include detailed label info
- `DETAILED_ACCOUNTS` - Include account details
- `CURRENT_COMMIT` - Include current commit info
- `ALL_COMMITS` - Include all commits
- `CURRENT_FILES` - Include file list for current revision
- `ALL_FILES` - Include file list for all revisions

Example:

```bash
curl -s 'https://review.opendev.org/changes/?q=project:openstack/kolla+status:merged&n=5&o=CURRENT_REVISION&o=MESSAGES&o=DETAILED_LABELS'
```

#### Get Inline Comments

This is the key endpoint for analyzing code review feedback:

```
GET /changes/{change-id}/comments
```

Example:

```bash
curl -s 'https://review.opendev.org/changes/972441/comments'
```

Response structure (keyed by file path):

```json
{
  "/COMMIT_MSG": [
    {
      "author": {
        "_account_id": 13252,
        "name": "Dr. Jens Harbott",
        "email": "frickler@offenerstapel.de"
      },
      "patch_set": 3,
      "line": 21,
      "range": {
        "start_line": 21,
        "start_character": 24,
        "end_line": 21,
        "end_character": 70
      },
      "message": "the pkgs doesn't exist for bookworm...",
      "unresolved": true
    }
  ],
  "/PATCHSET_LEVEL": [...],
  "releasenotes/notes/file.yaml": [...]
}
```

Special file paths:
- `/COMMIT_MSG` - Comments on the commit message
- `/PATCHSET_LEVEL` - General comments not attached to a specific file

#### Get Change Detail

```
GET /changes/{change-id}/detail
```

Returns comprehensive change information including messages, labels, and
reviewers.

#### Get Specific Revision

```
GET /changes/{change-id}/revisions/{revision-id}/review
```

Where `revision-id` can be a commit SHA or `current`.

## Common Patterns for Review Analysis

### Fetching Reviews with Multiple Patch Sets

Reviews with multiple patch sets indicate feedback was given. Query for these:

```bash
# SSH: Get reviews and check currentPatchSet.number > 1
ssh -p 29418 review.opendev.org gerrit query \
    --format=JSON \
    --current-patch-set \
    'project:openstack/kolla-ansible status:merged limit:50'
```

Then filter in your code for `currentPatchSet.number > 1`.

### Getting Detailed Inline Comments

For each interesting change number, fetch inline comments:

```bash
curl -s "https://review.opendev.org/changes/${change_number}/comments"
```

### Identifying Review Patterns

Common patterns found in Kolla/Kolla-Ansible reviews:

1. **Release Notes**: Look for comments mentioning "release note", "reno"
2. **Bug References**: Comments suggesting "bug report", "launchpad"
3. **Code Style**: Comments about "line length", "spacing", "formatting"
4. **Ansible Issues**: Comments about "changed_when", "name[missing]"
5. **Documentation**: Comments about wording, terminology

### Rate Limiting

The OpenStack Gerrit instance does not appear to have aggressive rate limiting,
but be respectful:

- Add delays between requests when fetching many changes
- Cache responses when possible
- Use the `limit` parameter to fetch only what you need

## Example: Batch Fetching Reviews

```python
#!/usr/bin/env python3
"""Example script to fetch and analyze Gerrit reviews."""

import json
import subprocess
import requests
import time

def fetch_reviews_ssh(project: str, limit: int = 50) -> list:
    """Fetch reviews via SSH API."""
    cmd = [
        'ssh', '-p', '29418', 'review.opendev.org',
        'gerrit', 'query',
        '--format=JSON',
        '--comments',
        '--current-patch-set',
        f'project:{project} status:merged limit:{limit}'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    reviews = []
    for line in result.stdout.strip().split('\n'):
        data = json.loads(line)
        if data.get('type') != 'stats':
            reviews.append(data)
    return reviews

def fetch_inline_comments(change_number: int) -> dict:
    """Fetch inline comments via REST API."""
    url = f'https://review.opendev.org/changes/{change_number}/comments'
    response = requests.get(url)
    return json.loads(response.text[4:])  # Skip ")]}'"

def analyze_reviews(project: str):
    """Analyze reviews for common feedback patterns."""
    reviews = fetch_reviews_ssh(project)

    for review in reviews:
        # Only look at reviews with multiple patch sets
        if review.get('currentPatchSet', {}).get('number', 1) > 1:
            change_num = review['number']
            print(f"Analyzing {review['subject']} (#{change_num})")

            comments = fetch_inline_comments(change_num)
            for filepath, file_comments in comments.items():
                for comment in file_comments:
                    print(f"  {filepath}: {comment['message'][:80]}...")

            time.sleep(0.5)  # Be nice to the server

if __name__ == '__main__':
    analyze_reviews('openstack/kolla-ansible')
```

## References

- [Gerrit REST API Documentation](https://gerrit-review.googlesource.com/Documentation/rest-api.html)
- [Gerrit SSH Command Reference](https://gerrit-review.googlesource.com/Documentation/cmd-index.html)
- [OpenDev Gerrit Instance](https://review.opendev.org/)
- [Gerrit Query Syntax](https://gerrit-review.googlesource.com/Documentation/user-search.html)
