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

## 6. The engine part, as built — FOR REVIEW (2026-11-25)

`deploy/localai/patches/review/0023-llama-cpp-park-when-told.patch`: in no
series, built by nobody, until the architecture session has read it. It
applies after staged 0022 (`engine-lang.series`'s lines).

**What it is.** §3.1, with LocalAI's two calls and no new one:

| the call | the model can park | it cannot (no context on demand, or weights that stay) |
|---|---|---|
| `Free` | the context is freed whatever keep it had, the weights leave: OK, message `parked`, metadata `{"parked":true,"park_size":…,"ms":…}`. Parked already: the same answer, nothing done | `UNIMPLEMENTED`, as today: the caller stops the process |
| `Free` while a request runs | `FAILED_PRECONDITION`, "busy: …": the model stays | — |
| `LoadModel`, the model already held | the weights come back if they were away, then `Model already loaded`, the bytes the wrapper keys on | `Model already loaded`, today's no-op |
| a request to a parked model | brings the weights back by itself, creates its context, answers; the model then STAYS on the device | — |
| `Status` | `READY`/`BUSY`; `memory.total` = the weights' bytes in HOST memory now; breakdown `parked` (0/1), `park_size`, `parks`, `unparks` | `UNIMPLEMENTED`, as today: LocalAI samples the process |
| no room when the weights must come back | `RESOURCE_EXHAUSTED`, message `no_room_on_device: the weights cannot come back to the device now`, the model still parked, the next request served | — |

**One answer for "the device has no room now".** A new error type in
llama.cpp's server (`no_room_on_device`, HTTP 503), which the backend answers
as gRPC `RESOURCE_EXHAUSTED` on every request RPC and on the repeated
`LoadModel`: parked weights that cannot come back; a context that cannot be
created; a graph the device refused memory for (`llama_process` −2).

**AMENDED once since it was sent (2026-11-25, found writing the server part): the refusal says its type at the head of
its message.** `RESOURCE_EXHAUSTED` has other meanings on LocalAI's wire — a stream that is busy (`pkg/grpc/motion.go`,
`tts_live.go`), a message over gRPC's size limit (`core/backend/embeddings.go` rewrites it as "conversation too long"), a
rate-limited upstream (`localai-proxy`) — so LocalAI's HTTP layer could not have mapped the bare code to "no room" without
calling those the same. Every refusal of the moment now reads `no_room_on_device: <what happened>`, built by ONE function in
the backend and told by `is_no_room_message` (`park_reply.h`); the code is unchanged, and so is everything else in the
patch. The llama.cpp half did not move.

| | pros | cons |
|---|---|---|
| the type at the head of the message (built) | one test on the status — code AND head — in LocalAI and in any client; a person reading a log sees it too; nothing new on the wire | a string is an interface: pinned by the header's own test |
| a gRPC trailer, as the canary's | not in the message | LocalAI's client makes a fresh call per RPC with no place that reads trailers: every call site would have to ask for them |
| the bare code | nothing to add | a busy stream and an oversized message answered "no room on the device" |

**Where it differs from §3.1 — ALL FIVE ACCEPTED by the architecture session (2026-11-25), the canary's as "the more
important half: a model served unchecked behind a warning is the artefact invariant 12 exists against". The patch's own
reading is still under way; nothing is staged before its findings:**

| | §3.1 said | built | why |
|---|---|---|---|
| `Free` under a running request | "anything else → Unimplemented" | `FAILED_PRECONDITION` busy | Unimplemented means "this backend cannot park", for good; busy is for now. The server part takes the next candidate and never stops a model for it (agreed 11-25) |
| `Status` | says parked and the host's bytes | only for a model that can park; one that cannot stays `UNIMPLEMENTED` | LocalAI's `/backend/monitor` falls back to sampling the process when `Status` is unimplemented: answering for every model would have changed that endpoint for all of them |
| the return by `LoadModel` | "the weights come back" | the weights only; the context is the next request's, as with context on demand | a context created for nobody is freed at the next idle tick anyway; `/backend/prepare` (step 3) may ask for more |
| automatic parking | removed | `LLAMA_SERVER_PARK_WEIGHTS` kept as the measuring switch it was, off by default — a measuring switch, never set in a live model's file | the rows of §3.1 can be run through today's LocalAI, which has no caller of `Free` but its stop path |
| a compute refusal at llama.cpp's own HTTP | (not said) | 503 `no_room_on_device`, where it was 500 `server_error` "Compute error." | one fact, one answer. Through LocalAI's HTTP nothing changes until the server part maps the gRPC code (today every backend error is 500) |

**Found on the way, fixed in it: the canary took a room refusal for a wrong
context.** With context on demand and the canary on — every encoder — the
first graph of a context created on a full card IS the canary. Refused for
memory, it was answered `503 unavailable_error` "context canary failed" and
counted in `context_canary_failures_total`: a determinism failure that never
happened. The canary now says `COMMON_CANARY_NO_MEMORY`: at a request it is a
context that cannot be created (`no_room_on_device`, counted nowhere as a
failure); at the LOAD it fails the load, where before it left the model
served UNCHECKED for the life of the process behind one warning line.

**What an unpatched LocalAI does with it.** Today's server calls `Free` only
before it stops a process. For a model that can park — none until a model's
file says `GGML_SYCL_PARKABLE=1` — that stop now parks first: E, about 0.04 s
and 575 MiB of host memory for bge-m3, 0.4 s and 6 GiB for MADLAD (§3.4, 1),
freed when the process dies a moment later. Harmless for the copies of the
rows; the reason the server part's stop path skips `Free` for such a model.
ACCEPTABLE ONLY BECAUSE no live model sets `GGML_SYCL_PARKABLE=1` before the server part is live
(the architecture session, 2026-11-25); tests/test_localai_review_park_when_told.py holds
every staged model file to it.

**MEASURED, on CPU builds, the device a stand-in** (`tools/measure/
park_cpu_standin.patch`: a weight buffer that copies its bytes aside and
poisons itself, refuses, or loses them — in no engine):

| what | how | result |
|---|---|---|
| the backend's gRPC handlers, compiled against the patched engine | `tools/measure/park_told.py selftest` | 33 of 33 on the amended patch (29 of 29 when it was first cut; four added since for a park that FAILS — no host memory for the copy: `Free` answers `INTERNAL`, nothing is parked, the model answers, the next `Free` parks): a model that cannot park answers as before; `Free`, `Status`, `LoadModel` as the table; the first vectors bit for bit over 12 cycles (6 returned by `LoadModel`, 6 by the request); a refused return `RESOURCE_EXHAUSTED`, still parked, the next served; 302 calls of `Free` beside live requests — 151 parked, 151 busy, every request's vectors intact |
| llama.cpp's own server | `tools/measure/park_cpu_test.py` | 48 of 48 (43 of 43 when the patch was cut — 17 of step 1, 26 told; five added for a park that fails: 500 `server_error`, the weights on the device, the model answering) |
| a refused graph | `tools/measure/refuse_cpu_test.py … --told` | 17 of 17: every refusal 503 `no_room_on_device`, none counted as a canary that failed, the same process, the next request the control's vectors |
| llama.cpp's server tests | `tools/server/tests/unit/test_park_weights.py`, `test_context_on_demand.py` | 25 passed |
| the messages and codes | `park_reply_test.cpp`, standalone | all passed; the bytes of `Model already loaded` pinned |
| `ggml-sycl.cpp` with `GGML_SYCL_PARK_REFUSE` | icpx, the one object | compiles |

**NOT measured: anything on a card.** After the review, on COPIES, with
`GGML_SYCL_PARK_VERIFY=1` (the card's bytes read back at every return), the
rows of §3.1 — München's, each through the backend's own address:

    python3 -I tools/measure/park_told.py cycles ADDR <model> -n 20                 # the return by LoadModel
    python3 -I tools/measure/park_told.py cycles ADDR <model> -n 20 --return request
    # a refused return: GGML_SYCL_PARK_REFUSE=2 in the copy's env, then the same line:
    # two cycles REFUSED (RESOURCE_EXHAUSTED, still parked), the rest served

and the gate's clause (§3.3): the sixteen fingerprints after N cycles.

## 7. The server part, commit by commit — FOR REVIEW

Each is a patch of LocalAI's SERVER, in no engine series, written on the
node's server tree (v5.0.1 + P1 + P13 + P19 = `v5.0.1-3`), and sent for
review before it is staged. A server build, no gate: the server is in no run
id.

**1. The readiness poll** — `review/0024-model-readiness-poll.patch`
(§3.2; separable, it parks nothing). After LocalAI starts a backend it asks
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
`pkg/model` suite on the server tree. NOT measured: a cold load's seconds on
the node before and after — München's row, the same model, R6's 2 s the
expectation.

**2. The parked state, the two calls, `/system`, the floor, 503** — being
written (§3.2, §3.4).

