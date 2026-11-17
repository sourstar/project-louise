# Collection updates by cadence, and the annotations cascade

*Design, 2026-11-17 (§2 and §3 decided the same day). Operator, through München: (1) "Monthly full dumps at
night" for wikipedia, as a first-class update of any collection from its
Codex's `update` block, wikidata next; (2) "it must add a signal to wake up
all the annotations relying on that collection's updates", clarified: "To
trigger a cascade of updates of the interested annotations once wikipedia
has completed its one." Part 1 is built (§6, 2026-11-17, deploy pending);
part 2, the cascade, is built (§4 "As built: the keeper", 2026-11-17, deploy pending).*

## 1. The update, any collection with `update.cadence`

Today nothing acts on `update: {cadence: monthly, day: 1, grace_days: 7}`.
The node holds wikipedia's 2026-10 snapshot and discovery reads only the
pinned `<wiki>/<run>/` dumps (`wikipedia/source.py` `discover`). That is 11
languages, `slices = "all"`, about 62 GB of dumps.

An **update pass** per collection, a maintainer of the worker, run under
`deploy/supervise-pass.sh`:

| step | what | already built, reused |
|---|---|---|
| 0. due | the period after the newest snapshot's has closed (09 §9.4 `snapshot_id_for`), and the origin says the run the cutoff names is COMPLETE for every held slice | Wikimedia's own completion record: `dumpstatus.json` of each wiki's dated run, jobs `articlesmultistreamdump` and `pagepropstable` at `"done"` (the standard; never a page scrape) |
| a. fetch | the run's files for every held slice, through the transfer budget (invariant 5) and the origin's etiquette, resumable per file | `acquire` with the dated-dumps delta mode and `spec.slice_profiles` |
| b. the run | the collection reads the new run (§2 decides how) | `discover`'s pin |
| c. records | `records build`, carrying every record line whose origin bytes are unchanged (`--records-from`, `parser_carried`) | the LATER build (#45) |
| d. snapshot | `snapshot create` as the delta of the previous one: content-hash added / changed / tombstoned; only the parts that changed re-issued | 02 §2.2, part-deltas.md |
| e. embed | only the changed chunks; vectors carried where `content_hash` / `source_hash` is unchanged (RULE 1) | #371's `source_hash` reuse |
| f. old run | the previous run's files deleted from the corpus, once the snapshot is published and the cascade (§4) has started (§3) | — |

- **When.** Inside the node's quiet hours (`quiet` windows, #192), at night.
  Fetching is a writer: it checks free space first (measuring.md).
- **Resumable, no agent.** One marker per collection, `state/update.json`:
  the period, the run, the step reached and each step's timings, written
  after each step's durable commit (invariant 2). A crash resumes at the
  step the marker names; supervise-pass reruns the pass.
- **Generic.** The steps belong to the platform. A plugin declares only its
  completion check (Wikimedia's dumpstatus; PubMed's update files are
  already applied in order; Gutenberg's catalogue grows by date). Wikidata
  is next: the same dated-dumps rule.
- **Sizing (BEFORE).** 62 GB of dumps, and the October records build and
  snapshot timings in STATUS. Each step's AFTER goes in the
  scheduling-log, per scheduling.md §6.

## 2. Where the dump run is named (decided: B)

**Operator, 2026-11-17:** "B", and "A collection can either sync with the source downloading single items (like gutenberg) or periodically download a full or an incremental snapshots (like wikipedia). In both cases the collection's identity remains the same."

- `origin.spec.files` in a dated-dump Codex names the FIRST run (today's
  Codex bytes are unchanged), and each snapshot names the run it was built
  from: `origin_run` (09 §9.4, §9.17; 06 §6.2; 02 §2.7).
- `collection_id` is stable, and annotations, peers, vectors and the
  cascade carry on across runs.
- Code: `discover` reads the run of the snapshot being built (the newest
  `origin_run`, else the Codex's run), and the collection file's `snapshot`
  check accepts a later run than the Codex's.
- Peers: a snapshot with `origin_run` read by older code. The field is
  optional and additive; whether the snapshot reader refuses unknown keys
  is checked, and amended if so, in the same PR.

## 3. Old dumps: the rule (decided)

**Operator, 2026-11-17:** "Delete the monthly snapshots once used, the peers
can reasonably check the compliancy." These are the ORIGIN's monthly dumps
in the corpus, not Louise's snapshots or parts.

- **Step f of the update:** the previous run's files are deleted from the
  corpus once the new snapshot is published AND the cascade it triggers
  (§4) has started. Never before either. A crash between the two leaves the
  files, and the step is redone.
- **What stays:** Louise's own snapshots and parts follow 02 §2.3 as before.
  Peers hold and verify them.
- **Why compliance stays checkable:** a peer re-fetches from the origin.
  Wikimedia serves recent dated runs, and `records-vs-origin` samples
  against the current one. When Wikimedia stops serving a run, its
  snapshots are reproducible only from peers (02 §2.2), and the collection
  records `origin_reachable_until`.
- **Written into the spec:** 06 §6.2, the origin's dumps held in the corpus.
  CLAUDE.md's invariant 11 is the operator's own text, and it changes on
  the operator's own word.

## 4. The cascade (part 2)

**The trigger** is the source's COMPLETED update: its new snapshot written,
signed and served. A partial, running or failed update triggers nothing.

**The standard** is a file marker per collection (our triggers-are-file-
markers rule; never a log grep).
- When a snapshot is published, the publisher writes
  `state/published/<snapshot_id>.json`: collection_id, snapshot hash, and
  the delta counts.
- The snapshot's own `delta` (09 §9.4: added, changed, tombstoned uids) is
  what the woken collections read. It is the published, signed record of
  what changed.

**Who is woken.** "Interested" = the collections that DECLARE the source:
- an annotations collection's `annotates` targets (Codex);
- a method's declared inputs (e.g. the gutenberg-editions method reads
  gutenberg, wikipedia and wikidata).

Nothing else is touched.

**Order.**
- The dependencies form a graph, and the keeper walks it in topological
  order (Kahn's algorithm). A collection runs only after every source it
  declares has completed for this round.
- A cycle is refused when it is declared (`hold config apply`, the
  Library's method box), naming the loop.
- When an annotations collection completes, its own published marker wakes
  the collections that declare IT, so the cascade is transitive.

**From the delta, never a full re-scan.**
- Claims given by hand: re-check only the claims with an end in the delta
  (changed, tombstoned), and re-pin the rest without reading them.
- Methods get a delta form. `wikipedia-versions`: recompute only the items
  (QIDs) of added, changed or tombstoned pages. `gutenberg-editions`:
  recompute the books in the gutenberg delta, plus the books whose
  Wikipedia page is in the wikipedia delta.
- A method with no delta form runs whole and says so; that is the
  exception.

**As built (2026-11-17): the delta forms.** Code: `app/hold/annotations_delta.py`
(the update), `annotation_methods.derive_from_delta` and `Method.delta` (the
keeper's entry point), the gutenberg part cache in `app/gutenberg_editions.py`.
Tests: `tests/hold/test_annotations_delta.py`, and two in
`test_gutenberg_editions.py`.

- **The pin is per claim.** Every claim carries `subject_hash`, `object_hash` and
  `target_snapshot` (09 §9.5), and an update re-pins every claim. So every claim's
  bytes move and every part is rewritten, whatever the delta. The claims file is
  read and checked whole, as before.
- **What the delta saves is the target.** An end record that the target's
  snapshots since the claims' pin do not name keeps its content hash (a delta is by
  content hash). That hash is taken from the claims' own pins. Every other end is
  read as before: a record the chain names, an end no claim pins (a claim set aside
  last time, a new claim, a community's members) and every chunk end. The table
  handed to `check_pins` and the part writer is the whole run's, digest for digest,
  so the bytes are the whole run's. The equality is tested for: a re-check, a new
  file, a method's live-ahead output brought in a month later, a chain that skipped
  a snapshot, graph claims on two targets, and on the pool.
- **The pin is read off the claims, never taken on trust.** A caller's `from` that
  differs is noted and not used. A snapshot missing between the pin and `to` makes
  the target read whole, with the reason given.
- **Taken only where it pays.** A frame of 1,000 records is decompressed whole for
  one wanted record. A target is served from its delta only when the claimed records
  its chain changed are expected in at most half its frames. The estimate uses the
  deltas' lengths, before any uid is hashed.
- **The three forms.**
  - Hand-made claims (form 1) go through `update(since=…)`. This is the default of
    `hold annotations update` (`--whole` reads everything), of the Library's
    re-check and of every method's update.
  - `wikipedia-versions` (form 2) has no delta form. Its claims come from the live
    manifest's `tokens` and page_props' items, and a snapshot delta names neither.
    Its update is re-pinned from the delta.
  - `gutenberg-editions` (form 3) has no delta form either. Its Wikidata tier reads
    the live wikipedia manifest, the dump's redirects and page_props, and the live
    wikidata manifest. Its gutenberg read goes through a part cache keyed on the
    part's root and the code's digest, so only re-issued parts are read. Its update
    is re-pinned from the delta.

**Measured** (synthetic, tmpfs, 3 processes in a 4-core sandbox, load 2–3 from
other sessions; scratchpad harnesses `measure/delta.py` and `ge_perf.py`):

| Fixture | Whole | From the delta |
|---|---|---|
| wikipedia's shape: 2 M records of ~1.2 KB in 1,000 parts; 480 k star claims about 32 % of them; 15 % of the records changed | 44.4 s. The target read is 4.5 s of it; the claims are the rest | Forced: 65.8 s against 65.9 s whole under load, the same bytes. By the estimate: read whole (100 % of frames), decision 0.3 s |
| the same, claims pinned two snapshots back (two deltas, 375 k uids) | 46.0 s | Forced: 52.9 s, the same bytes |
| big records: 16 k records of 50 KB; 3,840 claims; 5 records changed or withdrawn | 3.4 s (read 2.6 s) | 1.5 s (1 record read, 5,119 pinned), the same bytes |
| gutenberg-editions: 4,000 books of ~100 KB in 8 parts; a 2 M-row manifest | method 10.8 s; update 3.5 s | method 7.5 s with no part changed, 8.3 s with one; update 0.2 s (204 pinned, 1 read). The same bytes |

- **Modelled at the node's sizes.** `wikipedia-articles` (12.3 M claims, 52.9 M
  records) stays a whole pass, about 25× the first row: ~19 min on 3 processes. A
  monthly wikipedia delta is far above the ~0.15 % of records at which its frames
  stop all being read. **Measured on the node** (München, 2026-11-17 06:47Z, b747b20,
  quiet window, every collection resumed, wikipedia's embedding idle): `hold annotations
  update wikipedia-articles --dry-run` took 397.5 s wall, 2,294.5 s user + 292.2 s sys
  (~6.5 cores), with a 3,466 MiB peak RSS in the largest child and an 8.2 GiB peak
  workspace on /data. That is about 3× under the model, because the pool ran on ~6.5
  cores, not 3.
- `gutenberg-book-editions` gains most. Its update decompressed every frame holding
  a claimed book (~32 GB at 79 k books of ~400 KB); now it decompresses only the
  frames of the books that changed. Its method reads only the parts gutenberg
  re-issued. The wikipedia manifest pass (~1 min) stays.
- The node's own times are to be read off its first runs.

**Bounded, idempotent, resumable.**
- Each collection keeps `state/woken.json`: source, source snapshot, its
  state (`waiting` / `running` / `done` / `failed`), started, finished.
- A completed step is never redone for the same source snapshot.
- A crash resumes the step that was running. A failure is said and retried
  at most `KILLED_TRIES` times (as annotation_methods today).
- Each step runs in its own child process.

**Shown** in `hold collection show`, the Library card and `node show`:
- on the source: "woke N annotations (snapshot 2026-11)";
- on each annotations collection: "woken by wikipedia 2026-11 — running,
  started 03:12" and then "— done 03:41".

**As built (2026-11-17): the keeper.** Code: `app/hold/cascade.py` (the published marker, the graph, the gate,
the shown lines), `app/hold/cascade_keeper.py` (the round). Tests: `tests/hold/test_cascade_keeper.py`.

- **Published.** `snapshot.sign_snapshot` writes `state/published/<id>.json` after the sidecar's durable write. It
  is the one place a snapshot is signed, so `snapshot create`, the update pass, an annotations create or update and a
  re-creation all write it, once per snapshot hash. The sidecar's own write is now atomic (temporary, fsync, rename).
  A snapshot signed with no marker is backfilled by the round from a stat and the sidecar's `snapshot` field, never
  a re-hash. That covers one signed before this, or adopted with its sidecar. Only each source's newest signed
  snapshot is backfilled.
- **Who is woken: one graph.** `cascade.declarations` has an edge S → A for each target in A's `annotates` (the held
  collection answering for it, a records twin included) and for each held collection of a plugin in A's method's
  `inputs`. The gate's `annotators` reads the same graph, so gutenberg-book-editions holds wikipedia's old run too.
- **Order.** Kahn's algorithm over the graph. A collection waits while an annotations collection it declares has a
  wake pending. Within one round, a collection's own new snapshot wakes the ones after it: transitive.
- **Cycles.** A cycle is refused where it is declared, the loop named: `collection_file` (so `hold config apply --set
  method=` and the Library's box) and annotations `create`. The round refuses one written by hand, and says so once.
- **When a collection runs.** It runs when it is woken (a declared source's newest published snapshot is newer than
  what its last run read). It also runs when its method is due (`maintain`'s trigger, folded in; `maintain` is the
  round restricted to method collections), or when a wake is pending. It runs once, against every declared source's
  newest snapshot. It waits instead, said in `woken.json`, in three cases:
  - an annotations source has a wake pending;
  - a declared source is BEHIND: it has an update by cadence and holds no published snapshot of its current period
    (§6, "The current dump only"; this replaced the in-flight rule and the 24 h wait below). An update at `embed`
    or `old-run` has published, so it never deadlocks with the gate;
  - its own period's snapshot is written ("one snapshot a period: runs when 2026-12 opens").
- **The run.** `derive_from_delta` in its own children. A killed child is tried again `KILLED_TRIES` times. A refusal
  is said once per process. A worker that dies during a run (`woken.json` found `running`, not stopped) resumes it
  with the same sources. Three such deaths fail it until a source moves, recorded in the file, so a run that takes
  the worker down cannot loop it. A stop leaves `running` and `stopped`, and is resumed without being counted.
- **Only what is made here.** Claims given by hand are re-checked only where their claims file is held
  (`origin/claims.jsonl`). A collection adopted from a peer takes its publisher's snapshots.
- **What a wait keeps on disk.** A queued wake is not started, so the source's old run stays while a dependent waits.
  This happens when the dependent wrote its period's snapshot before the source published: at most one period
  (≤ 31 days) of one extra run, wikipedia's ~62 GB (74 GB declared). A dependent waiting for ANOTHER source that is
  behind keeps this source's old run for as long as that one takes, and the wait has no cap since 2026-11-17
  (§6): gutenberg-editions declares wikipedia and wikidata, so while wikipedia is refused at parser 5 every
  wikidata run it publishes keeps the one before it on disk (~104 GB) until wikipedia is re-created and caught up.
- **RETIRED 2026-11-17: the 24 h wait for an update to begin** (decided the same morning, choice B). A source
  counted as in flight from its cutoff until its `update.json` appeared, for at most 24 h (`cascade.BEGIN_WAIT_S`),
  and only when this node's keeper would update it. The operator's rule of the same day ("if the current dump fails,
  don't use an old dump") replaced it with the source-behind wait of §6, which has no time cap and no condition on
  the node's keeper: a dependent never derives from an old dump.
- **The cost of a round** (this sandbox, 4 cores, median of 5 × 300 rounds, a method collection held). Before, each
  pass ran `annotation_methods.due`: 4.3 ms at 1 snapshot and 2 claims, 12.9 ms at 409 snapshots and 2,000 claims.
  After, a settled round costs 0.46 ms at both sizes: markers and stats only. The cost test counts no part, no
  manifest and no listing of a snapshots directory. The first round after a start costs 2.4 ms and 4.5 ms (the
  listing and the claims' pin, read once).

## 5. Order of work

1. This note; the operator's choice in §2.
2. Part 2, the cascade. It does not depend on §2, and it is built after
   the gutenberg-editions method (in progress) lands, because both touch
   `annotation_methods`.
3. Part 1 on the chosen design, wikipedia first, then wikidata.

Each is a PR with tests (a cost test for every per-round selection,
scheduling.md §6) and a scheduling-log row. Every deploy waits for the
operator's word.

## 6. As built (part 1, 2026-11-17, branch `cadence-update-pass`)

- **The current run, one answer** (`app/hold/origin_run.py` `current_run`): the latest of the Codex's `files`
  run, the newest snapshot's `origin_run`, and the update marker's run once the update has moved it. The node's
  loader, `records.local_collection`, discovery, acquisition, `stale_unit` and a new snapshot's `origin_run` read
  it. The collection file's `snapshot` is accepted from the Codex's run on and never read for the run.
- **The completion check** is the plugin's `update_completion` (wikipedia: `completion.DumpStatus`, in each held
  wiki's `dumpstatus.json` the job that writes each file the collection reads at `done`), fetched by
  `app/acquire/completion.py` through the budget with the origin's User-Agent, one request at a time. *Found
  2026-11-17* (trwiki's 20261101 record): a wiki dumped in parts writes the one
  `pages-articles-multistream.xml.bz2` the collection reads in `articlesmultistreamdumprecombine`, after
  `articlesmultistreamdump` has written the parts. Only the latter was asked, so such a wiki read as complete before
  its file was written. The job whose `files` list the name now answers, the recombine job where none lists it yet.
- **The pass**: `louise hold collection update <name> [--dry-run] [--retry]` (`app/hold/update.py`), steps as §1,
  marker `state/update.json` (`app/hold/update_state.py`). The worker's `_update_keeper` runs it at night (the
  profile named `night`, `UPDATE_PROFILES`; `UPDATE_HOURS` on a node without profiles), one collection at a time,
  pausing the collection for the scan, the records and the snapshot. "The node's quiet windows" of #192 suspend
  parsing (`acquire-only`), so the pass's parse steps cannot run in them; adding `quiet` to `UPDATE_PROFILES` lets
  the fetch run there too, the rest waits for the night.
- **The old run's gate** (`app/hold/cascade.py`): published = the snapshot and its `.sig.json`; started = every
  annotations collection that declares this one has `state/woken.json` naming the snapshot at `running`, `done` or
  `failed`. Until the cascade (§4) writes `woken.json`, an annotated collection's old run stays. Since the
  keeper (§4 "As built: the keeper"), "declares" is the cascade's graph: `annotates` and a method's `inputs`.
- **DECIDED (operator, 2026-11-17): "Choice B", the origin by page.** The choices were: A keep the
  `origin` naming the dated file and byte range, so every part is re-issued each run; B a page-id
  `origin` located in the run through Wikimedia's multistream index; C an origin-only patch of the parts.
  B is built (branch `wikipedia-page-origin`):
  - **The form** (09 §9.5 amended): `<wiki>/pages-articles-multistream.xml.bz2#<page id>|<wiki>/page_props.sql.gz#<page id>`,
    resolved in the snapshot's run. The archive layer's row ranges keep theirs. Wikipedia's parser is 6.
    Parser 5 (`WikipediaSourceV5`) is kept for the live Codex, which reads and writes the byte range
    until it is re-created. Both forms resolve.
  - **The generic mechanism** (`app/keyed_origin.py`): a plugin declares `origin_key` (the key, the run's
    index, whether the node builds it, from which parser) and five hooks read by `plugin_io`. The key's
    table is one format, `KeyTable`: sorted int64 keys in Parquet, one row group read per lookup.
  - **Wikipedia's table** (`app/plugins/wikipedia/stream_index.py`): page id to stream range, built in one
    pass over the run's index. It is placed by `app/derived.py` (`wikipedia-stream-index`,
    `LOUISE_CACHE_DIR`), keyed on the run folder's resolved path, and fresh by the index's size and mtime
    (the CONTAINER_PACK template). The page_props unit is resolved through the existing page_props pack.
  - **The update pass:** `origin_names_run` is read at the Codex's parser (`plugin_at`). Parser 5 is
    refused with "re-create at parser 6 first"; parser 6 runs. An unchanged page's line is byte-identical
    across runs, so the records build takes it as it is. `ORIGIN_MOVES_ALONE` is RETIRED FROM THE BUILD:
    nothing rewrites an origin there any more. It stays for discovery's `_held_hash`, where a new run
    still moves a page's locator and not its bytes.
  - **The re-creation:** `hold records carry` (`app/hold/line_carry.py`) rewrites each line's `origin` in
    the bytes and takes every other byte. `dev carry --reconcile` reads its proof. `wikipedia-articles`
    is created again from its own snapshot's claims (`hold annotations claims`) against the new
    collection. The node's steps are [wikipedia-page-origin.md](wikipedia-page-origin.md).
  - **Measured** (this sandbox, 4 cores; synthetic wikipedia-shaped parts of 2,000 records, 1.45 MiB
    stored and 8.9 MiB of lines each):

    | | 1 process | 2 | 3 |
    |---|---|---|---|
    | the carry, ms a part | 224 | 137 | 105 |
    | the carry, records/s | 8,912 | 14,631 | 19,018 |
    | three indexes of 2 M lines built on the pool, s | 14.1 | 10.1 | 5.9 |

    One index of 2 M lines takes 4.1 s to read and 5.0 s to build; the table is 3.0 bytes a page. A lookup
    takes 1.13 ms when it reads a row group, 88 µs through `ensure` once the row group is held, and 7 µs on
    the table once found.
- **Wikidata, the same mechanism** (operator, 2026-11-17: "Do the wikidata step as well"; branch
  `wikidata-entity-origin`, stacked on wikipedia's). Its `origin` named the dated dump and the entity's
  ordinal, which every run moves.
  - **The form, parser 3:** `wikidatawiki/entities/all.json.bz2#<entity id>` (09 §9.5). Parser 2 is kept
    (`WikidataSourceV2`), and both forms resolve.
  - **The table:** the JSON dump has no index, so the node BUILDS one per run (`origin_key.built`):
    entity id to the line's ordinal. It is read from the run's pack's index columns and written as the
    pack's side table under the pack's writer (DUMP_PACK). It is placed with the pack, which is keyed on
    the dump's physical identity, and fresh by the dump's size and mtime and the pack's version and chunk
    count (`app/plugins/wikidata/entities.py`). It is not a byte range: a bz2 stream cannot be entered at
    a byte, and the pack keeps the lines by ordinal.
  - **Measured:** 12 M entities in 120 chunk files, built in 4.2 / 4.0 / 4.0 s on 1 / 2 / 3 of the parse
    pool's processes and in 3.1 / 2.9 / 2.8 s on threads, which it uses. The sort and the write, one each,
    are most of it. The table is 5.8 bytes an entity, about 690 MB for 118 M. A lookup takes 0.1 ms cold
    and ~50 µs warm.
  - **The update (decided from the origin's own files, the coordinator's read-only probe of
    `dumps.wikimedia.org/wikidatawiki/entities/`, 2026-11-17).**
    - What the probe found: an entity run publishes no `dumpstatus.json`, and `20261109/dumpstatus.json`
      gives 404. Its `wikidata-<R>-md5sums.txt` and `-sha1sums.txt` are rewritten as each file finishes, and
      list only the finished files, in finishing order. 20261109 wrote `all.json.gz` at 01:29,
      `all.json.bz2` at 06:01 and the BETA nt/ttl up to 21:40, and the sums at 21:48, listing all six.
      20261111 lists lexemes and truthy only; that run has no `all.json` at all. Not every run carries the
      full dump (recent runs: 20261102, 04, 06, 09, 11, 13).
    - **Complete** means the run's md5sums list `wikidata-<R>-all.json.bz2`
      (`app/plugins/wikidata/completion.py`, `Md5Sums`). It is fetched through `app/acquire/completion.py`:
      the budget, the origin's User-Agent, one request at a time.
    - **Waiting:** a 404, an empty sums file, or `all.json.gz` listed without the `.bz2`, however long it takes.
      *Retired 2026-11-17:* a run listing the `.gz` without the `.bz2` more than 3 days after it began was taken
      as never bringing it, and the run before it was taken. See "The current dump only" below.
    - **Not a candidate:** a run whose sums list finished files and none of the full dump never carries it
      (`skips`: lexemes or truthy only).
    - **After the fetch** the dump's md5 is held to that line, the origin's own checksum (`expected`). A
      mismatch is the FETCH failing, never the run, and it is MENDED (below). *Until 2026-11-17* the file was
      removed and its row left done, so the next night did not fetch it at all and failed on the missing file.
    - The refusal is lifted at parser 3.
- **The current dump only, and mended, never fetched again whole (operator, 2026-11-17, verbatim: "if the current
  dump fails, don't use an old dump (that should have already been integrated thus it's pointless); retry gently to
  fetch it with the already agreed policy. If available, a resume is always preferable over a dumb full
  re-download").** Branch `current-dump-only`.
  - **The current run** (`update.complete_run`, generic): the newest run begun before the cutoff that carries, or is
    writing, the collection's files, and is newer than the run the collection reads (`origin_run.current_run`; one
    not newer is already integrated). Wikidata's lexemes-only or truthy-only runs are not candidates (`skips`).
    Complete, it is taken. Incomplete, the pass WAITS and asks again at the keeper's next beat of the night
    (`UPDATE_RECHECK_S`), with the policy already in place: the budget, the origin's User-Agent, one request at a
    time. It never takes an older run. A NEWER run, begun on the cutoff's day or after, that completes first
    supersedes it ("newer is never pointless"; 09 §9.4 `origin_run` amended). At most 8 runs are asked a pass.
  - **Wikidata's run index** keeps the directories that hold or may be writing the full dump: `all.json.bz2` or
    `.gz` listed, or none of the run's files yet. It dropped every directory without `all.json.bz2`, so a run
    writing its dump was invisible and the one before it looked current.
  - **Said** in `hold collection show` and on the Library card: "update 2026-11: waiting for Wikimedia's 20261130
    full dump: .json.gz done, .bz2 not yet; asked again tonight".
  - **How a dated-dump file was fetched before.** `transport_https.fetch_one`: one streaming GET into `<file>.part`,
    each 64 KiB chunk fsynced and then charged to the budget. A `.part` left by a stop or a fault resumed with an
    open `Range: bytes=<its length>-`, and an origin answering 200 restarted it from 0. Within a pass a transient
    fault resumed `STREAM_ATTEMPTS` times; across passes, after the engine's backoff (`RETRY_BACKOFF_S`, 1, 5, 10,
    30, 60, 120 minutes, then every 120). So a PARTIAL file resumed by Range. A file that LANDED whole but short or
    wrong did not: nothing compared its length (the specs list these files without a size), and wikidata's md5
    check removed all 103.8 GB.
  - **The mend** (`app/acquire/repair.py`, from the fetch step, `update._held_to_origin`). Each file the hook names
    is held to the origin's md5: wikidata's md5sums line; wikipedia's `dumpstatus.json` `md5` and `size` per file,
    asked again through the budget (wikipedia was held to no checksum before). Where it differs:
    - (a) shorter than the origin's length (the record's `size`, or a one-byte Range's `Content-Range`): the rest is
      fetched from its length by closed Range requests and appended. Longer: cut at the length. Then the md5 again.
    - (b) the origin's length, wrong bytes, bzip2: its units are located and checked, the bad ones' bytes fetched
      again by Range and written in place, then the md5 again. The units are bzip2's own: a stream starts `BZh1`-`9`
      on a byte and ends with the 48-bit magic 0x177245385090 and its combined CRC; a block starts with the magic
      0x314159265359 at any bit offset and carries its data's CRC. A block is checked as `bzip2recover` checks it,
      alone in a stream of one block, decompressed by libbz2. Each stream's combined CRC is folded from its blocks'.
      Every bit is accounted for by a block, a stream header or a stream end that checks out, and whatever is not is
      bad. Wikipedia's multistream dump is mended stream by stream (a stream per 100 pages). The regions (64 MiB) are
      checked on the parse pool (`parallel.each_part`).
    - (c) only where the units cannot be told apart does the file go again in full, and the message says why: not a
      bzip2 file (a gzip of one member, the page_props: one CRC for the whole), every block checking out, more than
      half the file bad, the md5 still wrong after the mend, or an origin that ignores Range. Its row is reset, so the
      next fetch takes it whole after the engine's backoff, and the step fails, said and counted.
    - A mended file's row takes its new blake2. Every byte is drawn from the budget (`_stream_range`: each chunk after
      its fsync). A stop ends at the region being checked or the Range step (8 MiB) being written. `<file>.mend.json`
      beside it, written atomically after the bytes it describes, records how far it came, and the next pass resumes
      there (invariant 2).
  - **Wikidata's dump, probed** (one Range request, `bytes=0-8191`, 2026-11-17, `20261109`): 103,755,108,222 bytes
    (`Content-Range`), `Last-Modified` 2026-11-11 06:01:27 GMT, `BZh9` at byte 0, the first block's magic at bit 32,
    no other stream header and no other block magic in the first 8 KB. It is one stream (lbzip2's output), so it is
    mended block by block (~900 kB of JSON a block). Its streams alone could not be told apart, which would have meant
    (c), 104 GB again.
  - **Measured** (this sandbox, 4 cores, loopback origin, one byte flipped mid-file; scratchpad harness
    `measure_mend.py`; the scheduling-log row of 2026-11-17, "the mend"):

    | file | size | fetched again | Range requests | mend, 1 process | 3 processes |
    |---|---|---|---|---|---|
    | wikipedia's shape: 1,500 streams of 100 pages | 257,824,375 B | 171,898 B (0.067 %), the bad stream's block | 1 | 39.8 s (check 37.1 s) | 18.3 s (check 15.0 s) |
    | wikidata's shape: one stream, 339 blocks of JSON | 23,723,088 B | 69,984 B (0.30 %), the bad block | 2 (the length, the block) | 8.5 s (check 8.0 s) | 3.7 s (check 3.2 s) |

    The check is libbz2 decompressing every block: here 6.9 MB/s of bz2 a process on the wiki shape (libbz2 alone
    6.4) and 3.0 MB/s on the JSON shape (31 MB/s of JSON out). The rest is two md5 passes (~1.2 s for 258 MB).
    **Modelled at the node's sizes**, at this sandbox's rates: wikidata's 103.8 GB is ~35,000 process-seconds, ~49 min
    on 12 processes, against fetching 103.8 GB again from Wikimedia (~86 min at 20 MB/s, ~5.8 h at 5 MB/s; the
    node's rate from Wikimedia is not measured). enwiki's 27.1 GB multistream is ~65 min on one process, ~5.5 min on
    12. The node's own numbers come from its first mend, in `state/update.json` (`counts.mended`: bytes, seconds).
  - **Dependents never derive from an old dump** (`cascade.update_in_flight`, the keeper's wait). An annotations
    collection waits while a declared source that has an update by cadence (an `update` block, a dated origin, a
    completion check) holds no PUBLISHED snapshot of its current period, the latest whose cutoff has passed. The
    update pass builds that snapshot from the run the period names. This covers an update in flight, one failed and
    being retried, one waiting for a human ("…and waits for a human (`louise hold collection update wikipedia
    --retry`)"), one refused at an old parser ("waits for wikipedia's 2026-11 snapshot: re-create wikipedia at parser
    6 first"), and one not begun. There is no time cap, and nothing about this node's keeper: it waits until the
    source catches up, and says why in `woken.json` and on the card. A source with no update by cadence never makes
    anyone wait. The old-run gate is unchanged. Its cost: a stat of the listing and, behind, the update marker.
    `origin_names_run` is True at parser 2 and False at 3.
  - **The node's steps:** [wikidata-entity-origin.md](wikidata-entity-origin.md).
  - **A dated-dump Codex names its run** (2026-11-17, branch `wikidata-codex-run`): wikidata's live Codex carried `origin.spec = {"files": null}`, so its parser-3 snapshot named no run and records-vs-origin could not run; `create` now pins a dated plugin's first run (`--run`, or a `.next` re-creation's predecessor's), nothing creates, carries into or freezes a keyed Codex naming none, and `dev pin-run` corrects the unpublished wikidata.next in place ([wikidata-entity-origin.md](wikidata-entity-origin.md), "München from 2026-11-17's state").
- **Found on the way:**
  - every record line's `origin` names the run's file, so a dated-dump update re-issues EVERY part (the delta
    still lists only what changed by content). The records build carried a line whose origin bytes are the same at
    a new locator with the row's `origin` instead of parsing it (`ORIGIN_MOVES_ALONE`, `pack/build.py`; retired
    from the build by B, above);
  - a page the new run no longer holds is withdrawn by the scan (06 §6.1's deletion signal, reason `withdrawn`),
    read from the previous run's index while it is on disk;
  - a row discovery never hashed (`txt_hash` NULL: the first scan does not hash) read as changed on every new run;
    the scan now hashes the unit the row still names while it is on disk (`discovery._held_hash`).
