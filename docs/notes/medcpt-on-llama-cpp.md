# MedCPT on our llama.cpp: a BERT text pair, for embedding and for rerank

The operator, 2026-11-26, through München: "Move both" — MedCPT's article
encoder (a (title, abstract) pair, pubmed's pair space, `vega-medcpt-article-ov`)
and its cross-encoder (`louise-rerank-medcpt`) from OpenVINO to our llama.cpp —
and "Validate all the models and port MedCPT". Each model moves only if it
passes its own gate.

Staged: **0034** (`staged/0034-llama-cpp-bert-text-pairs.patch`, inner
`0026-bert-text-pairs.patch`), candidate **`engine-pair.series` =
`engine-pace.series` + 0034: `b77c50dd0fbb`** (`e3564b6fb4a0` void: the GELU
below came after it). Not built.

## 1. What was wrong, four things

| | what llama.cpp did | what MedCPT needs (NCBI's usage, the model cards) |
|---|---|---|
| segments | every token on token-type row 0 ("token types are hardcoded to zero") | `[CLS] a [SEP]` on row 0, `b [SEP]` on row 1 (Hugging Face's token_type_ids) |
| a pair's tokens and cut | no pair for an embedding; a rerank prompt with no closing `[SEP]` for a WordPiece vocabulary | `[CLS] a [SEP] b [SEP]`, cut longest first to `max_length=512` |
| the cross-encoder's head | the converter dropped BERT's pooler for every BERT ("we are only using BERT for embeddings") | `classifier(tanh(pooler.dense([CLS])))` |
| the GELU | the tanh GELU for every BERT (on the CPU from an f16 table) | transformers' BERT `"gelu"`: the exact erf GELU |

And the converter stopped on MedCPT's tokenizer ("BPE pre-tokenizer was not
recognized"): its hash is now registered (`medcpt`, WPM, as upstream's
`convert_hf_to_gguf_update.py` lists models; llama-vocab reads no
pre-tokenizer for WPM).

## 2. What 0034 does

- **libllama** (inner patch): `llama_context_params::pair_segments` (and
  `common_params`), default false. The BERT graph then gives a token after its
  sequence's first `[SEP]`, by position, row 1 (`src/llama-pair-segments.h`,
  read off the ubatch's tokens). A single text is all row 0, as before. A
  context asking it of a model that is not BERT, has no second token-type row
  or no separator is refused at creation. Without it the graph is the one it
  was.
- **The declared GELU** (inner patch): the converter writes the model's
  `hidden_act` as `bert.hidden_activation` — the key ModernBert already reads,
  not a new one — and the BERT graph computes what the file declares: `"gelu"`
  the erf GELU (`ggml_gelu_erf`, on the CPU and SYCL), the tanh names the tanh
  GELU, anything else refused. A GGUF without the key keeps the tanh GELU and
  its bytes. The activation is in the weights file, so in its digest.
- **The converter** (inner patch): a `BertForSequenceClassification` of
  BertModel itself keeps its pooler as the head's dense layer (`cls`, tanh by
  default); every class built on BertModel still drops it.
- **The gRPC server** (`text_pair.h`): the model option **`pairs:true`**.
  An embedding whose text is VeGa's pair marker followed by the JSON
  `[first, second]` (deploy/vega/vega_wrapper.py, app/vega/tag.py
  `backend_pair`) and a rerank's (query, document) become `[CLS] a [SEP] b
  [SEP]`, cut LONGEST FIRST as Hugging Face's
  `TruncationStrategy::LongestFirst` cuts (216 of 216 rows of its own table)
  to the bound: the load's context, the physical batch, the trained
  positions — 512. A first segment holding `[SEP]` is refused (the graph would
  start the second segment there), and so is one text with a `[SEP]` inside it
  in a pair model. **`rerank_score:sigmoid`**: the logit's sigmoid, as the
  OpenVINO backend and TEI gave MedCPT's; `raw` (the default) the logit. A
  llama.cpp without `pair_segments` (a fork compiling the same server) refuses
  a load asking `pairs:true`.

## 3. Measured here (CPU, this tree, against NCBI's usage: transformers, fp32, CPU)

67 PubMed (title, abstract) pairs — the fixture's, 40 fetched from PubMed, three
past 512 tokens, one per truncation case, and texts with accents, Greek, CJK and
a literal `[SEP]` in the abstract; for the cross-encoder 80 (title, abstract)
pairs, each title against its own abstract and its neighbour's. Through the gRPC
server (LoadModel and Embedding/Rerank as LocalAI calls them), f16 weights:

| | 0034, erf GELU declared | 0034, no key (tanh GELU) | without the segments |
|---|---|---|---|
| article encoder, worst cosine to torch | **0.9999999** (1 − 1.17e-7; median 1 − 4.3e-8) | 0.9999882 | 0.9736 |
| cross-encoder, sigmoid scores | **within 0.00063** | within 0.0071 | — |
| cross-encoder, logits | within 0.0062 | within 0.078 | off by up to 18.9; the order 29 of 40 |
| own abstract above the neighbour's | 40 of 40 (one tie: two records share an abstract) | the same | — |

- **Not bit-identical to torch, and it cannot be.** With the GELU closed, what
  remains is float rounding. f32 weights, flash attention OFF: worst 1 − 1.5e-12
  (median 5.3e-13), 941 of 51,456 components the same bits, the rest differing
  as float32 sums taken in another order do (ggml's tiles against torch's BLAS:
  float addition is not associative). f32 weights, flash attention ON: 1 − 8.6e-8,
  as the f16 weights with it (1 − 1.2e-7): the CPU's flash attention holds K and
  V in f16. So the 1e-7 of the table is f16, of the weights and of the
  attention, not the GELU. Bit-exactness is a run reproducing itself
  (invariant 12); between implementations, the space's tolerance.
- A GGUF WITHOUT the key, on this tree against the same tree before the GELU
  change: 67 of 67 vectors and 80 of 80 logits the same floats.
- Token ids and counts are Hugging Face's, 67 of 67. f32 weights give the
  same picture (tanh 0.9999882).
- Refusals: a `[SEP]` in a first segment, a `[SEP]` inside one text, a marker
  with no JSON pair give INVALID_ARGUMENT; one ordinary text is embedded. A
  fork's build (`LOCALAI_HAS_PAIR_SEGMENTS 0`) refuses `pairs:true` at load
  (FAILED_PRECONDITION).

NOT measured: on the card; against the OpenVINO runs' own vectors (on the
node); that every model without `pairs:true` and without the key answers byte
for byte as on the engine before (the graph's other branches are the
original lines; the engine gate's byte check measures it).

## 4. The article encoder is a NEW pubmed space

09 §9.3's weights digest is over the file bytes. pubmed's document side is the
OpenVINO IR directory, so a GGUF is other weights, another `space_id`
(invariant 1): the GGUF cannot be gated INTO pubmed's space. Moving it is
creating a space (docs/notes/new-collection.md), and on it:

- **its own CPU reference**, mandatory (no card run is gated without one,
  near-exact-runs.md §2), made by the GGUF's CPU twin: llama.cpp, the same
  weights and options, `gpu_layers: 0`. The torch runs of §3 are a parity
  check against NCBI's model, not that reference;
- **its calibration and golden vectors** (T2's sixteen, 03 §3.x);
- the **carry** (RULE 1) of the vectors pubmed holds — NCBI's 35.4 M and the
  4.58 M embedded on OpenVINO, all with the erf GELU — only where measured
  within the new space's tolerance; otherwise a re-embedding.

**Why the GELU is decided now.** The new space's CPU twin computes what its
card does, so with the tanh GELU the gate would never see the gap — but the
carry is measured against the erf vectors, and the gap would take ~1.2e-5 of
the tolerance before any card noise. Declared now, the new space starts at
1 − 1.2e-7 from NCBI's model, before a CPU reference and a calibration are
made on it; declared later, both would be made again.

pubmed's query side (`vega-medcpt-query`, a GGUF converted before this, tanh)
is untouched: its file, its space and its CPU reference stay.

## 5. The reranker: what can be made faster

Not a space; its scores feed the calibrated merge (05 §5.6), so a change of
scale is a change of calibration. Measured on CPU (4 threads), 48-document
reranks of 4 queries, f16, erf GELU, `pairs:true rerank_score:sigmoid`:

| | 48 documents | scores moved by batch-mates (order reversed, requests cut by 7) |
|---|---|---|
| one document a pass, flash attention ON | 3.7 s | 0 of 192 |
| one document a pass, flash attention OFF | 4.6 s | 0 of 192 |
| 4 a pass (`parallel:4`, ubatch 2,048), FA ON | 2.7 s | 174 of 192 |
| 8 a pass (`parallel:8`, ubatch 4,096), FA ON | 2.6 s | 184 of 192 |
| 4 a pass, FA OFF | 3.4 s | 63 of 192 |

- **Batching is the speed-up, and it breaks the score's independence from
  its batch-mates** (by at most 2.4e-9 here): the very defect fixed on
  OpenVINO on 2026-10-30 (approved-encoders.md, #112: "a document's score
  was not a function of (query, document)"). Not admissible as it is. On the
  card the gain is likely larger (one 300-token pass leaves a B70 mostly
  idle); the way to keep it is OpenVINO's — one static shape — which here
  means a ubatch always of the same sequences count and the same padded
  length, the fillers in their own sequences. Not built: measure on the card
  first whether one document a pass is slower than OpenVINO's 99 ms for 50.
- **Flash attention ON**: 1.25× on the CPU, still 0 moved. On the card
  `vega-medcpt-query` was not bit-exact across fresh loads with FA ON
  (approved-encoders.md); qwen's was made exact with `GGML_SYCL_FA_ONEDNN=0`.
  Measure the reranker's card run with FA ON + `GGML_SYCL_FA_ONEDNN=0` across
  fresh loads before keeping it.
- **Only the `[CLS]` row in the last layer** (CLS and RANK pooling read one
  row; the graph computes every row's last layer): ~7 % of the work by count
  (the last layer's Q, O and FFN for every other token), for the reranker
  and every CLS-pooled encoder. Not built: it changes the bytes of every live
  CLS-pooled model (bge-m3, MedCPT's query side), so it would come as an
  option per model, measured first.

## 6. For München: convert, load, gate

Convert both with the PATCHED converter — the llama.cpp checkout of the
engine build after its patches (`backend/cpp/llama-cpp/llama.cpp` once
`prepare.sh` has run), from `ncbi/MedCPT-Article-Encoder` and
`ncbi/MedCPT-Cross-Encoder`:

    python convert_hf_to_gguf.py <hf dir> --outtype f16 --outfile <name>-f16.gguf

Check both GGUFs hold `bert.hidden_activation = "gelu"`, and the
cross-encoder's `cls.weight` and `cls.bias` (the pooler) as well as
`cls.output.*`; without them it scores noise.

| model | options |
|---|---|
| the article encoder (a new pubmed space, §4) | `pairs:true`, `pooling:cls`, `parallel:1`, `ubatch:512`, context 512; `embd_normalize` as the space declares (the OpenVINO task returned the raw `[CLS]` row) |
| the cross-encoder | `reranking: true`, `pairs:true`, `rerank_score:sigmoid` (the OpenVINO scores were sigmoids), `parallel:1`, `ubatch:512`, context 512 |

The cross-encoder is gated against `louise-rerank-medcpt`'s scores and order on
the same (query, document) sets, its scores across fresh loads and
batch-mates (OpenVINO's #112 measurement), its latency against OpenVINO's,
and any calibration fitted on its scores compared before and after. The
article encoder waits for the operator's word on the new space (§4). A model
that fails stays on OpenVINO.
