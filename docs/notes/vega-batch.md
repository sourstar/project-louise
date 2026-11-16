# VeGa batch: one runner call per embedding request ("B")

*Architecture session, 2026-11-16, for the operator's "A and B" then "make it
within the frame of the current architecture". München's measurements of
the same day are the BEFORE. Status: BUILT (operator: "W"), not deployed.
The deploy is a new wrapper, so a new run: the gate runs again and its
fingerprints must be byte-identical to today's; then AFTER per §6.*

**Deploy check (first, before the gate):** one VEGA/2 request through the
node's own LocalAI, its vectors compared byte for byte with the same texts
sent as VEGA/1. The wrapper answers the batch as one FINAL vector
(`tokens` 1, `dim` = the concatenation's length), which LocalAI passes on as
it is. The client checks every item against its receipt, so an answer
LocalAI changed would be refused and nothing written.

## 1. What costs the time

Measured 2026-11-16 on the node (Arc, louise-sycl-f16-llama-cpp-281e1ee481b6,
DNN=0, parallel:1), bge-m3 on wikipedia chunks:

| path | chunks/s | per chunk |
|---|---|---|
| the engine alone (raw gRPC, one text a call) | 117.7 | 8.5 ms |
| the whole pipeline, alone on the card | 47.7–51.3 | ≈ 20 ms |

The 11.5 ms gap has two halves (München, the wrapper's `gaps` buckets):
- **per text, inside a request**, ~3–5 ms: LocalAI's `/v1/embeddings` loop
  calls the backend's `Embedding` RPC once per input. Each call goes
  LocalAI → gRPC → the VeGa wrapper (tag parse, cut) → gRPC → backend, then
  the receipt is written to VeGa and ACKNOWLEDGED before the vector returns
  (09 §9.12), then back.
- **between requests**: Louise's receipt fetch and check, the gate, the next
  send. That half is profiled separately (`louise dev embedder-profile
  --stages`) and is not this note's.

Context creation is not the loss (917 of 413,662 calls, A dropped), and
concurrency is not the cure (parallel:4 is not exact for bge-m3; concurrency
4 at the gate measured worse).

## 2. Where batching lives — the choice

| | pros | cons |
|---|---|---|
| **Status quo** (one input = one runner call) | nothing changes | the per-text hop stays: ~3–5 ms of every ~20 ms chunk |
| **W. Wrapper-side, VEGA/2 batch input** (recommended) | Only our code changes: the wrapper, VeGa's intake, Louise's client. LocalAI core, its proto and the engine binary are untouched, so nothing is re-patched per LocalAI release. The backend still embeds one text per call, so the vectors' bytes are those of today. VEGA/1 stays accepted, so it is reversible. | A wire-format version (VEGA/2). The runner's answer carries ONE `embedding` of N × dim floats, a use of LocalAI's API that only Louise reads. The wrapper → backend hop (localhost gRPC) is still per text until stage 2. |
| **L. LocalAI core + backend.proto** (one RPC with N inputs) | The runner's answer stays standard: N data items. One RPC end to end. Could go upstream. | Three code bases patched: LocalAI core (Go), backend.proto (Go, C++ and Python stubs) and the llama.cpp grpc-server, each re-applied at every LocalAI release until upstream takes it. A larger change for the same saving. |

**Recommendation: W**, then measure. Stage 2 (§5) removes the last per-text
hop only if the AFTER numbers show it is worth an engine patch.

The standards were looked at: the OpenAI and TEI APIs batch at the HTTP
level, which Louise already uses (`input` is a list). llama.cpp server's
multi-input `/embedding` would mean bypassing LocalAI, which is outside the
current architecture. LocalAI's backend protocol has no batch embedding call.

## 3. The wire format (09 §9.12 amendment, VEGA/2)

One input string carries the whole request:

```
"\x1eVEGA/2\x1e" + canonical JSON (§9.1) of
  {"form": "batch", "items": [<item>, …]}
item = {"form": "text", "id": "<id>", "text": "…"[, "window": n]}
     | {"form": "pair", "id": "<id>", "pair": ["…", "…"][, "window": n]}
```

- An item is EXACTLY a VEGA/1 tag's object, so its id rules, its input digest
  (the object without `id`) and its receipt are those of VEGA/1, unchanged.
  This keeps 03 §3.3 and every check Louise makes per input as they are.
- 1 to 1,024 items, the receipts route's own bound. Ids are distinct within
  a batch and fresh as ever. A request carries one batch input and nothing
  else.
- **The answer**: one `embedding` whose float32 bytes are the items' vectors
  concatenated in item order, every vector `dim` long (the Codex's
  `space.dim`).
  - The wrapper refuses a batch whose backend answers differ in length.
  - Louise refuses an answer whose length is not items × dim × 4 bytes, and
    splits it by `dim` before the per-input checks.
- **Failure**: one item refused refuses the whole batch with that item's
  error. This is what LocalAI's loop does today when one input fails.
- VEGA/1 single inputs stay accepted by the wrapper, so a node can roll back
  by its client alone.

**The intake** (platform-internal, fixed in 09 so the two programs agree)
gains one line:

```
{"kind": "receipts", "run_id": "sha256:…",
 "receipts": [{"id", "input_digest", "vector_digest"}, …]}
```

- It is all or none: every receipt is held, or the line is refused and none
  is.
- It gets one acknowledgement, given before the batch's answer returns.
- The 1 MiB line bound holds: 1,024 receipts are about 200 KB.
- The VEGA/1 `receipt` line stays.

## 4. What each part does

- **The wrapper** (`deploy/vega/vega_wrapper.py`). For a VEGA/2 input it runs
  today's per-input path (`Gate.embedding`: the window cut, the pair check,
  the keep count, the canary and the per-token refusal) for each item IN
  ORDER, one backend `Embedding` call each. So the decode is still one text
  at a time on parallel:1, which is invariant 12. Then:
  - it collects the receipts and delivers them in one `receipts` line;
  - it returns one `EmbeddingResult` whose `embeddings` are the concatenation
    and whose `prompt_tokens` is the sum.
  - `calls`, `gaps` and the keeps count stay per item.
  - The reload check (`loaded is state and child alive`) runs after the last
    item, before the receipts.
- **VeGa** (`app/vega/service.py`): the `receipts` kind, atomic, with the
  same refusals as `receipt` (unknown run, conflict).
- **Louise** (`app/vega/client.py`, `app/embeddings.py`):
  - `tag_batch` writes one VEGA/2 input per request;
  - the response is split by `dim`, then the receipts are fetched (one GET,
    ≤1,024 ids, as today) and checked per input;
  - a VEGA/2 refusal of the wrapper's form "unknown tag version" (an old
    wrapper) falls back to VEGA/1 for that endpoint, said once in the log.
- **The run**: the wrapper's files are in the run's tree. A new wrapper is a
  new run, so the gate runs again on its first load. The numeric path does
  not change, so the gate's vectors must be BYTE-identical to the old run's
  on the fingerprint texts. If they are not, that is a defect, not
  near-exactness.

## 5. Stage 2 (only on numbers): one backend call

The wrapper sends the batch's bare texts in one backend call, and the
llama.cpp grpc-server embeds them one after another on its single slot,
returning N vectors.
- This means a backend-side proto field and a grpc-server patch in the
  engine's series (one per engine), but no LocalAI core patch, since core
  never sends that field.
- It is a new engine, so the gate runs and the deploy waits for the
  operator's word.
- Built only if stage 1's AFTER shows the wrapper → backend hop is worth an
  engine patch.

## 6. Measurement (measuring.md §6)

- **BEFORE**: §1 (47.7–51.3 chunks/s alone; the `gaps` buckets).
- **AFTER**: the same collection, the same engine and settings, alone on the
  card. Report chunks/s, card busy %, and the `gaps` buckets per item and
  per request.
- **Exactness**: 2,000 wikipedia chunks through VEGA/1 and VEGA/2. The
  vectors must be byte-identical and all receipts held.
- The commit that ships it carries the numbers (scheduling.md §6).

### AFTER, 2026-11-16 (München; deployed: app a6e20fe, louise-vega vega:a6e20fe, every vega- wrapper rebuilt at a6e20fe)

- **Deploy check, before any gate** (`/root/measure/v2check.py`, the app's
  own `tag_batch`/`unpack`/`verify_response` through the node's LocalAI):
  vega-bge-m3 64 wikipedia chunks (window 1,024), vega-nemotron-3-embed-1b 64
  wikidata chunks (window 8,191), vega-qwen3-embedding-4b 16, vega-dar-legal-it
  16: **0 different, 0 refused**. LocalAI passes the concatenated answer
  unchanged.
- **Re-gate:** every vega- space gated on its new run, every FINGERPRINT
  IDENTICAL to the run it replaces. Nemotron spaces (EUR-Lex, EUR-Lex4,
  NeuRIS, Normattiva, legi_F, wikidata) run 1f09a0e5…; Normattiva-DAR
  5588fc4e…; gutenberg ebec8b15…; pubmed 7325195a… (query 2234745a…);
  wikipedia 955ca309…. All 16/16, batch-invariant True.
- **Exactness:** 2,000 random wikipedia chunks (seed 20261117), groups of 16,
  window 1,024: **byte-identical, every receipt held**.
- **Speed, request level (clean):** the same 2,000 chunks, VEGA/1 and VEGA/2
  alternating per group of 16, one stream, both warm: **69.5 s against 68.7 s
  (≈ 1 %)**. Removing LocalAI's per-text hop and the per-text receipt writes
  gains almost nothing. The per-item cost is inside the wrapper → backend path
  (§5, stage 2) or in the engine's per-call work, not in the LocalAI hop.
- **Speed, card level (NOT clean):** wikipedia alone 18:21–18:31Z: card 88–96 %
  busy, 40.3–44.3 chunks/s (BEFORE 47.7–51.3, 55–71 %). The worker was still
  gating gutenberg's QUERY run on `vega-qwen3-embedding-4b--gate` through the
  window (an unnamed gate holder at ~55 s/min), so the busy share is not
  wikipedia's alone and the chunks/s shared the card. It is to be measured again
  once no gate runs. The `gaps` buckets per request were not separated from that
  load.

### Per hop, clean, 2026-11-16 19:05–19:20Z (München; app c15e120, the same wrappers)

Architecture's method. Node paused, no gate for 5 min before it, the same 2,000
wikipedia chunks (`/root/measure/wp2k.jsonl`, window 1,024), warm (one batch
first, untimed). Harnesses in `/mnt/ssd3/louise-build/hop/`: `hop.py` (raw gRPC
on the bge-m3 wrapper's own socket, the wrapper's `app.vega.tag`, no LocalAI or
Louise), `lai.py` (the app's own `tag_batch` → POST → `verify_response`), `run.sh`.

| Hop | ms per text | chunks/s |
|---|---|---|
| Engine alone, 1 slot, one text a call (parallel:4 fork, earlier today) | 8.5 | 117.7 |
| **Wrapper direct, VEGA/2 batches of 16** | **8.05** | 124.2 (8.60 / 116.2 over 6,000, under py-spy) |
| Wrapper direct, VEGA/1, one text a call | 8.10 | 123.4 |
| **Through LocalAI, VEGA/2, the app's own client** | **8.52 POST + 0.11 receipt check = 8.63** | 115.9 |
| **The worker's pipeline, wikipedia alone** (19:10–19:20Z, 1,481 requests of 16) | **≈ 20.7** | 42.4–52.4 (card busy 50–64 %, lost 22–30 s/min) |

- **The wrapper and LocalAI together cost ≈ 0.6 ms a text** (8.05 against
  8.63). The wrapper's own path equals the engine's (the 8.5 ms raw figure was
  taken through another harness, so 8.05 is no faster engine). The VEGA/2 row
  above ("the per-item cost is inside the wrapper → backend path") is
  SUPERSEDED: neither the wrapper nor LocalAI holds the loss.
- **py-spy of the wrapper** (`record -r 200 --idle -f speedscope`, 60 s, VEGA/2
  direct; `spy.json`, `dump.txt`): 95 % of the samples in `embedding` are
  the gRPC call to the backend (`forward` → `_channel._blocking`). The window
  cut is 1.9 % (`_cut` → `_tokens`, TokenizeString), and the receipts line 1.0 %
  (`deliver_many`). Digests, tags and JSON are each under 0.3 %.
- **Over the window:** 158 of the 2,000 (7.9 %) have `token_bound` + 2 over
  1,024 and pay a TokenizeString. That is the 1.9 % above.
- **`/vega/keeps`, the engine's idle time before each call** (wikipedia's run
  955ca309…). The direct phases, ≈ 10,000 calls: 98 % at ≤ 4 ms, none at
  ≥ 32 ms. The pipeline's 10 min, 27,141 calls: 24,406 at ≤ 4 ms, then **≈ 1,500
  gaps of 32 ms–4 s** (32: 286, 64: 105, 128: 336, 256: 462, 512: 289, 1,024: 39,
  2,048: 5, 4,096: 2). That is **one per request** (1,481 requests): every
  request of the pipeline reaches the engine after the card has been idle about
  100–500 ms. Their sum, at 50–100 % of each bucket's bound, is 190–385 s of the
  600, the 22–30 s/min the card record calls lost.
- **The card record says a wikipedia request was held at the gate the whole
  time** ("held: wikipedia 60" request-seconds a minute, one in flight). So the
  idle time falls INSIDE the gate-held window, before the request reaches the
  wrapper: in `_request_vouched` between `g.admit` and LocalAI's call to the
  wrapper (`rage_keep.request`, `_post`, LocalAI under the pipeline's own body:
  `metadata` keep, `Louise-Level`), or in what the 'held' clock counts beyond
  the POST. `lai.py`'s POST of the same batches (no keep metadata, no level
  header) took 136 ms a request; the pipeline's held window is ≈ 330 ms.

**Conclusion (Architecture's rule):** the wrapper path is at the engine's cost,
so stage 2 (one backend call per batch) buys nothing. The ≈ 12 ms a text is on
Louise's side of the POST, inside the gate-held window, once per request. The
next measurement is the worker's own request, timed per stage. That means
`dev embedder-profile --stages` with the pipeline's body (keep metadata, level
header), plus a `py-spy dump` of the worker's embed threads during wikipedia
alone. Not run here: the node was mid-deploy (7efabc6 staging) when the window
closed, and `lai.py` already covers `--stages`' path without the pipeline's
body.

## 7. Tests

- tag: a VEGA/2 round trip, its canonical bytes, and the refusals (an
  empty batch, over 1,024 items, a duplicate id, a nested batch, a key too
  many).
- wrapper: per-item cuts and refusals, the order kept, one intake line, a
  reload mid-batch giving no receipt, and VEGA/1 still served.
- service: `receipts` is all or none, and an unknown run is refused.
- client: the split by dim, a wrong length refused, the fallback to VEGA/1,
  and a missing receipt for one item deferring the whole batch.
