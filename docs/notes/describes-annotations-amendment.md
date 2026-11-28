# The "describes" annotations amendment — DRAFT for Architecture's review

*Drafted 2026-11-28 by the image-collection session. It applies the
operator's decision of the same day: "1. The collection carry only the
metadata and the images, no change of specs. 2. We make an annotation with
the description and the embedding of the description". It also applies
Architecture's position on that decision ([image-collections.md](image-collections.md)
§11) and the binding points of the sweep ([derived-reading-sweep.md](derived-reading-sweep.md)).
It SUPERSEDES [derived-reading-amendment.md](derived-reading-amendment.md).
NOTHING IS APPLIED to docs/01–09 or CLAUDE.md until Architecture has
reviewed it and the operator has given invariant 4's wording.*

## 0. What changes, in one paragraph

An annotations collection may carry a new claim type, `describes`: the text
a frozen model wrote about one figure of a record of the annotated
collection. Such an annotations collection, and only such a one, carries a
text `space`. Its claims' text is cut, embedded and searched as that
collection's OWN ranked list in the cross-collection merge, each hit
labelled with its method. Every other claim type stays a fact-by-key,
placed and never ranked. The Met collection is unchanged: a plain plugin
collection at `record_line` 2.

## 1. 02 §2.10 — annotations collections

**"What it does not have"** gains one exception:

> **No text to cut and no space** — except an annotations collection whose
> claims are `describes` claims (below). It carries a text space, and the
> worker cuts and embeds that collection's claims alone. Nothing is
> acquired or indexed from an origin, since its origin is its method.

**The claim type `describes`** (new paragraph after `same-work` /
`translation-of`):

> `describes`: the claim's `text` is what its method, a model, wrote on
> reading one figure of a record of the annotated collection. The subject
> is that record and that figure. The text is the method's, never the
> record's. It is shown beside the record, labelled "described by <model>",
> and never as a fact of the annotated collection.

**The trust model is the one §2.10 already has.** A claim is attributed by
its method and pinned to content hashes, and "a reading peer compares
hashes and never re-runs a method". A description's fidelity to its picture
is therefore ATTRIBUTABLE, not automatically checked. So 08 and invariant 11
need NO exception (Architecture, 2026-11-28).

**Re-running when the target moves.** The method re-runs by itself on the
publishing node when the annotated collection publishes a newer snapshot
(collection-updates.md §4). It reads exactly the delta's added and changed
figures and carries the rest by hash (RULE 1: bytes provably identical). A
snapshot is published only when every figure of the pinned target snapshot
is described or named as undescribed. It is never a partial reading
presented as whole.

## 2. 09 §9.3 — the annotations Codex, version 13

Version 13 is the version-12 ANNOTATIONS object (`annotates` a list,
`select` where a rule selects) with a non-null `space`. It is written for an
annotations Codex whose claims include `describes`, and for no other, so no
Codex, space or `collection_id` already written moves. Below 13, an
annotations Codex with a `space` is refused.

- **`space`** is a text space, stated exactly as any text collection's
  (02 §2.4). By the `>=` rule, it carries `space.exactness` (required since
  10) and `space.document` where the space prefixes documents (since 11).
  Its encoder is gated like any (invariant 12). `space_id` is computed as
  any text space's: the describer is NOT in it.
- **`layers`**: one layer, a chunker cutting each claim's `text`. The
  `verbatim` chunker (one chunk per claim) is proposed. A longer description
  is cut by the space's `cut`.
- **`work`**: as for any embedded collection.
- **The method** (07 §7.8 `method`, 09 §9.5 `method` below) carries the read
  run, and is inside `collection_id`.
- Everything else stays as §9.3 fixes an annotations Codex today: `plugin`
  `"annotations"`, `origin.spec` `{source_id: "annotations", nonce}`, `unit`
  `"file"`, `cutoff_rule` `"record-date"`, `record_line` 1, `modality`
  `"text"`. `credits` gains a `community` entry, since it is now embedded.
- The plan names `roots`, `records`, `license`, `signature` and the space's
  checks (`vectors`, the calibration gate). `model-agreement` is NOT planned
  for `describes` (§4).

§9.18: `louise-codex` 13, with its history line.

## 3. 09 §9.5 — the claim

A `describes` claim is a §9.5 annotations claim with:

| field | value |
|---|---|
| `type` | `"describes"` |
| `subject` | `{uid, figure_sha256}`: the annotated record and its figure, by hash. A position would be renumbered by a figure added before it |
| `object` | null |
| `text` | string: the method's output, byte-exact. **New key**, REQUIRED for `describes`, ABSENT for every other type |
| `subject_hash` | the record's `content_hash` in `target_snapshot`, as for any claim |
| `object_hash` | null |
| `method` | `{kind: "model", id, version, model: <sha256 of the weights>, projector: <sha256 of the mmproj>, precision: "F16", prompt: <sha256 of the prompt text>, temperature: "0", engine: <the engine block>, set: <the read set's id>}`. `projector`, `precision`, `engine` and `set` are new keys, REQUIRED for `describes` |
| `receipt` | `{input_sha256, output_sha256}`: the figure's input bytes after the method's resize, and `text`'s sha256. **New key**, REQUIRED for `describes` |
| `confidence`, `evidence` | null (a model's description has neither) |
| `subject_lang` / `object_lang` | ABSENT; the text's language is the method's prompt's |

The claim's uid is `annotations:<n>`, an integer, as for every claim (the
text index and vector table key on an integer). Its identity (§9.5) is
(`type`, `subject.uid`, `subject.figure_sha256`, the method's `id`), so a
re-run reuses the uid. The record line keeps `title` `""`; `origin_hash` is
BLAKE2b-128 as everywhere; `origin` is the string locator
`<annotated uid>#figure=<sha256>`.

A figure the model cannot read (a refusal, an empty answer, a crash) is
DEFERRED, never errored. It is named in the snapshot as undescribed, and a
later run reads it.

## 4. 03 — the read gate and the checks

**The read gate, before any claim is written** (invariant 12 applied to a
generative method). The read set is 16 fixed CC0 figures from the Met's
public-domain set (a painting, a photograph, an object, text in an image, a
drawing, …), shipped in the platform image with their sha256, like
`calibration/<set>.jsonl`, and named in `method.set`. The 16 are read:
- one at a time, again in reverse order, over three fresh loads;
- as the producer runs, with its batch and concurrency (the server's
  slots), since a slot count can change greedy bytes on llama.cpp.

On the producer's own card and build, every reading reproduces its own
BYTES. A run that does not FAILS.

**The automatic checks** (operator, 2026-11-28: "Our automatic check is only
about 1", the embedding):
- `records`: the receipt's `input_sha256` is the held figure's input, and its
  `output_sha256` is `text`'s;
- `signature`: the publisher's, as any snapshot's;
- the vectors: within the space's tolerance (invariant 12, §3.1), as any
  text collection's.

They report "description not compared with the picture", never "verified".

**`model-agreement` (03 §3.2) is NOT planned for `describes`**, and 03
§3.2's `model-agreement` row says so by name: "describes claims: not
model-agreement; the qualitative check instead, reported as such", so the
spec does not read as if a check were owed and missing (Architecture,
2026-11-28). It re-runs
the method and needs a measured floor, and the operator has made the
picture-match qualitative. That qualitative check is recorded as what it is:
a person looking, or an optional re-read (`hold check <c> --re-read N`, best
on the same card class, where even the bytes may match). It is reported as
"looked at by <who>, <n> figures".

## 5. 05 — how it is searched and shown

**05 §5.3 / "Annotations are placed, never ranked"** gains:

> — except `describes` claims, which are searched as their annotations
> collection's OWN ranked list.

**05 §5.6, the cross-collection merge.** That list merges on calibrated
relevance where every list has a calibration, and by RRF otherwise, like any
collection's. It is never a weight on the annotated collection's list, and
the WEIGHTS rule is untouched.

**A hit** shows the ANNOTATED RECORD, by the claim's `subject.uid`: its
title, its fields and its figure preview, all from its own collection. The
description stands beside it, labelled "described by <method.id>". A record
found by both lists is shown ONCE and says both ("found by its
description"). A description never fills a field the record lacks.

## 6. 07 — commands and the collection file

- `hold collection create annotations` (07 §7.3) accepts a `describes`
  method and a recipe's `[space]`.
- §7.8 `method` names the describer run.
- `new-collection.md` gains case H′, "annotations with a space": steps 1, 2,
  3 (calibrate the space's encoder), 4, 5, 7 and 8, plus the read gate before
  step 5.

## 7. CLAUDE.md — invariant 4, the operator's words

Proposed by Architecture, to be given or reworded by the operator (München
has put it to them):

> …NEVER enter a ranking as items or weights — except an annotations
> collection's text claims, searched as that collection's own list in the
> cross-collection merge, each hit labelled with its method, never a weight
> on another list.

## 8. The build (scope of the one PR, each item with tests)

- `app/hold/codex.py`: `CODEX_VERSIONS` adds 13; `_validate_annotations`
  admits a `space` at 13 only and refuses it below. `written_version`
  returns 13 for such a Codex.
- §9.5 claims: the `describes` type, its new keys, and its identity.
- The worker's stage selection: an annotations collection with a space is
  cut and embedded; one without is skipped, as today.
- `app/copy_state.py`: the origin-check case for it.
- Ask-time ranking: the list, the merge and the one-hit display (05 §5.6).
- The tests that assert "unsupported Codex version 13"
  (`tests/hold/test_codex.py`, `tests/hold/test_backend_engine.py`) move
  to 14. The deploy grep (`deploy/16f/deploy-louise-madlad-t5.sh:223`) is
  named to München.
- Tests for Codex 13: round-trip; a `space` refused below 13; greedy
  decoding (`temperature "0"`) enforced at create; a `describes` claim
  refused without `text`, `receipt` or its method keys.

## 9. Open, for Architecture

1. `verbatim` (one chunk per claim), or the space's `cut` over longer
   descriptions? This depends on the describer's output length, which the
   100-image benchmark gives.
2. Does `credits` take a `community` entry for the embedding, as an embedded
   collection's does?
3. Is the read set's location `describe/<set>.jsonl` in the platform image
   beside `calibration/`?
