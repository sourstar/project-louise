# legal_article version 4, and what moving the shared legal space to it takes (2026-11-21)

**Status:** v4 is BUILT (app/chunking_legal.py, tests/test_chunking_legal_v4.py) and
cuts nothing on the node: `CHUNK_VERSION` and `SHARED_LAYER` stay 3, so every layer
cuts as before. The measurement is built (tools/measure/legal_chunker_drift.py) and
has not run on the node. The migration below is PROPOSED, not built. Moving the space
is the operator's decision (P8 of docs/notes/eurlex-review-2026-11-21.md, on branch
`ccr-bbc107f7-4at6fd`).

## 1. The defect

`legal_article` cuts an article past the window at its paragraphs, then its points,
then its sentences, then token windows (legal-chunking-retrieval.md §3). v3's sentence
level and point level knew four languages:

- **A sentence start was a Latin-1 capital** (`[A-ZÀ-ÖØ-Þ]`). A paragraph past the
  window in el, bg, cs, sk, sl, hr, pl, hu, ro, lt, lv or mt was not split at its
  sentences. It fell to `_windows`, which cuts mid-sentence. The same is true of any
  sentence that opens with Č, Š, Ž, Ł, Ő, Ș, Ț, Ż and so on.
- **The abbreviations were it/de/fr/en only.** The lookahead also takes a digit, so
  v3 cut "άρθ. | 5", "αριθ. | 1093/2010", "čl. | 5", "Úř. věst. | L 119", "s. | 1".
  It made the same cut in German and Italian, on forms its list lacked. The fixtures
  show two: NeuRIS's "BGBl. | I 2020" and Normattiva's "Il D.Lgs. | 2 gennaio 2018".
- **A point was an ASCII letter.** The Greek and Bulgarian marks, "α)", "(β)", "а)",
  were never points, so those paragraphs went straight to their sentences.

What v3 makes of a Greek text (from the test):

    Τα κράτη μέλη εφαρμόζουν το άρθ. | 5 παρ. | 2 του κανονισμού (ΕΕ) αριθ. |
    1093/2010. Η Επιτροπή ενημερώνεται χωρίς καθυστέρηση. Βλ. επίσης σ. | 12 …

**Where it bites:** only a section or an outline past the window (8,175 tokens)
reaches the point or sentence level. In the samples of legal-chunking-retrieval.md §1,
0.38–0.44 % of articles are past it. EUR-Lex's largest acts are much longer: 32018R0688
el has 54.3 M characters. The drift tool (§3) counts the real ones.

## 2. What v4 changes

| level | v3 | v4 |
|---|---|---|
| sentence start | Latin-1 capitals | `CAPITALS_V4`: every Lu/Lt letter of Basic Latin, Latin-1, Latin Extended-A and -B, Greek (not the Coptic letters) and Cyrillic. That is 403 letters, a frozen literal, sha256 `e8fe079e…5119af0`. It is UAX #29's `Upper` over those blocks, written out once from Python 3.12's tables (3.13 gives the same letters) and never read from the interpreter again |
| no cut after | it/de/fr/en forms | also the 24 languages' forms for article, paragraph, subparagraph, point, letter, number, page, the Official Journal, "see", "e.g." and "i.e.". It also refuses a cut after an initial in a new capital. Six short forms that are also units ("s" for seconds, "st", "т", "σ", "ν", "l") refuse only before a number |
| point | `a)`, `(a)`, `1°`, `1)` | also `α)`, `στ)`, `(β)`, `а)`, `(б)` |
| paragraph, windows | — | unchanged |

**The abbreviations follow the Interinstitutional Style Guide's conventions** (how each
language cites the OJ, and how it refers to an act's subdivisions), written down from
memory. style-guide.europa.eu does not resolve from the agent's network. Each form says
what it stands for, so someone with the Guide can check it before the space moves.

Left out on purpose:
- forms that end a sentence as often as they continue one: "etc." and its counterparts,
  the year (r., г., g., m.), "lpp."/"o." after a page number;
- forms that are words elsewhere ("ill", "mm", "ns", "oz", "all").

Estonian writes its abbreviations without a full stop, so it has none.

**v4 is identical to v3 by construction.** It cuts a text differently from v3 only where
the text holds a new capital, a new abbreviation or a Greek or Cyrillic point mark:
- its sentence candidates are v3's, plus those before a new capital;
- each candidate is decided by the text around it alone;
- it refuses every candidate v3 refused, using v3's own pattern, read with `pos`/`endpos`
  and a bounded look back. That is the same decision as v3's slice, and the test checks
  it candidate by candidate;
- its point marks are v3's plus the new ones.

The tests:
- v1, v2 and v3 give exactly the digests they gave before v4. Each digest covers the id,
  chapter and text of 11,748–11,774 chunks: the four plugins' real fixtures at five
  windows.
- On the it/fr/de/en fixtures, v4's chunk TEXTS equal v3's for every document at the
  shared window (8,175 tokens) and at 256. The ids differ only by `:4`.
- At forced windows of 64, 40 and 32 tokens, nearly every paragraph is cut into
  sentences. One document moves, at a new abbreviation: legge 306/2003's "Il D.Lgs.".
  NeuRIS's "BGBl." moves a sentence but no chunk.
- Every fixture text that holds nothing new has v3's sentences. So do 300 texts built
  from fixture sentences, at every level.
- Golden sentences for el, bg, cs, pl, hu, ro, lt, lv, mt, sk, sl, hr, et, fi and ga
  split at their ends and never after an abbreviation. They were written for the test,
  because no fixture holds a body in those languages.

**What v4 leaves as v3 had it.** Neither a capital nor an abbreviation decides these, so
by its own rule v4 does not touch them:
- an ordinal before a capital ("am 1. Januar", "članak 5. Uredbe") is still a cut;
- the lookahead still takes a digit;
- sentences packed into one piece are still joined by a space.

The standard that would decide ordinals is UAX #29 sentence breaks with CLDR's per-locale
suppressions, through ICU's BreakIterator via PyICU. The trained alternative is Punkt.
Either one is a new dependency whose data moves with each release, so its version would
have to enter `space_id`. It would pay if the drift tool, run on v4, still finds
mid-sentence cuts in numbers that matter.

## 3. Measure first (read-only, on the node)

```bash
for C in Normattiva legi_F NeuRIS5 EUR-Lex5; do
  python -m tools.measure.legal_chunker_drift $C --limit 5000 --out /root/measure/v4-drift-$C-pilot.json   # a pilot window
  python -m tools.measure.legal_chunker_drift $C --out /root/measure/v4-drift-$C.json
done
```

For each language, the tool reports:
- the documents and their chunks;
- the documents whose chunk texts differ;
- **to embed:** the v4 chunks whose text the document does not already hold. This is
  the whole encoder cost of the migration;
- **dropped:** the v3 chunks that v4 no longer cuts;
- the chunks that fell to `_windows` at v3 and at v4;
- a few examples.

It runs on the parse pool at the profile's cores. Run it in its own container with a
memory cap: a child meets the worker's peaks on EUR-Lex5's largest units.

Expected: "to embed" near 0 for Normattiva, legi_F and NeuRIS5, where only the new
it/fr/de forms in paragraphs past the window move a cut. For EUR-Lex5 it is the
paragraphs past the window in the other 20 languages.

## 4. What moving the shared space to v4 takes

### 4.1 What moves

| what | why it moves | source |
|---|---|---|
| `space_id` | the layers, with their chunker version, are inside it. So the four collections of D3 move together from S3 (`8ddb1bf5…`) to S4. One left behind splits the single ranked list | 09 §9.3, `Codex._space_id_of` |
| `collection_id` | the layer is in the Codex, so there are four new Codexes | 09 §9.3 |
| every chunk id | `<uid>:<seq>:<chunker version>` changes, even where the text is the same | 06 §6.3, 09 §9.5 |
| every part's `chunks_root` (and `chunks` where a cut moved) | SHA-256 over each chunk's id and BLAKE2b-128 of its text | 09 §9.4 |
| the vector table | its rows are keyed on `id`. A table pinned to S3 is a refuse-to-start under an S4 Codex | invariant 1, embeddings schema |
| the text index | it stores `chunk_id` (app/fts.py). Tantivy cannot change a stored field in place: an update deletes and re-adds, which tokenizes again | |
| bundles, coverage rows, `chunk_verification`, attestations | they name the old ids, collection and space | 09 §9.7, §9.10 |
| gate statements | each is signed for one `space_id` (app/hold/runs.py), so none speaks for S4 | 03 §3.3 |
| claims that name a chunk | `mention` and `evidence_chunk` claims are `{uid, chunk_id, text_hash}`. They are re-matched by `(uid, text_hash)`. Which annotations collections name these four is owed from the node | 09 §9.5 |

### 4.2 What RULE 1 lets it carry

| what | carried because |
|---|---|
| the records and the part files | a record line carries no layer, so `hold records build <to> --records-from <from>` gives the same bytes and the same `root` and `records_root`. Only `chunks` and `chunks_root` are cut again, on the pool, with no fetch and no parse (02 §2.7, the relayered sister) |
| the manifest's documents (discovery, catalogue, validity, keys, facets) | they are functions of the records, as in A1i |
| **every vector whose text v4 cuts again** | S3 and S4 differ by their layers alone. `space_differences` is empty: the same weights, dim, dtype, ctx, cut, pooling, normalisation, both instructions and epoch. A space is a function from cleaned text to vectors (02 §2.4), so the same text has the same vector. That is the rule 06 §6.3 already applies within a space, by chunk hash, across records in a versioned collection |
| the CPU reference, the calibration member | they are functions of the encoder side and the calibration set, both equal |
| the licence acknowledgement | the terms text is the same (`recreate.carry_ack`) |

Not carried:
- the text index (it stores the old ids);
- the chunk-hash holder table (it names the old ids);
- bundles and their evidence;
- the gate's statements: the gate runs once for S4, and its four collections share that
  run (`statements_for_space`).

### 4.3 The mechanism (proposed, not built)

This is a new case of new-collection.md: **F on a chunker version.** It is a
re-creation (case F, `<name>.next`) whose Codex differs from its predecessor's only in
its layers' chunker VERSION: the same chunker id, params, rule and input, and an empty
`space_differences`. It is declared in app/hold/workflow.py, and it uses the existing
steps except one new carry.

1. **Code.** `CHUNK_VERSION = 4`, so `SHARED_LAYER` and the three recipes state v4 (the
   recipe tests hold them byte for byte). `_carry_cpu_reference` carries across spaces
   whose `space_differences` is empty and whose calibration set is the same, not only
   within one `space_id`.
2. `hold collection new <plugin> --name <name> --re-create`, at v4.
3. `hold records build <name>.next --records-from <name>`, then `hold snapshot create`.
4. **New: `dev carry <name> <name>.next --rechunked` (amendment A1k; A1j is the
   origin-run pin of 07 §7.3).**
   - It refuses any other difference.
   - It copies the manifest and empties the index stage's marks (`RELAYERED_MARKS`), so
     the worker indexes each document again.
   - It sets `embed_done` 1 → 0 and keeps `embed_version`, so `may_reuse` holds.
   - It empties `SISTER_EMPTIED` (the chunk-hash holders, `chunk_verification`, bundles,
     queues) and the control rows that speak of S3.
   - It copies the vector table and re-pins it to S4.
5. **The gate, once for S4,** on the node's Nemotron run.
6. **The worker.**
   - The index stage cuts and indexes each document at v4, on CPU.
   - The embed stage re-keys each document through the existing code. In
     `upsert_book` no stored id matches a v4 id, so the document is rebuilt. Every
     chunk whose text hash the document (or, in these versioned collections, another
     version of it) already holds keeps that vector under its new id. Only the drift
     tool's "to embed" chunks reach the encoder.
7. **Before use.**
   - 0 rows whose id does not end in `:4`.
   - `hold check <name>.next --checks vectors-sample`.
   - The chunks check against the snapshot's `chunks_root`.
8. **The swap** (case F). The predecessor is forgotten on the operator's yes.
9. **Peers** (test peers, RULE 1).
   - They fetch the new snapshot. The record parts have the same roots, so nothing is
     downloaded again.
   - The bundles are exported again by the node and fetched. Alternatively, a peer runs
     the same carry over the vectors it holds: 06 §6.3's map is local.

**Published? None of the four** (München, from the node's own records, read-only,
2026-11-21). Normattiva, legi_F and NeuRIS hold one signed snapshot each (2026-10, written
11-13 to 11-16); EUR-Lex5 holds none. None of the 124 published attestations names any
of the four, and their manifests hold no bundles, bundle evidence, genesis imports or
providers. The two peers last made contact on 10-21 and 10-25, before any of the snapshots.
RULE 1 therefore re-creates all four; no sister is needed.

**By the book** each of the four would instead be parsed, cut, indexed and embedded in
full. Normattiva's ~8.2 M chunks alone, at ~30 chunks/s, take about 3 card-days
(STATUS 2026-11-10; the 8.2 M is the akn_article count of legal-chunking-retrieval.md
§7, so the real count is the drift tool's).

### 4.4 Decisions

**M1 — when the four move.** This moves the shared legal space to a new space at v4.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. All four in one window, EUR-Lex5 at its restart (recommended)** | the four leave S3 together | one list throughout; EUR-Lex5 is being derived again anyway (review, "The restart"); every identical vector is carried | a CPU pass over all four (records cut, re-index, re-key) and one gate |
| B. EUR-Lex5 alone now, the three later | EUR-Lex5 in S4, the three in S3 | the languages that need it first | two lists until the three move; the national collections' cross-list search is broken meanwhile |
| C. Status quo (v3) | nothing moves | no work | el/bg/cs/… paragraphs past the window cut mid-sentence; "αριθ. \| 1093" and "BGBl. \| I" cuts stay |

Recommendation: A. If the operator says nothing, nothing moves until the drift numbers
(§3) are in, and then A.

**M2 — the re-key.** This is how the four vector tables take their v4 ids.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. The embed stage's carry by chunk hash (recommended)** | each document's rows are deleted and re-added under the new ids | existing code (06 §6.3, `upsert_book`); exact for changed documents too | one Lance delete and add per document, as fast as the embed stage writes |
| B. One Lance `update` of `id` for the documents the drift found identical, the embed stage for the rest | one rewrite of the table's fragments | probably faster on millions of documents | new code and a second path; needs the drift's per-document verdict kept |

Recommendation: A, timed on a pilot of one window. B is used only if the comparison of
finish times favours it (CLAUDE.md: take the shorter). If the operator says nothing: A.

**M3 — the gate for S4.** This is the validation of the Nemotron run for the new space id.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. Run the gate once (recommended)** | one statement for S4, shared by the four | measured, nothing assumed (invariant 12); no new rule | one gate run of the card's time |
| B. Admit S3's statements in S4 | no run | no card time | a new trust rule for one gate run; a statement would speak for a space it never measured |

Recommendation: A. If the operator says nothing: A.

## 5. Conclusion

Moving the shared space to v4 replaces S3 with a new space S4 and re-creates the four
collections. Under RULE 1, everything carries except what names a chunk id: the records
and part files, the manifests, every vector whose text v4 cuts again, the CPU reference
and the calibration. The encoder's cost is exactly the drift tool's "to embed" count.
The rest is CPU:
- `chunks_root` per part;
- a re-index (the text index stores the ids);
- a re-key of every vector row;
- one gate run.

What is still to build: the `--rechunked` carry (A1k), the relaxed CPU-reference carry,
the case's declaration, and `CHUNK_VERSION = 4`. Each comes with its tests, and all of
it waits for the drift numbers and the operator's go.
