"""A collection yields before the kernel acts (app/memory_yield.py).

The operator, 2026-11-25: "All the workaround made on the spot must be
reflected in RAGE so that the same collection made from scratch can run
smoothly". The workaround: an operator's monitor read the worker cgroup's
anonymous memory every ten seconds and paused the heavy collection over
21,500 MiB, resuming it under 18,000 — an agent standing guard. The same
rule, in the worker: over HIGH the collection that holds the most of the
parse pool yields; under LOW it goes on; a yield that cannot bring the
memory down is bounded and says so.

The monitor's rule never fired on the node (München), so these tests hold
the RULE, not a measured effect.
"""
from __future__ import annotations

import threading

import pytest

from app import memory_yield as my

GIB = 1 << 30
LIMIT = 24 * GIB


def _part(collection: str, *, peak: int = 0, parse: int = 0, rows: int = 1) -> dict:
    return {"collection": collection, "kind": "fts", "rows": rows, "bytes": parse,
            "parse_bytes": parse, "estimated_peak": peak, "uids": []}


class Worker:
    """The worker's memory and its parse pool, as a look reads them."""

    def __init__(self) -> None:
        self.anon = 10 * GIB
        self.parts: list[dict] = []
        self.said: list[str] = []
        self.t = 1000.0

    def look(self, anon_gib: float | None = None, *, after: float = 10.0) -> dict:
        if anon_gib is not None:
            self.anon = int(anon_gib * GIB)
        self.t += after
        return my.look(memory=lambda: (self.anon, LIMIT), in_flight=lambda: list(self.parts),
                       say=self.said.append, now=self.t)


@pytest.fixture
def w():
    return Worker()


def test_the_lines_are_the_monitors_against_a_24_gib_cap():
    assert int(LIMIT * my.HIGH_FRACTION) // my.MIB == 21_504, "the monitor paused over 21,500 MiB"
    assert 17_900 < int(LIMIT * my.LOW_FRACTION) // my.MIB <= 18_000, "and resumed under 18,000"


def test_over_the_line_the_collection_holding_the_most_of_the_pool_yields_and_says_its_numbers(w):
    w.parts = [_part("eurlex", peak=6 * GIB), _part("eurlex", peak=3 * GIB), _part("normattiva", parse=40 << 20),
               _part("legi", parse=20 << 20)]
    assert w.look(20.9)["yielded"] == [], "under the line nothing yields"
    got = w.look(21.2)
    assert got["yielded"] == ["eurlex"]
    assert my.yields("eurlex") and my.yields("normattiva") is None, "the others keep working"
    line = w.said[-1]
    assert "eurlex yields" in line and "21,708 MiB anonymous" in line and "over 21,504 of 24,576" in line
    assert "2 parts, 9,216 MiB estimated" in line and "until the worker is under 17,940 MiB" in line
    assert "yielding to the worker's memory" in my.said("eurlex") and my.said("legi") is None


def test_it_goes_on_under_the_low_line_and_not_between_the_two(w):
    w.parts = [_part("eurlex", peak=6 * GIB)]
    w.look(21.2)
    assert w.look(19.0)["resumed"] == [] and my.yields("eurlex"), "between the lines it still yields"
    got = w.look(17.4, after=50.0)
    assert got["resumed"] == ["eurlex"] and my.yields("eurlex") is None
    assert "eurlex goes on" in w.said[-1] and "it yielded for 60 s" in w.said[-1]


def test_who_yields_is_by_estimated_peak_then_bytes_to_parse_then_parts(w):
    assert my.weights([_part("a", parse=5), _part("a", parse=7), _part("b", peak=9)]) == \
        {"a": (0, 12, 2), "b": (9, 0, 1)}
    w.parts = [_part("small-but-declared", peak=1 * GIB), _part("large-undeclared", parse=3 * GIB)]
    assert w.look(22.0)["yielded"] == ["small-but-declared"], "a declared peak is known; a size is a guess"
    my.reset()
    w.parts = [_part("a", parse=1 << 20), _part("b", parse=2 << 20), _part("a", parse=2 << 20)]
    assert w.look(22.0)["yielded"] == ["a"]


def test_still_over_the_line_after_a_while_the_next_heaviest_yields_too(w):
    w.parts = [_part("eurlex", peak=6 * GIB), _part("normattiva", parse=40 << 20)]
    assert w.look(22.0)["yielded"] == ["eurlex"]
    assert w.look(22.0, after=my.ESCALATE_S - 20)["yielded"] == [], "a yield is given its time"
    got = w.look(22.0, after=30)
    assert got["yielded"] == ["normattiva"] and "already yielding: eurlex" in w.said[-1]
    assert w.look(22.0, after=my.ESCALATE_S + 1)["yielded"] == [], "nobody is left to yield"


def test_a_yield_that_does_not_bring_the_memory_down_is_bounded_and_says_so(w):
    # the one real kill (2026-11-23) was a pass run inside the worker's container: not the pool's
    w.parts = [_part("eurlex", peak=6 * GIB)]
    w.look(22.0)
    w.parts = []                                   # its parts have finished; the memory has not moved
    assert w.look(22.0, after=my.MAX_YIELD_S - 20)["resumed"] == []
    got = w.look(22.0, after=30)
    assert got["resumed"] == ["eurlex"] and my.yields("eurlex") is None
    assert "the memory is not this collection's" in w.said[-1]
    # and it is not asked again in the same episode: a yield that did nothing is not repeated
    w.parts = [_part("eurlex", peak=6 * GIB)]
    assert w.look(22.0, after=my.ESCALATE_S + 1)["yielded"] == []
    w.look(17.0)                                   # the episode ends under the low line
    assert w.look(22.0)["yielded"] == ["eurlex"]


def test_a_collection_with_a_part_still_in_flight_keeps_yielding_past_the_bound(w):
    w.parts = [_part("eurlex", peak=6 * GIB)]
    w.look(22.0)
    assert w.look(22.0, after=my.MAX_YIELD_S + 60)["resumed"] == [], "its part is still what holds the memory"


def test_nothing_in_the_pool_and_the_memory_over_the_line_is_said_once(w):
    got = w.look(22.5)
    assert got["yielded"] == [] and my.yielding() == {}
    assert "nothing is in the parse pool" in w.said[-1] and "no collection to yield" in w.said[-1]
    n = len(w.said)
    w.look(22.6)
    w.look(22.7)
    assert len(w.said) == n, "once an episode"


def test_no_limit_to_read_does_nothing(w):
    assert my.look(memory=lambda: None, in_flight=lambda: [_part("a", peak=GIB)], say=w.said.append) == \
        {"anon": None, "limit": None, "yielded": [], "resumed": []}
    assert w.said == []


def test_the_cgroups_anonymous_memory_is_read_from_its_own_files(tmp_path):
    (tmp_path / "memory.max").write_text(f"{LIMIT}\n")
    (tmp_path / "memory.stat").write_text("file 123\nanon 18959302656\ninactive_file 9\n")
    assert my.anonymous(str(tmp_path)) == (18_959_302_656, LIMIT)
    (tmp_path / "memory.max").write_text("max\n")
    assert my.anonymous(str(tmp_path)) is None
    assert my.anonymous(str(tmp_path / "none")) is None


def test_the_watcher_ends_at_once_where_no_limit_reads(monkeypatch):
    monkeypatch.setattr(my, "anonymous", lambda cgroup=None: None)
    t = threading.Thread(target=my.watch, args=(threading.Event(),))
    t.start()
    t.join(2.0)
    assert not t.is_alive()


def test_the_pipeline_thread_stands_down_while_its_collection_yields(isolated_config, monkeypatch):
    """The worker's side: a yielding collection's thread starts nothing, says why
    in the collection's own log, and comes back when the yield ends."""
    from app import collections as collections_mod, db, worker
    c = collections_mod.by_name("gutenberg")
    steps: list = []
    gone = threading.Event()

    def step(con, collection, *a, **k):
        steps.append(collection.name)
        gone.set()                                   # one step is enough
        return False
    monkeypatch.setattr(worker, "_run_one_step", step)
    monkeypatch.setattr(worker.config, "WORKER_IDLE_SLEEP", 0.01)
    monkeypatch.setattr(worker.slice_scope, "ready", lambda con, coll: True)
    monkeypatch.setattr(worker.embed_select, "ready", lambda con, coll: True)
    monkeypatch.setattr(worker, "producers_enabled", lambda *a: False)
    monkeypatch.setattr(worker, "owns_own_embed", lambda coll: True)
    with my._lock:
        my._yielding["gutenberg"] = {"since": 0.0, "anon": 22 * GIB, "limit": LIMIT, "why": "it has the most"}
    t = threading.Thread(target=worker._pipeline_worker, args=(c, gone), daemon=True)
    t.start()
    try:
        import time
        time.sleep(0.3)
        assert steps == [], "nothing new of a yielding collection starts"
        con = db.connect(c)
        assert any("yielding to the worker's memory" in r["message"]
                   for r in con.execute("SELECT message FROM events"))
        my.reset()                                   # the worker is under its low line again
        t.join(5.0)
        assert steps == ["gutenberg"], "and it goes on by itself"
    finally:
        gone.set()
        t.join(2.0)
