"""tools/measure/search_memory.py: one search's memory and seconds, leg by leg,
with no model asked and nothing written (München, 2026-11-26: the web held
1.13 GB after one question, and the question's line said where its seconds
went, not its bytes)."""
from __future__ import annotations

import json

import pytest

from app import collections as collections_mod, fts, retrieval
from tools.measure import search_memory


@pytest.fixture
def held(isolated_config):
    c = collections_mod.default_collection()
    fts.index_book("it", 2, [{"id": "fake:2:0:1", "chapter": "", "text": "la balena bianca e il capitano"}], c)
    fts.index_book("en", 1, [{"id": "fake:1:0:1", "chapter": "", "text": "the white whale and the captain"}], c)
    yield c
    fts.close_all()


def test_each_step_says_its_seconds_its_memory_and_what_is_held_after_a_trim(held, monkeypatch, capsys, tmp_path):
    asked, written = [], []
    real_reranked, real_cite = retrieval._reranked, retrieval.verify_on_use.cite
    monkeypatch.setattr(retrieval, "_reranked", lambda *a, **k: asked.append("reranker") or real_reranked(*a, **k))
    monkeypatch.setattr(retrieval.verify_on_use, "cite", lambda *a, **k: written.append("cite"))
    monkeypatch.setattr(retrieval, "_enqueue_lazy_embed", lambda *a, **k: written.append("lazy"))
    out = tmp_path / "m.json"
    assert search_memory.main(["--query", "balena", "--times", "2", "--out", str(out)]) == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out) == got, "printed and written the same"
    assert got["collections"] == [held.name] and len(got["left_out"]) == 3
    steps = got["steps"]
    assert [(s["what"], s["mode"], s["time"]) for s in steps] == [
        (held.name, "fts", 1), (held.name, "fts", 2), (held.name, "catalog", 1), (held.name, "catalog", 2)]
    for s in steps:
        assert "error" not in s["search"], s["search"]
        assert {"before", "after", "after_trim", "seconds", "held_mib"} <= set(s)
        assert {"RssAnon", "RssFile", "VmRSS"} <= set(s["after_trim"])
    assert steps[0]["search"]["hits"] >= 1, "the text leg found the book"
    assert (asked, written) == ([], []), "no model asked, nothing written: the tool's own replacements stand"
    assert isinstance(got["held_mib"], int)


def test_the_trace_names_pythons_largest_sites(held):
    got = search_memory.measure("balena", [held.name], times=1, trace=True)
    assert got["python_sites"] and {"site", "mib", "blocks"} <= set(got["python_sites"][0])
    assert got["python_mib"] >= 0
    import tracemalloc
    tracemalloc.stop()
