# Parking, step 2: LocalAI parks where it unloads (design, 2026-11-25)

*For the architecture session's review; nothing here is built. Operator,
2026-11-25, to the choice "build step 2, designed with the architecture
session first": "I accept your recommendations for all the decisions above".
It is `models-beyond-the-card.md` §6 step 2 — "LocalAI's parked state, its
two calls, the RAM budget; the wrapper; the health-check poll at ~50 ms" —
with what step 1 measured on the card (`parking-prototype.md` §4a) and what
LocalAI v5.0.1's source already holds.*

## 1. What step 1 settled

| question of the design note | answer, measured on the B70 (bge-m3, F16) |
|---|---|
| does a model compute the same behind memory that can leave the card | yes: the same bytes, 36.9 against 36.8 ms a request |
| does it come back the same | yes, at every cycle, checked on the card itself |
| what does coming back cost | 15.5 ms (derived: 0.3–0.9 s); a request that finds its model parked takes 58.6 ms against 36.8 |
| what does a parked process keep of the card | 321 MiB, 2 above a process that never had weights; 575 MiB given back |
| what does a real unload cost | 6.28 s for the same model's next request |

So the copy is no longer the question. Step 2 is plumbing: who says "park",
who says "come back", and how much host memory parked models may hold.

## 2. The finding that shapes it: LocalAI's wire already has both verbs

Read in v5.0.1 (`dd5780a`), not assumed:

- `backend.proto` has `rpc Free(HealthMessage) returns (Result)`. LocalAI
  calls it before it stops a backend, "to release GPU resources"
  (`pkg/model/process.go`); a backend that does not implement it answers
  Unimplemented and nothing is lost.
- In DISTRIBUTED mode LocalAI already keeps the process after it:
  `core/services/worker/lifecycle.go`, `unloadModel` — "call gRPC Free() to
  release GPU memory without killing the backend process"; a backend whose
  Free did not succeed is stopped instead, because "a second LoadModel on
  that live process is not safe for all of them".
- The llama.cpp backend answers a repeated `LoadModel` for the model it
  holds with success, "Model already loaded": "a repeated request is a
  no-op, not a reload".

That is park and un-park, already on the wire with the meaning we need.
`models-beyond-the-card.md` §3.2 planned "two new RPCs in LocalAI's
backend.proto, additive as P1 was". They are not needed.

| the two calls | pros | cons |
|---|---|---|
| `Free` = park, a repeated `LoadModel` = un-park (LocalAI's own) | no change to backend.proto; the single-node loader is brought to what the distributed worker already does, which is an argument upstream can take; the VeGa wrapper proxies `Free` untouched (it intercepts `LoadModel` and `Embedding` only) | `Free` says nothing about HOW MUCH was freed: LocalAI must learn the state from its answer; `LoadModel` is one of the two calls the wrapper intercepts, so a repeated one has to be taught to it (§3.3) |
| two new RPCs, `Park` and `Prepare` | their meaning is exactly ours | a wire change to carry and to propose, for a meaning the wire already has |
| nothing (today) | nothing to build | every eviction is a process stop: 6.3 s for bge-m3, 9.5–11 s for MADLAD, 12.5 s for the 35B |

Recommended: LocalAI's own two. If nothing is said, I design to them.

## 3. The three parts

### 3.1 The engine's backend (ours; a new engine, gated)

- **`Free`**: no request running and the model able to park (its weights
  behind memory that can leave the card; no context that must stay) → the
  context is freed, the weights parked, the answer is success with the
  message `parked`. Anything else → Unimplemented, as today, and LocalAI
  stops the process. Never a half state: a park that fails brings the
  weights back before it answers.
- **`LoadModel` for the model already held**: parked → the weights come
  back, then "Model already loaded", as today. Not parked → today's no-op.
  Another model → today's refusal.
- **A request to a parked model** un-parks it by itself (the prototype
  does this at the context's creation): no caller has to know.
- **Parking is told, not automatic.** The prototype parks whenever a
  context is freed (its measuring switch `LLAMA_SERVER_PARK_WEIGHTS`). In
  step 2 a model keeps its weights on the card between requests, as today,
  and parks only on `Free`.
- A model is ABLE to park by its file: `GGML_SYCL_PARKABLE=1` (the weights
  behind mapped memory, measured as fast as before) and context on demand.
  A model without them answers Unimplemented: today's unload.
- `Status` says `parked` and the bytes held on the host, for `/system` and
  for the budget.

What must be shown on the card before any model is switched to it — each a
row on a COPY, with the prototype's verify switch (the card's bytes read
back and compared at every cycle):

| not yet tried | why it could differ | the row |
|---|---|---|
| a QUANTIZED model (qwen3-embedding-4b, Q4_K_M) | the engine rewrites quantized weights on the card at first use; parking copies the card's own bytes back, flags untouched — by construction, never run | N cycles, verified, the vectors the un-parked model's |
| buffers above 1 GiB (MADLAD, the 9B chat) | committed in pieces; none has been tried | the same |
| a refused un-park | a card cannot be made to refuse by filling it; the CPU stand-in showed the path above the device only | a switch like 0016's, `GGML_SYCL_PARK_REFUSE=N`: the request answered 503, the model still parked, the next one served |
| the 35B (experts on the host, a cache on the card) | its card part is the dense layers and the expert cache; its context is not on demand today | after the three above; the cache's own state is the context's and goes with it |
| OmniVoice (two model files) | context on demand refuses a model with a second file today | last, or it keeps today's unload |

### 3.2 LocalAI's server (ours to carry; proposed upstream)

- **A third state, parked**, in the single-node loader: where a model is
  stopped FOR ROOM today — the memory reclaimer, the LRU limit, a load's
  eviction — LocalAI calls `Free`; success with `parked` keeps the process
  and marks the model parked; anything else stops it, as today. A parked
  model counts nothing against the card and stays out of the LRU's count of
  loaded models. An explicit `/backend/shutdown` still stops the process.
- **A request to a parked model** goes to its process, which un-parks
  itself; LocalAI marks it loaded again.
- **Two HTTP calls for a caller that knows what comes next** (step 3,
  Louise's): `POST /backend/park {"model"}` and `POST /backend/prepare
  {"model"}` — the second un-parks a parked model (`LoadModel` on the live
  process) and loads one that is not loaded. `/system` says each model's
  state and its host bytes.
- **A host-memory budget for parked models** (a runtime setting): the sum
  of their host bytes may not pass it; over it, the least recently used
  parked model is stopped for real. The default is 0 — nothing is parked
  until a node says how much.
- **The readiness poll** (R6; operator, 2026-11-24: "Patch localAI when
  clockwork is implemented"): a started backend's health is asked every
  50 ms instead of every 2 s. It is what a cold load still pays once a
  parked return costs milliseconds.

### 3.3 VeGa and invariant 12 (`models-beyond-the-card.md` §4, unchanged)

- A parked model keeps its process, engine, flags and weights: the same
  validated run, no gate per un-park.
- The engine that can park is a new build: gated once per space.
- The gate gains its clause: the sixteen fingerprints after N park cycles,
  as after 300 context creations.
- The wrapper (the architecture session's, on main): `Free` is proxied
  untouched today. `LoadModel` is intercepted — the wrapper states a run
  at a load — so a repeated `LoadModel` with the options of the first must
  be let through as the SAME run, not stated as a second one, and one with
  other options refused as today. That is a change in the wrapper, and its
  packages are rebuilt.

## 4. What I need the architecture session to choose

| | choices | pros | cons |
|---|---|---|---|
| A. who decides to park | LocalAI alone, at every eviction for room | every client gains; nothing for Louise to do until step 3 | LocalAI evicts by least recent use, not by a question's plan |
| | the keeper too, with the two HTTP calls (step 3) | Louise parks what the question will not use and prepares what it will | step 3's work; needs the calls of §3.2 first |
| B. the host-memory budget | a LocalAI setting, the node's number | one place; LocalAI enforces it for every client | a second number beside the keeper's floor (`page_floor_gib`), to be kept consistent by hand |
| | the keeper's own floor decides, LocalAI's budget left large | one rule for host memory, the one page mode already uses | LocalAI's own evictions (no keeper involved) are then bounded only loosely |
| C. page mode once parking exists | both: paging the default safety net, parking for what is evicted | paging needs nothing of anyone; parking gives the card back for real, where paging holds it committed up to 3 GiB over | two mechanisms to explain |
| | parking only, page mode retired | one mechanism | a backend that cannot park falls back to a full unload; the default just built is thrown away |
| D. the order of the builds | engine first (its rows on copies), then the server, then the keeper | each is a deploy of its own and can stop there | a third pass after tonight's two |

My recommendations: A both, LocalAI first; B a LocalAI setting whose default
is 0, set on the node from the keeper's floor; C both; D engine, server,
keeper. If nothing is said I design to these and send each part's patch for
review before it is staged.

## 5. What it costs

- One more engine after the paging-default one (the backend's two calls,
  the refusal switch), and every gate again.
- A LocalAI server patch larger than the three we carry: a state in the
  loader, two endpoints, a setting. Its tests are LocalAI's own suite, as
  for P13 and P19.
- Host memory, by the budget: a parked model holds its card bytes on the
  host — 575 MiB for bge-m3, about 2.2 GiB for nemotron, about 6 for MADLAD.
  On 11-24 the node had 4.7 GB available at its worst hour.
