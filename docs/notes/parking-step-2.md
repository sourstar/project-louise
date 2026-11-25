# Parking, step 2: LocalAI parks where it unloads (design, 2026-11-25)

*REVIEWED by the architecture session 2026-11-25 (its answers are §4, the five
things it asked the note to add are §3.4); nothing here is built. Operator,
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
  model stays out of the LRU's count of loaded models, and counts against
  the card what its process still holds there (§3.4, 3). An explicit
  `/backend/shutdown` still stops the process — and does not park it first
  (§3.4, 1).
- **A request to a parked model** goes to its process, which un-parks
  itself; LocalAI marks it loaded again.
- **Two HTTP calls for a caller that knows what comes next** (step 3,
  Louise's): `POST /backend/park {"model"}` and `POST /backend/prepare
  {"model"}` — the second un-parks a parked model (`LoadModel` on the live
  process) and loads one that is not loaded. `/system` says each model's
  state and its host bytes.
- **The host's memory decides each park, when it happens** (§3.4, 5): one
  runtime setting, the host's floor for parking. Before a park LocalAI reads
  MemAvailable; if what would be left after the weights are copied is under
  the floor, the model is stopped instead. Unset — the default — nothing is
  parked. No static budget: the host at 15:00 is not the host at 03:00.
- **The readiness poll** (R6; operator, 2026-11-24: "Patch localAI when
  clockwork is implemented"): a started backend's health is asked every
  50 ms instead of every 2 s. It is what a cold load still pays once a
  parked return costs milliseconds. THE PATCH'S FIRST COMMIT, SEPARABLE
  (the architecture session): it gives up to two seconds on every cold load
  whether or not anything parks.

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
  other options refused as today. Today a repeated LoadModel drops the
  wrapper's `loaded`, hashes the weights and the tree again, restarts its
  counts and probes the tokenizer again, and an Embedding arriving meanwhile
  is refused "no run is stated" (the architecture session, which builds the
  change). IT RIDES THE ENGINE PART: the wrapper's files are inside what a
  run id hashes (München, from 2026-11-24: rebuilding the wrapper alone
  moved every vega run id and cost 61 minutes of gates), so a changed
  wrapper is a new run for every encoder and is gated with the park engine,
  never deployed on its own.

### 3.4 What the review asked the note to say (the architecture session, 2026-11-25)

**1. A park is not the prelude to a kill.** LocalAI calls `Free` before it
stops a process today, and `Free(HealthMessage)` carries nothing to say
which it is. LocalAI is the one that knows, so LocalAI decides: a model's
file says it parks (a model-config key, `park: true`, beside the engine's
own switches in its `env`); for such a model the STOP path does not call
`Free` at all — stopping the process frees the card, as it does for every
backend that has no `Free` — and the PARK path is the only caller. A LocalAI
that does not know the key (upstream's, or the distributed worker's unload)
calls `Free` and keeps the process: that parks, which is what the worker
meant. What a stop would cost if it did park first — E, from bge-m3's
measured rate (575 MiB copied out in 39.5 ms, 14.5 GiB/s): about 0.4 s and
6 GiB of host memory for MADLAD, and the 9B in proportion to what it holds
on the card. Both are M in the engine part's rows: the rate for a quantized
model and for buffers in pieces is not yet known.

**2. `/system` says the state, in a shape the keeper builds against.** A
parked model stays in `loaded_models` — it has a live process — with:

```json
{"id": "vega-bge-m3", "process": {"pid": 4711},
 "state": "parked",
 "parks": true,
 "size_vram": 336592896,
 "parked": {"size_host": 602931200, "size_vram_needed": 602931200, "since": "2026-11-25T06:25:29Z"},
 "load_options": {}, "load_options_hash": "…"}
```

- `state`: `"loaded"` or `"parked"`. Absent on a server without the patch:
  a client reads absent as loaded.
- `parks`: the model's file says it can be parked — what the keeper reads to
  choose `/backend/park` over `/backend/shutdown`.
- `size_vram`: UNCHANGED in meaning — the device memory its process holds
  NOW, as the driver accounts it. For a parked model that is the residual.
- `parked.size_host`: its weights' bytes held on the host.
- `parked.size_vram_needed`: what coming back commits on the card again —
  the room a client must see before it sends a request to a parked model.

A client that makes room (Louise's `ensure_loaded`, `model_evict`) must not
take a model listed here as on the card: `state: "parked"` is a load of
`size_vram_needed` bytes that costs milliseconds, and room is made for it
as for a load. The keeper's change, due WITH the server part.

**3. A parked process still holds card memory.** 321 MiB for bge-m3; ten
parked models are 3 GiB. Nobody has to estimate it: `size_vram` is the
driver's own count for the process and keeps saying it. LocalAI's reclaimer
counts it as it counts any process; the keeper's budget takes a parked
model at its `size_vram`, a loaded one as today.

**4. A refused return, as a client meets it.** When the weights cannot come
back — the card refused — the request is answered and the model stays
parked. Through LocalAI today that is HTTP 500, `rpc error: code = Internal
desc = …`, which no client can tell from a fault. With the server part:

```
HTTP 503
Retry-After: 2
{"error": {"code": 503, "type": "no_room_on_device",
           "message": "vega-bge-m3: the weights cannot come back to the device now"}}
```

from the backend's gRPC `RESOURCE_EXHAUSTED` — the code LocalAI's own
backends answer for "backend is busy"; not `UNAVAILABLE`, which LocalAI
reads as a backend it can no longer reach (`pkg/model/connection_errors.go`). The same status and type answer the two
refusals the engine already has — a context that cannot be created, and a
pool commit refused in the middle of a graph (today HTTP 500 "Compute
error.") — because all three are one fact: the card had no room at that
moment. A load that times out stays what it is (HTTP 500, another type).
For every client it is a DEFERRAL, never an error written to a document;
the clients' tests are the architecture session's.

**5. The host is not static.** 4.7 GB were available at the node's worst
hour. A budget of N GiB granted at 03:00 is wrong at 15:00, so there is no
budget: LocalAI reads MemAvailable before EACH park and parks only if the
floor holds after it; otherwise it stops the model, as today (§3.2). The
floor is one runtime setting, default unset, NEVER SET BY HAND: the keeper
writes it through `/api/settings`, as it writes the reclaimer's threshold,
from the node's one host-memory number — which now differs by mode (embed
mode, chat mode: main e231c4f1), and only Louise knows the mode.

## 4. Agreed with the architecture session (2026-11-25)

| | agreed | why, and what it changes |
|---|---|---|
| the two calls | LocalAI's own: `Free` parks, a repeated `LoadModel` un-parks | no new RPC |
| A. who decides to park | BOTH — and the keeper's first half ships WITH the server part, not in step 3 | on this node the one that evicts for room is Louise (`model_evict` → `/backend/shutdown`, the least recently used; LocalAI's reclaimer at 0.95 is only the net). With LocalAI alone step 2 would park almost nothing here. So with the server part the keeper's evictor calls `/backend/park` where the server offers it and the model parks, `/backend/shutdown` otherwise. Only the PREPARE half — the load ahead calling `/backend/prepare` — is step 3 |
| B. the host's memory | one LocalAI setting, default unset, written by the keeper, never by hand | one number for host memory, and it differs by mode; and it is a FLOOR checked at each park, not a budget (§3.4, 5) |
| C. page mode beside parking | BOTH, and which happens when is one sentence: over the card by at most `page_over_gib` with the host above its floor → the driver pages; otherwise room is made → park where the model can, stop where it cannot | they answer different questions |
| D. the order | engine, server, keeper | with A's amendment (the evictor's verb rides the server part) and the wrapper riding the ENGINE part (§3.3) |

The builds, then:

1. **The engine part** — the backend's `Free` and repeated `LoadModel`, the
   refusal switch, gRPC `RESOURCE_EXHAUSTED` for the three refusals; WITH
   the wrapper's repeated-LoadModel change (the architecture session's).
   One engine, one gating; its rows on copies (§3.1's table).
2. **The server part** — first commit the readiness poll; then the parked
   state, the two endpoints, `/system`'s shape, the floor, HTTP 503
   `no_room_on_device`. WITH the keeper's two changes: a parked model is
   not counted as on the card, and the evictor parks. A server build, no
   gate.
3. **Step 3** — the question's plan calls `/backend/prepare`.

Each part's patch is sent for review before it is staged.

## 5. What it costs

- One more engine after the paging-default one (the backend's two calls,
  the refusal switch, the wrapper's change with it), and every gate again.
- A LocalAI server patch larger than the three we carry: a state in the
  loader, two endpoints, a setting. Its tests are LocalAI's own suite, as
  for P13 and P19.
- Host memory, above the floor: a parked model holds its card bytes on the
  host — 575 MiB for bge-m3, about 2.2 GiB for nemotron, about 6 for MADLAD.
  On 11-24 the node had 4.7 GB available at its worst hour.

## 6. The engine part, as built — SECOND CUT, STAGED (2026-11-25)

**NO DEVICE HAS BEEN SEEN TO REFUSE A COMMIT.** That a refusal carries
`errc::memory_allocation` is the extension's specification and the runtime's
source, not an observation on the node, where the driver pages before it
refuses. So München's row with `GGML_SYCL_PARK_REFUSE` is a row of the SWITCH,
which raises that code by construction — and the first real refusal on a card
is a finding to record, whatever code it carries (the architecture session's
first condition on accepting this part).

`deploy/localai/patches/staged/0023-llama-cpp-park-when-told.patch`, in
`engine-park.series` = `engine-stream.series` line for line, then 0023:
engine **`ed93e3c1b2e2`**, not built. The first cut (ddc2fa1) was read
independently for the architecture session; what that reading found, and
what was done about each finding, is the table below. The second cut was
ACCEPTED for staging with three conditions: the lines above; the bound on
"not now" retries is the architecture session's to build in Louise's clients
with the keeper's half ("a deferral with Retry-After, and after N refusals
for one model in M minutes the evictor makes room or the request fails with
what it needs — never a silent loop"); and THE STREAM FIX IS NOT BEHIND
PARKING — a chat answer cut short is delivered as whole by today's engine, so
it is its own patch in the candidate built next (§6a).

**What it is.** §3.1, with LocalAI's two calls and no new one:

| the call | the model can park | it cannot (no context on demand, or weights that stay) |
|---|---|---|
| `Free` | the context is freed whatever keep it had, the weights leave: OK, message `parked`, metadata `{"parked":true,"park_size":…,"ms":…}`. Parked already: the same answer, nothing done. No host memory for the copy: `INTERNAL`, the weights stay on the device | `UNIMPLEMENTED`, as today: the caller stops the process |
| `Free` while a request runs, or while the first load runs | `FAILED_PRECONDITION`, "busy: …": the model stays | — (during the first load: the same `FAILED_PRECONDITION`) |
| `LoadModel`, the model already held, WITH THE OPTIONS OF ITS LOAD | the weights come back if they were away, then `Model already loaded`, the bytes the wrapper keys on | `Model already loaded`, today's no-op |
| `LoadModel`, the same name under OTHER options | `FAILED_PRECONDITION`, nothing brought back or loaded, the differing FIELDS named and never their values | the same |
| a request to a parked model | brings the weights back by itself, creates its context, answers. The model then STAYS on the device: a request never parks | — |
| `Status` | `READY`/`BUSY`; `memory.total` = the process's virtual size and `rss`, as LocalAI's own sample of a backend with no `Status`; breakdown `parked` (0/1), `park_size`, `parked_host`, `parks`, `unparks`, `no_room` | `UNIMPLEMENTED`, as today: LocalAI samples the process |
| no room when the weights must come back | `RESOURCE_EXHAUSTED`, message `no_room_on_device: the weights cannot come back to the device now`, the model still parked, the next request served | — |
| the return FAILS (the device fails, or the bytes read back are not the ones parked) | `INTERNAL`, "a failure, not a refusal": the model stays parked, its weights intact on the host. Never answered as a refusal | — |

**One answer for "the device has no room now".** A new error type in
llama.cpp's server (`no_room_on_device`, HTTP 503, counted in
`llamacpp:no_room_refusals_total`), which the backend answers as gRPC
`RESOURCE_EXHAUSTED` on every request RPC and on the repeated `LoadModel`:
parked weights the device has no room for; a context that cannot be created;
a graph the backend could not allocate memory for.

**The refusal says its type at the head of its message** (the first cut's one
amendment, found writing the server part). `RESOURCE_EXHAUSTED` has other
meanings on LocalAI's wire — a stream that is busy (`pkg/grpc/motion.go`,
`tts_live.go`), a message over gRPC's size limit (`core/backend/embeddings.go`
rewrites it as "conversation too long"), a rate-limited upstream
(`localai-proxy`) — so LocalAI's HTTP layer could not have mapped the bare
code to "no room" without calling those the same. Every refusal of the moment
reads `no_room_on_device: <what happened>`, built by ONE function in the
backend and told by `is_no_room_message` (`park_reply.h`).

| | pros | cons |
|---|---|---|
| the type at the head of the message (built) | one test on the status — code AND head — in LocalAI and in any client; a person reading a log sees it too; nothing new on the wire | a string is an interface: pinned by the header's own test |
| a gRPC trailer, as the canary's | not in the message | LocalAI's client makes a fresh call per RPC with no place that reads trailers: every call site would have to ask for them |
| the bare code | nothing to add | a busy stream and an oversized message answered "no room on the device" |

**THE INDEPENDENT READING OF THE FIRST CUT, FINDING BY FINDING** (the
architecture session's; its numbers):

| | the finding | in the second cut |
|---|---|---|
| 1 | every failed return was "no room" — bytes read back that were not the ones parked (`GGML_SYCL_PARK_VERIFY`), any exception of the device: a corruption retried for ever, and read as the refusal test passing | park and unpark say WHY (`ggml_backend_park_status`): `NO_MEMORY` only for `errc::memory_allocation` — the extension's own word, "If the constructor is unable to allocate the required memory … throws an exception with `errc::memory_allocation`" — and `FAILED` for anything else, answered 500 / `INTERNAL`, the model parked and intact. The pool's commit, LIVE since 0014, is held to the same test: any other exception is not survived as a refusal |
| 2 | a refused request to a parked model parked it again, ignoring that park's own result: a second copy of the weights per refused request, and a model that could be left on the device while the caller believed it parked | A REQUEST NEVER PARKS. Once back the weights stay; where they are is said (`Status`, `llamacpp:weights_parked`); the counts are of weights that left and came back, not of attempts. The measuring switch keeps its rule and reads what happened |
| 3 | under TODAY's server a refusal on embeddings is rewritten "conversation too long…" | "Under today's server", below; the server part stops that rewrite for a refusal |
| 4 | −2 and any context-creation failure meant "retry" for every model; a graph that never fits is deferred for ever | `llama_process` returns `LLAMA_PROCESS_NO_MEMORY` (−4) for the backend's allocation failure ALONE (the table below); the other −2 stay faults. What the engine cannot know — never against not now — is stated and counted, below |
| 5 | refusals that did not surface: a stream after its first chunk; the realtime RPC; the canary at the first load; OmniVoice's own decodes | the stream: a truncated answer delivered as complete, for ANY error, pre-existing — fixed as its OWN patch in the next candidate (§6a, staged 0025), and typed here; the realtime RPC: typed. The other two: stated limits, below |
| 6 | `Status` reported total 0 for a model on the device, where `/backend/monitor` read the process's memory | total is the process's virtual size again, with `rss`; the park's bytes are in the breakdown only |
| 7 | "Model already loaded" compared the NAME alone: the same name under other options un-parked and answered success while the old options computed — the dangerous one for invariant 12 | the backend keeps the options of its load and refuses a repeat that differs, `FAILED_PRECONDITION`, before anything is brought back, naming the fields and never their values (invariant 8). The wrapper's own refusal (#882) is the second lock |
| 8 | not inert for a model that cannot park | "What changes for EVERY model", below: the gate must cover it |
| 9 | counters and logs | parks/unparks count orders; `no_room_refusals_total` and `Status`'s `no_room`; −4's message says what it is; `Free` during the first load is `FAILED_PRECONDITION` |

**`llama_process` at the pin (78e0529 + the series), every way it does not
return 0** — read in `src/llama-context.cpp`:

| returns | where | what it is | answered |
|---|---|---|---|
| 1 | decode: the memory module found no slot for the batch | the context is full | retried with a smaller batch, then "Context size has been exceeded" |
| 2 | encode, decode: `GGML_STATUS_ABORTED` | the abort callback | as before |
| −1 | encode, decode: the batch itself | invalid input | 500 "Invalid input batch." |
| −2 | encode, decode: `output_reserve` came short | the HOST's output buffer | 500 `server_error`: a fault |
| −2 | decode: `memory->init_batch` gave nothing | the memory module could not begin the batch | 500: a fault |
| −2 | decode: `LLAMA_MEMORY_STATUS_NO_UPDATE` | a status that should not happen | 500: a fault |
| −2 | decode: `LLAMA_MEMORY_STATUS_FAILED_COMPUTE` | the memory module's own computation failed | 500: a fault |
| −3 | encode, decode: `GGML_STATUS_FAILED` | the graph failed | 500: a fault |
| **−4** (was −2) | encode, decode: `GGML_STATUS_ALLOC_FAILED` — the graph's buffers could not be allocated, or the device refused memory while it computed (the pool's refusal) | **the device was full at that moment** | **503 `no_room_on_device`** |

**What the engine cannot know: never against not now.** A graph that cannot
fit the card at all returns −4 at every attempt, as one that would fit a
minute later: the engine does not see the card's other tenants. It does not
guess. It counts (`llamacpp:no_room_refusals_total`, `Status`'s `no_room`),
LocalAI's 503 carries `Retry-After`, and WHO SENDS THE REQUEST AGAIN BOUNDS
ITS OWN RETRIES — Louise's clients as they bound a parse that never fits (the
architecture session's side). A context's creation that fails is answered
the same way on this reasoning: it was created with these very parameters at
the load, so what fails later is memory at that moment, the device's or the
host's.

**What changes for EVERY model the day this engine runs** — a model that
cannot park included; the gate must cover it:

| | before | with this engine |
|---|---|---|
| a context that cannot be created (context on demand) | gRPC `INTERNAL`; llama.cpp 503 `unavailable_error` | `RESOURCE_EXHAUSTED` "no_room_on_device: …"; 503 `no_room_on_device` |
| a graph the backend cannot allocate memory for | `INTERNAL` "Compute error."; 500 | `RESOURCE_EXHAUSTED` "no_room_on_device: Compute error: the device had no memory for this computation, try again later."; 503; `llama_process` −4 where it was −2 |
| a new context's canary refused for memory | "context canary failed", counted as a canary failure | a context that cannot be created; not counted |
| the canary refused for memory AT THE LOAD | a warning, the model served unchecked | the load fails (`INTERNAL`, as every load's failure) |
| an exception of the device in the pool's commit that is NOT an allocation failure | survived as a refusal | the backend leaves, as for every other exception of the device |
| a stream that meets an error after its first chunk | ends OK, the answer cut short | ends with the error's status |
| the same name loaded again under other options | "Model already loaded" | `FAILED_PRECONDITION`, the fields named |
| `Free`, `Status` | `UNIMPLEMENTED` before any check | the token is checked first, then `UNIMPLEMENTED` as before |
| `/metrics` | — | five more series (`weights_parked`, `weights_park_bytes`, `weights_parks_total`, `weights_unparks_total`, `no_room_refusals_total`) |
| llama.cpp's own `POST /park`, `/unpark` | 404 | 501 |

**Under today's server** — the interval between this engine and the server
part, for a client of LocalAI's HTTP. Every status is still HTTP 500 (today's
server maps no gRPC code); what moves is the text, and one rewrite:

| the request | the 500's message today | with this engine under today's server |
|---|---|---|
| embeddings, the context cannot be created | `rpc error: code = Internal desc = the context cannot be created now, …` | `conversation too long for per-token embeddings (gRPC message limit exceeded): rpc error: code = ResourceExhausted desc = no_room_on_device: the context cannot be created now, …` — LocalAI's own rewrite of ANY `RESOURCE_EXHAUSTED` on that route (`core/backend/embeddings.go`). The head is still in it: a client keys on `no_room_on_device: `, not on the words before |
| the same on chat, rerank, speech | `rpc error: code = Internal desc = …` | `rpc error: code = ResourceExhausted desc = no_room_on_device: …` |
| the slot cache | HTTP 500 | HTTP 507: that route maps `RESOURCE_EXHAUSTED` itself |
| a failover chain | the next target is tried for `INTERNAL` | the same for `RESOURCE_EXHAUSTED` (`core/services/failover/classify.go` takes both) |

Louise keys on "context canary failed" for a canary, and that message and its
trailer did not move. What its clients do with the other 500s is the
architecture session's; the server part answers 503 `no_room_on_device` and
stops the rewrite for a refusal.

**Where it differs from §3.1 — ALL FIVE ACCEPTED by the architecture session (2026-11-25), the canary's as "the more
important half: a model served unchecked behind a warning is the artefact invariant 12 exists against":**

| | §3.1 said | built | why |
|---|---|---|---|
| `Free` under a running request | "anything else → Unimplemented" | `FAILED_PRECONDITION` busy | Unimplemented means "this backend cannot park", for good; busy is for now. The server part takes the next candidate and never stops a model for it (agreed 11-25) |
| `Status` | says parked and the host's bytes | only for a model that can park; one that cannot stays `UNIMPLEMENTED` | LocalAI's `/backend/monitor` falls back to sampling the process when `Status` is unimplemented: answering for every model would have changed that endpoint for all of them |
| the return by `LoadModel` | "the weights come back" | the weights only; the context is the next request's, as with context on demand | a context created for nobody is freed at the next idle tick anyway; whether `/backend/prepare` asks for the context too is decided by a row (what a creation costs after a return) |
| automatic parking | removed | `LLAMA_SERVER_PARK_WEIGHTS` kept as the measuring switch it was, off by default — a measuring switch, never set in a live model's file | the rows of §3.1 can be run through today's LocalAI, which has no caller of `Free` but its stop path |
| a compute refusal at llama.cpp's own HTTP | (not said) | 503 `no_room_on_device`, where it was 500 `server_error` "Compute error." | one fact, one answer |

**The canary took a room refusal for a wrong context** (found writing the
first cut; the fifth point accepted). With context on demand and the canary
on — every encoder — the first graph of a context created on a full card IS
the canary. Refused for memory, it was answered `503 unavailable_error`
"context canary failed" and counted in `context_canary_failures_total`: a
determinism failure that never happened. The canary now says
`COMMON_CANARY_NO_MEMORY`: at a request it is a context that cannot be
created (`no_room_on_device`, counted nowhere as a canary that failed); at
the LOAD it fails the load, where before it left the model served UNCHECKED
for the life of the process behind one warning line.

**What an unpatched LocalAI does with it.** Today's server calls `Free` only
before it stops a process. For a model that can park — none until a model's
file says `GGML_SYCL_PARKABLE=1` — that stop now parks first: E, about 0.04 s
and 575 MiB of host memory for bge-m3, 0.4 s and 6 GiB for MADLAD (§3.4, 1),
freed when the process dies a moment later; and a `Free` that meets a running
request is logged "Error freeing GPU resources" before the stop, where
Unimplemented was a debug line. Harmless for the copies of the rows; the
reason the server part's stop path skips `Free` for such a model.
ACCEPTABLE ONLY BECAUSE no live model sets `GGML_SYCL_PARKABLE=1` before the server part is live
(the architecture session, 2026-11-25); tests/test_localai_review_park_when_told.py holds
every staged model file to it.

**Stated limits — known, not built:**

- **No device has been seen to refuse a commit** (this section's first lines). That its exception carries
  `errc::memory_allocation` is the extension's specification and the runtime's
  source (intel/llvm `physical_mem_impl.hpp`: out of resources, of host or of
  device memory → `memory_allocation`; anything else → `runtime`), not an
  observation on the node, where the driver pages before it refuses. A refusal
  that carried another code would be answered as a FAILURE — loudly, logged
  with its code — never retried in silence. The test switches raise
  `memory_allocation` by construction.
- **OmniVoice's own decodes are not typed**: a graph refused inside the speech
  pipeline is still "TTS generation failed", `INTERNAL`. The port takes no
  context on demand and does not park (§3.1's last row).
- **The canary refused for memory at the FIRST load** fails the load as
  `INTERNAL`, as every load that fails: a load is retried by its caller's own
  rule, not as a request's deferral.
- **Options a caller changes on purpose between a load and its repeat** — a
  credential a finalizer rotates — are other options: the repeat is refused,
  and the caller stops the process and loads afresh. None on this node.
- **A realtime session idle between two turns when `Free` arrives** was not
  judged by the reading and is not run here.

**MEASURED, on CPU builds, the device a stand-in** (`tools/measure/
park_cpu_standin.patch`: a weight buffer that copies its bytes aside and
poisons itself, refuses, FAILS, cannot park, or loses its bytes — in no
engine):

| what | how | result |
|---|---|---|
| the backend's gRPC handlers, compiled against the patched engine | `tools/measure/park_told.py selftest … --chat-gguf` | 49 of 49: a model that cannot park answers as before; the same name under other options refused, the fields named and nothing of their contents, parked or not; `Free`, `Status`, `LoadModel` as the table; the first vectors bit for bit over 12 cycles; a refused return `RESOURCE_EXHAUSTED` `no_room_on_device: …`, still parked, the next served; a FAILED return `INTERNAL`, still parked, counted as no refusal; weights that came back for a request whose context is refused stay — one park, one copy; a park that fails `INTERNAL`, nothing parked; some 300 calls of `Free` beside live requests, each done or refused busy, every request intact; a stream refused a graph after its first chunk ends `RESOURCE_EXHAUSTED`, the next one whole |
| llama.cpp's own server | `tools/measure/park_cpu_test.py` | 56 of 56 (17 of step 1, the rest told: the same cases over `/park` and `/unpark`) |
| a refused graph | `tools/measure/refuse_cpu_test.py … --told` | 19 of 19: every refusal 503 `no_room_on_device`, counted, none taken for a canary that failed, the same process, the next request the control's vectors |
| llama.cpp's server tests | `tools/server/tests/unit/test_park_weights.py`, `test_context_on_demand.py` | 25 passed |
| the messages and codes | `park_reply_test.cpp`, standalone; compiled and run from the patch by the repository's own test | all passed; the bytes of `Model already loaded` pinned |
| `ggml-sycl.cpp` | icpx, the one object | compiles |

**NOT measured: anything on a card.** After the review, on COPIES, with
`GGML_SYCL_PARK_VERIFY=1` (the card's bytes read back at every return — a
mismatch now FAILS the row, `INTERNAL`, where it would have read as a
refusal), the rows of §3.1 — München's, each through the backend's own
address:

    python3 -I tools/measure/park_told.py cycles ADDR <model> -n 20 --return request
    python3 -I tools/measure/park_told.py cycles ADDR <model> -n 20 --return load --options <its load_options of GET /system>
    # a refused return: GGML_SYCL_PARK_REFUSE=2 in the copy's env, then the same line:
    # two cycles REFUSED (RESOURCE_EXHAUSTED, still parked), the rest served

and the gate's clause (§3.3): the sixteen fingerprints after N cycles.

## 6a. The stream fix goes first: the candidate built next (2026-11-25)

**The defect (localai-bugs.md B21), on every chat model today.** LocalAI's
llama.cpp backend tested only the FIRST result of a stream for an error. A
later one — a computation failing in the middle of a generation — became one
more, empty, reply, and the call returned OK: an answer cut short by a
failure, delivered as complete.

| | pros | cons |
|---|---|---|
| its own patch in the candidate built NEXT, before parking (built: the architecture session's condition) | a wrong answer stops being delivered as right at the next engine swap, not two engines later; nine lines of LocalAI's backend, no llama.cpp change; one build and one gating with the paging default and the tag | one more line in a candidate the operator has not yet chosen |
| left inside the park patch | one patch fewer | "fix it after": every truncated chat answer until parking is live |
| status quo | nothing | a failure in the middle of an answer is invisible to every client |

`engine-stream.series` = `engine-lang.series` line for line, then staged 0025
(the fix) and 0026 (a test switch): engine **`792f050e318e`**, not built. It
stands in `d658549772ca`'s and `aa88ab5c356a`'s place if neither is gated: one
build, one gating for the paging default, the language tag and the stream.

**MEASURED**, on a CPU build of the backend at that base, a stand-in refusing
one graph when the first chunk of a stream is in
(`tools/measure/stream_fails_test.py`):

| the backend | the whole answer | a graph refused mid-way | the next answer |
|---|---|---|---|
| as it is in every engine so far | OK, 50 chunks | **OK, 4 chunks** — the defect | OK, 50 chunks |
| with 0025 | OK, 50 chunks | **INTERNAL "Compute error.", 3 chunks** | OK, 50 chunks, the same process |

**NOT measured: a card.** A card cannot be made to fail in the middle of an
answer: its driver pages before it refuses, and `GGML_SYCL_POOL_REFUSE`
refuses the next commits that GROW a pool, counted from the load — a pool has
done its growing before the first token. Hence 0026:
`GGML_SYCL_GRAPH_REFUSE=N` with `GGML_SYCL_GRAPH_REFUSE_ARM=<file>` — once the
file exists the next N graphs are abandoned before anything of them is
submitted, answered as a refused pool commit. Off unless a model's env sets
both; a test holds every staged model file free of it.

| | pros | cons |
|---|---|---|
| the switch in the candidate (built) | the card's own proof of the fix is in the pass; the same switch times any mid-request refusal later (parking's rows) | 29 lines of test code in the engine, one comparison per graph when unset |
| the candidate without it (drop the series' last line: another name) | nothing but the fix | the fix reaches the node with no row: measured on a CPU build only |

München's row, on a COPY of a chat model whose env has the two variables,
through the backend's own address:

    python3 -I tools/measure/stream_fails_test.py --addr ADDR --arm <file> --tokens 200

— it must end "ok: the stream ended as a failure …"; "DEFECT" is the old
behaviour. Through LocalAI's HTTP the same failure must end the SSE stream
with an error, not with a normal `[DONE]` after a short answer.

**ON THE CARD: IT HOLDS (München, 2026-11-25, the live engine
`619655e5833b`), through LocalAI's SSE** — the shape Louise meets; the
direct-address tool was not run. A copy of the 9B with
`GGML_SYCL_GRAPH_REFUSE=1` and its arm file:

| stream | data lines | content chunks | how it ended |
|---|---|---|---|
| whole | 161 | 155 | `finish_reason` "stop", `[DONE]`, no error |
| the file created when three content chunks were in | 11 | 7 | NO `finish_reason`; `{"error":{"code":"server_error","message":"rpc error: code = Internal desc = Compute error.","type":"server_error"}}`, then `[DONE]` |
| the file removed | 161 | 155 | `finish_reason` "stop", `[DONE]` |

The backend's ring: one "this graph is refused for the test", one "failed
with error -2". An answer cut by a failure now ends as one: before 0025 it
ended with a `finish_reason` and `[DONE]` after its seven chunks.

**BUILT, not live (München, 2026-11-25).** The name checked, the build
ended 0, installed BESIDE the live engine at 09:58:24Z as
`louise-sycl-f16-llama-cpp-792f050e318e-stack-7c301a7ba437`.
`d531d5d804f1` stays live; `d658549772ca` stays installed and was never
gated. Its swap and every gate wait for the operator's own yes to München,
which he tied to EUR-Lex5.next being repaired and drained: repaired and
indexing since 10:05Z, draining for hours. The keeper's paging default and
the staged speech model's backend name move when it is LIVE, not before.

## 6b. A canary refused for memory is not a canary that failed: cut out for the next engine (2026-11-25)

**What the node saw (München).** On the live engine `d531d5d804f1`, at
09:40:55Z, 27 seconds after a worker's start: one context of
`vega-qwen3-embedding-4b` discarded for not reproducing its canary — the
resident preload of 13 models had begun at 09:40:36Z, five voice pings were
running, the card went to 30,206 MiB of ~32 GiB. Louise did what it must
with the engine's word (invariant 12; operator, 2026-11-11: "Wire the canary
failure to the gate"): both statements of gutenberg's run set aside, its
embedding paused, the run gated again from scratch — validated at 10:20:41Z.
Forty minutes of one collection, no vector written from the discarded
context. ONE discard in 493 creations of that model, none on bge-m3 (16) or
nemotron (156), none since. The engine's own lines of that second had
rolled out of its ring: **that event is not proven to be what follows.**

**What a full card does, from the code and measured.** A context created
on demand computes two graphs before any request runs on it: llama.cpp's
warmup — an empty run whose failure llama.cpp IGNORES — and then the
canary. `canary_check` answered MISMATCH for every canary it could not
compute, and since staged 0018 a refused pool commit fails its graph
instead of ending the process. So a canary refused for memory was answered
`context canary failed`, counted, and logged "does not reproduce the canary
of the load": a determinism failure that never happened. At the LOAD the
same refusal answered NONE: the model served with "the contexts created on
demand are not checked", unchecked for the life of the process. On a CPU
build of `792f050e318e`'s llama.cpp source with the stand-in device that
refuses graphs (`tools/measure/canary_refused_test.py`): 503 `context
canary failed`, `context_canary_failures_total` = 1; at the load, served
unchecked — the tool's two DEFECT lines.

**The fix, staged 0028** (llama.cpp only; cut out of 0023's second cut,
§6 "The canary took a room refusal for a wrong context"):

| | it was | it is |
|---|---|---|
| `llama_process` for a graph the backend had no memory for | -2, like every other failure before a graph is computed | `LLAMA_PROCESS_NO_MEMORY` (-4). Still < -1: the server's own answer to a request ("Compute error.") is unchanged |
| the canary of a context created for a request | MISMATCH: `context canary failed`, counted | the context is discarded and it is a context that cannot be created: 503 `the context cannot be created now … try again later`, counted in no canary metric |
| the canary at the load | NONE: served unchecked | the load fails, and is tried again |
| a canary that was computed and differs, or that could not be computed for any other reason | `context canary failed`, counted | the same |

Through LocalAI every engine error is a 5xx carrying the engine's message.
`app/embeddings.py` tells a canary failure by the words `context canary
failed` and takes every other 5xx for a busy endpoint: the batch deferred,
its documents untouched, nothing set aside, nothing gated again. No line of
Louise moves, and no new error type is in this patch (that stays 0023's).

**THE WORDS THAT TELL THE TWO APART**, in a backend's own lines, in the
second around a discard — for the next time, on any engine before this one
(München keeps each model's ring around every worker start). Both end with
`the context created does not reproduce the canary of the load, it is
discarded (N so far)`. Before it:

| a refusal for memory | a real mismatch (invariant 12's case) |
|---|---|
| `…: the device refused N MiB for the pool (M MiB committed): …` | none of the lines on the left |
| `…: the graph is abandoned` | |
| `graph_compute: ggml_backend_sched_graph_compute_async failed with error -2` (once per refused graph: the warmup's, the canary's) | |
| `context canary: its output could not be computed on this context` | `context canary: N of M values differ (sizes A and B), the largest difference is G` |

"its output could not be computed" WITHOUT `failed with error -2` would be
a third thing — a compute that failed for another reason (`error -1`).

**The candidate: `engine-canary.series` → `619655e5833b`** —
`engine-stream.series` (`792f050e318e`) line for line, then 0028.
`792f050e318e` is built and installed beside the live engine, not gated.

| | pros | cons |
|---|---|---|
| build `619655e5833b` beside, and swap IT when EUR-Lex5.next is drained (recommended) | one swap and one gating carry the paging default, the language tag, the stream fix AND this; a full card at a worker's start no longer costs a collection its statements and forty minutes; an encoder loaded on a full card is never served unchecked | one more build on the node (sixteen minutes, during a drain that takes hours); `792f050e318e`'s build is not used, like `d658549772ca`'s |
| swap `792f050e318e` as built, `619655e5833b` at a later swap | no further build before the swap | two swaps and two gatings; between them every worker start on a full card can set a collection's statements aside again, and a chat answer is right while an encoder can still be served unchecked |
| neither: stay on `d531d5d804f1` (the status quo) | nothing moves | the stream defect (a chat answer cut short, delivered as complete), the language tag and this all stay |

If the operator says nothing: nothing is swapped — the node stays on
`d531d5d804f1`, both later engines wait, and the swap needs his own yes to
München as before. Building `619655e5833b` beside changes nothing that
runs.

**DECIDED (the operator, 2026-11-25, to München, verbatim): "Build
619655e5833b now, swap it after the drain, if it still applies".** The tree
e94b653 is on the node and the name checks there. The build is armed behind
the verdict of the app deploy of 11:08Z and is installed beside: nothing
live moves. The swap and every gate follow when EUR-Lex5.next has drained;
`792f050e318e` will not be swapped. München's pass carries this section's
card row.

**LIVE since 2026-11-25 15:18:30Z** (the operator, 13:40Z: "Deploy and
engine swap"). München: the byte check identical, the five llama.cpp
gates exact over three fresh loads with every fingerprint identical to
before, twenty unloads at exit 0. `d531d5d804f1` stays installed: the
engine a rollback puts back. The row on a copy was taken in the same
pass (below), with `keep_context_ms:0` on the copy — an on-demand context
is not released by itself (its keep defaults to -1), so without it the two
refusals would fall on the request's own graphs.

`ed93e3c1b2e2` (`engine-park.series`) is NOT renamed and does not list
0028: staged 0023 carries the same fix in its own form, on the same lines,
with the answer's type its own (`no_room_on_device`). A test holds the two
patches to the same lines, so one cannot move without the other.

MEASURED, on a CPU build of the candidate's source with the stand-in
(bge-small-en-v1.5 F16): before, DEFECT twice (6 of 8 checks); after, 12 of
12 — the refusal of a context's warmup and canary answered 503 "the context
cannot be created now", the counter at 0, the next request served with the
control's vectors bit for bit in the same process, a canary that does not
reproduce still answered and counted as one, the load refused, the same
load with room serving. `refuse_cpu_test.py` 13 of 13; llama.cpp's own
context-on-demand tests, 21.

**ON THE CARD: IT HOLDS (München, 2026-11-25, the live engine
`619655e5833b`).** A copy of bge-m3 on the engine directly, context on
demand and its canary on, `keep_context_ms:0`, `GGML_SYCL_GRAPH_REFUSE=2`
and its arm file (staged 0026):

| request | answer | the backend's ring |
|---|---|---|
| 1, 2 | 200, the vector `4ddc4b1be45e` (1024 dims) both times | the first on the load's own context; "context freed" after each; "its canary reproduced" for the second |
| 3, the arm file there | HTTP 500 in 0.021 s: `rpc error: code = Internal desc = the context cannot be created now, the memory it needs may be in use: try again later` | "this graph is refused for the test" ×2 (the warmup's, the canary's), "failed with error -2" ×2, "the device had no memory for its graph" ×1, "the device had no memory for the canary of the context created, it is discarded" ×1 — and "context canary failed", "does not reproduce the canary of the load", "values differ", "could not be computed": 0 |
| 4, 5, the file removed | 200, `4ddc4b1be45e` both times | "its canary reproduced" 3 in all, "context freed" 4, one process |

Through LocalAI the answer is HTTP 500, not llama.cpp's own 503: LocalAI
answers every backend error 500 with the engine's words inside. It is a
5xx without the words `context canary failed` — the one thing Louise's
client tests for — so the batch is deferred and nothing is gated again.
What the row does not show is a card REALLY full at a context's creation:
the switch refuses the graphs before the device is asked.

## 7. The server part, commit by commit

Each is a patch of LocalAI's SERVER, in no engine series, written on the
node's server tree (v5.0.1 + P1 + P13 + P19 = `v5.0.1-3`), and sent for
review before it is staged. A server build, no gate: the server is in no run
id.

**1. The readiness poll** — `staged/0024-model-readiness-poll.patch`
(§3.2; separable, it parks nothing). ACCEPTED as written by the architecture
session (2026-11-25): "it goes on the node with the server part, as the
operator tied it". After LocalAI starts a backend it asks
whether its gRPC service is ready, sleeps the configured delay — 2 s by
default — and asks again, up to 20 times. A backend ready after 100 ms is
seen two seconds later, on every cold load of every model.

| | pros | cons |
|---|---|---|
| ask every 50 ms inside the same wait (built) | a ready backend is seen at once; the wait is still attempts × delay spent sleeping, so a model's own `grpc.attempts` and `grpc.attempts_sleep_time` mean what they meant; one more check after the last sleep, where the old loop slept after its last | up to 800 health checks where there were 20, each a refused connection until the process listens — nothing beside a model's load |
| lower the default delay to 50 ms | one line | changes what every model file's `attempts` is worth: 20 attempts would wait 1 s, and a slow backend would be given up on |
| status quo | nothing to carry | up to 2 s on every cold load, most of what a small model's load costs |

MEASURED: its seven Go specs (ready at once: no sleep; ready after 150 ms:
seen at 150 ms; never ready: 40 s slept, no more and no less; a model's own
numbers; a delay under the interval; no delay; no attempts) and the whole
`pkg/model` suite on the server tree.

**ON THE NODE: IT HOLDS (München, 2026-11-25 night, server
`v5.0.1-6-gffb10fdf3`).** Three cold loads of `louise-rerank-medcpt`, the
first request each: 4.84 / 4.48 / 4.45 s before, 2.98 / 2.83 / 2.67 s
after — 1.5 to 1.9 s less on a cold load, R6's 2 s the expectation.

**2. The parked state, `/backend/park`, `/system`, the floor, 503** —
`staged/0027-model-park.patch`, STAGED (2026-11-25). On the server tree after 0024.
**AMENDED THE SAME DAY after its first run on a real server (§8):** a model
whose last request began or ended under a second ago is busy for a park.
**THIRD CUT (2026-11-25).** The architecture session read the first and
ACCEPTED it for staging with two changes ("The review's two changes",
below). An INDEPENDENT READING of the same patch, asked for beside theirs,
then found sixteen things; each is fixed, stated as a limit, or answered in
"The independent reading, finding by finding", below. The architecture
session read this third cut (at 5633e543: the lock's try, the return path,
the answers) and ACCEPTED it: "Both changes are as asked … B's rows are
each what I would have asked for; the three that change my half are taken
as written (404 type model_not_loaded; 500 type model_stopped — I skip the
shutdown on it; /backend/load 503 no_room_on_device with Retry-After). The
host read against the container's own limit is the important one." What it
leaves open are conditions, not changes: LocalAI's Postgres suites on the
node's build tree before the server build is used; `park: true` reaching a
real load as the first row on the park engine. A parked model outside the
idle timeout is the keeper's.

What it does (§3.2, §3.4), in the order a model meets it:

| | |
|---|---|
| a model's file says `park: true` | it may be parked; and, in a process LocalAI is about to stop, it is no longer sent `Free` on its way to being STOPPED (§3.4, 1). One reached by address has no process to stop and is sent `Free` as before |
| the keeper writes the host's floor (`park_host_floor_bytes`, through `/api/settings`; 0 by default) | parking is on. Before EACH park LocalAI asks the backend what it would move (`Status`, `park_size`), reads what the host can still give — its available memory and, under a container's memory limit, no more than the limit still allows — less what the parks in flight are about to copy, and parks only if the weights fit above the floor at that moment; otherwise the model is stopped as before (§3.4, 5) |
| LocalAI would stop a model to get the DEVICE back (the limit of loaded models, a concurrency group, the reclaimer when it watches a GPU) | it parks one that can park instead. A model not busy when it was selected is never stopped for being busy at the moment of the park: the eviction fails, the model is kept and tracked again, the caller selects again |
| an idle timeout; a reclaimer that watches the host's RAM; an eviction selected while busy (`force_eviction_when_busy`, the busy timeout) | the model is STOPPED, as before: the first wants it gone, the second would only fill the RAM it is asked to give back, the third is the operator's forced stop |
| `POST /backend/park {"model"}` (the keeper's evictor) | parks, waits for nothing but the park itself, never stops a model that works: 200 with `parked{…}`; 409 `model_busy` (take another) — a request is running, OR a load, a return or a stop of the same model holds its lifecycle lock, which is tried and never waited for; 412 `cannot_park` / `parking_off` / `host_memory_floor` (stop it instead, if room is still wanted); 404 `model_not_loaded`, written by the route so that it differs from the 404 of a server WITHOUT the route; 500 `model_stopped` when the backend broke the park (answered `Free` without parking, or not within 60 s) and was stopped — the device is free, the model is not loaded |
| a request to a parked model, or `POST /backend/load` | the model comes back BEFORE the request goes, under its lifecycle lock: the limits are enforced as for a load, the model is one of the loaded models again from the moment its `LoadModel` is sent, then one `LoadModel` with the options of its load. No room: the request is refused (503, on `/backend/load` too) and the model stays parked. ANY OTHER failed return: the process is stopped and the request goes on to an ordinary load, from the file |
| `GET /system` | for a model that parks: `"parks": true`, `"state": "loaded"` or `"parked"`, and when parked `"parked": {"size_host", "size_vram_needed", "since"}`; `size_vram` keeps its meaning |
| a backend's refusal of the moment | HTTP 503, `Retry-After: 2`, `{"error": {"code": 503, "type": "no_room_on_device", "message": "<model>: …"}}`, told by gRPC `ResourceExhausted` AND the head `no_room_on_device: ` of its message; in the middle of a chat stream, an error event of that type. A load refused for room does not arm the load-failure cooldown |

**Where it differs from §3.2 and §3.4 — for the review to accept or refuse.
ALL FIVE ACCEPTED (the architecture session, 2026-11-25), the fourth for
MEMORY only: a parked model that is BROKEN must not wait for the keeper —
change 1 below:**

| | the note said | built | pros | cons |
|---|---|---|---|---|
| the call that brings a model back | a new `POST /backend/prepare` | none: `POST /backend/load`, which exists, IS it — it loads a model that is not loaded and now brings back one that is parked | one endpoint fewer to carry; the existing verb already means "have it ready"; the keeper's load-ahead calls what it calls today | the name does not say "from a park": a reader of the API must know a parked model counts as not ready |
| `state` and `parks` in `/system` | `state` on every model, absent only on a server without the patch | only on a model that PARKS; a model that does not park is listed as it always was | LocalAI's own spec holds the exact entry of a model, and every other model's output is unchanged for upstream and for every client; the keeper needs them only where `parks` is true | "absent" now means either "an unpatched server" or "this model does not park": the keeper reads `parks`, never the absence of `state` |
| who brings a parked model back at a request | "goes to its process, which un-parks itself; LocalAI marks it loaded again" | LocalAI sends the `LoadModel` itself, before the request | its record is exact (no request result to watch — LocalAI's client has no place every call passes through); the limits are enforced as for a load; one place answers the typed 503 | one more RPC before the first request after a park; the backend's own return remains for a caller that does not go through LocalAI |
| a parked model and the watchdog | "stays out of the LRU's count … counts against the card what its process still holds" | not one of the loaded models at all until it is back: not counted, not an eviction candidate, not idle- or busy-checked | simple and exact: the registration is redone when it comes back | LocalAI never stops a PARKED model by itself — not for the host's memory, not for the residue its process keeps on the card (321 MiB for bge-m3). That decision is the keeper's, which sees `size_host` and `size_vram`; the reclaimer cannot reclaim a residue |
| the floor | "one runtime setting, default unset" | `park_host_floor_bytes`, 0 = nothing is parked, read at each park | no restart of anything; the keeper's change of mode is followed at the next park | it is persisted with the other runtime settings: after a restart of LocalAI the last floor written still stands until the keeper writes again |

**The review's two changes (the architecture session, 2026-11-25), built:**

| | it was | it is | pros | cons |
|---|---|---|---|---|
| 1. a return that FAILS (the backend says so, holds the model under other options, is gone, answers without success, or has not answered in 60 s) | the request got the fault and the model STAYED PARKED: the next request sent the same `LoadModel` to the same process and got the same fault, and by the fourth difference nothing ever stopped it — a model that needed an agent to come back | the process is stopped (`ErrParkedReturnFailed`, an Error line naming the model and the cause), and the request that met the failure goes on to an ordinary load from the file. A deferral (`no_room_on_device`) still leaves the model parked | no state fails for ever; no reader gets the fault of a process that is about to be stopped; the next park starts from a process that never failed | a failure is paid as a load (tens of seconds for a large model) instead of an error, so it is seen in the log and in the time, not in an HTTP code — München's rows count the line; a stop that itself fails leaves the model as it was and the next request tries both again |
| 2. `/backend/park` behind a load, a return or a stop of the same model | it waited for the model's lifecycle lock as long as its caller let it: the keeper's one evicting thread, held behind a cold load | the lock is TRIED: not free at once is 409 `model_busy`, like a running request. The watchdog's own evictions do the same for a model that parks | the route's word "never waits" is true; one stuck model holds no other eviction | a health probe of the model that is in flight at that instant also reads as busy (it holds the lock for its duration): the keeper takes another, or asks again |

**A deadline on the return** (the architecture session left it to this
part): it STOPS the process, and it can only be the return's own 60 s. A
return that has been sent is followed to its end whatever its caller does —
the `LoadModel` does not carry the request's cancellation — so a reader who
leaves in the middle of the copy leaves an exact record, and a return that
has not answered in 60 s is a backend that hangs (the copy was 15.5 ms for
bge-m3 on the card, parking-prototype.md §4a), not a slow one.

| a return's deadline | pros | cons |
|---|---|---|
| stop the process (built) | nothing fails for ever, whatever the backend is doing; one rule for every failure | a copy that really needed more than 60 s would be paid as a load each time: none is near it — say so if a card row shows one |
| leave it parked for the next `LoadModel` to find loaded | no load is paid if the copy was only slow | a backend that hangs fails every request after 60 s, for ever: the state the review refuses |

**The record can be stale ONE way, and the server does not close it.** A
request that took its client from `Load` BEFORE a park and reaches the
backend AFTER it is served by the engine's own return (staged 0023: "a
request to a parked model brings the weights back by itself"). LocalAI's
record then says `parked` and the watchdog does not track the model, while
its weights are on the device, until the model's next `Load` — whose
`LoadModel` the engine answers "already loaded", which makes the record
exact again. The server has no place every call passes through where it
could see this (the third difference). What the keeper can rely on:
`size_vram` in `GET /system` is the LIVE reading of the process at each
call (`xsysinfo.ProcessVRAM`, read from `/proc` when the route answers — no
cache), for a parked model as for any other. So a model listed `parked`
whose process holds its `size_vram_needed` on the card is loaded, and
`POST /backend/load` on it makes the record say so. The architecture
session first took that reconciliation into the keeper and then WITHDREW it
the same night ("my test for it was unsound, and the record heals at the
model's next request by your own design. Louise will never call
/backend/load"): nothing reconciles the record but the model's next request
through LocalAI — and since §8's amendment a park no longer lands inside a
request, which was the common way to get there.

**The independent reading, finding by finding** (a fresh agent given the
commit and the surrounding tree, not this note; 2026-11-25). Fourteen were
real, in whole or in part:

| # | found | now |
|---|---|---|
| 1 | a model coming back was invisible to the limit of loaded models and to group exclusivity until its `LoadModel` had answered: a load starting during the copy counted neither it nor a pending load | registered with the watchdog BEFORE the `LoadModel` is sent; untracked again if the return is refused or fails |
| 2 | a park whose caller left, or whose `Free` passed 60 s, kept the old record: a model listed loaded while its weights may be in host memory | a `Free` that has been sent is followed to its end whatever the caller does; one that has not answered in 60 s stops the process (`model_stopped`). The return had the same hole: closed by the review's change 1 |
| 3 | a return that failed left the model parked for ever | the review's change 1 |
| 4 | the limits were enforced BEFORE the lifecycle lock and the re-check of "parked": of two requests, the second could evict the model the first had just brought back; and after a failed return the request assumed "not loaded" although another request might have loaded it | enforced inside the return, under the lock, after the model is found still parked; after a failed or vanished return the store is looked at again (three looks at most) |
| 5 | "no room" was 500 on `/backend/load` — the route that brings a parked model back — and on `/v1/messages`, `/v1/responses` | `/backend/load` answers 503 `no_room_on_device` with `Retry-After`; the other two write their own error bodies and are a stated limit |
| 6 | an idle timeout PARKED a model that parks, and nothing ever stopped it; a reclaimer watching RAM "evicted" by filling RAM | only an eviction that wants the device back parks; the idle timeout and a RAM reclaimer stop |
| 7 | the host read was the HOST's `MemAvailable` capped at the container's total, whatever the container already used; two parks could both pass it | `xsysinfo.AllocatableMemory`: the minimum of the host's figure and the cgroup's limit less its working set (v2 and v1); the bytes of the parks in flight are reserved |
| 8 | a `park: true` model reached by address was no longer sent `Free` when stopped — the only thing that released it | `Free` is skipped only where there is a process to stop |
| 9 | the watchdog waited for the lifecycle lock before it knew whether the model parks: up to 65 s more for a model that does NOT park | answered before the lock is looked at (the review's change 2, same cut) |
| 10 | every `FAILED_PRECONDITION` from `Free` read as busy — the backend also says it of a model it does not hold, and a model "busy" for ever is never evicted | busy is the code AND the head `busy: ` of the message, which the engine writes on its three busy answers (staged 0023); anything else is "cannot park", so the model is stopped as before |
| 12 | the failover prober sends inference straight to a loaded backend: a parked target would be un-parked behind the loader's back | a parked model is "not loaded" for a probe |
| 13 | a load refused for room armed the load-failure cooldown: "ask again in 2 s", then locked out | not recorded as a failure |
| 14 | `/backend/park`'s 404 was rewritten "Resource not found" by the error handler — the answer of a server without the route; and the route, said never to stop anything, stopped a model whose `Free` did not park | 404 `model_not_loaded` written by the route; 500 `model_stopped`, and the route's words say the one case |
| 15 | the reclaimer untracked an address after a "successful eviction" — right for a stopped process, wrong for a parked one whose return may have registered it again | a parked target is not untracked a second time |
| 16 | the specs: no loader spec attached a watchdog, none called the real `ParkModelAtAddress`, none held `park: true` → load options or the setting → loader, the 404 was asserted against echo's handler, the stream's event and the embeddings mapping were untested, "the stop" ran only the no-process branch | 8 specs with a real watchdog; the real gRPC client's `FreeResult` against a gRPC server; the file's switch to the load options; the setting to the loader; the error body, the stream's event, the embeddings mapping; the stop of a model in a real process. Two of them were mutated to fail (registration after the `LoadModel`; parking without `forRoom`): 6 specs failed, as they should |

Kept, and said: **11** — with `force_eviction_when_busy` a busy model that
parks is force-stopped, never parked. That is the operator's setting doing
what it says; the commit's sentence is now "a model NOT busy when it was
selected is never stopped for being busy at the moment of the park". And
"takes another" was too strong: the caller selects AGAIN, and a model still
busy then is skipped. What the reading checked and found right — no
self-deadlock, no lock-order inversion, one `LoadModel` for two requests,
an eviction in flight settled, `NoRoomOnDevice` through `%w` and
`errors.Join`, a model that does not park unchanged — stands.

**Stated limits — known, not built:** `/v1/messages` and `/v1/responses`
write their own error bodies and answer a refusal 500 as before; a parked
model is outside the idle timeout — LocalAI never stops it by itself (the
fourth difference); `/backend/park` can answer 409 for an idle model whose
health probe is in flight at that instant; that `park: true` reaches the
MODEL a real load creates (`spawnGRPCModel`) is one line no spec here runs
— it is München's first row on the park engine: a model loaded from a file
with `park: true` is listed `"parks": true`. Also: the mid-stream error event is typed
on the chat route only (completions and realtime keep `server_error`); a
caller inside LocalAI that formats an error into a string before it
returns loses the type and answers 500 as before (chat and embeddings pass
it on; the others are not audited); a model held by a remote worker
(distributed mode) is not parked from the frontend; the settings page and
the generated API description do not show the new setting and route.

MEASURED (Go, where it was written, 2026-11-25): the loader against a
scripted backend, 40 specs; the loader and a REAL watchdog together, 8; the
watchdog's routing, 6; the real gRPC client's `FreeResult` against a gRPC
server, 3; the container-aware host read, 6; `park: true` to the load
options and the embeddings mapping, 3; the setting to the loader and the
failover probe, 2; the endpoint, `/system`, `/backend/load`'s 503, the
error body, the stream's event. Whole suites green: `pkg/model` (274 of
274), `pkg/xsysinfo`, `pkg/grpc`, `core/config`, `core/backend`,
`core/schema`, `core/http/endpoints/openai`. Under the race detector the
54 park specs are clean; the whole suite's three reports are one spec of
LocalAI's own (`process_exit_test.go`, a log buffer read while it is
written), which this patch does not touch. (With the amendment of §8: 41
loader specs, `pkg/model` 275 of 275, 278 with staged 0029's three.) `core/application`: 346 of 350
— the four that fail are LocalAI's OAuth login against a local TLS server,
which this container's proxy breaks; they fail the same without the patch.
NOT measured: a real backend (it needs the park engine), a card; and 47
specs of `core/http/endpoints/localai` and those of
`core/services/monitoring` that start a Postgres container could not run
here — no Docker — each failing at that start. **The architecture session's
condition on the server build: those two suites whole and green on the
node's server tree before the build is used there — München's to run.**

It does NOTHING until two things are true on the park engine: the keeper
has written the floor, and a model's file says `park: true`.

## 8. The two halves together: LocalAI's real server and a real backend (2026-11-25)

The operator, relayed by München: **"Do parking."** München cannot reach a
backend's own address (it listens inside the model server's container), so
the rows of §6 that go through gRPC cannot be taken on the node as written.
With the server part they do not have to be: `tools/measure/park_http.py`
drives parking through LocalAI's HTTP alone — the five routes a caller that
manages the device uses, which is what Louise's keeper does.

**It was run here first, on the real thing but for the card:** LocalAI's
server built from the node's server tree with staged 0024, 0027 and 0029
(`go build ./cmd/local-ai`), starting LocalAI's own llama.cpp backend built
for CPU from the park engine's source with the stand-in device
(`GGML_CPU_PARK_TEST`, dev only), two model files that say `park: true` and
one that does not, bge-small-en-v1.5 F16. No scripted backend, no stand-in
server: the first time the two halves met.

**It found two things no spec had, and one of LocalAI's own:**

| found | what it was | now |
|---|---|---|
| a park landed INSIDE a request | one HTTP embeddings request is one backend call PER INPUT; between two of them no call is in flight, so the model was not "busy" — 46 parks were accepted during a single request of 2,048 sentences. The answers stayed right (each next input brought the weights back); the weights were copied to the host and straight back 46 times | a model whose last request began or ended under a second ago is busy for a park (`parkIdleMin`; the client says how long it has been without a REQUEST — health checks and `Status` are not one). Re-run: 13,812 parks asked during the same request, 13,812 × 409 `model_busy` |
| the tool read `/system` wrong | LocalAI lists the models under `"loaded_models"`; the tool said `"models"` and was right only against its own stand-in | the tool reads the real key, and a test holds the shapes below against it |
| **LocalAI's own: a settings write that never answers** (localai-bugs.md B24) | `POST /api/settings` restarts the watchdog and waits for its loop to end; the loop is only started where a periodic check is on. With a limit of loaded models and no busy check, idle check or reclaimer, the write hung (300 s here, then the tool's timeout) holding the watchdog's lock, and every later write behind it. The setting itself had been applied | staged 0029: a watchdog whose loop never ran is not waited for. The same write: 0.09 s. THE NODE IS NOT IN THAT COMBINATION (München, 21:24Z: busy check on, no limit) — and must not be put in it before 0029 is in its server |

**The rows, on the final build** (each line is the tool's own output):

    cycles SERVER copy -n 20 --return request
    each park was asked right after a request: answered 409 model_busy 200 times in all before the model had been idle for its second
    20 of 20 cycles: the same bytes; a park 51.3 ms (median, 48.0-57.5), a return with its sixteen sentences 1193.6 ms (1126.7-1407.4)

    cycles SERVER copy -n 20 --return load
    each park was asked right after a request: answered 409 model_busy 200 times in all before the model had been idle for its second
    20 of 20 cycles: the same bytes; a park 49.7 ms (median, 46.5-57.9), a return with its sixteen sentences 1230.9 ms (1165.6-1289.2)

    busy SERVER copy
    2048 sentences in one request; while it ran, 8772 parks answered: {(409, 'model_busy'): 8772}
    the request itself: HTTP 200, vectors identical
    afterwards: parks, state 'loaded', size_vram ?
    ok: a busy model is refused 409 model_busy, and is still there

    refused SERVER copy          (the stand-in refuses the next two returns)
    parked; the sixteen vectors ea8f98c32f34
    request 1: HTTP 503 'no_room_on_device' Retry-After '2': copy: the weights cannot come back to the device now; parks, state 'parked', size_vram ?, 63 MiB on the host since 2026-11-25T21:35:08.364116731Z
    request 2: HTTP 503 'no_room_on_device' Retry-After '2': copy: the weights cannot come back to the device now; parks, state 'parked', size_vram ?, 63 MiB on the host since 2026-11-25T21:35:08.364116731Z
    request 3: served, vectors identical; parks, state 'loaded', size_vram ?
    ok: 2 return(s) refused as a deferral, then served

    stop SERVER copy             (a PARKED model shut down: the keeper's move when the host runs short)
    parked: parks, state 'parked', size_vram ?, 63 MiB on the host since 2026-11-25T21:35:01.247706173Z; its process 9950
    /backend/shutdown of the parked model: HTTP 200 in 103 ms: {"message": "model stopped"}
    /system afterwards: not listed
    the next request: HTTP 200 in 1.37 s, vectors identical; parks, state 'loaded', size_vram ?; its process 10938
    ok: a parked model is stopped as it is, and loads afresh

    failed SERVER copy           (the stand-in fails the next return)
    parked; its process 11212
    the request to it: HTTP 200 in 1.64 s, vectors identical; parks, state 'loaded', size_vram ?; its process 11246
    ok: the failed return cost a load, not an error

The backend's own lines agree: one "weights parked" and one "weights
unparked" per cycle. A return on this CPU is the copy plus sixteen sentences
embedded; the times are not the card's. And LocalAI's own limit of loaded
models, set to 1, with two models that park and one that does not: asking
for the second PARKED the first (the same process answered it later — a
return, not a load), asking for the third parked again, and the one that
does not park was stopped as it always was.

**That a model's file reaches the model a real load creates** — the one line
no spec ran (§7's stated limit) — is shown: after a real load `/system`
lists `"parks": true, "state": "loaded"`.

**THE SHAPES, read off the real server** (the keeper's half was built
against 0027's text; these are its bytes):

    GET /system, nothing loaded      {"backends":["llama-cpp-park"],"loaded_models":[]}
    a model that parks, loaded       "parks": true, "state": "loaded"        (a model that does not park: neither key)
    POST /backend/park {"model":"copy"}
      200  {"message":"model parked","parked":{"size_host":66547200,"size_vram_needed":66547200,"since":"2026-11-25T21:01:40.683232191Z"}}
           asked again while parked: the same answer, nothing done twice
      404  {"error":{"code":404,"message":"model \"nobody\": not loaded on this instance","type":"model_not_loaded"}}
      409  {"error":{"code":409,"message":"model \"copy\": model is still busy: its last request was 7 ms ago, and a model is parked once it has been without one for 1s","type":"model_busy"}}
      412  {"error":{"code":412,"message":"model \"copy\": parking is off: no host memory floor is set","type":"parking_off"}}
      412  {"error":{"code":412,"message":"model \"plain\": model cannot be parked: its file does not say `park: true`","type":"cannot_park"}}
      412  {"error":{"code":412,"message":"model \"copy\": parking would leave the host under its memory floor: 14953 MiB available, 0 MiB being parked by others, 63 MiB to park, a floor of 1048576 MiB","type":"host_memory_floor"}}
      500  {"error":{"code":500,"message":"parking model \"copy\": rpc error: code = Internal desc = the weights could not be parked, they stay on the device (is there host memory for them?)","type":""}}      (the engine said the park failed: the model stays loaded)
      400  {"error":{"code":400,"message":"code=400, message=model is required","type":""}}
    a refused return, on /v1/embeddings and on /backend/load alike
      503, Retry-After: 2   {"error":{"code":503,"message":"copy: the weights cannot come back to the device now","type":"no_room_on_device"}}
    POST /backend/load {"model":"copy"}      200  {"loaded":["copy"],"message":"model loaded"}
    POST /backend/shutdown of a parked model  200  {"message": "model stopped"}
    the floor: `park_host_floor_bytes`, BYTES, among the 97 keys of GET /api/settings (0 from the start);
      written by POST /api/settings with the whole object; read back as written

**NOT shown here, and only the card can:** the device's memory given back by
a park and taken by a return (`size_vram` is not read on a CPU), a card that
refuses a return for its own reasons, parkable buffers under the paging
default (0021) — the two have never run together on a card — and the times.

**THE PASS ON THE NODE** (München; the order and its reasons):

1. THE SERVER FIRST — the node's live tree, then `git am` of staged 0024,
   0027, 0029 in that order. `git rev-parse HEAD^{tree}` must read
   `54dae233a83d5921371bb77e1bdca489e4af6731` before anything,
   `8b7a1216b2cbec186dad14a31a127151dfe6cfc8` after 0024,
   `8faa0e471dfde057709dc6c36568a49b4ade415f` after 0027 and
   `e37b86e089b38d2667de8368965eddd1e6bf0782` after 0029; `--version` reads
   `v5.0.1-6-g…`. It is inert: nothing parks until the floor is above 0 AND a
   model's file says `park: true`. No gate. Before it is used: LocalAI's
   `core/http/endpoints/localai` and `core/services/monitoring` suites on
   its builder and on the live tree's (the control) — they start their own
   Postgres through the Docker socket — and `pkg/model` reading "Ran 278 of
   278".
2. THE ENGINE `ed93e3c1b2e2` (built 21:28:13Z): every llama.cpp vega run
   gated again, with the wrapper's part for a return through it
   (architecture's branch `wrapper-same-load`) in the vega package; the
   rollback is `619655e5833b`.
3. The two rows whose answers MOVE with this engine and server: the canary
   refused on a copy is now HTTP 503 `no_room_on_device` with
   `Retry-After: 2` (it was 500); the stream's error object is typed
   `no_room_on_device` (it was `server_error`). BOTH SEEN HERE FIRST, on the
   real server with the park engine's CPU build and the stand-in that
   refuses graphs: the one request with the arm file there answered `503`,
   `Retry-After: 2`, `{"error":{"code":503,"message":"the context cannot be
   created now, the memory it needs may be in use: try again later",
   "type":"no_room_on_device"}}`, no canary failure counted, the requests
   before and after served; and a chat stream of 60 chunks, a graph refused
   once three were in, ended after 4 with NO `finish_reason`, the object
   `{"code":"no_room_on_device","message":"Compute error: the device had no
   memory for this computation, try again later.","type":
   "no_room_on_device"}` and `[DONE]`, the next stream whole again.
4. PARKING ON A COPY — bge-m3 on the engine directly, context on demand,
   `park: true`, env `GGML_SYCL_PARKABLE=1` and `GGML_SYCL_PARK_VERIFY=1`,
   the floor at 12 GiB for the rows and back to 0 after:

        python3 -I tools/measure/park_http.py floor   SERVER 12884901888
        python3 -I tools/measure/park_http.py state   SERVER COPY
        python3 -I tools/measure/park_http.py cycles  SERVER COPY -n 20 --return request
        python3 -I tools/measure/park_http.py cycles  SERVER COPY -n 20 --return load
        python3 -I tools/measure/park_http.py busy    SERVER COPY
        python3 -I tools/measure/park_http.py stop    SERVER COPY
        python3 -I tools/measure/park_http.py refused SERVER COPY      # a second copy whose env adds GGML_SYCL_PARK_REFUSE=2
        python3 -I tools/measure/park_http.py floor   SERVER 0

   and, with the fingerprint texts, the gate's clause of §3.3: the sixteen
   fingerprints after the cycles. `failed` has no switch on a card and is
   not run there.
5. Parking near a REAL model is not in this pass: `GGML_SYCL_PARKABLE=1` in
   an encoder's env is a new run, gated, and which models park is the
   keeper's half and the operator's.

**STEP 1 ON THE NODE: THE SERVER IS BUILT, NOT SWAPPED (München,
2026-11-25 evening).** The four trees as above at every step; its HEAD
`ffb10fdf3`, `--version` `v5.0.1-6-gffb10fdf3`, image
`louise-localai:v5.0.1-gpu-intel-p1-p13-p19-poll-park`. In its builder:
`pkg/model` "Ran 278 of 278 Specs … 278 Passed", and `pkg/xsysinfo`,
`pkg/grpc`, `core/config`, `core/backend`, `core/schema` ok. **LocalAI's two
suites that need Postgres RAN WHOLE there** (testcontainers started its
own) — the first time anywhere for this patch:

| | `core/http/endpoints/localai` | `core/services/monitoring` |
|---|---|---|
| the live tree's builder (the control), two runs | ok, ok | ok, ok |
| the new tree's builder, three runs | 741 of 742, then ok, ok | ok, ok, ok |

The one failure is LocalAI's own websocket spec (localai-bugs.md B25: the
handler subscribes to new lines after it has sent the lines so far), in
files no patch touches. Counted here before the condition was called met:
the spec alone, alternately on both trees under the same load, 24 failures
in 300 on the new tree and 20 in 300 on the live one; 1 in 200 on each on a
quiet machine. The same rate. **The architecture session's condition on
the server build is met.**

**THE PASS (München, started 2026-11-25 22:46Z, the node paused; as it
stood at 23:29Z).** THE SERVER IS LIVE: "running: v5.0.1-6-gffb10fdf3", the
settings check clean, `/system` listing its 105 backends as before and no
row with `parks` or `state` (the keys are absent on a model that does not
park), `park_host_floor_bytes` 0, and the seven run ids under the new
server THE SAME as before. ONE vega package rebuilt, `vega-louise-llama-cpp`
(the architecture session's decision: the wrapper's change is inert in the
other three), in front of `ed93e3c1b2e2`, its wrapper carrying
`ALREADY_LOADED = "Model already loaded"`; the byte check identical. The
gates so far, each exact over three fresh loads with its fingerprint as
before: wikipedia (bge-m3) 98 s, gutenberg (qwen3-embedding-4b) 1,983 s,
pubmed query (medcpt-query) 70 s; NeuRIS and wikidata, the twenty unloads,
the acceptance checks, the two rows whose answers move and parking's rows
on copies were still to come.

