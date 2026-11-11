# Brief for a dedicated session: an idle encoder gives its context back

*Written 2026-11-12 for a session of its own (operator: "Write down
everything for a dedicated session, collect all the information needed
including all the material about B70 pitfalls and our localai and
llama.cpp customizations"). Read it whole before touching anything; then
CLAUDE.md, docs/notes/measuring.md and the notes it links.*

## 0. In one paragraph

The B70 (32.6 GB) holds ~34 GB of resident models, ~10 GB of it CONTEXT —
each llama.cpp model's KV cache plus its compute buffers — while at most
3.3 GB of context works at any one second (p95 2.2 GB; 24 h of the card
record). The idea, the operator's (2026-11-12), checked against the
literature (shared-context.md: Prism, OSDI '26, and kvcached do it with GPU
virtual memory for vLLM/SGLang on NVIDIA/ROCm, KV only): a CORPUS ENCODER
KEEPS ITS WEIGHTS AND FREES ITS `llama_context` AFTER N SECONDS IDLE; THE
NEXT BATCH CREATES IT AGAIN. The card's free memory is the common pool;
two encoders that overlap (nemotron + medcpt, 21.7 % of seconds) each still
get theirs. **Nothing is built yet.** This session: (1) measure the two
unknowns, (2) only if they pass, write the patch, (3) gate, (4) deploy on
the operator's word.

## 1. What decides whether it pays — measure FIRST

| unknown | why it decides | how |
|---|---|---|
| Does freed context memory go back to the DRIVER on the B70? | ggml-sycl's op-temporaries pool is a VMM pool, ON by default at our pin (`GGML_SYCL_ENABLE_VMM`, default 1, `ggml_sycl_pool_vmm`: it keeps committed pages for its lifetime), else the legacy pool, which caches up to 256 freed buffers; both release only when their owner is destroyed. If the owner outlives the `llama_context` (to read: `ggml-sycl/backend.hpp`, the pool's owner), freeing the context returns nothing. | per process: the driver's count (the xe fdinfo app/gpu_memory.py reads; LocalAI's `/system` `size_vram`) before, during and after a free; the backend's startup lines `GGML_SYCL_SUPPORT_VMM` / `GGML_SYCL_ENABLE_VMM` say which pool runs |
| How long is a context's create + free, per encoder? | it is paid by the first batch after every idle spell | the same instrument, timed per encoder at its live settings (ctx, batch, ubatch, flash attention, cache types) |
| Are the vectors byte-identical across create/free cycles? | invariant 12: a run reproduces itself | fixed texts embedded before, between and after cycles; `tools/vega_repro_check.py` on the patched backend later |

Acceptance (proposed; the operator may set others): ≥ 90 % of the
context's bytes back to the driver after a free; create ≤ 5 % of one
batch's time for that encoder (or under 250 ms, whichever is larger);
every vector byte-identical across 10 cycles. If the memory does not come
back, the patch is not built — record it here and in shared-context.md.

**The instrument** (none exists): a diagnostic llama.cpp program, or a
`deploy/localai/patches/diag/` patch (diag/README.md: never in `series`,
never on a live backend), built on BUILD_HOST with `build.sh sycl_f16` from
a local copy of `series`: load the model once, then loop create context →
embed the fixed texts → free → read the driver's count. A TEST COPY of an
encoder under its own model name, in a quiet window, run by München with
the node's harness (`/root/llama-intel-arc-b70`, measuring.md §1); nothing
in production touched. Measure the DEPLOYED settings (measuring.md §3).

## 2. Where the context lives — the code

- **LocalAI's llama.cpp backend** (`backend/cpp/llama-cpp/grpc-server.cpp`
  at our base, LocalAI master `16acc89`): it compiles llama.cpp's own
  `tools/server/server-task.cpp`, `server-queue.cpp`, `server-context.cpp`
  into its translation unit; `params_parse` turns `ModelOptions` into
  `common_params` (`n_ctx`, the `Options` strings `ubatch:`, `parallel:`,
  `kv_unified` — `params.kv_unified = true` by default there —, cache types
  from their own proto fields; `params.cache_idle_slots = true`); the
  server loop is `ctx_server.start_loop()`.
- **llama.cpp `ddf48f8`, `tools/server/server-context.cpp`**: `load_model`
  builds `llama_init = common_init_from_params(params_base)` (model AND
  context together), then `ctx_tgt = llama_init->context()`, the slots
  (`slot.mem.init(ctx_tgt, …)`) and the batch (`batch.init(ctx_tgt, …)`).
  UPSTREAM ALREADY HAS A SLEEPING STATE: `handle_sleeping_state` →
  `destroy()` (`llama_init.reset()`: model AND context) and, on wake,
  `load_model(params_base)` again; driven by `queue_tasks.on_sleeping_state`
  (llama-server's `--sleep-idle-seconds`). That frees the WEIGHTS too —
  what LocalAI's idle unload did, switched off for its cold starts (#398).
  The patch to write is that state with the model kept: free the context,
  the slots' memory and the batch; on wake, `llama_init_from_model(model,
  cparams)` and the slot/batch init again. Whether LocalAI's grpc-server
  wires the idle timer at all is to be read (not visible in the first
  100 kB of the file).
- **Who it is for**: corpus encoders only (vega-qwen3-embedding-4b, 3.3 GB
  of context, worked 31 s in 24 h; vega-nemotron-3-embed-1b 2.2 GB).
  pubmed's medcpt-article encoder is OpenVINO (`vega-louise-openvino`), not
  this backend; whether it runs on the card or the CPU is the live YAML's
  (`vega-medcpt-article-ov` vs `-ov-cpu`, deploy/vega/stage/models/) — to
  read: if the CPU, its 21.7 % overlap with nemotron is no overlap on the
  card at all. NOT the query path (rerankers, query encoders, chat, MADLAD):
  a person's first query never waits for a context (app/resident.py's tiers).
  An Options flag in the model YAML, off by default (e.g.
  `context_idle_s:600`), so each model opts in and the flag is in the
  load's stated options (the run, 09 §9.12).

## 3. Our LocalAI and llama.cpp customizations (the series)

Base: LocalAI master `16acc89` (llama.cpp `ddf48f8`) since 2026-11-02, the
ONE llama.cpp engine of every model (operator's A). Each patch is described
with its upstream status in docs/notes/localai-bugs.md "Patches we carry";
`tests/test_localai_patches.py` holds the series, the files and that
section to each other. A LocalAI patch that touches llama.cpp ADDS a file
under `backend/cpp/llama-cpp/patches/` which the backend's `prepare.sh`
applies to llama.cpp — the new patch follows that form.

| | what | file |
|---|---|---|
| P1 | `GET /system` lists each loaded model's load options | 0001-system-list-load-options.patch |
| P2 | `Embedding` embeds the token ids it is given (VeGa's token cut) | 0002-llama-cpp-embed-token-ids.patch |
| P3 | the encoder of encoder-decoder models runs (MADLAD/T5) | 0003-llama-cpp-encoder-decoder.patch |
| P4 | encoder-decoder slots encoded in waves | 0004-llama-cpp-encoder-decoder-waves.patch |
| P5 | ggml-sycl batched F16 matmul converts src1 for the GEMM that runs (the KQ fault, upstream-bugs.md S1) | 0005-llama-cpp-sycl-batched-mul-mat-src1.patch |
| P6 | Command R7B / Command A tool calls parse | 0006-llama-cpp-chat-cohere-command3.patch |
| P7 | upstream's dmmv cleanup, carried ahead of the pin | 0007-llama-cpp-sycl-dmmv-cleanup.patch |
| staged P8, P9 | encoder output per sequence; on the device (not rebased onto `16acc89`: 4 rejected hunks) | patches/staged/0006-, 0007- (staged/README.md) |
| staged P10 | Nemotron rerank-1b-v2 (#487), for a separate backend | patches/staged/0008-llama-cpp-nemotron-rerank.patch, nemotron-rerank.series |
| diag | `GGML_SYCL_CPU_OPS` bisection; `test-backend-ops` KQ shapes | patches/diag/ (README.md) |

The new patch is P11 (P1–P7 in the series, P8–P10 staged); it goes to
`staged/` first (built and tested on the CPU), then up into `series` after
its card gates (staged/README.md).

**The build** — `sh deploy/localai/llama-cpp/build.sh sycl_f16 <out>`
(LocalAI's own Dockerfile and CI arguments, the series applied with
`git am`); `INTEL_STACK=deploy/localai/llama-cpp/intel-stack-2026-11c.conf`
is the LIVE runtime ("11c", `STACK_ON=prebuilt`: LocalAI's prebuilt grpc
builder, oneAPI 2025.3 / libsycl 8, Level Zero loader 1.27, with compute
runtime 26.40.40196.8 + IGC 2.42.6 + gmmlib 22.10.2 layered on);
`SYCL_DEVICE_ARCH=bmg-g31` (AOT) finds no kernel image under libsycl 8 —
the stack is for JIT. Builds run on BUILD_HOST (ssd3/louise-build, #380).

**The engine's identity** — a Codex's `space.encoder.engine.image` is the
backend's file tree AND the VeGa wrapper package (`vega_wrapper.
engine_manifest`, since 2026-10-31): ANY rebuilt backend or changed wrapper
is a NEW ENGINE, so every model on it is a new run, gated once (docs/03
§3.3) before it writes. An engine swap archives the old tree and package
and proves the pair against the Codex (approved-encoders.md: "A Codex's
engine image is the backend's tree AND the wrapper package"; wikidata's
32b0caf3 = tree 0e705989 + package 601c849e). Staging and rollout scripts:
`deploy/vega/stage/` (build_vega_package.sh, stage_src.sh,
build_louise_backend.sh), `deploy/vega/switch/`.

**Bundle with it**: #502's wrapper side (inputs computed in more than one
pass, `kind: passes`, vega.md §2) waits for the next engine change that
re-gates every space — this one, if it reaches deployment.

**The VeGa wrapper's determinism contract** (deploy/vega/vega_wrapper.py
`DETERMINISM`, invariant 12): a SYCL card run of an embedder needs
`GGML_SYCL_ENABLE_DNN=0` and `GGML_SYCL_FA_ONEDNN=0` in the model's env and
`parallel:1`, or the wrapper states no run; `near_exact_relaxed` /
`LOUISE_NEAR_EXACT_MEASURED` is the one measured exception
(near-exact-runs.md).

## 4. B70 and stack pitfalls

"AE" = approved-encoders.md. Measured facts first, then what the notes call
probable or open.

| # | symptom | cause (as established) | remedy in force | where |
|---|---|---|---|---|
| M1 | same input, different bytes run to run | oneDNN picks GEMM kernels by heuristic | `GGML_SYCL_ENABLE_DNN=0` | AE "Known limits of the Arc Pro B70"; AE "Bitwise reproducibility, by vendor" |
| M2 | flash attention ON: a vector flips across fresh loads, a NaN, a vector 0.0865 off (qwen3-4b, 4 of 8 runs) | oneDNN's fused SDPA (`GGML_SYCL_FA_ONEDNN`, default 1), which DNN=0 does not cover | `GGML_SYCL_FA_ONEDNN=0`, the wrapper's contract | AE by-vendor rows (FA on / FA on + FA_ONEDNN=0); near-exact-runs.md; STATUS 2026-10-31 |
| M3 | FA_ONEDNN=1 exact across loads but another function (cosine 0.99952) | — | off for embedders (chat only, operator's C) | AE "oneDNN switched on, measured" |
| M4 | FA off: bit-exact but WRONG (Qwen3 cosine 0.126; MADLAD wrong text) | `ggml_sycl_mul_mat_batched_sycl` flat-copies src1 for oneDNN, oneMKL then reads it contiguous under DNN=0 | P5; MADLAD on an f32 KV cache | localai-bugs.md P5; upstream-bugs.md S1 |
| M5 | reranker at ctx 16,384: 1 of 20 scores differs on the first pass after a load | the oversized context (mechanism not isolated) | context = the platform window | AE "Known limits" |
| M6 | `parallel:4`: request order moves 16 of 20 scores | sequences batched together change each other's sums | `parallel:1`; the wrapper refuses a load without it | AE; vega_wrapper.py `determinism_lacks` |
| M7 | a batch of 16 long texts at cosine 0.93–0.98 | upstream bug on c0fd786, fixed by ddf48f8 | the gate's `LONG_PROBE` | AE "Every live space passes on 16acc89" |
| M8 | wrong vectors that repeat exactly; a same-card re-embed and the gate agree with them | torch's fp32 softmax (torch, not llama.cpp) | SDPA; only ANOTHER device catches this class | gpu-silent-wrong-vectors.md §1–2 |
| M9 | one NaN fails the whole `/v1/embeddings` with 500 | Go's JSON cannot encode NaN; intermittent from the backend | `embeddings._numeric_failure` retries | localai-bugs.md B9 |
| M10 | the reclaimer evicts one model every 500 ms while another process holds the card | `checkMemory` uses the device-wide `UsagePercent` | threshold 0.95 | localai-bugs.md B4 |
| M11 | a model still LOADING is evicted first; its request gets 500 | `lastUsed` is zero at registration | the preload budget and back-off (app/resident.py) | localai-bugs.md B11 |
| M12 | a backend whose eviction failed keeps running, uncounted, never evicted | untracked before the stop; a graceful stop waits 30 s then `ErrModelBusy` | none upstream | localai-bugs.md B3 |
| M13 | a wedged backend is logged "Deleting…" and stays alive | deleted only if `!IsAlive()` | busy watchdog (10 min since 2026-10-12; 5 min also on record — read the live value) | localai-bugs.md B10; STATUS |
| M14 | all 11 models preloaded (28.4 GB at rest); compute-buffer growth crossed 0.95; chat cold starts of 11–15 s | the room check counted the set at rest, without its working memory | budget = threshold × total − growth margin − AIMD penalty | STATUS "2026-11-08: the resident set preloaded within a measured budget" |
| M15 | space claimed, nothing to evict | `evictLRUModel` skips pinned models | preload, never pin; only a running embed batch pins | STATUS 2026-11-08 |
| M16 | the card overcommitted from outside: `xe` silently moves every model's buffers to host RAM; OOM kills; `xe` engine reset / `DEVICE_LOST` | a process outside the pool | card work through LocalAI, or only on a free card | STATUS 2026-09-20 afternoon; gazzetta-plugin.md |
| M17 | a YAML edit + `/backend/shutdown` reloads the OLD config | YAML is read at LocalAI's start | restart LocalAI | AE |
| M18 | a model loaded before an in-place backend rebuild keeps its old process; a hand-placed backend reads "not found" until restart | `/models/reload` stops nothing; `RegisterBackends` runs only at start | `/backend/shutdown` each model; the first gate must name a NEW engine and run id | measuring.md §9; vega.md §10 finding 2 |
| M19 | a measurement ran on a retired engine | a stale install left in place | `deploy/vega/retired_backends.py --engine` | measuring.md §6; AE "Retiring an engine" |
| M20 | a repeat measures the slot prompt cache (1.26e-3 off), not determinism | single-slot cache | no repeats served from it | measuring.md §3 |
| M21 | Level Zero loader ≥ 1.32: `ZE_RESULT_ERROR_UNSUPPORTED_VERSION`, falls to OpenCL, the load dies | the loader's refusal (the 6.18 kernel's part not proven) | loader 1.27; only the runtime updated (intel-stack-2026-11c) | AE "Known limits" |
| M22 | oneAPI 2026.2's `libsycl.so.9` segfaults at load, then an `xe` engine reset | — | oneAPI 2025.3 | AE "Known limits" |
| M23 | AOT bmg-g31: "No kernel named …" | no device image matched; AOT has no SPIR-V fallback | JIT | AE "Known limits" |
| M24 | the driver stated is not the one loaded | the backend tar bundles its own `libze_intel_gpu` (`ZE_ENABLE_ALT_DRIVERS`) | the driver read from the backend's `/proc` environ | vega.md §9, §10 finding 4 |
| M25 | `/system`'s pid and CPU % are the wrapper's (36 MB), not the llama.cpp child's | — | follow the descendants; "idle" = `encoder_use` + leases | vega.md §10 finding 5; scheduling.md §5 pitfall 17 |
| M26 | idle unload: 19 s cold starts, 12 a day | idle read off a signal the work does not move | the encoder lease (600 s TTL); `encoder_use` | scheduling.md §5 pitfall 17 |
| M27 | the idle watchdog unloaded the chat after 5 min | `watchdog_idle` | `watchdog_idle_enabled: false` | deploy/vega/stage/localai_settings.py |

**Probable or open:**
- **O1** `GGML_SYCL_ENABLE_MKL_FA` (default 1; GQA ≥ 2, KV ≥ 1024, oneMKL GEMMs): "next in line", never measured (AE "Arithmetic order").
- **O2** the ggml-sycl op pool: VMM, ON by default at `ddf48f8`, keeps committed pages for its life; legacy pool caches 256 buffers; both freed only with their owner. Whether a context's free returns memory on the B70 is UNMEASURED — §1's first question.
- **O3** create/free time per encoder: unmeasured (§1).
- **O4** THE GATE'S "FRESH LOADS" ARE PROCESS RELOADS (`/backend/shutdown`): they do not exercise a context re-created inside a live process. The new gate must cycle contexts in-process too (§6 step 5).
- **O5** B4's second effect: no wait for the driver to report freed memory, so a second eviction can follow (Ollama waits up to 5 s) — code reading only.
- **O6** hangs of unknown cause (`xe`, Level Zero with two runtimes on one card, or OpenVINO): STATUS ~1672–1700.
- **O7** AE "Bugs" table: the occasional non-finite vector; the gutenberg batching incident (`EMBED_BATCH=1`).
- **O8** upstream reports not seen here: OPT reorder corruption (#21893), XMX NaN (#30892), FA corruption (#19276, #30624), device-lost storms (#966, `SYCL_UR_USE_LEVEL_ZERO_V2=0`), CR 26.27 with loader < 1.32 segfaults (#972) — AE "Known limits", last row.
- **O9** S1's KQV case fails in the suite while the model's V side decodes right; not isolated (upstream-bugs.md S1).

**The notes disagree** (settle before relying on either): AE "Known limits" says the reclaimer is "pinned off", while localai_settings.py and STATUS 2026-11-08 have it ON at 0.95 (the live value is the settings file's); the busy watchdog is recorded as 5 and as 10 min.

## 5. GPU memory on this node — what the patch meets

- **The card**: 32,656 MiB (also written 32.6 GiB / 32.7 GB). Everything
  resident 2026-11-07: 30,267 MiB (92.7 %), host RAM peak 41.6 of 63.4 GB.
  2026-11-12: ~34 GB "resident" > the card — the driver keeps what does not
  fit in host RAM (below).
- **LocalAI's reclaimer**: ON at 0.95 (≈ 31.0 GB) since 2026-11-08 (0.68,
  then 0.85 on 2026-10-23); it evicts the least recently used unpinned
  model (B4, B11 above). Idle watchdog OFF; busy watchdog ON.
- **`size_vram`** (LocalAI `/system`): the driver's per-process count —
  a model's grown compute buffers included. app/resident.py keeps per model
  the fewest bytes seen (AT REST) and the most (LARGEST): the budget sums the
  at-rest sizes, the room check reads the largest. With idle release the
  at-rest size of an opted-in encoder falls to its weights + heap: the
  budget then admits more, and the growth margin (below) must carry the
  contexts that come back — check that it does before the switch.
- **The growth margin**: the card's count minus the at-rest sizes, each
  beat; its peak over 6 h; 3.0 GiB until measured (the night it was set:
  ≈ 28.4 GB at rest, 31.36 GB read, ≈ 2.96 GB grown). The AIMD penalty adds
  a model's at-rest size per eviction within 10 min of its load, halved
  after 2 h without one. `state/resident-budget.json`; `louise node resident
  show`.
- **"In host RAM"**: the driver's GPU buffers placed in host memory
  (`drm-resident-gtt` against `vram0` in fdinfo), real memory, not RSS; an
  idle model pushed off the card stays there and moves back only when used
  and the card has room. The card's used memory comes from
  `DRM_IOCTL_XE_DEVICE_QUERY` (needs `CAP_PERFMON`; the GPU probe has it).
  Each process also pays a SYCL / Level Zero heap floor of ~270 MiB.
  STATUS 2026-09-20 afternoon.
- **The context share per model**, 2026-11-12 (`size_vram` − the weights
  file; the backends' load lines are not kept at LocalAI's info level):
  qwen3-4b 3,262 MiB (≈ 1.1 GiB KV, ≈ 2.1 GB compute buffers at ubatch
  8192), nemotron 2,221, the MADLAD test copy 2,007, dar-legal-it 632, chat
  633, omnivoice 489, medcpt-query 276, bge-m3 232, medcpt rerank 213, bge
  reranker ~0. The exact KV / compute split needs one reload per model with
  the backend's stderr captured (LocalAI's debug level).

## 6. The plan, step by step

1. **Read** (this brief; CLAUDE.md invariants 1, 2, 7, 12 and the
   engineering rules; measuring.md §6; approved-encoders.md "Bitwise
   reproducibility, by vendor"; localai-bugs.md "Patches we carry" and B3,
   B4, B10, B11; scheduling.md §6 if the round loop or the pools are
   touched; app/resident.py's docstring).
2. **Read the code paths** in §2 at the pinned commits and answer: who owns
   the ggml-sycl pools; does destroying a `llama_context` destroy its
   backends; does LocalAI's grpc-server wire upstream's sleeping state.
3. **Measure** (§1) with München on a test copy, in a quiet window; write
   the numbers here. Stop if the memory does not come back.
4. **Write the patch** (`staged/`), CPU tests first as P3/P4 did (a model
   embedded before/after a context cycle, byte-identical; the idle timer;
   a request that arrives while the context is being freed — upstream's
   own sleep has that race, see the DEV write-up linked in shared-context.md's
   sources), then `build.sh`.
5. **Card gates** on a test name: `tools/vega_repro_check.py` (bit-identical
   over fresh loads) AND a cycle check of its own — contexts freed and
   re-created inside one live process, the fixed texts byte-identical across
   them (the gate's fresh loads are process reloads, pitfall O4) — then `louise hold embedder
   check <collection>` (the calibration gate, multi-load); throughput with
   `louise dev schedule-report --bench`; card memory at rest and in use.
6. **Deploy on the operator's word**, bundled with #502's wrapper (one
   re-gate of every space on the engine); BEFORE/AFTER rows: card memory at
   rest, reclaimer evictions per hour, the first batch's latency after an
   idle spell, chunks/s per collection.
7. **Record**: localai-bugs.md (the new P entry), shared-context.md (the
   measured numbers), STATUS.

## 7. Rules that bind this work

- Deploys and engine switches only on the operator's word; München (the
  node's session) runs everything on the node. A test copy under its own
  model name never touches a live space.
- "Fix it after" is never an option; a known defect is fixed before
  anything proceeds (CLAUDE.md).
- A new engine writes no vector before its run passes the gate (invariant
  1); every space on it is re-gated once; bundle engine changes to pay one
  re-gate (RULE 1).
- Measure before proposing; a scheduling or memory change whose commit has
  no numbers is a guess (CLAUDE.md, measuring.md).
- Every task ships tests; new behaviour without a test is unfinished.
- Questions to the operator: one shape (what it is; choices with pros and
  cons, the status quo included; the recommendation and what happens if
  they say nothing).
