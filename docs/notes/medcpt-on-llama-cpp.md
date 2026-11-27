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

**The operator's decision (2026-11-27):** "f16 as planned; if the
tollerance is within the multi peer tollerance there is no need to re-embed,
rule 1." (corrected by the operator: "rollerance" was "tollerance") So: the new space on the f16 GGUF (erf GELU declared); the vectors
pubmed holds are carried wherever they are within the space's tolerance
between peers (03 §3.1), and re-embedded only where they are not.

**The golden vectors on the CPU only (the operator, 2026-11-27: "Recalculate
the golden samples in CPU only").** Measured here on the f16 GGUF, erf GELU:

| | the same bytes? |
|---|---|
| 1, 3 and 4 threads; fresh loads | yes, 67 of 67, flash attention on and off |
| the same tree built for AVX2 against built for AVX-512 (this machine) | NO, 0 of 67: 1 − 1.6e-13 at worst with flash attention off, 1 − 6.8e-9 with it on |
| against torch | AVX2: 1 − 4.0e-8 (FA off), 1 − 1.2e-7 (FA on) |

So a CPU reference reproduces bit for bit on CPUs of one instruction set and
build, not on any CPU: the reference engine's device must name it (02 §2.4
calls a mismatch with the engine as written a determinism defect) — or the
engine must have no instruction-set-dependent code at all.

**The node's engines (München, 2026-11-27).** The node's CPU is an AMD Ryzen 5
7600 (Zen 4, AVX-512, no AMX). The SYCL images' CPU code is `llama-cpp-fallback`,
built with `GGML_NATIVE=OFF` and AVX, AVX2, AVX-512, FMA, F16C and BMI2 off
("CPU : SSE3 = 1 | SSSE3 = 1 | LLAMAFILE = 1 | REPACK = 1"): a `gpu_layers 0`
twin on a SYCL image runs baseline x86-64. The CPU reference engine
`louise-cpu-llama-cpp-ea2417d91dcc` is built with `GGML_CPU_ALL_VARIANTS` and
picks one of fourteen at its first load by the host's flags. MEASURED on the
node (München, each variant's own `ggml_backend_score()` with the engine's
libggml-base: zen4 1984, cooperlake 1728, icelake 1472, … x64 1; alderlake and
sapphirerapids 0): it loads `libggml-cpu-zen4.so` (AVX-512). Every CPU reference
stored on the node by that engine is the zen4 variant's: reproducible on a host
that selects zen4, not on another CPU.

The baseline built here the same way (everything off), the f16 erf GGUF, 67 pairs:

| | |
|---|---|
| baseline against this machine's AVX-512 build | 0 of 67 the same bytes; 1 − 2.2e-13 (FA off), 1 − 9.4e-9 (FA on) |
| baseline against torch | 1 − 4.0e-8 (FA off), 1 − 1.2e-7 (FA on): as near as any build |
| time, 4 threads | 49 s against 5.9 s (8×): the 512 calibration pairs in ~6 min |

A baseline build has no code that depends on the CPU it runs on, so its bytes
should be the same on every x86-64 CPU: the one reference engine for golden
vectors a peer of any kind can reproduce, at a cost that for MedCPT is minutes.
EXPECTED, NOT MEASURED across CPUs: the same binary on an Intel and an AMD CPU
gives one fingerprint — München can measure it with the image's CPU twin on
the node (Zen 4) and on a peer of another CPU. With
flash attention OFF the reference is nearer NCBI's model and moves least
between instruction sets. The tolerance `calibrate` writes must be measured
against every engine whose vectors are carried (OpenVINO's 4.58 M, NCBI's
35.4 M: torch as its stand-in) and the B70's run, or it comes out too tight
for the carry.

**The operator's decision on the golden vectors (2026-11-27): "A, as
recommended".** So, for the new space:

1. The golden vectors and the CPU reference are ONE computation, stored once:
   the SYCL image's own CPU twin (`gpu_layers 0`, the device hidden), baseline
   x86-64, flash attention OFF.
2. Before the calibration is written, the twin's fingerprint is compared on the
   node (Zen 4) and on a peer with another CPU. If they differ, the work stops
   there: the engine's device must then name the CPU, a format question for
   architecture.
3. The tolerance `calibrate` writes is measured against the B70's run,
   OpenVINO's (`vega-medcpt-article-ov`, the engine of the 4.58 M) and torch on
   the CPU as NCBI's stand-in for the 35.4 M (`deploy/vega/stage/article_reference.py`
   already computes NCBI's usage).
4. The query encoder is converted again from `ncbi/MedCPT-Query-Encoder` with
   this tree (`bert.hidden_activation = "gelu"`), with its own CPU reference and
   calibration role from the same twin.
5. The carry as decided: within the peers' tolerance carried, re-embedded only
   where not, the count said before any re-embedding.

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

Convert both with the engine's own converter, ONE command (München, 2026-11-27: the
patched checkout lived only inside the build container): `deploy/localai/llama-cpp/convert.sh`
builds llama.cpp's tree from the series as `build.sh` and LocalAI's `prepare.sh` do,
stages the model's files under the repository's name (no README.md, which the converter
would read as metadata; safetensors over pytorch_model.bin), converts, and writes
`<out>.lock` (the series, llama.cpp's version, every input's sha256, the GGUF's):

    sh deploy/localai/llama-cpp/convert.sh deploy/localai/patches/staged/engine-guard.series \
        <hf snapshot, or a models--ncbi--<name> cache directory> <name>-f16.gguf --outtype f16

MEASURED here from `engine-guard.series` (a fresh LocalAI and llama.cpp, nothing local):
`MedCPT-Article-Encoder-f16.gguf` sha256
`62d0e7b9bd70ec2155c641d5fa858d66a3a6a8bcd689b36a04254a7a4cadd7f5` and
`MedCPT-Cross-Encoder-f16.gguf` `078f8cde6ed112c106208846b4da85c54a936097979ad38678e3acb8c8bf640e`
(the latter from a cache directory with a README, links resolved) — the bytes of §3's
runs. The query encoder converts the same way.

Check both GGUFs hold `bert.hidden_activation = "gelu"`, and the
cross-encoder's `cls.weight` and `cls.bias` (the pooler) as well as
`cls.output.*`; without them it scores noise.

| model | options |
|---|---|
| the article encoder (a new pubmed space, §4) | `pairs:true`, `pooling:cls`, `parallel:1`, `ubatch:512`, context 512; `embd_normalize` as the space declares (the OpenVINO task returned the raw `[CLS]` row) |
| the cross-encoder | `reranking: true`, `pairs:true`, `rerank_score:sigmoid` (the OpenVINO scores were sigmoids), `parallel:1`, `ubatch:512`, context 512 |

**The cross-encoder's gate, exactly (2026-11-27), on `4b0da8dfcd96`.** A qual twin:

    {"name": "qual-rerank-medcpt-lc", "backend": "louise-sycl-f16-llama-cpp-4b0da8dfcd96-stack-7c301a7ba437",
     "reranking": true, "embeddings": true, "context_size": 512, "gpu_layers": 999, "mmap": true, "threads": 6,
     "flash_attention": "off",
     "parameters": {"model": "<MedCPT-Cross-Encoder-f16.gguf, sha256 078f8cde…>", "batch": 512, "seed": 0},
     "options": ["fit:off", "parallel:1", "cache_ram:0", "ubatch:512", "pairs:true", "rerank_score:sigmoid"],
     "env": {"GGML_SYCL_ENABLE_DNN": "0", "GGML_SYCL_FA_ONEDNN": "0", "NEO_CACHE_DIR": "/data/louise-cache/neo-cache"}}

Flash attention OFF: MedCPT's query encoder on this card was bit-exact across fresh
loads with it off and not with it on (approved-encoders.md); ON is measured after, as an
optimisation, never in the gate. The lists: the judged set pubmed's calibration was
fitted on, as the live search sends them (`python -m tools.measure.rerank_bench capture
umbrela <that judged.jsonl> --collection pubmed --out pubmed-medcpt.jsonl`).
*Found on the node (München, 2026-11-27): pubmed has NO reranker calibration and NO
judged set.* So the lists are today's real pubmed questions (the admin's, from
`state/query-traces/<day>.jsonl`: their `query`, one per line, `{"query": ...}`), and
the search builds them as it sends them (`rerank_bench capture queries <that file>
--collection pubmed --out pubmed-medcpt.jsonl`: the format for questions with no
judgement). No `--calibration`; no quality arm until a judged set exists (a public one,
TREC PM or TREC-COVID, is a fetch and a licence: the operator's); and the calibration's
name has nothing to keep — `louise-rerank-medcpt` is not calibrated today. The parity,
the candidate against itself and the latency, both models on the same lists:

    python -m tools.measure.rerank_parity --capture pubmed-medcpt.jsonl --queries 40 --per-query 50 \
        --url http://localai:8080/v1/rerank --model qual-rerank-medcpt-lc --reference-model louise-rerank-medcpt \
        --loads 3 --cut 7 --calibration <pubmed's a>,<b> --out medcpt-rerank-parity.json

PASS (proposed): the first document the same for every query (a reference tie at 1.0
counts as the same); the median share of the reference's top 10 kept ≥ 0.9; the largest
score difference ≤ 0.01; the candidate's scores the same bits again, reversed, sent seven
at a time, and after each of three fresh loads. REPORTED: both models' 50-document
latency (p50, p95, warm) and the largest difference of calibrated relevance. And the
judged quality, both arms on the same lists: `rerank_bench rerank` per arm, then
`rerank_bench score --arms OV,LC --base OV`.

**The calibration's name.** A calibration names the model it was fitted for, and under
another name it is not used: the merge falls back to rank (`merge_withheld`). Either the
model keeps its name (`louise-rerank-medcpt`, its backend moved) and its calibration
applies, its measured difference shown by the parity; or a new name, and the calibration
fitted again (`tools/measure/fusion_calibration.py`). The operator's.

The cross-encoder is gated against `louise-rerank-medcpt`'s scores and order on
the same (query, document) sets, its scores across fresh loads and
batch-mates (OpenVINO's #112 measurement), its latency against OpenVINO's,
and any calibration fitted on its scores compared before and after. The
article encoder goes as §4 says, on the operator's decision there. A model
that fails stays on OpenVINO.
