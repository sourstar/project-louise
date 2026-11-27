"""hold.refit — a collection's encoder window changed IN PLACE (amendment A1l,
2026-11-27; the operator, for gutenberg: "In place", and on the con that the
case is new to the spec: "rule 1").

`louise hold collection refit <name> --layer-param max_tokens=N --layer-param
tokenizer=sha256:…`. The `fixed` chunker's encoder window (app/chunking.py
chunk_fixed) keeps every window that fits as it was, byte for byte and under
its own id (`<uid>:<seq>:<version>`, 06 §6.3), and cuts only a window over
the limit. So the collection's new Codex differs from its own in that
layer's params alone, and every document whose windows all fit holds the
same chunks under both: the same index entries, the same vectors, the same
record lines. By the book a new collection would cut, index and embed every
document again (gutenberg: 19 GiB of vectors, for one book of Tibetan
script, PG 69207, whose one window passes 8,175 tokens).

IN PLACE: the collection keeps its NAME and its directories. Nothing is
copied and nothing is linked (operator, 2026-11-27: "Never use hard links,
always consciously load from a path"); every file stays at its path. What
the old Codex owned and the new one does not is SET ASIDE, renamed and never
deleted, so a failed proof puts it back:

  <SHARD>/.replaced/<old id>/   the old codex.json, its snapshots and the old
                                version of each part rebuilt (INSIDE the
                                SHARD: a SHARD is usually a mount of its own
                                and a rename does not cross one, discard.py)
  state/.replaced/<old id>/     its staging build, the collection file's old
                                text, the change set, and every manifest row
                                the switch changes or removes (saved.sqlite)

THE STEPS, each resumable from `state/refit.json` (invariant 2):

  changes  every in-scope document cut at the old and the new layers in the
           parse pool (`cut_window`, a window of rowids a task); the ones
           whose cuts differ are the change set, and the parts holding them
           are read from the parts' own indexes. Under the pause, before any
           write: search is served from the old state until the switch.
  switch   what the old Codex's space owned saved first (the change set's
           manifest rows, the old space's coverage and gate, share and
           publication rows, the vector tables' Lance versions); then the old
           Codex and snapshots set aside and the new Codex written; the
           collection file's `codex` line rewritten (app/collection_file.py
           `recodex`); the manifest re-pinned (hold/carry.py `_repin`); and
           the change set reset for the worker in three repeatable steps per
           window (carry.py's order: the marks, the rows, then the versions).
  parts    only the parts holding a changed document are written again from
           their own record lines (a line holds no layer, 09 §9.5: its bytes
           are kept; the part's chunk count, its index and its roots move),
           the old version set aside; the staging build database is the old
           one's (SQLite's backup, the rows of its last snapshot) with those
           parts' rows; then the new Codex's first snapshot, under the old
           latest id, each unchanged part's entry and file the old one's and
           its writer kept (`snapshot.create(writers_from=)`), held to the
           old snapshot entry by entry, and signed where this peer has a key.
  proof    a seeded sample of unchanged embedded documents cut at the new
           layers holds, id and text, exactly what the vector table holds
           (`carry._resign_proof`); a seeded sample of unchanged parts cut
           again gives their entries' chunk roots; the change set holds no
           row. A failure ROLLS BACK (`rollback`) and raises.

Then the command runs the CPU reference and the gate for the new space,
still paused, and unpauses: the worker cuts, indexes (delete-then-insert by
uid) and embeds the change set at the new layer.

THE STANDARD. 06 §6.3's carry within a space by what a chunk is (its id and
text), here across two spaces whose function from cleaned text to vectors
is one (`carry.space_differences` empty); 09 §9.4's part entry recomputed
the way the build computes it. Written down, not taken: a copy-on-write
filesystem snapshot (btrfs/ZFS) of the collection's directory would give
the rollback for free; it pays once the node's /data is one, and costs a
filesystem the node does not run.
"""
from __future__ import annotations

import dataclasses
import json
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .codex import Codex, CodexError

#: the pass's state, in the collection's state directory
MARKER = "refit.json"
#: where what the old Codex owned is set aside, in the SHARD and in state/
ASIDE = ".replaced"
#: documents a task of the parse pool
WINDOW = 500
#: the proof's sample of unchanged embedded documents
SAMPLE = 64
#: the proof's sample of unchanged parts cut again
PART_SAMPLE = 4
#: the changed uids a report names, at most
SHOWN = 20
#: the `fixed` chunker's OPTIONAL params, one group (app/layers.py
#: OPTIONAL_PARAMS): the encoder's window
FIT_PARAMS = ("max_tokens", "tokenizer")
STEPS = ("changes", "switch", "parts", "proof")


class RefitFailed(CodexError):
    """The proof did not hold: rolled back, nothing of the new Codex stays."""


# ---- what may be refit ------------------------------------------------------

def layers_refit_only(a: Codex, b: Codex) -> str | None:
    """None when `b`'s layers are `a`'s with only the `fixed` layers' encoder
    window (`FIT_PARAMS`) added or changed — the same number of layers, each
    the same chunker id, version, rule, input and every other param, at
    least one window other. Such a layer cuts every window that fits exactly
    as before; only a document with a window over the limit is cut
    otherwise. Else why not, in words."""
    from app import chunking
    la, lb = list(a.layers), list(b.layers)
    if len(la) != len(lb):
        return f"{len(la)} layer(s) and {len(lb)}: the layer list is fixed at creation"
    moved = 0
    for i, (x, y) in enumerate(zip(la, lb)):
        cx, cy = x.chunker.to_dict(), y.chunker.to_dict()
        px, py = dict(cx.pop("params", None) or {}), dict(cy.pop("params", None) or {})
        fx = {k: px.pop(k) for k in FIT_PARAMS if k in px}
        fy = {k: py.pop(k) for k in FIT_PARAMS if k in py}
        if (cx, px, x.rule, x.form) != (cy, py, y.rule, y.form):
            return (f"layer {i} changes more than the encoder window ({cy.get('id')} {py}): another "
                    f"param cuts every document again, which is a new collection (case A, or a case-J "
                    f"sister with --layer-param), not a refit")
        if fx != fy:
            if cy.get("id") != chunking.FIXED:
                return f"layer {i} is {cy.get('id')!r}: the encoder window is the `fixed` chunker's"
            if set(fy) != set(FIT_PARAMS):
                return (f"layer {i} states {sorted(fy) or 'no window'}: a refit states the encoder "
                        f"window whole, both of {', '.join(FIT_PARAMS)}")
            moved += 1
    return None if moved else "the layers are the same: there is nothing to refit"


def new_codex(old: Codex, layers) -> Codex:
    """The collection's own Codex with `layers`, at the version a new Codex
    in a new space is written at (`collection.derive`'s rule, never below the
    old one's), its slicing written at that version. Its lineage is the old
    one's (a root stays a root, a sister keeps her sister); the old
    `collection_id` is recorded by the pass (`state/refit.json`
    `predecessor`, as case F's run records its predecessor) and names the
    folder the old Codex is set aside in. Its credits are kept: its carried
    vectors are the old ones'. Refused in words unless only the encoder
    window moved, nothing that decides a record or the function."""
    from . import carry as carry_mod, codex as cx, collection as col
    from .seed import record_differences
    cand = dataclasses.replace(old, layers=tuple(layers))
    if cand.space is None:
        raise CodexError("refit: an annotations collection has no layers to refit")
    if cand.space.has_engine:
        version = cx.CODEX_VERSION
        if cand.space.has_options:
            version = max(version, cx.OPTIONS_VERSION)
        version = cx.written_version(cand.space, version)
    elif old.version >= cx.ENGINE_VERSION:
        raise CodexError("refit: a space is written with the ENGINE its reference vectors are computed "
                         "with (09 §9.3)")
    else:
        version = old.version
    version = max(version, old.version)
    new = dataclasses.replace(cand, version=version, slicing=col._slicing_at(old.slicing, version),
                              record_line=old.record_line if version > 1 else None)
    why = layers_refit_only(old, new)
    if why:
        raise CodexError(f"refit: {why}; nothing was written")
    other = [d for d in record_differences(old, new) if d != "layers"]
    if [f.to_dict() for f in old.facets] != [f.to_dict() for f in new.facets]:
        other.append("facets")
    if other:
        raise CodexError(f"refit: the new Codex would differ in {', '.join(other)} as well, which decide "
                         f"record bytes (02 §2.7) — a refit moves the encoder window alone; nothing was "
                         f"written")
    fn = carry_mod.space_differences(old, new)
    if fn:
        raise CodexError(f"refit: the spaces would differ in {', '.join(fn)} as well: the vectors kept "
                         f"are the same function's only; nothing was written")
    if new.space_id == old.space_id:
        raise CodexError("refit: the new layers give the same space: there is nothing to refit")
    cx.validate(new)
    return new


def planned_target(source_coll, planned: Codex):
    """The collection's local side read with the NEW Codex's layers, before
    the switch writes it: the change set is computed against it."""
    from app import layers as layers_mod
    return dataclasses.replace(source_coll, codex=planned, layers=layers_mod.layers_for(planned))


# ---- the change set ---------------------------------------------------------

def cut_window(source, target, lo: int, hi: int) -> dict:
    """One task of the pass: the documents of rowids (lo, hi], each in scope
    and without an error cut at the SOURCE's and the TARGET's layers, as the
    stages cut them (`record_source.title_sections_and_source`,
    `layers.chunks_for_document`). Returns the changed documents (rowid, uid,
    native id), the count compared and the errors, never a text. A document
    that cannot be cut is counted CHANGED: nothing proves its vectors are the
    new layer's, so the worker meets it as by the book. Module-level, so it
    pickles; reads the manifest read-only."""
    from app import db, layers, record_source
    con = db.read_only_connect(source)
    if con is None:
        return {"changed": [], "documents": 0, "errors": 0, "first_error": None}
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT rowid, * FROM documents WHERE rowid > ? AND rowid <= ? ORDER BY rowid", (lo, hi))]
    finally:
        con.close()
    plugin = source.plugin_instance()
    pair = layers.has_pair_layer(source)
    changed: list[list] = []
    documents = errors = 0
    first_error = None
    for row in rows:
        if row.get("error") is not None or not row.get("in_scope"):
            continue
        try:
            title, sections, _src = record_source.title_sections_and_source(source, row, plugin, pair=pair)
            before = [(c["id"], c["text"]) for c in layers.chunks_for_document(source, row, sections, title=title)]
            after = [(c["id"], c["text"]) for c in layers.chunks_for_document(target, row, sections, title=title)]
        except Exception as e:   # noqa: BLE001 — counted and reset; the stage meets it as by the book
            errors += 1
            first_error = first_error or f"{row['uid']}: {type(e).__name__}: {e}"[:300]
            changed.append([int(row["rowid"]), row["uid"], int(row["native_id"])])
            continue
        documents += 1
        if before != after:
            changed.append([int(row["rowid"]), row["uid"], int(row["native_id"])])
    return {"changed": changed, "documents": documents, "errors": errors, "first_error": first_error}


def _changes(s_coll, t_coll, *, start: int, window: int, processes: int | None, stop,
             each: Callable[[int, dict], None], log: Callable[[str], None]) -> bool:
    """The pass from rowid `start`: `each(hi, got)` per window, in order.
    False when a stop was honoured at a window boundary."""
    from app import parallel
    from . import rechunk
    cores, says = rechunk._processes(processes)
    if cores == 0:
        raise CodexError(f"refit: the node allows no core now ({says}); --processes N runs it anyway. "
                         f"Nothing was written")
    con = sqlite3.connect(f"file:{Path(s_coll.manifest_path).as_posix()}?mode=ro", uri=True)
    try:
        top = int(con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0)
    finally:
        con.close()
    step = max(1, int(window))
    tasks = [(s_coll, t_coll, lo, min(lo + step, top)) for lo in range(int(start), top, step)]
    if not tasks:
        return True
    log(f"[refit] {s_coll.name}: {len(tasks)} window(s) of {step} rowid(s) from {start} of {top}, cut at "
        f"both layers by at most {cores or parallel.processes()} process(es) ({says})")
    from app.hold import refit as me                       # by name: a spawned child imports it
    results = parallel.each_part(me.cut_window, tasks, limit=cores)
    try:
        for k, ((_s, _t, _lo, hi), got) in enumerate(zip(tasks, results), 1):
            each(hi, got)
            if k % 20 == 0 or k == len(tasks):
                log(f"[refit] {s_coll.name}: {k}/{len(tasks)} window(s) cut")
            if stop is not None and stop.is_set() and k < len(tasks):
                log(f"[refit] {s_coll.name}: stopped at rowid {hi} (a window boundary); run it again to resume")
                return False
    finally:
        close = getattr(results, "close", None)
        if close is not None:
            close()                                        # no part left queued
    return True


def parts_holding(shard_dir: Path, snap, uids) -> list[dict]:
    """The record parts of `snap` holding any of `uids`, from each part's own
    trailing index (no record decoded), read by threads as a first
    snapshot's delta reads them: `[{slice, n, path, uids}]` in the
    snapshot's order."""
    from concurrent.futures import ThreadPoolExecutor
    from app.pack import records as pack_records
    want = set(uids)
    if not want:
        return []

    def held(s) -> list[str]:
        p = Path(shard_dir) / s.path
        return sorted(want & set(pack_records.read_index(p).uids)) if p.is_file() else []
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="refit-parts") as ex:
        found = list(ex.map(held, snap.shards))
    return [{"slice": s.slice, "n": int(s.n), "path": s.path, "uids": got}
            for s, got in zip(snap.shards, found) if got]


# ---- the marker and the folders set aside -------------------------------------

def _hex(collection_id: str) -> str:
    return collection_id.split(":", 1)[-1]


def marker_path(root, name: str) -> Path:
    return Path(root) / name / "state" / MARKER


def shard_aside(root, name: str, old_id: str) -> Path:
    from . import collection as col
    return col.shard_dir_of(Path(root), name) / ASIDE / _hex(old_id)


def state_aside(root, name: str, old_id: str) -> Path:
    return Path(root) / name / "state" / ASIDE / _hex(old_id)


def read_marker(root, name: str) -> dict | None:
    p = marker_path(root, name)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _save(root, name: str, m: dict) -> None:
    p = marker_path(root, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(m, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def predecessor(root, name: str) -> Codex:
    """The Codex the collection held before a refit in progress — set aside
    once the switch began — or the one it holds now."""
    from . import codex as cx, collection as col
    m = read_marker(root, name)
    if m is not None and not m.get("done") and not m.get("rolled_back"):
        old_id = m["predecessor"]["collection_id"]
        here = col.shard_dir_of(Path(root), name) / "codex.json"
        if here.is_file() and cx.loads(here.read_bytes()).collection_id == old_id:
            return cx.loads(here.read_bytes())
        aside = shard_aside(root, name, old_id) / "codex.json"
        if aside.is_file():
            return cx.loads(aside.read_bytes())
        raise CodexError(f"refit: {marker_path(root, name)} names {old_id} and neither {here} nor {aside} "
                         f"holds it; nothing was written")
    return col.read(Path(root), name)


def _rename(src: Path, dst: Path) -> bool:
    """`src` to `dst` by one rename, never over a file: True when it moved,
    False when `src` is gone and `dst` holds it already (a resumed step)."""
    if not src.exists() and not src.is_symlink():
        if dst.exists():
            return False
        raise CodexError(f"refit: {src} is gone and {dst} does not hold it; nothing more was moved")
    if dst.exists():
        raise CodexError(f"refit: {dst} exists already; nothing is renamed over it")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return True


# ---- the rows saved before the switch -----------------------------------------

def _enc(v):
    return {"$b": v.hex()} if isinstance(v, (bytes, bytearray, memoryview)) else v


def _dec(v):
    return bytes.fromhex(v["$b"]) if isinstance(v, dict) and "$b" in v else v


def _in(col: str, values: list) -> tuple[str, list]:
    return f"{col} IN ({','.join('?' for _ in values)})", list(values)


def _selections(con, changed: list[list], old: Codex) -> list[tuple[str, str, list]]:
    """`(table, where, params)` for every manifest row the switch changes or
    removes: the change set's documents (their marks), their chunk hashes,
    imported-chunk and queue rows; the old space's coverage rows and its
    gate, share and publication control rows; the space pin."""
    from . import carry as carry_mod
    from app.share import coverage as cov
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out: list[tuple[str, str, list]] = []
    for k in range(0, len(changed), 500):
        win = changed[k:k + 500]
        rowids, uids, books = [c[0] for c in win], [c[1] for c in win], [c[2] for c in win]
        out.append(("documents", *_in("rowid", rowids)))
        if "chunk_hashes" in tables:
            out.append(("chunk_hashes", *_in("native_id", books)))
        for t in ("chunk_verification", "embed_queue"):
            if t in tables:
                out.append((t, *_in("uid", uids)))
        if "verify_queue" in tables:
            for uid in uids:                 # a chunk id is `<uid>:<seq>:<version>`: its key's range
                out.append(("verify_queue", "chunk_id >= ? AND chunk_id < ?", [f"{uid}:", f"{uid};"]))
    prefix = cov.shard_id(old, "")
    like = prefix.replace("%", r"\%").replace("_", r"\_") + "%"
    for t in ("coverage", "coverage_shards"):
        if t in tables:
            out.append((t, "shard LIKE ? ESCAPE '\\'", [like]))
    keys = list(carry_mod.SISTER_CONTROL)
    out.append(("control", f"key IN ({','.join('?' for _ in keys)})"
                + "".join(" OR key LIKE ?" for _ in carry_mod.SISTER_CONTROL_PREFIXES),
                keys + [p + "%" for p in carry_mod.SISTER_CONTROL_PREFIXES]))
    out.append(("space", "id = 1", []))
    return out


def _save_rows(manifest: Path, path: Path, changed: list[list], old: Codex) -> dict[str, int]:
    """Every row `_selections` names, as it is, into `path` (a small SQLite
    file of its own, one row per saved row). Written beside and renamed: a
    stop leaves none or the whole."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{manifest.as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    out = sqlite3.connect(tmp)
    counts: dict[str, int] = {}
    try:
        out.execute("CREATE TABLE saved(tbl TEXT NOT NULL, row TEXT NOT NULL)")
        for table, where, params in _selections(src, changed, old):
            cols = "rowid AS _rowid, *" if table == "documents" else "*"
            for r in src.execute(f"SELECT {cols} FROM {table} WHERE {where}", params):
                out.execute("INSERT INTO saved VALUES(?, ?)",
                            (table, json.dumps({k: _enc(r[k]) for k in r.keys()}, sort_keys=True)))
                counts[table] = counts.get(table, 0) + 1
        out.commit()
    finally:
        src.close()
        out.close()
    os.replace(tmp, path)
    return counts


def _restore_rows(manifest: Path, path: Path, changed: list[list], old: Codex, new: Codex) -> dict[str, int]:
    """The saved rows back, delete-then-insert by the same selections (the
    documents' by rowid, their marks only: a document row was never
    removed), the new space's coverage rows gone. One BEGIN IMMEDIATE."""
    from app import db
    from app.share import coverage as cov
    saved = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = [(t, json.loads(r)) for t, r in saved.execute("SELECT tbl, row FROM saved")]
    finally:
        saved.close()
    con = sqlite3.connect(manifest, timeout=60, isolation_level=None)
    con.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        db.wal(con)                                     # invariant 6: a manifest is WAL
        con.execute("BEGIN IMMEDIATE")
        try:
            for table, where, params in _selections(con, changed, old):
                if table != "documents":
                    con.execute(f"DELETE FROM {table} WHERE {where}", params)
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            like = cov.shard_id(new, "").replace("%", r"\%").replace("_", r"\_") + "%"
            for t in ("coverage", "coverage_shards"):
                if t in tables:
                    con.execute(f"DELETE FROM {t} WHERE shard LIKE ? ESCAPE '\\'", (like,))
            for table, row in rows:
                vals = {k: _dec(v) for k, v in row.items()}
                if table == "documents":
                    rowid = vals.pop("_rowid")
                    con.execute(f"UPDATE documents SET {', '.join(f'{k}=?' for k in vals)} WHERE rowid=?",
                                [*vals.values(), rowid])
                else:
                    con.execute(f"INSERT OR REPLACE INTO {table}({', '.join(vals)}) "
                                f"VALUES({', '.join('?' for _ in vals)})", list(vals.values()))
                counts[table] = counts.get(table, 0) + 1
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return counts


# ---- the reset (carry.py's three repeatable steps) -----------------------------

def _commit(con, fn) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        fn()
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise


def _reset(con, changed: list[list]) -> int:
    """Step (a): the changed documents' marks emptied as a relayered sister's
    are (hold/carry.py `RELAYERED_MARKS`) and `embed_done` 0 (a declined
    document's -1 kept); their chunk-hash, imported-chunk and queue rows
    dropped (their chunks are gone): the worker cuts, indexes and embeds
    them again. Inside the caller's transaction. Repeatable."""
    from . import carry as carry_mod
    if not changed:
        return 0
    rowids, uids, books = [c[0] for c in changed], [c[1] for c in changed], [c[2] for c in changed]
    n = con.execute(
        f"UPDATE documents SET embed_done=CASE WHEN embed_done>0 THEN 0 ELSE embed_done END, "
        f"{carry_mod.RELAYERED_MARKS} WHERE rowid IN ({','.join('?' for _ in rowids)})", rowids).rowcount
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "chunk_hashes" in tables:
        con.execute(f"DELETE FROM chunk_hashes WHERE native_id IN ({','.join('?' for _ in books)})", books)
    for t in ("chunk_verification", "embed_queue"):
        if t in tables:
            con.execute(f"DELETE FROM {t} WHERE uid IN ({','.join('?' for _ in uids)})", uids)
    if "verify_queue" in tables:
        for uid in uids:
            con.execute("DELETE FROM verify_queue WHERE chunk_id >= ? AND chunk_id < ?", (f"{uid}:", f"{uid};"))
    return n


def _unversion(con, changed: list[list]) -> int:
    """Step (c): `embed_version` 0 for the changed documents, after their rows
    went. Inside the caller's transaction. Repeatable."""
    if not changed:
        return 0
    rowids = [c[0] for c in changed]
    return con.execute(f"UPDATE documents SET embed_version=0 WHERE rowid IN ({','.join('?' for _ in rowids)})",
                       rowids).rowcount


# ---- the vector tables' versions ----------------------------------------------

def _tables(coll) -> list:
    """`[(its directory, the table)]` for every vector table of the collection
    (one, or a split collection's parts), the ones holding a table."""
    from app import embeddings, vector_parts
    out = []
    for view in vector_parts.views(coll):
        db = embeddings._conn(view)
        if embeddings.TABLE in db.table_names():
            out.append((str(view.lance_dir), db.open_table(embeddings.TABLE)))
    return out


def lance_versions(coll) -> dict[str, int]:
    return {d: int(t.version) for d, t in _tables(coll)}


def _restore_lance(coll, versions: dict[str, int], log) -> dict[str, str]:
    """Each table back to the version recorded before the switch deleted a
    row (Lance's own `restore`: a new version whose data is that one's)."""
    out = {}
    for d, t in _tables(coll):
        want = versions.get(d)
        if want is None:
            continue
        if int(t.version) == int(want):
            out[d] = "at its version"
            continue
        t.restore(int(want))
        out[d] = f"restored to version {want}"
        log(f"[refit] {coll.name}: vector table {d} restored to version {want}")
    return out


# ---- the parts ----------------------------------------------------------------

def _lines_at(t_coll, path: Path):
    """`(raw line, record, layer, chunk contributions)` for every record of the
    part at `path`, its chunks cut by `t_coll`'s layers resolved once for the
    part (`pack.build._record_from_line`, what a build does for a line it
    takes)."""
    from app import layers as layers_mod
    from app.pack import build as pack_build, records as pack_records
    if pack_records.is_parquet(path):
        raise CodexError(f"refit: {path} is a Parquet part: a refit of one is not built yet (its cells "
                         f"are written from the record, not kept line by line); nothing more was written")
    token = pack_build._task_layers.set(layers_mod.Resolved(t_coll))
    try:
        index = pack_records.read_index(path)
        for k in range(len(index.frames)):
            for raw in pack_records.read_frame(path, index, k):
                rec, layer, blob, _plates = pack_build._record_from_line(t_coll, json.loads(raw))
                yield bytes(raw), rec, layer, blob
    finally:
        pack_build._task_layers.reset(token)


def rebuild_part(t_coll, src: str, dst: str) -> dict[str, Any]:
    """The part at `src` written again at `dst` for the NEW layers: every
    record line kept byte for byte (a line holds no layer, 09 §9.5), each
    record's chunks counted and its chunk contributions taken by the new
    layers, so its index, its chunk count, its `chunks_root` and its pieces
    root are the ones a build of the new Codex writes. Returns the
    build-database row's fields. Module-level, so a task of the parse pool."""
    from app.pack import records as pack_records
    from . import records as hold_records, shard as shard_mod
    record_line = int(getattr(t_coll.codex, "record_line", 1) or 1)
    recs, lines, blobs = [], [], []
    layers: dict[int, int] = {}
    for raw, rec, layer, blob in _lines_at(t_coll, Path(src)):
        recs.append({"uid": rec["uid"], "native_id": rec.get("native_id"), "chunks": rec["chunks"],
                     "content_hash": rec.get("content_hash")})
        lines.append(raw + b"\n")
        blobs.append(blob)
        layers[layer] = layers.get(layer, 0) + 1
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    info = pack_records.write_shard(Path(dst), recs, record_line=record_line, lines=lines)
    return {"records": info.records, "chunks": info.chunks, "bytes": info.bytes, "sha256": info.sha256,
            "first_uid": info.first_uid, "last_uid": info.last_uid, "id_min": info.id_min,
            "id_max": info.id_max, "layers": json.dumps(layers, sort_keys=True),
            "root": shard_mod.pieces_root_of(Path(dst)),
            "records_root": hold_records.records_root(r["content_hash"] for r in recs),
            "chunks_root": hold_records.chunks_root_of(b"".join(blobs))}


def part_chunks(t_coll, path: str) -> tuple[int, str]:
    """`(chunks, chunks_root)` of the part at `path` cut by `t_coll`'s layers,
    nothing written: the proof's re-computation of an unchanged part. A task
    of the parse pool."""
    from . import records as hold_records
    n, blobs = 0, []
    for _raw, rec, _layer, blob in _lines_at(t_coll, Path(path)):
        n += int(rec["chunks"])
        blobs.append(blob)
    return n, hold_records.chunks_root_of(b"".join(blobs))


# ---- the dry run ----------------------------------------------------------------

def plan(root, name: str, layers, *, window: int = WINDOW, processes: int | None = None,
         log: Callable[[str], None] = print) -> dict[str, Any]:
    """The whole change-set pass and the parts holding it, nothing written,
    no lock taken: what `refit --dry-run` prints."""
    from . import records as rec, seed
    root = Path(root)
    old = predecessor(root, name)
    new = new_codex(old, layers)
    s_coll = rec.local_collection(root, name, old)        # its data is used: gated (invariant 9)
    t_coll = planned_target(s_coll, new)
    changed: list[list] = []
    counts = {"documents": 0, "errors": 0, "first_error": None}

    def each(_hi, got):
        changed.extend(got["changed"])
        counts["documents"] += int(got["documents"])
        counts["errors"] += int(got["errors"])
        counts["first_error"] = counts["first_error"] or got.get("first_error")
    _changes(s_coll, t_coll, start=0, window=window, processes=processes, stop=None, each=each, log=log)
    parts: list[dict] = []
    snapshot = None
    try:
        snap = seed.latest_snapshot(root, name)
    except CodexError:
        snap = None
    if snap is not None and snap.collection_id == old.collection_id:
        from . import collection as col
        parts = parts_holding(col.shard_dir_of(root, name), snap, [c[1] for c in changed])
        snapshot = snap.snapshot_id
    return {"name": name, "from": old.collection_id, "to": new.collection_id, "space_from": old.space_id,
            "space_to": new.space_id, "version": [old.version, new.version], "documents": counts["documents"],
            "changed": len(changed), "changed_uids": [c[1] for c in changed[:SHOWN]],
            "errors": counts["errors"], "first_error": counts["first_error"], "parts": parts,
            "snapshot": snapshot, "shard_aside": str(shard_aside(root, name, old.collection_id)),
            "state_aside": str(state_aside(root, name, old.collection_id)), "codex": new}


# ---- the run --------------------------------------------------------------------

def _changed(path: Path) -> list[list]:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def run(root, name: str, layers, *, window: int = WINDOW, processes: int | None = None,
        sample: int = SAMPLE, part_sample: int = PART_SAMPLE, stop=None, now: str | None = None,
        paused_here: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The refit, from wherever `state/refit.json` says it stopped. The caller
    holds the collection's write lock and has paused it (`paused_here`: the
    command did, so it resumes it at the end, recorded for a resumed run).
    Returns the marker as it ends; `stopped` when a stop was honoured."""
    from app import db, embeddings
    from . import carry as carry_mod, codex as cx, collection as col, records as rec
    root = Path(root)
    carry_mod.refuse_without_locks(root, (name,), "hold collection refit")
    old = predecessor(root, name)
    new = new_codex(old, layers)
    m = read_marker(root, name)
    if m is not None and m.get("done"):
        if m["to"]["collection_id"] == new.collection_id:
            log(f"[refit] {name}: done already ({new.collection_id[:23]}…)")
            return m
        # an EARLIER refit, finished: its marker joins what it set aside, out of
        # this one's way (the collection it made is this one's predecessor)
        _rename(marker_path(root, name), Path(m["state_aside"]) / MARKER)
        m = None
    if m is not None and m["to"]["collection_id"] != new.collection_id:
        raise CodexError(f"refit: {marker_path(root, name)} records a refit to {m['to']['collection_id']}; "
                         f"these params make {new.collection_id}. Finish that one, or roll it back "
                         f"(`--rollback`); nothing was written")
    sa, ta = shard_aside(root, name, old.collection_id), state_aside(root, name, old.collection_id)
    if m is None:
        for d in (sa, ta):
            if d.exists():
                raise CodexError(f"refit: {d} exists and no refit is recorded in progress; move it aside "
                                 f"first. Nothing was written")
        m = {"kind": "louise-refit", "name": name,
             "predecessor": {"name": name, "collection_id": old.collection_id, "space_id": old.space_id},
             "to": {"collection_id": new.collection_id, "space_id": new.space_id, "version": new.version},
             "layers": [l.to_dict() for l in new.layers], "shard_aside": str(sa), "state_aside": str(ta),
             "started": int(time.time()), "steps": {}, "done": False, "paused_here": bool(paused_here)}
        ta.mkdir(parents=True, exist_ok=True)
        _save(root, name, m)
        log(f"[refit] {name}: {old.collection_id[:23]}… → {new.collection_id[:23]}… in place; what the old "
            f"Codex owned is set aside under {sa} and {ta}")
    steps = m["steps"]
    changed_file = ta / "changed.jsonl"

    # ---- changes (the old Codex in place; nothing of the collection written)
    ch = steps.get("changes") or {"rowid": 0, "documents": 0, "changed": 0, "errors": 0, "first_error": None,
                                  "done": False}
    if not ch.get("done"):
        if old.collection_id != col.read(root, name).collection_id:
            raise CodexError("refit: the change set is computed before the switch, and the switch has begun")
        s_coll = rec.local_collection(root, name, old)
        t_coll = planned_target(s_coll, new)
        kept = [c for c in _changed(changed_file) if c[0] <= int(ch["rowid"])]
        changed_file.write_text("".join(json.dumps(c) + "\n" for c in kept), encoding="utf-8")

        def each(hi, got):
            if got["changed"]:
                with changed_file.open("a", encoding="utf-8") as f:
                    f.write("".join(json.dumps(c) + "\n" for c in got["changed"]))
            ch.update(rowid=hi, documents=int(ch["documents"]) + int(got["documents"]),
                      changed=int(ch["changed"]) + len(got["changed"]),
                      errors=int(ch["errors"]) + int(got["errors"]),
                      first_error=ch.get("first_error") or got.get("first_error"))
            steps["changes"] = ch
            _save(root, name, m)
        whole = _changes(s_coll, t_coll, start=int(ch["rowid"]), window=window, processes=processes,
                         stop=stop, each=each, log=log)
        if not whole:
            return {**m, "stopped": True}
        changed = _changed(changed_file)
        from . import seed
        try:
            snap = seed.latest_snapshot(root, name)
        except CodexError:
            snap = None
        parts = (parts_holding(col.shard_dir_of(root, name), snap, [c[1] for c in changed])
                 if snap is not None and snap.collection_id == old.collection_id else [])
        held_uids = {u for p in parts for u in p["uids"]}
        ch.update(done=True, shown=[c[1] for c in changed[:SHOWN]], parts=parts,
                  snapshot=snap.snapshot_id if snap is not None else None,
                  not_in_parts=len([c for c in changed if c[1] not in held_uids]))
        steps["changes"] = ch
        _save(root, name, m)
        log(f"[refit] {name}: {ch['documents']} document(s) compared, {ch['changed']} cut otherwise by the "
            f"encoder window" + (f" ({', '.join(ch['shown'])}{' …' if ch['changed'] > SHOWN else ''})"
                                 if ch["changed"] else "")
            + f"; {len(parts)} part(s) hold them" + (f"; {ch['errors']} could not be cut ({ch['first_error']}): "
                                                    f"reset too" if ch["errors"] else ""))
    changed = _changed(changed_file)
    manifest = Path(root) / name / "state" / "manifest.sqlite"

    # ---- switch
    sw = steps.get("switch") or {}
    if not sw.get("done"):
        s_now = rec._local_collection(root, name, old)
        if not sw.get("saved"):
            sw["rows"] = _save_rows(manifest, ta / "saved.sqlite", changed, old)
            sw["lance"] = lance_versions(s_now)
            sw["saved"] = True
            steps["switch"] = sw
            _save(root, name, m)
            log(f"[refit] {name}: saved before the switch — rows {sw['rows']}, vector table version(s) "
                f"{sw['lance']}")
        if not sw.get("codex"):
            shard = col.shard_dir_of(root, name)
            here = shard / "codex.json"
            if here.is_file() and cx.loads(here.read_bytes()).collection_id == old.collection_id:
                _rename(here, sa / "codex.json")
            if (shard / "snapshots").exists():
                _rename(shard / "snapshots", sa / "snapshots")
            if not here.is_file():
                tmp = here.with_name("codex.json.refit")
                tmp.write_bytes(new.canonical())
                os.replace(tmp, here)
            if cx.loads(here.read_bytes()).collection_id != new.collection_id:
                raise CodexError(f"refit: {here} holds {cx.loads(here.read_bytes()).collection_id}, neither "
                                 f"the old Codex nor the new one; nothing more was written")
            sw["codex"] = True
            steps["switch"] = sw
            _save(root, name, m)
            log(f"[refit] {name}: the old codex.json and snapshots set aside in {sa}; the new Codex written")
        if not sw.get("staging"):
            build = Path(root) / name / rec.STATE / rec.BUILD
            if build.exists():
                _rename(build, ta / "build")
            sw["staging"] = True
            steps["switch"] = sw
            _save(root, name, m)
        if not sw.get("file"):
            sw["file"] = _recodex_file(root, name, new.collection_id, ta, log)
            steps["switch"] = sw
            _save(root, name, m)
        if not sw.get("repin"):
            cleared = carry_mod._repin(manifest, old, new)
            gone = _drop_space_control(manifest)
            sw["repin"] = {"coverage_rows_cleared": cleared, "control_rows": gone}
            steps["switch"] = sw
            _save(root, name, m)
            log(f"[refit] {name}: the manifest re-pinned to {new.space_id[:23]}…; {cleared} coverage row(s) "
                f"and {gone} gate, share or publication row(s) of the old space gone (saved)")
        t_coll = rec._local_collection(root, name, new)
        at = int(sw.get("reset_at") or 0)
        con = db.connect(t_coll)
        try:
            for k in range(at, len(changed), max(1, int(window))):
                win = changed[k:k + max(1, int(window))]
                # (a) the marks, (b) the rows, outside any transaction (a Lance
                # write), (c) the versions: a stop between them finds the window
                # again, each step repeatable (carry.py `keep_producer`'s order)
                _commit(con, lambda: _reset(con, win))
                embeddings.delete_books([c[2] for c in win], t_coll, lock_con=con)
                _commit(con, lambda: _unversion(con, win))
                sw["reset_at"] = k + len(win)
                steps["switch"] = sw
                _save(root, name, m)
        finally:
            con.close()
        sw["done"] = True
        steps["switch"] = sw
        _save(root, name, m)
        log(f"[refit] {name}: {len(changed)} document(s) reset (their marks, chunk hashes and rows): the "
            f"worker cuts, indexes and embeds them at the new layer")
    t_coll = rec.local_collection(root, name, new)

    # ---- parts
    pt = steps.get("parts") or {}
    if not pt.get("done"):
        if stop is not None and stop.is_set():
            return {**m, "stopped": True}
        got = _parts(root, name, old, new, t_coll, ch_parts=steps["changes"].get("parts") or [], pt=pt,
                     save=lambda: (steps.__setitem__("parts", pt), _save(root, name, m)), stop=stop, now=now,
                     log=log)
        if got == "stopped":
            return {**m, "stopped": True}
        pt["done"] = True
        steps["parts"] = pt
        _save(root, name, m)

    # ---- proof
    pf = steps.get("proof") or {}
    if not pf.get("done"):
        pf = _proof(root, name, old, new, t_coll, changed, steps, sample=sample, part_sample=part_sample)
        if pf["failures"]:
            steps["proof"] = pf
            _save(root, name, m)
            log(f"[refit] {name}: THE PROOF FAILED — {'; '.join(pf['failures'][:5])}; rolling back")
            rollback(root, name, log=log)
            raise RefitFailed(f"refit: {name}: the proof failed ({'; '.join(pf['failures'][:3])}); rolled "
                              f"back: {name} holds {old.collection_id} as before")
        pf["done"] = True
        steps["proof"] = pf
        m["lance_after"] = lance_versions(t_coll)
        m["done"] = True
        m["finished"] = int(time.time())
        _save(root, name, m)
        log(f"[refit] {name}: proven — {pf['compared']} unchanged embedded document(s) cut to exactly the "
            f"chunks the vector table holds, {pf['parts_compared']} unchanged part(s) to their chunk roots, "
            f"the change set holding no row")
    return m


def _recodex_file(root, name: str, collection_id: str, ta: Path, log) -> str:
    """The collection file's `codex` line to the new id through
    app/collection_file.py, its old text kept in `ta` first."""
    from app import collection_file
    from . import deposit
    path = deposit.held_file(Path(root), name)
    if not path.is_file():
        log(f"[refit] {name}: no collection file at {path}; none rewritten")
        return f"none at {path}"
    kept = ta / f"{name}.toml"
    if not kept.is_file():
        tmp = kept.with_name(kept.name + ".tmp")
        tmp.write_bytes(path.read_bytes())
        os.replace(tmp, kept)
    said = collection_file.recodex(path, collection_id)
    log(f"[refit] {name}: {path}: {said}; its old text kept in {kept}")
    return said


def _drop_space_control(manifest: Path) -> int:
    """The control rows that speak of the OLD space (carry.py `SISTER_CONTROL`:
    its gate's last verdict, what its share paths fetched and served, its
    publications), saved before, gone in one BEGIN IMMEDIATE. Repeatable."""
    from app import db
    from . import carry as carry_mod
    con = sqlite3.connect(manifest, timeout=60, isolation_level=None)
    try:
        db.wal(con)
        con.execute("BEGIN IMMEDIATE")
        try:
            n = con.execute(
                f"DELETE FROM control WHERE key IN ({','.join('?' for _ in carry_mod.SISTER_CONTROL)})"
                + "".join(" OR key LIKE ?" for _ in carry_mod.SISTER_CONTROL_PREFIXES),
                (*carry_mod.SISTER_CONTROL, *(p + "%" for p in carry_mod.SISTER_CONTROL_PREFIXES))).rowcount
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return n


def _parts(root: Path, name: str, old: Codex, new: Codex, t_coll, *, ch_parts: list[dict], pt: dict,
           save, stop, now, log) -> str | None:
    """The parts step: the staging build database for the new Codex (the old
    one's rows of its last snapshot), the parts holding a changed document
    written again (each old version set aside), the new Codex's first
    snapshot, held to the old one entry by entry, signed where a key is."""
    from app.pack import build as pack_build
    from . import collection as col, records as rec, seed, snapshot as sn
    sa, ta = shard_aside(root, name, old.collection_id), state_aside(root, name, old.collection_id)
    snaps = sorted(p for p in (sa / "snapshots").glob("*.json") if not p.name.endswith(".sig.json")) \
        if (sa / "snapshots").is_dir() else []
    if not snaps:
        log(f"[refit] {name}: the old Codex froze no snapshot: no part to write again")
        pt["snapshot"] = None
        return None
    prev = sn.load(snaps[-1])
    staging = Path(root) / name / rec.STATE / rec.BUILD
    db_path = staging / pack_build.BUILD_DIR / pack_build.BUILD_DB
    if not pt.get("staging"):
        old_db = ta / "build" / pack_build.BUILD_DIR / pack_build.BUILD_DB
        rec._claim_staging(staging, new)
        pt["build_db"] = seed._write_build_db(old_db, db_path, prev)
        pt["staging"] = True
        save()
    shard = col.shard_dir_of(root, name)
    done = set(pt.get("rebuilt_parts") or [])
    todo = [p for p in ch_parts if p["path"] not in done]
    tasks = []
    for p in todo:
        here, kept = shard / p["path"], sa / "parts" / p["path"]
        tmp = here.with_name(f".{here.name}.refit")
        tmp.unlink(missing_ok=True)
        tasks.append((t_coll, str(kept if kept.is_file() else here), str(tmp)))
    from app import parallel
    from app.hold import refit as me                       # by name: a spawned child imports it
    results = parallel.each_part(me.rebuild_part, tasks) if tasks else iter(())
    try:
        for p, (_c, _src, tmp_s), row in zip(todo, tasks, results):
            here, kept, tmp = shard / p["path"], sa / "parts" / p["path"], Path(tmp_s)
            was = next(s for s in prev.shards if s.slice == p["slice"] and int(s.n) == int(p["n"]))
            if row["records_root"] != was.records_root or row["records"] != was.records:
                tmp.unlink(missing_ok=True)
                raise CodexError(f"refit: {p['path']} written again holds other records ({row['records']}, "
                                 f"{row['records_root'][:23]}…) than the old snapshot lists ({was.records}, "
                                 f"{was.records_root[:23]}…); nothing more was written")
            if not kept.is_file():
                _rename(here, kept)
            os.replace(tmp, here)
            con = sqlite3.connect(db_path, isolation_level=None)
            try:
                con.execute("BEGIN IMMEDIATE")
                n = con.execute(
                    "UPDATE shards SET records=?, chunks=?, bytes=?, sha256=?, first_uid=?, last_uid=?, "
                    "id_min=?, id_max=?, layers=?, written_at=?, root=?, records_root=?, chunks_root=? "
                    "WHERE slice=? AND n=?",
                    (row["records"], row["chunks"], row["bytes"], row["sha256"], row["first_uid"],
                     row["last_uid"], row["id_min"], row["id_max"], row["layers"], int(time.time()),
                     row["root"], row["records_root"], row["chunks_root"], p["slice"], int(p["n"]))).rowcount
                if n != 1:
                    raise CodexError(f"refit: the build database holds no row for {p['path']}")
                con.execute("COMMIT")
            finally:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                con.close()
            done.add(p["path"])
            pt["rebuilt_parts"] = sorted(done)
            save()
            log(f"[refit] {name}: {p['path']} written again ({was.chunks} → {row['chunks']} chunks); its "
                f"old version set aside in {kept}")
            if stop is not None and stop.is_set() and len(done) < len(ch_parts):
                log(f"[refit] {name}: stopped between parts; run it again to resume")
                return "stopped"
    finally:
        close = getattr(results, "close", None)
        if close is not None:
            close()
    rec.write_validation(root, name)
    snap = sn.create(root, name, snapshot_id=prev.snapshot_id, writers_from=prev, log=log)
    bad = _held_to(prev, snap, {(p["slice"], int(p["n"])) for p in ch_parts})
    if bad:
        raise CodexError(f"refit: the new snapshot does not hold the old one's parts as it must: "
                         f"{'; '.join(bad[:5])}")
    pt["facets"] = _rekey_ask_marker(root, name, prev, snap, new, ta, log)
    signed = 0
    try:
        from app.bundles import keys as node_keys
        key = node_keys.load_signing_key()
    except Exception as e:                              # noqa: BLE001 — an unsigned peer still works
        log(f"[refit] {name}: snapshot {snap.snapshot_id} unsigned — {e}")
    else:
        from datetime import datetime, timezone
        signed = len(sn.sign_snapshot(root, name, snap, key,
                                      now=now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                     ["signatures"])
    pt["snapshot"] = {"snapshot_id": snap.snapshot_id, "hash": snap.hash, "parts": len(snap.shards),
                      "rebuilt": len(ch_parts), "records": snap.records, "chunks": snap.chunks,
                      "chunks_before": prev.chunks, "signatures": signed}
    save()
    log(f"[refit] {name}: snapshot {snap.snapshot_id} for the new Codex — {len(snap.shards)} part(s), "
        f"{len(ch_parts)} written again, {snap.records} records, {prev.chunks} → {snap.chunks} chunks")
    return None


def _rekey_ask_marker(root: Path, name: str, prev, snap, new: Codex, ta: Path, log) -> str:
    """The ask indexes' marker (app/ask/facets.py: the snapshot whose facets,
    edges and content hashes the worker's maintainer indexed whole) re-keyed
    from the old snapshot to the new one, where it named the old one: all
    three are functions of the records and the relation parts, which the
    refit keeps byte for byte, and the facets the Codex declares are the
    same (`facets_digest`). Without it the maintainer would count the whole
    collection again for the same answers (RULE 1). The marker's old bytes
    kept in `ta` for the rollback."""
    from app.ask import facets as ask_facets
    index_dir = Path(root) / name / "index"
    path = ask_facets.marker_path(index_dir)
    if ask_facets.read_marker(index_dir) == (snap.snapshot_id, snap.hash):
        return "re-keyed (already)"
    if ask_facets.read_marker(index_dir) != (prev.snapshot_id, prev.hash) or \
            ask_facets.marker_facets(index_dir) not in (None, ask_facets.facets_digest(new)):
        return "not re-keyed: it named another snapshot; the maintainer indexes the new one"
    kept = ta / "ask-marker.json"
    if not kept.is_file():
        tmp = kept.with_name(kept.name + ".tmp")
        tmp.write_bytes(path.read_bytes())
        os.replace(tmp, kept)
    ask_facets.write_marker(index_dir, snap.snapshot_id, snap.hash, facets=ask_facets.facets_digest(new))
    log(f"[refit] {name}: the ask indexes' marker re-keyed to snapshot {snap.hash[:23]}… (the same records)")
    return "re-keyed"


#: what an unchanged part's entry must keep of the old one's
_SAME = ("root", "records_root", "first_id", "last_id", "records", "chunks", "chunks_root", "tokens",
         "bytes", "writer")
#: and a part written again
_SAME_RECORDS = ("records_root", "first_id", "last_id", "records")


def _held_to(prev, snap, rebuilt: set[tuple[str, int]]) -> list[str]:
    have = {(s.slice, int(s.n)): s for s in snap.shards}
    want = {(s.slice, int(s.n)): s for s in prev.shards}
    bad = [f"{k[0]}/{k[1]}: {'missing' if k not in have else 'extra'}" for k in sorted(set(have) ^ set(want))]
    for k in sorted(set(have) & set(want)):
        for f in (_SAME_RECORDS if k in rebuilt else _SAME):
            if getattr(have[k], f) != getattr(want[k], f):
                bad.append(f"{k[0]}/{k[1]}: {f} {getattr(have[k], f)!r} != {getattr(want[k], f)!r}")
    if [(r.slice, r.n, r.root) for r in snap.relations] != [(r.slice, r.n, r.root) for r in prev.relations]:
        bad.append("relations differ")
    if [(f.slice, f.n, f.root) for f in snap.figures] != [(f.slice, f.n, f.root) for f in prev.figures]:
        bad.append("figures differ")
    return bad


def _proof(root: Path, name: str, old: Codex, new: Codex, t_coll, changed: list[list], steps: dict, *,
           sample: int, part_sample: int) -> dict[str, Any]:
    """A seeded sample of unchanged embedded documents cut at the new layers
    against the vector table (`carry._resign_proof`); a seeded sample of
    unchanged parts cut again against their entries; no row held for the
    change set."""
    from app import db, embeddings
    from . import carry as carry_mod, collection as col, seed
    failures: list[str] = []
    con = db.connect(t_coll)
    try:
        docs = carry_mod._resign_proof(t_coll, con, seed=new.collection_id, n=int(sample))
    finally:
        con.close()
    if docs["mismatched"]:
        failures.append(f"{len(docs['mismatched'])} of {docs['compared']} sampled document(s) cut to chunks "
                        f"the vector table does not hold ({', '.join(docs['mismatched'][:5])})")
    held = embeddings.held_chunks(t_coll, [c[2] for c in changed]) if changed else None
    still = [c[1] for c in changed if held is not None and held.of(c[2])]
    if still:
        failures.append(f"{len(still)} changed document(s) still hold rows ({', '.join(still[:5])})")
    parts_compared = 0
    snap = None
    if (steps.get("parts") or {}).get("snapshot"):
        snap = seed.latest_snapshot(root, name)
        rebuilt = {(p["slice"], int(p["n"])) for p in (steps["changes"].get("parts") or [])}
        pool = [s for s in snap.shards if (s.slice, int(s.n)) not in rebuilt]
        rng = random.Random(new.collection_id)
        shard = col.shard_dir_of(root, name)
        picked = rng.sample(pool, min(int(part_sample), len(pool)))
        from app import parallel
        from app.hold import refit as me                   # by name: a spawned child imports it
        for s, (n, chunks_root) in zip(picked, parallel.each_part(
                me.part_chunks, [(t_coll, str(shard / s.path)) for s in picked])):
            parts_compared += 1
            if (n, chunks_root) != (int(s.chunks), s.chunks_root):
                failures.append(f"{s.path}: cut again at the new layers it gives {n} chunks "
                                f"({chunks_root[:23]}…), its entry {s.chunks} ({s.chunks_root[:23]}…)")
    return {"compared": docs["compared"], "sampled": docs["sampled"], "chunks": docs["chunks"],
            "without_vectors": docs["without_vectors"], "mismatched": docs["mismatched"],
            "parts_compared": parts_compared, "changed_without_rows": len(changed) - len(still),
            "failures": failures}


# ---- the rollback ---------------------------------------------------------------

def rollback(root, name: str, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Everything the refit changed put back, from whatever step it reached:
    the parts' old versions and the old snapshots, codex.json and staging
    back by rename (what the new Codex wrote set aside beside them under
    `rejected/`, never deleted), the vector tables restored to their
    recorded versions, the saved manifest rows back, the space re-pinned and
    the collection file's old text. The caller holds the write lock."""
    from app import collection_file  # noqa: F401 — the file is restored as its writer wrote it
    from . import carry as carry_mod, codex as cx, collection as col, deposit, records as rec
    root = Path(root)
    m = read_marker(root, name)
    if m is None or m.get("rolled_back"):
        raise CodexError(f"refit: {name} records no refit to roll back ({marker_path(root, name)})")
    carry_mod.refuse_without_locks(root, (name,), "hold collection refit --rollback")
    old_id, new_id = m["predecessor"]["collection_id"], m["to"]["collection_id"]
    if m.get("done"):
        # A FINISHED refit is rolled back only while nothing wrote since: the
        # worker embeds into the new space once it is resumed, and restoring
        # the tables' versions then would drop its rows under marks that say
        # embedded
        now_coll = rec._local_collection(root, name, col.read(root, name))
        if lance_versions(now_coll) != (m.get("lance_after") or {}):
            raise CodexError(f"refit --rollback: {name}'s vector table was written since the refit ended "
                             f"({m.get('lance_after')} → {lance_versions(now_coll)}): rolling back would lose "
                             f"those rows. A refit back to the old window is the way; nothing was changed")
    sa, ta = Path(m["shard_aside"]), Path(m["state_aside"])
    rejected_s, rejected_t = sa / "rejected", ta / "rejected"
    shard = col.shard_dir_of(root, name)
    steps = m.get("steps") or {}
    out: dict[str, Any] = {}
    # the old Codex, from where it is
    here = shard / "codex.json"
    old_cx = cx.loads((sa / "codex.json").read_bytes()) if (sa / "codex.json").is_file() else \
        cx.loads(here.read_bytes())
    if old_cx.collection_id != old_id:
        raise CodexError(f"refit: the Codex set aside is {old_cx.collection_id}, the marker names {old_id}")
    new_cx = cx.loads(here.read_bytes()) if here.is_file() and \
        cx.loads(here.read_bytes()).collection_id == new_id else None
    # the parts
    for p in sorted((sa / "parts").rglob("*")) if (sa / "parts").is_dir() else []:
        if not p.is_file():
            continue
        rel = p.relative_to(sa / "parts")
        if (shard / rel).is_file():
            _rename(shard / rel, rejected_s / "parts" / rel)
        _rename(p, shard / rel)
    for p in (steps.get("changes") or {}).get("parts") or []:
        (shard / p["path"]).with_name(f".{Path(p['path']).name}.refit").unlink(missing_ok=True)   # our own temp
    out["parts"] = len(steps.get("parts", {}).get("rebuilt_parts") or [])
    # the snapshots and the Codex
    if (sa / "snapshots").exists():
        if (shard / "snapshots").exists():
            _rename(shard / "snapshots", rejected_s / "snapshots")
        _rename(sa / "snapshots", shard / "snapshots")
    if (sa / "codex.json").is_file():
        if here.is_file():
            _rename(here, rejected_s / "codex.json")
        _rename(sa / "codex.json", here)
    # the staging build
    build = root / name / rec.STATE / rec.BUILD
    if (ta / "build").exists():
        if build.exists():
            _rename(build, rejected_t / "build")
        _rename(ta / "build", build)
    elif build.exists() and (steps.get("parts") or {}).get("staging"):
        _rename(build, rejected_t / "build")
    # the vectors, the manifest's rows, the pin
    coll = rec._local_collection(root, name, old_cx)
    sw = steps.get("switch") or {}
    if sw.get("lance"):
        out["lance"] = _restore_lance(coll, sw["lance"], log)
    manifest = root / name / "state" / "manifest.sqlite"
    if sw.get("saved") and (ta / "saved.sqlite").is_file():
        changed = _changed(ta / "changed.jsonl")
        out["rows"] = _restore_rows(manifest, ta / "saved.sqlite", changed, old_cx,
                                    new_cx or dataclasses.replace(old_cx))
    # the ask indexes' marker
    if (ta / "ask-marker.json").is_file():
        from app.ask import facets as ask_facets
        marker = ask_facets.marker_path(root / name / "index")
        tmp = marker.with_name(f".{marker.name}.refit")
        tmp.write_bytes((ta / "ask-marker.json").read_bytes())
        os.replace(tmp, marker)
        out["ask_marker"] = "restored"
    # the collection file
    kept = ta / f"{name}.toml"
    path = deposit.held_file(root, name)
    if kept.is_file() and (not path.is_file() or path.read_bytes() != kept.read_bytes()):
        tmp = path.with_name(path.name + ".refit")
        tmp.write_bytes(kept.read_bytes())
        os.replace(tmp, path)
        out["file"] = "restored"
    m["rolled_back"] = True
    m["rolled_back_at"] = int(time.time())
    m["rollback"] = out
    _save(root, name, m)
    # the folders set aside, and the marker with them, out of the next run's way
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    for d in (sa, ta):
        if d.exists():
            _rename(d, d.with_name(f"{d.name}.rolled-back-{stamp}"))
    _rename(marker_path(root, name), ta.with_name(f"{ta.name}.rolled-back-{stamp}") / MARKER)
    log(f"[refit] {name}: rolled back — it holds {old_id[:23]}… as before ({out})")
    return out
