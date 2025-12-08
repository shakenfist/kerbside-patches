#!/bin/bash

# $1 is how many merges to go back in time.
if [ -z ${1} ]; then
    rewind=1
else
    rewind=${1}
fi

echo "We will go back ${rewind} commits, with 1 being HEAD."

# Lock in new daily SHAs for each project we're patching
cwd=$(pwd)
projects=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
for project in ${projects}; do
    mkdir src
    repo=$(yq -r .repo ${project}/config.yaml)
    source_branch=$(yq -r .source_branch ${project}/config.yaml)
    previous_source_sha=$(yq -r .source_sha ${project}/config.yaml)
    directory=$(yq -r .directory ${project}/config.yaml)

    git clone --branch ${source_branch} ${repo} src/${directory}
    cd src/${directory}
    source_sha=$(git log --pretty=format:%H | head -${rewind} | tail -1 | cut -f 2 -d " ")
    cd ${cwd}
    rm -rf src

    echo
    if [ "${source_sha}" != "${previous_source_sha}" ]; then
        echo "Setting ${project} source_sha to ${source_sha}"
        yq -i -y ".source_sha = \"${source_sha}\"" ${project}/config.yaml
        yq -i -y ".previous_source_sha = \"${previous_source_sha}\"" ${project}/config.yaml
    else
	echo "SHA unchanged."
    fi

    echo
    echo
done
