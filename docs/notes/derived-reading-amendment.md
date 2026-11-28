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
| `read.mode` | `"describe"` (a closed enum; `"ocr"` joins only with ocr-in-rage.md's own decision and amendment) | what the run does to a figure |
| `read.encoder` | `{weights: "sha256:…", projector: "sha256:…", precision: "F16"…, label}` | the model's weights digest, its vision projector's digest, and the projector's precision, stated and never defaulted (operator, 2026-11-27) |
| `read.engine` | the engine block, as `space.encoder.engine` (build, image, args, env, device, slots) | frozen. The ONE engine that writes the collection's records. Other engines verify (`records-vs-origin`) and never contribute: another card decodes other text, so two writers would make the collection's bytes depend on who read what. Distributed contribution stays an embedding thing (Architecture, 2026-11-28) |
| `read.prompt` | `{sha256, text}` | the prompt, byte-exact, and its hash |
| `read.set` | the read set's id | the 16 fixed figures of the read gate (§4) |
| `read.decoding` | `{temperature: 0, seed, max_tokens, top_k: 1}` | greedy decoding. Anything else is refused at create |
| `read.input` | `{max_side, format}` | how a figure is resized before reading. It is derived and deterministic: the same figure bytes always give the same input bytes |

Fixed, as for annotations:
- `plugin` is `"reading"`. A uid is content-derived, `reading:<read uid>:<figure sha256, first 16 hex>`, so a re-creation never renumbers, and idempotence keys on it (invariant 2);
- `origin.spec` is `{source_id: "reading", nonce}`, `unit` `"figure"` and
  `cutoff_rule` `"record-date"`;
- `record_line` is 1, and `modality` is `"text"`.

## 3. 09 §9.5 — the record

A derived-reading record is a §9.5 record line 1 with these fields:

- `sections` is `[["", <the model's text>]]`.
- `title` is ABSENT. Every fact a result shows comes from the collection's
  own corpus, never from another collection, so the hit names the read
  record at ask time, by key, as annotations are named (Architecture,
  2026-11-28).
- `origin_hash` is the sha256 of the FIGURE's bytes, as `figures-<slice>`
  carries them.
- `origin` is `{collection_id, snapshot, uid, figure_sha256}`: the read
  record, and its figure by hash. A position would be renumbered by a figure
  added before it.
- `extra.read_receipt` is `{input_sha256, output_sha256, run}`: the input
  bytes after `read.input`, and the text's hash.

A figure that the model cannot read (a refusal, an empty answer, a crash) is
DEFERRED, never errored (CLAUDE.md "Deferral is not failure"). It is named in
the snapshot's `delta` as `unread`, and a later snapshot reads it.

## 4. 03 §3.3 — the read gate and `records-vs-origin`

**The gate, before any record is written.** It is invariant 12 applied to a
generative reading:
- the read set's 16 figures are read one at a time, again in reverse
  order, over three fresh loads, and as the producer runs: its batch and
  concurrency (the server's slots), since a slot count can change greedy
  bytes on llama.cpp. The read set is 16 fixed CC0 figures from the Met's
  own public-domain set (a painting, a photograph, an object, text in an
  image, a drawing, …), shipped in the platform image with their sha256 like
  `calibration/<set>.jsonl`, and named in `read.set`;
- on the producer's own card and build, every reading reproduces its own
  BYTES. A run that does not FAILS.

**The description is data** (operator, 2026-11-28: "We keep the description
and the verification is: 1. check that the embedding of the description is
reproducible (quantitative verification) 2. the peer can verify that the
description matches the picture (qualitative). Our automatic check is only
about 1."; Architecture accepted it). No cross-card bound is measured and no
re-reading is automatic: a cosine bound would accept a different text that
lands nearby.

**`records-vs-origin`, automatic**, checks three things:
- the receipt: its input is the held figure's sha256, its output is the
  record's text sha256;
- the publisher's signature over it;
- the vectors, within the space's tolerance (invariant 12, 03 §3.1).

It reports "description not compared with the picture", and never
"verified".

**The qualitative check** is a person looking, or an optional re-read
(`hold check <c> --re-read N`, best on the same card class, where even the
bytes may match). It is recorded and reported as what it is: "looked at by
<who>, <n> figures".

## 4a. 08 — the named exception

"No defence may rely on trusting the publisher" gets a written exception for
derived readings. A description's FIDELITY to its picture is ATTRIBUTABLE,
not detected: the signed receipt binds figure sha256 → text sha256 to its
publisher, and no automatic check claims more. Invariant 11's "everything
held is verifiable" names the same exception (CLAUDE.md: the operator's own
text, changed on the operator's own word).

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

- A hit reads "described by <read.encoder.label>". It adds nothing else
  unless a qualitative check was recorded for that collection. The read record's figure
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
(figures-amendment-plan.md). The sweep for the rest is a PRECONDITION:
nothing is applied before it.

## 8. Reviewed by Architecture, 2026-11-28

Five corrections, applied above: one writing engine; the figure keyed by
sha256; content-derived uids; the gate run as the producer runs; and the
closed-list sweep made a precondition (§7). The three answers: the read set
is Met CC0 figures named in `read.set`; `title` is absent; `ocr` waits for
its own decision. Next: the sweep, then ONE PR applying 02, 03, 05, 06, 07,
08, 09 and new-collection.md, with tests for Codex 13 (round-trip, the keys
refused below 13, greedy decoding enforced at create).
