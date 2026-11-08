# Legal collections: chunking and retrieval — to settle before any create (2026-11-08)

**The operator, 2026-11-08:**
- "'At embedding time a chunk longer than the encoder's window is truncated.'
  This is not admissible, it would break the meaning of important parts."
- "Teach the splitter German and French paragraph numbering."
- "For the chunking always refer to fitting studies before guessing or
  reinventing the wheel."
- "Before actually creating the final sister we have to settle the chunking
  and the retrieval of the information (search by article code, tags to
  group articles by status and category, and so on…)."

**Status:** the chunker is BUILT (`legal_article`, app/chunking_legal.py,
tests/test_chunking_legal.py); it is not wired into any plugin yet. The
decisions below are the operator's. Every create (legi, neuris, eurlex,
normattiva-nemo) waits for them: the chunker and its parameters are inside
`space_id`.

## 1. What was measured (München, 2026-11-08, Nemotron-3-Embed-1B's tokenizer)

Source: `/root/measure/tokdist.txt`.

| sample | n | median | p95 | p99 | max | over 8,192 | chars/token |
|---|---|---|---|---|---|---|---|
| JuriFindIT articles (Italian statutes) | 23,617 | 132 | 1,285 | 3,715 | 869,830 | 90 (0.38 %) | 3.04 |
| LEGI, one daily increment | 458 | 474 | 5,994 | 7,778 | 17,691 | 2 (0.44 %) | 3.73 |
| NeuRIS BtMG articles | 48 | 328 | 1,258 | 2,316 | 2,316 | 0 | 3.91 |
| NeuRIS BtMG annexes | 3 | 6,799 | — | — | 10,807 | 1 | 2.67 |

**Today's chunker, measured.** `akn_article` caps a piece at 8,000
characters. That is under the window unless a text runs below ~0.98
chars/token, so in these samples nothing was truncated. The defect is how
long articles are cut:
- its paragraph split knows only Italian numbering, so a long German or
  French article falls to content-defined cuts, mid-paragraph;
- its bound is in characters, not the encoder's tokens, so the window is
  not guaranteed by construction.

## 2. What the studies say

The studies' detail is in the research record of this session. The
numbers marked [u] were read from secondary text: arXiv and the ACL
Anthology are not reachable from the agent's network.

- **Unit: the article, by the document's own hierarchy.**
  - Every statute benchmark labels at article level: BSARD (ACL 2022),
    COLIEE Task 3, JuriFindIT (EACL 2026), GerLayQA (EACL 2024).
  - Structure-aware chunking beats fixed size on legislation: NitiBench
    (EMNLP 2025, arXiv 2502.10868) [u]; StrucChunk (arXiv 2610.26313),
    +44.9 % HR@5 [u]; multi-layered embeddings on the Brazilian
    Constitution (arXiv 2411.07739) [u].
  - Counter-example: ClaimRAG-LAW used fixed 600-token chunks for uneven
    article lengths [u].
- **Context on each chunk helps.**
  - Anthropic, "Contextual Retrieval" (2024, read first-hand): top-20
    failures 5.7 % → 3.7 % with contextual embeddings (−35 %), → 2.9 % with
    contextual BM25 too.
  - Summary-Augmented Chunking (NLLP 2025) on LegalBench-RAG: fewer
    wrong-document hits [u].
  - The deterministic form for a statute is the act, the hierarchy and the
    article heading prepended.
- **Semantic chunking is not worth its cost** (Qu et al., arXiv
  2410.13070). A recursive splitter over the document's own separators is
  competitive (Chroma, 2024 [u]; LegalBench-RAG [u]).
- **Long documents: late chunking** (Günther et al., arXiv 2409.04701):
  +0.5 to +6.5 nDCG@10, growing with length, for mean-pooled encoders with
  the whole document in one window [u]. No statute study compares it with
  splitting at sub-units.
- **Hybrid BM25 + dense:** fusing helps general encoders zero-shot and can
  hurt fine-tuned in-domain ones (Louis et al., COLING 2025, arXiv
  2409.01357, French legal) [u].
- **Paragraph numbering:**
  - DE: § → Absatz "(1)" → Nummer "1." → Buchstabe "a)" → "aa)" (Handbuch
    der Rechtsförmlichkeit);
  - FR: article → "I. –" → "1°" → "a)" (Sénat, *Le montage des textes*;
    Guide de légistique);
  - EU: "1." → "(a)" → "(i)" (Joint Practical Guide);
  - IT: comma "1.", "1-bis." → "a)" → "1)" (Circolare 20 aprile 2001).

## 3. The chunker built: `legal_article` (app/chunking_legal.py)

- One chunk per article (per section the plugin emits: article, annex,
  recital).
- An article past the window is split at:
  1. its PARAGRAPH numbering in the four systems ("1." / "2-bis." / "(1)" /
     "I. —");
  2. then its POINTS ("a)", "(a)", "aa)", "1°", "1)");
  3. then its SENTENCES, never after "art.", "Abs.", "n.", "Nr." …;
  4. last, consecutive token windows that continue the text.

  Pieces are packed back up to the window.
- The window is `max_tokens` of a tokenizer pinned by sha256. The recipe
  sets it to `ctx` minus the document instruction's tokens, so what the
  encoder receives always fits, by construction.
- Optional `header`: the section's label repeated on every piece, counted
  in the window.
- The pieces together are the whole text, tested word for word. Normattiva's
  `akn_article` is untouched.

## 4. Decisions

### C1 — How an article longer than the window is cut

What it is: what happens to the 0.4 % of articles longer than 8,192
tokens, and to the long annexes.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. Split at its own structure, token-bounded (`legal_article`, built)** | paragraph → point → sentence → token windows, each piece whole, nothing lost | follows NitiBench / StrucChunk; every piece starts at a citable unit; deterministic; fits by construction | no statute study compares it with late chunking; a piece loses the article's other paragraphs as context unless C2 adds a header |
| B. Late chunking | the whole article encoded once, pooled per piece | measured gains on long texts | needs the whole article in one window: up to 869,830 tokens here, which no engine holds; one amendment changes every vector of the article |
| status quo (`akn_article`) | 8,000-character pieces | nothing to change | Italian numbering only; a character bound, not a token one |

Recommendation: **A**. If the operator says nothing: A.

### C2 — A context header on every chunk

What it is: the act's name and the article's label written at the head of
each chunk, so a piece of a long article still says what it belongs to.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. Header on (`header = true`): the plugin's section label** (act, hierarchy, article) | each chunk begins "Codice civile — Libro IV › Art. 2043" | −35 % failures with contextual embeddings (Anthropic); the deterministic form, no LLM | tokens spent on every chunk (~10–30); the label must name the act, which needs each plugin's label checked; inside `space_id`, so not reversible |
| B. No header | the text only | shortest chunks | a split piece ("(3) Die Absätze 1 und 2 gelten …") does not say which law it is |

Recommendation: **A, measured first** (§5), with the label form fixed per
plugin. If the operator says nothing: measure, then A if it is not worse.

### C3 — Short articles

What it is: whether a short article stays one chunk or is always cut at
its paragraphs.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. The whole article, split only past the window** | median article = one chunk | the benchmarks' unit (BSARD, COLIEE, JuriFindIT, GerLayQA); fewest vectors | a long but fitting article is one vector for several rules |
| B. Always per paragraph | every comma or Absatz its own chunk | finer citations | ~3–5× the vectors; the benchmarks score articles, so finer gains go unmeasured |

Recommendation: **A**. If the operator says nothing: A.

### R1 — Search by article code (one mechanism for the four)

What it is: "art. 2043 c.c.", "§ 823 Abs. 1 BGB", "article L121-1 du code
de la consommation", "Article 5 of Regulation (EU) 2016/679" answered by
that article, pinned as a fact, never ranked (invariant 4). Today only
Italian citations reach an article (`app/ask/articles.py`, its pattern and
its URN Normattiva's): the per-exception code the operator ruled out.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. One article pin for every legal collection** | each reference source answers `citations(query) → (work key, article label)` in its own grammar (the four resolvers exist); `ask/articles.py` reads that, the version in force at the date, the article's chunk | one engine, four grammars; French and EU articles reachable by code; uses the validity index all four declare | a protocol change in refs, and Normattiva's path moved onto it (it is live) |
| status quo | Italian only | nothing to change | French, German and EU codes answered by ranking alone |

Recommendation: **A**, built with the architecture session's review. If
the operator says nothing: A.

### R2 — Tags to group articles (status, category)

What it is: filters a reader chooses, and counts per value. The facet
mechanism exists (05 §5.7, `ask facets`; Gutenberg and PubMed declare
theirs); the legal plugins declare none.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. Declare per plugin:** in force / repealed / not yet in force (from the validity index); act type; code or collection; year; EuroVoc and subject matter (EU, from the CDM notice); a filter the reader asks for, never a weight (invariant 4) | "articles of the code civil in force", "EU directives on data protection" | established in every portal (EUR-Lex: status and EuroVoc); no ranking changes; the fields are already in `extra` | facet fields are in the Codex, so they are fixed at create; a value list per plugin to check against real records |
| status quo | no grouping | nothing to change | status shown on a hit, never filterable |

Recommendation: **A**. If the operator says nothing: A.

### R3 — The word leg beside the dense one

The platform already fuses BM25 and dense by RRF (invariant 4). Louis et
al. (2025) warn that fusion can hurt an in-domain fine-tuned encoder;
Nemotron is general, where fusion helps. **No change; measured with §5**
(hybrid against dense, the encoder rule's own metric).

## 5. The measurement before any create

`tools/encoder_bakeoff.py`, on the node, on the pools already held. Each
variant is a chunker setting; the metric is hybrid MRR@10 at article
level, with the paired test.

| set | language | what it decides |
|---|---|---|
| JuriFindIT | it (statutes) | C1–C3 on Italian; Normattiva × Nemotron |
| BSARD | fr (Belgian statutes, the French language) | C2 header on/off |
| LEMUR-it/fr/de, EUR-Lex-Sum | EU acts | C1 on long acts |
| GerLayQA (research licence: measurement only, after its ack) | de, BGB/StGB/ZPO | German |

Variants: `akn_article` (today), `legal_article` header off, header on.

## 6. What changes after the decisions

- **The three legal plugins:** `chunking_profile = "legal_article"`, and
  their recipes state the layer:
  - the tokenizer:
    `sha256:797410dfb649a5b9ba92bc4fef7dbf4022d00e73de6867c4ac199a8846439421`.
    That is Nemotron-3-Embed-1B-BF16's `tokenizer.json` at revision
    c0c9fea93ea424587517f2c59e20db9f1d6bf615, 17,077,678 bytes (München,
    2026-11-08; `/root/measure/nemotron-tokenizer.json`). It goes into
    `TOKENIZERS_DIR` as `<hex>.json`.
  - `max_tokens` = 8,191 (the space's `ctx`: the model's context_size 8,192 less one, as llama.cpp embeds; München's dry run, 2026-11-08) − the instruction's tokens;
  - `header`.
- **The label each plugin writes** is checked to name the act and the
  article (C2).
- **normattiva-nemo:** with a new chunker, the sister no longer cuts as
  Normattiva does, so case J refuses to carry her manifest and text index
  (they are functions of the chunks).
  - Her parts (records) still carry; her chunks and text index are cut
    again, by CPU, with no fetch and no parse.
  - This needs a J variant, "a sister with other layers", which the
    architecture session is asked to agree.
- **R1 and R2** are built, then the measurement of §5, then the creates.

## 7. JuriFindIT, SAT-Graph RAG, multi-layer embeddings, and G-DSR (operator's question, 2026-11-08)

**What was read first-hand:** G-DSR, Louis, van Dijck & Spanakis, "Finding the Law: Enhancing Statutory
Article Retrieval via Graph Neural Networks", EACL 2023, arXiv 2301.12847. Its full text came through
the Hugging Face papers index.

**What was not:**
- SAT-Graph RAG (arXiv 2505.00039) and Lima's multi-layered embeddings (arXiv 2411.07739): not in that
  index, and arXiv is not reachable from this session;
- JuriFindIT's paper (EACL 2026): known here from its card and §7.2–7.3 of gazzetta-plugin.md.

**The finding that matters most:**
- G-DSR's graph is the law's own HIERARCHY: section nodes (the headings of books, titles, chapters and
  sections) and article nodes, not the citations.
- A 3-layer GNN (GATv2) over it enriches each article's vector with its headings and its neighbours.
- On BSARD's test set, over the best text-only retriever: R@100 82.7 → 84.3, R@500 92.8 → 93.1, mAP
  35.3 → 47.1 (+11.8), mRP 27.5 → 40.2. BM25 scores R@100 49.3 and mAP 16.8.
- The GNN's type barely matters (GCN, GAT, GraphSAGE all within ~0.5 R@100, their Table 4): "the act of
  fusing information from neighboring nodes is more important than the way the aggregation is
  performed".
- Their expert study (their Appendix A.1): lawyers rely on the headings of the structure to find
  articles.

| | **JuriFindIT** (EACL 2026) | **SAT-Graph RAG** (de Martim 2025) | **Multi-layer embeddings** (Lima 2024) | **G-DSR** (EACL 2023) |
|---|---|---|---|---|
| what it is | an Italian statute-retrieval set: 895 expert and 169k generated questions over 23,617 articles; a fine-tuned encoder (DAR-legal-it); a heterogeneous legislative graph with cross-article references | a graph of the norm: its hierarchy, each component's dated versions, and the amending actions as nodes; questions answered by deterministic traversal | one vector per level of the hierarchy (document, title, chapter, article, paragraph) | a GNN over the hierarchy (headings + articles) on top of a dense retriever |
| what it gives that the others do not | expert-judged measurement in Italian; a domain encoder; citation edges | time and causality: the version in force on a date, what changed an article, when and by which act, deterministic and auditable | retrieval at the granularity of the question: a whole chapter or one paragraph | a measured gain from structure on statutes |
| evidence | its own tables; here DAR-legal-it 0.803 MRR@10 hybrid, reproduced (gazzetta-plugin.md §7.1b) | none: a design paper | reported better than flat chunking; numbers unverified | first-hand, above |
| already in Louise | the set (held on the node, acknowledged); DAR-legal-it as Normattiva's encoder; `eli:cites` edges | every version a record; validity by key (`--as-of`); closures from repeals; `eli:amends` / `eli:repeals` edges; `louise_traverse` | nothing: one level | nothing |
| what adding the rest costs | the citation graph in ranking: a trained model over a graph that grows | the amending actions as their own index (who amended which article when), derived on CPU from metadata we already keep; no GPU | 3–5× the vectors: Normattiva's ~8.2 M chunks → 25–40 M, card-days | a trained GNN served beside the encoder (torch, as a model server, invariant 7); vectors no longer a function of the chunk's text: an amendment moves its neighbours' vectors, no carry by chunk hash, no peer can check them by the calibration gate as designed |

### Is there a better way

No published method dominates statute retrieval. The measured gains are:
- headings and neighbours (G-DSR);
- context on each chunk (Anthropic −35 %, then −67 % with a reranker);
- BM25 fused with a general encoder (Louis et al. 2025);
- rerankers (COLIEE's winners).

G-DSR's headings can be had without training: the hierarchy path written at the head of each chunk
(decision C2) puts the same headings into the text the encoder reads. It stays a pure function of the
record, the same on every peer, and needs no model of its own. This is inference, not a study: the
nearest evidence is Anthropic's −35 % for context headers.

### Using them all: each in a role that does not fight the others

| piece | role | taken from | cost | benefit |
|---|---|---|---|---|
| 1. article chunks with the hierarchy path as header (C2) | what is ranked | G-DSR's headings, Lima's levels, Anthropic's headers | ~20 tokens a chunk: +~15 % encoder time at a median of 132 tokens; inside `space_id` | the structural context the studies measure, deterministic |
| 2. one outline chunk per act version (title + headings) | a second, light level | Lima; G-DSR's section nodes | +~4 % vectors (Normattiva: ~342 k versions against ~8.2 M chunks) | questions about a whole law ("which act regulates …") reach the act |
| 3. the amending actions as an index: article → the acts that changed it, with their dates | facts, never ranked (invariant 4) | SAT-Graph | CPU only, from modifications already kept (Normattiva's `active_modifications`, LEGI's `LIENS`, the CDM's amendments) | "what changed art. X, when, by which act", answered deterministically |
| 4. the citation graph | restrict, traverse; candidate widening only if measured and approved (invariant 4's weights) | JuriFindIT | a measurement (`tools/legal_pilot.py run`) | the answers one edge away from a hit, counted before anything moves |
| 5. a reranker | after fusion | COLIEE, Anthropic | the card's time per query | the largest single measured gain |
| 6. the GNN (G-DSR) | not now | — | above | revisit only if 1–5 leave a gap that measurement shows |

Pieces 1, 2 and 5 enter the measurement of §5. Pieces 3 and 4 are no-GPU work after the creates.

**Also found:** *Bundesrecht: An Open Library and Corpus for German Statutory Reference Processing*
(arXiv 2605.31338, 2026). German statutory references ("§§ 1 bis 3 BGB", "Abs. 1 Satz 2") are what
neuris-refs' resolver parses. Its library is the standard to compare that resolver against before
extending it (do not reinvent the wheel).
