"""A STAGE THAT MAKES NO PROGRESS SAYS SO BY ITSELF (the lessons of
EUR-Lex5.next, row G3; app/stage_progress.py): the operator's monitor knew a
"hang" and a "done not rising"; the worker says both — one step that has
not ended, or steps tried and none committing, for STALL_S — once, again
every SAY_EVERY_S, and once more when the stage commits again. It reports
and pauses nothing."""
from __future__ import annotations

import threading

import pytest

from tests.conftest import *  # noqa: F401,F403 — the suite's fixtures
from tests.test_worker_memory_deferral import DEFER, _patch, _setup

from app import stage_progress as sp, worker

M = 60.0


def _step(collection, stage, uid, did, *, start, end):
    s = sp.step(collection, stage, uid, now=start)
    s.__enter__()
    s.did = did
    s.end(now=end)


def _look(now):
    said: list[str] = []
    assert sp.look(said.append, now=now) == said
    return said


def test_a_stage_that_commits_is_never_said():
    for i in range(40):
        _step("alpha", "fts", f"u{i}", True, start=i * M, end=i * M + 30)
        assert _look(i * M + 45) == []


def test_one_step_that_has_not_ended_is_said_with_its_document_and_again_later():
    _step("alpha", "fts", "u0", True, start=0, end=10)
    s = sp.step("alpha", "fts", "32018R0688-bg", now=100)
    s.__enter__()
    assert _look(100 + sp.STALL_S - 1) == [], "under the bound: nothing"
    (line,) = _look(100 + sp.STALL_S + 1)
    assert line.startswith("alpha: fts has been in ONE step for 30 min") and "32018R0688-bg" in line
    assert "nothing is paused" in line
    assert _look(100 + sp.STALL_S + 120) == [], "said once per SAY_EVERY_S"
    (again,) = _look(100 + sp.STALL_S + sp.SAY_EVERY_S + 2)
    assert "ONE step for 1 h 00 min" in again
    s.did = True
    s.end(now=100 + 2 * sp.STALL_S + 60)
    (back,) = _look(100 + 2 * sp.STALL_S + 61)
    assert back.startswith("alpha: fts commits again") and _look(100 + 2 * sp.STALL_S + 200) == []


def test_steps_tried_and_none_committing_are_said_with_their_count_and_the_last_document():
    # "did work" is not "made progress": a deferral is a try, never progress
    _step("alpha", "rdf", "ok", True, start=0, end=5)
    for i in range(31):
        _step("alpha", "rdf", f"big-{i}", False, start=10 + i * M, end=12 + i * M)
    (line,) = _look(10 + 31 * M)
    assert line.startswith("alpha: rdf has made NO PROGRESS for 31 min: 31 step(s) ended without a commit")
    assert "the last on big-30" in line
    _step("alpha", "rdf", "big-30", True, start=32 * M, end=32 * M + 5)
    (back,) = _look(32 * M + 6)
    assert "rdf commits again" in back


def test_a_stage_nobody_tries_any_more_is_not_a_stall(monkeypatch):
    # paused, held by a mode, nothing selected: the steps stopped coming
    _step("alpha", "fts", "big", False, start=0, end=5)
    assert _look(5 + sp.STALL_S + 10) == [], "its last try is older than the bound: nobody is trying"
    # ...and when it is tried again after the gap, the clock starts with that step
    _step("alpha", "fts", "big", False, start=3 * sp.STALL_S, end=3 * sp.STALL_S + 5)
    assert _look(3 * sp.STALL_S + 60) == [], "one deferral a minute ago is not thirty minutes without progress"


def test_each_collection_and_stage_is_its_own():
    s = sp.step("alpha", "fts", "stuck", now=0)
    s.__enter__()
    for i in range(40):
        _step("beta", "fts", f"b{i}", True, start=i * M, end=i * M + 1)
        _step("alpha", "rdf", f"r{i}", True, start=i * M, end=i * M + 1)
    (line,) = _look(sp.STALL_S + 1)
    assert line.startswith("alpha: fts has been in ONE step")


def test_the_workers_step_is_noted_as_progress_or_as_a_try(tmp_path, monkeypatch, isolated_config):
    c, con, row = _setup(tmp_path, "fts")
    _patch(monkeypatch, "fts", lambda r: DEFER)
    assert worker._execute_step(con, c, "fts", row) is False
    flow = sp._flows[(c.name, "fts")]
    assert flow["tries"] == 1 and flow["last_uid"] == row["uid"] and flow["in_step"] is None
    assert flow["idle_since"] is not None, "a deferral is a try: the clock runs"


def test_a_step_of_the_worker_that_commits_stops_the_clock(tmp_path, isolated_config):
    c, con, row = _setup(tmp_path, "fts")
    assert worker._execute_step(con, c, "fts", row) is True
    flow = sp._flows[(c.name, "fts")]
    assert flow["tries"] == 0 and flow["idle_since"] is None and flow["in_step"] is None


def test_a_step_left_by_an_exception_is_a_try_that_committed_nothing():
    with pytest.raises(RuntimeError):
        with sp.step("alpha", "embed", "u1"):
            raise RuntimeError("the encoder went away")
    flow = sp._flows[("alpha", "embed")]
    assert flow["in_step"] is None and flow["tries"] == 1 and flow["last_uid"] == "u1"


def test_the_watcher_looks_until_it_is_stopped_and_never_raises(monkeypatch):
    stop, seen = threading.Event(), []

    def look(say):
        seen.append(1)
        if len(seen) == 1:
            raise RuntimeError("a reading that fails is the next look's")
        stop.set()
    monkeypatch.setattr(sp, "look", look)
    t = threading.Thread(target=sp.watch, args=(stop, print), kwargs={"every_s": 0.01})
    t.start()
    t.join(5)
    assert not t.is_alive() and len(seen) == 2
