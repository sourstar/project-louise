"""The legal chunker's bounded encode against the whole one, on a
collection's REAL documents (2026-11-20; app/chunking_legal.py `_spans`):
the worker's own `chunks` for each document, once read in spans (as
deployed) and once in one encode (SPAN_CHARS past any text: as until
2026-11-20), compared chunk by chunk — id, chapter, text.

    python -m tools.measure.legal_chunk_identity <collection> [--uids FILE] [--largest N] [--every N] [--limit N]

`--uids`: one uid a line; `--largest N`: the N largest documents by size;
`--every N`: one document in N by rowid. Read-only. RUN IT IN A CONTAINER
OF ITS OWN with a memory cap: the whole encode is the one that held 11.18 GB
for 32018R0688 el. One JSON line a document, then the totals; exit 1 when
any document differs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _chunks(collection, row: dict, span_chars: int) -> tuple[list[tuple], float]:
    from app import chunking_legal as cl
    from app import parallel
    keep = cl.SPAN_CHARS
    cl.SPAN_CHARS = span_chars
    try:
        t0 = time.monotonic()
        (ok, value), = parallel._compute("chunks", collection, [dict(row)])
        took = time.monotonic() - t0
    finally:
        cl.SPAN_CHARS = keep
    if not ok:
        return [("error", value.get("type"), value.get("msg"))], took
    return [(c.get("id"), c.get("chapter"), c.get("text")) for c in value], took


def compare(collection, row: dict) -> dict:
    from app import chunking_legal as cl
    spans, t_spans = _chunks(collection, row, cl.SPAN_CHARS)
    whole, t_whole = _chunks(collection, row, 10 ** 15)
    out = {"uid": row["uid"], "size": row.get("size"), "chunks": len(spans),
           "characters": sum(len(c[2] or "") for c in spans if c[0] != "error"),
           "identical": spans == whole, "seconds_spans": round(t_spans, 2), "seconds_whole": round(t_whole, 2)}
    if spans != whole:
        first = next((i for i, (a, b) in enumerate(zip(spans, whole)) if a != b), min(len(spans), len(whole)))
        out.update({"first_difference": first, "chunks_whole": len(whole)})
    return out


def _rows(con, a) -> list[dict]:
    if a.uids:
        uids = [u.strip() for u in Path(a.uids).read_text(encoding="utf-8").splitlines() if u.strip()]
        rows = [con.execute("SELECT * FROM documents WHERE uid=?", (u,)).fetchone() for u in uids]
        return [dict(r) for r in rows if r is not None]
    if a.largest:
        return [dict(r) for r in con.execute("SELECT * FROM documents WHERE error IS NULL "
                                             "ORDER BY size DESC LIMIT ?", (a.largest,))]
    return [dict(r) for r in con.execute("SELECT * FROM documents WHERE error IS NULL AND rowid % ? = 0 "
                                         "ORDER BY rowid LIMIT ?", (max(1, a.every), a.limit or -1))]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="legal_chunk_identity", description=__doc__.split("\n")[0])
    p.add_argument("collection")
    p.add_argument("--uids", default=None)
    p.add_argument("--largest", type=int, default=None)
    p.add_argument("--every", type=int, default=1000)
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args(argv)
    from app import collections as collections_mod
    from app import db
    coll = collections_mod.by_name(a.collection)
    if coll is None:
        print(f"legal_chunk_identity: no collection {a.collection!r}", file=sys.stderr)
        return 2
    con = db.connect(coll)
    try:
        rows = _rows(con, a)
    finally:
        con.close()
    differ = 0
    for row in rows:
        got = compare(coll, row)
        differ += not got["identical"]
        print(json.dumps(got), flush=True)
    print(json.dumps({"collection": a.collection, "documents": len(rows), "differ": differ}))
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
