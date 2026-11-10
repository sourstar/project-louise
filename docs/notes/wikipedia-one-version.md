# Wikipedia: one version per article in search (design, 2026-11-10)

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

## Open: the passage shown when the largest version did not match

When only, say, the Italian version matched the question and the English one is bigger, the shown version's
passage is found by a text search inside that one article (the `keys` filter, `book_ids`), a few ms per hit; the
version that matched stays listed with its own passage. The operator's choice — see STATUS.
