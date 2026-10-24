#!/bin/bash

# Open all of the patches in an ORDER file in vscode, handling fixing
# the paths along the way.

echo "${1}"

for patch in $(grep -v -E "^#" ${1}/ORDER | sed 's/.*_patches/_patches/')
do
    echo "Opening ${patch}"
    code ${patch}
done

echo "Done"
