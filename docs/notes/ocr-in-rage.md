# OCR and a PDF text layer, through RAGE

*PROPOSED, 2026-11-26, for the operator's decision. Nothing is built.*

The operator, 2026-11-26: "Keep it in the todo list, together with the RAGE
ingestion API to be extended for image OCR and adding of text layer to pdf
(if pdf form is preserved in the parsed corpus). This is architecture's
call." An architectural change is discussed with the operator first, and two
of the choices below change the wire format and add a dependency (CLAUDE.md:
ask first) — so this note asks; §4 is the question.

## 1. The cases today

| collection | what has no text | count |
|---|---|---|
| EUR-Lex5 | html units whose pages are full-page scans, carried as figures (`.tif.jpg`, ≥ 1,000 × 1,300 px) | 559 units, 4,711 scans; ro 144 units, bg 128; 316 with three or more |
| EUR-Lex5 | acts held only as a PDF of scans, held whole (the `no-text-layer` issue) | 418 documents counted |
| deposit | a PDF that yields no text | refused at upload (06 §6.7: "there is no OCR"; D5) |
| Gazzetta (researched, not built) | Au.G.U.Sto, the Gazzetta del Regno 1860–1946, scanned PDF only | ~660,000 pages, out of scope "behind an OCR stage this platform does not have" (gazzetta-plugin.md §1.3) |

Today such a document is held, counted, and never found by a search.

## 2. What is not a choice (the rules already decide it)

- **The model runs behind HTTP** (invariant 7), and the call goes through
  RAGE (rage.md §1): a gate level beside CORPUS, the card shared with
  questions first, an answer admitted only from a validated run (03 §3.3),
  the run's receipt naming it. OCR is one more RAGE verb, `app/rage/ocr.py`,
  never a plugin's own client.
- **The text is made ONCE and carried; a peer never re-makes it to embed
  it.** Two cards do not read a page into the same characters (a GPU model
  decodes token by token; one flipped token changes the rest), so a text
  re-made on each node cuts different chunks and the network's vectors no
  longer meet (invariants 1 and 3). This rules out keeping OCR text only as
  each node's derived cache (option C below) for anything that is embedded.
- **The origin's bytes are never touched** (invariant 11): the scan stays the
  figure it is, the PDF stays the PDF the origin served. A searchable PDF is
  a NEW file, derived, beside it.
- **It is the corpus's information, and it says how it was read.** The text
  comes from the collection's own image, so the fundamental rule allows it;
  it is a machine's reading, not what the origin states, so a hit whose text
  came from OCR says so ("read by OCR, <engine>"), as an annotation names
  its method.
- **The engine is frozen in the Codex like the encoder's** (09 §9.3
  `space.encoder.engine`): build, image, args, env, device; any engine may
  contribute once its run passes a gate on fixed pages; peers check a sample
  against the origin. A new engine is a new reading, re-chunked where the
  text changed, as a parser bump is — not a new space: the space is how text
  becomes vectors, and OCR is upstream of that.
- **Licence**: reading a page is using it (invariant 9); `licensing.py`'s
  resolution gates it like every other use.

## 3. The choices

**Q1. Where the read text lives.**

| | what | pros | cons |
|---|---|---|---|
| A | in the record: a `sections` entry marked as read by OCR | one place; every reader and peer has it; no new member | a §9.5 change (a new `record_line`, so a new collection); the record is no longer reproduced byte for byte from the origin by the CPU parser — `records-vs-origin` would need the GPU and a tolerance for every record; a new engine rewrites the record parts |
| **B** | **a member of its own, `ocr-<slice>`, beside `figures-<slice>` (09 §9.16): one entry per figure `hash` (per PDF `origin_hash` and page for a PDF), the engine's text with its word boxes (ALTO XML)** | records unchanged and still reproduced exactly; the Nth figure marker is `figures[N]` (09 §9.5), so the text slots in where the scan stood; a new engine replaces the member, not the records; verified on its own terms | a new member kind (a wire change); `layers.chunks_for_document` reads two inputs; a peer that embeds must hold the member, so the cut DEFERS (never errors) a document whose reading it lacks |
| C | nowhere carried: each node reads into its own cache (`app/derived.py`) | no wire change at all | ruled out for embedding (§2: chunks differ per node); fit only for the PDF text layer |
| status quo | nothing read | nothing to build | 4,711 scans and 418 PDFs found by no search |

**Q2. Which engine** — decided by measuring, not by preference.

| | pros | cons |
|---|---|---|
| Tesseract 5 (CPU, LSTM; ALTO and hOCR built in; ron, bul, ita traineddata) | the same bytes on every x86 CPU of a build, so peers verify EXACTLY and without a card; word boxes for the text layer; no card time | weaker on degraded scans and faxes; CPU time taken from the records build |
| GLM-OCR (the louise-ocr project already runs it on the node's card) | strong on hard print; already measured on the card's memory (room-on-the-card.md) | verification at a tolerance (character error rate), to be measured as invariant 12 measures encoders; word boxes to be checked; card time |
| status quo (no engine) | — | as above |

Proposed rule: read a fixed sample of 100 pages (it, ro, bg; the scans and
the PDFs) with both, scored against the same act's text where the corpus
holds it in another form or a later version; take the lower error rate,
Tesseract on a tie. Pages per second on the card and on 8 cores go in the
same table.

**Q3. The PDF text layer** (only where the parsed corpus keeps the PDF, as
the operator said).

| | pros | cons |
|---|---|---|
| **OCRmyPDF's hOCR renderer over the carried reading** (the standard tool; ISO 32000 text render mode 3, invisible text over the page) | not our invention; a pinned version gives the same file every time, so it is DERIVED data, re-made by each node from origin + member and never carried (invariant 6, `LOUISE_CACHE_DIR`) | a new dependency (OCRmyPDF and pikepdf in the image); needs word boxes, which Q2's engine must give |
| our own writer (pypdfium2, already pinned) | no new dependency | reinventing the wheel; one page-level block of text without boxes: searchable, badly selectable |
| none (the text searchable in Louise only) | nothing to build | the reader's download stays an image |

## 4. The question

What it is: letting Louise read the words in a collection's page images and
scanned PDFs, so they can be searched and embedded, and giving a scanned
PDF a searchable copy beside the original.

| choice | effect |
|---|---|
| **B + measure Q2 + OCRmyPDF** (recommended) | records untouched; one new member and one new dependency; the engine chosen by the 100-page table; EUR-Lex5's 4,711 scans and 418 PDFs searchable once read |
| A + measure Q2 + OCRmyPDF | the same reach; EUR-Lex5 re-created on a new record line; every record's verification needs the card |
| B, no text layer | searchable in Louise; the downloaded PDF stays an image |
| nothing now | the status quo above |

Recommendation: B, the 100-page measurement first, then OCRmyPDF for the
layer. If the operator says nothing, nothing is built; the item stays on the
to-do list, as the operator asked.

## 5. What B would amend, once decided

09 §9.16 (the `ocr-<slice>` member) and §9.3 (the Codex's `ocr` engine block,
inside `collection_id`); 03 §3.3 (the gate on fixed pages, exact or at a
measured tolerance); 06 §6.7 (deposit's scans need no longer be refused); rage.md §4 (a "read"
part); `layers.chunks_for_document` (the reading at the figure's marker); 08
(the hit's "read by OCR" label, the searchable download beside the plate).
