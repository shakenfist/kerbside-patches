#!/bin/bash

# Scan this repository's git history for leaked credentials.
#
# Two things happen here, and the second is the more important one:
#
# 1. gitleaks scans every commit reachable from HEAD, or just the
#    commits in --range, against .gitleaks.toml, and the script fails
#    if anything is found.
#
# 2. A positive control proves the scanner can still fire. A detector
#    which reports nothing is indistinguishable from a detector which is
#    broken, and .gitleaks.toml carries allowlists which could in
#    principle grow until they forgive everything. So we plant two
#    credentials in a scratch directory and fail if gitleaks does not
#    report both of them. Green here means "scanned and found nothing",
#    not "did nothing".
#
# Reachability from HEAD, rather than the default of every ref, is
# deliberate: scanning every ref pulls in every abandoned daily-rebase
# and data branch this repository has ever pushed, which is both slow
# and not what anyone means by "scan this project's history".
#
# --range START..END narrows the scan to the commits reachable from END
# and not from START, which is how CI scans just a pull request's
# commits, or just the commits a push to develop added: the full history
# takes over ten minutes here. A range is widened back to the full
# history in two cases, because a narrow scan would be wrong in both:
#
# - the range changes the scan's own rules (.gitleaks.toml,
#   .gitleaksignore or this script). A new rule must be applied to the
#   history already on develop, not only to the commits that added it;
#   and a widened allowlist must be seen to pass against that history.
#
# - START is not in this clone, as after a force push to develop. There
#   is then no way to tell which commits are new.
#
# Usage:
#   tools/gitleaks-scan.sh [--gitleaks PATH] [--range START..END]
#
# Runs from anywhere inside the working tree -- it changes to the top
# itself -- but the clone must be a full one, not shallow.

set -e

GITLEAKS=gitleaks
RANGE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --gitleaks)
            if [ -z "$2" ]; then
                echo "--gitleaks needs a path."
                exit 1
            fi
            GITLEAKS="$2"
            shift 2
            ;;
        --range)
            case "$2" in
                ?*..?*) ;;
                *)
                    echo "--range needs START..END, not '$2'."
                    exit 1
                    ;;
            esac
            RANGE="$2"
            shift 2
            ;;
        *)
            # Refuse rather than ignore. A silently discarded flag would
            # leave the caller believing they had changed the scan.
            echo "Unrecognised argument: $1"
            echo "Usage: tools/gitleaks-scan.sh [--gitleaks PATH] [--range START..END]"
            exit 1
            ;;
    esac
done

if ! command -v "$GITLEAKS" >/dev/null 2>&1 && [ ! -x "$GITLEAKS" ]; then
    echo "gitleaks not found. Install it, or pass --gitleaks PATH."
    exit 1
fi

echo "Using $("$GITLEAKS" version) from $GITLEAKS"

if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
    echo "This is a shallow clone, so most of history cannot be scanned."
    echo "Check out with fetch-depth: 0."
    exit 1
fi

# Both .gitleaks.toml and .gitleaksignore are named relative to the
# working directory, and gitleaks looks for the latter in the directory
# it is run from. Run from a subdirectory -- the obvious thing to do when
# reproducing a CI failure -- the config would simply not be found, and
# any accepted historical findings would be missed silently.
cd "$(git rev-parse --show-toplevel)"

# The positive control. Both credentials are generated here rather than
# written into this file, because a literal one would be found by the
# scan above -- correctly, since a credential in a committed file is
# exactly what we are looking for.
#
# This repository mints no credential format of its own, so the control
# uses two upstream shapes instead: an AWS access key id and an SSH
# private key. Neither is allowlisted in .gitleaks.toml, so both are
# things the scan is expected to report.
CONTROL=$(mktemp -d)
trap 'rm -rf "$CONTROL"' EXIT

body=$(tr -dc 'A-Z0-9' </dev/urandom | head -c 16)
printf 'AWS_ACCESS_KEY_ID = "AKIA%s"\n' "$body" > "$CONTROL/planted.py"
ssh-keygen -q -t rsa -b 2048 -N '' -C control@example.com \
    -f "$CONTROL/id_rsa"

echo
echo "Positive control: two credentials planted in a scratch directory."
set +e
"$GITLEAKS" detect --source "$CONTROL" --config .gitleaks.toml --no-git \
    --redact --no-banner --report-path "$CONTROL/report.json" \
    --report-format json
control_status=$?
set -e

found=$(python3 -c "
import json

with open('$CONTROL/report.json') as f:
    print(' '.join(sorted({x['RuleID'] for x in json.load(f)})), end='')
")

for rule in aws-access-token private-key; do
    case " $found " in
        *" $rule "*) ;;
        *)
            echo
            echo "The positive control failed: gitleaks did not report the"
            echo "$rule rule against a credential planted for it to find."
            echo "Rules which did fire: ${found:-none}."
            echo
            echo "Do not trust a clean scan until this passes. Check the"
            echo "allowlists in .gitleaks.toml -- an allowlist wide enough"
            echo "to swallow the control is wide enough to swallow a real"
            echo "credential."
            exit 1
            ;;
    esac
done

if [ $control_status -eq 0 ]; then
    echo "The positive control did not set a failure exit code."
    exit 1
fi

echo "Positive control passed: both planted credentials were reported."
echo

# Settle what the real scan covers. gitleaks would fail on a range it
# cannot resolve with only "git error encountered, see logs", so both
# ends are checked here: a missing end is a caller error worth naming,
# and a missing start widens to the full history as described above.
LOG_OPTS="HEAD"
if [ -n "$RANGE" ]; then
    start="${RANGE%%..*}"
    end="${RANGE#*..}"
    if ! git rev-parse --verify --quiet "${end}^{commit}" >/dev/null; then
        echo "The end of the range, $end, is not a commit in this clone."
        exit 1
    fi
    if ! git rev-parse --verify --quiet "${start}^{commit}" >/dev/null; then
        echo "The start of the range, $start, is not in this clone, so"
        echo "which commits are new cannot be told. Scanning the full"
        echo "history instead."
    elif git diff --name-only "$start" "$end" -- \
            .gitleaks.toml .gitleaksignore tools/gitleaks-scan.sh \
            | grep -q .; then
        echo "The range changes the scan's own rules, so they must hold"
        echo "against the whole history. Scanning the full history."
    else
        LOG_OPTS="$RANGE"
    fi
fi

# The real scan.
if [ "$LOG_OPTS" = "HEAD" ]; then
    echo "Scanning every commit reachable from HEAD."
else
    echo "Scanning the $(git rev-list --count "$LOG_OPTS") commit(s) in $LOG_OPTS."
fi
"$GITLEAKS" detect --source . --config .gitleaks.toml --log-opts="$LOG_OPTS" \
    --redact --no-banner
