"""The scan's fetch-and-hash on the PARSE POOL (app/discovery.py `_hashes`;
CLAUDE.md: multi-threading is a must): a dated dump's next run moves every
page's signature, so its scan is all hashing — of the new run's page and of
the old run's copy where the row never recorded a hash. The pool and the
serial scan give the same hashes and the same decisions, row for row."""
from __future__ import annotations

import shutil

import pytest

from app import collections as collections_mod
from app import config, db, discovery, parallel
from app.hold import update_state
from app.plugins.wikipedia import archive
from app.plugins.wikipedia import source as wsrc
from tests.hold import wiki_runs

A = [(pid, f"Page {pid}", f"Text of page {pid}. " * (5 + pid % 7)) for pid in range(100, 400)]
GONE = {p for p, _, _ in A if p % 37 == 0}
CHANGED = {p for p, _, _ in A if p % 11 == 0} - GONE
B = [(p, t, txt + " Revised." if p in CHANGED else txt) for p, t, txt in A if p not in GONE]
B += [(pid, f"Page {pid}", f"New page {pid}.") for pid in range(400, 420)]


def _state(con) -> list[tuple]:
    return [tuple(r) for r in con.execute("SELECT uid, path, mtime, size, txt_hash, rdf_done, fts_done, "
                                          "embed_done, error FROM documents ORDER BY uid")]


@pytest.fixture
def two_runs(isolated_config, data_dir, config_dir):
    for cache in (wsrc._stream_cache, archive._groups, archive._layouts):
        cache.clear()
    root, _cx = wiki_runs.node_collection(data_dir, config_dir)
    corpus = root / "wikipedia" / "origin"
    wiki_runs.dump(corpus, wiki_runs.FIRST, A, props=[(p, "wikibase_item", f"Q{p}") for p, _, _ in A])
    c = collections_mod.by_name("wikipedia")
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
        con.execute("UPDATE documents SET rdf_done=1, fts_done=1, embed_done=1")
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM documents WHERE txt_hash IS NULL").fetchone()[0] == len(A)
    finally:
        con.close()
    wiki_runs.dump(corpus, "20261101", B, props=[(p, "wikibase_item", f"Q{p}") for p, _, _ in B])
    update_state.write(root, "wikipedia", {"period": "2026-11", "run": "20261101", "moved": True,
                                           "step": "run"})
    seed = data_dir / "state-after-A"
    shutil.copytree(root / "wikipedia" / "state", seed)
    return root, seed


def _scan(root, seed, processes: int, monkeypatch, *, unindexed=None):
    from app import unindexable
    state = root / "wikipedia" / "state"
    shutil.rmtree(state)
    shutil.copytree(seed, state)
    wsrc._stream_cache.clear()
    monkeypatch.setattr(config, "PARSE_PROCESSES", processes)
    monkeypatch.setattr(unindexable, "withdraw_many", lambda con, coll, uids: len(uids))
    c = collections_mod.by_name("wikipedia")
    assert c.snapshot == "20261101"
    con = db.connect(c)
    try:
        stats = discovery.sync_collection(con, c)
        return {k: v for k, v in stats.items() if k != "secs"}, _state(con)
    finally:
        con.close()


def test_the_pool_and_the_serial_scan_hash_and_decide_alike(two_runs, monkeypatch):
    root, seed = two_runs
    asked: list[tuple[str, int]] = []
    real = parallel.compute

    def compute(kind, collection, rows):
        asked.append((kind, len(rows)))
        return real(kind, collection, rows)
    monkeypatch.setattr(parallel, "compute", compute)
    serial_stats, serial = _scan(root, seed, 1, monkeypatch)
    assert serial_stats["changed"] == len(CHANGED) and serial_stats["relocated"] == len(A) - len(GONE) - len(CHANGED)
    assert serial_stats["added"] == 20, serial_stats
    parallel.shutdown()
    try:
        pooled_stats, pooled = _scan(root, seed, 2, monkeypatch)
        assert parallel.pool() is not None, "the scan ran on a real pool of two children"
    finally:
        parallel.shutdown()
    assert pooled_stats == serial_stats
    assert pooled == serial, "the same hashes, paths, marks and errors, row for row"
    hashed = [n for kind, n in asked if kind == "discover-hash"]
    assert sum(hashed) == 2 * (len(A) - len(GONE)), "every moved page went through the pool's work, both scans"


def test_a_ref_the_pool_cannot_hash_is_fetched_in_the_scans_process_and_fails_as_before(two_runs,
                                                                                       monkeypatch):
    root, seed = two_runs
    reference_stats, reference = _scan(root, seed, 1, monkeypatch)
    monkeypatch.setattr(parallel, "compute",
                        lambda kind, collection, rows: [(False, {"type": "OSError", "msg": "gone"})] * len(rows))
    stats, rows = _scan(root, seed, 1, monkeypatch)
    assert (stats, rows) == (reference_stats, reference), \
        "every task the pool failed is fetched again in the scan's process: the same decisions"
    # a ref no process can fetch fails the scan as the serial scan did
    from app.plugins.wikipedia.ids import native_id_of
    broken = str(native_id_of("tr", next(r for r in B if r[0] not in CHANGED)[0]))
    real_fetch = wsrc.WikipediaSource.fetch

    def fetch(self, ref):
        if ref.native_id == broken and "/20261101/" in ref.locator:
            raise OSError("the dump is unreadable here")
        return real_fetch(self, ref)
    monkeypatch.setattr(wsrc.WikipediaSource, "fetch", fetch)
    with pytest.raises(OSError, match="unreadable"):
        _scan(root, seed, 1, monkeypatch)
