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
  their recipes state the layer: the tokenizer's sha256, read on the node;
  `max_tokens` = 8,192 − the instruction's tokens; `header`.
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
