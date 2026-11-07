"""`hold vectors source-hashes <collection> [--apply]` — the backfill of
`embeddings.SOURCE_COLUMN` on a vector table written before it (09 §9.6,
amended 2026-11-07, operator: A; docs/notes/pubmed-updatefiles.md §10).

WHAT A ROW GETS. The 09 §9.5 `content_hash` of the record its vector was
made or admitted for — known, without the encoder, exactly where the
current snapshot's line for the record and the manifest agree that the
vector stands for that line:

- the document is `embed_done = 1` and in error nowhere (the mark says its
  vectors are its current record's: discovery resets it on any change);
- its manifest `content_hash` is the line's;
- its origin bytes are the line's (`txt_hash` = the line's `origin_hash`,
  record_source's freshness rule; no `txt_hash` is no evidence of change,
  as the records build reads it). The manifest's `content_hash` is written
  by the records build, never by the rdf stage, so a document changed and
  re-embedded since the last build carries the OLD build's hash there; this
  test is what keeps that hash off its row.

Any other row gets NULL, which never matches: its pair vector is made again
when its record next changes, never wrongly kept.

HOW. Two phases, each resumable (invariant 2):

1. The sources, part by part of the current snapshot: the lines' `uid`,
   `content_hash` and `origin_hash` read in the parse pool (only the line's
   last keys, by 09 §9.5's fixed order; a Parquet part's three columns),
   judged against the manifest here, and committed with the part's name
   into a work database under the collection's `state/` — a rerun skips the
   parts done.
2. Lance's own schema evolution (`LanceDataset.add_columns` with a batch
   function over `book_id`, in `embeddings.add_source_column`: the vector
   store's layer, the one place Lance is opened): new column files per fragment, the vectors and
   every other column untouched, one commit (pylance 9.0.1). Measured: a
   kill mid-pass leaves the table as it was (the commit is the last step)
   and its checkpoint file keeps the fragments done, which the rerun does
   not recompute. A table that already has the column is left as it is.

It writes the vector table, so `--apply` asks for the collection paused
and takes its write lock (louise.py); the work files go when it finishes.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

#: the work database and the add_columns checkpoint, under `state/`
WORK_DB = "source-hashes.sqlite"
CHECKPOINT = "source-hashes.ckpt.sqlite"
#: manifest rows looked up per query
_LOOKUP = 900

#: 09 §9.5's record line: `uid` first; `content_hash`, `origin_hash` last
_UID = re.compile(rb'^\{"uid":"((?:[^"\\]|\\.)*)"')
_TAIL = re.compile(rb'"content_hash":"([0-9a-f]{32})","origin_hash":(?:"((?:[^"\\]|\\.)*)"|null)\}$')


def part_sources(path: str) -> list[tuple[str, str, str | None]]:
    """(uid, content_hash, origin_hash) of every record of one part, in
    order — in a child of the parse pool (`parallel.each_part`)."""
    import json
    from app.pack import records as pack_records
    p = Path(path)
    if pack_records.is_parquet(p):
        pf = pack_records._parquet_file(p)
        out = []
        for g in range(pf.metadata.num_row_groups):
            t = pf.read_row_group(g, columns=["uid", "content_hash", "origin_hash"])
            out.extend(zip(t.column("uid").to_pylist(), t.column("content_hash").to_pylist(),
                           t.column("origin_hash").to_pylist()))
        return out
    index = pack_records.read_index(p)
    out = []
    for k in range(len(index.frames)):
        for line in pack_records.read_frame(p, index, k):
            u, t = _UID.match(line), _TAIL.search(line)
            if u is None or t is None:
                continue                        # not a record line it can read: no source
            uid = json.loads(b'"' + u.group(1) + b'"')
            origin = json.loads(b'"' + t.group(2) + b'"') if t.group(2) is not None else None
            out.append((uid, t.group(1).decode("ascii"), origin))
    return out


def _work(state_dir: Path) -> sqlite3.Connection:
    """The work database: (native_id, source) found, and the parts done."""
    state_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(state_dir / WORK_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS src(native_id INTEGER PRIMARY KEY, h BLOB NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS done(part TEXT PRIMARY KEY, kept INTEGER, seen INTEGER)")
    return con


def _judge(man: sqlite3.Connection, lines: list[tuple[str, str, str | None]]) -> list[tuple[int, bytes]]:
    """(native_id, 16 bytes) for the lines whose documents' vectors stand for
    them (the module note)."""
    by_uid = {u: (ch, oh) for u, ch, oh in lines}
    uids = list(by_uid)
    out = []
    for i in range(0, len(uids), _LOOKUP):
        part = uids[i:i + _LOOKUP]
        q = ("SELECT uid, native_id, embed_done, error, txt_hash, content_hash FROM documents "
             f"WHERE uid IN ({','.join('?' * len(part))})")
        for uid, native_id, embed_done, error, txt_hash, content_hash in man.execute(q, part):
            ch, oh = by_uid[uid]
            if embed_done != 1 or error is not None or content_hash != ch:
                continue
            if txt_hash and txt_hash != oh:
                continue                        # the origin moved since the line was built
            try:
                out.append((int(native_id), bytes.fromhex(ch)))
            except (TypeError, ValueError):
                continue
    return out


def _tables(local) -> list:
    from app import vector_parts
    return vector_parts.views(local)


def _table_of(collection):
    from app import embeddings
    db = embeddings._conn(collection)
    return db.open_table(embeddings.TABLE) if embeddings.TABLE in db.table_names() else None


def survey(root, name: str, local, con) -> dict[str, Any]:
    """The dry run: what `--apply` would do, read-only — the tables, their
    rows and whether each has the column, the documents whose vectors are
    marked current, and the snapshot the sources come from."""
    from app import embeddings
    from app.share import possession
    tables = []
    for view in _tables(local):
        tbl = _table_of(view)
        if tbl is None:
            continue
        tables.append({"table": str(Path(view.lance_dir)), "rows": tbl.count_rows(),
                       "column": embeddings.SOURCE_COLUMN in tbl.schema.names})
    snap = possession.current_snapshot(Path(root), name)
    embedded = con.execute("SELECT COUNT(*) FROM documents WHERE embed_done=1 AND error IS NULL"
                           ).fetchone()[0]
    return {"collection": name, "tables": tables,
            "rows": sum(t["rows"] for t in tables),
            "without_column": sum(1 for t in tables if not t["column"]),
            "embedded_documents": embedded,
            "snapshot": snap.snapshot_id if snap is not None else None,
            "parts": len(snap.shards) if snap is not None else 0}


def apply(root, name: str, local, con, *, state_dir: Path, log=None, stop=None) -> dict[str, Any]:
    """Both phases (the module note). `con` is the manifest, read here;
    the caller holds the collection's write lock and has checked it paused."""
    from app import embeddings, parallel
    from app.hold import collection as col
    from app.share import possession
    say = log or (lambda _m: None)
    todo_tables = [v for v in _tables(local)
                   if (t := _table_of(v)) is not None and embeddings.SOURCE_COLUMN not in t.schema.names]
    if not todo_tables:
        _clean(state_dir)
        return {"collection": name, "unchanged": True, "added": 0}
    work = _work(state_dir)
    try:
        # phase 1 — the sources, a part at a time, a commit each
        snap = possession.current_snapshot(Path(root), name)
        shard_dir = col.shard_dir_of(Path(root), name)
        done = {r[0] for r in work.execute("SELECT part FROM done")}
        parts = [s for s in (snap.shards if snap is not None else ()) if s.path not in done]
        say(f"[source-hashes] {name}: {len(parts)} part(s) to read of "
            f"{len(snap.shards) if snap is not None else 0}; {len(done)} done before")
        tasks = [(str(shard_dir / s.path),) for s in parts if (shard_dir / s.path).is_file()]
        missing = [s.path for s in parts if not (shard_dir / s.path).is_file()]
        by_path = {t[0]: s for t, s in zip(tasks, [s for s in parts if (shard_dir / s.path).is_file()])}
        for (path,), lines in zip(tasks, parallel.each_part(part_sources, tasks)):
            if stop is not None and stop.is_set():
                raise InterruptedError("stopped between two parts; the rerun resumes")
            kept = _judge(con, lines)
            work.execute("BEGIN IMMEDIATE")
            work.executemany("INSERT OR REPLACE INTO src(native_id, h) VALUES(?,?)", kept)
            work.execute("INSERT OR REPLACE INTO done(part, kept, seen) VALUES(?,?,?)",
                         (by_path[path].path, len(kept), len(lines)))
            work.execute("COMMIT")
        found = work.execute("SELECT COUNT(*) FROM src").fetchone()[0]
        # phase 2 — the column, by Lance's add_columns, table by table
        keys, vals = _load(work, found)
        added = filled = 0
        for view in todo_tables:
            got = _add_column(view, keys, vals, con, state_dir / CHECKPOINT)
            added += 1
            filled += got
            say(f"[source-hashes] {name}: {Path(view.lance_dir).name}: column added, "
                f"{got:,} row(s) with a source")
    finally:
        work.close()
    _clean(state_dir)
    return {"collection": name, "unchanged": False, "added": added, "sources": found,
            "rows_with_source": filled, "parts_missing": missing}


def _load(work: sqlite3.Connection, n: int):
    """The sources as two arrays sorted by native id — 24 bytes a document
    (pubmed's 40 M: ~0.96 GB), read in windows: a list of 40 M Python rows
    would be five times that."""
    import numpy as np
    keys = np.empty(n, dtype=np.int64)
    vals = np.empty((n, 16), dtype=np.uint8)
    cur = work.execute("SELECT native_id, h FROM src ORDER BY native_id")
    at = 0
    while at < n:
        got = cur.fetchmany(100_000)
        if not got:
            break
        k = len(got)
        keys[at:at + k] = [r[0] for r in got]
        vals[at:at + k] = np.frombuffer(b"".join(r[1] for r in got), dtype=np.uint8).reshape(k, 16)
        at += k
    return keys[:at], vals[:at]


def _add_column(view, keys, vals, con, checkpoint: Path) -> int:
    """`source_hash` onto one table, in the vector store's own layer
    (`embeddings.add_source_column`: Lance is opened there and nowhere else);
    the rows given a source."""
    from app import embeddings
    return embeddings.add_source_column(view, keys, vals, lock_con=con, checkpoint=checkpoint)


def _clean(state_dir: Path) -> None:
    for n in (WORK_DB, WORK_DB + "-wal", WORK_DB + "-shm", CHECKPOINT):
        (Path(state_dir) / n).unlink(missing_ok=True)
