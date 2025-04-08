# Run from the top directory.
#    positional arguments are the names of the projects to testapply. If
#    none are specified, then all are tested.

. _build/common.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Will build:\n\n${positional_args}${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

for project in ${positional_args}; do
    ./_build/apply-patches-and-test.sh ${project}
done

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}All patches applied correctly.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"
