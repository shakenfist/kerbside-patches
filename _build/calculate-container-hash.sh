#!/bin/bash

# For a given patch target, calculate a hash that uniquely identifies the
# patchset we used and the date we build on. This is useful for avoiding
# rebuilding the same containers over and over in CI.

unique=$(date "+%Y%m%d")
topdir=$(pwd)

for dir in ${*}; do
    if [ ! -e ${dir} ]; then
        echo "Hash directory ${dir} does not exist." >&2
    else
        cd ${dir}
        
        if [ "${dir}" == "src" ]; then
            # `src` is handled differently
            hash=$(tar c . | sha1sum - | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp')
            unique="${unique};${hash}"
        else
            # And the others are assumed to be directories of patches
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
        fi

        echo "Hash directory ${dir} is ${hash}." >&2
        cd ${topdir}
    fi
done

echo "v12-${unique}" | sha1sum | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp'
