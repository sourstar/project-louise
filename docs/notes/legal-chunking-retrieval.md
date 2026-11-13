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

**Version 2 (2026-11-08), from the first real records cut by v1.** Two
defects seen in the output of legge 306/2003 (Normattiva) and the NeuRIS
fixture:
- the article line was said twice, in the header and again as the body's
  first line ("… › Art. 1. — (Delega …)\nArt. 1. — (Delega …)\n1. Il
  Governo …"). v2 drops that first line from the piece when it only
  repeats the label's last segment; the record keeps it;
- the outline wrote every label's whole path, so "CAPO I — DISPOSIZIONI
  GENERALI …" stood before each of its articles. v2 writes a table of
  contents: each partition once, its articles indented under it.

Version 1 cuts as before (invariant 3). The three recipes state version 2.

**Version 3 (2026-11-08), from München's profile of normattiva-nemo's
records build.** The build of 341,959 records was on course for ~4.5 h, and
`py-spy` put 87–92 % of every pool child's time in `_count` under `_pack`.
v2 re-encoded the whole growing piece for every unit it added, which is
quadratic in a text's units.
- v3 counts each unit once and packs on the sum of the counts. It then holds
  each piece to the bound by one exact count, giving a unit back while that
  count is over.
- No piece is ever past the window, and the text is whole, as before.
- A piece may be a little shorter than v2's, where the sum over-counts a join.

Measured here with a byte-level BPE (a stand-in for Nemotron's tokenizer),
budget 2,000:

| text | v2 | v3 |
|---|---|---|
| an article of 399 commi | 2.33 M characters encoded, 0.81 s | 0.32 M, 0.13 s |
| an annex of 3,000 sentences | 34.4 M characters, 10.6 s | 1.38 M, 0.55 s |

The cut differs, so the layer's version and the space_id move:
- legi is re-made (she was to be re-made for yellow anyway);
- normattiva-nemo is made again from the new legi.

The recipes state version 3.

**Open: the Codice civile's hierarchy.** Its 3,280 articles arrive as
annexes named "CODICE CIVILE-art. N", with no partition. So their header
names the act (the record's title) and the article, without Libro or Titolo:

    REGIO DECRETO 16 marzo 1942, n. 262 — Approvazione del testo del Codice civile. › CODICE CIVILE-art. 2043
    Qualunque fatto doloso o colposo, che cagiona ad altri un danno ingiusto, …

The partition must come from the ORIGIN, or it does not enter a chunk: a
chunk is a pure function of the record (invariant 3), and the record
reproduces from the origin (invariant 11). München was asked on 2026-11-08
whether the member (annex names, annex headings, or partition elements) or
another form the spec fetches holds it. If it does, the parser carries it
forward as it carries `<chapter>`, and the header reads "… › LIBRO QUARTO —
Delle obbligazioni › TITOLO IX — Dei fatti illeciti › Art. 2043". That is a
parser version, so normattiva-nemo is then parsed, not carried. If it does
not, the header stays as above.

**Settled the same day (München, /root/measure/ccprobe.txt and nvapi/).
The origin holds no partition for the Codice civile, in any form, so the
header stays act + article.**

- The AKN member, `REGIO DECRETO_19420316_262/…_VIGENZA_2026-04-29_V347.xml`
  in M/codici.zip (base64 XML, 14.1 MB), has 3,280 attachments: 31
  "Disposizioni sulla legge in generale-art. N", then 3,249 "CODICE
  CIVILE-art. N".
- Each attachment is one flat article (paragraph, p, ins, ref). There is no
  book, chapter, part, section or hcontainer, and no num or heading outside
  the decree's two articles.
- The 12 mentions of LIBRO/CAPO are notes on repealed articles ("… a
  seguito della sostituzione del LIBRO V, TITOLO V, CAPO V …"): text about
  a partition, not the partition.
- No other file the node holds has the act.
- The open-data API (manual rev. 10, 10/03/2026) has no tree, index or
  sommario route. `dettaglio-atto-urn` (`…;262:2~art2043`) gives the article
  alone, and for legge 241/1990 it does not carry even the "CAPO I" that the
  AKN `<chapter>` gives. Its `idGruppo` is an opaque number with no label.

An outside table of the code's structure stays out of the chunks
(invariants 3 and 11).

**Option A tried, and closed (operator, 2026-11-08: "Take the partition
from Normattiva's own data, if it is there").** The open-data export was
run through Louise's own client (`tools/measure/normattiva_export_probe.py`,
AKN first, one job a format; München, /root/measure/nvexport2/), for the
acts of 16 March 1942. All five formats answered; none carries the code's
partitions:

| format | the Codice civile's member | partitions |
|---|---|---|
| AKN | `…_ORIGINALE_V0.xml`, 8.07 MB | none; one `title`, the decree's |
| XML (NIR 2.2) | `…_VIGENZA_2026-11-08_V0.xml`, 2.47 MB | one `capo`, the decree's own around its art. 1; the code is in `annessi`, flat |
| JSON | `…_ORIGINALE_V0.json`, 3.59 MB | none; two mentions inside article text |
| HTML | `…_ORIGINALE_V0.html`, 1.92 MB | none |
| URI | an 86-byte list | — |

The header of the Codice civile stays act + article.

An origin quirk, for whoever records a form one day: asked for the version
in force on 2026-11-08, AKN, JSON and HTML came back ORIGINALE_V0, and XML
and URI came back VIGENZA.

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

**Built (2026-11-12).** Each plugin's `default_facets`; a flat facet on
`extra.<key>` is read out of the record parts (app/ask/fieldvalues.py,
05 §5.7 amended), and `--records-from` admits a facets-only difference
(02 §2.7 amended): a collection re-created with them takes its records,
none parsed.

| plugin | status | act type | code / collection | year | subject |
|---|---|---|---|---|---|
| normattiva | `extra.in_force` (`false` only: a repeal state, the repealed-acts zip) | `extra.doc_type` | `extra.collection` (the zip) | `date` | — |
| legi | `extra.in_force` (ETAT: VIGUEUR `true`; ABROGE, ANNULE … `false`) | `extra.eli_type_document` (NATURE) | `extra.eli_title` | `date` (none on a code), `extra.version_from` | — |
| eurlex | `extra.in_force` (RESOURCE_LEGAL_IN-FORCE) | `extra.resource_type` | `extra.sector` (CELEX) | `date` | `subjects` (EuroVoc, directory codes, subject matters) |
| neuris | `extra.legal_force` (InForce, NotInForce, PartiallyInForce), `extra.in_force` | `extra.eli_type_document` (regtxt:typ) | `extra.abbreviation` (Jurabk) | `date` | — |

"Not yet in force" is no record field in any of the four: it is a date,
answered by `--as-of` over the validity index (app/validity.py). LEGI's
ETAT (VIGUEUR_DIFF, MODIFIE) is nested in `meta_article`, which 09 §9.3's
`extra.<key>` does not reach.

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

### 5.1 The chunker mode (2026-11-14, operator: "Build the chunker mode and send A")

The header was taken "by design" on 11-08 and never measured: the one comparison (DAR + `akn_article`
against Nemotron + `legal_article`) changed the encoder and the chunker together. A chunker setting is
inside `space_id`, so measuring arms as collections would mean a new space and its gate per arm.
`legal_pilot chunker-run` (tools/chunker_mode.py) measures the CUT instead:

- **Arms** are layers: `shipped` (the collection's own), `header-off`, `outline-off`, `akn`, or any layer
  as JSON. Each cuts the pool's records through `layers._chunker_of`, the stages' own path.
- **Vectors** come through the collection's served recipe, document and query instructions included,
  under ONE run per side: pinned before anything is embedded, validated for the space as a write's
  would be (`run_check.guard`), and an answer from another run stops the measurement. The cache is keyed
  by text, in a directory named by the recipe, the space and the two runs: a text two arms share is
  embedded once, and two engines never share a directory. Every arm is embedded FRESH by default, so
  the arms differ in the cut and nothing else (München's review of #621 and the legal session,
  2026-11-13: the stored vectors came from the run that embedded the collection, and their drift would
  read as the cut's effect). `--stored` lets the `shipped` arm take the collection's own vectors only
  where the stored text is byte-identical and a re-embedded sample gives them back by the space's rule
  (bytes in an exact space, the tolerance in a near one); seeds are never cached.
- **The cut reads what the embed stage reads**: `[title, sections]` from the collection's fresh record,
  the parse only where there is none (a stale record could otherwise give one chunk id two texts).
- **Readings**: `fts` (a throwaway index written by app/fts.py), `dense` (an exact scan, as deep in
  records as the text leg), `hybrid` (`retrieval._merge_rrf`) and `reranked` (`retrieval._reranked`,
  the collection's reranker). A rerank that falls back stops the run, and nothing is written.
- **Scoring** is `run`'s: (act, article) by the chunk's label, the gold by key. There is one file per arm
  and reading, and `chunker-report` compares every arm with the first, with Holm's correction and the
  smallest detectable difference.
- **Fairness**: every arm searches the same records (the pool, through `current_versions`) the same way
  (exact scan, one encoder run, one reranker). An arm with any chunk past the encoder's window is
  refused before anything is embedded (the operator's rule: no truncation). `--dry-run` cuts and counts
  every arm and embeds nothing.
- **Questions**: `--split validation` (179, the existing pool jf100), `train` (716) or `all` (895, a
  new pool built with `pool --split all`).

Arms for A: `shipped`, `header-off`, `akn`. THE DECISION RULE, STATED BEFORE THE RUN (München and the
legal session, 2026-11-13; the operator's choice, A unless said otherwise): one primary measure,
`reranked` MRR@10 (what production serves), and a margin δ = 0.02. For each arm against `shipped`,
d = arm − shipped per question, one-sided, Holm across the decided arms, from the percentile bootstrap:
**keep** the shipped cut when it is not worse than the arm by δ; **drop** it when the arm is better by δ;
otherwise **undecided** — on the 179 validation questions that means the same arms on all 895, and
undecided on all 895 drops the header (A). `chunker-report` prints the verdict (`--margin`, `--primary`,
`--decide`); every other metric and reading is descriptive. It refuses files of another measurement
(tag, code, runs, collection, seeding), and says on the page: the reranker reads the title twice in an
arm with the header (the head it attaches, and the header — as production does); the pool is the
shipped cut's text-leg top 100, so every arm meets the shipped cut's distractors and none of its own,
a bias toward the challengers (each arm's share of top-k records from the builder's list is shown,
for pools built from now on); no arm has the query language, the plan, translations or the catalogue
leg.

**Tonight's arms and the rule as applied (2026-11-13).** A fourth arm, `small` (operator: yes): the
shipped layer with `max_tokens` 2000 instead of 8175 and nothing else changed (header, outline,
tokenizer, version as shipped), so against `shipped` it measures the chunk size alone — about
`akn_article`'s 8,000 characters of Italian, Normattiva-DAR's granularity. Passed as a custom arm
(`--arm 'small={"chunker":"legal_article","version":3,"params":{…,"max_tokens":2000,…}}'`), no new
preset, so the code and its tag stay as merged. Holm over the three arms against `shipped`. The legal
session's reading, the same for every arm: the shipped cut stays unless an arm is BETTER by δ (`drop`:
that arm replaces it); `keep` otherwise; undecided goes to the 895 questions. A smaller unit costs
several times the vectors, so it must win, not tie.

### 5.2 The chunker per language (2026-11-13, operator: "Build the per-language test on LEMUR")

The operator's hypothesis: the best portioning follows structure and content, not language, so the
chunker that scores best with one embedder scores best in every language. Three things could still make
it depend on the language: a window counted in tokens (the same article costs more tokens in one
language than another, so a cap splits it in one and not the other), the embedder's strength per
language, and the text leg's analyzers.

`tools/chunker_languages.py` asks it where only the language changes:
- **Questions**: LEMUR's PARALLEL acts — those every language asked holds — so the acts, their
  structure and the queries' content are the same in every language.
- **Sections**: each body read by the eurlex plugin's own reader of a rendition without Formex
  (`rendition._sections`: an article heading in any of the 24 languages, number-first ones included).
  Per language, the articles found against the most any language found for the same act: a reader that
  misses headings in one language is said, not mistaken for the chunker.
- **Arms**: `structure` (legal_article, header and outline off, the collection's tokenizer and window),
  `structure-512` (the same at 512 tokens: the token cap's language effect), `window-512` (`fixed` over
  the body whole: no structure). No arm writes the act's title and the reranker judges chunks bare:
  LEMUR's query IS the act's header.
- **Everything else is the chunker mode's**: one validated encoder run per side, every arm fresh, app
  fts per language, exact dense scan, RRF, the collection's reranker; one collection's encoder for every
  language (`--collection`).
- **The verdict** (reranked MRR@10, stated before the run): for each arm against `structure`, each
  language's Δ with its interval; whether the Δ differs by language — a randomization test with the act
  as the block, Holm across the arms; and the best arm per language. The hypothesis stands where no
  arm's effect differs by language and every language has the same best arm.

LEMUR's caution stays: a title-shaped task (finding an act from its description). EUR-Lex-Sum asks the
same with content-shaped queries once its licence is acknowledged.

**The sets on the node (München, 2026-11-13, `/data/bakeoff-lemur-<code>`, test split, one row per
act, Apache-2.0):** it 225 acts, fr 224, de 226, en 227, fi 215, hu 192, el 100 (1.7 MB against ~10 MB
for the others). LEMUR splits per language, and older acts lack some languages (its card), so the
parallel set is the intersection the tool counts — Greek caps any set it is in at 100. The deciding
languages are it, fr, de and en, the slices the shared legal space embeds (the legal session); hu, fi
and el are a second set, for the window and the heading rules. If the four share well under ~150 acts,
the tool moves to LEMUR's `full` split before anything is embedded: no model here is trained on LEMUR,
so its test split protects nothing in this measurement.

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

## 8. Open (2026-11-12, from the architecture session's review of R1's follow-ups)

- **The Constitution is not held.** It is in none of Normattiva's 23
  pre-packaged collections ("Leggi costituzionali" holds the
  constitutional LAWS, not the Constitution; München: 0 records, no
  member), though held records cite `/akn/it/act/costituzione/stato/1947-12-27`.
  An acquisition gap: the API's export job (per act) is the route that
  would reach it, with its form recorded (invariant 11). Until then "art. 3
  Cost." maps to `urn:nir:stato:costituzione:1947-12-27` and pins nothing
  while not held.
- **Repealed articles outside Italy.** `article_repealed` exists per source
  (R1 (d)) and only Normattiva's detects ("ARTICOLO ABROGATO").
  PART FIXED 2026-11-13, for every source: a version shown with no date
  asked whose validity has ENDED (`valid_to` on or before today) is pinned
  as the last text in force ("dernier texte en vigueur"), never "in force"
  (`articles._ended`). That covers a LEGI article whose last version is
  ABROGE (its ETAT closes it: validity's end is its DATE_FIN), and an act
  Normattiva closes. NeuRIS FIXED 2026-11-14, from München's count over
  its 68,288 article sections (`refs/neuris.article_repealed`): the mark in
  the heading ("§§ 1 bis 4 — (weggefallen)", 1,059; glued
  "…(aufgehoben)") or as the whole body ("-" 1,014, "(weggefallen)" 397,
  "(aufgehoben)", "(Entfällt)", "—", "(gegenstandslos)", "(gestrichen)",
  "- - -", "(Durch Zeitablauf überholt)", "(ohne Inhalt)"); never
  "(Inkrafttreten)" (56), "(Änderungs-/Überleitungs-/Übergangsvorschrift)",
  "...", or a bare range heading (226), which are not removals. NeuRIS
  holds one expression per work, so a removed provision pins its current
  text (the mark) and no older one. STILL OPEN: EUR-Lex, measured the same
  way after its build.
- **An annex code's amendments (fixed 2026-11-12).** The older exports
  write a code's destinations as `urn:…;262#CODICE CIVILE-art. 2441`,
  `#Allegato 1 Codice del processo amministrativo-art. 120`, `#Norme-art.
  227`; the index stored `codicecivile-art2441`, which no citation's token
  meets (the node: c.c. 132 of 136 rows, disp. att. c.p.p. 35 of 35; in
  records c.c. 8,921 of 9,213 destinations, c.p.a. 575 of 655, disp. att.
  c.p.p. 2,680 of 3,364). The `article` column now holds ONE token,
  `<annex>|<token>` (`CODICE CIVILE|2441`; the annex whitespace normalised,
  case as held — München found no case-only variant), the bare token where
  the destination names no annex (normattiva `split_part`, validity
  `stored_article`); the table's key and schema are unchanged. A citation of
  an annex code lists its annex's statements, never another annex's
  article of the same number (the preleggi's art. 12 is not "art. 12 c.c.").
  THE FINAL RULE for a statement on an article that names NO annex (a bare
  `#1`, an AKN `~art_N`; the node: 12 rows, 3 on c.c. `#1`, 9 on c.p.a.
  `~art_1__para_1`): it is held to the candidates the held version's own
  labels show for that number (`articles._holders`) — one candidate: listed
  there, unmarked; several: listed on EVERY candidate's fact, the decree's
  own included, marked "la fonte non indica di quale testo"; the cited text
  not among them: not listed. Lost nowhere, claimed nowhere (invariant 11).
  On c.p.a. `art. 1` the decree and Allegati 1-4 each hold one, so all five
  are candidates by number alone: meant, since the origin does not say
  which. A statement on the whole act is an act, not an article, and stays.
  Every manifest re-derives the table once from its held records
  (`validity.AMENDMENTS_VERSION` 2, now 3, worker `_rederive_amendments`, the
  parse pool, no parse); a part that cannot be read is logged once, and
  only versions still holding an old labelled row (`article LIKE '%-art%'`)
  that no part rewrote are re-parsed by their rdf stage.
  Version 3 (2026-11-12, München's acceptance: 433 rows of 62 held, in-scope
  versions still in the old form on both manifests): an annex's article may
  be a WORD — `Allegato B-art. XI` → `Allegato B|xi`, `Regolamento-articolo
  transitorio` → `Regolamento|transitorio`, `unico`, `finale`,
  `addizionale` — taken only after `art.`, `articolo` as a word, or `art`
  and a space (an annex named `…-artigianato` is not split). From version
  2 the parts are read and ONLY the versions still holding an old-form row
  are rewritten (RULE 1: every other row is version 3's byte for byte).
- **An article cited in an annex, or named by a word (fixed 2026-11-12).**
  "art. XI dell'allegato B del r.d. 9 novembre 1861, n. 327" found the act
  and dropped both: the scan's `_ART` takes only "art. N" right before the
  act, and an article that is a word was no token. Now, where the scan
  found an act without its article, the pin reads back from the act
  (`normattiva.article_before`, at most 120 characters): "art. N" or a
  word article (a Roman numeral upper case, unico, transitorio, finale,
  addizionale), an optional "comma N", an optional annex — "[dell']allegato
  X", or a "regolamento", "statuto" or "tabella" with or without its number
  or letter (München's sample: `regolamento-artaddizionale`) — then
  del/della/al/alla or "approvato con", "annesso al", "allegata al". The scan itself is unchanged (the text index's
  hot path and its byte-for-byte reference). The cited annex ("allegato B")
  is resolved against the held version's own labels: every annex whose
  words begin with the cited ones, case folded (`articles.names_annex`);
  the one named EXACTLY wins where held ("regolamento" is "Regolamento", not
  also "Regolamento 2°"); none held, nothing is pinned — never the decree's
  own art. N in its place. Still not read: lower-case Roman numerals ("art.
  xi": kept out so a word is never an article) and an annex cited with no
  act ("art. XI dell'allegato B" alone).
  The resolved names key the amendments (`<annex>|<token>`), and the fact's
  id carries the annex (`…~allegatob~art3`). A short citation without a
  date takes the resolved `atto` fact by year and number. Chunk labels read
  word articles too (`label_token`: "Articolo unico" → `unico`).

- **Dead old-form rows.** The re-derivation rewrites every version a held
  part carries. An errored or out-of-scope version keeps its old labelled
  rows (`…-art…`); no citation's token matches them, so they show in no
  fact, and they go when the version is next parsed.

## 9. Context in chunks, beyond legal_article (operator and architecture session, 2026-11-14)

The operator: "check if the chunker we use has this kind of functionality,
if it can be added, and if there are other similar functionalities that may
improve specific use cases". Of the eight chunkers, only `legal_article`
writes context into its chunks (`header`, `outline`). The architecture
session's answer:

- **How: one shared module** (`app/chunking_context.py`: `header_of`, `toc`,
  a budgeted cut), called by each chunker that opts in, in its own unit and
  with its own split. The header is counted inside the window. The params are
  `header` and `outline` in the layer, inside `space_id`; an absent param gives
  today's bytes (invariant 3), with a test that every pinned space id is
  unchanged. legal_article v1–v3 keep their own code (golden-tested). `book`
  cannot take it (its window comes from the environment, grandfathered; its
  parameterised twin is `fixed`).
- **In the chunker contract, opt-in:** `header`, `outline`, and a table-aware
  split where the parser keeps tables (Formex, JATS). Neighbour headings come
  only after `header` is measured.
- **Elsewhere:** parent–child is a sister with a smaller `max_tokens` plus a
  results feature (05: fetch the article by the hit's label). Definitions are
  recorded by the PARSER (§9.5 `extra`), and the chunker may cite them later.
- **Refused in the chunker:** identifiers in the embedded text (noise in a
  vector; ids go to the key pin and the text index); late chunking (a
  space-level design: token embeddings, a VeGa receipt per pooled span);
  doc2query and RAPTOR (LLM output goes in an annotation of one collection;
  invariant 4 if it ranks).

**Measured before the legal full embedding** (the chunker is inside space_id).
One factor at a time, in order: header, then smaller units, then
definitions. The first: Normattiva (header on) against a sister with
`header = false` (outline as it is), the sister embedding only the jf100
pool. Both are searched restricted to the pool and with every vector query
a flat scan (`legal_pilot run --exact`), so they share their records, their
distractors and their search path. Each leg is reported apart (`--legs
dense|fts|hybrid`), then reranked; with the pool's chunk count in each arm,
and the smallest difference the test could detect (`compare`: 2.80·sd/√n).
