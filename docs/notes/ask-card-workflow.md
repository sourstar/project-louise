# The card for a person's question — the corrected workflow (decided 2026-11-09, to build)

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

## Measurement after the build (München)

One Italian Ask: chat/MADLAD/TTS cold loads mid-answer (target 0), time to first word and first sound, wikidata's
resume time; the same with the checkbox on.

## Replaces

#450's "release the corpus pins and let the LRU choose" (it chose MADLAD) and #451/#453's reading claim (step 6's
5-minute idle rule supersedes the 30 s linger). The 2026-10-23 rule "no eviction during processing" yields to the
operator's rule above for a person's question.
