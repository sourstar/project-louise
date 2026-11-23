# legal_article version 4, and what moving the shared legal space to it takes (2026-11-21)

**Status:** v4 is BUILT (app/chunking_legal.py, tests/test_chunking_legal_v4.py) and
cuts nothing on the node: `CHUNK_VERSION` and `SHARED_LAYER` stay 3, so every layer
cuts as before. Its abbreviations are CHECKED against the Style Guide and real EUR-Lex
text in the 24 languages and corrected (2026-11-21,
[legal-chunker-v4-abbreviations.md](legal-chunker-v4-abbreviations.md)). The measurement is built (tools/measure/legal_chunker_drift.py) and
has not run on the node. The carry the migration needs, `dev carry --rechunked`
(amendment A1k, §4.3 step 4, §4.5), is BUILT and tested on fixtures (2026-11-21), never
run on a node; it is a proposal for the architecture session. The creation side (§4.6)
and the case's declaration are PROPOSED, not built. Moving the space is the operator's
decision (P8 of docs/notes/eurlex-review-2026-11-21.md, on branch `ccr-bbc107f7-4at6fd`).

## 1. The defect

`legal_article` cuts an article past the window at its paragraphs, then its points,
then its sentences, then token windows (legal-chunking-retrieval.md §3). v3's sentence
level and point level knew four languages:

- **A sentence start was a Latin-1 capital** (`[A-ZÀ-ÖØ-Þ]`). A paragraph past the
  window in el, bg, cs, sk, sl, hr, pl, hu, ro, lt, lv or mt was not split at its
  sentences. It fell to `_windows`, which cuts mid-sentence. The same is true of any
  sentence that opens with Č, Š, Ž, Ł, Ő, Ș, Ț, Ż and so on.
- **The abbreviations were it/de/fr/en only.** The lookahead also takes a digit, so
  v3 cut "αριθ. | 45/2001", "čl. | 8 odst. | 1", "stk. | 1", "Úř. věst. | L 8", "s. | 1".
  It made the same cut in German and Italian, on forms its list lacked. The fixtures
  show two: NeuRIS's "BGBl. | I 2020" and Normattiva's "Il D.Lgs. | 2 gennaio 2018".
- **A point was an ASCII letter.** The Greek and Bulgarian marks, "α)", "(β)", "а)",
  were never points, so those paragraphs went straight to their sentences.

What v3 makes of the GDPR's Greek footnote 6 and Bulgarian recital 1 (from the test):

    (6) Κανονισμός (ΕΚ) αριθ. | 45/2001 του Ευρωπαϊκού Κοινοβουλίου … (ΕΕ L 8 της
    12.1.2001, σ. | 1).
    … лични данни е основно право. Член 8, параграф 1 …       (no cut before "Член")

**Where it bites:** only a section or an outline past the window (8,175 tokens)
reaches the point or sentence level. In the samples of legal-chunking-retrieval.md §1,
0.38–0.44 % of articles are past it. EUR-Lex's largest acts are much longer: 32018R0688
el has 54.3 M characters. The drift tool (§3) counts the real ones.

## 2. What v4 changes

| level | v3 | v4 |
|---|---|---|
| sentence start | Latin-1 capitals | `CAPITALS_V4`: every Lu/Lt letter of Basic Latin, Latin-1, Latin Extended-A and -B, Greek (not the Coptic letters) and Cyrillic. That is 403 letters, a frozen literal, sha256 `e8fe079e…5119af0`. It is UAX #29's `Upper` over those blocks, written out once from Python 3.12's tables (3.13 gives the same letters) and never read from the interpreter again |
| no cut after | it/de/fr/en forms | also the forms the 24 languages' legal text writes for article, paragraph, number, page, the Official Journal, "see", "e.g." and "i.e." (101 in all, each checked). It also refuses a cut after an initial in a new capital. Seven short forms that are also units or words ("s" for seconds, "т", "σ", "vol", "cap", "kap", "Kap") refuse only before a number |
| point | `a)`, `(a)`, `1°`, `1)` | also `α)`, `στ)`, `(β)`, `а)`, `(б)` |
| paragraph, windows | — | unchanged |

**The abbreviations are checked** (2026-11-21) against the Interinstitutional Style
Guide in each language (§ 3.1 the OJ, § 3.2 references to acts and their subdivisions,
Part Four, Annex A3) and against 143 Cellar items in the 24 languages. The first lists
were written from memory, when style-guide.europa.eu did not resolve from the agent's
network: 108 of their 210 forms left them (nine for the before-number list), most because EU acts write the subdivisions in
full (every language but cs, sk, pl and da; the Greek Guide forbids abbreviating them),
some because they are wrong ("pkt." is Danish "punktum", Polish "nr" takes no full stop,
"i" ends Danish sentences); 17 are added. Each form, its verdict and its source:
[legal-chunker-v4-abbreviations.md](legal-chunker-v4-abbreviations.md).

Left out on purpose:
- forms that end a sentence as often as they continue one: "etc." and "et seq." and their
  counterparts, "lpp."/"o." after a page number. The dates were measured: the Polish year
  "r." and the Lithuanian day "d." continue their sentence in 18 of 21 and 81 of 84
  cases, so they are in; the Bulgarian year "г." (the gram) and the Lithuanian year "m."
  (the metre) are out;
- forms that are words elsewhere ("sal", "por", "i", "ill", "mm", "ns", "oz", "all").

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
- Golden sentences in all 24 languages split at their ends and never after an
  abbreviation. They are real: paragraphs of 27 Cellar items trimmed to whole lines
  (tests/fixtures/eurlex/sentences/), read by the plugin's rendition reader, each
  checked verbatim against its fixture and against v3's cut.

**An ordinal its next word continues is no cut** (added 2026-11-21, measured, because
the space moves once and a v5 would move it again). On 32016R0679 and 32013R0575 from
Cellar, a number's full stop before a capital was:
- in German, ~470 of 488 times a date's day ("vom 8. Dezember 1986");
- in Croatian, ~240 of 388 times an article's or paragraph's number before its act ("člankom 81.
  Uredbe", "članka 114. UFEU-a");
- in Slovenian, never (96 of 96 are sentence ends: "iz člena 8. V primeru").

v4 refuses the cut before those words only (`AFTER_ORDINAL_V4`: the German months; the
Croatian act nouns in the cases that follow a number, never the nominative a sentence
opens with; "UFEU-", "UEU-"). Refusing a cut only joins two sentences, while cutting one
breaks it. Every other ordinal before a capital is a cut as in v3 ("stavka 3. Komisija",
a true start).

**What v4 leaves as v3 had it.** Neither a capital nor an abbreviation decides these, so
by its own rule v4 does not touch them:
- an ordinal before any other capital is still a cut;
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
- **dropped:** the v3 chunks whose text v4 no longer cuts;
- the chunks that fell to `_windows` at v3 and at v4;
- a few examples.

It runs on the parse pool at the profile's cores. Run it in its own container with a
memory cap: a child meets the worker's peaks on EUR-Lex5's largest units.

Both counts go by the texts a document holds, not by how many times it holds them
(fixed 2026-11-21): the tool first subtracted multisets, so a text v4 cuts twice where v3
cut it once counted as one to embed, while the carry by chunk hash keeps it. The
carry's dry run (§4.5) gives the same numbers on a fully embedded source
(tests/hold/test_rechunk.py).

Expected: "to embed" near 0 for Normattiva, legi_F and NeuRIS5, where only the new
it/fr/de forms in paragraphs past the window move a cut. For EUR-Lex5 it is the
paragraphs past the window in the other 20 languages.

**Measured** (München, 2026-11-22, tool at 550c4be9, read-only, 8g cap, 4 processes;
errors 0; JSONs in `/root/measure/chunker-v4/` on the node):

| collection | documents | chunks v3 → v4 | documents differ | to embed | dropped | windowed v3 → v4 (documents) | wall | peak |
|---|---|---|---|---|---|---|---|---|
| Normattiva | 342,082 | 7,958,578 → 7,958,446 | 324 | 1,133 | 1,265 | 13,012 → 13,054 (2,982 → 3,003) | 4,546 s | 4.1 GB |
| legi_F | 2,393,707 | 2,708,261 → 2,708,261 | 55 | 87 | 87 | 5,238 → 5,238 | 1,474 s | 2.7 GB |
| NeuRIS | 5,536 | 82,670 → 82,678 | 35 | 142 | 134 | 203 → 222 (40 → 42) | 83 s | 0.7 GB |

The three collections in service cost **1,362 chunks** of encoder time. Every other
vector carries under its new id. legi_F skipped 75 documents that are out of scope
or errored, as its stages do.

Normattiva and NeuRIS fall back to token windows a little more often at v4
(+42 and +19 chunks). A cut v3 made inside a sentence ("D.Lgs. | 2 gennaio", "vom
18. | Dezember") joins it again. Where the joined sentence passes the 8,175-token
window, it is windowed: in either version it is a run of text with no sentence end
in reach, so this is no regression.

EUR-Lex5's TOML is out of service (the operator: "make it not even searchable"),
so its count runs with `--toml` (35f303c7): pilot first, then whole.

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
- the text index (it stores the old ids); its catalogue (`index/_catalog`, by `rowid`) is;
- the chunk-hash holder table (it names the old ids): the re-key records the new ids of
  every document it completes, as the embed stage would;
- bundles and their evidence;
- the gate's statements: the gate runs once for S4, and its four collections share that
  run (`statements_for_space`).

### 4.3 The mechanism (the carry BUILT, the rest proposed)

This is a new case of new-collection.md: **F on a chunker version.** It is a
re-creation (case F, `<name>.next`) whose Codex differs from its predecessor's only in
its layers' chunker VERSION: the same chunker id, params, rule and input, and an empty
`space_differences`. It is to be declared in app/hold/workflow.py (not built), and it
uses the existing steps except one new carry (built).

1. **Code.** `CHUNK_VERSION = 4`, so `SHARED_LAYER` and the three recipes state v4 (the
   recipe tests hold them byte for byte): at the move, not before (§4.6).
   `_carry_cpu_reference` carries across spaces whose `space_differences` is empty, whose
   layers differ by their version alone and whose calibration set is the same, not only
   within one `space_id`: BUILT (A1k only; every other pair as before).
2. `hold collection new <plugin> --name <name> --re-create`, at v4 (§4.6: the workflow
   refuses it today).
3. `hold records build <name>.next --records-from <name>`, then `hold snapshot create`.
4. **`dev carry <name> <name>.next --rechunked` (amendment A1k, BUILT 2026-11-21; A1j is
   the origin-run pin of 07 §7.3).** app/hold/carry.py (the refusals and the manifest),
   app/hold/rechunk.py (the re-key), tests/hold/test_rechunk.py.
   - It refuses, writing nothing: any record difference but the layers (and the facets);
     layers that differ in more than their version, or not at all; any
     `space_differences` (weights, dim, dtype, ctx, cut, pooling, normalisation, both
     instructions, the query encoder, the epoch, the cleaning): nothing is crossed with
     it; a target that is not empty; a source table pinned to another space or width;
     a caller that does not hold BOTH write locks (the lock files name this process).
     The checks are carry.py's own, extended, never a copy.
   - It copies the manifest (the source keeps everything) and resets it as a relayered
     sister's (`_sister_reset`, A1i): `embed_done` 1 → 0 and every `embed_version` 0
     (nothing is held yet), every index mark and count (`RELAYERED_MARKS`), the
     chunk-hash holders, `chunk_verification`, the queues, the bundles, the gate's and
     the share paths' control rows, the validity sync marks. It copies `index/_catalog`
     (a function of the same rows by `rowid`) and no other index file, the CPU
     reference, the scan cursors and the collection file (embed "off"), and re-pins the
     space to S4. Run again after it finished, it changes nothing: its reset would undo
     the re-key's marks.
   - **The re-key** walks the source's documents by rowid in windows from a cursor. Each
     window is a task of the parse pool at the profile's cores. A child cuts every
     in-scope document at the TARGET's layers as the stages cut it (parallel.py's
     "chunks" kind) and returns each chunk's id, seq, chapter and BLAKE2b-128, never its
     text. Then, a group of at most 8,192 new chunks at a time, the source's rows of the
     group are read by `book_id` (`held_chunks`). A new chunk whose text hash is the
     hash of a row the same document holds (never by position) takes that row's vector
     bytes, its text and the record's `source_hash`, under its new id, seq and chapter,
     with the target's validity columns. Only the rows of a document whose vectors the
     embed stage would keep are read (`may_reuse`). The rows go through the embed
     stage's own batch writer (`write_pending`).
   - Then, in one BEGIN IMMEDIATE after the window's rows are durable: each document
     whose every chunk carried and that was embedded in the source is marked embedded
     (`mark_embedded`, its validity columns synced, the chunk hashes recorded in a
     versioned collection, as the embed stage does), then the counts and the cursor.
     Every other document stays not embedded at version 0. The embed stage keeps its
     carried rows by (id, text) and embeds the rest (tested).
   - Invariant 2: a window's range is committed before its first write. A stop or a kill
     after a write and before the cursor leaves it: the next run deletes what the
     window's documents hold in the target table (by `book_id`), then cuts the window
     again. SIGTERM is honoured at a window boundary (exit 130). At the end the table
     must hold exactly the rows carried, or `CarryMismatch` (exit 1).
5. **The gate, once for S4,** on the node's Nemotron run.
6. **The worker.**
   - The index stage cuts and indexes each document at v4, on CPU.
   - The embed stage embeds the documents the re-key left not embedded. In `upsert_book`
     every carried row matches its v4 id and text and is kept. Only the "to embed"
     chunks reach the encoder, fewer where a versioned collection carries a text from
     another version of the Work (`cross_carry`, 06 §6.3).
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

**Decided (operator, 2026-11-23): "A"** — all four in one window, before EUR-Lex5's
restart (whose order is the review note's "The restart": M1, retire-superseded, the
re-forms, the problem slice, the rest, serving). Drift measured on the node (München,
read-only): Normattiva, legi_F and NeuRIS 1,362 chunks to embed; EUR-Lex5 13,649
documents that differ and no vectors. RULE 1 re-creates the four: the bundles the worker
announced to the test peers on 2026-11-23 (Normattiva 178 parts, Normattiva-DAR 52,
legi_F 44) are not an official publication (operator, 2026-10-30: the peers' "only
purpose is to test the architecture"; architecture's reading, put to the operator with
this choice). M2 and M3 take their recommendations (C, A), as the operator said nothing
of them. The creation side (§4.6) is built as A before the move.

**M2 — the re-key.** This is how the four vector tables take their v4 ids.

| choice | effect | pros | cons |
|---|---|---|---|
| A. The embed stage's carry by chunk hash | the table copied, each document's rows deleted and re-added under the new ids by `upsert_book` | existing code (06 §6.3); exact for changed documents too | one Lance delete and add per document, at the embed stage's pace and only while the encoder's lane serves it; a copied table of S3 ids pinned to S4 meanwhile |
| B. One Lance `update` of `id` for the documents the drift found identical, the embed stage for the rest | one rewrite of the table's fragments | probably faster on millions of documents | an `update` sets no per-row value (`seq`, `chapter`); the rows v4 no longer cuts stay to delete; needs the drift's per-document verdict kept |
| **C. The carry's own re-key, `dev carry --rechunked` (BUILT 2026-11-21, recommended)** | the target's table written fresh, every row whose text v4 cuts again under its new id; the embed stage gets only the rest | CPU only, no encoder lane; the same keep-by-chunk-hash rule as A; resumable by window; its dry run gives the numbers first; the documents it completes are marked embedded at once | new code (app/hold/rechunk.py, ~460 lines, and ~150 in carry.py); reads the source by `book_id` a group at a time, whose cost on the node is not measured |

Recommendation: C, timed on a pilot window (`--dry-run` reports seconds for the cut,
the reads and the writes). If the per-window read dominates, the standardised
alternative is a sort-merge scan of the source's table, as `carry.keep_producer_scan`
does (rechunk.py says it). If the operator says nothing: C.

**M3 — the gate for S4.** This is the validation of the Nemotron run for the new space id.

| choice | effect | pros | cons |
|---|---|---|---|
| **A. Run the gate once (recommended)** | one statement for S4, shared by the four | measured, nothing assumed (invariant 12); no new rule | one gate run of the card's time |
| B. Admit S3's statements in S4 | no run | no card time | a new trust rule for one gate run; a statement would speak for a space it never measured |

Recommendation: A. If the operator says nothing: A.

### 4.5 How to run the carry (built; not run on a node)

After steps 1–3 (`<name>.next` made at v4, its parts from `<name>`'s record lines, its
snapshot frozen), with the worker stopped for both (RULE 1; the carry needs both write
locks: `louise node pause <name>` and `<name>.next`):

```bash
louise dev carry <name> <name>.next --rechunked             # the refusals and the plan; writes nothing
louise dev carry <name> <name>.next --rechunked --dry-run   # the whole pass, per language: documents,
                                                            # chunks, carried, to embed, dropped; seconds
                                                            # of cut / read / write; writes nothing
louise dev carry <name> <name>.next --rechunked --yes       # the manifest, the ack, then the re-key
```

- `--window N` sets the documents (rowids) a pool task cuts (500); `--processes N` asks
  fewer processes than the profile allows. Run it under `deploy/supervise-pass.sh` in a
  container with a memory cap: a child cuts a document as the index stage does and meets
  its peaks (EUR-Lex5's largest units).
- Stopped (SIGTERM, a kill, an OOM), the same `--yes` resumes: the carry says "carried
  earlier", the re-key goes on from its cursor and first undoes a window stopped
  between its write and its cursor.
- The dry run's "to embed" is the drift tool's on a fully embedded source (§3). A source
  whose documents are not all embedded gives more: their chunks are to embed either way.
- Before use: `0` rows whose id does not end in `:4` (the target holds no other), the gate
  for S4 (M3), `hold check <name>.next --checks vectors-sample`; the worker then indexes
  every document and embeds the "to embed" chunks.
- To time on the pilot: the index stage takes a document marked embedded with
  `fts_done = 0` by its fresh selection (`_FTS_FRESH`, as for `dev reindex`), not by its
  look-ahead chain, which reads `embed_done = 0` rows only (worker.py `_FTS_NEXT`). Most
  documents of the four are marked embedded by the re-key, so their re-index runs
  without the look-ahead.

### 4.6 The creation side: what re-creating the four at v4 needs (PROPOSED, not built)

What `SHARED_LAYER` binds today:
- the `default_layers` of the legi, neuris and eurlex plugins: what `create` writes into a
  Codex whose recipe states no `[[layers]]` (hold/template.py), and the layer
  `layers.layers_for` gives such a collection;
- the three recipes (legi, neuris, eurlex) state the same block with `version = 3`, held
  to it byte for byte (tests/test_legi_recipe.py, test_eurlex_recipe.py);
- case E, `--space-from <held>` (louise.py `_space_from_recipe`): the recipe's own layers,
  else the plugin's default, must equal the held space's layers, and a recipe that states
  a space must state that `space_id`. So once S4 is held, a collection created
  `--space-from` an S4 collection is refused while `SHARED_LAYER` or its recipe says 3,
  and a case-A creation from the recipe lands in S3, alone.
- Normattiva is not among them: its recipe (normattiva-recipe.toml) is the original
  DAR-legal-it collection's, its plugin's default is `akn_article`, and the node's
  Normattiva was made by case J (`--sister-of normattiva --space-from legi`), its layer
  copied from legi's space. No recipe makes its Codex.

What the re-creation meets today: `hold collection new --re-create` refuses a recipe whose
space moves (`recreate.refusal`: "A re-creation keeps the space"); its carry samples the
predecessor's vectors by the NEW chunk ids (`recreate.sample`, `vectors_by_chunk_id`),
which end in `:4` and find none; and it carries the table whole.

The smallest change, at the move (one commit, on the operator's go):

| choice | effect | pros | cons |
|---|---|---|---|
| **A. Derive from the predecessor: `--re-create --chunker-version 4` (recommended)** | the predecessor's Codex with every layer's chunker version set to 4, nothing else; case F admits a space that moves by `layers_version_only` alone, its sample looks a chunk's vector up by its text in the same document, and its carry is `--rechunked` | the same route for all four, Normattiva included; no recipe is read, so none can drift; the check is the carry's own | a new flag and ~40 lines in louise.py (`_recreate_check`, `_recreate_carry`); a second way to make a Codex in case F |
| B. Recipes at v4: the three recipes' `version = 3` → `4`, case F admits A1k, a recipe for Normattiva's Codex written | the re-creations read their recipes, as case F does now | no new flag | Normattiva needs a recipe that does not exist (its Codex is derived); four files to keep equal to one layer |
| C. Status quo: the hand route (`create` from an edited Codex, `records build --records-from`, `dev carry --rechunked`) | nothing changes in the workflow | nothing to build | each step typed by hand on the node, the case-F checks (the interim name, the swap) not run |

In every choice, the same commit sets `CHUNK_VERSION = 4` (one line; `SHARED_LAYER`
follows, the two tests that pin 3 move to 4) and the three recipes' `version = 4` (one
line each), so that a legal collection created after the move lands in S4. Recommendation:
A. If the operator says nothing: nothing moves until the drift numbers are in (M1), then A.

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

Built (2026-11-21, tested, not run on a node): the `--rechunked` carry (A1k) and its
dry run, the relaxed CPU-reference carry, the drift tool's counts by held text. Still to
build: the case's declaration in the workflow (§4.6, A), and `CHUNK_VERSION = 4` with the
three recipe lines. Each comes with its tests, and all of it waits for the drift numbers,
architecture's review of A1k and the operator's go.
