# ggml-org/llama.cpp (SYCL) — embeddings are not reproducible while oneDNN runs the matmuls

Measured 2026-08-27 on an Intel Arc Pro B70, image
`ghcr.io/ggml-org/llama.cpp:server-intel-b10630`, Qwen3-Embedding-8B-Q4_K_M,
`--embeddings --pooling last`.

**Root cause:**
- With `GGML_SYCL_ENABLE_DNN=1` (the default), matmuls go to oneDNN's
  `jit:gemm:any`. For some prompt lengths, its accumulation order varies
  between launches.
- oneDNN's deterministic mode is off by default and can only be set through
  its API (`dnnl_primitive_attr_set_deterministic`); llama.cpp never sets it.
- `GGML_SYCL_ENABLE_DNN=0` makes every vector bit-identical, at 1.7 % less
  throughput.

**Example:** the same text, 10 requests, serial, cache evicted between them:

| tokens | 9 | 65 | 257 | 512 | 513 | 1024+ |
|---|---|---|---|---|---|---|
| distinct vectors | 1 | 10 | 10 | 1 | 10 | 1 |

The drift is small (worst cosine 0.9999974), but it survives L2 + f16
storage, so stored embeddings cannot be verified by hash.

**Suggested fix:** a flag (or the default for `--embeddings`) that sets
oneDNN's deterministic attribute on the matmul primitives. And mention in
`docs/backend/SYCL.md` what `GGML_SYCL_ENABLE_DNN` costs in
reproducibility.


---

# ggml-org/llama.cpp (SYCL) — the batched F16 mul_mat reads a non-contiguous src1 with contiguous strides when oneDNN is compiled in but not used

*Ready to file 2026-10-28 as a PULL REQUEST (the fix is
`deploy/localai/patches/0005-llama-cpp-sycl-batched-mul-mat-src1.patch`'s
embedded `0005-sycl-batched-mul-mat-src1-strides.patch`: it applies to
`c0fd786` and, by München's reading, to master `e486a4b`), label `SYCL`.
Checked against the tracker the same day: no issue, no PR. Nearest: #27771
(bf16 at `bs=[1,3]` with a permuted operand on an Arc 140T, closed stale).*

**Bug.** `ggml_sycl_mul_mat_batched_sycl` (ggml/src/ggml-sycl/ggml-sycl.cpp;
F16 src0 × F32 src1 → F32: the KQ product over an F16 K cache, the KQV
product over an F16 V cache) converts src1 to F16 before its GEMM. In a
build with oneDNN (`GGML_SYCL_DNNL`) the conversion is a FLAT copy of src1's
whole extent ("oneDNN handles strided data and does not need overhead of
ggml_get_to_fp16_nc_sycl") — right for the oneDNN GEMM, which is then given
src1's own strides (`str_b0/1/2` from `nb1x`). But the code then sets
`s11 = ne10; s12 = ne11 * s11; s13 = ne12 * s12; is_src1_cont_2 = true`
unconditionally, and when the oneMKL GEMM runs instead of oneDNN's —
`GGML_SYCL_ENABLE_DNN=0`, or a device without an optimized oneDNN GEMM
(`ggml_sycl_dnnl_has_optimized_gemm`) — its `gemm_batch` calls (the strided
one with `s11`/`smb`, the pointer-array one with `nb12_scaled = s12 *
sizeof(half)`) read the flat copy as if it were contiguous. Every
non-contiguous src1 is read wrong: the permuted queries (`[0, 2, 1, 3]`) of a
decoder step with two or more tokens — for one token the permuted layout
happens to be the contiguous one — and any strided view with `ne13 > 1`.
Since bdca38376 (`sycl: Hotfix for non dnnl codepath`, #14677, 2025-07-14)
for a oneDNN build with the DNN switch off (`GGML_SYCL_DISABLE_DNN=1`, now
`GGML_SYCL_ENABLE_DNN=0`); the `has_optimized_gemm` runtime gate (#28985)
sends devices without one down the same path with the default DNN=1.

**What a user sees.** A T5 (MADLAD-400-3B-MT f16, 16 heads of 128) under
`llama-server -np 7` with `GGML_SYCL_ENABLE_DNN=0` on an Intel Arc Pro B70:
one sequence alone decodes right (the N = 1 kernel `mul_mat_vec_p021`); two
or more sequences decoding in one batch give wrong text — the first tokens
right, then divergence, deterministic. `--cache-type-k f32` alone fixes it
(the F32 K view takes the generic path); `--cache-type-v f32` alone does not
(the V product's src1, the softmax output, is contiguous). Unchanged by
`GGML_SYCL_ENABLE_FUSION/GRAPH/OPT=0`, `GGML_SYCL_DYNAMIC_PRECISION=F32`, and
by the GPU runtime (compute-runtime 26.27 and 26.35, IGC 2.38 and 2.41, Level
Zero loader 1.27 and 1.32: identical failures).

**Reproducer: the suite itself.**

```
GGML_SYCL_ENABLE_DNN=0 ./test-backend-ops -b SYCL0 -o MUL_MAT -p "type_a=f16,type_b=f32"
→ 191 OK, 32 FAIL, 60 not supported            (c0fd786, SYCL F16 build, oneDNN compiled in)
```

The 32: every `k_v`-strided case with `ne13 = 3` — `m∈{128,129}, n=1,
k∈{1056,1057}, k_v=2k, bs∈{[1,3],[2,3],[4,3],[8,3]}, nr∈{[1,1],[4,1]},
per=[0,1,2,3]` (e.g. `m=128,n=1,k=1056,bs=[1,3],nr=[1,1],per=[0,1,2,3],k_v=2112`:
NMSE 1.38) — and none with `bs=[x,1]`, which take `mul_mat_vec_nc`, not the
batched path. With `GGML_SYCL_ENABLE_DNN=1` the same binary passes them all — 223 OK /
0 FAIL / 60 not supported, only that variable changed (the oneDNN path reads
the strides). The suite's permuted
F16×F32 cases with n ≥ 2 all have `ne3 > 1` and are "not supported" by
`supports_op`, so the decoder's own shape was never covered: the PR adds
`test_mul_mat(F16, F32, 64, n, 128, {16, 1}, {1, 1}, {0, 2, 1, 3})` for
n = 2, 4, 7, which fails before the fix at every n ≥ 2 and every key count
(238 of 272 shapes in an exhaustive sweep) and passes at n = 1.

**Fix (the PR).** Decide the GEMM once, before the conversion
(`use_dnnl_gemm = g_ggml_sycl_enable_dnn && ggml_sycl_dnnl_has_optimized_gemm(...)`):
the flat copy only when the oneDNN GEMM will run, leaving `s11/s12/s13` and
`is_src1_cont_2` as they are; otherwise the gather (`to_fp16_nc_sycl`) with
the contiguous strides, as the non-oneDNN build has always done. 26 lines in
ggml-sycl.cpp, 8 in tests/test-backend-ops.cpp. Verified on the B70 (c0fd786 + the
patch, SYCL F16, oneAPI 2025.3.0, `GGML_SYCL_ENABLE_DNN=0`): `-o MUL_MAT -p
"type_a=f16,type_b=f32"` 226 OK / 0 FAIL / 60 not supported (191 / 32 / 60
before; the three new cases included), the exhaustive decoder sweep 272 / 0
(34 / 238 before), the whole `-o MUL_MAT` suite 2276 OK / 0 FAIL / 592 not
supported.

---

# A device-memory refusal at the pool's commit aborts the process (2026-11-26)

*For the filer: localai-bugs.md B18. Upstream has the abort in
ggml-org/llama.cpp #27595 (open, "bug-unconfirmed"), where it is read as a
usage problem ("the typical out of memory in running"); nobody there asks
that it cost the request instead of the process, and we have that fix:
`deploy/localai/patches/staged/0013-llama-cpp-sycl-pool-refusal.patch`
completed by `0018-llama-cpp-sycl-pool-refusal-reaches-the-graph.patch`
(the inner patches are against llama.cpp), live in our engine since
2026-11-25. File as a new issue that names #27595, or as the PR. What lies
between the two markers is the issue; its first line is the title.*

<!-- issue:B18 -->
SYCL: an out-of-device-memory at the VMM pool's commit aborts the process; it should fail the graph

**Version.** llama.cpp at `78e0529`, SYCL backend (`GGML_SYCL_F16`), oneAPI
2025.3, Intel Arc Pro B70.

**What happens.** While a graph runs, `ggml_sycl_pool_vmm::alloc` commits
device memory for the ops' temporaries. The commit is wrapped in
`SYCL_CHECK(CHECK_TRY_ERROR(...))`, so a refusal by the driver ends the
process:

```
level_zero backend failed with error: 39 (UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)
SYCL error: CHECK_TRY_ERROR(phys.emplace(dev, ctx, reserve_size)): Exception caught in this line of code.
  in function alloc at ggml-sycl.cpp
```

(#27595 shows the same abort at the pool's `phys->map(...)`.) The model's
own buffers fail softly — a device allocation that returns null fails the
load or the context. The pool's commit is the one path where being short of
memory for a moment costs the whole process: the weights, every slot, the
request in flight. On a card that several processes share, another model's
load is enough to trigger it.

**Expected.** The graph fails (`GGML_STATUS_ALLOC_FAILED`), which llama.cpp
already turns into `-2` from decode/encode and its server into an error for
that request; the process and the model stay, and the next request is
served.

**Fix we run.** The pool throws a dedicated type before it has changed
anything; `ggml_backend_sycl_graph_compute` catches it, waits for the queue
and returns `GGML_STATUS_ALLOC_FAILED`. Two things a first attempt got
wrong, for whoever writes the upstream change:

- the type must be neither a `sycl::exception` nor a `std::exception`: every
  op is itself called under `SYCL_CHECK(CHECK_TRY_ERROR(op(...)))`, whose
  catch aborts, so an exception of either kind never reaches the graph;
- what an abandoned op leaves behind is safe as it stands: its temporaries
  are the pool's and are released in reverse order as the stack unwinds, and
  the one thing ops change for good (the layout of quantized weights) takes
  its scratch memory from the device allocator, not the pool.

**Verified** on the B70 with a test switch that refuses the pool's next N
commits (a card cannot be made to refuse by filling it: the driver pages a
neighbour out first). Before: the first request that grew the pool ended the
process — the client saw "error reading from server: EOF" — and so did each
reload. After: the request fails with "Compute error", the process stays,
and the same model answers the next request. A patch is available.
<!-- /issue -->

