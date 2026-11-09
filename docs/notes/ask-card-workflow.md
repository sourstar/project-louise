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

## Measurement after the build (München)

One Italian Ask: chat/MADLAD/TTS cold loads mid-answer (target 0), time to first word and first sound, wikidata's
resume time; the same with the checkbox on.

## Replaces

#450's "release the corpus pins and let the LRU choose" (it chose MADLAD) and #451/#453's reading claim (step 6's
5-minute idle rule supersedes the 30 s linger). The 2026-10-23 rule "no eviction during processing" yields to the
operator's rule above for a person's question.
