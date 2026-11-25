"""The build's checks: is this collection made as the platform asks?
(app/build_check.py; `louise hold check <collection> --build`.)

The operator, 2026-11-25: "a consistency check shall be available also for
whom is willing to create a compliant collection", and "Every problem
encountered with a collection must become a lesson learned reflected in
RAGE". Two of EUR-Lex5's lessons, each counted by hand that day, as checks
anyone can run on their own collection:
- one unit, one live row (37,695 and then 405 units were held twice);
- a document indexed with no chunk states why (57 were told apart by hand).
"""
from __future__ import annotations

import json
import time
import types
from pathlib import Path

import pytest

from app import build_check as bc, collections as collections_mod, db, louise, worker


@pytest.fixture
def built(isolated_config):
    """The test collection, scanned: three documents in its manifest."""
    c = collections_mod.by_name("gutenberg")
    con = db.connect(c)
    worker._scan_only(con, c, "startup")
    assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] >= 3
    return c, con


def _uids(con, n: int) -> list[str]:
    return [r[0] for r in con.execute("SELECT uid FROM documents ORDER BY uid LIMIT ?", (n,))]


class OneDocumentPerUnit:
    """A plugin whose unit is one document: it names the id a unit has now."""
    ISSUES = {"title-only": "the source holds only a title"}

    def __init__(self, now: dict[str, str]) -> None:
        self.now = now

    def ref_of_unit(self, path: Path):
        uid = self.now.get(str(path))
        if uid is None:
            return None
        source, native = uid.split(":", 1)
        return types.SimpleNamespace(source_id=source, native_id=native)


# ---- one unit, one live row ---------------------------------------------------------------

def test_a_unit_held_by_two_live_rows_fails_and_names_which_to_retire(built):
    c, con = built
    a, b, other = _uids(con, 3)
    con.execute("UPDATE documents SET path='/corpus/unit-7' WHERE uid IN (?,?)", (a, b))
    got = bc.check_units(con, OneDocumentPerUnit({"/corpus/unit-7": b}))
    assert got.result == bc.FAIL and "1 unit(s) are held by more than one live row" in got.says
    assert got.detail["first"] == [{"retire": a, "keep": b}]
    assert "superseded_ids" in got.supplies and "retire-superseded" in got.supplies
    # the earlier row retired, as discovery retires one: the unit has one live row again
    con.execute("UPDATE documents SET error='superseded by ' || ? WHERE uid=?", (b, a))
    got = bc.check_units(con, OneDocumentPerUnit({"/corpus/unit-7": b}))
    assert got.result == bc.PASS and "no unit held twice" in got.says


def test_a_container_of_many_documents_is_not_judged_by_its_locator(built):
    # a PubMed file, a Wikipedia dump: rows share their locator by design
    c, con = built
    got = bc.check_units(con, c.plugin_instance())
    assert got.result == bc.INCONCLUSIVE and "ref_of_unit" in got.supplies


# ---- a document with no chunk says why ----------------------------------------------------

def test_a_document_indexed_with_no_chunk_and_no_stated_reason_fails(built):
    c, con = built
    a, b, other = _uids(con, 3)
    con.execute("UPDATE documents SET fts_done=1, chunks=12")
    assert bc.check_chunkless(con, c.plugin_instance()).result == bc.PASS
    con.execute("UPDATE documents SET chunks=0 WHERE uid IN (?,?)", (a, b))
    got = bc.check_chunkless(con, OneDocumentPerUnit({}))
    assert got.result == bc.FAIL and "2 of 2 document(s) indexed with no chunk state no reason" in got.says
    assert got.detail["first"] == [a, b] and "title-only" in got.detail["codes_declared"]
    assert "ISSUES" in got.supplies and "a parse to fix" in got.supplies
    # one states its reason: counted by reason; the other still fails the check
    now = int(time.time())
    con.execute("INSERT INTO issues(uid, code, detail, first_seen, last_seen) VALUES(?,?,?,?,?)",
                (a, "title-only", "empty ENACTING.TERMS", now, now))
    got = bc.check_chunkless(con, OneDocumentPerUnit({}))
    assert got.result == bc.FAIL and got.detail["by_reason"] == {"title-only": 1} and got.detail["first"] == [b]
    con.execute("INSERT INTO issues(uid, code, detail, first_seen, last_seen) VALUES(?,?,?,?,?)",
                (b, "no-text-layer", "a scan", now, now))
    got = bc.check_chunkless(con, OneDocumentPerUnit({}))
    assert got.result == bc.PASS and got.detail["by_reason"] == {"no-text-layer": 1, "title-only": 1}
    assert "each with its stated reason" in got.says


def test_a_document_in_error_or_out_of_scope_is_not_a_chunkless_document(built):
    c, con = built
    a, b, other = _uids(con, 3)
    con.execute("UPDATE documents SET fts_done=1, chunks=5")
    con.execute("UPDATE documents SET chunks=0, error='parse: boom' WHERE uid=?", (a,))
    con.execute("UPDATE documents SET chunks=0, in_scope=0 WHERE uid=?", (b,))
    assert bc.check_chunkless(con, c.plugin_instance()).result == bc.PASS


# ---- the run, and the command ----------------------------------------------------------------

def test_the_checks_read_only_and_say_so_where_nothing_was_built(built, tmp_path):
    c, con = built
    con.commit() if con.in_transaction else None
    before = Path(c.manifest_path).read_bytes()
    assert [v.check for v in bc.run(c)] == ["units", "chunkless"]
    assert Path(c.manifest_path).read_bytes() == before, "a check writes nothing"
    nowhere = types.SimpleNamespace(manifest_path=tmp_path / "none.db")
    got = bc.run(nowhere)
    assert {v.result for v in got} == {bc.INCONCLUSIVE} and "received from a peer" in got[0].supplies
    with pytest.raises(ValueError, match="no such build check"):
        bc.run(c, ["rescan-not-built"])


def test_one_command_for_a_collections_author(built, capsys):
    c, con = built
    con.execute("UPDATE documents SET fts_done=1, chunks=3")
    capsys.readouterr()
    assert louise.main(["hold", "check", "gutenberg", "--build"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["collection"] == "gutenberg" and out["failed"] == []
    assert [v["check"] for v in out["build"]] == ["units", "chunkless"]
    assert all(v["says"] for v in out["build"]), "every verdict in plain words"
    (a,) = _uids(con, 1)
    con.execute("UPDATE documents SET chunks=0 WHERE uid=?", (a,))
    assert louise.main(["hold", "check", "gutenberg", "--build", "--checks", "chunkless"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["failed"] == ["chunkless"] and out["build"][0]["supplies"]
    assert louise.main(["hold", "check", "gutenberg", "--build", "--checks", "nope"]) == 2
