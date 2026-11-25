// The engine's OWN ggml_sycl_get_env (ggml-sycl/common.cpp of the engine source), asked the way staged 0021 asks it:
// the two switches of paging with their new defaults, with and without a model's own environment.
//   . /opt/intel/oneapi/setvars.sh
//   icpx -fsycl -O1 tools/measure/sycl_env_default_check.cpp \
//     <build>/ggml/src/ggml-sycl/CMakeFiles/ggml-sycl.dir/common.cpp.o -o check -Wl,--unresolved-symbols=ignore-all
// (the object is linked alone: only this one function of it is called). Run here 2026-11-25, oneAPI 2025.3.3:
//   unset                                   GGML_SYCL_BMG_VRAM_CHECK: 0  GGML_SYCL_HOST_PINNED_MEM_2G: 1
//   GGML_SYCL_BMG_VRAM_CHECK=1              1  1      a model's file brings the check back
//   GGML_SYCL_HOST_PINNED_MEM_2G=0          0  0      a model's file keeps large buffers
//   both set                                1  0
//   GGML_SYCL_BMG_VRAM_CHECK=yes            0  1      a value that is no number: the default
#include <cstdio>
#include <cstdlib>
int ggml_sycl_get_env(const char * env_name, int default_val);
int main() {
    const int check = ggml_sycl_get_env("GGML_SYCL_BMG_VRAM_CHECK", 0);
    const int two_g = ggml_sycl_get_env("GGML_SYCL_HOST_PINNED_MEM_2G", 1);
    printf("GGML_SYCL_BMG_VRAM_CHECK: %d  GGML_SYCL_HOST_PINNED_MEM_2G: %d\n", check, two_g);
    return 0;
}
