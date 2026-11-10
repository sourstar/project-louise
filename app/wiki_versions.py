"""WIKIPEDIA: ONE VERSION PER ARTICLE IN SEARCH (operator, 2026-11-10;
docs/notes/wikipedia-one-version.md). Wikipedia search follows the
smart-embedding guidelines (docs/notes/wikipedia-smart-embedding.md): each
article — a Wikidata item — appears ONCE, in its version shown: English by
default; another language's version when it is BIGGER ("some entries are
specific to a nation, and the version in that nation's language is often
bigger and more accurate"). The operator chose A: the shown version, with
the passage of it that best matches the question.

THE MAP (`build`, the worker's scan thread beside the smart-embedding pass):
every document an item names, by native id → its item; every item → its
members, the shown one first. Written as .npy files under LOUISE_CACHE_DIR
(app/derived.py, invariant 6: derived, re-creatable, random-access), keyed on
the manifest's physical path, with the signature it was made for; the web
memory-maps it (`load`), so the ~1 GB the pass holds never enters it.

THE COLLAPSE (`one_version`, after the reranker, before the cut — 05 §5.3's
"each work once" step): the hits of one item keep one place, their best
rank; the head is the shown version — the hit itself when it matched, else
the shown version's passage that best matches the question (a text search
inside that one article, the `keys` filter), else its first passage. The
other versions found are listed under it (`editions`). A hit no item names,
or a map not built yet, is left as ranked: a guess about what an article IS
never costs a reader a hit.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from . import derived

KIND = "wikipedia-versions"
FILES = ("doc_nid", "doc_qid", "item_qid", "item_start", "mem_nid", "mem_tokens")
_ENGLISH = 0                        # app/plugins/wikipedia/ids.py LANG_ORDINAL["en"]
_UNKNOWN_FIRST = np.iinfo(np.int64).max


def shown_order(qid: np.ndarray, lang: np.ndarray, tokens: np.ndarray, nid: np.ndarray) -> np.ndarray:
    """The members of every item, grouped by item, THE SHOWN ONE FIRST: the
    largest by `tokens`; English on a tie (its ordinal is 0) and when its
    own length is not known yet; then the lower native id (the same data
    gives the same choice)."""
    eff = np.where((lang == _ENGLISH) & (tokens <= 0), _UNKNOWN_FIRST, tokens)
    return np.lexsort((nid, lang, -eff, qid))


def arrays(qid: np.ndarray, lang: np.ndarray, tokens: np.ndarray, nid: np.ndarray) -> dict[str, np.ndarray]:
    """The map's arrays from parallel per-document arrays (`qid` 0: none)."""
    keep = qid != 0
    qid, lang, tokens, nid = qid[keep], lang[keep], tokens[keep], nid[keep]
    order = shown_order(qid, lang, tokens, nid)
    mq = qid[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = mq[1:] != mq[:-1]
    by_doc = np.argsort(nid, kind="stable")
    return {"doc_nid": nid[by_doc].astype(np.int64), "doc_qid": qid[by_doc].astype(np.int64),
            "item_qid": mq[first].astype(np.int64),
            "item_start": np.append(np.nonzero(first)[0], len(order)).astype(np.int64),
            "mem_nid": nid[order].astype(np.int64), "mem_tokens": tokens[order].astype(np.int64)}


def place(collection) -> Path:
    where = derived.place(KIND, Path(collection.manifest_path))
    return where if where is not None else Path(collection.manifest_path).parent / KIND


def build(collection, con, *, signature: str, qids_for: Callable | None = None) -> dict[str, Any]:
    """Write the map for `collection` if the one held was made for another
    `signature` (the smart pass's: the manifest's last row, the page_props
    run). Returns what it did."""
    from . import embed_select
    dest = place(collection)
    meta_path = dest / "meta.json"
    try:
        if json.loads(meta_path.read_text()).get("signature") == signature:
            return {"skipped": "up to date"}
    except (OSError, ValueError):
        pass
    m = embed_select.read_manifest(con)
    ok = m["native_id"] >= 0
    lang, _page = embed_select.decode(np.where(ok, m["native_id"], 0))
    lang = np.where(ok, lang, -1)
    langs = sorted(int(x) for x in np.unique(lang[lang >= 0]))
    by_lang = (qids_for or embed_select.load_qids)(collection, langs)
    qid = np.zeros(len(lang), dtype=np.int64)
    for o, (pages_of, qids_of) in by_lang.items():
        at = np.nonzero(lang == o)[0]
        qid[at] = embed_select.lookup(pages_of, qids_of, _page[at])
    got = arrays(np.where(ok, qid, 0), lang, m["tokens"], m["native_id"])
    with derived.building(dest / "map"):
        dest.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            tmp = dest / f"{name}.tmp.npy"
            np.save(tmp, got[name])
            tmp.replace(dest / f"{name}.npy")
        meta = {"signature": signature, "documents": int(len(qid)),
                "items": int(len(got["item_qid"])), "with_item": int(len(got["doc_nid"]))}
        tmp = dest / "meta.json.tmp"
        tmp.write_text(json.dumps(meta))
        tmp.replace(meta_path)
    with _lock:
        _maps.pop(str(dest), None)
    return meta


# ---- the web ---------------------------------------------------------------------

_lock = threading.Lock()
#: place -> (meta.json mtime_ns, the arrays, memory-mapped)
_maps: dict[str, tuple[int, dict[str, np.ndarray]]] = {}


def load(collection) -> dict[str, np.ndarray] | None:
    """The map, memory-mapped; None when it is not built. Re-read when its
    meta.json changes (a rebuild replaces the files)."""
    dest = place(collection)
    try:
        stamp = (dest / "meta.json").stat().st_mtime_ns
    except OSError:
        return None
    key = str(dest)
    with _lock:
        hit = _maps.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    try:
        got = {name: np.load(dest / f"{name}.npy", mmap_mode="r") for name in FILES}
    except (OSError, ValueError):
        return None
    with _lock:
        _maps[key] = (stamp, got)
    return got


def _nid(hit: dict[str, Any]) -> int | None:
    from .retrieval import record_of
    try:
        return int(record_of(hit).split(":", 1)[1])
    except (KeyError, IndexError, ValueError):
        return None


def _item_of(m: dict[str, np.ndarray], nid: int) -> int | None:
    docs = m["doc_nid"]
    at = int(np.searchsorted(docs, nid))
    if at < len(docs) and int(docs[at]) == nid:
        return int(m["doc_qid"][at])
    return None


def _members(m: dict[str, np.ndarray], qid: int) -> list[int]:
    items = m["item_qid"]
    at = int(np.searchsorted(items, qid))
    if at >= len(items) or int(items[at]) != qid:
        return []
    lo, hi = int(m["item_start"][at]), int(m["item_start"][at + 1])
    return [int(x) for x in m["mem_nid"][lo:hi]]


def _lang_of(nid: int) -> str:
    from .plugins.wikipedia import ids
    return ids.decode_native_id(nid)[0]


def best_passage(collection, plan, nid: int) -> dict[str, Any] | None:
    """The shown version's passage that best matches the question: a text
    search inside that one article (the `keys` filter), with the plan's
    variant for its language; else its first passage; None if it has none
    held."""
    from . import fts
    lang = _lang_of(nid)
    group = fts.group_for(lang)
    try:
        queries = plan.variants(group, [lang]) if plan is not None else []
    except Exception:   # noqa: BLE001 — the query as typed then
        queries = []
    for q in queries or [getattr(plan, "core", "")]:
        if not q:
            continue
        try:
            got = fts.search_records(group, q, 1, collection=collection, book_ids=[nid])
        except Exception:   # noqa: BLE001 — the first passage then
            got = []
        if got:
            return got[0]
    try:
        chunks = fts.book_chunks(group, nid, collection)
    except Exception:   # noqa: BLE001
        return None
    if not chunks:
        return None
    return min(chunks, key=lambda c: _seq(c.get("chunk_id")))


def _seq(chunk_id: Any) -> int:
    try:
        return int(str(chunk_id).rsplit(":", 2)[1])
    except (IndexError, ValueError):
        return 1 << 30


#: the fields a passage brings to the hit it replaces; the rest (its rank,
#: scores, the reranker's verdict) stays the hit's
_PASSAGE = ("chunk_id", "book_id", "text", "chapter", "lang", "source")


def one_version(hits: Sequence[dict[str, Any]], collection, plan,
                passage: Callable[[Any, Any, int], dict[str, Any] | None] = best_passage
                ) -> list[dict[str, Any]]:
    """`hits` in ranked order: each article once, at its best rank, in its
    shown version (the module's docstring). Never raises: a map that
    cannot be read leaves the list as ranked."""
    try:
        m = load(collection)
    except Exception:   # noqa: BLE001
        m = None
    if m is None or not hits:
        return list(hits)
    item_of: list[int | None] = []
    for h in hits:
        nid = _nid(h)
        item_of.append(_item_of(m, nid) if nid is not None else None)
    found: dict[int, list[int]] = {}
    for i, q in enumerate(item_of):
        if q:
            found.setdefault(q, []).append(i)
    out: list[dict[str, Any]] = []
    for i, h in enumerate(hits):
        q = item_of[i]
        if not q:
            out.append(h)
            continue
        at = found[q]
        if at[0] != i:
            continue                       # a later version of an article already shown
        members = _members(m, q)
        shown = members[0] if members else _nid(h)
        matched = {_nid(hits[j]): j for j in at}
        if shown in matched:
            # the article's place and scores are its best rank's (the merge
            # across collections reads them); the passage is the shown one's
            head = dict(h)
            mine = hits[matched[shown]]
            head.update({k: mine[k] for k in _PASSAGE if k in mine})
            head_was = "matched"
        else:
            p = None
            try:
                p = passage(collection, plan, shown)
            except Exception:   # noqa: BLE001 — the version that matched then
                p = None
            head = dict(h)
            if p:
                head.update({k: p[k] for k in _PASSAGE if k in p})
                head_was = "shown version, its best passage"
            else:
                head_was = "matched (the shown version holds no passage here)"
        head["version_shown"] = head_was
        rank_of = {_nid(hits[j]): (j + 1, hits[j].get("chunk_id")) for j in at}
        editions = []
        for nid in members:
            if nid == _nid(head):
                continue
            rank, chunk = rank_of.get(nid, (None, None))
            editions.append({"uid": f"{collection.plugin}:{nid}", "title": None, "lang": _lang_of(nid),
                             "held": True, "rank": rank, "chunk_id": chunk})
        editions.sort(key=lambda e: (e["rank"] is None, e["rank"] or 0, e["uid"]))
        head["editions"] = editions
        out.append(head)
    return out
