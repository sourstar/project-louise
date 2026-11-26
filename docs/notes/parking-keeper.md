# Parking, Louise's half: the evictor parks, the keeper holds the floor (2026-11-25)

Operator, 2026-11-25, to München: "Do parking." The design is
`parking-step-2.md` (§4: A — who decides to park, B — the host's memory);
this is what Louise does with a LocalAI that carries the park patch
(staged `0027-model-park.patch`), built against that patch's exact answers.
**Without the patch, or with no model whose file says `park: true`, nothing
here runs: no row says `parks`, no park is asked, no floor is written**
(tests/test_parking.py, the first test of each part).

## 1. What changes, in the order a model meets it

| | before | now |
|---|---|---|
| a load needs room (`model_evict.make_room`) | the model with the oldest real use is stopped (`/backend/shutdown`) | the same model, in the same order, is PARKED where LocalAI lists it `"parks": true` (`POST /backend/park`), stopped otherwise |
| a question takes the card from the corpus encoders (`question_card.free_card`) | each is stopped | each is parked where it can; one already parked is left |
| a parked model in `/system` | — | listed, and NOT on the card: its `size_vram` is what its process still holds (321 MiB for bge-m3), never learnt as a size; the budget, the margin and the over-commitment count it as read |
| a request to a parked model (`model_load.ensure_loaded`) | — | room is made for its return as for a load, then one minimal request brings it back; logged `parked: … came back from host memory in 0.02 s` |
| the keeper and a parked model of its set | — | not loaded: brought back as a model is loaded — in the budget, with room, behind the beat |
| HTTP 503 `no_room_on_device` | a 5xx | the same deferral for documents (`EndpointBusy`); a question's calls keep their fallback and can tell it from a fault (`httpc.no_room`, `ModelRefused.retry_after_s`) |

What `/backend/park` answers, and what the evictor does (`model_load.park`,
`model_evict.give_back`):

| answer | meaning | the evictor |
|---|---|---|
| 200 `parked{size_vram_needed}` | the weights are in host memory | counts that many bytes freed — never more than the model held; its residue stays |
| 409 `model_busy` | it works, or its lock is held | NOT stopped; the next candidate is asked |
| 412 `cannot_park` / `parking_off` / `host_memory_floor`; 404 with no type (no such route); anything else | nothing moved | stops it instead (`/backend/shutdown`), as before |
| 404 `model_not_loaded`; 500 `model_stopped` | it is off the card | counted, and no stop is sent after it |

A parked model is a candidate LAST — after every loaded one, in the same
oldest-use order — and is then stopped for what its process still holds:
nothing of it is left to park.

**What the check before the first victim counted is made** (the review of
2026-11-25). That check counts each victim at all it holds, and a park gives
back only the weights. Where the parks leave the room short, the models just
parked are stopped for what their processes still hold, oldest use first,
until it fits — otherwise a model was parked and another stopped for a load
that still had no room (reproduced: 360 MiB free, a 900 MiB model parked, a
1,000 MiB one stopped, "1,935 of 2,200 MiB made").

## 2. The keeper's two duties (`resident._parked_follow`, each beat)

| duty | what it does | pros | cons |
|---|---|---|---|
| the floor | where a loaded model says it parks, LocalAI's `park_host_floor_bytes` is held to `[models] page_floor_gib` (12 GiB) — read whole, one key set, written whole, read again; the object's secrets never kept or said | one host-memory number for paging and parking; never set by hand; a server without the patch is never asked | one number for two uses: a node that wants parking with less host room than paging has to lower both |
| the host | when MemAvailable is under the floor, the parked model used longest ago is stopped, one a beat, never a pinned one (`model_evict.host_claims`) | the parse pool's room comes before a quick return (43 memory kills of the worker, 2026-11-19); LocalAI never stops a parked model itself | in embed mode at a busy hour (4.7 GB available at the worst, 2026-11-24) everything parked is stopped within a few beats: parking then pays only while the host has room — which is the hour it is safe |

The status quo, for comparison: every give-way is a stop. Pros: nothing in
host memory, nothing new to go wrong. Cons: 6–34 s to come back, each time
(model-profile.md), where a park's return was 15.5 ms for bge-m3.

## 3. Not built, and why

- **The stale record is NOT reconciled by Louise — built, found unsound,
  withdrawn the same night.** The server's record can say "parked" for a
  model whose weights a crossing request brought back (parking-step-2.md
  §7). A first cut told that case from the reading ("nearer what the model
  holds loaded than what a park leaves") and called `/backend/load`; the
  review reproduced the misread — after one partly paged reading a truly
  parked bge-m3 (337 MiB held, 603 to return) was taken for on the card,
  and the keeper would have un-parked it at every beat. Now a model listed
  parked IS parked for Louise, and Louise never calls `/backend/load`: the
  model's next request — or the keeper's own load, where the budget holds
  it — makes the record exact by the server's own design ("already
  loaded"). Until then Louise makes room for a return that costs nothing,
  which errs on the side of room.

- **The question's own models (the answer's chat, MADLAD) are never
  parked**: only a model on context on demand whose weights can leave the
  device parks (the engine's rule), and which files say `park: true` is the
  operator's choice.
- **No retry of a question's call on 503.** The server says "ask again in
  2 s"; room is made before every load and return, so a refusal is a race.
  Standard alternative, written down: honour `Retry-After` once for a call a
  person waits on (RFC 9110 §10.2.3) — to be judged on the first refusal a
  card is seen to give ("NO DEVICE HAS BEEN SEEN TO REFUSE A COMMIT",
  parking-step-2.md §6).
- **`/v1/messages` and `/v1/responses`** answer a refusal 500 (the server's
  stated limit): Louise calls neither.

## 4. Measured on the card, on a COPY (München, the parking pass, 2026-11-25 22:46Z – 2026-11-26 00:19Z)

Server v5.0.1-6-gffb10fdf3 (0024 + the amended 0027 + 0029), engine
ed93e3c1b2e2, through LocalAI's HTTP alone, on a copy of bge-m3; the floor
set to 12 GiB by the tool and back to 0 after. No real model parks.

| | measured |
|---|---|
| a park | 142 ms |
| a return, with sixteen sentences asked | 228 ms; 20 of 20 cycles the same bytes, by a request and by `/backend/load` alike — the TOOL's own sentences: the gate's clause (the sixteen FINGERPRINT texts after park cycles) was not run, and is owed to the gated run of the first real model that parks |
| what a park gives back | 996 of 1,315 MiB (76 %): 577 MiB are in host memory, and the process still holds 319 MiB of the card — the reason `make_room` counts a park at what the answer says moved, never at the model's whole size |
| a busy model | 1,084 parks asked during one 2,048-sentence request: every one 409 `model_busy`, the request served |
| a parked model stopped (`/backend/shutdown`) | 200 in 202 ms, not listed after; its next request loads it afresh in 4.17 s — what `host_claims` and the last-resort stop of a parked model do |
| a return the card refuses | HTTP 503 `no_room_on_device`, `Retry-After: 2`, still parked, then served |
| `/system` for a model that parks | "parks, state loaded, size_vram 1,315 MiB" → "state parked, size_vram 320 MiB, 577 MiB on the host since …"; a model that does not park carries neither `parks` nor `state` |

So a give-way that today costs its model 4.2 s to come back (bge-m3's own
fresh load in that pass) costs 0.14 s to make and 0.23 s to undo.

**Behind the LIVE wrapper, on the card, the node embedding beside it**
(München, 2026-11-26 01:22–01:25Z; copies of vega-bge-m3's file whole plus
`park: true`, `GGML_SYCL_PARKABLE=1`, `GGML_SYCL_PARK_VERIFY=1`, through
vega-louise-llama-cpp — wrapper 47bf02ea…, engine ed93e3c1b2e2; inputs
tagged, each return held to the receipts' run id, to the wrapper's
`/vega/keeps` and to the engine's counts; another run id than
vega-bge-m3's, gated by nobody, removed after):

| | measured |
|---|---|
| the load | ONE "loaded on Intel(R) Arc(TM) Pro B70 Graphics, run sha256:02112555…" line through all the cycles; no refusal to state the run |
| a return | "the load in force asked again (the same options, the same process …; the backend: 'Model already loaded'): run … stands" — one a return, never a second "loaded on" |
| the run id on the receipts | the same before the first park and after each of 43 returns; and the SAME after a stop of the parked model and its load afresh in a new process |
| the counts, a cycle | the engine: parks +1, unparks +1; the wrapper: `created` +1 (1 → 44 over 43 returns), `calls` + one per sentence asked (not +1), `canary_failed` 0 throughout |
| a return the card refuses | 503 `no_room_on_device`, `Retry-After: 2`, twice, still parked (319 MiB on the card, 577 on the host); the third request served, the same vectors |
| a busy model | 2,716 parks asked during one 2,048-sentence request, every one 409 |
| a parked model stopped | 200 in 1,314 ms through the wrapper; its next request loads afresh in 10.58 s |
| times, the node at work | a park 135–138 ms; a return with sixteen tagged sentences 377–392 ms (881–1,378 ms in the first three cycles after the load) |

With Tensor's off-node run (25 of 25, parking-step-2.md §11) this is every
junction: the server, the wrapper, the engine and the card together, on a
copy. What it is NOT: a gate. The run that parks a real model is gated with
the park-cycles clause, which holds a cycle to the engine's parks and
unparks AND the wrapper's `created` — a return that fails is answered 200 by
a new process under the same run id, and only those counts starting again
say so.

## 5. Rows owed from the node (none exists yet: nothing parks until a model's file says `park: true`)

A model parks when its file says `park: true` AND its `env` carries
`GGML_SYCL_PARKABLE=1` — a new run, gated once, with the gate's park-cycles
clause (§6, built 2026-11-26). Which models: bge-m3 first, then the five
llama.cpp encoders, with the engine's byte check on (operator, 2026-11-26:
"1. A then B 2. On but monitor if it creates overhead"). Whether the signed
statement says the cycles was not answered: it does not (§6).

1. `louise node resident show`: `"parks": true` for a model loaded from a
   file with `park: true` (the server's own first row).
2. The keeper's line "LocalAI's park floor set to 12,288 MiB" once, and no
   second one at the next beats.
3. A load that needs room: "card: parked X (… MiB to host memory)", no
   "card: unloaded", and the card's count down by that much.
4. The next request to X: "parked: X … came back from host memory in N s",
   against its cold start in model-profile.md.
5. A question in embed mode: the corpus encoders parked, embedding resumed
   after it — the seconds from the question's end to the first batch,
   against today's.
6. The host under its floor with a model parked: "card: X was parked … and
   is stopped", one a beat.

## 6. The gate's park cycles (`embedder.park_cycles`, built 2026-11-26)

A return is an upload of the weights again — a path no gate had seen. So a
model LocalAI says parks (`/system` `"parks": true`, read fresh once the
gate's 512 texts have loaded it) is parked and asked a fingerprint text, 64
times (four rounds of the sixteen, a new order each), and each answer is held
to the same text's first. It runs right after the two single-text passes and
BEFORE the fresh loads and the 300 contexts: a pass refused, deferred or
failed there has spent seconds, not the gate's minutes.

**One cycle, counted, never assumed** (`run_check.Parker`):

| step | asked of | must read |
|---|---|---|
| the engine's counts | `GET /backend/monitor?model=` `memory.breakdown` | (parks, returns, 0) |
| 1.2 s without a request | — | LocalAI refuses a park within 1 s of one |
| the park | `POST /backend/park` | 200 |
| the counts | as above | (parks + 1, returns, 1) |
| one text, alone — the request IS the return | the node's own client, not warmed first (`model_load.returns_on_request`) | the same bytes as its first answer |
| the counts | as above | (parks + 1, returns + 1, 0) |

| what the gate meets | verdict |
|---|---|
| a park refused 409 (the model works), counts something else moved, a model found parked, a model the runner no longer holds | not a cycle: asked again, 10 times 3 s apart, then DEFERRED |
| counts that STARTED AGAIN between the park and the answer | the return failed and a load answered in its place (200, the same vectors, the same run id): the run FAILS, and the fresh loads and contexts are not spent on it |
| the same, where Louise itself stopped the parked model meanwhile (app/own_unloads.py: a question took the card) | not a cycle: asked again |
| 412 `cannot_park` | REFUSED: the file says `park: true` and the engine was not started with `GGML_SYCL_PARKABLE=1` |
| 412 `host_memory_floor`, `parking_off` | deferred |
| no counts to read | REFUSED after three reads |
| an answer that differs | counted in `load_variation`: fails an `exact` collection's run, admitted within 0.99999 for a `near` one |

**The floor.** LocalAI parks nothing while `park_host_floor_bytes` is 0, and
the keeper that sets it beats in the worker; a gate from the command line may
run with the worker stopped. The gate sets the same one value itself
(`[models] page_floor_gib`, idempotent). A node whose floor is 0 parks
nothing: a model that asks to park is refused there, with the two ways out.

**The statement is unchanged** (no wire change; the operator's third choice,
not answered). A failed pass makes `load_variation` 0, an inexact one lowers
it, as the fresh contexts do. What the statement cannot say — that the cycles
were asked — the node records beside the batching
(`gate-parks.json` beside `gate-batching.json`: cycles, closest, the median
seconds of a park and of a return), and `runs.check` holds a model that parks NOW to it: a
run gated while its model did not park (its file gained `park: true`, or the
runner its patch, under the same run id) is gated again before anything is
written from it. Putting `park_cycles` in the signed statement instead is
09 §9.12 version 7 — a wire change, one more gate of every parking run, and
the only way a PEER can see the cycles were counted.

**Not held to the wrapper's `created`** (STATUS.md said it would be): a model
that parks is on context on demand, so every request creates a context —
`created` counts requests, not returns, and is read only for such a run after
a settling probe. The engine's parks and returns are the count.

**What the byte check costs** (`GGML_SYCL_PARK_VERIFY=1`: the engine reads
the card's bytes back at every return) is read in three places: the gate's
report (`hold embedder check`: "parking … a park N s, a return N s at the
median"), the evictor's line ("card: parked X (… MiB to host memory in N s)")
and the load record ("parked: X came back from host memory in N s") — against
0.14–0.23 s and 0.23–0.39 s on the card's copies without the check (§4).

**Open, asked of Tensor**: whether `/backend/park` parks a model LocalAI
holds PINNED. The gate pins its model (the twin, or the shared load) for its
whole length, so a server that refuses a pinned model would defer every such
gate with the server's own word in the reason.

The gate twin (`<model>--gate`) is the model's own file under another name:
`park: true` and its `env` go with it, it states the same run, and its cycles
are that run's.
