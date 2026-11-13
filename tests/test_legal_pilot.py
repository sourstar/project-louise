"""tools/legal_pilot.py — the pilot's measurement over a built legal
collection: gold articles resolved BY KEY, metrics over the gold node ids,
the reach of the misses along declared edges, and the temporal arm — all
deterministic, no model in the loop (gazzetta-plugin.md §7.2; review §10)."""
from __future__ import annotations

import base64
import json

import pytest

from app import collections as collections_mod, db, discovery
from tests.test_normattiva import BUILT, L306, M306, SARD_MEMBERS, _nid, _zip
from tests.test_validity import _run
from tools import legal_pilot as lp

LAW_M = "LEGGE_20031031_306/2003-11-15_003G0333_"
SARD_O = _nid("1948-03-09", "048C0003", "1948-03-10", "ORIGINAL")
SARD_V12 = _nid("1948-03-09", "048C0003", "2001-02-16", "CONSOLIDATED/20010216")
L306_ID = _nid("2003-11-15", "003G0333", "2025-10-02", "CONSOLIDATED/20251002")
#: two consecutive versions of a synthetic decree whose art. 8 loses a number
DEC_A = "DECRETO_20200102_3/2020-01-10_20G00003_VIGENZA_2021-01-01_V1.xml"
DEC_B = "DECRETO_20200102_3/2020-01-10_20G00003_VIGENZA_2022-01-01_V2.xml"
DEC_A_ID = _nid("2020-01-10", "20G00003", "2021-01-01", "CONSOLIDATED/20210101")
DEC_B_ID = _nid("2020-01-10", "20G00003", "2022-01-01", "CONSOLIDATED/20220101")
VERSION_A = BUILT.replace(b"<p>Fuori capo((3)).</p>", b"<p>Il termine e' di 30 giorni.</p>")
VERSION_B = BUILT.replace(b"<p>Fuori capo((3)).</p>", b"<p>Il termine e' di 60 giorni.</p>")

CORPUS = [
    {"id": 1, "file_name": "§akn§it§act§l§STATO_ITALIANO§2003-10-31§0306§ITA", "path": "chapter_1__article_5"},
    {"id": 2, "file_name": "§akn§eu§act§reg§CONSIL-EP§2016-04-27§0679§ITA", "path": "chapter_2__article_11"},
    {"id": 3, "file_name": "§akn§it§act§lcost§STATO_ITALIANO§1948-02-26§0003§ITA", "path": "title_1__article_1"},
    {"id": 4, "file_name": "§akn§it§act§l§STATO_ITALIANO§2003-10-31§0306§ITA", "path": "chapter_9__article_999"},
    {"id": 5, "file_name": "§akn§it§act§l§STATO_ITALIANO§2003-10-31§0306§ITA", "path": "/"},
]
QUESTIONS = [
    {"question": "Cosa prevede l'art. 5 della legge 306/2003?", "relevant_doc_ids": [1]},
    {"question": "Trattamento senza identificazione", "relevant_doc_ids": [2]},
    {"question": "Statuto e deleghe", "relevant_doc_ids": [1, 3]},
    {"question": "Un articolo che non c'e'", "relevant_doc_ids": [4, 5]},
]


@pytest.fixture
def built(tmp_path, isolated_config, monkeypatch):
    """A normattiva collection through rdf and fts: the Statuto Sardegna's
    versions, legge 306/2003, and the synthetic decree in two versions."""
    from app.plugins.normattiva import source as nsrc
    nsrc._zip_cache.clear()
    root = tmp_path / "normattiva"
    _zip(root / "akn/M/leggi-costituzionali.zip", dict(SARD_MEMBERS))
    _zip(root / "akn/M/codici.zip", {M306: base64.b64encode(L306)})
    _zip(root / "akn/M/decreti.zip", {DEC_A: VERSION_A, DEC_B: VERSION_B})
    c = collections_mod.Collection(
        name="normattiva", plugin="normattiva", profile=None, embed_mode_default=None,
        corpus_dir=root, manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")
    con = db.connect(c)
    discovery.sync_collection(con, c)
    _run(con, c, "rdf")
    return lp.Held(c, con)


def test_article_tokens_and_acts_are_read_from_the_datasets_names():
    assert lp.article_token("attachment_(2)__book_(3)__title_8__chapter_2__section_3__article_1167") == "1167"
    assert lp.article_token("Art. 1167.") == "1167" and lp.article_token("CODICE CIVILE-art. 906") == "906"
    assert lp.article_token("Articolo 11") == "11" and lp.article_token("Art. 5-bis.") == "5-bis"
    assert lp.article_token("article_2043bis") == "2043-bis" and lp.article_token("Allegato 1") is None
    assert lp.act_of("§akn§it§act§rd§STATO_ITALIANO§1942-03-16§0262§ITA") == ("rd", "1942-03-16", 262)
    assert lp.act_of("§akn§eu§act§reg§CONSIL-EP§2016-04-27§0679§ITA") is None, "not an Italian act"
    assert lp.act_of("garbage") is None


def test_gold_articles_resolve_by_key_and_coverage_excludes_what_is_not_held(built):
    cov = lp.coverage(built, CORPUS, QUESTIONS)
    assert (cov["questions"], cov["covered"], cov["excluded"]) == (4, 2, 2)
    gold = {int(k): v for k, v in cov["gold"].items()}
    assert gold[1]["uid"] == f"normattiva:{L306_ID}" and gold[1]["seqs"], "legge 306's art. 5, by its URN key"
    assert gold[3]["uid"] == f"normattiva:{SARD_V12}", "the Statuto's SHOWN version holds the gold article"
    assert gold[2]["why"] == "not an Italian act"
    assert gold[4]["why"] == "article not among the shown version's chunks"
    assert gold[5]["why"] == "no article in the node path"
    assert [e["index"] for e in cov["excluded_questions"]] == [1, 3]
    assert cov["articles_by_outcome"]["held"] == 2


def _hit(uid, seq, text="", rank=0):
    return {"uid": uid, "chunk_id": f"{uid}:{seq}:1", "text": text, "rank": rank}


def test_the_run_scores_the_gold_by_key_and_measures_the_reach_of_the_misses(built):
    cov = lp.coverage(built, CORPUS, QUESTIONS)
    gold = {int(k): lp.gold_from(v) for k, v in cov["gold"].items()}
    law = f"normattiva:{L306_ID}"
    art5 = gold[1].seqs[0]
    other = next(s for s in built.articles(law)[1] if s != art5)
    answers = {
        QUESTIONS[0]["question"]: {"lists": [{"hits": [_hit(law, other, rank=0), _hit(law, art5, rank=1)]}]},
        QUESTIONS[2]["question"]: {"lists": [{"hits": [_hit(law, art5, rank=0)]}]},
    }
    edges = {law: {"edges": [{"from": law, "to": "akn:it/act/legge_costituzionale/stato/1948-02-26/3",
                              "type": "eli:cites"}]}}
    got = lp.run(built, QUESTIONS, gold, k=10,
                 search_fn=lambda q, **kw: answers[q],
                 traverse_fn=lambda uid, **kw: edges.get(uid, {"edges": []}))
    s = got["summary"]
    assert s["questions_run"] == 2
    assert s["recall@1"] == pytest.approx((0.0 + 0.5) / 2) and s["recall@10"] == pytest.approx((1.0 + 0.5) / 2)
    assert s["mrr@10"] == pytest.approx((0.5 + 1.0) / 2)
    assert s["reach_misses"] == 1 and s["reach_reachable_by_one_edge"] == 1 and s["reach_share"] == 1.0, \
        "the Statuto's article was missed, and legge 306 cites the Statuto: reachable by one edge"
    rows = [r for r in got["per_question"] if "index" in r]
    assert rows[0]["ranking"][1] == 1 and rows[0]["ranking"][0] < 0, "a non-gold chunk is a synthetic id"
    assert rows[1]["missed"] == [3] and rows[1]["reachable_by_edge"] == [3]


def test_dated_question_stubs_come_from_versions_whose_article_lost_a_number(built):
    stubs = lp.temporal_make(built, limit=10)
    dec = [s for s in stubs if s["gold_uid"] == f"normattiva:{DEC_A_ID}"]
    assert len(dec) == 1, stubs
    s = dec[0]
    assert s["article"] == "8" and s["nugget"] == "30" and s["control_uid"] == f"normattiva:{DEC_B_ID}"
    assert s["as_of"] == "2021-12-31", "the last day of the earlier version"
    assert "art. 8" in s["question"] and s["review"].startswith("stub")


def test_the_temporal_run_holds_the_version_to_its_uid_and_the_nugget_to_its_text(built):
    a, b = f"normattiva:{DEC_A_ID}", f"normattiva:{DEC_B_ID}"
    seq_a = built.articles(a)[0]["8"][0]
    seq_b = built.articles(b)[0]["8"][0]
    item = {"work": built.work_of(a), "article": "8", "as_of": "2021-12-31", "gold_uid": a, "control_uid": b,
            "nugget": "30", "question": "Che cosa stabiliva l'art. 8 al 2021-12-31?"}

    def search(q, *, collections, limit, as_of):
        if as_of:
            return {"lists": [{"hits": [_hit(a, seq_a, text="Il termine e' di 30 giorni.")]}]}
        return {"lists": [{"hits": [_hit(b, seq_b, text="Il termine e' di 60 giorni.")]}]}
    got = lp.temporal_run(built, [item], k=5, search_fn=search)
    assert got["summary"] == {"n": 1, "version_right": 1, "nugget_found": 1, "control_current": 1,
                              "control_nugget_gone": 1}
    # a current-only answer to the dated question: the version is wrong and the nugget gone
    got = lp.temporal_run(built, [item], k=5,
                          search_fn=lambda q, **kw: {"lists": [{"hits": [_hit(b, seq_b, text="60 giorni")]}]})
    assert got["summary"]["version_right"] == 0 and got["summary"]["nugget_found"] == 0


def test_the_command_runs_end_to_end_on_files(built, tmp_path, monkeypatch, capsys):
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = tmp_path / "pilot"
    out.mkdir()
    pq.write_table(pa.Table.from_pylist(CORPUS), out / "corpus.parquet")
    pq.write_table(pa.Table.from_pylist(QUESTIONS), out / "questions-validation.parquet")
    monkeypatch.setattr(lp, "_open", lambda name, root: (built.collection, built.con))
    assert lp.main(["coverage", "--out", str(out), "--collection", "normattiva"]) == 0
    assert "covered 2" in capsys.readouterr().out
    law = f"normattiva:{L306_ID}"
    art5 = built.articles(law)[0]["5"][0]
    monkeypatch.setattr(lp, "default_search",
                        lambda q, **kw: {"lists": [{"hits": [_hit(law, art5, rank=0)]}]})
    monkeypatch.setattr(lp, "settle_fn", lambda names: {n: True for n in names})
    flags = ["run", "--out", str(out), "--collection", "normattiva", "--k", "5", "--no-reach"]
    assert lp.main(flags) == 2, "no pilot read for these settings: refused (measuring.md §12)"
    assert "no pilot read" in capsys.readouterr().err
    assert lp.main(flags + ["--pilot", "3"]) == 0
    said = capsys.readouterr().out
    tag = said.split("--tag ")[1].split()[0]
    pilot = (out / "runs" / "normattiva" / f"pilot-{tag}.md").read_text(encoding="utf-8")
    assert QUESTIONS[0]["question"] in pilot and "GOLD" in pilot, "the reader sees the hits and which is gold"
    assert not list((out / "runs").glob("*-run.json")), "a pilot is not a run"
    assert lp.main(["pilot-ok", "--out", str(out), "--collection", "normattiva", "--tag", tag,
                    "--by", "operator", "--note", "hits read, gold found"]) == 0
    assert lp.main(flags) == 0
    runs = sorted((out / "runs").glob("*-run.json"))
    assert runs, "a run is written with the collection named"
    payload = json.loads(runs[-1].read_text(encoding="utf-8"))
    assert payload["collection"] == "normattiva" and payload["summary"]["questions_run"] == 2
    stubs = tmp_path / "stubs.jsonl"
    assert lp.main(["temporal-make", "--collection", "normattiva", "--out", str(stubs), "--limit", "5"]) == 0
    assert stubs.read_text(encoding="utf-8").count("\n") >= 1


def test_a_gold_act_under_a_ministry_authority_resolves_by_date_and_number(built):
    """The coverage run of 2026-10-13 counted D.M. 7 novembre 2001 n. 458
    "act not held" while the collection holds it as
    `urn:nir:ministero.giustizia:decreto:2001-11-07;458`: the URN pattern
    admitted `stato` alone. Any authority now; the type abbreviation decides
    only where one (date, number) names several acts, and `dm` accepts the
    ministry's plain `decreto`."""
    built.con.execute("INSERT OR IGNORE INTO validity_works(key, work) VALUES (?, ?)",
                      ("urn:nir:ministero.giustizia:decreto:2001-11-07;458", "work-dm-458"))
    built.con.execute("INSERT OR IGNORE INTO validity_works(key, work) VALUES (?, ?)",
                      ("urn:nir:stato:legge:2001-11-07;458", "work-l-458"))
    built._acts = None
    assert built.work_for(("dm", "2001-11-07", 458)) == "work-dm-458"
    assert built.work_for(("l", "2001-11-07", 458)) == "work-l-458"
    assert built.work_for(("dlgs", "2001-11-07", 458)) is None, "two acts, neither of that type"


def test_each_search_is_timed_and_the_summary_says_mean_p50_p95_and_total(built):
    """Piece 5's time half (operator, 2026-11-08: "compare time and quality
    by adding 5"): every question's search wall time, summarised."""
    cov = lp.coverage(built, CORPUS, QUESTIONS)
    gold = {int(k): lp.gold_from(v) for k, v in cov["gold"].items()}
    got = lp.run(built, QUESTIONS, gold, k=10, search_fn=lambda q, **kw: {"lists": []}, traverse_fn=None)
    t = got["summary"]["seconds"]
    assert set(t) == {"mean", "p50", "p95", "total"} and t["total"] >= 0 and t["p95"] >= t["p50"] >= 0
    assert lp.timing([1.0, 2.0, 3.0, 4.0, 100.0]) == {"mean": 22.0, "p50": 3.0, "p95": 100.0, "total": 110.0}
    assert lp.timing([]) == {"mean": None, "p50": None, "p95": None, "total": 0.0}


def test_the_reranker_is_the_node_s_none_or_one_lent_and_only_inside_the_run(monkeypatch):
    """Piece 5's quality half: the same questions with the node's reranker,
    with none, and with another collection's lent — in this process only."""
    from types import SimpleNamespace
    from app import collections as collections_mod
    lent = collections_mod.RerankRecipe(url="http://localai:8080/v1/rerank", api="jina", model="louise-rerank-bge")
    donor = SimpleNamespace(name="gutenberg", reranker=lent)
    monkeypatch.setattr(collections_mod, "load_all", lambda: [donor])
    real = collections_mod.reranker_for
    target = SimpleNamespace(name="normattiva-nemo", reranker=None)
    with lp.reranker_override("configured"):
        assert collections_mod.reranker_for is real
    with lp.reranker_override("configured", donor) as named:
        assert named is lent and collections_mod.reranker_for is real, \
            "configured is untouched, and NAMED for the run's record (2026-11-13)"
    with lp.reranker_override("off"):
        assert collections_mod.reranker_for(target) is None and collections_mod.reranker_for(donor) is None
    with lp.reranker_override("from:gutenberg") as got:
        assert got is lent and collections_mod.reranker_for(target) is lent
    assert collections_mod.reranker_for is real, "given back after the run"
    for bad in ("from:nobody", "sometimes"):
        with pytest.raises(SystemExit):
            with lp.reranker_override(bad):
                pass


# ---- the pool (2026-11-08): one candidate set for the collections compared ---------------------

def test_the_pool_is_the_text_legs_top_records_and_every_gold_record(built):
    cov = lp.coverage(built, CORPUS, QUESTIONS)
    gold = {int(k): lp.gold_from(v) for k, v in cov["gold"].items()}
    law, sard = f"normattiva:{L306_ID}", gold[3].uid
    seen = []

    def text(q, *, collections, limit, as_of):
        seen.append((q, limit, as_of))
        return {"lists": [{"hits": [_hit(f"normattiva:{DEC_A_ID}", 0)]}]}
    got = lp.pool(built, QUESTIONS, gold, depth=50, as_of="2025-01-01", search_fn=text)
    assert {lp._native_of(law), lp._native_of(sard), DEC_A_ID} == set(got["native_ids"])
    assert got["records"] == 3 and len(got["questions"]) == 2 and got["depth"] == 50
    assert all(limit == 50 and as_of == "2025-01-01" for _q, limit, as_of in seen)
    assert got["questions"][0] == {"index": 0, "text_records": 1, "gold_records": 1, "gold_found_by_text": 0}


def test_the_text_search_takes_the_text_leg_alone(monkeypatch):
    from app import query_service
    from app.ask import results
    modes = []
    monkeypatch.setattr(query_service, "run_search",
                        lambda names, q, k, mode, translate, **kw: modes.append(mode) or {"hits": []})
    monkeypatch.setattr(results, "search", lambda q, *, run, **kw: run(["n"], q, 5, "hybrid", False))
    lp.text_search("q", collections=["n"], limit=5, as_of=None)
    assert modes == ["fts"], "no encoder decides the pool"


def test_the_pool_prefilter_restricts_every_leg_through_the_filters_path_and_only_inside(monkeypatch):
    from app import query_service
    monkeypatch.setattr(query_service, "book_ids_for", lambda f: (lambda c: {2, 9}) if f else None)
    stand_in = query_service.book_ids_for
    with lp.pool_prefilter({1, 2, 3}):
        assert query_service.book_ids_for(None)(object()) == {1, 2, 3}, "no filter: the pool"
        assert query_service.book_ids_for({"type": "x"})(object()) == {2}, "a reader's filter still applies"
    assert query_service.book_ids_for is stand_in, "restored after the run"
    with lp.pool_prefilter(None):
        assert query_service.book_ids_for is stand_in, "no pool restricts nothing"


def test_enqueue_queues_only_what_waits_and_the_state_counts_the_pool(built):
    from app import db
    c = built.collection
    con = db.connect(c)
    ids = [int(r[0]) for r in con.execute("SELECT native_id FROM documents ORDER BY rowid")]
    con.execute("UPDATE documents SET embed_done=1 WHERE native_id=?", (str(ids[0]),))
    con.execute("UPDATE documents SET embed_done=-1 WHERE native_id=?", (str(ids[1]),))
    con.commit()
    con.close()
    pool = ids[:3] + [999_999_999]
    assert lp.pool_state(c, pool) == {"embedded": 1, "waiting": 1, "declined": 1, "absent": 1}
    got = lp.enqueue(c, pool)
    assert got["queued"] == 1
    con = db.connect(c)
    queued = [r[0] for r in con.execute("SELECT uid FROM embed_queue")]
    con.close()
    assert queued == [f"normattiva:{ids[2]}"], "a declined, an embedded and an absent record are not queued"


def test_a_search_that_withheld_a_leg_stops_the_run_and_writes_nothing(built):
    cov = lp.coverage(built, CORPUS, QUESTIONS)
    gold = {int(k): lp.gold_from(v) for k, v in cov["gold"].items()}
    withheld = {"lists": [], "legs_withheld": [{"collection": "normattiva", "leg": "vector",
                                                "reason": "run not validated"}]}
    with pytest.raises(lp.chat_eval.SearchWithheld, match="vector"):
        lp.run(built, QUESTIONS, gold, k=5, search_fn=lambda q, **kw: withheld, traverse_fn=None)


def test_a_pooled_run_waits_while_a_pool_record_is_not_embedded(built, tmp_path, monkeypatch, capsys):
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = tmp_path / "pilot"
    (out / "runs").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(CORPUS), out / "corpus.parquet")
    pq.write_table(pa.Table.from_pylist(QUESTIONS), out / "questions-validation.parquet")
    monkeypatch.setattr(lp, "_open", lambda name, root: (built.collection, built.con))
    monkeypatch.setattr(lp, "settle_fn", lambda names: pytest.fail("settled before the pool was checked"))
    (out / "runs" / "pool-p.json").write_text(json.dumps({"native_ids": [L306_ID]}), encoding="utf-8")
    assert lp.main(["run", "--out", str(out), "--collection", "normattiva", "--pool", "p", "--no-reach"]) == 3
    assert "0 embedded" in capsys.readouterr().err


def test_the_pilot_takes_the_first_n_covered_of_every_category(built):
    cov = lp.coverage(built, CORPUS, QUESTIONS)
    gold = {int(k): lp.gold_from(v) for k, v in cov["gold"].items()}
    assert lp.pilot_questions(QUESTIONS, gold, 1) == [0], "the first covered; question 1 is not covered"
    cats = [{**q, "category": c} for q, c in zip(QUESTIONS, ("a", "a", "b", "b"))]
    assert lp.pilot_questions(cats, gold, 1) == [0, 2]
    assert lp.pilot_tag(reranker="from:gutenberg", k=20, pool_name="p", as_of=None).startswith(
        "rerank-from-gutenberg-k20-pool-p-asof-now-code-")


def test_two_runs_compare_question_by_question_and_other_questions_are_refused():
    def run_of(name, scores, pool="p"):
        rows = [{"index": i, "recall@10": r, "mrr@10": r, "ndcg@10": r} for i, r in enumerate(scores)]
        n = len(scores)
        return {"collection": name, "k": 20, "pool": pool, "per_question": rows,
                "summary": {"recall@10": sum(scores) / n, "mrr@10": sum(scores) / n, "ndcg@10": sum(scores) / n,
                            "seconds": {"mean": 0.1}}}
    a, b = run_of("nemo", [1.0] * 30), run_of("dar", [0.0] * 30)
    got = lp.compare(a, b)
    assert got["questions"] == 30 and got["mrr@10"]["a_minus_b"] == 1.0 and got["mrr@10"]["p"] < 0.001
    tie = lp.compare(a, run_of("same", [1.0] * 30))
    assert tie["mrr@10"]["a_minus_b"] == 0.0 and tie["mrr@10"]["p"] == 1.0
    with pytest.raises(SystemExit, match="pool"):
        lp.compare(a, run_of("dar", [0.0] * 30, pool=None))
    with pytest.raises(SystemExit, match="other questions"):
        lp.compare(a, run_of("dar", [0.0] * 29))


def test_pool_and_enqueue_run_from_the_command_line(built, tmp_path, monkeypatch, capsys):
    """München, 2026-11-09: `pool` fell through main() to temporal-run's
    branch (AttributeError on `a.questions`): the commands are run as the
    node runs them, not only their functions."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = tmp_path / "pilot"
    out.mkdir()
    pq.write_table(pa.Table.from_pylist(CORPUS), out / "corpus.parquet")
    pq.write_table(pa.Table.from_pylist(QUESTIONS), out / "questions-validation.parquet")
    monkeypatch.setattr(lp, "_open", lambda name, root: (built.collection, built.con))
    law = f"normattiva:{L306_ID}"
    monkeypatch.setattr(lp, "text_search", lambda q, **kw: {"lists": [{"hits": [_hit(law, 0, rank=0)]}]})
    assert lp.main(["pool", "--out", str(out), "--collection", "normattiva", "--name", "p", "--depth", "10"]) == 0
    pool = json.loads((out / "runs" / "pool-p.json").read_text(encoding="utf-8"))
    assert pool["name"] == "p" and lp._native_of(law) in pool["native_ids"] and pool["depth"] == 10
    assert "pool p:" in capsys.readouterr().out
    assert lp.main(["enqueue", "--out", str(out), "--collection", "normattiva", "--pool", "p"]) == 0
    assert "queued" in capsys.readouterr().out


def test_a_pool_record_becomes_its_work_s_version_in_the_run_s_window():
    """München, 2026-11-13: jf100 held the c.c.'s version shown on 11-09; a
    new one came, the search keeps shown=1, and the pool prefilter dropped
    the live version (rank 1 without the pool, absent with it)."""
    import sqlite3

    from tools import legal_pilot as lp
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE validity (uid TEXT, native_id INTEGER, work TEXT, valid_from INTEGER, "
                "valid_to INTEGER, shown INTEGER)")
    con.executemany("INSERT INTO validity VALUES (?,?,?,?,?,?)", [
        ("n:1", 1, "cc", 20260812, 20261024, 0),      # in the pool, superseded since
        ("n:2", 2, "cc", 20261024, 20261031, 0),
        ("n:3", 3, "cc", 20261031, None, 1),          # the live one
        ("n:10", 10, "repealed", 19900101, 20000101, 1),
    ])
    ids, report = lp.current_versions(con, [1, 3, 10, 99])
    assert ids == {3, 10, 99} and report == {"kept": 3, "moved": 1, "gone": 0}, \
        "the c.c. by its live version, once; a repealed act's last text; a record of no Work kept"
    ids, report = lp.current_versions(con, [1, 10], "2026-10-25")
    assert ids == {2} and report == {"kept": 0, "moved": 1, "gone": 1}, \
        "on a day: the version in force then; an act not in force that day leaves the pool"


def test_the_pilot_report_marks_gold_only_for_its_own_question(tmp_path):
    """A hit that is ANOTHER question's gold is not this one's: the report
    said GOLD while every metric counted 0 (DAR's q2, 2026-11-13)."""
    from types import SimpleNamespace

    from tools import legal_pilot as lp
    held = SimpleNamespace(collection=SimpleNamespace(name="c"))
    gold = {7: lp.Gold(node_id=7, file_name="f", path="p", act=None, token="638")}
    got = {"per_question": [{"index": 0, "relevant": [7], "ranking": [9, 7, -1],
                             "hits": [{"uid": "a", "label": "x"}, {"uid": "b", "label": "y"},
                                      {"uid": "c", "label": "z"}], "seconds": 1.0}]}
    text = lp.pilot_report(held, [{"question": "q"}], got, gold, tmp_path / "r.md", settings={}).read_text()
    lines = [l for l in text.splitlines() if l.strip()[:2] in ("1.", "2.", "3.")]
    assert "gold(other q)" in lines[0] and " GOLD " in lines[1] and "GOLD" not in lines[2] and "gold" not in lines[2]


# ---- the chunker measurement (the architecture session, 2026-11-14) -----------------------------

def test_a_run_restricted_to_one_leg_asks_the_reader_s_search_for_that_leg(monkeypatch):
    from app import query_service
    from app.ask import results
    asked = []
    monkeypatch.setattr(query_service, "run_search", lambda names, q, k, mode, tr, **kw: asked.append(mode) or {})
    monkeypatch.setattr(results, "search", lambda question, *, collections, limit, as_of, run=None:
                        run(collections, question, limit, "hybrid", False) if run else asked.append("own"))
    for mode in ("dense", "fts"):
        lp.leg_search(mode)("q", collections=["c"], limit=5, as_of=None)
    assert asked == ["dense", "fts"]
    assert lp.leg_search("hybrid") is lp.default_search, "hybrid is the reader's own search, untouched"
    with pytest.raises(SystemExit):
        lp.leg_search("catalog")


def test_exact_bypasses_the_vector_index_inside_the_run_only():
    from app import vector_index
    real = vector_index.query

    class Q:
        def bypass_vector_index(self):
            return "flat"
    with lp.exact_vectors(True):
        assert vector_index.query(Q(), None) == "flat"
    assert vector_index.query is real
    with lp.exact_vectors(False):
        assert vector_index.query is real


def test_the_default_tag_is_unchanged_and_a_leg_or_exact_run_has_its_own():
    base = lp.pilot_tag(reranker="off", k=20, pool_name="jf100", as_of=None)
    assert "-legs-" not in base and "-exact" not in base
    assert "-legs-dense-exact-" in lp.pilot_tag(reranker="off", k=20, pool_name="jf100", as_of=None,
                                                legs="dense", exact=True)


def test_the_detectable_difference_is_2_8_sd_over_root_n():
    xa, xb = [1.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 0.0]
    import statistics
    assert lp.detectable(xa, xb) == pytest.approx(2.8015 * statistics.stdev([1, 0, 1, 0]) / 2)


def test_two_runs_on_other_legs_or_search_modes_are_not_compared():
    row = {"index": 0, "recall@10": 1.0}
    a = {"k": 20, "pool": "p", "legs": "dense", "per_question": [row], "summary": {"recall@10": 1.0}}
    b = {**a, "legs": "hybrid"}
    with pytest.raises(SystemExit):
        lp.compare(a, b)
    with pytest.raises(SystemExit):
        lp.compare({**a, "exact": True}, a)
    old = {k: v for k, v in a.items() if k != "legs"}
    assert lp.compare({**old}, {**old, "legs": "hybrid"})["questions"] == 1, "a run before the flag is hybrid"


def test_the_pool_s_chunks_are_counted_from_the_manifest():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE documents (uid TEXT, chunks INTEGER)")
    con.executemany("INSERT INTO documents VALUES (?,?)", [("normattiva:1", 10), ("normattiva:2", 5),
                                                           ("normattiva:3", 7)])
    assert lp.pool_chunks(con, [1, 2, 9], "normattiva") == 15
