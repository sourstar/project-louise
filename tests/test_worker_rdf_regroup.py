"""A LANGUAGE THAT LEAVES ITS TEXT-INDEX GROUP (architecture's review of #776;
München, pubmed 2026-11-19: ~297k documents held in "en" and in their own
group). The rdf stage writes `lang`: where the new language's groups leave
one of the manifest's, the document's rows there are deleted — that writer
committed BEFORE the batch's SQLite commit — and its fts is done again in the
same UPDATE, so the fts stage writes the new group alone. Nothing for a
document whose group stays: no fts writer is touched."""
from __future__ import annotations

import pytest

from app import fts, parallel, worker
from tests.test_fts_batching import _discovered, _pending


def _indexed_en(tmp_path):
    c, con = _discovered(tmp_path)
    con.execute("UPDATE documents SET lang='en'")
    con.commit()
    while _pending(con) is not None:
        assert worker._execute_step(con, c, "fts", _pending(con)) is True
    row = con.execute("SELECT * FROM documents ORDER BY native_id LIMIT 1").fetchone()
    assert fts.books_held("en", [int(row["native_id"])], c) > 0 and row["fts_version"] > 0
    con.execute("UPDATE documents SET rdf_done=0 WHERE uid=?", (row["uid"],))
    con.commit()
    return c, con, row


def _parse_as(monkeypatch, uid: str, lang: str | None):
    real = parallel.metadata_for_rows

    def as_lang(collection, rows):
        got = real(collection, rows)
        return [(ok, {**meta, "lang": lang} if ok and r["uid"] == uid and lang else meta)
                for r, (ok, meta) in zip(rows, got)]
    monkeypatch.setattr(parallel, "metadata_for_rows", as_lang)


def _now(con, uid):
    return con.execute("SELECT lang, rdf_done, fts_done FROM documents WHERE uid=?", (uid,)).fetchone()


def test_an_rdf_re_run_moving_a_document_out_of_en_leaves_no_en_row(tmp_path, monkeypatch, isolated_config):
    c, con, row = _indexed_en(tmp_path)
    nid, other = int(row["native_id"]), fts.group_for("pl")
    assert other != "en"
    _parse_as(monkeypatch, row["uid"], "pl")
    assert worker._execute_step(con, c, "rdf", row) is True
    assert tuple(_now(con, row["uid"])) == ("pl", 1, 0), "its fts done again in the same UPDATE"
    assert fts.books_held("en", [nid], c) == 0, "its en rows gone before the marks"
    while _pending(con) is not None:
        worker._execute_step(con, c, "fts", _pending(con))
    assert fts.books_held(other, [nid], c) > 0 and fts.books_held("en", [nid], c) == 0, \
        "re-indexed in its new group alone"


def test_an_rdf_re_run_that_keeps_the_group_touches_no_fts_writer(tmp_path, monkeypatch, isolated_config):
    c, con, row = _indexed_en(tmp_path)
    deleted = []
    monkeypatch.setattr(fts, "delete_books", lambda *a, **k: deleted.append(a) or 0)
    _parse_as(monkeypatch, row["uid"], "en")
    assert worker._execute_step(con, c, "rdf", row) is True
    assert deleted == [] and tuple(_now(con, row["uid"])) == ("en", 1, 1)


def test_a_stop_between_the_delete_and_the_sqlite_commit_is_found_again(tmp_path, monkeypatch, isolated_config):
    """The delete is committed first (#769's rule): a stop after it rolls
    the batch back — the document's rdf still pending, its old language
    still the manifest's — and the next rdf pass finds the same move, sets
    its fts pending, and its re-index writes the new group. Never a stale
    copy, never a document out of the index for good."""
    c, con, row = _indexed_en(tmp_path)
    nid = int(row["native_id"])
    _parse_as(monkeypatch, row["uid"], "pl")

    def stopped(collection, entries):
        raise RuntimeError("stopped between the delete and the commit")
    real_write = worker.catalog.write
    monkeypatch.setattr(worker.catalog, "write", stopped)
    con.execute("BEGIN IMMEDIATE")
    with pytest.raises(RuntimeError, match="between the delete and the commit"):
        worker.stage_rdf(con, row, c)
    con.execute("ROLLBACK")
    assert fts.books_held("en", [nid], c) == 0, "deleted first"
    assert tuple(_now(con, row["uid"])) == ("en", 0, 1), "the batch rolled back: rdf pending, the old language"
    monkeypatch.setattr(worker.catalog, "write", real_write)
    assert worker._execute_step(con, c, "rdf", row) is True
    assert tuple(_now(con, row["uid"])) == ("pl", 1, 0)
    while _pending(con) is not None:
        worker._execute_step(con, c, "fts", _pending(con))
    assert fts.books_held(fts.group_for("pl"), [nid], c) > 0 and fts.books_held("en", [nid], c) == 0


def test_a_document_never_indexed_costs_no_delete(tmp_path, monkeypatch, isolated_config):
    """A fresh document (fts_version 0, fts pending) has no row anywhere: its
    first language costs no fts writer."""
    c, con = _discovered(tmp_path)
    row = con.execute("SELECT * FROM documents ORDER BY native_id LIMIT 1").fetchone()
    con.execute("UPDATE documents SET rdf_done=0, lang=NULL WHERE uid=?", (row["uid"],))
    con.commit()
    deleted = []
    monkeypatch.setattr(fts, "delete_books", lambda *a, **k: deleted.append(a) or 0)
    _parse_as(monkeypatch, row["uid"], "pl")
    assert worker._execute_step(con, c, "rdf", con.execute("SELECT * FROM documents WHERE uid=?",
                                                           (row["uid"],)).fetchone()) is True
    assert deleted == []
