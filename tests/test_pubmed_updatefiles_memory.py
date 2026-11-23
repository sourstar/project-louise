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
(tests/test_lance_session.py). Left out of the measure: the interpreter's
interned-string table, whose move to a bigger table landed in the window
only after a test had started the web app (2026-11-22; `_INTERNED_TABLE`).
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


def _usable(log2_slots: int) -> int:
    """The entries a dict table of 2**log2_slots slots holds before it grows
    (CPython's USABLE_FRACTION, Objects/dictobject.c: two thirds)."""
    return (1 << log2_slots) * 2 // 3


def _str_table_bytes(log2_slots: int) -> int:
    """The one block of a str-keyed dict's table at 2**log2_slots slots, as
    CPython lays it out (dictobject.c `new_keys_object`): a 32-byte header,
    a 4-byte index a slot (2**16 to 2**31 slots), a 16-byte entry (key,
    value) for each usable one. 2**18: 3,844,800; 2**19: 7,689,584."""
    return 32 + (1 << log2_slots) * 4 + _usable(log2_slots) * 16


# THE INTERPRETER'S INTERNED-STRING TABLE IS NOT THE PATH'S (2026-11-23). The
# process keeps every interned str in one dict, and pathlib interns every
# component of every path it parses (`PurePath._parse_path`: sys.intern):
# 2-3 new names a file on 3.12, where they are immortal; ~5,700 slots a file
# on 3.13, where they are mortal and come back as new entries. When its
# slots run out the dict moves to a new table (doubled, or compacted), and
# the old one, built before tracemalloc.start, was never traced: the file
# where that happens shows the WHOLE new table as growth, charged to
# pathlib's line — 3.8 MB at 2**18 slots, 7.7 MB at 2**19 — and the peak
# with it. Which file, if any, depends on how full the table was when the
# test began: a test that started the web app before this one had interned
# ~6,450 more strings (3.12; 3.13 leaves 2**18 slots, ~90,000 free), and
# the 12th file's 8,304,257 bytes were 7,689,584 of table (2026-11-22).
# Measured as a str-keyed dict table of 2**16 slots or more: in this window
# no other dict reaches one — the records here could not fill 2**15
# (`test_the_heap_stays_flat…` asserts it) — and a leak into such a dict
# would still be counted by its keys and values; only its table is left out.
_INTERNED_TABLE = frozenset(_str_table_bytes(k) for k in range(16, 31))


def _held_by_the_path(snapshot) -> tuple[int, int]:
    """(bytes the traced heap holds, less this test's own bookkeeping (its
    PMID lists), tracemalloc's and the interpreter's interned-string table;
    the bytes of that table left out)."""
    held = table = 0
    for t in snapshot.filter_traces([tracemalloc.Filter(False, __file__),
                                     tracemalloc.Filter(False, tracemalloc.__file__)]).traces:
        if t.size in _INTERNED_TABLE:
            table += t.size
        else:
            held += t.size
    return held, table


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
    # no dict the records could build reaches a table `_held_by_the_path` leaves out
    assert (2 + FILES) * PER_FILE <= _usable(15)
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
        tables: list[int] = []                         # the interned table left out, file by file
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
            peak = tracemalloc.get_traced_memory()[1]
            on_path, table = _held_by_the_path(tracemalloc.take_snapshot())
            # the table is held across the file, so it is in its peak; where
            # it moved in this file the peak loses at most what preceded it
            peaks.append(peak - table)
            after.append(on_path)
            tables.append(table)
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
        f"the path's heap grows {slope:,.0f} bytes a file ({PER_FILE} records each): {after} " \
        f"(the interned table, left out: {tables})"
    assert max(peaks[2:]) < 1.25 * max(peaks[:2]), \
        f"the per-file peak grows: {peaks} (the interned table, left out: {tables})"


def test_the_measure_leaves_out_a_table_built_before_tracing_and_keeps_a_leak():
    """The failure of 2026-11-22 in miniature, without touching the
    process's interned table: a str-keyed dict filled before tracing to the
    edge of its 2**17 slots takes one more key inside the window, and its
    new table — 2**18 slots, 3.8 MB the old one never showed — is left out
    (the layout `_str_table_bytes` states, checked on this interpreter),
    while 16 bytes kept a record beside it are counted. The measure of
    before 2026-11-23 counted the table: 3.8 MB of "growth"."""
    edge = {f"\x01edge-{i}": None for i in range(_usable(17))}
    kept: list[bytes] = []
    # compiled under names of their own, as pathlib's and the app's lines are,
    # so that the filter of this file's own bookkeeping does not hide them
    grow = compile("edge['\\x01one more'] = None", "<the table moves>", "exec")
    leak = compile("for _ in range(n): kept.append(bytes(16))", "<a leak of 16 bytes a record>", "exec")
    tracemalloc.start(1)
    try:
        gc.collect()
        before, _ = _held_by_the_path(tracemalloc.take_snapshot())
        exec(grow, {"edge": edge})
        exec(leak, {"kept": kept, "n": PER_FILE})
        snapshot = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
    assert _str_table_bytes(18) in {t.size for t in snapshot.traces}, \
        "this interpreter's str-keyed dict table is not the layout _str_table_bytes states"
    held, table = _held_by_the_path(snapshot)
    assert table == _str_table_bytes(18), table
    assert PER_FILE * 16 < held - before < 64 << 10, \
        f"{held - before:,} bytes counted for {PER_FILE} records of 16 bytes and a table left out"
    assert len(kept) == PER_FILE and len(edge) == _usable(17) + 1
