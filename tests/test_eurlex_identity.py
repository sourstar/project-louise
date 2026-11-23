"""ONE UNIT, ONE DOCUMENT (2026-11-21: EUR-Lex5 held 37,695 units twice).
A unit dated by its tree's work day while it held no Formex, re-formed into
Formex dated by its wrapper's OJ day, got a second id; discovery never
retired the first. Now: a new id at a unit retires the rows held at that
very locator under the ids the unit could have had (`superseded_ids`) — out
of every index, the validity row forgotten, tombstoned; and a repair pass
for what is held twice already (app/hold/identity.py)."""
from __future__ import annotations

import shutil
from pathlib import Path

from app import collections as collections_mod, db, discovery, fts, unindexable
from app.hold import identity
from app.plugins.base import DiscoverState
from app.plugins.eurlex import SOURCE, rdf
from app.plugins.eurlex import source as src
from tests.acquire.test_eurlex_dump import FORMEX, MTD, _items
from tests.test_eurlex_package import _scoped

CELEX = FORMEX[0]
KEYS = ("seen", "added", "changed", "missing", "container_errors", "relocated", "resigned", "retired",
        "superseded", "withdrawn")


def _coll(tmp_path) -> collections_mod.Collection:
    return collections_mod.Collection(
        name="eu", plugin="eurlex", profile=None, embed_mode_default=None, corpus_dir=tmp_path / "corpus",
        manifest_path=tmp_path / "eu" / "manifest.db", fts_dir=tmp_path / "eu" / "fts",
        lance_dir=tmp_path / "eu" / "lancedb")


def _scan(con, coll):
    refs = [r for r in SOURCE.discover(DiscoverState(corpus_root=coll.corpus_dir)) if r.scope == "document"]
    counts = {k: 0 for k in KEYS}
    assert discovery._apply_batch(con, SOURCE, refs, counts, None, wait=True,
                                  withdraw=lambda c, uids: unindexable.withdraw_many(c, coll, uids),
                                  collection=coll)
    return refs, counts


def test_a_unit_that_gains_its_formex_keeps_one_document(tmp_path, isolated_config):
    coll = _coll(tmp_path)
    unit = coll.corpus_dir / src.unit_rel("it", CELEX)
    unit.mkdir(parents=True)
    (unit / rdf.TREE_FILE).write_bytes(_scoped((FX := FORMEX[2] / MTD).read_bytes()))
    (unit / "L_1972160IT.01000101.doc.html").write_bytes(b"<html><body><p>Articolo 1</p></body></html>")
    con = db.connect(coll)
    try:
        (old,), _ = _scan(con, coll)
        uid_old = f"eurlex:{old.native_id}"
        con.execute("UPDATE documents SET rdf_done=1, fts_done=1, lang='it' WHERE uid=?", (uid_old,))
        con.commit()
        fts.index_book("it", int(old.native_id), [{"id": f"{uid_old}:0:1", "chapter": "", "text": "Articolo 1"}],
                       coll)
        # re-formed: its Formex lands (as the per-unit pass lands a package's members)
        for n, b in _items(FORMEX[2]).items():
            (unit / n).write_bytes(b)
        (new,), counts = _scan(con, coll)
        assert new.native_id != old.native_id, "the wrapper's OJ day is not the tree's work day here"
        assert old.native_id in SOURCE.superseded_ids(new)
        assert counts["retired"] == 1 and counts["added"] == 1, counts
        rows = {r["uid"]: r for r in con.execute("SELECT uid, error, rdf_done, fts_done FROM documents")}
        assert str(rows[uid_old]["error"]).startswith(f"superseded by eurlex:{new.native_id}")
        assert (rows[uid_old]["rdf_done"], rows[uid_old]["fts_done"]) == (0, 0)
        assert fts.books_held("it", [int(old.native_id)], coll) == 0, "out of the text index"
        # a scan again changes nothing: one document, one tombstone
        _refs, again = _scan(con, coll)
        assert again["retired"] == 0 and again["added"] == 0
    finally:
        con.close()


def test_the_units_held_twice_already_are_repaired(tmp_path, isolated_config):
    """EUR-Lex5 as it stands: both rows live at one locator. The one the unit
    names now is kept; the other leaves every index and is tombstoned."""
    coll = _coll(tmp_path)
    unit = coll.corpus_dir / src.unit_rel("it", CELEX)
    shutil.copytree(FORMEX[2], unit, ignore=shutil.ignore_patterns("notice.xml", "LEG_MTD*"))
    con = db.connect(coll)
    try:
        (now,), _ = _scan(con, coll)
        stale = "1234567890123"
        con.execute("INSERT INTO documents(uid, source, native_id, path, rdf_done, fts_done) VALUES(?,?,?,?,1,1)",
                    (f"eurlex:{stale}", "eurlex", stale, now.locator))
        con.commit()
        counts, retire = identity.plan(con, SOURCE, log=lambda m: None)
        assert (counts["shared_units"], counts["retire"], counts["unresolved"]) == (1, 1, 0)
        assert retire == [(f"eurlex:{stale}", f"eurlex:{now.native_id}")]
        assert identity.apply(con, coll, retire) == 1
        err = con.execute("SELECT error FROM documents WHERE uid=?", (f"eurlex:{stale}",)).fetchone()[0]
        assert err.startswith(f"superseded by eurlex:{now.native_id}")
        assert identity.plan(con, SOURCE, log=lambda m: None)[0]["shared_units"] == 0, "repaired once"
    finally:
        con.close()


def test_a_unit_registered_by_the_worker_withdraws_its_old_row_too(tmp_path, isolated_config):
    """Architecture's review of #799 (2026-11-23): `register` (the history's
    adopt path) called `_apply_batch` with neither `withdraw` nor the
    collection: the retired row was tombstoned with its text-index rows
    left, and nothing repairs a tombstone. Now it withdraws from THIS
    collection's indexes, as a scan does."""
    coll = _coll(tmp_path)
    unit = coll.corpus_dir / src.unit_rel("it", CELEX)
    unit.mkdir(parents=True)
    (unit / rdf.TREE_FILE).write_bytes(_scoped((FORMEX[2] / MTD).read_bytes()))
    (unit / "L_1972160IT.01000101.doc.html").write_bytes(b"<html><body><p>Articolo 1</p></body></html>")
    con = db.connect(coll)
    try:
        (old,), _ = _scan(con, coll)
        uid_old = f"eurlex:{old.native_id}"
        con.execute("UPDATE documents SET rdf_done=1, fts_done=1, lang='it' WHERE uid=?", (uid_old,))
        con.commit()
        fts.index_book("it", int(old.native_id), [{"id": f"{uid_old}:0:1", "chapter": "", "text": "Articolo 1"}],
                       coll)
        for n, b in _items(FORMEX[2]).items():
            (unit / n).write_bytes(b)
        new = next(r for r in SOURCE.discover(DiscoverState(corpus_root=coll.corpus_dir)) if r.scope == "document")
        counts = discovery.register(con, coll, [new])
        assert counts["retired"] == 1 and counts["added"] == 1, counts
        assert fts.books_held("it", [int(old.native_id)], coll) == 0, "out of THIS collection's text index"
    finally:
        con.close()
