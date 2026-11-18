"""`louise hold annotations claims <collection> --out FILE` — the claims an
annotations collection's latest snapshot holds, one per line, each as its
record holds it: pinned (`subject_hash`, `object_hash`, `target_snapshot`,
09 §9.5). A claims file `hold collection create annotations` and `hold
annotations update --claims` read.

WHY (2026-11-17, wikipedia re-created at parser 6, the operator's "Choice
B"). An annotations collection annotates a TARGET by its `collection_id`,
inside its own (09 §9.3 `annotates`), so a re-created target is followed by
a re-created annotations collection: its claims carried, pinned to the new
target's snapshot (02 §2.10). Its own claims file (`origin/claims.jsonl`)
is its method's whole output, read from the target's live manifest, and may
name ends the snapshot did not hold yet (`update(unheld="drop")` set them
aside); `create` refuses such a file. The snapshot's claims are the claims
that hold: created again against the re-created target, `create` reads
every end there (by the book) and checks each pin — a record whose content
moved, or a pin to another snapshot, refuses the file, naming the line.
A target re-created at a NEW PARSER moves both (gutenberg 10 -> 12,
2026-11-18): `create --repin` pins every claim anew, as `update` re-checks
the claims it holds, and says first how many rest on text that moved.

MULTI-CORE: one part per task in the parse pool (`parallel.each_part`), the
lines written here in the parts' order.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


def claims_of_part(path: str) -> bytes:
    """A part's claims, one line each, in record order (a pool task)."""
    from app.pack import records as pack_records
    out = bytearray()
    for r in pack_records.iter_fields(Path(path), ("sections",)):
        out += r["sections"][0][1].encode("utf-8") + b"\n"
    return bytes(out)


def export(root, ref: str, out: Path, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The claims of `ref`'s latest snapshot into `out`, written whole (a
    temporary name, then a rename)."""
    from app import parallel

    from . import collection as col
    from .codex import CodexError, is_annotations, targets_of
    from .seed import latest_snapshot
    root = Path(root)
    name, codex = col.resolve(root, ref)
    if not is_annotations(codex):
        raise CodexError(f"{name} is not an annotations collection: it holds records, not claims")
    snap = latest_snapshot(root, name)
    tree = col.shard_dir_of(root, name)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    n = size = 0
    try:
        with open(tmp, "wb") as f:
            for blob in parallel.each_part(claims_of_part, [(str(tree / s.path),) for s in snap.shards]):
                f.write(blob)
                n += blob.count(b"\n")
                size += len(blob)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    log(f"[annotations] {name}: {n:,} claim(s) of snapshot {snap.snapshot_id} into {out} ({size / 2**20:.1f} MiB), "
        f"each pinned as its record holds it")
    return {"collection": name, "snapshot": snap.snapshot_id, "claims": n, "bytes": size, "out": str(out),
            "targets": [{"collection_id": t.collection_id, "from_snapshot": t.from_snapshot}
                        for t in targets_of(codex)]}
