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

### 4.1 A pinned copy and the gate's twin, on the card (München, 2026-11-26 06:44:10–06:44:54Z)

On the operator's word ("Yes for pinned parking test on the card"), behind
the live wrapper, the node working and Legal's dump build running. A copy of
the live `vega-bge-m3` with `park: true`, the two env lines and `pinned: true`
KEPT, then its twin written as `gate_twin.ensure` writes it and pinned.

| | the pinned copy | its twin (`…--gate`) |
|---|---|---|
| parked by `POST /backend/park` | yes: 345 and 382 ms, 997 MiB given back | yes: 396 ms |
| back by the request | 1.58 and 1.22 s, vectors identical | 1.21 s, identical |
| the engine's counts | +1 parked, +1 back each cycle | the same |
| the run | one run id on all receipts | THE SAME run id, the same sixteen vectors |
| the config written through `/models/import` | — | keeps `park: true` AND the env; `/system` lists it, the monitor counts |
| cycles | 2, then 412 `host_memory_floor` | 1, then 412 |

- **The pin is no obstacle** (Tensor's §12 off the node, now on the card).
- **The keeper's first floor** (§5 row 2, seen): at 06:44:51Z, once, "resident:
  LocalAI's park floor set to 12,288 MiB … (it was 8,192) — qual-nx-pin,
  qual-nx-pin--gate can be parked". München had set 8 GiB by hand for the
  test; the keeper, which holds the floor wherever a loaded model says it
  parks, put the node's back between two rows. A floor set by hand lasts
  until the keeper's next beat.
- **The floor refused the later parks**: 8.7–9.7 GiB available under a 12 GiB
  floor while the dump build ran. During a heavy build nothing parks on this
  node — models are stopped instead, as before — and a parking model's gate
  defers. What parking gives depends on `page_floor_gib`; put to the operator
  with rows once a real model parks.
- **The byte check's price is NOT measured yet.** With `GGML_SYCL_PARK_VERIFY=1`
  a park took 345–396 ms (135–234 without, the night before) and a return
  1.2–1.6 s (0.23–0.39) — but these returns were sixteen sentences on a card
  at 28.6–31.3 GiB, those one sentence on a card with room. The clean row is
  the same copy and request with the line and without, on a quiet card; owed.
- The monitor answers 500 "not currently loaded" before a model's first load.

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
model that parks is parked and asked a fingerprint text, 64 times (four
rounds of the sixteen, a new order each), and each answer is held to the same
text's first. "Parks" has three parts, each asked of the one who knows: the
ENGINE (the run's own statement lists the environment it was launched with;
without `GGML_SYCL_PARKABLE` at a value other than nothing and zero — as the
engine itself reads it — it never moves its weights, whatever the file says:
`embedder.can_park`), the NODE (a host floor above 0) and the FILE
(LocalAI's `/system` `"parks": true`, read fresh once the gate's 512 texts
have loaded the model). It runs right after the two single-text passes and
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
| a park refused 409 (the model works), counts something else moved, a model found parked, a model the runner no longer holds | not a cycle: one text puts the model back on the card, the cycle is asked again, 10 times 3 s apart, then DEFERRED with the last reason |
| counts that STARTED AGAIN between the park and the answer | the return failed and a load answered in its place (200, the same vectors, the same run id): REFUSED as a run that parks, and NO statement kept (below) |
| an answer after a return that is not the text's first: a NaN, other bytes under `exact`, further than 0.99999 under `near` | REFUSED the same way — the pass never makes a failing statement |
| other bytes within 0.99999, under `near` | admitted; what the returns differ by is in the statement's `load_variation` |
| the same, where Louise itself stopped the parked model meanwhile (app/own_unloads.py: a question took the card, another gate reloaded the shared load, a twin was let go) | not a cycle: asked again |
| 500 `model_stopped` (the park broke its backend) | REFUSED, the same way |
| 412 `cannot_park` | REFUSED (it is not reached for an engine without the switch: that one is asked nothing) |
| 412 `host_memory_floor`, `parking_off` | deferred |
| `/system` silent, or not listing the model that has just answered | deferred: silence is never "does not park" |
| a NaN the client's retry would have hidden | seen: the gate's request is not healed (`embeddings.unhealed`), and behind VeGa a NaN names no run — which adds none, and is the verdict |
| no counts to read | REFUSED after three reads |
| the file says `park: true`, the engine was started WITHOUT the switch | asked nothing, held to nothing: `/system` lists `"parks": true` on the file's word, the park is 412 and the monitor 500 (Tensor, parking-step-2.md §12) — the model never parks, the evictor stops it as before, and its statement stands |
| the node's `page_floor_gib` is 0 | asked nothing: nothing parks on this node |
| an answer that differs | counted in `load_variation`: fails an `exact` collection's run, admitted within 0.99999 for a `near` one |

**Refused, not failed, and why** (`embedder.ParkRefused`). Nothing is written
from a run while its model parks before its cycles are counted, so a pass
that goes wrong has written nothing wrong; and a FAILING statement kept for
it would replace the run's good one and stop its collection even after
`park: true` is taken out again (the run id does not change with that line)
— for a failed return, on a verdict that a stop made by anything else, a
tool or the kernel, would also produce. So EVERY way the pass can go wrong
raises, keeps nothing, and says the two ways out; with the line out the
model does not park, its statement stands and embedding goes on. The first
cut kept a FAIL for an inexact return and for a NaN: both read again and
changed before the merge. While the line stays, the refusal is remembered
for an hour for that same run, PER COLLECTION AND ROLE (in the process: no
gate, no twin load, no unload in between) — a refusal is a collection's: one
bit off refuses a collection that asks for bytes and not one that allows
near-exact runs of the same model. Another run of the model, a restart, or
the line taken out (the model no longer parks) is gated at once.

**The floor.** LocalAI parks nothing while `park_host_floor_bytes` is 0, and
the keeper that sets it beats in the worker; a gate from the command line may
run with the worker stopped. The gate sets the same one value itself
(`[models] page_floor_gib`, idempotent). A node whose floor is 0 parks
nothing: a model that asks to park is refused there, with the two ways out.

**The statement is unchanged** (no wire change; the operator's third choice,
not answered). An inexact return lowers `load_variation`, as a fresh context
does. What the statement cannot say — that the cycles were asked — the node
records beside the batching (`gate-parks.json` beside `gate-batching.json`:
cycles, closest, the median seconds of a park and of a return, AND THE
DIGEST OF THE STATEMENT BODY OF THE SAME PASS), and `runs.check` holds a
model that parks NOW to it: a run gated while its model did not park (its
file gained `park: true`, or the runner its patch, under the same run id) is
gated again before anything is written from it. The digest is what the
review of 2026-11-26 added: counted alone, the 64 cycles of a pass that
FAILED on an inexact return admitted the run on another pass's statement —
its own older one, or a sibling collection's.

**What a sibling of the space lends** (`runs.parks_for_space`), so that the
gate stays ONE PER (space, run) when `park: true` lands on a model several
collections share: the EVIDENCE ABOUT THE RUN and never a verdict — 64
counted cycles whose every answer was the same bytes, under a passing
statement of that run the sibling still holds and its record names. The
borrower keeps its own statement (its sample, its batching, its CPU
comparison) and writes its own record for it, saying where the cycles were
counted. Cycles that were not byte-exact are lent only to a collection that
holds a byte copy of the SAME statement (what a near-exact run's returns
differ by is in the one statement that measured them); a borrowed record is
not lent on. AND A REFUSAL WITHDRAWS WHAT WAS COUNTED BEFORE
(`runs.refuse_parks`, the fourth read): when a later gate refuses the run as
one that parks, every record of that run and role in the space — counted or
borrowed — is marked refused and neither counts nor lends, until a gate that
counts its cycles again writes a fresh one. The newest evidence about the
run decides. For one round of review the sibling's STATEMENT
was copied instead: read against the borrower's own, stale, batching record
it admitted batched writes on a verdict made at one text a request — the
fault of the 714 books — and a sibling whose batches differed locked a
healthy collection out. Withdrawn before the merge. The query side is
held to the same record by the look (`_look_at_the_query_side`): nobody
writes a query's vector, so `runs.check` never sees it. Putting `park_cycles` in the signed statement instead is
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
0.14–0.23 s and 0.23–0.39 s on the card's copies without the check (§4). The
gate's own figures are what this node's client met: room made before the
return and VeGa's receipt are inside them.

**Open, the operator's**: the client asks a null or NaN vector again before
anyone sees it (`embeddings._embed_batch`, "intermittent SYCL corruption").
The park cycles now judge the engine's own first answer; the gate's other
single-text passes — the two orders, the first text after a fresh load —
still judge what the client's retry returned. Making them all judge the raw
answer is one line, and would FAIL a run that gives a NaN once in a while
and is healed today: a change of every encoder's gate, not taken here.

**A pinned model is parked** (asked of Tensor, answered 2026-11-26, off the
node on the live build's tree e37b86e0…, parking-step-2.md §12): the park
route does not look at the pin — HTTP 200, the engine counts it — whether the
file says `pinned: true` or `toggle-pinned` set it. The pin is the
watchdog's: LocalAI never parks a pinned model BY ITSELF. So the gate, which
pins its model (the twin, or the shared load) for its whole length, parks it
all the same. The pin does not hold the card's room for a model that is
away: a return another load took the room of is 503, the client's deferral,
and the gate is deferred.

**The gate twin** (`<model>--gate`) is the model's own config-json under
another name, written through `/models/import`: `park: true` (a config key
with a json tag) and its `env` go with it, it states the same run, and its
cycles are that run's (the same §12: the twin's file had `park: true`,
`/system` listed it, it parked and came back, counts 1 and 1). NOT YET ON
THE CARD: a pinned copy and a twin behind the wrapper — München runs both on
copies after the deploy.

## 7. A park is for a run admitted to park (2026-11-26, found on the node)

**What happened.** bge-m3's file got its three parking lines at 07:59:32Z.
`/models/reload` re-read the file and left the running backend as it was:
`/system` listed no `parks` for it and the monitor answered "no status for
a model that cannot park" — the file said it parks, the PROCESS did not.
After `POST /backend/shutdown` and one question (08:06:30Z) the new process
answered: "parking: this model can park, 577.23 MiB of weights leave the
device when it is told to", run `sha256:0211…1dde` (the run of last night's
copies: same engine, env and weights), `/system` `parks: true`. Then:

- the WORKER said nothing about the run — it had not heard it. The record of
  which run answers was each process's own; the web heard it from the
  question, and wikipedia had nothing to embed (03 §3.3, amended the same
  day: a run heard by any process is now written where every one reads it,
  `run_check._tell` / `_last_heard`);
- 27 s after the load the keeper wrote "LocalAI's park floor set to
  12,288 MiB … vega-bge-m3 can be parked" — for a run whose cycles nobody
  had counted. A write was held to the counted cycles (`runs.check`,
  `parks=`); a PARK was not;
- `louise hold embedder check wikipedia` was refused: "held by the node
  (worker) … retry when it is done". The manual door is open for a paused
  collection only (`node pause wikipedia`, the check, `node resume`), and
  the gate test's own words now say so.

**The rule** (the operator, the same morning, of the statement field:
"Either a model is validated or it does not embed"): a collection's encoder
is PARKED only when the run last heard from ITS PROCESS has its park cycles
counted for EVERY collection that uses it, document side and query side
(`run_check.park_admitted(server, model, process)`). The run heard is bound
to the backend PROCESS it was heard from — its pid AND its start, as the
runner's listing names them (a pid alone is LocalAI's container's count,
which starts again at each restart of that container) — and only where the
answer can only be that process's: one run in it and none that named no
run, from a process this process has known for `BIND_AFTER_S` (2 min: what
it must exceed is the gap from an answer to its hearing, ≤ the 10 s of the
receipts, never a request's length). A process that cannot bind (yet) never
erases a binding another made for the same run. A model name is not a process: a restart with another env is another
run under the same name, and a process nothing of Louise has asked yet (the
keeper's warm reads no run) is not admitted. Asked afresh each time (~0.3 ms
a collection): a refusal and a new run count at once; while any collection
file cannot be read, no model is parked (one not read may name it, under
stricter cycles). Otherwise — a process nobody heard, a
run not gated, one the gate refused, anything unreadable — it gives its room
by a stop, as before parking existed, and the line says "its file says it
parks and its run is not admitted to (not gated with its park cycles, not
yet bound to this process, or a collection file not read): stopped". A model that is no
collection's encoder has no gate and parks as LocalAI says.

**Every path that gives a model's room back asks it:** the evictor
(`model_evict.make_room_for`) and the question card (`question_card.
_give_back`, a question taking the card from the corpus encoders) — the
first cut asked it on the evictor's path only, and the second independent
read found the card's (the most frequent) parking without it. LocalAI never
parks a model by itself (Tensor, parking-step-2.md §12); the gate's own
cycles park its model by request (`Parker.park`), outside this rule, which
is what the cycles are for. The keeper still writes the host floor for a
server where a model parks: it is LocalAI's condition for any park.

**A run tested before and heard again** — after a restart, the model put
back — is re-checked by the worker's idle round by itself
(`run_check.verdict_outdated`), not only a run never tested.

**For whoever puts `park: true` into the next encoder's file:** the env
takes effect in a NEW process — stop the model, do not reload it; the first
answer of the new process is heard and gated by itself from this release
on; until its cycles are counted, and for the first two minutes of every new
process (after any restart of the model or of LocalAI), the model is stopped
for room, not parked. A failed look at a query side is looked at again after
FAIL_TTL_S (5 min), doubled at each failure in a row for its run up to 30 min, a timed-out encoder held as busy for every collection on it (`encoder_timeouts`), never at every step — and at once where another query
run is heard meanwhile.

