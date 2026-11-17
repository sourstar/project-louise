"""The wikidata entity table's build (app/plugins/wikidata/entities.py),
2026-11-17: the DIRECT-ADDRESS table (a counting sort: an item's number
indexes a uint32 array of ordinals, properties a small array of their own)
writes the generic sort's table byte for byte in less memory, answers every
lookup the same, refuses an entity twice as the generic one does (within a
chunk and across two), hands a value its arrays cannot hold to the generic
sort, and a lookup WAITS, bounded, for another process building the table —
a check's pool children reach their first lookup together, and the losers of
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

    def make(n: int, per: int = 10_000, ids=None, hint: bool = True) -> Path:
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
        # a version-3 pack's meta names its largest item (pack.py `max_item`): the array's size at once
        from app.plugins.wikidata.ids import PROPERTY_BASE
        items = ids[ids < PROPERTY_BASE]
        top = int(items.max()) if len(items) else 0
        monkeypatch.setattr(pack, "read_meta", lambda _d: {"complete": True, "pack_version": 1, "chunks": len(files),
                                                           **({"max_item": top} if hint else {})})
        monkeypatch.setattr(pack, "chunk_files", lambda _d: files)
        monkeypatch.setattr(pack, "pack_dir", lambda _d: d)
        return dump
    yield make
    entities.reset()


def _generic(monkeypatch):
    """The generic sort, as for a value the arrays cannot hold."""
    def unfit(*a, **k):
        raise entities._Unfit()
    monkeypatch.setattr(entities, "_direct_tables", unfit)


def _answers(dump, keys) -> list:
    entities.reset()
    return [entities.index_of(dump, int(k)) for k in keys]


def test_the_direct_table_writes_the_generic_tables_bytes_and_answers_alike(chunks, monkeypatch):
    from app.plugins.wikidata import ids
    keys = np.concatenate([np.random.default_rng(7).permutation(np.arange(1, 120_001, dtype=np.int64) * 3),
                           ids.PROPERTY_BASE + np.array([31, 279, 1435, 7], dtype=np.int64)])
    dump = chunks(0, ids=keys)
    direct = entities.build(dump).read_bytes()
    probe = [*keys[:500].tolist(), 4, 5, ids.PROPERTY_BASE + 32, 360_003]
    got = _answers(dump, probe)
    entities.path_of(dump).unlink()
    with monkeypatch.context() as m:
        _generic(m)
        assert entities.build(dump).read_bytes() == direct
        assert _answers(dump, probe) == got
    assert got[:3] == [0, 1, 2] and got[-4:-1] == [None, None, None]


def test_a_property_is_its_own_small_array_and_a_number_too_large_takes_the_generic_sort(chunks):
    from app.plugins.wikidata import ids
    # no `max_item` in the meta (a pack before version 3): the array grows as the numbers come
    dump = chunks(0, ids=np.array([42, ids.PROPERTY_BASE + 31, 5, 90_000], dtype=np.int64), per=1, hint=False)
    assert entities.index_of(dump, 90_000) == 3
    assert [entities.index_of(dump, k) for k in (42, ids.PROPERTY_BASE + 31, 5)] == [0, 1, 2]
    entities.path_of(dump).unlink()
    entities.reset()
    big = chunks(0, ids=np.array([1 << 40, 7], dtype=np.int64))
    assert entities.index_of(big, 1 << 40) == 0 and entities.index_of(big, 7) == 1


@pytest.mark.parametrize("generic", [False, True])
@pytest.mark.parametrize("per", [2, 4], ids=["across chunks", "within a chunk"])
def test_an_entity_twice_is_refused(chunks, monkeypatch, generic, per):
    dump = chunks(0, ids=np.array([9, 4, 9, 1], dtype=np.int64), per=per)
    if generic:
        _generic(monkeypatch)
    with pytest.raises(entities.EntityTableError, match="key 9 is listed twice"):
        entities.build(dump)
    assert not entities.path_of(dump).exists()


def test_a_property_twice_is_refused(chunks):
    from app.plugins.wikidata import ids
    dump = chunks(0, ids=np.array([5, ids.PROPERTY_BASE + 31, 6, ids.PROPERTY_BASE + 31], dtype=np.int64), per=2)
    with pytest.raises(entities.EntityTableError, match=f"key {ids.PROPERTY_BASE + 31} is listed twice"):
        entities.build(dump)


def test_the_direct_build_holds_less_than_the_generic_sort(chunks, monkeypatch):
    """numpy's allocations, which tracemalloc sees (Arrow's it does not):
    the direct table's peak is its array (4 bytes a number, the numbers
    here three apart) and a window of chunks; the generic sort's, every
    chunk, their concatenation, its order and both sorted copies."""
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
    direct = peak()
    _generic(monkeypatch)
    generic = peak()
    # the array, 4 bytes a number up to the largest (three numbers an entity here), and a few MiB beside it
    assert direct < 4 * 3 * n + (4 << 20), direct
    assert generic > 1.4 * direct, (generic, direct)


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
