"""MEASUREMENT (not product code): search latency while the reader's year
index is built (architecture, 2026-11-18: "measure search latency DURING
it ... If p95 rises, move the build"). The build now runs in a spawned
child (`reading.INDEX_IN_CHILD`); `--in-thread` measures the old way too.

    python -m tools.measure.reading_index_contention --collection <gutenberg's> \\
        --set known_items.jsonl [--n 40] [--in-thread]

READ-ONLY on the node's manifest; the facts store is pointed at an EMPTY
temporary directory (LOUISE_CACHE_DIR), so the build reads every part's
records — the first build after a FACTS_VERSION change, the worst case.
Phase 1: the queries searched one after another (k 10, no translation),
nothing else running. Phase 2: the same while the index builds. One JSON
line: p50 and p95 of each phase, searches in each, the build's seconds.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import threading
import time
from pathlib import Path


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 1) if xs else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--collection", required=True)
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--in-thread", action="store_true", help="build in the web thread, as before the child")
    a = ap.parse_args(argv)
    os.environ["LOUISE_CACHE_DIR"] = tempfile.mkdtemp(prefix="reading-contention-")
    from app import collections as collections_mod, reading, retrieval
    from tools.measure.reading_summary_eval import ro_connect
    queries = [q["query"] for q in (json.loads(x) for x in a.set.read_text(encoding="utf-8").splitlines() if x.strip())
               if "qid" in q and not q.get("refused")][: a.n] or ["chi era il mercante"]
    col = collections_mod.by_name(a.collection)
    con = ro_connect(col)

    def one(q: str) -> float:
        t0 = time.perf_counter()
        retrieval.search(con, q, k=10, mode="hybrid", translate=False, collection=col)
        return time.perf_counter() - t0
    for q in queries[:3]:
        one(q)                                                   # warm the search's own caches
    base = [one(q) for q in queries]
    reading.INDEX_IN_CHILD = not a.in_thread
    reading._year_index.clear()
    t_build = {"s": None}

    def build() -> None:
        t0 = time.perf_counter()
        reading._index_off_process(col, 1800)
        t_build["s"] = time.perf_counter() - t0
    th = threading.Thread(target=build, daemon=True)
    th.start()
    during, k = [], 0
    while th.is_alive() and k < 10 * len(queries):
        during.append(one(queries[k % len(queries)]))
        k += 1
    th.join()
    print(json.dumps({"collection": a.collection, "in_child": not a.in_thread,
                      "baseline": {"n": len(base), "p50_ms": pct(base, 0.5), "p95_ms": pct(base, 0.95)},
                      "during_build": {"n": len(during), "p50_ms": pct(during, 0.5), "p95_ms": pct(during, 0.95)},
                      "build_s": round(t_build["s"] or 0, 1),
                      "median_ratio": round(statistics.median(during) / statistics.median(base), 2)
                      if during and base else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
