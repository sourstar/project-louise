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
  to llama.cpp".

## 1. What it is for

Today a model is on the card or it is not. One that is not pays a full load
when it is next asked: MADLAD 8.70 s and the Nemotron reranker 7.51 s after
an unload (measured on the node, backend-qualification-2026-11-21.md, phase
5). So the keeper preloads what fits its weights budget, and a collection
with an encoder of its own asks which model leaves the card.

The aim is that the card's MEMORY stops deciding how many models the node
serves. Only the card's TIME is shared. The use cases this opens:
- ten collections embedding at once, each with its own encoder;
- an encoder per collection, chosen for the collection, never for the room;
- a larger chat model beside corpus work;
- a peer with a small card serving the same collections, slower.

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

VeGa's rule stands: "LocalAI is the only one handling VRAM on the card"
(vega.md §1). So Louise never loads or frees card memory herself. She asks.

| layer | what it does | where | why there |
|---|---|---|---|
| mechanism | a model PARKED: its process alive, its weights in host RAM, nothing on the card; putting it back is a copy | the engine (llama.cpp, our series) | nothing above the engine can do it |
| lifecycle | parks a model where it stopped one; un-parks it on a request; two calls, "prepare model X" and "park model Y" | LocalAI | it owns loading and eviction already; every client of LocalAI gains, with no scheduler at all |
| foresight | which model is on the card next, and when, ahead of the requests | RAGE | only RAGE knows the queue: which collections have work for hours, and that a person's question goes first |

### 3.1 The engine

- Context on demand (P11) already makes and frees a model's CONTEXT per
  request. Parking extends the same idea to the WEIGHTS: the device buffers
  are released, the host copy is kept, and an un-park allocates and copies.
- One backend carries nearly everything on the card: the encoders, the
  rerankers, the chat, MADLAD (the encoder-decoder patches) and OmniVoice
  (ported 2026-11-24, staged 0009 and 0010; operator: "I would like to have
  only one backend"). Outside it: medcpt-article on OpenVINO (312 MiB),
  parakeet (on the iGPU) and silero (CPU). They keep today's unload.
- The engine counts its uploads, as P11 counts context creations, and
  refuses rather than aborts when the device is full (staged 0013).

### 3.2 LocalAI

- A third state between loaded and unloaded: parked. Where the reclaimer or
  a load's room stops a backend today, it parks one that can be parked.
- A request to a parked model un-parks it. That alone serves every client of
  LocalAI: eviction becomes cheap, with no caller changed.
- Two calls, for a caller that knows what comes next: prepare, park. `/system`
  says each model's state.
- A host-RAM budget for parked models, and a real unload beyond it.
- It is generic, so it is proposed upstream (the pattern of
  `deploy/localai/upstream/`). Until it is taken, it is a patch we carry.

### 3.3 RAGE

- The plan per card: the next model is prepared while the current one
  works, when both fit the weights budget and the shared context budget.
- What leaves the card is the model whose next use is furthest away
  (Belady's rule: the queue is known), where today it is the oldest use.
- Turns are sized from the measured cost of a switch, so that switching is
  a small share of the card's time.
- A person's question first, as today: the tier-1 models stay on the card.
- Louise uses the two calls and nothing else (invariant 7).
- It changes WHAT RUNS WHEN: scheduling.md §6 binds it, with a row in
  scheduling-log.md before its deploy.

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

Nothing below is known today. Each decides a part of the design.

1. **A cold load's 7.5–8.7 s, split:** the process start, the file read
   (page cache cold and warm), the device's start, the copy, the first pass.
   Parking saves everything but the copy. If the copy is most of it, parking
   saves little.
2. **The copy's rate, host to B70,** for a 2.7 GB model. And whether a copy
   beside a working encoder slows that encoder: Clockwork's premise, to be
   shown on this card and driver.
3. **The card's busy share when two encoders alternate today**
   (`louise dev schedule-report --gpu`): what a plan could win.
4. **The host RAM that can be held for parked weights in the busy hours.**
   Measured 2026-11-24: 28.6 GB available with the node paused, 10–20 GB
   working, 4.7 GB at the lowest (the worker at 18 GiB beside two builds).
   The weights of everything loaded today are about 25.5 GB.

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
- If it holds, parking saves most of a load: what remains is the copy, a
  fraction of a second for an encoder.
- The copy's rate, indirectly: the driver brought 4.2 GiB of paged-out
  buffers back in 1.25–1.76 s (2.4–3.4 GiB/s); the 35B's whole load is at
  least 2.5 GiB/s with everything in it.
- A file out of the page cache costs far more than anything else (40.8 s
  against 8.1 s): parked weights held in host RAM are what avoids it.
- Not held: the split itself (1), a load beside a working encoder (2), the
  busy share with two encoders (3). Host memory per minute (4) is being
  logged since 2026-11-24, for seven days.

## 6. The order, each step on the operator's word

| step | who | done when |
|---|---|---|
| 0. the four measurements | München | the numbers are in this note; go or no go |
| 1. the engine parks one encoder (bge-m3, 621 MiB) | Tensor | N park cycles give the same bytes; the cycle is timed |
| 2. LocalAI's parked state, its two calls, the RAM budget; the wrapper | Tensor, then upstream | a parked model answers after an un-park; the gates pass on the build |
| 3. RAGE's plan | architecture | scheduling.md §6; a cost test; one deploy, measured on the node |

Each step is a deploy of its own. Each can stop there and still be worth
having: step 2 without step 3 already makes eviction cheap.

## 7. The alternatives

| choice | pros | cons |
|---|---|---|
| status quo | nothing to build | more models than fit means full loads, 7.5–8.7 s each |
| RAGE alone: planned turns over today's load and unload | no engine or LocalAI change | every switch is still a process start; only Louise gains |
| page mode (room-on-the-card.md, built, off) | automatic; +0.2–0.4 s for a small model, +1.3–1.8 s for the 35B (measured) | the driver chooses what moves and when; it holds host RAM; a full card killed a backend (0013 staged for it) |
| LocalAI and the engine alone | generic; could go upstream | reactive: the first request to a parked model waits for the copy |
| **both engines, in that order (the operator's choice)** | each layer where its knowledge is; RAGE adds two calls | the largest change; a LocalAI patch to carry until upstream takes it |

Existing tools, and why they are not the answer by themselves:
- **llama-swap** swaps whole llama.cpp server processes: every swap is a
  full load.
- **Triton's explicit model control** is the shape of the two calls (load
  and unload by API); its unload frees the model.
- **Ray Serve's model multiplexing** keeps an LRU of models per replica:
  the lifecycle of §3.2, without the foresight.

## 8. Open

- How the engine holds parked weights: the file's page cache, or a pinned
  host buffer. The first costs nothing more and can be dropped by the
  kernel; the second is certain and counts against the worker's neighbours.
- One process per model is LocalAI's shape: what a parked process costs in
  host RAM beyond its weights.
- The chat model's prompt cache across a park.
- Page mode once parking exists: kept as the fallback for a backend that
  cannot park, or retired.
