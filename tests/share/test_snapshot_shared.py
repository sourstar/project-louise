"""The current snapshot, loaded ONCE per file and shared (app/share/seeder.py
`current_snapshot`; docs/notes/STATUS.md, 2026-11-08).

Every background thread of the worker — the seeder's sync, the digest's
entries, the share fetch, the index maintainer — loaded the newest snapshot
anew on every beat, and each load hashes the whole file (pubmed's 2026-08:
709 MB, 1.2 s; three threads at once on the node, py-spy). Now one caller
loads a file (a single flight: the others wait for it), every caller gets a
view of the one header, and a delta a caller reads is its own and leaves
with it. tracemalloc counts the copies.
"""
from __future__ import annotations

import gc
import os
import threading
import tracemalloc
from pathlib import Path

import pytest

from app.hold import snapshot as sn
from app.share import seeder

PARTS = 3000
UIDS = 50_000


def _tree(tmp_path: Path) -> Path:
    shards = tuple(sn.Shard(slice=f"year={1900 + i // 20}", n=i % 20 + 1, root=f"{i:064x}",
                            records_root="cd" * 32, first_id=str(i * 100), last_id=str(i * 100 + 99),
                            records=100, chunks=100, chunks_root="ef" * 32, tokens=51_200, bytes=1 << 20)
                   for i in range(PARTS))
    snap = sn.Snapshot(collection_id="sha256:" + "11" * 32, snapshot_id="2026-08", previous=None,
                       shards=shards, delta=sn.Delta(tuple(f"pubmed:{u}" for u in range(UIDS)), (), ()),
                       files={s.path: {"length": s.bytes, "root": s.root} for s in shards})
    tree = tmp_path / "shard"
    (tree / "snapshots").mkdir(parents=True)
    (tree / "snapshots" / "2026-08.json").write_bytes(snap.canonical())
    return tree


@pytest.fixture
def loads(monkeypatch):
    got: list[str] = []
    real = sn.load

    def counted(path, *a, **k):
        got.append(str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(sn, "load", counted)
    return got


def _one_copy(tree: Path) -> tuple[int, int]:
    """What one loaded header holds on the traced heap, and the peak of its
    load (the parsed JSON and the dataclasses at once)."""
    gc.collect()
    before = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    held = sn.load(tree / "snapshots" / "2026-08.json")
    peak = tracemalloc.get_traced_memory()[1] - before
    gc.collect()
    size = tracemalloc.get_traced_memory()[0] - before
    del held
    return size, peak


def _asked_at_once(tree: Path, n: int, rounds: int = 1, touch=None) -> list:
    views: list = []
    lock = threading.Lock()
    start = threading.Barrier(n + (1 if touch else 0))

    def ask():
        start.wait()
        for _ in range(rounds):
            v = seeder.current_snapshot(tree)
            with lock:
                views.append(v)
    threads = [threading.Thread(target=ask) for _ in range(n)]
    if touch:
        threads.append(threading.Thread(target=lambda: (start.wait(), touch())))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return views


def test_threads_asking_at_once_share_one_load(tmp_path, loads):
    tree = _tree(tmp_path)
    tracemalloc.start(1)
    try:
        _copy, one_load = _one_copy(tree)
        loads.clear()
        gc.collect()
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        views = _asked_at_once(tree, 8)
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert loads == [str(tree / "snapshots" / "2026-08.json")], "one load for eight callers"
    assert len({id(v.shards) for v in views}) == 1, "one header, shared"
    assert len({id(v) for v in views}) == 8, "a view each"
    assert peak < 1.5 * one_load, f"{peak} bytes at the peak for eight callers, one load's is {one_load}"


def test_a_snapshot_whose_file_changes_is_held_once(tmp_path, loads):
    """The file re-stamped while threads ask: one load per stamp, never one
    per caller, and the cache keeps the newest alone."""
    tree = _tree(tmp_path)
    path = tree / "snapshots" / "2026-08.json"
    stamps: list[int] = []

    def touch():
        st = path.stat()
        for k in range(1, 4):
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + k * 1_000_000_000))
            stamps.append(k)
    tracemalloc.start(1)
    try:
        copy, _peak = _one_copy(tree)
        loads.clear()
        gc.collect()
        base = tracemalloc.get_traced_memory()[0]
        views = _asked_at_once(tree, 8, rounds=25, touch=touch)
        assert len(views) == 200 and all(v.snapshot_id == "2026-08" for v in views)
        del views
        gc.collect()
        left = tracemalloc.get_traced_memory()[0] - base
    finally:
        tracemalloc.stop()
    assert 1 <= len(loads) <= len(stamps) + 1, f"{len(loads)} loads for {len(stamps)} re-stamps"
    held = [k for k in seeder._SNAPS if k == str(tree)]
    assert len(held) == 1 and seeder._SNAPS[str(tree)][1][2] == path.stat().st_mtime_ns
    assert left < 1.5 * copy, f"{left} bytes left held after the callers let go, one copy is {copy}"


def test_a_delta_read_is_the_callers_and_leaves_with_it(tmp_path, loads):
    tree = _tree(tmp_path)
    tracemalloc.start(1)
    try:
        first = seeder.current_snapshot(tree)
        gc.collect()
        base = tracemalloc.get_traced_memory()[0]
        mine = seeder.current_snapshot(tree)
        assert len(mine.delta.added) == UIDS
        read = tracemalloc.get_traced_memory()[0] - base
        other = seeder.current_snapshot(tree)
        assert other.delta._delta is None, "another caller's view has read nothing"
        assert other.hash == mine.hash == first.hash
        del mine
        gc.collect()
        left = tracemalloc.get_traced_memory()[0] - base
    finally:
        tracemalloc.stop()
    assert read > 1 << 20, "the delta parsed is measurable"
    assert left < read / 10, f"{left} bytes still held after the reader let go of a {read}-byte delta"
    assert seeder._SNAPS[str(tree)][2].delta._delta is None, "the shared snapshot never holds a delta"
    assert len(loads) == 1


def test_possession_asks_the_shared_load_from_every_thread(tmp_path, loads, monkeypatch):
    """2026-11-08, #416 on the node: `holding` (every embed thread) reached
    `possession.current_snapshot`, which loaded the snapshot itself with a
    memo but no lock. 65 threads loaded pubmed's 709 MB file at once and the
    worker went to 93 GB in 30 s. It is the shared single flight now."""
    from app.hold import collection as col
    from app.share import possession
    tree = _tree(tmp_path)
    monkeypatch.setattr(col, "shard_dir_of", lambda root, name: tree)
    seeder._SNAPS.clear()
    got: list = []
    gate = threading.Barrier(16)

    def ask() -> None:
        gate.wait()
        got.append(possession.current_snapshot(tmp_path, "pubmed"))

    threads = [threading.Thread(target=ask) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 16 and all(g is not None and g.snapshot_id == "2026-08" for g in got)
    assert len([p for p in loads if p.endswith("2026-08.json")]) <= 1, loads
