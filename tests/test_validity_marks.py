"""V6 of the EUR-Lex review (2026-11-21): the maintainer's daily
`validity.roll` (and a neighbour's `validity.record`) resets `fts_done` and
`embed_done` of a version whose (valid_to, shown) moved, so its stages
rewrite its index rows. The fts and embed stages read the columns BEFORE
their long work and marked unconditionally after it: a reset in between was
lost — the text index and the vectors kept the old `shown`. A mark is now
written only while the version table still holds the columns the rows were
written with."""
from __future__ import annotations

import pytest

from tests.conftest import *  # noqa: F401,F403 — the suite's fixtures
from tests.test_validity import V12_ID, _run, _table, versioned  # noqa: F401 — the fixture

from app import db, embeddings, fts, validity, worker


def _the_roll_meanwhile(c, native_id: int):
    """What `validity.recompute` writes for a version whose end and `shown`
    moved — from ANOTHER connection, as the maintainer's thread does."""
    other = db.connect(c)
    try:
        other.execute("BEGIN IMMEDIATE")
        uid = other.execute("SELECT uid FROM validity WHERE native_id=?", (native_id,)).fetchone()[0]
        other.execute("UPDATE validity SET valid_to=20300101, shown=0, synced=0 WHERE uid=?", (uid,))
        other.execute("UPDATE documents SET fts_done=0, embed_done=0 WHERE uid=? AND rdf_done=1", (uid,))
        other.execute("COMMIT")
    finally:
        other.close()


def _doc(con, native_id: int):
    return con.execute("SELECT * FROM documents WHERE native_id=?", (str(native_id),)).fetchone()


def test_a_roll_during_the_text_index_keeps_its_reset(versioned, monkeypatch):
    c, con, _ = versioned
    _run(con, c, "rdf")
    assert _table(con)[V12_ID]["shown"] == 1
    real = fts.index_books
    fired: list = []

    def index_then_roll(entries, collection=None):
        real(entries, collection)
        if not fired:                                  # the roll lands between the index and the marks
            fired.append(1)
            _the_roll_meanwhile(c, V12_ID)
    monkeypatch.setattr(fts, "index_books", index_then_roll)
    worker.stage_fts(con, _doc(con, V12_ID), c)
    if con.in_transaction:
        con.execute("COMMIT")
    assert fired
    assert _doc(con, V12_ID)["fts_done"] == 0, "the roll's reset survives: its rows hold the old shown"
    # its turn again: indexed with the columns in force, and gone from today's view
    monkeypatch.setattr(fts, "index_books", real)
    _run(con, c, "fts")
    assert _doc(con, V12_ID)["fts_done"] == 1
    shown = {int(h["book_id"]) for h in fts.search("it", "Sardegna", limit=50, collection=c,
                                                    validity=validity.window(c, None))}
    assert V12_ID not in shown, "today's view no longer shows the version the roll ended"


def test_a_roll_during_an_embed_keeps_its_reset(versioned, monkeypatch):
    c, con, _ = versioned
    _run(con, c, "rdf", "fts")
    real = embeddings.upsert_book
    fired: list = []

    def embed_then_roll(native_id, *a, **k):
        got = real(native_id, *a, **k)
        if int(native_id) == V12_ID and not fired:
            fired.append(1)
            _the_roll_meanwhile(c, V12_ID)
        return got
    monkeypatch.setattr(embeddings, "upsert_book", embed_then_roll)
    worker.stage_embed(con, _doc(con, V12_ID), c)
    assert fired
    t = _table(con)[V12_ID]
    assert (_doc(con, V12_ID)["embed_done"], t["synced"]) == (0, 0), \
        "the reset survives: neither embedded nor synced from the old columns"
    monkeypatch.setattr(embeddings, "upsert_book", real)
    worker.stage_embed(con, _doc(con, V12_ID), c)            # its turn again
    assert (_doc(con, V12_ID)["embed_done"], _table(con)[V12_ID]["synced"]) == (1, 1)
    rows = embeddings._conn(c).open_table(embeddings.TABLE).search().where(
        f"book_id = {V12_ID}", prefilter=True).limit(500).to_list()
    assert rows and all(r["valid_to"] == 20300101 and r["shown"] == 0 for r in rows)


def test_a_roll_during_the_batched_embed_keeps_its_reset(versioned, monkeypatch):
    """`_embed_parallel` (every encoder's producers) marks through
    `embed_marks.mark_embedded`: the same condition, per uid."""
    c, con, _ = versioned
    from dataclasses import replace
    from app import licensing
    c = replace(c, license_ack=licensing.make_ack(c))       # the producers' selection is gated (invariant 9)
    _run(con, c, "rdf", "fts")
    real = embeddings.upsert_book
    fired: list = []

    def embed_then_roll(native_id, *a, **k):
        got = real(native_id, *a, **k)
        if int(native_id) == V12_ID and not fired:
            fired.append(1)
            _the_roll_meanwhile(c, V12_ID)
        return got
    monkeypatch.setattr(embeddings, "upsert_book", embed_then_roll)
    worker._embed_parallel(con, c, c.embed, slots=1)
    if con.in_transaction:
        con.execute("COMMIT")
    assert fired
    assert (_doc(con, V12_ID)["embed_done"], _table(con)[V12_ID]["synced"]) == (0, 0)
    others = con.execute("SELECT COUNT(*) FROM documents WHERE embed_done=1").fetchone()[0]
    assert others >= 1, "the others of the batch are marked as before"


def test_the_condition_marks_a_record_without_a_version_row(versioned):
    """A record the version table does not know reads as its id's default
    (`current_columns`): marked, as before."""
    c, con, _ = versioned
    cond, params = validity.still("normattiva:nowhere", validity.current_columns(20200101000000))
    assert con.execute(f"SELECT {cond}", params).fetchone()[0] == 1
    assert validity.moved_since(con, {}) == set()


@pytest.mark.parametrize("n", [1, 500])
def test_the_recheck_is_one_indexed_read_per_500(versioned, n):
    c, con, _ = versioned
    _run(con, c, "rdf")
    plan = " ".join(r[3] for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT native_id, valid_from, valid_to, shown FROM validity "
        f"WHERE native_id IN ({','.join('?' * n)})", list(range(n))))
    assert "idx_validity_native" in plan and "SCAN" not in plan, plan
