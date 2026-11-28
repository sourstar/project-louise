# A collection of images — how it fits, and what is decided

*PROPOSED, 2026-11-28; revised the same day with the Architecture
session's answer (relayed by München). Nothing is built.* The operator,
2026-11-28: "I want to create a collection with mostly, or only, images.
It's a new kind of collection and I believe that an image to text has to be
used."

## 1. What already exists

| piece | where | what it gives an image collection |
|---|---|---|
| figures carried as bytes | `record_line` 2, 09 §9.5 `figures`, member `figures-<slice>` (§9.16); decided 2026-09-08 ([figures-in-the-corpus.md](figures-in-the-corpus.md)) | the image itself travels, named by hash, optional to a peer; outside `content_hash` |
| previews | app/ask/preview.py (EUR-Lex5, 2026-11-26) | a hit can show the picture |
| a machine reading carried as a member | [ocr-in-rage.md](ocr-in-rage.md) option B, `ocr-<slice>` — PROPOSED, not decided | the mechanism OCR of the images needs |
| `modality` in the Codex | 02 §2.1, 06 §6.1, 09 §9.3: `text` · `dna` | the door to a picture space — nothing behind it built; no image calibration set (03, 09 §9.12) |

Today a record is text; a picture is found only by its caption.

## 2. What the rules already decide

- **The image is the corpus; the origin's own words about it are the
  record** (invariant 11): title, description, author, date, categories,
  EXIF, and each file's own LICENCE — Commons and Europeana licence per
  file. The collection's licence class is the strictest it admits.
- **Text IN the image is corpus**: OCR's reading is the image collection's
  own (`ocr-<slice>`, ocr-in-rage.md B).
- **A model's reading is made ONCE and carried**, never re-made per node
  (ocr-in-rage.md §2).
- **A VLM's description is NOT the image collection's data.** It brings
  what the model knows (it names places, people, works), and no prompt
  guarantees "seen, not known". Holding it in the image collection
  (the former option D1) is proposing an outside source for a collection's
  own data — itself the error (CLAUDE.md). Dropped.
- **An annotations collection cannot carry it either**: annotations are
  never embedded or ranked (02 §2.10, invariant 4), so an image could never
  be found by its description.

## 3. The order (Architecture, 2026-11-28)

1. **The corpus-only collection, no VLM.** The origin's metadata plus OCR,
   in an existing text space. Rule-clean, no new model.
2. **The described collection**, if the operator wants image-to-text: its
   OWN TEXT COLLECTION whose origin is the describer run on the image
   collection — one record per image, keyed on it, the method named, hits
   labelled "described by <model>", meeting the images at ask time by key.
   Needs an amendment to 02 §2.10 or a new collection kind (§5, the
   operator's question).
3. **A multimodal space (R2), written down.** Rule-clean by construction —
   an embedding is no shown fact and the pixels are the corpus — and
   deferred for its cost alone: `modality = "image"`, an image calibration
   set, invariant 12 for an image tower, no image reranker. A second space
   on the same collection when added (invariant 1).

## 4. One RAGE verb, one receipt, two members

- `read(image, mode = ocr | describe)`, through RAGE (invariant 7).
- Shared: the run and its receipt — engine, the prompt text (hashed),
  decoding parameters, image hash → text hash — so both verify the same way.
- NOT shared: the member. OCR is `ocr-<slice>` in the image collection;
  descriptions are the described collection's records.
- **The gate**: the engine frozen in the Codex, greedy decoding, the run
  reproduces itself (invariant 12). Beyond it: another card decodes other
  tokens, so a verifier cannot ask for bytes. Acceptance is DEFINED and
  MEASURED before anything is published — e.g. the verifier's re-reading,
  embedded in the space, within cosine X of the carried text, or a
  token-level bound — X taken from a run on two cards.

## 5. The operator's question: where the described collection fits

What it is: a collection of text that a model wrote about another
collection's images, searchable, each hit saying which model wrote it.

| choice | pros | cons |
|---|---|---|
| **amend 02 §2.10**: an annotations collection whose claims are text MAY be embedded and ranked when its Codex says so | one kind fewer; reuses `annotates`, `method` and the "by <model>" label already built | changes invariant 4's "annotations never rank"; every annotations reader must learn the exception |
| **a new collection kind, "derived reading"**: a text collection whose origin is a read run over another collection, `derives_from` + run receipt in the Codex | invariant 4 untouched; a clean name for a clean thing; reusable for translations later | a new kind: Codex field, closed lists in 02/06/09, a wizard case in new-collection.md |
| status quo: no described collection | nothing to build; step 1 still gives metadata + OCR search | an image with thin metadata and no text in it is found by nothing |

Recommendation: **a new kind** — it keeps invariant 4 whole. If the
operator says nothing, step 1 proceeds alone and step 2 waits.

## 6. Steps once the origin is named

1. **The origin** (operator): Commons, Europeana, a museum set, a deposit.
   Bulk first if it has a dump.
2. **The plugin** (06): origin form recorded ("THE ORIGIN FORM, and why"),
   `record_line` 2, every origin field kept, per-file licence,
   `origin_hash` per image.
3. **OCR** per ocr-in-rage.md (its engine measured on 100 images).
4. **The collection** through `louise hold collection new`
   ([new-collection.md](new-collection.md)), case A.
5. **The describer** (if §5 is answered): VLM on llama.cpp's multimodal
   server behind RAGE; X measured on two cards; then the described
   collection.

## 7. What it would amend

09 §9.16 (`ocr-<slice>`) and §9.3 (the read engine's block, inside
`collection_id`); 03 §3.3 (the read gate and its measured acceptance); 05/08
(the "read by OCR" and "described by" labels); 02 §2.10 or a new kind (§5);
for R2, 02 §2.4, 06 §6.1, 09 §9.3 and §9.12.

## 8. The first origin: archive.org's Met gallery (operator, 2026-11-28)

`https://archive.org/details/metropolitanmuseumofart-gallery`, uploaded
2014. Counted 2026-11-28 from archive.org's metadata API:

- **141,062 items**, one object each. A sampled item (`mma_stamp_seal_322153`)
  holds one JPEG of **8 KB** (low resolution), a thumbnail, and the Met's own
  `<id>_mma.json` (3 KB) beside `_meta.xml`.
- **Rich metadata**: title, culture, date, medium, dimensions, provenance,
  accession number, place, subjects. All of it is kept (invariant 11), the
  `_mma.json` included. Step 1 (metadata + OCR) finds most objects on this alone.
- **Licence**: `rights` points to the Met's Terms and Conditions (2014). It is
  not CC0, so the class is at least yellow and needs a recorded acknowledgement
  before the first byte (invariant 9).
- **Another origin form exists**: the Met's own Open Access release (CC0
  since 2017, its CSV and API, larger images, more objects). Invariant 11
  wants the form chosen and recorded, with its reason. Pros: green licence,
  better images, the issuer's own source. Cons: a different and larger
  origin than the one the operator named.

**The 35B as describer.** `qwen3.6-35b-a3b` is a vision-language model.
unsloth's repository ships its projector (`mmproj-F16.gguf`, 0.90 GB), but
our deploy (`louise-chat-qwen3.6-35b-a3b.yaml`) has `mmproj: ""`, so today
it cannot see. Making it describe means:
- a separate model entry with the same weights plus the projector (as
  `louise-sum-` already reuses the file) and greedy decoding, gated as §4 says;
- about 0.9 GB more on a card the 35B already nearly fills;
- card time: 141 k images at an unmeasured few seconds each is days, so
  this is measured on 100 images first (measuring.md).

## 9. DECIDED 2026-11-28 by the operator: Met Open Access, and a new kind

The operator: "Met Open Access, and a new kind for descriptions".

- **The origin is the Met's own Open Access**, not archive.org's 2014 copy.
  Probed 2026-11-28: `collectionapi.metmuseum.org/public/collection/v1/objects`
  lists **503,480 objects**. Per object, the API JSON holds the full record
  (constituents with ULAN and Wikidata URLs, dates, medium, measurements,
  `isPublicDomain`), plus `primaryImage` (original), `primaryImageSmall`
  (web-large) and `additionalImages`. Only public-domain objects carry
  images. The bulk is the Open Access CSV (`metmuseum/openaccess`, CC0),
  which has no image URLs. `/v1/search` was retired on 2026-10-01; its
  replacement is `/v1.1/search`, paginated.
- **The image form is the original**, by invariant 11: web-large holds less
  than the origin gave. One painting measured 8,291,194 B original against
  272,007 B web-large (30×). The total is NOT counted: assuming 1–4 MB on
  average over a few hundred thousand files, 0.5–2 TB. It is counted on a
  sample before acquisition, and it is the node's room that decides when,
  not whether. Images are a figures part a peer may hold or not. The
  describer and the previews read a resized copy as derived data
  (`LOUISE_CACHE_DIR`).
- **The metadata comes from the bulk first**: every record the CSV can give
  is built from it. The API is then asked per object only for what the CSV
  lacks (the image URLs, the constituents' detail). Which form is THE
  record's origin unit (the CSV row, or the API JSON, which is richer) is
  recorded beside the plugin, with its reason ("THE ORIGIN FORM, and why").
- **The descriptions are a new collection kind, "derived reading"**: a text
  collection whose origin is a `read(describe)` run over another
  collection's images, with `derives_from` and the run's receipt in its
  Codex. It is embedded, ranked and labelled "described by <model>", and
  meets the images by key at ask time. Invariant 4 stays whole. The
  amendment, to 02, 06 and 09 and new-collection.md's cases, is
  Architecture's to draft.

## 10. The Met plugin: THE ORIGIN FORM, and why (2026-11-28)

The operator's revision, the same day: "1. The collection carry only the
metadata and the images, no change of specs. 2. We make an annotation with
the description and the embedding of the description." So the Met collection
is an ordinary plugin collection, `record_line` 2. The descriptions are
discussed with Architecture in [derived-reading-sweep.md](derived-reading-sweep.md).

The two forms the Met serves, both probed 2026-11-28:

| form | what it holds | pros | cons |
|---|---|---|---|
| **the API JSON per object** (`/v1/objects/<id>`), objects listed by `/v1/objects` (503,480 ids) | every CSV column, plus `constituents` as a list (role, ULAN, Wikidata, gender), structured `measurements`, `primaryImage`, `additionalImages`, `objectURL` | the superset: nothing the Met states is lost (invariant 11); one unit per object, re-fetchable by id for `records-vs-origin`; the image URLs are where the images are | 503k requests through the transfer manager (invariant 5): hours, not minutes |
| the Open Access CSV (`metmuseum/openaccess`, `MetObjects.csv`, 341,135,952 B) | one row per object; several artists joined by `\|` | one download | no image URLs, no structured measurements: holding only it would mean holding less than the origin gives |

**Chosen: the API JSON**, kept whole as the origin unit (`origin_hash` over
its bytes). The CSV is not used. The bulk-first rule binds legal collections,
and here the bulk lacks what the collection is for. This choice goes into the
plugin's source module, as tests/test_invariant_11_origin_form.py requires.

## 11. Architecture's position on the annotation route (2026-11-28, via München)

1. **The Met as a plain plugin collection**: yes, with no spec change. Start.
2. **No exception to 08 or invariant 11.** 02 §2.10's trust model already
   covers this: a claim is attributed by its method and pinned to content
   hashes, and claims written by a language model are allowed. Describing is
   the collection's `method` (07 §7.8). The publishing node re-runs it when
   the Met moves, on the producer card, behind the read gate.
3. **The §2.10 and invariant 4 amendment, narrow:**
   - Only "describes" claims, whose object is TEXT, are embedded. Every other
     claim kind stays a fact-by-key.
   - The annotations Codex gains a `space` at a new version, and is refused
     below it.
   - Its encoder is gated (invariant 12). The worker cuts and embeds that
     collection alone.
   - It ranks as its OWN list in the cross-collection merge (05 §5.6), never
     as a weight on the Met's list.
   - A hit shows the Met record by key, with the description beside it,
     labelled "described by <model>". A record found by both lists is shown
     once, saying both.
   - The checks carry over: the receipt, the signature and the vectors are
     automatic; "description not compared with the picture" is said; "looked
     at by" is recorded.
   - Invariant 4's new wording is the operator's to give (München puts it to
     them): "…NEVER enter a ranking as items or weights — except an
     annotations collection's text claims, searched as that collection's own
     list in the cross-collection merge, each hit labelled with its method,
     never a weight on another list."
4. **One PR.** Its sweep adds the annotations Codex version, the worker's
   stages for annotations, and 05's merge.

## 12. DECIDED 2026-11-28: the bulk, and archive.org as a RULE 1 shortcut

The operator: "CSV bulk download, then the images from archive.org that fit
the file name and size, then the API requests of the remaining. By the book
in the ingestion pipeline: CSV bulk download and API request of the images.
We take a shortcut if it fits (rule 1), otherwise we go by the book."

**By the book (the plugin, what a verifier reproduces):** the Open Access
CSV is the bulk. The API's per-object JSON is fetched for the objects with
images, kept whole, and gives the image URLs. The images come from
images.metmuseum.org.

**The shortcut (an operator pass, never the plugin).** archive.org's
`metropolitanmuseumofart-gallery` copy, measured 2026-11-28 on 41
public-domain objects present in both:
- 24 match: the Met's current file is byte-for-byte
  `jpegtran -optimize -copy none` (libjpeg-turbo 2.1.5) of archive.org's,
  with its 18-byte JFIF APP0 removed;
- 17 differ (re-photographed, or truncated at 1 MiB);
- all 24 matches had the Met's size, and all 17 mismatches did not.

Acceptance, per file:
- the Met's `primaryImage` file name and HEAD `Content-Length` equal those
  of the rebuilt file;
- else it is fetched from the Met.

The Met's server (IIS) offers no content hash, so a size is evidence, not
proof. Before publication, a random 1 % of the accepted files is fetched
whole from the Met and compared byte for byte. ONE mismatch voids the
shortcut, and every accepted file is fetched by the book. `origin_hash` is
always over the bytes held, which are the Met's bytes.

Measured cost: archive.org served the 41 files in 44 s, the Met in 8 s. The
shortcut lightens the Met's load. It does not shorten the run unless
archive.org is fetched in parallel with the Met.

**Open (invariant 11, entity information):** for the ~48 % of objects
without images, the CSV holds `Dimensions` as a string, while the API JSON
adds structured `measurements`. Fetching their JSON as well (~240 k requests,
~1–7 h at 10–80 req/s) keeps them.
