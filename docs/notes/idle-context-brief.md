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
virtual memory for vLLM/SGLang on NVIDIA/ROCm, KV only): A MODEL KEEPS ITS
WEIGHTS AND FREES ITS `llama_context` AFTER N SECONDS IDLE; THE NEXT
REQUEST CREATES IT AGAIN. The card's free memory is the common pool; two
models that overlap each still get theirs. **Nothing is built yet.** This
session: (1) measure the two unknowns, (2) only if they pass, write the
patch, (3) gate, (4) deploy on the operator's word.

**A GENERAL DESIGN, NOT A LOUISE WORKAROUND** (operator, 2026-11-12:
"Embedders in CPU are an option but we cannot bet on it, this is a general
design improvement that can be beneficial for a number of different
scenarios other than Project Louise"). So: no argument here rests on where
one of OUR models happens to run (a CPU embedder, a model that idles today);
the feature is built for any llama.cpp server holding models it does not
use all the time — several models on one card, a desktop that shares the
GPU with other work, a server that answers in bursts — on every backend
(CUDA, ROCm, Vulkan, SYCL, Metal, CPU), every model kind (embedding,
rerank, generation, encoder-decoder), opt-in per model. It is written to
upstream's standards from the first line (llama.cpp's code style, its
server tests, `--help` text, docs/), so that proposing it to llama.cpp and
LocalAI is a filing, not a rewrite; whether and when it is filed is the
operator's word (as for P5's and the Nemotron rerank's PRs). OUR node is the
first deployment and its measurements are the first evidence, nothing more.

**The vocabulary is the standard one** (CLAUDE.md, "do not reinvent the
wheel"): vLLM's sleep mode has LEVELS — level 1 discards the KV cache and
keeps the weights (moved to CPU there), level 2 discards both; llama.cpp's
own `--sleep-idle-seconds` is today's level 2. The patch adds the level
that keeps the weights resident and frees the context (KV cache AND compute
buffers — more than kvcached frees): e.g. `--sleep-idle-seconds N
--sleep-level context|model` (default `model`, today's behaviour), the names
to be settled against upstream's conventions before filing.

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
back on the B70, that is a finding about ggml-sycl's pools (who owns them,
and whether a level-1 sleep must also destroy or trim the backend's pool),
not the end of the design: record it here and in shared-context.md, and
measure the same instrument on the CPU build (`build.sh cpu`, no card
needed) and on any other backend at hand — the design is for every
backend, and those numbers are part of the evidence an upstream proposal
carries.

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
- **Who it is for** — the DESIGN: any model, opted in. llama.cpp: the server
  flags above; LocalAI: a model YAML option passed through `params_parse`
  (e.g. `sleep_idle_s:600`, `sleep_level:context`), off by default, so each
  model opts in and the setting is in the load's stated options (for
  Louise: the run, 09 §9.12). Generation models free their slots' KV with
  the context: a conversation's cached prompt is recomputed after a sleep
  (upstream's sleep already does this; issue #29322 notes `--cache-ram`'s
  cache dropped on wake — the same holds here, and is documented, not
  hidden).
- **Who it is for** — OUR first deployment: the encoders whose context
  idles longest (vega-qwen3-embedding-4b, 1.9 GB of context after its
  trim, worked 31 s in 24 h; vega-nemotron-3-embed-1b 2.2 GB); the query
  path (rerankers, query encoders, chat, MADLAD) only once the wake time is
  measured small against a person's wait (app/resident.py's tiers keep them
  loaded today).

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
  8192; 1,882 MiB since its ubatch went to 2048 at 2026-11-12 06:52Z,
  `size_vram` 5,643 → 4,263), nemotron 2,221, the MADLAD test copy 2,007, dar-legal-it 632, chat
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
4. **Write the patch** (`staged/`) in upstream's form — llama.cpp's server
   tests (`tools/server/tests`, pytest) for the new sleep level, its `--help`
   and README text — CPU tests first as P3/P4 did (a model
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
7. **Upstream** (on the operator's word): an issue, then a PR to
   ggml-org/llama.cpp (the server's sleep level, its test, its docs), and to
   mudler/LocalAI (the YAML option through `params_parse`), with the
   measurements of step 3 and step 6 as the evidence; the patch leaves our
   series when upstream merges it (localai-bugs.md "Patches we carry").
8. **Record**: localai-bugs.md (the new P entry), shared-context.md (the
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

## 8. Review, 2026-11-11 — the code read at the pins, and what it changes

*A session without the node: nothing measured, nothing run on a card. Read:
llama.cpp `ddf48f8` and LocalAI `16acc89` (the pins), app/resident.py,
app/model_load.py, and upstream's issues and PRs (searched this day). Each
line names where it was read; "to measure" marks what reading cannot settle.*

**Verdict: sound, and worth the dedicated session — with the plan reordered
and four additions.** The mechanism is small (upstream's sleeping state with
the model kept), the largest feared unknown is answered by the code (R1), and
no upstream issue or PR proposes a weights-kept sleep (not a duplicate). The
gain on OUR node is smaller than §0's headline reads (R6), and the real work
is elsewhere than the brief puts it (R3, R4, R5).

### Step 2's three questions, answered

- **R1. Who owns the ggml-sycl pools? The backend instance — and a
  `llama_context` creates and frees its own.** `llama_context`'s constructor
  calls `ggml_backend_dev_init` per device (src/llama-context.cpp:432) and
  holds the results in `std::vector<ggml_backend_ptr> backends`
  (src/llama-context.h:396); the pools are members of that backend's
  `ggml_backend_sycl_context` (`pools[]`, `host_pools[]`,
  ggml-sycl/common.hpp:546–548, created on first use, :557);
  `ggml_backend_sycl_free` deletes it (ggml-sycl.cpp:6521), and the pool
  destructors give everything back: the VMM pool unmaps every commit and
  frees its virtual range (:2102–2116), the legacy pool frees its cached
  buffers (:1952–1977). The oneDNN engine and stream maps are members too.
  So O2's fear — an owner that outlives the context — does not hold at our
  pin: freeing the context destroys the op pool with it.
- **R2. KV and compute buffers go straight to Level Zero.** Where the build
  has `GGML_SYCL_SUPPORT_LEVEL_ZERO_API` and `GGML_SYCL_USE_LEVEL_ZERO_API`
  is not 0 (default 1, ggml-sycl.cpp:460), `ggml_sycl_malloc_device` /
  `ggml_sycl_free_device` are `zeMemAllocDevice` / `zeMemFree`
  (ggml-sycl/common.cpp:97–148): no SYCL USM pool sits between. What stays
  TO MEASURE is only the driver's side — whether compute-runtime and `xe`
  hand the pages back at the free or keep them for reuse — and what stays in
  the process whatever is freed (the queue, the JIT-compiled kernels, the
  ~270 MiB floor: `dpct::dev_mgr` is a process singleton,
  dpct/helper.hpp:995, so a wake should not compile again — to measure).
- **R3. LocalAI's backend reaches the idle timer today, and cannot survive
  it.** Any model option beginning with `-` is passed to llama-server's own
  parser (grpc-server.cpp:1302, `common_params_parse(…, LLAMA_EXAMPLE_SERVER)`
  :1505), and `ctx_server.start_loop()` (:266) starts the queue with
  `params_base.sleep_idle_seconds` (server-context.cpp:5203–5206): so
  `--sleep-idle-seconds` already arms the timer. But the wake is not wired:
  `wait_until_no_sleep()` is called only by upstream's HTTP layer
  (`server_res_generator`, server-context.cpp:5274–5280), and LocalAI's RPCs
  post through `get_response_reader()` (grpc-server.cpp:1647, 2604, 3205 …)
  without it — a task posted to a sleeping queue is never processed
  (server-queue.cpp:335–338 waits on `req_stop_sleeping` alone) — and they
  read `ctx_server.get_llama_context()`, null while asleep
  (server-context.h:79), at grpc-server.cpp:2593, 3196, 3750, 3832, 4570,
  4581, 4749. **The LocalAI half of P11 is therefore the wake call at the
  head of every RPC and an audit of those seven reads, not a YAML option
  alone**; `impl->vocab` and the chat templates stay valid with the model
  kept, which is an advantage of this level over upstream's.

### What the brief does not have

- **R4. A wake that cannot get its memory back is the NORMAL case of a
  shared pool, and upstream aborts on it**: `handle_sleeping_state(false)` →
  `GGML_ABORT("failed to reload model after sleeping")`
  (server-context.cpp:1161–1164). Prism and kvcached have a controller per
  card; here every process frees and takes alone, so two wakes can claim the
  same room. On the B70 the allocation does not fail — `xe` moves buffers to
  host RAM (M16) and the reclaimer evicts at 0.95 — elsewhere (CUDA) it
  fails. The design needs the rule written: a failed wake leaves the server
  asleep and REFUSES the request (503, retried), never aborts; for Louise
  that is a deferral, not an error (CLAUDE.md, "Deferral is not failure").
- **R5. app/resident.py reads a sleeping model as "partly off the card".**
  `REST_FLOOR = 0.5` (app/resident.py:261, app/model_load.py:159): a
  `size_vram` under half the largest seen is no at-rest reading (`_whole`,
  :718). qwen3-4b asleep holds ≈ 2.4 GB (4,263 − 1,882) against a largest of
  5,643 on file under the same name (0.42; 0.56 if the file was reset at the
  trim): discarded or accepted by an accident of ratio, per model. Accepted,
  the budget admits more preloads and the contexts' return is "growth" the
  margin learns only after it has peaked — M14 again; discarded, the saving
  is free card memory the budget does not spend. The second is the right
  FIRST deployment (the release is headroom, not budget), and it must be a
  rule, not a ratio: the keeper reads the sleep option from `/system` (P1
  lists load options) and counts an opted-in model at its AWAKE at-rest size.
- **R6. §0's "≈ 10 GB held, 3.3 GB working" is the figure for N = 0.** With
  an idle timer the held set is every context used in the last N seconds.
  By shared-context.md §2 nemotron runs 55.5 % of seconds and medcpt 22.6 %:
  at N = 600 both are probably awake most of the day, and the certain saving
  is qwen3-4b's 1.9 GB (31 s of work in 24 h), nemotron's 2.2 GB in part.
  **The card record already answers it, with no card time**
  (app/rage/card_record.py, per second): for N = 30, 60, 300, 600 s, the
  GB·hours each encoder would have given back over the same 24 h, and the
  wakes per day. That row belongs in §1 BEFORE the B70 measurement: it picks
  N and the models, and says whether the feature pays here at all.
  Also on the B70 the driver already moves what does not fit to host RAM
  (34 GB resident on 32.6), so the first ~1.4 GB released only ends that
  spill: the gain is choosing WHAT leaves the card — an idle context,
  re-created in (to measure) milliseconds — instead of the driver's choice,
  which can be a query model's weights (upstream #25646, open: idle weights
  evicted from an Intel dGPU, the perf drop reported).
- **R7. Ten cycles cannot gate a per-wake fault.** After P11 every wake is a
  "first pass after a load" — the class of M5 (1 of 20 scores, first pass
  only) and of M2 before its remedy (4 of 8 runs) — hundreds a day instead
  of once per process, and no check at write time sets a vector against a
  second computation of it. A fault at
  rate p per wake is caught by n clean cycles with probability 1 − (1 − p)ⁿ:
  n = 10 catches p = 5 % four times in ten; 95 % confidence needs 59 cycles
  for 5 %, 299 for 1 %. So: ≥ 300 cycles per encoder, the comparison on the
  FIRST answer after each wake, short and long probes (`LONG_PROBE`) both.
  A cycle is seconds; the gate stays minutes.
- **R8. The acceptance's denominator.** "≥ 90 % of the context's bytes" must
  count KV + compute buffers + op pool as the BACKEND counts them, not
  `size_vram` − the weights file (which holds whatever the process keeps:
  upstream #25271, open, measured 158 MB left on CUDA after a FULL sleep).
  The instrument half-exists: `GGML_SYCL_MEMTRACE=1` (ggml-sycl.cpp:440,
  memtrace.cpp) logs every allocation and free by kind — buffers, pool_leg,
  pool_vmm — and the device's total / free / allocated / other. Set it in
  the test copy's env and put its lines beside the driver's count; the
  diagnostic program is then only the create/free loop.
- **R9. The wake must not re-fit.** LocalAI sets `fit_params = true`
  (grpc-server.cpp:592) and `common_init_result`'s constructor runs
  `common_fit_apply` before every load (common/common.cpp:1307–1312).
  Upstream #30528 (closed unmerged, 2026-10-14) records that fit and sleep
  already disagree on wake. With the weights kept there is nothing to fit:
  the woken context is created from the cparams the FIRST load resolved,
  and the test says so (same n_ctx, n_batch, n_ubatch before and after).
- **R10. Upstream is moving under the same lines.** #30012 (open): the
  sleep race — a handler tokenises, the loop sleeps, the task is stranded or
  the unload segfaults — unfixed at `ddf48f8`; P11 edits the same functions
  and must carry that fix or its own guard, and its test. Direction on
  record is toward freeing MORE (#27307 exit sleeping workers, closed;
  #25271 device reset, open; #25570 open), so the proposal should lead with
  the wake time against a full reload, per backend.
- **R11. Scope of "every model kind".** `destroy()` also frees the draft /
  MTP context, the multimodal context and the TTS contexts
  (server-context.cpp:1135–1149; slots hold `mctx`, `tts.ctx`, :1523–1534).
  The first PR should define the context level for text models and refuse
  (or fully sleep) a server with a draft model or an mmproj, saying so in
  `--help`; and it cannot depend on P3/P4 (encoder-decoder is ours).
- **R12. The names.** vLLM's level 1 moves the weights to CPU RAM and frees
  the card whole; this level keeps the weights ON the card. It is a third
  thing, lighter than both of vLLM's: name it by what it frees
  (`--sleep-level context|model`, as §0 proposes) and do not number it —
  §1's "level-1 sleep" reads as vLLM's and is not.

### The plan, amended

| step | as written (§6) | amended |
|---|---|---|
| before 3 | — | **R6, as §9 amends it: the card record at N = 1…10 s** — GB·h released and wakes per day, per encoder. No card, no build. It picks the debounce; it is no longer a reason to stop. |
| 2 | read the code, answer three questions | done (R1–R3); left to read: nothing |
| 3 | measure memory back, create/free time | the same, with `GGML_SYCL_MEMTRACE=1` (R8), the driver's count split `vram0` / host, and whether the wake recompiles kernels (R2) |
| 4 | the patch | + the failed-wake rule (R4), no re-fit (R9), #30012's guard (R10), the scope (R11); LocalAI: the wake call in every RPC and the seven null reads (R3) |
| 5 | 10 cycles | ≥ 300 cycles, first answer after each wake (R7) |
| before 6 | "check that the margin carries it" | app/resident.py counts an opted-in model awake (R5), with its test — a Louise change, shipped with the deploy |

| | pros | cons |
|---|---|---|
| status quo | nothing to build or re-gate; the driver's spill works today | the driver chooses what leaves the card; ~1.4 GB lives in host RAM; every new model is fitted against contexts that work 31 s a day |
| as briefed | the card's memory follows the work; a general feature upstream lacks | LocalAI's RPCs hang or crash on the first sleep (R3); a wake under contention is undefined (R4); the keeper misreads the sizes (R5); the gate is too short (R7) |
| amended (recommended) | the same gain, known in GB·h before any build; the three ways it fails here are closed first | more work in LocalAI's server and one Louise change; a longer gate; still one re-gate of every space on the engine |

## 9. The operator's model, 2026-11-11 — idle or running, no sleep

*The operator, after §8: "This is the equivalent of a semi-cold start:
instead of loading the whole model only the context memory is requested …
In the very moment the model doesn't need the memory anymore, it is freed …
There is not necessarily a sleep, just idle or running models." And for a
query meanwhile: "the contexts of the batch in queue can be pruned, or some
other model can be evincted, or the chat processing can be postponed".
What follows is the reviewer's opinion, not yet the operator's decision.*

**It supersedes §0's "after N seconds idle" and §2's `sleep_idle_s:600`,
and R6 with them**: R6 read the saving at N = 600; at N → 0 the headline
holds. ≈ 24 GB at rest (34 − 10) + at most 3.3 GB of working context
(p95 2.2) ≈ 27 GB, against 34 GB held today and the reclaimer's 31.0 GB
line; 27–29 GB if the query path keeps its contexts. Eviction and
postponement become corner cases, and memory moves from a margin measured
after the fact (6 h peak, AIMD penalty) to admission before the allocation.

What the engine and the code say about it:

- **The unit is the model's context, not the job's.** A llama.cpp server
  has ONE context — a KV cache for the window, compute buffers for the
  ubatch — and our embedders run `parallel:1` (M6) with `EMBED_BATCH` 1
  (app/config.py:111): a batch's jobs pass through the same context one
  after another. There is nothing to return per job, and nothing lost: the
  batch's peak is one context whatever its length. Memory returned per job
  is Prism's elastic KV (shared-context.md §3); it pays only for a model
  with many parallel conversations.
- **The engine cannot see that a batch ended.** Between two jobs its queue
  is empty; a release on an empty queue frees and re-creates the context
  between inputs. "The very moment" is known to Louise: the last holder of
  the model's lease leaving (app/model_pins.py `hold` / `release`). Two
  ways to carry it: a debounce of a few seconds on the engine's idle timer
  (`--sleep-idle-seconds` takes any N ≥ 0; no new surface; a busy period
  costs at most one wake per N) — or an explicit release call from the
  lease (exact; a new LocalAI call, a wire change: ask first). The first,
  until the card record shows it wastes GB·h.
- **A queued batch holds nothing.** Preparing the next batch's context while
  the current one runs holds two contexts to save one creation; worth it
  only if §1's creation time is a real share of a batch. Without it, "prune
  the queued batch's contexts" has nothing to prune.
- **The order when a query finds no room**: (1) queued corpus work holds
  nothing; (2) the corpus stands aside — already the rule: a question
  releases the corpus pins (`release_for_question`, app/rage/dispatch.py
  `question`), and with the context on the lease that frees its contexts
  too, after the one request already sent (a GPU request is not
  pre-empted); (3) evict a model's weights — someone's next use pays 11–19 s;
  (4) postpone the chat — last, and against the standing order (query work
  highest, operator 2026-10-25). By the arithmetic above 3 and 4 should
  almost never fire.
- **The chat keeps its context at first.** 0.6 GB against the one path
  where a person waits, and its cached prompt dies with the context. Corpus
  encoders and MADLAD first (1,882 + 2,221 + 2,007 MiB of the 8,585 listed
  in §5); the query path once its creation time is measured against a
  person's wait.

What N → 0 makes decisive (§8 amended):

- §1's creation time is paid at every busy period, not after ten idle
  minutes: it is THE go / no-go number, read as chunks/s per collection.
- R10's race (#30012) is met all day, not once after a long idle: a
  prerequisite, with its test.
- R4: an acquire that fails is refused and retried by whoever arbitrates.
- R7: thousands of first passes after a creation a day. Beside the ≥ 300
  cycle gate, A CANARY PER CONTEXT: the wrapper embeds one fingerprint text
  right after each creation and sets its bytes against the run's own before
  any input — the first pass is then the canary's (M5's class), and a
  context that does not reproduce is discarded before it writes.
- R5 grows: the keeper and the dispatcher become the arbiter (sizes asleep
  and awake per model, room checked before the acquire). That is a change to
  WHAT RUNS WHEN: docs/notes/scheduling.md §6's checklist binds it.

| | pros | cons |
|---|---|---|
| timer, N = 600 s (§0 as written) | few wakes a day; the race and a per-wake fault rarely met | ≈ 2 GB freed (qwen3-4b); the budget still measured after the fact |
| the operator's model, per job | the finest grain | no counterpart in llama.cpp short of Prism's allocator; nothing to gain at `parallel:1` |
| the operator's model, per model on the lease (recommended) | ≈ 6 GB freed, the card under its line with room to spare; contention decided by Louise's own priorities | a creation per busy period on the embedding path; the race fix, the canary and the arbiter are prerequisites, not refinements |

## 10. Per request, with "stay warm" — the operator's form, 2026-11-11

*The operator: "when llama.cpp gets a request it allocates the context
memory; when the processing is done it frees it. A request may come with
the flag 'stay warm' and this will make it keep the memory until a timeout,
to prevent free/alloc back to back. Is it feasible, and efficient?" The
reviewer's answer; nothing measured.*

**Feasible — and simpler than the sleeping state §2 and §8 built on.**

- **One thread owns the context.** The server's main loop creates it when
  it takes a task and has none, and frees it when its queue is empty and no
  keep is pending. Upstream's request handlers never touch the context —
  none of `server_routes` reads `ctx_tgt` (server-context.cpp, from :5560);
  they tokenise with the MODEL, which stays; only `get_meta()` reads it, for
  two token strings the vocab gives (:5220–5224). So no handler waits for a
  wake, and the stranded-task race (#30012) has no counterpart: **R3 shrinks
  to LocalAI's seven reads of the context, R10 to a rebase.** The queue
  already tells work from housekeeping (`task_resets_idle_timer`,
  server-queue.cpp:24: a metrics task does not count).
- **The flag travels today, with no wire change.** LocalAI copies a
  request's `metadata` map into `PredictOptions.Metadata` (backend.proto
  field 52; core/http/middleware/request.go:459–461 →
  core/backend/options.go:673–689), embeddings build their options with the
  same function (core/backend/embeddings.go:130), and the backend already
  reads the map for `chat_template_kwargs` (grpc-server.cpp:2827). The VeGa
  wrapper must forward the field. To confirm with one request on the test
  copy.
- **The standard is Ollama's `keep_alive`** (CLAUDE.md, "do not reinvent the
  wheel"): a DURATION per request, with a server default; 0 = release when
  done. One value instead of a flag plus a timeout, and a name upstream's
  users know. For llama.cpp the default stays "keep" (today's behaviour) and
  a server opts in; our opted-in models default to 0.

**Efficient — only with the keep on every corpus request; one number
decides, and it is §1's.**

- Without it each request pays a creation and a free. A corpus request is
  one input (`EMBED_BATCH` 1): a 1,000-token chunk on qwen3-4b at its gated
  1,651 tok/s is ≈ 0.6 s, so every 60 ms of creation is 10 % of throughput.
  A creation allocates AND ZEROES the KV cache (llama-kv-cache.cpp:434;
  ≈ 1.1 GiB for qwen3-4b), reserves the compute buffers from the worst-case
  graphs, and the first decode commits the op pool again. Expected in the
  tens to hundreds of milliseconds — an expectation, not a measurement.
- With it, creations fall to one per busy period and the cost to
  creation ÷ the period's length. The keep's tail holds memory a few
  seconds: nothing in GB·h. And the LAST request of a batch sent with keep 0
  releases at once: §9's debounce and its explicit release are both this
  one field.
- A chat turn carries minutes, so a follow-up finds its cached prompt; the
  query path's short steps (translate, embed, rerank) would each pay a
  creation on a person's wait, so those models keep theirs until measured.
- Still owed whatever the form: the canary per context and the ≥ 300 cycle
  gate (§9, R7), and a SOAK — thousands of GB-sized allocations and frees a
  day is a new load on the `xe` / Level Zero stack of M21, M22 and O6.

| | pros | cons |
|---|---|---|
| per request, no keep | the least memory held; nothing to tune | a creation on every input: tens of % of embedding throughput at the expected cost |
| per request with a keep duration (recommended) | one creation per busy period; exact release on a batch's last request; a known standard; no wire change | every caller must set it right — a corpus request without it is a silent slowdown, so the client sets it, never a person |
| a server-side timer alone (§9's debounce) | no caller involved | no exact release; one timeout for corpus and chat alike |

### The keep's length — seconds, not milliseconds (2026-11-11)

*The operator: "Louise RAGE creates the work batches, thus the stay warm
flag keeps the context also between jobs. The timeout for the warm state
can then be just a few milliseconds." The reviewer: the premise holds, the
length does not follow from it.*

RAGE sets the keep on every corpus request, so no person can forget it —
right. But the keep must outlast the longest gap between two requests of
one stream AS THE ENGINE SEES IT, and what is on record is not milliseconds:

| the gap | measured | where |
|---|---|---|
| wikidata, 4 lanes, requests filled | card idle 11.1 s/min over ~832 requests/min ≈ 13 ms a request | gpu-saturation-plan.md, lever 2's table |
| wikipedia, 2 lanes, producer-bound | 25.6 s/min over ~153 requests/min ≈ 170 ms a request | the same |
| the corpus filling a query's gaps | it stands aside 9–37 ms between rerank variants, and for whole steps of a question (3.4–4.1 s of gaps in 11.6–16.7 s) | scheduling.md §12 |
| stream boundaries | whole idle seconds: 0.1–0.3 s/min | gpu-saturation-plan.md |

(Card-idle figures: an upper bound on the engine's empty queue, since part
of each is the engine's own turnaround with a request in hand. The gap at
the engine is NOT measured; the VeGa wrapper sees every request arrive and
every answer leave and can count it per run, beside `GET /vega/passes`.)

The two errors do not cost the same. A keep shorter than the gap frees and
re-creates the context on every request: silent, and tens of % of
throughput at the expected creation time — worst while the corpus fills a
question's gaps, when a person is waiting. A keep longer than needed holds
one context for its length after a busy period ends: at 2 s and a thousand
busy periods a day, 2.3 % of a day of ≤ 2.2 GB, on a card with ~4 GB to
spare at its worst second (§9).

So: 1–2 s to start, set by RAGE above the 99.9th percentile of the measured
gap, never by hand. The exact release stays available where RAGE knows the
end — keep 0 on a stream's last request — and one verb is still owed to the
arbiter for the rare query that finds no room (R4): release now.
(Withdrawn below: at 500 ms standing aside is the release, and a request
may only extend the keep.)

| | pros | cons |
|---|---|---|
| a few milliseconds | memory back at once; nothing held beyond the work | shorter than every measured gap but wikidata's: a creation per request, unseen |
| 1–2 s, set by RAGE from the measured gap (recommended) | no thrash across lanes, producer stalls and a question's short gaps; the tail costs nothing measurable | memory returns up to 2 s late; one more number RAGE measures |
| a long keep (minutes) with exact release only | never thrashes | a missed release holds the context: the timer design of §0 again |

### 500 ms, logged, passed in milliseconds (2026-11-11)

*The operator: "it looks that 500ms are a safe margin; we can log expired
stay warms for improvement and also pass the timeout in the argument in ms
(0 no stay warm)". The reviewer agrees with all three, and pins what each
needs.*

- **500 ms is a sound START, not yet a safe margin.** It is three times the
  worst mean on record (wikipedia's ~170 ms) — but the record has means, not
  tails. It is also about what a creation is expected to cost, which is the
  named rule for this choice (CLAUDE.md, "the algorithm with a name"): SKI
  RENTAL / the break-even time of power management — hold a resource idle
  for as long as getting it back would cost, and the total is never worse
  than twice the best possible. And it withdraws a verb: when a question
  claims the card the corpus stands aside and its context is gone within
  0.5 s by itself, so the arbiter needs no "release now".
- **Log the PREMATURE expiries, not the expiries.** Every busy period ends
  with one, by design; that is no signal. The signal is a creation that
  follows a free closely: at each creation, the milliseconds since the
  free, the keep in force and the creation's own time. Counted by the VeGa
  wrapper, which owns the llama.cpp child and whose counts are kept (the
  backends' own lines are not, at LocalAI's info level, §5) — per run, beside
  `GET /vega/passes`, and attributed to a collection by RAGE. Then RAGE
  raises the keep itself when premature expiries pass a share of its
  requests and lowers it slowly, as it sets its lanes (AIMD,
  gpu-saturation-plan.md lever 3): a number the system finds, never typed
  (RULE 2).
- **Milliseconds in the request, 0 = none — with three rules written down:**
  1. ABSENT IS NOT 0. A request without the field takes the model's default
     (its YAML), so a client that does not know the field — a warm, a probe,
     a person's curl — cannot make the context cycle.
  2. A REQUEST ONLY EXTENDS: the deadline is the latest of those asked, never
     the last. Otherwise a query's 0 on an encoder the corpus shares (a
     symmetric space's one encoder) cancels the stream's keep. A stream's
     last request with 0 then frees up to 500 ms late: nothing.
  3. The clock starts when the request's answer is complete, and only model
     work counts (`task_resets_idle_timer`'s precedent: a metrics task does
     not).
  An integer of milliseconds departs from Ollama's `keep_alive` (seconds, or
  a duration string) because the value is under a second; say so upstream.
- **The gate gets simpler.** The keep is RAGE's setting and no part of a
  run's identity, PROVIDED bytes are identical across creations — and that
  is proved by running the existing gate with keep 0: every fingerprint
  text then meets a new context, which is R7's cycle test, at the worst
  case, with no instrument of its own.

| | pros | cons |
|---|---|---|
| 500 ms, fixed, logged | simple; about the break-even; standing aside frees within 0.5 s | a collection whose gaps run longer cycles until someone reads the log |
| 1–2 s, fixed (the reviewer's earlier answer) | above every mean on record with more room | still a guess about tails; memory back later for no measured reason |
| 500 ms to start, raised and lowered by RAGE from the premature expiries (recommended) | each collection finds its own keep; no hand tuning; the log is the controller's input | one more small controller in RAGE, with its test |
