# The models the operator wants on the node — summary (2026-11-17; München's figures 2026-11-18)

*The operator, 2026-11-17: today's models, Qwen3.6-35B-A3B as the chat
model, Krea 2 image generation; "STT stays on igpu since 500 MB are anyway
reserved for igpu and they would be wasted otherwise"; VAD on the CPU.
"Make a summary with all the models I listed, ask München for data already
measured if available." München answered 2026-11-18 from the node's own
records (reads only, nothing newly measured); every figure below is
theirs unless marked E (estimate). MiB as `louise node resident show`
prints them (`state/resident-budget.json`, written 2026-11-18 00:00 +01);
GiB = MiB / 1024. The card: 32,656 MiB (31.9 GiB); the reclaimer's line
0.95 = 31,023 MiB; the budget that night 10,833 MiB (line − margin 4,113
− eviction penalty 16,075, last eviction 2026-11-17 22:14). Host RAM
61 GB, 39 used, 22 available; ssd5 452 GB free, ssd3 6.4 TB.*

## 1. The models

| model | role | runs on | at rest, MiB | largest, MiB | speed, where measured |
|---|---|---|---|---|---|
| **louise-chat-qwen3.5-9b-q4-p0** — the live chat (not qwen3-8b any more) | chat | B70 | 5,367 | 8,104 | — |
| **Qwen3.6-35B-A3B UD-Q4_K_M** (`louise-chat-qwen3.6-35b-a3b`, file 22,134,528,992 B) | chat; translation candidate | B70 | **21,642** (2026-11-03, `/root/measure/q36.log`: backend `louise-sycl-f16-llama-cpp-16acc89`, ctx 16,384, flash attention on, batch 2048 / ubatch 512, parallel 1) | 23,415 at 8 streams (2026-11-08, `/root/measure/batch35.txt`, 64 summary chunks, engine 11c; 21,633 at 1, 21,920 at 2, 22,473 at 4) | load + first token 28.6 s; prefill 606.5 tok/s on 1,913 prompt tokens; decode 92.2 tok/s alone; in the batch run 58 tok/s out at 1 stream, 68 at 8 (median latency 17.8 s). `louise-sum-…`: no record |
| louise-translate-madlad | translation | B70 | 4,850 | 8,005 | 16.3 ms/token; 3.03 s per translation; cold 9.5–11 s (STATUS) |
| omnivoice-cpp (omnivoice-base-Q8_0 656,395,008 B + tokenizer-Q8_0 288,889,600 B) | TTS | B70 | 1,442 | 2,676 | 0.7–4.7× the clip's length, typically 1.5–2× (17–37 s for 3–35 s of audio, 2 requests in flight; speech-latency.md) |
| louise-rerank-nemotron-1b (the bge reranker is out of the set) | reranker | B70 | 3,009 | 3,348 | — |
| louise-rerank-medcpt | reranker (pubmed) | B70 | 419 | 423 | — |
| vega-qwen3-embedding-4b | gutenberg, doc + query | B70 | 5,685 | 5,685 | — |
| vega-nemotron-3-embed-1b | legal (EUR-Lex, NeuRIS, Normattiva, legi) + wikidata, doc + query | B70 | 4,404 | 4,404 | — |
| vega-dar-legal-it | Normattiva-DAR, doc + query | B70 | 5,187 | 8,453 | — |
| vega-bge-m3 | wikipedia, doc + query | B70 | 1,335 | 1,335 | — |
| vega-medcpt-query | pubmed query | B70 | 361 | 484 | — |
| vega-medcpt-article-ov | pubmed doc | B70 | 308 | 312 | — |
| parakeet-cpp-multilingual-diarization-speakers-igpu | STT | **iGPU** (Vulkan RADV), live since 2026-11-11 | 0 | 0 | RTF 0.16 (CPU 0.09), 0.40 cores (stt-on-igpu.md, the last measurement); its cost to a running embed pass: open |
| silero | VAD | CPU | 0 | 0 | — |
| Krea 2 (§3) | image generation | B70 | never loaded; on disk a Q8_0 DiT, 12.7 GiB | — | nothing measured on the card |

**Sums.** Today's set at rest **32,367 MiB = 31.6 GiB** — the card's size,
1.3 GiB above the reclaimer's line: the driver's spill is already in use at
rest (the "≈ 34 GB resident" of the brief). Largest **43,229 MiB = 42.2 GiB**.

**Two at-rest figures read as growth, to check (München):** dar-legal-it
5,187 MiB at rest against 1.8 GB on 11-09 and a ~1.1 GB model file
(torch's caching allocator keeps what a corpus pass took?); qwen3-4b 5,685
against the 4,263 read after its ubatch cut on 11-12 (the budget file's
"rest" may predate the trim — idle-context-brief.md §6 flagged the reset).
Together ≈ 4.7 GiB of the card.

## 2. What the set weighs (GiB)

The "at rest" column adds München's measured sizes (the 35B's 21,642;
a Q3_K_XL of it E ≈ 16,600 = its 16,066 MiB file + the Q4's measured
533 MiB overhead). The "shared context" column is E: weights = rest
less the 11-12 context readings (≈ 9.6 GiB over the non-chat set, the
nemotron reranker's taken as 300 MiB), + the 3.2 GiB that works in the
same second (shared-context.md §2).

| set | at rest | shared context 3.2 |
|---|---|---|
| today's set, the 9B chat (MEASURED) | **31.6** | ≈ 25 |
| the 35B UD-Q4_K_M for the 9B | **≈ 47.5** | ≈ 41 |
| … UD-Q3_K_XL instead | ≈ 42.6 | ≈ 36 |
| 35B Q4, MADLAD gone (the 35B translates: Qwen3-30B-A3B chrF 52.2, MADLAD 35.8) | ≈ 42.8 | ≈ 38 |
| 35B Q3, MADLAD gone | ≈ 37.8 | ≈ 33.4 |
| 35B Q3, MADLAD gone, dar-legal-it and qwen3-4b back to their 11-09 / 11-12 sizes | ≈ 33.1 | ≈ 30.1 |
| + Krea 2: the Q8 DiT on disk 12.7 + text encoder Q4 2.3 (or on the CPU) + VAE 0.24 + floor | + 13.2–15.5 | the same |

Nothing fits. The 35B alone is two thirds of the card and today's set
already fills it. The nearest thing to a resident fit is the 35B at Q3
with MADLAD gone AND the two grown encoders trimmed: ≈ 30 GiB with the
shared context, at the reclaimer's line, no margin — and still no Krea 2.
Every other arrangement is a switch: the driver's spill (idle-context-brief.md
§6 "What the spill costs": ~0.8–0.9 s per 35B switch, E; **no measurement
of the spill's speed exists** — the 11-03 fdinfo snapshot in q36.log,
7.69 GiB VRAM / 862 MiB GTT for one process, has no timing) or LocalAI's
unload-and-reload (the 35B's load + first token: 28.6 s measured).

## 3. Krea 2 on the node (München's inventory, 2026-11-18)

- **Backend**: `intel-sycl-f16-stablediffusion-ggml`, digest
  `sha256:31070bb9…ccc2d1`, installed 2026-10-27 (also a
  `stablediffusion-ggml-development` of 10-16 and a partial development
  install). Its stable-diffusion.cpp commit is **not recorded** — whether
  it is the Krea2-capable pin (LocalAI v4.12.1 pins `0518b343`, which has
  it; upstream since 2026-06-25) is read from the installed backend, step 1.
- **Files in `/mnt/ssd5/apps/localai/models`**: `moodyKrea2Mix_v40-Q8_0.gguf`
  13,625,715,616 B (sha256 `b6c1076b…1152a`) — a community finetune of
  Krea 2 Turbo ("Moody Krea 2 Mix" v4.0), the same size as Turbo's Q8_0;
  its YAML (a gallery import of 2026-10-17, never used) is
  **misconfigured**: `backend: diffusers`, `known_usecases: chat`.
  `Qwen3-VL-8B-Instruct-Q4_K_M.gguf` 5,027,785,568 B is NOT Krea 2's text
  encoder — Krea 2 is trained on **Qwen3-VL 4B**'s states and takes no
  other (`Qwen/Qwen3-VL-4B-Instruct-GGUF`, Q4_K_M 2,497,281,664 B; missing).
  The **Wan 2.1 VAE** (253,815,318 B) is missing too. The FLUX.1 Krea set
  is complete (flux1-krea-dev-Q8_0 12,714,452,256 B, t5xxl_fp16,
  clip_l, ae; plus a flux2-vae.safetensors 336,211,292 B).
- So "the model is already available" is true of the DiT only: to run it,
  the backend's commit confirmed, the 4B text encoder and the Wan VAE
  fetched (≈ 2.75 GB), the YAML rewritten for `stablediffusion-ggml` with
  `llm_path` and `vae_path`, and the licence (Krea's community licence,
  `LICENSE.pdf` in the gated repos; the mix's own terms beside it) read
  and acknowledged before any byte moves (invariant 9). On the card:
  ≈ 13.2 GiB with the encoder on the CPU, ≈ 15.5 with it at Q4, + ~1 GiB
  while an image is made; a Q4_K_M DiT (6.7 GiB) would halve it.

## 4. Open

- The spill's speed: nothing measured (§2); the estimate stands alone.
- `lspci -vv -s 03:00.0 | grep LnkSta` and `dmidecode -t memory`: never
  read; the operator's word in München's session (the operator's own
  statement, 2026-11-17: PCIe 5.0 x16, DDR5-6400; channels unknown).
- The installed stablediffusion-ggml's stable-diffusion.cpp commit (§3).
- dar-legal-it's and qwen3-4b's at-rest growth (§1).
- parakeet's cost to a running embed pass (stt-on-igpu.md).
