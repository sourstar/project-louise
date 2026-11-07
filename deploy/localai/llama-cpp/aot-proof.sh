#!/bin/bash
# Run by build.sh inside the BUILDER image after an AOT build (SYCL_DEVICE_ARCH): which ocloc the SYCL driver
# really execs for the device code, and which libigc that ocloc really loads. oneAPI's image carries other copies
# (VTune's GTPin bundles its own ocloc and libigc under /opt), so the proof takes the path the build takes:
# setvars sourced, a one-kernel SYCL program compiled with -fsycl-targets=spir64_gen for the arch, under
# strace (execve) and LD_DEBUG=libs. Prints two lines, "aot_ocloc <path> -> <resolved> (<package>)" and
# "aot_igc <path> -> <resolved>"; exits non-zero when the compile fails or either cannot be read.
#   aot-proof.sh <arch>
set -eu
ARCH=${1:?arch}
command -v strace >/dev/null || { apt-get update -qq >/dev/null && apt-get install -y -qq strace >/dev/null; }
set +u; source /opt/intel/oneapi/setvars.sh --force >/dev/null 2>&1; set -u
cd "$(mktemp -d)"
cat > k.cpp <<'K'
#include <sycl/sycl.hpp>
int main() { sycl::queue q; int *a = sycl::malloc_shared<int>(4, q);
             q.parallel_for(4, [=](sycl::id<1> i) { a[i] += 1; }).wait(); return 0; }
K
LD_DEBUG=libs strace -f -e trace=execve -o exec.txt \
  icpx -fsycl -fsycl-targets=spir64_gen -Xsycl-target-backend=spir64_gen "-device $ARCH" k.cpp -o k 2> ld.txt
ocloc=$(grep -oE 'execve\("[^"]*ocloc[^"]*"' exec.txt | head -1 | sed 's/execve("//; s/"$//')
igc=$(grep -oE 'calling init: [^ ]*libigc\.so[^ ]*' ld.txt | head -1 | sed 's/calling init: //')
[ -n "$ocloc" ] && [ -n "$igc" ]
real=$(readlink -f "$ocloc")
echo "aot_ocloc $ocloc -> $real ($(dpkg -S "$real" 2>/dev/null | cut -d: -f1))"
echo "aot_igc $igc -> $(readlink -f "$igc")"
