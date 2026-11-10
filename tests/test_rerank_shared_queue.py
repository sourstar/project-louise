"""Lists that share a reranker are reranked one after another, each with its
whole budget; different rerankers at once (München, 2026-11-10: five
collections on louise-rerank-bge, wikipedia's list queued past its 9.6 s
budget and went unreranked)."""
import threading
import time
from types import SimpleNamespace

from app import collections as collections_mod, retrieval

BGE = collections_mod.RerankRecipe(url="http://ai/v1/rerank", api="jina", model="bge")
MEDCPT = collections_mod.RerankRecipe(url="http://ai/v1/rerank", api="jina", model="medcpt")


def test_lists_sharing_a_reranker_take_turns_and_other_rerankers_run_beside(monkeypatch):
    cols = {n: SimpleNamespace(name=n, reranker=r) for n, r in
            (("gutenberg", BGE), ("pubmed", MEDCPT), ("wikidata", BGE), ("wikipedia", BGE))}
    monkeypatch.setattr(collections_mod, "reranker_for", lambda c: c.reranker)
    spans, lock = {}, threading.Lock()

    def rerank_one(item):
        c, hits = item
        t0 = time.monotonic()
        time.sleep(0.15)
        with lock:
            spans[c.name] = (t0, time.monotonic())
        return [f"{c.name}:{h}" for h in hits], True, None
    per = [(cols[n], [1, 2]) for n in ("gutenberg", "pubmed", "wikidata", "wikipedia")]
    out = retrieval._rerank_lists(per, rerank_one)
    assert [o[0][0] for o in out] == ["gutenberg:1", "pubmed:1", "wikidata:1", "wikipedia:1"], "in order"
    bge = sorted(spans[n] for n in ("gutenberg", "wikidata", "wikipedia"))
    assert all(a[1] <= b[0] + 0.01 for a, b in zip(bge, bge[1:])), "bge's lists one after the other"
    g, p = spans["gutenberg"], spans["pubmed"]
    assert p[0] < g[1] and g[0] < p[1], "medcpt's list beside bge's"
