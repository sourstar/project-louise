# Backend qualification: the series on LocalAI v5.0.1 — protocol, results, report

*The operator, 2026-11-21, verbatim: "Run unattended, you are in charge of the
backend qualification. When done I want a full report of the performance
improvement or loss, and how the 35B MoE performs. Test also the tensor layer
split as a POC with all the current models (not those used for ocr) plus the
35B replacing the chat model." This session (cloud, no reach to the node)
holds the protocol and the report; München runs the node's steps from it, one
phase at a time, and sends each phase's figures back; they are folded in
below as they arrive, marked M. What the update is expected to bring, with
upstream's figures: [llama-cpp-update-2026-11-21.md](llama-cpp-update-2026-11-21.md).
The series itself: `deploy/localai/patches/series` at commit 094df03.*

Rules that bind every step: docs/notes/measuring.md §6 (deployed flags and
env; the engine named from the wrapper's vega.json; one job on the machine —
the node paused, every other pass stopped, named in the write-up; warm
answers only, the load counted and left out; free space before and after;
numbers labelled; a pilot of 3 before every whole run), §10 (an unloaded
system; a warm, pinned model), §11 (an embedder is validated in full, a chat
model is assumed to work unless our patch runs it — P3/P4 run MADLAD, so
MADLAD is checked), CLAUDE.md invariant 12 (the gate decides, nothing is
forced) and RULE 1 (the live engine keeps serving until the new one is
qualified; nothing re-embedded). Nothing of the POC is wired to Louise: test
copies under their own names, no vector written to a collection.

## 1. The set under test

The resident set as München read it on 2026-11-18 (model-set-summary.md §1),
the OCR container's models excluded, parakeet (iGPU) and silero (CPU) off the
card: `louise-chat-qwen3.5-9b-q4-p0` (replaced by `louise-chat-qwen3.6-35b-a3b`
in the POC), `louise-translate-madlad`, `omnivoice-cpp`,
`louise-rerank-nemotron-1b`, `louise-rerank-medcpt` (OpenVINO: measured, not
on this engine), `vega-qwen3-embedding-4b`, `vega-nemotron-3-embed-1b`,
`vega-dar-legal-it` (torch: measured, not on this engine), `vega-bge-m3`,
`vega-medcpt-query`, `vega-medcpt-article-ov` (OpenVINO). On the llama.cpp
engine: the chat, MADLAD, nemotron-1b rerank, qwen3-4b, nemotron-embed,
bge-m3, medcpt-query.

## 2. The phases

Every phase writes under `/root/measure/qual-2026-11-21/<phase>/` on the
node: the command, its stdout, the driver's memory reads, `GET /system`
before and after, free space before and after. A phase's figures come back
here as a table with their source file; the note's tables below are filled
from them.

### Phase 0 — baseline on the LIVE engine (`ddf48f8` + P1–P7), the node paused

Same day, same card, same inputs as phase 3; one model at a time, loaded,
warmed with one throwaway request, pinned; the card otherwise empty.

| model | what | harness |
|---|---|---|
| each llama.cpp encoder | chunks/s and tokens/s on the harness's throughput fixtures; `size_vram` at rest and after the run; load time | `llama-intel-arc-b70` `scripts/bench.py` (its `config.env`, `BENCH_FIXTURES`), through LocalAI as deployed |
| nemotron-1b reranker | warm latency per list, pairs/s | `python -m tools.measure.rerank_bench` (the captured lists of reranker-survey-2026-11.md) |
| MADLAD | s per translation (the 7-language wave), ms/token; the 379/379 reference set kept for phase 2 | `tools/measure/t5waves.py` (`--n` the pilot first) |
| chat 9B | prefill and decode tok/s at 128 and 2048 prompt tokens, warm | `tools/measure/chat_eval.py` (`warm`, `cold (not timed)`) or the server's `timings` on fixed prompts |
| the 35B on the live engine | the same, for a same-day pair with the 11-03 figures (606.5 / 92.2) | the same |
| omnivoice | s per clip, 3 clips | speech-latency.md's method |

### Phase 1 — build and install the new engine BESIDE the live one

1. From commit 094df03: `sh deploy/localai/llama-cpp/build.sh sycl_f16 /root/out`
   (`NAME_ONLY=1` prints the tag first; record it). Then the stage package.
2. Install the tar under a NEW backend name (the rollout-p2.sh pattern:
   dot-named tar, `louise-sycl-f16-llama-cpp-<digest>`), the live backend
   untouched. Record the image digest; the engine named from vega.json.
3. Confirm in the backend's log at first load: `GGML_SYCL_BMG_VRAM_CHECK: 1`,
   `GGML_SYCL_MMID_SCHED` present, oneDNN off (`GGML_SYCL_ENABLE_DNN=0`).

### Phase 2 — the series' own checks on the new engine, before it serves

| patch | check | pass |
|---|---|---|
| P2 | `embedding_tokens_test` (built with the backend) | all pass |
| P3 / P4 | `tools/measure/t5waves.py` against the kept references: one at a time equal to P3's bytes (379/379), in-wave equal to alone (the 616/626 figure or better), 4 arrival orders the same outputs | no regression against the live engine's figures |
| P5 | `test-backend-ops -o MUL_MAT -p "type_a=f16,type_b=f32"` on the card, and the KQ shapes (`deploy/localai/patches/diag`) | 0 FAIL |
| P6 | llama.cpp's `test-chat` in the build | pass |
| the engine | `GGML_SYCL_BMG_VRAM_CHECK`: load one model past the room on purpose (a test copy), confirm the refusal is an error LocalAI reports and the worker defers (nothing written) | refused, deferred, no host spill, no kill |

### Phase 3 — per-model qualification on the new engine, one at a time

For every llama.cpp encoder and the reranker, the card otherwise empty, the
new engine under a test name (`vega-<model>-q`), its YAML the live one with
`fit:off` (094df03):
1. **The gate** (invariant 12; docs/03 §3.3): `tools/vega_repro_check.py
   --loads 3 --repeats 2 --sweep 512` on the calibration texts — verdict
   exact / near-exact / fail, and the cosine to the stored CPU reference
   (`hold embedder calibrate` path). A fail stops the phase for that model
   and is a finding, not a tuning.
2. **Throughput**, phase 0's harness and fixtures.
3. **Bytes against the live engine** on the same 16 fingerprints: identical
   or not (both engines are SYCL on the same card; a difference is
   CLASSIFIED — arithmetic order or wrong — measuring.md §6).
MADLAD: phase 2's parity is its qualification; its warm speed here.
Chat 9B: warm speed (§11: assumed to work).

### Phase 4 — the 35B MoE on the new engine

The YAML `louise-chat-qwen3.6-35b-a3b` as it is (ctx 16,384, FA on, batch
2048 / ubatch 512, parallel 1), warm, pinned, prompts of 128 and 2048 tokens,
5 answers each, median:

| configuration | footprint (driver, MiB) | prefill tok/s | decode tok/s | first token (ms) | host RAM (GB) | notes |
|---|---|---|---|---|---|---|
| live engine (phase 0) | | | | | | the 11-03 pair: 21,642 MiB, 606.5 / 92.2 |
| new engine, default (`GGML_SYCL_MMID_SCHED=1`) | | | | | | |
| new engine, `GGML_SYCL_MMID_SCHED=0` | | | | | | the scheduler's share |
| `cpu_moe` + `moe_cache_mib 4096`, share 0 | | | | | | hit rate from the log |
| `cpu_moe` + `moe_cache_mib 4096`, share 0.5 | | | | | | |
| `cpu_moe` + `moe_cache_mib 8192`, share 0 | | | | | | |
| `cpu_moe` + `moe_cache_mib 8192`, share 0.5 | | | | | | |
| `scripts/moe-cache-tune.py`'s recommendation, if it differs | | | | | | |

Also: 8 streams on the default configuration once (the 11-08 pair: 23,415
MiB, 68 tok/s out), and the answers of the 128-token prompt read by eye for
garbage (measuring.md §12: a pilot).

### Phase 5 — the POC: everything resident, the 35B as chat, layers split

The operator's "tensor layer split": one card, so the split is between the
card and host RAM — llama.cpp's own: the 35B's experts in host RAM with the
GPU cache (`cpu_moe`, `moe_cache_mib`), and, only if the set still does not
fit, `gpu_layers` below the whole on the largest dense models (MADLAD's
encoder first, then qwen3-4b), with `GGML_OP_OFFLOAD_MIN_BATCH=1` on every
encoder so that the gate's path and production's are one. Test copies, no
collection touched.

1. **Fit.** Load, in this order, each read from the driver after its load:
   nemotron-embed, qwen3-4b, bge-m3, medcpt-query, nemotron-1b rerank,
   MADLAD, omnivoice, the 35B (the cache size from phase 4's best row that
   fits the room left); then dar-legal-it and the two OpenVINO models as
   they are. Record the sum, the free memory, host RAM. If the 35B does not
   fit: reduce its cache; then MADLAD's `gpu_layers`; then qwen3-4b's. Stop
   at the first configuration in which EVERYTHING is resident and no load
   was refused. Record it as the POC configuration.
2. **Sequential use** (the operator's premise): each model's warm throughput
   in that configuration, phase 0's harness, one model working at a time —
   against its phase 3 figure alone on the card. The ratio is the split's
   price per model.
3. **One concurrent pair**, as Ask does: a corpus batch on nemotron-embed
   running while the 35B answers a 2048-token prompt; both figures.
4. **Stability**: 30 minutes of a scripted Ask loop (`tools/measure/
   query_under_load.py`'s idle run) with the set resident; `GET /system`,
   the driver and host RAM every minute; any eviction, refusal or kill.

### Phase 6 — the report (below), and the cut-over decision

The engine is ADOPTED for production when: every encoder's gate passes
(exact, or near-exact where its collection allows), MADLAD's parity holds,
P5 reads 0 FAIL, and no model lost more than 5 % throughput without a
classified reason. The cut-over itself follows rollout-p2.sh's shape (one
run per model, gated once, BACKOUT written first) and is the operator's to
start — the qualification ends at the report.

## 3. Results

*Filled from München's figures as they arrive; M = measured on the node.*

### Phase 0 — baseline (live engine)

| model | load s | at rest MiB | throughput | source |
|---|---|---|---|---|
| | | | | |

### Phase 1 — the engine

| | |
|---|---|
| series tag | |
| backend name / image digest | |
| engine (vega.json) | |
| startup flags read | |

### Phase 2 — the series' checks

| patch | result |
|---|---|
| | |

### Phase 3 — per model, new engine against live

| model | gate | bytes vs live | throughput live → new | Δ | source |
|---|---|---|---|---|---|
| | | | | | |

### Phase 4 — the 35B

(the table of §2 phase 4, filled)

### Phase 5 — the POC

| configuration that fit | sum MiB | free MiB | host RAM | refusals |
|---|---|---|---|---|
| | | | | |

| model | alone (phase 3) | resident set, sequential | ratio | concurrent pair |
|---|---|---|---|---|
| | | | | |

## 4. Report

*Written when the phases are in: the performance improvement or loss per
model, how the 35B performs (plain, with the scheduler off, under the cache),
what the POC costs, and whether the engine is adopted.*
