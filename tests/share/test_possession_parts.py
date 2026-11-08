"""Possession PER PART (app/share/possession.py `part_possession`, `holding`;
docs/notes/STATUS.md, 2026-11-08).

The whole collection's index (`index`) costs ~680 B a uid held — 25 GiB for
pubmed's 40 M — and the worker built it on its first embed after a start and
kept it (the node's memray: 11.54 GB under `_index_cold`). The worker now
asks for one part: the accepted rows filed under its slice, through the
coverage table's own B-tree (`idx_coverage_shard`), and their possession
files through the process's byte-budgeted read cache. These tests hold it to
the old index's answers, its plan, and a flat heap whatever the parts.
"""
from __future__ import annotations

import gc
import json
import sqlite3
import tracemalloc
from types import SimpleNamespace

import pytest

from app import backend
from app.share import contribute, possession
from app.share import coverage as cov

OWN, PRED = "sha256:" + "a" * 64, "sha256:" + "b" * 64
SEG = "0123456789abcdef"
CODEX = SimpleNamespace(collection_id=OWN, space_segment=SEG, admitted_segments=())


class Snap:
    """The current snapshot's parts, found as a reader finds a record."""

    def __init__(self, shards):
        self.shards, self.snapshot_id = shards, "2026-09"

    def lookup_order(self, native_id, order="bytes"):
        k = int(native_id)
        return sorted((s for s in self.shards if int(s.first_id) <= k <= int(s.last_id)),
                      key=lambda s: int(s.last_id) - int(s.first_id))


def _part(sl, n, first, last):
    return SimpleNamespace(slice=sl, n=n, first_id=str(first), last_id=str(last), root=f"sha256:{sl}{n}",
                           chunks=10 ** 6)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Own bundles per part, one spanning two re-cut parts, a predecessor's
    under its own numbering, a predecessor's row without files, claims, an
    unaccepted row; records whose hash moved since their bundle."""
    root, name = tmp_path, "books"
    con = cov.ensure_schema(sqlite3.connect(":memory:"))
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE documents(uid TEXT PRIMARY KEY, chunks INTEGER)")
    bdir = tmp_path / "bundles"
    monkeypatch.setattr(contribute, "bundle_dir",
                        lambda root_, name_, codex, sid, bundle_id, **kw: bdir / sid / bundle_id)
    monkeypatch.setattr(possession, "prefixes",
                        lambda root_, name_, codex: [f"{OWN}/{SEG}/", f"{PRED}/{SEG}/"])
    parts = [_part("lang=en", 1, 1, 100), _part("lang=en", 2, 101, 200), _part("lang=fr", 1, 1000, 1100)]
    current = {f"c:{u}": f"h{u}" for u in list(range(1, 201)) + list(range(1000, 1101))}
    con.executemany("INSERT INTO documents VALUES(?, 1)", [(u,) for u in current])
    for u in (7, 150, 1050):
        current[f"c:{u}"] = f"h{u}-revised"         # changed since the bundles were made
    recs = {(s.slice, s.n): [(u, h) for u, h in current.items()
                             if int(s.first_id) <= int(u[2:]) <= int(s.last_id)] for s in parts}
    monkeypatch.setattr(possession, "part_records", lambda root_, name_, snap, s: recs[(s.slice, s.n)])
    monkeypatch.setattr(possession, "current_snapshot", lambda root_, name_: Snap(parts))

    def bundle(bid, prefix, sid, uids, accepted=True, files=True, producer="k"):
        if files:
            d = bdir / sid / bid
            d.mkdir(parents=True)
            (d / "possession.json").write_text(json.dumps({f"c:{u}": f"h{u}" for u in uids}))
        cov.record(con, bundle_id=bid, shard=f"{prefix}/{SEG}/{sid}", producer=producer,
                   uids=[f"c:{u}" for u in uids], chunks=len(uids), tiers={}, source=cov.FETCHED,
                   accepted_at="2026-11-01T00:00:00Z" if accepted else None)
    bundle("b-en1", OWN, "lang=en/00001", range(1, 101))
    bundle("b-en2", OWN, "lang=en/00002", range(120, 201))
    bundle("b-span", OWN, "lang=en/00002", range(90, 121), producer="other")   # spans en/1 and en/2
    bundle("b-fr", OWN, "lang=fr/00001", range(1000, 1101))
    bundle("b-pred", PRED, "lang=en/00007", range(50, 150))                    # another numbering
    bundle("b-fileless", PRED, "lang=en/00009", range(160, 181), files=False, producer="far")
    bundle("b-unaccepted", OWN, "lang=fr/00001", range(1000, 1010), accepted=False)
    for k in range(5):
        cov.claim(con, bundle_id=f"sha256:{k:064x}", shard=f"{OWN}/{SEG}/lang=en/00001", key=f"peer{k}")
    backend.READ_CACHE.clear()
    possession._memo.clear()
    possession._covered_memo.clear()
    possession._offered_memo.clear()
    return SimpleNamespace(root=root, name=name, con=con, parts=parts, recs=recs, current=current)


def test_each_part_answers_as_the_whole_index(world):
    w = world
    whole = possession.index(w.root, w.name, CODEX, w.con)
    for s in w.parts:
        part = possession.part_possession(w.root, w.name, CODEX, w.con, s.slice,
                                          possession._key_bounds(s.first_id, s.last_id))
        recs = w.recs[(s.slice, s.n)]
        assert possession.covered_uids(part, recs) == possession.covered_uids(whole, recs), s
        for u, h in recs:
            assert part.get(u) == whole.get(u), u
            assert part.bundles.get(u) == whole.bundles.get(u), u
            found = possession.holding(w.root, w.name, CODEX, w.con, u, u[2:])
            assert found is not None and found[1] == h
            assert found[0].get(u) == whole.get(u) and found[0].bundles.get(u) == whole.bundles.get(u)
        mine = {k: [r for r in v if not (contribute._uid_key(r[1]) < contribute._uid_key(f"_:{s.first_id}")
                                         or contribute._uid_key(r[0]) > contribute._uid_key(f"_:{s.last_id}"))]
                for k, v in whole.fileless.items() if k == s.slice}
        assert {k: v for k, v in part.fileless.items() if v} == {k: v for k, v in mine.items() if v}
    assert possession.holding(w.root, w.name, CODEX, w.con, "c:5000", "5000") is None, "in no part"


def test_covered_and_offered_answer_as_before(world):
    """The two passes over every part — the counting view and the digest's
    offer — give what they gave from the whole index."""
    w = world
    whole = possession.index(w.root, w.name, CODEX, w.con)
    snap = Snap(w.parts)
    own_ids = {b for (b,) in w.con.execute("SELECT bundle_id FROM coverage WHERE shard >= ? AND shard < ?",
                                           (f"{OWN}/{SEG}/", f"{OWN}/{SEG}0"))}
    expect_offer = {}
    for s in w.parts:
        ids = {b for u, h in w.recs[(s.slice, s.n)] for b in (whole.bundles.get(u) or {}).get(h, ())
               if b not in own_ids}
        if ids:
            expect_offer[cov.shard_id(CODEX, f"{s.slice}/{s.n:05d}")] = sorted(ids)
    assert possession.offered_by_part(w.root, w.name, CODEX, w.con, snap) == expect_offer
    assert expect_offer, "the fixture offers a predecessor's bundle"
    got = possession.covered(w.root, w.name, CODEX, w.con, snap)
    assert got == {cov.shard_id(CODEX, f"{s.slice}/{s.n:05d}"):
                   len(possession.covered_uids(whole, w.recs[(s.slice, s.n)])) for s in w.parts}
    assert all(got.values()), "every part covered in part"


def test_the_part_query_is_a_range_of_the_shard_index():
    con = cov.ensure_schema(sqlite3.connect(":memory:"))
    plan = " | ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + possession.PART_ROWS_SQL,
                                                ("p/lang=en/", "p/lang=en0")))
    assert "USING INDEX idx_coverage_shard" in plan and "shard>? AND shard<?" in plan, plan
    for bad in ("TEMP B-TREE", "LIST SUBQUERY", "SCAN coverage"):
        assert bad not in plan, plan


def test_the_heap_is_flat_whatever_the_parts(tmp_path, monkeypatch):
    """Eighty parts of 2,000 records, each with its bundle: what the worker
    holds after a pass is the read cache's budget, never the parts' uids
    (the whole index held ~680 B for each)."""
    con = cov.ensure_schema(sqlite3.connect(":memory:"))
    bdir = tmp_path / "bundles"
    monkeypatch.setattr(contribute, "bundle_dir",
                        lambda root_, name_, codex, sid, bundle_id, **kw: bdir / sid / bundle_id)
    monkeypatch.setattr(possession, "prefixes", lambda root_, name_, codex: [f"{OWN}/{SEG}/"])
    parts = [_part(f"year={1900 + n // 10}", n % 10 + 1, n * 2000, n * 2000 + 1999) for n in range(80)]
    for s in parts:
        sid = f"{s.slice}/{s.n:05d}"
        d = bdir / sid / f"b{s.slice}{s.n}"
        d.mkdir(parents=True)
        (d / "possession.json").write_text(json.dumps(
            {f"c:{u}": f"{u:032x}" for u in range(int(s.first_id), int(s.last_id) + 1)}))
        cov.record(con, bundle_id=f"b{s.slice}{s.n}", shard=f"{OWN}/{SEG}/{sid}", producer="k",
                   uids=[f"c:{s.first_id}", f"c:{s.last_id}"], chunks=2000, tiers={}, source=cov.FETCHED,
                   accepted_at="2026-11-01T00:00:00Z")
    monkeypatch.setattr(backend.READ_CACHE, "budget", 2 << 20)
    backend.READ_CACHE.clear()
    held_at: list[int] = []
    tracemalloc.start(1)
    try:
        gc.collect()
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        for k, s in enumerate(parts, 1):
            got = possession.part_possession(tmp_path, "books", CODEX, con, s.slice,
                                             possession._key_bounds(s.first_id, s.last_id))
            assert len(got) == 2000 and got.get(f"c:{s.first_id}") == [f"{int(s.first_id):032x}"]
            del got
            if k in (10, 80):
                gc.collect()
                held_at.append(tracemalloc.get_traced_memory()[0] - base)
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
        backend.READ_CACHE.clear()
    assert held_at[1] - held_at[0] < 1 << 20, f"held after 10 and 80 parts: {held_at}"
    assert held_at[1] < 4 << 20, f"{held_at[1]} bytes held after 80 parts (160,000 uids)"
    assert peak < 12 << 20, f"{peak} bytes at the peak of a pass"


def test_a_part_asked_again_is_not_built_again_until_its_rows_change(world):
    """The embed stage asks once a document, documents come part by part:
    the part's possession is kept by the accepted rows it was made of."""
    w = world
    s = w.parts[1]
    bounds = possession._key_bounds(s.first_id, s.last_id)
    one = possession.part_possession(w.root, w.name, CODEX, w.con, s.slice, bounds)
    assert possession.part_possession(w.root, w.name, CODEX, w.con, s.slice, bounds) is one
    cov.claim(w.con, bundle_id="sha256:" + "e" * 64, shard=f"{OWN}/{SEG}/lang=en/00002", key="peer")
    assert possession.part_possession(w.root, w.name, CODEX, w.con, s.slice, bounds) is one, \
        "a claim is no accepted row"
    d = w.root / "bundles" / "lang=en/00002" / "b-new"
    d.mkdir(parents=True)
    (d / "possession.json").write_text(json.dumps({"c:150": "h150-revised"}))
    cov.record(w.con, bundle_id="b-new", shard=f"{OWN}/{SEG}/lang=en/00002", producer="k",
               uids=["c:150"], chunks=1, tiers={}, source=cov.FETCHED, accepted_at="2026-11-08T00:00:00Z")
    two = possession.part_possession(w.root, w.name, CODEX, w.con, s.slice, bounds)
    assert two is not one and "h150-revised" in two.get("c:150"), "an accepted bundle is a new view"
