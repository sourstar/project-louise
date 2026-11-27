"""rerank_bench's `queries` set: questions with no judgement, for a parity of two
rerankers on the lists the live search sends (tools/measure/rerank_parity.py)."""
import json

import pytest

from tools.measure import rerank_bench as rb


def test_queries_become_unjudged_items_of_the_collection(tmp_path):
    f = tmp_path / "q.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in ({"query": "aspirin and colorectal cancer"},
                                                    {"query": "β-blockers after infarction", "lang": "en"})), encoding="utf-8")
    js = rb.load_set("queries", [f], collection="pubmed")
    assert js.format == "queries" and not js.exhaustive
    assert [(i.query, i.collection, i.lang, i.records, i.targets) for i in js.items] == [
        ("aspirin and colorectal cancer", "pubmed", None, {}, {}), ("β-blockers after infarction", "pubmed", "en", {}, {})]
    assert len({i.key for i in js.items}) == 2


def test_queries_need_the_collection_they_are_asked_of(tmp_path):
    f = tmp_path / "q.jsonl"
    f.write_text(json.dumps({"query": "x"}), encoding="utf-8")
    with pytest.raises(rb.Refused):
        rb.load_set("queries", [f])
