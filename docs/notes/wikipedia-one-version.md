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
   they fill.
2. **Searching with it** (`index_of`, `ClaimIndex`): every claim as a Python object in the web process, ~12 M of them.
   Needed: for a large annotations snapshot, a WORK INDEX built once per snapshot as derived data under
   `LOUISE_CACHE_DIR` (invariant 6) and memory-mapped by the web: the claimed uids sorted (with their strings, so a
   lookup never trusts a hash alone), each uid's work (connected components of the work claims, a union-find in
   numpy), each work's members, and the lengths the evidence states. `works.work_groups` reads it in place of the
   in-memory walk; a small collection keeps the in-memory index.
3. **Showing it**: `works.collapse` shows the largest edition where the claims state the lengths (English on a tie),
   and the shown edition's best passage when it did not match (the operator's A) — a rule of the search's grouping
   step that only an annotation stating lengths turns on; gutenberg's claims state none and are unchanged.
