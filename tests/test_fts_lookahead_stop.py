"""P4 of the EUR-Lex review (2026-11-21): the text index's look-ahead
(`worker._fts_take_ahead`, app/lookahead.py `Ahead.abandon`). A take waited
PARSE_TIMEOUT_S whatever the link held; abandoned, the link only cancelled
its Future: the compute went on, holding the large lane and its reservation,
and the stage parsed the same rows AGAIN — doubled large parses, the second
deferred behind the first's lane (then P1). Now a link's parse is
CONTROLLED: abandoned, it stops at its next part boundary (a large part's
own process terminated, the lane given back); and while it beats — alive
within `parallel.compute`'s own bound — the take waits for it instead of
parsing it a second time."""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import pytest

from tests.conftest import *  # noqa: F401,F403 — the suite's fixtures
from tests.test_fts_batching import _discovered

from app import config, lookahead, parallel, scheduled_checks, worker

K = 26
BIG = parallel.LARGE_PEAK_BYTES // K + 1


@pytest.fixture
def overlap(monkeypatch):
    """As on a pipeline thread of a node with a parse pool."""
    monkeypatch.setattr(parallel, "prefetch_allowed", lambda: True)
    monkeypatch.setattr(config, "FTS_OVERLAP", 1)
    monkeypatch.setattr(worker, "fts_sizes", lambda collection: (1, 10 ** 9))
    worker._ahead_here.on = True
    yield
    worker._ahead_here.on = False
    worker.drop_fts_ahead()


def _computed(monkeypatch) -> list[str]:
    """Every row really parsed (`parallel._compute`), by uid."""
    seen: list[str] = []
    real = parallel._compute

    def counting(kind, collection, part):
        seen.extend(r["uid"] for r in part)
        return real(kind, collection, part)
    monkeypatch.setattr(parallel, "_compute", counting)
    return seen


def _round(con, c, uid=None):
    r = con.execute("SELECT * FROM documents WHERE fts_done=0 ORDER BY rowid LIMIT 1").fetchone()
    assert r is not None and (uid is None or r["uid"] == uid), (dict(r) if r else None, uid)
    return worker._execute_step(con, c, "fts", r)


def test_an_abandoned_link_stops_and_its_rows_are_parsed_once(tmp_path, monkeypatch, isolated_config, overlap):
    """The reviewer's repro, counted where the work is: a link slower than
    the take's bound (2.5 s against 1 s) is let go of; its parse, told to
    stop, computes nothing, and the stage's own parse is the one. Before:
    the large row was parsed by both."""
    c, con = _discovered(tmp_path)
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 1.0)
    rows = [r["uid"] for r in con.execute("SELECT uid FROM documents ORDER BY rowid")]
    slow = rows[1]
    real = parallel.fts_for_rows

    def fts(collection, todo, *, names):
        if slow in [r["uid"] for r in todo] and threading.current_thread().name.startswith("fts-ahead-"):
            time.sleep(2.5)                         # longer than the take's bound, and silent
        return real(collection, todo, names=names)
    monkeypatch.setattr(parallel, "fts_for_rows", fts)
    seen = _computed(monkeypatch)
    assert _round(con, c, rows[0])
    t0 = time.monotonic()
    assert _round(con, c, slow), "abandoned after the bound, parsed here"
    assert time.monotonic() - t0 < 2.5
    time.sleep(2.0)                                 # the abandoned link wakes, told to stop
    assert seen.count(slow) == 1, f"the slow row was parsed {seen.count(slow)} times: {seen}"
    assert con.execute("SELECT fts_done FROM documents WHERE uid=?", (slow,)).fetchone()[0] == 1


class _Lane:
    """A large part's own process, in a test: answers (with the real parse)
    after `delay`, or never once cancelled — which `abandon` must reach."""
    made: list = []
    delay = 2.5

    def __init__(self, kind, collection, part, allowance):
        self.part, self.cancelled = part, False
        self._f = Future()
        self._f.set_running_or_notify_cancel()
        _Lane.made.append(self)
        rows = [dict(r) for r in part]

        def answer():
            if not self.cancelled and not self._f.done():
                self._f.set_result(parallel._compute(kind, collection, rows))
        threading.Timer(_Lane.delay, answer).start()

    @property
    def future(self):
        return self._f

    def add_done_callback(self, cb):
        self._f.add_done_callback(lambda _f: cb(self))

    def cancel(self):
        self.cancelled = True
        if not self._f.done():                      # its process terminated: broken
            from concurrent.futures.process import BrokenProcessPool
            self._f.set_exception(BrokenProcessPool("terminated"))
        return False

    def result(self, timeout=None):
        return self._f.result(timeout)


@pytest.fixture
def lane(monkeypatch):
    """The large lane as on a node with a pool: the gutenberg plugin given a
    peak per byte, its second document made large."""
    monkeypatch.setattr(config, "PARSE_PROCESSES", 4)
    monkeypatch.setattr(parallel, "pool", lambda: object())    # nothing reaches the shared pool here
    monkeypatch.setattr(parallel, "peak_per_byte", lambda collection: float(K))
    monkeypatch.setattr(parallel, "pool_memory", lambda: None)
    monkeypatch.setattr(scheduled_checks, "cgroup_memory", lambda cgroup=None: None)
    monkeypatch.setattr(parallel, "_LaneTask", _Lane)
    monkeypatch.setattr(parallel, "LARGE_WAIT_S", 0.5)
    _Lane.made = []
    _Lane.delay = 2.5
    parallel.forget_users()
    yield _Lane
    _Lane.delay = 2.5
    assert parallel._large_lane.acquire(blocking=False), "the lane given back"
    parallel._large_lane.release()
    assert parallel.reserved() == 0


def _made(lane, n: int = 1, within: float = 3.0) -> list:
    """The lane parts made so far, once `n` are (the link parses on its own thread)."""
    deadline = time.monotonic() + within
    while len(lane.made) < n and time.monotonic() < deadline:
        time.sleep(0.01)
    return lane.made


def _large(con, uid):
    con.execute("UPDATE documents SET size=? WHERE uid=?", (BIG, uid))
    con.commit()


def test_a_link_still_parsing_a_large_act_is_waited_for_never_parsed_twice(
        tmp_path, monkeypatch, isolated_config, overlap, lane):
    """32021R2017 el: ~730 s in the lane against a 120 s take. Here 2.5 s
    against 1 s: the link beats while its lane part runs, the take waits,
    and the row is written from that one parse. Before: abandoned at 1 s,
    parsed again by the stage, whose lane part waited for the first one's
    lane and was deferred."""
    c, con = _discovered(tmp_path)
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 1.0)
    rows = [r["uid"] for r in con.execute("SELECT uid FROM documents ORDER BY rowid")]
    _large(con, rows[1])
    seen = _computed(monkeypatch)
    assert _round(con, c, rows[0])
    made = _made(lane)
    assert len(made) == 1 and made[0].part[0]["uid"] == rows[1], "the large act in the lane, ahead"
    t0 = time.monotonic()
    assert _round(con, c, rows[1]) is True
    waited = time.monotonic() - t0
    assert 1.0 < waited < 5, f"waited for the link past the bound: {waited:.2f} s"
    assert len(lane.made) == 1, "one lane part for the act: never parsed twice"
    assert seen.count(rows[1]) == 1
    assert tuple(con.execute("SELECT fts_done, error FROM documents WHERE uid=?", (rows[1],)).fetchone()) == (1, None)


def test_a_link_let_go_of_gives_the_lane_back_at_once(tmp_path, monkeypatch, isolated_config, overlap, lane):
    """The pipeline thread's exit (or a stop) with a large act in the lane:
    within a slice its parse is stopped, its lane part terminated, the lane
    free. Before: the lane stayed held until the parse ended by itself."""
    c, con = _discovered(tmp_path)
    lane.delay = 30.0
    monkeypatch.setattr(config, "PARSE_TIMEOUT_S", 5.0)
    rows = [r["uid"] for r in con.execute("SELECT uid FROM documents ORDER BY rowid")]
    _large(con, rows[1])
    assert _round(con, c, rows[0])
    assert len(_made(lane)) == 1
    assert parallel.lanes_free() == parallel.LARGE_LANES - 1, "the act holds a lane"
    t0 = time.monotonic()
    worker.drop_fts_ahead(c)                         # let go of
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not lane.made[0].cancelled:
        time.sleep(0.02)
    assert lane.made[0].cancelled, "its lane part terminated"
    assert time.monotonic() - t0 < 2 * parallel.WAIT_SLICE_S
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and parallel.lanes_free() < parallel.LARGE_LANES:
        time.sleep(0.02)
    assert parallel.lanes_free() == parallel.LARGE_LANES, "its lane given back"


def test_the_chain_waits_for_a_beating_link_and_lets_a_quiet_one_go():
    """The take itself: a first link that beats is waited for past the
    bound; one that is quiet for the bound is abandoned, told to stop; a
    later link not answered stays held, the chain whole."""
    def link(rows, seconds, beats=True):
        def compute(todo, control):
            t_end = time.monotonic() + seconds
            while time.monotonic() < t_end:
                if control.is_set():
                    return [None] * len(todo)
                if beats:
                    control.beat()
                time.sleep(0.02)
            return [r * 10 for r in todo]
        return lookahead.Ahead(rows, [None] * len(rows), compute, controlled=True)

    chain = lookahead.Chain(0)
    chain.add(link([1], 0.8), 1)
    t0 = time.monotonic()
    assert chain.take(5, lambda pairs: False, timeout=0.3) == [(1, 10)]
    assert time.monotonic() - t0 >= 0.7, "waited for the beating link"
    quiet = link([2], 3.0, beats=False)
    chain = lookahead.Chain(0)
    chain.add(quiet, 2)
    assert chain.take(5, lambda pairs: False, timeout=0.3) is None
    assert quiet.control.is_set(), "told to stop"
    chain = lookahead.Chain(0)
    chain.add(link([3], 0.0), 3)
    later = link([4], 1.0)
    chain.add(later, 4)
    assert chain.take(5, lambda pairs: False, timeout=0.2) == [(3, 30)]
    assert not chain.broken and chain.held() == 1 and not later.control.is_set(), "kept, still computing"
    assert chain.take(5, lambda pairs: False, timeout=2) == [(4, 40)]
