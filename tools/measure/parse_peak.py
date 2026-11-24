"""The peak memory of ONE document's parse, kind by kind, each kind in a
fresh process (2026-11-20, the second OOM hold: EUR-Lex5's
eurlex:6661562791694272526, 32018R0688 el, 128 MB — the killed child held
11.16 GB, 87x its bytes and 3.4x the 26x estimate; 26x was measured on
32021R2017 el's `chunks` alone, 5.25 GB for 204 MB).

    python -m tools.measure.parse_peak <collection> <uid> [--kinds metadata,chunks,fts]

RUN IT IN A CONTAINER OF ITS OWN with a memory cap (deploy/supervise-pass.sh
`--memory`), never inside the worker's cgroup: the parse it measures is the
one that was killed there. Read-only: nothing is written.

Per kind, the worker's own computation (`parallel._compute`, what a pool
child runs): its seconds, the process's VmHWM once the plugin and, for
`fts`, the reference store are loaded (what every child keeps), its VmHWM
after the parse, the rise, the rise per byte of the row's `size` (the
plugin's PARSE_PEAK_PER_BYTE beside it), and what it made (chunks,
characters). `fts` is `chunks` plus the entity tagging of every chunk's
text (`entities.entity_ids_of`), so the two rises tell the chunker from the
tagging. A kind whose process the kernel killed says so, with its exit.
One JSON object.

THE HARNESS THAT EXISTS beside it: tools/measure/check_rss.py measures a
whole pass's process tree; this measures one document, by kind, which a
pass's tree cannot separate.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KINDS = ("metadata", "chunks", "fts")


def _hwm() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def measure_one(collection, row: dict, kind: str) -> dict:
    """One kind of `row`'s parse in THIS process: what it held and made."""
    from app import parallel
    collection.plugin_instance()
    if kind.startswith("fts"):
        from app import refs
        refs.read_store()
    before = _hwm()
    t0 = time.monotonic()
    (ok, value), = parallel._compute(kind, collection, [dict(row)])
    seconds = time.monotonic() - t0
    after = _hwm()
    out = {"kind": kind, "ok": ok, "seconds": round(seconds, 2), "hwm_before": before, "hwm_after": after}
    if before is not None and after is not None:
        size = int(row.get("size") or 0)
        out["rise"] = after - before
        out["rise_per_byte"] = round((after - before) / size, 1) if size else None
    if not ok:
        out["error"] = value
        return out
    chunks = value.get("chunks") if isinstance(value, dict) else value if isinstance(value, list) else None
    if isinstance(chunks, list):
        out["chunks"] = len(chunks)
        out["characters"] = sum(len(c.get("text") or "") for c in chunks if isinstance(c, dict))
    return out


def _child(name: str, uid: str, kind: str, q) -> None:
    from app import collections as collections_mod
    from app import db
    coll = collections_mod.by_name(name)
    if coll is None:
        q.put({"kind": kind, "error": f"no collection {name!r}"})
        return
    con = db.connect(coll)
    try:
        row = con.execute("SELECT * FROM documents WHERE uid=?", (uid,)).fetchone()
    finally:
        con.close()
    if row is None:
        q.put({"kind": kind, "error": f"no document {uid!r} in {name}"})
        return
    q.put(measure_one(coll, dict(row), kind))


def run(name: str, uid: str, kinds: list[str], *, target=None) -> dict:
    ctx = multiprocessing.get_context("spawn")
    got = []
    for kind in kinds:
        q = ctx.Queue()
        p = ctx.Process(target=target or _child, args=(name, uid, kind, q))
        p.start()
        p.join()
        got.append(q.get() if not q.empty() else
                   {"kind": kind, "killed": True, "exitcode": p.exitcode,
                    "note": "the process ended without an answer (-9: the kernel's kill)"})
    out = {"collection": name, "uid": uid, "kinds": got}
    try:
        from app import collections as collections_mod
        from app import parallel
        coll = collections_mod.by_name(name)
        out["peak_per_byte_declared"] = parallel.peak_per_byte(coll) if coll is not None else None
    except Exception as e:   # noqa: BLE001 — a report, not a pass
        out["peak_per_byte_declared"] = f"unread: {type(e).__name__}"
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="parse_peak", description=__doc__.split("\n")[0])
    p.add_argument("collection")
    p.add_argument("uid")
    p.add_argument("--kinds", default=",".join(KINDS))
    a = p.parse_args(argv)
    print(json.dumps(run(a.collection, a.uid, [k for k in a.kinds.split(",") if k]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
