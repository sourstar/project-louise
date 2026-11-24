# More models than the card holds: parking (2026-11-24)

*A design note for the operator's approval. Nothing here is built, and
nothing is measured that this note does not label as measured.*

The operator, 2026-11-24:
- "with a full card, by wisely unloading and preloading we can virtually
  reduce to zero the load overhead and run at almost full speed jobs using
  way more models than those that fit the VRAM all together. Assume we have
  10 collections with 10 different embedders that embed full time";
- "it is not a matter of how the current use case would improve, it is a
  matter of allowing use cases that today we couldn't afford";
- on where it is built: "Both engines, and omnivoice has already been ported
  to llama.cpp";
- on the scenario: "Heavy parsing/indexing/embedding like now is not the
  scenario for clockwork; its scenario is no data ingestion/processing,
  only serving queries on an unloaded system";
- on production: "My idea in production is to run the data ingestion at
  night only and serve the queries during the day. The data fetching can
  run 24h with a limited bandwidth during the day"; and, of the node as it
  runs now: "Keep the current mix, I told you about the production scenario
  for you to understand the scenario for clockwork".

## 1. What it is for

Today a model is on the card or it is not. One that is not pays a full load
when it is next asked: MADLAD 8.70 s and the Nemotron reranker 7.51 s after
an unload (measured on the node, backend-qualification-2026-11-21.md, phase
5). So the keeper preloads what fits its weights budget, and a collection
with an encoder of its own asks which model leaves the card.

THE SCENARIO IS SERVING (operator, 2026-11-24): a node that ingests nothing
and answers questions, over more collections and models than its card
holds. A question needs a few models, one after the other, and which ones
is known the moment it is asked. Measured on such a question (München,
2026-11-09, ask-card-workflow.md):
- its query encoders cold: 5.4 to 13.9 s each, the search 24 to 46 s;
- the first word of the answer 42.4 s after Ask with models cold, 14.3 s
  with the same models still loaded.

The aim is that the cold question costs what the warm one does. The card's
MEMORY then stops deciding how many models the node serves; only the
question in hand has to fit. The use cases this opens:
- many collections, each with the encoder and the reranker chosen for it,
  never for the room;
- a larger chat model, or several, beside them;
- a peer with a small card serving the same collections.

Heavy ingestion beside the questions (parsing, indexing, embedding) is not
this note's scenario. It keeps today's rules: corpus work stands aside for a
question, and the keeper's two budgets decide what is preloaded.

IN PRODUCTION THAT NODE IS THE DAY (operator, 2026-11-24): ingestion runs at
night only; by day the node answers questions and only fetches, on a limited
bandwidth. The day is this note's scenario; the night keeps the two budgets.
It is stated to fix the scenario and is NOT switched on: the node keeps its
current mix (operator, the same day). Nothing is to be built for it, the
settings exist: `[schedule] profile`, a profile's `mode = "acquire-only"`
(app/node_profile.py) and `[budget] schedule` (app/transfer/schedule.py).
Two things follow for this design, both for the day the plan is switched on:
- by day no document is embedded, so an encoder that only embeds documents
  need not be on the card: the keeper could leave it parked;
- the room for parked weights is the DAY's host RAM, which is not the paused
  node's: the idle parse pool may keep its children's memory while nothing
  parses. Not measured (§5, item 4).

## 2. The standard: Clockwork

Gujarati et al., "Serving DNNs like Clockwork", OSDI 2020. Read 2026-11-24.

- **Host RAM holds every model, the card is a cache.** A worker loads every
  model's weights into main memory at startup. GPU memory is pre-allocated
  and cut into 16 MB pages. A LOAD is a copy from RAM into free pages: 8.3 ms
  for ResNet50, whose inference takes 2.9 ms.
- **Three actions with a window each.** A central controller sends LOAD,
  UNLOAD and INFER, each with an `earliest` and a `latest` start. A worker
  runs exactly that and drops an action that misses its window.
- **Loading and computing overlap.** One INFER executor and one LOAD
  executor per GPU. They use different resources (the GPU, the PCIe copy)
  and "can run concurrently with negligible interference". Only one INFER
  runs at a time.
- **The controller predicts.** It keeps the last ten durations per worker,
  model and batch size. It loads the model with the most unmet demand and
  unloads the least recently used.

What does not carry over as it is:
- its models are tens to hundreds of MB and answer in milliseconds; ours are
  0.6 to 5 GB of weights and answer a batch in 0.3 to 4 s;
- it has its own runtime (TVM kernels); we have llama.cpp;
- it cancels a request that cannot meet its deadline; Louise defers
  (CLAUDE.md, "deferral is not failure").

## 3. Where each part is built (operator: both engines)

"Both engines": the model engine under LocalAI, and RAGE, Louise's own, whose claim opens a
question's plan.

VeGa's rule stands: "LocalAI is the only one handling VRAM on the card"
(vega.md §1). So Louise never loads or frees card memory herself. She asks.

| layer | what it does | where | why there |
|---|---|---|---|
| mechanism | a model PARKED: its process alive, its weights in host RAM, nothing on the card; putting it back is a copy | the engine (llama.cpp, our series) | nothing above the engine can do it |
| lifecycle | parks a model where it stopped one; un-parks it on a request; two calls, "prepare model X" and "park model Y" | LocalAI | it owns loading and eviction already; every client of LocalAI gains, with no scheduler at all |
| foresight | which models a question needs next, put on the card before it reaches them | Louise: the question's card (`app/question_card.py`) behind RAGE's claim | only Louise knows a question's plan: its collections' encoders, then the rerankers, the chat, the voice |

### 3.1 The engine

- Context on demand (P8 in the series since 11-22) already makes and frees
  a model's CONTEXT per request, in 23–55 ms (measured 11-12). Parking is
  that lifecycle carried one level down, to the WEIGHTS, and it needs it: a
  model can be parked only with no live context.
- One backend carries nearly everything on the card: the encoders, the
  rerankers, the chat, MADLAD (the encoder-decoder patches) and OmniVoice
  (ported 2026-11-24, staged; operator: "I would like to have only one
  backend"). Outside it: medcpt-article on OpenVINO (312 MiB), parakeet (on
  the iGPU) and silero (CPU). They keep today's unload.
- The engine counts its uploads, as P8 counts context creations, and
  refuses rather than aborts when the device is full (staged 0013).

What the engine does today, which parking has to change (the Tensor
session, from the source, 2026-11-24):
- **llama.cpp keeps no host copy of the weights for a GPU backend,** nor the
  loader that knows each tensor's place in the file: both go after the
  load. Parking keeps that map, and the file open or mapped, to copy again.
- **ggml-sycl rewrites QUANTIZED weights on the card at first use** (the
  reorder layout, `opt_for_reorder`: Q4_0, Q8_0, Q2 to Q6_K, IQ3; never
  F16). It fires on a matmul whose right-hand side has 8 columns or fewer,
  so the warm-up's one or two tokens rewrite every quantized weight in
  place. MADLAD and bge-m3 (F16) have no rewrite; qwen3-embedding-4b
  (Q4_K_M) has.
- **What a process's first run on the card is made of, and who keeps it:**

  | part | belongs to | paid at an un-park |
  |---|---|---|
  | the kernel programs (created per process, from the driver's cache; oneMKL's own at its first call) | the process | no |
  | the rewrite of quantized weights | the weights on the card, and a flag on each tensor | by what parking keeps, below |
  | the pools and the compute buffer | the llama context | yes, as every context on demand pays it today |
  | the runtime's own first use | the process | no |

  So a parked F16 model pays the upload and a context creation, nothing
  else. For a quantized one, parking keeps either the card's bytes copied
  back to the host (already rewritten: a download at each park, and host
  memory that is not reclaimable), or nothing and reads the file again
  (page cache, reclaimable; the rewrite is paid at every un-park, and the
  engine MUST clear the flag on every tensor, or it reads the file's layout
  as the rewritten one: wrong vectors, silently).
- **The warm-up is not dropped on a gated encoder.** `--no-warmup` moves
  the same cost to the first request, and on a quantized encoder the
  weights would then change layout, and the engine its kernel path, at some
  later call.
- **Every tensor's address changes at an un-park.** Whatever remembers one
  must be gone first: contexts (P8 does that), recorded SYCL graphs, the
  fused-op and expert caches.
- **A parked process is not nothing on the card:** its SYCL/Level Zero
  context and compiled kernels stay. With one process per model, that floor
  is multiplied by the number of parked models (§5, 5).
- **OmniVoice is two model files** (a text model and an audio model, the
  second with its own weights on the card). P8 refuses such a model today;
  parking it means parking both halves.

### 3.2 LocalAI

- A third state between loaded and unloaded: parked. Where the reclaimer or
  a load's room stops a backend today, it parks one that can be parked.
- A request to a parked model un-parks it. That alone serves every client of
  LocalAI: eviction becomes cheap, with no caller changed.
- Two calls, for a caller that knows what comes next: prepare, park. `/system`
  says each model's state. For the backend they are two new RPCs in
  LocalAI's backend.proto, additive as P1 was. A backend that cannot park
  answers "unsupported" and keeps today's unload.
- A host-RAM budget for parked models, and a real unload beyond it.
- It is generic, so it is proposed upstream (the pattern of
  `deploy/localai/upstream/`). Until it is taken, it is a patch we carry.

### 3.3 Louise: the question's plan

The module exists: `app/question_card.py` (ask-card-workflow.md) already
moves models for a question, with today's loads and unloads. RAGE's claim
(`dispatch.question`) says a question is open; the card's side follows it.

- At the question's start, its SEARCH's models are prepared: the query
  encoders of the collections asked, and the translator under Quality.
- While the search runs, the ANSWER's models are prepared, in the order
  the answer meets them: the rerankers, the chat, the voice. This is
  `after_search` today, with a copy where it pays a load.
- What leaves the card is parked, never stopped: the models the question
  does not use, the oldest real use first (model_evict's rule, kept).
- A question in flight protects its own models, as today (spec 5).
- The page knows the collections chosen before Ask is pressed: preparing
  their encoders then is a later refinement, measured first.
- Louise uses the two calls and nothing else (invariant 7).
- No corpus schedule changes. Planned turns between corpus encoders are
  out of this note's scope.

## 4. VeGa and invariant 12

- **Receipts:** unchanged. The wrapper forwards calls it does not know as
  opaque bytes, so park and un-park reach the engine.
- **The run:** a parked model keeps its process, engine, flags and weights.
  It is the same validated run, and no gate runs per un-park.
- **A new build is a new run:** the engine that can park is gated once per
  space, as at the cut-over of 2026-11-24.
- **The gate gains a clause:** an un-park uploads the weights again, so the
  gate shows the same bytes after N park cycles, as it does after 300
  context creations. The engine's upload count goes into the run's statement.
- **The wrapper is rebuilt** if parking adds a load option: it refuses a
  load whose options it cannot name.

## 5. What is measured first

Each decides a part of the design. What was already held is in §5.1; the
split of a load (1) and the parked floor (5), measured on the unloaded
node, are in §5.2. The rest is not known today.

1. **A cold load's 7.5–8.7 s, split:** the process start, the file read
   (page cache cold and warm), the device's start, the copy, the first pass.
   Parking saves everything but the copy. If the copy is most of it, parking
   saves little.
2. **The copy's rate, host to B70,** for a 2.7 GB model. And whether a copy
   beside a working encoder slows that encoder: Clockwork's premise, to be
   shown on this card and driver.
3. **A question's timeline on today's code, cold and warm,** stage by
   stage (the 2026-11-09 figures, taken again on the live engine): what
   parking has to bring to the warm case.
4. **The host RAM a serving node has for parked weights.** Measured
   2026-11-24: 28.6 GB available with the node paused, against about 25.5
   GB of weights for everything loaded today. (Under heavy ingestion it fell
   to 4.7 GB: not this scenario.) On the production day (fetching only, the
   parse pool idle) it is not measured: the paused node's figure is the
   nearest one held.
5. **What a parked process still holds on the card:** its device context
   and compiled kernels, with no weights. LocalAI stops a backend's process
   when it unloads, so no such process exists today; its stand-ins read
   319 MiB (§5.2). The empty card reads 25 MiB. It decides how many models
   beyond the card really fit.

### 5.1 What the node already holds (München, 2026-11-24)

Totals only, from the day's runs: a request to a model not loaded until its
first answer, the weights in the page cache, the card not full. TAKEN ON AN
INGESTING NODE: not this note's scenario, and to be read as upper bounds
(§5.2 has the same loads on the unloaded node).

| model | weights | cold load |
|---|---|---|
| bge-m3 (through the wrapper) | 621 MiB | 6.0 s |
| qwen3-embedding-4b (the gate's cold start) | 2,737 MiB | 4.2 s |
| nemotron-3-embed-1b (engine direct, context held) | 2,512 MiB | 5.17–6.11 s |
| the Nemotron reranker | 2,465 MiB | 7.41–7.51 s |
| the OmniVoice port | 1,442 MiB | 7.7–8.4 s (61.9 s the first time on a new engine: its kernels compiled) |
| MADLAD | 4,850 MiB | 8.14–8.70 s |
| the 35B chat | 20.6 GiB | 8.1–8.8 s (40.8 s with its file not yet in the page cache) |

- **The totals do not grow with the size.** 0.6 GiB takes 6.0 s and 20.6 GiB
  takes 8.1 s. So most of a load is not the copy. This is read off totals of
  different models on different paths, not a split: the split (a load with
  `verbosity:4`, an encoder and MADLAD) confirms or corrects it.
- It holds, and the split says why (§5.2). It also corrects what parking
  saves: the copy is a fraction of a second, but a part as large as the
  process's start comes after the copy, and a parked model may pay it again.
- The copy's rate: 16.2 GiB/s host to B70 by the expert-cache tuner's fit
  (backend-qualification-2026-11-21.md, phase 4, row 7), so about 0.2 s for
  a 2.7 GB model. Memory the DRIVER brings back is slower: 4.2 GiB of
  paged-out buffers in 1.25–1.76 s (2.4–3.4 GiB/s).
- A device's kernels compiled for the first time cost about 60 s (the first
  load on a new engine); later loads read the cache.
- A file out of the page cache costs far more than anything else (40.8 s
  against 8.1 s): parked weights held in host RAM are what avoids it.
- Not held: a load beside a working encoder (2), the busy share with two
  encoders (3). Host memory per minute (4) is being logged since
  2026-11-24, for seven days.

### 5.2 A load, split, and the parked floor, on an unloaded node (München, 2026-11-24, 15:16–15:44Z)

THE CONDITIONS, as §5.3 asks them, and as they were:
- the node paused for 1,715 s and never resumed inside; no pass, no
  transfer, no question. Left running and named: web, the gpu probe and
  governor, LocalAI, the vega wrapper, jellyfin, calibre, cloudflared,
  tailscale, the OCR container, three host units writing a line a minute;
- 585 s of settling after the pause before anything was timed; the card at
  65 °C, its idle reading, from 15:19 on;
- the card EMPTY and alone for every load: its counter at 25 MiB and nothing
  loaded before each, only the asked model after; no row left out;
- the quiet, read three times: 0.83, 0.32 and 0.12 busy cores of 12; load
  average 2.21, 1.42, 0.56 (one minute, still decaying from the work
  stopped); MemAvailable 37.8 GB;
- THE CAVEAT: the web container, restarted by the deploy 20 minutes before,
  used 0.5 to 0.7 of a core during the live rows. The copies ran after it at
  0.1 to 0.3 and agree with the live rows within 0.1 s, so the rows are
  taken;
- the path: the LIVE models by their names, through LocalAI, the vega
  wrapper and the engine (MADLAD direct to the engine, as deployed), for
  the totals; COPIES with `verbosity: 4`, direct to the engine, for the
  split. Engine louise-sycl-f16-llama-cpp-c3294e050ff2-stack-7c301a7ba437
  (the wrapper's vega.json), LocalAI v5.0.1-2-gac69f2840, the models' own
  flags and env. The file in the page cache. A pilot of each first, read,
  then four loads.

**The totals, live models, seconds:**

| | bge-m3 (577 MiB on the card) | MADLAD f16 (5,109 MiB) | qwen3-embedding-4b Q4_K_M (2,375 MiB) |
|---|---|---|---|
| request → first answer, cold | 6.22–6.32 | 6.53–6.63 | 4.25–4.26 |
| the same request, loaded | 0.03 | 0.35 | 0.05 |
| unload: the call → the card's counter at empty | 0.21 | 0.21 | 0.11 (and 15.1 once) |
| on the card after the request, by the counter | 921 MiB | 7,246 MiB | 2,811 MiB |

**The split, copies, seconds from the backend's "Server listening":**

| part of a cold load | bge-m3 | MADLAD | qwen3-embedding-4b | a parked process pays it again |
|---|---|---|---|---|
| "Server listening" → LocalAI's load call | 2.04–2.07 | 2.03–2.04 | 2.03–2.06 | no: no spawn |
| the file's header and vocabulary | 0.28–0.30 | 0.44–0.50 | 0.10 | no |
| the weights, read and copied, and the context made | 0.19–0.21 | 0.81–0.83 (1.43 on the first load) | 0.35–0.38 | yes: this is the un-park |
| the warm-up run | 3.43–3.55 | 2.67 | 1.53–1.56 | by the source no, but a quantized model's rewrite (§3.1); not measured |
| "model loaded" → the first answer | 0.01 | 0.37 | 0.01 | — |
| **the copy's total** | **6.17–6.27** | **6.43–7.03** | **4.18–4.28** | |

- **The wrapper and the path add under 0.1 s:** the live totals are the
  copies' within that.
- **The copy runs at 6.2 to 6.4 GiB/s** for the two larger models, the read
  from the page cache, the copy and the context together. bge-m3's 0.2 s is
  mostly not copy (577 MiB). MADLAD's first load, and its pilot, took 1.43
  and 1.55 s for the same step: not explained.
- **The unload frees the card in 0.11 to 0.21 s.** Today it ends the
  process. A park is the same free with the process kept; it copies nothing
  back, since an F16 model's host copy is the file in the page cache.
- **The warm-up run is the largest part after LocalAI's wait,** and for
  bge-m3 the largest of all: 3.4 to 3.5 s of 6.2 (its ubatch is 8,192). It
  is the cost of a process's first compute on the card; the same request
  answers in 0.03 s afterwards.
- **The rewrite of a quantized model's weights could not be taken apart.**
  The qwen copy with `GGML_SYCL_ENABLE_OPT=0` warmed up in 1.96–1.97 s,
  0.4 s SLOWER than with the rewrite, and answered in 0.10 s against 0.06:
  without the rewrite the engine runs other kernels, so the difference is
  not the rewrite's cost. Its upper bound is the whole warm-up, 1.55 s.
- **WHAT A PARKED MODEL WOULD PAY, derived and not measured** (no parked
  process exists): by §3.1 the kernel programs stay with the process and a
  context costs 23 to 55 ms (measured 11-12), so an F16 model pays the copy
  and a context. For a quantized one the rewrite is added, somewhere under
  the warm-up.

  | | cold today | parked, derived |
  |---|---|---|
  | bge-m3 | 6.2–6.3 s | about 0.3 s |
  | MADLAD | 6.5–6.6 s | about 0.9 s (1.5 s where the copy was slow) |
  | qwen3-embedding-4b | 4.25 s | 0.4 to 1.9 s |

  Step 1 measures the first row.
- **AN UNLOAD OF A VEGA MODEL SOMETIMES TOOK 15 s MORE:** 15.06 and 15.12
  s on three of fourteen (both pilots, one qwen load), the card freed only
  then; MADLAD and the direct copies never (0 of 22). THE ENGINE WAS TOLD TO
  STOP TWICE. LocalAI's stop SIGTERMs the backend's process group, polls
  for the pid it started, and SIGKILLs the group after its process
  library's 15 s (mudler/go-processmanager; the Tensor session, from the
  source). Behind the wrapper the engine got the group's signal and, a
  millisecond later, the wrapper's own: llama.cpp answers a second with
  "terminating immediately", an exit inside its handler, which sometimes
  hung with the card held. A direct engine prints the first line only
  (München, every unload logged). Fixed in the wrapper: a backend told
  already is left to go by itself (`OWN_EXIT_S`), and the wrapper's whole
  stop is under LocalAI's 15 s (`GRACE_S` 12, from 30). The engine's exit
  from a signal handler is an upstream defect, on Tensor's list.

- **2.0 s of every load pass before the engine is asked anything:** the
  backend listens, and LocalAI's load call reaches it 2.0 s later, the same
  for both models. No engine work is in it. IT IS A POLL'S SLEEP (the Tensor
  session, from LocalAI's source, 2026-11-24): `spawnGRPCModel`
  (pkg/model/initializers.go) checks the new backend's health the instant
  it has spawned it, before the process has bound its port, then sleeps
  `grpcAttemptsDelay` whole seconds and checks again; the defaults are 20
  attempts and 2 s (pkg/model/loader_options.go), the same on upstream's
  master. Read from the source; that the WHOLE 2.0 s is this sleep is
  inferred (the server's log level does not print the loop's lines), and
  it fits every load of the retake: 2.03 to 2.07 s, whatever the model.
  - What controls it: the model's YAML only, `grpc: attempts` and
    `attempts_sleep_time`, in whole seconds, 1 at least (0 reads as unset).
    No node-wide setting.
  - It is paid by every cold load of every backend LocalAI spawns, so also
    by every load after an eviction. A parked model does not pay it (no
    spawn); the cold loads that remain do.
  - The fix is a server patch: poll every ~50 ms for the first seconds,
    then as today; about 1.9 s off every cold load. The YAML key at 1 would
    take 1 s off with no build.
  - DECIDED (operator, 2026-11-24, to München): "Patch localAI when
    clockwork is implemented". The server patch, built with parking (§6,
    step 2). Until then the sleep stays at 2 s, and no model file takes the
    key.

**The floor of a parked process,** each alone on the empty card, the context
freed (`keep_context_ms` 0, the log's last line "context freed"), idle 8 s:

| | by the card's counter | fdinfo, client 1 + client 2 | GTT |
|---|---|---|---|
| a process with no weights (a medcpt-query copy, `gpu_layers 0`, context on demand) | 319 MiB | 251 + 67 | 271 |
| vega-bge-m3 | 896 MiB | 828 + 67 | 99 |
| vega-nemotron-3-embed-1b | 2,513 MiB | 2,446 + 67 | 99 |

- **A parked process holds 319 MiB of the card, 8 s after its last
  request.** Two readings agree: the process with no weights, and bge-m3's
  896 less its 577 of weights. 67 of it is a second DRM client every
  llama.cpp backend has.
- **LEFT IDLE FOR MINUTES IT HOLDS LESS: the driver moves it to system
  memory** (München, the same evening, 19:48Z and 19:49Z, 4.7 and 5.7
  minutes after the last request, the card at about 26 of 32 GiB): bge-m3
  613 MiB on the card where it held 896, qwen3-embedding-4b 2,498 where it
  held 2,727. About 215–225 MiB of the first client and the 65–67 MiB of
  the second had left the card and showed as GTT instead (bge-m3's 99 MiB
  of GTT became 354, qwen's 213 became 306). Moved, not freed, and brought
  back at the next request at the driver's rate (2.4–3.4 GiB/s, §5.1).
  LocalAI's `size_vram` is the VRAM of both clients, so it says the same.
  So on a card under pressure the floor of an idle process tends toward its
  weights alone, at the price of host memory: the driver already does for
  a process's own buffers what parking would do for its weights. Whether a
  process with no weights goes to nothing is not read yet.
- Ten parked models are at most 3.2 GiB of the card's 32. With one process
  per model THE FLOOR, not the weights, bounds how many models a node
  serves; how far idle time lowers it is the reading above.
- Nemotron's 2,513 is the figure of 11-23 (2,512). The keeper counted it at
  2,229, a partial reading remembered as its weights: §8.

### 5.3 The protocol of the retake

measuring.md §10 and §6, with nothing left to a word like "paused":

1. **Nothing else runs,** and the write-up names what was stopped: the node
   paused (its mode restored after), every pass beside the worker stopped
   or finished (a records build, an index build, a calibration, a CPU
   reference, a deploy's test run), transfers held, no question asked.
2. **The card is empty** before the first load: every backend unloaded, the
   card's counter at its empty reading (25 MiB). One model at a time.
3. **The quiet is recorded, before and after:** the host's load average,
   each container's CPU from its cgroup over the window (measuring.md §8),
   MemAvailable, the card's counter. A window that is not quiet is not
   timed.
4. **The deployed thing is measured:** the engine named from the wrapper's
   vega.json, the models' own flags and env, and the path a question takes
   (LocalAI, the wrapper, the engine), saying so where a copy goes direct.
5. **A pilot:** the first load of each model read before the rest.
6. **Timed, four loads each, the file in the page cache:** bge-m3 (F16,
   step 1's model), MADLAD (F16), qwen3-embedding-4b (Q4_K_M). The split as
   in the table above, the client's total, the same request warm, and the
   unload: from the call to the card's counter back at empty.
7. **A measurement copy, never a deployed model:** qwen with
   `GGML_SYCL_ENABLE_OPT=0`, whose warm-up has no rewrite (the switch
   changes the kernels an encoder runs: invariant 12). It did not give the
   rewrite's cost (§5.2). The copy with `grpc: attempts_sleep_time: 1` was
   not run: the poll's choice was made first.
8. **The floor,** each alone on the empty card, the context freed (the
   request carrying `keep_context_ms` as the deployed model does): bge-m3,
   and the no-weights stand-in. By fdinfo per client and by the card's
   counter.

### 5.4 The first readings, on a busy node: withdrawn

The operator, 2026-11-24: "Have you measured with the unloaded system? No,
you did not, I'm sitting next to the busy node. Your measurements are
garbage." The first loads (14:29–14:34Z) were timed with the worker paused
and nothing else: measuring.md §10, the operator's rule since 2026-11-03
("this is not the first time it happens"), was not applied, and the
architecture session published the figures without it. What ran in that
window (München's record):
- the worker was pausing, not idle: a wikidata vector index refresh begun
  at 14:26, Normattiva's fts children parsing, its cgroup at 16.6–17.5 GiB;
- one backend of 4.4 GB stayed on the card through the first run ("card
  emptied" was false), and between the two runs the node RAN for 25 s: it
  cold-started three models and embedded;
- CPU 79–83 °C and card 74–80 °C every minute; no load average and no
  container's CPU recorded;
- the two "floor" processes still held their contexts (no
  `keep_context_ms` on the requests): 308 and 1,312 MiB were not floors.

What they gave, against §5.2: qwen 4.51–4.58 s (4.25), MADLAD 6.67–7.93 s
(6.53–6.63), the copy 0.42–0.50 and 0.83–0.92 s (0.35–0.38 and 0.81–0.83),
the warm-up 1.69 and 2.93 s (1.55 and 2.67). The shape was the same and
every time was high; the "0.3 to 0.7 GiB a parked model" was wrong.

## 6. The order, each step on the operator's word

| step | who | done when |
|---|---|---|
| 0. the four measurements | München | the numbers are in this note; go or no go |
| 1. the engine parks one encoder (bge-m3, 621 MiB) | Tensor | N park cycles give the same bytes; timed apart: the park, the un-park (allocate, copy, reorder), the first request after; the parked process's floor on the card read |
| 2. LocalAI's parked state, its two calls, the RAM budget; the wrapper; the health-check poll at ~50 ms (operator, 2026-11-24) | Tensor, then upstream | a parked model answers after an un-park; the gates pass on the build |
| 3. the question's plan on the two calls (`question_card`) | architecture | a cold question's first word within a second or two of the warm one's, measured on the node |

WHAT STEP 0 SAYS (§5.2, the unloaded node): a cold load is 4.3 to 6.6 s,
and almost none of it is the copy. 2.0 s are LocalAI's wait, 1.5 to 3.5 s
the process's first compute, 0.2 to 0.8 s the weights. A parked F16 model
would return in about 0.3 to 0.9 s, by the source and the parts measured;
no parked process exists to time. Each parked process holds 319 MiB of the
card. So: go for step 1, which measures what is derived here. LocalAI's
poll sleep is decided: patched with step 2. Still to measure: a load beside
a working encoder (2), a question's timeline on an unloaded node (3).

Each step is a deploy of its own. Each can stop there and still be worth
having. In the serving scenario step 2 alone gives most of the gain: a
question's first request to a parked model waits for a copy, not a load.
Step 3 hides the copy as well.

## 7. The alternatives

| choice | pros | cons |
|---|---|---|
| status quo | nothing to build | more models than fit means full loads, 7.5–8.7 s each |
| Louise alone: the question's card over today's load and unload (live since 2026-11-09) | no engine or LocalAI change | every model a question meets cold is a process start, 5–14 s each; only Louise gains |
| page mode (room-on-the-card.md, built, off) | automatic; +0.2–0.4 s for a small model, +1.3–1.8 s for the 35B (measured) | the driver chooses what moves and when; it holds host RAM; a full card killed a backend (0013 staged for it) |
| LocalAI and the engine alone | generic; could go upstream | reactive: the first request to a parked model waits for the copy |
| **both engines, in that order (the operator's choice)** | each layer where its knowledge is; Louise adds two calls to a module she has | the largest change; a LocalAI patch to carry until upstream takes it |

Existing tools, and why they are not the answer by themselves:
- **llama-swap** swaps whole llama.cpp server processes: every swap is a
  full load.
- **Triton's explicit model control** is the shape of the two calls (load
  and unload by API); its unload frees the model.
- **Ray Serve's model multiplexing** keeps an LRU of models per replica:
  the lifecycle of §3.2, without the foresight.

## 8. Open

- How the engine holds parked weights: the file's page cache, or a pinned
  host buffer. The first costs nothing more, and the kernel will drop it
  sometimes (4.7 GB were available at the worst hour): the un-park then
  reads the SSD, which is still not a process start. The second is certain
  and competes with the worker. NOT as GGML_SYCL_USM_SYSTEM memory:
  measured 2026-11-24, such pages do not make room for a neighbour (a load
  beside them died).
- What is parked: the file's bytes (the reorder runs again at each
  un-park) or the reordered bytes read back once (§3.1).
- One process per model is LocalAI's shape: what a parked process costs in
  host RAM beyond its weights, and on the card (319 MiB each, §5.2), and
  whether several models could share one process's device context, which is
  not LocalAI's shape today.
- (Fixed 2026-11-24.) The keeper learnt an on-demand model's weights as the
  fewest reading over 0.3 of its largest, kept for ever: nemotron 2,229 MiB
  counted, 2,513 held at rest (§5.2). They are now what its process holds
  at rest, the latest time it was seen so (shared-context.md, "the two
  budgets").
- For a quantized model, what parking keeps (§3.1): the card's bytes copied
  back, or the file read again with every tensor's flag cleared.
- The chat model's prompt cache across a park.
- Page mode once parking exists: kept as the fallback for a backend that
  cannot park, or retired.
