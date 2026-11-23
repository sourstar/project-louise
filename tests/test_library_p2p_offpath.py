"""The Library card's p2p line is computed off the request path (app/web.py
`_p2p_summary`). On it, every bundle gutenberg accepted made the next view
recompute coverage over 1,639 parts — 6-14 s each, every 1-3 min, seven
views stacked on the same rebuild (München, 2026-10-27)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app import web


@pytest.fixture
def slow(monkeypatch):
    calls: list[float] = []
    gate = threading.Event()

    def compute(collection):
        calls.append(time.time())
        gate.wait(5)
        return {"parts": len(calls)}

    monkeypatch.setattr(web, "_p2p_compute", compute)
    monkeypatch.setattr(web, "_p2p_cache", {})
    monkeypatch.setattr(web, "_p2p_refreshing", {})
    monkeypatch.setattr(web, "_COUNTS_COLD_WAIT_S", 0.2)
    c = SimpleNamespace(name="g", manifest_path="/x/g/state/manifest.sqlite",
                        codex=SimpleNamespace(space=object()))
    return c, calls, gate


def test_a_stale_value_is_served_at_once_and_refreshed_once_behind_it(slow):
    c, calls, gate = slow
    web._p2p_cache[c.manifest_path] = (time.time() - web._P2P_TTL_S - 1, {"parts": 0})
    t0 = time.perf_counter()
    got = [web._p2p_summary(c) for _ in range(7)]           # seven views at once
    assert time.perf_counter() - t0 < 0.5, "never waits for the computation"
    assert got == [{"parts": 0}] * 7
    gate.set()
    for _ in range(100):
        if web._p2p_cache[c.manifest_path][1] == {"parts": 1}:
            break
        time.sleep(0.02)
    assert len(calls) == 1, "one refresh in flight, not one per view"
    assert web._p2p_summary(c) == {"parts": 1}


def test_a_cold_cache_never_waits_and_says_it_is_counting(slow):
    """2 s a card on every view while a big collection's refresh ran: 8.55 s
    of a warm 14.9 s Library (München, 2026-11-22)."""
    c, calls, gate = slow
    t0 = time.perf_counter()
    got = [web._p2p_summary(c) for _ in range(5)]
    assert time.perf_counter() - t0 < 0.1, "no wait, on the first view or the next"
    assert got == [web.P2P_COUNTING] * 5
    gate.set()
    for _ in range(100):
        if c.manifest_path in web._p2p_cache:
            break
        time.sleep(0.02)
    assert web._p2p_summary(c) == {"parts": 1} and len(calls) == 1, "one refresh, then its value"


def test_no_codex_no_line_and_no_work(slow):
    c, calls, gate = slow
    assert web._p2p_summary(SimpleNamespace(name="x", manifest_path="/y", codex=None)) is None
    assert calls == []


def test_a_local_archives_shards_are_walked_once_a_ttl_per_corpus_path(monkeypatch, tmp_path):
    """The card walked the whole corpus for `*.parquet` on every view: 1.57 s
    of a warm Library (München, 2026-11-22)."""
    monkeypatch.setattr(web, "_shard_totals", {})
    walks: list[str] = []

    def walk(c):
        walks.append(str(c.corpus_dir))
        return ("a.parquet", "b.parquet")
    a = SimpleNamespace(corpus_dir=tmp_path / "a")
    b = SimpleNamespace(corpus_dir=tmp_path / "b")
    assert [web._local_shard_total(a, walk) for _ in range(20)] == [2] * 20
    assert web._local_shard_total(b, walk) == 2
    assert walks == [str(a.corpus_dir), str(b.corpus_dir)], "once per corpus path"
    stamp, n = web._shard_totals[str(a.corpus_dir)]
    web._shard_totals[str(a.corpus_dir)] = (stamp - web._SHARD_TOTAL_TTL_S - 1, n)
    web._local_shard_total(a, walk)
    assert len(walks) == 3, "walked again once the count is older than its TTL"
