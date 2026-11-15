# wiki-gutenberg — bottlenecks with a local model first, then which Claude model (PLAN, 2026-11-15)

*Not built, not measured. Every number below marked (i) is INFERRED from
the notes cited, not counted (measuring.md §6); the pilot in §1 exists to
replace them. Prices: Anthropic list prices cached 2026-11-03, Batch API
at −50 %.*

## 0. The volume (counted, wiki-gutenberg-amendments.md "Which books")

Kept: 44,479 books, **6,769,933 chunks, 4.95 B tokens** (~730 tokens a
chunk). Relations/communities start on LoCC C, D, E, F, N (~15 %,
wikidata-plugins.md "Scope").

The unknown that drives every cost: **linkable mentions per chunk**.
Assumed (i) 15 (range 8–30) → ~100 M mentions.

## 1. The pilot on the local models (step 1, ~2 nights)

A stratified sample of **1,000 kept chunks** (language incl. la/el, LoCC,
length), run end to end through the four jobs with the DEPLOYED engines
and flags (measuring.md), in the night window (operator 2026-11-03).

| job | local model (as planned) | measure per chunk / per request |
|---|---|---|
| 1 mentions | GLiNER 2.5 | mentions/chunk, linkable share, chunks with ≥ 2 mentions, chunks/s |
| 2 pick | Nemotron-3.5-Rerank-8B vs Lightning-30B-A3B | prompt + output tokens, prefill and decode tok/s at the deployed concurrency, acc@1 (`kg_link_pick_eval.py`, Mewsli-X) |
| 3 relations | Qwen3.8-27B vs Qwen3.6-35B-A3B | tokens in/out, tok/s, malformed-JSON rate, F1 (`kg_relations_eval.py`, Re-DocRED) |
| 4 summaries | the job-3 winner | community count from Leiden on the pilot graph, extrapolated; s/summary; blind read of 30 (`summary_eval.py`) |

Plus the agreement floor for `model` methods (decided 2026-10-20): the
pilot's mentions re-run twice. Output: per-job card-days for the full
corpus = the bottleneck table.

### What the pilot is expected to show (i)

Single B70, from the measured 87 output tok/s of a 3 B-active MoE
(classical-translation.md) and ~2.6 k tok/s prefill-class rates
(approved-encoders.md):

| job | full kept corpus, local | why |
|---|---|---|
| 1 mentions | 2–5 days | encoder-class model, 4.95 B tokens |
| 2 pick, LLM | **months** (~38 B prompt tokens: chunk + 10 candidates × each mention) | prefill-bound |
| 2 pick, cross-encoder | **months** (~1 B pairs) | one forward pass per candidate |
| 3 relations | **1–2 months** with parallel slots (~1.6 B output tokens) | decode-bound |
| 4 summaries | 1–2 weeks (i, community count unknown) | 16–32 k contexts |

So the expected bottlenecks are **2 (pick)** and **3 (relations)**; the
earlier "2–3 weeks total" in wikidata-plugins.md assumed 15 % of the
corpus and no measured pick cost.

## 2. Which Claude model, per job

| job | proposal | pros | cons |
|---|---|---|---|
| 1 mentions | **stay local (GLiNER)** | not the bottleneck; offsets exact; reproducible | — |
| 2 pick | **Haiku 5.5** | a 1-of-10 choice with short output; cheapest; removes the biggest bottleneck | weakest on Latin/Greek context; must beat Rerank-8B's acc@1 on Mewsli-X |
| 3 relations | **Sonnet 5.5** (Haiku 5.5 if within ~2 F1 points) | multilingual, structured outputs (no malformed JSON), thinking off via `between_tools` | ~20× Haiku's price |
| 4 summaries | **Opus 5.5** (Sonnet 5.5 if the blind read ties) | read by users: quality and low invention matter most; 1 M context | priciest per token; volume small |
| status quo: all local | — | no cost, reproducible, verifiable by any peer | months of card time, beside wikidata and gutenberg-nemo's 12–15 card-days |

Fable 5.2 is not proposed: nothing here needs it at 2× Opus's price.

## 3. Coarse cost and time (Batch API)

Per-request shapes (i): pick ~5.7 k in / 150 out per chunk (all its
mentions in one request); relations ~2.5 k in (1.5 k instructions,
cacheable) / 300 out; summaries ~24 k in / 500 out, ~100 k communities.

| job | scope | model | cost | wall clock |
|---|---|---|---|---|
| 2 pick | all kept, 6.8 M requests | Haiku 5.5 | **~$2 k** (Sonnet 5.5: ~$44 k) | 3–7 days |
| 3 relations | LoCC C–F, N (~1 M) | Sonnet 5.5 | **~$2–3 k** (Haiku: ~$170) | 1–2 days |
| 3 relations | all kept (~5.4 M) | Sonnet 5.5 | **~$14–22 k** (Haiku: ~$1.1 k) | 3–6 days |
| 4 summaries | ~100 k communities | Opus 5.5 | **~$5 k** (Sonnet: ~$2.7 k) | 1–2 days |
| pilot A/B | 1,000 chunks, all jobs | the above | **< $30** | hours |

Wall clock is bounded by the account's batch queue and rate limits
(68 batches of 100 k requests for the pick), not by compute. Every cost
scales linearly with the mention count: at 30 mentions/chunk the pick
doubles.

**Coarse total, initial scope:** Haiku pick + Sonnet relations (C–F, N) +
Opus summaries ≈ **$9–10 k, ~2 weeks** including the pilot, against
**~2–4 months** of card time locally (i).

## 4. What blocks using Claude at all (operator decisions, before any build)

1. **Verification (invariant 11, A-K1's `model` method).** The method pins
   `model: <sha256 of the weights>` and `temperature: "0"` so a peer can
   re-run a sample. A Claude model has no weights digest, no temperature
   (400 since 2026-10-21), is not bit-reproducible, and is retired one
   day — after which its claims cannot be re-run by anyone.
   Choices: (a) a new method kind `api-model` (provider, model id, prompt
   hash), agreement measured while the model is served — a hashed-bytes
   change; (b) Claude only as a JUDGE/teacher: labels a gold set to choose
   the local models and set the agreement floor, claims stay local;
   (c) Claude produces, a local model re-checks a sample at tolerance.
2. **Invariant 5:** the API client joins the model-server exemption list,
   with its reason.
3. **New dependency** (`anthropic` SDK): ask first.
4. Sending the text out: Gutenberg is public domain; the license gate
   (invariant 9) is unaffected, but it is the first outbound model call.

Recommendation: run the pilot (§1) now — it is needed whatever is
decided — and decide 4.1 on its numbers.
