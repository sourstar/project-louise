"""MEASUREMENT (not product code): the reranker's copy, ON against OFF, on
the known-item set (architecture, 2026-11-18: "reranked MRR@10, reranker
copy ON against OFF … the number the operator's ON default was promised";
the rule stated before the run, docs/notes/historical-languages.md: ON stays
within 0.02, a larger loss to the operator the same day).

    python -m tools.measure.reading_known_item_mrr --collection <gutenberg's> \\
        --set known_items.jsonl --out mrr.jsonl [--depth 50] [--permutations 10000] [--seed 1]

READ-ONLY. Each kept query is searched on the product's own path
(`retrieval.search`, k 10, the depth given, under Quality, no translation —
the queries are Italian and the targets Italian), once with
`retrieval.READING_RERANK` True and once False, the two runs back to back.
The target is the query's record; its reciprocal rank is 1/rank among the
10 shown — a hit is the target where its record, or an edition grouped
under it, is the target — and 0 if absent. One JSON line per query, then
the summary: MRR@10 on and off, the paired difference (sign-flip
permutation p, 95 % bootstrap interval), a row per century (each with its
n, the 1400s their own), and how many queries had a hit the copy moved.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

RULE = "ON stays if MRR@10(on) >= MRR@10(off) - 0.02; a larger loss to the operator the same day"


def rank_of(hits: list[dict], target: str) -> int | None:
    from app.retrieval import record_of
    for k, h in enumerate(hits[:10], 1):
        uids = {record_of(h)} if h.get("chunk_id") else {str(h.get("uid"))}
        uids |= {str(e.get("uid")) for e in (h.get("editions") or []) if isinstance(e, dict)}
        if target in uids:
            return k
    return None


def search(con, col, query: str, depth: int, on: bool) -> list[dict]:
    from app import reading, retrieval
    keep = retrieval.READING_RERANK
    retrieval.READING_RERANK = on
    try:
        with reading.quality(True):
            res = retrieval.search(con, query, k=10, mode="hybrid", translate=False, collection=col,
                                   rerank_depth=depth)
    finally:
        retrieval.READING_RERANK = keep
    return list(res.get("hits") or [])


def main(argv: list[str] | None = None) -> int:
    from app import collections as collections_mod
    from tools.measure.reading_summary_eval import paired_stats, ro_connect
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--collection", required=True)
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--depth", type=int, default=50)
    ap.add_argument("--permutations", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--readings-tsv", type=Path, help="the derived pairs file: a scratch table synced from it "
                                                      "by the product's own sync, before the node's")
    a = ap.parse_args(argv)
    from tools.measure import reading_store
    version = reading_store.use(a.readings_tsv)
    if not version:
        return reading_store.refuse("no reading table is held: copy ON would read nothing and be compared with "
                                    "OFF. Sync wikisource-readings, or pass --readings-tsv")
    lines = [json.loads(x) for x in a.set.read_text(encoding="utf-8").splitlines() if x.strip()]
    queries = [q for q in lines if "qid" in q and not q.get("refused")]
    col = collections_mod.by_name(a.collection)
    con = ro_connect(col)
    rows = []
    with open(a.out, "w", encoding="utf-8") as out:
        out.write(json.dumps({"set": a.set.name, "set_header": lines[0] if lines and "qid" not in lines[0] else None,
                              "depth": a.depth, "seed": a.seed, "rule": RULE, "reading_tables": version,
                              "tables_from": str(a.readings_tsv) if a.readings_tsv else "the node's refs.db"},
                             ensure_ascii=False) + "\n")
        for q in queries:
            on_hits, off_hits = search(con, col, q["query"], a.depth, True), search(con, col, q["query"], a.depth, False)
            r_on, r_off = rank_of(on_hits, q["uid"]), rank_of(off_hits, q["uid"])
            moved = any(m.get("read") for h in on_hits for m in (h.get("moved_by") or [])
                        if str(m.get("weight", "")).startswith("historical reading"))
            row = {"qid": q["qid"], "stratum": q.get("stratum"), "year": q.get("year"), "uid": q["uid"],
                   "query": q["query"], "rank_on": r_on, "rank_off": r_off,
                   "rr_on": 1 / r_on if r_on else 0.0, "rr_off": 1 / r_off if r_off else 0.0, "copy_moved": moved}
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()

        def block(rs: list[dict]) -> dict:
            on = [r["rr_on"] for r in rs]
            off = [r["rr_off"] for r in rs]
            return {"n": len(rs), "mrr_on": round(statistics.mean(on), 4) if on else None,
                    "mrr_off": round(statistics.mean(off), 4) if off else None,
                    "on_minus_off": paired_stats([x - y for x, y in zip(on, off)], a.permutations, a.seed),
                    "queries_with_a_moved_hit": sum(1 for r in rs if r["copy_moved"])}
        by_century: dict[str, list[dict]] = {}
        for r in rows:
            c = (int(r["year"]) // 100) * 100 if r.get("year") is not None else None
            by_century.setdefault(f"{c}s" if c is not None else "unknown", []).append(r)
        overall = block(rows)
        loss = (overall["mrr_off"] or 0) - (overall["mrr_on"] or 0)
        summary = {**overall, "by_century": {k: block(v) for k, v in sorted(by_century.items())},
                   "loss_over_0_02": loss > 0.02, "rule": RULE}
        summary["reading_tables"] = version
        out.write(json.dumps({"summary": summary}, ensure_ascii=False) + "\n")
    print(json.dumps(summary), file=sys.stderr)
    if not overall["queries_with_a_moved_hit"]:
        return reading_store.refuse("zero queries had a hit whose copy changed: the run measured nothing "
                                    "(the rows are written, the number is not a result)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
