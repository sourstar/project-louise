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
    parallel.forget_users()
    assert parallel._large_lane.acquire(blocking=False), "the lane is free when a test starts"
    parallel._large_lane.release()
    yield ran
    assert parallel._large_lane.acquire(blocking=False), "every test gives the lane back"
    parallel._large_lane.release()


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
