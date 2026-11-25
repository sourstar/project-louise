// The pool's refusal through the SYCL backend's OWN macros (ggml-sycl/common.hpp of the engine source):
// SYCL_CHECK(CHECK_TRY_ERROR(op(...))) is how ggml_sycl_op_mul_mat calls the op that asks the pool for memory.
//   sycl_refusal_macro_check old   the refusal as staged 0013 threw it (a std::runtime_error): the macro takes it and
//                                  the process is aborted, with the lines the card showed on 2026-11-25
//   sycl_refusal_macro_check new   the refusal as staged 0018 throws it: it passes the macro and reaches a catch
//                                  outside the op
// Built with the engine's compiler and its defines, linked to libggml-base; no device is opened:
//   . /opt/intel/oneapi/setvars.sh
//   icpx -O1 -std=gnu++17 -fsycl -DMKL_ILP64 -DGGML_BACKEND_SHARED -DGGML_SHARED -DGGML_SYCL_DNNL=1 -DGGML_SYCL_F16 \
//     -DGGML_SYCL_GRAPH -DGGML_SYCL_HOST_MEM_FALLBACK -DGGML_SYCL_SUPPORT_XMX -DGGML_SYCL_WARP_SIZE=16 -D_GNU_SOURCE \
//     -I<llama.cpp>/ggml/src/ggml-sycl -I<llama.cpp>/ggml/src -I<llama.cpp>/ggml/include \
//     tools/measure/sycl_refusal_macro_check.cpp -o check -L<build>/bin -lggml-base -Wl,-rpath,<build>/bin
// Run here 2026-11-25 (oneAPI 2025.3.3, the candidate source of engine-refusal.series): old exits 134, new exits 0.
#include "common.hpp"

#include <cstdio>
#include <cstring>
#include <stdexcept>

struct refusal_old : public std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct refusal_new {
    const char * what;
};

static void op_old() { throw refusal_old("the SYCL pool could not commit device memory"); }
static void op_new() { throw refusal_new{ "the SYCL pool could not commit device memory" }; }

// as ggml_sycl_op_mul_mat: a function-try-block that ends the process on a sycl::exception, the op under the macros
static void mul_mat_like(bool old) try {
    if (old) {
        SYCL_CHECK(CHECK_TRY_ERROR(op_old()));
    } else {
        SYCL_CHECK(CHECK_TRY_ERROR(op_new()));
    }
} catch (sycl::exception const & exc) {
    std::cerr << exc.what() << "Exception caught at file:" << __FILE__ << ", line:" << __LINE__ << std::endl;
    std::exit(1);
}

int main(int argc, char ** argv) {
    const bool old = argc > 1 && strcmp(argv[1], "old") == 0;
    try {
        mul_mat_like(old);
    } catch (const refusal_old & e) {
        printf("reached the graph's catch: %s\n", e.what());
        return 0;
    } catch (const refusal_new & e) {
        printf("reached the graph's catch: %s\n", e.what);
        return 0;
    }
    printf("no refusal\n");
    return 3;
}
