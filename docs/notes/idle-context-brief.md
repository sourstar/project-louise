# Brief for a dedicated session: a model's context on request

*Written 2026-11-12 for a session of its own (operator: "Write down
everything for a dedicated session, collect all the information needed
including all the material about B70 pitfalls and our localai and
llama.cpp customizations"). REWRITTEN the same day with the design the
operator settled in review (§1): the context is not given back by an idle
timer, it is allocated by the request and kept by the request's own keep.
The first text, and the review that led here, are
`git show d058f93:docs/notes/idle-context-brief.md` and
`git show e9f35bd:docs/notes/idle-context-brief.md` (its §8–§10). Read it
whole before touching anything; then CLAUDE.md, docs/notes/measuring.md and
the notes it links.*

*A date to settle: this day's notes (this brief, shared-context.md, STATUS)
are dated 2026-11-12, and the commits that carry them are stamped
2026-11-11 (68aaf1d, 06:54Z, records "LIVE since 2026-11-12 06:52Z"). What
was said and read in review is dated here by the commits' clock.*

## 0. In one paragraph

The B70 (32.6 GB) holds ~34 GB of resident models, ~10 GB of it CONTEXT —
each llama.cpp model's KV cache plus its compute buffers — while at most
3.3 GB of context works at any one second (p95 2.2 GB; 24 h of the card
record). The design, the operator's, checked against the literature
(shared-context.md: Prism, OSDI '26, and kvcached do it with GPU virtual
memory for vLLM/SGLang on NVIDIA/ROCm, KV only): A MODEL KEEPS ITS WEIGHTS;
ITS `llama_context` IS ALLOCATED WHEN A REQUEST ARRIVES AND FREED WHEN THE
WORK IS DONE; A REQUEST MAY CARRY A KEEP, IN MILLISECONDS, THAT HOLDS THE
CONTEXT THAT LONG AFTER ITS ANSWER, so that requests back to back do not
free and allocate between them. A semi-cold start: a model is idle
(weights only) or running. Louise's RAGE makes the work batches and sets
the keep on every request, per job type, 50 ms to start (the operator's, once
the node was measured; 20 ms at first). The card's free memory
is the common pool: with every model opted in, ≈ 24 GB at rest + at most
3.3 GB working ≈ 27 GB; in the first deployment (§1.3) ≈ 26.5 GB at rest
and under 30 GB at the peak — against ~34 GB held today and the reclaimer's
31.0 GB line. **Nothing is built yet, and nothing below is measured.**
This session: (1) measure the unknowns (§2), (2) only if they pass, write
the patch (§3), (3) gate, (4) deploy on the operator's word.

**A GENERAL DESIGN, NOT A LOUISE WORKAROUND** (operator, 2026-11-12:
"Embedders in CPU are an option but we cannot bet on it, this is a general
design improvement that can be beneficial for a number of different
scenarios other than Project Louise"). So: no argument here rests on where
one of OUR models happens to run (a CPU embedder, a model that idles today);
the feature is built for any llama.cpp server holding models it does not
use all the time — several models on one card, a desktop that shares the
GPU with other work, a server that answers in bursts — on every backend
(CUDA, ROCm, Vulkan, SYCL, Metal, CPU), every model kind in the end
(embedding, rerank, generation, encoder-decoder; §1.2 says what the first
version takes), opt-in per model, and useful with no orchestrator at all (a
model's own default keep). It is written to upstream's standards from the
first line (llama.cpp's code style, its server tests, `--help` text,
docs/), so that proposing it to llama.cpp and LocalAI is a filing, not a
rewrite; whether and when it is filed is the operator's word (as for P5's
and the Nemotron rerank's PRs). OUR node is the first deployment and its
measurements are the first evidence, nothing more.

**The vocabulary is the standard one** (CLAUDE.md, "do not reinvent the
wheel"): Ollama's `keep_alive` — a duration a request carries, with a
server default, 0 = release when the request is done — applied to the
CONTEXT instead of the whole model, and in milliseconds because the value
is under a second (to be said upstream). The sleeps are the neighbours, not
the model: vLLM's sleep mode discards the KV cache and moves the weights to
CPU RAM (level 1) or discards both (level 2); llama.cpp's
`--sleep-idle-seconds` frees model and context after an idle time. Here
nothing sleeps and no timer runs on its own: the request decides. Names
used in this brief, to be settled against upstream's conventions before
filing: the request's `keep_context_ms`; the server's `--context-on-demand`
with `--keep-context-ms N` for requests that say nothing; LocalAI's model
options `context_on_demand:true`, `keep_context_ms:N`.

## 1. The design, as settled in review

### 1.1 The operator's words (2026-11-11)

- "This is the equivalent of a semi-cold start: instead of loading the
  whole model only the context memory is requested." "Is there is not
  enough memory something is evincted." "In the very moment the model
  doesn't need the memory anymore, it is freed." "There is not necessarily
  a sleep, just idle or running models."
- "when llama.cpp gets a request it allocates the context memory; when the
  processing is done it frees it. A request may come with the flag 'stay
  warm' and this will make it keep the memory until a timeout, to prevent
  free/alloc back to back."
- "Louise RAGE creates the work batches, thus the stay warm flag keeps the
  context also between jobs."
- "it looks that 500ms are a safe margin; we can log expired stay warms for
  improvement and also pass the timeout in the argument in ms (0 no stay
  warm)."
- "Let's start with a 20 ms expiration time, the timeout shall be trimmed
  per job type: several collections share the same model, but their way to
  queue the jobs may be different, leading to tifferent stay warm timeouts;
  a timeout per model may fit our scenario but it might not be a general
  purpose approach." (This replaces the 500 ms of the line above.)
- "The trimming time shall not be modified if the expiry occurred because
  of a job missed scheduling (for instance when a chat model takes over)".
- "I choose option B, trimmed by RAGE. Then after a first observation
  period we can possibly set a new default timeout." (B: 20 ms to start,
  trimmed by RAGE per stream, the trim and the wrapper's reporting in the
  first deployment.)
- "A request that carries no parameter is using the context in the
  canonical way: no free after use". (This replaces the review's "absent is
  not 0" below: a request without the field is no longer given the model's
  default — it leaves the context loaded.)
- "Start at 50 ms" (2026-11-11, in München's session, the node measured:
  §10.1). It replaces the start of 20 ms above; the floor a keep may decay
  to stays 20 ms — he moved the start, not the floor (`keep.START_MS`,
  `FLOOR_MS`).
- "Add the canary; it must be possible to disable it once we have the
  confidence that the architecture is sound" (2026-11-11, in the author's
  session, on §10's table of the two choices). §1.4, §10 "The canary".
- "Wire the canary failure to the gate before deployment. Wire chat to
  release the context after use: we already know that is is a one shot
  operation. Reranker, propose a solution" (2026-11-11, the same session).
  §10 "The canary" (what sends a run back), "Chat", "The reranker".
- "Fill the upstream" (2026-11-11, the same session), and on the question
  of what his "A" answered: "Reranker option A, prepare everything for
  upstream, I will upload it eventually". A — the reranker takes its keep
  from its model's YAML (§10 "The reranker"). Upstream — the material is
  PREPARED and he files it himself: llama.cpp forbids an agent to write a
  post, a commit message or to open a pull request
  (`deploy/localai/upstream/llama-cpp-context-on-demand/` and
  `localai-context-on-demand/`, each a diff and its
  [READING-GUIDE.md](../../deploy/localai/upstream/llama-cpp-context-on-demand/READING-GUIDE.md)).
- "If there is a query in the meantime, the chat model gets a context
  memory; if there is not enough memory there are still options (in this
  order): the contexts of the batch in queue can be pruned, or some other
  model can be evincted, or the chat processing can be postponed."

What follows is that design with what reading the code added. A line that
is the review's and not the operator's own word says (review); §9 lists
them.

### 1.2 The engine — llama.cpp's server

- **Two states.** IDLE: the weights are resident, there is no context.
  RUNNING: the context exists — while a task is queued or in work, and
  after it until the latest keep asked has run out.
- **The loop's thread owns the context** (review). The server's main loop
  creates it when it takes a task that needs one and has none, and frees it
  when its queue is empty and the keep has run out. Request handlers never
  touch it: they tokenise with the MODEL, which stays (§3). So nothing
  waits for a wake, and this is NOT upstream's sleeping state, which frees
  the model, blocks every handler until the reload and has an open race
  (§3, H3).
- **The keep**, an integer of milliseconds in the request, 0 = none. Three
  rules (review):
  1. A REQUEST WITHOUT THE FIELD USES THE CONTEXT THE CANONICAL WAY
     (operator): it is NOT freed after it, cached prompt included. A client
     that does not know the field — a probe, a person's curl, another
     program on the same LocalAI — is served exactly as by a model without
     the option. The context then stays until a request that carries a keep
     is answered: the last request to finish decides whether it may go. (A
     server option, `--keep-context-ms N` / the model option
     `keep_context_ms:N`, gives a keep to the requests that carry none, for
     a model whose clients cannot be changed; unset, as it is by default,
     the rule above holds.) The context the LOAD made is no request's: it
     goes once the model is idle, and the first request creates one.
  2. AMONG THE REQUESTS THAT CARRY ONE, A KEEP ONLY EXTENDS (review). The
     deadline is the latest of those asked, never the last one's: a query's
     0 on an encoder the corpus shares (a symmetric space's one encoder)
     must not cancel the stream's keep. A stream's last request sent with 0
     then frees up to one keep late: nothing.
  3. The clock starts when the request's answer is complete, and only model
     work counts: a metrics task neither creates a context nor extends one
     (`task_resets_idle_timer`'s precedent, §3).
- **A creation that cannot have its memory is REFUSED** (review): the task
  is answered with an error its caller retries, and the server stays idle.
  Never an abort — in a shared pool this is the normal case of contention,
  not a fault. For Louise it is a deferral, never an error on a document
  (CLAUDE.md).
- **The context is the one the first load resolved** (review): the same
  n_ctx, n_batch, n_ubatch and cache types; no re-fit to whatever memory is
  free at that moment (H2).
- **Opt-in per model; off is today's behaviour exactly** — the context
  created at load and held for the model's life.
- **Scope of the first version** (review): text models — embedding, rerank,
  generation; encoder-decoder in OUR series only (P3/P4 are ours, and the
  upstream PR cannot depend on them). A server with a draft model or a
  multimodal projector refuses the option at start and says so: their
  contexts hang off the same lifecycle (§3, H8) and are a second step.
- **What a freed context takes with it**: the slots' KV — a conversation's
  cached prompt is computed again (upstream's sleep does the same; issue
  #29322 notes `--cache-ram`'s cache dropped on wake). Documented in
  `--help` and the README, not hidden; it is what a chat turn's keep of
  minutes is for.

### 1.3 Louise

- **RAGE sets the keep on every corpus request, PER JOB TYPE: 20 ms to
  start, then trimmed** (operator; the start is 50 ms since the node was
  measured — §1.1, "Start at 50 ms" — and 20 ms is the floor). A job type is a STREAM: one
  collection's one kind of work at one endpoint (its corpus embedding; a
  query's embedding, its rerank …) — collections that share a model queue
  their jobs differently and get different keeps. The server knows nothing
  of job types: it holds the latest deadline asked (rule 2), which is what
  keeps the design general; a keep per model is only the default for a
  request that carries none. No person types it and no dialog shows it
  (RULE 2).
- **The trim is RAGE's, automatic, and in the FIRST deployment** (operator,
  choice B; the rule's constants below are the review's, to be set by
  measurement):
  20 ms is a start only because the trim is fast. It is inside the
  pipeline's own turnaround — LocalAI sends one text per call, and between
  two texts the wrapper publishes a receipt and makes a tokenise call;
  wikidata's mean gap is ≈ 13 ms, wikipedia's ≈ 170 ms (§2) — so a stream
  with wider gaps, left at 20 ms, pays a creation on most requests. The rule: a stream's keep
  DOUBLES on each premature expiry and decays slowly without one (AIMD, as
  RAGE's requests in flight, gpu-saturation-plan.md lever 3), between a
  floor of 20 ms and a ceiling; a stream then reaches its level within a
  handful of creations whatever the start (and again after a restart: the
  value is not kept, §10). "Premature" is defined by the creation's MEASURED
  time (§2): an expiry is premature when the stream's next request is ready
  within ten creations' time of it (§10 says why ten and not one) — the
  break-even of SKI RENTAL, weighted for what each side costs here. Trimmed by
  hand from a log, 20 ms would be a known slowdown deployed (CLAUDE.md,
  performance) on every stream nobody had trimmed yet.
- **After a first observation period the default may be set anew**
  (operator). Two values are "the default" and one observation feeds both:
  the START RAGE gives a new stream (20 ms), and the model's own default
  for a request that carries no keep (its YAML). What the period must show,
  per stream: the keep it settled at and how far it moved; premature and
  excused expiries, each with its cause; creations a day and a creation's
  time; chunks/s against the BEFORE row. Its length is the operator's to
  set — long enough for every collection's streams and for questions to
  have happened. A new default is the operator's word, from those rows; the
  trim goes on working under it.
- **An expiry the stream did not cause does not move its keep**
  (operator). Without this rule every question would double the keep of
  every corpus stream it made stand aside, and hold their contexts longer
  exactly when the question needs the card. Stated so that no list of
  causes is needed (review): A STREAM IS JUDGED ON WHEN ITS NEXT REQUEST
  WAS READY, NOT ON WHEN IT WAS SENT. Ready before the keep ran out and
  held back from an idle endpoint — a question's claim (the chat taking
  over: `dispatch._stand_aside`), a deferral, a creation refused for
  memory, a model loading, a paused node or collection — the expiry is
  EXCUSED: it neither doubles the keep nor counts as a clean interval
  toward its decay, and it is logged with its cause, apart from the
  premature ones. Ready only after the keep ran out, the stream was late
  by its own queueing and is judged on that gap alone. RAGE's gate already
  has both times and the cause per request (`dispatch.call`: the stand
  aside, `admit`'s wait, `GateTimeout`; `stood_aside()` per plugin); the
  wrapper says THAT a call met an expired keep, only RAGE knows WHY.
- **Who does what with the keep** (review) — one owner, RAGE; everyone
  else passes it on or obeys it:

  | | who | where |
  |---|---|---|
  | decides the value | RAGE, per job type (a collection's one kind of work at one endpoint): 50 ms (20 at first), then its own trim | a RUNTIME value, kept as the last good one like its requests in flight (invariant 10) — not an `[encoder]` key, not in a signed recommendation (09 §9.19), no dialog |
  | writes it into the request | Louise's embedding client, every place a body is built — the gate's and the probe's too | app/embeddings.py (`_body`, the probes) |
  | carries it | LocalAI, then the VeGa wrapper; neither reads it to decide | the request's `metadata` (§3) |
  | obeys it | llama.cpp's server: the deadline and §1.2's three rules | P11, part 1 |
  | answers for a request without it | the model's YAML default, written once at deploy | deploy/vega/stage/models/ |
  | counts what it cost | the VeGa wrapper → VeGa; RAGE reads it back | beside `GET /vega/passes` |
  | overrides by hand | nobody by default; an operator's override, if ever wanted, is a command (RULE 2) | not planned |
- **How it travels — no wire change** (review; §3): in the request's
  `metadata` map, which LocalAI already carries to the backend for
  `chat_template_kwargs`; the VeGa wrapper copies the request it forwards.
  Nothing docs/09 fixes is touched — to confirm against 03 §3.3 and
  vega.md §2 that a field outside the tag is outside the receipt.
- **Expired keeps are logged, for improvement** (operator). The signal is
  the PREMATURE one (review): every busy period ends with an expiry, by
  design; the cost is a creation that closely follows a free. Counted by
  the VeGa wrapper — it sees every call arrive and every answer leave, so
  it knows by its own clock when a call met an expired keep: the
  milliseconds since the last answer, the keep in force, the call's extra
  time — per run, beside `GET /vega/passes`; the backends' own lines are
  not kept at LocalAI's info level (§6). At a keep of 20 ms ONLY the
  wrapper can tell: RAGE's own clock differs from the engine's by the
  pipeline's turnaround, which is of that size. So the wrapper says which
  calls met an expired keep, by the tag's one-time id, and RAGE joins them
  to its streams and names the collection (every log line names its
  collection, CLAUDE.md).
- **First deployment: the corpus encoders and MADLAD** — qwen3-4b
  (1,882 MiB of context, worked 31 s in 24 h), nemotron (2,221), the MADLAD
  test copy (2,007): 6,110 of the 8,585 MiB listed in §6. The other
  2,475 MiB stay: ≈ 26.5 GB at rest, and under 30 GB with the corpus at its
  recorded peak — one to two GB under the reclaimer's line, not four. The
  query path (rerankers, query encoders, chat) joins once a creation is
  measured small against a person's wait (app/resident.py's tiers keep them
  loaded today); a chat turn then carries a keep of minutes, so a follow-up
  finds its cached prompt.
- **A query that finds no room** — the operator's order, as the code
  already provides it (review):
  1. "the contexts of the batch in queue can be pruned": a queued batch
     holds no context — nothing is allocated before its request — and the
     RUNNING stream's returns by itself: a question's claim makes the
     corpus stand aside (app/rage/dispatch.py; a request already sent
     finishes), and its context is gone within its keep, milliseconds. No
     "release now" call is needed.
  2. "some other model can be evincted": LocalAI's reclaimer, the least
     recently used unpinned model, weights and all — its next use pays
     11–19 s (M14, M26).
  3. "the chat processing can be postponed": last, against the standing
     order (query work highest, operator 2026-10-25), bounded by the
     request's own timeout.
  By §0's arithmetic 2 and 3 should rarely fire; the BEFORE/AFTER rows of
  §7 step 6 count them.
- **The keeper meets sizes it does not yet understand** (review; §6): an
  opted-in model has TWO sizes, idle and running, and app/resident.py's
  `REST_FLOOR` takes the idle one for a model partly off the card. First
  deployment: the released memory is HEADROOM, not budget — the keeper
  counts an opted-in model at its running size, by rule (it reads the
  option from `/system`, P1), with its test. Making the dispatcher admit
  contexts against the card is a change to WHAT RUNS WHEN:
  docs/notes/scheduling.md §6 binds it.

### 1.4 Invariant 12 — every creation is a first pass after a load

- The keep is RAGE's runtime value (§1.3) and NO part of a run's identity
  — provided the bytes are identical across creations.
  The option itself is in the load's stated options, so opting a model in
  is a new run, gated once (09 §9.12).
- **The gate with keep 0 is the cycle test** (review): every fingerprint
  text then meets a new context — the worst case, with no instrument of its
  own (O4). Ten creations do not gate a per-creation fault: one at rate p
  is caught by n clean creations with probability 1 − (1 − p)ⁿ — n = 10
  catches p = 5 % four times in ten; 95 % needs 59 at 5 %, 299 at 1 %. So
  ≥ 300 creations per encoder, short and long probes (`LONG_PROBE`) both.
  The class is known here: M5 (1 of 20 scores, first pass only) and M2
  before its remedy (4 of 8 runs).
- **A canary per context** (review; DECIDED by the operator 2026-11-11 and
  built, §10 "The canary"): every context created computes one fixed input
  and sets its output against the load's own before any request runs on it;
  a context that does not reproduce it is discarded before it writes. It is
  the ENGINE that does it, in its creation path — the review had the wrapper
  embed a fingerprint text, but the wrapper learns that a context was
  created only when the answer is back. Until it, no check at write time
  set a vector against a second computation of anything.
- The gate measures what is deployed (measuring.md §3): the keep-0 pass AND
  a pass at RAGE's keep.

### 1.5 Why this form

| | pros | cons |
|---|---|---|
| status quo — every context held for the model's life | nothing to build or re-gate; the driver's spill to host RAM works today | 34 GB held on a 32.6 GB card; the driver chooses what leaves it; every new model is fitted against contexts that work seconds a day |
| an idle timer with the weights kept (the first brief: a level of llama.cpp's sleep, N seconds) | few creations a day; rides upstream's sleeping state | at N = 600 s ≈ 2 GB freed (only qwen3-4b idles that long); every handler must wake the server; upstream's sleep has an open race |
| per request, no keep | the least memory held; nothing to tune | a creation on every input: one is ≈ 0.6 s (§2), so every 60 ms of creation is 10 % of throughput |
| **per request with a keep in ms, set by RAGE (settled)** | one creation per busy period; ≈ 6 GB of context given back while idle on our node; a question frees the corpus's contexts by standing aside; a known standard (`keep_alive`); no wire change | a creation per busy period on the embedding path; a keep shorter than a stream's gaps cycles the context unseen — hence the log; the gate grows to ≥ 300 creations |
| memory mapped per KV block (Prism, kvcached) | the finest grain, per conversation | a KV allocator on Level Zero virtual memory, for KV only, while the compute buffers are the larger share here; nothing exists for SYCL (shared-context.md §3) |

## 2. What decides whether it pays — measure FIRST

| unknown | why it decides | how |
|---|---|---|
| **How long is a creation + free, per encoder?** The go / no-go number. | Paid at every busy period, and on every request whose keep was too short. A corpus input is short work: a 1,000-token chunk on qwen3-4b at its gated 1,651 tok/s is ≈ 0.6 s. A creation allocates AND ZEROES the KV cache (≈ 1.1 GiB for qwen3-4b), reserves the compute buffers from the worst-case graphs, and the first decode commits the op pool again (§3). Expected in the tens to hundreds of ms — an expectation, not a measurement. | the instrument below, per encoder at its live settings (ctx, batch, ubatch, flash attention, cache types); the first decode after a creation timed apart — the queue and its compiled kernels are the process's (§3), so it should not compile again: to confirm |
| Does freed context memory go back to the DRIVER on the B70? | The code says the backend gives it all back (§3: the op pools die with the context; KV and compute buffers are `zeMemFree`d). What reading cannot settle is the driver's side — compute-runtime and `xe` handing the pages back at the free, or keeping them for reuse — and what a process keeps whatever is freed (its ~270 MiB floor, §6; upstream #25271 measured 158 MB left on CUDA after a FULL sleep). | per process, before, during and after a free: the backend's own count (`GGML_SYCL_MEMTRACE=1`: every allocation and free by kind — buffers, pool_leg, pool_vmm — and the device's total / free / allocated / other) beside the driver's (the xe fdinfo app/gpu_memory.py reads, `vram0` and host apart; LocalAI's `/system` `size_vram`); the startup lines `GGML_SYCL_SUPPORT_VMM` / `GGML_SYCL_ENABLE_VMM` / `GGML_SYCL_USE_LEVEL_ZERO_API` say which paths run |
| Are the vectors byte-identical across creations? | invariant 12 (§1.4) | fixed texts embedded before, between and after creations in the instrument; then the gate with keep 0 on the patched backend, ≥ 300 creations per encoder |
| How long are the gaps between two requests of one stream, AT THE ENGINE? | It is what the keep must outlast. On record are card-idle MEANS, an upper bound: wikidata (4 lanes) ≈ 13 ms a request (11.1 s/min over ~832 requests/min); wikipedia (2 lanes, producer-bound) ≈ 170 ms (25.6 s/min over ~153) — gpu-saturation-plan.md, lever 2's table. The corpus stands aside 9–37 ms between a question's rerank variants and for whole steps (scheduling.md §12); stream boundaries leave whole idle seconds, 0.1–0.3 s/min. No tail is on record. | RAGE times it at its gate — an answer's arrival to the next request's send, per endpoint, every lane — with no engine change. Inside one request LocalAI sends ONE TEXT PER CALL (§3): those gaps only the wrapper sees — counted on the test copy now, in production from the engine change on (a changed wrapper is a new engine, §4); a histogram by powers of two, as `GET /vega/passes` serves lengths |
| What does a keep under a second release, and how often does it create? | sizes the gain before any build | the card record (app/rage/card_record.py, per second; no card time): per encoder over 24 h, the GB·h given back and the creations a day, a model counted running in every second it worked |
| Does the stack bear it? | thousands of GB-sized allocations and frees a day is a new load on the `xe` / Level Zero stack of M21, M22 and O6 | a soak in the instrument: 10,000 creations, the driver's count flat at the end, no `xe` reset in the kernel log |

Acceptance (proposed; the operator may set others): ≥ 90 % of the context's
bytes — KV + compute buffers + op pool AS THE BACKEND COUNTS THEM, not
`size_vram` less the weights file — back to the driver after a free; a
creation + free under 250 ms per encoder; with the keep in force every
collection's chunks/s within 5 % of its BEFORE row (`louise dev
schedule-report --bench`); every vector byte-identical across ≥ 300
creations; the soak clean. If the memory does not come back on the B70,
that is a finding about the driver or ggml-sycl (who keeps it, and whether
a free must also trim something), not the end of the design: record it
here and in shared-context.md, and measure the same instrument on the CPU
build (`build.sh cpu`, no card needed) and on any other backend at hand —
the design is for every backend, and those numbers are part of the evidence
an upstream proposal carries.

**The instrument** (half exists): `GGML_SYCL_MEMTRACE=1` in a test copy's
env gives the backend's count on today's engine. The rest is a diagnostic
llama.cpp program, or a `deploy/localai/patches/diag/` patch
(diag/README.md: never in `series`, never on a live backend), built on
BUILD_HOST with `build.sh sycl_f16` from a local copy of `series`: load the
model once, then loop create context → embed the fixed texts → free → read
the driver's count. A TEST COPY of an encoder under its own model name, in
a quiet window, run by München with the node's harness
(`/root/llama-intel-arc-b70`, measuring.md §1); nothing in production
touched. Measure the DEPLOYED settings (measuring.md §3), and watch free
space: measurement is a writer (measuring.md §6).

## 3. Where the context lives — the code, read at the pins

*llama.cpp `ddf48f8` and LocalAI `16acc89`, read 2026-11-11; nothing here
was run. Paths are llama.cpp's unless LocalAI is named; `grpc-server.cpp`
is LocalAI's `backend/cpp/llama-cpp/grpc-server.cpp`.*

**The request's path on our node** (vega.md §2): Louise → `POST
/v1/embeddings` (model `vega-…`, tagged inputs) → LocalAI → gRPC
`Embedding`, ONE TEXT PER CALL → the VeGa wrapper (cuts with the backend's
`TokenizeString`, forwards, sends the receipt BEFORE the vector goes back)
→ llama.cpp's backend. The engine sees one task per text, with the
wrapper's and LocalAI's turnaround between two.

**LocalAI's llama.cpp backend:**
- It compiles llama.cpp's own `tools/server/server-task.cpp`,
  `server-queue.cpp`, `server-context.cpp` into its translation unit;
  `params_parse` turns `ModelOptions` into `common_params` (`n_ctx`, the
  `Options` strings `ubatch:`, `parallel:`, `kv_unified` —
  `params.kv_unified = true` by default there —, cache types from their own
  proto fields; `params.cache_idle_slots = true`; `params.fit_params =
  true`, grpc-server.cpp:592); the server loop is `ctx_server.start_loop()`
  (:266).
- Any option beginning with `-` goes to llama-server's own parser (:1302;
  `common_params_parse(…, LLAMA_EXAMPLE_SERVER)`, :1505): the server's new
  flags need no LocalAI code to be SET; §0's model options are for a
  readable YAML.
- Its RPCs post tasks through `get_response_reader()` (:1647, 2604, 3205 …)
  and tokenise with `impl->vocab`: neither needs a context. SEVEN PLACES
  READ ONE, `ctx_server.get_llama_context()`: `PredictStream` :2593,
  `Predict` :3196, `Embedding` :3750 and :3832, `TokenizeString` :4570 and
  :4581, `Detokenize` :4749 (`parse_options`, `llama_pooling_type`,
  `common_token_to_piece`). Each has a model- or params-side equivalent and
  must take it, or it reads null while the model is idle — and the wrapper
  calls `TokenizeString` for every input. That is the LocalAI half of the
  patch.
- A request's `metadata` map reaches the backend today: the OpenAI
  request's field (LocalAI core/schema/openai.go:265) → `RequestMetadata`
  (core/http/middleware/request.go:459–461) → `PredictOptions.Metadata`
  (backend/backend.proto, field 52; core/backend/options.go:673–689), read
  in the backend for `chat_template_kwargs` (:2827, :3429). Embeddings
  build their options with the same function
  (core/backend/embeddings.go:130), and the VeGa wrapper copies the request
  it forwards (`q.CopyFrom(req)`, deploy/vega/vega_wrapper.py). To confirm
  with one request on a test copy.

**llama.cpp, `tools/server/`:**
- `load_model` (server-context.cpp:1203) builds `llama_init =
  common_init_from_params(params_base)` (:1305) — model AND context
  together: `common_init_result` owns both (common/common.cpp:1178–1179),
  runs `common_fit_apply` first (:1312) and `llama_init_from_model(model,
  cparams)` last (:1411) — then `ctx_tgt`, the slots (`slot.mem.init(ctx_tgt,
  …)`, :1525) and the batch (`batch.init(ctx_tgt, …)`, :1584).
- `destroy()` (:1135) frees the draft context, `llama_init` (model and
  context) and the multimodal context; the slots also hold `mctx` and
  `tts.ctx` (:1523–1534).
- UPSTREAM'S SLEEPING STATE is `handle_sleeping_state` (:1151): `destroy()`,
  and on wake `load_model(params_base)` again or `GGML_ABORT("failed to
  reload model after sleeping")` (:1161–1164); driven by
  `queue_tasks.on_sleeping_state` and `start_loop(sleep_idle_seconds ×
  1000)` (:5203–5206; server-queue.cpp:278–362; llama-server's
  `--sleep-idle-seconds`, PR #18228). Handlers wait for the wake in
  `server_res_generator` (`wait_until_no_sleep()`, :5274–5280); a task
  posted to a sleeping queue is not processed (server-queue.cpp:335–338).
  It frees the WEIGHTS too — what LocalAI's idle unload did, switched off
  for its cold starts (#398). It is NOT the vehicle for this design (§1.2).
- What the design uses is the queue beneath it: the loop already tells an
  empty queue and an elapsed time (`should_sleep`, server-queue.cpp:290–297)
  and work from housekeeping (`task_resets_idle_timer`, :24: a metrics task
  does not count).
- No route handler reads the context: nothing in `server_routes`
  (server-context.cpp from :5560) touches `ctx_tgt`; only `get_meta()` does,
  for two token strings the vocab gives (:5220–5224).
- A creation: `llama_context`'s constructor makes its own backends
  (`ggml_backend_dev_init` per device, src/llama-context.cpp:432; held in
  `backends`, src/llama-context.h:396); the KV cache is allocated and
  ZEROED (`ggml_backend_buffer_clear`, src/llama-kv-cache.cpp:434); the
  scheduler reserves the compute buffers.

**ggml-sycl — what a free gives back:**
- The op pools belong to the backend instance, and a context frees its
  backends: `pools[]` and `host_pools[]` are members of
  `ggml_backend_sycl_context` (ggml-sycl/common.hpp:546–548, made on first
  use, :557); `ggml_backend_sycl_free` deletes it (ggml-sycl.cpp:6521); the
  VMM pool unmaps every commit and frees its virtual range (:2102–2116), the
  legacy pool frees its cached buffers (:1952–1977). The oneDNN engine and
  stream maps are members too.
- KV and compute buffers are `zeMemAllocDevice` / `zeMemFree` where the
  build has `GGML_SYCL_SUPPORT_LEVEL_ZERO_API` and
  `GGML_SYCL_USE_LEVEL_ZERO_API` is not 0 (default 1, ggml-sycl.cpp:460;
  ggml-sycl/common.cpp:97–148): no SYCL USM pool sits between.
- The queue and its compiled kernels are the process's (`dpct::dev_mgr`, a
  singleton, dpct/helper.hpp:995).
- `GGML_SYCL_MEMTRACE=1` (ggml-sycl.cpp:440, memtrace.cpp) logs every
  allocation and free by kind.

**Hazards read in the code** — each becomes a test of the patch:

| | hazard | where | what the patch does |
|---|---|---|---|
| H1 | a reload that fails ABORTS the process | server-context.cpp:1161–1164 | a creation that fails is refused (§1.2) |
| H2 | fit and reload disagree: LocalAI loads with `fit_params = true`, the first fit writes its result into the params, and a wake re-fits or fails (upstream #30528, closed unmerged 2026-10-14) | grpc-server.cpp:592; common/common.cpp:1312 | the context is created from the first load's resolved cparams, never through `common_fit_apply` |
| H3 | upstream's sleep race: a handler tokenises, the loop sleeps, the task is stranded or the unload segfaults (#30012, open; unfixed at `ddf48f8`) | server-queue.cpp | nothing to guard — the loop's thread owns the context and the model stays — but P11 edits the same files: expect a rebase |
| H4 | seven reads of a context that is null while idle, the wrapper's `TokenizeString` among them | grpc-server.cpp, above | model- or params-side equivalents |
| H5 | the keeper takes an idle model for one partly off the card | app/resident.py `REST_FLOOR`, §6 | the keeper's rule (§1.3) |
| H6 | every creation is a first pass after a load (M5, M2) | — | the keep-0 gate, the canary (§1.4) |
| H7 | a keep shorter than a stream's gaps: a creation per request, unseen | — | the wrapper's count, RAGE's keep (§1.3) |
| H8 | `load_model` does more than model + context: slots, batch, prompt cache, metrics, the chat templates (once), the draft and multimodal contexts | server-context.cpp:1203–1641 | split it — what a load does once, what each creation does; refuse draft / mmproj (§1.2) |
| H9 | the loop checks its idle condition once a second (`max_wait_time`) | server-queue.cpp:289, :353–360 | the wait ends AT the keep's deadline: a keep in ms cannot ride a 1 s poll |
| H10 | while the loop's thread is inside a decode, a WORKER thread takes new tasks (`yield_to_queue`, `worker_loop`) | server-queue.cpp:163–270 | creation and free belong to the loop's thread, between iterations, never to the worker |

**Upstream, as searched 2026-11-11** — no issue or PR proposes a context on
request or a weights-kept release: not a duplicate. Around it: #18228 (the
sleep); #30012 (open: its race); #30528 (closed unmerged: re-fit on wake);
#25271 (open: `ggml_backend_dev_reset()`, 158 MB left on CUDA after a
sleep); #27307 (closed) and #25570 (open): exit sleeping router workers;
#25646 (open: an Intel dGPU evicts an idle model's weights, the perf drop
reported on Vulkan — our M16 seen by others); #26130 (open: per-device
memory on `/metrics`). The direction on record is toward freeing MORE when
idle; the proposal should lead with a creation's time against a full
reload, per backend.

**The patch to write (P11)** — four parts, one design:
1. llama.cpp's server (a file under LocalAI's
   `backend/cpp/llama-cpp/patches/`, §4): the option and its default keep;
   `load_model` split (H8); the loop's create and free (H9, H10); the
   keep's three rules; the refusal (H1); no re-fit (H2); `--help`, the
   README, and server tests (`tools/server/tests/unit/`, beside
   test_sleep.py): a model embedded before and after a creation,
   byte-identical; a keep kept, and expired; a request without the field; a
   0 that does not shorten; a creation refused, then retried; a request
   arriving as the context is freed.
2. LocalAI's backend: the seven reads (H4); the keep read from `metadata`;
   the model options.
3. The VeGa wrapper: the field forwarded (it is, by `CopyFrom`); the gaps
   and the premature expiries counted and told to VeGa; the canary (§1.4).
   The wrapper is part of every run's engine (§4).
4. Louise: RAGE's keep on corpus requests — in every place app/embeddings.py
   builds a request body — and its trim per stream (a premature expiry
   doubles; an excused one, a stand-aside for a question among them, moves
   nothing); the keeper's rule (H5); the gate's keep-0 pass. Each with its
   test.

## 4. Our LocalAI and llama.cpp customizations (the series)

Base: LocalAI master `16acc89` (llama.cpp `ddf48f8`) since 2026-11-02, the
ONE llama.cpp engine of every model (operator's A). Each patch is described
with its upstream status in docs/notes/localai-bugs.md "Patches we carry";
`tests/test_localai_patches.py` holds the series, the files and that
section to each other. A LocalAI patch that touches llama.cpp ADDS a file
under `backend/cpp/llama-cpp/patches/` which the backend's `prepare.sh`
applies to llama.cpp — the new patch follows that form.

| | what | file |
|---|---|---|
| P1 | `GET /system` lists each loaded model's load options | 0001-system-list-load-options.patch |
| P2 | `Embedding` embeds the token ids it is given (VeGa's token cut) | 0002-llama-cpp-embed-token-ids.patch |
| P3 | the encoder of encoder-decoder models runs (MADLAD/T5) | 0003-llama-cpp-encoder-decoder.patch |
| P4 | encoder-decoder slots encoded in waves | 0004-llama-cpp-encoder-decoder-waves.patch |
| P5 | ggml-sycl batched F16 matmul converts src1 for the GEMM that runs (the KQ fault, upstream-bugs.md S1) | 0005-llama-cpp-sycl-batched-mul-mat-src1.patch |
| P6 | Command R7B / Command A tool calls parse | 0006-llama-cpp-chat-cohere-command3.patch |
| P7 | upstream's dmmv cleanup, carried ahead of the pin | 0007-llama-cpp-sycl-dmmv-cleanup.patch |
| staged P8, P9 | encoder output per sequence; on the device (not rebased onto `16acc89`: 4 rejected hunks) | patches/staged/0006-, 0007- (staged/README.md) |
| staged P10 | Nemotron rerank-1b-v2 (#487), for a separate backend | patches/staged/0008-llama-cpp-nemotron-rerank.patch, nemotron-rerank.series |
| diag | `GGML_SYCL_CPU_OPS` bisection; `test-backend-ops` KQ shapes | patches/diag/ (README.md) |

The new patch is P11 (P1–P7 in the series, P8–P10 staged); it goes to
`staged/` first (built and tested on the CPU), then up into `series` after
its card gates (staged/README.md).

**The build** — `sh deploy/localai/llama-cpp/build.sh sycl_f16 <out>`
(LocalAI's own Dockerfile and CI arguments, the series applied with
`git am`); `INTEL_STACK=deploy/localai/llama-cpp/intel-stack-2026-11c.conf`
is the LIVE runtime ("11c", `STACK_ON=prebuilt`: LocalAI's prebuilt grpc
builder, oneAPI 2025.3 / libsycl 8, Level Zero loader 1.27, with compute
runtime 26.40.40196.8 + IGC 2.42.6 + gmmlib 22.10.2 layered on);
`SYCL_DEVICE_ARCH=bmg-g31` (AOT) finds no kernel image under libsycl 8 —
the stack is for JIT. Builds run on BUILD_HOST (ssd3/louise-build, #380).

**The engine's identity** — a Codex's `space.encoder.engine.image` is the
backend's file tree AND the VeGa wrapper package (`vega_wrapper.
engine_manifest`, since 2026-10-31): ANY rebuilt backend or changed wrapper
is a NEW ENGINE, so every model on it is a new run, gated once (docs/03
§3.3) before it writes. An engine swap archives the old tree and package
and proves the pair against the Codex (approved-encoders.md: "A Codex's
engine image is the backend's tree AND the wrapper package"; wikidata's
32b0caf3 = tree 0e705989 + package 601c849e). Staging and rollout scripts:
`deploy/vega/stage/` (build_vega_package.sh, stage_src.sh,
build_louise_backend.sh), `deploy/vega/switch/`.

**Bundle with it**: #502's wrapper side (inputs computed in more than one
pass, `kind: passes`, vega.md §2) waits for the next engine change that
re-gates every space — this one, if it reaches deployment.

**The VeGa wrapper's determinism contract** (deploy/vega/vega_wrapper.py
`DETERMINISM`, invariant 12): a SYCL card run of an embedder needs
`GGML_SYCL_ENABLE_DNN=0` and `GGML_SYCL_FA_ONEDNN=0` in the model's env and
`parallel:1`, or the wrapper states no run; `near_exact_relaxed` /
`LOUISE_NEAR_EXACT_MEASURED` is the one measured exception
(near-exact-runs.md).

## 5. B70 and stack pitfalls

"AE" = approved-encoders.md. Measured facts first, then what the notes call
probable or open.

| # | symptom | cause (as established) | remedy in force | where |
|---|---|---|---|---|
| M1 | same input, different bytes run to run | oneDNN picks GEMM kernels by heuristic | `GGML_SYCL_ENABLE_DNN=0` | AE "Known limits of the Arc Pro B70"; AE "Bitwise reproducibility, by vendor" |
| M2 | flash attention ON: a vector flips across fresh loads, a NaN, a vector 0.0865 off (qwen3-4b, 4 of 8 runs) | oneDNN's fused SDPA (`GGML_SYCL_FA_ONEDNN`, default 1), which DNN=0 does not cover | `GGML_SYCL_FA_ONEDNN=0`, the wrapper's contract | AE by-vendor rows (FA on / FA on + FA_ONEDNN=0); near-exact-runs.md; STATUS 2026-10-31 |
| M3 | FA_ONEDNN=1 exact across loads but another function (cosine 0.99952) | — | off for embedders (chat only, operator's C) | AE "oneDNN switched on, measured" |
| M4 | FA off: bit-exact but WRONG (Qwen3 cosine 0.126; MADLAD wrong text) | `ggml_sycl_mul_mat_batched_sycl` flat-copies src1 for oneDNN, oneMKL then reads it contiguous under DNN=0 | P5; MADLAD on an f32 KV cache | localai-bugs.md P5; upstream-bugs.md S1 |
| M5 | reranker at ctx 16,384: 1 of 20 scores differs on the first pass after a load | the oversized context (mechanism not isolated) | context = the platform window | AE "Known limits" |
| M6 | `parallel:4`: request order moves 16 of 20 scores | sequences batched together change each other's sums | `parallel:1`; the wrapper refuses a load without it | AE; vega_wrapper.py `determinism_lacks` |
| M7 | a batch of 16 long texts at cosine 0.93–0.98 | upstream bug on c0fd786, fixed by ddf48f8 | the gate's `LONG_PROBE` | AE "Every live space passes on 16acc89" |
| M8 | wrong vectors that repeat exactly; a same-card re-embed and the gate agree with them | torch's fp32 softmax (torch, not llama.cpp) | SDPA; only ANOTHER device catches this class | gpu-silent-wrong-vectors.md §1–2 |
| M9 | one NaN fails the whole `/v1/embeddings` with 500 | Go's JSON cannot encode NaN; intermittent from the backend | `embeddings._numeric_failure` retries | localai-bugs.md B9 |
| M10 | the reclaimer evicts one model every 500 ms while another process holds the card | `checkMemory` uses the device-wide `UsagePercent` | threshold 0.95 | localai-bugs.md B4 |
| M11 | a model still LOADING is evicted first; its request gets 500 | `lastUsed` is zero at registration | the preload budget and back-off (app/resident.py) | localai-bugs.md B11 |
| M12 | a backend whose eviction failed keeps running, uncounted, never evicted | untracked before the stop; a graceful stop waits 30 s then `ErrModelBusy` | none upstream | localai-bugs.md B3 |
| M13 | a wedged backend is logged "Deleting…" and stays alive | deleted only if `!IsAlive()` | busy watchdog (10 min since 2026-10-12; 5 min also on record — read the live value) | localai-bugs.md B10; STATUS |
| M14 | all 11 models preloaded (28.4 GB at rest); compute-buffer growth crossed 0.95; chat cold starts of 11–15 s | the room check counted the set at rest, without its working memory | budget = threshold × total − growth margin − AIMD penalty | STATUS "2026-11-08: the resident set preloaded within a measured budget" |
| M15 | space claimed, nothing to evict | `evictLRUModel` skips pinned models | preload, never pin; only a running embed batch pins | STATUS 2026-11-08 |
| M16 | the card overcommitted from outside: `xe` silently moves every model's buffers to host RAM; OOM kills; `xe` engine reset / `DEVICE_LOST` | a process outside the pool | card work through LocalAI, or only on a free card | STATUS 2026-09-20 afternoon; gazzetta-plugin.md |
| M17 | a YAML edit + `/backend/shutdown` reloads the OLD config | YAML is read at LocalAI's start | restart LocalAI | AE |
| M18 | a model loaded before an in-place backend rebuild keeps its old process; a hand-placed backend reads "not found" until restart | `/models/reload` stops nothing; `RegisterBackends` runs only at start | `/backend/shutdown` each model; the first gate must name a NEW engine and run id | measuring.md §9; vega.md §10 finding 2 |
| M19 | a measurement ran on a retired engine | a stale install left in place | `deploy/vega/retired_backends.py --engine` | measuring.md §6; AE "Retiring an engine" |
| M20 | a repeat measures the slot prompt cache (1.26e-3 off), not determinism | single-slot cache | no repeats served from it | measuring.md §3 |
| M21 | Level Zero loader ≥ 1.32: `ZE_RESULT_ERROR_UNSUPPORTED_VERSION`, falls to OpenCL, the load dies | the loader's refusal (the 6.18 kernel's part not proven) | loader 1.27; only the runtime updated (intel-stack-2026-11c) | AE "Known limits" |
| M22 | oneAPI 2026.2's `libsycl.so.9` segfaults at load, then an `xe` engine reset | — | oneAPI 2025.3 | AE "Known limits" |
| M23 | AOT bmg-g31: "No kernel named …" | no device image matched; AOT has no SPIR-V fallback | JIT | AE "Known limits" |
| M24 | the driver stated is not the one loaded | the backend tar bundles its own `libze_intel_gpu` (`ZE_ENABLE_ALT_DRIVERS`) | the driver read from the backend's `/proc` environ | vega.md §9, §10 finding 4 |
| M25 | `/system`'s pid and CPU % are the wrapper's (36 MB), not the llama.cpp child's | — | follow the descendants; "idle" = `encoder_use` + leases | vega.md §10 finding 5; scheduling.md §5 pitfall 17 |
| M26 | idle unload: 19 s cold starts, 12 a day | idle read off a signal the work does not move | the encoder lease (600 s TTL); `encoder_use` | scheduling.md §5 pitfall 17 |
| M27 | the idle watchdog unloaded the chat after 5 min | `watchdog_idle` | `watchdog_idle_enabled: false` | deploy/vega/stage/localai_settings.py |

**Probable or open:**
- **O1** `GGML_SYCL_ENABLE_MKL_FA` (default 1; GQA ≥ 2, KV ≥ 1024, oneMKL GEMMs): "next in line", never measured (AE "Arithmetic order").
- **O2** the ggml-sycl op pool: VMM, ON by default at `ddf48f8`, keeps committed pages for its life; legacy pool caches 256 buffers; both freed only with their owner — and the owner is the backend instance, which a `llama_context` creates and frees (§3): the BACKEND gives the memory back at a context's free. Whether the DRIVER does on the B70 is UNMEASURED — §2.
- **O3** a creation + free's time per encoder: unmeasured (§2).
- **O4** THE GATE'S "FRESH LOADS" ARE PROCESS RELOADS (`/backend/shutdown`): they do not exercise a context created inside a live process. The gate's keep-0 pass does (§1.4).
- **O5** B4's second effect: no wait for the driver to report freed memory, so a second eviction can follow (Ollama waits up to 5 s) — code reading only.
- **O6** hangs of unknown cause (`xe`, Level Zero with two runtimes on one card, or OpenVINO): STATUS ~1672–1700.
- **O7** AE "Bugs" table: the occasional non-finite vector; the gutenberg batching incident (`EMBED_BATCH=1`).
- **O8** upstream reports not seen here: OPT reorder corruption (#21893), XMX NaN (#30892), FA corruption (#19276, #30624), device-lost storms (#966, `SYCL_UR_USE_LEVEL_ZERO_V2=0`), CR 26.27 with loader < 1.32 segfaults (#972) — AE "Known limits", last row.
- **O9** S1's KQV case fails in the suite while the model's V side decodes right; not isolated (upstream-bugs.md S1).

**The notes disagree** (settle before relying on either): AE "Known limits" says the reclaimer is "pinned off", while localai_settings.py and STATUS 2026-11-08 have it ON at 0.95 (the live value is the settings file's); the busy watchdog is recorded as 5 and as 10 min.

## 6. GPU memory on this node — what the patch meets

- **The card**: 32,656 MiB (also written 32.6 GiB / 32.7 GB). Everything
  resident 2026-11-07: 30,267 MiB (92.7 %), host RAM peak 41.6 of 63.4 GB.
  2026-11-12: ~34 GB "resident" > the card — the driver keeps what does not
  fit in host RAM (below).
- **LocalAI's reclaimer**: ON at 0.95 (≈ 31.0 GB) since 2026-11-08 (0.68,
  then 0.85 on 2026-10-23); it evicts the least recently used unpinned
  model (B4, B11 above). Idle watchdog OFF; busy watchdog ON.
- **`size_vram`** (LocalAI `/system`): the driver's per-process count —
  a model's grown compute buffers included. app/resident.py keeps per model
  the fewest bytes seen (AT REST) and the most (LARGEST): the budget sums the
  at-rest sizes, the room check reads the largest. With a context
  on request an opted-in model has TWO sizes, idle (weights + heap) and
  running, and `REST_FLOOR = 0.5` (app/resident.py:261, app/model_load.py:159;
  `_whole`, app/resident.py:718) takes a reading under half the largest for
  a model partly off the card: qwen3-4b idle holds ≈ 2,381 MiB (4,263 −
  1,882) against a largest of 5,643 on file under the same name (0.42; 0.56
  if the file was reset at the trim) — discarded or accepted by an accident
  of ratio. Accepted, the budget admits more and the contexts' return is
  growth the margin (below) learns only after it has peaked — M14 again;
  discarded, the release is free memory the budget does not spend. The
  second is the first deployment, by rule (§1.3, H5).
- **The growth margin**: the card's count minus the at-rest sizes, each
  beat; its peak over 6 h; 3.0 GiB until measured (the night it was set:
  ≈ 28.4 GB at rest, 31.36 GB read, ≈ 2.96 GB grown). The AIMD penalty adds
  a model's at-rest size per eviction within 10 min of its load, halved
  after 2 h without one. `state/resident-budget.json`; `louise node resident
  show`.
- **"In host RAM"**: the driver's GPU buffers placed in host memory
  (`drm-resident-gtt` against `vram0` in fdinfo), real memory, not RSS; an
  idle model pushed off the card stays there and moves back only when used
  and the card has room. The card's used memory comes from
  `DRM_IOCTL_XE_DEVICE_QUERY` (needs `CAP_PERFMON`; the GPU probe has it).
  Each process also pays a SYCL / Level Zero heap floor of ~270 MiB.
  STATUS 2026-09-20 afternoon.
- **The context share per model**, 2026-11-12 (`size_vram` − the weights
  file; the backends' load lines are not kept at LocalAI's info level):
  qwen3-4b 3,262 MiB (≈ 1.1 GiB KV, ≈ 2.1 GB compute buffers at ubatch
  8192; 1,882 MiB since its ubatch went to 2048 at 2026-11-12 06:52Z,
  `size_vram` 5,643 → 4,263), nemotron 2,221, the MADLAD test copy 2,007, dar-legal-it 632, chat
  633, omnivoice 489, medcpt-query 276, bge-m3 232, medcpt rerank 213, bge
  reranker ~0. The exact KV / compute split needs one reload per model with
  the backend's stderr captured (LocalAI's debug level).

## 7. The plan, step by step

1. **Read** (this brief; CLAUDE.md invariants 1, 2, 7, 12 and the
   engineering rules; measuring.md §6; approved-encoders.md "Bitwise
   reproducibility, by vendor"; localai-bugs.md "Patches we carry" and B3,
   B4, B10, B11; vega.md §2, §9, §10; scheduling.md §6 and §12 — RAGE and
   the keeper are touched; gpu-saturation-plan.md's levers; app/resident.py's
   docstring). §3's reading is done: re-read only what a rebase moved.
2. **Count without the card** (§2): the card record at a keep under a second — the
   GB·h given back and the creations a day, per encoder; and the gaps
   between requests as RAGE's gate times them. Write the numbers here.
3. **Measure on the card** (§2) with München on a test copy, in a quiet
   window: a creation + free's time and the first decode after it; the
   memory back (`GGML_SYCL_MEMTRACE=1` beside the driver's count); the
   bytes across creations; the per-text gaps; the soak. Write the numbers
   here. Stop if a creation is not small against §2's acceptance, or the
   memory does not come back.
4. **Write the patch** (§3, P11's four parts): `staged/` first, CPU tests
   first as P3/P4 did, in upstream's form; then `build.sh`.
5. **Card gates** on a test name: `tools/vega_repro_check.py`; the gate with
   keep 0, ≥ 300 creations per encoder, and a pass at RAGE's keep (`louise
   hold embedder check <collection>`, the calibration gate, multi-load);
   the canary seen to refuse a planted bad context; throughput with `louise
   dev schedule-report --bench`; card memory idle and running.
6. **Deploy on the operator's word**, bundled with #502's wrapper (one
   re-gate of every space on the engine) and with the keeper's rule;
   BEFORE/AFTER rows: card memory at rest and at its peak, reclaimer
   evictions per hour, creations and premature expiries per collection, the
   first request's latency after an idle spell, chunks/s per collection.
   Then THE FIRST OBSERVATION PERIOD (§1.3): the same rows per stream with
   the keep each settled at, and from them, on the operator's word, a new
   default.
7. **Upstream** (on the operator's word): an issue, then a PR to
   ggml-org/llama.cpp (the server's option, its tests, its docs), and to
   mudler/LocalAI (the model options, the keep from `metadata`, the seven
   reads), with the measurements of steps 3 and 6 as the evidence; the
   patch leaves our series when upstream merges it (localai-bugs.md
   "Patches we carry").
8. **Record**: localai-bugs.md (the new P entry), shared-context.md (the
   measured numbers), vega.md (the wrapper's counts), scheduling.md (RAGE's
   keep), STATUS.

## 8. Rules that bind this work

- Deploys and engine switches only on the operator's word; München (the
  node's session) runs everything on the node. A test copy under its own
  model name never touches a live space.
- "Fix it after" is never an option; a known defect is fixed before
  anything proceeds (CLAUDE.md).
- A new engine writes no vector before its run passes the gate (invariant
  1); every space on it is re-gated once; bundle engine changes to pay one
  re-gate (RULE 1).
- Measure before proposing; a scheduling or memory change whose commit has
  no numbers is a guess (CLAUDE.md, measuring.md).
- Every task ships tests; new behaviour without a test is unfinished.
- Questions to the operator: one shape (what it is; choices with pros and
  cons, the status quo included; the recommendation and what happens if
  they say nothing).
- RAGE, the keeper or the round loop touched: docs/notes/scheduling.md
  FIRST and its checklist (§6); measure with `louise dev schedule-report`
  before proposing.
- Performance is second only to consistency (CLAUDE.md): a keep that makes
  a context cycle is a defect, found by measuring and fixed like any other.

## 9. What changed from the first brief, and whose word each part is

- **The first brief** (d058f93): the context freed after N idle seconds, as
  a level of llama.cpp's sleep. Its §3–§5 and §7 are §4–§6 and §8 here,
  unchanged but for O2–O4, the keeper's paragraph in §6 and two rules in §8.
- **The review of the code** (ce7ad03 … e9f35bd; §8–§10 of e9f35bd's text,
  with the exchange that led here): §3.
- **The operator's** (§1.1, and §0's general design): the semi-cold start;
  idle or running, no sleep; allocated on the request, freed when the work
  is done; the keep in the request, in ms, 0 = none; RAGE sets it, per job
  type, 20 ms to start and trimmed, and not for an expiry a missed
  scheduling caused;
  expired keeps logged; the trim RAGE's own, per stream, in the first
  deployment (choice B); a new default possibly set after a first
  observation period; the order when a query finds no room.
- **The review's — written into the design, NOT yet the operator's word:**
  the keep's three rules; the loop's thread owning the context; the
  refusal; no re-fit; the first version's scope; the premature expiry as
  the signal, and the trim's constants (doubling, the decay, the ceiling,
  "premature" by a creation's measured time, "ready, not sent"); the keeper
  counting an opted-in model
  at its running size; the gate with keep 0 and ≥ 300 creations; the
  canary; §2's acceptance.

## 10. Implementation — where it stands (2026-11-11)

*The operator, 2026-11-11: "Implement it". Written by the session that
implemented it, with no node: everything below ran on a CPU, in a cloud
container. §2's measurements were then made on the node by München's
session, on test copies (§10.1); nothing is deployed
and no engine changed.*

| part (§3, P11) | state | where | checked |
|---|---|---|---|
| 1. llama.cpp's server | written, in upstream's form | `deploy/localai/patches/staged/llama.cpp/0011-server-context-on-demand.patch` | CPU: its own 13 server tests; nine existing suites unchanged with the option off (localai-bugs.md "P11 (staged)") |
| 2. LocalAI's backend | written | `deploy/localai/patches/staged/0009-llama-cpp-context-on-demand.patch`, `staged/context-on-demand.series` | CPU: `grpc-server` built from the candidate series and driven over gRPC, 18 checks; the same vector byte for byte with and without the option |
| 4a. RAGE's keep and its trim | written | `app/rage/keep.py`, `app/embeddings.py` (both request paths) | `tests/test_rage_keep.py`, `tests/test_context_on_demand.py` |
| 4b. the option read from `/system`, else from the answering run's statement | written; 2026-11-12: the node's LocalAI (v4.12.1, upstream) lists NO `load_options` — P1 is not in it — so a row without them says nothing, and a validated run's `engine.options` is noted where a write or a search is checked; the gate's fixed keep is sent whatever `/system` says | `app/model_load.py` (`context_on_demand`, `listed_on_demand`, `note_run_options`), `app/run_check.py` (`_note_options`), `app/rage/keep.py` (`for_request`) | `tests/test_context_on_demand.py`, `tests/test_run_check.py`, `tests/hold/test_fresh_contexts.py` |
| 4c. the keeper's rule (H5) | written | `app/resident.py` (`_rest_size`) | the same |
| 4d. the gate's fresh-context pass | written; REVIEWED 2026-11-12 (the coordinator's integration): the creations are COUNTED (`counted`, the engine's count, one per request or the pass is deferred) and an engine with no count is refused; the option is read from the run's statement, else `/system` read now, a runner that cannot say defers | `app/hold/embedder.py` (`fresh_contexts`, 300 creations, short and long probes), `app/run_check.py` (`creates_per_request`, `fresh_contexts`, its keep fixed at 0); 03 §3.3 amended twice. RECORDED 2026-11-11 (München: "I can't confirm that the 300-fresh-context pass ran" — the statement carries no count, and `/vega/keeps` starts again at every load, the gate's own three included): every gate keeps `{contexts, closest, at}` per role and run in `state/gate-contexts.json` (`runs.record_contexts`, `contexts_measured`; local, no wire change), and `hold embedder check` prints the count, or "none asked". IN THE SIGNED STATEMENT since the same day (operator: B): 09 §9.12 version 6 carries `contexts` and `context_variation` for a run that creates its context per request, the count is part of the verdict (≥ 300), and a version-5 statement of such a run is gated again by its holder | `tests/hold/test_fresh_contexts.py` |
| the count for §2 from the card record | written | `tools/measure/context_on_request.py` (read-only; for the node to run) | `tests/test_context_on_request_tool.py` |
| §2's gaps, as RAGE sees them | written | `app/rage/keep.py` (`_gap`, `lines`): for EVERY embedding and translation stream, keyed or not — how idle its model was when its next request was ready, by powers of two; one line a stream in the worker's log (`[sched] keep (since start) — …`, with the scheduler's other lines) | `tests/test_rage_keep.py` |
| translation's keep | written | `app/translate.py` (`_t5_completion`: the translation stream's keep; its warm 0) | `tests/test_context_on_demand.py` |
| 3a. the engine SAYS, per call, whether its context was created for it | written (staged with P11) | llama.cpp half: `server_task_result::t_ctx_create_us`, `/embedding`'s `context_create_ms`; LocalAI half: `Embedding` answers with the trailing metadata `llama-context-created-us` — the creation's microseconds, -1 when the context was there, nothing without the option (`context_on_demand.h`); no change to backend.proto | CPU: llama.cpp's tests (15 then, 20 with the canary's); `grpc-server` over gRPC, 23 checks then (first written here as 25: miscounted), 30 with the canary's — twenty requests with a keep of 0 after one that held the context: the first says -1, nineteen say a creation, as many as the backend logged |
| 3b. the VeGa wrapper: its probes say a keep of 0; its count of calls against the keep | written — a wrapper change is an engine change (§4): it ships only with one | `deploy/vega/vega_wrapper.py` (`_probe_request`, `KeepCount`, `created_us`), `app/vega/service.py` (`kind: keeps`, `GET /vega/keeps`, beside `passes`; not a docs/09 format) | `tests/vega/test_wrapper_logic.py`, `test_service.py`, `test_wrapper_grpc.py` (real gRPC, the fake backend speaking the trailer) |
| 3c. the gate reads the count — what admits an on-demand engine at all | written | `app/run_check.py` (`creations_of`: `(calls, created)` of the run that answers, made current before the first round), `app/hold/embedder.py` (`_round_counts`: a round of N requests is N calls AND N creations, else deferred) | `tests/hold/test_fresh_contexts.py`; `tests/vega/test_wrapper_grpc.py` (`creations_of` through the real wrapper and receipts service) |
| 3d. the canary per context (§1.4; operator 2026-11-11: "Add the canary; it must be possible to disable it…") | written (staged with P11; the wrapper's part an engine change) | llama.cpp half: `common_init_result::canary_record` / `canary_check`, `server_context` (`context_acquire`), `--no-context-canary`, `/metrics` `context_canary`, `context_canary_failures_total`; LocalAI half: the model option `context_canary`, the trailing metadata `llama-context-canary: failed` beside the error; `deploy/vega/vega_wrapper.py` (`canary_refused`, `KeepCount.canary_failed`), `app/vega/service.py` (`canary_failed` in `kind: keeps`), `app/rage/keep.py` (the record per model), `app/worker.py` (a failure logged as an error) | CPU: llama.cpp's 20 tests; `grpc-server` over gRPC, 30 checks; `tests/vega` (the fake backend failing its canary over real gRPC), `tests/test_rage_keep.py`, `tests/test_context_on_demand.py` (the refusal is a deferral). NOT measured: its cost on the card |
| 3e. a canary failure sends the run back to the gate (operator 2026-11-11: "Wire the canary failure to the gate before deployment") | written | `app/embeddings.py` (`EndpointCanaryFailed`), `app/run_check.py` (`canary_failed`, `_suspect`, `_send_back`: no write under the kept statement, which is set aside, and the run gated from scratch), `app/hold/runs.py` (`record_canary`: the run's history, kept), `app/worker.py` (the beat, for a failure nobody here was refused) | `tests/test_run_check.py` (the request; a repeat across restarts; a gate that then fails; a gate that could not finish; the query side; the beat) |
| chat's keep (operator 2026-11-11: "Wire chat to release the context after use") | written | `app/llm.py` (`_request`: every chat request to LocalAI carries a keep of 0, the warm too), `app/rage/keep.py` (`one_shot_body`) | `tests/test_louise_level.py`; `grpc-server` over gRPC: a generation model without the option answers the same with the key, nothing freed |
| the reranker (operator 2026-11-11: "Reranker option A") | A: THE MODEL'S OWN KEEP. No code — a YAML line; ONE backend with P10 and P11, built and checked on the CPU; the card test asked of München (below, step 6), the live reranker untouched until it passes | `deploy/localai/patches/staged/nemotron-rerank-context-on-demand.series` | CPU: `grpc-server` from the combined series over gRPC — the 32 checks, and 8 on `Rerank` (below); the check found a defect of P11 in a re-created context, fixed (`init_stop`, below) |
| 4e. RAGE reads the engine's count back (the keep's dating) | written | `app/rage/keep.py` (`Keeps.observe`: the measured creation time; a per-model floor under every stream's keep when the engine created contexts RAGE's clock did not expect), `app/run_check.py` (`engine_counts`: the count of the run each model's answers last named), `app/worker.py` (`_read_engine_counts`: the beat, every 30 s, one bounded read of VeGa) | `tests/test_rage_keep.py` (the node's 8-in-13 pattern raised 20 → 40 → 80 and held; the floor is the model's and does not decay; expected creations, another process's, a new load), `tests/test_run_check.py` |
| §2's measurements, steps 3–4 | MADE 2026-11-12 (München, test copies, node paused) — §10.1 | the node | byte-equal over 300 keep-0 creations, each of three encoders |

**The integration review (2026-11-12, the coordinator; fixed before merge):**

- the fresh pass COUNTED requests, not creations: the engine keeps a context
  for the longest keep asked, so another collection on the same model, or
  the stream's own last keep, would have answered all 300 probes on one
  context, and the run been stated exact on creations nobody made. Now every
  round is held to the engine's count (one context a request, else
  deferred), and with no count readable the run is refused;
- the pass was chosen from a cached `/system` read, a miss reading as "no
  option" and the statement looking the same: now the run's own statement
  decides, else `/system` read at that moment, and a runner that cannot say
  defers the gate;
- (München, steps 3–4) the node's LocalAI lists no `load_options` at all:
  every model read "no option", so no request would ever carry a keep, and
  the gate's keep of 0 — sent only to a model `/system` said yes for — would
  never reach the engine. Now a row without options says nothing; the
  option comes from the validated statement of the run the model's answers
  named (VeGa states LoadModel's options), noted at the write's and the
  search's check; the gate's fixed keep is sent whatever `/system` says;
- the keeper counted an on-demand model never seen working at its weights
  (~2 GB short): it now counts as a model never seen (the set's median)
  until a reading shows it working (`model_load.least_vram`);
- one keep test passed vacuously (the refused request was the stream's
  first): it now refuses after an answered request, past the keep, and holds
  the excuse to be counted.
- Left for the author, in the C++ (staged, not built into anything live):
  P9's Embedding reads `get_meta()` per text where `impl->pooling_type`
  would do; P11's `context_acquire` is not gated on
  `params_base.context_on_demand` (unreachable today); the rerank check now
  runs before the control vectors; `context_release()` frees under
  `mutex_tasks`. And RAGE dates a keep from the answer's arrival where the
  server dates it from the slot's release: a return trip longer than
  `TRANSIT_MS` makes a creation RAGE judges clean (speed only; step 4's
  creations-against-requests count shows it).

**After the review (2026-11-11, the author's session):**

- **The four C++ items are fixed in the staged patches.** The rerank check is
  back AFTER the control vectors (`common_init_context_apply` now holds the
  load's steps in the load's order, and the load ignores a step that stops
  early exactly as before; a later creation that stops on one is refused);
  the idle callback runs WITHOUT `mutex_tasks` (the free no longer blocks a
  `post()`); `context_acquire` is gated on the option; `Embedding` reads the
  pooling type cached at the load — one `get_meta()` a load, none a text.
  With the option off the nine existing suites give the untouched
  baseline's results again (83 pass, 46 fail on the stand-in models, the
  same 46).
- **The count is the ENGINE'S WORD PER CALL, not a clock's guess.** The first
  wrapper count followed the keep on the wrapper's clock; the review's
  finding 1 is exactly why that cannot gate anything — a context may be kept
  for another caller's request. So the server marks the task a context was
  created for, its result carries the creation's time, and LocalAI's
  `Embedding` says it beside the answer as trailing metadata. *The standard
  alternatives, written down:* a field in `EmbeddingResult` (a backend.proto
  change every LocalAI backend would carry — to propose upstream with the
  patch); llama.cpp's `/metrics` (`context_creations_total`: not reachable
  through LocalAI's gRPC, and a total cannot say WHICH call).
- **What the wrapper counts** (per load, `GET /vega/keeps`): `calls`, `keyed`
  (carried a keep), `told` (the backend spoke for them), `created` and
  `created_ms` (the backend's own), `busy` (sent while it worked), `failed`,
  and `gaps` — how long the backend had been idle when a call was sent, by
  powers of two of a millisecond, the scale of RAGE's own (§2's "at the
  engine"). It tells VeGa the whole count BEFORE THE VECTOR GOES OUT for a
  call its context was created for and a call that asked for a keep of 0,
  in the background (a minute) otherwise: one more exchange on the receipt's
  socket beside a creation, none on a kept context.
- **The gate holds a round to BOTH numbers**, calls and creations (the
  coordinator agreed, 2026-11-11). Creations alone add up wrongly when
  another collection's request has a context created in the round while one
  of the pass's own rides a kept one. A reading is current only as of the
  last told answer, so before its first reading the gate sends its probe
  with a keep of 0 until one is created for it alone (at most 6 times, 0.5 s
  apart): the count is then current and the keep its own earlier passes
  left has run out. A VeGa without the route, or a backend that says nothing
  (the test backend built from 6ea8de4 is one), is a refusal, not a retry.
- **The canary — decided and built** (operator, 2026-11-11, on the table
  of the two choices that stood here: "Add the canary; it must be possible
  to disable it once we have the confidence that the architecture is
  sound").
  - *What it does.* At the load the engine computes the output of one fixed
    short input on its context — the pooled embedding of an embedding
    model, the logits of the last token otherwise; at most 64 tokens —
    TWICE, and refuses the load if the two differ (a model that does not
    reproduce itself cannot have a canary). Every context created later
    computes the same input after its warmup and BEFORE any request runs on
    it: the same values bit for bit, or the context is discarded and the
    request refused — `503`, "context canary failed: …", which LocalAI
    passes on as HTTP 500 and Louise reads as any busy endpoint's: a
    deferral, asked again, never an error on a document.
  - *Where.* In llama.cpp's creation path (`context_acquire`). The wrapper
    cannot do it: it learns that a context was created when the answer is
    back, and with a keep of 0 that context is already gone.
  - *How it is switched off.* The model option `context_canary:false` in the
    encoder's YAML (`--no-context-canary` for llama.cpp's own server). It is
    one of the load's options, so it is in the run's statement: a run says
    whether its contexts were checked, switching it off is a new run, and
    the gate validates that run once like any other. No dialog, no file of
    Louise's (RULE 2). And it is said EITHER WAY: the backend's default is
    on, and a default is in nobody's statement, so the VeGa wrapper states no
    run for a load that declares `context_on_demand` without
    `context_canary:true` or `context_canary:false` (`canary_unstated`; the
    coordinator's review). A YAML with the option therefore reads
    `context_on_demand`, `context_canary:true`.
  - *What the confidence is read from.* The engine's `/metrics`; the
    wrapper's count per run, `canary_failed` beside `created`
    (`GET /vega/keeps`, told at once, logged by VeGa each time it grows);
    and RAGE's record per model since the worker started, published with
    the scheduler's lines — `contexts (since start) — <model> at <server>:
    N created by the engine and used, 56 ms a creation; 0 discarded for not
    reproducing its canary` — a discarded context being logged as an ERROR
    with the model's name the moment it is read. "Sound" is that record:
    creations in the thousands, discarded none.
  - *What it costs.* One pass of at most 64 tokens per creation, inside the
    creation's time (`created_ms`). NOT measured on the card: München's
    figures (a creation ~55 / ~40 / ~23 ms) are without it; with the next
    build the same cells give it, and `context_canary:false` on a test copy
    gives the difference.
  - *What it proves.* That THIS context, right after its creation and its
    warmup, computes one fixed input to the same bits as the load's context
    did: the memory it was given, its buffers and its first real pass behave
    as the load's. It is the check invariant 12 had no place for — a vector
    set against a second computation at the moment of writing, not at the
    gate.
  - *What it does not prove.* That any OTHER input is right on that context
    (one input, at most 64 tokens: a fault that needs a long input, or a
    particular one, passes it); that the load's context was itself right
    (it was held only to itself, twice); anything about a context AFTER its
    canary (a kept context is not checked again). So it lowers the chance
    that a bad context writes and makes a bad engine loud; the gate's
    counted 300, on the sixteen fingerprints and the longest texts, remain
    what admits a run.
  - *A FAILURE SENDS THE RUN BACK TO THE GATE* (operator, 2026-11-11: "Wire
    the canary failure to the gate before deployment"). The discarded
    context wrote nothing; but the failure is the engine's own word that it
    does not reproduce itself on a new context, which is what a run fails
    for (invariant 12). So from the moment it is seen — by the request that
    was refused (`embeddings.EndpointCanaryFailed`), or for anybody else's
    request by the worker's beat from the engine's count — the run is
    SUSPECT: `check_write` defers every write under its kept statement, a
    cached verdict is not used, and the stage's next look sets the statement
    aside (`runs.set_aside`, as a failed re-check does; kept as evidence)
    and runs the WHOLE gate against the run, its 300 counted fresh contexts
    among the rest. It contributes again only if it passes; if it fails, the
    failure is its statement and the collection's embedding pauses with the
    reason. The query side of an asymmetric space is sent back by its own
    failures. THE RUN'S HISTORY IS KEPT (`state/gate-canary.json`, local,
    beside `gate-batching.json`; the coordinator's review): a pass does not
    erase it — the verdict reads "… validated for this space — gated again
    after N canary failure(s) of this run, the last <time>" — and a second
    failure of the same run is logged as A REPEAT with the earlier times.
    The web process gates nothing: it finds no statement once the worker has
    set it aside, and withholds the vector leg until the new one is kept.
  - *After `POST /lora-adapters`* the next context records the canary again
    (the adapters changed what a context gives); not our case, upstream's.

- **RAGE's dating of the keep** (the review's last item), CONFIRMED ON THE
  NODE AND FIXED. At a keep of 20 ms one lane of requests sent back to back
  still had its context created 8 times in 13 (bge-m3) and 67 times in 200
  (Qwen3-4B): the way from the engine's answer to the client and back to
  the engine outlasts 20 ms about one time in three here, and RAGE's own
  clock sees none of it — its gap is under its allowance (`TRANSIT_MS`), so
  it judged those intervals clean and would never have raised the keep. No
  statistic of its own round trips bounds that way (the fastest answer is by
  definition a fast trip). So RAGE now reads the ENGINE's count back
  (`Keeps.observe`, at the worker's beat, for the run `run_check` last
  heard as each model): the creation's measured time replaces
  `CREATION_MS` for that model (the premature window is ten of it), and
  between two readings the contexts the engine created are set against the
  requests RAGE's clock expected to create one — a stream's first, an
  expiry it judged, the gate's own. More created than expected (by 2, and
  by a tenth of the requests sent): the way there and back outlasts the
  keep of the streams that were sending, and no keep of that MODEL goes
  under twice their smallest again. The floor is the model's — the way is
  its path, not a job's rhythm — and it does not decay. What the engine
  created for another process's requests (a person's query, from the web
  process) is in its count and not in RAGE's; the thresholds are for that.
  *The alternative, written down:* the engine's word per REQUEST, by the
  tag's one-time id (§1.3 had it) — exact per stream, at the price of a
  second read of VeGa per request or a new key in `GET /vega/receipts`'s
  answer (a docs/09 format: to ask first). It would pay if two streams of
  one model need different floors.

### 10.1 Steps 3–4 on the node (München, 2026-11-12)

Test copies under their own model names, the live engine's build with P11,
the node paused for each run. **All three encoders gave the SAME BYTES over
300 keep-0 creations each**: a context created per request changes no vector.

| encoder (test copy) | idle → with its context | back per free | a creation: keep 0 less kept, medians at a pause of 100 ms (below) |
|---|---|---|---|
| bge-m3 | 883 → 1,444 MiB | 561 MiB | 73 − 33 = ~40 ms |
| Nemotron-3-embed-1B | 2,512 → 4,399 MiB | 1,887 MiB | 91 − 34 = ~55 ms |
| Qwen3-Embedding-4B (gutenberg's ctx 8192, batch = ubatch 2048) | 2,737 → 4,605 MiB | ~1,870 MiB | 76 − 53 = ~23 ms |

- Every keep-0 request rose and fell (Qwen3: 60/60, 240/240).
- The build: `louise-llama-cpp:sycl_f16-7e61f8307476-stack-7c301a7ba437`, the
  candidate series at 6ea8de4 (stack 2026-11c), installed as
  `louise-sycl-f16-llama-cpp-7e61f8307476-cod-test`; bare test models
  (`cod-test-bge-m3`, `cod-test-nemotron-embed`, `cod-test-qwen3-4b`), no
  wrapper in front. It is the patch BEFORE the review's fixes and the
  trailer (3a): the engine an on-demand run is gated on is a later build.
- **Keep 20 ms at ONE lane frees the context between back-to-back requests**:
  bge-m3 8 of 13, Qwen3 67 creations in 200 single-text requests. Singles
  100 ms apart: 30/30 creations, as they should. Four lanes (Nemotron) hold
  it. The cause is the dating gap the review named: RAGE dates the keep
  from the answer's arrival, the server from the slot's release. The
  context session addresses it with the wrapper's count.
- Keep 500 ms: one creation in 200 back-to-back requests; singles 100 ms
  apart, none.
- The node's LocalAI (v4.12.1, upstream) lists no `load_options` in
  `/system`. Louise now reads the option from the answering run's
  statement (#524).
- Between 20 and 500 ms (the operator's question; Nemotron test copy,
  10:39–10:52Z, node paused, byte-equal throughout;
  `/mnt/ssd3/louise-build/cod/measure/cod-test-nemotron-embed-keeps.json`):

  | keep | 200 texts, batch 16, 4 lanes: creations in 13 requests | singles 100 ms apart: creations in 30 | singles' median latency |
  |---|---|---|---|
  | 20 ms | 1 | 31 | — |
  | 50 ms | 1 | 30 | 92 ms |
  | 100 ms | 1 | 30 | 108 ms |
  | 150 ms | 1 | 1 | 37 ms |
  | 200 ms | 1 | 1 | 37 ms |
  | 300 ms | 1 | 1 | 36 ms |
  | 500 ms | 1 | 0 | — |

  At production batch with 4 lanes every keep from 20 ms holds the context
  (the one creation is the first). For requests 100 ms apart, the threshold
  is the gap plus the client's turnaround: 150 ms and up hold the context,
  100 ms and below recreate it every time.
- **A CREATION'S COST, MEASURED AS SUCH** (München, the same day; short
  texts of 512 tokens, the node paused, every vector byte-checked, 2.5 s
  between cells and the keep-2,000 baseline last). The median latency of a
  request whose context is created for it (keep 0, a pause of 100 ms
  between requests) less the median on a kept context (keep 2,000) is the
  table's last column: Nemotron ~55 ms, bge-m3 ~40 ms, Qwen3-4B ~23 ms.
  Nemotron's whole grid, 30 creations of 30 in every cell of the first two
  rows:

  | keep | pause between requests (ms) | median latency (ms) |
  |---|---|---|
  | 0 (the free right after the answer) | 0 / 25 / 50 / 100 / 200 / 300 / 500 | 63 / 86 / 80 / 91 / 95 / 86 / 81 |
  | 50 (the free by the timer) | 100 / 300 / 500 | 88 / 78 / 72 |
  | 2,000 (kept) | 100 | 34 |

  ONE CREATION PATH: neither how the context was freed, nor the age of the
  free, nor the pause changes what a creation costs. `CREATION_MS`'s 100
  was the right order; the engine's own figure, with no client's clock, is
  the wrapper's `created_ms` (3a, 3b).
- **The figures first written in that column were void** (corrected by
  München and the author's session): 0.8 / 3.4 / 8.4 ms came from pairs of
  requests — one with a keep of 2,000 ms, one with a keep of 0 ("warm"),
  0.3 s of sleep, one with a keep of 0 ("cold") — and BOTH halves were
  warm: a keep only extends another's (§1.2, rule 2), so the first
  request's 2,000 ms held the context through the "cold" one. What was
  built on them the same day is withdrawn (a pause in the gate's
  fresh-context pass, and its clause in 03 §3.3). The keep-50/100 rows
  above had the cost all along: 92 ms a request at a keep of 50 ms against
  36 ms kept, and 108 ms at 100 ms — there the deadline falls ON the next
  arrival, and the request also waits for the free in progress, ~16 ms
  (the loop's thread is the one that frees).

**Chat — one shot** (operator, 2026-11-11: "Wire chat to release the context
after use: we already know that is is a one shot operation"). Every request
of the chat client (`app/llm.py` `_request`: an answer, a streamed answer, a
round with tools, the warm) carries `metadata.keep_context_ms = "0"` to
LocalAI, whose chat route hands a request's `metadata` to the backend
(`core/http/middleware/request.go`, `core/backend/options.go`, at 16acc89).
A chat model loaded with `context_on_demand` then frees its context when
the answer is complete; one loaded without it ignores the key (checked on
the rebuilt `grpc-server`: the same completion with and without the key,
nothing freed, nothing created). Only to LocalAI (`model_load.
speaks_localai`): Ollama has its own `keep_alive`, and another OpenAI-style
service is sent nothing it did not ask for. No stream and no trim: there is
nothing to learn from one request a question. What it costs where requests
DO follow each other — the summariser's map and reduce, `ask chat`'s rounds
with tools — is a creation per request and the prompt evaluated again from
its first token (the cached prompt goes with the context): to measure on
the chat model before it is loaded on demand, and the reason `one_shot_body`
is a function of its own.

**The reranker — A, the model's own keep** (operator, 2026-11-11: "Reranker,
propose a solution"; then, on the table below: "Reranker option A"). The facts: LocalAI's `RerankRequest` carries no
metadata, in its HTTP form (`{model, query, documents, top_n}`) or its
gRPC one, so a rerank cannot say its keep; a question reranks each
collection's list on its own, in parallel (05 §5.6), so several requests
reach one reranker within the same moment — a keep must bridge THEM, and 0
would create a context between two of them; the Nemotron reranker runs on a
backend of its own (`staged/nemotron-rerank.series`, P10), which does not
carry P11; and it is on a person's path, where a creation (~23–55 ms
measured for the encoders) is latency.

| | pros | cons |
|---|---|---|
| **as it is: the reranker keeps its context** (what happens if nothing is decided) | nothing to build or gate; no creation ever on a search's path | its context's share of the card stays held around the clock |
| **A. the model's own keep — CHOSEN**: load the reranker with `context_on_demand`, `context_canary:true` and `keep_context_ms:500` in its YAML: every request that carries no keep gets the model's (P11 has the option) | no code, no LocalAI change; one line a model, in the run's stated options; 500 ms bridges one question's requests (it bridged every pattern measured on the node) and the context is back half a second after a search | the reranker's backend must be built from one series with P10 AND P11 (applied together, never built together) and the reranker seen to keep working on it; the first rerank after a quiet moment pays a creation on a person's path; one value per model, fixed — nobody trims it |
| **B. `metadata` on the rerank request**, in LocalAI (HTTP schema, `backend.proto`, the backends) | the standard route: a rerank says its keep like every other request, and RAGE could set it; right to propose upstream with P11 | a change to LocalAI's CORE and proto — the node runs upstream's core today, so it means running our own until upstream takes it; every backend sees the proto change; far more than the case needs |

What A needed, and what was done on the CPU (2026-11-11):

- **One backend with both patches**: `staged/nemotron-rerank-context-on-demand.series`
  — the live series, P10, P11. `git am` of the two staged patches on the
  series gives the tree that was built; `grpc-server` from it passes the 32
  gRPC checks of P11, and 8 on `Rerank` with the model loaded
  `context_on_demand`, `context_canary:true`, `keep_context_ms:500`: the
  canary of a rank-pooling model recorded at the load; the load's context
  gone once idle; a rerank creates the context and the next ones within the
  model's keep find it; the same scores as without the option; the context
  given back half a second after the last rerank; the next rerank creates it
  again, its canary reproduced; a context made to fail its canary refused
  and the next rerank served. (A stand-in model: the scores mean nothing,
  the path is what is checked — the real one is the card's to say.)
- **A DEFECT OF P11 FOUND BY THAT CHECK, fixed before anything was built on
  it.** The load returns a context on which one of its later steps stopped
  early — the control vectors, the check of a reranking model's tokens — and
  serves it, as upstream always has. P11's re-creation REFUSED such a
  context: a model whose load stops there was served by its load's context,
  then answered 503 to every request after its first free, for ever. The
  stand-in reranker is one (its vocabulary fails the reranking check: a
  warning at the load). Fixed: the load records where its steps stopped
  (`common_init_result::init_stop`) and a re-creation is accepted when it
  ends in the SAME place — the state of the load's context, or none. Its
  test (`test_rerank_identical_across_contexts`, llama.cpp's 21st) fails
  before and passes after; the nine existing suites are unchanged; the plain
  `context-on-demand.series` backend was rebuilt with it and passes its 32
  checks again. München's build `618e5a2b7e24` (from `5fd224d`) carries the
  defect and is not to be used: the engine is built from the commit that
  carries this.
- **Still the card's to say** (step 6 of "For München"): that the real
  reranker gives the same scores on the combined backend as on its own; what
  a creation and its canary cost it; what a search's rerank takes with and
  without. Until then the live reranker and its backend are untouched.

B stays for upstream: a rerank request that says its own keep is LocalAI's
to add (`metadata` on the rerank request), noted in LocalAI's package
([READING-GUIDE.md](../../deploy/localai/upstream/localai-context-on-demand/READING-GUIDE.md) §5).

**A model with no receipts cannot say it is on demand — DECIDED: P1 in the
node's core** (the coordinator, 2026-11-11, on this session's finding).
Louise learns a model is loaded on demand from `/system`'s `load_options`
(P1) or from the statement of the run that answers. The node's core is
upstream's and lists no `load_options`; chat, the bare-backend reranker
(`louise-rerank-nemotron-1b`) and MADLAD (`louise-translate-madlad`) answer
under no VeGa run. So, loaded on demand today, (a) the resident keeper
would size such a model at REST by `/system`'s figure while its next
request needs the context back — it could preload into that room; (b)
RAGE's translation stream would send MADLAD no keep, and its context would
stay. Both close once `/system` lists the options: `model_load` already
reads them, no Louise code changes. THE RULE THAT FOLLOWS: **none of the
three goes on demand in production before `/system` lists their
`load_options`**. The reranker's TEST copy is not in the keeper's set, so
step 6 goes ahead.

| | pros | cons |
|---|---|---|
| **as it is** | nothing to build | the three models stay loaded WITHOUT the option: their contexts are held |
| **the core with P1 — decided** | the model says it itself, a statement where one exists; the code that reads it is written and tested | P1 is not in the node's core yet: the node runs our core instead of upstream's until upstream takes it |
| **an observed fallback** (a `/system` figure that drops under the same backend pid) — refused | no core change | an inference where a statement exists; blind until one read lands during a request and a later one after it, and a 500 ms keep is rarely sampled; one more heuristic to maintain |

**What the code settled, beyond the brief:**

- **H4 was larger than seven reads.** `server_context::get_meta()` itself
  read the context (two token strings, the pooling type, the slot's size),
  and LocalAI calls it on its RPC threads per request. It no longer does;
  that is in the llama.cpp half, so upstream's own routes gain it too.
- **The first decode of every new context is the WARMUP's.** A creation
  goes through the same steps as a load (`common_init_context_apply`:
  adapters, then the warmup run where the model's options have it), so the
  class of M5 — a difference on the first pass after a load — falls on a
  decode nobody reads. It does not CHECK bytes: the canary — the engine's
  own, right after that warmup — and the keep-0 gate do (§1.4).
- **§2's instrument is the staged backend itself.** A test copy loaded with
  `context_on_demand` and asked with keep 0 pays one creation and one free
  per request, and says each in its log with its milliseconds ("context
  created in … ms", "context freed in … ms, it lived … s"); `/metrics` has
  `context_creations_total` and `context_creation_seconds_total`. No
  diagnostic program is needed beside it — `GGML_SYCL_MEMTRACE=1` and the
  driver's count as §2 says.
- **A refused creation arrives as HTTP 500 through LocalAI** (gRPC
  `INTERNAL`, the message "the context cannot be created now…"), which
  Louise already reads as a deferral (07 §7.8; `embeddings._endpoint_error`).
  A test holds that the words are not one of the over-long-input spellings,
  which would cut the batch and blame a document for a full card.
- **"Premature" is ten creations' time, not one** (§1.3 said "about one"):
  `PREMATURE_FACTOR = 10` × a creation (100 ms until measured). Holding an
  idle context costs memory nobody was waiting for; a creation costs card
  time on the embedding path, and at one creation's time a stream with
  steady 300 ms gaps would have paid a creation on every request for ever.
  The review's constant, like the ceiling (2 s) and the decay (a tenth a
  minute): all three are for the first observation period to correct.
- **The keep is NOT kept across a restart** (§1.3 said it would be).
  Invariant 10's letter is that a runtime value survives nothing, and
  app/sizing.py's windows already start again from their default; a stream
  finds its level again in a handful of creations. No state file, no change
  to 07 §7.8.
- **The gaps are counted BEFORE any model takes a keep.** Every embedding
  and translation stream's requests are counted as they go (`keep._gap`),
  whatever their model: how long it had been idle when the request was
  ready, in buckets of powers of two, the requests sent while it worked and
  those held back over 50 ms apart. The worker's beat publishes one line a
  stream. So the record §2 asks for — and the "first observation period"
  the operator wants a new default from — starts with the next deploy of
  the worker, with no engine change.
- **RAGE judges by its own clock, with an allowance** (`TRANSIT_MS`, 10 ms):
  it takes the context for expired when its own gap comes within that of the
  keep, so a borderline case costs a longer keep and never a hidden
  creation. The trim therefore needs nothing from the engine to work. What
  it cannot see is an expiry BETWEEN TWO TEXTS OF ONE REQUEST (LocalAI
  sends one text per call): that is the wrapper's count (3b: its `gaps`
  are per call, and `created` is the backend's word), and the reason the
  per-text gaps are in §2.
- **A model's keep is only sent where LocalAI lists the option.** A runner
  that does not know `metadata` is never sent it (`model_load.context_on_demand`).
- **Rerank carries no keep**: LocalAI's `RerankRequest` has no metadata map,
  so a rerank leaves its model's context loaded unless the model's YAML
  gives one (`keep_context_ms:N`). Embedding, completion and chat carry the
  request's.
- **What "no parameter, no free" asks of Louise** (the operator's rule,
  2026-11-11). Every request of hers to an on-demand model must SAY its
  keep, or the context stays: RAGE's embedding requests do; her WARM now
  carries 0 (`keep.warm_body`), so that a model preloaded and not yet asked
  — qwen3-4b works 31 s a day — does not hold its context from its load to
  its first request. Translation's segments (MADLAD on llama.cpp, in the
  first deployment) carry their stream's keep too, and its warm 0. CHAT
  carries a keep of 0 on every request to LocalAI ("Chat", below). Not
  keyed: rerank (it cannot be: no metadata map — "The reranker", below),
  which takes its keep from its YAML's `keep_context_ms:N` or keeps its
  context. The VeGa wrapper's own probes at a load carry a keep
  of 0 where the load declares the option (3b; they carried none before,
  and the load then held its context until Louise's first request).
- **The reranker's own backend is not touched, and the two patches live
  together.** Since 2026-11-11 the Nemotron reranker of five collections
  runs on a separate backend built from `staged/nemotron-rerank.series`
  (P10, `louise-sycl-f16-llama-cpp-9d17b8a39845`; the coordinator: "Any
  engine change must keep it working"). `context-on-demand.series` does not
  carry P10 and builds another backend. Checked: the live series + P10 + P11
  apply together — `git am` of the nine LocalAI patches, then the eight
  llama.cpp patches on `ddf48f8` with no fuzz on P10's or P11's. BUILT
  together on the CPU since the operator chose A for the reranker
  (`staged/nemotron-rerank-context-on-demand.series`, "The reranker" above);
  on the card that tree is a new engine, gated like any other.

**For München** (the operator, 2026-11-11: "Ask München to do the build for
you. It has desktop docker as well"). The BUILD is asked; nothing after it
touches a live model, and what runs on the node runs on the operator's word
in München's session (§8).
1. **The build** — asked. From this branch, with `build.sh` unchanged:
   `SERIES_FILE=deploy/localai/patches/staged/context-on-demand.series sh
   deploy/localai/llama-cpp/build.sh sycl_f16 <out>` with the live
   `INTEL_STACK` (§4), on BUILD_HOST or the desktop's Docker; and
   `… build.sh cpu <out>` where a CPU backend helps (the desktop: the same
   checks as this session's, with real models). The tag is the candidate
   series' digest, so it is no engine any Codex names.
2. **A test copy** — install the tar under its own backend name; a TEST copy
   of an encoder's YAML under its own model name with `context_on_demand`
   added to `options:` and `GGML_SYCL_MEMTRACE=1` to its `env`, LocalAI's log
   at debug for that run.
3. **A creation's cost** — with the node's harness, in a quiet window, at the
   encoder's live settings: N requests with
   `"metadata": {"keep_context_ms": "0"}` (each creates a context and frees
   it; WITHOUT the field a request leaves the context loaded and measures
   nothing). From the log, "context created in … ms" and "context freed in
   … ms" (first and steady); from MEMTRACE and fdinfo, the bytes before,
   during and after; every vector compared with the same text's first.
4. **A keep's effect** — the same with a keep of 20 and of 500, at
   production's batch and lanes: the creations counted against the requests
   sent (`context_creations_total` on the backend's `/metrics` where it is
   reachable, else the log's lines). Behind the VeGa wrapper (a later
   build, 3a–3b): `GET /vega/keeps` — the run's `calls`, `created`,
   `created_ms` and its `gaps` at the engine.
5. **The canary on the card** (3d; the build that carries it). An on-demand
   model's YAML now needs `context_canary:true` beside `context_on_demand`,
   or the wrapper states no run. (a) Its cost: the keep-0 cell of §10.1 with
   `context_canary:true` against the same cell on a copy with
   `context_canary:false` — the difference is the canary's pass. (b) Seen to
   refuse: a test copy with `LLAMA_SERVER_CTX_CANARY_FAIL: "3"` in its `env`
   — the next three contexts created fail their canary: three requests
   answered HTTP 500 "context canary failed", `canary_failed` 3 at
   `GET /vega/keeps`, VeGa's log line, then requests served and their
   vectors byte-equal. (c) The load: its log says "context canary:
   recorded".
6. **The reranker on demand** (operator, 2026-11-11: "Reranker option A").
   (a) The build: `SERIES_FILE=deploy/localai/patches/staged/nemotron-rerank-context-on-demand.series
   sh deploy/localai/llama-cpp/build.sh sycl_f16 <out>`, installed under its
   own backend name. (b) A TEST copy of `louise-rerank-nemotron-1b`'s YAML
   under its own model name on that backend, `options:` gaining
   `context_on_demand`, `context_canary:true`, `keep_context_ms:500`; the
   live reranker and its backend untouched. (c) The load's log: "context
   canary: recorded", and NO warning that a step stopped (a control vector,
   the reranking check) — if there is one, say which. (d) The same scores:
   `tools/measure/nemotron_rerank_parity.py`'s pairs against the copy and
   against the live model — equal, and equal again across at least 300
   creations (a pause longer than 500 ms between reranks; the log counts
   "context created in"). (e) The cost: "context created in … ms" with
   `context_canary:true` and on a second copy with `context_canary:false`;
   the memory back while idle (the §10.1 method). (f) A search's rerank:
   one question's reranks (several collections at once) against the copy —
   ONE creation for the question, none between its requests — and its time
   beside the live model's. Then the operator decides the switch.
