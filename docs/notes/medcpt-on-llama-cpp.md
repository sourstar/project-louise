# MedCPT on our llama.cpp: a BERT text pair, for embedding and for rerank

The operator, 2026-11-26, through München: "Move both" — MedCPT's article
encoder (a (title, abstract) pair, pubmed's pair space, `vega-medcpt-article-ov`)
and its cross-encoder (`louise-rerank-medcpt`) from OpenVINO to our llama.cpp —
and "Validate all the models and port MedCPT". Each model moves only if it
passes its own gate against its OpenVINO run.

Staged: **0034** (`staged/0034-llama-cpp-bert-text-pairs.patch`, inner
`0026-bert-text-pairs.patch`), candidate **`engine-pair.series` =
`engine-pace.series` + 0034: `e3564b6fb4a0`**. Not built.

## 1. What was wrong, three things

| | what llama.cpp did | what MedCPT needs (NCBI's usage, the model cards) |
|---|---|---|
| segments | every token on token-type row 0 ("token types are hardcoded to zero") | `[CLS] a [SEP]` on row 0, `b [SEP]` on row 1 (Hugging Face's token_type_ids) |
| a pair's tokens and cut | no pair for an embedding; a rerank prompt with no closing `[SEP]` for a WordPiece vocabulary | `[CLS] a [SEP] b [SEP]`, cut longest first to `max_length=512` |
| the cross-encoder's head | the converter dropped BERT's pooler for every BERT ("we are only using BERT for embeddings") | `classifier(tanh(pooler.dense([CLS])))` |

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
server (LoadModel and Embedding/Rerank as LocalAI calls them):

| | with 0034 | without the segments |
|---|---|---|
| article encoder, cosine to torch, f32 | 0.9999882 at worst, 67 of 67; token ids and counts Hugging Face's, 67 of 67 | 0.9736 at worst |
| article encoder, f16 | 0.9999884 at worst | — |
| cross-encoder, logits | within 0.079 (f32), 0.078 (f16) | off by up to 18.9; the order 29 of 40 |
| cross-encoder, `rerank_score:sigmoid` | within 0.0075 (f32), 0.0071 (f16) | — |
| cross-encoder, own abstract above the neighbour's | 40 of 40 (one tie: two records share an abstract) | — |
| refusals | `[SEP]` in a first segment, `[SEP]` inside one text, a marker with no JSON pair: INVALID_ARGUMENT; one ordinary text embedded | — |
| a fork's build (`LOCALAI_HAS_PAIR_SEGMENTS 0`) | `pairs:true` refused at load, FAILED_PRECONDITION | — |

**The residual is the GELU, not the pair**: ggml's BERT uses the tanh GELU
(on the CPU an f16 table), BERT the erf GELU; it is the same for every BERT on
llama.cpp, the live text space's MedCPT included, and it does not move with
f16 or flash attention. Moving BERT to `ggml_gelu_erf` would move every BERT
space's vectors: a new space, never part of this patch. Written down here so
it can be weighed: it would bring the cosine to torch nearer 1, and cost a
re-embedding of every BERT collection.

NOT measured: on the card; against the OpenVINO runs themselves (their
vectors are on the node); that a model without `pairs:true` answers byte for
byte as on the engine before (the graph's other branch is the original lines;
the engine gate's byte check measures it).

## 4. For München: convert, load, gate

Convert both with the PATCHED converter — the llama.cpp checkout of the
engine build after its patches (`backend/cpp/llama-cpp/llama.cpp` once
`prepare.sh` has run), from the Hugging Face repositories `ncbi/MedCPT-Article-Encoder`
and `ncbi/MedCPT-Cross-Encoder`:

    python convert_hf_to_gguf.py <hf dir> --outtype f16 --outfile <name>-f16.gguf

The cross-encoder's GGUF must hold `cls.weight` and `cls.bias` (the pooler) as
well as `cls.output.*`; without them it scores noise.

The models' options (beside the ones the OpenVINO models' YAML and the text
space's article encoder carry):

| model | options |
|---|---|
| the article encoder, pair space | `pairs:true`, `pooling:cls`, `parallel:1`, `ubatch:512`, context 512; `embd_normalize:-1` if the space stores the raw `[CLS]` row (the OpenVINO task returns it raw) |
| the cross-encoder | `reranking: true`, `pairs:true`, `rerank_score:sigmoid` (the OpenVINO scores were sigmoids), `parallel:1`, `ubatch:512`, context 512 |

Gate each against its OpenVINO run: the article encoder against
`vega-medcpt-article-ov`'s vectors at the space's tolerance (and the CPU
reference at 0.999, invariant 12), with the run's reproducibility measured as
for any engine; the cross-encoder against `louise-rerank-medcpt`'s scores on
the same (query, document) sets, and its order. A model that fails stays on
OpenVINO.
