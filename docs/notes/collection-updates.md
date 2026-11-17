# Collection updates by cadence, and the annotations cascade

*Design, 2026-11-17 (§2 and §3 decided the same day). Operator, through München: (1) "Monthly full dumps at
night" for wikipedia, as a first-class update of any collection from its
Codex's `update` block, wikidata next; (2) "it must add a signal to wake up
all the annotations relying on that collection's updates", clarified: "To
trigger a cascade of updates of the interested annotations once wikipedia
has completed its one." Nothing here is built yet. One decision blocks part
1 (§2); part 2 does not depend on it.*

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
  stop all being read.
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

## 5. Order of work

1. This note; the operator's choice in §2.
2. Part 2, the cascade. It does not depend on §2, and it is built after
   the gutenberg-editions method (in progress) lands, because both touch
   `annotation_methods`.
3. Part 1 on the chosen design, wikipedia first, then wikidata.

Each is a PR with tests (a cost test for every per-round selection,
scheduling.md §6) and a scheduling-log row. Every deploy waits for the
operator's word.
