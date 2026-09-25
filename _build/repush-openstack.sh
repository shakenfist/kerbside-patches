#!/bin/bash -e

# Find push eligible patch streams
projects=$(find . -maxdepth 2 -type f -name "config.yaml" | cut -f 2 -d "/")
for project in ${projects}; do
    mkdir -p src
    repo=$(yq -r .repo ${project}/config.yaml)
    source_branch=$(yq -r .source_branch ${project}/config.yaml)
    destination_branch=$(yq -r .destination_branch ${project}/config.yaml)
    directory=$(yq -r .directory ${project}/config.yaml)
    repush=$(yq -r '.repush // false' ${project}/config.yaml)
    number_of_patches=$(cat ${project}/ORDER | wc -l)

    echo
    echo "Processing ${project} with repush set to ${repush}..."
    if [ "${repush}" == "true" ]; then
        rm -rf src

        echo "Reapply patch series into src/${directory}..."
        _build/test-apply.sh --skip-tests --update-patches "${project}"

        echo "Validating what landed..."
        echo
        cd "src/${directory}"

        echo "Project: ${project}"
        echo "Source branch: ${source_branch}"
        echo "Destination branch: ${destination_branch}"
        echo "Repush: ${repush}"
        echo "Expected number of patches: ${number_of_patches}"
        echo

        git status
        echo

        if [ $(git branch --show-current) != "${destination_branch}" ]; then
            echo "Incorrect destination branch!"
            exit 1
        fi
        
        git log --oneline | head -$(( ${number_of_patches} + 2 ))
        echo

        echo "Proceed?"
        read -r proceed
        if [ "${proceed}" == "y" ]; then
            git review
        fi

        cd ../..
    fi
done
