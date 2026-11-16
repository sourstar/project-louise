"""Every view of the shared snapshot shares its keyed bounds (app/share/seeder.py
`current_snapshot`, 2026-11-16).

The embed pool takes a view per document. The bounds cache was created on each
view, so every document re-keyed and sorted all of wikipedia's 26,551 parts:
35–60 ms of pure Python a document, the GIL held 57.8 s of 60 by the pool, and
the embed lanes starved (py-spy --gil, München; docs/notes/vega-batch.md §6).
"""
from __future__ import annotations

from app.hold import records, snapshot as sn
from app.share import seeder


def _shard(n: int, first: int, last: int) -> sn.Shard:
    return sn.Shard(slice="lang=en", n=n, root="sha256:" + "0" * 64, records_root="sha256:" + "1" * 64,
                    first_id=str(first), last_id=str(last), records=last - first + 1, chunks=1,
                    chunks_root="sha256:" + "2" * 64, tokens=1, bytes=1)


def _snap(tmp_path) -> sn.Snapshot:
    f = tmp_path / "2026-11.json"
    f.write_text('{"added": [], "changed": [], "tombstoned": []}')
    shards = tuple(_shard(i + 1, i * 100 + 1, i * 100 + 100) for i in range(50))
    return sn.Snapshot(collection_id="sha256:" + "a" * 64, snapshot_id="2026-11", previous=None,
                       shards=shards, delta=sn.LazyDelta(f, 0, f.stat().st_size))


def test_every_view_shares_the_keyed_bounds(tmp_path, monkeypatch):
    shared = _snap(tmp_path)
    monkeypatch.setattr(seeder, "_shared", lambda _d: shared)
    keyed = []
    real = records.order_key

    def counting(native_id, order):
        keyed.append(native_id)
        return real(native_id, order)
    monkeypatch.setattr(records, "order_key", counting)

    first, second = seeder.current_snapshot(tmp_path), seeder.current_snapshot(tmp_path)
    assert first is not second and first.delta is not second.delta, "each caller still has its own view"
    assert [s.n for s in first.lookup_order("250", "numeric")] == [3]
    after_first = len(keyed)
    assert after_first > 100, "the first view keyed every part's bounds"
    assert [s.n for s in second.lookup_order("4999", "numeric")] == [50]
    assert len(keyed) - after_first == 1, "the second view keyed only its own id, not the bounds again"
    assert second.__dict__["_keyed_cache"] is first.__dict__["_keyed_cache"] is shared.__dict__["_keyed_cache"]


def test_the_shared_bounds_change_no_answer(tmp_path, monkeypatch):
    shared = _snap(tmp_path)
    monkeypatch.setattr(seeder, "_shared", lambda _d: shared)
    alone = _snap(tmp_path)                       # never viewed: keys its own bounds
    view = seeder.current_snapshot(tmp_path)
    for nid in ("1", "100", "101", "2500", "5000", "5001", "0"):
        assert [s.n for s in view.lookup_order(nid, "numeric")] == \
            [s.n for s in alone.lookup_order(nid, "numeric")], nid
