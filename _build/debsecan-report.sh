#!/bin/bash -e

# Run from the top directory.
. _build/common.sh

banner "Security vulnerability scan with debsecan"

##############################################################################
# Build a scanner image from the same base distro                            #
##############################################################################

# The scanner image uses the same base OS as the container images being
# scanned. This ensures debsecan has the correct vulnerability data source
# for the distro (Debian uses tracker.debian.org, Ubuntu uses its own).
echo -e "${H2}Building debsecan scanner image${Color_Off}"
echo -e "${H3}Base: ${distro}:${distro_version}${Color_Off}"

scanner_image="debsecan-scanner-${distro}-${distro_version}"
if ! docker build -q -t ${scanner_image} -f - /dev/null <<DOCKERFILE
FROM ${distro}:${distro_version}
RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        --no-install-recommends debsecan ca-certificates && \
    rm -rf /var/lib/apt/lists/*
DOCKERFILE
then
    echo -e "${Red}Failed to build scanner image.${Color_Off}"
    echo -e "${Red}debsecan scanning requires network access to install packages.${Color_Off}"
    echo -e "${Red}Skipping vulnerability scan.${Color_Off}"
    trap - EXIT
    exit 0
fi

##############################################################################
# Scan each built image                                                      #
##############################################################################

debsecan_dir="${topdir}/archive/debsecan"
mkdir -p "${debsecan_dir}"

# Accumulate per-image results for the summary
declare -a scanned_images
declare -a scanned_cve_counts
declare -a scanned_fixable_counts

for target in ${build_targets}; do
    complete_image_tag="${target}-${distro}-${distro_version}-${image_tag}"

    images=$(docker image list --format json | \
        jq --slurp -r \
        ".[] | select(.Tag == \"${complete_image_tag}\") | .Repository")

    if [ -z "${images}" ]; then
        echo -e "${H3}No local images found for tag ${complete_image_tag}, skipping${Color_Off}"
        continue
    fi

    for image in ${images}; do
        safe_name=$(echo ${image} | tr '/' '-')
        echo
        echo -e "${H2}Scanning ${image}:${complete_image_tag}${Color_Off}"

        # Create a temporary container to extract the dpkg database and
        # os-release. The container is never started -- we just need the
        # filesystem.
        workdir=$(mktemp -d)

        cid=$(docker create "${image}:${complete_image_tag}" true)

        if ! docker cp "${cid}:/var/lib/dpkg/status" "${workdir}/status" \
            2>/dev/null
        then
            echo -e "${H3}No dpkg database found -- not Debian-derived, skipping${Color_Off}"
            docker rm "${cid}" > /dev/null
            rm -rf "${workdir}"
            continue
        fi

        if ! docker cp "${cid}:/etc/os-release" "${workdir}/os-release" \
            2>/dev/null
        then
            echo -e "${H3}No os-release found, skipping${Color_Off}"
            docker rm "${cid}" > /dev/null
            rm -rf "${workdir}"
            continue
        fi

        docker rm "${cid}" > /dev/null

        # Determine the suite and verify this is Debian-derived
        suite=$(grep "^VERSION_CODENAME=" "${workdir}/os-release" \
            | cut -d= -f2)
        distro_id=$(grep "^ID=" "${workdir}/os-release" | cut -d= -f2)
        id_like=$(grep "^ID_LIKE=" "${workdir}/os-release" \
            | cut -d= -f2 || true)

        if [ -z "${suite}" ]; then
            echo -e "${H3}Could not determine suite, skipping${Color_Off}"
            rm -rf "${workdir}"
            continue
        fi

        if [ "${distro_id}" != "debian" ] \
            && [ "${distro_id}" != "ubuntu" ] \
            && [[ "${id_like}" != *"debian"* ]]
        then
            echo -e "${H3}Not Debian-derived (ID=${distro_id}), skipping${Color_Off}"
            rm -rf "${workdir}"
            continue
        fi

        echo -e "${H3}Distro: ${distro_id}, Suite: ${suite}${Color_Off}"

        # Full detail report (human-readable)
        docker run --rm \
            -v "${workdir}/status:/scan/status:ro" \
            ${scanner_image} \
            debsecan --suite "${suite}" --status /scan/status \
                --format detail \
            > "${debsecan_dir}/${safe_name}-detail.txt" 2>&1 || true

        # Simple one-line-per-CVE report (for counting / parsing)
        docker run --rm \
            -v "${workdir}/status:/scan/status:ro" \
            ${scanner_image} \
            debsecan --suite "${suite}" --status /scan/status \
                --format simple \
            > "${debsecan_dir}/${safe_name}-simple.txt" 2>&1 || true

        # Fixable CVEs only (packages where a fix is available in the suite)
        docker run --rm \
            -v "${workdir}/status:/scan/status:ro" \
            ${scanner_image} \
            debsecan --suite "${suite}" --status /scan/status \
                --only-fixed --format simple \
            > "${debsecan_dir}/${safe_name}-fixable.txt" 2>&1 || true

        image_cves=$(wc -l < "${debsecan_dir}/${safe_name}-simple.txt")
        image_fixable=$(wc -l < "${debsecan_dir}/${safe_name}-fixable.txt")

        echo -e "${H3}Total CVEs: ${image_cves}${Color_Off}"
        echo -e "${H3}Fixable CVEs: ${image_fixable}${Color_Off}"

        scanned_images+=("${image}:${complete_image_tag}")
        scanned_cve_counts+=(${image_cves})
        scanned_fixable_counts+=(${image_fixable})

        rm -rf "${workdir}"
    done
done

##############################################################################
# Generate summary report                                                    #
##############################################################################

total_images=${#scanned_images[@]}
total_cves=0
total_fixable=0

for i in $(seq 0 $((total_images - 1))); do
    total_cves=$((total_cves + scanned_cve_counts[i]))
    total_fixable=$((total_fixable + scanned_fixable_counts[i]))
done

{
    echo "debsecan Vulnerability Report"
    echo "============================="
    echo ""
    echo "Generated: $(date -Iseconds)"
    echo "Base distro: ${distro} ${distro_version}"
    echo "Images scanned: ${total_images}"
    echo "Total CVEs (all images): ${total_cves}"
    echo "Fixable CVEs (all images): ${total_fixable}"
    echo ""

    for i in $(seq 0 $((total_images - 1))); do
        image="${scanned_images[i]}"
        safe_name=$(echo ${image%%:*} | tr '/' '-')

        echo "--- ${image} ---"
        echo "Total: ${scanned_cve_counts[i]}, Fixable: ${scanned_fixable_counts[i]}"

        fixable_file="${debsecan_dir}/${safe_name}-fixable.txt"
        if [ -s "${fixable_file}" ]; then
            echo ""
            echo "Fixable CVEs:"
            cat "${fixable_file}"
        fi
        echo ""
    done
} > "${debsecan_dir}/summary.txt"

# Also generate a JSON summary for machine consumption
{
    echo "{"
    echo "  \"generated\": \"$(date -Iseconds)\","
    echo "  \"distro\": \"${distro}\","
    echo "  \"distro_version\": \"${distro_version}\","
    echo "  \"total_images\": ${total_images},"
    echo "  \"total_cves\": ${total_cves},"
    echo "  \"total_fixable\": ${total_fixable},"
    echo "  \"images\": ["

    for i in $(seq 0 $((total_images - 1))); do
        image="${scanned_images[i]}"
        comma=""
        if [ $i -lt $((total_images - 1)) ]; then
            comma=","
        fi
        echo "    {"
        echo "      \"image\": \"${image}\","
        echo "      \"total_cves\": ${scanned_cve_counts[i]},"
        echo "      \"fixable_cves\": ${scanned_fixable_counts[i]}"
        echo "    }${comma}"
    done

    echo "  ]"
    echo "}"
} > "${debsecan_dir}/summary.json"

##############################################################################
# Report results                                                             #
##############################################################################

echo
echo -e "${H2}debsecan Summary${Color_Off}"
echo -e "${H3}Images scanned: ${total_images}${Color_Off}"
echo -e "${H3}Total CVEs: ${total_cves}${Color_Off}"
echo -e "${H3}Fixable CVEs: ${total_fixable}${Color_Off}"
echo -e "${H3}Reports: ${debsecan_dir}/${Color_Off}"

# Clean up the scanner image
docker rmi ${scanner_image} > /dev/null 2>&1 || true

if [ "${debsecan_fail_on_fixable}" == "true" ] \
    && [ ${total_fixable} -gt 0 ]
then
    echo
    echo -e "${Red}BUILD FAILURE: ${total_fixable} fixable CVEs found.${Color_Off}"
    echo -e "${Red}Run with --skip-debsecan to bypass, or update packages.${Color_Off}"
    exit 1
fi

trap - EXIT

banner "Security vulnerability scan complete."
