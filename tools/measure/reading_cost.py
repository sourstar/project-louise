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
first call of the process) and warm, how many hits stated a year, and
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
    import shutil
    import sqlite3
    from app.plugins.base import RefSyncState
    from app.refs import store as rs, wikisource_readings as wr
    d = Path(tempfile.mkdtemp(prefix="reading-cost-"))
    shutil.copy(tsv, d / wr.FINAL)
    con = rs.connect(d / "refs.db")
    rs.sync_source(con, wr.WikisourceReadingsSource(), RefSyncState(ref_root=d))
    con.row_factory = sqlite3.Row
    return rs.RefStore(con, [wr.WikisourceReadingsSource().schema()])


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
        t0 = time.perf_counter(); years = reading.years(records); cold = time.perf_counter() - t0
        warm = []
        for _ in range(a.runs):
            t0 = time.perf_counter(); reading.years(records); warm.append(time.perf_counter() - t0)
        texts = [h["text"] for h in hits]
        reading.warm(store=store) if store is not None else reading.warm()
        look = []
        changed = 0
        for _ in range(a.runs):
            t0 = time.perf_counter()
            found = reading.lookup(reading.words_of(texts), store) if store is not None else \
                reading.lookup(reading.words_of(texts))
            out = [reading.apply(h["text"], years.get(i // 2, 0), found, reading.RERANK)
                   for i, h in enumerate(hits) if (i // 2) in years]
            look.append(time.perf_counter() - t0)
            changed = sum(1 for (t, used) in out if used)
        print(json.dumps({"collection": name, "records": len(records), "with_year": len(years),
                          "years_cold_ms": round(cold * 1000, 1),
                          "years_warm_ms": round(statistics.median(warm) * 1000, 1),
                          "lookup_apply_ms": round(statistics.median(look) * 1000, 1),
                          "texts": len(texts), "texts_changed": changed,
                          "version": found.version}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
