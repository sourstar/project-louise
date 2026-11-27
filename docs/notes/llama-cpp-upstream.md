# Our llama.cpp on upstream's latest

**The rule (the operator, 2026-11-27: "1.B always").** Every new engine starts from
upstream llama.cpp's master of the day it is cut, every one of our patches carried onto
it — not from the pin the last engine had. A fix upstream makes (a pooler, a precision, a
kernel) reaches us with the next engine instead of being written twice. LocalAI's base
stays where it is (`dd5780a`) until it is moved by its own decision: its gRPC is the one
the live server (`v5.0.1-…-0032`) speaks, and LocalAI's master now carries its own inner
patches 0002–0008, whose numbers are ours.

## 1. How a move is made (2026-11-27: 78e0529 → 8e23278c)

1. A worktree at upstream's master; each file of the series' `patches/` applied in `ls`
   order as a commit — `git apply` where it applies, `git apply --3way` where only the
   context moved, by hand where both sides changed the same lines.
2. LocalAI's `prepare.sh` run against that tree with the patches already in (a copy of
   `backend/cpp/llama-cpp` without `patches/`, `llama.cpp` a link to the tree), then
   llama-server, the gRPC server and every patch's standalone test built and run on the
   CPU. Where upstream changed an API under LocalAI's `grpc-server.cpp`, LocalAI's own fix
   is taken when it has one (do not reinvent).
3. The fixes folded into the patch each belongs to; each patch whose text changed
   regenerated under its own message; the rest kept byte for byte. The set must apply to
   upstream's tree with `patch -p1` and NO fuzz and give the built tree exactly.
4. The same inputs through the old build and the new one on the same CPU: an output byte
   that moves is named before anything goes to a card.
5. The SYCL backend compiled with icpx; then the outer patch (`LLAMA_VERSION`, the
   regenerated inner patches, the grpc-server fix) and a candidate series. Every gate
   again on the card: it is another llama.cpp.

## 2. The move of 2026-11-27 (`engine-upstream.series`, outer 0037)

299 upstream commits. Fifteen of our 24 inner patches applied as they were; nine were
carried:

| patch | what moved upstream | what was done |
|---|---|---|
| 0012 OmniVoice | the per-tensor scale arguments of `build_attn`, `build_ffn` and `build_lora_mm` folded into the tensors (#28898, `a386950`); Moshi and VoxCPM2 added in the same lists | the backbone without the scale arguments; our projector, gen-audio type and capability beside theirs |
| 0026 BERT text pair | the pooler kept for `BertForSequenceClassification` (#33122, `15fcfc7`); `build_ffn`'s arguments; Hy4's unified-KV rule beside ours | our pooler hunk dropped for theirs; the GELU key written for their class too (without it MedCPT's cross-encoder falls back to the tanh GELU); the FFN call in its new form |
| 0013, 0015, 0016, 0022, 0023 | SYCL's environment table (`GGML_SYCL_FUSE_CONV_WINDOW` added) and the docs' Level Zero row | context only |
| 0008, 0017 | new tests beside ours; an `#include` beside ours | context only |

And LocalAI's `a1937e98` for the decision API's audio flag (#32578):
`parse_questions(body, chat_params.allow_audio)`, probed by `prepare.sh`.

No environment variable our configs set was removed (removed upstream: four
`GGML_OPENVINO_NPU_*` and `LLAMA_SERVER_DEBUG_PROXY_ALLOW_PRIVATE`).

**Measured on the CPU (the development container: an Intel Xeon with AVX-512, 4 threads), old build
(`78e0529` + the series) against new:** MedCPT's article encoder, 67 PubMed pairs through
the gRPC server with `pairs:true` — the same bytes; its cross-encoder, 40 reranks with
`rerank_score:sigmoid` — the same bytes (both on the same GGUFs); OmniVoice, two clips at
seed 1234 (a designed voice and the default) — the same bytes, and "female, purple"
refused with the same reason. Every standalone test of the patches passes; the server's
parking and context-on-demand tests, 25 of 25 on an idle CPU (a first run while the icpx
compile held the cores stopped at the test's own timing guard, 15.7 s for 2.5). The SYCL
backend and libllama compile with icpx (`GGML_SYCL_F16=ON`).

**What moves on the card, from upstream:** q6_K dequantized in f32 on SYCL (#32726,
`ed2153e`): the bytes of every model with Q6_K tensors (the Q4_K_M encoders and chat)
move — each encoder's run is validated anew against its space's calibration and CPU
reference, as invariant 12 asks of any engine; BMG decode at head size 256 on the FA
vector kernel (#30606); the MoE cache's experts selected on another backend (#33133).
**The converter's GGUFs, converted again from the series alone** (`convert.sh
engine-upstream.series`, a fresh LocalAI and llama.cpp, the patches applied as
`prepare.sh` applies them — the series' own end-to-end check): the article encoder
`62d0e7b9bd70ec2155c641d5fa858d66a3a6a8bcd689b36a04254a7a4cadd7f5`, THE SAME BYTES as
from `engine-guard.series` (the new pubmed space's weights digest does not move); the
cross-encoder `4dff2f7712595ad1c69d3d62c9f1a40d56cf0375aa072b2e53708d0c2ea4ec50`, where
it was `078f8cde…`: its 201 tensors the same bytes, one key more,
`bert.classifier.activation = "tanh"` (upstream's #33122; the graph's default), and its
40 reranks through the new build the same score bytes as the old GGUF's. A reranker has
no space; its calibration names the model, not the file.
