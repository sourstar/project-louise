"""The wikidata entity table's build (app/plugins/wikidata/entities.py),
2026-11-17: the PACKED SORT writes the generic sort's table byte for byte
in less memory (23 bytes an entity at the peak, measured, against the 60 of
the build before it), refuses an entity twice as the generic one does,
and a lookup WAITS, bounded, for another process building the table — a
check's pool children reach their first lookup together, and the losers of
`derived.writing` errored every record of their page (München's first
records-vs-origin at parser 3 would have)."""
from __future__ import annotations

import threading
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import derived
from app.plugins.wikidata import entities, pack


@pytest.fixture
def chunks(tmp_path, monkeypatch):
    """A pack of `n` entities in chunk files of `per`, in a dump order that
    is not id order, as `pack.build` writes their `native_id` and `index`."""
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    entities.reset()

    def make(n: int, per: int = 10_000, ids=None) -> Path:
        d = tmp_path / "pack"
        d.mkdir(exist_ok=True)
        ids = np.random.default_rng(7).permutation(np.arange(1, n + 1, dtype=np.int64) * 3) if ids is None else ids
        files = []
        for k, i in enumerate(range(0, len(ids), per)):
            f = d / f"{k:05d}.parquet"
            pq.write_table(pa.table({"native_id": pa.array([str(x) for x in ids[i:i + per]]),
                                     "index": pa.array(np.arange(i, i + len(ids[i:i + per]), dtype=np.int64))}), f)
            files.append(f)
        dump = tmp_path / "wikidata-20261012-all.json.bz2"
        dump.write_bytes(b"x")
        monkeypatch.setattr(pack, "read_meta", lambda _d: {"complete": True, "pack_version": 1, "chunks": len(files)})
        monkeypatch.setattr(pack, "chunk_files", lambda _d: files)
        monkeypatch.setattr(pack, "pack_dir", lambda _d: d)
        return dump
    yield make
    entities.reset()


def test_the_packed_sort_writes_the_generic_tables_bytes(chunks, monkeypatch):
    dump = chunks(120_000)
    packed = entities.build(dump).read_bytes()
    entities.path_of(dump).unlink()
    monkeypatch.setattr(entities, "packed_of", lambda path: None)          # the generic sort
    assert entities.build(dump).read_bytes() == packed
    entities.reset()
    assert entities.index_of(dump, 3 * 5) is not None and entities.index_of(dump, 4) is None


def test_a_property_s_id_is_packed_and_one_too_large_takes_the_generic_sort(chunks, monkeypatch):
    from app.plugins.wikidata import ids
    dump = chunks(0, ids=np.array([42, ids.PROPERTY_BASE + 31, 5], dtype=np.int64))
    assert [entities.index_of(dump, k) for k in (42, ids.PROPERTY_BASE + 31, 5)] == [0, 1, 2]
    assert entities.packed_of(str(pack.chunk_files(dump)[0])) is not None
    entities.path_of(dump).unlink()
    entities.reset()
    big = chunks(0, ids=np.array([1 << 40, 7], dtype=np.int64))
    assert entities.packed_of(str(pack.chunk_files(big)[0])) is None
    assert entities.index_of(big, 1 << 40) == 0 and entities.index_of(big, 7) == 1


@pytest.mark.parametrize("generic", [False, True])
def test_an_entity_twice_is_refused(chunks, monkeypatch, generic):
    dump = chunks(0, ids=np.array([9, 4, 9, 1], dtype=np.int64), per=2)
    if generic:
        monkeypatch.setattr(entities, "packed_of", lambda path: None)
    with pytest.raises(entities.EntityTableError, match="key 9 is listed twice"):
        entities.build(dump)
    assert not entities.path_of(dump).exists()


def test_the_packed_build_holds_less_than_the_generic_sort(chunks, monkeypatch):
    """numpy's allocations, which tracemalloc sees (Arrow's it does not):
    the packed sort's peak is its chunks and their concatenation, ~21 bytes
    an entity here; the generic sort's ~33, its order and both sorted
    copies beside the concatenations. (The process's RSS, measured with
    Arrow's reads: 23 against 60 bytes an entity before 2026-11-17.)"""
    n = 400_000
    dump = chunks(n, per=50_000)

    def peak() -> int:
        entities.path_of(dump).unlink(missing_ok=True)
        entities.reset()
        tracemalloc.start()
        try:
            entities.build(dump, workers=1)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    packed = peak()
    monkeypatch.setattr(entities, "packed_of", lambda path: None)
    generic = peak()
    assert packed < 24 * n, packed
    assert generic > 1.4 * packed, (generic, packed)


def test_a_lookup_waits_for_another_builder_and_defers_past_its_bound(chunks):
    dump = chunks(30_000)
    lock = pack.pack_dir(dump) / pack.WRITER
    held, release = threading.Event(), threading.Event()

    def other():
        with derived.writing(lock, "another process's pack build"):
            held.set()
            release.wait(10)
    t = threading.Thread(target=other)
    t.start()
    held.wait(5)
    try:
        with pytest.raises(derived.Busy):
            entities.ensure(dump, wait_s=0.3)
        threading.Timer(0.4, release.set).start()
        t0 = time.monotonic()
        p = entities.ensure(dump, wait_s=10)
        assert p.is_file() and time.monotonic() - t0 >= 0.3, "waited, then built"
    finally:
        release.set()
        t.join(5)
