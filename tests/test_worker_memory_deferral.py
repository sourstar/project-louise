"""P1 of the EUR-Lex review (2026-11-21): a deferral of the parse pool — the
large lane or the pool's memory (`MemoryWait`), a memory kill's hold
(`MemoryKilled`), a batch stopped at a part boundary (`Stopped`) — on the
SELECTED row of an rdf or fts batch was written onto the document as
`error=…, retries=1` by `worker._execute_step`, which never asked
`transient_failure`: the large acts went out of every selection. A deferral
leaves the row pending and untouched; and it costs that row alone, never the
batch's other rows (a large act deferred round after round would otherwise
hold its collection's stage back for as long as the lane is busy)."""
from __future__ import annotations

import pytest

from tests.conftest import *  # noqa: F401,F403 — the suite's fixtures
from tests.test_fts_batching import _discovered

from app import parallel, worker

DEFER = (False, {"type": "MemoryWait", "msg": "no room for a 3.9 GB parse within 120 s"})
STOPPED = (False, {"type": "Stopped", "msg": "its batch was stopped"})


def _state(con, uid):
    return tuple(con.execute("SELECT error, retries, rdf_done, fts_done FROM documents WHERE uid=?",
                             (uid,)).fetchone())


def _patch(monkeypatch, stage, answer):
    """The pool's answer per row, `answer(row) -> (ok, value) | None` (None:
    the real parse)."""
    if stage == "rdf":
        real = parallel.metadata_for_rows

        def meta(collection, rows):
            got = real(collection, rows)
            return [answer(r) or g for r, g in zip(rows, got)]
        monkeypatch.setattr(parallel, "metadata_for_rows", meta)
    else:
        real = parallel.fts_for_rows

        def fts(collection, rows, *, names):
            got = real(collection, rows, names=names)
            return [answer(r) or g for r, g in zip(rows, got)]
        monkeypatch.setattr(parallel, "fts_for_rows", fts)


def _setup(tmp_path, stage):
    c, con = _discovered(tmp_path)
    if stage == "rdf":
        con.execute("UPDATE documents SET rdf_done=0, fts_done=0")
        con.commit()
    return c, con, con.execute("SELECT * FROM documents ORDER BY rowid LIMIT 1").fetchone()


@pytest.mark.parametrize("why", [DEFER, STOPPED], ids=["MemoryWait", "Stopped"])
@pytest.mark.parametrize("stage", ["fts", "rdf"])
def test_a_deferral_of_the_whole_batch_writes_nothing_on_the_document(stage, why, tmp_path, monkeypatch,
                                                                      isolated_config):
    """The reviewer's repro: every row of the batch deferred. Before: the
    selected row was left `error='fts: no room for a 3.9 GB parse within
    120 s', retries=1`, out of every selection."""
    c, con, row = _setup(tmp_path, stage)
    before = _state(con, row["uid"])
    _patch(monkeypatch, stage, lambda r: why)
    assert worker._execute_step(con, c, stage, row) is False, "a deferral, not an attempt"
    assert _state(con, row["uid"]) == before, "the row untouched: no error, no retry, no mark"
    assert getattr(worker._deferral, "memory", False) is (why is DEFER)


@pytest.mark.parametrize("stage", ["fts", "rdf"])
def test_a_deferred_selected_row_costs_itself_never_its_batch(stage, tmp_path, monkeypatch, isolated_config):
    c, con, row = _setup(tmp_path, stage)
    peers = [r["uid"] for r in con.execute("SELECT uid FROM documents WHERE uid<>?", (row["uid"],))]
    assert peers, "the fixture has peers"
    before = _state(con, row["uid"])
    deferring = {row["uid"]}
    _patch(monkeypatch, stage, lambda r: DEFER if r["uid"] in deferring else None)
    assert worker._execute_step(con, c, stage, row) is True, "its peers were written: progress"
    assert _state(con, row["uid"]) == before, "the deferred row left pending, untouched"
    col = 2 if stage == "rdf" else 3
    assert all(_state(con, u)[col] == 1 and _state(con, u)[0] is None for u in peers), \
        f"every peer {stage}-done: {[_state(con, u) for u in peers]}"
    # and its own turn comes again, and it goes through once the pool takes it
    deferring.clear()
    again = con.execute("SELECT * FROM documents WHERE uid=?", (row["uid"],)).fetchone()
    assert worker._execute_step(con, c, stage, again) is True
    assert _state(con, row["uid"])[col] == 1


def test_a_memory_kill_is_held_and_never_written_on_the_document(tmp_path, monkeypatch, isolated_config):
    """The operator's rule of 2026-11-19 (#779): the first memory kill is not
    computed again; the worker's hold is written by its handler. The row it
    was parsing is not the document's fault: nothing is written on it."""
    c, con, row = _setup(tmp_path, "fts")
    before = _state(con, row["uid"])

    def killed(collection, rows, *, names):
        raise parallel.MemoryKilled(3, 4, "fts of alpha")
    monkeypatch.setattr(parallel, "fts_for_rows", killed)
    assert worker._execute_step(con, c, "fts", row) is False
    assert _state(con, row["uid"]) == before
    assert worker.transient_failure(parallel.MemoryKilled(3, 4, "x"))


def test_a_too_large_document_is_still_its_error(tmp_path, monkeypatch, isolated_config):
    """`TooLarge` is not a deferral: past the most the worker can ever give,
    a person must see it (app/parallel.py `never_fits`)."""
    c, con, row = _setup(tmp_path, "fts")
    _patch(monkeypatch, "fts", lambda r: (False, {"type": "TooLarge", "msg": "estimated at 24.0 GB"}))
    assert worker._execute_step(con, c, "fts", row) is True
    err, retries, _rdf, fts_done = _state(con, row["uid"])
    assert "TooLarge" in err and retries == 1 and fts_done == 0
