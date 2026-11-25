"""A WORK'S CHAIN FOLLOWS EVERY VERSION THAT LEAVES IT (2026-11-21, the
review's V1 and V2): reconcile's forget left the chain to a `record` that a
re-parse stating no start never brings, and a version moving to another
Work left its old Work's chain stale — a predecessor closed by a version it
no longer holds, no version shown, the Work out of the in-force filter."""
from app import collections as collections_mod, db, validity

DAY = 20251121


def _mk(tmp_path):
    c = collections_mod.Collection(
        name="t", plugin="normattiva", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "c", manifest_path=tmp_path / "m.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "l")
    con = db.connect(c)
    for uid, nid in (("t:1", 1), ("t:2", 2)):
        con.execute("INSERT INTO documents(uid, source, native_id, path, rdf_done, fts_done, embed_done) "
                    "VALUES(?,?,?,?,1,1,1)", (uid, "t", str(nid), f"/x/{nid}"))
    con.commit()
    return c, con


def _meta(start, work="W"):
    return {"from": start, "work": work, "has_text": True, "in_force": None, "keys": [], "closes": []}


def _rows(con):
    return {r["uid"]: (r["work"], r["valid_from"], r["valid_to"], r["shown"])
            for r in con.execute("SELECT * FROM validity")}


def test_forget_without_chain_then_no_start(tmp_path):
    _, con = _mk(tmp_path)
    validity.record(con, "t:1", 1, _meta(20200101), day=DAY)
    validity.record(con, "t:2", 2, _meta(20220101), day=DAY)
    con.commit()
    assert _rows(con)["t:1"] == ("W", 20200101, 20220101, 0)
    assert _rows(con)["t:2"][3] == 1
    # carry.reconcile step 1 (app/hold/carry.py:1429-1430)
    validity.forget(con, "t:2", chain=False)
    # the rdf stage's re-parse states no start (app/worker.py:234-239)
    validity.forget(con, "t:2", day=DAY)
    con.commit()
    rows = _rows(con)
    print(rows)
    # what a build by the book gives: t:1 open and shown
    assert rows["t:1"] == ("W", 20200101, None, 1)


def test_record_moving_a_version_to_another_work(tmp_path):
    _, con = _mk(tmp_path)
    validity.record(con, "t:1", 1, _meta(20200101), day=DAY)
    validity.record(con, "t:2", 2, _meta(20220101), day=DAY)
    # a re-parse names another Work for t:2
    validity.record(con, "t:2", 2, _meta(20220101, work="W2"), day=DAY)
    con.commit()
    rows = _rows(con)
    print(rows)
    assert rows["t:1"] == ("W", 20200101, None, 1)


def test_forget_many_gives_forget_one_after_another_and_recomputes_each_work_once(tmp_path, monkeypatch):
    """2026-11-25: discovery retired thousands of re-formed rows one `forget`
    each, a language's versions of one act each recomputing the same Work."""
    def build(path):
        c = collections_mod.Collection(
            name="t", plugin="normattiva", profile=None, embed_mode_default=None,
            corpus_dir=path / "c", manifest_path=path / "m.db", fts_dir=path / "fts", lance_dir=path / "l")
        con = db.connect(c)
        for i in range(1, 7):
            con.execute("INSERT INTO documents(uid, source, native_id, path, rdf_done, fts_done, embed_done) "
                        "VALUES(?,?,?,?,1,1,1)", (f"t:{i}", "t", str(i), f"/x/{i}"))
        for i, (start, work) in enumerate([(20200101, "W"), (20210101, "W"), (20220101, "W"),
                                           (20200101, "V"), (20210101, "V"), (20230101, "V")], 1):
            validity.record(con, f"t:{i}", i, _meta(start, work=work), day=DAY)
        con.commit()
        return con
    one = build(tmp_path / "one")
    for uid in ("t:2", "t:3", "t:5"):
        validity.forget(one, uid, day=DAY)
    one.commit()
    many = build(tmp_path / "many")
    calls = []
    real = validity.recompute
    monkeypatch.setattr(validity, "recompute", lambda con, work, **k: calls.append(work) or real(con, work, **k))
    validity.forget_many(many, ["t:2", "t:3", "t:5", "t:2"], day=DAY)
    many.commit()
    assert _rows(many) == _rows(one)
    assert sorted(calls) == ["V", "W"], "each Work once"
