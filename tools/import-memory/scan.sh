#!/bin/bash
# Find packages that OpenStack services import but do not use, and what they cost.
# See docs/import-memory-scans.md.
#
#   scan.sh build                       build the image from master
#   scan.sh probe [OPTIONS] > out.jsonl probe every role in a roles file
#   scan.sh marginal [--with DIR] BASE TARGET
#                                       anon RSS that TARGET adds on top of BASE
#   scan.sh shell                       a shell in the image, to read installed source
#
# Options for probe:
#   --with DIR      install the source tree DIR (for example src/neutron) over
#                   the image's copy before probing; repeatable
#   --roles REGEX   only probe roles whose name matches the extended regex
#   ROLES_FILE      a roles file in this directory (default roles.txt)
#
# IMAGE (default import-memory:latest) and UPPER_CONSTRAINTS (default the
# requirements master branch) override the defaults; extra arguments to build
# are passed to docker build, for example --build-arg PROJECTS=...

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image="${IMAGE:-import-memory:latest}"
uc_url="${UPPER_CONSTRAINTS:-https://opendev.org/openstack/requirements/raw/branch/master/upper-constraints.txt}"

# Directories named by --with, mounted read-only at /with/<basename>.
with_args=()

# Runs a command in the image. Each --with tree is copied before the command
# starts, because pbr writes egg-info into the tree and so a read-only mount
# cannot be installed directly. The copy belongs to another user, so git is
# told to trust it: pbr takes the list of files to install from git, and
# without it a project that names only its top-level package (oslo.utils
# does) silently loses its subpackages. PBR_VERSION saves pbr working out a
# version from history the copy may not have. A failed install stops the
# run, so unpatched figures are never reported as patched ones.
run() {
    docker run --rm -v "${here}:/probe:ro" "${with_args[@]}" "${image}" sh -c '
        git config --global --add safe.directory "*"
        for d in /with/*; do
            [ -d "$d" ] || continue
            cp -a "$d" "/tmp/$(basename "$d")"
            PBR_VERSION=0.0.0 pip install --no-deps --no-build-isolation -q --root-user-action=ignore --disable-pip-version-check "/tmp/$(basename "$d")" >&2 ||
                { echo "scan.sh: installing $d failed" >&2; exit 1; }
        done
        exec "$@"' sh "$@"
}

# Parses --with DIR options, leaving the other arguments in the array rest.
parse_with() {
    rest=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --with)
                [ $# -ge 2 ] || { echo "--with needs a directory" >&2; exit 1; }
                [ -d "$2" ] || { echo "--with: $2 is not a directory" >&2; exit 1; }
                dir="$(cd "$2" && pwd)"
                with_args+=(-v "${dir}:/with/$(basename "${dir}"):ro")
                shift 2
                ;;
            *)
                rest+=("$1")
                shift
                ;;
        esac
    done
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
        shift
        regex=.
        args=("$@")
        # --roles is parsed here, --with by parse_with.
        filtered=()
        while [ ${#args[@]} -gt 0 ]; do
            case "${args[0]}" in
                --roles)
                    [ ${#args[@]} -ge 2 ] || { echo "--roles needs a regex" >&2; exit 1; }
                    regex="${args[1]}"
                    args=("${args[@]:2}")
                    ;;
                *)
                    filtered+=("${args[0]}")
                    args=("${args[@]:1}")
                    ;;
            esac
        done
        parse_with "${filtered[@]}"
        [ ${#rest[@]} -le 1 ] || { echo "usage: $0 probe [--with DIR] [--roles REGEX] [ROLES_FILE]" >&2; exit 1; }
        roles_file="${rest[0]:-roles.txt}"
        # Each role gets a fresh interpreter so nothing is attributed to the
        # wrong role. Its deprecation warnings go to stderr and are dropped,
        # but the --with installs' errors are not. A backslash-bar in the
        # regex is accepted as alternation.
        run sh -c 'regex=$(printf "%s" "$2" | sed "s/[\\\\][|]/|/g")
                   grep -v "^#" "/probe/$1" | grep -v "^$" | while read -r role mods; do
                       printf "%s\n" "$role" | grep -Eq -- "$regex" || continue
                       python -I /probe/probe.py "$role" $mods 2>/dev/null
                   done' sh "${roles_file}" "${regex}"
        ;;
    marginal)
        shift
        parse_with "$@"
        [ ${#rest[@]} -eq 2 ] || { echo "usage: $0 marginal [--with DIR] BASE[,BASE...] TARGET[,TARGET...]" >&2; exit 1; }
        run sh -c 'python -I /probe/marginal.py "$1" "$2" 2>/dev/null' sh "${rest[0]}" "${rest[1]}"
        ;;
    shell)
        docker run --rm -it -v "${here}:/probe:ro" -w /usr/local/lib/python3.12/site-packages "${image}" bash
        ;;
    *)
        sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
