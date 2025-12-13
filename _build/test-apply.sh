#!/bin/bash -e

# Run from the top directory.
#    positional arguments are the names of the projects to testapply. If
#    none are specified, then all are tested.

. _build/common.sh

banner "Will build:\n\n${@}"

extra=""
if [ ${skip_tests} == "true" ]; then
    extra="${extra} --skip-tests"
fi
if [ -n "${test_patch}" ]; then
    extra="${extra} --test-patch ${test_patch}"
fi
if [ ${use_ci_registry} == "true" ]; then
    extra="${extra} --use-ci-registry"
fi
if [ ${update_patches} == "true" ]; then
    extra="${extra} --update-patches"
fi

for project in ${@}; do
    ./_build/apply-patches-and-test.sh ${extra} ${project}
done

trap - EXIT

banner "All patches applied correctly."
