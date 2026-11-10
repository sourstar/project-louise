"""tools/measure/rerank_bench.py: reranker arms on IDENTICAL candidate lists
(docs/notes/reranker-survey-2026-11.md, "The benchmark").

The capture keeps what the live path would send the reranker, the rerank
step sends exactly the live requests, both resume, and the scores are the
hand-computed ones."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from app import collections as collections_mod, retrieval
from tools.measure import chat_eval
from tools.measure import rerank_bench as rb

URL = "http://localai:1/v1/rerank"
SETTINGS = {"catalog_first": True, "rerank_fuse": False, "language_order": "score"}


class _Resp:
    status_code = 200

    def __init__(self, data: Any) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self._data


def _score(query: str, doc: str) -> float:
    """A stand-in reranker: words shared by the query and the text."""
    return float(len(set(query.lower().split()) & set(doc.lower().split()))) + 1.0 / (1 + len(doc))


@pytest.fixture
def server(monkeypatch):
    """LocalAI's /v1/rerank as a stand-in, every body it was sent kept."""
    bodies: list[dict[str, Any]] = []

    def post(url, json=None, timeout=None, headers=None, **kw):
        bodies.append(json)
        return _Resp({"results": [{"index": i, "relevance_score": _score(json["query"], d)}
                                  for i, d in enumerate(json["documents"])]})
    monkeypatch.setattr(retrieval.httpc, "post", post)
    monkeypatch.setattr(retrieval, "_warm_reranker", lambda recipe: None)
    monkeypatch.setattr(retrieval.model_load, "backend_pid", lambda *a, **kw: None)
    return bodies


def _hit(n: int, lang: str, text: str, *, head: str = "", extra: list[str] = (), catalog: int | None = None):
    h: dict[str, Any] = {"chunk_id": f"gutenberg:{n}:0:1", "book_id": n, "lang": lang, "text": text,
                         "source": "dense", "leg_ranks": {"vector": n}}
    if catalog is not None:
        h["leg_ranks"]["catalog"] = catalog
    if head:
        h[retrieval.HEAD] = head
    h[retrieval.MATCHED] = [h] + [{"chunk_id": f"gutenberg:{n}:{j}:1", "book_id": n, "lang": lang, "text": t}
                                  for j, t in enumerate(extra, 1)]
    return h


def _fused() -> list[dict[str, Any]]:
    return [
        _hit(1, "it", "la balena bianca e il capitano", head="Moby Dick — Melville",
             extra=["il capitano Achab", "un terzo pezzo mai letto"]),
        _hit(2, "en", "the white whale and the captain", head="Moby Dick — Melville", extra=["Call me Ishmael"]),
        _hit(3, "en", "a biography of the author", catalog=1),
        _hit(4, "fr", "la baleine blanche", head="Moby Dick — Melville",
             extra=["the white whale and the captain"]),
    ]


# ---------------------------------------------------------------- fidelity


def test_the_harness_sends_exactly_the_live_requests_and_orders_as_the_live_path(server):
    """The same fused list, the same translations: the live `_rerank_list`
    and the harness send the same bodies (one per variant, a duplicate text
    once, two chunks a record under its title line) and give the same order,
    the catalogue's first record first."""
    query, translations = "la balena bianca", {"en": "the white whale", "de": "der weiße Wal"}
    recipe = collections_mod.RerankRecipe(url=URL, api="jina", model="fake-rerank", window=0)
    live, ok = retrieval._rerank_list(query, [dict(h) for h in _fused()], recipe, 30.0, queries=translations)
    assert ok
    live_bodies = list(server)
    server.clear()
    row = {"key": "q", "collection": "gutenberg", "plan": {"core": query, "translations": translations},
           "candidates": rb.as_sent(query, _fused(), translations, 50)}
    assert [len(c["chunks"]) for c in row["candidates"]] == [2, 2, 1, 2], "JUDGED_CHUNKS of each record"
    assert row["candidates"][0]["chunks"][0]["text"] == "Moby Dick — Melville\n\nla balena bianca e il capitano"
    got = rb.rerank_row(row, rb.Arm("A1", "fake-rerank"), recipe)
    assert server == live_bodies, "the very bodies the node sends"
    order = [row["candidates"][i]["book_id"] for i, _, _ in rb.ranked(row, got, SETTINGS)]
    assert order == [h["book_id"] for h in live]
    assert order[0] == 3, "CATALOG_FIRST, as shipped"
    assert [c["query"] for c in got["calls"]] == ["la balena bianca", "the white whale"]


def test_an_instruction_goes_in_the_query_in_the_documented_form_and_typed_asks_once(server):
    query = "la balena bianca"
    row = {"key": "q", "collection": "gutenberg", "plan": {"core": query, "translations": {"en": "the white whale"}},
           "candidates": rb.as_sent(query, _fused(), {"en": "the white whale"}, 50)}
    recipe = collections_mod.RerankRecipe(url=URL, api="jina", model="m", window=0)
    got = rb.rerank_row(row, rb.Arm("A2i", "m", instruction="Find the book"), recipe)
    assert [c["query"] for c in got["calls"]] == [
        "\n<Instruct>: Find the book\n<Query>: la balena bianca",
        "\n<Instruct>: Find the book\n<Query>: the white whale"]
    per = rb.Arm("A2c", "m", instructions={"gutenberg": "Books"})
    assert per.query_for("gutenberg", "q") == "\n<Instruct>: Books\n<Query>: q"
    assert rb.Arm("A2", "m").query_for("gutenberg", "q") == "q", "no instruction: the model's own default"
    server.clear()
    typed = rb.rerank_row(row, rb.Arm("T", "m", queries="typed"), recipe)
    assert len(server) == 1 and server[0]["query"] == query
    assert len(typed["scores"]) == 4 and all(len(s) == len(c["chunks"])
                                            for s, c in zip(typed["scores"], row["candidates"]))


def test_the_arms_cut_is_the_window_of_its_own_tokens(monkeypatch):
    """`fit_to_window` with the ARM's model, before the clock; the row says
    how many texts were cut."""
    asked: list[tuple[str, str]] = []

    def cut(texts, recipe):
        asked.append((recipe.model, recipe.window))
        return [t[:10] for t in texts]
    row = {"key": "q", "collection": "gutenberg", "plan": {"core": "x", "translations": {}},
           "candidates": [{"query_sent": "x", "chunks": [{"text": "short"}, {"text": "a much longer text"}]}]}
    recipe = collections_mod.RerankRecipe(url=URL, api="jina", model="arm-model", window=2048)
    sent: list[list[str]] = []
    got = rb.rerank_row(row, rb.Arm("A", "arm-model"), recipe,
                        post=lambda r, q, d, t: sent.append(d) or [0.0] * len(d), cut=cut)
    assert asked == [("arm-model", 2048)] and sent == [["short", "a much lon"]] and got["cut"] == 1


# ---------------------------------------------------------------- capture


def _set(n_per_kind: int = 2) -> rb.JudgedSet:
    items = []
    for kind in ("title", "descriptive"):
        for i in range(n_per_kind):
            items.append(rb.Item(key=f"{kind}-{i}", query=f"the white whale {kind} {i}", collection="gutenberg",
                                 kind=kind, lang="en", records={"2": ("work", 1)}, targets={"work": 1}))
    return rb.JudgedSet(name="g", format="gutenberg", source="test", items=items)


class _Search:
    """The live search, as far as the reranker step: the fused list handed to
    `retrieval._reranked` as `retrieval.search` hands it."""

    def __init__(self, withheld: list[dict[str, Any]] | None = None) -> None:
        self.asked: list[str] = []
        self.withheld = withheld

    def __call__(self, collection, query, *, k, translate, depth):
        self.asked.append(query)
        recipe = collections_mod.RerankRecipe(url=URL, api="jina", model="live-model")
        hits = [dict(h) for h in _fused()]
        ranked, ok, why = retrieval._reranked(query, hits, recipe, 5.0, depth, {"en": "the white whale"})
        result: dict[str, Any] = {"hits": ranked, "query_lang": "en"}
        if why:
            result["rerank_withheld"] = [{"collection": collection, "reason": why}]
        if self.withheld:
            result["legs_withheld"] = self.withheld
        return result


def _capture(path: Path, search: _Search, **kw) -> dict[str, Any]:
    kw.setdefault("settle", lambda names: {n: True for n in names})
    kw.setdefault("data_version", lambda names: {"gutenberg": "snap-1"})
    return rb.capture(kw.pop("js", None) or _set(), path, search=search, say=lambda s: None, **kw)


def test_capture_keeps_what_the_reranker_would_read_and_resumes(tmp_path):
    out = tmp_path / "g.jsonl"
    search = _Search()
    got = _capture(out, search, limit=1)
    assert got["captured"] == 2 and len(search.asked) == 2, "the pilot: the first query of each kind"
    assert retrieval._reranked.__name__ == "_reranked", "the live function is back after the capture"
    rows = [r for r in rb.read_jsonl(out) if "header" not in r]
    assert [r["key"] for r in rows] == ["title-0", "descriptive-0"]
    r0 = rows[0]
    assert r0["plan"]["core"] == "the white whale title 0" and r0["plan"]["translations"] == {"en": "the white whale"}
    assert r0["reranker"] == {"url": URL, "api": "jina", "model": "live-model", "window": 2048, "lent": False}
    assert [c["book_id"] for c in r0["candidates"]] == [1, 2, 3, 4]
    assert [c["query_sent"] for c in r0["candidates"]] == ["the white whale title 0", "the white whale",
                                                           "the white whale", "the white whale title 0"]
    assert r0["candidates"][2]["leg_ranks"] == {"vector": 3, "catalog": 1}
    grades = [[ch["grade"] for ch in c["chunks"]] for c in r0["candidates"]]
    assert grades == [[None, None], [1, 1], [None], [None, None]], "each chunk judged at capture"
    search.asked.clear()
    got = _capture(out, search)
    assert got == {**got, "already": 2, "captured": 2} and len(search.asked) == 2, "only what is missing"
    search.asked.clear()
    assert _capture(out, search)["captured"] == 0 and search.asked == []
    with pytest.raises(rb.Refused, match="translate"):
        _capture(out, search, translate=False)
    with pytest.raises(rb.Refused, match="data_version"):
        _capture(out, search, data_version=lambda names: {"gutenberg": "snap-2"})


def test_a_search_that_withheld_a_leg_is_never_recorded(tmp_path):
    out = tmp_path / "g.jsonl"
    search = _Search(withheld=[{"collection": "gutenberg", "leg": "vector", "reason": "run not tested"}])
    with pytest.raises(chat_eval.SearchWithheld, match="run not tested"):
        _capture(out, search)
    assert [r for r in rb.read_jsonl(out) if "header" not in r] == []
    with pytest.raises(chat_eval.SearchWithheld, match="not settled"):
        _capture(tmp_path / "h.jsonl", _Search(), settle=lambda names: {"gutenberg": False})
    assert rb._withheld({"rerank_withheld": [{"collection": "g", "reason": rb.CAPTURED}]}) == []
    assert rb._withheld({"rerank_withheld": [{"collection": "g", "reason": "busy"}]}) == ["g (rerank_withheld): busy"]


# ---------------------------------------------------------------- rerank


def test_rerank_resumes_per_query_and_pins_its_arm(tmp_path):
    cap = tmp_path / "g.jsonl"
    _capture(cap, _Search())
    calls: list[str] = []

    def post(recipe, query, docs, timeout):
        calls.append(query)
        return [_score(query, d) for d in docs]
    kw = dict(post=post, cut=lambda t, r: t, pid=lambda u, m: 7, say=lambda s: None)
    got = rb.rerank(cap, rb.Arm("A1", "m1"), limit=1, **kw)
    assert got["reranked"] == 2 and calls[0] == "warm", "warmed once before the first timed query"
    assert len(calls) == 1 + 2 * 2, "two variants a query"
    rows = [r for r in rb.read_jsonl(rb.arm_path(tmp_path, cap, "A1")) if "header" not in r]
    assert [r["key"] for r in rows] == ["title-0", "descriptive-0"]
    assert all(r["warm"] and r["ms"] >= 0 and len(r["calls"]) == 2 for r in rows)
    calls.clear()
    assert rb.rerank(cap, rb.Arm("A1", "m1"), **kw)["reranked"] == 2 and len(calls) == 1 + 2 * 2
    calls.clear()
    assert rb.rerank(cap, rb.Arm("A1", "m1"), **kw)["reranked"] == 0 and calls == [], "nothing, not even a warm-up"
    with pytest.raises(rb.Refused, match="model"):
        rb.rerank(cap, rb.Arm("A1", "another-model"), **kw)
    with pytest.raises(rb.Refused, match="fused order"):
        rb.rerank(cap, rb.Arm("A0", "m1"), **kw)
    with pytest.raises(rb.Refused, match="no instruction for gutenberg"):
        rb.rerank(cap, rb.Arm("A2c", "m2", instructions={"wikipedia": "x"}), **kw)
    pids = iter(range(100))
    kw["pid"] = lambda u, m: next(pids)
    rb.rerank(cap, rb.Arm("A3", "m3"), **kw)
    assert not any(r.get("warm") for r in rb.read_jsonl(rb.arm_path(tmp_path, cap, "A3")) if "header" not in r), \
        "another backend after a query than before it: a load it paid, not timed"


def test_rerank_with_concurrency_answers_every_query_once(tmp_path):
    cap = tmp_path / "g.jsonl"
    _capture(cap, _Search(), js=_set(5))
    got = rb.rerank(cap, rb.Arm("A1", "m1"), concurrency=3, post=lambda r, q, d, t: [0.0] * len(d),
                    cut=lambda t, r: t, say=lambda s: None)
    keys = [r["key"] for r in rb.read_jsonl(rb.arm_path(tmp_path, cap, "A1")) if "header" not in r]
    assert got["reranked"] == 10 and sorted(keys) == sorted({*keys}) and len(keys) == 10


# ---------------------------------------------------------------- score


def _write(path: Path, header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(x) + "\n" for x in [{"header": header}, *rows]), encoding="utf-8")


def _cand(n: int, lang: str | None, grades: list[tuple[str | None, int | None]], catalog: int | None = None):
    lr = {"vector": n} | ({"catalog": catalog} if catalog else {})
    return {"uid": f"g:{n}", "book_id": n, "lang": lang, "leg_ranks": lr, "query_sent": "q",
            "chunks": [{"chunk_id": f"g:{n}:{j}:1", "text": f"t{n}.{j}", "target": t, "grade": g}
                       for j, (t, g) in enumerate(grades)]}


CAP_HEADER = {"tool": "rerank_bench capture", "set": "s", "relevant": 1, "exhaustive": True, "retrieval": SETTINGS}


def _arm_row(key: str, scores: list[list[float]], ms: float = 10.0, warm: bool = True):
    return {"key": key, "scores": scores, "calls": [{"query": "q", "documents": 1, "ms": ms}], "ms": ms, "warm": warm}


def _two_queries(tmp_path: Path) -> Path:
    cap = tmp_path / "s.jsonl"
    q1 = {"key": "q1", "kind": "k", "lang": "en", "query": "the whale", "targets": {"w": 1}, "plan": {"core": "q", "query_lang": "en"},
          "candidates": [_cand(0, "en", [(None, None)]), _cand(1, "it", [(None, None)]),
                         _cand(2, "en", [("w", 1), ("w", 1)]), _cand(3, None, [(None, None)])]}
    q2 = {"key": "q2", "kind": "k", "lang": "it", "query": "la balena", "targets": {"w": 1}, "plan": {"core": "q", "query_lang": "it"},
          "candidates": [_cand(0, "it", [(None, None)]), _cand(1, "en", [(None, None)])]}
    _write(cap, CAP_HEADER, [q1, q2])
    _write(tmp_path / "s.X.jsonl", {"arm": "X"},
           [_arm_row("q1", [[0.5], [0.4], [0.9, 0.2], [0.1]], ms=10.0),
            _arm_row("q2", [[0.3], [0.2]], ms=20.0)])
    _write(tmp_path / "s.Y.jsonl", {"arm": "Y"},
           [_arm_row("q1", [[0.5], [0.4], [0.9, 0.2], [0.1]], ms=30.0),
            _arm_row("q2", [[0.3], [0.2]], ms=40.0, warm=False)])
    return cap


def test_score_is_the_hand_computed_one(tmp_path):
    """q1: the relevant record fused third (MRR 1/3, nDCG 1/log2 4), first
    under X; q2: none relevant among the candidates."""
    cap = _two_queries(tmp_path)
    res = rb.score_set(cap, ["A0", "X", "Y"], base="X")
    a0, x, y = (res["arms"][a] for a in ("A0", "X", "Y"))
    assert res["paired"] == 2
    assert a0["mrr@10"] == round((1 / 3) / 2, 4) and a0["ndcg@10"] == 0.25
    assert a0["r@1"] == 0.0 and a0["r@10"] == 0.5
    assert x["mrr@10"] == 0.5 and x["ndcg@10"] == 0.5 and x["r@1"] == 0.5 and x["r@10"] == 0.5
    assert a0["other_lang@10"] == 0.5, "q1: it and no language of 4 (0.5); q2: en of 2 (0.5)"
    assert x["it_en_gap"] == {"mrr@10": -1.0, "it": 1, "en": 1}
    assert a0["it_en_gap"]["mrr@10"] == round(0 - 1 / 3, 4)
    assert x["list_ms"]["warm"] == 2 and y["list_ms"]["warm"] == 1 and y["cold"] == 1, "a cold list is not timed"
    assert y["vs_base"]["p"] == 1.0 and y["vs_base"]["delta"] == 0.0, "identical arms: p = 1"
    assert "judged@10" not in x, "an exhaustive set has nothing unjudged"
    assert "list_ms" not in a0 and "vs_base" not in x


def test_ndcg_takes_grades_and_counts_a_target_once():
    row = {"targets": {"a": 1, "b": 3, "c": 2}, "lang": "en",
           "candidates": [_cand(0, "en", [("a", 1)]), _cand(1, "en", [("b", 3)]), _cand(2, "en", [("b", 3)])]}
    m = rb.query_metrics(row, [(0, 0, None), (1, 0, None), (2, 0, None)], relevant=2)
    ideal = 7 + 3 / math.log2(3) + 1 / math.log2(4)
    assert m["ndcg@10"] == pytest.approx((1 + 7 / math.log2(3)) / ideal), "b's second record adds nothing"
    assert m["first"] == 2 and m["mrr@10"] == 0.5, "grade 1 is not relevant at threshold 2"
    assert m["judged@10"] == 1.0 and m["other_lang@10"] == 0.0


def test_a_record_is_judged_by_the_chunk_that_scored_it_and_the_catalogue_goes_first():
    row = {"targets": {"art": 1}, "lang": "it",
           "candidates": [_cand(0, "it", [(None, None), ("art", 1)]), _cand(1, "it", [(None, None)], catalog=1)]}
    fused = rb.ranked(row, None, SETTINGS)
    assert fused == [(0, 0, None), (1, 0, None)] and rb.query_metrics(row, fused, 1)["mrr@10"] == 0.0, \
        "A0: the record's best fused chunk, not the gold article"
    arm = {"scores": [[0.1, 0.9], [0.5]], "calls": [{"query": "q"}]}
    shipped = rb.ranked(row, arm, SETTINGS)
    assert [i for i, _, _ in shipped] == [1, 0], "the catalogue's first record first, as shipped"
    assert rb.query_metrics(row, shipped, 1)["first"] == 2
    raw = rb.ranked(row, arm, SETTINGS, raw=True)
    assert raw[0] == (0, 1, 0.9) and rb.query_metrics(row, raw, 1)["mrr@10"] == 1.0


def test_other_language_share_counts_a_record_with_no_language_as_another():
    row = {"targets": {}, "lang": "it",
           "candidates": [_cand(0, "en", [(None, None)]), _cand(1, "it", [(None, None)]),
                          _cand(2, None, [(None, None)]), _cand(3, "EN", [(None, None)])]}
    order = [(i, 0, None) for i in range(4)]
    assert rb.query_metrics(row, order, 1)["other_lang@10"] == 0.75
    row["lang"], row["plan"] = None, {"query_lang": None}
    assert rb.query_metrics(row, order, 1)["other_lang@10"] is None, "no language known: not counted"


def test_the_randomization_test():
    same = [0.0, 1.0, 0.5, 1 / 3] * 10
    assert rb.versus(same, same)["p"] == 1.0
    better = rb.versus([1.0] * 30, [0.25] * 30)
    assert better["p"] < 0.01 and better["delta"] == 0.75 and better["better"] == 30
    mixed = rb.versus([1.0, 0.0] * 15, [0.0, 1.0] * 15)
    assert mixed["p"] == 1.0 and mixed["better"] == mixed["worse"] == 15


def test_score_and_show_from_the_command_line(tmp_path, capsys):
    cap = _two_queries(tmp_path)
    assert rb.main(["score", str(cap), "--arms", "A0,X,Y", "--base", "X", "--out", str(tmp_path / "res")]) == 0
    md = (tmp_path / "res.md").read_text(encoding="utf-8")
    assert "| X | 2 | 0.500 | 0.500 | 0.500 | 0.500 |" in md
    assert json.loads((tmp_path / "res.json").read_text(encoding="utf-8"))[0]["arms"]["Y"]["vs_base"]["p"] == 1.0
    assert rb.main(["score", str(cap), "--arms", "A0,Z"]) == 2, "an arm never reranked: refused"
    capsys.readouterr()
    assert rb.main(["show", str(cap), "--arms", "A0,X", "--limit", "1"]) == 0
    shown = capsys.readouterr().out
    assert "## [k] q1" in shown and "q2" not in shown and "REL g:2" in shown


# ---------------------------------------------------------------- the sets


def test_the_gutenberg_set_from_the_campaigns_data_and_from_the_tsvs(tmp_path):
    data = tmp_path / "queries.py"
    data.write_text("DATA = " + json.dumps([
        {"qid": "WQ1-en", "kind": "title", "lang": "en", "query": "Moby Dick", "books": ["gutenberg:2701", 15]},
        {"qid": "WQ1-it", "kind": "pair", "lang": "it", "query": "la balena", "books": [2701]}]), encoding="utf-8")
    js = rb.load_gutenberg([data])
    assert [(i.key, i.kind) for i in js.items] == [("WQ1-en", "title"), ("WQ1-it", "pair")]
    assert js.items[0].records == {"2701": ("work", 1), "15": ("work", 1)} and js.items[0].targets == {"work": 1}
    assert rb.judge_by_record(js.items[0], "gutenberg:2701", "gutenberg:2701:3:1", 2701) == ("work", 1)
    assert rb.judge_by_record(js.items[0], "gutenberg:9", "gutenberg:9:0:1", 9) is None
    tsv = tmp_path / "q.tsv"
    tsv.write_text("WQ40185#T\ten\tThe Divine Comedy\nWQ9\tit\tun romanzo\n", encoding="utf-8")
    works = tmp_path / "works.json"
    works.write_text(json.dumps([{"work": "WQ40185", "books": ["gutenberg:1000"]},
                                 {"work": "WQ4509219", "books": ["gutenberg:8800"]},
                                 {"work": "WQ9", "books": ["gutenberg:5"]}]), encoding="utf-8")
    t = rb.load_gutenberg([tsv], works=works)
    assert [(i.kind, sorted(i.records)) for i in t.items] == [("title", ["1000", "8800"]), ("descriptive", ["5"])], \
        "one_language_score.ALSO: an overlapping work answers too"
    with pytest.raises(rb.Refused, match="--works"):
        rb.load_gutenberg([tsv])


def test_the_known_item_and_umbrela_sets(tmp_path):
    q = tmp_path / "queries.jsonl"
    q.write_text("".join(json.dumps(x) + "\n" for x in [
        {"uid": "wikidata:42", "family": "person", "lang": "en", "query": "an author"},
        {"uid": "wikidata:7", "family": "work", "lang": "it", "query": "un'opera"}]), encoding="utf-8")
    ki = rb.load_known_item(q, collection="wikidata")
    assert [(i.kind, i.records, i.targets) for i in ki.items] == [
        ("person", {"42": ("wikidata:42", 1)}, {"wikidata:42": 1}), ("work", {"7": ("wikidata:7", 1)}, {"wikidata:7": 1})]
    j = tmp_path / "judged.jsonl"
    j.write_text("".join(json.dumps(x) + "\n" for x in [
        {"query": "q1", "collection": "pubmed", "book_id": 1, "grade": 3},
        {"query": "q1", "collection": "pubmed", "book_id": 2, "grade": 0},
        {"query": "q1", "collection": "pubmed", "book_id": 3, "grade": None},
        {"query": "q1", "collection": "wikipedia", "book_id": 9, "grade": 1},
        {"query": "q2", "collection": "wikipedia", "book_id": 5, "grade": 2}]), encoding="utf-8")
    um = rb.load_umbrela(j)
    assert um.relevant == 2 and not um.exhaustive
    assert [(i.collection, i.query, i.targets) for i in um.items] == [
        ("pubmed", "q1", {"1": 3, "2": 0}), ("wikipedia", "q2", {"5": 2})], "a list with no grade >= 2 is left out"
    assert rb.judge_by_record(um.items[0], "pubmed:2", "pubmed:2:0:1", 2) == ("2", 0)
    assert [i.collection for i in rb.load_umbrela(j, collections=["pubmed"]).items] == ["pubmed"]


def test_the_pilot_takes_the_first_items_of_every_kind():
    items = _set(3).items
    assert [i.key for i in rb.first_per_kind(items, 2)] == ["title-0", "title-1", "descriptive-0", "descriptive-1"]
    assert rb.first_per_kind(items, None) == items
