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
before and after, free space before and after. **Every timed row carries
the node's MODE (`paused` is the only one measuring.md §10 allows for a
speed figure) and the card's other holders at its start, from the driver**
(added 2026-11-22 after F3: a reboot handed the mode back to `running` and
a loaded card was compared with a paused one). A phase's figures come back
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

**AMENDED 2026-11-24, 08:1xZ — the operator to München, relayed verbatim.**
Shown the POC's parts (S5 part 1, S5 part 2, the offloaded arm, the
driver-swap arm without the option): "Cancel the Driver-swap arm without
the option / And keep the Offloaded arm option noted just in case option 2
has bad numbers, otherwise cancel it as well"; asked whether that includes
S5 part 2's pass with the single 18.5 GiB buffer: "Yes cancel it too". So:
- **arm S's S1–S4 are CANCELLED** (the 35B resident, one buffer, left to the
  driver), and so is part 2's pass with copy A: the plain whole-buffer swap
  is never measured on this node;
- **S5 runs as the POC's swap arm**: part 1 unchanged (A B A B on an empty
  card), part 2 once, with the 2 GiB buffers only (copy B);
- **the offloaded arm (steps 1–4 above: `cpu_moe` and the cache) is a
  FALLBACK**: it runs only if part 2's numbers are bad, and is cancelled if
  they are good.

*What "bad" means for part 2 — proposed to the operator, who decides on the
figures.* Bad on ANY of:
1. an abort (an `xe` error or reset, an OOM line, MemAvailable under 4 GB)
   or a refused load;
2. the 35B's first answer after the three small models were loaded on top
   reaches its first token more than **2 s** later than warm (warm: about
   0.25 s on the 128-token prompt; expected for ~3 GiB brought back at the
   measured 16.2 GiB/s: 0.2–0.4 s);
3. its next answer is not warm again: first token more than 10 % over warm,
   or decode under 90 tok/s (resident 100.4 / 97.9; the offloaded arm's best
   84.3);
4. a small model's request after the 35B has answered takes more than 1 s
   over its own warm time;
5. the driver moves the 35B whole all the same (its host share grows by more
   than twice what the card was over): the option then buys nothing.
The 2 s is one answer's worth of what the offloaded arm costs — 16–30 % of
the decode speed, 0.6–1.3 s on a 300-token answer, on EVERY answer — with a
margin: under it, the swap is the cheaper way to keep the set loaded.

**Arm S — the driver's swap (operator, 2026-11-24: "can't a memory swap
occur as-is and the model be reloaded as-is, like if it was just idle in the
meantime?" — "Yes, add it to the POC").** The same set with the 35B RESIDENT
(no `cpu_moe`) and nothing evicted: what does not fit is left to the `xe`
driver, which moves an idle model's buffers to host RAM and back at its next
request, the process alive throughout. Never timed on this node (the
estimate: 0.4–0.9 s for a model of MADLAD's size, 1.3–2.7 s for the 35B,
from the measured 16.2 GiB/s upload). Test copies, node paused, web stopped
(Louise's room-maker would otherwise unload models), every model of the arm
pinned in LocalAI or its reclaimer held off for the window (München's
choice, recorded), and `GGML_SYCL_BMG_VRAM_CHECK=0` in the env of the
llama.cpp copies (the check refuses a load onto a full card).
- **Guards.** Start only with MemAvailable ≥ 22 GB (the set is about 43 GiB
  on a 31.9 GiB card: ~11 GiB goes to the host). ABORT, unload everything
  and record, on any of: an `xe` error, engine reset or DEVICE_LOST line in
  dmesg; an OOM line; MemAvailable under 4 GB.
- **S1, the load.** The order of the fit step, then the 35B. After each
  load: every process's VRAM and GTT (fdinfo, summed over its DRM clients),
  the card's used, MemAvailable, the ARC — who was pushed out, and how much.
- **S2, the swap, timed.** One warm-sized request to each model in turn, in
  an Ask's order (query encoder → reranker → MADLAD → the 35B → omnivoice),
  three rounds: the request's wall time against the same request's warm
  time alone (phases 3 and 4) — the difference is the swap-in — with the
  model's VRAM and GTT before and after, and which process gave the room.
  Then the 35B twice running: the second must be warm (no thrash).
- **S3, the comparison.** Fifteen minutes of the Ask loop in this
  arrangement: seconds per loop and per step, the 35B's decode tok/s, set
  beside the offloaded arm's loop.
- **S4, the cliff, once and bounded.** A nemotron batch while the 35B
  answers the 2k prompt: both figures; under a quarter of the alone figure
  is the cliff, recorded, the pair stopped.
- **S5, a partial swap (operator, 2026-11-24: "while idle, just move some GB
  to make only the necessary room").** The driver moves whole allocations,
  and the 35B's weights are ONE of 18.5 GiB (phase 2's refusal line:
  "cannot allocate 18543 MiB: 0 MiB already allocated"), so as it loads
  today it leaves the card whole or not at all. The engine has a switch that
  splits them: `GGML_SYCL_HOST_PINNED_MEM_2G=1` caps a buffer at 2 GiB
  (`ggml_backend_sycl_buffer_type_get_max_size`), and ggml then lays the
  weights over about ten buffers the driver can move one by one. Upstream
  documents it as affecting start-up time and "need more test"; its note that
  allocations above 2 GiB "serialize host-to-device copies with compute" may
  also bear on the 16.2 GiB/s upload rate measured in phase 4. Rows: the
  35B with that env alone on the card — load time, the two prompts (against
  phase 4's row 1), the number and sizes of its allocations; then S1 and S2
  again with it: how many MiB of the 35B go to the host when a small model
  needs room (expected: about what is needed, not 18.5 GiB), and what its
  next answer pays to bring them back.
  **Brought forward (operator, 2026-11-24: "Test
  GGML_SYCL_HOST_PINNED_MEM_2G"), ahead of the rest of the POC and after
  the cut-over's own checks.** Part 1, no overcommit: two test copies of
  the 35B differing only by the env, loaded A, B, A, B on an empty card —
  load to first token, the two prompts, the footprint, the allocations
  listed by `GGML_SYCL_MEMTRACE=1`, and the five short answers of B
  against A's, text for text. Part 2, the partial swap under arm S's
  guards: the 35B, then MADLAD, the reranker and nemotron-embed loaded on
  top (the card over by ~3 GiB), each process's VRAM and GTT after every
  step, the 35B's first-token time when it is asked again; once with 2 GiB
  buffers and once with the single buffer.
- Then everything unloaded, pins and settings restored, the GTT back at its
  baseline, the live set reloaded.
If the window is short, S1 and S2 come first: the measured swap time is the
one number nobody has.

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

*F3 diagnostics so far (München, phase2/f3/):* C — one request on each
engine with `GGML_SCHED_DEBUG=1` (the split list is not printed at
llama-server's log level; the server's own `print_timing` compared
instead): same text, same 19 tokens, identical output; prompt eval 63.7 vs
63.8 ms; eval 60.1 vs 53.0 ms/token — the new engine ~12 % faster per token
(absolute speeds are the debug env's, which prints on every compute);
graphs reused 0 / reallocs 0 on BOTH, so SYCL graphs are not the
difference. B — `GGML_SYCL_DEBUG=1`: the per-op trace runs; LocalAI's log
buffer keeps 1,000 lines, so a "disabling SYCL graphs" line at graph build
would be cut; none in what is kept. Read here: both servers post
`NEXT_RESPONSE` while a slot is processing, so P4's 50 ms gather is
re-entered every ms on both — the loop's wait is not the mechanism; the
BMG VRAM check runs on model-buffer allocations only — not per request.
The per-request breakdown on T4 (München, 03:31Z, **the node in mode
`running` — the worker shared the card; comparable among themselves, not
with phase 0's paused rows**; `phase2/f3/t4-*.row.json`):

| run | median wall | prompt eval ms | eval ms/token | tokens | residual wait ms | vs P3 / vs torch |
|---|---|---|---|---|---|---|
| live engine | 1.19 s | 45.3 | 43.7 | 10 | 758 | 375/379 / 345 |
| new engine, as is | 1.26 s | 39.9 | 41.3 | 16.5 | 429 | 375/379 / 345 |
| new, `LLAMA_ENC_DEC_WAVE_MS=0` | 1.11 s | 52.6 | 44.8 | 11 | 409 | 375/379 / 345 |
| new, `parallel:1` | 1.31 s | 73.2 | 62.9 | 10 | 569 | 375/379 / 345 |
| new, the pilot of 3 (card still free) | 0.50 s | 18.5 | 16.9 | 25 | 33 | 18/18 |

**Reading: F3 is most likely LOAD, not the engine.** Under the same
running node the two engines are within 6 %; the pilot on the new engine
reads the paused-card speed (0.50 s, 16.9 ms/token); the residual waits
are the worker's share of the card. **Confirmed (München): phase 2's MADLAD run on the new
engine (23:54–00:39Z) ran with the node in `running`** — resumed at
23:43Z after the memtest, nothing paused it; the web was stopped, the OCR
paused and the card emptied at the start, but the worker put its encoders
back on the card. So F3 compared a loaded card against phase 0's paused
one; the mode of each run is now in `phase2/f3/README`. **The paused rerun (München, 04:10Z, after deploy b40d19e; the node
paused by the script; the card's only holder the model under test,
69,404 KiB; web stopped, OCR paused; `f3/t4p-*`, `f3/t4p-holders.txt`):**

| run | median wall | prompt eval ms | eval ms/token | tokens | residual ms | vs P3 / vs torch |
|---|---|---|---|---|---|---|
| live engine | 0.43 s | 17.7 | 14.9 | 13.5 | 211 | 375/379 / 345 |
| new engine, as is | **0.42 s** | 17.9 | 16.6 | 24 | −13 | 375/379 / 345 |
| new, `LLAMA_ENC_DEC_WAVE_MS=0` | 0.37 s | 16.5 | 14.9 | 24 | −20 | 375/379 / 345 |
| new, `parallel:1` | 0.37 s | 15.9 | 14.7 | 16.5 | 112 | 375/379 / 345 |

**F3 CLOSED: a measurement artifact.** The new engine is −2 % against the
live one on MADLAD, paused; the loaded controls (bge-m3 2.23 s, nemotron
3.26 s under the running node) against the paused ones at 6400 (bge-m3
1.233 s vs phase 0's 1.238; nemotron 1.329 vs 1.33): **DDR5 at 6400
changes nothing on the (B) path**, and phase 0's rows stand as the
baseline. **Phase 2 is fully green.**

**The 375/379 decided:** not a fail of the new engine — identical to the
live engine's bytes, 345/379 identical to torch on both; the record it is
held to (P3's one-slot bytes) came from the 10-28 engine, and three of the
four differences moved towards torch. The standing reference for MADLAD
becomes torch's translations (345/379, no garbage, in-wave == alone), and
P3's byte record is re-cut from the adopted engine at the cut-over, dated.
**Settled (München):** torch gives 1936, as both current engines do; the
old P3 record said 1930, and the Italian query names no year — the engine
was right, the record wrong. Of the four against P3's record, three move to
torch (ja WQ2266122-no; fr WQ27912-de "en rimes" against P3's ungrammatical
"rimée"; fi 1936) and one moves away by word order with the same meaning
(en WQ2984121-fi). **None of the 379 is wrong on either engine; new == live
on all 379, byte for byte.** Written in `phase2/f3/README`. Lesson
written into the protocol: every timed row carries the node's mode and the
card's other holders at its start — a reboot can hand the mode back to
`running` unnoticed. Graph-off and check-off controls dropped.

**Conditions changed between phases**: the host's DDR5 runs at **6400
MT/s** since the operator's second reboot (2026-11-21 ~22:31Z, EXPO;
stressapptest 12 GB × 60 min PASS, 0 MCE/EDAC); phase 0 ran at 5200. A
control re-run of two phase-0 rows on the live engine at 6400 is ordered
before phase 3's first row.

### The rebuilt engine (after F4) — phases 1 and 2 again, München 2026-11-22 (M)

Files: `qual-2026-11-21/rebuild/`. Built from 41fb62a2 as phase 1
(`INTEL_STACK=intel-stack-2026-11c.conf build.sh sycl_f16`, stack
7c301a7ba437): `louise-llama-cpp:sycl_f16-c3294e050ff2-stack-7c301a7ba437`
from `dd5780aa904d` + **8 patches**, 13 min; tar sha256 `238527aad8dcadb1…`,
image sha256 `e569c9fbe285be6a4d494ecf1a06ba7cd413354cf3f3a83c05a923c4e1aafad6`;
installed beside the live engine as
`louise-sycl-f16-llama-cpp-c3294e050ff2-stack-7c301a7ba437`, the live one
untouched, `133ef68669a1` kept as history. The binary now carries P11's
strings (`llama-context-created-us`, `context_on_demand`; the fallback too)
and P10's (21 nemotron strings). `vega-louise-llama-cpp-q` repointed at it
(wrapper rev a6e20fe, the live one's; its file-digests cache dropped).

Phase 2, short form, on the tree LocalAI `dd5780aa` + 8 patches by `git am`,
llama.cpp `78e05298`; node paused, web stopped, OCR paused, card emptied
(`rebuild/holders.txt`):

| check | result |
|---|---|
| P5: `test-backend-ops -o MUL_MAT -p type_a=f16,type_b=f32` | 235/235, 0 FAIL |
| KQ shapes (`GGML_TEST_KQ_SHAPES=1`) | 272/272, 0 FAIL |
| the whole MUL_MAT group in one run | exit 139 at start — the fp8 abort seen in phase 2 on `133ef` too (SYCL aborts on f8_e4m3 instead of skipping); not ours, not new |
| P6: `test-chat` | All tests passed |
| P2: `embedding_tokens_test` | exit 0 |
| **P11: `context_on_demand_test`** | exit 0 |
| the VRAM check (a 35B test copy, ctx 1,048,576) | refused: `cannot allocate 18432 MiB: 20583 MiB already allocated, 11760 MiB usable…`; HTTP 500; 0 kernel OOM lines |
| MADLAD T4, paused, 60 | 375/379 against P3's aged record, 345/379 torch, **379/379 identical to the live engine**, median **0.42 s** (live 0.43) |

Phase 3 reruns on this engine (below, "second pass").

### Phase 3 — per model, new engine against live

**First pass, München 2026-11-22 (M), STOPPED on two engine findings** (node
paused for every row, the card holding only the model under test, web
stopped, OCR paused; `phase3/holders.txt`, `log.txt`, `log-gates.txt`):

| model (collection, window) | (1) gate, `hold embedder check --remeasure`, no `--accept` | (1b) `vega_repro_check` loads 3 × repeats 2 + sweep 512 | (2) median s, new / phase 0 | (3) 16 fingerprints, bytes vs live |
|---|---|---|---|---|
| bge-m3 (wikipedia, 1024) | **refused**: "its backend said what it did with its context for 0 of 50 calls — an engine before P11's count" | 528/528 bit-identical | 1.204 / 1.238 (−2.7 %) | 16/16 identical |
| nemotron-3-embed-1b (NeuRIS, 8191) | not run: no stored CPU reference for the document side's weights (run f68e1beb…) — a gap of the collection, not the engine; Legal's | 528/528 | 1.277 / 1.330 (−4.0 %) | 16/16 |
| qwen3-embedding-4b (gutenberg, 8191) | **refused**: P11 | no verdict: the 1 h bound at window 8191 (give it 3 h) | 4.311 / 4.374 (−1.4 %) | 16/16 |
| medcpt-query (pubmed, 512) | **PASS, exact**; CPU worst 0.999951; whole set worst 0.999649 | 528/528 | 0.347 / 0.360 (−3.6 %) | 16/16 |
| louise-rerank-nemotron-1b | **fails to load**: `done_getting_tensors: wrong number of tensors` | — | — | — |

**Finding F4 — the series file did not record what was live.** The first
v5.0.1 engine (`133ef68669a1`, from 094df03) carries neither P11 (context on
demand) nor P10 (the nemotron reranker's architecture): the repository's
`series` held P1–P7, and both were "staged" there, yet the node's live
engine `281e1ee481b6` was built from `staged/context-on-demand.series`
(P1–P7 + 0009) and the live reranker's `10aa8b1851d7` from
`staged/nemotron-rerank.series` (P1–P7 + 0008) — München's reading of the
node's build records, sha256 per patch. So the gate refused the new engine
for every `context_on_demand` model (idle-context-brief.md §10: an engine
that does not say what it did with its context is not admitted), and the
reranker's GGUF did not load. **Fixed in the repository the same day:** P10
and P11 enter the series as 0007 and 0008 on v5.0.1 (P10 as it was, offsets
only; P11's LocalAI half with one hunk re-placed by hand, its llama.cpp half
in the upstream form, which differs from the staged one in comments only),
every inner hunk exact in `prepare.sh`'s order, `git am` of the eight
verified on a fresh v5.0.1; the staged copies and their candidate `.series`
files removed; localai-bugs.md renumbered. **The engine is rebuilt from the
new commit and phase 3 is rerun in full under the same conditions.** What
stands from this pass, to be repeated on the rebuilt engine: bytes 16/16
identical to live on all four encoders, repro 528/528 on three, throughput
−1.4 to −4.0 % (faster) on the real path, medcpt-query's gate exact.

Also in `phase3/GAPS`: NeuRIS's missing CPU reference (the gate will meet it
at cut-over too; Legal's collection), qwen3-4b's repro bound (3 h at window
8191).

**Second pass — the rebuilt engine `c3294e050ff2`, München 2026-11-22,
07:25–10:27Z (M): everything that ran PASSED.** Node paused at every one of
the 18 rows (`phase3-r/holders.txt`: mode and holders per row), web
stopped, OCR paused. The only refusal is NeuRIS's gate, for want of a
CPU-reference copy — the collection's, not the engine's. P11 is counted
now and the gates admit the run.

| encoder (collection) | gate | repro, 3 loads × 2 passes + sweep 512 | throughput, median, new vs phase 0 | 16 fingerprints, bytes vs live |
|---|---|---|---|---|
| bge-m3 (wikipedia) | **PASS** | bit-identical 528/528 | 1.203 s vs 1.238 (−2.8 %) | 16/16 identical |
| nemotron-3-embed-1b (NeuRIS) | refused: no CPU-reference copy for the collection | bit-identical 528/528 | 1.28 s vs 1.33 (−3.8 %) | 16/16 identical |
| qwen3-embedding-4b (gutenberg) | **PASS** | bit-identical 528/528, in 78 min (3 h bound) | 4.285 s vs 4.374 (−2.0 %) | 16/16 identical |
| medcpt-query (pubmed) | **PASS** | bit-identical 528/528 | 0.35 s vs 0.360 (−2.8 %) | 16/16 identical |

**louise-rerank-nemotron-1b** on the rebuilt engine loads, and: parity
against torch (20 queries, `nemotron_rerank_parity.py`) **pass** —
same_top1 1.0, median and min τ 1.0, max |Δ| 0.049, the engine reproduces
itself; p50 354 ms per query, the first query 7.6 s cold. `rerank_bench`,
175 queries, concurrency 1: Q3 cold 175/175 and warm 175/175; **scores
identical to phase 0's live warm run in 175/175 lists (max |Δ| 0.0)**;
median 2,468 ms warm / 2,461 cold against 2,512 (−1.8 %); total 481.7 s
against 491.0. (The script's comparison line printed `None` — wrong field
names; München read the jsonl directly: a small tool defect to fix.)

**NeuRIS's reference gap (F5, the collection's):** the node store holds
nemotron's CPU reference for the same weights (wikidata's, carried to
legi_F), but the gate reads only per-collection copies. The operator
cancelled a one-off copy and ordered that the engine resolve a reference
per model through its API and inherit it at creation, with no
per-collection code — with the architecture session now; NeuRIS waits for
it, and will meet the same refusal at cut-over until then.

Files: `/root/measure/qual-2026-11-21/phase3-r/` (`log.txt`,
`holders.txt`, `parity.json`, `qual-r-p3-rr/`). The node restored to
`running`, the web up; phase 4 starts when Legal's CPU jobs end.

### Phase 4 — the 35B

**Pilot and first rows, München 2026-11-22 21:32–21:37Z (M)** — engine
`c3294e`, node paused, web stopped, card empty; the two fixed prompts, 5
answers median. The pilot's answers (p128, rows 1 and 3) are coherent and
without garbage: Italian, one English answer and two English words inside
Italian at temperature 0.7 — sampling, not the engine. Rows 2–8 and the 9B
wait for the next window (the pilot's go was not given in time).

| row | footprint | p128: first token / prefill / decode | p2048: first token / prefill / decode | host RAM available |
|---|---|---|---|---|
| 0 — the host's DRAM copy bandwidth, 6 processes | — | **38 GB/s** (37 / 38 / 40) — 2 × 32 GB rated 5600, configured 6400; half the dual-channel figure modelled in §3b (caveats: the docker `--cpus 6` quota, a numpy copy with first-touch faults, a single-CCD Ryzen whose fabric caps writes) | | |
| 1 — new engine, defaults | (invalid: the script read the wrong process; redone) | 0.277 s / **505** / 97.5 tok/s | 2.50 s / **824** / 96.3 | — |
| phase 0, the live engine, same prompts | 21,782 MiB | 0.292 s / 480 / 99.6 | 2.51 s / 820 / 97.3 | — |
| 3 — `cpu_moe` + cache 4096 MiB, share 0, mlock off | (invalid, redone) | 0.507 s / 276 / **68.8** | 3.00 s / 687 / **64.5** | 21,442 → 3,762 MiB when loaded (the experts mmap'd, ~17.7 GB) |

Reading so far: the device-side MoE scheduler gives the 35B **+5 % prefill
at 140 tokens and +0.5 % at 2,061**, decode −2 / −1 % — the B70 user's
end of upstream's range (+5–7 %), not the B60's +23 / +136 %. Under the
cache (4 GiB, uploads only) decode falls to **69 / 65 tok/s** (−29 / −33 %)
and prefill to 276 / 687 — the room is bought at a third of the speed; the
hit rate (logged by llama.cpp only when the cache is destroyed, at unload)
and the footprint come with the rerun. The host's 38 GB/s halves the CPU
share's ceiling against §3b's model, so the upload path should stay the
better miss path. Rows 2, 4–8 and the 9B follow.

**The rerun, complete — München 2026-11-23 21:30–21:58Z (M).** Engine
`c3294e`, the 35B's YAML as it is (ctx 16,384, FA on, batch 2048 / ubatch
512, parallel 1); node paused, web stopped, OCR paused, the card emptied
before each row; LocalAI's debug on for the window and off again. Footprint
= VRAM and GTT summed over the backend's DRM clients (a backend has two;
the pilot read one) after the 5th answer. No row skipped: every cache row
met its host-RAM floor. Files: `phase4b/` (`log.txt`, `chat-*.json`,
`ring-*.txt`, `holders.txt`, `moe-cache-tune.out`).

| row | p128: first token s / prefill / decode tok/s | p2048: first token / prefill / decode | VRAM MiB | system memory held by the driver (GTT) MiB |
|---|---|---|---|---|
| phase 0 — the LIVE engine | 0.292 / 480 / 99.6 | 2.512 / 820 / 97.3 | 21,782 | — |
| 1 — new engine, defaults | **0.274 / 510.3 / 100.4** | **2.494 / 826.3 / 97.9** | 21,782 | 618 |
| 2 — `GGML_SYCL_MMID_SCHED=0` | 0.274 / 510.4 / 100.4 | 2.497 / 825.4 / 97.9 | 21,782 | 618 |
| 3 — `cpu_moe`, cache 4096, share 0 | 0.496 / 282.2 / **69.7** | 2.955 / 697.4 / **65.3** | **7,474** | 19,280 |
| 4 — cache 4096, share 0.5 | 0.552 / 253.8 / 59.4 | 3.081 / 668.9 / 55.1 | 7,470 | 19,280 |
| 5 — cache 8192, share 0 | 0.477 / 293.4 / **84.3** | 2.890 / 713.1 / **80.6** | **11,562** | 19,280 |
| 6 — cache 8192, share 0.5 | 0.492 / 284.7 / 74.7 | 2.942 / 700.5 / 72.8 | 11,552 | 19,280 |
| 8 — defaults, 8 streams, p128, once | 1,249 tokens out in 8.84 s = **141.3 tok/s** in total | | 22,214 | 626 |
| the 9B on the new engine (dense control; phase 0: 0.121 / 1,157 / 77.5 · 0.624 / 3,301 / 75.5 · 6,288 MiB) | 0.107 / **1,305.6** / 77.7 | 0.604 / **3,413.4** / 75.7 | 6,284 | 664 |

Each cache row ran with `mlock` off and on: equal within noise (row 3 on:
0.518 / 270.5 / 69.4 · 2.995 / 688.0 / 65.0; rows 4–6 within 1 %).

Row 7, `scripts/moe-cache-tune.py` (the engine's own `llama-server`,
`--max-mib 10240`, ~9 min): measured 1,856 MiB — share 0: 42.5 t/s, hits
**64 %**; 0.5: 53.0, 68 %; 0.95: 59.6, 58 % · 5,568 MiB — share 0: 70.0,
hits **90 %**; 0.5: 67.2, 86 %; 0.95: 72.7, 84 %. Its fit of the machine:
"uploads **16.2 GiB/s**, CPU 52.7 GiB/s, CPU part 0.98 ms, rest 10.72 ms
per token, fit within 0.5 %"; 18,662 MiB of experts. Predicted, warm cache,
share 0: 896 MiB 34.9 · 1,856 44.4 · 3,712 59.5 · 5,568 70.7 · 7,424 78.5 ·
9,280 83.9 · 18,882 (every expert cached) 93.2 t/s; a CPU share helps only
below ~5.5 GiB of cache (3,712: 67.6 at share 0.8). Its last line: "all
experts fit on SYCL0: drop `-cmoe`".

What phase 4 says:
- **The new engine on the 35B, resident: prefill +6.3 % at 140 tokens and
  +0.8 % at 2,061, decode +0.8 / +0.6 %, first token −6 % / −0.7 %, the same
  21,782 MiB.** Upstream's +23 / +136 % (a B60, another model, ubatch 2048)
  does not appear here.
- **The device-side MoE scheduler does nothing for this model at these
  settings**: row 2 equals row 1 to the token. Whether it is not eligible
  (the width heuristic at ubatch 512) or simply no faster is not known; one
  row with `GGML_SYCL_MMID_SCHED=3` (the override bit) and one with
  `ubatch:2048` are added to phase 5's window.
- **Offloaded, the 35B frees 14.3 GiB of the card at a third of the decode,
  or 10.2 GiB at a sixth**: 7,474 MiB with a 4 GiB cache (69.7 / 65.3
  tok/s, −31 / −33 %), 11,562 MiB with 8 GiB (84.3 / 80.6, −16 / −18 %);
  prefill −45 % at short prompts and −14 to −16 % at 2k; first token 0.5 s
  and 2.9–3.0 s. Share 0 wins at both sizes on the real path.
- **The price is host memory, not card memory: 19.3 GiB of system memory
  held by the driver in every `cpu_moe` row, whatever the cache.** The
  experts are not in the process (RSS ~650 MB, `VmLck` 0) but in the card's
  GTT, so `mlock` pins nothing and changes nothing; that memory is not
  reclaimable while the model is loaded. It pushed the ZFS ARC from 16.3 to
  2.2 GiB once. With the node running (22 GB available on 11-18) the
  offloaded 35B leaves the host a few GB plus whatever the ARC gives back.
- **The hit rate reaches no log through LocalAI** (0 `llama_moe_cache`
  lines in the ring or in the container's log with debug on: the backend
  is killed at shutdown before its destructor prints). The tuning script
  gives it: 64 % at 1.9 GiB, 90 % at 5.6 GiB.
- **Two of my estimates were wrong, both too high**: the host's DRAM copy
  reads 38 GB/s (I modelled 75–85), and the upload path 16.2 GiB/s by the
  script's fit (I used ~45 GB/s). The spill's swap estimate is corrected
  with it in idle-context-brief.md §6: a 35B switch is ~1.3 s for the
  inbound copy alone and up to ~2.7 s with the outbound, not 1.3 s in all.
- 8 streams give 141 tok/s in total on short prompts (not comparable with
  the 11-08 summary run's 68: those were long prompts).
- **The dense control is faster too**: the 9B's prefill +12.8 % at 140
  tokens and +3.4 % at 2,061, decode +0.3 %.

### Phase 5 — the POC

**S5, the swap arm (München, 2026-11-24 08:08–08:14Z; M). Engine
`c3294e050ff2`, the node paused, web stopped; logs
`/root/measure/cutover-c3294e/{log.txt,s5/,s5p2/}`.**

*Part 1 — what the 2 GiB buffers cost with nothing else on the card.* Copy
A as the model is, copy B with `GGML_SYCL_HOST_PINNED_MEM_2G=1`, loaded A B
A B:

| | load to first token | 128-token prompt: first token / prefill / decode | 2048-token prompt | VRAM | GTT |
|---|---|---|---|---|---|
| A, second load | 8.10 s | 0.280 s / 499.8 / 100.4 tok/s | 2.500 s / 824.4 / 98.0 | 21,782 MiB | 198 MiB |
| B, second load | 8.27 s | 0.280 s / 499.3 / 100.6 | 2.499 s / 824.8 / 98.0 | 21,782 MiB | 486 MiB |

(First loads 40.8 s and 17.2 s: the page cache, not the option.) The five
short answers, text for text: B = A in both pairs, and each copy equals
itself. **The option costs nothing measurable but 288 MiB of host memory.**
Not obtained: the list of allocations (the engine's memory trace goes
through the backend's log filter: the model option `verbosity`, not
LocalAI's debug switch). The proof that the weights are split is part 2.

*Part 2 — the card overcommitted, copy B only (the operator cancelled the
single-buffer pass).* The 35B, then MADLAD, the reranker and nemotron-embed
on top, each asked in turn, two rounds; the VRAM check off on the copies,
everything pinned. VRAM / GTT in MiB per process:

| step | time | what moved |
|---|---|---|
| the 35B alone, warm | first token 0.279 s, 99.2 tok/s | 21,747 / 454 |
| MADLAD, reranker, nemotron loaded (8.1, 7.4, 5.8 s cold) | warm 0.35, 0.03, 0.02 s | at the third load the 35B gave **4,232 MiB** (17,216 / 4,685) — what was needed, not its 18.5 GiB |
| the 35B asked again | first token **2.040 s** (+1.76), decode 98.1; the next answer 0.277 s, 99.3 | the 35B back (21,147 / 755); MADLAD pushed out by 5.4 GiB (1,828 / 6,032) |
| round 2: MADLAD, reranker, nemotron | first request 0.77, 0.24, 0.34 s (+0.42, +0.21, +0.32) | each comes back, the one idle longest goes: the reranker, then nemotron, then 4.2 GiB of the 35B |
| round 2: the 35B | first token **1.523 s** (+1.25), 98.5; the next 0.279 s, 100.1 | as after round 1 |

No abort, no kernel line; MemAvailable 39.4 GB at the start, 28.6 GB at the
lowest. Against the proposed bar (phase 5, above): (a) no abort; (b) +1.76 s
and +1.25 s, under 2 s; (c) the next answer warm, 99–100 tok/s; (d) the
small models +0.2 to +0.4 s; (e) 4.2 GiB given for a card over by about
3.5 GiB. **None of the five is met: by that bar the numbers are good.**

What the measurement corrects: bringing memory back is SLOW next to an
upload — 4.2 GiB in 1.25–1.76 s is 2.4–3.4 GiB/s, where a plain upload ran
at 16.2 GiB/s and the estimate for this step was 0.2–0.4 s. And the driver
chooses its victim by idleness and by whole buffers: MADLAD, without the
option, lost 5.4 GiB at once. So a set over the card by 3.5 GiB costs, per
round of an Ask in which every model works once, about 0.2–0.4 s on each
small model and 1.3–1.8 s on the 35B: 2–3 s a round, nothing when the same
model answers twice running, and no decode speed.

**The offloaded arm, on the operator's word to München the same hour
("Measure now the experts caching option") (M, 08:19–08:22Z).** The same
sequence and requests, the 35B as `cpu_moe` with `moe_cache_cpu_share:0`,
the VRAM check on, nothing overcommitted. Decode is over ONE answer of at
most 128 tokens here, noisier than phase 4's medians (given in brackets):

| | 8 GiB cache | 4 GiB cache |
|---|---|---|
| the 35B on the card | 11,518 MiB | 7,421 MiB |
| warm, alone: first token / decode | 0.887 s / 66.5 tok/s [0.477 / 84.3] | 0.824 s / 60.1 [0.496 / 69.7] |
| the three small models loaded on top | nothing moved: 25.7 GiB on the card | nothing moved: 21.6 GiB |
| the 35B after them, round 1 | 2.094 s / 39.7 (the cache still filling), then 1.219 s / 52.1 | 0.826 s / 61.6, then 0.808 s / 62.0 |
| the small models after it | 0.35, 0.03, 0.02 s: their warm times | the same |
| the 35B, round 2 | 0.819 s / 69.7, then 0.710 s / 57.2 | 0.831 s / 54.8, then 0.809 s / 56.2 |
| MemAvailable, lowest | 34.1 GB | 34.1 GB |

No abort, no kernel line.

**The two arms side by side** (128-token prompt; the set is the 35B,
MADLAD, the nemotron reranker and nemotron-embed):

| | the 35B on the card | first token | decode | what the others pay | a 300-token answer after the others worked |
|---|---|---|---|---|---|
| resident, 2 GiB buffers, the driver swaps (S5) | 21.7 GiB; the set 3.5 GiB over the card | 0.28 s; 1.5–2.0 s on the first answer after the others worked | 98–100 tok/s | +0.2 to +0.4 s each, once, after the 35B answered | about 5.0 s; 3.3 s when it answers twice running |
| experts in host RAM, 8 GiB cache | 11.5 GiB; the set fits with 5 GiB to spare | 0.7–0.9 s (0.48 by phase 4's median) | 52–70 here (84 by the median) | nothing | about 5.4 s (4.0 s by the medians) |
| experts in host RAM, 4 GiB cache | 7.4 GiB; 9 GiB to spare | 0.81–0.83 s (0.50) | 55–62 (70) | nothing | about 5.8 s (4.8 s) |

Read together: the swap keeps the 35B's own speed and charges a toll at
each change of model — about 2–3 s over a round in which every model works
once; the cache charges every answer (a third of the decode speed lost, half
a second more to the first token) and nothing at a change, holds 19.3 GiB of
host memory at all times, and leaves room on the card. For answers of a few
hundred tokens the two cost about the same per round; the swap wins on long
answers and on a model answering twice running, the cache on short answers
and on room for something else (Krea 2). Neither is the arrangement in which
the Ask's own models and the 35B fit TOGETHER without the toll: the 35B,
nemotron-embed and the reranker are 28.9 GiB, under the reclaimer's line —
with MADLAD as the one that swaps, an Ask in the question's own language
would pay nothing. That arrangement was not measured.

**The operator's question, 08:3xZ: can the two be combined, so that what is
swapped is experts not needed?** Not with 2 GiB blocks: the driver knows
nothing of experts and brings every buffer of a process back when it runs
(the 35B was whole again after its next answer), and a 2 GiB block of the
all-on-card layout is the complete expert stacks of about four layers, of
which every token reads some. Two things would do what he describes, neither
measured: (a) an engine switch, `GGML_SYCL_USM_SYSTEM=1` — buffers of 4 GiB
or more become host memory the card reads through shared virtual memory, the
driver bringing in the 2 MiB pages a kernel touches; it needs the device to
report that capability, does not combine with the 2 GiB cap, and its speed
is unknown; (b) an expert cache that gives slots back under pressure, which
the engine does not have (`llama-moe-cache.cpp` sizes its slots at load): a
patch, with `cpu_moe`'s costs. (a) is one more row on the node, on the
operator's word to München.

**The operator, later the same morning: `GGML_SYCL_USM_SYSTEM` with a
history of page use, for preloading and for swapping — an opinion asked.**
What is read from the engine: the switch puts buffers of 4 GiB and more in
plain host memory and the card reads them through shared virtual memory;
its documentation calls it experimental and asks for an Xe2 card (the B70 is
one), Linux, a kernel built with `CONFIG_DRM_XE_GPUSVM`, and "enough host
memory for model weights and caches"; the engine gives the driver no hint
about those buffers today (no prefetch, no advice); and its expert cache
already counts each expert's use with a half-life (`llama-moe-cache.cpp`,
"CRF"), in memory only. What follows is design, not measurement:
- it fits the model: an expert of the 35B is about 3 MB, a page 2 MiB, so
  residency by page is nearly residency by expert; a page is in host memory
  OR on the card, never both (the cache duplicates, and holds 19.3 GiB);
  and the graph is the plain resident one, so what is on the card should
  run at the resident speed, not the cache's;
- the history should be the engine's own count of expert use, which is
  exact, not a record of page faults: saved with the model at unload, read
  at load to bring the dense weights and the most-used experts to the card
  before the first token (as much as the keeper allows), and read again
  when the keeper needs room, to push the least-used experts back to host
  memory while the model keeps answering;
- unknown, and only the node can say, in this order: whether the driver
  reports the capability (the node's kernel); whether the 35B then answers
  at about 100 tok/s once warm and what the first touches cost; what the
  driver itself evicts under pressure (likely by age of arrival, not by
  use — the reason the engine should choose); and whether user space can
  move a page each way on demand (to the card by touching or prefetching it
  from a kernel, back by reading it from the CPU: how Linux device memory
  behaves as I know it, NOT verified on xe here).
Order: the one-row test of the switch as it is; the history is worth
building only if that row runs at resident speed.

**The switch as it is — MEASURED by München, 2026-11-24 08:53–09:11Z, on
the operator's word ("Test A"); logs `/root/measure/cutover-c3294e/{log.txt,usm/}`.**
The card reports the capability: the verbose load prints
`GGML_SYCL_USM_SYSTEM: 1`, no "does not support" line, `SYCL0 model buffer
size = 20583.34 MiB`.

| | resident (part 1, copy A) | `GGML_SYCL_USM_SYSTEM=1` |
|---|---|---|
| load to first token | 8.1 s (file in the page cache) | 70.1, 84.3, 85.0 s |
| 128-token prompt: first token / prefill / decode | 0.280 s / — / 100.6 tok/s | 0.288 s / 486.2 / 73.5 (median of the first 5); single answers after: 100.7, 101.1, 99.9, 99.6 |
| 2,048-token prompt | — | 2.480 s / 831.2 / 98.3 |
| the five answers | — | equal to copy A's, 5 of 5 |
| card, by the process's own counters (fdinfo) | 21,782 MiB | **3,238 MiB** (+ 434 GTT) |
| card, by the driver's manager (`…/dri/0000:03:00.0/vram0_mm`) | — | **21,656 MiB** loaded (25 MiB empty), 21,690 after a long answer |
| backend's resident size | — | 19.5 GB |

Beside other models (part 2's sequence): MADLAD 8.66 s first / 0.34 warm
and the reranker 7.51 / 0.03, both fine; **the nemotron-embed copy's
backend exited on load** (`Backend process exited unexpectedly …
exitCode=-1`, requests 500 then 503 — once, see below); after it the 35B's own counters read
329 / 3,342 (its ordinary buffers pushed out) and its next answer took
0.725 s to the first token at 101.1 tok/s, the one after 0.278 s (2.04 s
with 2 GiB blocks). No kernel line of the abort kind; MemAvailable never
under 32.7 GB. `vram0_mm` was not read during that sequence, so how many of
the 35B's pages the driver took back under pressure is NOT measured.

**What it says.** Speed once warm is the resident one — the condition set
above is met. The saving is not there as the engine loads today, and the
source says why (`ggml-sycl.cpp`, `ggml_backend_sycl_buffer_set_tensor`):
the load writes every tensor with a copy that runs ON THE CARD
(`stream->memcpy`), into a buffer that is host memory, so the card faults
on every page and the driver moves each one to video memory — the whole
model, 2 MiB at a time, which is also the 70–85 s. Three things follow:
- the footprint is the resident one, and the per-process counters miss
  18.4 GiB of it: anything that budgets from fdinfo (München's
  `drm_holders`, the keeper) undercounts — the driver's `vram0_mm` is the
  figure;
- the 19.5 GB resident size is the same pages counted again, not host
  memory held besides: MemAvailable was 38,488 MiB before the first load
  and 37,357 MiB with the copy loaded and answering (München's log) — the
  load cost the host **about 1.1 GB**. A page moved to a card stays in the
  process's count;
- a newcomer was not served by a swap: its backend died while the 35B's
  pages held the card — ONCE (08:57:34 → 08:57:40; the later refusals were
  LocalAI's cooldown after a failed load, corrected by München). That copy
  ran without the switch (`GGML_SYCL_BMG_VRAM_CHECK=0` alone). LocalAI
  kept only the last line of its stderr, so the cause is NOT known; a
  failed device allocation is the likely one.

**What would make it do what the operator described** (design, nothing
measured): write the load from the HOST when the buffer is host memory (a
plain `memcpy`, ~10 lines in the same function), so that a page reaches
the card only when a kernel reads it — the dense weights and the experts
actually used; then the history (above) to choose what to bring first and
what to give back. Open before any of that is worth an hour: the
newcomer's exit, and whether the driver gives such pages back to a model
that asks for ordinary memory.

**Paging by use — where it stands (the operator, later that day: "we spoke
about paging and history of the experts' usage for preloading and paging
policy").** Not built and not measured: Test A was its precondition, no
more. Read in the engine since:

- a SECOND thing brings every page to the card whatever the load does: at
  the first token the engine reorders each layer's expert tensor WHOLE
  (`opt_for_reorder_id` → `reorder_qw`, for Q3_K to Q6_K, Q8_0 and MXFP4),
  reading and rewriting the experts nobody selected with the rest;
- the idea has the numbers on its side — measured, phase 4's tuner: a
  cache of 1.9 GiB took 64 % of the expert reads and one of 5.6 GiB 90 %,
  of 18.7 GiB of experts. A third of the experts do nine tenths of the
  work;
- the engine counts expert use only inside its `cpu_moe` cache
  (`llama-moe-cache.cpp`); resident, nothing counts, so a history needs
  that count added before it has anything to save.

**Written for the measurement, and deleted after it (below):
`staged/0012-llama-cpp-sycl-usm-by-use.patch`** (edc48d6 has it)
— `GGML_SYCL_USM_SYSTEM=2`: for a system allocation the weights are written
from the host and the expert tensors keep the layout they were loaded in
(the kernels take either). 21 lines in `ggml-sycl.cpp`; the value 1 is as
measured. NOT compiled here (no SYCL compiler) and never run.

| | pros | cons |
|---|---|---|
| **measure it** (an engine build with the patch, one window of the rows below) | says in one evening whether the idea can work at all on this card and driver: how much of the model ordinary use brings to the card, at what speed, and what a neighbour's load does | an engine build (27 min last time) and a card window; an experimental driver path that killed a neighbour once; the expert kernels on the plain layout are slower by an amount nobody knows |
| build the history first | nothing waits on the node | three more engine pieces (the count, its file, the preload and give-back) on a base that may not page at all |
| the history for the cache that already works (`cpu_moe`): expert counts saved at unload, the hottest loaded first | small; measured need — the cache's first answers after a load were 2.09 s / 39.7 tok/s against 0.7–0.9 s / 52–70 warm | keeps the cache's costs: 19.3 GiB of host memory, 84 tok/s at best with 8 GiB |
| leave it (today) | nothing to do | the 35B stays a choice between a toll at each change of model and a slower model |

Recommendation: measure it. If the operator says nothing, the patch stays
staged and nothing runs.

**The rows (München's, on the operator's word there).** The 35B's copy
with `GGML_SYCL_USM_SYSTEM=2`, the rest as Test A, `verbosity:4`; at every
step the driver's `vram0_mm`, MemAvailable, the process's counters, and the
backend's ring tailed each second during a load:

1. the load, before any request: seconds, and video memory — it should be
   near nothing;
2. the first answer (128-token prompt): first token, decode, video memory
   after — the dense weights and the first experts arriving;
3. five answers, the fixed prompt: medians, video memory after each, and
   whether the text equals copy A's (other kernels: it may not, at
   temperature 0.7);
4. **twenty different questions** (the chat contest's): video memory after
   each. Where it levels off is the number the whole idea rests on;
5. the 2,048-token prompt: prefill, video memory after;
6. warm, the fixed prompt five times: against 100.4 tok/s resident;
7. the neighbours of part 2, each loaded and asked: does each load, or
   exit as one did in Test A (its last lines kept this time); what the
   35B's share of video memory does; then the 35B again — first token,
   decode;
8. unloaded: video memory back to 25 MiB.

Stop at any abort or kernel line, and under the host-memory floor. What
follows from it: a level well under 21.7 GiB at near-resident speed, and
neighbours that load — then the history is worth building (the count of
selected experts in this mode, saved at unload; the hottest brought in at
load; the least used given back when the keeper asks). Everything on the
card after twenty questions, or neighbours that die — the idea ends there,
and the history can still serve the cache (the table's third row).

**THE EIGHT ROWS (München, 2026-11-24 12:45–13:10Z, on the operator's
"Go").** Engine `louise-sycl-f16-llama-cpp-271bff744c28-stack-7c301a7ba437`
(the series + the patch; it compiles), the 35B's test copy with
`GGML_SYCL_USM_SYSTEM=2`, node paused, web stopped; no abort, no kernel
line. Files on the node: `/root/measure/usm-by-use/rows/` (`rows.jsonl`,
`samples.txt` — video memory and MemAvailable every 0.5 s — and the dying
backend's ring).

| row | the driver's video memory | host MemAvailable | time, speed |
|---|---|---|---|
| before | 25 MiB | 24.5 GB | |
| 1 — the load | 2.4 → 3.3 GiB over 80 s: the weights stay in HOST memory, as written | down to 4.9–5.5 GB, **lowest 4,551 MiB** | |
| 2 — the first answer | 3.3 → 8.3 → 14.4 → 21.3 → **21.7 GiB in about 30 s** | back to 23 GB | first token 110.8 s from the request, the load included; 69.8 tok/s |
| 3 — the fixed prompt, four more | 21,643–21,691 MiB (the process's counters 3,204 / 714) | | 0.247–0.273 s; 93.6–97.3 tok/s; **0 of 5 texts equal copy A's** (sensible Italian, another wording) |
| 4 — twenty different questions | 21,727 → 21,773 MiB: flat, it was everything already | | 0.148–0.201 s; 92.6–97.3 then 100.6–101.1 tok/s for the last sixteen |
| 5 — the 2,048-token prompt | 21,807 MiB | | first token **1.798 s** (copy A: 2.500); 98.6 tok/s |
| 6 — warm, the fixed prompt ×5 | | 23.2 GB | 0.234–0.243 s (copy A: 0.280); **100.8–101.1 tok/s** (100.4) |
| 7 — the neighbours | MADLAD loads (8.70 s, 0.36 warm; 29,029 MiB), the reranker loads (7.51 s, 0.03; 32,206, the card full) | 21.0 GB | **nemotron-embed's backend dies on load**; then the 35B 1.065 s to the first token, the next 0.230 s, 101.0 tok/s |
| 8 — unloaded | 25 MiB | 25.1 GB | |

**The whole model is on the card after ONE answer.** The load does what
the patch meant; the use does not, and not for a reason in the engine: a
prompt of 128 tokens makes 128 selections in every layer, and between them
they name almost every expert. The tuner's figure said as much — a cache
holding 30 % of the experts still missed 10 % of the reads: the less-used
experts are read all the time, each a little. A page is the wrong unit for
that. Without a policy that takes pages back, by-use paging ends where
resident starts, after 111 s instead of 8 and with the host at 4.5 GB on
the way; with one, every prompt would page the cold two thirds in again at
the 2.4–3.4 GiB/s measured for coming back (S5). The engine's cache answers
a miss ON THE CPU, from host memory, moving nothing — which is why it holds
84 tok/s with a third of the experts on the card.

**And such pages do not make room for a neighbour** — Test A's open
question, answered by row 7. While nemotron-embed loaded, the driver did
take about 3.5 GiB of the 35B back to host memory (its counters 382 /
3,569; the host 3.3 GB lower), and the newcomer died all the same, its last
lines kept this time: `level_zero backend failed with error: 39
(UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)` at `ggml-sycl.cpp:2147`, `alloc`,
`phys.emplace(dev, ctx, reserve_size)` — the reservation of its context on
demand. With ordinary 2 GiB buffers (S5) the same three neighbours all
loaded.

**So the idea ends here**, by the rule set before the run: paging the
35B's experts by use is not the way to hold fewer of them on the card —
the cache is — and memory of this kind may not share a card with models
that load beside it. The patch is deleted from `staged/` as its row said.
What stays of the operator's idea: the usage history for the cache (its
first answers after a load were 2.09 s / 39.7 tok/s against 0.7–0.9 s /
52–70 warm), and, for the rest of the set, the context on demand on every
model and whole idle models paged instead of unloaded.

**One thing the run showed that nobody was looking for.** With the expert
tensors left in the layout they were loaded in, the model was not slower
and started sooner: the 2,048-token prompt in 1.798 s against 2.500 and
2.480 (resident; Test A on the same kind of memory, reordered), the short
one in 0.234–0.243 s against 0.280, at 100.8–101.1 tok/s against 100.4.
One run, one figure for the long prompt, on memory allocated another way:
not yet a measurement. `staged/0012-llama-cpp-sycl-reorder-experts-switch.patch`
adds `GGML_SYCL_REORDER_EXPERTS` (default 1 = today) so that the two
layouts can be compared on the model as it normally runs — the same test
copy, the switch 1 then 0, both prompts five times each: first token,
prefill and decode, and the answers read for sense (other kernels round
otherwise: the texts will differ, as they did here). Compiled here
2026-11-24 (Intel's compiler installed in the cloud session), never run.

| | pros | cons |
|---|---|---|
| **compare the two layouts** (an engine build, four rows) | if it holds, the 35B starts a long prompt a quarter sooner for one line in its file — and so may any MoE model on this backend | a build and a short card window for what may be an artefact of that run |
| leave it (today) | nothing to do | a possible quarter of the 35B's prefill left unread |

Recommendation: compare, the next time an engine is built for anything
else. If the operator says nothing, the patch stays staged.

| configuration that fit | sum MiB | free MiB | host RAM | refusals |
|---|---|---|---|---|
| | | | | |

| model | alone (phase 3) | resident set, sequential | ratio | concurrent pair |
|---|---|---|---|---|
| | | | | |

## 3b. Old engine measured against the new engine as upstream's claims project it

*Written 2026-11-22 before phase 4, at the operator's request, so that the
report can set each projection beside its measurement. M = measured on the
node (phase 0 on the live engine `281e1ee`; phase 3's first pass on
`133ef68`, same kernels as the rebuilt engine); P = projected from
upstream's figures, with the hardware they were taken on — not ours until
phase 4 says so.*

| model, quantity | old engine (M) | new engine | basis |
|---|---|---|---|
| 35B prefill, 140-token prompt | 480 tok/s, first token 0.29 s | **P ≈ 500–1,130 tok/s**, first token ≈ 0.12–0.28 s | #30156 on a B60, Qwen3-30B-A3B Q4_K_M, 128 tokens: +136 %; a user's Arc Pro B70 on another MoE: +5–7 % — the range is the claim's spread; our ubatch is 512, the PR's 2048 |
| 35B prefill, 2,061-token prompt | 820 tok/s, first token 2.51 s | **P ≈ 880–1,010 tok/s**, ≈ 2.0–2.3 s | the same PR at 2048 tokens: +23 % (B60), +7 % (B70 user) |
| 35B decode, 1 stream | 99.6 / 97.3 tok/s | **P ≈ 95–100 tok/s** | #30156: −4.6 % at short prompts, flat at 2048; nothing else upstream touches dense-path decode |
| 35B footprint | 21,782 MiB | **M 21,642 MiB (phase 2, the check's own count)**; under the cache **P ≈ 9 GiB** with 4 GiB of cache | the cache: experts (≈ 18.4 GB) in host RAM |
| 35B decode under the MoE cache | — (never run) | **P ≈ 45–75 tok/s** by the community's cards; **P ≈ 60–80 tok/s for THIS host** at 60–80 % hits (E, modelled below) | community, Qwen3.6-35B-A3B Q6_K_XL on CUDA: 42 → 67 tok/s (2080 Ti, PCIe 3.0, 6.8 GB cache, share 0.95), 44 → 74 (5060 Ti, PCIe 5.0 x8); their RAM unknown — slower links than ours, so not our DDR5-6400 or Gen5 x16. SYCL unreported; the cache trades speed for room, it does not beat the resident 99.6 |

*The MoE cache against this host's RAM and link (E, 2026-11-22).* Per token
the 35B touches 8 experts × 40 layers = 320 of its 10,240, ≈ 1.8 MB each at
Q4: ≈ 575 MB. With a hit rate h the misses are (1 − h) × 575 MB per token,
served two ways: uploaded to the card — PCIe 5.0 x16, ~45 GB/s practical,
the host reading them at DRAM speed, which at 6400 (~80 GB/s practical) is
not the limit — or computed on the CPU from host RAM (`moe_cache_cpu_share`),
which IS DRAM-bound: the Ryzen 7600 reads ≈ 70–80 GB/s at 6400 against
≈ 57–65 at 5200, so the 6400 profile lifts the CPU share's ceiling by about a
fifth and makes a CPU-computed miss slightly cheaper than an uploaded one.
Lower bounds per token at h = 0.6 / 0.8: misses 230 / 115 MB → 5.1 / 2.6 ms
uploaded, 3.3 / 1.6 ms on the CPU, beside the card's own ≈ 10 ms (99.6 tok/s
resident); serialised, ≈ 13–15 ms → **65–80 tok/s**, overlapped better.
What decides it is the hit rate on OUR prompts (upstream saw 61–93 % by
cache size on a larger model) and whether the host experts are PINNED:
mmap'd (the default) they are not, and upstream's docs say the uploads then
run below link speed — phase 4's cache rows run twice, `mlock` on and off.
The host's DRAM copy bandwidth itself has never been measured here
(stressapptest was a stability test): one 6-process copy measurement goes
into phase 4's conditions.
| MADLAD, one translation / one 7-language question | 0.43 s / 1.06 s | **M 0.42 s** (−2 %); bytes identical 379/379 | phase 2 rerun, paused |
| encoders, 24 texts through LocalAI + VeGa | bge-m3 1.238 s · nemotron 1.330 · qwen3-4b 4.374 · medcpt-query 0.360 | **M 1.204 · 1.277 · 4.311 · 0.347** (−2.7 / −4.0 / −1.4 / −3.6 %); bytes 16/16 identical; medcpt's gate exact | phase 3 first pass on `133ef68`; repeated on `c3294e` |
| nemotron-1b reranker, per list | 2,512 ms median | **P ≈ 2,500 ms** (no upstream claim on this path) | phase 3 second pass measures it |
| chat 9B prefill / decode | 1,157 / 3,301 tok/s · 77.5 / 75.5 tok/s | **P unchanged** (dense; no claim) | phase 4 measures it alongside |
| a model that does not fit | the driver spills to host RAM, silently | **M refused** (HTTP 500, no spill, nothing killed) | #31285, phase 2 |
| idle context (P11) | given back after the keep | **M carried** (`context_on_demand_test` exit 0; the gate admits the engine) | the rebuilt engine |

Reading: the one place the new engine should be visibly faster is the
35B's prefill — first token at the 2k-token Ask prompt — by somewhere
between +7 % and +23 % by the two SYCL datapoints; everything dense is
within a few per cent either way, and the MoE cache is a room gain
(21.6 → ~9 GiB) paid in decode, to be measured on SYCL for the first time
here.

## 4. Report (phases 0–4; the POC, phase 5, follows)

*2026-11-23. Every figure is München's, measured on the node with the node
paused, the web stopped and the card holding only the model under test,
unless marked E. Old = the live engines (`281e1ee481b6`, llama.cpp
`ddf48f8` + P1–P7 + P11; the reranker's `10aa8b1851d7` with P10). New =
`louise-sycl-f16-llama-cpp-c3294e050ff2-stack-7c301a7ba437`: LocalAI v5.0.1,
llama.cpp `78e0529`, the eight-patch series of commit 41fb62a, the same
Intel stack.*

### 4.1 Verdict

**The new engine is QUALIFIED.** The series' own checks pass (P5
`test-backend-ops` 235/235 and the KQ shapes 272/272, `test-chat`,
`embedding_tokens_test`, `context_on_demand_test`); every gate that could
run passes (bge-m3, qwen3-embedding-4b, medcpt-query; NeuRIS's is refused
for the collection's missing reference copy, not for the engine); every
encoder is bit-identical over three fresh loads, two orders and a 512-step
sweep, and gives the LIVE engine's bytes on all sixteen fingerprints; the
reranker's scores equal the live ones in 175 of 175 lists and match torch;
MADLAD equals the live engine on 379 of 379 translations; no model is
slower. Nothing was re-embedded and nothing serves on it yet: the cut-over
is the operator's to start (§4.5).

### 4.2 Performance, old against new

| model | quantity | old | new | change |
|---|---|---|---|---|
| vega-bge-m3 | 24 texts through LocalAI + VeGa, median | 1.238 s | 1.203 s | **−2.8 %** |
| vega-nemotron-3-embed-1b | the same | 1.330 s | 1.28 s | **−3.8 %** |
| vega-qwen3-embedding-4b | the same | 4.374 s | 4.285 s | **−2.0 %** |
| vega-medcpt-query | the same, one at a time | 0.360 s | 0.35 s | **−2.8 %** |
| louise-rerank-nemotron-1b | per list, median of 175 | 2,512 ms | 2,468 ms | **−1.8 %** |
| louise-translate-madlad | per translation, median | 0.43 s | 0.42 s | **−2 %** |
| chat 9B | prefill at 140 / 2,061 tokens | 1,157 / 3,301 tok/s | 1,306 / 3,413 | **+12.8 % / +3.4 %** |
| chat 9B | decode | 77.5 / 75.5 tok/s | 77.7 / 75.7 | +0.3 % |
| the 35B, resident | prefill at 140 / 2,061 tokens | 480 / 820 tok/s | 510 / 826 | **+6.3 % / +0.8 %** |
| the 35B, resident | decode | 99.6 / 97.3 tok/s | 100.4 / 97.9 | +0.8 % / +0.6 % |
| the 35B, resident | first token | 0.292 / 2.512 s | 0.274 / 2.494 s | −6 % / −0.7 % |
| every model | card memory | — | the same within 0.1 % | none |

No loss anywhere. The gains are small and real: 2–4 % on the encoders,
the reranker and MADLAD, 3–13 % on chat prefill. omnivoice (its own
backend), dar-legal-it (torch) and the OpenVINO pair do not run on this
engine and are unchanged. The engine-only path could not be measured on
either engine (the backend images ship no `llama-server`), so every figure
is the real path Louise uses.

Two things change that are not speed: a model that does not fit is REFUSED
(HTTP 500 in 3 s, no spill to host RAM, nothing killed) where the old
engine let the driver spill it silently; and the series now records what
runs (finding F4).

### 4.3 How the 35B performs

| configuration | decode, short / 2k prompt | prefill | first token | on the card | on the host |
|---|---|---|---|---|---|
| resident (as today's YAML) | 100.4 / 97.9 tok/s | 510 / 826 tok/s | 0.27 / 2.49 s | 21,782 MiB | — |
| experts in host RAM, 8 GiB cache | 84.3 / 80.6 (−16 / −18 %) | 293 / 713 | 0.48 / 2.89 s | 11,562 MiB | 19.3 GiB held by the driver |
| experts in host RAM, 4 GiB cache | 69.7 / 65.3 (−31 / −33 %) | 282 / 697 | 0.50 / 2.96 s | 7,474 MiB | 19.3 GiB held by the driver |
| for scale: the 9B | 77.7 / 75.7 | 1,306 / 3,413 | 0.11 / 0.60 s | 6,284 MiB | — |

- Resident, the 35B decodes faster than the 9B (100 against 78 tok/s) and
  takes four times longer to its first token on a 2k-token Ask prompt (2.5 s
  against 0.6 s); eight streams give 141 tok/s in total.
- Offloaded, it works and stays usable: a 4 GiB cache holds it in 7.3 GiB
  of the card at 65–70 tok/s, an 8 GiB cache in 11.3 GiB at 81–84 tok/s.
  Misses are best uploaded (share 0); computing them on the CPU loses at
  both sizes; `mlock` is a no-op. Hit rate 64 % at 1.9 GiB of cache, 90 % at
  5.6 GiB (the tuning script's runs).
- The cost of offloading is the HOST: 19.3 GiB of system memory the driver
  holds for the experts for as long as the model is loaded, on a 61 GB
  machine that had 22 GB available with the node running. The ZFS ARC was
  pushed from 16 to 2 GiB once during the runs.
- The update itself brings the 35B +6 % prefill on short prompts and
  under 1 % elsewhere. The device-side MoE scheduler, the change upstream
  credits with +23 to +136 %, makes no difference here (on or off: the
  same to the token); two more rows in phase 5's window say whether it can
  be made to engage.

### 4.4 Findings

| | finding | state |
|---|---|---|
| F1 | MADLAD on the live engine no longer matched its 379/379 record (375/379) | the record had aged: three of four differences move to torch, none is wrong; the reference becomes torch, the byte record is re-cut at cut-over. The live engine had never had its own MADLAD run |
| F2 | LocalAI's `auto_upgrade_backends` was true since 11-08 and v5.0.1 upgraded three non-Louise backends | set back to false by München |
| F3 | MADLAD read 2.4× slower on the new engine | a measurement artifact: the node had gone back to `running` after a reboot; paused, −2 %. Every timed row now records the node's mode and the card's holders |
| F4 | the repository's series did not hold what was live (P10, P11 were "staged") | fixed: both in the series (41fb62a), the engine rebuilt, phases 1–3 rerun |
| F5 | NeuRIS has no CPU-reference copy, so its gate cannot run | CLOSED 2026-11-24: the reference resolves per model through RAGE's API (9ed2281d, live since 11-23); NeuRIS gates with its space at the cut-over |
| F6 | the device-side MoE scheduler has no effect on the 35B at our settings | two rows queued (override bit, ubatch 2048) |
| F7 | with `cpu_moe` the experts live in driver-held system memory (19.3 GiB), not in the process: `mlock` does nothing, the memory is not reclaimable | a cost to budget, §4.3 |
| F8 | the MoE cache's statistics reach no log through LocalAI (the backend is killed before its destructor prints) | the tuning script is the way to read hit rates |
| F9 | `rerank_bench`'s comparison line prints `None` (wrong field names) | a tool defect, to fix |
| F10 | my estimates of the host were too high: DRAM copy 38 GB/s measured (75–85 modelled), uploads 16.2 GiB/s (45 GB/s used) | the spill's swap estimate corrected in idle-context-brief.md §6 |

### 4.5 What is left

- **Phase 5, the POC**: run 2026-11-24 as the operator cut it down — §4.6.
- **The cut-over**, the operator's to start, in a window: BACKOUT written
  first; one run per model, gated once with its acceptance recorded;
  MADLAD's byte record re-cut from the adopted engine; the YAMLs with
  `fit:off` (094df03); room made BEFORE every load, since the engine now
  refuses what does not fit and today's set at rest fills the card; NeuRIS
  after its reference (F5).

| choice for the chat model | pros | cons |
|---|---|---|
| keep the 9B (the status quo) | 0.6 s to the first token; 6.3 GiB; nothing to decide | the smaller model |
| the 35B resident | 100 tok/s; the larger model | 21.8 GiB: the rest of the set does not stay on the card, so models are evicted and reloaded around it (28.6 s to reload the 35B itself); 2.5 s to the first token |
| the 35B offloaded, 4 GiB cache | the larger model in 7.3 GiB of the card; the whole set may stay resident (the POC confirms) | 65–70 tok/s; 3.0 s to the first token; 19.3 GiB of host memory held, the ARC squeezed |

Recommendation: adopt the engine (it is faster everywhere and equal byte
for byte); decide the chat model after the POC and the chat contest's
quality scores, since performance alone does not choose between a 9B at
0.6 s and a 35B at 3 s. If the operator says nothing, nothing changes: the
live engines keep serving and the 9B stays the chat model.

### 4.6 The POC, as run (2026-11-24)

*What ran, on the adopted engine: the 35B with MADLAD, the nemotron
reranker and nemotron-embed (36.1 GiB of models on a 31.9 GiB card), each
asked in turn, two rounds — once with the 35B resident and the engine's
2 GiB buffers, the driver swapping (S5), once with its experts in host RAM
behind an 8 GiB and a 4 GiB cache. What did NOT run, cancelled by the
operator or never reached: the swap with one 18.5 GiB buffer, the whole set
of §1 loaded at once, the concurrent pair, the 30-minute loop. Figures: §3,
phase 5.*

| the 35B as chat | pros | cons |
|---|---|---|
| **resident, 2 GiB buffers, the driver swaps** | its own speed, 98–100 tok/s, 0.28 s to the first token when it answers twice running; gives back only what a neighbour needs (4.2 GiB of 21.7); the option costs nothing alone | 1.3–1.8 s more to the first token each time it returns after another model worked, and 0.2–0.4 s on each small model: 2–3 s a round; needs what the node refuses today — a load onto a full card (the VRAM check off for it) and a reclaimer that does not evict at 0.98; coming back runs at 2.4–3.4 GiB/s, so a set further over the card pays more |
| **experts in host RAM, 8 GiB cache** | 11.5 GiB on the card: the set fits with 5 GiB to spare, nothing ever moves, no policy to change — two options in the model file | every answer slower: 0.5–0.9 s to the first token, 52–84 tok/s; 19.3 GiB of host memory held for as long as it is loaded |
| experts in host RAM, 4 GiB cache | 7.4 GiB on the card, 9 GiB to spare (Krea 2's room) | 55–70 tok/s; the same host memory |
| the status quo: the 9B | 0.11 s to the first token, 78 tok/s, 5.4 GiB, nothing to change | the smaller model |

**What the POC answers.** The operator's premise holds: models used one
after the other can share a card they do not fit on together. Both ways
work on the B70 with no error in any run; for answers of a few hundred
tokens they cost about the same per round, and the choice is between a
toll at each change of model and a slower model at every answer.

**Recommendation**, performance alone: if the 35B becomes the chat model,
the 8 GiB cache first — it is a model file's two options, inside the
node's present rules — and the swap as the faster arrangement to build
toward, with the keeper's and the reclaimer's rules changed for it (the
architecture session's) and the one layout not yet measured: the 35B with
the Ask's own two models resident together (28.9 GiB) and only the rest
swapping. Which model answers is still the chat contest's to say. If the
operator says nothing, nothing changes: the 9B stays.

## 5. The cut-over (operator, 2026-11-24: "Engine now, chat later")

The engine is adopted; the chat model stays the 9B until the POC and the
chat contest decide. München runs the cut-over on the node, in a window the
operator grants there, on its own switch tooling (the 11-11 switch's), in
this order. approved-encoders.md "Retiring an engine after a swap" is the
standing procedure; what follows is this swap's instance of it.

**What changes on the node**
- the wrapper package `vega-louise-llama-cpp` points at
  `louise-sycl-f16-llama-cpp-c3294e050ff2-stack-7c301a7ba437` (at the live
  wrapper revision, as `vega-louise-llama-cpp-q` was built for phase 3): every
  `vega-` llama.cpp encoder and the chat model move with it;
- `louise-translate-madlad` and `louise-rerank-nemotron-1b` name that engine
  directly (their YAMLs in this commit);
- the thirteen YAMLs carry `fit:off` (094df03), as gated in phase 3.
Nothing else: no collection's data, no Codex, no app code. The engine's VRAM
check stays at its default (on): Louise frees the room before every cold
load (`model_load._make_room`, operator 2026-11-16), a refused load reads as
a busy endpoint, and the check is the backstop that replaces the silent
spill.

**Order**
0. BACKOUT first, on disk before anything moves: the live wrapper package
   copied outside the backends path (`backend_files.sh`'s `bf_copy`: the ACL
   trap), every YAML about to change copied with its sha256; the old engines
   stay installed until step 7. Backout = the wrapper and the YAMLs put back,
   `models/reload`, each llama.cpp model shut down.
1. The node paused, the web stopped; every llama.cpp model shut down; the
   driver read (the card's zero).
2. The wrapper repointed, the YAMLs replaced, `models/reload`. The test
   copies (`vega-*-q`, `qual-*`) and the `-q` wrapper package removed, so
   that nothing loads twice.
3. The gates, one collection and role at a time, with their acceptance
   recorded (`hold embedder check`): wikipedia (bge-m3), gutenberg
   (qwen3-embedding-4b), pubmed's query side (medcpt-query), and every
   collection on nemotron that is not frozen. A run that does not reproduce
   itself, or a CPU reference below its line, stops the cut-over and the
   BACKOUT runs — the wrapper is one package, so it is all or nothing.
   Expected from phase 3: exact, the bytes the live engine gave.
4. **NeuRIS**: its gate cannot run until a CPU reference resolves for it
   (F5). If the architecture session's per-model reference is deployed by
   the window, NeuRIS gates like the rest. If not, NeuRIS's run on the new
   engine stays untested: its vector leg is withheld and its embedding
   deferred, its text search goes on (03 §3.3), until that deploy. München
   puts this to the operator with the window: cut over now with NeuRIS's
   vectors waiting, or hold the whole cut-over for that deploy.
5. MADLAD on its live name: t5waves T4 and T3 (the pilot first), equal to
   the qualification's rows; its byte record re-cut from this engine and
   dated. The reranker: `nemotron_rerank_parity.py` and twenty lists of
   `rerank_bench`, scores equal to phase 0's. The chat 9B: one warm answer
   on each fixed prompt.
6. The web up, the node back to its mode. One Ask read end to end. Thirty
   minutes watched: refusals by the VRAM check, evictions, deferrals in the
   worker's log, `louise node resident show`.
7. Retirement (standing permission): the old engines a Codex names archived
   first (`tools.archive_backend`, its sidecar), then
   `python3 -I deploy/vega/retired_backends.py`, then `--delete`:
   `281e1ee481b6…`, `10aa8b1851d7`, the first v5.0.1 engine `133ef68669a1`
   (it never served). Its output is the record.
8. The record: what ran — the switch script, the final names, the gates'
   lines — comes back here and is committed, so that the repository holds
   what is live (F4's lesson).

The keeper's context reserve and the reclaimer's line at 0.98 (the
operator's B, with the architecture session) are another change: not in the
same step, so that what the thirty minutes show belongs to one of them.

**State on the node when the order arrived (München, 2026-11-24):** nothing
started; the window and the NeuRIS choice are with the operator. The keeper
change (reserve 3,262 MiB, reclaimer 0.98, deploy 8030ea0) is live since
00:30Z and its watch is over (budget 28,740 MiB). Another deploy waits for
the operator's word — architecture's 7f118736, after a worker OOM hold at
01:46Z — and the cut-over goes AFTER it and its check: one change per
watch. Step 4 may be moot: that night's migration put NeuRIS, legi_F and
Normattiva on chunker v4 in ONE space (`sha256:a5630843fdc709ae…`) whose
calibration reference was embedded at NeuRIS's derive, and on the live
engine NeuRIS is admitted under the same validated run as the other two
(`sha256:1f09a0e553ea2dfd…`). Whether the new engine's run still needs the
per-model reference resolution is München's question to architecture.
Phase 3's evidence for nemotron is the engine's (bit-identical runs, the
live engine's bytes) and holds for the new space; its gate runs on that
space at the cut-over like any other.

**Step 4 is DROPPED and F5 is closed (architecture, through München,
2026-11-24).** The per-model CPU reference resolution is live (9ed2281d,
`app/rage/reference.py`, deployed 11-23 16:23Z; NeuRIS.next's gate passed
exact with the reference found by its model's key). NeuRIS, legi_F and
Normattiva are one space (`a5630843…`) with one validated run: at the
cut-over it gates ONCE on the new engine, on any of the three (NeuRIS, the
smallest), and its statement covers all three; wikidata's and
Normattiva-nemo's nemotron spaces gate on their own against the same
model's reference. No collection waits. The order stands: architecture's
7f118736 and its check, then the cut-over's window.

**Second attempt, 2026-11-24 06:40Z (München): steps 3 and 5 PASSED; the
engine `c3294e050ff2` serves.** After wikipedia's CPU reference and a clean
preflight 0b (11 roles found). Step 3, each gate with `--remeasure --accept`,
the card emptied before it, exit 0:

| gate | whole set, worst / median | itself | CPU reference, worst / median | tok/s | statement |
|---|---|---|---|---|---|
| wikipedia, bge-m3, document | 0.999939 / 0.999999, 0 of 512 under 0.995 | exact, 3 fresh loads; 300 fresh contexts, closest 1.0000000 | 0.999930 / 0.999999 | 56,828 | `document-e470257a42df7739.json` |
| gutenberg, qwen3-embedding-4b, document and query | 0.999473 / 0.999949 | exact | 0.996084 / 0.997927 | 3,120 | `document-` and `query-449ca5005f89a66f.json` |
| pubmed, query side, medcpt-query | 0.999649 / 1.000000 | exact | 0.999951 | 30,958 | `query-d1e409e859d6bac4.json` |
| NeuRIS (covers legi_F and Normattiva), nemotron | 1.000000 | exact | 0.999997 | 6,863 | `document-71f82f1d15c01b37.json` |
| wikidata, its own space, nemotron | the same figures, the same fingerprint `9863c1d7…` | exact | the same | 6,858 | the same statement |

Step 5: MADLAD, pilot 18/18, then T4 one at a time 379/379 identical to the
qualification's engine (375/379 to P3's old one-slot record, 345/379 to
torch, median 0.42 s), T3 the same group twice 624/626, in a wave equal to
alone 612/626, 1.05 s a question; its byte record re-cut
(`/root/madlad-gguf/cmp-t4-c3294e050ff2-2026-11-24.jsonl`, 379 rows, sha256
`6ae3e651d9f9a51c…`). The reranker: parity on 20 queries, the engine
reproduces itself; 20 of 20 lists with scores equal to phase 0's, largest
difference 0.0, 2,441 ms a list (phase 0: 2,496). The chat 9B: 0.113 s /
1,244 / 78.0 tok/s on the short prompt, 0.609 s / 3,383 / 75.7 on the long
one (phase 0: 0.121 / 1,157 / 77.5 and 0.624 / 3,301 / 75.5). The OCR model
loaded and answered. No `xe` or OOM line.

As it runs: `fit:off` is in ELEVEN model files; MADLAD runs WITHOUT it, as
it was qualified. The repository's `louise-translate-madlad.yaml` carries it
(094df03) and the "thirteen" above is the repository's count: both are
corrected when step 8 brings the live files here.

Still to come, in this order (the operator, to München: "After part 1 do
also part 2"): S5 part 1 (running since 08:08Z) and part 2, then step 6 (the
web up; the node stays PAUSED, the operator paused it himself at 06:30Z),
the thirty minutes, step 7, step 8.

**First attempt, 2026-11-24 06:16–06:18Z (München): BACKED OUT at the first
gate, cleanly.** The operator's word: "Deploy and step 3 right after".
Step 0 on disk (the wrapper copied by `bf_copy`, 5,054 files; `vega.json`,
`file-digests.json` and 14 YAMLs with their sha256; `backout.sh`). Step 2 as
corrected by München before it ran: the repository's YAMLs LAG the live
ones (no `context_on_demand` / `context_canary` on the three embedders,
nemotron unpinned, qwen3-4b at ubatch 8192 for the live 2048, MADLAD at
ctx 4096 / parallel 7 for the live 2048 / 4), so nothing was copied from
the repository — each live file kept every key and gained `fit:off`
(eleven files), and `louise-translate-madlad`, `-madlad-q8` and
`louise-rerank-nemotron-1b` were pointed at the new engine. The cold load
on the new engine worked (`vega-bge-m3` in 6.0 s). Then wikipedia's gate
exited 2 BEFORE any measurement: the node holds no CPU reference for
bge-m3's weights with that space's inputs, and "every card run is held to
its CPU twin (invariant 12), so the gate is not run without one". Backout
in six seconds: `vega.json` byte-equal, the YAMLs' sha256 back, the web
down about two minutes, the live set loaded again on `281e1ee481b6`.

**F11 — the qualification never met the gate's precondition.** Phase 3's
bge-m3 "PASS" ran through the `-q` wrapper without `--accept`, on a path
that does not ask the node's reference store; the store holds references
for qwen3-4b (both sides), nemotron (document) and medcpt-query, none for
bge-m3. Nothing failed in the engine. For the retry: a PREFLIGHT with no
window — every served collection whose encoder goes through the wrapper
enumerated (not only the five named above), the reference each gate will
ask for resolved read-only and listed held or missing, query sides
included, every missing one made on the CPU first — and that table is the
retry's step 0b. München is making bge-m3's reference
(`hold embedder cpu-reference wikipedia`) and retries on the same word.

**The preflight (München, with architecture's read-only resolver:
`reference.resolve` by the Codex's weights, set, ctx, cut and instruction;
nothing created on a miss).** The collections whose encoder goes through the
wrapper are exactly: wikipedia (bge-m3), gutenberg (qwen3-embedding-4b,
document and query), pubmed's query side (medcpt-query), wikidata (nemotron,
its own space) and S4 = NeuRIS / legi_F / Normattiva (and EUR-Lex5.next:
Legal's, paused, not gated, covered by NeuRIS's statement). Nothing on
granite, harrier or a `-nemo` collection is held; nemotron spaces declare no
query encoder, so no query reference is asked. Every reference FOUND —
wikidata's is the same key as S4's (ctx 8191, "passage: ", tokens) — **but
wikipedia's** (weights `daec91ff…`, ctx 1024, tokens), in the making since
06:30Z. pubmed's document side (OpenVINO) and Normattiva-DAR (torch) are
outside the cut-over. The retry is armed on the node: it waits for that
reference, runs the preflight again into the cut-over's log, requires every
role found and the backout copies byte-equal to the live files, then steps
1–3 once. On F11's other path: München reads the 11-22 gate as having taken
the collection's own kept reference, under the code live before the
node-store rule of that evening (9ed2281d); the gate's output comes with
step 8.

**After it**: phase 5, the POC, on the adopted engine — the live set as it
is, the 35B as the only test copy in place of the 9B — in a later window. Two arms
(§2 phase 5): the 35B offloaded with its cache, and arm S, the 35B resident
with the driver swapping idle models to host RAM.
