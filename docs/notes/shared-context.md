# One context for the models on one card — prior art and what fits us

*2026-11-12. The operator: "load all the models that are needed without
their own context, a common context will be filled to fulfill the
processing of the upcoming batched models" — and "before proceeding check
online if there is something similar or any publication".*

## 1. What the card holds

The resident set, read by München 2026-11-12 ~06:05Z (`size_vram` less the
weights file; the load lines are not kept): ≈ 34 GB resident on the 32.6 GB
B70, ≈ 10 GB of it CONTEXT (KV cache + compute buffers), almost all in the
encoders: vega-qwen3-embedding-4b 3,262 MiB, vega-nemotron-3-embed-1b 2,221,
the MADLAD test copy 2,007, then 0.2–0.6 GB each. The chat model's 16,384
window costs ~0.6 GB (Qwen3.5's hybrid attention).

## 2. How much of it works at once

The card record, 2026-11-10T06:20Z → 11-11T06:20Z (85,167 s; the corpus
gates and `q`; rerankers, MADLAD and chat are on the query path and appear
only as `q`):

| | share of seconds |
|---|---|
| nemotron (wikidata + normattiva-nemo) running | 55.5 % |
| medcpt-article (pubmed) running | 22.6 % |
| qwen3-4b (gutenberg) running | 31 s in the day |
| nemotron AND medcpt both running | **21.7 %** |
| three or more corpus models | 0.16 % |
| a person's query (`q`) with a corpus model running | 254 s of 27,474 (corpus work stands aside by design) |

Context held by the models running in the same second: peak 3,262 MiB,
p95 2,221 MiB — against ~10 GB held. Most of the context is idle at any
moment; gutenberg's 3.3 GB worked 31 seconds that day.

## 3. Prior art

- **Prism** (Yu, Qiao, Xu et al.; arXiv 2505.04021, OSDI '26; first titled
  "Unleashing GPU Sharing for Cost-Efficient Multi-LLM Serving", now "…via
  GPU Memory Ballooning"): exactly the idea. Each engine reserves a large
  VIRTUAL address space for its KV cache at start; PHYSICAL memory is mapped
  only on demand and reclaimed when its load drops, so models share the
  card's memory elastically instead of fixed partitions. Its observation is
  ours: the set of models active together shifts over time. Reported >2×
  cost savings, 3.3× SLO attainment (the authors' figures). Not read in
  full here: arxiv.org is not in this session's allowed hosts; the summary
  is from search results and the project pages.
- **kvcached** (github.com/ovg-project/kvcached, Berkeley Sky lab, Apache
  2.0): Prism's driver, open source, "deployed across 10K+ GPUs". vLLM
  (≥ 0.17) and SGLang (≥ 0.5.11), NVIDIA and AMD ROCm, PyTorch ≥ 2.10. The
  KV cache only, not the compute buffers. llama.cpp is on its roadmap,
  unchecked; Ollama in progress. Nothing for Intel / SYCL / Level Zero.
- **llama.cpp**: `--sleep-idle-seconds` frees the WHOLE model, weights and
  KV, when idle — what LocalAI's idle unload did and we switched off for its
  cold starts (#398). No KV-only release. ggml-sycl's pool for the
  temporaries of its ops is a virtual-memory pool (`ggml_sycl_pool_vmm`,
  sycl ext oneapi virtual_mem) where the build and the device support it
  and `GGML_SYCL_ENABLE_VMM` is not 0 — ON by default at our pin `ddf48f8`
  (`g_ggml_sycl_enable_vmm = ggml_sycl_get_env("GGML_SYCL_ENABLE_VMM", 1)`),
  else the legacy pool, which caches up to 256 freed buffers; either gives
  its memory back only when its owner is destroyed. PR #29265 (2026-09-22)
  proposed the VMM pool OFF by default because it keeps committed pages for
  its whole life; CLOSED UNMERGED 2026-09-24 (corrected 2026-11-12: an
  earlier version of this note said it was off). Discussion #25920 asks for
  dynamic model slots.
- **vLLM sleep mode**: level 1 discards the KV cache and moves the weights
  to CPU RAM; level 2 discards both. A whole-engine pause, CUDA only.

## 4. What fits us

The standard (Prism/kvcached) runs on engines and cards we do not use; a
port would be its KV allocator into llama.cpp's KV cache on Level Zero
virtual memory, for KV only — while on our encoders the compute buffers are
the larger part (Qwen3-4B: ~2.1 of 3.3 GB). So, by "do not reinvent the
wheel" (CLAUDE.md), the case is solved simply and the standard written down:

- **A. Release an idle context** (the coarse form of Prism's reclaim): a
  corpus encoder keeps its weights; after N seconds without work its
  llama_context (KV and compute buffers) is freed, and the next batch
  creates it again. The card's free memory is the common pool; two models
  that overlap (nemotron + medcpt, 21.7 %) each still get theirs, so nobody
  queues. Query-path models keep theirs (a person's first query never waits
  for one). A patch to our llama.cpp backend (P11: P1–P7 are the series, P8–P10 staged), and each
  changed encoder re-gated once (invariant 12; context creation is part of
  a fresh load, which the gate already covers).
- **A's trigger, settled in review** (operator, the same day): not "after N
  seconds" and no sleep — the context is allocated when a request arrives,
  freed when the work is done, and held between requests by a keep in
  milliseconds the request carries (RAGE sets it per job type, 50 ms to
  start — the operator's, 2026-11-11; 20 ms at first). And for
  any opted-in model, not corpus encoders only. idle-context-brief.md §1.
- **To measure before building:** the time to create and free a context on
  the B70 for each encoder, and that freed memory is the driver's again (a
  SYCL pool that keeps it would make A a no-op). A test copy in a quiet
  window; nothing in production touched.
- **A general design** (operator, 2026-11-12: "Embedders in CPU are an
  option but we cannot bet on it, this is a general design improvement that
  can be beneficial for a number of different scenarios other than Project
  Louise"): a sleep level for llama.cpp's server that keeps the weights and
  frees the context, for any model and backend, written to upstream's
  standards and proposed upstream on the operator's word; our node is its
  first deployment.
- **The dedicated session's brief**: [idle-context-brief.md](idle-context-brief.md)
  (the code paths, the series, the B70 pitfalls, the memory facts, the plan).
- **When the standard would pay:** if the engines move to vLLM/SGLang on a
  CUDA or ROCm card, kvcached is the off-the-shelf answer for the KV share;
  or if kvcached lands for llama.cpp (its roadmap), at least on the card we
  run.


## 2026-11-23: the reserve (operator, B)

The operator's definition: "Whats left are models and weights". The keeper's
budget counts each model at rest (its weights), and the working memory —
contexts made on demand, KV and compute buffers — is kept outside it:
`app/resident.py` `CONTEXT_RESERVE`, this note's §2 peak, 3,262 MiB, is kept
free under the reclaimer's line however quiet the margin's 6 h window was
(a margin measured above it counts instead). The reclaimer's line moves to
0.98 in LocalAI's settings (`memory_reclaimer_threshold`), which the keeper
reads; between the line and the card's full 100 %, 650 MiB stay a safety
net. On the B70 (32,656 MiB): the budget 0.98 × 32,656 − 3,262 = 28,741
MiB, against 31,020 before, when a quiet window had measured a margin of 0.

Open, measured before the reserve is sized again: the three on-demand
embedders (bge-m3, nemotron-3-embed-1b, qwen3-embedding-4b) read the same
size at rest and at their largest. Either the keeper's once-a-minute
samples never catch a context that lives for a request plus a 20 ms keep,
or the freed memory is not given back to the card, and the reserve then
counts it twice. vega-bge-m3's backend, sampled every 50 ms (xe fdinfo)
idle, through a burst and after, tells which.


## 2026-11-24: the two budgets (operator)

The operator, on what "models and weights" meant: "I meant separate 'models
and weights' from the context, because we use a 'shared' context and it has
to be considered in a separated budget". The card's free memory is the pool
every on-demand model's context is made from and given back to; it is
budgeted apart from the weights.

    line (0.98 × the card)
      = the weights budget               each model at rest; one loaded with
                                         context_on_demand at its WEIGHTS
      + the growth measured (6 h peak)   what models that keep their buffers
                                         grew by, and anything else on the card
      + the shared context budget        the two largest contexts of the set's
                                         on-demand models, never under 3,262 MiB
      + the eviction penalty

On the node (München's readings of 11-12 and 11-23):

| on-demand model | weights | with its context | context |
|---|---|---|---|
| qwen3-embedding-4b | 2,737 MiB | 5,685 | 2,948 |
| nemotron-3-embed-1b | 2,512 | 4,404 | 1,892 |
| bge-m3 | 621 | 1,335 | 714 |

- Before: the three counted whole, 11,424 MiB, of which 5,554 were held only
  while they worked; the reserve was 3,262 MiB and the growth was hidden
  under that over-count (the margin read 0).
- Now: they count 5,870 MiB of weights; the shared context budget is 2,948 +
  1,892 = 4,840 MiB (two at once: 21.7 % of the day against 0.16 % for three,
  §2); the growth is measured again, and a context live at a sample is taken
  out of it (LocalAI reads such a model at its weights, working or not), so
  it is not kept twice.
- A context never seen (LocalAI's size does not show it) is held by the
  floor. Sizing it needs a per-process reading of the card (xe fdinfo), as
  München took for bge-m3: open.
- The growth and the shared budget are ADDED, never the larger of the two
  (2026-11-23's reserve): growth held by the chat or the translator is not
  room a context can be made in.
- HOW THE WEIGHTS ARE KNOWN (2026-11-24, after the two budgets' first hour
  live): what the model's process holds AT REST, the latest time it was
  seen so, kept in the file for a restart (`resident._note_rested`,
  `_weights`). At rest is: the same backend process holding the same bytes
  for 10 s of `/system` readings (`model_load.steady_vram`), AND nobody on
  the node having asked the model for 10 s (`encoder_use`). The bytes alone
  do not say it: a stream that works without pause keeps its context from
  one request to the next, and the model holds weights and context
  steadily. Nor does `/system`'s cpu, which behind VeGa is the wrapper's
  (scheduling.md, Perugia). A context left held by a request without a keep
  reads as weights until the model next rests: the cautious side, and not
  for ever. Until then they were the
  fewest reading of all over 0.3 of the largest, kept for ever, and a
  reading taken while a model loaded stayed as its weights: nemotron counted
  2,229 MiB where it holds 2,513 at rest (München, on the empty card), its
  context 2,175 where it is 1,891. The two still summed to its largest, so
  nothing was over-committed; the split was wrong. The live engine's
  figures at rest: qwen3-embedding-4b 2,727, nemotron 2,513, bge-m3 897 MiB
  (621 on the engine before the cut-over).
