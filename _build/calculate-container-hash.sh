#!/bin/bash

# For a given patch target, calculate a hash that uniquely identifies the
# patchset we used and the date we build on. This is useful for avoiding
# rebuilding the same containers over and over in CI.

unique=$(date "+%Y%m%d")
topdir=$(pwd)

for dir in ${*}; do
    cd ${dir}
    if [ -e PREPATCH ]; then
        for patch in $(cat PREPATCH); do
            hash=$(sha1sum ${patch} | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp')
            unique="${unique};${hash}"
        done
    fi

    if [ -e ORDER ]; then
        for patch in $(cat ORDER); do
            hash=$(sha1sum ${patch} | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp')
            unique="${unique};${hash}"
        done
    fi
    cd ${topdir}
done

echo "v3-${unique}" | sha1sum | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp'