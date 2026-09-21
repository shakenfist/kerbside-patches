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

# Validate the Depends-On footers before doing anything expensive. This is a
# property of the patches alone, so it does not need a source tree, and a bad
# link is worth hearing about before a clone and a tox run rather than after.
# It lives here rather than in apply-patches-and-test.sh so that building
# images (assemble-source.sh) never depends on review.opendev.org being up.
if [ "${skip_depends_on_check}" == "true" ]; then
    banner "Skipping Depends-On validation."
else
    banner "Validating Depends-On footers"
    ./tools/check-depends-on.py ${@}
fi

for project in ${@}; do
    ./_build/apply-patches-and-test.sh ${extra} ${project}
done

trap - EXIT

banner "All patches applied correctly."
