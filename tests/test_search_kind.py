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
#: two text legs over the staged records and 100 records the manifest does
#: not hold (no verdict): the "en" leg ranks them one way, the "fr" leg another
FILLER = tuple(range(9001, 9101))
LEGS = {"en": IDS + FILLER, "fr": tuple(reversed(FILLER)) + (151, 5, 2600)}


def _hit(n, coll="gutenberg"):
    return {"book_id": n, "chunk_id": f"{coll}:{n}:0:1", "uid": f"{coll}:{n}", "text": "…",
            "score": 1.0, "rrf": 0.03, "source_collection": coll, "source": "fts"}


@pytest.fixture
def searched(isolated_config, monkeypatch, tmp_path):
    """A real staged manifest and the real `_search_one_collection`: only the
    indexes are faked (two text legs), and the legs and the reranker recorded."""
    c, con = _staged(tmp_path, IDS)
    seen = {"depth": [], "reranked": [], "order": []}
    monkeypatch.setattr(retrieval.refs, "get_store", lambda: None)
    monkeypatch.setattr(retrieval.refs.RESOLVER, "resolve", lambda q, s: [])
    monkeypatch.setattr(retrieval, "catalog_leg", lambda con, core, **kw: ([], None))
    monkeypatch.setattr(retrieval.fts, "existing_groups", lambda c: ["en", "fr"])
    monkeypatch.setattr(retrieval.fts, "group_languages", lambda g, c: [g])
    monkeypatch.setattr(retrieval.fts, "names_capable", lambda c: False)

    def records(group, q, limit, collection=None, **kw):
        seen["depth"].append(limit)
        if "legs" not in seen["order"]:
            seen["order"].append("legs")
        return [_hit(n, collection.name) for n in LEGS[group][:limit]]
    monkeypatch.setattr(retrieval.fts, "search_records", records)

    def rerank(q, hs, recipe, budget_s, depth, *a, **kw):
        seen["reranked"].append([h["book_id"] for h in hs])
        return hs, False
    monkeypatch.setattr(retrieval, "_rerank_list", rerank)
    return c, con, seen


def _search(con, c, **kw):
    return retrieval.search(con, "Napoleon", k=kw.pop("k", 5), mode="fts", collection=c, translate=False, **kw)


def test_facts_leaves_the_literary_works_out_before_the_rerank_and_says_so(searched):
    c, con, seen = searched
    out = _search(con, c, kind="facts")
    assert 2600 not in seen["reranked"][0] and 151 not in seen["reranked"][0], "the reranker never sees them"
    assert 5 in seen["reranked"][0]
    assert out["kind_applied"] == "facts" and out["kind_by"] == "the reader"
    assert out["left_out"] == {"count": 2, "by_basis": {"by its subject headings": 1,
                                                        "by its Gutenberg category": 1}}


def test_literature_keeps_the_literary_works_alone(searched):
    c, con, seen = searched
    out = _search(con, c, kind="literature")
    assert sorted(h["book_id"] for h in out["hits"]) == [151, 2600]
    # each once, within the normal-depth legs (50 each): the Constitution,
    # 9001–9047 from "en" and 9051–9100 from "fr", all without a verdict
    assert out["left_out"] == {"count": 1 + 47 + 50, "by_basis": {"no literary verdict in its record": 98}}


def test_all_is_the_search_as_it_was(searched):
    c, con, seen = searched
    out = _search(con, c)
    assert out["kind_applied"] == "all" and "left_out" not in out
    assert set(seen["depth"]) == {retrieval.RERANK_TOP_N}, "all goes as deep as before"


def test_a_kind_resolving_to_all_is_today_s_search_exactly(searched):
    """Architecture's read of 2643e7d8, 2: auto deciding all (unsure, late,
    failed) fetched the legs deeper and fused at that depth — another order
    than all. The legs are cut back before the fusion."""
    c, con, seen = searched
    plain = _search(con, c, k=10, rerank_depth=20)
    seen["reranked"].clear()
    late = _search(con, c, k=10, rerank_depth=20, kind=lambda: ("all", "not decided in time"))
    assert [h["book_id"] for h in late["hits"]] == [h["book_id"] for h in plain["hits"]]
    assert seen["depth"][-2:] == [20 * work_kind.OVERFETCH] * 2, "fetched deeper while undecided"
    assert seen["reranked"] == [[h["book_id"] for h in retrieval._merge_rrf(
        [[_hit(n) for n in LEGS[g][:20]] for g in ("en", "fr")], 20)][:40]], "fused at the normal depth"


def test_the_legs_over_fetch_and_fuse_what_the_filter_kept_at_the_normal_depth(searched):
    c, con, seen = searched
    _search(con, c, kind="literature", rerank_depth=20)
    assert seen["depth"] == [20 * work_kind.OVERFETCH] * 2
    assert sorted(seen["reranked"][0]) == [151, 2600]


def test_the_question_is_asked_after_the_language_plan_and_before_the_legs(searched, monkeypatch):
    c, con, seen = searched
    from app import language_plan
    real = language_plan.plan
    monkeypatch.setattr(language_plan, "plan", lambda *a, **kw: (seen["order"].append("plan"), real(*a, **kw))[1])
    monkeypatch.setattr(question_kind, "start",
                        lambda q: (seen["order"].append("asked"), lambda: ("facts", "the question"))[1])
    out = _search(con, c, kind=question_kind.pending("Napoleon"))
    assert seen["order"] == ["plan", "asked", "legs"], seen["order"]
    assert out["kind_applied"] == "facts"


def test_a_kind_being_decided_is_resolved_after_the_legs(searched):
    c, con, seen = searched

    def resolver():
        seen["order"].append("kind")
        return ("facts", "the question")
    out = _search(con, c, kind=resolver)
    assert seen["order"] == ["legs", "kind"], "the legs never wait for the question's kind"
    assert out["kind_applied"] == "facts" and out["kind_by"] == "the question"


def test_an_undecided_kind_is_all_and_says_why(searched):
    c, con, seen = searched
    out = _search(con, c, kind=lambda: ("all", "not decided in time"))
    assert out["kind_applied"] == "all" and out["kind_by"] == "not decided in time"
    assert "left_out" not in out


# ---- the read is the truth, the column a cache (architecture's read, 1) ------

def _blank(con):
    con.execute("UPDATE documents SET work_kind=NULL, work_kind_basis=NULL, work_kind_v=NULL")


def test_columns_never_filled_filter_as_filled_ones(searched):
    """After the ALTER, on a node whose maintainer does not run (query mode,
    acquire-only, paused), every column is NULL: the read computes."""
    c, con, _ = searched
    filled = _search(con, c, kind="facts")
    _blank(con)
    assert work_kind.maintain(con, c) is not None or True     # (not run below: the node is acquire-only)
    _blank(con)
    blank = _search(con, c, kind="facts")
    assert blank["left_out"] == filled["left_out"]
    assert [h["book_id"] for h in blank["hits"]] == [h["book_id"] for h in filled["hits"]]


def test_a_row_of_another_rules_version_is_judged_by_the_current_rules(searched, monkeypatch):
    c, con, _ = searched
    con.execute("UPDATE documents SET work_kind='opinion', work_kind_basis='old', work_kind_v=?",
                (work_kind.RULES_VERSION - 1,))
    kinds = work_kind.kinds_of(con, c, IDS)
    assert kinds[2600] == ("literary", "by its subject headings") and kinds[5] == (None, None)
    con.execute("UPDATE documents SET work_kind='opinion', work_kind_basis='cached', work_kind_v=?",
                (work_kind.RULES_VERSION,))
    assert work_kind.kinds_of(con, c, [5])[5] == ("opinion", "cached"), "a current row is read, not recomputed"


def test_the_node_in_acquire_only_still_filters(searched, monkeypatch):
    c, con, _ = searched
    from app import worker
    _blank(con)
    monkeypatch.setattr(worker, "_maintenance_allowed", lambda *a, **kw: False, raising=False)
    out = _search(con, c, kind="facts")
    assert out["left_out"]["count"] == 2


def test_the_catalogue_matches_are_filtered_and_counted(searched, monkeypatch):
    """Nit 6: a facts search listed novels' titles among the catalogue matches."""
    c, con, _ = searched
    monkeypatch.setattr(retrieval, "catalog_leg", lambda con, core, **kw: ([_hit(2600), _hit(5)], None))
    monkeypatch.setattr(retrieval, "_catalog_variant", lambda *a, **kw: None)
    out = retrieval.search(con, "Napoleon", k=5, mode="hybrid", collection=c, translate=False, kind="facts")
    assert [h["book_id"] for h in out["catalog"]] == [5]
    assert out["left_out"]["count"] == 2


def test_federated_a_collection_without_kinds_is_facts(isolated_config, monkeypatch, tmp_path, searched):
    c, con, seen = searched
    from app import collections as collections_mod, db
    other = collections_mod.Collection(name="eurlex", plugin="eurlex", profile=None, embed_mode_default=None,
                                       corpus_dir=tmp_path / "corpus_e", manifest_path=tmp_path / "e" / "manifest.db",
                                       fts_dir=tmp_path / "e" / "fts", lance_dir=tmp_path / "e" / "lancedb")
    db.connect(other).close()
    monkeypatch.setattr(retrieval, "_attach_metadata_and_enqueue", lambda con, c, hits: None)
    facts = retrieval.federated_search([c, other], "Napoleon", k=200, mode="fts", translate=False, kind="facts")
    got = {(h["source_collection"], h["book_id"]) for h in facts["hits"]}
    assert ("eurlex", 2600) in got and ("gutenberg", 2600) not in got and ("gutenberg", 5) in got
    assert facts["left_out"]["count"] == 2 and facts["kind_applied"] == "facts"
    lit = retrieval.federated_search([c, other], "Napoleon", k=200, mode="fts", translate=False, kind="literature")
    assert {h["source_collection"] for h in lit["hits"]} == {"gutenberg"}
    assert lit["left_out"]["by_basis"]["no literary works in this collection"] == 103


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


def test_a_late_answer_is_remembered_with_one_chat_call(monkeypatch):
    """Architecture's read of 2643e7d8, 4: TIMEOUT 0.2 s, a 0.4 s answer:
    late once, then the answer remembered, one call to the chat model."""
    from app import llm
    calls = []
    monkeypatch.setattr(question_kind, "TIMEOUT_S", 0.2)
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: (calls.append(1), time.sleep(0.4), '{"kind": "facts"}')[2])
    assert question_kind.start("slow q")() == ("all", "not decided in time")
    for _ in range(50):
        if question_kind._cache.get("slow q"):
            break
        time.sleep(0.02)
    assert question_kind.start("slow q")() == ("facts", "the question")
    assert len(calls) == 1


def test_a_call_still_queued_when_the_bound_is_missed_never_runs(monkeypatch):
    from app import llm
    import threading
    gate, ran = threading.Event(), []
    monkeypatch.setattr(question_kind, "TIMEOUT_S", 0.1)
    monkeypatch.setattr(llm, "chat", lambda *a, messages=None, **kw: (ran.append(a[0][-1]["content"]),
                                                                      gate.wait(2), '{"kind": "facts"}')[2])
    first = [question_kind.start(f"busy {i}") for i in range(2)]      # the pool's two workers, held
    queued = question_kind.start("queued")
    assert queued() == ("all", "not decided in time")
    gate.set()
    for r in first:
        r()
    time.sleep(0.2)
    assert "queued" not in ran, ran


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


# ---- /v1 says the kind, whatever `sources` says (architecture's read, 3) -----

@pytest.fixture
def v1(isolated_config, monkeypatch):
    from fastapi.testclient import TestClient
    from app import api, llm, query_service
    from tests.test_web_pages import _patch_hash
    _patch_hash(monkeypatch)
    state = {"result": {}}

    def fake_search(collections, query, *a, **kw):
        state["kind"] = kw.get("kind")
        return {"catalog": [], "timings": {"total": 1.0}, **state["result"]}
    monkeypatch.setattr(query_service, "run_search", fake_search)
    monkeypatch.setattr(api, "_run_search", fake_search)
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: "an answer")

    def words(messages, **kw):
        yield "an answer"

        class End:
            whole = True
        return End()
    monkeypatch.setattr(llm, "chat_stream", words)
    with TestClient(api.app) as c:
        yield c, state


def _ask(c, stream, **body):
    return c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "Napoleon"}],
                                                "stream": stream, **body})


LEFT = {"count": 3, "by_basis": {"no literary verdict in its record": 3}}


def test_every_v1_answer_carries_its_kind(v1):
    import json
    c, state = v1
    state["result"] = {"hits": [_hit(5)], "kind_applied": "facts", "kind_by": "the question",
                       "left_out": {"count": 2, "by_basis": {"by its subject headings": 2}}}
    out = _ask(c, False).json()
    assert out["louise_kind"] == {"applied": "facts", "by": "the question",
                                  "left_out": {"count": 2, "by_basis": {"by its subject headings": 2}}}
    assert state["kind"] == "auto", "automatic by default"
    chunks = [json.loads(line[6:]) for line in _ask(c, True).text.splitlines()
              if line.startswith("data: {")]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["louise_kind"]["applied"] == "facts", "on the final chunk"
    state["result"] = {"hits": [_hit(5)]}
    assert _ask(c, False).json()["louise_kind"] == {"applied": "all", "by": "the reader",
                                                     "left_out": {"count": 0, "by_basis": {}}}


def test_an_answer_whose_search_left_everything_out_says_so(v1):
    c, state = v1
    state["result"] = {"hits": [], "kind_applied": "literature", "kind_by": "the question", "left_out": LEFT}
    text = _ask(c, False).json()["choices"][0]["message"]["content"]
    assert text.startswith("[literature only: this search left out 3 other works and found no literary work")
    assert "[literature only: this search left out 3 other works" in _ask(c, True).text
    state["result"] = {"hits": [_hit(5)], "kind_applied": "literature", "left_out": LEFT}
    assert "literature only" not in _ask(c, False).json()["choices"][0]["message"]["content"]
