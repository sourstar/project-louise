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

### Phase 0 — baseline (live engine), München 2026-11-21 (M)

Conditions: the node paused throughout (the operator's own pause, 09:46Z);
the web container stopped for each timed window (10:56–11:36Z and
11:37–11:38Z are the valid ones; 10:22–10:28Z void for everything but chat
— the chat runs there hit llama.cpp's prompt cache, 0.04 s first tokens;
10:32–10:55Z void: ~10.8 GB of the web preload sat on the card);
`louise-ocr-larex` paused in each window; the card emptied through
`/backend/shutdown` before every model, the driver reading 0 MiB. Live
engine `louise-sycl-f16-llama-cpp-281e1ee481b6-stack-7c301a7ba437`. Path
(A) — the engine's own `llama-server` — cannot run on either engine: the
backend images carry no `llama-server` (live: `llama-cpp-grpc`,
`llama-cpp-fallback`, `llama-cpp-rpc-server`; new: `llama-cpp-grpc` only),
so every encoder row is path (B): Louise's tagged client through LocalAI
and VeGa, the 24 `bench.json` texts in production-shaped batches, 3 warm
passes, median. Files: `/root/measure/qual-2026-11-21/phase0/`.

| model | result | VRAM after (driver) | load |
|---|---|---|---|
| vega-nemotron-3-embed-1b (NeuRIS recipe) | 1.33 s per pass, 18.05 chunks/s, 16,216 tok/s | 2,516 MiB | ~7.8 s |
| vega-qwen3-embedding-4b (gutenberg) | 4.37 s, 5.49 chunks/s, 4,930 tok/s | 2,736 MiB | ~8.1 s |
| vega-bge-m3 (wikipedia) | 1.24 s, 19.39 chunks/s, 17,423 tok/s | 898 MiB | ~7.9 s |
| vega-medcpt-query (pubmed, one at a time) | 0.36 s, 66.7 queries/s (ctx 512 truncates the bench texts: tok/s not meaningful) | 483 MiB | ~4.9 s |
| louise-rerank-nemotron-1b (rerank_bench, jurifindit-speed, 175 queries, concurrency 1) | 2,512 ms median per list, p90 4,478–4,498 ms, cold and warm alike | 3,278 MiB | — |
| louise-translate-madlad (t5waves; pilot T3 18/18) | **T4 one at a time 375/379 identical to P3's one slot (FAIL)**, 345/379 identical to torch, 0.42 s median per translation; **T3 624/626 identical (FAIL)**; in-wave == alone 610/626 (PASS); 1.06 s per question, p90 1.44 s | 7,221 MiB | — |
| chat 9B, `louise-chat-qwen3.5-9b-q4-p0` (2 fixed prompts × 5 answers, n_predict 256, seed 0, a unique first line against the cache; client-side stream timing — LocalAI v5 returns no `timings`) | 140 tok: first token 0.121 s, prefill 1,157 tok/s, decode 77.5 tok/s · 2,061 tok: 0.624 s, 3,301 tok/s, 75.5 tok/s | 6,288 MiB | — |
| the 35B, `louise-chat-qwen3.6-35b-a3b` (the same) | 140 tok: 0.292 s, **480 tok/s, 99.6 tok/s** · 2,061 tok: 2.512 s, **820 tok/s, 97.3 tok/s** | 21,782 MiB | — |
| omnivoice-cpp (1 warm-up + 3 clips) | 0.79 s, 1.12 s, 1.27 s | 1,369 MiB | — |

The prompts (`phase0/prompts/`): a real Normattiva Ask context, 128 and
2,048 tokens by the 9B's tokenizer (140 and 2,061 with the run line);
the 128-token answers read without garbage (both models answer that the
passages do not cover the question — correctly).

**Finding F1 — MADLAD on the live engine is no longer 379/379.** The
engine was qualified at 379/379 one-at-a-time and 616/626 in-wave; today
375/379 and 624/626 (in-wave == alone 610/626 holds). **Classified
(München, 12:10Z):** none of the 4 + 2 is garbage or a truncation; three
of the four one-at-a-time differences now match torch — the reference
implementation — exactly, so the live engine moved TOWARDS the reference
and P3's recorded bytes away from it; one of the four changes a year in
parentheses (1930 → 1936), a difference of meaning whichever side is
right — the torch reference decides it; the two in-wave differences are a
single token each. And **the live engine never had its own MADLAD
qualification run**: the 11-11 engine switch checked 8 answers, so the
drop from 379/379 happened at or after that switch and went unmeasured
until today — a process finding beside the engine one. Phase 2's bar for
P3/P4 on the new engine is set against TODAY's figures, every newly
differing translation classified the same way.

**Finding F2 — LocalAI's `auto_upgrade_backends` was true** in
`runtime_settings.json` since 11-08, overriding
`LOCALAI_AUTO_UPGRADE_BACKENDS=false`; v5.0.1 acted on it today and
upgraded three non-Louise backends (`intel-sycl-f16-llama-cpp-development`
08:01 CET, `intel-sycl-f16-ced` 12:03, `intel-transformers` 12:06), with
~12 more listed (vulkan-parakeet-cpp, intel-sycl-f32-omnivoice-cpp, …);
none of the `louise-*` / `vega-*` engines touched. München set it back to
false through `POST /api/settings` (no restart, backup kept). A backend
that changes under a qualified run is an engine change nobody gated.

### Phase 1 — the engine, München 2026-11-21 (M)

| | |
|---|---|
| built from | commit cb4edac2 (094df03 + the protocol), `INTEL_STACK=intel-stack-2026-11c.conf sh deploy/localai/llama-cpp/build.sh sycl_f16` — the LIVE engine's stack, so that only the series changes; stack digest 7c301a7ba437 (= live) |
| base + series | LocalAI `dd5780aa904d` + 6 patches; series tag `133ef68669a1`; 14 min; exit 0 |
| image | `louise-llama-cpp:sycl_f16-133ef68669a1-stack-7c301a7ba437`, sha256 `7c97ad698bed422bac43e8cf4ec4758360b5c8aed3c74ecf0074f8619e151c93`; tar sha256 `c911477dcb4da8cc…`; stack.lock kept |
| installed as | `louise-sycl-f16-llama-cpp-133ef68669a1-stack-7c301a7ba437`, install-external from the dot-named tar, 12:02:04Z; the live backend untouched |
| first load | test copy `qual-nemotron-embed-1b-raw.yaml` (the live nemotron YAML on the new backend, no VeGa, no collection): dim 2048 answered |
| flags | the engine's stderr is not in LocalAI's log at INFO; the binary carries `GGML_SYCL_BMG_VRAM_CHECK`, `GGML_SYCL_MMID_SCHED`, `GGML_SYCL_ENABLE_DNN`; the running engine's environ: `GGML_SYCL_ENABLE_DNN=0`, `GGML_SYCL_FA_ONEDNN=0`, `NEO_CACHE_DIR`; BMG_VRAM_CHECK and MMID_SCHED unset = compiled defaults (1, 1) |
| VeGa wrapper package | not yet; built for phase 3's `vega-<model>-q` names (`build_vega_package.sh`) |
| test binaries | the image ships no `test-backend-ops`, `test-chat`, `embedding_tokens_test`: built once from the same tree in the same builder and stack for phase 2 (§2 phase 2, as amended in the work order) |

### Phase 2 — the series' checks, München 2026-11-21/22 (M)

Test binaries built once from the engine's own tree (LocalAI `dd5780aa` +
the 6 patches by `git am`, llama.cpp `78e05298` with `prepare.sh`'s
patches) in the engine's builder `louise-base-grpc-intel:7c301a7ba437`, a
throwaway container never installed; the KQ-shapes diag patch applied to
the test only (fuzz 2, offset 962); `test-chat` needed
`LLAMA_BUILD_TOOLS=ON` (mtmd.h), `embedding_tokens_test` needed
`CMAKE_PREFIX_PATH=/opt/grpc` and libjpeg-dev. Card SYCL0,
`GGML_SYCL_ENABLE_DNN=0`, `GGML_SYCL_FA_ONEDNN=0`, BMG_VRAM_CHECK and
MMID_SCHED at their defaults. Files: `phase2/` (`build*.log`, `byop.txt`,
`op-<OP>.log`, `card-holders.txt`).

| check | result |
|---|---|
| P5: `test-backend-ops -o MUL_MAT -p type_a=f16,type_b=f32` | **235/235, 0 FAIL** |
| `GGML_TEST_KQ_SHAPES=1 -o MUL_MAT` | **272/272** |
| P6: `test-chat` (CPU) | All tests passed |
| P2: `embedding_tokens_test` (CPU) | exit 0 |
| the full suite, op by op (119 ops; one run aborted at SET_ROWS f16→f8_e4m3, exit 139) | 5 ops abort (139) ON f8_e4m3 after every earlier case passed (ADD 149 OK, CPY 109, FLASH_ATTN_EXT 4,277, MUL_MAT 159, MUL_MAT_ID 150): SYCL aborts on fp8 instead of skipping — no model of ours uses it. Numeric FAILs, none on our paths: SIGMOID/HARDSIGMOID bf16 (ERR 1.0–3.7e-7 against 1e-7), GROUP_NORM ne=[3,1,1,65537] (4.0), PAD_REFLECT_1D 16,777,217 (2.0), ROLL with a permute (1.9), TIMESTEP_EMBEDDING dim=1 (1.0) — edge shapes and diffusion ops. "0/0" for five `-o` names that match no case. Condition: 4,024 MiB of the live engine stayed on the card (correctness only) |
| (e) the VRAM check, on purpose: the live set resident (12 models, 25,078 MiB, GTT 2,366 MiB), a test copy of the 35B on the new engine | **HTTP 500 in 3.0 s**: `ggml_sycl_free_mem_enough: SYCL0: cannot allocate 18543 MiB: 0 MiB already allocated, 4607 MiB usable (4607 MiB free of 32656 MiB), set GGML_SYCL_BMG_VRAM_CHECK=0 to disable this check; [SYCL-MEMTRACE] alloc FAILED`; LocalAI: "Backend process exited unexpectedly", "Failed to load model"; **GTT 2,366 MiB before and after — no spill; nothing killed; the 12 models still loaded** |

**(f) MADLAD P3/P4 on the new engine** (t5waves as phase 0; pilot 18/18;
card emptied, web stopped, OCR paused; run 2026-11-22 00:1x–00:39Z, the
first attempt interrupted by the operator's reboot):

| | live engine (phase 0) | new engine | bar |
|---|---|---|---|
| T4 one at a time vs P3's bytes | 375/379 | 375/379 | ≥ 375 MET |
| T4 vs torch | 345/379 | 345/379 | — |
| T3 same group twice | 624/626 | 625/626 | ≥ 624 MET |
| in-wave == alone | 610/626 | 612/626 | ≥ 610 MET |
| median per translation (T4) | 0.42 s | **1.01 s** | — |
| median per question (T3) | 1.06 s, p90 1.44 | **2.19 s, p90 3.05** | — |

**Finding F3 — MADLAD is 2.4× slower on the new engine, bytes no worse.**
As it stands this fails the adoption bar. First diagnostic ordered: the
same run with `GGML_SYCL_BMG_VRAM_CHECK=0` — the 500 above shows the check
(`ggml_sycl_free_mem_enough`, a Level Zero memory query) inside the
allocation path, and MADLAD's waves allocate per request; if the speed
returns, F3 is the check's placement (upstream bug, env in MADLAD's YAML
until moved). Then the 9B chat on the new engine (engine-wide or MADLAD's
path alone). The new engine's differing translations: to classify after
phase 3.

**Conditions changed between phases**: the host's DDR5 runs at **6400
MT/s** since the operator's second reboot (2026-11-21 ~22:31Z, EXPO;
stressapptest 12 GB × 60 min PASS, 0 MCE/EDAC); phase 0 ran at 5200. A
control re-run of two phase-0 rows on the live engine at 6400 is ordered
before phase 3's first row.

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
