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
            # `src` is handled differently. My original idea here was to generate
            # a tarball on the fly and then use the hash of that, but that doesn't
            # work because tarballs include file modification times and the sort
            # order of their entries is interdeterminate. While there are flags to
            # address those, I could not get it to work reliably. Instead, we
            # are going to hash the contents of every python file...
	    #
	    # Additionally, I exclude kolla-ansible here because its code does not
	    # change the content of images.
            echo "Using src hashing for ${dir}..." >&2
            hash=$(find . -type f -name "*.py" -path "./kolla-ansible" -prune -exec cat {} \; | sort | sha1sum - | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp')
            unique="${unique};${hash}"
        elif [ $(echo "_build etc tools" | grep -c "${dir}" || true) -gt 0 ]; then
            # These directories are like `src`, but has a simpler structure and
            # no python files
            echo "Using config hashing for ${dir}..." >&2
            hash=$(find . -type f -exec cat {} \; | sort | sha1sum - | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp')
            unique="${unique};${hash}"
        else
            # And the others are assumed to be directories of patches
            echo "Using patch hashing for ${dir}..." >&2
            if [ -e PREPATCH ]; then
                for patch in $(cat PREPATCH); do
                    hash=$(sha1sum ${patch} | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp')
                    unique="${unique};${hash}"
                done
            fi

            if [ -e ORDER ]; then
                for patch in $(grep -v -E "^#" ORDER); do
                    hash=$(sha1sum ${patch} | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp')
                    unique="${unique};${hash}"
                done
            fi
        fi

        echo "Hash of directory ${dir} is ${hash}." >&2
        cd ${topdir}
    fi
done

echo "v15-${unique}" | sha1sum | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp'
