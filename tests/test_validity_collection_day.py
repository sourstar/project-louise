"""Architecture's review of #799, defect 6 (2026-11-23): `shown` recomputed
by discovery's retirement, identity's repair, write_amendments and the
rederive's reconcile followed the UTC day, not the collection's own
(`validity.today(collection)`, its Codex's clock), as the rdf stage does."""
from __future__ import annotations

from app import collections as collections_mod, db, validity
from app.hold import identity

DAY = 19991231              # a day no clock gives today: where it arrives, it was passed


def _coll(tmp_path):
    return collections_mod.Collection(
        name="t", plugin="eurlex", profile=None, embed_mode_default=None, corpus_dir=tmp_path / "c",
        manifest_path=tmp_path / "m.db", fts_dir=tmp_path / "f", lance_dir=tmp_path / "l")


def test_identity_s_repair_recomputes_on_the_collection_s_day(tmp_path, monkeypatch):
    c = _coll(tmp_path)
    con = db.connect(c)
    con.execute("INSERT INTO documents(uid, source, native_id, path) VALUES('e:old','eurlex','1','/x/1')")
    con.commit()
    validity.record(con, "e:old", 1, {"from": 20130101, "work": "W", "has_text": True, "in_force": 1,
                                      "keys": [], "closes": []})
    con.commit()
    seen = []
    monkeypatch.setattr(validity, "today", lambda collection=None, now=None: DAY)
    real = validity.forget
    monkeypatch.setattr(validity, "forget", lambda con_, uid, day=None, **k: seen.append(day) or real(con_, uid, day=day))
    monkeypatch.setattr("app.unindexable.withdraw_many", lambda con_, coll, uids: None)
    assert identity.apply(con, c, [("e:old", "e:new")]) == 1
    assert seen == [DAY]


def test_write_amendments_recomputes_on_the_day_it_is_given(tmp_path, monkeypatch):
    c = _coll(tmp_path)
    con = db.connect(c)
    validity.declare_changes_end(con, True)
    validity.record(con, "e:t", 1, {"from": 20130101, "work": "T/en", "has_text": True, "in_force": 1,
                                    "keys": ["celex:T"], "closes": []})
    seen = []
    real = validity.recompute
    monkeypatch.setattr(validity, "recompute", lambda con_, work, **k: seen.append(k.get("day")) or real(con_, work, **k))
    validity.write_amendments(con, [("e:a", [("celex:T", "", "", 20150101, "substitution")])], day=DAY)
    assert seen and set(seen) == {DAY}


def test_discovery_s_retirement_forgets_on_the_collection_s_day(monkeypatch):
    from app import discovery
    seen = []
    monkeypatch.setattr(validity, "forget", lambda con, uid, day=None, **k: seen.append(day))
    discovery._forget_validity(None, "e:x", DAY)
    assert seen == [DAY]
    import inspect
    src = inspect.getsource(discovery._apply_batch)
    assert "(uid_alt, vday)" in src and "_validity.today(collection)" in src
