"""app/parallel.py's large-document lane (2026-11-19, the node: 41 OOM kills
of the worker in a day under EUR-Lex5's 180-204 MB acts, 24 languages of one
act next to each other in rowid order; one of them peaks at 5.25 GB to chunk,
25.7x its bytes; a batch of them outlived the 120 s deadline and was parsed
again in the main process, which the kernel killed).

A row whose `PARSE_PEAK_PER_BYTE` × size reaches LARGE_PEAK_BYTES is a part
of its own, at most one is in flight in the process, it starts only with room
for its peak, its batch's deadline grows with its bytes, and it is never
parsed in the main process: where the pool cannot take it, it is deferred
(MemoryWait), never an error."""
from types import SimpleNamespace

import pytest

from app import config, parallel, worker

K = 26
BIG = parallel.LARGE_PEAK_BYTES // K + 1           # one byte past the lane's threshold


class _Plugin:
    PARSE_PEAK_PER_BYTE = K


def _coll(k=True):
    plugin = _Plugin() if k else object()
    return SimpleNamespace(name="eurlex", plugin_instance=lambda: plugin)


def _rows(sizes):
    return [{"uid": f"e:{i}", "size": s} for i, s in enumerate(sizes)]


@pytest.fixture
def fake_compute(monkeypatch):
    """What a child would compute, recorded by where it ran."""
    ran = {"in_process": []}

    def compute(kind, collection, part):
        return [(True, r["uid"]) for r in part]
    monkeypatch.setattr(parallel, "_compute", compute)
    monkeypatch.setattr(parallel, "_partition", lambda c, rows, want: [rows[i:i + 2] for i in range(0, len(rows), 2)])
    monkeypatch.setattr(parallel, "_room", lambda peak: True)
    monkeypatch.setattr(config, "PARSE_PROCESSES", 4)
    # a large part's own process (2026-11-20, `_LaneTask`) is the test's pool:
    # the lane's rules are asserted on what it is handed, as before
    monkeypatch.setattr(parallel, "_lane_executor", lambda: _LaneViaPool())
    parallel.forget_users()
    assert parallel._large_lane.acquire(blocking=False), "the lane is free when a test starts"
    parallel._large_lane.release()
    yield ran
    assert parallel._large_lane.acquire(blocking=False), "every test gives the lane back"
    parallel._large_lane.release()


class _LaneViaPool:
    """A lane task's executor, in a test: the part goes to whatever `pool()`
    the test set, unbounded (no data limit is ever set on the test's process)."""

    def submit(self, fn, kind, collection, part, allowance):
        return parallel.pool().submit(parallel._compute, kind, collection, part)

    def shutdown(self, wait=True):
        pass


class _Pool:
    """Futures that finish when taken; counts the large parts in flight."""

    def __init__(self):
        self.live_large = 0
        self.max_large = 0
        self.timeouts = []
        self.raise_on = None

    def submit(self, fn, kind, collection, part):
        pool = self
        large = parallel._large(K, part) > 0
        if large:
            pool.live_large += 1
            pool.max_large = max(pool.max_large, pool.live_large)

        class F:
            callbacks = []

            def add_done_callback(self, cb):
                self.callbacks = [*self.callbacks, cb]

            def result(self, timeout=None):
                pool.timeouts.append((part[0]["uid"], timeout))
                if pool.raise_on == part[0]["uid"]:
                    raise TimeoutError("no answer")
                if large:
                    pool.live_large -= 1
                for cb in self.callbacks:
                    cb(self)
                return fn(kind, collection, part)

            def cancel(self):
                return False
        return F()


def test_a_large_row_is_a_part_of_its_own_and_the_order_is_kept():
    parts = [[{"uid": "a", "size": 1}, {"uid": "b", "size": BIG}], [{"uid": "c", "size": 1}, {"uid": "d", "size": BIG}],
             [{"uid": "e", "size": BIG}, {"uid": "f", "size": 1}]]
    got = parallel._isolate_large(K, parts)
    assert [[r["uid"] for r in p] for p in got] == [["a"], ["b"], ["c"], ["d"], ["e"], ["f"]]
    assert parallel._isolate_large(0, parts) == parts, "a plugin declaring no peak never takes the lane"


def test_at_most_one_large_part_is_in_flight_and_the_results_keep_their_order(fake_compute, monkeypatch):
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    rows = _rows([1, BIG, BIG, 1, 1, BIG, 1, 1])
    got = parallel.compute("chunks", _coll(), rows)
    assert got == [(True, r["uid"]) for r in rows]
    assert pool.max_large == 1, "the 24 languages of one act are never parsed together"
    assert parallel._large_lane.acquire(blocking=False), "the lane is given back"
    parallel._large_lane.release()


def test_a_large_part_extends_its_batch_deadline_by_its_bytes(fake_compute, monkeypatch):
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    rows = _rows([BIG, 1, 1])
    parallel.compute("chunks", _coll(), rows)
    big_timeout = dict(pool.timeouts)["e:0"]
    assert big_timeout >= config.PARSE_TIMEOUT_S + parallel.LARGE_SECONDS_PER_BYTE * BIG - 5, \
        "a 204 MB act's parse is not cut off at 120 s and parsed again in the main process"


def test_a_large_row_is_never_parsed_in_the_main_process(fake_compute, monkeypatch):
    rows = _rows([1, BIG, 1])
    monkeypatch.setattr(parallel, "pool", lambda: None)            # no pool
    got = parallel.compute("chunks", _coll(), rows)
    assert got[0] == (True, "e:0") and got[2] == (True, "e:2")
    assert got[1][0] is False and got[1][1]["type"] == "MemoryWait"
    # a single large row with a pool goes to the pool, not in-process
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    assert parallel.compute("chunks", _coll(), _rows([BIG])) == [(True, "e:0")]
    assert pool.timeouts and pool.timeouts[0][0] == "e:0"


def test_a_pool_that_times_out_defers_the_large_row_instead_of_parsing_it_here(fake_compute, monkeypatch):
    pool = _Pool()
    pool.raise_on = "e:1"
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "_note_failure", lambda e, ex=None: None)
    got = parallel.compute("chunks", _coll(), _rows([1, BIG, 1]))
    assert got[0] == (True, "e:0") and got[2] == (True, "e:2"), "the others computed in-process, as before"
    assert got[1][0] is False and got[1][1]["type"] == "MemoryWait"


def test_without_room_a_large_row_waits_bounded_then_is_deferred(fake_compute, monkeypatch):
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "_room", lambda peak: False)
    monkeypatch.setattr(parallel, "LARGE_WAIT_S", 0.05)
    monkeypatch.setattr(parallel.time, "sleep", lambda s: None)
    got = parallel.compute("chunks", _coll(), _rows([BIG, 1, 1]))
    assert got[0][0] is False and got[0][1]["type"] == "MemoryWait"
    assert got[1:] == [(True, "e:1"), (True, "e:2")]
    assert parallel._large_lane.acquire(blocking=False), "a lane taken without room is given back"
    parallel._large_lane.release()


def test_a_row_deferred_before_is_asked_never_waited_for(fake_compute, monkeypatch):
    """Defect 3 of the EUR-Lex5 review: a deferred large row stays pending, so
    the next round selects it first again, as part 0 with nothing in flight,
    and `_take_lane(wait=True)` waited LARGE_WAIT_S every round while other
    large parses held the lane — a poll, not a turn. A fresh row still waits
    once, bounded; deferred, it is asked again without a wait, and the rows
    behind it go on at once. Admitted, it is forgotten."""
    import time
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "LARGE_WAIT_S", 0.3)
    monkeypatch.setattr(parallel, "WAIT_SLICE_S", 0.02)
    asked: list = []
    real = parallel._take_lane

    def take(peak, *, wait, until):
        t0 = time.monotonic()
        got = real(peak, wait=wait, until=until)
        asked.append((wait, time.monotonic() - t0))
        return got
    monkeypatch.setattr(parallel, "_take_lane", take)
    rows = _rows([BIG, 1, 1])
    assert parallel._large_lane.acquire(blocking=False), "another batch's large parse holds the lane"
    try:
        first = parallel.compute("chunks", _coll(), rows)
        second = parallel.compute("chunks", _coll(), rows)
    finally:
        parallel._large_lane.release()
    for got in (first, second):
        assert got[0][0] is False and got[0][1]["type"] == "MemoryWait" and got[1:] == [(True, "e:1"), (True, "e:2")]
    assert asked[0][0] is True and asked[0][1] >= 0.3, "a fresh row waits once, bounded, as before"
    assert asked[1][0] is False and asked[1][1] < 0.1, f"deferred before: asked, never waited for ({asked})"
    assert "not waited for" in second[0][1]["msg"]
    assert parallel.compute("chunks", _coll(), rows) == [(True, r["uid"]) for r in rows], "the lane free: admitted"
    assert not parallel.deferred_before(_coll(), rows[:1]), "admitted: forgotten"


def test_the_worker_defers_a_memory_wait_and_never_errors_the_document():
    with pytest.raises(parallel.MemoryWait):
        parallel.raise_for({"type": "MemoryWait", "msg": "a large document waits"})
    assert worker.transient_failure(parallel.MemoryWait("a large document waits"))


def test_eurlex_declares_its_measured_peak():
    from app.plugins.eurlex.source import EurlexSource
    assert EurlexSource.PARSE_PEAK_PER_BYTE == 26
    assert 204_218_240 * EurlexSource.PARSE_PEAK_PER_BYTE >= parallel.LARGE_PEAK_BYTES, \
        "the measured 204 MB act takes the lane"


def test_a_document_that_can_never_fit_is_its_error_not_an_endless_deferral(fake_compute, monkeypatch):
    """München's park, 2026-11-19: a 911 MB act at 26x is ~24 GB, past the
    worker's whole 24 GiB. Deferred, it would be asked again every round for
    ever; it is the document's error instead, with both numbers."""
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    from app import scheduled_checks
    monkeypatch.setattr(scheduled_checks, "cgroup_memory", lambda cgroup=None: (24 * 2 ** 30, 4 * 2 ** 30))
    rows = _rows([911_000_000, 1, 300_000_000])
    got = parallel.compute("chunks", _coll(), rows)
    assert got[2] == (True, "e:2"), "a 300 MB act (7.8 GB) is admitted"
    assert got[0][0] is False and got[0][1]["type"] == "TooLarge"
    assert "67% of the worker's whole memory limit (24.0 GB)" in got[0][1]["msg"]
    assert got[1] == (True, "e:1") and pool.max_large == 1, "only the one that fits was submitted"
    assert not worker.transient_failure(RuntimeError("TooLarge: " + got[0][1]["msg"])), \
        "an error a person sees, not a deferral"


# --- the pool's memory (2026-11-20) --------------------------------------------
#
# Nine kills of the worker from 03:39Z to 04:08Z under EUR-Lex5's text index:
# the 40-82 MB acts below the lane's threshold went through the ordinary pool,
# several to a part, eleven slots of them, and the processes killed held
# 4.5-8.6 GB. A part is now cut where its rows' estimates sum past
# PART_PEAK_BYTES, and every part in flight reserves its estimate from one
# budget per process.

MID = 60 * 2 ** 20                                  # a 60 MB act: ~1.5 GB at 26x


def test_a_part_is_cut_where_its_estimates_would_pass_the_part_bound():
    parts = [_rows([MID] * 5 + [BIG, 10, 10])]
    got = parallel._cut_by_peak(K, parts)
    assert [r["uid"] for p in got for r in p] == [r["uid"] for r in parts[0]], "every row, in order"
    assert all(parallel.part_peak(K, p) <= parallel.PART_PEAK_BYTES or len(p) == 1 for p in got)
    assert [len(p) for p in got] == [1, 1, 1, 1, 1, 1, 2], "1.5 GB each: one act a part; the large alone"
    small = [_rows([10] * 8)]
    assert parallel._cut_by_peak(K, small) == small, "small rows are not cut"
    assert parallel._cut_by_peak(0, parts) == parts, "a plugin declaring no peak is cut as before"


class _HeldPool(_Pool):
    """Futures that finish only when taken, recording the reservation at each
    submit: the estimates in flight at once."""

    def __init__(self):
        super().__init__()
        self.reserved_at_submit = []

    def submit(self, fn, kind, collection, part):
        f = super().submit(fn, kind, collection, part)
        self.reserved_at_submit.append(parallel.reserved())
        return f


def test_the_estimates_in_flight_never_pass_the_budget(fake_compute, monkeypatch):
    pool = _HeldPool()
    budget = 3 * K * MID + 1                        # three acts at once
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "pool_memory", lambda: budget)
    rows = _rows([MID] * 10)
    got = parallel.compute("fts", _coll(), rows)
    assert got == [(True, r["uid"]) for r in rows], "every row computed, in order, none deferred"
    assert max(pool.reserved_at_submit) <= budget and max(pool.reserved_at_submit) == 3 * K * MID
    assert parallel.reserved() == 0, "every reservation given back"
    assert fake_compute["in_process"] == []


def test_a_part_the_budget_cannot_take_is_deferred_never_parsed_here(fake_compute, monkeypatch):
    """Another batch holds the budget and this one has nothing in flight to
    drain: it waits LARGE_WAIT_S, then its rows are deferred (MemoryWait, a
    transient failure), never parsed in the main process."""
    pool = _HeldPool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "pool_memory", lambda: 2 * K * MID)
    monkeypatch.setattr(parallel, "LARGE_WAIT_S", 0.05)
    monkeypatch.setattr(parallel, "_in_process", lambda *a: pytest.fail("parsed in the main process"))
    assert parallel._reserve(2 * K * MID, wait=False, until=0)      # another batch's
    give_back = parallel._reservation_releaser(2 * K * MID)
    try:
        got = parallel.compute("fts", _coll(), _rows([MID, MID]))
    finally:
        give_back()
        give_back()                                  # once only
    assert [ok for ok, _ in got] == [False, False] and {v["type"] for _, v in got} == {"MemoryWait"}
    with pytest.raises(parallel.MemoryWait):
        parallel.raise_for(got[0][1])
    assert worker.transient_failure(parallel.MemoryWait("x")) and parallel.reserved() == 0


def test_a_failed_batch_gives_its_reservations_back(fake_compute, monkeypatch):
    pool = _HeldPool()
    pool.raise_on = "e:0"
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "pool_memory", lambda: 10 * K * MID)
    monkeypatch.setattr(parallel, "_in_process", lambda kind, c, rows, k: [(True, r["uid"]) for r in rows])
    parallel.compute("fts", _coll(), _rows([MID] * 4))
    assert parallel.reserved() == 0


def test_with_no_limit_to_read_nothing_is_reserved_or_waited_for(fake_compute, monkeypatch):
    pool = _HeldPool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "pool_memory", lambda: None)
    rows = _rows([MID] * 6)
    assert parallel.compute("fts", _coll(), rows) == [(True, r["uid"]) for r in rows]
    assert parallel.reserved() == 0


def test_a_parse_child_goes_first_for_the_oom_killer():
    """Three of the nine kills took the main process, which holds the rounds
    and writes the hold with what the pool had in flight."""
    import subprocess
    import sys
    code = ("from app import parallel; parallel._first_for_the_oom_killer(); "
            "print(open('/proc/self/oom_score_adj').read().strip())")
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.stdout.strip() == str(parallel.CHILD_OOM_SCORE_ADJ) == "500", r.stderr


# --- the measured side (2026-11-20, the first OOM hold) ---------------------------
#
# 14:42:50Z: the estimates in flight were 7.12 GB, inside the 16 GB budget,
# while the cgroup already held 18.5 GB — the main process 4.12 GB and eleven
# children at 0.69-1.88 GB, four with nothing in flight. Two children killed
# at 2.5 and 2.9 GB: their keep plus their part.

GB = 2 ** 30


def _cgroup(monkeypatch, used: int, limit: int = 24 * GB):
    from app import scheduled_checks
    monkeypatch.setattr(scheduled_checks, "cgroup_memory", lambda cgroup=None: (limit, used))


def test_a_part_is_admitted_only_within_the_measured_room(fake_compute, monkeypatch):
    pool = _HeldPool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "pool_memory", lambda: 16 * GB)
    monkeypatch.setattr(parallel, "LARGE_WAIT_S", 0.05)
    monkeypatch.setattr(parallel, "_in_process", lambda *a: pytest.fail("parsed in the main process"))
    _cgroup(monkeypatch, used=int(19.5 * GB))            # 19.5 + 1.56 > 0.85 x 24 GiB
    got = parallel.compute("fts", _coll(), _rows([MID]))
    assert got[0][0] is False and got[0][1]["type"] == "MemoryWait", "no room: deferred"
    assert pool.reserved_at_submit == [] and parallel.reserved() == 0, "nothing started"
    _cgroup(monkeypatch, used=10 * GB)
    rows = _rows([MID] * 6)
    assert parallel.compute("fts", _coll(), rows) == [(True, r["uid"]) for r in rows], "room: admitted"
    # 10 GB resident: 0.85 x 24 GiB leaves room for four 1.5 GB estimates in flight at once
    assert max(pool.reserved_at_submit) <= 0.85 * 24 * GB - 10 * GB


def test_the_room_is_never_exempted_where_nothing_is_reserved():
    """The budget grants a part alone; the measured room does not: a pool
    whose idle children hold the room defers it."""
    assert parallel.reserved() == 0
    import app.scheduled_checks as sc
    real = sc.cgroup_memory
    sc.cgroup_memory = lambda cgroup=None: (24 * GB, 23 * GB)
    try:
        assert parallel._reserve(K * MID, wait=False, until=0) is False
    finally:
        sc.cgroup_memory = real
    assert parallel.reserved() == 0


def test_a_deferral_for_room_stops_the_childrens_keep_only_when_alone(monkeypatch):
    stopped: list = []
    monkeypatch.setattr(parallel, "discard", lambda ex, *a, **k: stopped.append(ex))
    monkeypatch.setattr(parallel, "_pool", "the pool")
    monkeypatch.setattr(parallel, "_pool_size", 11)
    monkeypatch.setattr(parallel, "_wanting", {"eurlex": 1, "pubmed": 1})
    monkeypatch.setattr(parallel, "_held", {"pubmed": 3})
    assert parallel.recycle_for_room("eurlex") == 0 and stopped == [], "pubmed is inside: kept"
    monkeypatch.setattr(parallel, "_wanting", {"eurlex": 2})
    monkeypatch.setattr(parallel, "_held", {"eurlex": 2})
    assert parallel.recycle_for_room("eurlex", own_slots=0) == 0, "another eurlex stage is inside: kept"
    monkeypatch.setattr(parallel, "_wanting", {"eurlex": 1})
    monkeypatch.setattr(parallel, "_held", {})
    assert parallel.recycle_for_room("eurlex") == 11 and stopped == ["the pool"] and parallel._pool is None


def test_the_batch_that_was_short_of_room_stops_the_keep(fake_compute, monkeypatch):
    pool = _HeldPool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "pool_memory", lambda: 16 * GB)
    monkeypatch.setattr(parallel, "LARGE_WAIT_S", 0.05)
    _cgroup(monkeypatch, used=23 * GB)
    asked: list = []
    monkeypatch.setattr(parallel, "recycle_for_room", lambda name, own_slots=0: asked.append((name, own_slots)))
    parallel.compute("fts", _coll(), _rows([MID]))
    assert asked == [("eurlex", 0)]
    asked.clear()
    _cgroup(monkeypatch, used=2 * GB)
    parallel.compute("fts", _coll(), _rows([MID]))
    assert asked == [], "room: nothing stopped"


def test_a_heavy_part_gives_back_what_it_freed_and_says_it(monkeypatch, capsys):
    seq = iter([900 * 2 ** 20, 200 * 2 ** 20])
    monkeypatch.setattr(parallel, "_rss_anon", lambda: next(seq))
    monkeypatch.setattr(parallel, "_compute", lambda kind, c, rows: [(True, r["uid"]) for r in rows])
    assert parallel._compute_heavy("fts", _coll(), _rows([MID])) == [(True, "e:0")]
    assert "gave back 700 MB after a fts part of 60.0 MB" in capsys.readouterr().err
    monkeypatch.setattr(parallel, "_rss_anon", lambda: 100 * 2 ** 20)
    assert parallel._give_back() is None, "below the threshold: nothing collected"


def test_a_heap_that_is_held_is_not_collected_on_every_part(monkeypatch):
    """A child whose 1.6 GB are held objects frees nothing: its threshold is
    raised past what is left, and it is collected again only once it has
    grown half a threshold beyond it."""
    collected = []
    monkeypatch.setattr(parallel, "_trim_floor", parallel.TRIM_ABOVE_BYTES)
    rss = {"v": 1_600 * 2 ** 20}
    monkeypatch.setattr(parallel, "_rss_anon", lambda: rss["v"])
    import gc
    monkeypatch.setattr(gc, "collect", lambda: collected.append(1) or 0)
    assert parallel._give_back() == (1_600 * 2 ** 20, 1_600 * 2 ** 20)
    assert parallel._give_back() is None and len(collected) == 1, "held: not collected again"
    rss["v"] = 1_600 * 2 ** 20 + parallel.TRIM_ABOVE_BYTES // 2
    assert parallel._give_back() is not None and len(collected) == 2, "grown past it: collected"

def test_every_part_runs_the_give_back(fake_compute, monkeypatch):
    """2026-11-24: only a part with an estimate did, and every other plugin's
    children kept their peak heap for life: twelve of Normattiva's fts at
    1.6-1.75 GB, a kernel kill in the worker's 24 GB."""
    seen: list = []

    class P(_HeldPool):
        def submit(self, fn, kind, collection, part):
            seen.append(fn.__name__)
            return super().submit(fn, kind, collection, part)
    monkeypatch.setattr(parallel, "pool", lambda: P())
    monkeypatch.setattr(parallel, "pool_memory", lambda: None)
    parallel.compute("fts", _coll(), _rows([MID, MID]))
    parallel.compute("fts", _coll(k=False), _rows([MID, MID]))
    assert set(seen) == {"_compute_heavy"}, seen


# --- the lane's own process (2026-11-20, after the second hold) ---------------------
#
# 32018R0688 el's fts held 11.18 GB against 3.25 estimated; the kernel killed
# it inside the worker's cgroup. A large part now runs in a process of its
# own whose data limit is the room it was granted.


def test_a_parse_past_its_bound_fails_in_its_own_process_only():
    """The bound itself, in a real process: an allocation past the room is a
    MemoryError in that process, recorded per row — the parent untouched."""
    import subprocess
    import sys
    def run(allowance: str) -> str:
        code = ("from app import parallel\n"
                "parallel._one = lambda kind, c, row: len(bytearray(1 << 30))\n"
                f"print(parallel._compute_bounded('fts', None, [{{'uid': 'e:1'}}], {allowance}))\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False)
        return r.stdout.strip() + r.stderr[-300:]
    bounded = run("256 << 20")       # each in a process of its own, used once, as a lane's
    assert "MemoryError" in bounded and "(False" in bounded, bounded
    assert run("None") == "[(True, 1073741824)]", "a limit is set only where room was granted"


class _Done:
    """A finished lane future."""

    def __init__(self, rows=None, exc=None):
        self.rows, self.exc = rows, exc

    def result(self, timeout=None):
        if self.exc:
            raise self.exc
        return self.rows

    def add_done_callback(self, cb):
        cb(self)

    def cancel(self):
        return False


class _OneShot:
    def __init__(self, fut, code=None):
        self.fut, self._processes = fut, {1: type("P", (), {"exitcode": code})()}

    def submit(self, *a):
        return self.fut

    def shutdown(self, wait=True):
        pass


def _lane(monkeypatch, fut, code=None, allowance=4 * GB):
    monkeypatch.setattr(parallel, "_lane_executor", lambda: _OneShot(fut, code))
    monkeypatch.setattr(parallel, "_remembered", {})
    return parallel._LaneTask("fts", _coll(), _rows([BIG]), allowance)


def test_a_memory_error_is_asked_again_with_twice_the_room(monkeypatch):
    from app import scheduled_checks
    monkeypatch.setattr(scheduled_checks, "cgroup_memory", lambda cgroup=None: (24 * GB, 4 * GB))
    t = _lane(monkeypatch, _Done([(False, {"type": "MemoryError", "msg": ""})]))
    (ok, v), = t.result()
    assert not ok and v["type"] == "MemoryWait" and "passed the 4.0 GB it was given" in v["msg"]
    assert parallel.remembered(_coll(), {"uid": "e:0"}) == 8 * GB, "asked again with twice the room"


def test_an_abort_is_read_as_the_bound_and_a_crash_as_the_documents_error(monkeypatch):
    import signal
    from concurrent.futures.process import BrokenProcessPool

    from app import scheduled_checks
    monkeypatch.setattr(scheduled_checks, "cgroup_memory", lambda cgroup=None: (24 * GB, 4 * GB))
    (_ok, v), = _lane(monkeypatch, _Done(exc=BrokenProcessPool("x")), code=-signal.SIGABRT).result()
    assert v["type"] == "MemoryWait" and "a Rust allocation refused" in v["msg"]
    (_ok, v), = _lane(monkeypatch, _Done(exc=BrokenProcessPool("x")), code=-signal.SIGSEGV).result()
    assert v["type"] == "RuntimeError" and "signal 11" in v["msg"]


def test_past_the_most_the_worker_can_give_it_is_the_documents_error(monkeypatch):
    from app import scheduled_checks
    monkeypatch.setattr(scheduled_checks, "cgroup_memory", lambda cgroup=None: (24 * GB, 4 * GB))
    (_ok, v), = _lane(monkeypatch, _Done([(False, {"type": "MemoryError", "msg": ""})]), allowance=10 * GB).result()
    assert v["type"] == "TooLarge" and "twice the 10.0 GB its parse passed" in v["msg"]


def test_the_cgroups_own_kill_is_still_the_holds(monkeypatch):
    from concurrent.futures.process import BrokenProcessPool

    from app import oom_hold
    monkeypatch.setattr(oom_hold, "killed", lambda cgroup=None: (0, 1))
    with pytest.raises(BrokenProcessPool):
        _lane(monkeypatch, _Done(exc=BrokenProcessPool("x"))).result()


def test_a_remembered_need_is_the_next_admissions(fake_compute, monkeypatch):
    """The room its parse passed, doubled, is what the next admission asks."""
    pool = _HeldPool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "pool_memory", lambda: 30 * GB)
    _cgroup(monkeypatch, used=2 * GB)
    monkeypatch.setattr(parallel, "_remembered", {})
    row = _rows([BIG])
    parallel._remember(_coll(), row[0], 9 * GB)
    allowances: list = []
    real = parallel._LaneTask

    def recording(kind, c, part, allowance):
        allowances.append(allowance)
        return real(kind, c, part, allowance)
    monkeypatch.setattr(parallel, "_LaneTask", recording)
    assert parallel.compute("fts", _coll(), row) == [(True, "e:0")]
    assert pool.reserved_at_submit == [9 * GB] and allowances[0] >= 9 * GB
    parallel._remember(_coll(), row[0], 17 * GB)            # past 2/3 of 24 GiB
    (_ok, v), = parallel.compute("fts", _coll(), row)
    assert v["type"] == "TooLarge" and "twice the room its parse passed before" in v["msg"]
