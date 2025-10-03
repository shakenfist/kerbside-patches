#!/bin/bash -e

# Run from the top directory.
#    positional arguments are the names of the projects to testapply. If
#    none are specified, then all are tested.

. _build/common.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Will build:\n\n${@}${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

extra=""
if [ ${skip_tests} == "true" ]; then
    extra="${extra} --skip-tests"
fi
if [ ${use_ci_registry} == "true" ]; then
    extra="${extra} --use-ci-registry"
fi

for project in ${@}; do
    ./_build/apply-patches-and-test.sh ${extra} ${project}
done

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}All patches applied correctly.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"
