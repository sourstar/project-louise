# Collection updates by cadence, and the annotations cascade

*Design, 2026-11-17. Operator, through München: (1) "Monthly full dumps at
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

## 2. DECISION: where the dump run is named

The spec puts the run in the collection's IDENTITY. For a dated-dump
origin, `origin.spec.files` is "the dump pinned" (09 §9.17, 06 §6.2), and
`origin.spec` is inside `collection_id`. So under today's rules, a new
month's dump is a new Codex: a daughter (02 §2.7, `continues`).

The other half of the same spec already describes a collection that follows
its dumps. A snapshot's records are those "the plugin's `cutoff_rule`
enumerates at that instant: for dated dumps (Wikipedia) the last dump
before it" (02 §2.2). The two halves disagree.

| choice | pros | cons |
|---|---|---|
| **A. A daughter each month** (the spec as written) | No format change. | A new `collection_id` every month. Every annotations collection names its target's `collection_id` (`annotates`, 02 §2.10), so each month would orphan them all: a new annotations Codex each, and the cascade of part 2 broken by design. Peers adopt a new collection monthly. |
| **B. One collection follows its runs** (amend 09 §9.17 and 06 §6.2) | `collection_id` is stable, and annotations, peers, vectors and the cascade carry on. The cutoff rule already says which run each snapshot holds. RULE 1: no identity churn. | A format amendment. `files` in a dated-dump Codex names the FIRST run (the bytes of today's Codex unchanged), and each snapshot names the run it was built from, a new snapshot field `origin_run`. Peers on the old code read a snapshot of a newer run and must know the field. |

**Recommendation: B.** If the operator says nothing, nothing in part 1 is
built: A or B changes what step b is.

## 3. Old dumps: the rule

Invariant 11, "everything held is verifiable": a superseded part version is
kept where the origin keeps its history, and dropped where it replaces in
place.

- Wikimedia keeps dated runs, but only the recent ones. While Wikimedia
  serves a run, every record built from it can be re-fetched and
  reproduced (`records-vs-origin`). The local copy of that run is then a
  cache of the origin, not the evidence.
- **So:** last month's dump may go once the new snapshot is published and
  its `records` check passes. The node keeps the run of the snapshot it
  serves, needed for repairs and parses.
- When Wikimedia stops serving a run, the snapshots built from it are
  reproducible only from peers (02 §2.2). Their superseded parts follow
  invariant 11's drop rule, and the collection records
  `origin_reachable_until`.

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
