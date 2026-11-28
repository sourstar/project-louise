# The derived-reading amendment — DRAFT for Architecture's review

*Drafted 2026-11-28 by the image-collection session, at Architecture's
request (relayed by München). It applies the operator's decision of the same
day, "a new kind for descriptions" ([image-collections.md](image-collections.md)
§9). NOTHING IS APPLIED to docs/01–09 until Architecture has reviewed it.
Each section below names the document and section it lands in, and what it
adds there.*

## 0. What it is

A **derived-reading collection** is a FOURTH kind of collection, beside a
plugin's (02 §2.1), a deposit (§2.9) and annotations (§2.10). Its records are
the TEXT a frozen model wrote when reading another collection's figures, one
record per figure. Unlike annotations, it is cut, embedded and ranked like any
text collection. Unlike a plugin's collection, its origin is not a public
source: it is a read run over a named snapshot of another collection.

Why a kind of its own:
- A model's reading is not the image collection's own data. The model brings
  what it knows, which the fundamental rule forbids.
- Annotations are never ranked (invariant 4), so an image could never be
  found by its description.

The first case is the Met's images described by `qwen3.6-35b-a3b`. The kind
also fits a translation run later. That is not claimed here, and is not
decided.

## 1. 02 — a new §2.11 "Derived-reading collections"

Its text, in 02's register:

- **Its origin is a read run.** It names the collection it reads, the snapshot
  it read, and the run that read it. No public origin reproduces it, so two
  runs are two collections, as two annotators are (§2.10).
- **It has a space.** Its text is cut by a declared chunker and embedded in an
  ordinary text space. `space_id` is computed exactly as any text
  collection's (§2.4, 09 §9.3). The read run is NOT in `space_id`: the space
  is how text becomes vectors, and the reading is upstream of it. The run IS
  in `collection_id`, so another run is another collection.
- **One record per figure.** Each figure the read snapshot holds gets exactly
  one record, keyed to the figure's own record and figure (§3 below).
- **It is shown as what it is.** Every hit says "described by <model>"
  (05, 08). It is never shown as a fact of the read collection's records, and
  never merged into them. The two meet at ask time by key, as annotations do.
- **Its licence** is the stricter of the read collection's licence and the
  describer's weights licence (invariant 9, `app/licensing.py`).
- **It is a root.** It has no lineage, derivation or certifier. Its snapshots
  follow the read collection's (§5 below).

## 2. 09 §9.3 — the Codex, version 13

Version 13 is the version-11 object with `derives_from` and `read`, written
for a derived-reading Codex and for no other. So no Codex, space or
`collection_id` already written moves. Both keys are inside `collection_id`.

| key | type | meaning |
|---|---|---|
| `derives_from` | `{collection_id, snapshot}` | the collection read and the snapshot read. Required at 13, ABSENT below it |
| `read.mode` | `"describe"` (· `"ocr"` once ocr-in-rage.md is decided) | what the run does to a figure |
| `read.encoder` | `{weights: "sha256:…", projector: "sha256:…", precision: "F16"…, label}` | the model's weights digest, its vision projector's digest, and the projector's precision, stated and never defaulted (operator, 2026-11-27) |
| `read.engine` | the engine block, as `space.encoder.engine` (build, image, args, env, device) | frozen. Any engine may contribute once its run passes the read gate (§4 below) |
| `read.prompt` | `{sha256, text}` | the prompt, byte-exact, and its hash |
| `read.decoding` | `{temperature: 0, seed, max_tokens, top_k: 1}` | greedy decoding. Anything else is refused at create |
| `read.input` | `{max_side, format}` | how a figure is resized before reading. It is derived and deterministic: the same figure bytes always give the same input bytes |
| `read.acceptance` | `{metric, bound, measured_on: [device, device]}` | the measured cross-card bound (§4 below) |

Fixed, as for annotations:
- `plugin` is `"reading"` and uids are `reading:<n>`;
- `origin.spec` is `{source_id: "reading", nonce}`, `unit` `"figure"` and
  `cutoff_rule` `"record-date"`;
- `record_line` is 1, and `modality` is `"text"`.

## 3. 09 §9.5 — the record

A derived-reading record is a §9.5 record line 1 with these fields:

- `sections` is `[["", <the model's text>]]`.
- `title` is the read record's title, copied. It is the one field copied, so
  that a hit can be named; it is labelled as the read collection's.
- `origin_hash` is the sha256 of the FIGURE's bytes, as `figures-<slice>`
  carries them.
- `origin` is `{collection_id, snapshot, uid, figure: <index>}`: the read
  record and which of its figures.
- `extra.read_receipt` is `{input_sha256, output_sha256, run}`: the input
  bytes after `read.input`, and the text's hash.

A figure that the model cannot read (a refusal, an empty answer, a crash) is
DEFERRED, never errored (CLAUDE.md "Deferral is not failure"). It is named in
the snapshot's `delta` as `unread`, and a later snapshot reads it.

## 4. 03 §3.3 — the read gate and `records-vs-origin`

**The gate, before any record is written.** It is invariant 12 applied to a
generative reading:
- the same 16 fixed figures (shipped like the calibration set, one set per
  modality, 09 §9.12) are read one at a time, again in reverse order, over
  three fresh loads;
- on the producer's own card and build, every reading reproduces its own
  BYTES. A run that does not FAILS.

**The cross-card bound, measured before anything is published.** Another card
decodes other tokens, so a verifier cannot ask for bytes:
- the 16 figures, and a 100-figure sample of the collection, are read on two
  different cards;
- each pair of readings is embedded in the collection's own space;
- `read.acceptance.bound` is the minimum cosine observed, minus the space's
  engine tolerance. It is stated in the Codex with the two devices.

**`records-vs-origin`.**
- It re-reads a sample of figures, embeds each re-reading in the space, and
  accepts a record when it lies within `bound` of the carried text's vector.
- On the producer's own card and build, it accepts bytes only.
- A verifier without the figures member fetches the sampled figures first, or
  says "unverified" (never "verified").

## 5. Updates — collection-updates.md §4, the cascade

- `derives_from` is a declared source, like `annotates`. A new snapshot of the
  read collection wakes it.
- The next snapshot reads exactly the `delta`'s added and changed figures.
  Tombstoned figures are tombstoned. The rest are carried by hash
  (RULE 1: bytes provably identical).
- **Never a partial reading presented as whole.** A snapshot is published only
  when every figure of the new read snapshot is read or named `unread`. Until
  then the previous snapshot is served, and it says which read snapshot it is
  pinned to.

## 6. 05 / 08 — how a hit shows

- A hit reads "described by <read.encoder.label>". The read record's figure
  preview stands beside it (app/ask/preview.py).
- A description never appears among the read collection's own facts, and
  never fills a field the read collection's record lacks.
- Across collections, the description's ranked list merges like any other
  collection's (05 §5.6). It is a different collection, so there is no
  in-collection fusion.

## 7. Closed lists this touches (to be swept before applying)

- 02 §2.1, the list of kinds;
- 06 §6.1, `plugin` values;
- 07 §7.3, `hold collection create <kind>`;
- 09 §9.18, the format-version table;
- new-collection.md, a case K "A derived reading" (steps 1, 2, 5, 7, 8, plus
  the read gate).

The figures amendment found thirteen closed lists where three were expected
(figures-amendment-plan.md). This draft has not been swept for the rest.

## 8. Open, for Architecture

1. The read set: 16 figures of which collection? A fixed set shipped in the
   platform image, like `calibration/<set>.jsonl`, is proposed.
2. Is `title` copied (§3), or left absent with the hit naming the read record
   at ask time? Copying is simpler; absence is purer.
3. Should `ocr` join `read.mode` now or with ocr-in-rage.md's decision?
