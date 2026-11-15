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

## 5. Recalculated with Qwen (Alibaba Model Studio) and DeepSeek (2026-11-15)

Same token shapes as §3 (i). Prices read 2026-11-15 from the providers'
pages, USD per 1 M tokens, thinking off:
- **Qwen, International (Singapore):** Batch API −50 %, which cannot be
  combined with the context cache.
  - qwen3.7-flash: $0.03 in / $0.13 out up to 32 K input.
  - qwen3.8-flash: $0.15 / $0.47.
  - qwen3.7-plus: $0.4 / $1.6 (20 % promotion).
  - qwen3.8-max: $2 / $6.
- **DeepSeek:** there is no Batch API. Off-peak (outside 01–04 and
  06–10 UTC on weekdays) is half price.
  - deepseek-flash (V4.1-Flash), off-peak: $0.15 in on a cache miss,
    $0.003 on a hit, $0.6 out.
  - deepseek-v4-pro, off-peak: $0.66 / $0.022 / $1.98.
  - The prefix cache is automatic, so relations' 1.5 K instructions are
    nearly free.

| job | scope | qwen3.7-flash | qwen3.8-flash | qwen3.7-plus | qwen3.8-max | deepseek-flash | deepseek-v4-pro | Claude (§3) |
|---|---|---|---|---|---|---|---|---|
| 2 pick | all kept | **~$650** | ~$3.1 k | — | — | ~$6.4 k | ~$27 k | ~$2 k (Haiku) |
| 3 relations | C–F, N | ~$60 | ~$260 | **~$740** | ~$3.4 k | ~$340 | ~$1.3 k | ~$2–3 k (Sonnet) |
| 3 relations | all kept | ~$310 | ~$1.4 k | ~$4.0 k | — | ~$1.8 k | ~$6.9 k | ~$14–22 k |
| 4 summaries | ~100 k | ~$40 | ~$190 | ~$520 | **~$2.6 k** | ~$390 | ~$1.7 k | ~$5 k (Opus) |

The same roles as §2 (cheap picker, mid-size model for relations, top
model for summaries), initial scope:
- **Qwen:** 3.7-flash + 3.7-plus + 3.8-max ≈ **$3.9 k**, or ≈ $1.9 k with
  summaries on 3.7-plus. Wall clock 3–7 days (batch, 24 h windows).
- **DeepSeek:** flash + v4-pro + v4-pro ≈ **$9.4 k**, of which the pick is
  $6.4 k: with no Batch API, the 38 B pick tokens cost 3× Qwen's
  batch-priced flash. Wall clock ~1–3 days off-peak (2,500 concurrent
  requests on flash).
- **Everything on qwen3.7-flash:** ≈ **$750**. This is the floor; quality
  is unknown until the pilot.

Points to weigh beyond price:
- **Verification (§4.1).** It changes only where the served model IS a
  published open-weight checkpoint. Then A-K1's `model` method can pin
  real weights and a peer can re-run the sample locally.
  - DeepSeek's API models are V4 checkpoints: check whether the exact
    builds (`-1017`, `-0813`) are published.
  - Qwen's `-flash`, `-plus` and `-max` API models are closed. The open
    ones (qwen3.6-35b-a3b, qwen3.8-27b) are the local candidates of
    wiki-gutenberg-amendments.md.
  - Even when the weights are published, a hosted engine is not
    bit-exact. Agreement is checked at tolerance, as for any `model`
    method.
- **Jurisdiction:** DeepSeek is served from China, Qwen International from
  Singapore. Gutenberg text is public domain, but this would be the
  node's first outbound model call (§4.4).
- **Latin and Greek:** no published score for any of these models; the
  pilot's la/el slice decides.
- §4.2–4.3 apply unchanged: an invariant-5 exemption and a new client
  dependency (both are OpenAI-compatible, so no vendor SDK is needed).

## 6. History and memoirs only; local MoE for English, dense for the rest (2026-11-15)

**Scope:** LoCC C, D, E, F, plus memoirs (biography, autobiography,
memoirs, diaries, correspondence, personal narratives, CT). Fiction,
poetry and drama are out, except a memoir. Counted with
`tools/measure/kg_scope_count.py` on `pg_catalog.csv`, fetched 2026-11-15
from aleph.pglaf.org:

| | books | chunks (i) | tokens (i) |
|---|---|---|---|
| English: history 8,617, memoirs 3,199 | 11,816 | 1.85 M | 1.37 B |
| other languages: history 2,248, memoirs 530 | 2,778 | 0.43 M | 0.32 B |
| **total** | **14,594** | **2.28 M** | **1.69 B** (34 % of kept) |

- The books are counted. The memoir count (3,729) matches the 3,752 that
  München counted, which checks the rule.
- Chunks and tokens are scaled from book counts, with München's measured
  means: memoirs 168 chunks and 130 k tokens a book, other kept books
  152 and 111 k.
- Other languages: fr 986, de 408, it 318, nl 277, fi 239, es 197,
  pt 151, zh 56 … la 9, el 30.

**Local models: the two already benchmarked** on the B70, a quiet card,
2 requests in flight (classical-translation.md):
- MoE **Qwen3-30B-A3B**: decode 87 tok/s.
- Dense **Gemma 3 27B**: decode 22.9 tok/s.
- Prefill was not measured apart (i): 1,500 tok/s assumed for the MoE,
  400 for the dense model.
- Per item, MoE / dense: pick 5.5 / 20.8 s, relations 5.1 / 19.4 s,
  summary 21.7 / 81.8 s.
- Mentions (GLiNER) are the same in every row: ~1–2 days.

| option | pick | relations | summaries | **card-days / wall clock** | **cost** |
|---|---|---|---|---|---|
| **local: MoE for en, dense for the rest** | 118 + 104 d | 87 + 77 d | 7 + 6 d | **~400 d (~13 months)** | electricity only (i ~€0.6–0.9 k) |
| local: MoE for all languages | 118 + 27 d | 87 + 20 d | 7 + 2 d | ~260 d (~9 months) | electricity only |
| Qwen API: 3.7-flash, 3.7-plus, 3.8-max (batch) | $220 | $1,350 | $860 | 2–5 days | **~$2.4 k** |
| Qwen API: all 3.7-flash (batch) | $220 | $100 | $15 | 2–5 days | ~$335 |
| DeepSeek: flash, v4-pro, v4-pro (off-peak) | $2,150 | $2,340 | $570 | 1–3 days | ~$5.1 k |
| DeepSeek: all flash (off-peak) | $2,150 | $610 | $130 | 1–3 days | ~$2.9 k |
| Claude: Haiku 5.5, Sonnet 5.5, Opus 5.5 (batch) | $735 | $4.7 k | $1.8 k | 2–5 days | ~$7.2 k |

How to read it:
- The dense model takes 19 % of the chunks and **47 % of the card time**:
  187 days against the MoE's 212. That is the 4× decode gap measured in
  classical-translation.md.
- The pick dominates every row. It is one LLM request a chunk, with ten
  candidates for each mention (§3).
- Parallel slots were not benchmarked. More slots could cut the local
  rows 2–4× (i), and the pilot (§1) measures it.

Pros and cons:
- **Local:** verifiable by any peer (weights pinned, §4.1 holds), no data
  leaves the node, no new dependency. But it takes 9–13 months of the
  only card, beside gutenberg-nemo's 12–15 card-days and wikidata.
- **API:** done in days, at $0.3–7 k. But §4.1–4.4 apply, and every
  hosted row is a closed or unpinned model for a verifier.

## 7. The local option, part by part (2026-11-15)

Inputs:
- §6's per-item times. The MoE (Qwen3-30B-A3B) does English, the dense
  model (Gemma 3 27B) the other languages; mentions are GLiNER.
- **Card draw 200 W**, measured at the 230 W cap (node-runbook.md,
  2026-10-26). **Node 0.3 kW** with CPU and the rest of the system (i).
- **€0.30/kWh** (i): a parameter, not the operator's tariff.
- **14.5 processing hours a day.** The quiet periods (06–08, 12–13:30,
  16:30–22:30) suspend processing (operator 2026-11-02), so calendar days
  = card-days × 24 / 14.5 = **× 1.66**. §6's "~13 months" left this
  factor out: the right figure is ~22 months.

| part | model | requests | tokens in / out | card-days | calendar days | kWh | € | share |
|---|---|---|---|---|---|---|---|---|
| 1 mentions, en | GLiNER | 1.85 M chunks | 1.37 B / — | 1.2 | 2 | 9 | 3 | 0.3 % |
| 1 mentions, other | GLiNER | 0.43 M chunks | 0.32 B / — | 0.3 | 0.5 | 2 | 1 | 0.1 % |
| **2 pick, en** | MoE | 1.85 M | 10.5 B / 0.28 B | **118** | 195 | 847 | 254 | **29 %** |
| **2 pick, other** | dense | 0.43 M | 2.45 B / 0.06 B | **104** | 172 | 747 | 224 | **26 %** |
| **3 relations, en** | MoE | 1.48 M | 3.7 B / 0.44 B | **87** | 144 | 628 | 188 | **22 %** |
| **3 relations, other** | dense | 0.34 M | 0.86 B / 0.10 B | **77** | 128 | 556 | 167 | **19 %** |
| 4 summaries, en | MoE | 27 k | 0.66 B / 14 M | 7 | 11 | 50 | 15 | 1.7 % |
| 4 summaries, other | dense | 6 k | 0.15 B / 3 M | 6 | 10 | 44 | 13 | 1.5 % |
| **total** | | | 20 B / 0.9 B | **400** | **~660 (~22 months)** | **~2,900** | **~€860** | |

What the table says:
- **Picking and relations are 96 % of the cost.** Mentions and summaries
  together are under 4 %.
- **The dense model's 0.43 M chunks cost as much as the MoE's 1.85 M**
  (187 card-days against 212). Giving the other languages to the MoE
  saves ~140 card-days (§6), against the quality gap that the pilot's
  non-English slice measures.
- **Money is not the constraint:** ~€0.06 a book. The constraint is time
  on the only card, which also has gutenberg-nemo (12–15 card-days) and
  wikidata queued.
- **Levers, in order of what they save (all to be measured):**
  - the pick as a cross-encoder or as one request per mention, with a
    shorter prompt (the pick's 13 B prompt tokens are the prefill bill);
  - parallel slots, 2–4× (i; but "no scaling with parallel slots" is
    reported for the B70, approved-encoders.md);
  - processing in the quiet periods too, × 1.66 on the calendar (an
    operator decision: it reverses 2026-11-02's suspension).

## 8. The same at 24×7 (operator's assumption, 2026-11-15)

Same books as §6: history (LoCC C–F) and memoirs, 14,594 books, fiction,
poetry and drama out. Same models and inputs as §7, but processing never
stops, so calendar days = card-days. Energy and € are unchanged: the work
is the same.

| part | model | days | kWh | € | share |
|---|---|---|---|---|---|
| mentions (en + other) | GLiNER | 1.5 | 11 | 4 | 0.4 % |
| pick, en | MoE | 118 | 847 | 254 | 29 % |
| pick, other | dense | 104 | 747 | 224 | 26 % |
| relations, en | MoE | 87 | 628 | 188 | 22 % |
| relations, other | dense | 77 | 556 | 167 | 19 % |
| summaries (en + other) | MoE / dense | 13 | 94 | 28 | 3 % |
| **total** | | **~400 (~13 months)** | **~2,900** | **~€860** | |

With every language on the MoE: ~260 days (~8.5 months), ~1,900 kWh,
~€560. The card does nothing else meanwhile: gutenberg-nemo and wikidata
wait, or share the card and stretch both.
