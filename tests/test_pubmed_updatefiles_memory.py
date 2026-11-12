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

PER_FILE = 480
FILES = 12
#: the first measured files take what the process builds once (a resolver's
#: index for a new stamp, the free lists' fill); the slope is read after them
WARM = 3
#: a leak of 16 bytes a record is 7.5 KiB a file at PER_FILE; what the path
#: holds per FILE (the scan cursor's entry, the pack's memo and lock) does not
#: grow with the records in it: 3.1 KiB a file at 240 records, 1.6 at 480
#: (2026-11-12, before the per-pack glob went)
SLOPE_MAX = 6 << 10


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
    # FLAT: the heap's TREND over the files after the warm-up, by least
    # squares (statistics.linear_regression), held under what a per-RECORD
    # leak would add — 16 bytes kept a record is 7.5 KiB a file, a record's
    # XML kept ~1.2 MiB. A spot difference (last − second, the test until
    # 2026-11-12) read Python's free lists and one-time builds as growth:
    # ±15 KiB file to file, more than the bound, with nothing kept per record.
    from statistics import linear_regression
    slope = linear_regression(list(range(WARM, FILES)), after[WARM:]).slope
    assert slope < SLOPE_MAX, \
        f"the path's heap grows {slope:,.0f} bytes a file ({PER_FILE} records each): {after}"
    assert max(peaks[2:]) < 1.25 * max(peaks[:2]), f"the per-file peak grows: {peaks}"
