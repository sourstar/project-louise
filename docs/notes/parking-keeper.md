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

## 2. The keeper's three duties (`resident._parked_follow`, each beat)

| duty | what it does | pros | cons |
|---|---|---|---|
| the floor | where a loaded model says it parks, LocalAI's `park_host_floor_bytes` is held to `[models] page_floor_gib` (12 GiB) — read whole, one key set, written whole, read again; the object's secrets never kept or said | one host-memory number for paging and parking; never set by hand; a server without the patch is never asked | one number for two uses: a node that wants parking with less host room than paging has to lower both |
| the record | a model listed parked whose process holds it on the card (a request crossed its park) is given back to LocalAI's record by `POST /backend/load` | LocalAI's watchdog tracks it again; the evictor parks it rather than stopping it | told from the reading (nearer loaded than parked), and only where this process knows the model at rest; a tiny model whose residue is most of it stays "parked" until its next request, which makes the record exact anyway |
| the host | when MemAvailable is under the floor, the parked model used longest ago is stopped, one a beat, never a pinned one (`model_evict.host_claims`) | the parse pool's room comes before a quick return (43 memory kills of the worker, 2026-11-19); LocalAI never stops a parked model itself | in embed mode at a busy hour (4.7 GB available at the worst, 2026-11-24) everything parked is stopped within a few beats: parking then pays only while the host has room — which is the hour it is safe |

The status quo, for comparison: every give-way is a stop. Pros: nothing in
host memory, nothing new to go wrong. Cons: 6–34 s to come back, each time
(model-profile.md), where a park's return was 15.5 ms for bge-m3.

## 3. Not built, and why

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

## 4. Rows owed from the node (none exists yet: nothing parks until the park engine and a `park: true` file)

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
