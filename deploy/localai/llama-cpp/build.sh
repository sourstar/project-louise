#!/bin/sh
# Builds LocalAI's llama.cpp backend WITH OUR PATCHES (deploy/localai/patches:
# the series' base plus `git am` of each, in order) as the LocalAI backend
# image upstream publishes — the same Dockerfile and build arguments as its CI
# matrix (.github/backend-matrix.yml at the base) — and saves it as a tar that
# LocalAI installs from (Backends → From URL, `ocifile://<tar>`).
#
#   sh deploy/localai/llama-cpp/build.sh <variant> <out dir>
#
# <variant>: `cpu`       — ubuntu:24.04, llama-cpp-cpu-all
#            `sycl_f16`  — intel/oneapi-basekit, the node's Arc (upstream's
#                          intel-sycl-f16-llama-cpp)
#            `hipblas`   — rocm/dev-ubuntu-24.04:7.2.1, an AMD card (upstream's
#                          rocm-llama-cpp); AMDGPU_TARGETS=gfx1200 narrows it
#                          to the RX 9060 XT
# Out: <out dir>/louise-llama-cpp-<variant>-<series digest>.tar, and the image
# tagged louise-llama-cpp:<variant>-<series digest>.
#
# SERIES_FILE=<path> builds from another series file: a CANDIDATE engine on a
# newer base, beside the live one. Its lines are read as the series' are;
# patch names resolve in deploy/localai/patches; the series and its tag are
# unchanged when it is unset. (2026-11-02: the candidate on LocalAI master
# 16acc89, llama.cpp ddf48f8, passed its checks and became the series, the
# operator's A: one llama.cpp engine for every model.)
#
# SYCL_DEVICE_ARCH=<arch> (sycl_f16 only) compiles the device code ahead of time for that card
# (-DGGML_SYCL_DEVICE_ARCH, e.g. bmg-g31 for the B70) instead of shipping SPIR-V the driver compiles at first use.
# INTEL_STACK=<file> (sycl_f16 only) builds on a pinned Intel stack (intel-stack-*.conf): LocalAI's own
# Dockerfile.base-grpc-builder on the file's ONEAPI_IMAGE, then its debs, each checked against its sha256, on top
# (Dockerfile.intel-stack). Either one makes ANOTHER engine: its tag says so (-aot-<arch>, -stack-<digest>), and
# <out dir>/<tar>.stack.lock records the arch, the image and every deb's sha256. Unset, the build is unchanged.
#
# Needs git, docker (BuildKit) and the network; nothing from Louise's image.
# The engine it builds is a NEW engine (another tree than upstream's): each
# model on it is a new run, gated once (docs/03 §3.3), measured for bitwise
# reproducibility first (CLAUDE.md invariant 12).
set -eu
VARIANT=${1:?variant: cpu | sycl_f16 | hipblas}
OUT=${2:?out dir}
HERE=$(cd "$(dirname "$0")" && pwd)
PATCHES=$HERE/../patches
SERIES_FILE=${SERIES_FILE:-$PATCHES/series}
BASE=$(sed -n 's/^base //p' "$SERIES_FILE")
SERIES=$(grep -v '^#' "$SERIES_FILE" | grep -v '^base ' | grep .)

case $VARIANT in
  cpu)      BUILD_TYPE=; BASE_IMAGE=ubuntu:24.04
            BUILDER=quay.io/go-skynet/ci-cache:base-grpc-amd64 ;;
  sycl_f16) BUILD_TYPE=sycl_f16; BASE_IMAGE=intel/oneapi-basekit:2025.3.0-0-devel-ubuntu24.04
            BUILDER=quay.io/go-skynet/ci-cache:base-grpc-intel-amd64 ;;
  hipblas)  BUILD_TYPE=hipblas; BASE_IMAGE=rocm/dev-ubuntu-24.04:7.2.1
            BUILDER=quay.io/go-skynet/ci-cache:base-grpc-rocm-amd64 ;;
  *) echo "unknown variant: $VARIANT" >&2; exit 2 ;;
esac
if [ -n "${SYCL_DEVICE_ARCH:-}${INTEL_STACK:-}" ] && [ "$VARIANT" != sycl_f16 ]; then
  echo "SYCL_DEVICE_ARCH and INTEL_STACK are for the sycl_f16 variant" >&2; exit 2
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git -C "$WORK" init -q
git -C "$WORK" -c core.autocrlf=false fetch -q --depth 1 https://github.com/mudler/LocalAI.git "$BASE"
git -C "$WORK" -c core.autocrlf=false checkout -q FETCH_HEAD
for p in $SERIES; do
  git -C "$WORK" -c user.name=build -c user.email=build@localhost -c core.autocrlf=false \
    am -q "$PATCHES/$p"
done
# the series' own digest names the build: the same patches, the same tag
HEAD=$(cd "$PATCHES" && cat "$SERIES_FILE" $SERIES | sha256sum | cut -c1-12)
NAME=$VARIANT-$HEAD
CMAKE_ARGS=
LOCK=
if [ -n "${SYCL_DEVICE_ARCH:-}" ]; then
  CMAKE_ARGS="-DGGML_SYCL_DEVICE_ARCH=$SYCL_DEVICE_ARCH"
  NAME=$NAME-aot-$SYCL_DEVICE_ARCH
  LOCK="sycl_device_arch $SYCL_DEVICE_ARCH"
fi
if [ -n "${INTEL_STACK:-}" ]; then
  . "$INTEL_STACK"
  mkdir -p "$WORK/stack/debs"
  LOCK="$LOCK
oneapi_image $ONEAPI_IMAGE"
  echo "$DEBS" | while read -r want url; do
    [ -n "$url" ] || continue
    f="$WORK/stack/debs/$(basename "$url")"
    curl -fsSL -o "$f" "$url"
    got=$(sha256sum "$f" | cut -d' ' -f1)
    if [ "$want" != - ] && [ "$want" != "$got" ]; then
      echo "sha256 mismatch: $url ($got, the stack pins $want)" >&2; exit 3
    fi
    echo "deb $got $(basename "$url")" >> "$WORK/stack/debs.lock"
  done
  LOCK="$LOCK
$(cat "$WORK/stack/debs.lock")"
  STACK=$(printf '%s\n' "$LOCK" | sha256sum | cut -c1-12)
  NAME=$NAME-stack-$STACK
  # LocalAI's own grpc builder base, on the stack's oneAPI image, then the pinned debs on top
  DOCKER_BUILDKIT=1 docker build --progress=plain -f "$WORK/backend/Dockerfile.base-grpc-builder" \
    --build-arg BASE_IMAGE="$ONEAPI_IMAGE" --build-arg BUILD_TYPE="$BUILD_TYPE" --build-arg UBUNTU_VERSION=2404 \
    -t "louise-base-grpc-intel:$STACK-base" "$WORK"
  DOCKER_BUILDKIT=1 docker build --progress=plain -f "$HERE/Dockerfile.intel-stack" \
    --build-arg BUILDER_BASE="louise-base-grpc-intel:$STACK-base" -t "louise-base-grpc-intel:$STACK" "$WORK/stack"
  BUILDER=louise-base-grpc-intel:$STACK
fi
TAG=louise-llama-cpp:$NAME

DOCKER_BUILDKIT=1 docker build --progress=plain -f "$WORK/backend/Dockerfile.llama-cpp" \
  --build-arg BUILD_TYPE="$BUILD_TYPE" --build-arg BASE_IMAGE="$BASE_IMAGE" \
  --build-arg BUILDER_TARGET=builder-prebuilt --build-arg BUILDER_BASE_IMAGE="$BUILDER" \
  --build-arg UBUNTU_VERSION=2404 ${AMDGPU_TARGETS:+--build-arg AMDGPU_TARGETS="$AMDGPU_TARGETS"} \
  ${CMAKE_ARGS:+--build-arg CMAKE_ARGS="$CMAKE_ARGS"} \
  -t "$TAG" "$WORK"
mkdir -p "$OUT"
docker save "$TAG" -o "$OUT/louise-llama-cpp-$NAME.tar"
if [ -n "$LOCK" ]; then
  printf 'series %s\nbase %s\n%s\n' "$HEAD" "$BASE" "$LOCK" > "$OUT/louise-llama-cpp-$NAME.tar.stack.lock"
fi
echo "built $TAG from $BASE + $(echo $SERIES | wc -w) patch(es): $OUT/louise-llama-cpp-$NAME.tar"
