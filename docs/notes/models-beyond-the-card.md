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
- **ggml-sycl rewrites weights on the card at first use** (the reorder
  layout). A parked copy is either the file's bytes, and the reorder runs
  again after every un-park (a pass over the weights on the card,
  deterministic, so the same bytes), or the reordered bytes read back once.
  The choice decides an un-park's real cost (§8).
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
split of a load (1) and the parked floor (5), read on the node the same
day, are in §5.2. The rest is not known today.

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
   when it unloads, so no such process exists today; two stand-ins are read
   in §5.2 (0.3 to 0.7 GiB). The empty card reads 25 MiB. It decides how
   many models beyond the card really fit.

### 5.1 What the node already holds (München, 2026-11-24)

Totals only, from the day's runs: a request to a model not loaded until its
first answer, the weights in the page cache, the card not full.

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

### 5.2 A load, split, and the parked floor (München, 2026-11-24)

Test copies of two models on the live engine (c3294e), `verbosity: 4`, the
node paused, the file in the page cache, 14:29–14:34Z. Four loads of each,
the range. Seconds on the engine's own clock, from the backend process's
"Server listening".

| part of a cold load | qwen3-embedding-4b (2,375 MiB on the card) | MADLAD f16 (5,109 MiB) | a parked process pays it again |
|---|---|---|---|
| "Server listening" → LocalAI's load call | 2.03–2.05 | 2.01–2.07 | no |
| the file's header and vocabulary | 0.09 | 0.45–0.60 | no |
| the weights, read and copied to the card | 0.42–0.50 (4.6–5.5 GiB/s) | 0.83–0.92 (5.4–6.0 GiB/s) | yes: this is the un-park |
| the context's buffers | 0.02 | 0.01 | yes, as context on demand already does per request |
| buffers → "model loaded": the warm-up run | 1.8 | 2.85–3.06 | NOT KNOWN |
| the first answer | in the total | 0.38–0.96 | — |
| **the client, request to first answer** | **4.51–4.58** | **6.67–7.93** | |
| the same request with the model loaded | 0.02–0.03 | 0.87–0.89 | |

- **The copy is the smallest part,** a tenth of a load. Its rate is 4.6 to
  6.0 GiB/s, the read from the page cache and the copy together (§5.1's
  16.2 GiB/s was a fit, for the copy alone). One load of each, right after
  another model had been unloaded, took 1.82 s and 2.05 s for the same step
  (1.3 and 2.4 GiB/s): not explained.
- **2.0 s of every load pass before the engine is asked anything:** the
  backend listens, and LocalAI's load call reaches it 2.0 s later, the same
  for both models. No engine work is in it. IT IS A POLL'S SLEEP (the Tensor
  session, from LocalAI's source, 2026-11-24): `spawnGRPCModel`
  (pkg/model/initializers.go) checks the new backend's health the instant
  it has spawned it, before the process has bound its port, then sleeps
  `grpcAttemptsDelay` whole seconds and checks again; the defaults are 20
  attempts and 2 s (pkg/model/loader_options.go), the same on upstream's
  master. Read from the source; that the WHOLE 2.0 s is this sleep is
  inferred, and LocalAI's own debug lines around the loop, or a copy with
  the key below at 1, would make it a reading.
  - What controls it: the model's YAML only, `grpc: attempts` and
    `attempts_sleep_time`, in whole seconds, 1 at least (0 reads as unset).
    No node-wide setting.
  - It is paid by every cold load of every backend LocalAI spawns, so also
    by every load after an eviction. A parked model does not pay it (no
    spawn); the cold loads that remain do.
  - The fix is a server patch: poll every ~50 ms for the first seconds,
    then as today; about 1.9 s off every cold load. The YAML key at 1 takes
    1 s off with no build. Neither is built: the operator's choice.
- **The part after the buffers is as large as the start, and it is the
  warm-up run alone** (the engine's "empty run"), read line by line in one
  load of each: 1.685 s of qwen's 1.77 s and 2.934 s of MADLAD's 3.12 s,
  with no line during it. The canary takes 1 ms; the slots and the template
  the rest. The same run on a loaded model answers in 0.02 s, so this is
  the cost of a process's FIRST compute on the card. The log does not say
  what it is made of: the cached kernels being loaded and the pools' first
  allocation would stay with a parked process; a rewrite of the weights
  (§3.1) would be paid at each un-park, by §8's choice. So an un-park costs
  between the copy alone and the copy with that part:

  | | cold today | un-park, at best | un-park, the first use paid again |
  |---|---|---|---|
  | qwen3-embedding-4b | 4.5 s | 0.45 s | 2.3 s |
  | MADLAD | 6.7–7.9 s | 0.9 s | 3.9 s |

- **Parking itself, card to RAM, copies nothing.** The weights never change
  and their host copy is the file in the page cache: a park frees the
  card's buffers. Not timed apart, since an unload today takes the process
  down.
- "The totals do not grow with the size" (§5.1) is these two parts: a fixed
  2.0 s, and a first use that grows less than the weights.

The floor of a parked process, by two stand-ins (each process's own fdinfo;
the card was NOT otherwise empty, one live backend held 2.5 to 4.4 GB):
- a process holding the device with no weights on it (a medcpt-query copy,
  `gpu_layers 0`, context on demand, idle after one request): 308 MiB of
  card, 235 MiB of GTT (host memory the driver maps), RSS 237 MB;
- a bge-m3 copy with context on demand, idle after one request, its 577 MiB
  of weights on the card: 1,312 MiB of card and 880 of GTT, so 735 MiB
  beyond its weights.

The two figures are not counted twice across a process's two DRM clients
(München's check: the card's own counter gives 334 and 1,337 MiB for the
same processes by difference, and a live backend's second client holds 68
MiB and shares nothing). Why the bge-m3 copy idles at 1,312 MiB when the
keeper counts the live one at 621 is open.

So a parked process costs between 0.3 GiB (the first never computed on the
card) and 0.7 GiB (what the second holds beyond its weights, unless a park
also frees the engine's pools). Ten parked models are 3 to 7 GiB of the
card's 32. With one process per model THE FLOOR, not the weights, bounds how
many models a node serves; step 1 reads the real one.

## 6. The order, each step on the operator's word

| step | who | done when |
|---|---|---|
| 0. the four measurements | München | the numbers are in this note; go or no go |
| 1. the engine parks one encoder (bge-m3, 621 MiB) | Tensor | N park cycles give the same bytes; timed apart: the park, the un-park (allocate, copy, reorder), the first request after; the parked process's floor on the card read |
| 2. LocalAI's parked state, its two calls, the RAM budget; the wrapper | Tensor, then upstream | a parked model answers after an un-park; the gates pass on the build |
| 3. the question's plan on the two calls (`question_card`) | architecture | a cold question's first word within a second or two of the warm one's, measured on the node |

WHAT STEP 0 SAYS SO FAR (§5.2): go, and the gain is smaller than the totals
suggested. Parking takes a 4.5 s load to between 0.45 and 2.3 s, and a 7 s
load to between 0.9 and 3.9 s; where in that range is the rewrite at first
use, which step 1 times. Before step 1: LocalAI's poll sleep, 2.0 s of every
cold load, is the operator's choice (§5.2), and it pays with or without
parking; and one reading of the source asked of Tensor, what a process's
first compute on the card is made of. Still to measure: a load beside a working encoder (2), a
question's timeline on the live engine (3).

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
  host RAM beyond its weights (RSS 237 MB for the stand-in of §5.2), and on
  the card (0.3 to 0.7 GiB each, §5.2): whether a park frees the engine's
  pools, and whether several models could share one process's device
  context, which is not LocalAI's shape today.
- LocalAI's poll sleep (§5.2): left, halved by a key in each model's YAML,
  or removed by a server patch. With the operator.
- The bge-m3 copy's idle size on the card, twice the keeper's figure for
  the live model (§5.2).
- The chat model's prompt cache across a park.
- Page mode once parking exists: kept as the fallback for a backend that
  cannot park, or retired.
