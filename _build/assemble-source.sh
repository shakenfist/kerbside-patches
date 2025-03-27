# Run from the top directory.
target_release="${1}"

if [ -z ${target_release} ]; then
    echo "Please specify a target release."
    exit 1
fi

. _build/common.sh

echo
echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Building patched source tree for ${target_release}${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"

# Fetch kerbside
echo -e "${H2}Cloning kerbside${Color_Off}"
mkdir src
cd src
git clone https://github.com/shakenfist/kerbside
tar cf kerbside.tgz kerbside
cd ..
echo
echo

# Apply patches. We skip tests here because there are separate CI jobs to
# cover that and the tests take ages to run.
echo -e "${H2}Finding projects for release ${target_release}${Color_Off}"
declare -a directories
projects=$(find . -type f -name "config.yaml" | cut -f 2 -d "/")
for project in ${projects}; do
    echo -e "${H3}Considering ${project}${Color_Off}"
    release=$(yq -r .release ${project}/config.yaml)
    echo "${project} is release ${release}"
    if [ "${release}" == "${target_release}" ]; then
        num_patches=$(cat ${project}/ORDER | wc -l)
        if [ -e ${project}/FORCE ]; then
            num_patches=$(( ${num_patches} + 1 ))
        fi
        echo "...there are ${num_patches} queued"
        if [ ${num_patches} -lt 1 ]; then
            echo "...but there are are no active patches"
        else
            echo "...will be included."
            directories+=(${project})
        fi
    fi
done
echo
echo
echo -e "${H3}The following directories contain patches: ${directories[@]}${Color_Off}"
echo

export skip_tests="true"
for project in "${directories[@]}"; do
    apply_patches_and_test_one ${project}
done

trap - EXIT

echo -e "${H1}==================================================${Color_Off}"
echo -e "${H1}Patched source tree finalized.${Color_Off}"
echo -e "${H1}==================================================${Color_Off}"