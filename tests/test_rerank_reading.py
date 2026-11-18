"""The reranker reads a COPY with its period's old words read (05 §5.6,
amended 2026-11-18; operator: "Yes to both, replacing the words the
reranker does not know with the words it knows, just enables it to do its
job"): on by default; every hit it moved says so, from its place had the
original been read to its place now (invariant 4, WEIGHTS); the hit shown
keeps its own text."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app import reading, retrieval
from app.refs.readings import REPLACE, Reading

RECIPE = SimpleNamespace(api="tei", model=None, url="http://reranker/v1/rerank")


@pytest.fixture
def reranker(monkeypatch):
    """Scores a text by how often it says 'uomo' — the word the reranker 'knows'."""
    sent: list[list[str]] = []

    def call(recipe, body, timeout, deadline):
        sent.append(list(body["texts"]))
        return [{"index": k, "score": len(re.findall(r"\buomo\b", t.lower())) - k * 0.001}
                for k, t in enumerate(body["texts"])]
    monkeypatch.setattr(retrieval, "_rerank_call", call)
    monkeypatch.setattr(retrieval, "_warm_reranker", lambda r: None)
    monkeypatch.setattr(retrieval, "fit_to_window", lambda texts, r: texts)
    monkeypatch.setattr(retrieval.model_load, "backend_pid", lambda *a: None)
    monkeypatch.setattr(retrieval, "CATALOG_FIRST", False)
    monkeypatch.setattr(retrieval, "RERANK_FUSE", False)
    return sent


@pytest.fixture
def huomo(monkeypatch):
    """Records 0 and 1 state 1554; huomo -> uomo holds 1400-1899."""
    found = reading.Lookup({"huomo": [Reading("huomo", "uomo", REPLACE, None, None, "it", 1400, 1899,
                                              "wikisource-variants 1", "wikisource-readings")]},
                           "wikisource-readings 1 abc")
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: 1554, 1: 1554})
    monkeypatch.setattr(reading, "lookup", lambda words, store=None: found)


HITS = [{"chunk_id": "c:1", "text": "un altro testo", "lang": "it"},
        {"chunk_id": "c:2", "text": "l'huomo e l'huomo", "lang": "it"},
        {"chunk_id": "c:3", "text": "un uomo", "lang": "it"}]


def test_the_copy_moves_a_hit_and_says_from_where_to_where(reranker, huomo):
    ranked, ok = retrieval._rerank_held("chi era l'uomo", [dict(h) for h in HITS], RECIPE, 5.0, 50, None)
    assert ok and [h["chunk_id"] for h in ranked] == ["c:2", "c:3", "c:1"]
    assert ranked[0]["moved_by"] == [{"weight": "historical reading (wikisource-readings 1 abc)",
                                      "from": 3, "to": 1}]
    assert ranked[0]["text"] == "l'huomo e l'huomo"               # the hit shown: its own text
    assert "moved_by" not in ranked[1] and "moved_by" not in ranked[2]
    # the copy and, for the hit it changed only, the original: scored in the same call
    assert reranker[0] == ["un altro testo", "l'uomo e l'uomo", "un uomo", "l'huomo e l'huomo"]


def test_a_record_with_no_year_is_read_as_it_is(reranker, monkeypatch):
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {})
    ranked, _ = retrieval._rerank_held("q", [dict(h) for h in HITS], RECIPE, 5.0, 50, None)
    assert reranker[0] == [h["text"] for h in HITS] and not any("moved_by" in h for h in ranked)


def test_on_by_default_and_off_only_for_its_measurement(reranker, huomo, monkeypatch):
    assert retrieval.READING_RERANK is True
    monkeypatch.setattr(retrieval, "READING_RERANK", False)
    ranked, _ = retrieval._rerank_held("q", [dict(h) for h in HITS], RECIPE, 5.0, 50, None)
    assert reranker[0] == [h["text"] for h in HITS] and not any("moved_by" in h for h in ranked)


def test_the_same_versions_give_the_same_order(reranker, huomo):
    a, _ = retrieval._rerank_held("q", [dict(h) for h in HITS], RECIPE, 5.0, 50, None)
    b, _ = retrieval._rerank_held("q", [dict(h) for h in HITS], RECIPE, 5.0, 50, None)
    assert [(h["chunk_id"], h.get("moved_by")) for h in a] == [(h["chunk_id"], h.get("moved_by")) for h in b]
