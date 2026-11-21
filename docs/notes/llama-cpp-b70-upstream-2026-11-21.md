# llama.cpp upstream since our pin, for the B70 (read 2026-11-21)

*Our engine: llama.cpp `ddf48f8` (2026-10-31) under LocalAI `16acc89` + P1–P7
(`deploy/localai/patches/series`). Upstream master `29b52d398` (b12600,
2026-11-20): 576 commits, 24 touching `ggml/src/ggml-sycl`. Read in the
source; nothing built or measured.*

| commit | what | for us |
|---|---|---|
| 18c5a95e2 (#30156) | device-side MoE schedule + XMX dequant-GEMM for MUL_MAT_ID (Q4_K included); upstream: ~+50 % pp512 on Qwen3-30B-A3B (B60), −42 % ppl time with `-ncmoe` | **the speed-up**: louise-chat/sum-qwen3.6-35b-a3b (UD-Q4_K_M). Env `GGML_SYCL_XMX_GATHER_TYPES`, `GGML_SYCL_MMID_SCHED`. Chat only: no embedder runs MUL_MAT_ID |
| 818ac6b07 (#31285) | `GGML_SYCL_BMG_VRAM_CHECK=1` default: on BMG a buffer beyond free VRAM is refused; before, Level Zero silently spilled it to system RAM (compute-runtime#1047) | **behaviour change**: the card holds ~34 GB of models on 32.6 GB; a load that spilled now FAILS. Interacts with LocalAI's eviction (B4, B11). Must be measured before rollout, or set 0 |
| dcbff2166 (#31873) | AOT builds pass `-ze-intel-greater-than-4GB-buffer-required` to ocloc; without it AOT gave garbage above 4 GB | our parked AOT bmg-g31 path (approved-encoders.md) lacks this flag: a reason it may give wrong output, independent of the libsycl-8 kernel-match failure |
| dde9de82c (#31536) | oneMKL FA: the mask indexed modulo heads / batches | path active only with FA on + quantized KV + batch ≥ 1024 + prompt ≥ 1024; embedders (F16 KV) not reached |
| 09338b85e (#31138) | oneDNN FA cache thread-safe, failures memoized | we run `GGML_SYCL_FA_ONEDNN=0`: not reached |
| 12e9084dc (#31465) | exact rounding in CPY to Q4_1/Q5_1/Q8_0 | moves bytes of any quantized KV cache (chat); embedders unaffected |
| 9214bdb55, f3b407ca1, f7591a6a0, a0ad695cb/f3d7725a7 | bin-bcast fast division; CLAMP on strided views; f16 SWIGLU_OAI; cached device id | small; bin-bcast is on every model's path, so a new engine for every space |
| 5e18eb021 (#31325) | dmmv cleanup | = our P7, drops from the series |
| 2c93f0b6e (#30956) | multi-B70 tensor split exchange | one B70: none |

Not upstream yet: P5's batched F16 matmul fix (no commit since the pin
touches it), P10's #24083 (still open). A bump is a new engine for every
space (invariant 12): every embedder re-gated, P2–P6 rebased.
