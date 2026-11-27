# What precision our encoders compute in (2026-11-27)

The operator, on OpenVINO's GPU default: "HALF FUCKING PRECISION AS A DEFAULT!!!" — a
defect. What follows is read from the code that runs today, and from one measurement
on a CPU; what only the node can read back is said so, with the command that reads it.

## 1. The Python encoders (louise-python)

| model | engine | what the code asks | what that gives |
|---|---|---|---|
| `vega-medcpt-article-ov` (pubmed, 4.58 M vectors) | OpenVINO 2026.3.1, GPU plugin | `compile_model(xml, "GPU", {"CACHE_DIR": …})` — no `INFERENCE_PRECISION_HINT` (deploy/medcpt-rerank/server.py `_compile`) | the GPU plugin's default inference precision, f16 (to read back on the node, §3); the IR's weights are f32 (`compress_to_fp16=False`) |
| `vega-medcpt-article-ov-cpu` | OpenVINO, CPU plugin | the same, `"CPU"` | the CPU plugin's default: **bf16** on a CPU with AVX512_BF16 or AMX — MEASURED here (an Intel Xeon with both): `INFERENCE_PRECISION_HINT` read back `bfloat16`, cosine 0.99995 to torch fp32 at worst over 12 pubmed pairs; with the f32 hint, 1 − 1.7e-12. The node's Ryzen 5 7600 (Zen 4) has AVX512_BF16: its CPU twin is very likely bf16 too |
| `louise-rerank-medcpt` | OpenVINO, GPU plugin | the same, no hint | f16 compute; AND its IR's weights are f16 in the file (deploy/medcpt-rerank/convert.py: `compress_to_fp16=True`) |
| `vega-dar-legal-it` (normattiva) | torch 2.14.0+xpu, sentence-transformers 5.1.0, transformers 4.55.4 | `SentenceTransformer(dir, device="xpu", model_kwargs={"attn_implementation": "sdpa"})` — no `torch_dtype` | transformers 4.x loads float32 by default: f32 weights. The float32 matmul precision on the card (torch's `get_float32_matmul_precision()`, oneDNN's `fp32_precision`) is torch's default, not set: read back on the node (§3) |

## 2. Our llama.cpp on the card (the `sycl_f16` build, live and candidate alike)

The live stack is built with `GGML_SYCL_F16=ON`. That build makes three paths compute in
f16. None is stated in a model's YAML or a Codex. The same holds at upstream's
`8e23278c`.

1. **Matrix products, library GEMM** (every batch of more than one token: an
   embedding, a prompt): `GGML_SYCL_DYNAMIC_PRECISION` defaults to **F16** in this
   build (common.hpp), and the activations (src1) are ROUNDED TO f16 before the GEMM
   (oneMKL/oneDNN, f16 × f16, accumulated in f32). An op asking f32 for src1
   (`ggml_prec_set_src`) is honoured; no model of ours asks.
2. **Mat-vec, one token** (`dequantize_mul_mat_vec`, for F16, BF16 and the
   Q4_0…Q6_K weights when the reordered MMVQ/XMX kernels do not take it): the
   activations rounded to f16, and the products SUMMED IN f16 (`sycl::half2 tmp`).
3. **Flash attention**: the tile kernel holds KQ in f16 and accumulates softmax·V in
   f16 (`SYCL_FAST_FP16` is defined unconditionally); the vector kernel the same in
   this build; the oneDNN SDPA path (`GGML_SYCL_FA_ONEDNN=1`, the default) takes f16
   Q, K, V. The graph asks f32 accumulation of every flash attention
   (`ggml_prec_set_acc(cur, GGML_PREC_F32)`, llama-graph.cpp); NO SYCL flash-attention
   kernel reads that request.

One switch turns paths 1 and 2 back to f32 without a rebuild:
`GGML_SYCL_DYNAMIC_PRECISION=F32` in the model's env. Path 3 has no switch: flash
attention OFF computes attention through the matrix products (path 1, then f32 if the
switch is set). Measured (MedCPT's reranker, München, 2026-11-27): the largest score
difference to the model was 0.0039 with FA off and 0.0070 with FA ON. Both on f16
activations, and the switch is not yet measured.

What is not a hidden cut: quantized weights take q8_1 activations
(MMVQ/MMQ), as on every backend; the weights' type states it. The KV cache is f16 by
llama.cpp's default (`cache_type_k/v`), a storage type the YAML does not state either.

On the CPU (the CPU reference engine, `louise-cpu-llama-cpp`): an F16 weight's product
keeps f32 activations (`vec_dot_type` F32 at our pin); flash attention converts Q to f16
for its K·Q and accumulates V in f32.

## 3. Read back on the node, and the f32 measurement

`tools/measure/encoder_precision.py` (tests/test_measure_encoder_precision.py) runs in
louise-python's own directory with its own interpreter. It does not run inside the live
process: it compiles as the live backend compiles, so it reads the same plugin's answer
for the same config. Per arm it reads back `INFERENCE_PRECISION_HINT` and
`EXECUTION_MODE_HINT` (or torch's dtype and float32 settings), embeds the collection's
sample, and compares the result with the STORED vectors and with the f32 CPU arm:

    python -m tools.cross_device_check pubmed --export /tmp/pubmed-sample.json
    D=<LocalAI's backends dir>/louise-python
    $D/python/bin/python3 -B -E -s tools/measure/encoder_precision.py --backend-dir $D \
        ov --model-dir <models>/weights/medcpt/medcpt-article-ov --sample /tmp/pubmed-sample.json --context 512
    python -m tools.cross_device_check normattiva --export /tmp/normattiva-sample.json
    LOUISE_TORCH_DETERMINISTIC=1 $D/python/bin/python3 -B -E -s tools/measure/encoder_precision.py \
        --backend-dir $D torch --model-dir <models>/weights/dar-legal-it --sample /tmp/normattiva-sample.json --context 8192

For llama.cpp, path 1 and 2's switch is measured on a twin with
`GGML_SYCL_DYNAMIC_PRECISION=F32` in its env (the rerank parity and vega_repro_check,
as for any setting: measuring.md).

So that a run SAYS its precision rather than leaving it to be read back: each backend's
load reply names it. OpenVINO's `compiled.get_property("INFERENCE_PRECISION_HINT")`;
torch's dtype; llama.cpp's own startup lines already print `GGML_SYCL_F16: yes` and
`GGML_SYCL_DYNAMIC_PRECISION: F16`, which no gate reads today. Which precision a space
admits is the architecture's rule ("precision stated and gated").
