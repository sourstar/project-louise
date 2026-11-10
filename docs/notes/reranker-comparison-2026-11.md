# Text rerankers ≤ 3B that llama.cpp can run: comparison (2026-11-10)

Desk research only: no model was run. Configs and cards were read from the Hugging Face Hub. The llama.cpp
status was read from upstream master `7592b61` (2026-11-10): `conversion/__init__.py`, `conversion/{bert,qwen,qwen3vl,llama}.py`,
`src/llama-graph.cpp`, `src/models/*.cpp` and `gguf-py/gguf/constants.py`. arxiv.org and the HAKARI leaderboard app could
not be reached from this session. The HF markdown copies of the papers have lost their tables, so paper numbers below
come from the papers' text or from the model cards.

## Status labels (llama.cpp master 7592b61)

- **NATIVE**: the converter registers the HF architecture and `build_pooling(RANK)` produces the model's score.
- **PATCH-SMALL**: the architecture's trunk is already supported. The patch is one converter class (head extraction,
  `pooling_type=RANK`, prompt template) plus at most one `cls_out` `create_tensor` line in the model loader. No new graph code.
- **PATCH-MEDIUM**: as above, plus a small change inside an existing graph.
- **PATCH-LARGE**: new graph or pooling code, or the score is computed outside llama.cpp (projector, listwise glue).
- **NO**: llama.cpp cannot run it, even with a reasonable patch.

What the code shows:
- `build_pooling` RANK (`src/llama-graph.cpp:4362`) selects one row: CLS for encoders, the last token when the model is
  causal (`:467`), or the mean when `pooling_type_cls == MEAN`. It then applies `cls` → act → `cls_norm` → `cls_out`.
  It applies softmax only for `LLM_ARCH_QWEN3`/`QWEN3VL`.
- `cls_out` is loaded only by `bert`, `modern-bert`, `neo-bert`, `lfm2`, `qwen3`, `qwen35` and `qwen3vl`. The `llama`/`llama-embed`, `qwen2`,
  `gemma` and `minicpm` loaders do not load it, hence the "loader line" in PATCH-SMALL.
- The Qwen3 converter (`conversion/qwen.py:178-262`) detects a reranker from the README title, the repo name or a
  `*SequenceClassification` arch. It then writes the `yes`/`no` rows of the LM head as `cls.output` (2 labels → softmax).
  Repos with other names (zerank, ctxl) are converted as plain LMs.
- A single-logit head on Qwen3 can be written as `[W_tok; 0]`. The graph's softmax then returns sigmoid(logit), which keeps
  the order, so no graph change is needed.

## Protocol keys (each group is one source; do not compare across groups)

| key | source | candidates (first stage, depth) | metric |
|---|---|---|---|
| **Q** | Qwen3-Reranker-0.6B card | top-100 from Qwen3-Embedding-0.6B; retrieval subsets of MTEB(eng,v2) "MTEB-R", MMTEB "MMTEB-R", MLDR | MTEB main score |
| **J3** | jina-reranker-v3 card (= arXiv 2509.25085 Table 2) | top-100 from jina-embeddings-v3 | nDCG@10; MKQA Recall@10 |
| **J35** | jina-reranker-v3.5 card (= arXiv 2607.18152 Table 1) | top-100 from jina-embeddings-v5-text-small, "unified MTEB v2 pipeline" | nDCG@10; RTEB excludes MIRACL |
| **H** | HAKARI-Bench paper text, snapshot 2026-06-09 | fixed hybrid set: BM25 + dense via RRF, top-100, plus one positive appended if missing | nDCG@10 ×100, per-benchmark macro; *overall* = 551 tasks / 43 langs; *short-ML* = non-English, query < 70 chars and doc < 1000 chars |
| **HL** | Horizon-Labs cards + leaderboard `results.json` (snapshot 2026-10-02) | MTEB reranking candidate lists: MIRACL 18 langs × 60 q × 100 cand.; WikipediaRerankingMultilingual 16 langs × 60 q × **9** cand. | nDCG@10 |
| **IT** | albertobarnabo/bge-reranker-v2-m3-italian card | mMARCO-it (translated MS MARCO), 2,000 queries, top-100 from bge-m3-italian | nDCG@10 / MRR@10 |

## Main table

✓ = commercial use allowed · ⚠ = allowed with a caveat · ✗ = non-commercial. "—" = no source gives it.

| model | params | llama.cpp status (and GGUF) | licence | max ctx | Italian? | Q MTEB-R | Q MMTEB-R | Q MLDR | J3 BEIR | J3 MIRACL | J3 MKQA | J35 BEIR | J35 MIRACL | J35 RTEB | H overall | H short-ML | HL MIRACL | HL Wiki | HL Wiki-it | IT nDCG / MRR | own card (own protocol) | latency (published) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **A. Multilingual, commercial use allowed** | | | | | | | | | | | | | | | | | | | | | | |
| BAAI/bge-reranker-v2-m3 | 568M | NATIVE (`XLMRobertaForSequenceClassification` → BERT + CLS head); live on this node; many community GGUFs | Apache-2.0 ✓ | 8,192 pos. (card examples 512; node runs 2,560) | multilingual, no list; measured on it (IT, HL) | 57.03 | 58.36 | 59.51 | 56.51 | 69.32 | 67.88 | — | — | — | 63.07 | 67.41 | 0.825 | 0.958 | 0.969 | 0.821 / 0.786 | — | local only: 50×300 chars 1.7 s, 50×3k chars 5.5 s (Arc, survey note) |
| albertobarnabo/bge-reranker-v2-m3-italian (2026-09) | 568M | NATIVE (same arch); no GGUF | Apache-2.0 weights ⚠ (trained on mMARCO; card says MS MARCO terms are non-commercial research) | as above | Italian fine-tune | — | — | — | — | — | — | — | — | — | — | — | — | — | — | **0.830 / 0.797** (paired bootstrap vs base: +0.0096 nDCG, p < 0.0001) | — | — |
| Qwen/Qwen3-Reranker-0.6B | 0.6B | NATIVE (Qwen3 reranker path: yes/no rows → `cls.output`, softmax); ggml-org Q8_0 GGUF | Apache-2.0 ✓ | 32k | 100+ langs, no list | 65.80 | 66.36 | 67.28 | 56.28 | 57.70 | 65.34 | 56.94 | 67.12 | 68.41 | **68.03** | 66.48 | 0.785 | 0.958 | — | — | — | local only: 40×2,000 chars 1.86 s (Arc, Aug., survey note) |
| Qwen/Qwen3-VL-Reranker-2B | 2.1B | NATIVE for text (`Qwen3VLTextModel` inherits the Qwen3 reranker detection; `qwen3vl.cpp` loads `cls_out`; softmax for QWEN3VL). Community GGUFs (mradermacher, tooktang Q8_0): check they carry `cls.output` | Apache-2.0 ✓ | 32k | "30+ languages", no list | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | MMTEB(Retrieval) 70.0 vs 68.1 for its first stage Qwen3-VL-Embedding-2B (card; first stage and depth not stated) | — |
| Horizon-Labs/multilingual-reranker-base (2026-10) | 308M | NATIVE (`ModernBertForSequenceClassification`, mean pooling, mmBERT tokenizer); no GGUF | Apache-2.0 ✓ | **trained on 384 tokens/pair** (8,192 pos.) | yes (listed) | — | — | — | — | — | — | — | — | — | — | — | 0.800 | 0.967 | 0.969 | — | distilled from Qwen3-Reranker-4B; "weaker than the teacher on long or technical passages" | — |
| Horizon-Labs/multilingual-reranker-small (2026-10) | 141M | NATIVE; Q-P1NG Q8_0 GGUF, made with stock `convert_hf_to_gguf.py` plus one metadata edit (`tokenizer.ggml.metaspace.prepend_scheme` changed from `always` to `first`) | Apache-2.0 ✓ | 384 tokens/pair | yes (listed) | — | — | — | — | — | — | — | — | — | — | — | 0.779 | 0.969 | 0.967 | — | Q-P1NG GGUF card: 35 Italian questions over 2 PDFs, Hit@1 25 → 31 over hybrid top-10 | Q-P1NG card: ~1 s for 10 passages, laptop CPU |
| cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 | 118M | NATIVE (`XLMRobertaForSequenceClassification`); community GGUF | Apache-2.0 ⚠ (trained on mMARCO) | 512 | yes (listed) | — | — | — | — | — | — | — | — | — | — (HAKARI excluded it from its tables as an outlier) | — | — | — | — | — | — | — |
| Alibaba-NLP/gte-multilingual-reranker-base | 306M | **PATCH-MEDIUM**: `NewForSequenceClassification` is not registered. It is a BERT with RoPE (NTK ×8), a gated-GELU MLP (inferred from the 306M parameter count) and post-LN. Needs a converter class plus enabling GeGLU in the RoPE branch of `bert.cpp` (the JINA_BERT_V3 path). GGUF only for the gpustack/llama-box fork | Apache-2.0 ✓ | 8,192 | yes (listed) | 59.51 | 59.44 | 66.33 | — | — | — | — | — | — | 62.97 | — | — | — | — | — | card: results only as an image | — |
| mixedbread-ai/mxbai-rerank-base-v2 | 0.5B | **PATCH-SMALL**: `Qwen2ForCausalLM`; score = logit("1") vs logit("0") at the last token (repo's `1_LogitScore`: true id 16, false id 15) → converter writes `cls.output` = W₁₆ − W₁₅, RANK, prompt template; `qwen2.cpp` needs the `cls_out` line (~30 lines). Community GGUFs are plain LM conversions without the head | Apache-2.0 ✓ | 32k (config) | yes (listed) | — | — | — | 58.40 | 55.32 | 64.24 | 59.58 | 64.90 | 61.44 | — | — | — | — | — | — | BEIR avg 55.57, "Multilingual" 28.56 (protocol not stated) | A100 0.67 s (card; unit and protocol not stated) |
| mixedbread-ai/mxbai-rerank-large-v2 | 1.5B | PATCH-SMALL (same as base) | Apache-2.0 ✓ | 32k (config) | yes (listed) | — | — | — | 61.44 | 57.94 | 67.06 | 62.45 | 69.65 | 70.81 | — | — | — | — | — | — | BEIR avg 57.49, "Multilingual" 29.79 | A100 0.89 s (card; as above) |
| nvidia/llama-nemotron-rerank-1b-v2 | 1.2B | **PATCH-SMALL**: `LlamaBidirectionalForSequenceClassification` is not registered. **Open PR #24083** (unmerged): converter `score.weight` → `cls.output`, RANK + mean pooling, `CLS_OUT` loaded in `llama_embed`, BOS fix. The kread GGUF needs that PR | OpenMDW-1.1 + Llama 3.2 Community Licence ⚠ (card: "ready for commercial use") | 8,192 | yes (26 evaluated langs incl. it) | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | pipeline with llama-nemotron-embed-1b-v2, Recall@5: MIRACL (subsampled, 26 langs) 65.80; MLQA cross-lingual 86.83; MLDR 70.69; NQ/HotpotQA/FiQA/TechQA 73.64 | — |
| BAAI/bge-reranker-v2-gemma | 2.5B | **PATCH-SMALL**: `GemmaForCausalLM` is registered; score = logit("Yes") at the last token → converter writes `cls.output` = W_Yes, RANK, prompt; `gemma.cpp` needs the `cls_out` line. Plain-LM GGUFs exist | Apache-2.0 on card ⚠ (base gemma-2b is under the Gemma Terms of Use) | 8,192 base (card example 1,024) | multilingual, no list | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | BGE publishes images only | — |
| zeroentropy/zerank-1-small(-reranker) | 1.7B | **PATCH-SMALL**: `Qwen3ForCausalLM`, but reranker detection does not fire; score = sigmoid(logit("Yes")/5) → converter writes `[W_Yes; 0]` (order kept by the graph's softmax); no graph change | Apache-2.0 ✓ | 32,768 | **en only** (card) | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | top-100 from text-embedding-3-small, nDCG@10: legal 0.817, medical 0.773, finance 0.861, code 0.730, STEM 0.680, conversational 0.556 | — |
| BAAI/bge-reranker-v2-minicpm-layerwise | 2.7B | **PATCH-LARGE**: `LayerWiseMiniCPMForCausalLM` has per-layer heads (layers 8–40, output layer chosen at inference). Converting a fixed cutoff (truncate at layer N, keep its head) could reduce it to converter + loader line; untested | Apache-2.0 on card ⚠ (base MiniCPM-2B-dpo has its own model licence) | 2,048 | multilingual; BGE recommends it for zh/en | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | images only | — |
| lightblue/lb-reranker-0.5B-v1.0 | 0.5B | **PATCH-LARGE** for `/rerank`: score = expected value of ratings 1–7 over full-vocab probabilities (needs a vocab softmax plus a weighted sum). Works unpatched through `/completion` with `n_probs`, with the expectation computed client-side. bartowski GGUF (plain LM) | Apache-2.0 ✓ | — | yes (listed, 95+) | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | images only | card: "on average faster than BGE reranker v2" (image) |
| **B. Non-commercial** | | | | | | | | | | | | | | | | | | | | | | |
| jinaai/jina-reranker-v2-base-multilingual | 278M | NATIVE (`XLMRobertaForSequenceClassification`; GGUF-my-repo conversions exist; score parity not checked) | CC-BY-NC-4.0 ✗ | 1,024 | multilingual, no list | 58.22 | 63.73 | 39.66 | 57.06 | 63.65 | 67.90 | — | — | — | rank 11 of 54 (value not in text) | — | — | — | — | — | — | — |
| jinaai/jina-reranker-v3 | 0.6B | **PATCH-LARGE**: listwise (query + ≤ 64 docs in one context; per-doc last-token states → MLP projector → cosine). `JinaForRanking` is not registered. The official GGUF runs through `rerank.py` over `llama-embedding` with an external projector (hanxiao fork "recommended") | CC-BY-NC-4.0 ✗ | 131k, shared by the list | multilingual, no list | — | — | — | 61.94 | 66.83 | 67.92 | 62.10 | 72.20 | 68.01 | — | — | — | — | — | — | — | A100, bs 1, top-100 listwise: NQ 371 ms; AILACasedocs (docs ~1,904 tokens) 16.1 s (J35 paper) |
| jinaai/jina-reranker-v3.5 (2026-07) | 0.6B | **PATCH-LARGE**: as v3, plus hybrid sliding-window attention. The official GGUF needs the littlewine fork; **upstream PR #26286 open** | CC-BY-NC-4.0 ✗ | 131k | multilingual, no list | — | — | — | — | — | — | 63.20 | 74.11 | 70.95 | — | — | — | — | — | — | Struct-IR 48.3 (controlled pool) | A100, bs 1, top-100: NQ 305 ms (328 docs/s); AILACasedocs 10.3 s |
| jinaai/jina-reranker-m0 | 2.4B | at least PATCH-SMALL/MEDIUM (`JinaVLForRanking` on Qwen2-VL, not registered; head not inspected) | CC-BY-NC-4.0 ✗ | — | multilingual | — | — | — | 58.95 | 66.75 | 68.19 | — | — | — | — | — | — | — | — | — | — | — |
| ContextualAI/ctxl-rerank-v2-instruct-multilingual-1b | ~1B (Qwen3, 14 × 2048) | **PATCH-SMALL**: `Qwen3ForCausalLM`; score = raw logit of vocab id 0 at the last token → `[W₀; 0]` + instruction prompt; community GGUFs (keisuke-miyako, no card) | CC-BY-NC-SA-4.0 ✗ | 32K | 100+, no list | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | card/blog: images only | — |
| ContextualAI/ctxl-rerank-v2-instruct-multilingual-2b | ~2B (Qwen3, 18 × 2560) | PATCH-SMALL (same) | CC-BY-NC-SA-4.0 ✗ | 32K | 100+ | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | images only | — |
| **C. English-only (only for English collections, e.g. PubMed)** | | | | | | | | | | | | | | | | | | | | | | |
| Alibaba-NLP/gte-reranker-modernbert-base | 149M | NATIVE (`modern-bert.cpp` names it) | Apache-2.0 ✓ | 8,192 | en | — | — | — | — | — | — | — | — | — | — | — | 0.473 | 0.898 | — | — | BEIR 56.19, LoCo 90.68, CoIR 79.99 | — |
| cross-encoder/ettin-reranker-{17,32,68,150,400}m-v1 (2026-05) | 17M–400M | PATCH-SMALL: config says `ModernBertModel`; the head is stored as sentence-transformers modules (Pooling → Dense → LayerNorm → Dense) that the converter must map to `cls`/`cls_norm`/`cls_out` | Apache-2.0 ✓ | — | en | — | — | — | — | — | — | — | — | — | — | (short-English, 400m: 70.23) | — | — | — | — | — | — |
| ibm-granite/granite-embedding-reranker-english-r2 | ~149M | NATIVE (`ModernBertForSequenceClassification`) | Apache-2.0 ✓ | — | en | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| ncbi/MedCPT-Cross-Encoder | ~110M (BERT-base) | NATIVE (`BertForSequenceClassification`) | public domain ✓ | 512 | en (biomedical) | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | local only: 72–99 ms per 50 on OpenVINO (survey note) |

Notes on the cells:
- J3: the jina-v3 card gives BEIR 61.94; the paper text says 61.85 and gives the MIRACL average as 66.50 in §5.4 against
  66.83 in its own table.
- HAKARI's text gives Qwen3-0.6B's "long-task" z = +1.62. bge-reranker-v2-m3 scores short +1.10 / long +0.06. bge-reranker-v2-m3
  is rank 1 on NanoMIRACL at 87.57.
- HL's MIRACL column counts as in-domain for bge-reranker-v2-m3 and for Horizon v1.1, which were trained on MIRACL train (HL card).
  Wikipedia has 9 candidates per query, so every model scores about 0.96–0.97.
- The HAKARI leaderboard now also lists mxbai-rerank-base/large-v2, the ettin rerankers and Nemotron-3.5-Rerank-8B. Their values
  are only in its 539 MB DuckDB, which was not read.
- The "local only" latencies are this node's own measurements as quoted in `docs/notes/reranker-survey-2026-11.md`, not
  re-verified. No source publishes SYCL/Arc numbers. The vendor A100 numbers use undisclosed or listwise protocols.

## Excluded, and why

- **Over 3B**: Qwen3-Reranker-4B (MMTEB-R 72.74 [Q]; MIRACL 76.56, RTEB 77.68 [J35]) and -8B; Qwen3-VL-Reranker-8B;
  zerank-1 and zerank-2 (4B, English); ctxl-rerank-v2 6b; bge-reranker-v2.5-gemma2-lightweight (9B); Querit-4B;
  nv-rerankqa-mistral-4b-v3; Nemotron-3.5-Rerank-8B; AsteroRank-Lepton (Qwen3.5-4B, CC-BY-NC); Contrastive-LM CLM-v0.1-8B;
  E2Rank-4B/8B; the jhu-clsp rank1 family (7B and up, generative reasoning); semantique ant-4b.
- **Not runnable, even with a reasonable patch**: late-interaction / multi-vector models (jina-colbert-v2, GTE-ModernColBERT,
  ColBERT-Zero, LFM2.5-ColBERT-350M, mxbai-edge-colbert, answerai-colbert-small). DeBERTa-v2 rerankers (mxbai-rerank-*-v1,
  English): the converter has no DeBERTa. T5/mT5 rerankers (monoT5, mT5-mMARCO): a T5 converter exists, but there is no
  token-score RANK path for encoder-decoders, so this would be PATCH-LARGE.
- **No Italian claimed**: hindsight-ai/hs-rerank-1 (114M, MIT, NATIVE arch, en/es/fr/de/pt/ja/ko/zh) and hs-decide-1-flash
  (Qwen3.5-0.8B "decision model", same 8 languages); Japanese, Korean and Polish rerankers (ruri-v3-reranker, japanese-reranker-*,
  KURE-Reranker-nano, polish-reranker-*).
- **Italian-only and too small or unevaluated** (not in the table): vincolle/reranker-bert-italian-uncased-mmarco-mnrl
  (ModernBERT, NATIVE; only an in-distribution dev score, nDCG@10 0.9235); osiria/minilm-l{6,12}-h384-italian-cross-encoder
  (33M, MIT, NATIVE arch, no evaluation); nickprock/cross-encoder-italian-bert-stsb (STS, not retrieval);
  semantique-team/ant-08b (adapter published 2026-11-10, no evaluation).
- **English-only** models other than group C: the ms-marco-MiniLM/TinyBERT cross-encoders and jina-reranker-v1-*.
- tomaarsen/Qwen3-Reranker-0.6B-seq-cls has the same weights as `Qwen3ForSequenceClassification`. That arch is not in the
  converter map; use the original.
- An Italian legal retrieval set exists (ReDiX-Benchmark-Legal-ita, 3,310 queries). It is gated and publishes no reranker scores.

## Sources

- llama.cpp upstream master 7592b61 (local sparse checkout `/tmp/claude-0/up/lc`): `conversion/__init__.py`, `conversion/qwen.py`,
  `conversion/bert.py`, `conversion/llama.py`, `conversion/qwen3vl.py`, `src/llama-graph.cpp`, `src/models/*.cpp`, `gguf-py/gguf/constants.py`
- llama.cpp PR #24083 (nemotron rerank, open): https://github.com/ggml-org/llama.cpp/pull/24083 · PR #26286 (jina v3.5, open), cited by
  https://huggingface.co/jinaai/jina-reranker-v3.5-GGUF
- [Q] https://huggingface.co/Qwen/Qwen3-Reranker-0.6B (arXiv 2506.05176)
- [J3] https://huggingface.co/jinaai/jina-reranker-v3 · https://arxiv.org/abs/2509.25085 · https://huggingface.co/jinaai/jina-reranker-v3-GGUF
- [J35] https://huggingface.co/jinaai/jina-reranker-v3.5 · https://arxiv.org/abs/2607.18152
- [H] https://arxiv.org/abs/2606.22778 (read via https://huggingface.co/papers/2606.22778) · https://huggingface.co/spaces/hakari-bench/leaderboard ·
  https://huggingface.co/datasets/hakari-bench/leaderboard_database
- [HL] https://huggingface.co/Horizon-Labs/multilingual-reranker-base · https://huggingface.co/Horizon-Labs/multilingual-reranker-small ·
  https://huggingface.co/spaces/Horizon-Labs/multilingual-retrieval-leaderboard (`results.json`) · https://huggingface.co/Q-P1NG/multilingual-reranker-small-GGUF
- [IT] https://huggingface.co/albertobarnabo/bge-reranker-v2-m3-italian
- Model cards and configs: https://huggingface.co/BAAI/bge-reranker-v2-m3 · https://huggingface.co/BAAI/bge-reranker-v2-gemma ·
  https://huggingface.co/BAAI/bge-reranker-v2-minicpm-layerwise · https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B ·
  https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base · https://huggingface.co/gpustack/gte-multilingual-reranker-base-GGUF ·
  https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v2 · https://huggingface.co/mixedbread-ai/mxbai-rerank-large-v2 ·
  https://huggingface.co/nvidia/llama-nemotron-rerank-1b-v2 · https://huggingface.co/kread/llama-nemotron-rerank-1b-v2-GGUF ·
  https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual · https://huggingface.co/jinaai/jina-reranker-m0 ·
  https://huggingface.co/ContextualAI/ctxl-rerank-v2-instruct-multilingual-1b · https://huggingface.co/ContextualAI/ctxl-rerank-v2-instruct-multilingual-2b ·
  https://huggingface.co/zeroentropy/zerank-1-small-reranker · https://huggingface.co/lightblue/lb-reranker-0.5B-v1.0 ·
  https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 · https://huggingface.co/Alibaba-NLP/gte-reranker-modernbert-base ·
  https://huggingface.co/cross-encoder/ettin-reranker-400m-v1 · https://huggingface.co/ibm-granite/granite-embedding-reranker-english-r2 ·
  https://huggingface.co/ncbi/MedCPT-Cross-Encoder · https://huggingface.co/hindsight-ai/hs-rerank-1 · https://huggingface.co/hindsight-ai/hs-decide-1-flash ·
  https://huggingface.co/vincolle/reranker-bert-italian-uncased-mmarco-mnrl · https://huggingface.co/osiria/minilm-l12-h384-italian-cross-encoder
- Starting point: `/home/user/louise-rag/docs/notes/reranker-survey-2026-11.md`

## What the published numbers do and do not let us conclude

1. **No reranker in this list has a published Italian legal score.** The only Italian numbers are: mMARCO-it, which is
   translated web QA (the Italian bge fine-tune beats its base by +0.0096 nDCG@10); Wikipedia-it, where every model is
   at about 0.97 with 9 candidates; and a 35-question anecdote. For Italian law, only the node's own benchmark (JuriFindIT etc.) can decide.
2. **The ranking depends on the protocol, consistently in one direction.** On short multilingual QA (MIRACL in J3, HL and
   NanoMIRACL; HAKARI short-ML), bge-reranker-v2-m3 leads every ≤ 3B model that has numbers. bge-reranker-v2-m3 is trained on MIRACL.
   On long or heterogeneous tasks (MLDR and MMTEB-R in Q; HAKARI overall and long), Qwen3-Reranker-0.6B leads. The node's lists
   of 2,048-token chunks are closer to the long regime, but that is an inference, not a measurement.
3. **Qwen3-0.6B's MIRACL score depends on the harness.** It ranges from 57.70 (J3) to 67.12 (J35) to 0.785 (HL). Mixing columns
   from different sources would give any desired order.
4. **Commercially usable and runnable today without a patch**: bge-v2-m3 (and its Italian fine-tune ⚠), Qwen3-Reranker-0.6B,
   Qwen3-VL-Reranker-2B (text) and Horizon base/small. The 384-token training of the Horizon models is a poor fit for 2k-token
   chunks. **Small patches unlock**: mxbai base/large-v2, nemotron-1b (PR open), bge-gemma (licence ⚠) and zerank-1-small
   (English-only claim). mxbai, nemotron, zerank and Qwen3-VL-2B have no independent multilingual numbers beside bge and Qwen3,
   except the jina tables for mxbai.
5. **No published latency transfers to the Arc/SYCL setup.** The vendor numbers are A100, listwise or of unstated protocol.
   Cost must be measured on the node.
