# Updating the llama.cpp backend: what the node gains (2026-11-21)

*The operator: "run unattended, give me a report later, I want to know what
I gain updating my backend to the latest version (plus my patches)". The
node's backend is LocalAI's llama.cpp backend built by
`deploy/localai/llama-cpp/build.sh` from LocalAI master of 2026-11-02
(`16acc89`) with `LLAMA_VERSION ddf48f8` (llama.cpp of 2026-10-31) and our
series (P1–P7, `deploy/localai/patches/series`). "Latest" here is LocalAI
v5.0.1 (2026-11-20), whose llama.cpp pin is `78e0529` (2026-11-18); llama.cpp
master was `29b52d3` (b12600, 2026-11-20) when read. Every figure is
upstream's, from its PRs, unless marked M (measured on our node) or E
(estimate). Nothing was built or run on the node.*

## 0. In one paragraph

The update is worth doing for ONE model, the 35B MoE chat candidate, and
for one safety change. For the 35B it brings the SYCL device-side MoE
scheduler (prefill +23 % at 2k-token prompts, +136 % at short ones, on a
B60 with Qwen3-30B-A3B Q4_K_M) and the completed MoE expert cache (the
experts in host RAM, the hottest on the card; decode 1.5–2× over
experts-on-CPU on CUDA cards, SYCL unreported), which is the only known
way to hold the 35B in ~9 GiB instead of 21.6. For every model it ends the
driver's silent spill to host RAM by refusing a buffer that does not fit
(the report that led to it was a kernel panic on a B70), so a load that
does not fit fails cleanly instead. For the encoders it brings nothing
measurable and costs a re-gate of every space (invariant 12). Our live
series rebases with context refreshes only: P5 and P10 apply as they are,
P3/P4/P6 need their line offsets refreshed, P7 is upstream now and is
dropped, P11 applies in its upstream form. The staged P8/P9 (encoder
memory and store) conflict and need real work — they are not live.

## 1. The two trees

| | ours today | LocalAI v5.0.1 |
|---|---|---|
| LocalAI | master 2026-11-02, `16acc89` (the node runs the v4.12.1 app; our backend image is built from this) | v5.0.1, 2026-11-20 (`dd5780a`); v5.0.0 on 11-18 |
| llama.cpp | `ddf48f8`, 2026-10-31 | `78e0529`, 2026-11-18 (master `29b52d3` of 11-20 is two days further; nothing in those days touches us but a MoE-cache crash fix, #32636) |
| llama.cpp version | 0.x | **1.0.0** (bump 11-13; `ggml : prune` #29801 and the removal of DIAG_MASK ops #31946 are API cuts LocalAI 5.0.1's `grpc-server.cpp` already compiles against — the reason to take LocalAI's pin, not master) |

## 2. What changes, per model

| change | PR, merged | effect | applies to |
|---|---|---|---|
| **SYCL device-side MoE scheduler** — the MMID routing and tile schedule built on the GPU, no expert-id readback and CPU sort; grouped XMX GEMM | #30156, 11-10 | Qwen3-30B-A3B Q4_K_M, one B60: prompt 128 tok PP 313 → 738 tok/s (+136 %), TTFT 410 → 174 ms; 2048 tok PP 1244 → 1530 (+23 %), TTFT 1647 → 1340 ms; TG 65.3 → 62.3 (−4.6 %) at 128, flat at 2048. On an Arc Pro B70 (a user, another MoE, IQ1_M): pp512 +5.6 %, pp2048 +7.1 %, tg flat. Also fixes `-ncmoe` prompt processing (the experts are uploaded into a compute-buffer copy). `GGML_SYCL_MMID_SCHED` (default 1; bit 2 lifts the width heuristic) | the 35B (its Ask prompts are ~2k tokens: TTFT); nothing dense |
| **MoE expert cache completed** — ClassLRU replaces SLRU (#31233); `--moe-cache-cpu-share F`, misses computed on the CPU, a tuning script (#30966); CRF miss pick (#31853); the CPU part no longer waits (#31888); no cache for the draft (#31497) | 11-06…11-08 | ClassLRU: decode +6–7 %, 8 % fewer uploads, vs SLRU. Community, Qwen3.6-35B-A3B Q6_K_XL: RTX 2080 Ti 11 GiB 42.1 → 66.7 tok/s; RTX 5060 Ti 16 GiB 44.2 → 73.7 (model-extracted, CUDA). Gemma 4 26B-A4B on a 5090: 86.9 → 93.2. **SYCL named nowhere.** Our pin has `--moe-cache-mib` with the SLRU cache only; LocalAI exposes `moe_cache_mib` and `moe_cache_cpu_share` at both bases | the 35B: ≈ 3.4 GiB non-expert weights + 1.2 context + the cache → **≈ 9 GiB with a 4 GiB cache** (E) against 21.6 M; the experts' 18.4 GB in host RAM (22 GB available on 11-18) |
| **BMG VRAM check** — a model or split buffer is refused (`nullptr`) when it does not fit the device's free memory or would push llama.cpp's total past the device's; `GGML_SYCL_BMG_VRAM_CHECK=0` restores the old behaviour | #31285, 11-07 | the report: a kernel panic on a B70 loading 45,469 MiB onto 32,656. Before, only `max_mem_alloc_size` was checked and the driver moved the overflow to host RAM (our 2026-09-20 incident, M16). KV and compute buffers are not named (inferred: not covered) | every llama.cpp model: **a load onto a full card FAILS instead of spilling** (§3) |
| MTP drafting fixes — save/restore the MTP hidden state (#30141), conv snapshots (#31660), target states read from a device buffer (#31927), `--fit` counts NextN layers (#31569) | 11-03…11-09 | correctness of `--spec-type draft-mtp`, which our pin has but we never ran (the 35B YAML: "no drafter"). The 35B has an MTP head (`mtp_num_hidden_layers 1`; unsloth's `-MTP-GGUF`). Gain = the acceptance rate's, unmeasured anywhere we read | the 35B, if MTP is tried |
| SYCL FA: a mask broadcast over heads or batches fixed in the MKL path | #31536, 11-17 | multi-sequence attention on the oneMKL path (ours: `GGML_SYCL_ENABLE_DNN=0`). Possibly the S1 family (the F16 KQ product wrong with ≥ 2 sequences, worked around by the f32 KV cache, +9 % time, ~1 GB): to measure whether the workaround can go | MADLAD, chat |
| SYCL: exact rounding in CPY to Q4_1/Q5_1/Q8_0 (#31465); fast division in bin-bcast kernels (#32213); CLAMP on strided views (#31784); device-id cache (#31126, #32013) | 11-07…11-17 | bytes MAY change (quantised KV cache writes; element-wise ops): the gate decides per space | every model; the encoders' re-gate |
| SYCL AOT: garbage output for models > 4 GB in AOT builds, fixed by a CMake buffer-size flag | #31873, 11-12 | titled for `arl-h`; "in AOT case" generally (inferred). Our AOT `bmg-g31` variant failed to run at all on 2026-11-08 (approved-encoders.md "Known limits": `No kernel named …` under libsycl 8) | the AOT variant, if retried |
| `sycl: dmmv code cleanup` | #31325, 11-02 | = our P7, carried ahead of the pin | drop 0007 from the series |
| oneDNN FA cache thread-safe (#31138); oneDNN SDPA un-gated on Alchemist (#31574); fast `comm_exchange` for `-sm tensor` (+19 % PP on B70s, #30956); YaRN truncation on sycl (#30802) | 11-02…11-12 | oneDNN is off here (invariant 12); multi-GPU is not ours | none |
| CUDA/HIP/Metal/Vulkan MoE work (fused gate/up MMQ #31188: Qwen3.6-35B-A3B PP +6 % on a 5090; few-row MMQ for MUL_MAT_ID #32065; …) | 10-25…11-20 | other backends | none |

Not changed by the update: the spill's physics (idle-context-brief.md §6),
dense decode, the encoders' speed.

## 3. Behaviour changes to handle before the first load

1. **The VRAM check.** Today's set at rest is 32,367 of 32,656 MiB (M,
   11-18): the next load onto that card would be REFUSED, where today the
   driver spills it. LocalAI's reclaimer acts after the fact (usage above
   0.95); app/resident.py's budget (`fits under threshold × total − used`)
   is the check that must hold before every load, and a refused load must
   read as a DEFERRAL in the worker, never an error on a document
   (CLAUDE.md). The env `GGML_SYCL_BMG_VRAM_CHECK=0` in a model's YAML
   keeps the spill for that model, measured first (M14/M16 are the
   reasons not to).
2. **`--fit` is ON by default** in LocalAI's backend — at our live base
   (`16acc89`, `params.fit_params = true`, target margin 1 GB, min ctx
   4096) as at 5.0.1. It adjusts only UNSET arguments; our YAMLs set
   `gpu_layers: 999` and `context_size`, so it adjusts nothing for them —
   a YAML that leaves either unset gets whatever fits at load, silently.
   Set `fit: off` (LocalAI option `fit` / `fit_params`) in every encoder
   and reranker YAML, invariant 12: a model's placement must not depend on
   what else was on the card when it loaded.
3. `GGML_OP_OFFLOAD_MIN_BATCH` (at both trees): only matters if a model
   runs with layers in host RAM (`-ngl N`), which none of ours does.
4. `#32636` (11-20, after 5.0.1's pin): the MoE cache with a quantised KV
   cache aborts in `ggml_new_tensor`; we run f32 KV, and the 35B would
   be the only cache user — take the fix when LocalAI bumps, or carry it.

## 4. Our patches on LocalAI 5.0.1's trees (dry-run 2026-11-21, `git apply`, `patch -p1 --dry-run`)

| patch (series) | what | on `78e0529` / LocalAI `dd5780a` | work |
|---|---|---|---|
| 0001 system list load options (LocalAI, Go) | P1 | applies | none |
| 0002 embed token ids (`grpc-server.cpp`) | P2 | applies | none |
| 0003 encoder-decoder (`server-context.cpp`) | P3 | applies | none |
| 0004 encoder-decoder waves | P4 | `git apply` refuses (offsets), `patch` applies all 10 hunks | refresh the context lines |
| 0005 sycl batched mul_mat src1 strides | P5 | applies as it is — **the upstream bug is still there** | none; the `test-backend-ops` case re-run on the card |
| 0006 chat Cohere Command 3 parser | P6 | code applies; the `tests/test-chat.cpp` hunk needs its offsets | refresh one test hunk |
| 0007 sycl dmmv cleanup | P7 | upstream (#31325): applying it is a no-op | **drop** |
| staged 0008 nemotron rerank (llama.cpp `0010`) | P10 | applies | none |
| staged 0009 context on demand (LocalAI side) | P11 | 17 of 18 hunks; hunk 6 in `grpc-server.cpp` fails | refresh one hunk |
| staged 0009's llama.cpp half (`0011`) | P11 | 50 of 52 hunks in our staged form; **the upstream-form diffs 1–3 (`deploy/localai/upstream/llama-cpp-context-on-demand/`, cut against `b8abe41`) apply cleanly** | use the upstream form |
| staged 0006/0007 encoder-decoder memory and store | P8, P9 | conflicts in `llama-context.cpp`, `llama-graph.cpp/.h`, `tests/CMakeLists.txt` (they conflict on the OLD pin too: they predate the last rebase of the series) | real rebase; not live, not needed for the update |

So the live series moves with context refreshes and one deletion; no
semantic conflict was found. Verification is the series' own: P3's 379/379
MADLAD check and the 616/626 in-wave figure, P5's `test-backend-ops` case,
P6's chat tests, P2's `embedding_tokens_test`.

## 5. What it costs

- **Every space is re-gated** (invariant 12: a vector is written only from
  a validated run; an engine change is a new run): sixteen fingerprints,
  two orders, three fresh loads, the batch bound — per encoder and
  reranker, ~10 models; minutes each on an idle card, in a window the
  operator grants. New kernels (#31465, #32213) may move an `exact` run to
  `near-exact` or fail it: the gate says which, and the stored CPU
  reference does not change.
- MADLAD's P3/P4 and the S1 workaround re-verified; the f32 KV cache kept
  until #31536 is measured against the F16 cache.
- A build (`build.sh` on the new base, the series refreshed) and the
  stage's package (`deploy/vega/stage/build_vega_package.sh`).
- The resident set must be made to fit BEFORE loads (§3.1), or the first
  refused load after the deploy is the first thing the operator sees.

## 6. Recommendation

| choice | pros | cons |
|---|---|---|
| **A. Move the series to LocalAI 5.0.1's base (`78e0529`) now** | the 35B's prefill and its cache; the spill closed by default; P7 dropped; the LocalAI 5.0.1 add-ons session gets one backend, not two | a re-gate of every space; MADLAD re-verified; the resident budget must hold before every load; the MoE cache on SYCL is unmeasured until we measure it |
| B. Stay on `ddf48f8` | nothing to re-gate; the spill keeps carrying the full card | the 35B keeps 21.6 GiB and today's prefill; the silent spill stays, with its panic/OOM history; P7 carried; two bases once LocalAI 5.0.1 is adopted |
| C. Move the CHAT model alone to a separate backend on `78e0529` (the P10 precedent: a staged patch on its own backend, live 2026-11-11) | the 35B's gains and the cache measured now, no encoder re-gated | two engines to build and watch; the spill stays open for the encoders' engine; LocalAI 5.0.1's grpc-server split between two llama.cpp versions |

Recommendation: **C first, A after** — C measures the two things that
decide A (the MoE cache on SYCL, the scheduler's gain on the B70) without
touching a single space, and the 35B is the one model that gains. A
follows in a granted window, with the encoders' YAMLs carrying `fit: off`
and the budget check in front of every load. If the operator says
nothing, nothing is built: the node stays on `ddf48f8`.

## 7. Sources

Read 2026-11-21: ggml-org/llama.cpp master `29b52d3` and `ddf48f8`,
`78e0529` (shallow clones; `common/arg.cpp`, `tools/server/README.md`,
`ggml/src/ggml-sycl/*`, `docs/backend/SYCL.md`, `docs/moe-cache.md`,
`docs/speculative.md`); PRs #30156, #30966, #31233, #31888, #31285,
#31873, #31188, #32636 and the merged-PR lists for moe / expert /
mul_mat_id / mtp / speculative / sycl (Oct 24 – Nov 21); mudler/LocalAI
releases v5.0.0 and v5.0.1, `backend/cpp/llama-cpp/` at v5.0.1 and at our
base `16acc89`. Our patches: `deploy/localai/patches/`, their inner
llama.cpp diffs extracted and applied with `git apply --3way` and
`patch -p1 --dry-run` on the three trees.

## 8. The operator chose A (2026-11-21) — done on the repository side

- **The series** (`deploy/localai/patches/series`): base LocalAI v5.0.1
  `dd5780aa904d132c784867d37fc713df8d984c8a` (llama.cpp `78e0529`). P1, P2
  unchanged; P3–P6 regenerated on `78e0529` with LocalAI's own
  `0001-add-server-task-type-score.patch` applied first, the way
  `prepare.sh` applies `patches/` (`ls` order, `patch -p1`): added and
  removed lines identical, context and offsets refreshed, every hunk exact
  on a fresh tree; `git am` of the six succeeds on a fresh v5.0.1. P7
  deleted (in the base). Each regenerated patch's message carries the
  rebase line. `tests/test_localai_patches.py` holds series, files and
  localai-bugs.md to each other.
- **`fit:off`** in the options of every llama.cpp-backed encoder, reranker
  and MADLAD YAML (13 files, `deploy/vega/stage/models/`): LocalAI's backend
  defaults `fit_params` to true at both bases. The chat YAMLs set layers
  and context and are left alone.
- **A refused load is already a deferral**: `app/embeddings.py` treats any
  endpoint answer that is not an input refusal as the endpoint's — busy or
  wrong — never the document's (CLAUDE.md), so the BMG VRAM check's
  `nullptr` → LocalAI's load error → `EndpointDeferred`. No app change.

### For München, on the node, in a granted window

1. Build: `sh deploy/localai/llama-cpp/build.sh sycl_f16 <out>` from this
   branch — the tag is the new series digest (`NAME_ONLY=1` prints it). The
   build applies the series with `git am` and LocalAI builds llama.cpp
   `78e0529` with `patches/` through `prepare.sh`. Then the stage package
   (`deploy/vega/stage/build_vega_package.sh`) as for every engine.
2. The series' checks on the new engine, before any model serves: P2's
   `embedding_tokens_test`; P3/P4: MADLAD 379/379 and in-wave equal to
   alone (the `0003`/`0004` entries of localai-bugs.md); P5:
   `test-backend-ops -o MUL_MAT -p "type_a=f16,type_b=f32"` 0 FAIL on the
   card, and the KQ shapes; P6: `test-chat`.
3. Room BEFORE every load: the resident budget must hold (`louise node
   resident show`), since a buffer that does not fit is now refused, not
   spilled; keep `GGML_SYCL_BMG_VRAM_CHECK` at its default 1. The first
   loads after the deploy: with the card emptied (node paused), one model
   at a time, each read from the driver.
4. Re-gate every space (invariant 12): the gate runs per encoder and
   reranker on first use; expect bytes to move for some (#31465, #32213) —
   the gate says `exact` / `near-exact` / fail, nothing is forced.
5. Measure, then record (docs/notes/measuring.md §6): the 35B on the new
   engine (prefill and decode at 128 and 2048 prompt tokens, against the
   11-03 figures 606.5 / 92.2 tok/s), then the 35B under the MoE cache
   (`--cpu-moe`, `moe_cache_mib` 4096 and 8192, `moe_cache_cpu_share` 0 and
   0.5): footprint, tok/s, hit rate (`scripts/moe-cache-tune.py`), host RAM
   — the row that decides whether the wanted set fits. Then
   `GGML_SYCL_MMID_SCHED=0` once, to see the scheduler's share.
6. MADLAD's f32 KV cache: one run of the F16 cache's S1 case on the new
   engine (#31536) — kept at f32 unless it passes.
7. The spill's speed, if the operator wants it measured: one timed 35B
   switch with `GGML_SYCL_BMG_VRAM_CHECK=0` on that model alone and
   `GGML_SYCL_MEMTRACE=1`, the card otherwise idle; then back to 1.
