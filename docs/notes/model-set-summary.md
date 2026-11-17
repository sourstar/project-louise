# The models the operator wants on the node — summary (2026-11-17)

*The operator, 2026-11-17: today's models, Qwen3.6-35B-A3B as the chat
model in place of qwen3-8b, Krea 2 image generation; "STT stays on igpu
since 500 MB are anyway reserved for igpu and they would be wasted
otherwise"; VAD on the CPU. "Make a summary with all the models I listed,
ask München for data already measured if available." München was asked
(the request is at the end); the rows marked E are ESTIMATES from file
sizes and the notes' measured totals, to be replaced by München's
figures. Sizes in GiB; the notes' `size_vram` figures (decimal GB from
LocalAI's byte counts) are converted. The card: 31.9 GiB (32,656 MiB);
the reclaimer's line 0.95 ≈ 30.3; host RAM 63.4 GB, peak 41.6 used with
everything resident (11-07).*

## 1. The models

| model | role | runs on | held on the card (weights + context) | measured? | speed, if measured |
|---|---|---|---|---|---|
| **Qwen3.6-35B-A3B UD-Q4_K_M** (`louise-chat-qwen3.6-35b-a3b`; the same file as `louise-sum-…`) | chat; translation candidate | B70 | file 20.6 (22,134,528,992 B) + ≈ 1.2 context at 16k (KV ≈ 0.3: 10 full-attention layers of 40, 2 KV heads × 256; logits and compute ≈ 0.8) + 0.26 floor ≈ **22.1** | file M; load **E** — asked of München | not measured. Qwen3-30B-A3B Q4_K_M on this card: 87 tok/s, 5.1 s per ~400-token passage, 19.2 GB held (classical-translation.md) |
| qwen3-8b (live chat, to be replaced) | chat | B70 | 6.8 (7.3 GB held, 633 MiB of it context) | M, 11-09 | — |
| MADLAD-400 3B (translator; stays unless the 35B translates: chrF 52.2 vs 35.8) | translation | B70 | 7.5 (8.0 GB held, 2,007 MiB context); a P3 test copy 6.29 GB | M | 16.3 ms/token; 3.03 s per translation; cold 9.5–11 s |
| vega-qwen3-embedding-4b (gutenberg) | encoder | B70 | 4.2 (4,263 MiB, 1,882 of it context at ubatch 2048) | M, 11-12 | — |
| vega-nemotron-3-embed-1b (legal, wikidata) | encoder | B70 | 4.1 (4.4 GB, 2,221 MiB context) | M, 11-09 | — |
| dar-legal-it (torch XPU) | encoder | B70 | 1.7 (1.8 GB, 632 MiB context) | M | — |
| bge-m3 | encoder | B70 | 1.2 (1.3 GB, 232 MiB context) | M | — |
| medcpt, article + query (OpenVINO) | encoder | B70 | 0.75 (0.8 GB, 276 MiB context) | M | — |
| bge reranker + medcpt reranker | rerank | B70 | 1.4 (1.5 GB, 213 MiB context) | M | — |
| omnivoice | TTS | B70 | 2.0 (2.2 GB, 489 MiB context) | M; time per clip asked | — |
| parakeet (`parakeet-cpp`) | STT | **iGPU** (Radeon 610M, Vulkan) — 0 on the B70 | iGPU: VRAM 516 MB (the whole carve-out) + GTT 1.32 GB | M, 11-11 (stt-on-igpu.md) | RTF 0.161 (CPU 0.090), 0.40 cores, transcripts identical |
| silero | VAD | CPU | 0 | M | — |
| **Krea 2 Turbo** — DiT GGUF + Qwen3-VL-4B text encoder + Wan 2.1 VAE (image-generation-krea.md, amended) | image generation | B70 | DiT Q4_K_M 6.7 / Q8_0 12.7; text encoder Q4_K_M 2.3 / Q8_0 4.0 or on the CPU; VAE 0.24; + 0.3 floor → **7.3 / 9.6 / 17.2**; + ~1 while an image is made, freed between images | files M; load **E**; not installed (flux.1-krea-dev-ggml 12.7 GB on disk, never loaded) | not measured. Supported by the stable-diffusion.cpp LocalAI v4.12.1 pins (0518b343); no gallery entry — a hand-written YAML |

Today's non-chat weights (encoders, MADLAD, TTS, rerankers, floors) are
13.6 GiB by the at-rest reading (11-07) and 14.8 by the mid-question one
(11-09); their contexts 7.8 GiB; the context that works in the same
second peaks at 3.2 GiB (shared-context.md §2).

## 2. What the set weighs (idle-context-brief.md §6, the table)

| set | contexts held | shared context 3.2 | weights only |
|---|---|---|---|
| today's set with qwen3-8b (MEASURED) | 28.2 | ≈ 23 | 19.8 |
| the 35B UD-Q4_K_M in its place | ≈ 43.6 | **≈ 38** | ≈ 34.5 |
| … UD-Q3_K_XL (15.7 GiB file) instead | ≈ 38.7 | ≈ 33 | ≈ 29.6 |
| 35B Q4, MADLAD dropped (the 35B translates) | — | ≈ 32.5 | ≈ 29.3 |
| 35B Q3, MADLAD dropped | — | **≈ 27.6** | ≈ 24.4 |
| + Krea 2 Turbo, Q4 DiT, encoder on the CPU / both Q4 / both Q8 | + 7.3 / 9.6 / 17.2 | the same | the same |

Nothing of the set is resident on 31.9 GiB as it stands. The one
resident fit is the 35B at Q3 with MADLAD gone (≈ 27.6, 2.7 under the
reclaimer's line); Krea 2 is resident in no row — a load per use (LocalAI,
10 s and more) or the driver's spill (~0.3 s for a Q4 DiT, ~0.8–0.9 s per
35B switch over the PCIe 5.0 x16 link and DDR5-6400; estimated,
idle-context-brief.md §6 "What the spill costs").

## 3. Asked of München (2026-11-17, from this session)

Per-model `size_vram` at rest and largest as app/resident.py keeps them;
whether the 35B was ever loaded on the card and at what size, load time,
tok/s; omnivoice's size and time per clip; parakeet's deployment on the
iGPU today; the stablediffusion-ggml backend's presence, variant and
stable-diffusion.cpp commit, and any Krea 2 files on disk; free space on
ssd3 and ssd5 and the host RAM headroom; any measured record of the
driver's spill speed; the link state and RAM channel count if already
read. Answers replace the E rows above, each with its date and source.
