# Context on demand for LocalAI upstream: the llama.cpp backend's half

*Prepared 2026-11-11 for the operator ("Reranker option A, prepare
everything for upstream, I will upload it eventually"). PREPARED, NOT FILED.
It follows llama.cpp's half, the package beside this one:
[`../llama-cpp-context-on-demand/`](../llama-cpp-context-on-demand/READING-GUIDE.md)
— the order, the measured facts and the design are there.*

- **The diff:** `context-on-demand.diff`, against LocalAI master
  `4b3c4ec72fcd25fdef26d24a7816eb59a1c342d1` (2026-11-11). Apply it with
  `git apply`.
- **Its source:** `deploy/localai/patches/staged/0009-llama-cpp-context-on-demand.patch`
  (P11 in our staged series), WITHOUT the llama.cpp patch file it carries
  there: upstream LocalAI gets the feature from its llama.cpp pin. The C++
  is unchanged but for one merge in `CMakeLists.txt`; the docs section is
  new, because LocalAI requires docs in the same change.
- **No commit message and no trailers** are in the diff. Those are the
  submitter's (§4).

## 1. What it does

With llama.cpp's `--context-on-demand` a model keeps its weights loaded and
a request can give its context (KV cache, compute buffers) back after its
answer. This change lets LocalAI use it: three model options, the request's
keep read from `metadata`, and the backend made safe while there is no
context.

## 2. The files, one line each

| file | change |
|---|---|
| `backend/cpp/llama-cpp/grpc-server.cpp` | the SEVEN reads of `ctx_server.get_llama_context()` are gone — it is null between requests: `parse_options`, `TokenizeString`, `Detokenize` take the vocab, `Embedding` the pooling type read once at the load. Options `context_on_demand`, `keep_context_ms:N`, `context_canary`. `Predict`, `PredictStream`, `Embedding` give their task the keep of `metadata.keep_context_ms`. `Embedding` answers with trailing metadata `llama-context-created-us` and, on a canary refusal, `llama-context-canary: failed` |
| `backend/cpp/llama-cpp/context_on_demand.h` (new) | parsing the keep; the DETECTION of llama.cpp's fields at compile time, so the file builds against a pin or fork without the feature (the options then log "not supported by this llama.cpp, ignored") |
| `backend/cpp/llama-cpp/context_on_demand_test.cpp` (new) | the header's standalone test, picked up by `backend/cpp/run-unit-tests.sh` |
| `backend/cpp/llama-cpp/CMakeLists.txt`, `prepare.sh` | the test's target; the two files copied beside `grpc-server.cpp` |
| `docs/content/advanced/model-configuration.md` | section "Context on demand": the three options, the request key, what a refused request means, the two trailers |

No change to `backend.proto`, to the Go core or to any other backend.

## 3. Testing it before submitting

- **Run here** (CPU, stand-in models): LocalAI master with llama.cpp at its
  pin (`7c2653a`, 21 commits before llama.cpp's `b8abe41`) plus the three
  llama.cpp parts, which apply there with no fuzz: `grpc-server` builds;
  `context_on_demand_test.cpp` passes standalone; driven over gRPC as
  LocalAI drives it, 32 of 32 — the keep from `metadata`, the same vector
  byte for byte with and without the option, the two trailers, the canary,
  a refused creation, a generation model — and 8 of 8 on `Rerank` with the
  model's own `keep_context_ms`. (Master has no token-id input — that is
  our P2 — so that one check embedded a text instead.) The diff, applied to
  a clean checkout, gives the files that were built.
- **NOT run:** LocalAI's Go tests and `make build`; the fork backends that
  compile the same `grpc-server.cpp` (turboquant, bonsai) — whether each
  fork has `server_context_meta::pooling_type`, which `Embedding` now
  reads, is their CI's to say; the build against a llama.cpp WITHOUT the
  feature (what LocalAI's CI would do if this were filed before its pin has
  it: by design it builds, the fields being detected — not built here).
- **By hand, with real models:** the brief's steps for the node
  ([idle-context-brief.md](../../../../docs/notes/idle-context-brief.md), "For München").

## 4. LocalAI's rules for AI-assisted changes, and how this meets them

The same as for P1's package beside this one
([`../localai-system-load-options/READING-GUIDE.md`](../localai-system-load-options/READING-GUIDE.md) §4):
no `Signed-off-by` and no `Co-Authored-By` from the AI — the diff carries
none; an `Assisted-by: AGENT_NAME:MODEL_VERSION` trailer that you fill in
(left out here on purpose: this project names no model in its files); you
understand every line; conventional commits; docs with the code — done.

## 5. What a reviewer may ask, with the answers

- **"Why not just pass `--context-on-demand` through the generic
  passthrough?"** It would set the flag, and the backend would then read a
  null context: `TokenizeString` and `Detokenize` dereference it for every
  token (by reading the code; not run). The seven context reads are the
  part that cannot be a flag. The named options also put the setting in the
  model's `options:` like its neighbours.
- **"Why trailing metadata and not a proto field?"** No proto change, so no
  other backend sees anything; a client that does not read trailers is
  unaffected.
- **"Why does rerank not take a keep?"** `RerankRequest` carries no
  metadata map. The model's `keep_context_ms` covers it. Adding `metadata`
  to the rerank request is a core and proto change — theirs to want; not in
  this diff.
- **"What if the pin has no such feature?"** The options are ignored with a
  log line; nothing else changes. Filed after llama.cpp's merge and
  LocalAI's pin, the question does not arise.

## 6. What it does not do

- It does not change a model loaded without the option: no context is
  freed, no trailer is sent, the same vector byte for byte (checked).
- It does not touch LocalAI's own model unloading (the watchdog, LRU).
- It does not add the request key to rerank, TTS, or any other backend.
