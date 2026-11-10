# Sisters share one text index (operator, 2026-11-10: choice A)

**Status:** BUILT 2026-11-10 (app/text_index.py, tests/test_text_index.py); "As built" at the end names where the build amends this design. It amends 02 §2.7. Until now that section read: a sister's "manifest and text index are copied, being functions of the records alone". The amendment is that a sister whose index would be byte for byte her sister's no longer gets a copy of it: she shares it.

The operator's question was: "2 or more sisters share the same corpus but not the same vectors … in a node the indexing for a sister is valid for all of them, isn't it?" The answer was yes, for sisters that cut the same chunks, and the operator chose **A**: "One shared text index, written by one sister, read by the others". Vocabulary (operator, the same day; 01 and 02 §2.7): a sister's source is **her sister**, never her mother.

## What is shared, and what is not

- **Shared:** the text index, i.e. the tantivy directories, one per language group. Its content depends only on the records (keyed by `book_id` = the record's `native_id`), the layers, the cleaning and the schema inputs (validity declared, names capable). It does not depend on the manifest's rowids.
- **Not shared:**
  - The manifest. Each sister keeps her own embed marks, queues and bundles (A1i).
  - The catalogue index (`app/catalog.py`). It is keyed on `documents.rowid`, and two sisters' rowids diverge as soon as they discover new records in different orders.
  - The facets and the vectors.

## Who shares: the index identity (computed, RULE 2)

Two held collections share an index when their **index identity** is equal. The identity is a BLAKE2b-128 over the canonical JSON of:

| part | why |
|---|---|
| the records' lineage: `plugin`, `origin`, `toolchain.platform_image`, `parser`, `cleaning_version`, `slicing` | the fields that decide a record (02 §2.7); equal for sisters by construction |
| `layers`, whole (chunker ids, versions, parameters, input forms) | a relayered sister cuts other chunks (normattiva-nemo against normattiva) |
| `update` | a weekly sister holds newer records than a monthly one between their shared cutoffs (02 §2.7) |
| the slices the node holds | a sister holding a slice her sister does not must not show its records, nor lose them |
| the index schema inputs (validity declared, names capable) | the schema itself |

The identity is a content key, not a name, so the rule "bookkeeping keys on physical identity" still holds: its directory is the identity. Every collection's index lives at `collections/.text-index/<identity>/<group>/`, and a collection with no sister there is the common case: it is simply the only one using that directory. `Collection.fts_dir` resolves to that directory, so the worker's one writer per path (`fts._writer`, keyed on `fts_dir`) is automatically one writer per group.

## Who writes: the writer, sticky

Each group has **one writer**: the member recorded in `.text-index/<identity>/writer.json`.
- When the group is formed, it is the first member by name.
- It changes **only when the writer is no longer held** (forgotten, or its file retired). The next member by name takes over.
- A paused writer stays the writer. This is option A as offered: "Writing moves to another sister when the writing one is forgotten. The readers wait on its indexing."

The rule is sticky so that the index is always some single member's by-the-book index. A "first unpaused member" rule would let a resumed writer write records older than the index already holds.

- **The writer** indexes exactly as today: delete-then-insert by `book_id`, commit, then mark (invariant 2). Its deletes (a record out of scope, superseded, erased) are its own, and the readers make the same ones, because they have the same records, update and slices.
- **A reader** never writes the index. Its index stage becomes a **mark copy**, one set-based statement per rowid window, in `BEGIN IMMEDIATE`, with the writer's manifest ATTACHed read-only. A document gets `fts_done=1` with the writer's `fts_version`, `entity_version`, `chunks` and `tokens` when the writer's manifest holds the same `uid` at the same `content_hash`, with `fts_done=1`. Its entity tags (`entities.write_tags_many`) are copied from the writer's in the same transaction, and its genesis demand (`bundle_genesis.announce`, `reopen_finals_for_range`) runs as the writer's stage runs it, since that is the reader's own archive.
- A document the writer has not indexed yet is **deferred, never an error**. The reader's pass is driven by the writer's commits: an event the writer's stage sets, plus a slow timer as the safety net. So a reader with nothing to copy never spins and never wins a round ("did work" is not "made progress").
- Pausing the writer therefore pauses its readers' indexing, and with it their embedding, which depends on `fts_done`. `hold collection show` says so ("text index written by X, paused").

## What a reader sees

A reader searches its writer's index.
- **At every quiet point** (all members through the same snapshot), that index is byte for byte the one the reader would have built itself. That is RULE 1's test.
- **Between quiet points**, the reader may see records its writer reached first: a newer version of a text, or a document it has not discovered yet. This is the lag that already exists today between any collection's text leg and its vector leg, and it closes at the reader's next pass of that record.
- A reader's mark never claims a version the index does not hold at the moment it is copied. When the writer moves that record forward, the reader's record moves forward on the same chain, its marks reset, and the copy is made again.

## Splitting and joining

A member whose identity changes splits off into its own index. The usual causes are holding a different slice or changing its `update`. The split:
1. **hard-links** the group's directory into the new identity's directory. tantivy's segments are immutable, and `meta.json` is replaced by rename, so the two directories diverge safely.
2. **resets the splitting member's index marks** wherever they differ from the old writer's, by `uid`, `content_hash`, `fts_version`, `entity_version` and `fts_done`, so it indexes exactly what its copy lacks.

It then becomes the writer of its own copy.

**Joining** is the creation of a sister with the same layers (case J without `--relayered`). The carry **hard-links** her sister's index files instead of copying them, so it costs no bytes and no time: tantivy writes `meta.json` and `.managed.json` by atomic rename and never rewrites a segment, so the two directories diverge safely. Her copied manifest already holds her sister's marks. The next worker boot merges the two directories into one (the migration below).

The carry does not move an index while a worker may hold it open: renaming or merging happens only at the worker's boot, before any stage and before the worker opens any index.

## Forget

- Forgetting a member removes only its own directory.
- `.text-index/<identity>` stays while any held collection resolves to it. One that no held collection resolves to is removed by the worker's sweep: at boot, and when the files change.
- If the writer is forgotten, the next member by name becomes the writer (above).

## Where each process finds an index

`Collection.fts_dir` is resolved every time the files are read, by one function, `text_index.place`. It is `.text-index/<identity>` when that directory exists, or when the collection has no index of its own at `<collection>/index` (a new collection starts there directly). Otherwise it is the collection's own `<collection>/index`, until the worker's boot migrates it.

A rename of a directory is atomic, and the web resolves per request, so a search during a migration reads either the old directory or the new one, never half of one. The index cache is keyed on the path, so a moved index is a new cache entry, and the old handle goes unused.

## Migration (the existing copies; RULE 1)

At the worker's boot, before any stage, for each identity whose directory does not exist yet:
1. **Rename** one member's `<collection>/index` into `.text-index/<identity>`, on the same filesystem. Prefer the member whose manifest marks the most documents indexed; on a tie, the first by name. That member becomes the writer.
2. For **every other member**:
   - remove its own `<collection>/index` (the disk saving, the reason for A);
   - reset its index marks wherever they differ from the writer's (the same comparison as a split, in rowid windows with `BEGIN IMMEDIATE`);
   - its stage then copies the writer's marks as the writer reaches each record.

A single collection's migration is the rename alone. The comparison statement is timed against a real manifest before deploy (CLAUDE.md: a plan check is not enough). It walks the reader's rowids in windows and probes the writer's uid index once per row.

## Not in this change, written down

- **Sisters still PARSE each record each.** The reader's rdf stage still runs, and its tags could come from the writer's parse. One parse across a group is the larger saving; it follows the "one parse" work (STATUS #63).
- **The catalogue index is per manifest.** Keying it on `native_id` instead of rowid would let a group share it too.
- **Standard alternative:** tantivy has no multi-tenant index. The usual shape is an index per tenant group with a tenant filter field: here a `slice` field plus a filter at search would let sisters with different slices share one index. It pays once several sisters per group hold different slices.

## Tests the build ships

- **Identity:** equal for a sister with the same layers, update and slices; different when any one of them differs (one test per row of the table).
- **Writer:**
  - indexes exactly once;
  - a reader copies marks and entity tags and writes no index (the writer object is called by the writer alone);
  - a deferred document is no error and is never selected in a loop.
- **Writer forgotten:** the next member writes, and its first pass writes nothing the index already holds.
- **Split:** hard-linked, the marks reset where they differ, then the copy is that member's by-the-book index (compared byte for byte with an index built from scratch).
- **Migration:** two existing copies end as one directory; the other member's marks are reset where they differed; the freed bytes are reported; it is idempotent.
- **Forget:** the shared directory stays while held and goes with its last member.
- **Invariants 2 and 6, and the plan check** of the mark-copy and comparison statements.

## As built (2026-11-10): the amendments to the design above

Each one is simpler than the design's, or removes a case it left open.

| design | as built | why |
|---|---|---|
| `Collection.fts_dir` resolves to `.text-index/<identity>` | `fts_dir` stays `<name>/index`; the language groups are at `text_index.base(fts_dir)`, read from `<name>/state/text-index.json` (the PLACEMENT) per use, cached on its mtime | the catalogue (`_catalog`, keyed on rowids) and the facets live in `index/` and must stay per collection; the web follows a move without a restart |
| every collection's index under `.text-index/<identity>` | only GROUPS of two or more; a collection with no sister keeps `<name>/index`, byte for byte | no move, no risk and no cost for today's node (no sister group, München 2026-11-10); a member left alone goes back to its own index, hard-linked |
| a reader ATTACHes the writer's manifest | a read-only connection on the writer's manifest, per thread; the reader's write is guarded on its row being the one read | ATTACH and DETACH cannot run inside a transaction, and the stage's writes are |
| same record = same `uid` at the same `content_hash` | the same `content_hash`, else the same `txt_hash`, else the same discovery signature (`path`, `mtime`, `size`) | both hashes are NULL on most rows of the node's manifests (wikipedia's 30.9 M rows, Perugia's 79,659) |
| the reader's pass driven by an event the writer sets plus a slow timer | PUSHES: the writer's fts commit gives its uids to every reader's queue, the reader's own rdf commit gives its own; one WALK of the reader's pending rows when its thread starts (the pushes are in memory) | O(batch) per commit; a slow timer re-walked O(pending) rows the writer had not reached, every time |
| migration only at boot; `split` by hard link | `settle` at the worker's boot: FORM (the member with the most indexed documents renames its group directories in), JOIN (marks reset where they differ from the writer's, then the own copy removed), LEAVE (a hard-linked copy back into `<name>/index`); another filesystem leaves the group unshared, said in the log | one function, idempotent, every step complete or redone |
| an index directory no collection resolves to is removed by the sweep | removed by `hold collection forget` of its group's LAST member (part of the forget approved); one found otherwise is set aside (`.stale-…`) and named at boot, never deleted, never adopted | an index no manifest's marks describe must not be read as anyone's |
| a joining sister hard-links her sister's index | the carry (case J) hard-links the language groups (lock files created empty, never one inode for two) and copies the catalogue; when her sister's index is already shared and their identities are equal, she JOINS at once and her linked copy goes; the proof compares the index AS READ (`text_index.index_digest`) | no byte copied, no reset at the next boot |

**A reader never writes the index**: `fts.index_books` and `rewrite_without` raise
`fts.ReaderWrite` for it, its deletes are skipped (its writer makes the same
ones), and `_next_book` selects no fts work for it — so its documents waiting on
the writer cost no round. `hold collection show` says whose index a collection
reads (`text_index`).

**Measured: nothing yet.** The node holds no sister group today; the first
same-layers sister (case J) is the first measurement — the carry's linked
bytes, the boot's `settle` (its `reset_differing` compares a manifest's rows
on threads, ~O(rows)), and the reader's copy rounds (`fts` log lines "copied
N documents' index marks from W").
