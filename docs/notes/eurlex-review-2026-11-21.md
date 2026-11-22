# EUR-Lex5 review, 2026-11-21

The operator stopped EUR-Lex5 (paused, frozen, out of serving: München,
09:48–09:53Z) and ordered a review of the collection and of the engine:
"It will not start again until any … issue has not been understood and
fixed … Any further run of eurlex has to start from the problematic parts
and monitored every 5 minutes." The legal session is in charge; München
runs its exact steps.

Sources: München's read-only measurements (`/root/measure/eu5-review/` on
the node, items a–u), and four independent code reviews, each reproducing
its findings (scratch tests under the session's scratchpad `review-*/`).
Every row below was reproduced or traced to its lines; none is a guess.

## The collection as it stood (09:36–09:47Z)

970,426 documents: rdf 931,552, fts 929,975, embed off; 486 errors. Units by
form: Formex 470,755, legacy HTML 309,936, Formex package (zip) 105,495, PDF
only 46,593. Issues: entry-into-force-before-publication 129,887,
start-from-document-date 48,509, end-before-start 908, no-text-layer 418.
1,909 documents indexed with 0 chunks. The node: kernel panic 01:56Z (ZFS
write thread, a general protection fault in the slab allocator — not the
app's; no OOM before it), whole node paused by the operator 09:46Z.

## Findings

Owner: L = the legal session's code, A = architecture's (the engine), both
fixed by the legal session now (operator, 2026-11-21).

| # | Where | Defect | Effect | Owner |
|---|---|---|---|---|
| C1 | discovery + eurlex `_unit_day` | a unit re-formed into a zip got a new native id (its wrapper's OJ day, not the tree's work day); discovery never retires a row its unit no longer yields | **37,695 units held twice**, both indexed | L (#774) |
| C2 | formex `_corrections` | a real corrigendum's text is inside DESCRIPTION (QUOT.S, a TBL); only OLD/NEW.CORR were read | every Formex corrigendum indexed empty | L (parser 5) |
| C3 | formex `_recitals` | DIV.CONSID (headed recital groups) read into no section | 6/140 acts; up to 219 recitals each | L |
| C4 | formex `parse` | an annexed document whose root is not ANNEX (an ACT, GENERAL, AGR) kept only as a tree; an AGR of COLL.LETTERS read as nothing; an agreement's P recitals into meta only | ~5/140 acts; 3/16 agreements empty | L |
| C5 | formex `lines` | text buffered before a TBL (through P, QUOT.S) dropped | 11/140 acts | L |
| C6 | formex `lines` | TI.BLK inside a table dropped | 5/140 acts | L |
| C7 | formex | consolidation marks (CLG PIs) in LIST, CORPUS, TITLE, CONS.DOC, NOTE not collected | up to 60 % of a consolidation's marks | L |
| C8 | formex `inline` | DLIST/TERM/DEFINITION/CELL glued; a table nested in a CELL emitted twice | 4/140 acts | L |
| C9 | formex `own_wrapper` | a single wrapper is taken whatever act it names (hr: 3 acts of 2009–11 hold a 1998 act's package) | another act's text in the record | L |
| C10 | formex | a wrapper naming no main file held reads as an empty act | indexed empty | L |
| C11 | rendition | quoted article headings in XHTML amending acts become articles; annex headings number-first (hu, lv, lt, et, ro) missed; "14a. cikk" missed; correlation-table cells become articles; footnotes all in the last section; recitals one section | every XHTML/HTML amending act; annexes merged into the last article | L |
| V1 | validity `forget(chain=False)` | the chain is left to a `record` that may never come (a re-parse stating no start, a refused re-parse) | a Work out of the in-force filter | L (A1c) |
| V2 | validity `record` | a version moving to another Work leaves the old Work's chain stale | no version of it shown | L |
| V3 | ask/articles, validity.describe | with no history slice held, an amended original is pinned as "text in force today" | outdated law as current | L |
| V4 | ask/history | a version fetched "as of" a day becomes the shown, open-ended version for every later reader | outdated law as current | L |
| V5 | ask/history `_fact` | the fetched version parsed outside every bound, in the worker, in acquire-only mode; a refused parse said "fetched" | OOM risk; silent failure | L |
| V6 | worker roll vs fts/embed | the daily validity roll's reset can be overwritten by a stage that read before it | stale `shown` in the index and vectors | A |
| P1 | worker `_execute_step` | a `MemoryWait` (a deferral) on the selected row written as a permanent error | the large acts out of every selection | A |
| P2 | parallel `compute` | one deadline for the whole batch; a timeout recomputes the batch in the main process; after 3 the pool is off for the process's life (`reset_failures` never called) | **the fts crawl (0.7 → 73 ms/doc)**, one core | A |
| P3 | parallel estimate | a zip package's size is its compressed bytes (Formex deflates ~4.3x) | a big package in an uncapped shared child: OOM | L (#774) + A |
| P4 | fts look-ahead | an abandoned take does not stop its compute; the row is parsed twice, the lane held | doubled large parses, then P1 | A |
| P5 | discovery + rdf | a re-scan that changes `lang` writes it before the rdf stage compares; the old group's rows stay | the pubmed duplicate again by another path | L (#776) |
| P6 | fts `index_books` | a `mul` record that loses a section keeps its rows in that group | stale rows | A |
| P7 | parallel | a stop is not checked inside a batch (a large part: ~730 s against a 120 s grace) | SIGKILL mid-unit | A |
| P8 | chunking_legal `_SENTENCE_END` | Greek and Cyrillic sentences never split | an over-long el/bg paragraph cut mid-sentence (needs chunker v4: a space change — the operator's decision) | L |
| Q1 | cellar + work_trees | a tree kept 6 h is landed whatever the listing's newer date; the engine records the unit current | a stale unit for good | L (#759) |
| Q2 | cellar `TreeInventory.list` | one due unit failing as an episode returns the due list forever: no listing again | no new act ever fetched | L (#717) |
| Q3 | engine backoff | `_upsert_failed` keeps the first failure's date; a moved date reads as a new file, asked at once every pass | retry storms, every collection | A |
| Q4 | eurlex_dump incomplete record | a per-PROCESS lock; the CLI build races the worker | lost re-form and fetched marks | L |
| Q5 | eurlex_dump | `mark_fetched` rewrites the whole 15 MiB record per unit; `incomplete_due` re-parses every held re-form unit per listing | ~11 min/h serialized, 26 GiB/h written | L |
| Q6 | cellar prepare / eurlex_dump | an unreadable newer dump: the pass goes to Cellar for everything (bulk first broken); an unreadable item zip stamped built | API instead of bulk | L |
| Q7 | eurlex_dump build | no stop checked during `prepare`'s build | SIGKILL, the build again | L |
| Q8 | eurlex_dump | `tried`/`fetched` never cleared by a newer dump | never asked first again | L |
| O1 | deploy | the worker keeps no log file; every deploy and the panic erased it | the 11-19/20 evidence lost | A |
| D1 | eurlex start of validity | 129,887 acts state an entry into force before their OJ publication (a decision takes effect on notification, Art. 297(2) TFEU); validity starts at the publication | in-force filter wrong between notification and publication | the operator's decision |

## Fixed (branch `ccr-bbc107f7-4at6fd`)

| Commit | Findings | Held by |
|---|---|---|
| b54a82ae | C2–C10: the Formex reader | tests/test_eurlex_coverage.py — 175/175 acts of 14 shapes, every fragment and every CLG mark (before: 43 acts, 7,059 fragments lost) |
| 8c8f03de | C1, P5: one unit, one document; the 37,695 repaired by `louise dev retire-superseded` | tests/test_eurlex_identity.py, tests/test_discovery_language_move.py |
| 9ca1f22c | V1, V2: every chain a version leaves is recomputed; V5: a version fetched on request read on the parse pool, bounded, only where the mode parses, never silent | tests/test_validity_chain_review.py, tests/ask/test_history_fact.py |
| d39e62f1 | V3, V4: a held amendment ends the text it replaced (05 amended); held manifests re-chained once | tests/test_validity_changed_since.py; before deploy, `tools/measure/validity_changed_since.py` per manifest counts what the re-chain moves |

| a7e0e8a8 | D1, choice B (decided unattended, the operator's "Run unattended"): an original starts at its stated entry into force, before its OJ publication where the notice says so (Art. 297(2) TFEU), never before the act's own date (`entry-into-force-before-adoption`) | tests/test_eurlex.py |

| 515b3dc9 (merge) | Q1–Q8: a kept tree serves a unit only as late as its listing; a deferred due unit waits its backoff, the listing runs; a failed fetch records the version that failed; the incomplete record flock'ed and appended to (426 → 0.1 ms a mark; 280 s → 1.8 s a listing); an unreadable dump ARCHIVE holds the pass back, an unreadable MEMBER leaves its unit to Cellar after the bulk; an unreadable `datadump.json` said; the build stops at a chunk boundary and resumes; a newer dump clears tried/fetched; one build of a corpus at a time | tests/acquire/test_eurlex_acquisition_review.py, test_fetch_backoff.py, test_common_engine.py |

| merge of the engine branch | P1: a memory deferral is never an error, its peers written; P2: the pool's watchdog per part, answers kept, the pool back after 300 s on probation; P3: a package's parse size from its central directory, unpacked; P4: an abandoned look-ahead stops its parse; P6: a `mul` record leaves the groups its chunks no longer reach; P7: a stop honoured within 1 s at part boundaries (was ≈222 s); V6: the fts/embed marks written only where the validity columns did not move; O1: /data/logs/worker.log and web.log, rotating, 384 MiB at most each (the node runs app.worker_guard: the children's lines included; the web image rebuilt) | tests/test_worker_memory_deferral.py, test_parallel_watchdog.py, test_parallel_parse_size.py, test_fts_lookahead_stop.py, test_fts_mul_regroup.py, test_validity_marks.py, test_log_file.py |
| 40ecdee9 | V3/V4 corrected on München's count: a statement dated by the first version that states it, an undated day (2999-01-01) none — legi_F's 161,627 and Normattiva's 28,418 were repetitions | tests/test_validity_changed_since.py |
| 787261aa | the refs store's column check raced (found by the engine run) | tests/test_refs_store_open_race.py |

| e1addb75 | chain rule 2 only where the plugin declares it (`VALIDITY_ENDS_AT_AMENDMENT`: EUR-Lex): München's second count, Normattiva 20,365 and legi_F 14,382 versions ended against the origin's own dates, EUR-Lex5 76,922 as intended | tests/test_validity_changed_since.py |

| 63c1fa2c (merge) | C11, the XHTML/HTML reader: quoted headings, table cells and nested elements never articles; number-first annexes and lettered articles in the 24 languages; footnotes to the notes, numbered recitals as sections; division headings as label prefixes (labels identical to Formex 3 → 10 of 10 dual-form acts); the final formula and signatures a "Final" section; legacy footnotes filed under the section that calls them (1,349 notes in 126 of 128 acts); legacy amending acts' articles 54 → 56 of 57 against Formex (the last one is the origin's: 31998R1191's item holds the next act); the flaky dump-build test was its own zip timestamps | tests/test_eurlex_rendition.py (coverage on 374 real items) |
| 24d90986 | an article's label read in the 24 languages, its number in one spelling ("14 bis" = "14α" = "14а" = "14a") | tests/ask/test_article_citations.py |
| f4b55b53 | test_mirrors isolated from the process-wide transfer manager | — |
| 873a49f7 | the act and its article cited in the 24 languages (en fr de it es pt nl before), each language's forms read in five acts' own text in it (from Cellar): every language reads ≥ 0.96 of English's act citations and ≥ 0.94 of its (act, article) pairs on those acts; the article nearest the act (a heading's "Articolo 84" was taken for the sentence's article 2, every language); per question 15.3 → 17.2 µs mean, per act at indexing 14.1 → 7.1 ms. (Coordinations and lists: the next row.) The 17 languages' documents get their cited acts' tags (doc_keys, local) at their next text-index pass: the re-derivation below | tests/ask/test_article_citations_24.py (215 real citations, 72 negatives, a Hungarian and a Greek question pinned) |
| 83b785c1, 698c4eea, b4ca6b43, 430820d5 | the question forms (what a reader types and no OJ text writes: bg "чл.", el "άρθ.", sl "5. člen", lt "5 str.", the old "art."), apart from the evidenced forms; every article of a list ("Articles 6 and 8 of …", a range's two ends, never what lies between); every act of a coordination ("Regulations (EU) 2016/1036 and (EU) 2016/1037"), an article with it the first act's unless "of each of"; three wrong locations in the evidence corrected. On the five acts every language reads ≥ 0.97 of English's act citations, ≥ 0.95 of its (act, article) pairs; per question 21.0 µs mean, per act 7.27 ms | tests/ask/test_article_citations_24.py (584: + 21 typed questions, 120 lists, 120 coordinations) |

| e3c94810 | C9 corrected on München's reading of the 3 units the v4 pilot refused (each act's own text in its files): a lone wrapper's OJ read from its name, never from the act's adoption DATE (31959R0005, 31964D0732 hr: Special Edition packages); a lone wrapper the expression's own tree lists is that act's whatever its NO.CELEX says, said as `formex-wrapper-names-another-act` (31958D1006(02), the decision printed twice); an OJ older than the act still refuses (the 2009-11 hr acts holding a 1998 act's package). The pilot on e3c94810: 0 errors in 2,000 | tests/test_eurlex_coverage.py |

NeuRIS's 0 amendments, checked (München, read-only, 2026-11-21): 0 of 5,536 records hold `active_modifications`, and 0 of the corpus's 15,785 LegalDocML files (zip members included) holds `activeModifications`, `passiveModifications`, `textualMod` or an `analysis` block (control: `akn:meta` in 15,785 of 15,785, `akn:lifecycle` in 5,551). The test-phase origin states none; the reader drops nothing.

In progress: P8, choice A: chunker v4 built behind its
version and measured per collection first — chunk ids carry the version,
so moving the shared legal space re-keys every vector row of the four
collections and their snapshots (docs/notes/legal-chunker-v4.md, owed).

## The restart (after every row is fixed and deployed)

Each step is München's, on the legal session's word, reported every 5
minutes; a step whose numbers are wrong stops the next. EVERY DOCUMENT IS
DERIVED ONCE (RULE 1), which fixes the order.

Measured read-only on the node (2026-11-22, the units check and the problem
slice from 6d933d04; /root/measure/eu5-restart/):
- 932,731 live documents, and 37,695 duplicates that retire-superseded tombstones;
- the problem slice: 300,093 (by reason: package lacked 126,652, package 105,495, corrigendum 49,769, duplicate-kept 37,695, included document 36,250, chunkless 1,909, unreadable or not this act's wrapper 630, errored 460, no-text-layer 418, largest 200, item-not-held 130, in another's place 82, form-disagrees 15, misnamed 14, formex-not-this-act 7);
- the rest: 632,638.

1. **After the deploy, the v4 move first** (docs/notes/legal-chunker-v4.md;
   A1k, once architecture approves). EUR-Lex5 holds no vectors (embed off),
   so it is re-created at v4 and derived ONCE at v4. Derived at v3 first, its
   932k documents would be derived again for v4.
2. `louise dev retire-superseded EUR-Lex5` (dry run, then `--yes`): the
   37,695 duplicates retired.
3. **The re-forms**: the 126,652 units whose tree lists a Formex package
   they lack (`reform.txt`; ~2,175 a language of 2025 among them). Bulk
   first: the Data Dump's re-form build, then Cellar for what the dump does
   not hold (the incomplete record, Q1–Q8). Each unit is derived when its
   files land (discovery resets it), so they are in neither list below:
   derived now, they would be derived twice and their first text replaced.
4. **The problem slice**: `louise dev rederive EUR-Lex5 --ids problem.txt
   --retry-errors --yes` (`reform.txt`'s units out of it).
   - `--retry-errors` clears a listed document's error, so the corrected reader
     reads it again (C9's refusals were the acts' own text); a tombstone keeps
     its error.
   - Checked when drained: 0 chunkless, 0 errors but the stated refusals, and
     the coverage of a sample read.
5. **The rest**: `--ids rest.txt`. Never the whole collection again.
6. Only then `acquire_enabled = true` for the routine passes, and EUR-Lex5
   served again.

Rebuild the lists after step 2 and before step 4, because the re-form moves
units: `eurlex_units_check --list-flagged`, then `eurlex_problem_slice
--toml <its stopped TOML> --units flagged.tsv --largest 200 --out-dir <dir>`.

## Order

1. The reader (C2–C11) with a COVERAGE TEST: every text fragment of the
   files an act's wrapper names (and their inclusions) is in a section, a
   note or the record's kept trees, on real acts of every form and
   language — invariant 11 as a test, not a review.
2. Identity (C1, P5): one unit is one document; the repair of the 37,695.
3. The engine (P1–P4, P6, P7, V6, O1) and acquisition (Q1–Q8).
4. Validity and history (V1–V5).
5. Then EUR-Lex5 re-derived whole (parser 5 amended in place: nothing
   published, RULE 1), the problematic units FIRST (the duplicates, the
   1,909 empty ones, corrigenda, the swapped/shifted, the packages, the
   large ones), München reporting every 5 minutes; the rest only after
   that slice reads right.
