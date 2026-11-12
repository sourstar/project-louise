"""Flat and tree facets on a field the manifest does not carry (05 §5.7;
amended 2026-11-12, legal-chunking-retrieval.md §4 R2): `extra.<key>` — a
legal act's force status, its type, its code — or a top-level record field
other than the manifest's `lang`, `authors`, `subjects`, `title`. A FILTER
the reader asks for, never a weight (invariant 4): what this module gives
is a set of records, and nothing it reads enters a ranking.

The values are read out of the record parts this peer holds, as a range
facet's are (app/ask/ranges.py, whose part bookkeeping this follows): one
pass per part, keyed on the part's path and its root — a part already read
at that root is skipped, a re-issued one replaced whole (delete-then-insert,
the mark last, invariant 2), a part the snapshot no longer lists dropped.
A part is read ONCE for every such facet that needs it: the `extra` column
is the record's largest, and normattiva holds 340,000 of them.

A value is a scalar or a list of scalars: a string as written, a boolean
as JSON writes it (`true`, `false` — 09 §9.5's `in_force`), a number as
its JSON text. An object is no facet value: it is counted as `unparsed`
and said, never guessed into one.

Every bound is facets.py's: more than `max_distinct` distinct values and
the field is not counted, more than `rows_bound` (value, record) rows and
it has no postings; either is said in the cache. The postings hold every
value, broad ones too: the filter refuses a value of more than
`filters.MAX_IDS` records by its count, before any posting is read.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.ask import ranges

_SCHEMA = (
    ("CREATE TABLE IF NOT EXISTS value_postings (field TEXT NOT NULL, value TEXT NOT NULL, "
     "native_id INTEGER NOT NULL, part INTEGER NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS idx_value_postings ON value_postings(field, value, native_id)",
    "CREATE INDEX IF NOT EXISTS idx_value_part ON value_postings(part)",
    ("CREATE TABLE IF NOT EXISTS value_parts (id INTEGER PRIMARY KEY, field TEXT NOT NULL, part TEXT NOT NULL, "
     "root TEXT NOT NULL, records INTEGER NOT NULL, rows INTEGER NOT NULL, unparsed INTEGER NOT NULL, "
     "UNIQUE(field, part))"),
)


def ensure_schema(pcon) -> None:
    for stmt in _SCHEMA:
        pcon.execute(stmt)


def scalars(v: Any) -> tuple[list[str], int]:
    """A record's values for a flat or tree facet, and how many were not a
    scalar (an object, a list inside a list)."""
    out: list[str] = []
    bad = 0
    for one in (v if isinstance(v, list) else [v]):
        if one is None or one == "":
            continue
        if isinstance(one, bool):
            out.append("true" if one else "false")
        elif isinstance(one, (int, float)):
            out.append(json.dumps(one))
        elif isinstance(one, str):
            if one.strip():
                out.append(one.strip())
        else:
            bad += 1
    return list(dict.fromkeys(out)), bad


def _read_part(path: Path, fields: list[str]) -> dict[str, dict[str, Any]]:
    """One read of the part for every field: `{field: {rows, records, unparsed}}`."""
    from app.pack import records as pack_records
    columns = sorted({"extra" if f.startswith("extra.") else f for f in fields})
    got = {f: {"rows": [], "records": 0, "unparsed": 0} for f in fields}
    for rec in pack_records.iter_fields(path, ("native_id", *columns)):
        for f in fields:
            got[f]["records"] += 1
        try:
            nid = int(rec.get("native_id"))
        except (TypeError, ValueError):
            continue        # as every facet pass: the legs take numeric ids only
        for f in fields:
            values, bad = scalars(ranges._value_of(rec, f))
            got[f]["unparsed"] += bad
            got[f]["rows"].extend((v, nid) for v in values)
    return got


def clear(pcon, field: str) -> None:
    ensure_schema(pcon)
    pcon.execute("BEGIN IMMEDIATE")
    try:
        pcon.execute("DELETE FROM value_postings WHERE part IN (SELECT id FROM value_parts WHERE field=?)", (field,))
        pcon.execute("DELETE FROM value_parts WHERE field=?", (field,))
        pcon.execute("DELETE FROM built WHERE field=?", (field,))
        pcon.execute("COMMIT")
    except BaseException:
        pcon.execute("ROLLBACK")
        raise


def _drop_part(pcon, part_id: int) -> None:
    pcon.execute("DELETE FROM value_postings WHERE part=?", (part_id,))
    pcon.execute("DELETE FROM value_parts WHERE id=?", (part_id,))


def index(pcon, facets: list[dict[str, Any]], *, shard_dir: Path, snapshot, snapshot_id: str | None,
          rows_bound: int, max_distinct: int, stop: Callable[[], bool] | None = None) -> dict[str, dict[str, Any]]:
    """Bring each facet's postings to `snapshot`'s parts held here and mark
    them built for `snapshot_id`: `{field: cache entry}` (facets.py writes
    it). Stopped between parts by `stop` (ranges.Stopped)."""
    ensure_schema(pcon)
    held = [s for s in snapshot.shards if (Path(shard_dir) / s.path).is_file()]
    documents = sum(int(s.records) for s in held)
    names = [f["field"] for f in facets]
    out: dict[str, dict[str, Any]] = {}
    live = []
    for f in facets:
        if documents > rows_bound:
            clear(pcon, f["field"])
            out[f["field"]] = _entry(f, documents, None, f"{documents} records hold its values, more than the "
                                                         f"{rows_bound} rows a field's index may reach on this peer")
            continue
        r = pcon.execute("SELECT snapshot_id FROM built WHERE field=?", (f["field"],)).fetchone()
        if r is not None and r[0] != snapshot_id:
            # rows of two snapshots must never answer as either: unmarked until done
            pcon.execute("DELETE FROM built WHERE field=?", (f["field"],))
        live.append(f["field"])
    done = {field: {} for field in live}
    for row in pcon.execute("SELECT id, field, part, root FROM value_parts"):
        if row[1] in done:
            done[row[1]][row[2]] = (row[0], row[3])
    paths = {s.path for s in held}
    for field in live:
        for part, (pid, _root) in done[field].items():
            if part not in paths:
                pcon.execute("BEGIN IMMEDIATE")
                try:
                    _drop_part(pcon, pid)
                    pcon.execute("COMMIT")
                except BaseException:
                    pcon.execute("ROLLBACK")
                    raise
    for s in held:
        wanted = [field for field in live if done[field].get(s.path, (None, None))[1] != s.root]
        if not wanted:
            continue
        if stop is not None and stop():
            raise ranges.Stopped(f"{', '.join(wanted)}: the pass was stopped before it finished")
        got = _read_part(Path(shard_dir) / s.path, wanted)
        pcon.execute("BEGIN IMMEDIATE")
        try:
            for field in wanted:
                g = got[field]
                old = pcon.execute("SELECT id FROM value_parts WHERE field=? AND part=?", (field, s.path)).fetchone()
                if old is not None:
                    _drop_part(pcon, old[0])
                pid = pcon.execute(
                    "INSERT INTO value_parts(field, part, root, records, rows, unparsed) VALUES(?,?,?,?,?,?)",
                    (field, s.path, s.root, g["records"], len(g["rows"]), g["unparsed"])).lastrowid
                pcon.executemany("INSERT INTO value_postings(field, value, native_id, part) VALUES(?,?,?,?)",
                                 [(field, v, nid, pid) for v, nid in g["rows"]])
            pcon.execute("COMMIT")
        except BaseException:
            pcon.execute("ROLLBACK")
            raise
    by_field = {f["field"]: f for f in facets}
    for field in live:
        out[field] = _finish(pcon, by_field[field], snapshot_id=snapshot_id, documents=documents,
                             rows_bound=rows_bound, max_distinct=max_distinct)
    return {n: out[n] for n in names}


def _entry(f: dict[str, Any], documents: int, values: dict[str, int] | None, why: str | None,
           postings: dict[str, Any] | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"field": f["field"], "kind": f["kind"], "separator": f.get("separator"),
                         "authority": f.get("authority"), "documents": documents, "values": values,
                         "postings": postings or {"built": False, "why": why}}
    if why is not None:
        e["why"] = why
    return e


def _finish(pcon, f: dict[str, Any], *, snapshot_id: str | None, documents: int, rows_bound: int,
            max_distinct: int) -> dict[str, Any]:
    field = f["field"]
    parts = pcon.execute("SELECT rows, unparsed FROM value_parts WHERE field=?", (field,)).fetchall()
    rows = sum(p[0] for p in parts)
    unparsed = sum(p[1] for p in parts)
    if rows > rows_bound:
        clear(pcon, field)
        return _entry(f, documents, None, f"{rows} values, more than the {rows_bound} rows a field's index "
                                          f"may reach on this peer")
    counts: dict[str, int] = {}
    sep = f.get("separator") if f["kind"] == "tree" else None
    # the covering index, in value order: one streamed scan of the field
    for value, n in pcon.execute("SELECT value, COUNT(*) FROM value_postings WHERE field=? GROUP BY value",
                                 (field,)):
        if sep:
            pieces = value.split(sep)
            for i in range(1, len(pieces) + 1):
                path = sep.join(pieces[:i])
                counts[path] = counts.get(path, 0) + n
        else:
            counts[value] = n
        if len(counts) > max_distinct:
            clear(pcon, field)
            return _entry(f, documents, None, f"more than {max_distinct} distinct values: not counted on this "
                                              f"peer (bounded memory; 05 §5.7's counts are for a field a "
                                              f"reader can browse)")
    from app.ask import filters as filters_mod
    pcon.execute("INSERT OR REPLACE INTO built(field, snapshot_id, documents) VALUES(?,?,?)",
                 (field, snapshot_id, documents))
    narrow = sum(1 for c in counts.values() if c <= filters_mod.MAX_IDS)
    return _entry(f, documents, counts, None,
                  {"built": True, "rows": rows, "parts": len(parts), "values": narrow,
                   "broad": len(counts) - narrow, "unparsed": unparsed})
