"""Where the seconds of "one hit per work" go for ONE collection's candidates:
the claims walked, the frames read for them, the collection's own relations,
the members' rows — with no model asked and nothing written.

    python -m tools.measure.works_walk --collection wikipedia --query "…"
                                       [--k 50] [--times 2] [--out FILE]
    (from /srv, the image's tree, in a container of the WEB's recipe: its image,
     env and volumes — never inside the web's own cgroup)

WHY (München, 2026-11-26, the deploy of 8c92a5f0): a question's search line
said "rerank by list: wikipedia 10,309 (50 records, …: answers 1,016, works
9,229 [claims 8,837, facts 212, passages 180 for 6 edition(s)])" — nine tenths
of that list's time is `works.work_groups`, with the claim index's seek table
in place. "claims" is three things, and the line cannot say which:
  - THE WALK (`works._walk`): each candidate's work, whole — every claim
    naming it, then every claim naming each record those name. A lookup
    finds its rows in a memory-mapped array and then reads, for EACH claim,
    the zstd frame of ~1,000 claims its line sits in (app/hold/
    claim_index.py): a work of 100 language editions whose claims lie in 100
    frames is 100 frames decompressed for 100 lines;
  - THE COLLECTION'S OWN RELATIONS (`works._Edges`): two indexed queries a
    step over every record reached;
  - THE MEMBERS' ROWS (`annotations._target_rows`): title and language of
    every record of every work found, from the manifest.

WHAT IT RUNS. The text leg's first `k` records for the query (`retrieval.
federated_search`, mode "fts", the reranker replaced by "kept the fused
order", the grouping left out) — the candidates a real question groups —
then `works.work_groups` over them, `--times` times: the first is the cold
one. Around it, counters: lookups and the claims they returned; frames read
(a frame not in the index's cache: one pread and one decompress), their
compressed and decompressed bytes and their seconds; distinct frames; the
relations' and the rows' queries and seconds; the works found and their
members.

WHAT IT LEAVES OUT, and says so: the vector leg, the reranker and the query's
translation (models: a question takes the card from the corpus); the search's
two writes. The candidates are therefore the TEXT leg's, not the fused
list's: the same kind of records, not the same fifty.

Prints one JSON object (and writes it to `--out`). Nothing else is written.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any


class _Clock:
    """Calls, seconds and whatever a wrapped function adds, by name."""

    def __init__(self) -> None:
        self.n: dict[str, int] = {}
        self.s: dict[str, float] = {}
        self.more: dict[str, int] = {}

    def wrap(self, name: str, fn, after=None):
        def timed(*a, **kw):
            t0 = time.monotonic()
            try:
                got = fn(*a, **kw)
            finally:
                self.n[name] = self.n.get(name, 0) + 1
                self.s[name] = self.s.get(name, 0.0) + (time.monotonic() - t0)
            if after is not None:
                after(self, got, *a, **kw)
            return got
        return timed

    def add(self, key: str, n: int) -> None:
        self.more[key] = self.more.get(key, 0) + int(n)

    def told(self) -> dict[str, Any]:
        return {"calls": dict(self.n), "seconds": {k: round(v, 3) for k, v in self.s.items()},
                **{k: v for k, v in self.more.items()}}


def candidates(collection, query: str, k: int) -> list[str]:
    """The text leg's first `k` records for `query`, ungrouped."""
    from app import retrieval
    kept = (retrieval._reranked, retrieval._one_hit_per_work, retrieval.verify_on_use.cite,
            retrieval._enqueue_lazy_embed)
    retrieval._reranked = lambda query, hits, *a, **kw: (hits, False, "measurement: no reranker asked")
    retrieval._one_hit_per_work = lambda hits, *a, **kw: hits
    retrieval.verify_on_use.cite = lambda *a, **kw: None
    retrieval._enqueue_lazy_embed = lambda *a, **kw: None
    try:
        got = retrieval.federated_search([collection], query, k=k, mode="fts", translate=False)
    finally:
        (retrieval._reranked, retrieval._one_hit_per_work, retrieval.verify_on_use.cite,
         retrieval._enqueue_lazy_embed) = kept
    return list(dict.fromkeys(retrieval.record_of(h) for h in got.get("hits") or []))


def walk_once(collection, uids: list[str], *, root=None, annotations=None) -> dict[str, Any]:
    """`works.work_groups` over `uids`, with its parts counted. `root` and
    `annotations` are `work_groups`' own (None: the node's, every one held)."""
    from app.ask import annotations as ask_annotations, works
    from app.hold import claim_index
    from app.pack import records as pack_records
    clock = _Clock()
    frames: set[tuple[str, int]] = set()

    def read_at(c, lines, path, offset, csize, rsize, n, k=None):
        c.add("frame_compressed_bytes", csize)
        c.add("frame_bytes", rsize)
        c.add("frame_lines", n)
        frames.add((str(path), int(offset)))

    def read_by_index(c, lines, path, index, k):
        c.add("frame_bytes", sum(len(x) for x in lines))
        c.add("frame_lines", len(lines))
        frames.add((str(path), int(k)))

    def looked(c, claims, self, uid, kind):
        c.add("claims_returned", len(claims))

    def rows(c, got, collection, uids):
        c.add("rows_asked", len(set(uids)))
        c.add("rows_held", len(got))

    def edges(c, got, self, uids):
        c.add("relation_uids", len(set(uids)))
        c.add("relation_edges", len(got))

    kept = (pack_records.read_frame_at, pack_records.read_frame, claim_index.DiskClaimIndex.claims_of,
            claim_index.DiskClaimIndex._lines, ask_annotations._target_rows, works._Edges.__call__, works._walk)
    pack_records.read_frame_at = clock.wrap("frame_read", kept[0], read_at)
    pack_records.read_frame = clock.wrap("frame_read", kept[1], read_by_index)
    claim_index.DiskClaimIndex.claims_of = clock.wrap("lookup", kept[2], looked)
    claim_index.DiskClaimIndex._lines = clock.wrap("frame_asked", kept[3])
    ask_annotations._target_rows = clock.wrap("rows", kept[4], rows)
    works._Edges.__call__ = clock.wrap("relations", kept[5], edges)
    works._walk = clock.wrap("walk", kept[6])
    t0 = time.monotonic()
    try:
        try:
            work_of, members = works.work_groups(collection, uids, root=root, annotations=annotations)
            error = None
        except Exception as e:   # noqa: BLE001 — said: the parts counted so far still are
            work_of, members, error = {}, {}, f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        (pack_records.read_frame_at, pack_records.read_frame, claim_index.DiskClaimIndex.claims_of,
         claim_index.DiskClaimIndex._lines, ask_annotations._target_rows, works._Edges.__call__,
         works._walk) = kept
    told = clock.told()
    sizes = sorted((len(v) for v in members.values()), reverse=True)
    return {"seconds": round(time.monotonic() - t0, 3), "error": error,
            "candidates": len(uids), "candidates_in_a_work": sum(1 for u in uids if u in work_of),
            "works": len(members), "members": sum(sizes), "largest_works": sizes[:5],
            "distinct_frames_read": len(frames),
            # a frame asked that was not read was in the index's cache
            "frames_from_cache": told["calls"].get("frame_asked", 0) - told["calls"].get("frame_read", 0),
            **told}


def measure(name: str, query: str, *, k: int = 50, times: int = 2, root=None, annotations=None) -> dict[str, Any]:
    from app import collections as collections_mod
    collection = collections_mod.by_name(name)
    if collection is None:
        raise SystemExit(f"{name}: no collection of that name is held here")
    out: dict[str, Any] = {"collection": name, "query": query, "k": k,
                           "left_out": ["the vector leg and the reranker (models)",
                                        "the query's translation (a model)",
                                        "the citations queue and the lazy-embed queue (writes)"]}
    t0 = time.monotonic()
    uids = candidates(collection, query, k)
    out["candidates_seconds"] = round(time.monotonic() - t0, 3)
    out["candidates"] = len(uids)
    out["runs"] = [walk_once(collection, uids, root=root, annotations=annotations) for _ in range(max(1, times))]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--collection", required=True, help="the collection's local name")
    ap.add_argument("--query", required=True)
    ap.add_argument("--k", type=int, default=50, help="how many records the rerank's list holds (50)")
    ap.add_argument("--times", type=int, default=2, help="the walk this many times: the first is the cold one")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):    # the app's own lines: stdout carries the JSON alone
        got = measure(a.collection, a.query, k=a.k, times=a.times)
    text = json.dumps(got, indent=1, ensure_ascii=False)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
