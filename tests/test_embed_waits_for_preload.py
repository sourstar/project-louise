"""A collection's own embedding waits for the keeper's preload after a start
(app/resident.py `holds_embedding`, app/worker.py `_preload_holds`).

gutenberg on the node, 2026-11-25 (München): the worker started at 09:40:28,
its embed producer at :31, the keeper began loading thirteen models at :36,
and at :55 one context of the collection's encoder failed its canary on a
card with 1,493 MiB free. The run was set aside and gated again forty
minutes later — a race the worker made with itself, at every start."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app import db, resident, worker


@pytest.fixture
def keeper(monkeypatch):
    """A keeper whose mode preloads, with no beat yet and nothing loading."""
    resident.reset()
    monkeypatch.setattr(resident, "preloads", lambda state=None: (True, "running"))
    monkeypatch.setattr(resident, "_resumed_at", lambda: None)
    yield
    resident.reset()


def _loading(monkeypatch, alive: bool) -> None:
    monkeypatch.setattr(resident, "_warm_thread", SimpleNamespace(is_alive=lambda: alive))


def test_before_the_keepers_first_beat_a_start_waits_and_not_for_ever(keeper):
    why = resident.holds_embedding(started=1000.0, now=1003.0)       # the producer, 3 s after the start
    assert why and "has not made its first beat yet (3 s after the worker's start)" in why
    assert resident.holds_embedding(started=1000.0, now=1000.0 + resident.FIRST_BEAT_WAIT_S) is None, \
        "a keeper that never beats holds nobody"


def test_while_the_preload_loads_after_a_start_embedding_waits(keeper, monkeypatch):
    resident._note_first_beat()
    _loading(monkeypatch, True)
    why = resident.holds_embedding(started=1000.0, now=1024.0)       # 24 s after the start: the node's case
    assert why == "the keeper is still loading the models of Ask after the worker's start (24 s ago)"
    _loading(monkeypatch, False)
    assert resident.holds_embedding(started=1000.0, now=1090.0) is None, "the loads have ended"


def test_nothing_is_held_past_the_window_and_a_later_reload_holds_nothing(keeper, monkeypatch):
    resident._note_first_beat()
    _loading(monkeypatch, True)
    assert resident.holds_embedding(started=1000.0, now=1000.0 + resident.START_HOLD_S) is None
    assert resident.holds_embedding(started=1000.0, now=9000.0) is None, \
        "a single reload hours later: a hold at each would starve embedding"


def test_a_resume_from_a_mode_that_preloads_nothing_is_a_start_too(keeper, monkeypatch):
    import time
    resident._note_first_beat()
    resumed = time.time() - 30                      # the node resumed thirty seconds ago
    monkeypatch.setattr(resident, "_resumed_at", lambda: resumed)
    monkeypatch.setattr(resident, "_last_beat_wall", resumed + 5)      # the keeper has beaten since
    _loading(monkeypatch, True)
    why = resident.holds_embedding(started=1000.0, now=90000.0)
    assert why == "the keeper is still loading the models of Ask after the node's resume"
    _loading(monkeypatch, False)
    assert resident.holds_embedding(started=1000.0, now=90000.0) is None


def test_after_a_resume_embedding_waits_for_the_keepers_next_beat_and_not_for_ever(keeper, monkeypatch):
    """The gap of a start, at a resume (an engine swap pauses the node and
    empties the card): the run mode is written, the embed producers see it
    within two seconds, and the keeper starts its loads only at its next
    beat. Until it has beaten once SINCE the resume, embedding waits."""
    import time
    resident._note_first_beat()                     # it beat long ago, before the pause
    resumed = time.time() - 3
    monkeypatch.setattr(resident, "_resumed_at", lambda: resumed)
    monkeypatch.setattr(resident, "_last_beat_wall", resumed - 6000)
    _loading(monkeypatch, False)                    # no load has started yet: the gap
    why = resident.holds_embedding(started=1000.0, now=90000.0)
    assert why and "has not made a beat since the node's resume (3 s ago)" in why, why
    resident._note_first_beat()                     # the beat after the resume
    assert resident.holds_embedding(started=1000.0, now=90000.0) is None, "beaten, and nothing to load"
    # a keeper that never beats again holds nobody past its bound
    monkeypatch.setattr(resident, "_last_beat_wall", resumed - 6000)
    monkeypatch.setattr(resident, "_resumed_at", lambda: time.time() - resident.FIRST_BEAT_WAIT_S - 1)
    assert resident.holds_embedding(started=1000.0, now=90000.0) is None


def test_a_mode_that_preloads_nothing_holds_nothing(keeper, monkeypatch):
    monkeypatch.setattr(resident, "preloads", lambda state=None: (False, "the node is acquire-only"))
    assert resident.holds_embedding(started=1000.0, now=1003.0) is None


def test_the_first_beat_is_noted_after_its_loads_have_started(keeper, monkeypatch):
    """No gap: by the time the first beat is known, the loads it started are
    already seen as loading."""
    order: list[str] = []
    monkeypatch.setattr(resident, "_start_warm", lambda due, stop: order.append("warm") or [])
    real = resident._note_first_beat
    monkeypatch.setattr(resident, "_note_first_beat", lambda: (order.append("noted"), real())[1])
    resident.beat([], embed_mode=lambda c: "off", state="running")
    assert order[-1] == "noted" and resident._first_beat is not None


def test_the_workers_gate_defers_says_so_once_and_then_that_it_goes_on(isolated_config, monkeypatch, tmp_path):
    backend_gate = worker.backend_gate
    collection = SimpleNamespace(name="gutenberg")
    con = db.connect()
    asked: list[int] = []
    monkeypatch.setattr(worker, "_integrity_halts", lambda con, c: False)
    monkeypatch.setattr(worker, "_endpoint_pause_holds", lambda con, c: False)
    monkeypatch.setattr(worker, "_endpoint_busy_holds", lambda c: False)
    monkeypatch.setattr(worker, "_quarantine_allows", lambda con, c, what: True)
    monkeypatch.setattr(backend_gate, "embed_allowed", lambda con, c, stop=None: asked.append(1) or True)
    why = ["the keeper is still loading the models of Ask after the worker's start (24 s ago)"]
    monkeypatch.setattr(resident, "holds_embedding", lambda started, now=None: why[0])
    for _ in range(4):
        assert worker._embedding_allowed(con, collection) is False
    assert asked == [], "the gate's own probe does not go out beside the loads either"
    lines = [r[0] for r in con.execute("SELECT message FROM events WHERE stage='embed' ORDER BY rowid")]
    assert len(lines) == 1 and lines[0].startswith("embedding waits: the keeper is still loading"), lines
    assert "at most 10 min after a start" in lines[0]
    why[0] = None
    assert worker._embedding_allowed(con, collection) is True and asked == [1]
    lines = [r[0] for r in con.execute("SELECT message FROM events WHERE stage='embed' ORDER BY rowid")]
    assert len(lines) == 2 and lines[1].startswith("embedding goes on"), lines
    assert worker._embedding_allowed(con, collection) is True
    assert con.execute("SELECT COUNT(*) FROM events WHERE stage='embed'").fetchone()[0] == 2


def test_a_worker_long_started_never_waits(isolated_config):
    assert worker._BORN < 0, "the suite's worker started long ago (tests/conftest.py)"
    assert resident.holds_embedding(worker._BORN) is None


def test_a_copy_whose_run_is_untested_is_not_gated_beside_the_loads_either(isolated_config, monkeypatch):
    """`_settle_the_run` gates a version-3 copy at once, with nothing to
    embed: that gate's own load and probes wait for the preload as well."""
    from app import copy_state, run_check
    collection = SimpleNamespace(name="legi_F")
    con = db.connect()
    asked: list[int] = []
    monkeypatch.setattr(run_check, "applies", lambda c: True)
    monkeypatch.setattr(copy_state, "of", lambda c: SimpleNamespace(
        test=lambda name: SimpleNamespace(status=copy_state.OPEN)))
    monkeypatch.setattr(worker.backend_gate, "embed_allowed", lambda con, c, stop=None: asked.append(1) or True)
    why = ["the keeper is still loading the models of Ask after the worker's start (24 s ago)"]
    monkeypatch.setattr(resident, "holds_embedding", lambda started, now=None: why[0])
    worker._settle_the_run(con, collection)
    assert asked == []
    why[0] = None
    worker._settle_the_run(con, collection)
    assert asked == [1]


def test_the_resume_is_read_from_the_run_modes_own_history(isolated_config):
    """No fixture of this file here: the real `_resumed_at`, on the test's own
    node store."""
    from app import node_state
    resident.reset()
    node_state.set_worker_state("paused", by="an engine swap")
    node_state.set_worker_state("running", by="the swap is done")
    assert resident._resumed_at() is not None
    node_state.set_worker_state("acquire-only", by="t")
    assert resident._resumed_at() is None, "running to acquire-only: both preload, nothing resumed"
    resident.reset()
