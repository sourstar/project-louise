# A collection of images — how it fits, and what architecture must decide

*PROPOSED, 2026-11-28, for the operator and the Architecture session.
Nothing is built.* The operator, 2026-11-28: "I want to create a collection
with mostly, or only, images. It's a new kind of collection and I believe
that an image to text has to be used."

## 1. What already exists

| piece | where | what it gives an image collection |
|---|---|---|
| figures carried as bytes | `record_line` 2, 09 §9.5 `figures`, member `figures-<slice>` (§9.16); decided 2026-09-08 ([figures-in-the-corpus.md](figures-in-the-corpus.md)) | the image itself travels, named by hash, optional to a peer; outside `content_hash` |
| previews | app/ask/preview.py (EUR-Lex5, 2026-11-26) | a hit can show the picture |
| a machine reading carried as a member | [ocr-in-rage.md](ocr-in-rage.md) option B, `ocr-<slice>` — PROPOSED, not decided | the exact mechanism an image-to-text reading needs |
| `modality` in the Codex | 02 §2.1, 06 §6.1, 09 §9.3: `text` · `dna` | the door to a picture space — nothing behind it built; no image calibration set (03, 09 §9.12) |

Today a record is text; a picture is found only by its caption.

## 2. What the rules already decide

- **The image is the corpus; the origin's own words about it are the
  record** (invariant 11): title, description, author, date, licence,
  categories, EXIF — every field the origin states, `extra.<name>` where no
  §9.5 key fits. An image with metadata is already searchable by text with
  no model at all.
- **A model's reading is made ONCE and carried, never re-made per node**
  (ocr-in-rage.md §2): two cards decode different tokens, so a text re-made
  per node cuts different chunks and the network's vectors stop meeting.
- **It goes through RAGE** (invariant 7, rage.md): one more verb beside OCR,
  its engine frozen in the Codex like an encoder's, gated on fixed images
  before any reading is admitted.
- **It is labelled.** A hit whose text a model wrote says "described by
  <model>", as an annotation names its method.
- **The licence gate covers the reading** (invariant 9).

## 3. The one hard point: a description is not only the corpus

OCR reads what is IN the image. A vision-language model also brings what
it KNOWS: "the Pantheon in Rome, 2nd century" when the photograph shows a
domed portico and its metadata says nothing. That is outside information
entering a collection's own data — the fundamental rule
("A collection has only the information contained in its corpus"). Two
honest readings, the operator's to choose:

| | rule | pros | cons |
|---|---|---|---|
| **D1** | the description is the collection's, held as `describe-<slice>`, prompt limited to what is visible ("describe what is shown; do not name places, people or works"), labelled | searchable at once; one member, the OCR mechanism | the prompt cannot fully stop the model naming things; the line between "seen" and "known" is judged by a model |
| **D2** | the description is an ANNOTATIONS collection about the image collection (method: the model), already allowed outside sources (operator, 2026-11-16, choice A) | the fundamental rule untouched; the model free to name what it recognises; replaced without touching the images | a second collection; the description meets the images at ask time only — embedding it means embedding the annotations collection |

Recommendation: **D2** — it is the shape the fundamental rule already has an
answer for, and the model's recognition (the most useful part) is kept.

## 4. How the picture is found — two routes

| | route | pros | cons |
|---|---|---|---|
| **R1** | **image → text → the existing text space** (operator's instinct): origin metadata + OCR + description embedded as text | everything reused: text encoders, their calibration, the gate, the text reranker, FTS, the Ask page; no new modality | finds what was SAID about a picture, never what it looks like; quality capped by the describer; card time per image to describe |
| R2 | a multimodal space: `modality = "image"`, an image–text encoder (SigLIP 2 or jina-clip class, behind HTTP), text queries into the same space | finds by look ("a red staircase"); no describer, no outside-knowledge question | largest change on the list: a new modality, an image calibration set, invariant-12 measurement for an image tower, chunks that are figures, a verifier must hold the figures member to re-embed; no image reranker |
| R1 then R2 | R1 now; R2 as a second space on the same collection (a node runs several spaces side by side, invariant 1) | usable soon; R2 is added, not substituted | two builds |

Recommendation: **R1 then R2**, R2 written down here and built when a
collection whose metadata is thin proves R1 short.

## 5. The steps, in the project's order

1. **The origin** (operator): which collection — Wikimedia Commons,
   Europeana, a museum's open-access set, the operator's own files
   (deposit)? The bulk-first rule applies if it is a dump. This decides the
   licence class and how much metadata there is.
2. **Architecture decides** D1/D2, R1/R2, and the shared member with
   ocr-in-rage.md Q1 (one "read" verb, two modes: OCR and describe).
3. **The plugin** (06): origin form recorded ("THE ORIGIN FORM, and why"),
   `record_line` 2, every origin field kept, `origin_hash` per image.
4. **The describer**: a VLM on llama.cpp's multimodal server (mtmd) behind
   RAGE; measured as invariant 12 measures encoders (same image, same card,
   reproduces itself; greedy decoding; [measuring.md](measuring.md)).
5. **The collection** through `louise hold collection new`
   ([new-collection.md](new-collection.md)), case A (or H for D2's
   annotations).

## 6. What it would amend

09 §9.16 (the `describe-<slice>` or shared `read-<slice>` member) and §9.3
(the describer's engine block, inside `collection_id`); 03 §3.3 (the gate on
fixed images); 05/08 (the "described by" label); for R2 also 02 §2.4, 06
§6.1, 09 §9.3 and §9.12 (`modality = "image"`, an image calibration set).
