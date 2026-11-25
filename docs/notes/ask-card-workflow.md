# The card for a person's question — the corrected workflow (decided 2026-11-09, built, not deployed)

The operator's rule, verbatim: "Embedding models can be evicted to serve the chat pipeline: the ultimate goal of
project louise is to provide answers." Until the whole query/answer pipeline is complete (the results' translation
included), evicted embedders are not reloaded. Relayed by München, approved by the operator as "Corrected workflow".

## Why (measured, München, 2026-11-09, on 338ba39 = #450–#454)

An Italian Ask, mid-question: MADLAD 8.0, qwen3-4b 5.6, Nemotron 4.4, omnivoice 2.2, dar-legal-it 1.8, bge-m3 1.3,
rerankers 1.5, medcpt 0.8 GB resident (~25.6 GB); chat (7.3 GB) then loaded and the reclaimer evicted MADLAD, its
true LRU (last used 5 min before). #450 released the corpus pins as designed, but the query encoders and Nemotron
were more recently used than MADLAD. Chat ↔ MADLAD cold loads of 5.5–15.2 s per question; search 15–40 s.

## The spec

1. **At rest**: preload chat, MADLAD, TTS (omnivoice), the rerankers, in that order, within the memory the running
   embedders leave. VAD (silero) and STT (parakeet) stay on the CPU (0 MiB VRAM) and never enter the card's eviction.
2. **Ask**: corpus embedding stops at once, without waiting for its batch; the corpus encoders are UNLOADED actively
   (LocalAI unload, not LRU), idle ones first, then busy ones. The worker treats the dropped request as a DEFERRAL,
   never an error (CLAUDE.md), with a test.
3. **Search**: load the query encoders of the question's collections, and MADLAD only if the query is translated
   before the search (confirm in the code). If space is short, unload TTS (not needed until reading).
4. **After the search**: unload the query encoders; ensure chat, the rerankers, MADLAD (unless the checkbox below is
   on) and TTS are loaded.
5. **Answer, translation, reading**: nothing in that set is evicted while the question is open.
6. **Resume**: when every result is translated, the reading has ended, and 5 MINUTES pass with no user activity (a
   new Ask, the mic, audio playing): corpus embedding resumes and reloads its encoders; the query encoders are
   preloaded when the card is idle.
7. **Never evicted**: a model pinned by a gate or a supervised pass.
8. **The Quality / Speed switch** (operator, 2026-11-09, choice B, its form decided): on the Ask page, in the SAME
   form as "Search only" / "Search + AI summary" — two choices, remembered in the reader's browser.
   - **Quality** (the default): as now — MADLAD translates the query, the results and the entries.
   - **Speed**: the CHAT model translates instead of MADLAD; MADLAD is not loaded for that reader's question (8 GB
     freed on the card). The label says it trades quality for speed.
   A reader's choice (RULE 2: operators' choices live in the command line); the node keeps MADLAD as its default and
   still preloads it at rest.
   **ONE SWITCH (operator, 2026-11-12):** the Quick / Quality text-search switch beside it (2026-11-09,
   `language_plan.quick`) was "totally confusing" with this one. Speed now is both: the chat model translates AND
   the text search reads only the question's language and English. The request keeps `speed` and `quick` apart for
   the API and the measurements; the page sets both from this one switch (tests/test_ask_switch_browser.py).

## Built (2026-11-09; deploy waits for the operator)

| Spec | Where | Test |
|---|---|---|
| 1 at rest: chat, MADLAD, TTS, rerankers; STT/VAD on the CPU | app/resident.py `TIER_OF` (MADLAD moves from the last tier to 1,1) | tests/test_resident.py order tests |
| 2 corpus encoders unloaded, idle first, busy retried (4 × 2 s), gate/pass pins kept | app/question_card.py `free_card`, `on_question` (stand-aside hook) and `watch` (1 s, whether corpus work runs or not) | tests/test_question_card.py |
| 2 the cut batch is a deferral | the existing classification (embeddings `_endpoint_fault`): LocalAI's gRPC 500, a dropped connection | tests/test_endpoint_classification.py, worker path, documents untouched |
| 3 MADLAD before the search | confirmed: language_plan translates the query before it (Quality); the chat model does it under Speed | tests/test_relay.py |
| 3 short of space → TTS unloaded | `question_card.before_search`: only if the SEARCH'S SET — its query encoders, chat (it reads the query's language restriction) and, under Quality, MADLAD (it translates the query before the embedding) — cannot fit in the free space PLUS what the question does not use (amended twice after the measurements below) | tests/test_question_card.py |
| 4 the answer's models loaded behind; query encoders unloaded only for their room | `question_card.after_search`: chat, MADLAD (not under Speed), TTS, rerankers that are missing; a query encoder goes only if they do not fit — the smallest that gives the room alone, else the largest first (amended below); no probe reading → they go | tests/test_question_card.py, tests/test_relay.py |
| 5 nothing evicted mid-question | the keeper loads nothing while the claim is held (`resident.beat`); corpus stands aside and its encoders are off | tests/test_question_card.py |
| 6 resume after 5 min with no activity | dispatch `QUESTION_LINGER_S` 15 → 300 s for the search, the answer, the results' translation, every sentence read (the 30 s and the last sentence's 0 retired); `POST /ask/active` from voice.js on the microphone and on each clip played (one a minute at most) | tests/test_speech.py, tests/test_relay.py |
| 8 Quality / Speed | ask.html group, `translate.speed`, `AskQueryBody.speed`, `TranslateBody.speed` | tests/test_relay.py, tests/test_ask_switch_browser.py |

The Ask relay only: `/ask/query` and `/v1/chat/completions` keep their behaviour for other clients. Pins are not used
for spec 5 (operator 2026-11-08: preloaded, never pinned); nothing else loads while the question holds the card.

## Measured on a2aaf67 (München, 2026-11-09, one Italian Ask per mode, node running)

| | Quality | Speed |
|---|---|---|
| chat cold mid-answer | none | none |
| TTS | unloaded at the question's start; first sentence 14.1 s | unloaded at the start; reloaded (8.4 s) while the answer streamed; tap → first sound 2 ms |
| query encoders | 5 cold: 5.5 / 9.1 / 7.9 / 13.9 / 8.1 s; search 32.4 s | 4 cold: 5.4 / 7.0 / 11.6 / 7.4 s; search 24.0 s |
| answer | 2,308 chars in 9.9 s, first word 1.8 s | 8.1 s, first word 0.1 s |
| wikidata resume | ~7 min after the answer (Nemotron cold 5.2 s) | ~7 min |
| busy encoder after 4 tries | none | none |

Two defects of the build, both fixed by measuring the room (the spec's own condition, "if space is short"): spec 4
unloaded the query encoders after every search whether or not the answer's models needed the room, so every Ask paid
them cold (and the keeper preloads them only after the 5 minutes); spec 3 counted the card as it was at the
question's start, with the corpus encoders the worker was unloading still on it, so it unloaded TTS every time.

## Measured on c26cb21 (München, 2026-11-09, three Italian Asks, node running)

| | Quality 1 | Quality 2 (within 5 min) | Speed |
|---|---|---|---|
| TTS at question start | kept | kept | kept |
| query encoders cold | — | none (they stayed) | qwen3-4b 8.1 s (evicted in Q2) |
| MADLAD | cold 8.6 s INSIDE the search; card 98.6 % → chat evicted, reloaded 6.3 s | cold 11.4 s inside the search → qwen3-4b evicted | — (evicted by qwen3-4b) |
| search | 41.3 s | 44.4 s | 46.0 s |
| first word (from Ask / in the answer) | 49.8 s / 8.2 s | 44.9 s / 0.1 s | 46.7 s / 0.1 s |
| tap → first sound | 2 ms | 1 ms | 1 ms |

The card holds chat 7.3 + MADLAD 8.0 + TTS 2.2 + rerankers 1.5 + five query encoders 13.9 = 32.9 GB against a 31.0 GB
line: one of them is always out. Under Quality the search itself uses chat (the language reading) and MADLAD (the
query's translation) before the query encoders; counted out of the search's room, MADLAD loaded inside it and the
reclaimer evicted chat or a query encoder. Fixed: the search's set includes them, so TTS gives its room before the
search (it reloads while the answer streams: tap → sound 1–2 ms above), and after the search the smallest query
encoder that gives TTS its room goes. Each Ask search now writes one line of its steps (`[info:ask] search … ms:
languages, catalog, fts, dense, rerank`, the slowest collection of each leg), which was measured all along and never
written: the 41–46 s with at most one cold load is to be read from it.

## Measured on 4027f3c (München, 2026-11-09)

TTS kept and tap → sound 2–3 ms in all three Asks. The search's own line, verbatim:
`search 43,761 ms (quality): languages 2,866, catalog 725, fts 20,074 (wikipedia), dense 13,344, rerank 6,913`;
29,465 ms and 37,894 ms for the next two, wikipedia's fts 19.0–24.9 s each time. München's probe of wikipedia's ten
language groups (Q1's query): reload 0–1 ms warm (not the cost), the searches 13,204 ms one after the other per
variant (en 3,280, fr 2,254, it 2,004 … other 215), two variants an Ask. **Fixed**: the text leg runs its groups and
variants at once (app/retrieval.py `FTS_THREADS`; tantivy releases the GIL, measured 3.2× on 4 cores), the lists
fused in the serial order (tests/test_fts_parallel.py): expected ~3.3 s, the slowest group. Fewer segments (en 30,
fr 34) would cut the slowest group further: a merge, measured on its own.

MADLAD's 8,005 MiB on the card is not its weights (the F16 GGUF is 887 MB): `parallel: 7` slots of 4,096 context with
flash attention off. Fewer slots (or flash attention, if MADLAD's T5 path takes it) would free several GB — the room
the set lacks (32.9 GB against 31.0) — at a cost to how many results translate at once: measured before any change.

## Measured on 1d5ba93 + MADLAD `parallel: 4` (München, 2026-11-09 16:41Z, node running)

| | search | languages | fts (slowest) | dense | rerank | first word from Ask | in the answer |
|---|---|---|---|---|---|---|---|
| Quality 1 | 26.1 s | 12.3 s (MADLAD cold 8.5 s, evicted chat) | 5.6 s (wikipedia) | 1.9 s | 6.7 s | 42.4 s | 15.2 s (chat cold 13.4 s) |
| Quality 2 (≤ 5 min) | 12.1 s | 1.1 s | 3.6 s (wikipedia) | 2.0 s | 6.8 s | **14.3 s** (best yet) | 0.1 s |
| Speed | 37.6 s | 2.1 s | 9.4 s (wikidata) | 2.1 s | 6.7 s | 38.8 s | 0.1 s |

fts fixed by #461 (3.6–9.4 s against 19.0–24.9 s); tap → sound 1–2 ms throughout. Open, for Friday: (a) Quality's
language step loads MADLAD (~8.5 s) and that evicts chat; (b) Speed's ~15 s not covered by the step times (the legs
add to ~22 s of 37.6; vega-medcpt-article-ov was loading meanwhile); (c) rerank 6.7 s, steady, now the largest fixed
cost after fts.

## Ahead of the stage (2026-11-24; operator: "I want the preemptive model load to be implemented")

Until now each model was loaded when its stage reached it. The 11-09 rows above are that: MADLAD cold inside the
language step (8.5 s), the query encoders one after the other inside the search, chat cold in the answer (13.4 s);
a cold question's first word at 42.4 s against 14.3 s warm.

`question_card.load_ahead` (called by the ask route between `before_search` and the search):

- THE PLAN is every model the question meets, in the order it meets them: chat, the translation model (not under
  Speed), the query encoders, the rerankers, text to speech.
- Each missing one is loaded AHEAD, `AHEAD_AT_ONCE` (4) at a time. LocalAI loads different models at the same time:
  its loader holds no lock across a load (pkg/model/loader.go `loadModel`, v5.0.1).
- ROOM FIRST, ONCE, FROM WHAT THE QUESTION DOES NOT USE: before any load, the loaded models outside the plan give
  room, the oldest real use first (`model_evict.make_room`, the call `before_search` and `after_search` make), for
  the longest run of the plan from its start that they can give. Without it a card full of other questions' models
  (the serving node) gave a load ahead no room, and each stage's cold load unloaded them one by one.
- NEVER A MODEL OF THE QUESTION'S. Each load's cold-load hook is told the room is made (`model_evict.room_made`):
  by itself that hook unloads the oldest real use, the question's own models included. The evictions among the
  question's models stay where they were: `before_search`, `after_search`.
- ONLY INTO MEASURED ROOM: a load starts when the card has its size free under the reclaimer's line, the loads in
  flight (every question's) counted whole. One that does not fit waits for the room the worker is giving back,
  read every 0.5 s for 10 s and every 2 s after, and nothing later in the order goes before it.
- It stops starting loads when the search ends. `after_search` first waits for the loads on their way, so a model
  half loaded is not given room a second time.
- No card reading, or a model never seen loaded: nothing is loaded ahead, and the stages load as before.
- The mechanism is the stage's own warm request (`model_load.ensure_loaded`): a stage that reaches a model being
  loaded ahead waits on the same load.

| | pros | cons |
|---|---|---|
| as it was: each stage loads its own | nothing new; one load on the card at a time | every cold load is inside a stage, one after the other |
| ahead, built | the loads overlap each other and the stages before them; the idle models that would go one by one go at once, and none of the question's goes | up to four loads on the card at once, their cost to each other unmeasured; a model outside the plan is unloaded for the reranker and the voice at the question's start, not after its search; one card read and one `/system` read per beat while a model waits |

TO MEASURE (München, the protocol of models-beyond-the-card.md §5.3): the rows of the 11-09 table for one cold
Italian Ask per mode, before and after this, and each load's seconds alone against beside three others.

### Measured on 8337d36a, engine b190673dbdbf (München, 2026-11-25 ~01:37Z)

THE WORST CASE, by construction: the card EMPTIED (25 MiB, nothing loaded), the node paused, one Italian
question through `/ask/relay`, one run a mode. It is "the cold Ask now", not a pair with 11-09: that row was
another build, another engine, fewer collections and a running node.

| | first word from Ask | search | languages | catalog | fts (slowest) | dense | rerank | in the answer |
|---|---|---|---|---|---|---|---|---|
| Quality, cold, 11-25 | 48.1 s | 44.8 s | 13.7 s | 1.6 s | 12.3 s (wikidata) | 1.4 s | 15.6 s | 2.1 s |
| Quality 1, 11-09 (for the legs only) | 42.4 s | 26.1 s | 12.3 s | | 5.6 s | 1.9 s | 6.7 s | 15.2 s |
| Speed, 11-25, the SAME question again | 29.4 s | 27.3 s | 0.006 s | 0.007 s | 3.1 s (wikipedia) | 8.3 s (Normattiva-DAR) | 17.1 s | 1.5 s |

The loads (Quality, ten models): started in three groups as slots freed — the 9B chat, MADLAD, nemotron,
dar-legal-it (31,693 MiB free): 5.2 / 8.8 / 8.3 / 13.3 s; qwen3-4b, medcpt-query, bge-m3 (16,117 free): 4.8 / 5.4 /
8.6 s; the two rerankers and the voice (9,865 free): 7.9 / 7.9 / 12.1 s, the last ended 28–32 s after Ask. Alone,
on the unloaded node, a load is 4.3–6.6 s (models-beyond-the-card.md §5.2). The card after: 26,927 MiB.

WHAT IT SAYS.
- What was a load inside a stage and is no longer: DENSE 1.4 s with every query encoder cold at Ask (11-09's cold
  rows: 4–5 cold encoders, 24–32 s of the search); THE ANSWER's first word 2.1 s after the search (11-09: 15.2 s,
  chat cold 13.4 s). The rerankers were on the card 28 s after Ask; the rerank stage began at 29 s.
- What a load ahead cannot give: LANGUAGES 13.7 s is the first stage's own two models from an empty card, the chat
  (it reads the query) and MADLAD (it translates it). Nothing can be loaded ahead of the first stage; it is what
  keeping them, or parking them, is for.
- What is no load at all, and is now most of a cold Ask: RERANK 15.6 s — the lists that share a reranker go one
  after the other (`retrieval._rerank_lists`, 2026-11-10: the reranker answers one sequence at a time, bit-exact),
  2.45 s a list on this engine; and FTS 12.3 s on wikidata's index after a two-hour pause.
- Four at once: each load is slower beside others (5–13 s against 4.3–6.6 alone) and the four are still on the
  card in 13.3 s where one after the other would take about 25 s. `AHEAD_AT_ONCE` stays 4.
- The Speed row is NOT a cold Speed question: asked second with the same words, its language plan was the cached
  one (6 ms), so its stages reached the encoders 3 s after Ask, before a load of 5–13 s could end (dense 8.3 s).
  A first question reads its language with the chat model first, as Quality does.

STILL TO TAKE: a WARM row a mode on this build (a second, different question within the five minutes), so each
leg's cold cost is cold less warm. The cold Speed row with a question of its own is below.

### The cold Speed row: web 9a4cec4f, engine d531d5d804f1 (München, 2026-11-25 06:28:42Z)

The same worst case: the card emptied (25 MiB), the node paused, one question of its own through `/ask/relay`
("Che cosa prevede la direttiva europea sul diritto di recesso nei contratti a distanza?"), Speed.

| | first word from Ask | search | languages | catalog | fts (slowest) | dense | rerank | in the answer |
|---|---|---|---|---|---|---|---|---|
| Speed, cold, a question of its own | 58.7 s | 54.0 s | 15.7 s | 1.3 s | 12.2 s (wikidata) | 1.6 s | 23.3 s (6 of 8 lists) | 2.8 s |

The loads (nine models, no MADLAD in Speed), from Ask: the 9B chat 10.7 s, qwen3-4b 5.5, nemotron 8.6, dar-legal-it
13.0 (ended 9–16 s after Ask); medcpt-query 5.3, bge-m3 9.0, the nemotron reranker 8.1 (18–22 s); the medcpt
reranker 8.8 (26 s); the voice 13.6 (33 s). The card after: 20,027 MiB.

WHAT IT SAYS.
- The load ahead holds in Speed as in Quality: DENSE 1.6 s with every encoder cold at Ask, the answer's first word
  2.8 s after the search.
- LANGUAGES 15.7 s holds the chat model's own load (10.7 s): the first stage's, which nothing can be loaded ahead
  of, as in Quality.
- RERANK 23.3 s is no load: the rerankers were on the card 22–26 s after Ask and the legs before the stage add to
  30.9 s. Six lists in 23.3 s is 3.9 s a list, against 2.45 s (2,446 ms a list on this engine, the pass's step 5).
  NOT READ, and nothing is built on it: whether the excess is the last load ahead (the voice, loading until 33 s)
  competing with the reranker for the card, or these lists' own sizes. The per-list times were not kept. The row
  that separates the two is München's: the same question cold, and again once every load ahead has ended.
- NOT A PAIR with the Quality row above (48.1 s): another question, another engine build.

## Measurement after the build (München)

One Italian Ask: chat/MADLAD/TTS cold loads mid-answer (target 0), time to first word and first sound, wikidata's
resume time; the same with the checkbox on.

## Replaces

#450's "release the corpus pins and let the LRU choose" (it chose MADLAD) and #451/#453's reading claim (step 6's
5-minute idle rule supersedes the 30 s linger). The 2026-10-23 rule "no eviction during processing" yields to the
operator's rule above for a person's question.
