"""MEASUREMENT (not product code): what the historical reader costs a rerank
at depth 50 on the node's REAL manifests (architecture, 2026-11-18, fix c:
"Time reading.years and reading.lookup on the node against the real
gutenberg and normattiva manifests BEFORE the deploy"; measuring.md §6) —
gutenberg alone since the operator's "Only Gutenberg, normattiva language is
too recent" (2026-11-18).

    python -m tools.measure.reading_cost --collection gutenberg \\
        [--depth 50] [--runs 5] [--readings-tsv wikisource_readings.tsv] [--seed 1]

READ-ONLY on the node: it reads the collections' snapshots and records and
the refs.db; with --readings-tsv it syncs that file into a SCRATCH refs.db
in a temporary directory (never the node's) and times the lookup there.

Per collection, `depth` records sampled from its snapshot's first parts
(seeded), each a hit of its first section's first 600 characters (two
chunks a record, as the reranker sends): `reading.years` timed cold (the
first call of the process) and warm, then the year index built
(`reading.index_years`, the web process's warm) and `reading.years` timed
on it, how many hits stated a year, and
`reading.lookup` + `reading.apply` over the 2 x depth texts. One JSON line
per collection on stdout.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import tempfile
import time
from pathlib import Path


def sample_hits(name: str, depth: int, seed: int) -> list[dict]:
    from app import collections as collections_mod
    from app.ask.results import _root
    from app.hold import collection as col, shard as shard_mod
    from app.pack import records as pack_records
    from app.share import seeder
    c = collections_mod.by_name(name)
    if c is None:
        raise SystemExit(f"no collection {name}")
    tree = col.shard_dir_of(_root(), name)
    snap = seeder.cached_snapshot(tree)
    if snap is None:
        raise SystemExit(f"{name}: no snapshot held")
    rng = random.Random(seed)
    shards = list(snap.shards)
    rng.shuffle(shards)
    hits: list[dict] = []
    for s in shards:
        path = tree / shard_mod.shard_path(s.slice, s.n)
        if not path.is_file():
            continue
        recs = list(pack_records.iter_records(path))
        rng.shuffle(recs)
        for r in recs:
            secs = r.get("sections") or []
            text = " ".join(str(t) for _h, t in secs[:2])[:1200]
            for k in range(2):
                hits.append({"chunk_id": f"{r['uid']}:{k}:1", "uid": r["uid"], "source_collection": name,
                             "lang": r.get("lang"), "text": text[k * 600:(k + 1) * 600]})
            if len(hits) >= 2 * depth:
                return hits
    return hits


def scratch_store(tsv: Path):
    """The shared scratch tables, synced by the product's own sync (tools/measure/reading_store.py)."""
    from tools.measure import reading_store
    return reading_store.scratch(tsv)

def main(argv: list[str] | None = None) -> int:
    from app import reading
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--collection", action="append", required=True)
    ap.add_argument("--depth", type=int, default=50)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--readings-tsv", type=Path)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    store = scratch_store(a.readings_tsv) if a.readings_tsv else None
    for name in a.collection:
        hits = sample_hits(name, a.depth, a.seed)
        records = hits[::2]                                   # reading.years is per record
        # the modules a web process's own search has imported before any rerank:
        # imported here first, their cost reported apart (München's unexplained
        # ~0.8 s cold, 2026-11-18, was these first imports)
        t0 = time.perf_counter()
        import app.retrieval, app.ask.works, app.ask.figures, app.ask.results  # noqa: E401,F401
        import app.share.seeder, app.hold.collection, app.pack.records  # noqa: E401,F401
        t_imports = time.perf_counter() - t0
        # the cold call's parts: the collection files and the snapshot, which a
        # web process's own search has already loaded, then the reader's batch
        t0 = time.perf_counter(); col_obj = reading._collection(name); t_col = time.perf_counter() - t0
        t0 = time.perf_counter(); col_obj is not None and reading._snapshot_of(col_obj); t_snap = time.perf_counter() - t0
        t0 = time.perf_counter(); years = reading.years(records); cold = time.perf_counter() - t0
        warm = []
        for _ in range(a.runs):
            t0 = time.perf_counter(); reading.years(records); warm.append(time.perf_counter() - t0)
        # the year index, as the web process's warm builds it at start (off the query path)
        from app import reading as rd
        t0 = time.perf_counter(); idx = rd.index_years(rd._collection(name)); index_s = time.perf_counter() - t0
        indexed = []
        for _ in range(a.runs):
            t0 = time.perf_counter(); rd.years(records); indexed.append(time.perf_counter() - t0)
        texts = [h["text"] for h in hits]
        reading.warm(store=store) if store is not None else reading.warm()
        look, sql, app_ = [], [], []
        changed = 0
        first_lookup = None
        for run in range(a.runs):
            if run == 0:
                reading._word_cache.clear(); reading._word_cache_version["v"] = ""
            t0 = time.perf_counter()
            found = reading.lookup(reading.words_of(texts), store) if store is not None else \
                reading.lookup(reading.words_of(texts))
            t1 = time.perf_counter()
            out = [reading.apply(h["text"], years.get(i // 2, 0), found, reading.RERANK)
                   for i, h in enumerate(hits) if (i // 2) in years]
            t2 = time.perf_counter()
            if run == 0:
                first_lookup = t1 - t0                   # the word cache empty: what a new query's words cost
            sql.append(t1 - t0); app_.append(t2 - t1); look.append(t2 - t0)
            changed = sum(1 for (t, used) in out if used)
        print(json.dumps({"collection": name, "records": len(records), "with_year": len(years),
                          "cold_imports_ms": round(t_imports * 1000, 1),
                          "cold_collection_ms": round(t_col * 1000, 1),
                          "cold_snapshot_ms": round(t_snap * 1000, 1),
                          "years_cold_ms": round(cold * 1000, 1),
                          "years_warm_ms": round(statistics.median(warm) * 1000, 1),
                          "index_build_s": round(index_s, 1),
                          "index_complete": bool(idx and idx["complete"]),
                          "index_records_with_year": len([v for v in (idx or {}).get("years", {}).values() if v]),
                          "years_indexed_ms": round(statistics.median(indexed) * 1000, 2),
                          "lookup_apply_ms": round(statistics.median(look) * 1000, 1),
                          "lookup_uncached_ms": round((first_lookup or 0) * 1000, 1),
                          "lookup_cached_ms": round(statistics.median(sql[1:] or sql) * 1000, 2),
                          "apply_ms": round(statistics.median(app_) * 1000, 2),
                          "texts": len(texts), "texts_changed": changed,
                          "version": found.version}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
