# Pre-search caches: what a search could look up instead of compute

*Opened 2026-11-12. The operator kept `wikipedia-articles` "if it speeds up the
search", renamed it with "cache" in the name ("in the end it is a sort of
pre-search"). They also asked for a note on whether similar pre-search caches
might speed up some searches. This note is that check. Nothing in it is built yet.*

## The rule a cache here must keep

A pre-search cache holds a result that the search would otherwise compute,
keyed on everything the result depends on, so the same inputs give the same
output with or without it (invariant 4 (5), invariant 3):

- Key on physical identity and versions: the collection id, the snapshot id,
  the space id, the model's digest, never a name (CLAUDE.md, "Caches,
  cursors and bookkeeping key on physical identity").
- Derived data, re-creatable: under `LOUISE_CACHE_DIR` through
  `app/derived.py` when it lives on disk (invariant 6), or in process memory,
  bounded.
- It never decides anything the search would not. A miss computes. A hit is
  the same bytes.

`wikipedia-articles-cache` is already one of these. Its claims are the
one-version-per-article grouping, precomputed from the records' Wikidata
items and lengths (app/wiki_versions.py), and read at search time from the
claim index (app/hold/claim_index.py). It is an annotations collection, not
a private cache, because a reader can untick it and a peer can decline it
(the operator, 2026-11-10: "I don't want to silently hijack the searches").

## To measure first (search `timings`, the deployed node)

1. **Does `wikipedia-articles-cache` speed up the search?** The operator's
   condition for keeping it. Run the same 50 wikipedia queries twice: once
   with the chip on, once unticked. Read `timings.total` and the grouping's
   share of it. Then compare with what the grouping cost live before #468
   removed it (#467's worker map, if its log lines survive). The cache
   cannot be faster than no grouping at all. The question is whether it is
   faster than computing the same grouping live. If its cost per search is
   above the live map's, the cache goes and the grouping is computed per
   search. Until then, it stays as the operator said.
2. **Which stage repeats across the Ask page's two searches** (the
   question's and the answer's): `timings.dense`, `fts`, `rerank`, and the
   plan. The plan is already cached per (query, languages)
   (`language_plan._cache`), and so are result translations
   (`web._translation_cache`). The rest are computed again.

## Candidates, in the order the timings are likely to rank them

| candidate | key | saves | pros | cons |
|---|---|---|---|---|
| status quo | — | — | nothing to keep consistent | every repeat pays the model calls again |
| **rerank scores** | (reranker model digest, query text, chunk id, chunk content hash) | the reranker call for pairs already scored: the answer's search re-ranks most of the question's candidates | the largest per-search model cost after the chat; exact (the same model and pair give the same score: gated bit-exact) | memory per pair (~60 B); must drop on a reranker swap (the digest in the key does it) |
| **query vectors** | (space id, query side's weights digest, instruction, text) | `embeddings.embed_query` per collection and variant: a search over 5 collections × 4 language variants is up to 20 encoder calls | exact under invariant 12; tiny (a vector per key) | a cold query gains nothing; the encoder call is often <50 ms when the card is free |
| **whole result lists** | (query, collections and their snapshot ids, settings, annotations chosen) | everything, for a repeated query | the biggest saving where queries repeat (eval sets, the Ask page's reload) | invalidated by any snapshot, setting or annotation change; real readers rarely repeat a query |
| **more annotation-style groupings** | as `wikipedia-articles-cache` | a per-search join: wikidata's entity → its wikipedia pages, pubmed's errata/retraction → the notice | shared, inspectable, a reader's choice | a collection of its own to build and update; only where a join is costly live |

Already pre-computed, not candidates: the catalogue index (app/catalog.py),
the field facets (value_postings), the text indexes, the relations index
(app/ask/traverse.py `index_edges`, which the search now reads for
gutenberg's works), the part facts (app/ask/part_cache.py).

## What would settle it

The node's numbers from measurement 2: stage times for the question's search
and the answer's search on the same queries. A stage that costs a large
share of the answer's search and repeats the question's work is the first
cache to build, and the standard is a bounded LRU (`functools.lru_cache`, or
`cachetools.LRUCache` if a size bound in bytes is needed). The rerank cache
comes first if `timings.rerank` dominates, as it did in the rerank work of
2026-10 (scheduling-log.md).
