#!/bin/bash
# Build libspice-server.so.1 from a spice-server ref, optionally with a
# directory of patches applied, so that the host's own qemu can be run
# against it. The library is the only thing that changes: qemu's SPICE
# support is a module (ui-spice-core.so) linked against
# libspice-server.so.1, so pointing LD_LIBRARY_PATH at the output
# directory swaps the server under an unmodified distribution qemu.
#
# Usage: build-spice.sh NAME REF [PATCH_DIR]
#
#   NAME       a short label for this build (e.g. v0.15.2, master,
#              master-trace-ring). It names the output directory and the
#              qemu wrapper.
#   REF        any ref SPICE_REPO knows: a tag, a branch or a commit.
#   PATCH_DIR  optional directory of git format-patch files, applied in
#              name order with git am. A 0000-*cover-letter* patch is
#              skipped.
#
# The source is cloned from SPICE_REPO into the output directory and
# checked out there; SPICE_REPO itself is never modified, so a local
# clone is a fine (and fast) source. The compile and spice-server's own
# meson test suite run in a debian:trixie container, never on the host.
# The container matches a Debian 13 host's glibc and GStreamer, which is
# what lets the library load in the host's qemu and find the host's
# GStreamer plugins and VA drivers.
#
# The build follows Debian's spice source package, debian/rules and
# debian/control: the same Build-Depends, the same meson setup options
# debhelper (compat 13) passes, and the same compiler flags, taken from
# dpkg-buildflags inside the container. That is -O2 with -g, so the
# library carries debug info for backtraces at the optimisation Debian
# ships; it is not stripped. Debian passes no -D feature options at all
# and relies on the defaults finding every Build-Depend; this script
# names the features those defaults resolve to (gstreamer=1.0, lz4,
# sasl, opus, smartcard) so that a missing dependency fails the build
# instead of silently producing a library without it. Debian also
# disables the test-listen test with a patch, because it fails in a
# minimal build environment. This script runs it: it needs a writable
# HOME, which the container gets, and a GIO TLS backend and system trust
# store for its test client, so the build image adds glib-networking and
# ca-certificates, which nothing in the library links. The image also
# has git, which spice-server's meson setup runs to version the build.
#
# spice-server's spice-protocol requirement is read from the ref's
# meson.build. When the container's libspice-protocol-dev is older than
# that (master needs a newer one than trixie ships), spice-protocol is
# built from SPICE_PROTOCOL_REPO at the tag v<required version> into a
# private prefix inside the output directory and used from there.
# spice-protocol is headers only, so this changes nothing qemu links.
#
# Outputs, in OUTPUT_DIR:
#   lib/libspice-server.so.1   the library (unstripped)
#   qemu-NAME.sh               execs QEMU with LD_LIBRARY_PATH=lib/
#   commit                     the full SHA that was built (after patches)
#   build-info.txt             ref, base and built SHAs, patches and
#                              their sha256, the spice-common and
#                              spice-protocol used, the image, the
#                              compiler flags and meson options, and the
#                              meson test summary
#   meson-testlog.txt          meson test's full log
#   src/, obj/                 the source and build trees, kept so a
#                              debugger can find the source: the flags
#                              map src/ to ".", so in gdb use
#                              "directory OUTPUT_DIR/src"
#
# Env overrides:
#   SPICE_REPO           spice-server repository URL or local clone
#                        (default https://gitlab.freedesktop.org/spice/spice.git)
#   SPICE_COMMON_REPO    spice-common, the spice-server submodule (default
#                        https://gitlab.freedesktop.org/spice/spice-common.git;
#                        the submodule's own URL is relative, which a local
#                        SPICE_REPO cannot resolve)
#   SPICE_PROTOCOL_REPO  spice-protocol (default
#                        https://gitlab.freedesktop.org/spice/spice-protocol.git)
#   OUTPUT_DIR           where the build goes (default ./build-NAME next
#                        to this script, which .gitignore covers). It is
#                        removed and recreated, unless it is non-empty
#                        and not an earlier build, which is refused.
#   QEMU                 the qemu binary the wrapper execs (default
#                        qemu-system-x86_64 from PATH, resolved now)
#   IMAGE                the base image (default debian:trixie)
#   RUN_TESTS            1 runs meson test (default); 0 skips it. A test
#                        failure fails the build, but the library and
#                        wrapper are still left in place to inspect.
#   JOBS                 parallel jobs for the compile (default nproc);
#                        the tests always run one at a time
#
# Requires on the host: git and docker.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)

if [ $# -lt 2 ] || [ $# -gt 3 ]; then
    echo "usage: $0 NAME REF [PATCH_DIR]" >&2
    exit 2
fi
NAME=$1
REF=$2
PATCH_DIR=${3:-}
case "${NAME}" in
    *[!A-Za-z0-9._-]*|'') echo "ERROR: NAME may contain only letters, digits, '.', '_' and '-'" >&2; exit 2 ;;
esac
if [ -n "${PATCH_DIR}" ]; then
    PATCH_DIR=$(cd "${PATCH_DIR}" && pwd)
fi

SPICE_REPO=${SPICE_REPO:-https://gitlab.freedesktop.org/spice/spice.git}
SPICE_COMMON_REPO=${SPICE_COMMON_REPO:-https://gitlab.freedesktop.org/spice/spice-common.git}
SPICE_PROTOCOL_REPO=${SPICE_PROTOCOL_REPO:-https://gitlab.freedesktop.org/spice/spice-protocol.git}
OUTPUT_DIR=$(realpath -m "${OUTPUT_DIR:-${HERE}/build-${NAME}}")
QEMU=$(command -v "${QEMU:-qemu-system-x86_64}") || { echo "ERROR: qemu binary not found" >&2; exit 1; }
IMAGE=${IMAGE:-debian:trixie}
RUN_TESTS=${RUN_TESTS:-1}
JOBS=${JOBS:-$(nproc)}
BUILD_IMAGE=kerbside-spice-build:trixie

log() { echo "[build-spice ${NAME}] $*" >&2; }

# A local SPICE_REPO is recorded, and cloned, by its absolute path.
if [ -d "${SPICE_REPO}" ]; then
    SPICE_REPO=$(cd "${SPICE_REPO}" && pwd)
fi

# OUTPUT_DIR is removed and recreated, so refuse anything that is not
# empty and not an earlier build of this script.
if [ -n "$(ls -A "${OUTPUT_DIR}" 2>/dev/null)" ] && [ ! -e "${OUTPUT_DIR}/build-info.txt" ]; then
    echo "ERROR: ${OUTPUT_DIR} is not empty and not a build-spice.sh output; refusing to remove it" >&2
    exit 1
fi
rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}/lib"
SRC=${OUTPUT_DIR}/src

# 1. Source: a fresh clone at REF, spice-common at the commit REF pins,
# then the patches.
log "cloning ${SPICE_REPO} at ${REF}"
git clone --quiet --no-checkout "${SPICE_REPO}" "${SRC}"
git -C "${SRC}" -c advice.detachedHead=false checkout --quiet "${REF}"
BASE_SHA=$(git -C "${SRC}" rev-parse HEAD)
git -C "${SRC}" submodule init --quiet
# The URL is keyed by the submodule's name, which .gitmodules maps to its path.
COMMON_KEY=$(git -C "${SRC}" config -f .gitmodules --get-regexp '^submodule\..*\.path$' |
    awk '$2 == "subprojects/spice-common" { sub(/\.path$/, ".url", $1); print $1 }')
if [ -z "${COMMON_KEY}" ]; then
    echo "ERROR: no subprojects/spice-common submodule in ${REF}" >&2
    exit 1
fi
git -C "${SRC}" config "${COMMON_KEY}" "${SPICE_COMMON_REPO}"
# A local SPICE_COMMON_REPO is a file: URL, which git refuses for
# submodules unless told otherwise; the URL here is the caller's choice.
# Recursive, because spice-common has its own submodule (recorder), and
# meson setup, which runs with no network in the container, insists on
# finding every submodule present.
git -C "${SRC}" -c protocol.file.allow=always submodule update --quiet --init --recursive
COMMON_SHA=$(git -C "${SRC}/subprojects/spice-common" rev-parse HEAD)

PATCHES=()
if [ -n "${PATCH_DIR}" ]; then
    for p in "${PATCH_DIR}"/*.patch; do
        [ -e "${p}" ] || continue
        case "$(basename "${p}")" in
            0000-*cover-letter*) continue ;;
        esac
        PATCHES+=("${p}")
    done
    if [ "${#PATCHES[@]}" -eq 0 ]; then
        echo "ERROR: no patches in ${PATCH_DIR}" >&2
        exit 1
    fi
    log "applying ${#PATCHES[@]} patch(es) from ${PATCH_DIR}"
    # The committer date follows the author date, so that the same base and
    # patches always give the same built SHA.
    git -C "${SRC}" -c user.name=build-spice -c user.email=build-spice@localhost \
        am --quiet --committer-date-is-author-date "${PATCHES[@]}"
fi
BUILT_SHA=$(git -C "${SRC}" rev-parse HEAD)

# spice-protocol: the version this ref asks for.
PROTOCOL_REQ=$(sed -n "s/^spice_protocol_version *= *'\([0-9.]*\)'.*/\1/p" "${SRC}/meson.build" | head -1)
if [ -z "${PROTOCOL_REQ}" ]; then
    echo "ERROR: could not read spice_protocol_version from ${SRC}/meson.build" >&2
    exit 1
fi

# 2. The build image: debian:trixie plus the Build-Depends of Debian's
# spice source package, dpkg-dev for dpkg-buildflags, git for the version
# meson setup derives, and glib-networking and ca-certificates for
# test-listen's TLS client.
log "preparing ${BUILD_IMAGE} from ${IMAGE}"
docker build --quiet -t "${BUILD_IMAGE}" - > /dev/null <<EOF
FROM ${IMAGE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential dpkg-dev meson ninja-build pkgconf python3 python3-pyparsing python3-six \
        libcacard-dev libglib2.0-dev libgstreamer-plugins-base1.0-dev libgstreamer1.0-dev \
        libjpeg-dev liblz4-dev libopus-dev libpixman-1-dev libsasl2-dev libspice-protocol-dev \
        libssl-dev liborc-0.4-dev git glib-networking ca-certificates && \
    rm -rf /var/lib/apt/lists/*
EOF
IMAGE_ID=$(docker image inspect --format '{{.Id}}' "${BUILD_IMAGE}")

SYSTEM_PROTOCOL=$(docker run --rm "${BUILD_IMAGE}" pkg-config --modversion spice-protocol)
PROTOCOL_SOURCE="libspice-protocol-dev ${SYSTEM_PROTOCOL} (container)"
if ! docker run --rm "${BUILD_IMAGE}" pkg-config --atleast-version="${PROTOCOL_REQ}" spice-protocol; then
    log "spice-protocol ${PROTOCOL_REQ} needed, container has ${SYSTEM_PROTOCOL}; building v${PROTOCOL_REQ}"
    git clone --quiet --depth 1 --branch "v${PROTOCOL_REQ}" "${SPICE_PROTOCOL_REPO}" \
        "${OUTPUT_DIR}/spice-protocol"
    PROTOCOL_SOURCE="spice-protocol v${PROTOCOL_REQ} $(git -C "${OUTPUT_DIR}/spice-protocol" rev-parse HEAD)"
fi

# 3. Configure, compile and test in the container, as the invoking user,
# with no network, and with the output directory mounted at the same path
# so that paths in logs and debug info are host paths.
FEATURES=(-Dgstreamer=1.0 -Dlz4=true -Dsasl=true -Dopus=enabled -Dsmartcard=enabled)
log "building in ${BUILD_IMAGE}"
rc=0
docker run --rm --network none -u "$(id -u):$(id -g)" -e HOME="${OUTPUT_DIR}/home" \
    -e OUT="${OUTPUT_DIR}" -e RUN_TESTS="${RUN_TESTS}" -e JOBS="${JOBS}" \
    -v "${OUTPUT_DIR}:${OUTPUT_DIR}" -w "${SRC}" "${BUILD_IMAGE}" \
    bash -euo pipefail -c '
        mkdir -p "${HOME}"
        export LC_ALL=C.UTF-8
        if [ -d "${OUT}/spice-protocol" ]; then
            meson setup --prefix="${OUT}/protocol" "${OUT}/spice-protocol/obj" "${OUT}/spice-protocol" > /dev/null
            meson install -C "${OUT}/spice-protocol/obj" > /dev/null
            export PKG_CONFIG_PATH="${OUT}/protocol/share/pkgconfig"
        fi
        # Run from the source tree, as dh does, so -ffile-prefix-map maps it to ".".
        eval "$(dpkg-buildflags --export=sh)"
        {
            echo "CFLAGS: ${CFLAGS}"
            echo "CXXFLAGS: ${CXXFLAGS}"
            echo "CPPFLAGS: ${CPPFLAGS}"
            echo "LDFLAGS: ${LDFLAGS}"
        } > "${OUT}/flags.txt"
        # The options debhelper compat 13 passes to meson setup.
        meson setup "${OUT}/obj" "${OUT}/src" \
            --wrap-mode=nodownload --buildtype=plain --prefix=/usr \
            --sysconfdir=/etc --localstatedir=/var --libdir=lib/x86_64-linux-gnu \
            -Dpython.bytecompile=-1 '"${FEATURES[*]}"'
        meson compile -C "${OUT}/obj" -j "${JOBS}"
        cp -L "${OUT}/obj/server/libspice-server.so.1" "${OUT}/lib/libspice-server.so.1"
        if [ "${RUN_TESTS}" = 1 ]; then
            # One test at a time: several tests listen on the same fixed
            # ports (test-leaks and test-smartcard both use 5912), so in
            # parallel they fail with "Address already in use".
            meson test -C "${OUT}/obj" --num-processes 1 --print-errorlogs
        fi
    ' || rc=$?

if [ -e "${OUTPUT_DIR}/obj/meson-logs/testlog.txt" ]; then
    cp "${OUTPUT_DIR}/obj/meson-logs/testlog.txt" "${OUTPUT_DIR}/meson-testlog.txt"
fi

# 4. The wrapper, the recorded SHA and the build record.
WRAPPER=${OUTPUT_DIR}/qemu-${NAME}.sh
cat > "${WRAPPER}" <<EOF
#!/bin/sh
# qemu with spice-server ${NAME} (${BUILT_SHA}) from build-spice.sh.
LD_LIBRARY_PATH="${OUTPUT_DIR}/lib\${LD_LIBRARY_PATH:+:\${LD_LIBRARY_PATH}}" exec "${QEMU}" "\$@"
EOF
chmod +x "${WRAPPER}"
echo "${BUILT_SHA}" > "${OUTPUT_DIR}/commit"

summary=''
if [ -e "${OUTPUT_DIR}/meson-testlog.txt" ]; then
    # meson test ends its log with an Ok:/Fail:/Skipped: block.
    summary=$(grep -E '^(Ok|Expected Fail|Fail|Unexpected Pass|Skipped|Timeout): ' \
        "${OUTPUT_DIR}/meson-testlog.txt" | tr -s ' ' | paste -sd ' ' -)
fi
{
    echo "name: ${NAME}"
    echo "ref: ${REF}"
    echo "repo: ${SPICE_REPO}"
    echo "base sha: ${BASE_SHA}"
    echo "built sha: ${BUILT_SHA}"
    echo "spice-common: ${COMMON_SHA}"
    echo "spice-protocol: ${PROTOCOL_SOURCE} (needs >= ${PROTOCOL_REQ})"
    if [ "${#PATCHES[@]}" -gt 0 ]; then
        echo "patches from ${PATCH_DIR}:"
        for p in "${PATCHES[@]}"; do
            echo "  $(sha256sum "${p}" | cut -d' ' -f1)  $(basename "${p}")"
        done
    fi
    echo "image: ${BUILD_IMAGE} ${IMAGE_ID} (from ${IMAGE})"
    echo "meson features: ${FEATURES[*]}"
    cat "${OUTPUT_DIR}/flags.txt" 2>/dev/null || true
    echo "qemu: ${QEMU}"
    echo "meson test: ${summary:-not run}"
    echo "build rc: ${rc}"
} > "${OUTPUT_DIR}/build-info.txt"
cat "${OUTPUT_DIR}/build-info.txt" >&2

if [ "${rc}" -ne 0 ]; then
    log "build or tests failed (rc ${rc}); see ${OUTPUT_DIR}"
    exit "${rc}"
fi
log "done: ${WRAPPER}"
