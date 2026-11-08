"""PubMed's update files through the worker's own path — discovery (the
supersede rule, DeleteCitation's withdraw, the pack built in the calling
process), the rdf and fts stages — over a baseline and many update files,
with PMIDs revised again and again across files: the Python heap stays
FLAT, whatever the number of files (tracemalloc, the standard tool).

Written for the worker's OOM kills of 2026-11-08 (docs/notes/STATUS.md):
on fixtures this path holds nothing per file or per PMID beyond the
process's one byte-budgeted read cache, which is pinned small here so that
it is full from the first file. What grows on fixtures is the vector
store's caches, native memory tracemalloc cannot see
(tests/test_lance_session.py).
"""
from __future__ import annotations

import gc
import random
import tracemalloc

import pytest

from app import backend, db, discovery, worker
from tests.test_pubmed_updatefiles import _article, _collection, _container, _delete

PER_FILE = 240
FILES = 8


def _held_by_the_path(snapshot) -> int:
    """Bytes the traced heap holds, less this test's own bookkeeping (its
    PMID lists) and tracemalloc's."""
    return sum(st.size for st in snapshot.filter_traces([
        tracemalloc.Filter(False, __file__), tracemalloc.Filter(False, tracemalloc.__file__)]
    ).statistics("filename"))


def _drain(con, c) -> int:
    steps = 0
    while True:
        stage, row = worker._next_book(con, db.get_scope(con), embed_allowed=False, collection=c)
        if stage not in ("rdf", "fts"):
            return steps
        worker._execute_step(con, c, stage, row)
        steps += 1


@pytest.fixture
def small_read_cache(monkeypatch):
    monkeypatch.setattr(backend.READ_CACHE, "budget", 256 << 10)
    backend.READ_CACHE.clear()
    yield
    backend.READ_CACHE.clear()


# HERMETIC: pytest's warning capture keeps every warning a test emits until
# it ends, and tantivy deprecates `delete_documents` once per document (fts
# and catalog: 4,608 warnings here) — ~220 KB a file of the test's own,
# linear, which a run without `-W ignore` measured as the path's growth
# (2026-11-08). A process without the capture keeps none: the default filter
# shows a DeprecationWarning once per place, its registry keyed on the place.
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_the_heap_stays_flat_over_many_update_files(tmp_path, isolated_config, small_read_cache,
                                                     monkeypatch):
    from app import unindexable
    withdrawn = [0]                     # counted, not kept: the test holds no uid
    monkeypatch.setattr(unindexable, "withdraw_many",
                        lambda con_, coll, uids: withdrawn.__setitem__(0, withdrawn[0] + len(uids)))
    rng = random.Random(11)
    c = _collection(tmp_path)
    nxt, held = 30_000_001, []
    for n in (1, 2):                                   # the baseline
        kids = []
        for _ in range(PER_FILE):
            kids.append(_article(nxt, f"Baseline citation {nxt}."))
            held.append(nxt)
            nxt += 1
        _container(c.corpus_dir / f"pubmed26n{n:04d}.xml.gz", *kids)
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
        _drain(con, c)
        gc.collect()
        tracemalloc.start(1)
        after: list[int] = []
        peaks: list[int] = []
        for k in range(3, 3 + FILES):
            revised = rng.sample(held, PER_FILE // 2)  # PMIDs revised again, file after file
            kept = set(revised)
            gone = rng.sample([p for p in held if p not in kept], PER_FILE // 20)
            kids = [_article(p, f"Citation {p}, revision {k}.", f"Corrected in file {k}.") for p in revised]
            for _ in range(PER_FILE // 2 - PER_FILE // 20):
                kids.append(_article(nxt, f"New citation {nxt}."))
                held.append(nxt)
                nxt += 1
            kids.append(_delete(*gone))
            held = [p for p in held if p not in set(gone)]
            _container(c.corpus_dir / "updatefiles" / f"pubmed26n{k:04d}.xml.gz", *kids)
            tracemalloc.reset_peak()
            stats = discovery.sync_collection(con, c)
            assert (stats["added"], stats["changed"]) == (PER_FILE // 2 - PER_FILE // 20, PER_FILE // 2)
            assert _drain(con, c) > 0
            gc.collect()
            peaks.append(tracemalloc.get_traced_memory()[1])
            after.append(_held_by_the_path(tracemalloc.take_snapshot()))
        tracemalloc.stop()
        assert withdrawn[0] == FILES * (PER_FILE // 20), "every DeleteCitation withdrawn"
        pending = con.execute("SELECT COUNT(*) FROM documents WHERE error IS NULL AND "
                              "(rdf_done=0 OR fts_done=0)").fetchone()[0]
        assert pending == 0, "every revision and new citation indexed"
    finally:
        con.close()
    # FLAT: what the path holds after the last file is what it held after the
    # second, but for its per-CONTAINER state (the scan cursor's entry, the
    # pack's memo and lock: ~1 KiB a file, bounded by the origin's files) —
    # against the 6 × 240 records processed since: 32 bytes kept a record
    # would be 45 KiB, a record's XML kept ~0.6 MiB
    grown = after[-1] - after[1]
    assert grown < 40 << 10, f"the path's heap grew {grown} bytes over {FILES - 2} files: {after}"
    assert max(peaks[2:]) < 1.25 * max(peaks[:2]), f"the per-file peak grows: {peaks}"
