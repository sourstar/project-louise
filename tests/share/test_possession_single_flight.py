"""One build per part, whatever the threads asking (app/single_flight.py,
app/share/possession.py `_flights`).

2026-11-08, #419 on the node: the snapshot was shared, and the embed pool's
65 threads piled up one step on. 42 of them decoded the SAME part's records
in `part_records` on a `_part_hashes_cache` miss, before the part's file
existed (py-spy at 8.3 GB; cgroup OOM at 23.2 GB 45 s after start, with
pubmed paused). Now one thread decodes a part, the others get its answer,
and the decode streams the records instead of holding the part.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import tracemalloc
from types import SimpleNamespace

import pytest

from app import backend, single_flight
from app.hold import land
from app.share import contribute, possession
from app.share import coverage as cov

THREADS = 16
OWN = "sha256:" + "a" * 64
SEG = "0123456789abcdef"
CODEX = SimpleNamespace(collection_id=OWN, space_segment=SEG, admitted_segments=())
PART = SimpleNamespace(slice="lang=en", n=1, first_id="1", last_id="2000", root="sha256:" + "cd" * 32,
                       chunks=2000)


class Snap:
    shards, snapshot_id = (PART,), "2026-09"

    def lookup_order(self, native_id, order="bytes"):
        return [PART] if 1 <= int(native_id) <= 2000 else []


def _at_once(n: int, fn) -> list:
    gate = threading.Barrier(n)
    out: list = []
    lock = threading.Lock()

    def run(i: int) -> None:
        gate.wait()
        got = fn(i)
        with lock:
            out.append(got)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One part of 2,000 records whose share/PARTS file does not exist yet
    (the node's first run of #416), one accepted bundle over it, and a
    records reader that counts its decodes and takes its time."""
    decodes: list[int] = []

    def records_for(root, name, snapshot, shard, uids, *, stream=False):
        decodes.append(1)
        time.sleep(0.2)                    # every thread arrives during the decode

        def gen():
            for u in range(1, 2001):
                yield {"uid": f"c:{u}", "content_hash": f"h{u}"}
        return gen() if stream else list(gen())
    monkeypatch.setattr(land, "records_for", records_for)
    monkeypatch.setattr(possession, "current_snapshot", lambda root, name: Snap())
    monkeypatch.setattr(possession, "prefixes", lambda root, name, codex: [f"{OWN}/{SEG}/"])
    bdir = tmp_path / "bundles"
    monkeypatch.setattr(contribute, "bundle_dir",
                        lambda root, name, codex, sid, bundle_id, **kw: bdir / sid / bundle_id)
    d = bdir / "lang=en/00001" / "b1"
    d.mkdir(parents=True)
    (d / "possession.json").write_text(json.dumps({f"c:{u}": f"h{u}" for u in range(1, 2001)}))
    db = tmp_path / "coverage.db"
    con = cov.ensure_schema(sqlite3.connect(db))
    cov.record(con, bundle_id="b1", shard=f"{OWN}/{SEG}/lang=en/00001", producer="k",
               uids=[f"c:{u}" for u in range(1, 2001)], chunks=2000, tiers={}, source=cov.FETCHED,
               accepted_at="2026-11-01T00:00:00Z")
    con.commit()
    con.close()
    backend.READ_CACHE.clear()

    def connect():
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        return c
    return SimpleNamespace(root=tmp_path, decodes=decodes, connect=connect)


def test_threads_asking_holding_for_one_uncached_part_decode_it_once(world):
    """München's test: N threads asking `holding` for uids of one uncached
    part decode it once."""
    def ask(i: int):
        con = world.connect()
        try:
            return possession.holding(world.root, "wikipedia", CODEX, con, f"c:{i + 1}", str(i + 1))
        finally:
            con.close()
    got = _at_once(THREADS, ask)
    assert len(world.decodes) == 1, f"{len(world.decodes)} decodes of one part for {THREADS} threads"
    assert all(g is not None for g in got)
    assert sorted(g[1] for g in got) == sorted(f"h{i + 1}" for i in range(THREADS))
    assert len({id(g[0]) for g in got}) == 1, "one possession view of the part, shared"
    assert len(possession._flights) == 0, "nothing left in flight"


def test_the_part_view_and_its_bundle_map_are_built_once(world, monkeypatch):
    """The same defect one layer down: the part's possession view and the
    bundle maps it reads were filled the same unguarded way."""
    built: list[int] = []
    real = possession._part_view
    monkeypatch.setattr(possession, "_part_view", lambda *a, **k: (built.append(1), real(*a, **k))[1])
    loads: list[str] = []
    real_loads = possession.json.loads

    def counted(raw, *a, **k):
        loads.append("x")
        time.sleep(0.05)
        return real_loads(raw, *a, **k)
    monkeypatch.setattr(possession.json, "loads", counted)
    bounds = possession._key_bounds(PART.first_id, PART.last_id)

    def ask(_i: int):
        con = world.connect()
        try:
            return possession.part_possession(world.root, "wikipedia", CODEX, con, PART.slice, bounds)
        finally:
            con.close()
    got = _at_once(THREADS, ask)
    assert len({id(v) for v in got}) == 1
    assert len(built) == 1, f"{len(built)} builds of one view"
    assert len(loads) == 1, f"{len(loads)} reads of one bundle's possession.json"


def test_a_part_decode_streams_its_records(tmp_path, monkeypatch):
    """Two fields a record are kept: a part of big records is never held
    whole while its hashes are read (2026-11-08: the threads in
    `records_for` held whole wikipedia parts)."""
    big = "x" * (1 << 20)

    def records_for(root, name, snapshot, shard, uids, *, stream=False):
        def gen():
            for u in range(64):
                yield {"uid": f"c:{u}", "content_hash": f"h{u}", "text": big + str(u)}
        return gen() if stream else list(gen())
    monkeypatch.setattr(land, "records_for", records_for)
    tracemalloc.start(1)
    try:
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        got = possession.part_records(tmp_path, "wikipedia", None, PART)
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert got == [(f"c:{u}", f"h{u}") for u in range(64)]
    assert peak < 8 << 20, f"{peak} bytes at the peak for a 64 MiB part: it was held whole"


def test_the_group_hands_one_result_to_every_caller():
    group = single_flight.Group()
    runs: list[int] = []

    def slow():
        runs.append(1)
        time.sleep(0.2)
        return object()
    got = _at_once(THREADS, lambda _i: group.do("k", slow))
    assert len(runs) == 1 and len({id(g) for g in got}) == 1
    assert len(group) == 0


def test_the_group_raises_the_leaders_error_to_its_waiters():
    group = single_flight.Group()
    runs: list[int] = []

    def fails():
        runs.append(1)
        time.sleep(0.2)
        raise OSError("disk")

    def ask(_i: int):
        try:
            group.do("k", fails)
        except OSError as e:
            return str(e)
    assert _at_once(8, ask) == ["disk"] * 8 and len(runs) == 1
    assert len(group) == 0, "a failed call leaves nothing in flight: the next caller tries again"


def test_a_waiter_waits_with_a_bound_then_does_the_work_itself():
    """CLAUDE.md: a pipeline thread never waits without a bound."""
    group = single_flight.Group()
    release = threading.Event()
    started = threading.Event()

    def stuck():
        started.set()
        release.wait(5)
        return "leader"
    t = threading.Thread(target=lambda: group.do("k", stuck))
    t.start()
    started.wait(5)
    t0 = time.monotonic()
    assert group.do("k", lambda: "mine", timeout=0.1) == "mine"
    assert time.monotonic() - t0 < 2
    release.set()
    t.join()


def test_a_bundles_vectors_are_read_once(tmp_path, monkeypatch):
    """One layer further down: `held_vectors` reads a bundle's vectors as
    lists; every thread of a part asks the same bundle."""
    reads: list[int] = []

    class T:
        def column(self, c):
            return SimpleNamespace(to_pylist=lambda: [f"x{i}" if c == "chunk_id" else [0.0] for i in range(10)])

    def read_table(path, columns=None):
        reads.append(1)
        time.sleep(0.2)
        return T()
    import pyarrow.parquet as pq
    monkeypatch.setattr(pq, "read_table", read_table)
    d = tmp_path / "b"
    d.mkdir()
    (d / "header.json").write_text("{}")
    monkeypatch.setattr(contribute, "bundle_dir", lambda *a, **k: d)
    possession._vectors_cache.clear()
    got = _at_once(THREADS, lambda _i: possession._bundle_vectors(tmp_path, "pubmed", CODEX, "s", "b-1"))
    assert len(reads) == 1 and len({id(g) for g in got}) == 1
