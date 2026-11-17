# Wikidata's origin by entity: the re-creation at parser 3 (runbook for München)

*2026-11-17. Operator: "Choice B", then "Do the wikidata step as well" (docs/notes/collection-updates.md
§6). Branch `wikidata-entity-origin`, stacked on `wikipedia-page-origin`; the mechanism is
[wikipedia's](wikipedia-page-origin.md) (app/keyed_origin.py). NOTHING HERE RUNS WITHOUT THE OPERATOR'S
WORD. Every deletion is named with its size and is the operator's yes, given in München's session.*

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
  2. `--run YYYYMMDD`;
  3. for `<name>.next`, the run `<name>` holds: its Codex's current run, else its file's `snapshot`.
     `create` says which on stderr.

  With none of these, `create` refuses (exit 2). Wikipedia's Codex is unchanged, byte for byte.
- Guards, in plain words, each with a test (tests/hold/test_codex_run.py): nothing creates, carries into
  (`dev carry`, `records carry`) or freezes (`records build`, `snapshot create`) a Codex whose plugin names
  its records' origins by the origin's own key at that parser version and which names no run.
  `records carry` holds each part to the run the target names: a line read in another run refuses it.
- `louise dev pin-run`: an UNPUBLISHED collection gets the corrected Codex in place (below).
- The check's memory: vectors-sample and chunks cut a part one record at a time, and the entity table's
  sort is packed (measured below). The checks run in their own container, never in the worker.

**Why in place, not a carry to a fresh `.next`** (RULE 1; nothing was published, `share = false`):

| path | pros | cons |
|---|---|---|
| **`dev pin-run wikidata.next`** (chosen) | rewrites the one snapshot file, its sidecar and a few small files (the 4,009 parts are already run-free and byte-identical to a by-the-book build); seconds per GB of snapshot; extra space = one snapshot file for a moment; the result is byte for byte the creation with the corrected Codex (tests/hold/test_pin_run.py: the SHARD, sidecar, markers, published marker, claim, collection file, the manifest's documents and cursors, the build database) | a new command; amends 07 §7.8's "a name holds one collection for its life" for a collection whose id never left this node; refuses whenever anything was published or shared, or a file names the old id that it does not know |
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
   - The cap is modelled from the measurements below: ~5 GB at 8 processes, with the entity table's
     build. Write the measured peak (`tree_peak_bytes` in the JSON) here.
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
