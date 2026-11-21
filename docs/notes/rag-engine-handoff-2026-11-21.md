# To architecture: what the EUR-Lex5 review gives the RAG engine, 2026-11-21

The operator, 2026-11-21: "Any improvement here goes to architecture to
become part of the rag engine api." The review (eurlex-review-2026-11-21.md)
fixed the collection in the engine's own modules. This note lists every piece
that is the ENGINE's — a hook, a behaviour, a tool — for architecture to
review, adopt as API, document in 06/07, rename or generalise. Nothing is
deployed. The work is on branch `ccr-bbc107f7-4at6fd`, from 3da4f9c7 (#794).
It goes to `main` as one PR once C11 (the XHTML reader) and P8 (chunker v4)
land. Until then architecture can review commit by commit.

## 1. Plugin protocol: new optional hooks (read with a default; absent = as before)

| Hook | Where the engine reads it | What it answers | Implemented by | Commit |
|---|---|---|---|---|
| `superseded_ids(ref) -> list[str]` | app/discovery.py `_apply_batch` | the ids the unit was held under before (a form change moved its id): discovery retires those rows at the same locator — out of every index, validity forgotten, tombstoned with the reason | eurlex | 8c8f03de |
| `parse_size(row) -> int \| None` | app/plugin_io.py `parse_size`, app/parallel.py `learn_parse_sizes` | the bytes a parse reads, where `size` misleads (a zip: its members unpacked, from the central directory) — the pool's admission estimate | eurlex | 15b5b5c3, 96d4bb01 |
| `VALIDITY_ENDS_AT_AMENDMENT` (class attribute) | app/validity.py `of_doc`, `rechain` | the base holds originals, not every version: a held amendment ends the text it replaced (chain rule 2) | eurlex | (this branch) |
| `ArticleNotes.changed` (field) | app/ask/articles.py `_ended_note` | the words for "text until {day}; amended since, the amended text is not held" | the four legal refs | d39e62f1 |
| `UnitFetcher.prepare` may raise `spec.HeldBack` | app/acquire/engine.py | a bulk that must be used first cannot be: the pass ends, said, the per-unit API never asked; `prepare` runs under the pass's listing bound and stop | eurlex (`TreeUnits`) | 0786761f, 49f59827, 8ae7ad00 |

## 2. Engine behaviour changed (every collection)

- **Validity chains** (app/validity.py; docs/05 amended 2026-11-21):
  - `forget` always recomputes the chain (V1);
  - `record` recomputes the Work a version leaves (V2);
  - CHAIN RULE 2, for a plugin that declares `VALIDITY_ENDS_AT_AMENDMENT` (EUR-Lex: originals only, its consolidations a slice): a Work's last version also ends at the first day a held statement says its text changed (`first_change_detail`). Statements are dated by the FIRST version that states them, and a day from `UNDATED_FROM` (2999-01-01) names none. A collection whose origin dates every version keeps the origin's word: Normattiva's 20,365 and legi_F's 14,382 would otherwise have been ended. The declaration is kept in the manifest (`validity_changes_end`);
  - recorded and forgotten statements recompute the Works they target;
  - held manifests are re-chained once by the index maintainer (`CHAIN_VERSION`, `rechain`, worker `_rechain_validity`);
  - commits 9ca1f22c, d39e62f1, 40ecdee9. Before deploy: `tools/measure/validity_changed_since.py` per manifest. München's first count found the dating defect; the second count is under way.
- **Parse pool** (app/parallel.py):
  - P2: a watchdog per part; answers kept; the pool back after 300 s on probation;
  - P7: a stop honoured within 1 s at part boundaries (`controlled`);
  - P3: parse sizes;
  - a `doc` task kind, plain title / sections / extra, for a bounded parse outside the stages (history on request, V5).
- **Worker** (app/worker.py):
  - P1: a memory deferral is never an error, and the selected row's peers are written;
  - V6: the fts and embed marks are written only where the validity columns did not move since read;
  - #776: a language leaving a group drops the old group's rows at the rdf stage.
- **Discovery:** P5, a re-scan's language move drops the old group's rows before its commit (8c8f03de).
- **Text index** (app/fts.py, app/lookahead.py):
  - `groups_for_lang` and `stored_by_group`;
  - P6: a `mul` record leaves the groups its chunks no longer reach;
  - P4: an abandoned look-ahead stops its parse.
- **Derived check:** `derived_check.verify` reads every group and its strays, and writes under a bounded `_begin` (#776).
- **Acquisition engine** (app/acquire/engine.py):
  - Q3: a failed fetch records the version that failed, so the backoff holds across a moved date;
  - a held file whose newer version fails is not done.
- **Refs store:** the column check races; it is now done under BEGIN IMMEDIATE (787261aa).
- **Logs (O1):** app/log_file.py, rotating `/data/logs/worker.log` and `web.log`, 384 MiB at most each; the web runs through app/web_main.py. THE IMAGE MUST BE REBUILT (Dockerfile CMD).

## 3. Patterns built for EUR-Lex that belong in the engine (not generic yet)

Each one is proposed, not built generically. Architecture decides the API.

| Pattern | Built as | Generic form proposed | Pays for |
|---|---|---|---|
| **Invariant 11 as a test**: every word of the origin's files in a section, a note or a kept tree | tests/test_eurlex_coverage.py (Formex, 175 real acts), the rendition's coverage test | a harness each plugin feeds with its origin files and its "kept beside" list, run on real fixtures in CI | Normattiva, LEGI, NeuRIS readers: the same 43-acts-of-175 losses were found only by this test |
| **One unit, one document** | `superseded_ids` + `louise dev retire-superseded` (app/hold/identity.py) | a protocol rule: an id derived from anything that can change (a form, a day) declares its old ids | any plugin whose id is not the origin's own |
| **The origin's units against their names** | tools/measure/eurlex_units_check.py (a TIFF under `.xml`, files in each other's place, a wrapper under another name) | a check every bulk-fed plugin runs on its corpus | dumps are transports, not origins |
| **The bulk-first ledger** | eurlex_dump's incomplete record: flock'ed, append-only marks, a build lock, a per-member "the bulk cannot give it" | an engine ledger: what the bulk gave, what it cannot give (with the reason), what the API may fetch and when | NeuRIS, Normattiva (CLAUDE.md "bulk first", once a day) |
| **History on request** | app/ask/history.py (Cellar) | hooks `versions_of(work)` and `fetch_version(...)`, the bounded fetch and parse in the engine | LEGI, Normattiva past versions |
| **The chain measured before it moves** | tools/measure/validity_changed_since.py | the maintainer's re-derive passes count what they will move and say it before applying | every one-off re-derive |

## 3b. An engine gap found on the way (not built)

- **Entity tags have one global version.** `config.ENTITY_VERSION` re-tags every document of every collection. The EUR-Lex citation grammar now reads 24 languages, and it also reads more in the old seven: implementing acts, "des Artikels", "n.o", the act nearest the article. Documents already indexed keep their old tags until their next text-index pass. A bump would re-index pubmed and wikipedia for a few EU citations. **Proposed:** a version per resolver (`refs.RESOLVER`'s parts), so that only documents tagged by a resolver that changed are re-tagged. The four legal collections are re-indexed by the chunker v4 move anyway.
- **The refs store** switched to WAL without a retry, so a brand-new store opened by two processes at once failed with "database is locked" (609a8403). The same pattern may sit in other stores opened at boot (the node's own state, the manifests' first open). **Proposed:** one shared helper for opening a store in WAL.

## 4. Open, on the same branch

- C11: the XHTML/HTML reader's division headings, final formula, legacy footnotes.
- P8, chunker v4: the shared legal space's ids carry the chunker version. Moving it re-keys every vector row of the four collections and their snapshots. docs/notes/legal-chunker-v4.md §4 is the plan; architecture approves it.
  - **Built for review, never run on a node:** amendment A1k (02 §2.4, marked PROPOSED), `louise dev carry <from> <to> --rechunked [--dry-run | --yes]`. The target's space differs from the source's only by the layers' chunker version. The manifest is copied as a relayered sister's (A1i reset). Then each window of documents is cut at the target's layers on the parse pool. A new chunk takes the vector bytes of a row the same document holds with the same text hash, never matched by position. The rows go through the embed stage's writer (`write_pending`). A complete document is marked embedded; the embed stage gets the rest.
  - **Where:** app/hold/rechunk.py (the re-key) and app/hold/carry.py (the refusals, the copy, the CPU reference across an A1k pair); tests/hold/test_rechunk.py, 30 tests; notes §4.3 and §4.5. The drift tool now counts by held text, as the carry does; its dry run matches the carry's numbers (test).
  - **For architecture to settle:**
    - adopt A1k, or not;
    - the per-window read of the source by `book_id` against a sort-merge scan (`keep_producer_scan`'s pattern), decided by a pilot's timings;
    - Lance `merge_insert("id")` was not used: `id` carries no index, so it would read the whole target table every window. The resume deletes the stopped window by `book_id` instead;
    - the creation side (§4.6): the recommendation is `--re-create --chunker-version 4`, deriving from the predecessor's Codex, because no recipe makes Normattiva's. `CHUNK_VERSION` and `SHARED_LAYER` stay 3 until the move.
- Seen, not this review's: `tests/hold/test_block_origin.py` needs `indexed_bzip2`, and `test_process_signals` needs jinja2; neither is in this sandbox.
