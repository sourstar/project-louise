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

## 9. Roadmap (2026-11-18)

Scope: history and memoirs (§6). Local models, 24×7 (§8). Each step
ends at a gate, and a step starts only after the gate before it.
"Done" = in the repo with tests (CLAUDE.md).

| # | step | what | duration (i) | gate (output) |
|---|---|---|---|---|
| 0 | **Decide** (operator) | (a) local or API (§4.1); (b) MoE for every language, or split with the dense model (§6); (c) 24×7, which reverses 2026-11-02's quiet periods; (d) the first slice: la + el (39 books) or a small English one | 1 day | four answers |
| 1 | **Engine checks** (night) | Qwen3-30B-A3B and Gemma 3 27B on the deployed SYCL llama.cpp: same bytes at temperature 0 over 3 fresh loads (invariant 12); **prefill tok/s** and **parallel-slot scaling**, the two unmeasured inputs; GLiNER determinism under torch (invariant 12 "Open") | 2 nights | engine statements + a measured throughput table |
| 2 | **Benchmarks** (existing harnesses) | pick: `kg_link_pick_eval.py` on Mewsli-X, LLM pick against a cross-encoder pick; relations: `kg_relations_eval.py` on Re-DocRED; summaries: `summary_eval.py` (WikiLingua, BookSum); MoE against dense on the non-English rows | 3–4 nights | acc@1, F1, chrF, tok/s per model: settles 0(b) and the pick's form |
| 3 | **Pilot** (§1) | 1,000 stratified chunks of the scope, end to end; mentions per chunk, ≥ 2-mention share, community count; the `model-agreement` floor from 1,000 mentions re-run twice (decided 2026-10-20) | 2 nights | §6–8 recomputed from counts, not (i); go / no-go |
| 4 | **Build the methods** (`tools/`) | GLiNER mentions over a collection's chunks (parse pool, resumable per chunk, `supervise-pass.sh`); linking (search in wikidata's vectors + the chosen picker); relations; Leiden communities + summaries. Each method is written into the Codex `method` | 1–2 weeks | tests green; a dry run on 100 chunks |
| 4b | **Ask side** (amendments, order step 3) | `attach` places a mention on its chunk; relations on either end; a community as a fact; the reader highlights mentions | 1 week, beside 4 | browser check |
| 5 | **Prerequisite: wikidata embedded** | linking searches wikidata's Nemotron vectors | per STATUS.md | wikidata vectors served |
| 6 | **First instance** (amendments, order step 5) | the slice from 0(d): create the annotations Codex (version 9, two targets, Parquet); run all four jobs; `hold records build`, snapshot, `hold check` with `model-agreement`; read a hand sample | la + el: ~1–3 days | checks PASS + a hand-read sample |
| 7a | **Run: English mentions** | GLiNER over 1.85 M chunks | ~1.5 days | claims written |
| 7b | **Run: English pick** | MoE (or the picker step 2 chose) | ~118 days | |
| 7c | **Run: English relations** | MoE | ~87 days | |
| 7d | **Run: other languages, pick + relations** | dense ~181 days, or MoE ~47 (0(b)) | 47–181 days | |
| 7e | **Communities + summaries** | Leiden on the whole graph, then summaries | ~13 days | |
| 8 | **Publish** | records build, snapshot, checks, share; the claims follow gutenberg to gutenberg-nemo by re-pinning the target (amendments, "Which gutenberg") | 1–2 days | snapshot signed, peers adopt |

Notes:
- **Total** to step 8: ~1 month of preparation (steps 0–6), then
  **~270–400 days** of runs (7). Each run publishes as it completes:
  7a + 7b + 7c give a usable English entity graph without waiting for 7d.
- **The card is exclusive during 7.** gutenberg-nemo (12–15 card-days)
  and wikidata's embedding should finish before 7b, or the runs stretch.
- **Every run is resumable** (invariant 2), so a faster picker or more
  slots found during 7 is deployed at once and the run resumed
  (CLAUDE.md, "A fix that makes a running job faster").
- **Dependencies to ask first:** GLiNER under louise-python (torch);
  Leiden (`leidenalg` + `python-igraph`, or `graspologic`).

## 10. Italian memoirs first, then memoirs before history (operator, 2026-11-18)

"I want to start from historical memories in Italian: allows me to verify
the results and to extract knowledge that is usually not present in the
mainstream books. Actually memories can be the first and most valuable
group of books to be processed."

**The Italian slice, counted** (`pg_catalog.csv`, 1,108 Italian texts):
- **26 books by the memoir rule** (§6: subjects Biography, Memoirs,
  Correspondence, Personal narratives …, or LoCC CT). 16 of them are also
  history (LoCC C–F). Examples: *Memorie di un vecchio carbonaro
  ravegnano*, *I Mille*, *Dallo Stelvio al mare* (WWI personal
  narratives), *Dal cellulare a Finalborgo*.
- **The rule misses memoirs whose subjects do not say so.** *Ricordi di
  un garibaldino* (2 vols) and *Ricordi intorno alle Cinque Giornate di
  Milano* carry only "Italy -- History -- …". They are in the scope as
  history, but not in the memoir group. Their titles say what they are:
  52 Italian titles hold memorie, ricordi, diario, lettere, vita di,
  autobiografia or confessioni. Some of those are not memoirs (*Memorie
  di Giuda*, *Memorie del Presbiterio* is fiction).
- **Italian history + memoirs: 318 books.**

| first slice | books | chunks (i) | time, dense / MoE (i) |
|---|---|---|---|
| Italian memoirs, rule as written | 26 | ~4.4 k | ~2 days / ~0.6 day |
| Italian history + memoirs | 318 | ~49 k | ~21 days / ~6 days |

**The run order becomes:**
1. **Italian memoirs** (the first instance, step 6).
2. **Memoirs in every language**: 3,729 books, ~626 k chunks. English
   537 k on the MoE, about 60 days for pick and relations. Other
   languages 89 k: dense ~37 days, MoE ~10. Summaries ~3 days. **About
   70–100 days.**
3. **History**: the remaining 10,865 books, ~1.65 M chunks, ~200–300
   days.

Every step publishes when it is done, so a memoir graph exists after
about 3 months.

**Open: widen the memoir rule with title words?** See the question in
the reply of 2026-11-18. The rule is the records' own, written in the
Codex `method` before it applies (invariant 11).

### Decided (operator, 2026-11-18): the Italian title list

"Yes medieval first person chronicles, absolutely! Yes eyewitness account,
no matter if it's travel or otherwise. I confirm the no."
- 26 of the 54 candidates are YES: memoirs, biographies, first-person
  chronicles and eyewitness accounts, travel included. The other 28 are
  NO: fiction, and history or criticism that is not a memoir.
- The list: `wiki-gutenberg-memoir-titles-it.toml`, marked in
  `wiki-gutenberg-memoir-candidates-it.md`.
- **The Italian memoir slice is 52 books** (26 by subjects + 26 by
  title): ~8.7 k chunks (i), ~4 days on the dense model, ~1 on the MoE.
- **The meaning of "memoir" is widened by this decision:** first-person
  chronicles and eyewitness accounts count. The same title pass is owed
  for the other languages, each list marked by the operator, before step
  2 of the run order (memoirs in every language).

### Decided (operator, 2026-11-18): no books for children, nothing that is not a memoir

"No biographies for children, and keep out anything not being a memory
like 'The Physical Life of Woman': it is a waste of GPU time and even
worse it's tainting the scope."
- **Children's books leave the memoir group:** a "Juvenile …" subject or
  a children's bookshelf (`kg_scope_count.is_juvenile`). The memoir
  group goes from 3,729 to **3,598** books (en 3,074, other 524).
- **A title candidate is out unless its record shows a memoir**, as a
  travel account, an eyewitness account or source, or a life (a person
  and a life word). Only titles that state the author's own account are
  left to the operator.
- Owed: the same question for history books for children, which are
  still in the history scope (392 books).
