#!/bin/bash

# $1 is how many merges to go back in time.

# Lock in new daily SHAs for each project we're patching
cwd=$(pwd)
projects=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
for project in ${projects}; do
    mkdir src
    repo=$(yq -r .repo ${project}/config.yaml)
    source_branch=$(yq -r .source_branch ${project}/config.yaml)
    directory=$(yq -r .directory ${project}/config.yaml)

    git clone --branch ${source_branch} ${repo} src/${directory}
    cd src/${directory}
    source_sha=$(git log --merges | grep -E "^commit" | head -${1} | tail -1 | cut -f 2 -d " ")
    cd ${cwd}
    rm -rf src

    echo
    echo "Setting ${project} source_sha to ${source_sha}"
    yq -i -y ".source_sha = \"${source_sha}\"" ${project}/config.yaml

    echo
    echo
done