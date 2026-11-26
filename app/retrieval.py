"""Retrieval orchestration.

Pipeline per query:
  0. resolve -> expand -> plan (architecture-v2.md §4, docs/05): regex/dictionary
     entity resolution against the reference layer (app/refs/). No entities found
     -> every step below is a structural no-op, degenerating exactly to the
     pre-P0.3 pipeline. Entities found -> two separate outputs, NEVER
     blended (CLAUDE.md invariant 4): structured lookups become PinnedFacts
     for the LLM context (never ranked, never in RRF); the ranked lists are
     left as they are — 05 §5.2: nothing boosts (the soft RRF boost
     doc_keys-tagged hits used to get lost on TREC PM, 2026-10-25). The legs
     are handed only the entities the query NAMES; the facts take those and
     docs/05's one hop, bounded per link, with what the bound left out named
     in `facts_withheld` (2026-09-21: one gene expanded to 21,968 variants).
  1. catalog search (the catalog index over title/author/subjects,
     app/catalog.py — subjects are English LoC headings for every
     language, so this is cross-lingual for free)
  2. lexical fan-out: the query in every language the indexes hold, BM25
     each group with its own (app/language_plan.py — once per search)
  3. dense search over whatever is embedded (cross-lingual natively)
  4. reciprocal-rank-fusion merge, optional external rerank
  5. lazy-embed hook: top books not yet embedded get queued

P0.1 runs a single (Gutenberg) collection; source-qualified, multi-collection
federation is P0.5's job. Search hits keep their pre-refactor int `book_id`
shape (native_id) — chunk/document identity internally is `uid`-keyed, but the
public API surface is unchanged.
"""
import contextvars
import json
import re
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from . import (cancel, catalog, collections as collections_mod, config, copy_state, db,
               embeddings, model_load, fts, httpc, language_plan, refs, work_dedup)
# the refusal check by its own name: a test that swaps `httpc` for an httpx
# client (tests/test_localai_backends.py) keeps it (#798, 2026-11-22)
from .httpc import raise_for_status as _raise_for_status
from .rage import dispatch
from .language_plan import LanguagePlan
from .refs.resolver import row_of
from .bundles import verify_on_use
from .hold import runs as hold_runs

RRF_K = 60


@dataclass(frozen=True)
class PinnedFact:
    """Facts-by-key — never a ranked hit. Deliberately NOT a dict and NOT
    subscriptable: CLAUDE.md invariant 4 requires PinnedFact instances never
    enter _merge_rrf's `lists`/`merged`. _merge_rrf accesses h["chunk_id"]
    etc., so passing a PinnedFact into it raises TypeError immediately — the
    boundary is structurally enforced, not just conventional.

    `via`: the link a fact was reached through, when docs/05's expand reached
    it (§5.4) — `{"from": the named entity, "link": the declared link's name,
    "on": its join}` — and None when the query named the entity itself. A
    ClinVar variant pinned beside "BRCA2" otherwise reads as something the
    reader asked about."""
    entity_id: str
    kind: str
    source_id: str
    label: str | None
    fields: dict
    via: dict | None = None

def catalog_search(con, query: str, limit: int = 10,
                   langs: tuple[str, ...] | list[str] = (), collection=None) -> list[dict]:
    """The catalogue leg's hits (`catalog_leg` without its reason)."""
    return catalog_leg(con, query, limit, langs, collection)[0]


#: (index path, reason) already said on stdout by this process: once each
_catalog_told: set[tuple[str, str]] = set()


def catalog_leg(con, query: str, limit: int = 10,
                langs: tuple[str, ...] | list[str] = (), collection=None
                ) -> tuple[list[dict], str | None]:
    """(the catalogue leg's hits, why it is empty when it cannot answer).
    `langs`, when given, keeps only documents written in those languages
    (a query's language restriction, app/language_plan.py).

    The collection's catalog index answers (app/catalog.py): top-k without
    scoring every match, 18–55 ms on pubmed's 40M records where the SQLite
    FTS5 catalog it replaced took 4–57 s. A collection whose index is not
    complete yet (its first backfill, or a rebuild after a version change),
    or whose index cannot be read, has an empty catalog leg; the other legs
    still answer — and the search SAYS so (`legs_withheld`, leg "catalog").
    It used to say nothing: an index the process could not open (München,
    2026-10-21: a read-only mount, tantivy's lock file) emptied the leg with
    `legs_withheld` [] and no log line, and two measurement runs scored
    search without it (title MRR 0.56 against 0.87) without knowing."""
    q = " ".join(t for t in query.split() if t.isalnum() or t.replace("'", "").isalnum())
    if not q:
        return [], None
    collection = collection or collections_mod.default_collection()
    try:
        hits = catalog.search(con, collection, q, limit, langs=langs)
    except Exception as e:   # noqa: BLE001 — an index problem empties the leg, never fails a search
        why = f"the catalogue index could not be read: {type(e).__name__}: {e}"[:300]
    else:
        if hits is not None:
            return hits, None
        why = ("the catalogue index is not complete yet (its first backfill, or a rebuild): "
               "the leg is empty until it is")
    key = (str(catalog.path_for(collection)), why)
    if key not in _catalog_told:
        _catalog_told.add(key)
        print(f"{time.strftime('%H:%M:%S')} [{collection.name} warn:catalog] {why}", flush=True)
    return [], why

def record_of(hit: dict) -> str:
    """The record a chunk hit belongs to: its uid, `<plugin>:<native_id>` —
    a chunk id is `<uid>:<seq>:<version>` (app/chunking.py), and a native
    id may itself hold a colon, so the split is from the right."""
    return str(hit["chunk_id"]).rsplit(":", 2)[0]


#: 05 §5.1's leg order, for the one tie it leaves open: a record ranked
#: equally by both legs carries the text leg's best chunk
_LEGS = ("text", "vector", "catalog")


def _merge_rrf(lists: list[list[dict]], depth: int | None = None, *,
               own: frozenset[int] | set[int] = frozenset(),
               query_lang: str | None = None) -> list[dict]:
    """05 §5.1–5.2 (amended 2026-10-09: this is what the code now does).

    TWO LEGS, AND THE RECORD IS THE UNIT. `lists` are what the legs
    returned, chunk hits in rank order: one list per text query variant
    (a language group's translation, or one of a mixed group's), and the
    vector leg's (`source == "dense"`). A record's rank in one list is its
    position among that list's distinct records — the rank of its best
    chunk there. The text lists are variants of ONE leg (§5.2: "a variant
    is a query, not a leg"): the leg orders its records by their best rank
    across the variants, ties by uid bytes, and a record's rank in the leg
    is its POSITION in that one order — so the leg casts one vote per
    rank, however many languages it went out in (counting each variant's
    rank 1 as the leg's rank 1 would let sixteen records share it, which
    is the flood again: R@10 0.237 against 0.388 on the same legs). The
    vector leg is one list already. Each leg contributes its top `depth`
    records, and a record's score is the sum over the legs that ranked it
    of 1 / (60 + rank). Ordered by score, ties by uid bytes (one
    collection: `collection_id` is the same for every record).

    It used to fuse CHUNKS from one list per language group: a gutenberg
    query entered RRF as 16 text lists and one vector list, so each
    group's rank 1 — Hungarian's, Romanian's — scored like the vector
    leg's, and a book filled several slots, one per matching chunk.
    Measured over 451 queries (docs/notes/one-language-per-work.md): the
    work in the fused top 10 for 14.4 % of them that way, 39.7 % fused as
    the specification says.

    Each fused entry is the record's BEST CHUNK — the one that ranked
    highest in the leg that ranked the record highest (the text leg on a
    tie) — with `leg_ranks` (§5.3 `legs`), `sources` (every list that
    returned the record: `fts:<group>`, `dense`) and `rrf`.

    TIES IN THE TEXT LEG (amended 2026-10-09). A book is indexed in one
    language group only, so sixteen variants give sixteen different rank-1
    records, and their order decides positions 1–16. By uid bytes alone it
    was arbitrary, and title queries paid for it (R@10 0.821 → 0.615 on
    39). Equal best ranks now go: a rank reached by the USER'S OWN WORDS
    first — `own`, the indexes of the lists run with the query as typed —
    then a record written in `query_lang`, the language the query is in
    (`LanguagePlan.query_lang`), then uid bytes. Measured on the captured
    legs (tools/measure/one_language_rescore.py): titles R@10 0.744, MRR
    0.501 (uid alone: 0.615, 0.419); descriptive R@10 0.397, MRR 0.219
    (0.388, 0.187). Neither key boosts: both only order records the leg
    already ranks equal (§5.2)."""
    query_lang = (query_lang or "").lower() or None
    # uid -> leg -> (its tie key: rank first, its best chunk)
    best: dict[str, dict[str, tuple[tuple, dict]]] = {}
    sources: dict[str, set[str]] = {}
    # uid -> chunk id -> (its best position in any list, the hit): the
    # record's MATCHED chunks, which the reranker judges (05 §5.6)
    matched: dict[str, dict[str, tuple[int, dict]]] = {}
    for li, hits in enumerate(lists):
        seen: set[str] = set()
        for i, h in enumerate(hits):
            uid = record_of(h)
            mc = matched.setdefault(uid, {})
            if h["chunk_id"] not in mc or i < mc[h["chunk_id"]][0]:
                mc[h["chunk_id"]] = (i, h)
            if uid in seen:
                continue                  # a later chunk of a record this list already ranked
            seen.add(uid)
            rank = len(seen)
            # 05 §5.1 (amended 2026-10-20): the catalogue is an index of its
            # own — titles, creators, subjects — and so a leg of its own: as
            # a variant of the text leg its rank 4 sat behind seventeen
            # language groups' ranks 1–3 ("War and Peace": fused rank 113)
            leg = ("vector" if h["source"] == "dense"
                   else "catalog" if h["source"] == CATALOG_SOURCE else "text")
            key = (rank, li not in own,
                   query_lang is None or (h.get("lang") or "").lower() != query_lang)
            mine = best.setdefault(uid, {})
            if leg not in mine or key < mine[leg][0]:
                mine[leg] = (key, h)
            sources.setdefault(uid, set()).add(h["source"])
    scores: dict[str, float] = {}
    rank_in: dict[str, dict[str, int]] = {}    # uid -> leg -> its rank in that ONE leg
    for leg in _LEGS:
        ordered = sorted((legs[leg][0], uid) for uid, legs in best.items() if leg in legs)
        for rank, (_, uid) in enumerate(ordered if depth is None else ordered[:depth], 1):
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (RRF_K + rank)
            rank_in.setdefault(uid, {})[leg] = rank
    merged = []
    for uid, score in scores.items():
        ranks = rank_in[uid]
        top = min(ranks, key=lambda leg: (ranks[leg], _LEGS.index(leg)))
        entry = {**best[uid][top][1], "rrf": score, "sources": sorted(sources[uid]),
                 "leg_ranks": {leg: ranks[leg] for leg in _LEGS if leg in ranks}}
        entry[MATCHED] = _matched_chunks(best[uid][top][1], matched[uid])
        merged.append(entry)
    merged.sort(key=lambda m: (-m["rrf"], record_of(m)))
    return merged


#: 05 §5.6 (amended 2026-10-10): how many of a record's matched chunks the
#: reranker judges — its best chunk and the next best-placed — the record
#: taking the best score. Measured live (79 queries, docs/notes/
#: one-language-per-work.md): with the record's title over each, title
#: queries MRR 0.635 (one chunk) → 0.660 (two), descriptive 0.169 → 0.223;
#: three chunks scored no better (0.629, 0.238) and send ~25 % more text.
JUDGED_CHUNKS = 2
#: the fused entry's key holding those chunks; never leaves the search
MATCHED = "_matched"
#: the fused entry's key holding its record's title and authors, which the
#: reranker reads above each chunk (05 §5.6); never leaves the search
HEAD = "_head"
#: at most this many characters of title and authors above a chunk: a
#: 512-token reranker reads the head first, and a paper with forty authors
#: must not spend the chunk's tokens on names
HEAD_CHARS = 300


def _attach_heads(con, collection, hits: list[dict]) -> None:
    """Each hit's record title and authors as one line, `title — authors`,
    under `HEAD`: what the reranker reads above the chunk, so that a byline
    or a heading is judged as part of its book (05 §5.6, amended
    2026-10-10). One indexed manifest query over the candidates — the shape
    `_attach_metadata_and_enqueue` runs on the final list. A manifest that
    cannot answer leaves the chunks bare: the rerank goes on as before."""
    ids = sorted({str(h["book_id"]) for h in hits if h.get("book_id") is not None})
    if not ids or con is None:
        return
    marks = ",".join("?" for _ in ids)
    try:
        rows = con.execute(f"SELECT native_id,title,authors FROM documents "
                           f"WHERE source=? AND native_id IN ({marks})",
                           [collection.plugin] + ids).fetchall()
    except Exception:   # noqa: BLE001 — no heads is the old, bare rerank
        return
    line = {str(r["native_id"]): " — ".join(x for x in (r["title"], r["authors"]) if x)
            for r in rows}
    for h in hits:
        got = line.get(str(h.get("book_id")))
        if got:
            h[HEAD] = got[:HEAD_CHARS]


def _matched_chunks(best: dict, chunks: dict[str, tuple[int, dict]]) -> list[dict]:
    """The record's chunks the legs matched, its best chunk first, then by
    their best position in any list (ties by chunk id), `JUDGED_CHUNKS` of
    them."""
    rest = sorted((pos, cid) for cid, (pos, _) in chunks.items() if cid != best["chunk_id"])
    out = [best] + [chunks[cid][1] for _, cid in rest]
    return out[:JUDGED_CHUNKS]

#: 05 §5.6: the reranker re-orders the top 50 of ONE ranked list — the
#: default of a per-request `rerank_depth` (operator, 2026-09-18: "set the
#: maximum number of results a query returns before and after reranking,
#: with as default the recommended amount").
RERANK_TOP_N = 50
#: 05 §5.4: `limit`, the results per list AFTER the reranker — 10 by default
DEFAULT_RESULTS = 10
#: 05 §5.4 caps `limit` — the results per list — at 100
MAX_RESULTS = 100
#: 05 §5.4 caps the rerank depth at 300 (operator, 2026-09-21: "I cannot
#: increment the rerank from above 100"; then "up to 300 per leg"). It used to
#: share MAX_RESULTS, only because a leg was said to contribute its top 100
#: records — which the code never did: a leg goes to max(limit, depth, 20).
MAX_RERANK_DEPTH = 300


def _rerank_budget_s(depth: int = RERANK_TOP_N) -> float:
    """The node's `[ask] latency_budget_ms` (07 §7.8), in seconds, for a list
    reranked `depth` deep: the budget is set for the default depth of 50 and
    given to a deeper list IN PROPORTION — 300 candidates have six times as
    long (05 §5.6; operator, 2026-09-21: "scale the timeout proportionally").
    Never less than the budget itself: a shallower list keeps it, since a
    reranker's cost is not all per candidate and a squeezed budget would only
    turn more reranks into the fused order."""
    from . import platform_config
    try:
        base = platform_config.load().ask.latency_budget_ms / 1000.0
    except Exception:   # noqa: BLE001 — a broken platform.toml must not break search
        base = platform_config.AskConfig().latency_budget_ms / 1000.0
    return base * max(1.0, depth / RERANK_TOP_N)


#: 05 §5.6, a candidate under measurement (2026-10-20, query campaign run 3):
#: False — the reranker's order replaces the fused one (as specified); True —
#: the reranker is ONE MORE VOTE, its order fused with the fused order by
#: reciprocal rank (`_rerank_order`). Titles fell from MRR 0.738 before the
#: reranker to 0.579 after it once the instructed vector leg brought books
#: ABOUT a work among the 50 ("Moby Dick": a Melville biography first). Not
#: switched until München's post-rerank measurement of titles AND
#: descriptive decides.
RERANK_FUSE = False


#: 05 §5.6 (amended 2026-10-21; query campaign run 4): after the reranker, the
#: record the CATALOGUE leg ranks first — whose title, creators or subjects
#: hold every word of the query best — keeps the first place. The reranker
#: judges passages, and a biography's passage that names "Moby Dick" beats
#: the novel's own pages. Measured post-rerank on the API's path with a
#: bit-exact reranker, 40 labelled titles (tools/measure/rerank_variants.py):
#: MRR 0.578 → 0.782, R@1 0.425 → 0.700, R@10 0.925 → 0.975. RERANK_FUSE gave
#: 0.720 / 0.575 / 1.000 and missed the operator's bar (MRR 0.738). A query
#: with no catalogue hit — every descriptive one measured — is untouched.
CATALOG_FIRST = True

#: How records in DIFFERENT languages are ordered after each was judged
#: against the question in its own language (05 §5.6, amended 2026-10-23):
#: "score" — by the reranker's score alone; "slots" — the reranker orders
#: each language's records among themselves, and each language keeps the
#: positions the fused order gave it, so two languages' scores are never
#: compared. Measured first on six it/en pairs (München, 2026-10-23):
#: "score" took MRR 0.119 → 0.444 but showed an Italian query 9–10 of 10
#: non-Italian records and lost La vita nuova — an English pair scores
#: systematically higher than an Italian one on bge-reranker-v2-m3. Not
#: switched until campaign run 5 measures both.
#: THE STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"): score
#: CALIBRATION — Platt scaling or isotonic regression fitted per language
#: pair on labelled queries maps every pair's scores onto one probability
#: scale, so records of all languages are ordered by one number. It needs
#: labelled queries per language (the campaign's are mostly English); it
#: pays once a few dozen per language exist.
RERANK_LANGUAGE_ORDER = "score"


def _catalog_first(order: list[int], head: list[dict]) -> list[int]:
    """`order` with the catalogue leg's rank-1 record of `head` moved first."""
    first = next((i for i in order if (head[i].get("leg_ranks") or {}).get(CATALOG_SOURCE) == 1), None)
    return order if first is None else [first] + [i for i in order if i != first]


def _rerank_order(scores: list[float], *, fuse: bool) -> list[int]:
    """The order of `scores`' candidates (each index its position in the
    fused order): by the reranker's score, best first — or, `fuse`, by
    1/(K + fused rank) + 1/(K + reranker rank), ties by the reranker's rank
    then the fused one (RRF_K, as `_merge_rrf`)."""
    by_score = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    if not fuse:
        return by_score
    rerank_rank = {i: r for r, i in enumerate(by_score, 1)}
    return sorted(range(len(scores)), key=lambda i: (-(1.0 / (RRF_K + i + 1) + 1.0 / (RRF_K + rerank_rank[i])),
                                                     rerank_rank[i], i))


def _by_language_slots(head: list[dict], scores: list[float]) -> list[int]:
    """RERANK_LANGUAGE_ORDER "slots": each language's records in the
    reranker's order, each in the positions its language held in the fused
    order — scores of two languages are never compared."""
    def lang(i: int) -> str:
        return (head[i].get("lang") or "").lower()
    queues: dict[str, list[int]] = {}
    for i in range(len(head)):
        queues.setdefault(lang(i), []).append(i)
    for q in queues.values():
        q.sort(key=lambda i: (-scores[i], i))
    return [queues[lang(p)].pop(0) for p in range(len(head))]


def _rerank_list(query: str, hits: list[dict], recipe, budget_s: float,
                 depth: int = RERANK_TOP_N,
                 queries: dict[str, str] | None = None) -> tuple[list[dict], bool]:
    """05 §5.6 for ONE ranked list: the reranker re-orders its top `depth`
    (50 unless the request says otherwise) and the rest keep the fused
    order below them. Each candidate goes whole —
    its chunk's text; this used to cut every one to 2,000 characters,
    so a long chunk was judged on a prefix the model never saw alone. A
    512-token model's own server truncates at its limit (TEI's `truncate`);
    the platform does not. Unreachable, refusing, over the node's budget or
    answering for only some candidates: the fused order stands and the
    second value says False — the fallback is visible, never silent, and
    its reason is noted for `_reranked` (amended 2026-10-10: 4 of 980 live
    fallbacks said nothing).

    THE RECORD IS JUDGED, NOT ONE CHUNK OF IT (amended 2026-10-10, 05
    §5.6). Each record sends its `JUDGED_CHUNKS` best matched chunks
    (`_merge_rrf`'s `MATCHED`), each under the record's own title and
    authors (`HEAD`, `_attach_heads`), and takes the best score; the
    result carries the chunk that scored it. The reranker read only the
    leg's best chunk, bare, and a byline ("by William Shakespeare", 22
    characters) or a heading won it: measured live on 39 title queries,
    MRR 0.342 → 0.660 and R@10 0.718 → 0.872 (26 better, 0 worse), and
    on 40 descriptive ones MRR 0.121 → 0.223
    (docs/notes/one-language-per-work.md). The budget grows with the
    texts sent, as it grows with the depth.

    THE BUDGET IS THE RERANK'S, NOT THE MODEL'S LOAD (amended 2026-10-09,
    05 §5.6): a reranker its runner reports as not loaded is warmed first
    under its LOAD bound (app/model_load.py), and the budget starts after.
    A load past that bound is the fused order, its reason among
    the reasons `_reranked` collects ("model loading took longer than N s").

    EACH CANDIDATE AGAINST THE QUERY IN ITS OWN LANGUAGE (2026-10-23,
    operator: "I see results returned only in the language of the query").
    `queries` is the search's translations, by language (the language plan
    made them already: no translation is asked here); a candidate in one of
    those languages is scored against that variant, any other against the
    query as typed — one reranker call per variant, sharing the budget.
    Measured on the node: "la balena bianca e l'ossessione del capitano"
    against Moby-Dick's English passage scored -5.298, LAST of five, and
    "Al polo australe in velocipede" (Italian) 0.817 first; against the
    query's English translation Moby-Dick scored +1.204, first. A cross-
    language pair scores far below a same-language one on bge-reranker-v2-m3,
    so the reranker demoted every hit not in the query's language.

    ONE LIST, ONE STEP, ONE CLAIM (2026-11-07). The warm, the cut and every
    variant's call hold the node's claim from the first to the last, at
    RERANK; each call's own `dispatch.call` nests inside it. Claimed per
    call, the claim cleared in the 9–37 ms between two variants (scheduling-
    log 2026-10-25), and a corpus request standing aside could go out there
    and share the card with the next variant. The gaps between a query's
    STEPS stay corpus's to fill (operator, 2026-10-25); a list is one step."""
    if recipe is None or not hits:
        return hits, False
    with dispatch.interactive(dispatch.Level.RERANK):
        return _rerank_held(query, hits, recipe, budget_s, depth, queries)


#: 05 §5.6 (amended 2026-11-18): the reranker reads a COPY of a record's
#: chunks with its period's old words read (app/reading.py) where the record
#: states a year a reading holds. Operator, 2026-11-18: "Yes to both,
#: replacing the words the reranker does not know with the words it knows,
#: just enables it to do its job" — on by default; a reorder under invariant
#: 4's WEIGHTS rule, so every hit it moved says so and by how much
#: (`moved_by`: its place had the original been read, and its place now).
#: False only for the measurement of it (tools/measure, from the command line).
#: Under QUALITY only (operator, 2026-11-18: "Keep the word replacement under
#: quality search only"): the request's choice, `reading.quality`.
READING_RERANK = True


#: the last time a reading failure was logged, by collection: at most one a minute each
_reading_logged: dict[str, float] = {}
READING_LOG_EVERY_S = 60.0


def _reading_failed(collection: str, e: BaseException) -> None:
    """The reading's fallback, said: at most once a minute per collection,
    naming it (every log line names its collection, CLAUDE.md)."""
    now = time.monotonic()
    if now - _reading_logged.get(collection, float("-inf")) < READING_LOG_EVERY_S:
        return
    _reading_logged[collection] = now
    print(f"{time.strftime('%H:%M:%S')} [{collection} warn:reading] the reranker reads the texts as "
          f"they were: {type(e).__name__}: {e}", flush=True)


def _reading_copies(head: list[dict], owner: list[tuple[int, dict]], texts: list[str]
                    ) -> tuple[dict[int, str], str, dict[int, int]]:
    """{text index: its ORIGINAL text} for the texts the reader changed (each
    changed in `texts` in place), and the reading's version. Nothing on any
    failure: the rerank reads the texts as they were."""
    from . import reading
    if not READING_RERANK or not reading.quality_on():
        return {}, "", {}                       # under Speed: no year, no lookup, no copy (operator)
    try:
        years = reading.years(head)
        if not years:
            return {}, "", {}
        chunks = [(t, i, c) for t, (i, c) in enumerate(owner) if i in years]
        found = reading.lookup(reading.words_of(c.get("text") or "" for _t, _i, c in chunks))
    except Exception as e:   # noqa: BLE001 — a reading is a help, never the search's failure
        _reading_failed(reading.collection_of(head[0]) if head else "?", e)
        return {}, "", {}
    originals: dict[int, str] = {}
    for t, i, c in chunks:
        body = c.get("text") or ""
        lang = str(head[i].get("lang") or "").lower()[:2]
        new, used = reading.apply(body, years[i], found, reading.RERANK, lang=lang)
        if used and new != body:
            originals[t] = texts[t]
            title = head[i].get(HEAD) or ""
            texts[t] = f"{title}\n\n{new}" if title else new
    return originals, found.version, years


#: WHAT ONE LIST'S RERANK WAS MADE OF, for the search's own timings
#: (`_rerank_parts`; München, 2026-11-25: a question's "rerank 20,000" in
#: query mode, and nothing said of how many lists, how many texts or which
#: part). Set by the caller that wants it; `_rerank_held` fills it and
#: changes nothing of what it does.
_rerank_note: contextvars.ContextVar[dict | None] = \
    contextvars.ContextVar("retrieval_rerank_note", default=None)


def _note_rerank(**parts) -> None:
    note = _rerank_note.get()
    if note is not None:
        for k, v in parts.items():
            note[k] = round(note.get(k, 0) + v, 1) if isinstance(v, float) else note.get(k, 0) + v


def _rerank_held(query: str, hits: list[dict], recipe, budget_s: float, depth: int,
                 queries: dict[str, str] | None) -> tuple[list[dict], bool]:
    """`_rerank_list`'s work, inside the list's one claim."""
    head, tail = hits[:depth], hits[depth:]
    texts: list[str] = []
    owner: list[tuple[int, dict]] = []        # text index -> (its record's index, the chunk)
    for i, h in enumerate(head):
        title = h.get(HEAD) or ""
        for c in (h.get(MATCHED) or [h])[:JUDGED_CHUNKS]:
            body_text = c.get("text") or ""
            texts.append(f"{title}\n\n{body_text}" if title else body_text)
            owner.append((i, c))
    # THE READER'S COPY, and the ORIGINALS of the texts it changed, scored
    # too (the ruling: "score the ORIGINAL copy too, but only for the hits
    # whose copy changed"): appended after the copies, so the delta of each
    # moved hit is measured in the same call
    originals, reading_version, reading_years = _reading_copies(head, owner, texts)
    original_of: dict[int, int] = {}          # the original's text index -> its copy's
    for t, text in originals.items():
        original_of[len(texts)] = t
        texts.append(text)
        owner.append(owner[t])
    budget_s = budget_s * max(1.0, len(texts) / max(1, len(head)))
    # WARMED BEFORE THE CUT: the cut asks the reranker's own tokenizer, and a
    # cold one took that request as its load — 10 s timeout, the text cut by
    # bytes instead of tokens, a second load started by the next text, and
    # no cold start logged (München, 2026-10-27 15:42)
    t_part = time.monotonic()
    try:
        _warm_reranker(recipe)
    except model_load.LoadTimeout as e:
        return _fused_order(hits, str(e))
    finally:
        _note_rerank(warm_ms=(time.monotonic() - t_part) * 1000)
    t_part = time.monotonic()
    texts = fit_to_window(texts, recipe)
    _note_rerank(records=len(head), fit_ms=(time.monotonic() - t_part) * 1000)
    # the query each text is judged against: its record's language's variant
    variants = {k.lower(): v for k, v in (queries or {}).items() if v}
    groups: dict[str, list[int]] = {}
    for t, (i, _c) in enumerate(owner):
        lang = (head[i].get("lang") or "").lower()
        groups.setdefault(variants.get(lang) or query, []).append(t)
    deadline = time.monotonic() + budget_s
    scores: dict[int, float] = {}
    for q, idx in groups.items():
        # the first call has the whole budget, as the one call always had;
        # each further variant what remains of it
        remaining = budget_s if not scores else deadline - time.monotonic()
        if remaining <= 0:
            return _fused_order(hits, f"the rerank took longer than its {budget_s:.1f} s budget")
        part = [texts[t] for t in idx]
        # A TEXT IS SCORED ONCE PER CALL (2026-11-03: the rerank was 6.2 s of
        # a 7.0 s search on Ask's benchmark). Two records can send the same
        # text against the same query — the editions of a work under one
        # title, a chunk two legs matched — and a pair's score is a pure
        # function of the pair: the reranker runs one sequence at a time
        # (`parallel:1`, the wrapper's contract) and was measured bit-exact
        # with any batch-mates and in any order (approved-encoders.md,
        # louise-rerank-bge 2026-10-31). So the duplicate is not sent and
        # takes the score its twin got: the same order, by construction.
        distinct = list(dict.fromkeys(part))
        if recipe.api == "jina":
            body = {"query": q, "documents": distinct, "top_n": len(distinct)}
        else:
            body = {"query": q, "texts": distinct, "truncate": True}
        if recipe.model:
            # the file's `rerank_model` (07 §7.8, amended 2026-10-08): a runner
            # that picks the model from the body (LocalAI) is told which; only
            # RERANK_URL names none
            body["model"] = recipe.model
        # the backend serving the reranker now: another pid after a timed-out
        # request means it was evicted and loaded again in between, a cold
        # start the budget paid (model_load; the cached check missed it)
        pid = model_load.backend_pid(recipe.url, recipe.model)
        sent = time.monotonic()
        _note_rerank(calls=1, texts=len(distinct), chars=sum(len(x) for x in distinct))
        try:
            try:
                data = _rerank_call(recipe, body, remaining, deadline)
            finally:
                _note_rerank(call_ms=(time.monotonic() - sent) * 1000)
            rows = data["results"] if isinstance(data, dict) else data
            got = {int(o["index"]): float(o["relevance_score"] if "relevance_score" in o
                                          else o["score"]) for o in rows}
        except Exception as e:   # noqa: BLE001 — any failure is the fused order, said so
            return _fused_order(hits, _cold_or(e, recipe, pid, sent, budget_s))
        if sorted(got) != list(range(len(distinct))):
            return _fused_order(hits, f"the reranker answered for "
                                      f"{sum(1 for i in got if 0 <= i < len(distinct))} of "
                                      f"{len(distinct)} candidates")
        at = {text: k for k, text in enumerate(distinct)}
        for local, t in enumerate(idx):
            scores[t] = got[at[part[local]]]
    best: dict[int, tuple[float, dict]] = {}
    as_was: dict[int, float] = {}             # each record's best score had nothing been read
    for t, (i, c) in enumerate(owner):
        if t in original_of:
            as_was[i] = max(as_was.get(i, float("-inf")), scores[t])
            continue
        if i not in best or scores[t] > best[i][0]:
            best[i] = (scores[t], c)
        if t not in originals:
            as_was[i] = max(as_was.get(i, float("-inf")), scores[t])
    ranked = []

    def _ordered(score_of: dict[int, float]) -> list[int]:
        if RERANK_LANGUAGE_ORDER == "slots" and len(groups) > 1 and set(score_of) == set(range(len(head))):
            return _by_language_slots(head, [score_of[i] for i in range(len(head))])
        return _rerank_order([score_of[i] for i in range(len(head))], fuse=RERANK_FUSE) \
            if set(score_of) == set(range(len(head))) else sorted(score_of, key=lambda i: (-score_of[i], i))
    order = _ordered({i: b[0] for i, b in best.items()})
    moved: dict[int, list[dict]] = {}
    if originals:
        # CLAUDE.md invariant 4 (2), WEIGHTS (amended 2026-11-18): "every
        # result it moved" — every record whose place differs from its place
        # had nothing been read, `read` saying whether its own copy changed
        # (False: it moved because another record was read)
        before = _ordered(as_was)
        from . import reading
        changed = {owner[t][0] for t in originals}
        at_before = {r: k for k, r in enumerate(before)}
        for k, i in enumerate(order):
            if i in at_before and at_before[i] != k:
                entry = {"weight": reading.label(reading_version), "from": at_before[i] + 1, "to": k + 1,
                         "read": i in changed,
                         # whom the readings are from (operator, 2026-11-18: "add the usual credits")
                         "credits": reading.credits(reading.sources_in(reading_version))}
                if i in changed and getattr(reading_years.get(i), "by", ""):
                    entry["by"] = reading_years[i].by        # what dated it: shown as the badge shows it
                moved.setdefault(i, []).append(entry)
    if CATALOG_FIRST:
        before = order
        order = _catalog_first(order, head)
        if order and before and order[0] != before[0]:
            # CLAUDE.md invariant 4, rule 2: a weight's result says what moved
            # it and how far — here from the reranker's place to the first
            moved.setdefault(order[0], []).append({"weight": "catalog_first",
                                                   "from": before.index(order[0]) + 1, "to": 1})
    for i in order:
        score, c = best[i]
        h = dict(head[i])
        if c.get("chunk_id") != h.get("chunk_id"):
            # the chunk that scored the record is the one the result shows
            h.update({f: c[f] for f in ("chunk_id", "text", "chapter") if f in c})
        h["rerank_score"] = score
        if i in moved:
            h["moved_by"] = moved[i]
        ranked.append(h)
    return ranked + tail, True

#: A token covers at least this many bytes of UTF-8, in every script the
#: reranker's tokenizer (XLM-R's SentencePiece) reads: a text of at most
#: window * WINDOW_BYTES_PER_TOKEN bytes fits the window uncounted.
WINDOW_BYTES_PER_TOKEN = 2
#: how many times a long candidate is cut and counted again before the byte
#: cut stands in
WINDOW_TRIES = 3


def _tokens_url(recipe) -> str:
    """LocalAI's tokenizer beside its /v1/rerank (the same model's tokens)."""
    return recipe.url.rsplit("/v1/rerank", 1)[0] + "/v1/tokenize"


def fit_to_window(texts: list[str], recipe) -> list[str]:
    """Each candidate cut to the reranker's `window` of ITS tokens (05 §5.6,
    amended 2026-10-21), so the model's context can be the window. A text
    short enough in bytes goes uncounted; a longer one is counted by the
    model's own tokenizer and cut in proportion until it fits — the same
    text always the same cut. Where the count cannot be had (no window, a
    TEI endpoint, which truncates itself, or a failed call), a text over
    `window * WINDOW_BYTES_PER_TOKEN` bytes is cut to that, which fits."""
    window = getattr(recipe, "window", 0) or 0
    if not window or recipe.api != "jina":
        return texts
    limit = window * WINDOW_BYTES_PER_TOKEN
    out = []
    for t in texts:
        if len(t.encode("utf-8")) <= limit:
            out.append(t)
            continue
        out.append(_cut_by_tokens(t, window, recipe) or _cut_bytes(t, limit))
    return out


def _cut_bytes(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _cut_by_tokens(text: str, window: int, recipe) -> str | None:
    cut = text
    for _ in range(WINDOW_TRIES):
        try:
            r = httpc.post(_tokens_url(recipe), json={"model": recipe.model, "content": cut}, timeout=10)
            r.raise_for_status()
            n = len(r.json()["tokens"])
        except Exception:   # noqa: BLE001 — the byte cut stands in
            return None
        if n <= window:
            return cut
        cut = cut[:max(1, int(len(cut) * window / n * 0.95))]
    return None


#: Where `_rerank_list` says WHY a list kept its fused order, when it knows:
#: set by `_reranked` around one call (a context variable, so a test's
#: stand-in for `_rerank_list` keeps the arguments it always took).
_rerank_why: contextvars.ContextVar[list[str] | None] = \
    contextvars.ContextVar("louise_rerank_why", default=None)


def _fused_order(hits: list[dict], reason: str) -> tuple[list[dict], bool]:
    """The fused order, its reason noted where `_reranked` collects it."""
    why = _rerank_why.get()
    if why is not None:
        why.append(reason)
    return hits, False


def rerank_failure(e: BaseException, budget_s: float) -> str:
    """Why a rerank request failed, in a reader's words (05 §5.6: every
    fallback carries its reason — measured 2026-10-10, 4 of 980 live
    searches kept the fused order and none said why): over the budget,
    refused with a status, unreachable, or an answer that could not be
    read. Never the request's body or headers."""
    if isinstance(e, dispatch.GateTimeout):
        return f"the reranker was busy with other work for the whole {budget_s:g} s budget"
    if isinstance(e, httpx.TimeoutException) and not isinstance(e, httpx.ConnectTimeout):
        return f"the reranker did not answer within {budget_s:g} s"
    if isinstance(e, httpx.HTTPStatusError):
        said = _server_said(e.response)
        return (f"the reranker refused the request (HTTP {e.response.status_code}"
                + (f": {said})" if said else ")")
                + (", and again when asked once more" if getattr(e, "retried", False) else ""))
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "the reranker could not be reached"
    if isinstance(e, (KeyError, IndexError, TypeError, ValueError)):
        return f"the reranker's answer could not be read ({type(e).__name__})"
    return f"the reranker failed ({type(e).__name__})"


#: a server error (5xx) is asked again ONCE, after this pause, when the
#: budget still has room: the rerank is a pure function of its body, and
#: under load the reranker refuses transiently (2026-10-25: 130 of 5,792
#: night calls answered 500, scattered — 1 in 21 within one minute — and
#: none by day). One refused call of a query's 4-6 used to cost the whole
#: list its rerank. The standard client policy (urllib3's `Retry` with a
#: status list, gRPC's retry on UNAVAILABLE), kept to one try because the
#: budget is a person's wait; a library is not adopted for one call site.
RERANK_RETRY_PAUSE_S = 0.25


def _rerank_call(recipe, body: dict, timeout: float, deadline: float) -> dict:
    """One rerank request, through the gate: its JSON answer. A 5xx is
    asked again once when at least the pause and a second remain before
    `deadline`; anything else raises as it came."""
    for attempt in (1, 2):
        with dispatch.call(recipe.url, dispatch.Level.RERANK, timeout=timeout):
            r = httpc.post(recipe.url, json=body, timeout=timeout,
                           headers=dispatch.level_headers())
        if r.status_code >= 500 and attempt == 1:
            left = deadline - time.monotonic() - RERANK_RETRY_PAUSE_S
            if left >= 1.0:
                time.sleep(RERANK_RETRY_PAUSE_S)
                timeout = left
                continue
        try:
            _raise_for_status(r)              # the server's reason with the status
        except httpx.HTTPStatusError as e:
            e.retried = attempt == 2          # what the reason says
            raise
        from . import model_use
        model_use.touch(recipe.url, recipe.model)        # a real rerank (app/model_use.py)
        return r.json()
    raise AssertionError("unreachable")


def _server_said(response) -> str:
    """The error a model server put in its answer, short: LocalAI's
    `{"error": {"message": …}}`, else the start of the text. Never the
    request's body or headers."""
    try:
        got = response.json()
        msg = (got.get("error") or {}).get("message") if isinstance(got, dict) else None
        if not msg and isinstance(got, dict):
            msg = got.get("error") if isinstance(got.get("error"), str) else got.get("detail")
    except Exception:   # noqa: BLE001 — not JSON: its text
        msg = None
    text = str(msg or getattr(response, "text", "") or "").strip().replace("\n", " ")
    return text[:160]


def _cold_or(e: BaseException, recipe, pid: int | None, sent: float, budget_s: float) -> str:
    """A failed rerank request's reason: a timeout whose model is served by
    another backend than when the request went was a COLD START inside the
    budget (operator, 2026-10-09: "Timeouts shall not include cold start"),
    recorded as one and said so; anything else is `rerank_failure`."""
    if isinstance(e, httpx.TimeoutException) and not isinstance(e, httpx.ConnectTimeout) \
            and model_load.reloaded_since(recipe.url, recipe.model, pid):
        model_load.note_cold_call(recipe.url, recipe.model, "rerank", time.monotonic() - sent)
        return (f"the reranker was evicted before the request and loaded again inside its "
                f"{budget_s:g} s budget")
    return rerank_failure(e, budget_s)


def _reranked(query: str, hits: list[dict], recipe, budget_s: float,
              depth: int, queries: dict[str, str] | None = None
              ) -> tuple[list[dict], bool, str | None]:
    """`_rerank_list`, and the reason its list kept the fused order when one
    is known (05 §5.6: the fallback is visible, never silent)."""
    why: list[str] = []
    token = _rerank_why.set(why)
    try:
        ranked, ok = _rerank_list(query, hits, recipe, budget_s, depth,
                                  **({"queries": queries} if queries else {}))
    finally:
        _rerank_why.reset(token)
    return ranked, ok, (why[0] if why and not ok else None)


def _rerank_parts(run) -> tuple[Any, dict]:
    """`run()` — one list's rerank and the grouping of its works — with what
    it was made of: {"rerank": the whole of it in ms, and from
    `_rerank_held`: "records" judged, "calls" (one per query variant),
    "texts" and "chars" sent, "warm_ms" (0 where the model was loaded),
    "fit_ms" (the texts cut to the model's window by its own tokenizer),
    "call_ms" (the reranker's answers), and "works_ms" where the caller
    notes it, with its parts (`_note_works`)}. Nothing of the search changes."""
    note: dict = {}
    token = _rerank_note.set(note)
    t0 = time.monotonic()
    try:
        got = run()
    finally:
        _rerank_note.reset(token)
    note["rerank"] = round((time.monotonic() - t0) * 1000, 1)
    return got, note


def _rerank_withheld(collection, reason: str) -> dict:
    """05 §5.6's reason beside `reranked: false`, for one list."""
    return {"collection": collection.name, "reason": reason}


def _warm_reranker(recipe) -> None:
    """One minimal rerank, in the list's own body shape, when the runner
    says the reranker is not loaded — bounded by its load bound, outside the
    rerank budget (app/model_load.py)."""
    if not recipe.model:
        return
    if recipe.api == "jina":
        body: dict = {"query": "warm", "documents": ["warm"], "top_n": 1}
    else:
        body = {"query": "warm", "texts": ["warm"], "truncate": True}
    body["model"] = recipe.model

    def warm(bound_s: float):
        with dispatch.interactive(dispatch.Level.RERANK):
            return httpc.post(recipe.url, json=body, timeout=bound_s,
                              headers=dispatch.level_headers())

    model_load.ensure_loaded(recipe.url, recipe.model, warm, caller="rerank", max_age_s=0.0)


#: docs/05's one hop, bounded: at most this many facts per declared link from
#: each entity the query names. A person reads pinned facts as cards above
#: the results; 25 is already more than a screen of them, and BRCA2 alone
#: reaches 21,961 ClinVar variants (2026-09-21). The rest are counted in the
#: answer's `facts_withheld`, and the traversal tool (docs/05 §5.4) is how
#: they are reached.
FACTS_PER_LINK = 25


def _entity_facts(query: str, store) -> tuple[list, list["PinnedFact"], list[dict]]:
    """resolve, then docs/05's expand — one hop along each declared link,
    bounded — then the facts. Returns the entities the query NAMES (the
    legs', and only those: the 21,968 variants BRCA2 reaches tag not one
    PubMed document the gene does not), the pinned facts, and what each link
    reached beyond its bound: named in the answer, never dropped in silence
    (docs/05). A fact expand reached names the link (`PinnedFact.via`)."""
    resolved = refs.RESOLVER.resolve(query, store)
    reached = [(r, link) for r in resolved
               for link in refs.RESOLVER.expand_bounded(r, store, FACTS_PER_LINK)]
    facts = _build_pinned_facts(
        [(e, None) for e in resolved]
        + [(e, _via(r, link)) for r, link in reached for e in link.entities], store)
    withheld = [{"entity_id": r.entity_id, "table": link.table,
                 "reached": link.total, "pinned": len(link.entities)}
                for r, link in reached if link.total > len(link.entities)]
    return resolved, facts, withheld


def _article_facts(query: str, collections, facts, as_of, store=None,
                   query_lang: str | None = None) -> list[dict]:
    """The cited articles' texts, pinned by key beside the act (app/ask/
    articles.py; operator 2026-10-13: "art. 12 della legge … n. 2205" ranked
    other acts' articles above its own and never read it). Facts, never
    hits (invariant 4); never fatal to the search. An act held in several
    languages is pinned once, in `query_lang` where held (the plan's)."""
    from .ask import articles
    try:
        return articles.pin(query, collections, facts, as_of=as_of, store=store,
                            query_lang=query_lang)
    except Exception:                       # noqa: BLE001 — the search answers without it
        return []


def _via(named, reached) -> dict:
    """The link a fact was reached through (docs/05 §5.4), from the entity
    the query named."""
    link = reached.link
    return {"from": named.entity_id, "link": link.name,
            "on": f"{link.from_table}.{link.from_column} → {link.to_table}.{link.to_column}"}


def _build_pinned_facts(entities_found, store) -> list[PinnedFact]:
    """`entities_found`: (entity, via) pairs, the named ones first — an
    entity both named and reached is a fact once, and the query named it."""
    facts: list[PinnedFact] = []
    seen: set[str] = set()
    for e, via in entities_found:
        if e.entity_id in seen:
            continue
        seen.add(e.entity_id)
        row = row_of(store, e) or {}
        facts.append(PinnedFact(entity_id=e.entity_id, kind=e.kind, source_id=e.source_id,
                                 label=e.label, fields=row, via=via))
    return facts

def _enqueue_lazy_embed(con, book_ids: list[int], collection):
    now = int(time.time())
    for bid in book_ids:
        uid = f"{collection.plugin}:{bid}"
        row = con.execute(
            "SELECT embed_done FROM documents WHERE uid=? AND in_scope=1", (uid,)).fetchone()
        # 0 only: 1 is embedded, and -1 is not embedded here by choice
        # (07 §7.8 `embed_slices`), a search included
        if row and row["embed_done"] == 0:
            con.execute(
                "INSERT INTO embed_queue(uid,priority,ts) VALUES(?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET priority=100, ts=excluded.ts",
                (uid, 100, now))

def _best_rank(lists: list[list[dict]], limit: int) -> list[dict]:
    """Several variants of one query against one group, as ONE ranked list:
    a record's rank is its best across the variants (docs/05 §5.2 — a
    variant is a query, not a leg, so three CJK variants cast one vote).
    The search no longer calls it (2026-10-09): every variant's list goes
    to `_merge_rrf`, which ranks a record by its best across all of them."""
    if len(lists) == 1:
        return lists[0]
    best: dict[str, tuple[int, dict]] = {}
    for hits in lists:
        for rank, h in enumerate(hits):
            seen = best.get(h["chunk_id"])
            if seen is None or rank < seen[0]:
                best[h["chunk_id"]] = (rank, h)
    ordered = sorted(best.values(), key=lambda rh: (rh[0], rh[1]["chunk_id"]))
    return [h for _, h in ordered][:limit]


#: 05 §5.1 (amended 2026-10-20): the source of the catalogue leg — a
#: record's title, creators and subjects (app/catalog.py)
CATALOG_SOURCE = "catalog"


def limited_langs(collection, only) -> tuple[str, ...]:
    """The record languages a search limited to `only` keeps, for the legs
    that filter by the RECORD's language (catalogue, vectors). A
    multilingual (`mul`) record is written in its plugin's `languages`, one
    section each (wikidata: `("en",)`, a Codex's list for its life, 02 §2.7):
    it is kept when one of them is asked for. 2026-10-24: every "in English"
    search left wikidata's 75.9 M records out of these legs, their `lang`
    being `mul`. The text leg routes the sections themselves
    (fts.by_section_language). Unlimited: `()`, every language, as before."""
    only = tuple(only or ())
    if not only:
        return only
    if fts.MULTILINGUAL not in only and set(only) & set(fts.section_languages(collection)):
        return only + (fts.MULTILINGUAL,)
    return only
#: the legacy prompt's "[catalog matches]" block reads at most this many
CATALOG_PROMPT = 10
#: the catalogue variant's chunk lookup asks one text query per language
#: group for all its records at once, this many chunk hits per record deep
CATALOG_CHUNKS_PER_RECORD = 8


def _seq_of(chunk_id: str) -> int:
    try:
        return int(str(chunk_id).rsplit(":", 2)[1])
    except (IndexError, ValueError):
        return 1 << 30


def _catalog_variant(collection, hits: list[dict], query: str, restrict: dict) -> list[dict]:
    """05 §5.1 (amended 2026-10-20; query campaign, docs/notes/
    query-campaign.md): the catalogue's ranked records as the CATALOGUE
    LEG, in the catalogue's order, each carried by a chunk of its own — the
    one the query's words match best in that record, else its first. Until
    today the catalogue's hits reached only the legacy prompt: "War and
    Peace" missed the top 100 though 2600 is held and indexed, because a
    title reached a book only through its own text and anthologies quoting
    it won. First made a variant of the text leg; on the API's path (the
    query as typed to all seventeen language groups) its rank 4 sat behind
    seventeen groups' ranks 1–3, fused rank 113 (München, live) — the
    catalogue is another index, so another leg (invariant 4: fused by RRF,
    one vote per rank). A versioned
    collection's window takes no fallback chunk: a record whose matching
    chunk is outside it is left to the other lists.

    ONE text query per language group for all its records (`book_ids`),
    not one per record: per record it cost pubmed 160–758 ms a query, fifty
    lookups over 40 M chunks (München, 2026-10-20); one query costs it
    136–492 ms cold, 23–42 ms warm. The hits come sorted by score, then
    `fts.tie_key` — the record's uid, then the chunk's place in it — with a
    tie at the cut read whole (`fts.search`, 2026-10-20), so a record's
    first hit is its best-scoring chunk and, among chunks of EQUAL score,
    its earliest: the same chunk a query of its own returns, on every peer.
    (Before that fix 18 of 498 captured queries carried a record by another
    chunk of equal score — tantivy's pick among ties, and the id string's
    order: München.) A record absent from a FULL answer is asked alone; one
    absent from a short answer has no matching chunk."""
    groups = set(fts.existing_groups(collection))
    window = restrict.get("validity")
    kw = {"validity": window} if window else {}
    wanted: list[tuple[int, str]] = []
    by_group: dict[str, list[int]] = {}
    for h in hits:
        try:
            bid = int(h["book_id"])
        except (KeyError, TypeError, ValueError):
            continue
        group = fts.group_for(h.get("lang") or "")
        if group not in groups or bid in by_group.get(group, ()):
            continue
        wanted.append((bid, group))
        by_group.setdefault(group, []).append(bid)
    best: dict[tuple[int, str], dict | None] = {}
    for group, ids in by_group.items():
        limit = len(ids) * CATALOG_CHUNKS_PER_RECORD
        got = fts.search(group, query, limit=limit, collection=collection, book_ids=ids, **kw)
        for c in got:
            best.setdefault((int(c["book_id"]), group), c)
        if len(got) >= limit:
            # a full answer: a record crowded out by others' chunks is asked alone
            for bid in ids:
                if (bid, group) not in best:
                    one = fts.search(group, query, limit=1, collection=collection, book_ids=[bid], **kw)
                    if one:
                        best[(bid, group)] = one[0]
    out: list[dict] = []
    for bid, group in wanted:
        chunk = best.get((bid, group))
        if chunk is None and not window:
            chunks = fts.book_chunks(group, bid, collection=collection)
            chunk = min(chunks, key=lambda c: _seq_of(c["chunk_id"])) if chunks else None
        if chunk is not None:
            out.append({**chunk, "score": 0.0, "source": CATALOG_SOURCE})
    return out


#: the text leg's searches at once, per collection: its language groups and
#: their variants (wikipedia: 10 groups x 2 variants an Ask)
FTS_THREADS = max(2, min(16, os.cpu_count() or 4))


def _search_one_collection(con, collection, query: str, k: int, mode: str, translate: bool,
                            entity_ids: list[str], plan: LanguagePlan | None = None,
                            depth: int = 0, book_ids: set[int] | None = None,
                            as_of: int | None = None) -> dict:
    """catalog + fts fan-out + dense + intra-collection RRF —
    the per-collection core, extracted so search() (one collection) and
    federated_search() (N collections) share identical logic. `plan` is the
    search's language plan, made once for all its collections; without one
    this collection plans alone. Each leg goes at least `depth` deep, so a
    reranker asked for its top N has N candidates to order. `entity_ids`,
    the entities the query names, change no rank and restrict nothing
    (05 §5.2, amended 2026-10-25); the `keys` filter arrives as `book_ids`. Returns
    {"catalog", "merged", "translations", "timings", "scope"}."""
    timings: dict[str, float] = {}
    vector_leg: str | None = None      # why the vector leg was withheld, if it was
    scope = db.get_scope(con)
    if plan is None:
        plan = language_plan.plan(query, [collection], translate)
    core = plan.core

    # 05 §5.2: a facet filter RESTRICTS every leg to the records it admits —
    # a prefilter on both legs, never a boost; one admitting nothing returns
    # nothing, which is an answer
    if book_ids is not None and not book_ids:
        return {"catalog": [], "merged": [], "translations": plan.translations,
                "timings": timings, "scope": scope, "vector_leg": vector_leg, "filtered": 0}
    # the restriction reaches a leg only when there is one: an unfiltered
    # search calls the legs exactly as before
    restrict = {"book_ids": sorted(book_ids)} if book_ids else {}
    # 05 §5.1 (amended 2026-10-12), app/validity.py: a versioned collection's
    # legs see one version per Work — the latest text, or the version in
    # force as of `as_of` — by a column range; every other collection sees
    # no window at all
    from app import validity as validity_mod
    window = validity_mod.window(collection, as_of)
    if window:
        restrict = dict(restrict, validity=window)

    # A leg starts only while the caller is still there (app/cancel.py).
    cancel.checkpoint()
    t0 = time.time()
    # as deep as the text leg's other variants: it is one of them (§5.1)
    text_why = None
    catalog_hits, catalog_why = (
        catalog_leg(con, core, limit=max(k, depth, 20), langs=limited_langs(collection, plan.only),
                    collection=collection)
        if mode in ("hybrid", "catalog") else ([], None))
    if book_ids:
        catalog_hits = [h for h in catalog_hits if int(h.get("book_id", -1)) in book_ids]
    if window and catalog_hits:
        # the catalog index carries no validity columns: its hits are
        # admitted by the version table, bounded by the leg's own count
        allowed = validity_mod.admit(con, window, [int(h.get("book_id", -1)) for h in catalog_hits])
        catalog_hits = [h for h in catalog_hits if int(h.get("book_id", -1)) in allowed]
    timings["catalog"] = round((time.time() - t0) * 1000, 1)

    lists: list[list[dict]] = []
    own: set[int] = set()              # the lists run with the query as typed
    limit = max(k, depth, 20)
    if mode in ("hybrid", "fts"):
        t0 = time.time()
        # 05 §5.2 (amended 2026-10-10): the main languages, the query's own,
        # the mixed groups — the other one-language indexes get no variant
        main = plan.main_for(collection) if hasattr(plan, "main_for") else None
        tasks: list[tuple[str, str]] = []
        for group in fts.existing_groups(collection):
            if not plan.searches(group):
                continue
            held = fts.group_languages(group, collection)
            # every variant's list goes to the fusion as it is: they are
            # variants of the ONE text leg, and `_merge_rrf` ranks a record
            # by its best rank across all of them (05 §5.2)
            for q in (plan.variants(group, held, main) if main is not None
                      else plan.variants(group, held)):
                tasks.append((group, q))
        cancel.checkpoint()

        #: the text indexes that could not be searched, each said once, named
        #: in `legs_withheld` (leg "text"; the operator, 2026-11-23: "A")
        text_failed: dict[str, str] = {}

        def one(task: tuple[str, str]) -> list[dict]:
            group, q = task
            cancel.checkpoint()            # one group per language: a query can fan out wide
            # deep enough to hold `limit` RECORDS, not chunks (05 §5.1,
            # amended 2026-10-10: app/leg_depth.py)
            try:
                return fts.search_records(group, q, limit, collection=collection,
                                          langs=plan.lang_filter(group), raise_errors=True, **restrict)
            except fts.TextLegError as e:
                text_failed.setdefault(group, str(e))
                return []
        # THE GROUPS AND THEIR VARIANTS AT ONCE (CLAUDE.md: multi-threading is
        # a must; München, 2026-11-09: wikipedia's ten language indexes, one
        # after the other, 13.2 s a variant and 19-25 s an Ask — en alone
        # 3.3 s). tantivy releases the GIL while it searches (measured 3.2x
        # on 4 cores); the lists come back in the tasks' order, so the fusion
        # sees exactly what the serial loop gave it (invariant 3).
        if len(tasks) > 1:
            with ThreadPoolExecutor(max_workers=min(len(tasks), FTS_THREADS)) as ex:
                results = list(ex.map(cancel.carry(one), tasks))
        else:
            results = [one(t) for t in tasks]
        for (group, q), hits in zip(tasks, results):
            if hits:
                if q == core:
                    own.add(len(lists))          # the user's own words: first on a tie
                lists.append(hits)
        if fts.names_capable(collection):
            # BY NAME (operator, 2026-10-24; wikidata): the records one of
            # whose names in any language IS a word sequence of the reader's
            # own words — in one of the asked languages when the search is
            # limited to some (fts.search_names) — longest name, then most
            # linked, first. A list of the text leg, as the catalogue's is.
            cancel.checkpoint()
            by_name = fts.search_names(collection, core, limit, langs=list(plan.only) or None, **restrict)
            if by_name:
                own.add(len(lists))
                lists.append(by_name)
        if catalog_hits:
            # 05 §5.1 (amended 2026-10-20): the catalogue leg, run with the
            # reader's own words
            cancel.checkpoint()
            try:
                variant = _catalog_variant(collection, catalog_hits, core, restrict)
            except Exception as e:   # noqa: BLE001 — a text index it cannot open fails the leg, not the search
                variant = None
                catalog_why = (f"the catalogue's records could not be carried by their text: "
                               f"{type(e).__name__}: {e}")[:300]
                print(f"{time.strftime('%H:%M:%S')} [{collection.name} warn:catalog] {catalog_why}",
                      flush=True)
            if variant:
                own.add(len(lists))
                lists.append(variant)
        timings["fts"] = round((time.time() - t0) * 1000, 1)
        if text_failed:
            searched = len({g for g, _ in tasks})
            text_why = (f"{len(text_failed)} of its {searched} text index(es) could not be searched"
                        + ("; the others answered" if len(text_failed) < searched else "")
                        + ": " + "; ".join(text_failed[g] for g in sorted(text_failed)))[:600]

    if mode in ("hybrid", "dense"):
        # F1-D6: this leg used to be gated on scope["embed_mode"] != "off",
        # which conflated "don't SPEND on embedding" with "don't USE
        # embeddings". A consumer-role node (§14: import-only) runs with
        # embed_mode=off precisely because its vectors arrive from the
        # network — and the old gate then refused to search the very vectors
        # it had imported. embed_mode governs the worker's spending (lazy
        # enqueue, backfill) and stays respected there; retrieval uses
        # whatever vectors exist. With no table, embeddings.search returns []
        # and an unreachable endpoint degrades via the except below.
        cancel.checkpoint()
        t0 = time.time()
        try:
            # 03 §3.3: a run that FAILED the gate withholds this leg, and the
            # text leg goes on — known before the encoder is even asked
            vector_leg = copy_state.vector_leg_withheld(collection)
            # A5, now at the level of a request (docs/notes/scheduling.md
            # §12): the claim stands corpus embedding aside on the whole
            # device for as long as this query embeds, and the worker
            # yields between CHUNKS — no longer between documents, which on
            # gutenberg measured up to 145 s.
            with dispatch.interactive(dispatch.Level.QUERY):
                # the restriction is a PREFILTER (measured 2026-09-18: +0.8
                # s at most, on wikipedia); a post-filter would return an
                # empty leg whenever the top k were in other languages
                if vector_leg is None:
                    lists.append(embeddings.search_records(core, limit, collection=collection,
                                                           langs=list(limited_langs(collection, plan.only)) or None,
                                                           **restrict))
        except (hold_runs.RunUnvalidated, hold_runs.RunRefused) as e:
            # the answer named a run not yet tested, or one that failed — or
            # VeGa's receipt did not fit it (embeddings.ReceiptRefused): its
            # vector is discarded, and the response says so (05 §5.3: never
            # a silent drop)
            vector_leg = str(e)
        except model_load.LoadTimeout as e:
            # the query encoder was cold and its load outlasted the bound
            # (app/model_load.py): the leg is missing, and the response
            # says why (05 §5.3) — the next search finds it loaded
            vector_leg = str(e)
        except embeddings.EndpointTimedOut as e:
            # the query encoder took the query and did not answer in time —
            # a hung backend (2026-10-09): the leg is missing, the text leg
            # goes on, and the response says why (05 §5.3: never a silent drop)
            vector_leg = f"the query encoder did not answer in time: {e}"
            db.log_event(con, "warn", "dense", f"dense search withheld: {e}")
        except embeddings.EndpointMisconfigured as e:
            # the query encoder's endpoint is the wrong one or refuses us
            # (404, 401, …): waiting will not bring the leg back, so the
            # response says why it is missing (05 §5.3: never a silent drop)
            vector_leg = f"the query encoder's endpoint refused the query: {e}"
            db.log_event(con, "warn", "dense", f"dense search withheld: {e}")
        except embeddings.ValidityColumnsMissing as e:
            # the table predates the collection's validity columns: the
            # text leg answers by validity, this one says why it cannot
            vector_leg = str(e)
            db.log_event(con, "warn", "dense", f"dense search withheld: {e}")
        except Exception as e:
            db.log_event(con, "warn", "dense", f"dense search failed: {e}")
        timings["dense"] = round((time.time() - t0) * 1000, 1)

    merged = _merge_rrf(lists, limit, own=own, query_lang=getattr(plan, "query_lang", None))
    # what the reranker will read above each candidate's chunks (05 §5.6)
    if collections_mod.reranker_for(collection) is not None:
        _attach_heads(con, collection, merged[:limit])
    # 05 §5.2: nothing boosts. The +1/60 an entity-tagged candidate used to
    # get lost on TREC Precision Medicine (80 topics, NDCG@10 0.3073 against
    # 0.3143 without it, docs/notes/query-campaign.md), and the spec never
    # allowed it. Nor do the entities the query names restrict the legs
    # (amended 2026-10-25, operator: restricting lost too, 0.3002 / 0.2983):
    # they are pinned as facts beside the lists, and a reader who wants the
    # tagged records alone asks with the `keys` filter (app/ask/filters.py).

    return {"catalog": catalog_hits[:CATALOG_PROMPT], "merged": merged, "translations": plan.translations,
            "timings": timings, "scope": scope, "vector_leg": vector_leg,
            "catalog_leg": catalog_why, "text_leg": text_why,
            "filtered": len(book_ids) if book_ids is not None else None}


def _leg_withheld(collection, reason: str, leg: str = "vector") -> dict:
    """05 §5.3, §5.4's `legs_withheld` entry: the copy is SEARCHED — its text leg
    goes on — and one leg of it is not, with the reason (03 §3.3: a run that
    failed the gate, or an answer from a run not yet tested; the catalogue's
    index not complete or not readable). Beside `withheld`, which names
    copies left out whole."""
    entry = {"collection": collection.name, "leg": leg, "reason": reason}
    codex = getattr(collection, "codex", None)
    if codex is not None:
        entry["collection_id"] = codex.collection_id
    return entry


def _legs_withheld(collection, partial: dict) -> list[dict]:
    """The legs of one collection's search that did not answer, each with why."""
    out = []
    if partial.get("vector_leg"):
        out.append(_leg_withheld(collection, partial["vector_leg"]))
    if partial.get("catalog_leg"):
        out.append(_leg_withheld(collection, partial["catalog_leg"], leg="catalog"))
    if partial.get("text_leg"):
        out.append(_leg_withheld(collection, partial["text_leg"], leg="text"))
    return out


#: the plugins whose records' `subjects` are a library's subject headings
#: and class codes (LCSH, LCC) and whose `bookshelves` hold the library's
#: own categories — what a work IS, not what it is about
CLASSIFIED_PLUGINS = frozenset({"gutenberg"})


def _attach_metadata_and_enqueue(con, collection, hits: list[dict]) -> None:
    """Attaches title/authors/lang to each hit and, in 'lazy' embed_mode,
    queues the hit documents not yet embedded. Shared by search() and
    federated_search()'s post-fuse step."""
    ids = list({h["book_id"] for h in hits})
    meta = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        for r in con.execute(
                f"SELECT native_id,title,authors,lang,subjects,bookshelves FROM documents "
                f"WHERE source=? AND native_id IN ({marks})",
                [collection.plugin] + [str(i) for i in ids]):
            # the record's own classification — its subject headings and
            # class codes, its shelves (PG's "Category: …") — for what the
            # answer's model is told the work IS (`_work_said`; operator,
            # 2026-11-26: "Collections like gutenberg carry the category tag
            # to determine what's fiction, what's a fact and what's an opinion")
            meta[int(r["native_id"])] = {"title": r["title"], "authors": r["authors"], "lang": r["lang"]}
            # ONLY WHERE THE SUBJECTS ARE A LIBRARY'S HEADINGS: PG's LCSH and
            # LCC, and its category shelves. A Wikipedia article's categories
            # ("Italian poetry", "1890s novels") say what it is ABOUT, and
            # read as a genre they would call an encyclopedia literature
            # (architecture, 2026-11-26)
            if collection.plugin in CLASSIFIED_PLUGINS:
                meta[int(r["native_id"])].update(subjects=r["subjects"] or "", bookshelves=r["bookshelves"] or "")
    for h in hits:
        h.update(meta.get(h["book_id"], {}))
        h.pop("rrf", None)
        h.pop(MATCHED, None)        # the search's own bookkeeping, never a field
        h.pop(HEAD, None)
    # the original publication year, from the record (operator, 2026-11-16)
    from .ask import works
    works.attach_first_published(collection, hits)
    # Provenance badge per hit: locally embedded, or imported from a producer
    # and possibly verified (architecture-v2.md §6 T4). Replaces P0.6's
    # hardcoded 'local ✓' placeholder in the Ask provenance drawer.
    verify_on_use.annotate(con, hits)
    # 05 §5.3 / 03 §3.5 T4: the chunks this search cites are queued for the
    # worker to re-embed once, asynchronously — a search never waits on the
    # encoder, and a queue that cannot be written never costs the answer
    try:
        verify_on_use.cite(con, [h["chunk_id"] for h in hits if h.get("chunk_id")])
    except Exception:   # noqa: BLE001
        pass
    scope = db.get_scope(con)
    if scope["embed_mode"] == "lazy":
        _enqueue_lazy_embed(con, ids[:10], collection)


def _attach_work_fields(con, collection, hits: list[dict], query: str) -> list[dict]:
    """Give each hit what `work_dedup` needs, then drop the translations
    that compete with something better.

    NO LONGER ON THE SEARCH PATH (2026-10-09). Dropping an edition lost
    the work the reader wanted on 21 of 451 measured queries and found it
    on 2 (docs/notes/one-language-per-work.md); a search now groups a
    work's editions after the reranker instead (app/ask/works.py), from
    claims rather than the (author, LCSH) guess. Kept, with its tests,
    until the operator retires `work_dedup` and `documents.work`.

    BEFORE THE RERANK, deliberately. Dedup after the top-k cut would
    return a short page — twelve hits asked for, seven delivered, and the
    five slots spent on suppressed translations rather than refilled. Run
    here, the reranker draws its k from the survivors and the page is
    full of distinct works.

    Cost is one indexed lookup over the candidate set's ids (≤ 4k, so ≤
    48 by default) against the `(source, native_id)` key — the same query
    shape `_attach_metadata_and_enqueue` already runs on the final set.
    A hit whose document has no `work` row is passed through untouched: a
    guess about what a book IS must never cost a reader the book.
    """
    ids = list({h["book_id"] for h in hits})
    if not ids:
        return hits
    marks = ",".join("?" for _ in ids)
    works: dict[int, dict] = {}
    try:
        rows = con.execute(
            f"SELECT native_id, lang, work FROM documents "
            f"WHERE source=? AND native_id IN ({marks})",
            [collection.plugin] + [str(i) for i in ids]).fetchall()
    except Exception:   # noqa: BLE001 — A DEDUP THAT CANNOT RUN RETURNS EVERY
        return hits     # HIT, NEVER NONE. `work` is added by an ALTER in
        # `db.connect`, so a manifest opened some other way — an older
        # process still running mid-deploy, a hand-opened connection — has
        # no such column, and this is on the path of EVERY query. Losing
        # the dedup costs a reader some duplicate translations; raising
        # here costs them the search.
    for r in rows:
        try:
            parsed = json.loads(r["work"]) if r["work"] else None
        except (TypeError, ValueError):
            parsed = None           # unreadable is no key, never a broken page
        if parsed:
            works[int(r["native_id"])] = {
                "work_author": parsed.get("author", ""),
                "work_subjects": tuple(parsed.get("subjects") or ()),
                "translated": bool(parsed.get("translated")),
                "lang": r["lang"]}
    for h in hits:
        h.update(works.get(h["book_id"], {}))
    return work_dedup.prefer_originals(hits, query=query)


def _rerank_lists(per_collection: list, rerank_one) -> list:
    """`rerank_one` over each (collection, list), in their order. LISTS THAT
    SHARE A RERANKER GO ONE AFTER THE OTHER, each with its whole budget from
    its own start; different rerankers at once (München, 2026-11-10: with
    louise-rerank-bge serving five collections, all five lists started their
    9.6 s budgets together, the reranker answers one sequence at a time
    (`parallel:1`, bit-exact), and the fifth — wikipedia — spent its budget
    queued: "the reranker did not answer within 9.6 s", its list unreranked).
    The time is the same — one reranker does the work of all its lists in
    turn either way — but no list loses its budget waiting for another's.

    A SEARCH NOBODY WAITS FOR STOPS BETWEEN TWO LISTS, not after the last
    (München, 2026-11-25: a search replaced by a second one went on for 9 s
    inside this — 17.9 s of rerank for its lists — while the second search
    queued behind it on the same reranker, `parallel:1`). One rerank call
    cannot be interrupted (app/cancel.py); the next one is not started.

    WHAT A LIST DOES AFTER ITS RERANKER ANSWERED IS NOT THE RERANKER'S TURN.
    `rerank_one` may return a function — the grouping of the list's works —
    in the result's place: it runs on a thread of its own while the NEXT list
    of the same reranker is judged, and its result is the list's. The node,
    2026-11-26, a first question: six lists one after the other, 39.3 s of
    rerank, of which wikipedia's works alone 18.8 s with the reranker idle
    behind them and four lists waiting for it."""
    from concurrent.futures import Future
    groups: dict[tuple, list[int]] = {}
    for i, (c, _hits) in enumerate(per_collection):
        r = collections_mod.reranker_for(c)
        key = (r.url, r.model) if r is not None else ("", getattr(c, "name", str(i)))
        groups.setdefault(key, []).append(i)
    out: list = [None] * len(per_collection)

    with ThreadPoolExecutor(max_workers=max(1, len(groups))) as ex, \
            ThreadPoolExecutor(max_workers=max(1, len(per_collection)), thread_name_prefix="works") as after:
        def rerank_in_turn(idx: list[int]) -> list:
            got = []
            for i in idx:
                cancel.checkpoint()         # the caller has gone: the next list's call is not made
                step = rerank_one(per_collection[i])
                got.append((i, after.submit(cancel.carry(step)) if callable(step) else step))
            return got
        for part in ex.map(cancel.carry(rerank_in_turn), list(groups.values())):
            for i, got in part:
                out[i] = got.result() if isinstance(got, Future) else got
    return out


def _one_hit_per_work(hits: list[dict], collection, plan: LanguagePlan,
                      show_in: str | None, annotations=None, limit: int | None = None,
                      timings: dict | None = None) -> list[dict]:
    """05 §5.3 (amended 2026-10-09): each work once, after the reranker and
    before the cut to `k`, shown in the reader's language where one of its
    editions in it was found (app/ask/works.py). Grouping that cannot run
    returns the list as ranked. `annotations`: the annotations collections
    whose work claims group (None = every one held; the Ask page's choice,
    2026-10-19). `timings`: told what the grouping was made of
    (`works.one_hit_per_work`)."""
    from .ask import works
    return works.one_hit_per_work(hits, collection,
                                  works.preference(show_in, plan.query_lang),
                                  annotations=annotations, plan=plan, limit=limit, timings=timings)


def _works_parts(t_works: float, split: dict) -> dict:
    """The works' whole time and its parts, as a list's rerank note carries
    them: `works_ms`, and `works_claims_ms`, `works_facts_ms`,
    `works_passages_ms` with `works_passages_n` (`_rerank_parts`, the
    search's line)."""
    return {"works_ms": (time.monotonic() - t_works) * 1000,
            **{f"works_{k}_ms": float(v) for k, v in split.items() if k != "passages_n"},
            **({"works_passages_n": int(split["passages_n"])} if split.get("passages_n") else {})}


def _note_works(t_works: float, split: dict) -> None:
    _note_rerank(**_works_parts(t_works, split))


def search(con, query: str, k: int = 12, mode: str = "hybrid",
           translate: bool = True, collection=None,
           rerank_depth: int = RERANK_TOP_N, all_languages: bool = False,
           book_ids: set[int] | None = None, show_in: str | None = None,
           as_of: int | None = None, annotations=None) -> dict:
    """mode: hybrid | fts | dense | catalog. Single collection — unchanged
    behavior/signature from pre-P0.5. See federated_search() for N&gt;1.
    `k` results after the reranker, which orders the top `rerank_depth`;
    a work's editions are one of them (`show_in`: the language the reader
    asked results in, the Ask page's "show results in")."""
    collection = collection or collections_mod.default_collection()
    t_total = time.time()

    # resolve -> expand -> plan (architecture-v2.md §4, docs/05). No entities
    # found (the common case) -> no facts and no entity ids; found, they are
    # pinned as facts and change no rank (05 §5.2: nothing boosts).
    store = refs.get_store()
    resolved, facts, facts_withheld = _entity_facts(query, store)

    cancel.checkpoint()                    # the plan may call the translator
    t0 = time.time()
    plan = language_plan.plan(query, [collection], translate, every=all_languages)
    plan_ms = round((time.time() - t0) * 1000, 1)
    partial = _search_one_collection(con, collection, query, k, mode, translate,
                                     [e.entity_id for e in resolved], plan, rerank_depth,
                                     **({"book_ids": book_ids} if book_ids is not None else {}),
                                     **({"as_of": as_of} if as_of is not None else {}))
    timings = {"languages": plan_ms, **partial["timings"]}

    cancel.checkpoint()
    t0 = time.time()
    # Every record competes: the edition-dropping rule that ran here
    # (`_attach_work_fields`) lost the work the reader wanted on 21 of 451
    # queries and found it on 2 (docs/notes/one-language-per-work.md) —
    # editions are grouped after the reranker instead, never dropped.
    candidates = partial["merged"][: max(k * 4, rerank_depth)]

    def _rerank_and_group():
        ranked, reranked, rerank_why = _reranked(plan.core, candidates,
                                                 collections_mod.reranker_for(collection),
                                                 _rerank_budget_s(rerank_depth), rerank_depth,
                                                 plan.translations)
        t_works, split = time.monotonic(), {}
        final = _one_hit_per_work(ranked, collection, plan, show_in, annotations, limit=k, timings=split)[:k]
        _note_works(t_works, split)
        return final, reranked, rerank_why
    (final, reranked, rerank_why), parts = _rerank_parts(_rerank_and_group)
    timings["rerank"] = round((time.time() - t0) * 1000, 1)
    timings["rerank_parts"] = {collection.name: parts}

    # This one WRITES (the lazy-embed queue): a search nobody is waiting for
    # enqueues nothing.
    cancel.checkpoint()
    _attach_metadata_and_enqueue(con, collection, final)

    timings["total"] = round((time.time() - t_total) * 1000, 1)
    result = {"query": query, "mode": mode, "translations": partial["translations"],
              "language": plan.summary(), "query_lang": plan.query_lang,
              "query_lang_by": getattr(plan, "query_lang_by", ""),
              "main_languages": list(getattr(plan, "main", ())),
              "catalog": partial["catalog"], "hits": final,
              "facts": [asdict(f) for f in facts] + _article_facts(query, [collection], facts, as_of, store,
                                                                   plan.query_lang),
              "facts_withheld": facts_withheld,
              "timings": timings, "reranked": reranked}
    withheld = _legs_withheld(collection, partial)
    if withheld:
        result["legs_withheld"] = withheld
    if rerank_why:
        result["rerank_withheld"] = [_rerank_withheld(collection, rerank_why)]
    if partial.get("filtered") is not None:
        result["filtered"] = {collection.name: partial["filtered"]}
    return result


def _merge_rrf_federated(lists: list[list[dict]]) -> list[dict]:
    """Second RRF pass across collections' already-intra-collection-merged
    candidate lists. Rank is each list's OWN position —
    raw per-collection score/rrf values are never compared across
    collections (architecture-v2.md's RRF-fuses-rankable-lists design, and
    this task's own "scores never compared across collections" contract).
    Each list belongs to exactly one collection (already deduped
    intra-collection by _merge_rrf), so no cross-list key collisions are
    possible — this is flat rank-to-score assignment, not an accumulating
    merge like _merge_rrf. dict(h) on a bare PinnedFact raises TypeError
    immediately (a frozen dataclass isn't a mapping), so this inherits the
    same invariant-4 type-safety guarantee as _merge_rrf."""
    scored = []
    for hits in lists:
        for rank, h in enumerate(hits):
            entry = dict(h)
            entry["rrf"] = 1.0 / (RRF_K + rank + 1)
            scored.append(entry)
    scored.sort(key=lambda x: -x["rrf"])
    return scored


def _merge_by_relevance(lists: list[tuple[list[dict], Any]]) -> list[dict]:
    """05 §5.6 (amended 2026-10-31, operator's B): the collections' lists
    merged on each record's PROBABILITY OF RELEVANCE — its reranker's score
    through that collection's own calibration (`collections.Calibration`,
    Platt scaling) — where rank fusion gave every collection's first record
    the same weight, however weak (docs/notes/federated-merge.md).

    EACH LIST KEEPS ITS OWN ORDER. A record's merge value is the best
    probability at or below it in its own list (a running maximum from the
    bottom), so what its collection put above another record stays above it:
    the catalogue's first record (§5.6), a language's slots, the tail the
    reranker did not read (probability 0 — it follows every judged record).
    Ties: the higher place in its own list, then the list's order (the
    collections by name, as the caller sorted them). Each hit keeps `rrf`, its
    rank-fusion value, beside `relevance`."""
    scored: list[tuple[float, int, int, dict]] = []
    for li, (hits, calibration) in enumerate(lists):
        envelope = [0.0] * len(hits)
        best = 0.0
        for i in range(len(hits) - 1, -1, -1):
            score = hits[i].get("rerank_score")
            if score is not None:
                best = max(best, calibration.probability(score))
            envelope[i] = best
        for i, h in enumerate(hits):
            entry = dict(h)
            entry["rrf"] = 1.0 / (RRF_K + i + 1)
            entry["relevance"] = round(envelope[i], 6)
            scored.append((-envelope[i], i, li, entry))
    scored.sort(key=lambda t: t[:3])
    return [t[3] for t in scored]


def _relevance_merge_withheld(lists: list[tuple], reranked: dict[str, bool]) -> list[dict]:
    """Why these lists cannot be merged on relevance — one entry per list
    that has no usable calibration this time; [] when every one has. `lists`
    is (collection, hits, recipe) per collection."""
    why: list[dict] = []
    for collection, _hits, recipe in lists:
        cal = getattr(recipe, "calibration", None) if recipe is not None else None
        if recipe is None:
            reason = "no reranker"
        elif cal is None:
            reason = "its reranker has no calibration"
        elif cal.rerank_model != recipe.model:
            reason = f"calibrated for {cal.rerank_model}, its reranker is {recipe.model}"
        elif not reranked.get(collection.name):
            reason = "its list was not reranked this time"
        else:
            continue
        why.append({"collection": collection.name, "reason": reason})
    return why


def federated_search(collections: list, query: str, k: int = 12, mode: str = "hybrid",
                      translate: bool = True, rerank_depth: int = RERANK_TOP_N,
                      all_languages: bool = False, book_ids_for=None,
                      show_in: str | None = None, as_of: int | None = None,
                      annotations=None, keep_lists: bool = False) -> dict:
    """Query-time federation across N collections (architecture-v2.md §3/§4).
    Entity resolution/PinnedFacts happen ONCE, collection-agnostic — never
    multiplied per collection (CLAUDE.md invariant 4). Each collection's
    hybrid search runs in parallel (ThreadPoolExecutor, matching
    embeddings.py's _embed_texts()'s own parallelism idiom — this codebase is
    synchronous throughout, no asyncio); a collection whose connection or
    search fails is isolated (logged, contributes no candidates) rather than
    failing the whole query. With exactly one collection this degenerates to
    the same candidate order search() itself produces — see
    config.FEDERATION_CANDIDATE_BUDGET, which only applies when len(collections)>1."""
    t_total = time.time()

    # The same entity step as search(), through the same helper: the two paths
    # came in a pair, and fixing one left the other pinning 21,969 facts.
    store = refs.get_store()
    resolved, facts, facts_withheld = _entity_facts(query, store)
    entity_ids = [e.entity_id for e in resolved]

    # ONE language plan for the whole search: one model call translates the
    # query into the union of what every collection holds, where each
    # collection used to ask for its own — serialised anyway, since the
    # node's Ollama runs one request at a time (OLLAMA_NUM_PARALLEL=1).
    cancel.checkpoint()                    # the plan may call the translator
    t0 = time.time()
    plan = language_plan.plan(query, collections, translate, every=all_languages)
    plan_ms = round((time.time() - t0) * 1000, 1)

    def _run_one(collection):
        try:
            con = cancel.watch(db.connect(collection))
        except Exception as e:
            return collection, None, e
        try:
            admitted = book_ids_for(collection) if book_ids_for else None
            partial = _search_one_collection(con, collection, query, k, mode, translate,
                                              entity_ids, plan, rerank_depth,
                                              **({"book_ids": admitted} if admitted is not None else {}),
                                              **({"as_of": as_of} if as_of is not None else {}))
            for h in partial["merged"]:
                h["source_collection"] = collection.name
            return collection, partial, None
        except Exception as e:
            try:
                db.log_event(con, "warn", "federation",
                             f"collection '{collection.name}' search failed: {e}")
            except Exception:
                pass
            return collection, None, e

    # `carry`: a pool thread does not inherit the search's token, and without
    # it every per-collection leg would run on after its caller had gone.
    # Cancelled is a BaseException, so `_run_one`'s `except Exception` lets it
    # through rather than logging a cancelled search as a failed collection.
    with ThreadPoolExecutor(max_workers=max(1, len(collections))) as ex:
        outcomes = list(ex.map(cancel.carry(_run_one), collections))
    cancel.checkpoint()
    # ex.map preserves input order already, but sort explicitly on
    # collection.name for a deterministic tie-break in the federated fuse
    # below regardless of executor internals (invariant 3).
    outcomes.sort(key=lambda o: o[0].name)

    per_collection: list[tuple] = []      # (collection, its merged list)
    translations: dict[str, str] = {}
    timings_per_collection: dict[str, dict] = {}
    legs_withheld: list[dict] = []
    catalog: list[dict] = []
    filtered: dict[str, int] = {}         # 05 §5.2: records a filter admitted, per collection
    for collection, partial, err in outcomes:
        if partial is None:
            continue
        if partial.get("filtered") is not None:
            filtered[collection.name] = partial["filtered"]
        merged = partial["merged"]
        if len(collections) > 1:
            # never below the depth the request asked the reranker for: a
            # budget of 40 under a depth of 50 made "reranked from 50" a
            # claim about 40
            merged = merged[:max(config.FEDERATION_CANDIDATE_BUDGET, rerank_depth)]
        per_collection.append((collection, merged))
        translations.update(partial["translations"])
        timings_per_collection[collection.name] = partial["timings"]
        legs_withheld.extend(_legs_withheld(collection, partial))
        catalog.extend(partial["catalog"])
    collection_by_name = {c.name: c for c in collections}

    t0 = time.time()
    # 05 §5.6: each collection's list is reranked ON ITS OWN, by that
    # collection's reranker — two spaces are never put on one score scale —
    # and only then fused, by rank. This used to fuse first and rerank the
    # merged list once, with the node's one RERANK_URL: every collection
    # scored by the same model, on one scale. A work's editions are grouped
    # after it, per collection (work claims name one collection's records,
    # and `book_id` alone is ambiguous across them; 05 §5.3). The lists
    # rerank in parallel: one slow reranker costs the query its own time,
    # not the sum.
    budget_s = _rerank_budget_s(rerank_depth)

    rerank_parts: dict[str, dict] = {}

    def _rerank_one(item):
        collection, merged = item
        candidates = merged[: max(k * 4, rerank_depth)]

        (ranked, ok, why), parts = _rerank_parts(lambda: _reranked(
            plan.core, candidates, collections_mod.reranker_for(collection), budget_s,
            rerank_depth, plan.translations))

        def group_works():
            # each work once, per collection (work claims are about one
            # collection's records), before the fuse cuts to k — OFF the
            # reranker's turn (`_rerank_lists`): the next list of the same
            # reranker is judged meanwhile
            t_works, split = time.monotonic(), {}
            grouped = _one_hit_per_work(ranked, collection, plan, show_in, annotations, limit=k, timings=split)
            for key, v in _works_parts(t_works, split).items():
                parts[key] = round(v, 1) if isinstance(v, float) else v
            # the list's own time: its rerank and its works, as the line has always said it
            parts["rerank"] = round(parts.get("rerank", 0) + parts["works_ms"], 1)
            rerank_parts[collection.name] = parts
            return grouped, ok, why
        return group_works

    ranked = _rerank_lists(per_collection, _rerank_one)
    reranked = {c.name: ok for (c, _), (_, ok, _) in zip(per_collection, ranked)}
    rerank_withheld = [_rerank_withheld(c, why)
                       for (c, _), (_, _, why) in zip(per_collection, ranked) if why]
    # 05 §5.6 (amended 2026-10-31): on relevance when every list's reranker
    # is calibrated and answered; by rank otherwise, and the result says why
    recipes = [(c, hits, collections_mod.reranker_for(c))
               for (c, _), (hits, _, _) in zip(per_collection, ranked)]
    merge_withheld = _relevance_merge_withheld(recipes, reranked) if len(recipes) > 1 else []
    if len(recipes) > 1 and not merge_withheld:
        final = _merge_by_relevance([(hits, r.calibration) for _, hits, r in recipes])[:k]
        merged_by = "relevance"
    else:
        final = _merge_rrf_federated([hits for hits, _, _ in ranked])[:k]
        merged_by = "rank"
    rerank_ms = round((time.time() - t0) * 1000, 1)

    # metadata attach + lazy-embed enqueue, grouped by source_collection
    # since bare book_id is ambiguous across collections
    by_collection: dict[str, list[dict]] = {}
    for h in final:
        by_collection.setdefault(h["source_collection"], []).append(h)
    # This WRITES (the lazy-embed queue): nothing is enqueued for a search
    # nobody is waiting for.
    cancel.checkpoint()
    for name, hits in by_collection.items():
        collection = collection_by_name[name]
        con = cancel.watch(db.connect(collection))
        _attach_metadata_and_enqueue(con, collection, hits)

    timings = {
        "languages": plan_ms,
        "catalog": max((t["catalog"] for t in timings_per_collection.values()), default=0.0),
        "fts": max((t.get("fts", 0.0) for t in timings_per_collection.values()), default=0.0),
        "dense": max((t.get("dense", 0.0) for t in timings_per_collection.values()), default=0.0),
        "rerank": rerank_ms,
        "per_collection": timings_per_collection,
        # what each list's rerank was made of (`_rerank_parts`); lists that
        # share a reranker ran one after the other (`_rerank_lists`)
        "rerank_parts": rerank_parts,
    }
    timings["total"] = round((time.time() - t_total) * 1000, 1)

    result = {"query": query, "mode": mode, "translations": translations,
              "language": plan.summary(), "query_lang": plan.query_lang,
              "query_lang_by": getattr(plan, "query_lang_by", ""),
              "main_languages": list(getattr(plan, "main", ())),
              "catalog": catalog, "hits": final,
              "facts": [asdict(f) for f in facts] + _article_facts(query, collections, facts, as_of, store,
                                                                   plan.query_lang),
              "facts_withheld": facts_withheld,
              "timings": timings, "reranked": reranked, "merged_by": merged_by}
    if legs_withheld:
        result["legs_withheld"] = legs_withheld
    if rerank_withheld:
        result["rerank_withheld"] = rerank_withheld
    if merge_withheld and any(getattr(r, "calibration", None) for _, _, r in recipes):
        # a calibration is configured and was not used this time: say why
        result["merge_withheld"] = merge_withheld
    if filtered:
        result["filtered"] = filtered
    if keep_lists:
        # MEASUREMENT (tools/measure/fusion_calibration.py): each collection's
        # list as the merge saw it
        result["lists"] = {c.name: {"reranked": reranked.get(c.name, False),
                                    "rerank_model": getattr(r, "model", None), "hits": hits}
                           for c, hits, r in recipes}
    return result

ANSWER_SYSTEM = (
    "You are a research assistant over a large multilingual library of "
    "public-domain books (Project Gutenberg). Answer the user's question using "
    "ONLY the numbered sources provided. Cite sources inline as [n]. Sources "
    "may be in a different language than the question: read them in their "
    "language, answer in the user's language. If the sources are insufficient, "
    "say so plainly. Blocks marked [reference] are authoritative structured "
    "facts, not ranked evidence — use them but do not cite them with [n]."
)

#: A SOURCE IS A WORK OF ITS TIME (operator, 2026-11-26: "the AI summary
#: makes no distinction between facts and fiction; it happens that a
#: historical character is mentioned in a book, and the content of the book
#: is reported as an historical fact"; "sometimes a source reports a time
#: span related to the time the source has been written (Napoleon is dead
#: since 100 years and still...); the AI summary must take into account the
#: publication date (the first publication date, not the publication in
#: Gutenberg)"). Each source's header says what its own record says of the
#: work — its first publication, its subjects, whether they call it a
#: literary work (`_work_said`) — and the model is told how to read them.
#: Only the collection's own corpus, or an annotation named as such
#: (CLAUDE.md, "A collection holds only the information contained in its
#: corpus"); where the record says nothing, the header says nothing.
WORK_RULE = (
    " Each source is a work of its own time. Where its header says it is a literary work (fiction, a "
    "novel, a play, poetry), what it tells is what that work narrates: write \"in the novel …\" or "
    "\"the author imagines …\", never present its events or its characters' deeds as historical fact. "
    "A time span or a 'now' in a source (\"dead these hundred years\", \"today\") counts from that "
    "work's first publication, when its header gives one: date it from that year, never from today; "
    "where the header gives none, do not turn it into a date. Where its header says a work of opinion or "
    "belief (essays, letters, speeches, philosophy, religion, politics), what it states is its author's "
    "view: attribute it (\"X argues …\", \"according to X …\"), never present it as established fact. "
    "A date's source is named where the header names it (\"by …\")."
)

#: A LITERARY WORK, as the record's own subjects say it — the Library of
#: Congress Subject Headings Project Gutenberg's records carry (its RDF
#: `dcterms:subject`, app/plugins/gutenberg/rdf.py), where the genre is the
#: heading's FORM: its last subdivision ("Napoleon I … -- Fiction", "-- Drama",
#: "-- Poetry", "-- Juvenile fiction") or a genre heading of its own
#: ("Historical fiction", "Short stories", "Love stories", "English poetry").
#: Never a word anywhere in a heading: "Roman Empire", "Romance languages",
#: "Drama -- History and criticism" are about history, languages or
#: literature, and calling them fiction would tell the model a history book
#: invents (architecture, 2026-11-26).
LITERARY_FORMS = frozenset({"fiction", "juvenile fiction", "drama", "juvenile drama", "poetry",
                            "juvenile poetry", "comic books, strips, etc"})
LITERARY_HEADING = re.compile(
    r"^(?:[\w' .-]+ )?(?:fiction|poetry|drama|stories|short stories|tales|fairy tales|fables|"
    r"novels|romances|plays|verse|ballads|sonnets|epic poetry)$", re.IGNORECASE)


def literary(subject: str) -> bool:
    """Whether one subject heading calls its record a literary work: its
    form subdivision (the last), or its main heading as a genre — with its
    qualifiers stripped ("Fairy tales -- Germany", "Short stories, American",
    "Greek drama (Tragedy)", "Epic poetry, Greek") — never a heading about
    literature (its criticism, its history, its study) nor about the Bible."""
    parts = [p.strip().rstrip(".") for p in str(subject).split("--")]
    if not parts or not parts[0]:
        return False
    if len(parts) > 1 and parts[-1].lower() in LITERARY_FORMS:
        return True                       # the heading's FORM: "France -- History -- … -- Drama"
    if any(p.lower() in ("history and criticism", "criticism and interpretation", "bibliography",
                         "study and teaching", "history", "technique", "authorship", "indexes") for p in parts[1:]):
        return False
    main = re.sub(r"\s*\([^)]*\)\s*$", "", parts[0])          # "(Tragedy)", "(Literary genre)"
    main = re.sub(r",\s*[^,]+$", "", main)                        # ", American", ", Greek", ", English"
    if re.match(r"^bible\b", main, re.IGNORECASE):
        return False
    return parts[-1].lower() in LITERARY_FORMS or bool(LITERARY_HEADING.match(main))


#: PG's own shelves of kind ("Category: Novels", "Category: Essays, Letters &
#: Speeches", "Category: History - European"): what the work IS, by the
#: shelf's NAME (a word of it, these names being kinds, not topics)
CATEGORY = "Category:"
CATEGORY_LITERARY = re.compile(r"\b(novels?|fiction|short stories|poetry|drama|plays|theatre|humou?r|"
                               r"mythology|legends|folklore|fairy tales|romance|adventure|mystery|crime)\b",
                               re.IGNORECASE)
CATEGORY_OPINION = re.compile(r"\b(essays|letters|speeches|philosophy|ethics|religio\w*|spirituality|"
                              r"politics|opinion|sermons|theology)\b", re.IGNORECASE)
#: Library of Congress Classification, as PG's records carry it (its class
#: letters, "PQ", "DC", "PZ"): PZ is fiction in English and juvenile
#: fiction; B but BF (psychology) is philosophy and religion. The other
#: literature classes (PA-PT) hold criticism and the classics' histories
#: too: they are named, never read as fiction.
LCC = re.compile(r"^[A-Z]{1,3}$")


def _work_kind(subjects: list[str], categories: list[str], classes: list[str]) -> str | None:
    """What a record says its work IS — "a literary work", "a work of
    opinion or belief" — or None where it says neither. Its own subject
    headings' form first (`literary`), then PG's category shelves, then its
    LCC class."""
    if any(literary(x) for x in subjects) or any(CATEGORY_LITERARY.search(c) for c in categories) \
            or "PZ" in classes:
        return "a literary work"
    if any(CATEGORY_OPINION.search(c) for c in categories) \
            or any(c.startswith("B") and not c.startswith("BF") for c in classes):
        return "a work of opinion or belief"
    return None


def _split(v) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.split("|") if x.strip()]
    return [str(x).strip() for x in (v or ()) if str(x).strip()]


def _work_said(h: dict) -> str:
    """What a hit's own record says of its WORK, for the answer's header:
    its first publication (`first_published`; "by PG's automatic summary"
    where that is where the year was read, app/ask/works.py), what it is
    (`_work_kind`), PG's category shelves, its LCC class and its subject
    headings — set on a hit only for a CLASSIFIED_PLUGINS record. Empty
    where the record says nothing."""
    parts = []
    year = h.get("first_published")
    if year:
        by = h.get("first_published_by")
        parts.append(f"first published {year}" + (f" (by {by})" if by else ""))
    every = _split(h.get("work_subjects")) or _split(h.get("subjects"))
    classes = [x for x in _split(h.get("subjects")) if LCC.match(x)]
    subjects = [x for x in every if not LCC.match(x)]
    categories = [x[len(CATEGORY):].strip() for x in _split(h.get("bookshelves")) if x.startswith(CATEGORY)]
    kind = _work_kind(subjects, categories, classes)
    if kind:
        parts.append(kind)
    if categories:
        parts.append("Gutenberg category: " + "; ".join(categories[:4])[:160])
    if classes:
        parts.append("LCC " + ", ".join(classes[:3]))
    if subjects:
        parts.append("subjects: " + "; ".join(subjects[:6])[:240])
    return (" — " + " — ".join(parts)) if parts else ""


#: QWEN3'S OWN SWITCH, in the prompt — as `llm._RESTRICT_SYSTEM` carries it.
#: The node's chat model ignores `chat_template_kwargs.enable_thinking` and
#: thinks before it answers. Measured on "Diritto all'oblio" over
#: normattiva (2026-10-13, five runs): about 1,400 characters of hidden
#: reasoning per answer, the first visible word after 19–34 s, and the
#: reasoning — written in English — pulled the Italian question's answer
#: into English once in four runs. With the switch: no reasoning, the first
#: word after 0.4 s warm (9.8 s cold), the answer in the question's
#: language and whole. Nothing is lost: the reasoning was never shown.
THINK_OFF = " /no_think"

#: THE CITATIONS, ASKED LAST AND BY EXAMPLE. With the switch on, the model
#: stopped citing: "Diritto all'oblio" over normattiva, 2026-10-13, 0
#: markers in Italian and 1 without a language, where the thinking runs
#: had cited four sources — the reasoning was where it planned them, and
#: the page's [n] links went with it ("complete, but without the links",
#: operator). The system prompt's "cite as [n]" sits far from the end;
#: asked again after the question it is followed, and a reminder written
#: as "[n]" was copied literally ("[n4]", 0 markers in four runs) where
#: the example "[2] or [2][5]" gave 4–6 markers in each of three runs. The
#: page links `[digits]` only (ask.html linkCitations), so the example
#: shows that shape and nothing else inside the brackets.
CITE_REMINDER = ("After each statement, cite the source it rests on by its number in square "
                 "brackets, like [2] or [2][5]: the numbers of the sources above, nothing "
                 "else inside the brackets.")

#: Characters of passages an answer is given. The chat runs with a 16,384-
#: token context and Ollama silently drops the START of a prompt that does
#: not fit — the instructions first. The results count is the reader's to
#: set now (up to 100 x 1,800 characters), so the passages stop at this
#: budget: the default 10 (18,000) always fit, and a longer list gives the
#: answer its first passages and never a truncated prompt.
ANSWER_SOURCE_CHARS = 24_000


def answer_hits_shown(hits: list[dict], budget: int = ANSWER_SOURCE_CHARS) -> int:
    """How many of `hits` the answer is given: the passages up to `budget`
    characters (ANSWER_SOURCE_CHARS, or less where the chat model's context
    is short of room, app/api.py `_fit_answer`), the first always."""
    spent = 0
    for i, h in enumerate(hits, 1):
        spent += len(h["text"][:1800])
        if i > 1 and spent > budget:
            return i - 1
    return len(hits)


def build_answer_messages(query: str, result: dict, answer_lang: str | None = None, *,
                          source_chars: int = ANSWER_SOURCE_CHARS) -> list[dict]:
    """`answer_lang` (an ISO code, STATUS 5j "show results in"): the model
    answers in that language directly — cheaper than translating the
    answer afterwards, and the [n] citations stay aligned.

    A hit's `reading` (app/historical.py `prepare`, the reader's Quality):
    what its head says of an older stage of its language; with one, the
    system prompt says how to read such a source."""
    blocks = []
    for fact in result.get("facts", []):
        label = fact.get("label") or fact["entity_id"]
        field_lines = "\n".join(f"{k}: {v}" for k, v in fact.get("fields", {}).items())
        via = fact.get("via")
        reached = (f"\nreached from {via['from']} through {via['link']} ({via['on']})"
                   if via else "")
        blocks.append(f"[reference] {label} — {fact['entity_id']}{reached}\n{field_lines}")
    shown_hits = result["hits"][:answer_hits_shown(result["hits"], source_chars)]
    for i, h in enumerate(shown_hits, 1):
        # numbered by position on the page, so [n] still opens hit n
        # Source-qualified location (e.g. "gutenberg:1342 CHAPTER I",
        # "pubmed:38112345 Title") — chunk_id is uid-prefixed with the
        # source plugin (app/chunking.py), so this is recoverable even for
        # search()'s single-collection hits, not just federated_search()'s.
        source_id = h.get("chunk_id", "").split(":", 1)[0] or "?"
        loc = f"{source_id}:{h['book_id']}"
        if h.get("chapter"):
            loc += f" {h['chapter']}"
        head = f"[{i}] {loc} — {h.get('title','?')} — {h.get('authors','?')} ({h.get('lang','?')})" + _work_said(h)
        blocks.append(head + (h.get("reading") or "") + "\n" + h["text"][:1800])
    if result["catalog"]:
        cat = "\n".join(f"- {c['title']} — {c['authors']} (book #{c['book_id']})"
                        for c in result["catalog"][:8])
        blocks.append("[catalog matches]\n" + cat)
    context = "\n\n---\n\n".join(blocks) if blocks else "(no sources found)"
    system = ANSWER_SYSTEM + WORK_RULE
    if any(h.get("reading") for h in shown_hits):
        from . import historical
        system += historical.SYSTEM_RULE
    # THE ANSWER'S LANGUAGE IS NAMED, AND SAID LAST (operator, 2026-11-25: "I
    # made a search with a query in italian and the answer was in french" —
    # "Parlami della vita di Dante Alighieri" over gutenberg, the first source
    # a French history of Italian literature, the small chat model, no answer
    # language chosen on the page). Until then a question with no language
    # chosen had ONE instruction, "answer in the user's language", in the
    # system prompt, thousands of words of sources above the question; the
    # model was left to work out which language that is, and followed its
    # first source. The search already knows the question's language
    # (`query_lang`, 05 §5.2): it is named — in the system prompt and again
    # after the sources and the question — and where nobody knows it, that
    # line still says whose language the answer takes.
    asked = (answer_lang or "").strip().lower() or None
    lang = asked or (str(result.get("query_lang") or "").strip().lower() or None)
    if lang:
        from . import llm            # it imports this module's neighbours: late
        name = llm.language_phrase(lang)
        system += (f" Write your whole answer in {name}, whatever language the question or the sources "
                   f"are in." if asked else
                   f" The question is written in {name}: write your whole answer in that language, "
                   f"whatever language the sources are in.")
        after = f"Write the answer in {name}, not in the language of the sources."
    else:
        after = "Write the answer in the language of the question above, not in the language of the sources."
    system += THINK_OFF
    # after the sources and the question; the citation reminder stays the very
    # last thing the model reads (2026-10-13: without it last, it stopped citing)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Sources:\n\n{context}\n\nQuestion: {query}\n\n{after} {CITE_REMINDER}"},
    ]
