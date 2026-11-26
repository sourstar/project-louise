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


def test_a_search_nobody_waits_for_stops_between_two_lists_of_one_reranker(monkeypatch):
    # München, 2026-11-25: a search replaced by a second one went on for 9 s inside the rerank
    # of its lists, and the second queued behind it on the same reranker (`parallel:1`)
    import pytest
    from app import cancel
    cols = [SimpleNamespace(name=n, reranker=BGE) for n in ("gutenberg", "wikidata", "wikipedia", "eurlex")]
    monkeypatch.setattr(collections_mod, "reranker_for", lambda c: c.reranker)
    token, called = cancel.Token(), []

    def rerank_one(item):
        called.append(item[0].name)
        if item[0].name == "wikidata":
            token.cancel()              # the caller leaves while the second list is at the reranker
        return list(item[1]), True, None
    with pytest.raises(cancel.Cancelled):
        cancel.run(token, retrieval._rerank_lists, [(c, [1]) for c in cols], rerank_one)
    assert called == ["gutenberg", "wikidata"], "the call in flight ends; the third and fourth are never made"


def test_with_its_caller_there_every_list_is_reranked(monkeypatch):
    from app import cancel
    cols = [SimpleNamespace(name=n, reranker=BGE) for n in ("gutenberg", "wikidata", "wikipedia")]
    monkeypatch.setattr(collections_mod, "reranker_for", lambda c: c.reranker)
    token = cancel.Token()
    out = cancel.run(token, retrieval._rerank_lists, [(c, [c.name]) for c in cols],
                     lambda item: (list(item[1]), True, None))
    assert [o[0] for o in out] == [["gutenberg"], ["wikidata"], ["wikipedia"]]
    assert token.where == "rerank_in_turn", "the page's 'no progress' line names where the search is"


def test_a_lists_works_are_grouped_off_the_rerankers_turn(monkeypatch):
    """The node, 2026-11-26, a first question: "rerank 39,324", six lists one
    after the other, wikipedia's works alone 18.8 s — with the reranker idle
    behind them and four lists waiting for it. What a list does after its
    reranker answered runs beside the next list's rerank; the results come
    back in the lists' order, each its own."""
    cols = [SimpleNamespace(name=n, reranker=BGE) for n in ("wikipedia", "gutenberg", "wikidata")]
    monkeypatch.setattr(collections_mod, "reranker_for", lambda c: c.reranker)
    spans, lock = {}, threading.Lock()

    def mark(what: str, name: str, t0: float) -> None:
        with lock:
            spans[(what, name)] = (t0, time.monotonic())

    def rerank_one(item):
        c, hits = item
        t0 = time.monotonic()
        time.sleep(0.05)                                   # the reranker's answer
        mark("rerank", c.name, t0)

        def group_works():
            t1 = time.monotonic()
            time.sleep(0.4 if c.name == "wikipedia" else 0.01)   # the slow one first
            mark("works", c.name, t1)
            return [f"{c.name}:{h}" for h in hits], True, None
        return group_works
    t0 = time.monotonic()
    out = retrieval._rerank_lists([(c, [1]) for c in cols], rerank_one)
    took = time.monotonic() - t0
    assert [o[0] for o in out] == [["wikipedia:1"], ["gutenberg:1"], ["wikidata:1"]], "each list its own, in order"
    reranks = [spans[("rerank", c.name)] for c in cols]
    assert all(a[1] <= b[0] + 0.01 for a, b in zip(reranks, reranks[1:])), "the reranker still takes its lists in turn"
    assert spans[("rerank", "wikidata")][1] < spans[("works", "wikipedia")][1], \
        "the third list was judged while the first one's works were still being grouped"
    assert took < 0.4 + 0.05 + 0.15, f"the slowest works plus one rerank, not the sum ({took:.2f} s)"


def test_a_works_step_that_fails_is_the_searchs_failure_as_before(monkeypatch):
    import pytest
    cols = [SimpleNamespace(name=n, reranker=BGE) for n in ("a", "b")]
    monkeypatch.setattr(collections_mod, "reranker_for", lambda c: c.reranker)

    def rerank_one(item):
        def group_works():
            if item[0].name == "b":
                raise RuntimeError("the claim index could not be read")
            return list(item[1]), True, None
        return group_works
    with pytest.raises(RuntimeError, match="claim index"):
        retrieval._rerank_lists([(c, [1]) for c in cols], rerank_one)
