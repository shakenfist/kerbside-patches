#!/bin/bash

# Open all of the patches in an ORDER file in vscode, handling fixing
# the paths along the way. A patch's review commentary
# (_patches/<patch stem>.md) is opened beside it when there is one.
#
# Usage: tools/open-patch-series.sh [-a|--all] <series>
#
# With -a or --all, patches whose ORDER lines are commented out (held back
# from applying and repushing) are opened too, so they can be reviewed
# without editing ORDER.

usage() {
    echo "Usage: $0 [-a|--all] <series>" >&2
    exit 1
}

all=false
series=""
for arg in "$@"; do
    case "${arg}" in
        -a|--all)
            all=true
            ;;
        -*)
            echo "Unknown option ${arg}" >&2
            usage
            ;;
        *)
            series="${arg%/}"
            ;;
    esac
done

if [ -z "${series}" ] || [ ! -f "${series}/ORDER" ]; then
    usage
fi

echo "${series}"

if [ "${all}" == "true" ]; then
    # Commented-out lines too, but only those naming a patch, so that prose
    # comments in ORDER are skipped.
    patches=$(sed -n -E 's/^#?[[:space:]]*.*_patches/_patches/p' \
        "${series}/ORDER" | grep -E '\.patch$')
else
    patches=$(grep -v -E "^#" "${series}/ORDER" | sed 's/.*_patches/_patches/')
fi

for patch in ${patches}
do
    echo "Opening ${patch}"
    code "${patch}"
    commentary="${patch%.patch}.md"
    if [ -f "${commentary}" ]; then
        echo "Opening ${commentary}"
        code "${commentary}"
    fi
done
code "${series}/config.yaml"

echo "Done"
