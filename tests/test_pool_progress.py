"""A batch in flight says on what (app/pool_progress.py).

München, on the node, 2026-11-25: EUR-Lex5.next's pipeline sat in its fts
stage for nine minutes and more on one 136 MB act — one pool child busy, ten
idle, the counts still — and nothing in the log said a batch was in flight,
or on what. The pool's own record of its parts, read once a minute by a
watcher: the unit, its size, how long, and for a large part that it has
the large lane and how long its answer is still waited for."""
from __future__ import annotations

import threading

from app import parallel, pool_progress as pp

MIB = 1 << 20
ACT = "lang=bg/2018/32018R0688"


def _part(collection="EUR-Lex5.next", kind="fts", *, since=0.0, uid=ACT, size=136_205_384, rows=1,
          large=False, until=None) -> dict:
    return {"collection": collection, "kind": kind, "rows": rows, "bytes": size, "parse_bytes": size,
            "estimated_peak": 0, "uids": [[uid, size]], "since": since, "large": large, "until": until}


def _look(parts, now):
    said: list[str] = []
    pp.look(in_flight=lambda: parts, say=said.append, now=now)
    return said


def test_a_part_in_flight_for_less_than_a_minute_says_nothing():
    assert _look([_part(since=100.0)], now=159.0) == []


def test_after_a_minute_it_says_the_unit_its_size_and_how_long_then_once_a_minute():
    parts = [_part(since=100.0)]
    first = _look(parts, now=160.0)
    assert first == ["EUR-Lex5.next fts: 1 part in the parse pool; the oldest for 60 s — "
                     f"{ACT}, 129.9 MiB"], first
    assert _look(parts, now=200.0) == [], "not again within the minute"
    again = _look(parts, now=640.0)
    assert len(again) == 1 and "the oldest for 9 min" in again[0], again


def test_a_large_part_says_it_is_alone_in_the_lane_and_how_long_it_is_still_waited_for():
    parts = [_part(since=100.0, large=True, until=388.0)]
    said = _look(parts, now=160.0)[0]
    assert "in the large lane, a process of its own (at most 2 large units at a time in the whole worker" in said
    assert "its answer is waited for 4 min more" in said, said
    assert said.endswith("The collection's pipeline waits for it, its other stages behind it")
    late = _look(parts, now=688.0)[0]
    assert "5 min past the time its size is given" in late, late


def test_several_parts_of_a_collection_are_counted_and_the_oldest_decides():
    parts = [_part(since=300.0, uid="b", size=5 * MIB), _part(since=100.0, uid="a", size=2 * MIB, rows=3),
             _part("gutenberg", "chunks", since=350.0, uid="g")]
    said = _look(parts, now=380.0)
    assert said == ["EUR-Lex5.next fts: 2 parts in the parse pool; the oldest for 5 min — a and 2 more, 2.0 MiB"], said


def test_a_part_that_was_said_and_has_answered_is_said_once_with_how_long_it_took():
    parts = [_part(since=100.0)]
    _look(parts, now=700.0)
    left = _look([], now=1300.0)
    assert left == [f"EUR-Lex5.next fts: {ACT} has left the parse pool after 20 min"], left
    assert _look([], now=1400.0) == []


def test_a_part_that_answered_before_it_was_ever_said_leaves_no_line():
    assert _look([_part(since=100.0)], now=130.0) == []
    assert _look([], now=131.0) == []


def test_the_pool_records_when_a_part_entered_and_whether_it_has_the_large_lane():
    class C:
        name = "EUR-Lex5.next"
    rows = [{"uid": ACT, "size": 136_205_384}]
    parallel._enter(1, "fts", C(), rows, peak=3 << 30, large=True, until=1234.5)
    try:
        got = [p for p in parallel.in_flight() if p["collection"] == "EUR-Lex5.next"][0]
        assert got["large"] is True and got["until"] == 1234.5 and got["since"] > 0
        assert got["uids"] == [[ACT, 136_205_384]]
        said: list[str] = []
        pp.look(say=said.append, now=got["since"] + 600.0)       # the pool's own record, read as it is
        assert said and ACT in said[0] and "in the large lane, a process of its own" in said[0], said
    finally:
        parallel._leave(1)


def test_the_worker_starts_the_watcher_on_a_thread_of_its_own(monkeypatch):
    from app import worker
    started: list[str] = []

    class T:
        def __init__(self, *a, name="", **k):
            self.name = name

        def start(self):
            started.append(self.name)
    monkeypatch.setattr(worker.threading, "Thread", T)
    worker._start_oom_watch()
    assert "pool-progress" in started and "memory-yield" in started, started


def test_a_look_that_fails_does_not_end_the_watcher(monkeypatch):
    stop = threading.Event()
    calls = []

    def boom(**k):
        calls.append(1)
        stop.set()
        raise RuntimeError("no")
    monkeypatch.setattr(pp, "look", boom)
    monkeypatch.setattr(pp, "CHECK_EVERY_S", 0.01)
    said: list[str] = []
    pp.watch(stop, said.append)
    assert calls and "look failed (RuntimeError: no)" in said[0]
