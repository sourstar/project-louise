# Collection updates by cadence, and the annotations cascade

*Design, 2026-11-17 (§2 and §3 decided the same day). Operator, through München: (1) "Monthly full dumps at
night" for wikipedia, as a first-class update of any collection from its
Codex's `update` block, wikidata next; (2) "it must add a signal to wake up
all the annotations relying on that collection's updates", clarified: "To
trigger a cascade of updates of the interested annotations once wikipedia
has completed its one." Part 1 is built (§6, 2026-11-17, deploy pending);
part 2, the cascade, is not.*

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

## 6. As built (part 1, 2026-11-17, branch `cadence-update-pass`)

- **The current run, one answer** (`app/hold/origin_run.py` `current_run`): the latest of the Codex's `files`
  run, the newest snapshot's `origin_run`, and the update marker's run once the update has moved it. The node's
  loader, `records.local_collection`, discovery, acquisition, `stale_unit` and a new snapshot's `origin_run` read
  it. The collection file's `snapshot` is accepted from the Codex's run on and never read for the run.
- **The completion check** is the plugin's `update_completion` (wikipedia: `completion.DumpStatus`, jobs
  `articlesmultistreamdump` and `pagepropstable` at `done` in each held wiki's `dumpstatus.json`), fetched by
  `app/acquire/completion.py` through the budget with the origin's User-Agent, one request at a time.
- **The pass**: `louise hold collection update <name> [--dry-run] [--retry]` (`app/hold/update.py`), steps as §1,
  marker `state/update.json` (`app/hold/update_state.py`). The worker's `_update_keeper` runs it at night (the
  profile named `night`, `UPDATE_PROFILES`; `UPDATE_HOURS` on a node without profiles), one collection at a time,
  pausing the collection for the scan, the records and the snapshot. "The node's quiet windows" of #192 suspend
  parsing (`acquire-only`), so the pass's parse steps cannot run in them; adding `quiet` to `UPDATE_PROFILES` lets
  the fetch run there too, the rest waits for the night.
- **The old run's gate** (`app/hold/cascade.py`): published = the snapshot and its `.sig.json`; started = every
  annotations collection that declares this one has `state/woken.json` naming the snapshot at `running`, `done` or
  `failed`. Until the cascade (§4) writes `woken.json`, an annotated collection's old run stays.
- **OPEN, the operator's: the origin form.** Until it is decided, a plugin declaring `origin_names_run`
  (wikipedia) is planned (`--dry-run`) and refused (exit 2) by the pass and the keeper. The choices: A keep
  the `origin` naming the dated file and byte range (every part re-issued each run); B a page-id `origin`
  located in the run through Wikimedia's multistream index (09 §9.5 changed); C an origin-only patch of
  the parts (09 §9.9's patch, for the `origin` alone).
- **Found on the way:**
  - every record line's `origin` names the run's file, so a dated-dump update re-issues EVERY part (the delta
    still lists only what changed by content). The records build carries a line whose origin bytes are the same at
    a new locator with the row's `origin` instead of parsing it (`ORIGIN_MOVES_ALONE`, `pack/build.py`);
  - a page the new run no longer holds is withdrawn by the scan (06 §6.1's deletion signal, reason `withdrawn`),
    read from the previous run's index while it is on disk;
  - a row discovery never hashed (`txt_hash` NULL: the first scan does not hash) read as changed on every new run;
    the scan now hashes the unit the row still names while it is on disk (`discovery._held_hash`).
