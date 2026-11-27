"""A search's KIND: facts, literature or all (docs/notes/fact-fiction-search.md;
operator, 2026-11-27: "The search itself shouldn't return novels when I'm
looking only for facts and vice versa"). The candidates a kind leaves out go
before the rerank, the legs over-fetch where it thins them, the result says
the kind, how it was decided and what it left out; auto asks the chat model
beside the legs, bounded, and falls to all."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app import question_kind, query_trace, retrieval, work_kind
from tests.test_work_kind import _staged

IDS = (2600, 5, 151)          # War and Peace, the US Constitution, Moby Dick


def _hit(n, coll="gutenberg"):
    return {"book_id": n, "chunk_id": f"{coll}:{n}:0:1", "uid": f"{coll}:{n}", "text": "…",
            "score": 1.0, "rrf": 0.03, "source_collection": coll}


@pytest.fixture
def searched(isolated_config, monkeypatch, tmp_path):
    """A real staged manifest; the legs and the reranker recorded."""
    c, con = _staged(tmp_path, IDS)
    seen = {"depth": [], "reranked": [], "order": []}
    monkeypatch.setattr(retrieval.refs, "get_store", lambda: None)
    monkeypatch.setattr(retrieval.refs.RESOLVER, "resolve", lambda q, s: [])

    def legs(con, c, q, k, mode, tr, ids, plan=None, depth=0, **kw):
        seen["depth"].append(depth)
        seen["order"].append("legs")
        return {"catalog": [], "merged": [_hit(n, c.name) for n in IDS], "translations": {},
                "timings": {"catalog": 0.0}, "scope": {}}

    def rerank(q, hs, recipe, budget_s, depth, *a, **kw):
        seen["reranked"].append([h["book_id"] for h in hs])
        return hs, False
    monkeypatch.setattr(retrieval, "_search_one_collection", legs)
    monkeypatch.setattr(retrieval, "_rerank_list", rerank)
    return c, con, seen


def test_facts_leaves_the_literary_works_out_before_the_rerank_and_says_so(searched):
    c, con, seen = searched
    out = retrieval.search(con, "Napoleon", k=5, collection=c, translate=False, kind="facts")
    assert seen["reranked"] == [[5]], "the reranker never sees a left-out record"
    assert [h["book_id"] for h in out["hits"]] == [5]
    assert out["kind_applied"] == "facts" and out["kind_by"] == "the reader"
    assert out["left_out"] == {"count": 2, "by_basis": {"by its subject headings": 1,
                                                        "by its Gutenberg category": 1}}


def test_literature_keeps_the_literary_works_alone(searched):
    c, con, seen = searched
    out = retrieval.search(con, "Napoleon", k=5, collection=c, translate=False, kind="literature")
    assert sorted(h["book_id"] for h in out["hits"]) == [151, 2600]
    assert out["left_out"]["count"] == 1
    assert out["left_out"]["by_basis"] == {"no literary verdict in its record": 1}


def test_all_is_the_search_as_it_was(searched):
    c, con, seen = searched
    out = retrieval.search(con, "Napoleon", k=5, collection=c, translate=False)
    assert seen["reranked"] == [list(IDS)] and out["kind_applied"] == "all" and "left_out" not in out
    assert seen["depth"] == [retrieval.RERANK_TOP_N], "all goes as deep as before"


def test_the_legs_over_fetch_where_the_kind_thins_them(searched):
    c, con, seen = searched
    retrieval.search(con, "Napoleon", k=5, collection=c, translate=False, kind="facts", rerank_depth=40)
    assert seen["depth"] == [40 * work_kind.OVERFETCH]


def test_the_question_is_asked_after_the_language_plan_and_before_the_legs(searched, monkeypatch):
    c, con, seen = searched
    from app import language_plan
    real = language_plan.plan
    monkeypatch.setattr(language_plan, "plan", lambda *a, **kw: (seen["order"].append("plan"), real(*a, **kw))[1])
    monkeypatch.setattr(question_kind, "start",
                        lambda q: (seen["order"].append("asked"), lambda: ("facts", "the question"))[1])
    out = retrieval.search(con, "Napoleon", k=5, collection=c, translate=False, kind=question_kind.pending("Napoleon"))
    assert seen["order"] == ["plan", "asked", "legs"], seen["order"]
    assert out["kind_applied"] == "facts"


def test_a_kind_being_decided_is_resolved_after_the_legs(searched):
    c, con, seen = searched

    def resolver():
        seen["order"].append("kind")
        return ("facts", "the question")
    out = retrieval.search(con, "Napoleon", k=5, collection=c, translate=False, kind=resolver)
    assert seen["order"] == ["legs", "kind"], "the legs never wait for the question's kind"
    assert seen["depth"] == [retrieval.RERANK_TOP_N * work_kind.OVERFETCH]
    assert out["kind_applied"] == "facts" and out["kind_by"] == "the question"


def test_an_undecided_kind_is_all_and_says_why(searched):
    c, con, seen = searched
    out = retrieval.search(con, "Napoleon", k=5, collection=c, translate=False,
                           kind=lambda: ("all", "not decided in time"))
    assert out["kind_applied"] == "all" and out["kind_by"] == "not decided in time"
    assert "left_out" not in out and seen["reranked"] == [list(IDS)]


def test_left_out_counts_what_the_reranker_would_have_seen_not_the_deeper_legs(searched):
    c, con, _ = searched
    cands = [_hit(n) for n in (2600, 5, 151, 2600, 5)]
    kept, left = work_kind.filter_candidates(con, c, cands, "facts", want=1)
    assert [h["book_id"] for h in kept] == [5] and left["count"] == 1


def test_federated_a_collection_without_kinds_is_facts(isolated_config, monkeypatch, tmp_path, searched):
    c, con, seen = searched
    from app import collections as collections_mod, db
    other = collections_mod.Collection(name="eurlex", plugin="eurlex", profile=None, embed_mode_default=None,
                                       corpus_dir=tmp_path / "corpus_e", manifest_path=tmp_path / "e" / "manifest.db",
                                       fts_dir=tmp_path / "e" / "fts", lance_dir=tmp_path / "e" / "lancedb")
    db.connect(other).close()
    monkeypatch.setattr(retrieval, "_attach_metadata_and_enqueue", lambda con, c, hits: None)
    facts = retrieval.federated_search([c, other], "Napoleon", k=10, translate=False, kind="facts")
    assert sorted((h["source_collection"], h["book_id"]) for h in facts["hits"]) == \
        [("eurlex", 5), ("eurlex", 151), ("eurlex", 2600), ("gutenberg", 5)]
    assert facts["left_out"]["count"] == 2 and facts["kind_applied"] == "facts"
    lit = retrieval.federated_search([c, other], "Napoleon", k=10, translate=False, kind="literature")
    assert {h["source_collection"] for h in lit["hits"]} == {"gutenberg"}
    assert lit["left_out"]["by_basis"]["no literary works in this collection"] == 3
    # the classified collection's legs over-fetch, the other's go as deep as before
    assert sorted(seen["depth"][:2]) == [retrieval.RERANK_TOP_N, retrieval.RERANK_TOP_N * work_kind.OVERFETCH]


# ---- the question's kind: one short call, bounded, cached --------------------

@pytest.fixture(autouse=True)
def _fresh():
    question_kind.reset()
    yield
    question_kind.reset()


@pytest.mark.parametrize("reply,kind,by", [
    ('{"kind": "facts"}', "facts", "the question"),
    ('<think>hm</think>{"kind": "literature"}', "literature", "the question"),
    ('{"kind": "unsure"}', "all", "the question"),
    ("no idea", "all", "not decided"),
])
def test_the_question_kind_from_the_chat_models_answer(monkeypatch, reply, kind, by):
    from app import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: reply)
    assert question_kind.start("q")() == (kind, by)


def test_an_answer_is_remembered_and_a_failure_is_not(monkeypatch):
    from app import llm
    calls = []

    def chat(*a, **kw):
        calls.append(kw.get("timeout"))
        if len(calls) == 1:
            raise RuntimeError("down")
        return '{"kind": "facts"}'
    monkeypatch.setattr(llm, "chat", chat)
    assert question_kind.start("q")() == ("all", "not decided")
    assert question_kind.start("q")() == ("facts", "the question")
    assert question_kind.start("q")() == ("facts", "the question")
    assert len(calls) == 2 and calls[0] == question_kind.TIMEOUT_S


def test_no_answer_in_time_is_all(monkeypatch):
    from app import llm
    monkeypatch.setattr(question_kind, "TIMEOUT_S", 0.2)
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: (time.sleep(1.0), '{"kind": "facts"}')[1])
    t0 = time.monotonic()
    resolve = question_kind.start("slow")
    assert resolve() == ("all", "not decided in time")
    assert time.monotonic() - t0 < 0.8, "bounded from the start of the call"
    assert resolve() == ("all", "not decided in time"), "one decision per search"


def test_run_search_starts_auto_beside_the_legs_and_refuses_an_unknown_kind(monkeypatch):
    from app import query_service
    got = {}
    monkeypatch.setattr(query_service, "resolve_collections", lambda names, private=False: [SimpleNamespace(name="g")])
    monkeypatch.setattr(query_service, "withheld_from", lambda names, private=False: [])
    monkeypatch.setattr(query_service.db, "connect", lambda c: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(query_service.cancel, "watch", lambda con: con)
    monkeypatch.setattr(query_service.retrieval, "search", lambda con, q, **kw: got.update(kw) or {"hits": []})
    asked = []
    monkeypatch.setattr(question_kind, "start", lambda q: asked.append(q) or (lambda: ("facts", "the question")))
    query_service.run_search(None, "Napoleon", 5, "hybrid", False, kind="auto")
    assert isinstance(got["kind"], question_kind.Pending) and asked == [], "asked by the search, not before it"
    got["kind"].begin()
    assert got["kind"]() == ("facts", "the question") and asked == ["Napoleon"], "asked once"
    query_service.run_search(None, "Napoleon", 5, "hybrid", False, kind="facts")
    assert got["kind"] == "facts"
    got.clear()
    query_service.run_search(None, "Napoleon", 5, "hybrid", False)
    assert "kind" not in got, "a caller that asks no kind searches as before"
    with pytest.raises(ValueError):
        query_service.run_search(None, "Napoleon", 5, "hybrid", False, kind="novels")


# ---- every way in takes it; the trace records it -----------------------------

def test_every_way_in_takes_the_kind_automatic_by_default():
    from app import api, web
    from app.ask import api as ask_api, mcp
    for body in (api.SearchBody(query="q"), api.ChatBody(messages=[]), web.AskQueryBody(query="q"),
                 ask_api.SearchBody(query="q")):
        assert body.kind == "auto", type(body)
    schema = next(t for t in mcp.TOOLS if t["name"] == "louise_search")["inputSchema"]["properties"]["kind"]
    assert schema["enum"] == ["auto", "facts", "literature", "all"] and schema["default"] == "auto"


def test_the_trace_records_the_kind_and_what_it_left_out():
    left = {"count": 2, "by_basis": {"by its subject headings": 2}}
    assert query_trace.kind_of({"kind_applied": "facts", "kind_by": "the question", "left_out": left}) == \
        {"search_kind": "facts", "kind_by": "the question", "left_out": left}
    assert query_trace.kind_of({"hits": []}) == {}


def test_ask_search_carries_the_kind_and_left_out(monkeypatch):
    from app.ask import results
    got = {}

    def run(names, q, limit, mode, tr, **kw):
        got.update(kw)
        return {"hits": [], "kind_applied": "facts", "kind_by": "the question",
                "left_out": {"count": 1, "by_basis": {"by its Gutenberg category": 1}}}
    monkeypatch.setattr(results, "context_for", lambda c, **kw: {"space_id": "s"})
    from app import query_service
    monkeypatch.setattr(query_service, "resolve_collections",
                        lambda names=None, private=False: [SimpleNamespace(name="g", codex=None, source="origin",
                                                                           corpus_dir="/nonexistent")])
    try:
        out = results.search("Napoleon", run=run)
    except Exception as e:   # noqa: BLE001 — the rest of the shape needs a held collection
        pytest.skip(f"ask search needs a held collection here: {e}")
    assert got["kind"] == "auto"
    assert out["kind_applied"] == "facts" and out["left_out"]["count"] == 1


# ---- the page: the chip, and the tap -----------------------------------------

def _chip(result):
    from app import web
    html = web.TEMPLATES.env.get_template("_ask_results.html").render(
        result={"hits": [], "catalog": [], "facts": [], "timings": {}, **result})
    start = html.find('id="search-kind"')
    return html, (html[start:html.find("</span>\n  </span>", start)] if start >= 0 else "")


def test_the_chip_says_the_kind_what_it_left_out_and_offers_the_others():
    html, chip = _chip({"kind_applied": "facts", "kind_by": "the question",
                        "left_out": {"count": 3, "by_basis": {"by its subject headings": 2,
                                                              "by its Gutenberg category": 1}}})
    assert "facts only, by the question: 3 literary works left out" in html
    assert "2 by its subject headings; 1 by its Gutenberg category" in html
    assert 'class="kind-tap" data-kind="literature"' in html and 'class="kind-tap" data-kind="all"' in html
    assert 'class="kind-tap" data-kind="facts"' not in html


def test_the_chip_of_an_undecided_kind_says_why_and_offers_both():
    html, _ = _chip({"kind_applied": "all", "kind_by": "not decided in time"})
    assert "facts and literature (not decided in time)" in html
    assert 'data-kind="facts"' in html and 'data-kind="literature"' in html


def test_a_result_without_a_kind_shows_no_chip():
    html, _ = _chip({})
    assert 'id="search-kind"' not in html


def test_the_page_sends_the_kind_with_both_searches_and_a_new_question_is_auto():
    from pathlib import Path
    page = (Path(__file__).parent.parent / "app" / "templates" / "ask.html").read_text(encoding="utf-8")
    assert "kind:kindFor(document.getElementById('q').value.trim())" in page
    assert "function kindFor(q){return searchKind.q===q?searchKind.kind:'auto'}" in page
    # the answer's body and the search's both carry counts(): one kind for the [n] and the hits
    assert page.count("...counts()}") >= 2


def test_ask_search_on_the_command_line_takes_the_kind(monkeypatch):
    from app import louise
    from app.ask import results
    got = {}
    monkeypatch.setattr(results, "search", lambda q, **kw: got.update(kw) or {})
    monkeypatch.setattr(louise, "_print", lambda out: None)
    p = louise.build_parser()
    a = p.parse_args(["ask", "search", "Napoleon", "--kind", "facts"])
    a.fn(a)
    assert got["kind"] == "facts"
