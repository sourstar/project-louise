"""AN AMENDMENT HELD ENDS THE TEXT IT REPLACED (2026-11-21, the EUR-Lex5
review's V3 and V4). EUR-Lex's base holds originals; the consolidations are
its history slice, fetched on request. An original amended since, with no
consolidation held, was the "text in force today"; a consolidation fetched
for an as-of day became the open-ended version every later reader got. The
amending act's own statement (its `eli:amends` edge, in this collection's
corpus) says the next version starts on its day: the chain ends there, held
or not (app/validity.py `first_change`)."""
from app import collections as collections_mod, db, validity
from app.ask import articles

DAY = 20251121
BASE = "celex:32013R0575"


def _mk(tmp_path, uids):
    c = collections_mod.Collection(
        name="t", plugin="eurlex", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "c", manifest_path=tmp_path / "m.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "l")
    con = db.connect(c)
    for n, uid in enumerate(uids, 1):
        con.execute("INSERT INTO documents(uid, source, native_id, path, rdf_done, fts_done, embed_done) "
                    "VALUES(?,?,?,?,1,1,1)", (uid, "eurlex", str(n), f"/x/{n}"))
    con.commit()
    return c, con


def _version(start, work="32013R0575/en", keys=(BASE,), amends=()):
    return {"from": start, "work": work, "has_text": True, "in_force": 1, "keys": list(keys), "closes": [],
            "amends": [(t, "", "", None, "amends") for t in amends]}


def _row(con, uid):
    r = con.execute("SELECT valid_from, valid_to, shown FROM validity WHERE uid=?", (uid,)).fetchone()
    return tuple(r) if r is not None else None


def _record(con, uid, nid, meta):
    validity.record(con, uid, nid, meta, day=DAY)
    con.commit()


def test_an_original_amended_since_is_not_the_text_in_force(tmp_path):
    _c, con = _mk(tmp_path, ["e:orig", "e:amending"])
    _record(con, "e:orig", 1, _version(20130628))
    assert _row(con, "e:orig") == (20130628, None, 1)
    # the 2019 amending regulation, its own Work, states it amends the 2013 act
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"],
                                           amends=[BASE]))
    assert _row(con, "e:orig") == (20130628, 20190627, 1), "ended at the amendment; still the one shown"
    v = articles._chosen(articles._versions(con, "32013R0575/en"), None)
    assert articles._ended(v, None, today=lambda: DAY)
    note = articles._ended_note(con, "32013R0575/en", v, articles._DEFAULT_NOTES)
    assert note == "text until 2019-06-27; amended since, the amended text is not held"
    # an as-of day after it admits no text of the act: it is not held
    assert articles._chosen(articles._versions(con, "32013R0575/en"), 20200101) is None
    assert articles._chosen(articles._versions(con, "32013R0575/en"), 20150101)["uid"] == "e:orig"


def test_the_order_the_versions_arrive_in_does_not_matter(tmp_path):
    _c, con = _mk(tmp_path, ["e:orig", "e:amending"])
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"],
                                           amends=[BASE]))
    _record(con, "e:orig", 1, _version(20130628))
    assert _row(con, "e:orig") == (20130628, 20190627, 1)


def test_a_version_fetched_as_of_a_day_is_closed_by_the_next_amendment(tmp_path):
    """V4: the 2015 consolidation, fetched for as-of 2015-06-01, is not the
    text of 2024: the 2019 amendment held ends it."""
    _c, con = _mk(tmp_path, ["e:orig", "e:a2014", "e:a2019", "e:c2015"])
    _record(con, "e:orig", 1, _version(20130628))
    _record(con, "e:a2014", 2, _version(20140301, work="32014R0001/en", keys=["celex:32014R0001"], amends=[BASE]))
    _record(con, "e:a2019", 3, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"], amends=[BASE]))
    _record(con, "e:c2015", 4, _version(20150101))            # the history fetch, registered
    assert _row(con, "e:orig") == (20130628, 20150101, 0)
    assert _row(con, "e:c2015") == (20150101, 20190627, 1)
    versions = articles._versions(con, "32013R0575/en")
    assert articles._chosen(versions, 20240101) is None, "2024: not held, never the 2015 text"
    assert articles._chosen(versions, 20150601)["uid"] == "e:c2015"


def test_a_change_held_as_a_version_ends_nothing_more(tmp_path):
    _c, con = _mk(tmp_path, ["e:orig", "e:amending", "e:c2019"])
    _record(con, "e:orig", 1, _version(20130628))
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"],
                                           amends=[BASE]))
    _record(con, "e:c2019", 3, _version(20190627))            # the consolidation of that day, held
    assert _row(con, "e:orig") == (20130628, 20190627, 0)
    assert _row(con, "e:c2019") == (20190627, None, 1), "the text in force: open"
    assert validity.changed_at(con, "32013R0575/en", {"valid_from": 20130628, "valid_to": 20190627}) is None


def test_forgetting_the_amending_act_reopens_the_text(tmp_path):
    _c, con = _mk(tmp_path, ["e:orig", "e:amending"])
    _record(con, "e:orig", 1, _version(20130628))
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"],
                                           amends=[BASE]))
    validity.forget(con, "e:amending", day=DAY)
    con.commit()
    assert _row(con, "e:orig") == (20130628, None, 1)
    # a re-parse that no longer states the amendment withdraws it the same way
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"],
                                           amends=[BASE]))
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"]))
    assert _row(con, "e:orig") == (20130628, None, 1)


def test_what_is_not_a_change(tmp_path):
    """A whole-act repeal is a closure's, never a change; a statement by a
    version of the same Work is not one from outside; a day before the
    version's start changes nothing. A repeal of ONE article is a change."""
    _c, con = _mk(tmp_path, ["e:orig", "e:rep", "e:self"])
    _record(con, "e:orig", 1, _version(20130628))
    validity._record_amendments(con, "e:rep", [(BASE, "", "", 20200101, "repeals"),
                                               (BASE, "", "", 20200101, "repeal")])
    validity._record_amendments(con, "e:orig", [(BASE, "", "", 20200101, "substitution")])
    assert validity.first_change(con, "32013R0575/en", 20130628) is None
    validity._record_amendments(con, "e:rep", [(BASE, "5", "", 20200101, "repeal"),
                                               (BASE, "", "", 20100101, "substitution")])
    assert validity.first_change(con, "32013R0575/en", 20130628) == 20200101
    # a version naming the act that amended it, and the day (a passive statement)
    validity._record_amendments(con, "e:rep", [])
    validity._record_amendments(con, "e:orig", [(BASE, "", "celex:32021R0001", 20210601, "amended_by")])
    assert validity.first_change(con, "32013R0575/en", 20130628) == 20210601
    validity._record_amendments(con, "e:orig", [(BASE, "", "celex:32021R0001", None, "amended_by")])
    assert validity.first_change(con, "32013R0575/en", 20130628) is None, "no day named: none to end at"


def test_a_repeal_on_the_day_of_a_change_reads_as_the_last_text(tmp_path):
    _c, con = _mk(tmp_path, ["e:orig", "e:amending"])
    meta = {**_version(20130628), "closes": []}
    _record(con, "e:orig", 1, meta)
    con.execute("INSERT INTO validity_closures(key, by_uid, closed_at) VALUES(?,?,?)", (BASE, "e:x", 20190627))
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"],
                                           amends=[BASE]))
    v = articles._versions(con, "32013R0575/en")[-1]
    assert v["valid_to"] == 20190627 and validity.changed_at(con, "32013R0575/en", v) is None
    assert articles._ended_note(con, "32013R0575/en", v, articles._DEFAULT_NOTES) == "last text in force"


def test_a_held_manifest_is_rechained_once_resumably(tmp_path):
    _c, con = _mk(tmp_path, ["e:orig", "e:amending", "e:other"])
    _record(con, "e:orig", 1, _version(20130628))
    _record(con, "e:other", 3, _version(20100101, work="32010R0001/en", keys=["celex:32010R0001"]))
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"],
                                           amends=[BASE]))
    # the chain as the old rule left it, the index rows current
    con.execute("UPDATE validity SET valid_to=NULL WHERE uid='e:orig'")
    con.execute("UPDATE documents SET fts_done=1, embed_done=1")
    con.commit()
    assert not validity.chain_current(con)
    stops = iter([False, True])
    works, moved, done = validity.rechain(con, day=DAY, window=1, should_stop=lambda: next(stops))
    assert (works, done) == (1, False), "one window, then the stop: its cursor committed"
    works, moved, done = validity.rechain(con, day=DAY, window=1)
    assert done and works == 2 and moved == 1 and validity.chain_current(con)
    assert _row(con, "e:orig") == (20130628, 20190627, 1)
    marks = {r[0]: (r[1], r[2]) for r in con.execute("SELECT uid, fts_done, embed_done FROM documents")}
    assert marks == {"e:orig": (0, 0), "e:amending": (1, 1), "e:other": (1, 1)}, "only the moved re-indexed"
    assert validity.rechain(con, day=DAY) == (0, 0, True)


def test_the_measure_counts_what_the_rechain_will_move(tmp_path):
    from tools.measure import validity_changed_since as m
    _c, con = _mk(tmp_path, ["e:orig", "e:amending", "e:later"])
    _record(con, "e:orig", 1, _version(20130628))
    _record(con, "e:later", 3, _version(20100101, work="32010R0001/en", keys=["celex:32010R0001"]))
    _record(con, "e:amending", 2, _version(20190627, work="32019R0876/en", keys=["celex:32019R0876"],
                                           amends=[BASE, "celex:32010R0001"]))
    validity._record_amendments(con, "e:x", [("celex:32010R0001", "", "", 20300101, "substitution")])
    con.execute("UPDATE validity SET valid_to=NULL")
    con.commit()
    got = m.measure(con, today=DAY, examples=5)
    assert (got["works_targeted"], got["would_end"], got["ended_already"], got["ends_later"]) == (2, 2, 2, 0)
    assert got["by_year"] == {"2019": 2}
    assert {e["work"] for e in got["examples"]} == {"32013R0575/en", "32010R0001/en"}


def test_the_change_query_is_indexed(tmp_path):
    """Run by every chain recompute (the rdf stage, per record): each table
    by its index, no temp B-tree, no list subquery."""
    _c, con = _mk(tmp_path, [])
    import re as _re
    src = __import__("inspect").getsource(validity.first_change)
    sql = "".join(_re.findall(r'"([^"]*)"', src.split("con.execute(", 1)[1].split(".fetchone()", 1)[0]))
    sql = sql.replace("{','.join('?' for _ in WHOLE_REPEALS)}", ",".join("?" for _ in validity.WHOLE_REPEALS))
    plan = [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql,
                                      ("w", *validity.WHOLE_REPEALS, "w", 1))]
    assert any("idx_validity_works_work" in p for p in plan), plan
    assert any("idx_validity_amendments_target" in p for p in plan), plan
    assert not any("TEMP B-TREE" in p or "LIST SUBQUERY" in p or p.startswith("SCAN") for p in plan), plan
