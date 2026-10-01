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
            # order of their entries is interdeterminate. Instead, we hash one
            # line per source tree: the directory name and the git tree object
            # id of its HEAD. A tree id covers every file's content and mode,
            # and none of the commit metadata, so it is deterministic and
            # reflects patched content and upstream source_sha bumps alike.
            #
            # Every tree under `src` is included, kolla-ansible and
            # ansible-collection-kolla among them, because the images contain
            # Kolla-Ansible (the deployer image) and a change to it must change
            # the image tag. The trees are git repositories both locally and in
            # CI, as assemble-source.sh tars each clone with its .git directory.
            # A directory which is not a git repository cannot be hashed this
            # way, so it is skipped with a warning rather than hashed wrongly.
            echo "Using src hashing for ${dir}..." >&2
            tree_list=""
            for tree in $(find . -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort); do
                if tree_id=$(git -C "${tree}" rev-parse 'HEAD^{tree}' 2>/dev/null); then
                    tree_list="${tree_list}${tree} ${tree_id}"$'\n'
                else
                    echo "WARNING: src/${tree} is not a git repository, skipping it." >&2
                fi
            done
            if [ -z "${tree_list}" ]; then
                # Hashing nothing would give every build the same tag, which is
                # the bug this replaced. Fail, and let the caller refuse to build.
                echo "ERROR: no git trees found under src, refusing to hash." >&2
                exit 1
            fi
            hash=$(printf '%s' "${tree_list}" | sha1sum - | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp')
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

echo "v23-${unique}" | sha1sum | cut -f 1 -d " " | sed -rn 's/^(........).*/\1/gp'
