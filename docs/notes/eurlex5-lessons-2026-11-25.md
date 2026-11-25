# EUR-Lex5: every problem, and what the platform must do or refuse

The operator, 2026-11-25: "Every problem encountered with a collection must
become a lesson learned reflected in RAGE", and "All the workaround made on
the spot must be reflected in RAGE so that the same collection made from
scratch can run smoothly".

Each row below is a problem EUR-Lex5 met, written for a plugin author who has
never seen EUR-Lex. It names the platform's lesson: what the platform must DO,
or must REFUSE. Owner: **built** (the commit), **legal** (the eurlex plugin,
mine to build), or **engine** (architecture's: the platform's API, worker
or checks). The engine rows are proposals until architecture decides the API.

## A. A document must never stop the node

| # | Problem | The platform's lesson | Owner |
|---|---|---|---|
| A1 | A citation pattern backtracked without end on one document. hu 32007L0019's list of numbers, 2^n; pl 32018L0410's list of articles before an act, 12^n. The in-process fallback held the worker 21 minutes, every collection with it. | Every pattern that reads corpus text (resolvers, chunkers, readers) is run in CI on a HOSTILE set (long lists, every separator, every language) in a child process with a deadline. A plugin whose pattern does not end is refused at test time, not met in production. | built: #858, #872 (the hostile set in a child); tests/test_refs_eurlex_linear.py |
| A2 | Work recomputed in the worker's own process after a pool timeout had no bound. | No per-document work runs unbounded in the worker. A document that does not come back is named in the log (collection, stage, uid) and gets its error at its turn. It is never retried silently and never counted against the pool. | built: #859, and the "[parse pool]" line now naming collection and time |
| A3 | The admission estimate (26 x bytes, measured 11-19) went stale twice. The chunker's bounded spans (11-20) lowered the real peak, and figures (TIFF) cost about 3 a byte, not 26. Units of 600 MB-1 GB were refused as TooLarge forever, their text never held. | A plugin's peak estimate is per KIND of byte (text, figure, package), measured on its largest units, and re-measured when its parse changes. A TooLarge refusal states the estimate and the measurement it rests on. | built: f91d6a63 (`parse_size` weights figures). engine: tie the re-measure to a parser or chunker version change |
| A4 | Large package units beside three other collections under one 24 GiB cap. The pause-on-memory rule lived in an operator's monitor script. | The worker yields a collection under memory pressure by itself and says so. No operator script is needed for it. | engine: in progress (architecture) |
| A5 | A memory kill wrote "not computed again until the cause is known" on EVERY row of the part in flight. The two units that carried it were bystanders (256 MiB each, measured). | The kill's error names the part. The row whose child was killed is told apart from the bystanders, so the OOM rule holds the cause, not the innocent. | engine |

## B. One unit, one live row

| # | Problem | The platform's lesson | Owner |
|---|---|---|---|
| B1 | Unchanged units got new ids and their old rows stayed live: 405 units held twice. Discovery dated a unit by one rule (the wrapper without the tree's listing), the parse read it by another (with it). The lone-wrapper rules of 11-21/23 split them. | A document's id is decided by ONE function that discovery and the parse share, with the same inputs. Test: for every fixture unit, the id discovery gives equals `ref_of_unit`, and equals what the parse reads. | built: #876 (84a3aaa8); tests/test_eurlex_unit_day.py |
| B2 | A unit's id moves when its form moves (Cellar re-formed 105 k units into packages on 11-21: tree day became OJ day). `superseded_ids` must name every day a rule ever gave, and a FUTURE rule change must be added by hand. | A plugin declares ONE DOCUMENT PER UNIT. Discovery then retires every other live row at the unit's locator when the unit's ref arrives, whatever rule gave it, and every scan heals what is held twice. A platform check, `hold check`, refuses a locator held by two live rows. The rule cannot be general: a pubmed or wikipedia file holds many documents. | engine (proposed); legal: `superseded_ids` names all known days (#876) |
| B3 | 37,695 + 405 duplicates were repaired by hand (`dev retire-superseded`: dry run, a go, `--yes`). | With B2, no collection ever needs the hand repair. A change of the dating rule is treated like a version change: counted (dry run) and said before it moves an id. | engine (B2); legal: the tool stays for an old manifest |

## C. Discovery: cost, progress, resumption

| # | Problem | The platform's lesson | Owner |
|---|---|---|---|
| C1 | The startup scan read every unit, about 970 k, on one thread at every worker start: 28-55 minutes. Packages were read whole (to 815 MB) only to date the unit, and a non-Formex unit's tree was read twice. | Per-unit discovery work scales with what CHANGED. A unit's derived facts (its day, its id) are kept by the physical identity of its files (names, sizes, mtimes) and the code that derives them. An unchanged unit is only listed and statted. | built: #866 (38720e9a, a package read by its path), #884 (52d41434, the tree read once). legal: the day kept by physical identity (building) |
| C2 | Threads per language were measured SLOWER (1 thread 0.29 s, 4 threads 2.08 s on cached units: GIL contention between short syscalls). | Parallel discovery is processes or a cache, never threads for Python-bound walks. A speed-up is measured before it ships. | recorded here; not shipped |
| C3 | The scan was silent between "corpus scan (startup)" and "discovery done". Its position was read with py-spy. | `DiscoverState` gives a plugin a `say` that names the collection, and a walk reports its position every minute. | engine |
| C4 | An interrupted walk restarted from zero, because eurlex yields its refs sorted by id, only at the end. | With C1's cache a re-walk of what was walked costs only a stat. A plugin whose ids must sort keeps its walk's results by physical identity, so an interruption loses nothing. | legal (C1's cache) |
| C5 | Every restart rescanned an unchanged corpus. | As C1. | legal (C1) |

## D. The stages must never wait without saying on what, nor wait for what no one asks

| # | Problem | The platform's lesson | Owner |
|---|---|---|---|
| D1 | The stages stood still behind the slice scope, the cursor below MAX(rowid), with one line said once. | A stage that waits says what it waits for (cursor N of M; the scan it needs) at every report, not once. | engine |
| D2 | A collection paused mid-scan, then resumed, requested no scan. The slice scope never caught up until a worker restart. | Every wait has its request. A resume asks for the scan its pause cut short. | engine (sent) |
| D3 | The look-ahead never ran for a carried collection: carried rows bear embed marks, and the chain skipped them. | Every scheduling optimisation is tested on a CARRIED or rechunked fixture as well as a fresh one. | built: #867 |
| D4 | The large lane's executor was one-use (gh-115634). | Built. | built: #869 |
| D5 | A heavy document holds its whole fts batch while the pool idles. | A batch is not held by one part. | engine (open) |

## E. A document's text, and its absence, are stated

| # | Problem | The platform's lesson | Owner |
|---|---|---|---|
| E1 | 57 documents were indexed with no chunk and told apart by hand: 16 scans with no text layer, 41 Formex acts whose source holds only a title (empty PREAMBLE and ENACTING.TERMS). | A document with no chunk must state WHY, as an issue. A platform check refuses a chunkless document that states none. | legal: issue `title-only` (building); `no-text-layer` exists. engine: the check |
| E2 | Formex readers lost words: 43 of 175 acts in a section, a note, an inclusion or a package. | Invariant 11 as a TEST HARNESS: every word of the origin's files is in a section, a note or a kept tree, run on each plugin's real fixtures. | built for eurlex (tests/test_eurlex_coverage.py); engine: the generic harness (handoff 2026-11-21 §3) |
| E3 | A lone wrapper labelled another act ((01) for (02)); special-edition volumes holding two acts' files under one FIN.ID; an OJ older than the act. | The origin's own LISTING (the tree) outranks a label inside a file. Discovery and the parse read with the same listing (B1). | built: e3c94810, #876 |

## F. The origin and its bulk

| # | Problem | The platform's lesson | Owner |
|---|---|---|---|
| F1 | The Data Dump holds no zip member: 126,652 units lack their Formex package. It files a TIFF under `.xml`, files in each other's place, a wrapper under another name. | Dumps are transports, not origins. A bulk-first LEDGER records what the bulk gave, what it cannot give and why, and what the API may fetch. Every bulk-fed plugin runs a units-against-their-names check. | built for eurlex (eurlex_dump ledger, tools/measure/eurlex_units_check.py); engine: the generic ledger and check |
| F2 | Cellar serves some Formex as ONE zip; it was read as XHTML. | Read the form the origin serves (`formex.unpack`) and hold it as served. | built (formex.unpack) |
| F3 | "Do not fetch" had to be written into a TOML by hand. `hold config apply --set acquire_enabled` refuses it, and 07 §7.8 does not list it. | A collection's file has a documented key for it. | engine |

## G. Re-deriving a held collection

| # | Problem | The platform's lesson | Owner |
|---|---|---|---|
| G1 | The restart had to start from the problematic parts (`rederive --ids --first`, `--release`, the units check, the problem slice). | Re-deriving a held collection is problem-first by its tool. A collection built from scratch derives each document once and needs none of it. | built: #829 (`dev rederive --first/--release`), tools/measure/eurlex_problem_slice.py |
| G2 | The validity chain's rule 2 would have ended 145 k Normattiva and LEGI versions wrongly. It was measured before it moved anything. | Every re-derive pass counts what it will move and says it before applying. | built for validity (tools/measure/validity_changed_since.py); engine: the general rule |
| G3 | Stop rules (hangs, "done not rising", chunkless, memory, scan and scope bounds) lived in an operator's monitor. | They become the worker's own health checks, reported, with the collection paused where a check fails. | engine |
