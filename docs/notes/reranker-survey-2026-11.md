# Rerankers: a survey for a universal default (2026-11-10)

*The operator, 2026-11-10: "As for the embedders, it would be beneficial to find universal re-ranker or almost
universal re-ranker … evaluate alternatives to BGE. What I'm looking for is both quality and speed." A desk survey
(public model cards, papers, leaderboards; read-only), then a benchmark on the node's own judged sets (below).*

## Already settled here (reranker-scope.md §12–14a, approved-encoders.md, model-profile.md, federated-merge.md)

bge-reranker-v2-m3 is the incumbent: bit-exact with FA on + `GGML_SYCL_FA_ONEDNN=0` (live 2026-10-31, 16–36 % faster
than strict), ctx 2,560, `parallel:1`, ~1.1 GB; 50×300 chars 1.7 s, 50×3k chars 5.5 s; its known flaw, an Italian
query scoring an English passage −1.68 logits lower. Qwen3-Reranker-0.6B (ggml-org Q8_0) measured on the Arc in
August: 1.86 s for 40×2,000 chars, relevant first for it→en; its cross-language gap never measured. LAMAR-600m tried,
not adopted (gap −2.0). jina-reranker v2/v3/v3.5: CC-BY-NC, out. MedCPT on OpenVINO: 72–99 ms per 50, bit-exact.

## Candidates

| model | size | langs (it?) | licence | quality (source) | speed | runs here |
|---|---|---|---|---|---|---|
| **bge-reranker-v2-m3** (live) | 568M | 100+, it | Apache-2.0 | MMTEB-R 58.36, MLDR 59.51 [1]; MIRACL 69.32, best in [2]; HAKARI 63.07 [3] | measured above | live (llama.cpp) |
| **Qwen3-Reranker-0.6B** | 0.6B (~1.45× bge per token) | 100+, it | Apache-2.0 | MMTEB-R **66.36**, MLDR **67.28** [1]; HAKARI **68.03**, 1st of 11 over 551 tasks / 43 languages [3]; MIRACL 57.70 [2] / 67.12 [4] | 1.86 s, 40×2k chars (here, Aug) | ggml-org Q8_0, `--pooling rank`; needs FA on + FA_ONEDNN=0 (P5) |
| Qwen3-Reranker-4B | 4.0B (~12× bge) | 100+, it | Apache-2.0 | MMTEB-R 72.74 [1]; MIRACL 76.56, RTEB 77.68 [4] | ~5.8 s / query (modelled) | community GGUF; ~4.3 GB Q8 |
| Qwen3-Reranker-8B | 8B | 100+ | Apache-2.0 | MMTEB-R 72.94, MLDR 70.19 [1] | ~8.7 s (modelled) | VRAM too tight |
| **gte-multilingual-reranker-base** | 306M (~⅓ bge compute) | 70+, it | Apache-2.0 | MMTEB-R 59.44, MLDR 66.33 [1]; HAKARI 62.97 ≈ bge [3] | not published | no mainline llama.cpp; TEI / OpenVINO |
| mxbai-rerank-base/large-v2 | 0.5B / 1.5B | 100+, it | Apache-2.0 | MIRACL 55.3 / 57.9 [2], 64.9 / 69.7 [4] | A100 0.67 / 0.89 s | no llama.cpp path |
| llama-nemotron-rerank-1b-v2 | 1.2B | 26, it | OpenMDW + Llama 3.2 | own pipeline only (MIRACL R@5 65.8) | — | no |
| zerank-2 | 4B | en only | Apache-2.0 | own card: legal 0.664, biomed 0.722 nDCG@10 | — | not multilingual |
| Querit-4B / hs-rerank-1 | 4B / 114M | no Italian | Apache / MIT | MTEB multilingual v2 rerank 71.09 (Querit) | — | no Italian |

Not shortlisted: bge-reranker-v2-gemma / v2.5-gemma2 / minicpm-layerwise (size or licence); English-only rerankers;
ColBERT-style late interaction (per-token vectors for ~10 M chunks; jina-colbert-v2 NC). No benchmark covers every
model; MIRACL flips with protocol; no Italian or Italian-legal reranker scores exist anywhere; no SYCL latencies are
published but ours.

## The ≤ 3B, llama.cpp-runnable comparison (operator, 2026-11-10)

Every published benchmark, one column group per source, with each model's llama.cpp status read from upstream
master: [reranker-comparison-2026-11.md](reranker-comparison-2026-11.md).

## Shortlist

| | pros | cons |
|---|---|---|
| 1. bge-reranker-v2-m3 (baseline, keep) | live and exact, small, best MIRACL in [2], best on short pairs [3] (wikidata) | MLDR 59.5 on long documents; the cross-language gap; 7–8 s cold load |
| 2. **Qwen3-Reranker-0.6B — the universal challenger** | top of HAKARI, +8 MMTEB-R and +7.8 MLDR over bge; trusted GGUF on the same runner; an instruction per collection (legal, biomedical, entity) at no cost | MIRACL conflicting; ~1.45× compute + ~60 prompt tokens a pair; SYCL Qwen3 pitfalls (P5, FA_ONEDNN); calibration to refit; the gap unmeasured |
| 3. gte-multilingual-reranker-base — the speed arm | ≈ bge quality at ~⅓ the compute, 8k context | an OpenVINO sidecar (like medcpt), custom code, no quality gain |
| 4. Qwen3-Reranker-4B — the quality ceiling | best open MIRACL and RTEB (legal-heavy) | ~12× compute, 4–8 GB: offline, or normattiva's short lists if measured worth it |
| 5. Dedicated: MedCPT for pubmed | 99 ms / 50 | one 2026 BioASQ study had bge 0.682 vs MedCPT 0.640: both on pubmed's set |

Also one arm independent of the model: bge-reranker-v2-m3 on OpenVINO (fp16/int8 IRs published 2026-11-01), the
medcpt sidecar's pattern (~120 k tokens/s) — a speed hypothesis, unmeasured.

## The benchmark (measuring.md §6: the deployed engine, the node paused, warm, a 3-item pilot first)

Each query's fused candidates captured ONCE (top 50, 2 chunks each, the 2,048-token window), then reranked by every
arm — the same lists for all. Arms: A0 fused (no reranker), A1 bge live, A2 Qwen3-0.6B (default and per-collection
instruction), A3 gte-multilingual (OpenVINO), A4 bge on OpenVINO, A5 Qwen3-4B (normattiva, pubmed only), A6 MedCPT
(pubmed). Sets: gutenberg 496 queries (`rerank_lang_variants.py`), wikidata known-item 400 (`wikidata_encoder_ab.py`),
wikipedia known-item 400, 243 logged queries UMBRELA-judged (`fusion_calibration.py --rerank-like`), JuriFindIT 179
+ temporal (`legal_pilot.py`), BSARD / LegalQuAD / GerDaLIR, TREC PM 2017/18 (pubmed); depth sweep 10/40/50/100
(`pipeline_eval.py`). Metrics: MRR@10 (primary), nDCG@10, R@1, R@10, other-language share of the top 10 and the
it/en gap; latency per list p50/p95 warm, cold load, VRAM; NaN checks and the same order over 3 fresh loads × 2
orders. Rule: paired randomization p < 0.05, no loss on titles or descriptive, latency per list no worse than bge's;
`[reranker] calibration` refit before any switch.

**The harness: `tools/measure/rerank_bench.py`** (its docstring is the reference). The older tools search once per
arm, so their arms saw different lists. Steps, in the node's image:
- `capture FORMAT SOURCE... --out SET.jsonl [--collection C] [--limit 3]`, once per set. This is the Ask tab's
  search (`run_search`, translation on), stopped at `retrieval._reranked`. Kept per query: the top 50 records in fused
  order and their leg ranks; each record's 2 matched chunks under its title line, as `_rerank_held` builds them; and
  the query each record is sent against (the plan's translation into its language, else the core query). Texts are
  kept uncut. Formats: `gutenberg` (run 5's DATA, or the TSVs with `--works`), `known-item` (wikidata_encoder_ab's
  queries.jsonl), `umbrela` (fusion_calibration's judged.jsonl), `jurifindit` (legal_pilot's fetched directory).
- `rerank SET.jsonl --arm A2 --model <model> [--instruction collection]`, once per arm. Each request is the live one:
  one per variant, each text cut by `fit_to_window` to the window in the arm's own tokens, and timed alone. The model
  is warmed and pinned; a cold query is not timed. One request at a time unless `--concurrency`.
- `score SET.jsonl... --arms A0,A1,A2,A2i --out res` gives the table and JSON. `show SET.jsonl --arms …` is the
  pilot's reading.

Every step resumes per query and refuses other settings in the same file. `--limit N` takes the first N queries of
every kind.

How scores are counted:
- At record level, on each record's best chunk.
- The order as shipped: CATALOG_FIRST applies, and `--raw` leaves it out.
- Editions are not grouped by work, so the numbers are not run 5's.

Not covered: the temporal questions, BSARD / LegalQuAD / GerDaLIR, TREC PM (no format yet), and the cold load, VRAM
and fresh-load checks.

Qwen3-Reranker's instruction goes in the query: `/v1/rerank` has no field for it, and the GGUF's `rerank` template
already carries the default instruction. The form is the card's, `\n<Instruct>: X\n<Query>: Q`
(`--instruction-form` to change it). The pilot must show the instructed arm's scores separating.

## Sources

[1] https://huggingface.co/Qwen/Qwen3-Reranker-0.6B · [2] https://huggingface.co/jinaai/jina-reranker-v3 ,
https://arxiv.org/abs/2509.25085 · [3] HAKARI-Bench https://arxiv.org/abs/2606.22778 ,
https://huggingface.co/spaces/hakari-bench/leaderboard · [4] https://huggingface.co/jinaai/jina-reranker-v3.5 ,
https://arxiv.org/abs/2607.18152 · https://huggingface.co/gpustack/gte-multilingual-reranker-base-GGUF ·
https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base · https://huggingface.co/mixedbread-ai/mxbai-rerank-large-v2 ·
https://huggingface.co/nvidia/llama-nemotron-rerank-1b-v2 · https://huggingface.co/zeroentropy/zerank-2-reranker ·
https://huggingface.co/Querit/Querit-4B · https://huggingface.co/hindsight-ai/hs-rerank-1 ·
https://arxiv.org/pdf/2609.04999 · https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF ·
https://huggingface.co/OpenVINO/bge-reranker-v2-m3-fp16-ov · https://huggingface.co/OpenVINO/Qwen3-Reranker-0.6B-seq-cls-fp16-ov ·
https://docs.openvino.ai/2026/model-server/ovms_demos_rerank.html
