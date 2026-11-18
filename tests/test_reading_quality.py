"""The historical reader runs under QUALITY only (operator, 2026-11-18:
"Keep the word replacement under quality search only"; architecture: the
one flag is the request's Quality/Speed choice, carried by every path that
reranks or answers; a path with no choice keeps the Ask path's default,
Quality). Under Speed: no year, no table lookup, no copy, no moved_by."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import api, query_service, reading, relay, retrieval, web
from app.ask import chat
from tests.test_relay import _wait
from tests.test_rerank_reading import HITS, RECIPE, reranker  # noqa: F401 — the fixture


def test_the_search_page_carries_the_choice(isolated_config, monkeypatch):
    seen = []
    monkeypatch.setattr(web, "_ask_result", lambda body: seen.append(reading.quality_on()) or {"hits": [], "timings": {}})
    c = TestClient(api.app)
    for speed in (False, True):
        r = c.post("/ask/relay", json={"kind": "search", "search": {"query": "balene", "speed": speed}})
        job = relay.get(r.json()["id"], "guest:None")
        _wait(lambda: job.state == relay.DONE)
    assert seen == [True, False]


@pytest.fixture
def searched(isolated_config, monkeypatch):
    from tests.test_web_pages import _patch_hash
    _patch_hash(monkeypatch)
    seen = []

    def fake(collections, query, *a, **kw):
        seen.append(reading.quality_on())
        return {"hits": [], "catalog": [], "timings": {"total": 1.0}}
    monkeypatch.setattr(query_service, "run_search", fake)
    monkeypatch.setattr(api, "_run_search", fake)
    from app import llm

    def words(messages, **kw):
        yield "an answer"

        class End:
            whole = True
        return End()
    monkeypatch.setattr(llm, "chat_stream", words)
    return seen


def test_the_answer_api_carries_the_choice_into_its_search_thread(searched):
    with TestClient(api.app) as c:
        for speed in (False, True):
            c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "chi era?"}],
                                                 "stream": True, "speed": speed})
    assert searched == [True, False]


def test_the_search_api_carries_no_choice_and_keeps_the_ask_default(searched):
    with TestClient(api.app) as c:
        c.post("/api/search", json={"query": "balene"})
    assert searched and all(searched)


def test_ask_chat_carries_the_choice(monkeypatch):
    seen = []
    monkeypatch.setattr(chat, "_ask", lambda question, **kw: seen.append(reading.quality_on()) or {})
    chat.ask("q", speed=False)
    chat.ask("q", speed=True)
    chat.ask("q")
    assert seen == [True, False, True]
    assert reading.quality_on() is True                     # nothing leaks out of the call


def test_under_speed_the_reranker_reads_nothing(reranker, monkeypatch):  # noqa: F811
    def never(*a, **k):
        raise AssertionError("looked up under Speed")
    monkeypatch.setattr(reading, "years", never)
    monkeypatch.setattr(reading, "lookup", never)
    with reading.quality(False):
        ranked, ok = retrieval._rerank_held("q", [dict(h) for h in HITS], RECIPE, 5.0, 50, None)
    assert ok and reranker[0] == [h["text"] for h in HITS] and not any("moved_by" in h for h in ranked)


def test_under_speed_the_model_reads_no_glossary(monkeypatch):
    from app import historical

    def never(*a, **k):
        raise AssertionError("looked up under Speed")
    monkeypatch.setattr(reading, "years", never)
    with reading.quality(False):
        assert historical._glossary([(1, {"text": "l'huomo", "lang": "it"})]) == {}
