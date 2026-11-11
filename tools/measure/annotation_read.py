"""MEASUREMENT (not product code): the cost of attaching annotations to N
hits (app/ask/annotations.attach, 05 §5.3), BEFORE and AFTER the 2026-11-11
amendment (02 §2.10: a claim applies to every held collection holding its
record, decided per claim by hash).

  before — the rule as it was: an annotations collection is consulted only
           where its `annotates` names the result's collection (or its carry
           twin), from `from_snapshot` on;
  after  — the code as it is: also any held collection of a plugin its
           claims name (`ClaimIndex.plugins`), each claim then judged by the
           same content-hash comparison.

Both run in one process on the same hits: `before` by replacing
`annotations.applicable` / `annotations.is_about` with the old rule for its
runs. The first call of each (cold: the claim index loaded or memory-mapped)
is reported apart from the warm median and p95 of `--repeat` calls.

On the node (read-only; wikipedia-articles holds 12.3 M claims, read through
its disk index, app/hold/claim_index.py):

    docker compose exec louise python tools/measure/annotation_read.py \\
        live wikipedia-articles --n 10 50 200 --repeat 20

Here, on a synthetic pair of sisters (a target and a collection holding the
same records under another space; `--disk` reads the claims through the disk
index, as the node does for a large snapshot). The synthetic mode builds its
collections with the test suite's fixtures (tests/hold), so it runs from the
repository root:

    python tools/measure/annotation_read.py synthetic --records 20000 \\
        --claims 20000 --n 10 50 200 --repeat 20 [--disk]

Prints one JSON object per (collection, n).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ask import annotations as facts          # noqa: E402
from app.hold import annotations as ann           # noqa: E402


def _before_applicable(root, usable, ids, plugins, read_snapshot):
    return [a for a in usable if (t := facts.target_among(a.codex, ids)) is not None
            and ann.applies_from(t.from_snapshot, read_snapshot)]


def _before_is_about(root, a, ids, plugins):
    from app.hold.codex import targets_of
    ids = set(ids)
    return any(t.collection_id in ids for t in targets_of(a.codex))


@contextmanager
def rule(which: str):
    if which == "after":
        yield
        return
    saved = facts.applicable, facts.is_about
    facts.applicable, facts.is_about = _before_applicable, _before_is_about
    try:
        yield
    finally:
        facts.applicable, facts.is_about = saved


def _hits(uids):
    return [{"uid": u, "rank": i, "text": "…"} for i, u in enumerate(uids, 1)]


def time_attach(coll, ctx, uids, root, held, repeat: int) -> dict:
    out = {}
    for which in ("before", "after"):
        times, attached = [], 0
        with rule(which):
            for i in range(repeat + 1):
                hs = _hits(uids)
                t0 = time.perf_counter()
                facts.attach([{"hits": hs}], {id(h): coll for h in hs}, {coll.name: ctx},
                             root=root, collections=held)
                times.append(time.perf_counter() - t0)
                attached = sum(len(h["annotations"]) for h in hs)
        warm = sorted(times[1:]) or times
        out[which] = {"cold_ms": round(times[0] * 1e3, 3),
                      "warm_median_ms": round(statistics.median(warm) * 1e3, 3),
                      "warm_p95_ms": round(warm[min(len(warm) - 1, int(0.95 * len(warm)))] * 1e3, 3),
                      "claims_attached": attached}
    return out


# ---- live, on the node ------------------------------------------------------

def _sample_uids(coll, n: int, seed: int) -> list[str]:
    """n uids of the manifest, by rowid windows from random starts: never a
    full scan (CLAUDE.md, hot-path queries walk from a cursor)."""
    from contextlib import closing
    from app import db
    rnd = random.Random(seed)
    out: list[str] = []
    with closing(db.connect(coll)) as con:
        top = con.execute("SELECT max(rowid) FROM documents").fetchone()[0] or 0
        tries = 0
        while len(out) < n and top and tries < 50 * n:
            tries += 1
            r = con.execute("SELECT uid FROM documents WHERE rowid >= ? ORDER BY rowid LIMIT 1",
                            (rnd.randint(1, top),)).fetchone()
            if r and r[0] not in out:
                out.append(r[0])
    return out


def live(args) -> None:
    from app import collections as collections_mod
    from app.ask import results
    coll = collections_mod.by_name(args.collection)
    if coll is None:
        sys.exit(f"{args.collection}: not a collection this node serves")
    root = results._root()
    held = collections_mod.load_annotations()
    ctx = results.context_for(coll)
    for n in args.n:
        uids = _sample_uids(coll, n, args.seed)
        print(json.dumps({"mode": "live", "collection": coll.name, "n": len(uids),
                          "snapshot": ctx.get("snapshot_id"),
                          **time_attach(coll, ctx, uids, root, held, args.repeat)}), flush=True)


# ---- synthetic, here ----------------------------------------------------------

def synthetic(args) -> None:
    import dataclasses
    from types import SimpleNamespace
    from app import collections as collections_mod, config, db
    from app.hold import claim_index, collection as col
    from tests.hold.conftest import pubmed
    from tests.hold.test_annotations import TARGET_SNAPSHOT, a_claim, a_target, claims_file, make
    rnd = random.Random(args.seed)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config.CACHE_DIR = str(root / "fast")
        texts = tuple(f"record {i}" for i in range(1, args.records + 1))
        target, hashes = a_target(root, texts=texts)
        sister, _ = a_target(root, name="pubmed-nemo", texts=texts,
                             space=dataclasses.replace(pubmed().space, tolerance="0.96"))
        pairs = set()
        while len(pairs) < args.claims:
            a, b = rnd.sample(range(1, args.records + 1), 2)
            pairs.add((min(a, b), max(a, b)))
        codex, _ = make(root, claims=claims_file(*(a_claim(f"pubmed:{a}", f"pubmed:{b}") for a, b in sorted(pairs))))
        if args.disk:
            claim_index.LARGE = 0                       # read through the disk index, as the node's large one
            claim_index.build(col.shard_dir_of(root, "pubmed-same-work"))
            ann._indexes.clear()
        held = [SimpleNamespace(name="pubmed-same-work", codex=codex, plugin="annotations", license_ack=None,
                                manifest_path=root / "pubmed-same-work" / "state" / "manifest.sqlite")]
        colls = []
        for name, c in (("pubmed", target), ("pubmed-nemo", sister)):
            coll = collections_mod.Collection(
                name=name, plugin="pubmed", profile=None, embed_mode_default=None,
                corpus_dir=root / name / "shard", manifest_path=root / name / "state" / "manifest.sqlite",
                fts_dir=root / name / "index", lance_dir=root / name / "lance", codex=c)
            with db.connect(coll) as con:
                con.executemany(
                    "INSERT INTO documents(uid, source, native_id, path, title, lang, content_hash) "
                    "VALUES(?,?,?,?,?,?,?)",
                    [(u, "pubmed", u.split(":")[1], f"x/{u}", u, "en", h) for u, h in hashes.items()])
            colls.append(coll)
        for n in args.n:
            uids = [f"pubmed:{i}" for i in rnd.sample(range(1, args.records + 1), min(n, args.records))]
            for coll in colls:
                ctx = {"collection_id": coll.codex.collection_id, "snapshot_id": TARGET_SNAPSHOT}
                print(json.dumps({"mode": "synthetic", "disk_index": bool(args.disk), "records": args.records,
                                  "claims": args.claims, "collection": coll.name, "n": len(uids),
                                  **time_attach(coll, ctx, uids, root, held, args.repeat)}), flush=True)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="mode", required=True)
    lv = sub.add_parser("live")
    lv.add_argument("collection")
    sy = sub.add_parser("synthetic")
    sy.add_argument("--records", type=int, default=20_000)
    sy.add_argument("--claims", type=int, default=20_000)
    sy.add_argument("--disk", action="store_true")
    for s in (lv, sy):
        s.add_argument("--n", type=int, nargs="+", default=[10, 50, 200])
        s.add_argument("--repeat", type=int, default=20)
        s.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    (live if args.mode == "live" else synthetic)(args)


if __name__ == "__main__":
    main()
