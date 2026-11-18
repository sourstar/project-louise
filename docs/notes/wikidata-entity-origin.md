# Wikidata's origin by entity: the re-creation at parser 3 (runbook for München)

*2026-11-17. Operator: "Choice B", then "Do the wikidata step as well" (docs/notes/collection-updates.md
§6). Branch `wikidata-entity-origin`, stacked on `wikipedia-page-origin`; the mechanism is
[wikipedia's](wikipedia-page-origin.md) (app/keyed_origin.py). NOTHING HERE RUNS WITHOUT THE OPERATOR'S
WORD. Every deletion is named with its size and is the operator's yes, given in München's session.*

## 2026-11-18: verifying against the origin, by the dump's blocks (operator: "A")

*Branch `wikidata-origin-blocks`. 03 §3.2 amended; 07 `hold check` amended.*

**Before.** records-vs-origin had no reader for one 103 GB bzip2 stream, so the nightly check re-parsed the
kept copy and said INCONCLUSIVE "kept copy" (München, 2026-11-18: 9,795 records reproduced, 0 differing). It
could never PASS.

**Now** (app/bz2_blocks.py, app/acquire/block_origin.py; `_member_origin` picks it for a Codex whose plugin's
spec is a dated single-file HTTPS dump, whose `origin.spec` names its run, whose run's data file is a `.bz2`,
and whose plugin frames it one unit a line, `origin_lines`):

1. **The copy's block index.** One pass of `indexed_bzip2` 1.7.0 (MIT; its parallel reader on
   `parallel.processes()` threads; 2.0.0 was released on 2026-11-18, too new) gives each block's start BIT
   and decompressed byte. In the same pass, numpy counts per block the unit lines (`{`) begun before it, and
   keeps the file's other lines (`[` and `]`), so the index holds the file to dump.py's framing. Derived
   (invariant 6): `<LOUISE_CACHE_DIR>/bz2-blocks/<tag of the dump>/<name>.blocks.npz`, fresh while the
   dump's size, mtime and inode are the same.
   - Resumable: every 60 s the state is written, cut at the last block that begins on a byte. The next pass
     reads on from there through a view: the 4-byte stream header, then the file from that byte.
   - Segments: the reader is begun again every 16 GiB decompressed, so its map of blocks never holds the
     whole file's.
   - A stop: SIGTERM or SIGINT ends the pass at its next 16 MiB step, its state written; `hold check` exits
     130.
2. **A record's unit.** The entity goes through the run's entity table (`origin_ordinals`: the table
   ensured once a check, 15.7 µs a record against 75.9 through `local_of`) to its ordinal, and two searches
   of the index give the blocks its line lies in. The unit is that block range, weighed by its compressed
   bytes, [⌊start bit / 8⌋, ⌈end bit / 8⌉). Records in the same blocks are one unit, fetched once.
3. **The comparison.** The units the bound takes are fetched by Range, each whole, through
   `zipdelta.HttpRangeReader`: the mend's client, every byte drawn from the budget. Keep-alive and
   `RangeRefused` were added to it, opt-in. At most `max_conns` (2) requests run at once, read ahead of the
   re-parse. Each range is compared with the copy's same bytes. The origin's length and 4-byte header are
   compared once a check. So the bound counts exactly what moves, plus 5 bytes (the header and the length
   probe).
   - Fixed in the same client: an origin answering 200 (ignoring Range) had its whole body read into memory
     before RangeUnsupported was raised — a 103 GB dump, for the mend and for this. The body of a 200 is
     now never read.
   - A check that re-parses nothing from the origin (`--checks roots`) builds no index.
4. **The outcomes** are 03 §3.2's:
   - range, header and length equal: the record re-parsed from the copy in the parse pool and judged by
     `verdict` (same, moved, changed, failed), as the origin's re-parse would be;
   - bytes, header or length different: CHANGED AT THE ORIGIN, the reason named. More than 10 % so is
     INCONCLUSIVE "changed";
   - no answer, a 404 or 410 (the run no longer served), or no Range: unreachable, with the reason, and the
     origin is not asked again that check. All unreachable is INCONCLUSIVE with the reason: a deferral;
   - a record the copy cannot place: unreadable, named.

**Why equal bytes reproduce the record from the origin** (written in block_origin.py too):
- A bzip2 block decodes from its own bits: magic, CRC, tables, data. No state passes between blocks, and the
  header gives only the block-size level.
- So the origin's bits over a block, with the copy's header, decode to the copy's bytes, which hold the
  line. The copy's re-parse is then the origin's.
- What is not re-read is every earlier block. That the origin begins a block at that bit is what the dated
  run's immutability, its equal length and header and the block magic in both make the only reading.
  Proving it bit by bit means reading the whole file.
- The line is textified with the copy's label table, built from every line of the copy, which acquisition
  held to the origin's md5. The blocks prove the record's own unit, the bytes `origin_hash` covers.
- A differing block does not say which side moved. The mend (`app/acquire/repair.py`, the blocks' CRCs)
  tells.

**Not taken, and why:**

| alternative | pros | cons |
|---|---|---|
| decode the fetched block on its own (shift its bits after a fresh `BZh9`, close it as a one-block stream, as repair.py's `check_block`) | proves the decoded bytes; would tell moved from changed where bytes differ | proves nothing more where bytes are equal (equal bits decode equally); rests on the same premise (a block begins there); a second decoder path |
| each block a shared piece of the units that read it (#745) | a block two overlapping ranges hold (a line across a boundary: 1.8 % of units) fetched and counted once | measured 447 bytes a unit in check.py's tables against 202 (840,000 units: 358 against 162 MiB); saves bytes only when both ranges are taken, a few in 10^4 of wikidata's units |
| a bit scanner of our own (repair.py has one) | no dependency | not the proven standard; the operator ruled indexed_bzip2 |

**Measured** (this sandbox: 4 cores, nothing else running, libbz2 24 MB/s a core). The fixture is a synthetic
wikidata-shaped dump, one stream at level 9 (`tools/measure/bz2_blocks_bench.py dump OUT 300000`; `index` and
`compare` give the rows below, under `check_rss`): 300,000 entity lines of 10.7 kB,
308,484,804 bytes compressed, 3,211,550,951 decompressed (ratio 10.4), 3,569 blocks of 900 kB, 86.4 kB
compressed a block.

| what | result |
|---|---|
| index build, 4 threads | 36.5 s: 88.0 MB/s decompressed, 8.5 MB/s compressed; peak 114 MiB RSS (110 Pss), `tools/measure/check_rss.py` (a first run of the same pass: 34.3 s) |
| index build, 3 threads | 46.0 s (69.9 MB/s); 107 MiB |
| index build, 2 threads | 63.5 s (50.6 MB/s); 100 MiB (the first run) |
| where the time goes | indexed_bzip2's read alone: 27 MB/s at 1 thread, 99 at 4; the newline scan is ~6 % |
| in 512 MiB segments, 4 threads | 37.7 s: six readers begun again, +3 %; 126 MiB |
| stopped at 12 s, resumed | 32 % done; the rest in 26.0 s through the view: 38.1 s in all against 36.5; 120 MiB |
| the index | 91,718 bytes: 25.7 a block |
| comparison: 3,000 records drawn at random, loopback origin, 2 connections | 2,056 units, 37 of them two blocks (1.8 %); 180,906,822 bytes, 88.0 kB a unit, 60.3 kB a record; 2,057 Range requests; 1.98 ms a unit (fetch, read of the copy, compare) |
| a unit, per record | 15.7 µs (wikidata's plugin, the fixture's table); 75.9 µs through `local_of` before `origin_ordinals` |

**Modelled to the node's dump** (103,755,108,222 bytes; ~1.5 TB decompressed, wikidata-build-plan.md, ratio
~14.5; blocks of 900 kB):
- **The index.** ~1.67 M blocks, ~62 kB compressed each; ~43 MB on the fast cache.
- **Decoding speed on the node.** libbz2 runs at 76 MB/s a core there (pack.py, measured) against 24 here,
  ×3.2. So indexed_bzip2 runs at ~75 MB/s a thread on the node.
- **The build.** 12 threads (its own container): ~28 min. 8: ~42 min. 3 (the nightly pool): ~1.9 h. With
  `NIGHTLY_BUILD_S` = 1 h the nightly has it whole on its second night, re-parsing the kept copy meanwhile.
- **The build's memory.** ~110 MiB, plus the arrays (1.67 M × 25 bytes = 42 MB, twice during a
  concatenation), plus one segment's map: ~200 MiB. It is modelled, and it is before any pool.
- **What a check reads.** 256 MiB / 62 kB ≈ 4,300 units, so ~4,300 Range requests at 2 connections (~2 min
  at 50 ms a request). In a sampled part's span a block holds ~71 entities, ~4.5 of them that part's
  records (modelled). So ~19,000 records are compared from the origin, against 9,795 re-parsed from the copy
  today.
- **The check's tables.** The 200 sampled parts span up to every block: up to ~1 M units, ×202 bytes ≈
  200 MiB in the parent during the check. The lookups take 15.7 µs × 3.79 M sampled records × 2 passes ≈
  2 min.
- **In the worker's cap, at 3 processes.** While the index is not whole: ≤ 1 h a night, ~200 MiB, before the
  pools. Once whole: the parent gains ~43 MB of index, ~200 MiB of unit tables, 2 fetch threads and a window
  of 512 records (~15 MB). The children are unchanged per child, re-parsing ~2× today's records. About
  +250 MiB on the parent. The first scheduled run records its own peak, and the admission takes it × 1.2.

**What the node does** (nothing runs without the operator's word):
1. Deploy the image (requirements.txt gains `indexed_bzip2==1.7.0`, a prebuilt cp312 wheel).
2. The first index build: either the nightly does it in two nights at 3 threads, or (recommended) once, in
   its own container at the node's cores, `deploy/supervise-pass.sh`'s form (`--memory 4g` is ample):
   ```bash
   python -m app.louise hold check wikidata --checks records-vs-origin
   ```
   - The collection is `wikidata` since step 8's rename (2026-11-18). With no `--origin` it finds the copy
     itself: the collection's corpus, `<root>/wikidata/origin` (the dump kept at step 7's forget). By hand
     it builds the index whole, then asks the origin.
   - A stop resumes at the next run. `--origin-else DIR` would bound the build to an hour, as the nightly.
   - Expected: ~30 min at 12 threads, ~43 MB on `LOUISE_CACHE_DIR`.
3. Expect PASS "N record(s) re-parsed from the origin" (~19,000 modelled), `origin` "reachable", ≤ 256 MiB
   from dumps.wikimedia.org.

## 2026-11-17, evening: step 6 was INCONCLUSIVE — the Codex named no run

*Branch `wikidata-codex-run`. München ran steps 1–5 below; step 6's records-vs-origin said "an origin naming
an entity is read in a run, and none was given".*

**The cause.** The creation path never read the plugin's acquisition spec into the Codex (template.py
`_origin`: "the recipe supplies it"). Wikipedia's recipe stated `[origin.spec]` whole, its run's files
included. Wikidata's stated `[origin]` without a `spec` (wikidata-recipe.toml), so its Codex got
`origin.spec = {"files": null}`: no `delta.mode`, no run. Its run was written by hand on 2026-10-20 into the
collection file as `snapshot = "20261012"`. At parser 2 nothing noticed, because each record's own `origin`
named the dated dump. Step 2 copied that Codex. At parser 3 the run left the record, and nothing named it.

**The collection file's `snapshot`** is a pre-Codex key (collections.py `DEPRECATED_CODEX_KEYS`; it is not
a key of 07 §7.8, and the loader warns). For a Codex that pins no run it is the run that acquisition and
discovery read. For a Codex that names its run, it is only checked against the Codex and never read
(collection-updates.md §6). It is never a Codex's source of truth. `create` reads it only as the
predecessor's word for a `.next` re-creation, and each carried line is then held to it.

**The fix.**

- `create` pins a dated-dump plugin's FIRST run (09 §9.17) and writes its WHOLE identifying half: every
  key the recipe (a TOML or a whole Codex) states none for, from the plugin's acquisition spec, in the
  form wikipedia's Codex states it. wikidata's becomes `source_id` "wikidata", `transport` "https", `url`
  "https://dumps.wikimedia.org", `layout` "single-file", `listing` "snapshot-index", `checksums`
  `{kind "none", suffix "", algo ""}`, `delta` `{mode "dated-dumps", subdir null}`, the plugin's
  `filters` and that run's `files`. A key the recipe states is never touched, so wikipedia's Codex is
  byte-identical. Other plugins state their half in their recipes, as before (pubmed's scheme-less `url`
  is written down in origin_run.py). The run comes from, in order:
  1. the recipe's own `files`;
  2. `--run YYYYMMDD` (an operator's);
  3. DETERMINED, RULE 2 (`origin_run.determine`):
     - the run the corpus holds, when the update's completion check says it is complete. Of several runs
       the newest is taken; a newer one not complete is waited for, never passed over for an older, and
       so refused;
     - else, for `<name>.next`, the run `<name>` holds: its Codex's current run, else its file's
       `snapshot`;
     - else the origin's latest complete run, from its index of runs, through the budget.

  `create` says which run it took and why, on stderr and in the Library's notice. Where none of these can
  tell, it refuses (exit 2), saying what would supply the run; an origin that does not answer is exit 3.
- Guards, in plain words, each with a test (tests/hold/test_codex_run.py): nothing creates, carries into
  (`dev carry`, `records carry`) or freezes (`records build`, `snapshot create`) a Codex whose plugin names
  its records' origins by the origin's own key at that parser version and which names no run.
  `records carry` holds each part to the run the target names: a line read in another run refuses it.
- `louise dev pin-run`: an UNPUBLISHED collection gets the corrected Codex in place (below). Every file a
  writer under `shard/` and `state/` puts the old id in is handled by pin_run.py's table, which was read
  from the code. What may have left the node is read so that a doubt refuses: a locked node database, an
  unreadable Codex file or a missing manifest refuses the pin.
- The check's memory: vectors-sample and chunks cut a part one record at a time, and the entity table is
  a direct-address table (measured below). The checks run in their own container, never in the worker,
  and the worker's scheduled check holds a collection paused on its own.

**Why in place, not a carry to a fresh `.next`** (RULE 1; nothing was published, `share = false`):

| path | pros | cons |
|---|---|---|
| **`dev pin-run wikidata.next`** (chosen) | rewrites the one snapshot file, its sidecar and a few small files (the 4,009 parts are already run-free and byte-identical to a by-the-book build); seconds per GB of snapshot; extra space = one snapshot file for a moment; the result is byte for byte the creation with the corrected Codex (tests/hold/test_pin_run.py: the SHARD, sidecar, markers, published marker, claim, collection file, the manifest's documents and cursors, the build database); a wikidata.next holding every writer's file, its vectors split by family, pins cleanly and its vectors still pass vectors-sample (tests/hold/test_pin_run_writers.py) | a new command; amends 07 §7.8's "a name holds one collection for its life" for a collection whose id never left this node; refuses whenever anything was published or shared, or a file names the old id that it does not know |
| carry to `wikidata.next2` | only the existing carries | 4,009 parts written again (their bytes again in space until wikidata.next is forgotten), the first snapshot's delta re-read (75.9 M uids), the manifest, index and vectors moved a second time; hours |
| status quo | nothing to do | a snapshot that names entities in no run: unverifiable against the source (invariant 11), never published |

## München from the node's state of 2026-11-17 (numbered; nothing runs without the operator's word)

State: `wikidata` (parser 2) and `wikidata.next` (parser 3, Codex `3d70200e…` naming no run) are both
paused. wikidata's manifest, index and vector table are moved to wikidata.next. wikidata.next holds 4,009
carried parts (75,896,183 records) and snapshot 2026-10 (`93cef74e…`, no `origin_run`). Reconcile is done
(0 to embed again). Nothing is published and nothing is forgotten. `W` below is the worker container,
`ix-louise-rag-worker-1`.

1. **Read only, before anything:**
   ```bash
   df -h /data "$LOUISE_CACHE_DIR"
   docker exec W python -m app.louise hold collection show wikidata.next   # Codex 3d70200e…, snapshot 2026-10
   docker exec W cat /data/collections/wikidata.next.toml                  # share = false; any `snapshot` line: note it
   ```
2. **Deploy** the image carrying this branch, as §5 of node-runbook.md. Keep both collections paused.
3. **The dry run** (read only, seconds; it hashes the snapshot once through the page cache, file-backed
   and reclaimable, so `docker exec` is fine):
   ```bash
   docker exec W python -m app.louise dev pin-run wikidata.next --dry-run
   ```
   Expect "would pin run 20261012" with:
   - the sources that name a run, all agreeing: the manifest's locators; the line carry's source,
     wikidata, by its file's `snapshot`; and wikidata.next's own file, if it states one;
   - "space unchanged", and the new collection id;
   - the snapshot's size (~1.6 GB modelled: 75.9 M uids at ~21 bytes), the extra space it needs for a
     moment;
   - every file naming the old id, each with what is done to it (pin_run.py's table, read from the
     code's writers). The removals are: the old sidecar, step 6's attestations of the old id,
     `state/origin-check.json` (step 6's note that the check could not run), any origin acknowledgement,
     gate statement, scheduled-check file or do-nothing update marker made under the old id. This node's
     RAGE settings are signed again for the new id. An update under way, a peer's RAGE settings, an
     anchor, a chain or a file the pass does not know refuse it. The kinds not read (record parts,
     SQLite databases, binary members) are named;
   - "the run's files in the corpus: N held, M missing": the dump (103,409,622,204 bytes) must be held.
     The md5sums are beside it; the sha1sums may not be. records-vs-origin reads the dump alone.

   STOP on any refusal, and read it.
4. **The pin** (the operator's yes to the removals step 3 listed). Run it in its own container, as every
   pass is (`deploy/supervise-pass.sh`, deploy/watchdog/README.md's form; `--memory 4g`):
   ```bash
   systemd-run --unit louise-wikidata-pin -p KillMode=mixed -p TimeoutStopSec=330 \
     /root/louise-rag/deploy/supervise-pass.sh wikidata-pin --memory 4g \
     --volumes-from W --network container:W --env-file <the worker's env> -w /srv <image> \
     python -m app.louise dev pin-run wikidata.next --yes
   ```
   Modelled, from this machine's measured rates: the splice runs at ~0.5 GB/s and each load's hash at
   ~0.7 GB/s, so with one splice and three loads this is ~10–20 s plus the sweep, which the dry run timed.
   A stop resumes from `state/pin-run.json`. After it, `hold collection show wikidata.next` names the new
   id, and the snapshot names `origin_run` 20261012.
   - **The gate, before any embed resumes**, where the pin removed a statement made under the old id (a
     carry carries none, so a creation by the book has none): `docker exec W python -m app.louise hold
     embedder check wikidata.next`. A statement of the same space that wikidata made is borrowed and kept
     (`runs.statements_for_space`), so this may pass without measuring.
5. **The checks, in their own container, never in the worker** (step 6's OOM). `VERDICTS=1`: a check that
   FAILS is an answer, not a crash. The pass runs under `tools/measure/check_rss.py`, so the peak it
   reaches is measured, not assumed.
   ```bash
   systemd-run --unit louise-wikidata-check -p KillMode=mixed -p TimeoutStopSec=330 \
     --setenv=VERDICTS=1 --setenv=PARSE_PROCESSES=8 \
     /root/louise-rag/deploy/supervise-pass.sh wikidata-check --memory 12g \
     --volumes-from W --network container:W --env-file <the worker's env> -w /srv <image> \
     python -m tools.measure.check_rss --out /data/tmp/wikidata-next-check-rss.json -- \
     python -m app.louise hold check wikidata.next \
       --checks roots,records,chunks,records-vs-origin,vectors-sample --origin <wikidata's corpus>
   ```
   - `PARSE_PROCESSES` is given to the supervisor, not to `docker run`, so that an OOM kill (exit 137,
     which check_rss passes on) halves the pool for the next run.
   - The cap was modelled at ~5 GB for 8 processes. MEASURED on the node (2026-11-18, below): 11,291
     MiB of summed RSS at 2,003.6 s of 2,279 s, not OOM-killed at 12g. The model was off by more than
     2×; see "Measured on the node".
   - The worker's own scheduled check holds wikidata.next while it is paused (since this branch), so
     this check is the only one; `hold collection show wikidata.next` says "held: the collection is
     paused" under `scheduled_check`.
   - The first lookup builds run 20261012's entity table: ~26 s, ~0.8 GB in one process (modelled:
     4 bytes per item number to Q141 M), and ~0.7–0.9 GB on disk in the pack's directory.
   - Expect roots, records, chunks and vectors-sample to PASS.
   - Expect records-vs-origin to be INCONCLUSIVE "kept copy", with `copy_reproduced` = compared and 0
     failed. The corpus is the copy this node keeps, so the parser and the run's copy agree; a PASS
     needs the origin re-fetched (03 §3.2).
   - Every record points into the one dump, so a share of the sampled records is compared (~6,800,
     printed).
   - What "kept copy" means for the copy's use follows 03 §3.6, as it does for wikipedia
     (`hold collection show`).
6. **Each annotations collection that annotates wikidata**: step 7 below, against `wikidata.next` and its
   new id.
7. **The forgets**: step 8 below, the operator's yes with the sizes their dry runs print. wikidata's
   parts and snapshots go; the corpus and the pack are KEPT, because wikidata.next reads them.
8. **The names and resume**: step 9 below.
   - The collection file's `snapshot` goes with the old file at the forget. A copy of it on
     wikidata.next's file is checked against the Codex and never read for the run.

## The check's memory, measured (2026-11-17)

`tools/measure/check_rss.py`: the scheduled checks' sampler (`session_rss`) over the check's session,
every 0.1 s, and each process's VmHWM. Run on this branch on a wikidata-shaped fixture: parser 3, run
pinned, 20,105 records of **4,771 bytes a line** (wikidata's size), three processes, the encoder and the
vector store faked. Each check was run alone, then all five, in two worlds: 203 parts of 100 records, and
one part of 20,000 records (the node's ~18,900). "Before" is origin/main's `_chunks_part` and
`picked_inputs`. Peaks in MiB; one process imports to ~188.

| check | 203 parts: tree / parent / child | one 20,000-record part: tree / parent / child |
|---|---|---|
| roots | 733 / 189 / 189 | 715 / 188 / 188 |
| records | 705 / 192 / 189 | 741 / 213 / 189 |
| chunks | 721 / 197 / 189 (before 737 / 197 / 189) | 716 / 211 / 189 (before 734 / 219 / 188) |
| records-vs-origin | 1,281 / 422 / 382 | 1,300 / 427 / 385 |
| vectors-sample | 742 / 198 / 189 (before 733 / 200 / 189) | 742 / 212 / 188 (**before 749 / 737 / 189**) |
| all five | 1,485 / 428 / 353 | 1,480 / 431 / 351 (before 1,485 / 951 / 351) |

**The entity table's build**, a fresh process, 3 threads, a synthetic pack of dense ids:

| entities | direct-address (now): peak, time | packed sort (5cd1ada): peak, time | generic (before): peak, time |
|---|---|---|---|
| 4 M | 227 MiB, 1.1 s | 274 MiB, 1.1 s | 395 MiB, 1.4 s |
| 8 M | 241 MiB, 1.9 s | 331 MiB, 1.9 s | 617 MiB, 2.9 s |
| 16 M | 287 MiB, 3.5 s | 444 MiB, 4.0 s | 1,049 MiB, 5.1 s |

The direct-address table is a counting sort: an item's number indexes a uint32 array of ordinals, and the
properties have a small array of their own. Its marginal cost is 4 bytes per item number, against 14.2
bytes an entity packed and 54 generic. At 118 M entities, whose largest item is Q141 M, that is ~0.8 GB
and ~26 s (modelled), against ~1.9 GB packed and ~6.6 GB generic. The table's bytes are the same
whichever builds it (tests/test_wikidata_entity_table.py). A sort of each chunk to find an entity twice
in it was half the time at first; a read-back after writing replaces it.

**What scales, and what scaled wrongly.**

- Nothing scales with the number of parts. 203 parts and 3 parts give the same peaks.
- What scaled with a PART'S SIZE was vectors-sample's cut of a whole part, before 5cd1ada: +549 MiB for
  20,000 records, ~27 KB a record, in whichever process cut it. At the node's part size that is ~0.5 GB in
  each of the pool's processes at once, which is the likely 2.4 GB process the memcg killed. Now it is
  none: one record at a time, and the draw is the same (09 §9.1, `cx.sample_by`).
- What scales with the run is the entity table's build, in the one process that makes the first lookup.
  It is proportional and bounded: ~0.8 GB at 118 M (the direct-address table).
- What scales with the pool, by design, is ~190 MiB of imports per process, and in records-vs-origin
  another ~200 MiB of read caches per process. Those caches are bounded by `READ_CACHE_BYTES` (256 MiB),
  so on the real pack a child may reach ~450 MiB.
- So the whole check at 8 processes is ~0.43 + 8 × 0.45 + 0.8 ≈ 5 GB (modelled). That is why step 5
  caps it at 12 GiB in its own container, and why it is never run in the worker's 24 GiB beside an
  embedding.

**Measured on the node, 2026-11-18** (München, step 5: d696742, `--memory 12g`, PARSE_PROCESSES=8,
nproc 12, one run, exit 0 in 2,279 s; `/data/tmp/wikidata-next-check-rss.json`):

- The summed peak was 11,291 MiB (11,839,963,136 bytes) at 2,003.6 s.
- There were 50 processes over the run: the parent, the multiprocessing resource tracker, and 48 spawned
  children, which is six pools of 8. Each check builds its own pool (`parallel.each_part`), so the pools
  ran one after another, not at once; the file cannot say which were alive at the peak.
- The parent reached 1,673 MiB. That fits the entity table being built in the parent (~0.43 + ~0.8 GB
  modelled, plus the table's read).
- The children reached far more than the ~450 MiB modelled: 14 of them reached 786–882 MiB, and the
  rest 500–750 MiB. One pool of 8 such children plus the parent is ≈ 8.3 GiB of summed RSS on its own.
- The sum counts a page shared between processes once per process, so 11.3 GiB is an upper bound on
  what the memory cgroup charged. That bound held: no OOM at 12g, 0.7 GiB under the cap.

What is not known yet, and how the next run tells it: which check's children reach ~850 MiB, and what
they hold beyond ~190 MiB of imports and the 256 MiB read cache. `tools/measure/check_rss.py` now records
the processes alive at the peak, each process's first and last time seen, their Pss (shared pages split
among their sharers, as smem reports it), and the cgroup's own `memory.peak` and `anon` peak. The next
check of a wikidata run under it gives the real charge, and the model is corrected from that, not
guessed. Until then the cap stays 12g, under `supervise-pass.sh`, which halves the pool after an OOM kill.
The worker's scheduled check runs a quarter of the pool (`check_processes`: 3 of 12): ~1.7 GiB plus
3 × ~0.9 ≈ 4.3 GiB summed, against the 4 GiB it is admitted with while no peak of it is recorded. The
first scheduled run records its own peak, and later admissions use it × 1.2.

The alternatives, written down in entities.py:
- the packed sort, which pays over the direct table where the ids are sparse;
- an external merge sort, which bounds the memory whatever N and would pay at billions of entities.

## What changes, and what is carried

```
wikidatawiki/entities/20261012/wikidata-20261012-all.json.bz2#123456
  becomes
wikidatawiki/entities/all.json.bz2#Q42
```

The entity is named by its id and read in the snapshot's run through the run's ENTITY TABLE. The node
builds the table from the run's pack in one pass: entity id to the line's ordinal, a side table in the
pack's directory, ~690 MB for 118 M entities. Parser 3, so a new Codex: a re-creation (RULE 1; nothing
official was published from wikidata). Nothing else in a line moves. The parse reads the same pack and
labels.

| what | how |
|---|---|
| the record parts (~5,500 of ~20,000 entities, cut by content) | `hold records carry`: each line's `origin` rewritten in the bytes, every other byte taken; each part held to the old one (uids, `records_root`, counts) and to the content cut a build by the book makes |
| the snapshot | the same id as the old one, frozen from the carried parts: a build's by the book (the mechanism's tests, and tests/test_wikidata_entity_origin.py line for line and part for part) |
| manifest, text index, vector table | `dev carry --rebuilt`, MOVED |
| the vectors' reconcile | from the carry's proof: 0 to embed again |
| `space_id` | unchanged (the parser is not part of it, 09 §9.3) |
| annotations collections that ANNOTATE wikidata | created again from their own claims against the new collection, every target named (step 7) |
| methods with wikidata among their INPUTS (gutenberg-editions) | nothing: an input is a plugin, so the new collection is read (cascade.py) |

**The monthly update runs at parser 3** (collection-updates.md §6). A run is complete when its
`wikidata-<R>-md5sums.txt` lists `wikidata-<R>-all.json.bz2`. The update takes the CURRENT run only: the newest
before the cutoff that carries or is writing the full dump (lexemes-only and truthy-only runs are not candidates).
While it is incomplete the pass waits and asks again at its nightly beat, saying so ("waiting for Wikimedia's
20261130 full dump: .json.gz done, .bz2 not yet; asked again tonight"). It never takes an older run; a newer run
that completes first supersedes it. The fetched dump is held to that md5 line, and a mismatch is MENDED: the dump is
one bzip2 stream (probed 2026-11-17: 103,755,108,222 bytes, `BZh9`), so its bad 900 kB blocks are found by their
CRCs on the parse pool and only their bytes are fetched again by Range. Only a dump whose blocks cannot be told
apart goes again in full, and the message says why. At parser 2 the pass refuses: re-create at parser 3 first. Every
annotations collection that declares wikidata (gutenberg-editions, by its method's inputs) waits meanwhile, with no
time cap, rather than derive from the old dump; `hold collection show <it>` says why.

**Before the first update, check** (read only): `louise hold collection update wikidata --dry-run` names the run
the pass would take. While a mend is under way, `<dump>.mend.json` sits beside the dump; it is the mend's own record
and is removed when the dump matches. Do not delete it by hand: a stopped mend resumes from it.

## Sizes and times

The node's own numbers come from the dry runs. These estimates use wikipedia's measured carry rate:
224 ms a part at 1 process, 105 ms at 3, for parts of 1.45 MiB. Wikidata's parts are larger (20,000
entities each), so scale by their bytes, which `--dry-run` prints.

- **The entity table:** 12 M entities in 2.8 s here (threads), so ~30 s for 118 M. It is built at the
  first lookup (records-vs-origin) and is ~690 MB in the pack's directory.
- **Peak extra space:** the new parts (wikidata's latest snapshot's bytes, printed by the dry run), plus
  the new snapshot file, plus the table, plus the claims export of each annotations collection that
  annotates wikidata.

## The steps

**0. Before anything** (read only):

```bash
df -h /data /shards "$LOUISE_CACHE_DIR" "$LOUISE_FILES_CACHE_DIR"
louise hold collection show wikidata          # parser 2, its latest snapshot id S, its parts
louise hold collection list                   # the annotations collections; `show` each to see whether it annotates wikidata
```

**1. Deploy** the image carrying parser 3. Then re-check the live collection; it parses at 2:

```bash
louise hold check wikidata --checks records-vs-origin --origin <wikidata's corpus>
```

**2. The new Codex**, the live one with the parser and the image changed:

```bash
python -c "import json,sys; c=json.load(open(sys.argv[1])); c['parser']['version']=3; \
  c['toolchain']['platform_image']=sys.argv[2]; c.pop('collection_id',None); \
  json.dump(c,open('/tmp/wikidata3.json','w'))" /data/collections/wikidata/shard/codex.json <deployed image digest>
louise hold collection create --recipe /tmp/wikidata3.json --name wikidata.next \
  --encoder <url> --api <api> --embed-model <model>        # the [encoder] of collections/wikidata.toml
```

The family map member (`slicing/<sha256>.json`) and the calibration member are copied from wikidata
(`complete_members`). *Amended 2026-11-17 (evening):* `create` pins the Codex's run. It says
"run: 20261012 — the run wikidata's records were read in (collections/wikidata.toml `snapshot`)" on stderr,
and the Codex's `origin.spec` names that run's files with `delta.mode` "dated-dumps". STOP if it says
nothing of a run. A Codex naming none is refused (exit 2), and so is every carry into one and every freeze
of one.

**3. Pause** both, then **4. carry the manifest, text index and vector table** (moved):

```bash
louise node pause wikidata
louise node pause wikidata.next
louise dev carry wikidata wikidata.next --rebuilt
louise dev carry wikidata wikidata.next --rebuilt --yes
```

STOP if it names any difference but `parser` and `toolchain.platform_image`. Since 2026-11-17 the carry
also copies `state/cpu-reference/` (the same space and calibration set; "derived state carried" in its
log). The scan cursors stay behind, since the parser moved: wikidata.next's first scan re-enumerates once.

**5. The parts** (its own container, `deploy/supervise-pass.sh`, the night profile):

```bash
louise hold records carry wikidata.next --from wikidata --dry-run
louise hold records carry wikidata.next --from wikidata
```

Each part is checked against the content cut as it is rewritten. A part that does not end where the rule
ends it refuses the carry, names the part and writes nothing. The way round it is then `records build
wikidata.next --records-from wikidata`, which takes the same lines. The carry ends with snapshot S of
wikidata.next, signed.

**6. The vectors and the checks:**

```bash
louise dev carry wikidata wikidata.next --reconcile --yes          # 0 to embed again
louise hold check wikidata.next --checks roots,records,chunks,records-vs-origin,vectors-sample \
  --origin <wikidata's corpus>
```

The first lookup builds the run's entity table, ~30 s at 118 M entities. It needs the run's pack, which is
already there. *Amended 2026-11-17 (evening):* the check runs in its own container, never by `docker exec`
in the worker. Step 6 of 2026-11-17 ran there beside wikipedia's embedding, and the memcg OOM killed a
2.4 GB process. The command, its cap and what to expect are step 5 of "München from the node's state"
above. With the corpus as `--origin`, records-vs-origin is INCONCLUSIVE "kept copy", 0 failed.

**7. Each annotations collection that annotates wikidata** (wiki-gutenberg, if held: gutenberg and
wikidata) is created again, every target named. The claims must be pinned to wikidata's S; `hold
annotations derive <c> --dry-run` or its `show` says so, and if they are not, `hold annotations update`
comes first:

```bash
louise hold annotations claims <c> --out /data/tmp/<c>.claims.jsonl
louise hold collection create annotations --name <c>.next --annotates gutenberg --from-snapshot <its pin> \
  --annotates wikidata.next --from-snapshot S --snapshot <c's latest snapshot id> \
  --claims /data/tmp/<c>.claims.jsonl --dry-run
# then the same without --dry-run, and `hold config apply <c>.next --set method=<its method>` if it has one
```

Created as `<c>.next` while `<c>` is held, the new collection file takes `<c>`'s `title`; the rename keeps it.

**8. The forgets**, the operator's yes with the sizes their dry runs print:

```bash
louise hold collection forget wikidata --dry-run        # the old parts and snapshots; the corpus and the pack KEPT
louise hold collection forget <c> --dry-run
```

The old collection's `state/build/` is its records build's staging (the build database: a row per record
of the plan, and the parts' entries): derived, read by nothing once step 5 has ended, and removed by the
forget with the directory (`ls -ld` it first: a folder linked in from elsewhere would leave its target).

**9. The names and resume:**

```bash
louise hold collection rename wikidata.next wikidata
louise hold collection rename <c>.next <c>
louise node resume wikidata
louise hold config apply wikidata --set embed=<the mode it had>
louise hold collection update wikidata --dry-run   # planned: the run the next cutoff names, from its md5sums
```
