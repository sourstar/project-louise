"""app/parallel.py `compute` after the EUR-Lex review of 2026-11-21.

P2: one deadline for the whole batch; a timeout threw the pool away and
computed the WHOLE batch again in the main process (the rows already answered
included); after PARSE_MAX_FAILURES the pool was off for the process's life
(`reset_failures` had no caller) — the fts crawl, 0.7 -> 73 ms/doc on one
core. Now: the watchdog is per part (no answer for PARSE_TIMEOUT_S), what
answered is kept, nothing heavy is parsed in the main process, and a disabled
pool comes back on probation.

P7: a stop was not looked at inside `compute` (a large part: ~730 s against
docker's 120 s grace). Now a stop is honoured at part boundaries and bounds
every wait inside it."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app import config, parallel, scheduled_checks, worker

K = 26
MID = 60 * 2 ** 20                                   # a 60 MB act: ~1.5 GB at 26x
BIG = parallel.LARGE_PEAK_BYTES // K + 1              # one byte past the lane's threshold


class _Plugin:
    PARSE_PEAK_PER_BYTE = K


def _coll(k: bool = True):
    plugin = _Plugin() if k else object()
    return SimpleNamespace(name="eurlex", plugin_instance=lambda: plugin)


def _rows(sizes):
    return [{"uid": f"e:{i}", "size": s} for i, s in enumerate(sizes)]


class _Pool:
    """Real futures, answered by the test (`answer`) or on a timer."""

    def __init__(self):
        self.futures: list[tuple[list[dict], Future]] = []

    def submit(self, fn, kind, collection, part):
        f = Future()
        f.set_running_or_notify_cancel()
        self.futures.append((part, f))
        return f

    def answer(self, i: int, after: float = 0.0) -> None:
        part, f = self.futures[i]
        rows = [(True, r["uid"]) for r in part]
        if after:
            threading.Timer(after, lambda: f.done() or f.set_result(rows)).start()
        else:
            f.set_result(rows)


class _Own:
    """A PROCESS OF ITS OWN, as `_alone` starts one for a part that was in
    flight when the pool failed — here a future the test decides: answered at
    once, or never for a part holding a uid of `never`. `ran` records what
    was handed to such processes, `ended` those terminated."""
    ran: list[list[str]] = []
    never: set[str] = set()
    ended: list[list[str]] = []
    refuse: BaseException | None = None

    def __init__(self):
        self.part: list[str] = []

    def submit(self, fn, kind, collection, part, allowance):
        if _Own.refuse is not None:
            raise _Own.refuse
        self.part = [r["uid"] for r in part]
        _Own.ran.append(self.part)
        f = Future()
        f.set_running_or_notify_cancel()
        if not set(self.part) & _Own.never:
            f.set_result([(True, r["uid"]) for r in part])
        return f

    def shutdown(self, wait=True, cancel_futures=False):
        if cancel_futures:                           # `discard`: the process terminated
            _Own.ended.append(self.part)


@pytest.fixture
def own(monkeypatch):
    _Own.ran, _Own.never, _Own.ended, _Own.refuse = [], set(), [], None
    monkeypatch.setattr(parallel, "_lane_executor", _Own)
    monkeypatch.setattr(parallel, "ISOLATE_START_S", 0.0)
    return _Own


@pytest.fixture
def here(monkeypatch):
    """Every row computed in THIS process, by uid."""
    seen: list[str] = []

    def compute(kind, collection, part):
        seen.extend(r["uid"] for r in part)
        return [(True, r["uid"]) for r in part]
    monkeypatch.setattr(parallel, "_compute", compute)
    monkeypatch.setattr(parallel, "_partition", lambda c, rows, want: [[r] for r in rows])
    monkeypatch.setattr(scheduled_checks, "cgroup_memory", lambda cgroup=None: None)
    monkeypatch.setattr(config, "PARSE_PROCESSES", 4)
    parallel.forget_users()
    parallel.reset_failures()
    yield seen
    parallel.reset_failures()
    assert parallel._large_lane.acquire(blocking=False), "every test gives the lane back"
    parallel._large_lane.release()
    assert parallel.reserved() == 0, "every reservation given back"


# ---- P2: the watchdog is per part; what answered is kept ------------------------------

def test_a_part_that_answered_is_kept_when_another_times_out(here, own, monkeypatch):
    """Before: the timeout sent the WHOLE batch to `_in_process` (4 rows
    parsed again here, the 3 answered ones included). Then only the part that
    did not answer was computed here — and on 2026-11-24 that part held the
    document the child had hung on: the worker's own thread hung on it too,
    the GIL with it, for 21 minutes. Now it is parsed again in a process of
    its own, and nothing that was in flight is computed here."""
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 0.3)
    real_submit = pool.submit

    def submit(fn, kind, collection, part):
        f = real_submit(fn, kind, collection, part)
        if part[0]["uid"] != "e:1":                  # e:1 never answers
            pool.answer(len(pool.futures) - 1)
        return f
    pool.submit = submit
    rows = _rows([1, 1, 1, 1])
    t0 = time.monotonic()
    got = parallel.compute("fts", _coll(k=False), rows)
    assert got == [(True, r["uid"]) for r in rows]
    assert here == [], f"nothing that was in flight is computed in this process: {here}"
    assert own.ran == [["e:1"]], "the part that did not answer, in a process of its own"
    assert time.monotonic() - t0 < 3
    assert parallel.health()["failures"] == 1, "it came back alone: the pool's failure, counted"


def test_a_document_that_does_not_come_back_alone_is_an_error_on_it(here, own, monkeypatch, capsys):
    """The node, 2026-11-24: one document on which the parse never ends. Its
    part does not answer in the pool; parsed again in a process of its own,
    the part does not come back; each of its rows alone, one does not: THAT
    document is answered as an error (it leaves every selection until
    someone looks), its neighbour is parsed, nothing is computed in this
    process, and the failure is the document's — the pool is not switched
    off for it (off, its work would run in this process)."""
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "_partition", lambda c, rows, want: [rows[i:i + 2] for i in range(0, len(rows), 2)])
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(config, "PARSE_MAX_FAILURES", 1)
    own.never = {"e:3"}
    real_submit = pool.submit

    def submit(fn, kind, collection, part):
        f = real_submit(fn, kind, collection, part)
        if "e:3" not in {r["uid"] for r in part}:
            pool.answer(len(pool.futures) - 1)
        return f
    pool.submit = submit
    rows = _rows([1, 1, 1, 1, 1, 1])
    t0 = time.monotonic()
    got = parallel.compute("fts", _coll(k=False), rows)
    assert [g for i, g in enumerate(got) if i != 3] == [(True, r["uid"]) for i, r in enumerate(rows) if i != 3]
    assert got[3][0] is False and got[3][1]["type"] == "ParseNoAnswer", got[3]
    assert not worker._deferred_row(got[3][1]), "an error on the document, not a deferral"
    assert here == [], f"nothing computed in this process: {here}"
    assert own.ran == [["e:2", "e:3"], ["e:2"], ["e:3"]], "its part, then each of its rows alone"
    assert own.ended == [["e:2", "e:3"], ["e:3"]], "the processes that did not come back, terminated"
    assert time.monotonic() - t0 < 5, "bounded: twice the timeout, not for ever"
    health = parallel.health()
    assert health["failures"] == 0 and health["disabled"] is False, "the document's failure, not the pool's"
    err = capsys.readouterr().err
    assert "document e:3 did not come back from a process of its own" in err
    # the pool's lines say WHEN and WHERE (the node's line of 2026-11-24 said neither)
    line = next(ln for ln in err.splitlines() if "[parse pool] TimeoutError" in ln)
    assert re.match(r"\d\d:\d\d:\d\d \[parse pool\] TimeoutError in fts of eurlex: ", line), line
    # LEFT PENDING as a peer, it comes back, in the end as its batch's only
    # row: one light row is computed in this process — not this one. It is
    # answered as it was, with no pool, no process and nothing computed here
    asked = len(pool.futures)
    again = parallel.compute("fts", _coll(k=False), [rows[3]])
    assert again[0][0] is False and again[0][1]["type"] == "ParseNoAnswer"
    assert here == [] and len(pool.futures) == asked and len(own.ran) == 3
    both = parallel.compute("fts", _coll(k=False), [rows[3], {"uid": "e:9", "size": 1}])
    assert both[0][1]["type"] == "ParseNoAnswer" and both[1] == (True, "e:9"), "its neighbours go on, in order"


def test_where_no_process_can_be_started_the_rows_wait_and_are_not_parsed_here(here, own, monkeypatch):
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 0.3)
    own.refuse = OSError("cannot allocate memory")
    real_submit = pool.submit

    def submit(fn, kind, collection, part):
        f = real_submit(fn, kind, collection, part)
        if part[0]["uid"] != "e:1":
            pool.answer(len(pool.futures) - 1)
        return f
    pool.submit = submit
    got = parallel.compute("fts", _coll(k=False), _rows([1, 1, 1]))
    assert got[0] == (True, "e:0") and got[2] == (True, "e:2")
    assert got[1][0] is False and worker._deferred_row(got[1][1]), "deferred: asked again, the document untouched"
    assert here == []


def test_a_stop_ends_the_second_parse_and_its_processes(here, own, monkeypatch):
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(parallel, "ISOLATE_START_S", 30.0)          # it would wait half a minute
    own.never = {"e:1"}
    real_submit = pool.submit

    def submit(fn, kind, collection, part):
        f = real_submit(fn, kind, collection, part)
        if part[0]["uid"] != "e:1":
            pool.answer(len(pool.futures) - 1)
        return f
    pool.submit = submit
    stop = threading.Event()
    threading.Timer(0.8, stop.set).start()
    t0 = time.monotonic()
    with parallel.controlled(stop=stop):
        got = parallel.compute("fts", _coll(k=False), _rows([1, 1, 1]))
    assert time.monotonic() - t0 < 5, "the stop bounds the wait"
    assert got[1][0] is False and got[1][1]["type"] == "Stopped" and here == []
    assert own.ended == [["e:1"]], "its process terminated"


def test_the_watchdog_waits_for_no_progress_not_for_the_whole_batch(here, monkeypatch):
    """Parts answering 1.2 s apart under a 1.5 s bound: every one within the
    bound of the one before it. Before: the batch's deadline (1.5 s from its
    start) passed while the second part was still computing, and the batch
    was parsed again here."""
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 1.5)
    rows = _rows([1, 1, 1])
    real_submit = pool.submit

    def submit(fn, kind, collection, part):
        f = real_submit(fn, kind, collection, part)
        i = len(pool.futures) - 1
        pool.answer(i, after=1.2 * (i + 1))
        return f
    pool.submit = submit
    got = parallel.compute("fts", _coll(k=False), rows)
    assert got == [(True, r["uid"]) for r in rows]
    assert here == [], f"nothing computed here: {here}"
    assert parallel.health()["failures"] == 0


def test_after_a_failure_nothing_heavy_is_parsed_in_the_main_process(here, monkeypatch):
    """A 60 MB act is ~1.5 GB at 26x: below the lane, above
    IN_PROCESS_PEAK_BYTES. Before: a broken pool sent it to `_in_process`,
    which parsed everything under the lane's threshold here. Now it is
    deferred (MemoryWait) and the light rows are computed here."""
    from concurrent.futures.process import BrokenProcessPool

    class Broken:
        def submit(self, *a, **k):
            raise BrokenProcessPool("a child died")
    monkeypatch.setattr(parallel, "pool", lambda: Broken())
    monkeypatch.setattr(parallel, "pool_memory", lambda: None)
    got = parallel.compute("fts", _coll(), _rows([1, MID, 1]))
    assert got[0] == (True, "e:0") and got[2] == (True, "e:2")
    assert got[1][0] is False and got[1][1]["type"] == "MemoryWait"
    assert "e:1" not in here
    with pytest.raises(parallel.MemoryWait):
        parallel.raise_for(got[1][1])
    # and while the pool is OFF, the same: the light rows here, the heavy deferred
    monkeypatch.setattr(parallel, "pool", lambda: None)
    here.clear()
    got = parallel.compute("fts", _coll(), _rows([1, MID]))
    assert got[0] == (True, "e:0") and got[1][1]["type"] == "MemoryWait" and here == ["e:0"]


def test_a_disabled_pool_comes_back_on_probation(here, monkeypatch, capsys):
    """Before: after PARSE_MAX_FAILURES the pool was off until a restart, and
    nothing called `reset_failures`. Now it is off for PARSE_RETRY_S, then
    tried again with one failure to spare; a second disable is twice as long,
    and an answered batch clears the count."""
    made: list = []
    monkeypatch.setattr(parallel, "_Executor", lambda **kw: made.append(kw) or SimpleNamespace())
    monkeypatch.setattr(parallel, "discard", lambda ex, *a, **k: None)
    monkeypatch.setattr(config, "PARSE_MAX_FAILURES", 2)
    monkeypatch.setattr(config, "PARSE_RETRY_S", 0.2)
    monkeypatch.setattr(config, "PARSE_RETRY_MAX_S", 10)
    parallel._note_failure(TimeoutError("one"))
    parallel._note_failure(TimeoutError("two"))
    assert parallel.health()["disabled"] is True
    assert parallel.pool() is None, "off for now"
    time.sleep(0.25)
    assert parallel.pool() is not None and made, "back after PARSE_RETRY_S"
    assert parallel.health()["disabled"] is False and parallel.health()["failures"] == 1, "on probation"
    assert "on probation" in capsys.readouterr().err
    parallel._note_failure(TimeoutError("three"))                     # the probation's one failure
    assert parallel.health()["disabled"] is True
    assert 0.3 < parallel._disabled_until - time.monotonic() <= 0.4, "off twice as long"
    time.sleep(0.45)
    assert parallel.pool() is not None
    parallel._note_success()                                         # a batch answered
    assert parallel.health()["failures"] == 0 and parallel._disabled_times == 0
    parallel.shutdown()


# ---- P7: a stop at part boundaries; every wait bounded by it --------------------------

def test_a_stop_ends_a_batch_at_its_next_part_boundary(here, monkeypatch):
    """A large part that would run for ever (32021R2017 el: ~730 s) and the
    worker's stop set 0.3 s in: `compute` returns within a slice, the part
    that answered kept, the rest Stopped (a deferral), the lane given back.
    Before: it waited for the large part until its own deadline."""
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "pool_memory", lambda: None)
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 5.0)

    class Lane:
        def __init__(self, kind, collection, part, allowance):
            self.part, self._f = part, Future()
            self._f.set_running_or_notify_cancel()
            self.cancelled = False
            lanes.append(self)

        @property
        def future(self):
            return self._f

        def add_done_callback(self, cb):
            self._f.add_done_callback(lambda _f: cb(self))

        def cancel(self):
            self.cancelled = True
            return False

        def result(self, timeout=None):
            return self._f.result(timeout)
    lanes: list = []
    monkeypatch.setattr(parallel, "_LaneTask", Lane)
    real_submit = pool.submit

    def submit(fn, kind, collection, part):
        f = real_submit(fn, kind, collection, part)
        pool.answer(len(pool.futures) - 1)
        return f
    pool.submit = submit
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    beats: list[float] = []
    t0 = time.monotonic()
    with parallel.controlled(stop=stop, beat=lambda: beats.append(time.monotonic())):
        got = parallel.compute("fts", _coll(), _rows([1, BIG, 1, 1]))
    took = time.monotonic() - t0
    assert took < 0.3 + 2 * parallel.WAIT_SLICE_S, f"the stop took {took:.2f} s"
    assert got[0] == (True, "e:0")
    assert got[1][0] is False and got[1][1]["type"] == "Stopped"
    assert lanes and lanes[0].cancelled, "the large part's own process let go of"
    assert beats, "the wait said it was alive"
    with pytest.raises(parallel.Stopped):
        parallel.raise_for(got[1][1])
    assert worker.transient_failure(parallel.Stopped("x")), "a deferral, never an error"


def test_a_stop_set_before_a_batch_computes_nothing(here):
    stop = threading.Event()
    stop.set()
    with parallel.controlled(stop=stop):
        got = parallel.compute("fts", _coll(k=False), _rows([1, 1]))
    assert [v["type"] for _ok, v in got] == ["Stopped", "Stopped"] and here == []
    # the stop is the calling thread's, and only inside `controlled`
    assert parallel.compute("fts", _coll(k=False), _rows([1])) == [(True, "e:0")]


def test_the_waits_for_the_lane_and_for_room_end_with_the_stop(here, monkeypatch):
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    assert parallel._large_lane.acquire(blocking=False)              # another batch holds the lane
    try:
        t0 = time.monotonic()
        with parallel.controlled(stop=stop):
            assert parallel._take_lane(1, wait=True, until=time.monotonic() + 60) is False
        assert time.monotonic() - t0 < 2
    finally:
        parallel._large_lane.release()
    monkeypatch.setattr(parallel, "pool_memory", lambda: 10)
    monkeypatch.setattr(parallel, "_reserved", 10)                   # another batch holds the budget
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    t0 = time.monotonic()
    with parallel.controlled(stop=stop):
        assert parallel._reserve(5, wait=True, until=time.monotonic() + 60) is False
    assert time.monotonic() - t0 < 2
    monkeypatch.setattr(parallel, "_reserved", 0)
