"""A records build's relations parts (09 §9.14): the edges each record's
plugin states, one part beside each record part of the same slice and
number, `relations/<slice>/<NNNNN>.jsonl.zst`.

WHERE THE EDGES COME FROM. The plugin's optional `relations(doc)` (06 §6.1)
reads what the document's record keeps — normattiva, legi, eurlex and
neuris read `extra` alone — so the edges are computed from the RECORD LINES
of the part, never from the parse: a parsed record and one taken from held
lines (`--records-from`, `_record_from_line`) are the same line, and give
the same edges by construction. The doc each call gets is the line's own
fields (`doc_of`); a plugin without `relations` writes nothing and its
build is byte for byte what it was.

WHEN. After the parts are cut (`records.build`) and before they are
promoted, over the staged parts with the node's pool (`parallel.each_part`,
one task per part); or, for a collection already built, over the parts its
build database names (`backfill`, `louise hold records relations`, RULE 1:
nothing re-parsed, no record part touched). Each part is recorded done in
the build database once its file is whole (`relations_parts`), keyed on
the record part's root and the snapshot id, so a stopped pass resumes after
the last part it recorded (invariant 2) and an unchanged re-run is a no-op.

Considered and not taken: emitting the edges inside the build's
`commit_written` (app/pack/build.py), where the part's records are already
in hand. The line there travels compressed and slimmed, and the snapshot
id the edges carry is the snapshot's, not the build's (below) — the pass
here re-reads each part once, in parallel, and is skipped entirely for a
plugin with no relations.

THE SNAPSHOT AN EDGE NAMES (09 §9.14: "the snapshot in which the edge was
first listed"). A build does not know the id `hold snapshot create` will
give: it takes the id `create` would take by default (the clock's period),
or the build's `--snapshot` where that is a snapshot id, and records it in
the build database (`META_KEY`). `snapshot create` refuses to freeze the
parts under any other id (`require_for`) rather than list edges that name a
snapshot that never listed them. An edge identical to one in the previous
snapshot's relations part of the same slice and number keeps that part's
id; any other carries this one.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .codex import CodexError


class RelationsError(CodexError):
    """A relations part cannot be written as 09 §9.14 fixes it."""


#: the build database's record of the snapshot id the relations parts were
#: written for: "" while a pass is under way, the id once every part is done
META_KEY = "relations_for"
TABLE = """CREATE TABLE IF NOT EXISTS relations_parts(
  slice TEXT NOT NULL, n INTEGER NOT NULL, record_root TEXT NOT NULL,
  snapshot TEXT NOT NULL, edges INTEGER NOT NULL, root TEXT,
  PRIMARY KEY(slice, n))"""
#: 09 §9.4's snapshot ids: `YYYY-MM` or `YYYY-Www`
SNAPSHOT_ID = re.compile(r"^\d{4}-(?:\d{2}|W\d{2})$")
#: what a line gives `relations()`: every field of the record (09 §9.5)
FIELDS = ("uid", "native_id", "lang", "title", "creators", "date", "subjects",
          "keys", "sections", "extra", "origin", "origin_hash")


@dataclass(frozen=True)
class Result:
    parts: int                  # parts computed by this run
    edges: int                  # edges in them
    interrupted: bool = False


# --------------------------------------------------------------------------
# pure: a part's lines

def plugin_of(codex):
    """The collection's plugin when it implements `relations()`, else None."""
    from app.collections import PLUGIN_REGISTRY
    plugin = PLUGIN_REGISTRY.get(codex.plugin)
    return plugin if callable(getattr(plugin, "relations", None)) else None


def doc_of(line: dict[str, Any]):
    """The `CanonicalDoc` a record line stands for: the line's own fields.
    `raw_hash` is the line's `origin_hash` (the same value, 09 §9.5)."""
    from app.plugins.base import CanonicalDoc
    return CanonicalDoc(
        uid=str(line["uid"]), title=str(line.get("title") or ""),
        creators=[str(c) for c in line.get("creators") or []],
        language=str(line.get("lang") or "und"), date=line.get("date"),
        subjects=[str(s) for s in line.get("subjects") or []],
        keys=[str(k) for k in line.get("keys") or []],
        extra=dict(line.get("extra") or {}),
        sections=[tuple(s) for s in line.get("sections") or []],
        raw_hash=str(line.get("origin_hash") or ""))


def part_lines(plugin, lines: Iterable[dict[str, Any]], snapshot_id: str,
               carried: dict[tuple[str, str, str], str] | None = None) -> list[bytes]:
    """09 §9.14's edge lines of one part: `from` the record's uid, `to` and
    `type` as the plugin yields them, each (from, type, to) once, sorted by
    their bytes; `snapshot` the earlier id `carried` names for an identical
    edge, else `snapshot_id`."""
    from . import records as rec
    triples: set[tuple[str, str, str]] = set()
    for line in lines:
        doc = doc_of(line)
        for e in plugin.relations(doc):
            triples.add((doc.uid, str(e.type), str(e.to)))
    carried = carried or {}
    ordered = sorted(triples, key=lambda t: (t[0].encode(), t[1].encode(), t[2].encode()))
    return [rec.edge_line(f, to, ty, carried.get((f, ty, to), snapshot_id)) for f, ty, to in ordered]


def _from_of(line: bytes) -> str:
    return json.loads(line)["from"]


def _carried(candidates: list[str], root: str, size: int) -> dict[tuple[str, str, str], str]:
    """The previous snapshot's edges of this part, {(from, type, to):
    snapshot}, from whichever candidate file holds its listed bytes."""
    from app.pack import records as pack_records
    from . import shard as shard_mod
    for c in candidates:
        p = Path(c)
        if p.is_file() and p.stat().st_size == size and shard_mod.pieces_root_of(p) == root:
            return {(e["from"], e["type"], e["to"]): e["snapshot"] for e in pack_records.iter_records(p)}
    raise RelationsError(
        f"{Path(candidates[0]).name}: the previous snapshot's relations part (root {root}) is not "
        f"held, so the snapshot each edge was first listed in cannot be carried (09 §9.14)")


def compute_part(plugin_name: str, record_path: str, previous: tuple | None,
                 snapshot_id: str, out_path: str) -> tuple[int, int, str | None]:
    """One part, in a pool child: the record part's edges written to
    `out_path` (§9.5's framing, `uids` keyed by `from`), nothing where it
    has none. `previous`: (candidate paths, root, bytes) of the previous
    snapshot's relations part of this slice and number. (edges, bytes,
    root)."""
    from app.collections import PLUGIN_REGISTRY
    from app.pack import records as pack_records
    from . import shard as shard_mod
    carried = _carried(list(previous[0]), previous[1], int(previous[2])) if previous else {}
    lines = part_lines(PLUGIN_REGISTRY[plugin_name], pack_records.iter_fields(Path(record_path), FIELDS),
                       snapshot_id, carried)
    out = Path(out_path)
    if not lines:
        out.unlink(missing_ok=True)
        return 0, 0, None
    pack_records.write_lines(out, lines, key_of=_from_of)
    raw = out.read_bytes()
    return len(lines), len(raw), shard_mod.pieces_root(raw)


# --------------------------------------------------------------------------
# the pass

def snapshot_id_of(codex, given: str | None = None) -> str:
    """The id the edges are written for: `given` where it is a snapshot id,
    else the one `hold snapshot create` takes by default."""
    if given and SNAPSHOT_ID.match(given):
        return given
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from . import snapshot as sn
    return sn.snapshot_id_for(datetime.now(ZoneInfo(codex.origin.clock)), codex.update.cadence)


def _build_db(staging: Path) -> Path:
    return Path(staging) / ".build" / "build.db"


def write(codex, *, staging: Path, shard_dir: Path, snapshot_id: str, promote: bool = False,
          stop=None, log=print, name: str = "") -> Result:
    """The relations part of every part the build database names, into
    `staging/relations/…`; with `promote`, each into the SHARD as soon as it
    is whole (`backfill`). A plugin without `relations()`: nothing, not even
    a database write."""
    from app import parallel
    from . import shard as shard_mod, snapshot as sn
    plugin = plugin_of(codex)
    if plugin is None:
        return Result(0, 0)
    staging, shard_dir = Path(staging), Path(shard_dir)
    db_path = _build_db(staging)
    if not db_path.is_file():
        raise RelationsError(f"{name}: no build database; `hold records build` first (07 §7.3)")
    con = sqlite3.connect(db_path, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        con.execute(TABLE)
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, '')", (META_KEY,))
        con.execute("COMMIT")
        rows = [dict(r) for r in con.execute("SELECT slice, n, path, root FROM shards ORDER BY slice, n")]
        done = {(r["slice"], r["n"]): (r["record_root"], r["snapshot"])
                for r in con.execute("SELECT * FROM relations_parts")}
        previous = sn._previous(shard_dir, snapshot_id)
        before = {(r.slice, r.n): r for r in previous.relations} if previous else {}
        todo, tasks = [], []
        for r in rows:
            if done.get((r["slice"], r["n"])) == (r["root"], snapshot_id):
                continue
            staged = staging / r["path"]
            record = staged if staged.is_file() else shard_dir / r["path"]
            if not record.is_file():
                raise RelationsError(f"{name}: {r['path']} is neither staged nor in the SHARD")
            rel = shard_mod.relations_path(r["slice"], r["n"])
            prev = before.get((r["slice"], r["n"]))
            prev_arg = ((str(shard_dir / rel), str(shard_mod.superseded_name(shard_dir / rel, previous.snapshot_id))),
                        prev.root, prev.bytes) if prev else None
            todo.append(r)
            tasks.append((codex.plugin, str(record), prev_arg, snapshot_id, str(staging / rel)))
        parts = edges = 0
        stopped = False
        if todo:
            log(f"[records relations] {name}: {len(todo)} of {len(rows)} part(s) to write for {snapshot_id}"
                f" on {parallel.processes()} process(es)")
        results = parallel.each_part(compute_part, tasks)
        try:
            for r, (n_edges, _bytes, root) in zip(todo, results):
                if promote:
                    promote_part(staging, shard_dir, r["slice"], r["n"], n_edges)
                con.execute("BEGIN IMMEDIATE")
                con.execute("INSERT OR REPLACE INTO relations_parts(slice, n, record_root, snapshot, edges, root) "
                            "VALUES(?,?,?,?,?,?)", (r["slice"], r["n"], r["root"], snapshot_id, n_edges, root))
                con.execute("COMMIT")
                parts += 1
                edges += n_edges
                if stop is not None and stop.is_set():
                    stopped = len(todo) > parts
                    break
        finally:
            results.close()
        if stopped:
            log(f"[records relations] {name}: stopped after {parts} part(s); the next run resumes")
            return Result(parts, edges, True)
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (META_KEY, snapshot_id))
        con.execute("COMMIT")
    finally:
        con.close()
    if parts:
        log(f"[records relations] {name}: {edges} edge(s) in {parts} part(s) for {snapshot_id}")
    return Result(parts, edges)


def promote_part(staging: Path, shard_dir: Path, slice_: str, n: int, edges: int) -> None:
    """One relations part into the SHARD, the way a build promotes its
    records (`shard.promote`, the listed version it replaces kept under its
    superseded name until the next snapshot); with no edges, a relations
    part still at that path is retired the same way, so no part's old edges
    are listed beside a record part that no longer states them."""
    from . import shard as shard_mod
    rel = shard_mod.relations_path(slice_, n)
    staged, target = Path(staging) / rel, Path(shard_dir) / rel
    if staged.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shard_mod.promote(staged, target, shard_dir=Path(shard_dir), until_next_snapshot=True)
    elif edges == 0 and target.is_file():
        shard_mod.keep_superseded(target, Path(shard_dir), "", until_next_snapshot=True)
        target.unlink(missing_ok=True)


def promote_staged(staging: Path, shard_dir: Path) -> int:
    """Every part `write` recorded into the SHARD (a build's promotion,
    before its record parts: a crash between the two leaves edges beside
    the part they were computed from, not a new part beside old edges)."""
    db_path = _build_db(staging)
    if not db_path.is_file():
        return 0
    con = sqlite3.connect(db_path, timeout=60)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='relations_parts'").fetchone():
            return 0
        rows = con.execute("SELECT slice, n, edges FROM relations_parts ORDER BY slice, n").fetchall()
    finally:
        con.close()
    for slice_, n, edges in rows:
        promote_part(staging, shard_dir, slice_, int(n), int(edges))
    return len(rows)


def require_for(db_path: Path, snapshot_id: str, name: str = "") -> None:
    """`snapshot create`'s guard: the relations parts beside the build's
    record parts were written for THIS snapshot id, and completely. Parts
    that hold NO edge name no snapshot — their bytes are the same under any
    id — so a finished pass that found none freezes under whichever id
    `create` is given (a plugin with relations over records that state
    none: gutenberg's books without catalogue notes, 2026-11-12)."""
    con = sqlite3.connect(db_path)
    edges = None
    try:
        row = con.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
        if row is not None and row[0] not in ("", snapshot_id):
            edges = con.execute("SELECT COALESCE(SUM(edges), 0) FROM relations_parts").fetchone()[0]
    except sqlite3.Error:
        row = None
    finally:
        con.close()
    if row is None or row[0] == snapshot_id or (row[0] != "" and edges == 0):
        return
    if row[0] == "":
        raise CodexError(f"{name}: the relations pass did not finish; `hold records relations {name}"
                         f" --snapshot {snapshot_id}` completes it (09 §9.14)")
    raise CodexError(
        f"{name}: the relations parts were written for snapshot {row[0]}, and their edges name it as "
        f"the snapshot that first lists them (09 §9.14); `hold records relations {name} --snapshot "
        f"{snapshot_id}` writes them for {snapshot_id}")


def backfill(root, ref: str, *, snapshot_id: str | None = None, stop=None, log=print) -> Result:
    """`louise hold records relations <collection>` (RULE 1): the relations
    parts of the record parts the collection's build database names — the
    latest snapshot's once that build is frozen — written into the SHARD,
    no record part touched, for `hold snapshot create` to list."""
    from . import collection as col, records as rec, shard as shard_mod, snapshot as sn
    name, codex = col.resolve(Path(root), ref)
    rec.local_collection(Path(root), name, codex)          # invariant 9: its data is used
    if plugin_of(codex) is None:
        log(f"[records relations] {name}: plugin {codex.plugin} states no relations (06 §6.1); nothing to write")
        return Result(0, 0)
    shard_dir, _state, staging = rec._paths(root, name)
    sid = snapshot_id_of(codex, snapshot_id)
    if snapshot_id and sid != snapshot_id:
        raise RelationsError(f"{snapshot_id!r} is not a snapshot id (YYYY-MM or YYYY-Www, 09 §9.4)")
    out = shard_dir / shard_mod.snapshot_path(sid)
    if out.is_file() and not sn.load(out).relations:
        raise RelationsError(
            f"{name}: snapshot {sid} exists and lists no relations; a snapshot is written once (09 §9.4). "
            f"Name the next period with --snapshot, or discard {sid} (`hold snapshot discard`) and "
            f"rebuild it with `records build --records-from-discarded {sid}`, which writes them")
    return write(codex, staging=staging, shard_dir=shard_dir, snapshot_id=sid, promote=True,
                 stop=stop, log=log, name=name)
