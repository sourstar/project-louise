# Wikipedia: one version per article in search — as an annotations collection (2026-11-10)

*The operator, 2026-11-10, relayed by München after seeing one page ranked several times in several languages:
wikipedia search follows the smart-embedding guidelines ([wikipedia-smart-embedding.md](wikipedia-smart-embedding.md)).
Each article appears ONCE. The version shown is English by default; if another language's version is BIGGER, that
one ("some entries are specific to a nation, and the version in that nation's language is often bigger and more
accurate"). Both legs and their fusion.*

## The rule

- **The article** is the Wikidata item (`wikibase_item` from the pinned run's `page_props`, read as the
  smart-embedding pass reads it). A page no item names is its own article.
- **The version shown** is the item's largest article by the manifest's `tokens` (the smart pass's measure, one unit
  across scripts); English when the sizes tie or one is unknown.
- **Its place** is the best rank any of its versions reached after the reranker. The other versions found are
  listed under it (`editions`), as gutenberg's editions are.

## Where

The step already exists: 05 §5.3's "each work once", after the reranker and before the cut to `k`
(`retrieval._one_hit_per_work`, `app/ask/works.py collapse`). For wikipedia the work groups come from the item map
instead of an annotations collection's work claims, and the edition order is "largest, English on ties" instead of
the reader's language. Both legs are covered by construction: the collapse runs on the fused, reranked list.

## Data

The worker's smart-embedding pass already reads `(rowid, native_id, tokens)` and every language's item ids. It also
writes a derived map under `LOUISE_CACHE_DIR` (`app/derived.py`, invariant 6): document → item, item → the version
shown. The web memory-maps it, so the ~1 GB the pass holds never enters the web process. It is rebuilt when the
pass's signature changes (the choice, the manifest's last row, the page_props run).

## The passage shown when the largest version did not match (operator, 2026-11-10: A)

When only, say, the Italian version matched the question and the English one is bigger, the shown version's
passage is found by a text search inside that one article (the `keys` filter, `book_ids`) with the plan's variant
for its language — else its first passage; the version that matched stays listed with its rank (`editions`). The
article keeps its best rank's place and scores (the merge across collections reads them). The hit says which it
is (`version_shown`). Built: `app/wiki_versions.py`, the worker's scan (`_build_wiki_versions`), the collapse in
`retrieval._one_hit_per_work`; tests `tests/test_wiki_versions.py`. Rejected, the operator's own first idea (hide the
other versions): an article matched only through a hidden version would disappear.

## Amended the same day (operator): an annotation, not the collection's own logic

"I don't want to silently hijack the searches of the other peers. The collection itself shouldn't enforce a logic
that might hide some results … the right approach would be to create an annotation." So #467's grouping inside
wikipedia's search and the worker's map are removed. The rule is stated instead as `same-work` claims in an
annotations collection, `wikipedia-articles` (02 §2.10): a peer applies it only if it holds it, a reader can untick it
on the Ask page, and every version stays listed under the article (05 §5.3, invariant 4).

- **The claims** (`app/wiki_versions.py claims`, written by `tools/annotations_from_wikipedia.py`): a star per
  article, every other version against the version shown, evidence the Wikidata item and both lengths. `--count`
  says how many claims the annotations collection would hold.
- **Open before the build of the collection:** the claim index is held in memory at search time
  (`app/hold/annotations.py index_of`), built for gutenberg's thousands of claims; wikipedia's articles are millions.
  The count decides whether that index needs a memory-mapped form first.
- **Then:** the search shows the largest edition where the claims give the lengths (English on ties), and the shown
  version's best passage where it did not match (the operator's A).

## Measured: the size (München, 2026-11-10, `tools.annotations_from_wikipedia wikipedia --count`)

4,476,494 articles of two versions or more, 16,803,013 versions, **12,326,519 claims**; the count took 158.6 s and
6.97 GiB at its peak. Two pieces of the annotations machinery were built for gutenberg's thousands of claims and do
not hold at this size; both are local, neither changes what peers exchange (09 §9.4's claim records stay as they are):

1. **Creating it** (`app/hold/annotations.py create`): `read_claims` keeps every claim and its raw line in memory
   (~12 M objects), and `target_records` reads the content hash of every claimed record from the target's parts —
   16.8 M of wikipedia's 52.9 M records, most of its frames. Needed: a STREAMING create — claims read, checked and
   pinned part by part in bounded memory, the target's records read once in part order, the claim parts written as
   they fill. **Built 2026-11-10** (`app/hold/annotations_stream.py`): the file copied once into a workspace and
   read in four passes on the parse pool — checked, the targets read once, pinned in the file's order with the
   numbering keys in an external merge sort, the parts written by number — keeping ~135 bytes a claim. Every byte
   written is the in-memory path's (`tests/hold/test_annotations_stream.py`, and against origin/main's code).
   Measured on a synthetic star (tracemalloc, 200,000 claims): in-memory 898 MB, streamed 135 MB in one process
   (one part of 50,000 records), 69 MB in the parent on the pool. RSS on the pool (3 processes): 1 M claims in
   60 s, parent 421 MB; 2 M in 120 s, parent 556 MB, all processes 1.3 GB — so ~2 GB parent for 12.3 M.
   **`hold annotations update` streamed the same way** (2026-11-10): the previous snapshot's claims kept as
   identity digest, number and hash (~40 bytes a claim), read a part a task; a re-check hard-links the held claims
   file into the workspace instead of copying it (RULE 1). Same bytes, result and log as the in-memory path
   (`tests/hold/test_annotations_update_stream.py`). Measured on the star, 200,000 claims held and a new file:
   in-memory 1,404 MB traced, 1.7 GB RSS, 30 s; streamed on the pool (3) 55 MB traced, parent 168 MB RSS, all
   processes 756 MB, 16.5 s (serial: 294 MB, 29 s). 2 M claims held, re-checked after the target moved (every claim
   re-pinned, 39,605 dropped): 126 s, parent 689 MB, all processes 1.34 GB — ~300 bytes a claim in the parent, most
   of it the delta's uid strings (every claim changed), so ~3.7 GB for a re-check of all 12.3 M.
   **Workspaces clear themselves**: each create and update holds an exclusive flock beside its workspace
   (`.annotations-<create|update>.<name>.lock`); every create, update and preview, and the worker at boot, removes
   and says each workspace whose lock can be taken (its process is gone) — never a live one, and a second create of
   the same name is refused rather than let clear the first's (`annotations_stream.sweep`).
2. **Searching with it** (`index_of`, `ClaimIndex`): every claim as a Python object in the web process, ~12 M of them.
   Needed: for a large annotations snapshot, a WORK INDEX built once per snapshot as derived data under
   `LOUISE_CACHE_DIR` (invariant 6) and memory-mapped by the web: the claimed uids sorted (with their strings, so a
   lookup never trusts a hash alone), each uid's work (connected components of the work claims, a union-find in
   numpy), each work's members, and the lengths the evidence states. `works.work_groups` reads it in place of the
   in-memory walk; a small collection keeps the in-memory index.
   **Built automatically** (2026-11-10): `create` and `update` build the claim index of a large snapshot as soon as
   it is written, and the worker's `claim-index` thread builds any large current snapshot's that is missing
   (adopted from a peer, made earlier) at boot and when the files change, low priority, idempotent, a failure said
   once per snapshot (`claim_index.maintain`). The build reads the parts on the parse pool (`index_part`), the same
   bytes as the one-process build, and no longer reads a first snapshot's delta (which lists every claim) into the
   shared snapshot cache.
3. **Showing it**: `works.collapse` shows the largest edition where the claims state the lengths (English on a tie),
   and the shown edition's best passage when it did not match (the operator's A) — a rule of the search's grouping
   step that only an annotation stating lengths turns on; gutenberg's claims state none and are unchanged.
   **Built** (`works.largest`, `collapse`, `best_passages`): the lengths and the claims' centre are read from each
   claim's evidence; a tie between two non-English versions goes to the centre (the producer's own choice). The shown
   version keeps the work's best place and scores, brings its own passage, and says so (`shown_as`, invariant 4(2)).
   A version that did not match costs one text search inside that one record (its language's query variant, else its
   first passage), only for the works on the page (`limit = k`), side by side (8 threads). Its time is inside the
   search line's `rerank`; München times it on the node after the annotation is created.

## Created on the node (München, 2026-11-11, operator "A")

On 77f4b73's image; each pass in its own container (16 GB cap), wall time and peak RSS by getrusage:

| step | wall | peak RSS | result |
|---|---|---|---|
| `tools.annotations_from_wikipedia` | 240.9 s | 6.81 GiB | claims file 4.21 GB |
| `create --dry-run` | 460.4 s | 2.70 GiB | exit 0 |
| `create` | 828.6 s (13.8 min, ~0.9 M claims/min) | 2.68 GiB | 12,326,519 claims, snapshot `sha256:2880ec5ab96b…`, collection `sha256:2b4b4c7ec952…` |

- The parts: 1.4 GB on tank (`louise-shard/wikipedia-articles`); the collection's directory on ssd4 is 73 KB.
- The claim index was built inside the create ("12,326,519 claims, 24,653,038 ends: searched from disk") and found by the
  worker at 16:12:19Z: 345 MB under `cache/annotation-claim-index/`, 247 parts. The estimate above (~2 GB parent for
  the create) held: 2.68 GiB peak.
- One wikipedia query ("Leonardo da Vinci", 10 hits, hybrid, no translation; `query_service.run_search`), three runs,
  ms:

  | | rerank | total |
  |---|---|---|
  | before | 1,975 / 2,500 / 2,422 | 4,341 / 3,197 / 3,189 |
  | after | 3,305 / 1,959 / 1,849 | 6,653 / 2,689 / 3,362 |

  Warm, the shown version's passage lookup adds nothing measurable (the difference is under the node's normal load);
  the first run after includes the index's first, cold open.
