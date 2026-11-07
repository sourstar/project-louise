# What belongs on the fast disk, and what does not

*Proposal, 2026-08-28. Measured on the reference node, which had run out of
room by getting this wrong.*

The node has two pools: `ssd4`, 1.4 TB and fast, and `tank`, 8 TB of
mechanical disk. It reached **94% full on the SSD with 7.7 TB free on the
HDD**, which is not a capacity problem — it is a placement problem.

## 1. The rule

> **SSD** if the access is RANDOM and a query waits for it.
> **HDD** if the access is SEQUENTIAL and in bulk, or the bytes are written
> once and rarely read at all.

And the corollary this node proved the hard way:

> **Size follows from mutability.** A mutable store is 3–5× its logical
> content, because it carries versions, tombstones and merge debris. An
> immutable artefact is exactly its content.

So mutable stores are the ones that *belong* on the SSD and are also the
ones that *must be actively bounded* — pruning is not housekeeping, it is
the price of the tier. Immutable artefacts need no maintenance at all, which
is precisely why they can sit on slow disk and be forgotten.

## 2. Measured placement

| artefact | this node | access | tier |
|---|---|---|---|
| origin corpus (epub, XML.gz, dumps) | gutenberg **261 GB**, wikivec 525 GB, pubmed 49 GB | sequential, during parse; written once | **HDD** |
| pack record shards (`.jsonl.zst`) | gutenberg 6.5 GB | one zstd frame at a time; immutable | **HDD** |
| pack vectors (safetensors) | — | bulk read once on import | **HDD** |
| genesis archive shards (parquet) | pubmed 23 GB | 437 MB per shard, sequential (6.3 s) | **HDD** |
| derived parse caches (`PUBMED_PACK_DIR`) | 61 GB | sequential during parse; rebuildable | **HDD** |
| **SQLite manifests** | pubmed 19 GB, wikivec 5.6 GB | random reads + constant small WAL writes | **SSD** |
| **tantivy text index** | wikivec 71 GB, gutenberg 41 GB, pubmed 41 GB | random reads per query; merge-heavy | **SSD** |
| **Lance vector table** | wikivec 587 GB, pubmed 269 GB | random reads per query; delete+insert churn | **SSD** |
| acquisition staging | 303 MB | sequential write, then move | **SSD** |

## 3. The finding that pays for the whole exercise

Gutenberg's corpus sits on the SSD at **261 GB**. Its composition:

| | files | bytes | read by Louise? |
|---|---|---|---|
| `.epub` | 146,616 | **141.3 GB** | **no** |
| `.jpg` | 142,891 | | **no** |
| `.png` | 132,009 | | **no** |
| `.mobi` | 45,914 | | **no** |
| `.txt` | 77,756 | **28.3 GB** | **yes** — the only thing parsed |
| `.rdf` | 79,295 | | yes — metadata |

**About 230 GB of the fastest storage on the node holds files no code path
ever opens.** The Gutenberg plugin reads `.txt` and `.rdf`; epub, mobi and
the cover images arrive because the mirror ships whole book directories.

Moving that corpus to `tank` frees more than twice what the vector-version
leak did, costs nothing in query latency (it is only touched during parse,
sequentially), and needs no code change — `corpus_dir` is already a path in
the collection's TOML.

## 4. Why a pack cannot ship its Lance table

The same rule answers the question directly: **a pack is immutable and a
Lance dataset is a mutable database.** Concretely, measured here:

| collection | vectors (f16, raw) | Lance directory | ratio |
|---|---|---|---|
| wikivec (58.2M × 1024) | 119 GB | **587 GB** | 4.9× |
| pubmed (32.7M × 768) | 50 GB | **269 GB** | 5.4× |
| gutenberg (1.37M × 4096) | 11 GB | **33 GB** | 2.9× |

Where the excess goes: superseded versions (177 GB on wikivec, 522,650 of
them), index files (125 GB), transaction logs — and the `text` column, which
Lance stores beside every vector and which the pack **already carries in
`records/`**. Shipping the Lance table would ship the corpus text twice.

Four more reasons, each fatal on its own:

- **File count.** 115,528 `.lance` files on wikivec, 75,162 on pubmed,
  against 1,591 shards for the whole Gutenberg pack. A torrent of 100k+
  files is metadata-bound before it transfers a byte.
- **No cross-snapshot dedup.** Torrent v2 shares byte-identical files
  between snapshots; compaction rewrites data files, so a new snapshot
  would share almost nothing. Immutable per-shard safetensors dedup
  perfectly.
- **Format lock-in.** A pack should be readable in ten years by anyone; the
  Lance on-disk format is a moving target and would pin the whole community
  to one library version. Safetensors is a frozen, trivially parsed layout —
  the reason §9 chose it.
- **It breaks partial download.** The format's whole shape is slice × kind,
  so a peer can take German Wikipedia's vectors alone. A Lance dataset is
  one object whose manifest references every fragment; a subset is a broken
  dataset.

**The format already has the better version of what the question is after.**
§5.2 publishes the index's **centre points**, and §5.3 optionally publishes
the vectors re-sorted into buckets. The centres are the expensive part of an
index — the k-means training — so a peer that downloads them skips straight
to assignment. That is a portable index, without shipping a database.

So: **the Lance table is a local derived cache, exactly like the tantivy
index.** Both are rebuilt from published, immutable inputs; neither travels.
That is already doctrine for the ANN index ("indexes are local, built over
imported vectors, never shared"), and it generalises.

## 5. What to put in the architecture

Today every collection's TOML names absolute paths (`[paths] manifest`,
`fts`, `lancedb`) and a `corpus_dir`, and there are one-off knobs
(`ACQUIRE_STAGING_DIR`, `PUBMED_PACK_DIR`, `PACK_MOUNTS_DIR`). The mechanism
exists; the *policy* does not, so placement is decided per collection by
whoever creates it, and gets it wrong silently.

Proposed:

1. **Two named tiers in Tier-1 config**: `FAST_DIR` and `BULK_DIR`.
2. **Placement by artefact class, not by collection.** A collection that
   declares no explicit paths gets `FAST_DIR/<name>/{manifest.db,fts,lancedb}`
   and `BULK_DIR/<name>/` for corpus, packs and derived caches. Explicit
   paths keep overriding, for the cases that need it.
3. **The create flow shows which tier each artefact will land on**, with the
   free space on each — the moment an operator can still choose cheaply.
4. **A boot check**, not a boot refusal: warn when a bulk artefact is on the
   fast tier or the fast tier is under `VECTOR_INDEX_MIN_FREE_GB`. Placement
   is an operator's decision; a silent one is the problem.

The tiers are the honest generalisation of what this deployment already did
by hand (STATUS 5i moved pubmed/wikipedia/wikivec corpora to `tank` and left
gutenberg behind — which is exactly the 261 GB above).

## 6. Open questions

1. **Does the Gutenberg mirror have to bring epub/mobi/images at all?**
   `extras` exists for opt-in formats, but the corpus is fetched by rsync
   from the *epub* mirror, so they arrive regardless. A filtered rsync would
   avoid moving 230 GB that is never read — at the cost of no longer holding
   a faithful copy of the origin, which invariant 11 may care about.
2. **Should the vector table's payload and index be split across tiers?**
   The `text` column is bulk and cold; the ids and the IVF index are hot and
   small. Lance keeps them in one directory, so this is not available today —
   but it is the reason the vector store is simultaneously the biggest and
   the most latency-sensitive artefact, and worth revisiting.
3. **wikivec's `_indices` is 125 GB** for 58M vectors. IVF-PQ at 96 B/vector
   would be ~6 GB, so either the index is IVF-FLAT (storing the vectors a
   second time) or stale index versions are retained. Worth measuring before
   sizing any tier.

## 7. Decided, 2026-09-21

The operator: "export bundle should be done in tank only", and the DRAG
goes to tank too "unless the snapshot can be used in place of the existing
collection data". It cannot. Search reads the manifest, the text index and
the Lance table, and nothing reads `drag/` per query. The DRAG is what
REBUILDS those stores on a peer, not a substitute for them. (The one partial
exception is open question 2 in §6: records could supply chunk text
instead of the copies kept in Lance and tantivy, at the price of reading
from the HDD per query.)

- **Exports**: `EXPORT_DIR`, a Tier-1 path to bulk storage. The Library's
  download, `snapshot vectors`' default output and a contribution are
  built under `EXPORT_DIR/<name>/`. When `EXPORT_DIR` is set but not
  mounted, the export is refused, never created, because inside a container
  an unmounted path is the container's own storage. Unset means beside the
  manifest, as before (a single-disk node).
- **The DRAG**: a bulk dataset bind-mounted at `collections/<name>/drag/`,
  exactly as `origin/` is. The records build still stages under `state/`,
  on the fast pool, and `drag.promote` crosses the mount by copying to a
  hidden name next to the target, syncing it, and renaming. That is still
  all-or-nothing. The bare `os.replace` it replaces would have failed on
  the first shard with EXDEV.
- **Not automatic**: a new collection's `drag/` is on the fast pool until
  its mount is added. Add it before the collection's first `records build`.

## 8. ssd3, the 8 TB SSD pool: measured before deciding (2026-11-07)

The operator: "Check ssd3 speed before deciding and estimate the performance
loss if any". The proposal under decision: the Lance vector tables and the
derived cache (`LOUISE_CACHE_DIR`) on ssd3; the manifests and text indexes stay
on ssd4. Raw files: /root/measure/fio-ssd.txt, ssd-rerun.txt, real-ab.txt and
fio/*.json on the node.

**The disks.**

| pool | device | interface | NAND | written | rated | free (before) |
|---|---|---|---|---|---|---|
| ssd3 | Samsung SSD 870 QVO 8TB (fw SVQ02B6Q), single disk, ashift 12 | SATA 3.3, 6 Gb/s | QLC | 53 GB (103,444,305 LBAs), 5,765 h | 2,880 TBW (Samsung's rating for the 8 TB QVO) | 7.14 TiB |
| ssd4 | Kingston FURY Renegade 2 TB, SFYRDK2000G (fw EIFK51.2), single disk, ashift 12 | NVMe, PCIe 4.0 x4 (LnkSta 16 GT/s x4) | TLC | 36.4 TB, 2 % used, 1,653 h | 2,000 TBW | 592 GiB |

**Method.** Test datasets `ssd3/louise-fio` and `ssd4/louise-fio`,
primarycache=metadata (ARC holds no data), compression off, recordsize 128K,
atime off, incompressible buffers; created for this and destroyed after. Reads
use O_DIRECT (OpenZFS 2.4.4): buffered libaio does its read at submission, so
its latency and queue depth mean nothing (the first attempt measured 1 µs and
was thrown away). The ssd3 reads and its sustained write ran with the node
running (nothing of Louise is on ssd3); everything touching ssd4 ran with the
node paused.

**fio, two runs each (MEASURED).**

| job | ssd3 | ssd4 | ssd3 against ssd4 |
|---|---|---|---|
| a. randread 4K QD1 (point reads) | 1,676–2,033 IOPS; p50 424 µs, p99 2.7–4.0 ms | 2,500–2,596 IOPS; p50 399–403 µs, p99 0.51–0.83 ms | p50 the same; p99 5–8× worse |
| b. randread 64K QD4 (vector reads) | 76–123 MiB/s; p50 1.7 ms, p99 9.8–16 ms | 158–160 MiB/s; p50 1.6 ms, p99 1.9 ms | −23 % to −52 %; p99 5–8× worse |
| c. randread 4K QD32 × 4 (IOPS ceiling) | 3,894–3,960 IOPS | 9,718–9,945 IOPS | −60 % |
| d. seqread 1M QD8 | 460–473 MiB/s (SATA's ceiling) | 1,401–1,609 MiB/s | −68 % |
| e. seqwrite 1M QD8 sustained, O_DIRECT | 287 MiB/s the first minute; the cache cliff at 60 s after 18 GiB; then flat at **158 MiB/s** to 1 TiB (6,593 s) | 79–125 MiB/s over 143 GiB: ZFS's direct-write path, not the disk (below) | — |
| e'. seqwrite 1M, 120 s, BUFFERED (as Louise writes) | 535 MiB/s | 733 MiB/s | −27 % (both inside a cache) |
| e''. seqwrite 1M QD8, 120 s, O_DIRECT | 261 MiB/s | 260 MiB/s | the same: ZFS's direct path is the cap |

The 1 TiB written to ssd3 is 0.035 % of its rated 2,880 TBW. ssd3's QLC cache
had refilled by the buffered run (an hour later, after the file's delete), so
its 535 MiB/s is cache speed; the sustained rate is the 158 MiB/s.

**f. A manifest's WAL commit on ssd4 (4K randwrite, fsync each, QD1), 60 s
(MEASURED, noisy).**

| | buffered (SQLite's case), the rerun | O_DIRECT, phase 2 |
|---|---|---|
| alone | 56 IOPS; fsync p50 2.8 ms, p99 338 ms | 317 IOPS; fsync p50 2.3 ms, p99 21 ms |
| beside a seq write on ssd4 (today) | 23 IOPS; fsync p50 2.9 ms, p99 200 ms | 105 IOPS; fsync p50 4.9 ms, p99 31 ms |
| beside a seq write on ssd3 (the proposal) | **98 IOPS**; fsync p50 3.0 ms, p99 111 ms | 2 IOPS; fsync p50 422 ms, p99 1.28 s |

Buffered, which is how SQLite writes, moving the bulk write to ssd3 helps
ssd4's commits: 98 IOPS against 23. The direct-I/O collapse (2 IOPS) did not
repeat buffered; it is read as the O_DIRECT path waiting on the slow pool's
transaction group (`zfs_dirty_data_max` 4 GiB, one limit for every pool), not
as what a manifest sees. The two "alone" rows disagree (56 against 317 IOPS),
so these rows show direction, not a figure to plan on.

**Real workloads, identical copies on both (MEASURED).**

- **2a, a vector search:** normattiva's Lance table (19 GiB copied, the tree
  sha256 `991c35658d0c7966` on both), its own indices (IvfPq, BTree, Bitmap)
  and settings (nprobes 30, refine 30), k=50, 200 real queries (titles of 200
  random normattiva documents, embedded once through its query side, 768
  dims), node paused, four passes each:

  | | p50 | p95 |
  |---|---|---|
  | ssd3 | 352–384 ms | 415–479 ms |
  | ssd4 | 96–101 ms | 119–126 ms |
  | ssd3 against ssd4 | **×3.7–3.8** | ×3.5–3.8 |

- **2b, the derived cache:** wikidata's pack (there is no labels.sqlite; the
  cache is 122 GiB of Parquet files), 40 files (4.8 GiB, the tree sha256
  `8df8c2b1f81a02c1` on both), 200 random row groups read as the parse reads
  them (pyarrow `read_row_group`):

  | | 1 thread | 11 threads (the parse pool) |
  |---|---|---|
  | ssd3 | 72.5 row groups/s, 80 MB/s | 180.3 row groups/s, 204 MB/s |
  | ssd4 | 113.1 row groups/s, 125 MB/s | 296.2 row groups/s, 335 MB/s |
  | ssd3 against ssd4 | −36 % | −39 % |

**The estimate, one row per workload.**

| workload | ssd4 today | ssd3 | loss or gain | kind |
|---|---|---|---|---|
| a search's dense step, one collection, cold | ~98 ms p50, ~122 ms p95 | ~370 ms p50, ~450 ms p95 | **+~270 ms a collection, ×3.8**; a search over several collections pays it in each, in parallel | measured (2a) |
| embedding writes | card-bound: wikidata 34 chunks/s × 2,048 × 2 B ≈ 0.14 MB/s | 158 MiB/s sustained | none: ssd3's margin is ~1,000× | modelled |
| a 355 GiB table rewrite or carry copy (wikipedia's) | ~11 GiB/min measured (2026-10-31), ~32 min | 18 GiB at cache speed, then 158 MiB/s (9.3 GiB/min); a copy inside ssd3 also reads over the same SATA link (~465 MiB/s in all) | ~39–60 min against ~32 min: **+20 % to +85 %** | modelled from 1e and 1d |
| a label-bound wikidata parse | 296 row groups/s at the pool's width | 180 row groups/s | **−39 %** where the parse waits on the pack; nothing where it is CPU-bound | measured (2b) |
| a manifest commit under a vector copy | 23 IOPS, fsync p99 200 ms (buffered) | the copy on ssd3: 98 IOPS, p99 111 ms | **gain**: the commits stop sharing the NVMe with the bulk write | measured (1f), noisy |

**Reading.** ssd3 is a SATA QLC disk: 158 MiB/s sustained writes after an
18 GiB cache, about a third of ssd4's sequential reads and of its reads at
depth. The placement it costs visibly is the one the proposal puts first:
Lance tables on ssd3 make a cold dense search about 3.8× slower (+270 ms p50
per collection). The derived cache loses 36–39 % on pack reads, which matters
only while a wikidata parse waits on them. What the proposal gains is ssd4's
commits under bulk vector writes (1f) and 7 TiB of room. Vectors ARC already
holds would not pay the loss; ARC's size against the tables was not measured
here.

### Decided (operator, 2026-11-07): B, ssd3 as the build area

"I agree." The SEARCHED vector tables stay on ssd4: a cold dense search is
×3.8 slower on ssd3 (2a). ssd3 is the BUILD AREA: a table being built — a
collection `hold collection new` makes (case A) or re-creates under
`<name>.next` (case F), not yet the one people search — is written there,
which costs nothing (embedding writes ~0.1 MB/s against ssd3's 158 MiB/s
sustained), and moves to ssd4 when it takes over. The coming uses: the
Nemotron re-embeds of gutenberg and wikipedia (wikipedia's old table was
355.6 GiB; at 2,048 dimensions a new one is about twice that, beyond ssd4's
604 GB free) and the re-creation copies. The derived cache
(`LOUISE_CACHE_DIR`) stays on ssd4.

**Built** (app/vector_place.py; 07 §7.3 `vectors move`, §7.8):

- `LOUISE_BUILD_DIR` (identity tier), resolved by app/vector_place.py alone
  (invariant 6's guard, tests/test_invariant_6_storage.py). Unset: every
  path is today's.
- **A symbolic link**, the pattern of SHARDS_ROOT/ORIGINS_ROOT/BUNDLES_ROOT:
  `<name>/lance` → `<LOUISE_BUILD_DIR>/lance/<name>`. Every reader and writer
  keeps opening `<name>/lance` (`vector_place.table_dir`, the one function),
  so the caches keyed on that path — LanceDB connections, index state,
  writer registrations — stay right across a move: the key does not change
  and the bytes behind it are proven the same. LanceDB does not resolve the
  link (measured, lancedb 0.40.0 / pylance 9.0.1: a connection and a table
  opened before a switch answered the same rows and neighbours after it,
  the original deleted; a write after it landed behind the new link). The
  alternative, a placement recorded in the collection's state, would change
  `Collection.lance_dir` on a move and leave every other process's caches on
  a deleted path.
- `new` places the link before the collection's file exists; the carry
  copies onto the build area through it; case F's swap, after the
  operator's forget, moves the table to ssd4; `hold vectors move <c> --to
  hot|build` moves any table: copy (a file a unit, hashed as read, a
  journal), verify (tree digest both sides, rows), switch (one
  `renameat2(RENAME_EXCHANGE)` under the vector write lock), the source
  removed. Dry run by default, with the estimate at the rates above.
  `forget` and `rename` follow the link.

**Done 2026-11-07:** München created `ssd3/louise-build` with the
properties below (7.14 TiB free); `BUILD_HOST = "/mnt/ssd3/louise-build"`.

**For München, before the first use:** the dataset, then its name in
deploy/16f/flatten_mounts.py `BUILD_HOST` (or `LOUISE_BUILD_HOST=` to the
deploy), then a deploy, which mounts it at /build in web and worker and
sets `LOUISE_BUILD_DIR=/build`:

    zfs create -o atime=off -o compression=lz4 -o recordsize=1M \
               -o primarycache=metadata ssd3/<dataset>

- `atime=off`: reads never write.
- `compression=lz4`: f16 vectors barely compress, and lz4 gives up early on
  incompressible blocks; the text column does compress.
- `recordsize=1M`: what the build area sees is sequential — Lance writes
  whole data files and never rewrites one, a compaction or index build
  scans, and a move reads every file end to end at SATA's ceiling. Large
  records cut the metadata and the QLC's read-modify-write. The cost is on
  random reads of a table SEARCHED from ssd3 (case A before it takes over,
  or the hour of a swap's move): a 4 KiB vector read pulls a 1 MiB
  record. Those searches are the ones this decision keeps off ssd3.
- `primarycache=metadata`: a table on ssd3 is read once (built, moved); its
  data in the ARC would evict the searched tables' on ssd4. The cost: a
  table searched from ssd3 hits the disk each time — measured as such in 2a.

**Not measured yet:** `RENAME_EXCHANGE` on the node's OpenZFS 2.4.4 inside
the containers (supported on Linux by OpenZFS; where it is not, the switch
is three renames, a window of microseconds with no `lance`, finished by the
next run); the move's real rates, which its output reports.

## 9. Apps to ssd3: decided (operator, 2026-11-07): "A + all apps"

München's read-only inventory (2026-11-07, raw output on the node at
/root/measure/inventory.txt): ssd4 1.81 T, 663 G free (64 % used); ssd5 944 G,
139 G free (85 %). The choices put to the operator:

| choice | free after | pros | cons |
|---|---|---|---|
| A: what nothing reads | ssd4 ~806 G, ssd5 ~280 G | nothing slows | ssd5 still the tight one |
| **A + all apps (chosen)** | ssd5 ~480 G (~530 G once the backends' snapshots expire) | doubles ssd5's room | backends start from SATA (first start after a reboot); deploy builds and tests on ssd3; ~25 min outage for the move; writes past the 18 GB SLC cache at 158 MiB/s |
| nothing | ssd5 139 G | no work | ssd5 85 % full |

What moves:
- A: ssd4 louise-rag/rebuild (134 G, untouched since 09-21) and the
  measurement folders (~9 G); ssd5 apps/llama-cpp (39 G, unmounted) and old
  model folders (~9 G).
- All apps: the TrueNAS apps pool (Docker images, build cache, app_mounts,
  including the stopped ComfyUI, Jellyfin and Immich), localai/backends
  (87 G), open_webui_data.

What stays on NVMe: every LocalAI model (read at every model load, not only
at boot) and all of Louise's searched data (ssd4).

Datasets: ssd3/apps (recordsize 128K, primarycache all: programs need the
ARC) and ssd3/attic (recordsize 1M, zstd). Not louise-build, whose
primarycache=metadata is right for tables read once and wrong for programs.
ssd3 is already replicated to tank nightly (operator, 2026-11-07), the build
area included. That is worth keeping: a table being built there represents
days of card time.

Measured before and after (two runs each): a LocalAI model load, cold right
after a restart and warm; a deploy's total time and its test stage. If a model
load is more than ~2 s slower cold, or slower warm, localai/backends alone goes
back to NVMe.

**Done 2026-11-07 (München).** The operator said yes in München's session to
the moves and to the prune. The prune removed 44.13 GB of non-Louise images and
99.68 GB of build cache; every louise-rag and vega tag was kept.
- A: copied to ssd3/attic with rsync -aHAX, then each verified (file count,
  bytes, sha256 listing) before its source went. On ssd4: rebuild 195.9 GB,
  bakeoff 7.3, measure 1.3. On ssd5: apps/llama-cpp 41.7, bakeoff 7.4,
  dar-legal-it-models 1.2, dnabert2-models 0.9 and the small ones. data/staging
  (live: ACQUIRE_STAGING_DIR) and data/hub stay on ssd4.
- Apps:
  - ix-apps was moved by TrueNAS's own migration. It did not carry the local
    images; they were recovered from the old dataset, with the same image IDs
    (node-runbook.md §10).
  - localai/backends and open_webui_data were moved with zfs send, then an
    incremental after the stop. The engines' tree digests are equal on both
    sides (ea2417d91dcc 0e705989 / 32b0caf3, louise-python-20261021a
    dfb5e81a / 4724a9b9).
  - The sources are kept as `*.moved`; destroying them is the operator's yes,
    with their size.
  - Down 20:47–21:24 (37 min, the image recovery included). On start, the
    worker re-checked every kept run with the same ids.

Measured, first answer's wall clock, the node paused; BEFORE two runs, AFTER
three:

| | BEFORE | AFTER |
|---|---|---|
| LocalAI ready after a restart | 2.03, 1.02 s | 1.03, 1.02, 1.02 s |
| reranker, cold | 7.71, 6.80 | 6.92, 6.99, 6.89 |
| chat, cold | 5.81, 5.61 | 11.22 (the first read after the move), 5.60, 5.60 |
| reranker, warm | 5.03, 6.89 | 7.10, 6.89, 6.89 |
| chat, warm | 5.70, 5.60 | 5.79, 5.69, 5.59 |

No AFTER run is more than 2 s above the slowest BEFORE one, except the first
read after the move. **The backends stay on ssd3.** Free space right after,
before the snapshots expire and the `*.moved` datasets go: ssd4 594 G (~205 G
more once its 2-hour snapshots release), ssd5 107 G, ssd3 6.8 T.
