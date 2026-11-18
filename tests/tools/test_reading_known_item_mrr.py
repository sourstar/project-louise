"""tools/measure/reading_known_item_mrr.py: the target's rank, the copy on
and off on the product's path, the per-century rows, the loss rule."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app import collections as collections_mod, reading, retrieval
from tools.measure import reading_known_item_mrr as m, reading_summary_eval as e


def test_a_target_is_ranked_by_its_record_or_an_edition_grouped_under_it():
    hits = [{"chunk_id": "gutenberg:9:0:1"}, {"chunk_id": "gutenberg:8:0:1", "editions": [{"uid": "gutenberg:5"}]}]
    assert m.rank_of(hits, "gutenberg:9") == 1 and m.rank_of(hits, "gutenberg:5") == 2
    assert m.rank_of(hits, "gutenberg:7") is None


def test_on_and_off_each_query_under_quality_with_the_copy_switched(monkeypatch, tmp_path):
    seen = []

    def fake(con, q, **kw):
        seen.append((retrieval.READING_RERANK, reading.quality_on(), kw["translate"]))
        target = {"chunk_id": "gutenberg:1:0:1", "moved_by": [{"weight": "historical reading (v)", "read": True}]}
        other = {"chunk_id": "gutenberg:2:0:1"}
        return {"hits": [target, other] if retrieval.READING_RERANK else [other, target]}
    monkeypatch.setattr(retrieval, "search", fake)
    monkeypatch.setattr(collections_mod, "by_name", lambda n: SimpleNamespace(name=n))
    monkeypatch.setattr(e, "ro_connect", lambda c: None)
    st = tmp_path / "set.jsonl"
    st.write_text("\n".join(json.dumps(x) for x in [
        {"set": "reading-known-items"},
        {"qid": 1, "stratum": "g:1300:1399", "year": 1310, "uid": "gutenberg:1", "query": "q", "refused": None},
        {"qid": 2, "stratum": "g:1400:1499", "year": 1450, "uid": "gutenberg:1", "query": "q2", "refused": None},
        {"qid": 3, "uid": "gutenberg:1", "query": "x", "refused": "not a question"}]) + "\n", encoding="utf-8")
    out = tmp_path / "mrr.jsonl"
    m.main(["--collection", "g", "--set", str(st), "--out", str(out), "--permutations", "200"])
    assert seen == [(True, True, False), (False, True, False)] * 2 and retrieval.READING_RERANK is True
    s = json.loads(out.read_text(encoding="utf-8").splitlines()[-1])["summary"]
    assert s["n"] == 2 and s["mrr_on"] == 1.0 and s["mrr_off"] == 0.5 and s["queries_with_a_moved_hit"] == 2
    assert set(s["by_century"]) == {"1300s", "1400s"} and s["by_century"]["1400s"]["n"] == 1
    assert s["loss_over_0_02"] is False
