"""The parts of the update by cadence (app/hold/update.py), one by one: the
cutoff rule's run, the embed step's selection (its plan, and its cost as a
cost test — scheduling.md §6 items 3 and 7), the scan's note, and the night.
The completion check and the old run's gate have their own files
(test_update_completion.py, test_cascade.py)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.hold import update, update_state

# ---------------------------------------------------------------- the run a cutoff names

def test_the_run_is_the_last_one_begun_before_the_cutoff():
    cutoff = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert update.run_before(("20261201", "20261120", "20261101", "latest"), cutoff) == "20261120"
    assert update.run_before(("20261201",), cutoff) is None, "a run of the cutoff's own day is after it"
    assert update.run_before(("../../etc", "2026112"), cutoff) is None, "only YYYYMMDD is a run"


def test_a_night_spans_midnight():
    evening = datetime(2026, 12, 1, 23, 0).astimezone()
    morning = datetime(2026, 12, 2, 4, 0).astimezone()
    assert update.night_of(evening) == update.night_of(morning)
    assert update.night_of(datetime(2026, 12, 2, 23, 0).astimezone()) != update.night_of(morning)


# ---------------------------------------------------------------- the embed step's selection

def _manifest(tmp_path, rows):
    from app import db
    con = sqlite3.connect(tmp_path / "m.sqlite")
    con.executescript(db.SCHEMA)
    con.executemany("INSERT INTO documents(uid, source, native_id, path, in_scope, rdf_done, fts_done, "
                    "embed_done, error) VALUES(?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return con


def _rows(n_errored: int, waiting_at: list[int]):
    rows = [(f"w:{i}", "w", str(i), "p", 1, 1, 1, 1, None) for i in range(1, 2001)]
    # what an update leaves behind: withdrawn pages, errored, marks reset
    rows += [(f"w:e{i}", "w", str(10_000 + i), "p", 1, 0, 0, 0, "withdrawn: gone") for i in range(n_errored)]
    rows += [(f"w:x{i}", "w", str(90_000 + i), "p", 1, 1, 1, 0, None) for i in waiting_at]
    return rows


@pytest.mark.parametrize("sql", [update.EMBED_FIRST_SQL, update.EMBED_LEFT_SQL])
def test_the_embed_probe_is_an_indexed_range_from_its_cursor(tmp_path, sql):
    con = _manifest(tmp_path, _rows(10, [1]))
    params = (1, 1, 0) if sql == update.EMBED_FIRST_SQL else (1, 1, 0, 1000)
    plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql, params))
    assert "idx_documents_scope (in_scope=? AND rdf_done=? AND fts_done=? AND embed_done=? AND rowid>?)" \
        in plan, plan
    # the bounded count reads its own subquery's rows ("SCAN (subquery-1)"),
    # never the table
    assert "TEMP B-TREE" not in plan and "SCAN documents" not in plan, plan


def _steps(con, fn) -> int:
    """SQLite virtual-machine steps a call costs (a progress handler every
    step): the count a cost test asserts, never a wall clock."""
    n = [0]

    def tick():
        n[0] += 1
        return 0
    con.set_progress_handler(tick, 1)
    try:
        fn()
    finally:
        con.set_progress_handler(None, 1)
    return n[0]


def test_the_embed_probe_costs_what_is_left_never_what_was_withdrawn(tmp_path):
    """A cost test (scheduling.md §6 item 7; §5 pitfall 5, the finished
    collection): 20,000 errored rows that will never embed sit in the
    waiting index; the probe walks past them once, and from the cursor it
    then keeps it costs a few steps — the same as with none."""
    con = _manifest(tmp_path, _rows(20_000, [5]))
    con.row_factory = sqlite3.Row
    first = update.embed_waiting(con, 0)
    assert first is not None
    cold = _steps(con, lambda: update.embed_waiting(con, 0))
    warm = _steps(con, lambda: update.embed_waiting(con, first))
    assert warm < 200 < cold, (warm, cold)
    con.execute("UPDATE documents SET embed_done=1 WHERE uid LIKE 'w:x%'")
    assert _steps(con, lambda: update.embed_waiting(con, first)) < 200
    assert update.embed_waiting(con, first) is None
    assert update.embed_left(con, 0) == 0


# ---------------------------------------------------------------- the scan's note

def test_a_scan_tells_the_update_only_when_it_read_the_moved_run_whole(tmp_path):
    state = tmp_path / "wikipedia" / "state"
    c = SimpleNamespace(snapshot="20261101", manifest_path=state / "manifest.sqlite")
    update.note_scan(c, {"seen": 3})
    assert not (state / update_state.FILE).exists(), "no marker: nothing written"
    update_state.write(tmp_path, "wikipedia", {"step": "run", "moved": True, "run": "20261101"})
    update.note_scan(c, {"seen": 3, "interrupted": True})
    assert update_state.read(tmp_path, "wikipedia").get("scan") is None
    update.note_scan(SimpleNamespace(snapshot="20261001", manifest_path=c.manifest_path), {"seen": 3})
    assert update_state.read(tmp_path, "wikipedia").get("scan") is None, "another run's scan"
    update.note_scan(c, {"seen": 3, "changed": 1, "container_errors": 1})
    assert update_state.read(tmp_path, "wikipedia")["scan"]["complete"] is False
    update.note_scan(c, {"seen": 3, "changed": 1})
    scan = update_state.read(tmp_path, "wikipedia")["scan"]
    assert scan["complete"] is True and scan["run"] == "20261101" and scan["counts"]["changed"] == 1


# ---------------------------------------------------------------- the night

def test_the_night_is_the_profile_named_night(monkeypatch):
    from app import config, cpu_limit
    monkeypatch.setattr(config, "UPDATE_PROFILES", ("night",))
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(12, "night"))
    assert update.window_open()[0]
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(12, "quiet", mode="acquire-only"))
    ok, why = update.window_open()
    assert not ok and "night only" in why


def test_a_node_with_no_profile_takes_its_update_hours(monkeypatch):
    from app import config, cpu_limit
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.NO_PROFILE)
    monkeypatch.setattr(config, "UPDATE_HOURS", "00:00-06:00")
    assert update.window_open(datetime(2026, 12, 2, 3, 0).astimezone())[0]
    assert not update.window_open(datetime(2026, 12, 2, 14, 0).astimezone())[0]


def test_each_step_runs_only_where_the_mode_runs_what_it_does(monkeypatch):
    from app import cpu_limit
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.NO_PROFILE)
    assert update.step_allowed("fetch", "acquire-only")
    assert not update.step_allowed("records", "acquire-only")
    assert update.step_allowed("embed", "paused"), "a look at the manifest, nothing run"
    assert not update.step_allowed("fetch", "paused")
