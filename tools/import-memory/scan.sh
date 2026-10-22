#!/bin/bash
# Find packages that OpenStack services import but do not use, and what they cost.
# See docs/import-memory-scans.md.
#
#   scan.sh build                       build the image from master
#   scan.sh probe [ROLES] > out.jsonl   probe every role in ROLES (default roles.txt)
#   scan.sh marginal BASE TARGET        anon RSS that TARGET adds on top of BASE
#   scan.sh shell                       a shell in the image, to read installed source
#
# IMAGE (default import-memory:latest) and UPPER_CONSTRAINTS (default the
# requirements master branch) override the defaults; extra arguments to build
# are passed to docker build, for example --build-arg PROJECTS=...

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image="${IMAGE:-import-memory:latest}"
uc_url="${UPPER_CONSTRAINTS:-https://opendev.org/openstack/requirements/raw/branch/master/upper-constraints.txt}"

run() {
    docker run --rm -v "${here}:/probe:ro" "${image}" "$@"
}

case "${1:-}" in
    build)
        shift
        context="$(mktemp -d)"
        trap 'rm -rf "${context}"' EXIT
        cp "${here}/Dockerfile" "${context}/"
        curl -sSf -o "${context}/upper-constraints.txt" "${uc_url}"
        docker build -t "${image}" "$@" "${context}"
        ;;
    probe)
        roles="${2:-roles.txt}"
        # Each role gets a fresh interpreter so nothing is attributed to the
        # wrong role. Deprecation warnings go to stderr and are dropped.
        run sh -c 'grep -v "^#" "/probe/$1" | grep -v "^$" | while read -r role mods; do
                       python -I /probe/probe.py "$role" $mods
                   done' sh "${roles}" 2>/dev/null
        ;;
    marginal)
        [ $# -eq 3 ] || { echo "usage: $0 marginal BASE[,BASE...] TARGET[,TARGET...]" >&2; exit 1; }
        run python -I /probe/marginal.py "$2" "$3" 2>/dev/null
        ;;
    shell)
        docker run --rm -it -v "${here}:/probe:ro" -w /usr/local/lib/python3.12/site-packages "${image}" bash
        ;;
    *)
        sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
