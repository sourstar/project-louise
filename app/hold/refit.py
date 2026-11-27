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
the way the build computes it. THE NODE'S /data IS ZFS, snapshotted every
hour (München, 2026-11-27): a ZFS snapshot taken before the run is the
node's practice and the operator's host step (`--dry-run` says so) — a
whole-filesystem rollback beside this command's own, which rolls back one
collection while the node runs its others. A rollback BY the filesystem was
not taken as this command's: it reaches every collection on the dataset,
and a container cannot take one.

WHAT WAS SHARED UNDER THE OLD ID (the operator, 2026-11-27: "Look the other
peer was a test peer and Gutenberg has been shared only for test purpose").
Every collection on this node has been shared for testing only (RULE 1: no
official publication so far), so a refit SUPERSEDES what was shared and
never refuses for it: pin-run's checks list each item
(`pin_run.published_under`), the dry run and the output say each one, and
the rows and files are set aside under state/.replaced/<old id>/ and put
back by a rollback; every file naming the old id (pin-run's `_sweep`) is
re-keyed or set aside. The peers holding the old id keep it, and see a new
collection. Once something is published OFFICIALLY the refit refuses, as
pin-run does — the one condition, `official_publication`.

QUARANTINE. Attestations are keyed on the collection's id (copy_state), so
the new id has none: the `attest` step runs `roots`, `records` and `chunks`
on the parts written again (`hold check`'s `attest_landed`, scoped to
parts), still paused, and signs them with this node's key — the unchanged
parts are the old snapshot's byte for byte, proven entry by entry.
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
STEPS = ("changes", "switch", "parts", "proof", "attest")
#: the collection file's old text, kept (by its role, not its name: a rename
#: moves the collection and this folder with it)
KEPT_FILE = "collection.toml"
#: the files naming the old id set aside or kept, at their paths from the
#: collection's directory
FILES = "files"


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
    """One task of the pass: EVERY document of rowids (lo, hi] — whatever its
    error or its scope: cutting asks no encoder, and a document whose cut
    moves must be reset and its part written again even when it holds an
    error or is out of scope now (the independent read of e492ebaf, 1) — cut
    at the SOURCE's and the TARGET's layers, as the stages cut them
    (`record_source.title_sections_and_source`, `layers.chunks_for_document`).
    Returns the changed documents (rowid, uid, native id), the counts, never a
    text. A document that cannot be cut is counted CHANGED: nothing proves its
    vectors are the new layer's, so the worker meets it as by the book.
    Module-level, so it pickles; reads the manifest read-only."""
    from app import db, layers, record_source
    con = db.read_only_connect(source)
    if con is None:
        return {"changed": [], "documents": 0, "errors": 0, "first_error": None, "errored": 0,
                "out_of_scope": 0}
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT rowid, * FROM documents WHERE rowid > ? AND rowid <= ? ORDER BY rowid", (lo, hi))]
    finally:
        con.close()
    plugin = source.plugin_instance()
    pair = layers.has_pair_layer(source)
    changed: list[list] = []
    documents = errors = errored = out_of_scope = 0
    first_error = None
    for row in rows:
        errored += row.get("error") is not None
        out_of_scope += not row.get("in_scope")
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
    return {"changed": changed, "documents": documents, "errors": errors, "first_error": first_error,
            "errored": errored, "out_of_scope": out_of_scope}


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
    top = _top(s_coll)
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


def _top(coll) -> int:
    """The manifest's MAX(rowid): where the pass ends, and what the switch
    holds the change set to (a document added since was not compared)."""
    con = sqlite3.connect(f"file:{Path(coll.manifest_path).as_posix()}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0)
    finally:
        con.close()


def cut_part(s_coll, t_coll, path: str, chunks: int, chunks_root: str) -> dict[str, Any]:
    """One task of the PARTS pass: the part's own records cut at the new
    layers and held to its entry (`chunks`, `chunks_root`, which the old
    layers gave). A part that differs is one the refit writes again, chosen
    from the parts themselves and not from the manifest alone (the
    independent read of e492ebaf, 1); its records are then cut at both layers
    to name the documents whose cut moved. Module-level, so it pickles."""
    from . import records as hold_records
    n, blobs = 0, []
    for _raw, rec, _layer, blob in _lines_at(t_coll, Path(path)):
        n += int(rec["chunks"])
        blobs.append(blob)
    root = hold_records.chunks_root_of(b"".join(blobs))
    if (n, root) == (int(chunks), chunks_root):
        return {"differs": False}
    old = {rec["uid"]: (int(rec["chunks"]), blob) for _raw, rec, _l, blob in _lines_at(s_coll, Path(path))}
    new = {rec["uid"]: (int(rec["chunks"]), blob) for _raw, rec, _l, blob in _lines_at(t_coll, Path(path))}
    return {"differs": True, "chunks": n, "chunks_root": root,
            "uids": sorted(u for u in new if old.get(u) != new[u])}


def _parts_pass(s_coll, t_coll, shard_dir: Path, snap, *, start: int, processes: int | None, stop,
                each: Callable[[int, dict, dict], None], log: Callable[[str], None]) -> bool:
    """Every record part of `snap` from part `start`, in the parse pool:
    `each(k, part, got)` per part, in order. False when a stop was honoured
    between parts."""
    from app import parallel
    from . import rechunk
    cores, says = rechunk._processes(processes)
    shards = list(snap.shards)[int(start):]
    if not shards:
        return True
    log(f"[refit] {s_coll.name}: {len(shards)} part(s) cut again at the new layers and held to their "
        f"entries, by at most {cores or parallel.processes()} process(es) ({says})")
    from app.hold import refit as me
    tasks = [(s_coll, t_coll, str(Path(shard_dir) / s.path), int(s.chunks), s.chunks_root) for s in shards]
    results = parallel.each_part(me.cut_part, tasks, limit=cores)
    try:
        for k, (s, got) in enumerate(zip(shards, results), int(start) + 1):
            each(k, {"slice": s.slice, "n": int(s.n), "path": s.path}, got)
            if stop is not None and stop.is_set() and k < len(snap.shards):
                log(f"[refit] {s_coll.name}: stopped after part {k}; run it again to resume")
                return False
    finally:
        close = getattr(results, "close", None)
        if close is not None:
            close()
    return True


# ---- the change set's file: one validated line a document ------------------------

def _line(c: list) -> str:
    return json.dumps([int(c[0]), str(c[1]), int(c[2])], separators=(",", ":")) + "\n"


def read_changed(path: Path) -> list[list]:
    """The change set, each line validated: `[rowid, uid, native id]` and a
    newline. A torn LAST line (a stop inside an append) is dropped — the
    cursor never counted its window; any other bad line refuses."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    tail = lines.pop()                       # "" when the file ends with its newline
    out: list[list] = []
    for k, l in enumerate(lines):
        try:
            v = json.loads(l)
            ok = (isinstance(v, list) and len(v) == 3 and isinstance(v[0], int) and isinstance(v[1], str)
                  and isinstance(v[2], int))
        except ValueError:
            ok = False
        if not ok:
            raise CodexError(f"refit: {path} line {k + 1} is not a change-set line ({l[:80]!r}); nothing "
                             f"more was written")
        out.append(v)
    del tail                                 # torn: its window was never counted
    return out


def _write_changed(path: Path, changed: list[list]) -> None:
    """The whole change set, written beside and renamed over (atomic)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(_line(c) for c in changed), encoding="utf-8")
    os.replace(tmp, path)


def _append_changed(path: Path, changed: list[list]) -> None:
    if changed:
        with path.open("a", encoding="utf-8") as f:
            f.write("".join(_line(c) for c in changed))
            f.flush()
            os.fsync(f.fileno())


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


def asides(root, name: str, m: dict) -> tuple[Path, Path]:
    """The marker's two folders set aside, from the collection's directory:
    stored relative to it (`hold collection rename` moves the directory and
    refit.json with it), an absolute path read as it is (a marker written
    before)."""
    base = Path(root) / name
    return tuple(Path(m[k]) if Path(m[k]).is_absolute() else base / m[k]   # type: ignore[return-value]
                 for k in ("shard_aside", "state_aside"))


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


def _place(tmp: Path, path: Path) -> None:
    """A file written beside its path, renamed over it (atomic)."""
    os.replace(tmp, path)


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
    document's -1 kept), their error and retries cleared (a document the
    refit cuts again is met by the stages afresh, the independent read of
    e492ebaf, 1); their chunk-hash, imported-chunk and queue rows dropped
    (their chunks are gone): the worker cuts, indexes and embeds them again.
    Inside the caller's transaction. Repeatable."""
    from . import carry as carry_mod
    if not changed:
        return 0
    rowids, uids, books = [c[0] for c in changed], [c[1] for c in changed], [c[2] for c in changed]
    n = con.execute(
        f"UPDATE documents SET embed_done=CASE WHEN embed_done>0 THEN 0 ELSE embed_done END, "
        f"error=NULL, retries=0, "
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
    (one, or a split collection's parts), the ones holding a table; the
    directory from the collection's own folder, so a rename keeps the
    recorded versions' keys (`hold collection rename`)."""
    from app import embeddings, vector_parts
    base = Path(coll.manifest_path).parent.parent
    out = []
    for view in vector_parts.views(coll):
        db = embeddings._conn(view)
        if embeddings.TABLE in db.table_names():
            out.append((os.path.relpath(str(view.lance_dir), str(base)), db.open_table(embeddings.TABLE)))
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


# ---- the text index ---------------------------------------------------------------

def shared_index_refusal(root, name: str) -> str | None:
    """Why the collection cannot be refit in place, or None: a SHARED text
    index (app/text_index.py `placement`, `.text-index/<identity>`) holds the
    members' documents once, and its identity includes the layers — a refit
    of one member would leave the others reading or writing an index cut at
    another layer (the independent read of e492ebaf, 2). A group refit is not
    built; none exists on the node (München, 2026-11-27)."""
    from app import text_index
    from . import collection as col
    mine = (text_index.placement(Path(root), name) or {}).get("dir")
    if not mine:
        return None
    others = sorted(n for n, _c in col._held(Path(root))
                    if n != name and (text_index.placement(Path(root), n) or {}).get("dir") == mine)
    if not others:
        return None
    return (f"{name}'s text index is shared with {', '.join(others)} ({mine}): its identity includes the "
            f"layers, so a refit of {name} alone would leave {'them' if len(others) > 1 else 'it'} on an index "
            f"cut at another layer. A refit of a group is not built; nothing was written")


def _by_group(coll, changed: list[list]) -> dict[str, list[int]]:
    """The change set's native ids by the text-index groups each may sit in
    (`fts.groups_for_lang`, a `mul` record in several)."""
    from app import fts
    con = sqlite3.connect(f"file:{Path(coll.manifest_path).as_posix()}?mode=ro", uri=True)
    try:
        langs = {}
        for k in range(0, len(changed), 500):
            win = [c[0] for c in changed[k:k + 500]]
            langs.update({int(r[0]): r[1] for r in con.execute(
                f"SELECT rowid, lang FROM documents WHERE rowid IN ({','.join('?' for _ in win)})", win)})
    finally:
        con.close()
    out: dict[str, list[int]] = {}
    for c in changed:
        for g in fts.groups_for_lang(coll, langs.get(int(c[0]))):
            out.setdefault(g, []).append(int(c[2]))
    return out


def index_held(coll, changed: list[list]) -> int:
    """How many entries the collection's text index holds for the change set."""
    from app import fts
    return sum(fts.books_held(g, ids, coll) for g, ids in _by_group(coll, changed).items())


def _index_writers(coll, changed: list[list]) -> list[str]:
    """The groups holding entries of the change set, each with its writer
    TAKEN by this process (tantivy: one writer a directory). Refused, exit 3,
    when another process holds one: the worker keeps its index writers for
    its life (app/fts.py `_writer`), so the refit asks for a stop, not for
    code that works around it (CLAUDE.md RULE 1)."""
    from app import fts
    groups = [g for g, ids in _by_group(coll, changed).items() if fts.books_held(g, ids, coll)]
    for g in groups:
        try:
            fts._writer((str(fts.base_of(coll)), g), fts.get_index(g, coll))
        except Exception as e:   # noqa: BLE001 — tantivy's LockBusy, said as the refusal
            _release_index_writers(coll)
            err = CodexError(f"refit: {coll.name}'s text index ({g}) is written by another process — the "
                             f"worker keeps its index writer ({type(e).__name__}: {str(e)[:120]}). The refit "
                             f"deletes the change set's index entries itself: `louise node stop`, then run "
                             f"this again (RULE 1: a tool that needs the worker's writers asks for a stop). "
                             f"Nothing of the switch was written")
            err.exit_code = 3                     # type: ignore[attr-defined]
            raise err from None
    return groups


def _drop_index_entries(coll, changed: list[list]) -> int:
    """The change set's entries gone from the text index (delete by book id,
    the index stage's own key: its re-index is delete-then-insert by it),
    through the writers `_index_writers` took. Repeatable."""
    from app import fts
    n = 0
    for g, ids in _by_group(coll, changed).items():
        if fts.books_held(g, ids, coll):
            n += fts.delete_books(g, ids, coll)
    return n


def _release_index_writers(coll) -> None:
    """This process lets go of the collection's index writers, so the worker
    takes them again once it is resumed (`fts.close_all`'s way, for one
    collection's directory)."""
    from app import fts
    base = str(fts.base_of(coll))
    with fts._lock:
        mine = [(k, fts._writers.pop(k)) for k in list(fts._writers) if k[0] == base]
    for _k, (writer, lock) in mine:
        with lock:
            writer.wait_merging_threads()


# ---- what other code reads of a refit -----------------------------------------------

def predecessors(state_dir) -> tuple[str, ...]:
    """The collections whose records this one holds byte for byte BY A REFIT
    in place: the predecessor `state/refit.json` names while it is not rolled
    back (from its first step: before the switch it is this collection's own
    id), and every earlier refit's, kept with what it set aside. An
    annotations collection about a predecessor applies to these records as a
    carry's twin's does (hold/carry.py `records_twins_of`, 02 §2.4 A1f; the
    independent read of e492ebaf, 6: gutenberg-book-editions names
    gutenberg's pre-refit id)."""
    state_dir = Path(state_dir)
    found = [state_dir / MARKER] + sorted(p / MARKER for p in (state_dir / ASIDE).glob("*")
                                         if p.is_dir() and ".rolled-back-" not in p.name) \
        if (state_dir / ASIDE).is_dir() else [state_dir / MARKER]
    out = []
    for p in found:
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("kind") == "louise-refit" and not m.get("rolled_back"):
            out.append(str(m["predecessor"]["collection_id"]))
    return tuple(dict.fromkeys(out))


def is_predecessor(state_dir, collection_id: str) -> bool:
    """Whether a refit in place here replaced `collection_id`: the collection
    held here IS that one, under a new Codex (ranked as itself, not as a
    twin)."""
    return collection_id in predecessors(state_dir)


def gated(m: dict | None) -> bool:
    """Whether a finished refit's new space passed its gate."""
    return bool(m) and (m.get("gate") or {}).get("rc") == 0


def note_gate(root, name: str, rc: int) -> None:
    """The CPU reference and gate's outcome, in the marker: a refit done and
    not gated goes straight to them when the command is run again (the
    independent read of e492ebaf, 4)."""
    m = read_marker(root, name)
    if m is None:
        return
    m["gate"] = {"rc": int(rc), "at": int(time.time())}
    _save(root, name, m)


# ---- what was shared under the old id, and every file naming it ----------------

def official_publication(root, name: str, codex: Codex) -> str | None:
    """THE ONE CONDITION between superseding what was shared and refusing
    (the operator, 2026-11-27: "Look the other peer was a test peer and
    Gutenberg has been shared only for test purpose"; RULE 1: no official
    publication so far). Every collection on this node has been shared for
    testing only, so a refit supersedes what was shared and never refuses for
    it. Published OFFICIALLY — a certified collection, or one a certifier's
    pointer or anchor names (03 §3.7) — it refuses, as pin-run does. The
    reason in words, or None."""
    from .anchor import state_path as anchor_path
    from .certify import pointer_path
    if getattr(codex, "publication", "open") == "certified":
        return f"{name} is certified (publication = \"certified\"): an official publication is superseded, " \
               f"never refit in place"
    if pointer_path(Path(root), name, codex).is_file():
        return f"a certifier's pointer names {name}: it is published officially"
    if anchor_path(Path(root), name, codex).is_file():
        return f"a certifier's anchor names {name}: it is published officially"
    return None


def published(root, name: str, codex: Codex) -> list[str]:
    """What this node shared or published under the collection's id, each
    item in words: pin-run's checks, one function (`pin_run.published_under`)."""
    from . import deposit, pin_run
    file = deposit.held_file(Path(root), name)
    if not file.is_file():
        return []
    return pin_run.published_under(Path(root), name, codex, file)


def _refuse_official(root, name: str, codex: Codex) -> None:
    why = official_publication(root, name, codex)
    if why:
        raise CodexError(f"refit: {why}; nothing was written")


#: the node database's rows about the old id that the switch sets aside
def _node_selections(name: str, old_id: str) -> list[tuple[str, str, list]]:
    return [("control", "key = ?", [f"publish:{name}"]),
            ("attestations_published", "collection_id = ?", [old_id])]


def _node_rows_aside(name: str, old_id: str, path: Path) -> dict[str, int]:
    """node.sqlite's rows about the old id — the publisher's pass of the
    name, the attestations it published of the id — saved into `path`, then
    deleted, one BEGIN IMMEDIATE. Repeatable (saved once)."""
    from app import node_state
    db_path = node_state.path()
    if not db_path.exists():
        return {}
    con = sqlite3.connect(db_path, timeout=60, isolation_level=None)
    con.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = []
        for t, where, params in _node_selections(name, old_id):
            if t in tables:
                rows += [(t, json.dumps({k: _enc(r[k]) for k in r.keys()}, sort_keys=True))
                         for r in con.execute(f"SELECT * FROM {t} WHERE {where}", params)]
        if not path.is_file():
            tmp = path.with_name(path.name + ".tmp")
            tmp.unlink(missing_ok=True)
            out = sqlite3.connect(tmp)
            out.execute("CREATE TABLE saved(tbl TEXT NOT NULL, row TEXT NOT NULL)")
            out.executemany("INSERT INTO saved VALUES(?, ?)", rows)
            out.commit()
            out.close()
            os.replace(tmp, path)
        con.execute("BEGIN IMMEDIATE")
        try:
            for t, where, params in _node_selections(name, old_id):
                if t in tables:
                    counts[t] = con.execute(f"DELETE FROM {t} WHERE {where}", params).rowcount
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return {k: v for k, v in counts.items() if v}


def _node_rows_back(path: Path) -> int:
    """The saved node.sqlite rows back (insert or replace). Repeatable."""
    from app import node_state
    if not path.is_file() or not node_state.path().exists():
        return 0
    saved = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = [(t, json.loads(r)) for t, r in saved.execute("SELECT tbl, row FROM saved")]
    finally:
        saved.close()
    con = sqlite3.connect(node_state.path(), timeout=60, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            for t, row in rows:
                vals = {k: _dec(v) for k, v in row.items()}
                con.execute(f"INSERT OR REPLACE INTO {t}({', '.join(vals)}) VALUES({', '.join('?' for _ in vals)})",
                            list(vals.values()))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return len(rows)


def _ours(rel: str) -> bool:
    """A file this pass handles itself, or keeps: not the sweep's."""
    return (rel in ("shard/codex.json", f"state/{MARKER}", f"state/{MARKER}.tmp")
            or rel.startswith(("shard/snapshots/", f"shard/{ASIDE}/", f"state/{ASIDE}/", "state/build/")))


def names_plan(root, name: str, old_id: str) -> list[dict[str, str]]:
    """Every file of the collection naming the old id (pin-run's `_sweep`),
    each with what the switch does with it — set aside, re-pointed to the
    new id, or signed again for it — and refused in words where none applies
    (or a RAGE settings file another peer signed, or an update under way).
    `[{rel, action, why}]`, read-only."""
    from . import carry as carry_mod, line_carry, pin_run, update_state
    from .cascade import PUBLISHED_DIR
    from app.share.fetch import CHAIN
    root = Path(root)
    hexid = old_id.split(":", 1)[-1]
    out: list[dict[str, str]] = []
    unknown: list[str] = []
    dirs: set[str] = set()
    aside_files = {f"state/{pin_run._ORIGIN_TRIED}", f"state/{pin_run._ORIGIN_ACK}", "state/calibration.json",
                   *(f"state/{f}" for f in pin_run._SCHEDULED)}
    for rel in pin_run._sweep(root, name, old_id):
        if _ours(rel):
            continue
        if rel.startswith(f"state/attestations/{hexid}/"):
            d = f"state/attestations/{hexid}"
            if d not in dirs:
                dirs.add(d)
                out.append({"rel": d, "action": "aside", "why": "the attestations of the old id"})
        elif rel.startswith("shard/rage-settings/") and rel.endswith(".json"):
            try:
                from app.bundles import keys as node_keys
                key = node_keys.load_signing_key()
            except Exception:   # noqa: BLE001 — refused below in words
                key = None
            pin_run._resignable(root / name / rel, key)
            out.append({"rel": rel, "action": "resign", "why": "this node's RAGE settings, signed again for the "
                                                               "new id (refused for it otherwise)"})
        elif rel in (f"state/{carry_mod.MARKER}", f"state/{line_carry.MARKER}"):
            out.append({"rel": rel, "action": "repoint", "why": "a carry's record: `to` names the new id"})
        elif rel == f"state/{update_state.FILE}":
            mk = update_state.read(root, name) or {}
            if not (mk.get("step") == "due" and not mk.get("moved")):
                raise CodexError(f"refit: {name}'s update of the old Codex is under way (step {mk.get('step')!r}, "
                                 f"state/{update_state.FILE}): finish or abandon it first; nothing was written")
            out.append({"rel": rel, "action": "aside", "why": "an update due that had done nothing"})
        elif (rel in aside_files or rel.startswith((f"state/{PUBLISHED_DIR}/", "state/calibration/",
                                                    f"state/{CHAIN}/"))):
            out.append({"rel": rel, "action": "aside", "why": "what the old id's checks, gate, publication or "
                                                              "fetch wrote"})
        else:
            unknown.append(rel)
    if unknown:
        raise CodexError(f"refit: {', '.join(unknown[:5])} name(s) {old_id} and the refit does not know what "
                         f"to do with it; nothing was written")
    return out


def _apply_names(root: Path, name: str, items: list[dict[str, str]], done: list[str], old_id: str,
                 new_id: str, ta: Path, save) -> None:
    """Each file naming the old id handled as `names_plan` said, its old bytes
    kept under `ta/files/<rel>` (set aside by rename, or copied before a
    re-point or a signature), each recorded in `done`."""
    from . import pin_run
    base = Path(root) / name
    for it in items:
        rel = it["rel"]
        if rel in done:
            continue
        here, kept = base / rel, ta / FILES / rel
        if it["action"] == "aside":
            if here.exists():
                _rename(here, kept)
        else:
            if not kept.exists() and here.is_file():
                kept.parent.mkdir(parents=True, exist_ok=True)
                tmp = kept.with_name(kept.name + ".tmp")
                tmp.write_bytes(here.read_bytes())
                os.replace(tmp, kept)
            if it["action"] == "repoint":
                pin_run._repoint_json(here, old_id, new_id)
            else:
                from app.bundles import keys as node_keys
                pin_run._resign_rage(here, old_id, new_id, node_keys.load_signing_key())
        done.append(rel)
        save()


def _names_back(root: Path, name: str, items: list[dict[str, str]], ta: Path, rejected_t: Path) -> int:
    """Each file the switch set aside or rewrote put back: what the new Codex
    wrote at its path set aside under `rejected/`, never deleted. Each move
    decided by what lies where (a re-run finishes a stopped one)."""
    base = Path(root) / name
    n = 0
    for it in reversed(items):
        rel = it["rel"]
        here, kept = base / rel, ta / FILES / rel
        if not kept.exists():
            continue
        if it["action"] == "aside":
            if here.exists():
                _rename(here, rejected_t / FILES / rel)
            _rename(kept, here)
        else:
            tmp = here.with_name(here.name + ".refit")
            tmp.write_bytes(kept.read_bytes())
            os.replace(tmp, here)
            kept.unlink()                                  # its bytes are back at their path
        n += 1
    return n


def _attest(root: Path, name: str, new: Codex, t_coll, steps: dict, log) -> dict[str, Any]:
    """03 §3.6's test 1 for the NEW id, still paused: `roots`, `records` and
    `chunks` over the parts written again (a seeded unchanged one where none
    was), signed with this node's key (`check.attest_landed`, scoped to
    parts). Attestations are keyed on the id, so the new one has none until
    this runs, and an enforced quarantine would withhold it from search."""
    from . import check as chk, equivalence, seed
    if not (steps.get("parts") or {}).get("snapshot"):
        log(f"[refit] {name}: it holds no snapshot, so there is no part to check or attest; its quarantine "
            f"waits for its first snapshot's checks, as any collection's")
        return {"parts": [], "results": {}, "attestations": {}, "failed": [],
                "why": "the collection holds no snapshot: nothing to attest"}
    snap = seed.latest_snapshot(root, name)
    parts = [p["path"] for p in (steps.get("changes") or {}).get("parts") or []]
    if not parts and snap.shards:
        parts = [random.Random(new.collection_id).choice(list(snap.shards)).path]
    try:
        from app.bundles import keys as node_keys
        key = node_keys.load_signing_key()
    except Exception:   # noqa: BLE001 — checked, not attested; said
        key = None
    got = chk.attest_landed(root, name, new, snap, parts, key=key, platform_image=equivalence.running_image())
    failed = sorted(c for c, r in got["results"].items() if r == "fail")
    out = {"parts": got["parts"], "results": got["results"], "attestations": got["attestations"],
           "why": got["why"], "failed": failed}
    log(f"[refit] {name}: roots, records and chunks of {len(got['parts'])} part(s) for the new id: "
        + ", ".join(f"{k} {v.upper()}" for k, v in got["results"].items())
        + (f"; attested ({len(got['attestations'])})" if got["attestations"] else
           f"; NOT attested — {got['why']}: an enforced quarantine withholds {name} until `louise hold check "
           f"{name} --checks roots,chunks` runs with the node's key"))
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

def staging_refusal(root: Path, name: str, snap) -> str | None:
    """Why the parts cannot be written again, or None: the staging build
    database must describe the old snapshot — its rows are the new snapshot's
    entries for every part kept (`seed._write_build_db`). Asked before the
    pass, not after the switch (the second read of 10cc6f34, 6)."""
    from app.pack import build as pack_build
    from . import records as rec, seed
    if snap is None:
        return None
    db_path = Path(root) / name / rec.STATE / rec.BUILD / pack_build.BUILD_DIR / pack_build.BUILD_DB
    if not db_path.is_file():
        return (f"{name} holds no staging build database ({db_path}): its parts were not built here, so "
                f"nothing says how they were cut; a refit writes parts again from a build's rows")
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        bad = seed._mismatch(seed._rows(con), snap)
    finally:
        con.close()
    if bad:
        return (f"{db_path} does not describe {name}'s snapshot {snap.snapshot_id} ({'; '.join(bad[:3])}): "
                f"the parts' entries cannot be taken from it")
    return None


def _attest_readiness() -> str:
    """Whether this process can sign the new id's attestations, in words."""
    from . import equivalence
    try:
        from app.bundles import keys as node_keys
        node_keys.load_signing_key()
    except Exception as e:   # noqa: BLE001
        return f"NOT here: no node key ({type(e).__name__})"
    if equivalence.running_image() is None:
        return "NOT here: this process does not know its image (LOUISE_PLATFORM_IMAGE)"
    return "yes: the node's key and image"


def annotations_naming(root, name: str, old_id: str) -> list[str]:
    """The held annotations collections whose targets name `old_id`: each is
    retargeted (`louise hold annotations retarget <name>`) once the refit is
    done; until then its claims apply to the records as a twin's."""
    from . import collection as col
    from .codex import targets_of
    return sorted(n for n, c in col._held(Path(root)) if n != name and getattr(c, "annotates", None) is not None
                  and any(t.collection_id == old_id for t in targets_of(c)))


def _old_snapshot(root: Path, name: str, old: Codex):
    """The old Codex's latest snapshot while it is in place, else None."""
    from . import seed
    try:
        snap = seed.latest_snapshot(root, name)
    except CodexError:
        return None
    return snap if snap.collection_id == old.collection_id else None


def plan(root, name: str, layers, *, window: int = WINDOW, processes: int | None = None,
         log: Callable[[str], None] = print) -> dict[str, Any]:
    """Both passes — every document cut at both layers, every part cut again
    — and nothing written, no lock taken: what `refit --dry-run` prints."""
    from . import collection as col, records as rec
    root = Path(root)
    why = shared_index_refusal(root, name)
    if why:
        raise CodexError(f"refit: {why}")
    old = predecessor(root, name)
    new = new_codex(old, layers)
    _refuse_official(root, name, old)
    shared = published(root, name, old)
    why = staging_refusal(root, name, _old_snapshot(root, name, old))
    if why:
        raise CodexError(f"refit: {why}; nothing was written")
    files = names_plan(root, name, old.collection_id)
    s_coll = rec.local_collection(root, name, old)        # its data is used: gated (invariant 9)
    t_coll = planned_target(s_coll, new)
    changed: list[list] = []
    counts = {"documents": 0, "errors": 0, "first_error": None, "errored": 0, "out_of_scope": 0}

    def each(_hi, got):
        changed.extend(got["changed"])
        for k in ("documents", "errors", "errored", "out_of_scope"):
            counts[k] += int(got.get(k) or 0)
        counts["first_error"] = counts["first_error"] or got.get("first_error")
    _changes(s_coll, t_coll, start=0, window=window, processes=processes, stop=None, each=each, log=log)
    parts: list[dict] = []
    snap = _old_snapshot(root, name, old)
    if snap is not None:
        _parts_pass(s_coll, t_coll, col.shard_dir_of(root, name), snap, start=0, processes=processes,
                    stop=None, each=lambda _k, p, got: parts.append({**p, **got}) if got["differs"] else None,
                    log=log)
    held = {c[1] for c in changed}
    return {"name": name, "from": old.collection_id, "to": new.collection_id, "space_from": old.space_id,
            "space_to": new.space_id, "version": [old.version, new.version], **counts,
            "changed": len(changed), "changed_uids": [c[1] for c in changed[:SHOWN]],
            "parts": parts, "parts_only": sorted({u for p in parts for u in p["uids"]} - held)[:SHOWN],
            "snapshot": snap.snapshot_id if snap is not None else None,
            "index_entries": index_held(s_coll, changed), "published": shared, "names": files,
            "attest": _attest_readiness(), "annotations": annotations_naming(root, name, old.collection_id),
            "shard_aside": str(shard_aside(root, name, old.collection_id)),
            "state_aside": str(state_aside(root, name, old.collection_id)), "codex": new}


# ---- the run --------------------------------------------------------------------

def _abandon(root: Path, name: str, ta: Path) -> None:
    """A refit that changes nothing leaves nothing: the change-set file, the
    marker and the folder this run made for them, its own bookkeeping (no
    byte of the collection was written)."""
    for p in (ta / "changed.jsonl", ta / "changed.jsonl.tmp"):
        p.unlink(missing_ok=True)
    for d in (ta, ta.parent):
        try:
            d.rmdir()
        except OSError:
            pass
    marker_path(root, name).unlink(missing_ok=True)


def _changes_step(root: Path, name: str, old: Codex, new: Codex, m: dict, *, window: int,
                  processes: int | None, stop, log) -> list[list] | None:
    """The `changes` step, from its cursors: the manifest pass, then the
    parts pass, then the parts' documents joined to the change set. None
    when a stop was honoured. Refused, leaving nothing, when nothing is cut
    otherwise."""
    from . import collection as col, records as rec
    steps, ta = m["steps"], asides(root, name, m)[1]
    changed_file = ta / "changed.jsonl"
    ch = steps.get("changes") or {"rowid": 0, "documents": 0, "changed": 0, "errors": 0, "first_error": None,
                                  "errored": 0, "out_of_scope": 0, "parts_at": 0, "parts": [],
                                  "manifest_done": False, "done": False}
    if ch.get("done"):
        return read_changed(changed_file)
    if old.collection_id != col.read(root, name).collection_id:
        raise CodexError("refit: the change set is computed before the switch, and the switch has begun")
    why = staging_refusal(root, name, _old_snapshot(root, name, old))
    if why:
        raise CodexError(f"refit: {why}; nothing of the switch was written")
    s_coll = rec.local_collection(root, name, old)
    t_coll = planned_target(s_coll, new)
    if not ch.get("manifest_done"):
        # a resumed pass: the lines its cursor counted, nothing torn (atomic)
        _write_changed(changed_file, [c for c in read_changed(changed_file) if c[0] <= int(ch["rowid"])])

        def each(hi, got):
            _append_changed(changed_file, got["changed"])
            ch.update(rowid=hi, changed=int(ch["changed"]) + len(got["changed"]),
                      first_error=ch.get("first_error") or got.get("first_error"))
            for k in ("documents", "errors", "errored", "out_of_scope"):
                ch[k] = int(ch.get(k) or 0) + int(got.get(k) or 0)
            steps["changes"] = ch
            _save(root, name, m)
        if not _changes(s_coll, t_coll, start=int(ch["rowid"]), window=window, processes=processes,
                        stop=stop, each=each, log=log):
            return None
        ch["manifest_done"] = True
        steps["changes"] = ch
        _save(root, name, m)
    snap = _old_snapshot(root, name, old)
    if snap is not None and int(ch.get("parts_at") or 0) < len(snap.shards):
        def each_part(k, part, got):
            if got["differs"]:
                ch["parts"] = [*ch["parts"], {**part, **{x: got[x] for x in ("uids", "chunks", "chunks_root")}}]
            ch["parts_at"] = k
            steps["changes"] = ch
            _save(root, name, m)
        if not _parts_pass(s_coll, t_coll, col.shard_dir_of(root, name), snap, start=int(ch.get("parts_at") or 0),
                           processes=processes, stop=stop, each=each_part, log=log):
            return None
    changed = read_changed(changed_file)
    # the documents the PARTS name whose cut moved, joined where the manifest
    # pass did not find them (the parts are what the refit writes again)
    extra = sorted({u for p in ch["parts"] for u in p["uids"]} - {c[1] for c in changed})
    found: list[list] = []
    if extra:
        con = sqlite3.connect(f"file:{Path(s_coll.manifest_path).as_posix()}?mode=ro", uri=True)
        try:
            for k in range(0, len(extra), 500):
                win = extra[k:k + 500]
                found += [[int(r[0]), r[1], int(r[2])] for r in con.execute(
                    f"SELECT rowid, uid, native_id FROM documents WHERE uid IN ({','.join('?' for _ in win)})", win)]
        finally:
            con.close()
        changed = sorted(changed + found)
        _write_changed(changed_file, changed)
    if not changed and not ch["parts"]:
        _abandon(root, name, ta)
        raise CodexError(f"refit: {name}: the new window cuts no document otherwise ({ch['documents']} compared, "
                         f"every part cut again the same): there is nothing to refit, and nothing was written")
    ch.update(done=True, changed=len(changed), shown=[c[1] for c in changed[:SHOWN]],
              snapshot=snap.snapshot_id if snap is not None else None,
              from_parts=len(found), parts_only=sorted(set(extra) - {c[1] for c in found})[:SHOWN],
              # what the switch holds the change set to: a document added or a
              # row embedded since means it was computed on another collection
              top=_top(s_coll), lance=lance_versions(s_coll))
    steps["changes"] = ch
    _save(root, name, m)
    log(f"[refit] {name}: {ch['documents']} document(s) compared ({ch.get('errored', 0)} holding an error, "
        f"{ch.get('out_of_scope', 0)} out of scope, compared all the same), {len(changed)} cut otherwise by the "
        f"encoder window" + (f" ({', '.join(ch['shown'])}{' …' if len(changed) > SHOWN else ''})" if changed else "")
        + f"; {len(ch['parts'])} part(s) cut otherwise" + (f"; {ch['errors']} could not be cut "
                                                         f"({ch['first_error']}): reset too" if ch["errors"] else ""))
    return changed


def _moved_since(root: Path, name: str, old: Codex, ch: dict) -> str | None:
    """Why the change set is stale, or None: the manifest grew, or a vector
    table moved, since `changes` ended (a stop, a `node resume`, books
    embedded at the old layer meanwhile — the independent read of e492ebaf,
    7)."""
    from . import records as rec
    s_coll = rec._local_collection(root, name, old)
    top, lance = _top(s_coll), lance_versions(s_coll)
    if top != int(ch.get("top") or 0):
        return f"the manifest's last rowid moved {ch.get('top')} → {top}"
    if lance != (ch.get("lance") or {}):
        return f"the vector table moved {ch.get('lance')} → {lance}"
    return None


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
    why = shared_index_refusal(root, name)
    if why:
        raise CodexError(f"refit: {why}")
    m = read_marker(root, name)
    if m is not None and m.get("rolled_back"):
        _rollback_moves(root, name, m, log)               # a rollback stopped after its last write
        m = None
    old = predecessor(root, name)
    new = new_codex(old, layers)
    _refuse_official(root, name, old)
    if m is not None and m.get("done"):
        if m["to"]["collection_id"] == new.collection_id:
            log(f"[refit] {name}: done already ({new.collection_id[:23]}…)")
            return m
        # an EARLIER refit, finished: its marker joins what it set aside, out of
        # this one's way (the collection it made is this one's predecessor)
        _rename(marker_path(root, name), asides(root, name, m)[1] / MARKER)
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
             "layers": [l.to_dict() for l in new.layers], "shard_aside": str(sa.relative_to(root / name)), "state_aside": str(ta.relative_to(root / name)),
             "started": int(time.time()), "steps": {}, "done": False, "paused_here": bool(paused_here),
             # what was shared under the old id (pin-run's checks), superseded: the
             # node shared for testing only (`official_publication`)
             "published": published(root, name, old)}
        ta.mkdir(parents=True, exist_ok=True)
        _save(root, name, m)
        log(f"[refit] {name}: {old.collection_id[:23]}… → {new.collection_id[:23]}… in place; what the old "
            f"Codex owned is set aside under {sa} and {ta}")
        for item in m["published"]:
            log(f"[refit] {name}: shared under the old id — {item}: superseded (shared for testing only, "
                f"RULE 1); the peers holding the old id keep it and see a new collection")
    steps = m["steps"]
    changed_file = ta / "changed.jsonl"

    # ---- changes (the old Codex in place; nothing of the collection written),
    # held to the collection as it is when the switch begins
    while True:
        changed = _changes_step(root, name, old, new, m, window=window, processes=processes, stop=stop, log=log)
        if changed is None:
            return {**m, "stopped": True}
        if steps.get("switch"):
            break                                  # the switch began on this change set
        moved = _moved_since(root, name, old, steps["changes"])
        if moved is None:
            break
        log(f"[refit] {name}: the change set is stale — {moved}; computed again")
        steps.pop("changes", None)
        _write_changed(changed_file, [])
        _save(root, name, m)
    manifest = Path(root) / name / "state" / "manifest.sqlite"

    # ---- switch
    sw = steps.get("switch") or {}
    if not sw.get("done"):
        s_now = rec._local_collection(root, name, old)
        # the index writers FIRST, before any write: taken, or refused (exit 3)
        _index_writers(s_now, changed)
        try:
            if not sw.get("saved"):
                sw["names"] = names_plan(root, name, old.collection_id)
                sw["rows"] = _save_rows(manifest, ta / "saved.sqlite", changed, old)
                sw["lance"] = lance_versions(s_now)
                sw["outside"] = _rows_outside(s_now, changed)
                sw["top"] = _top(s_now)
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
                    raise CodexError(f"refit: {here} holds {cx.loads(here.read_bytes()).collection_id}, "
                                     f"neither the old Codex nor the new one; nothing more was written")
                sw["codex"] = True
                steps["switch"] = sw
                _save(root, name, m)
                log(f"[refit] {name}: the old codex.json and snapshots set aside in {sa}; the new Codex written")
            if not sw.get("named"):
                done_names = sw.setdefault("names_done", [])
                _apply_names(root, name, sw.get("names") or [], done_names, old.collection_id, new.collection_id,
                             ta, lambda: (steps.__setitem__("switch", sw), _save(root, name, m)))
                sw["node_rows"] = _node_rows_aside(name, old.collection_id, ta / "node-saved.sqlite")
                sw["named"] = True
                steps["switch"] = sw
                _save(root, name, m)
                if sw.get("names") or sw["node_rows"]:
                    log(f"[refit] {name}: naming the old id — "
                        + ", ".join(f"{it['rel']} {it['action']}" for it in sw.get("names") or [])
                        + (f"; node rows set aside {sw['node_rows']}" if sw["node_rows"] else ""))
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
                log(f"[refit] {name}: the manifest re-pinned to {new.space_id[:23]}…; {cleared} coverage "
                    f"row(s) and {gone} gate, share or publication row(s) of the old space gone (saved)")
            t_coll = rec._local_collection(root, name, new)
            at = int(sw.get("reset_at") or 0)
            con = db.connect(t_coll)
            try:
                for k in range(at, len(changed), max(1, int(window))):
                    win = changed[k:k + max(1, int(window))]
                    # (a) the marks, (b) the rows and the index entries, outside
                    # any transaction, (c) the versions: a stop between them
                    # finds the window again, each step repeatable (carry.py
                    # `keep_producer`'s order)
                    _commit(con, lambda: _reset(con, win))
                    embeddings.delete_books([c[2] for c in win], t_coll, lock_con=con)
                    sw["index_entries"] = int(sw.get("index_entries") or 0) + _drop_index_entries(t_coll, win)
                    _commit(con, lambda: _unversion(con, win))
                    sw["reset_at"] = k + len(win)
                    steps["switch"] = sw
                    _save(root, name, m)
                    if stop is not None and stop.is_set() and sw["reset_at"] < len(changed):
                        log(f"[refit] {name}: stopped after {sw['reset_at']} of {len(changed)} document(s) "
                            f"reset; run it again to resume")
                        return {**m, "stopped": True}
            finally:
                con.close()
        finally:
            _release_index_writers(s_now)
        sw["done"] = True
        steps["switch"] = sw
        _save(root, name, m)
        log(f"[refit] {name}: {len(changed)} document(s) reset (their marks, error, chunk hashes, vector rows "
            f"and text-index entries): the worker cuts, indexes and embeds them at the new layer")
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
        _save(root, name, m)
        log(f"[refit] {name}: proven — {pf['compared']} unchanged embedded document(s) cut to exactly the "
            f"chunks the vector table holds, {pf['parts_compared']} unchanged part(s) to their chunk roots, "
            f"the change set holding no vector row and no index entry")

    # ---- attest (03 §3.6 test 1 for the new id, still paused)
    at = steps.get("attest") or {}
    if not at.get("done"):
        at = _attest(root, name, new, t_coll, steps, log)
        if at["failed"]:
            steps["attest"] = at
            _save(root, name, m)
            log(f"[refit] {name}: {', '.join(at['failed'])} FAILED for the new id; rolling back")
            rollback(root, name, log=log)
            raise RefitFailed(f"refit: {name}: {', '.join(at['failed'])} failed over the parts written again; "
                              f"rolled back: {name} holds {old.collection_id} as before")
        at["done"] = True
        steps["attest"] = at
        m["lance_after"] = lance_versions(t_coll)
        m["done"] = True
        m["finished"] = int(time.time())
        _save(root, name, m)
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
    kept = ta / KEPT_FILE
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
            if "chunks_root" in p and (row["chunks"], row["chunks_root"]) != (int(p["chunks"]), p["chunks_root"]):
                tmp.unlink(missing_ok=True)
                raise CodexError(f"refit: {p['path']} written again cuts {row['chunks']} chunks "
                                 f"({row['chunks_root'][:23]}…), the parts pass cut {p['chunks']} "
                                 f"({p['chunks_root'][:23]}…); nothing more was written")
            if not kept.is_file():
                _rename(here, kept)
            _place(tmp, here)
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
    if not docs["compared"]:
        # a proof that compared nothing proves nothing (the independent read of
        # e492ebaf, 1): no unchanged embedded document was found to hold to its rows
        failures.append(f"the proof compared no document ({docs['sampled']} sampled, "
                        f"{docs['without_vectors']} without vectors): nothing shows the kept rows are the new "
                        f"layer's")
    entries = index_held(t_coll, changed) if changed else 0
    if entries:
        failures.append(f"the text index still holds {entries} entr(y/ies) of the change set")
    parts_compared = 0
    snap = None
    if (steps.get("parts") or {}).get("snapshot"):
        snap = seed.latest_snapshot(root, name)
        rebuilt = {(p["slice"], int(p["n"])) for p in (steps["changes"].get("parts") or [])}
        pool = [s for s in snap.shards if (s.slice, int(s.n)) not in rebuilt]
        rng = random.Random(new.collection_id)
        shard = col.shard_dir_of(root, name)
        picked = rng.sample(pool, min(int(part_sample), len(pool)))
        if pool and not picked:
            failures.append("the proof compared no unchanged part")
        for p in steps["changes"].get("parts") or []:
            got = next((x for x in snap.shards if (x.slice, int(x.n)) == (p["slice"], int(p["n"]))), None)
            if got is None or (int(got.chunks), got.chunks_root) != (int(p["chunks"]), p["chunks_root"]):
                failures.append(f"{p['path']}: the snapshot's entry is not what the parts pass cut")
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
            "index_entries": entries,
            "failures": failures}


# ---- the rollback ---------------------------------------------------------------

def _rows_outside(coll, changed: list[list]) -> dict[str, int]:
    """Each vector table's rows that are NOT the change set's: the refit's own
    writes delete the change set's rows only, so a count that moved here is
    another writer's (the third read of 39f352f0, 10)."""
    from app import embeddings
    books = sorted({int(c[2]) for c in changed})
    held = embeddings.held_chunks(coll, books) if books else None
    mine = sum(len(held.of(b)) for b in books) if held is not None else 0
    return {"outside": sum(int(t.count_rows()) for _d, t in _tables(coll)) - mine}


def _written_since(root: Path, name: str, coll, m: dict) -> str | None:
    """Why the collection was written by another since the switch saved it,
    or None: its manifest grew, or its vector table holds other rows than the
    change set's. Rows embedded meanwhile are never dropped in silence."""
    from . import records as rec
    sw = (m.get("steps") or {}).get("switch") or {}
    changed = read_changed(asides(root, name, m)[1] / "changed.jsonl")
    if sw.get("top") is not None and _top(coll) != int(sw["top"]):
        return f"its manifest's last rowid moved {sw['top']} → {_top(coll)} since the switch"
    if sw.get("outside") is not None:
        now = _rows_outside(rec._local_collection(root, name, coll.codex), changed)
        if now != sw["outside"]:
            return f"its vector table holds other rows than the switch left ({sw['outside']} → {now})"
    return None


def _versions_gone(coll, versions: dict[str, int]) -> list[str]:
    """The recorded versions a restore needs that a table no longer holds (a
    maintainer prunes old versions: app/vector_index.py's keep window)."""
    gone = []
    for d, t in _tables(coll):
        want = versions.get(d)
        if want is None:
            continue
        held = {int(v["version"] if isinstance(v, dict) else getattr(v, "version")) for v in t.list_versions()}
        if int(want) not in held:
            gone.append(f"{d} version {want}")
    return gone


def rollback(root, name: str, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Everything a refit changed put back, from whatever step it reached,
    RESUMABLE: each sub-step recorded in the marker (`rollback`), the
    vectors and the manifest's rows FIRST — after checking that every
    version the restore needs is still held, before anything moves — then
    the files by rename, each move decided by what lies where, so a re-run
    finishes a stopped one. What the new Codex wrote is set aside under
    `rejected/`, never deleted. The change set's index marks are reset, so
    the worker indexes those documents again at the old layer: the refit
    deleted their entries, or the worker already indexed them at the new one
    (the independent read of e492ebaf, 5: reset, not refused). A FINISHED
    refit is refused once its vector table was written since. The caller
    holds the write lock."""
    from . import carry as carry_mod, codex as cx, collection as col, deposit, records as rec
    root = Path(root)
    m = read_marker(root, name)
    if m is None:
        raise CodexError(f"refit: {name} records no refit to roll back ({marker_path(root, name)})")
    if m.get("rolled_back"):
        return _rollback_moves(root, name, m, log)
    carry_mod.refuse_without_locks(root, (name,), "hold collection refit --rollback")
    old_id, new_id = m["predecessor"]["collection_id"], m["to"]["collection_id"]
    sa, ta = asides(root, name, m)
    rejected_s, rejected_t = sa / "rejected", ta / "rejected"
    shard = col.shard_dir_of(root, name)
    steps = m.get("steps") or {}
    sw = steps.get("switch") or {}
    rb = m.setdefault("rollback", {})
    here = shard / "codex.json"
    old_cx = cx.loads((sa / "codex.json").read_bytes()) if (sa / "codex.json").is_file() else \
        cx.loads(here.read_bytes())
    if old_cx.collection_id != old_id:
        raise CodexError(f"refit: the Codex set aside is {old_cx.collection_id}, the marker names {old_id}")
    coll = rec._local_collection(root, name, old_cx)
    if not rb.get("checked"):
        from . import retarget as retarget_mod
        moved = retarget_mod.naming(root, new_id)
        if moved:
            # an annotations collection a retarget moved to the new id would
            # name a collection no longer held (the third read of 39f352f0, 6)
            raise CodexError(f"refit --rollback: {', '.join(moved)} name(s) the new id {new_id[:23]}…, moved there "
                             f"by a retarget; the way back is `louise hold annotations retarget <name> --undo` "
                             f"for each, then this rollback; nothing was changed")
        if not m.get("done") and sw.get("saved"):
            why = _written_since(root, name, coll, m)
            if why:
                raise CodexError(f"refit --rollback: {why}: restoring the recorded versions would drop rows "
                                 f"another writer added; finish the refit instead (the same command resumes it); "
                                 f"nothing was changed")
        if m.get("done") and lance_versions(coll) != (m.get("lance_after") or {}):
            # the worker embeds into the new space once resumed: restoring the
            # tables' versions then would drop its rows under marks that say embedded
            raise CodexError(f"refit --rollback: {name}'s vector table was written since the refit ended "
                             f"({m.get('lance_after')} → {lance_versions(coll)}): rolling back would lose "
                             f"those rows, so {name} is left as it is; nothing was changed")
        gone = _versions_gone(coll, sw.get("lance") or {})
        if gone:
            raise CodexError(f"refit --rollback: the vector table no longer holds the version(s) recorded "
                             f"before the switch ({', '.join(gone)}: pruned since), so its rows cannot be put "
                             f"back; nothing was changed")
        rb["checked"] = True
        _save(root, name, m)
    # 1. the vectors, to their recorded versions
    if sw.get("lance") and not rb.get("lance"):
        rb["lance_restored"] = _restore_lance(coll, sw["lance"], log)
        rb["lance"] = True
        _save(root, name, m)
    # 2. the manifest's rows, then the change set's index marks
    if sw.get("saved") and not rb.get("rows"):
        changed = read_changed(ta / "changed.jsonl")
        cur = cx.loads(here.read_bytes()) if here.is_file() else None
        new_cx = cur if cur is not None and cur.collection_id == new_id else \
            (cx.loads((rejected_s / "codex.json").read_bytes()) if (rejected_s / "codex.json").is_file() else old_cx)
        manifest = root / name / "state" / "manifest.sqlite"
        rb["rows_restored"] = _restore_rows(manifest, ta / "saved.sqlite", changed, old_cx, new_cx)
        # ALWAYS: a stop after an index delete and before `reset_at` was saved
        # left entries gone under marks the rows put back as indexed (the
        # second read of 10cc6f34, 3); an extra re-index of the change set is
        # the safe direction
        rb["index_marks_reset"] = _reset_index_marks(manifest, changed)
        rb["rows"] = True
        _save(root, name, m)
    if (ta / "node-saved.sqlite").is_file() and not rb.get("node_rows"):
        rb["node_rows_back"] = _node_rows_back(ta / "node-saved.sqlite")
        rb["node_rows"] = True
        _save(root, name, m)
    # 3. the ask indexes' marker, 4. the collection file
    if (ta / "ask-marker.json").is_file() and not rb.get("ask_marker"):
        from app.ask import facets as ask_facets
        marker = ask_facets.marker_path(root / name / "index")
        tmp = marker.with_name(f".{marker.name}.refit")
        tmp.write_bytes((ta / "ask-marker.json").read_bytes())
        os.replace(tmp, marker)
        rb["ask_marker"] = True
        _save(root, name, m)
    kept = ta / KEPT_FILE
    path = deposit.held_file(root, name)
    if kept.is_file() and not rb.get("file"):
        if not path.is_file() or path.read_bytes() != kept.read_bytes():
            tmp = path.with_name(path.name + ".refit")
            tmp.write_bytes(kept.read_bytes())
            os.replace(tmp, path)
        rb["file"] = True
        _save(root, name, m)
    # 5. the files, by rename, each decided by what lies where: first those
    # naming the old id, and the new id's own attestations
    rb["names_back"] = _names_back(root, name, sw.get("names") or [], ta, rejected_t)
    new_att = root / name / "state" / "attestations" / _hex(new_id)
    if new_att.exists():
        _rename(new_att, rejected_t / FILES / "state" / "attestations" / _hex(new_id))
    for p in (steps.get("changes") or {}).get("parts") or []:
        (shard / p["path"]).with_name(f".{Path(p['path']).name}.refit").unlink(missing_ok=True)   # our own temp
    for p in sorted(q for q in (sa / "parts").rglob("*") if q.is_file()) if (sa / "parts").is_dir() else []:
        rel = p.relative_to(sa / "parts")
        if (shard / rel).is_file():
            _rename(shard / rel, rejected_s / "parts" / rel)
        _rename(p, shard / rel)
    if (sa / "snapshots").exists():
        if (shard / "snapshots").exists():
            _rename(shard / "snapshots", rejected_s / "snapshots")
        _rename(sa / "snapshots", shard / "snapshots")
    if (sa / "codex.json").is_file():
        if here.is_file():
            _rename(here, rejected_s / "codex.json")
        _rename(sa / "codex.json", here)
    build = root / name / rec.STATE / rec.BUILD
    claim = build / rec.STAGING_CLAIM
    if build.exists() and claim.is_file() and json.loads(claim.read_text(encoding="utf-8")).get("collection_id") \
            == new_id:
        _rename(build, rejected_t / "build")               # the new Codex's staging
    if (ta / "build").exists() and not build.exists():
        _rename(ta / "build", build)
    rb["files"] = True
    rb["parts"] = len(steps.get("parts", {}).get("rebuilt_parts") or [])
    rb["stamp"] = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    m["rolled_back"] = True
    m["rolled_back_at"] = int(time.time())
    _save(root, name, m)
    out = _rollback_moves(root, name, m, log)
    log(f"[refit] {name}: rolled back — it holds {old_id[:23]}… as before ({rb})")
    return out


def _rollback_moves(root: Path, name: str, m: dict, log) -> dict[str, Any]:
    """A finished rollback's last moves: the folders set aside, and the marker
    with them, out of the next refit's way. Each move skipped where it is
    done (a re-run finishes a stopped one)."""
    stamp = m["rollback"]["stamp"]
    sa, ta = asides(root, name, m)
    sa2, ta2 = sa.with_name(f"{sa.name}.rolled-back-{stamp}"), ta.with_name(f"{ta.name}.rolled-back-{stamp}")
    if sa.exists() and not sa2.exists():
        _rename(sa, sa2)
    if ta.exists() and not ta2.exists():
        _rename(ta, ta2)
    mp = marker_path(root, name)
    if mp.is_file():
        _rename(mp, ta2 / MARKER)
    return m["rollback"]


def _reset_index_marks(manifest: Path, changed: list[list]) -> int:
    """The change set's index marks emptied after the rows came back: the
    worker indexes them again (delete-then-insert by book) at the old layer.
    One BEGIN IMMEDIATE. Repeatable."""
    from app import db
    con = sqlite3.connect(manifest, timeout=60, isolation_level=None)
    try:
        db.wal(con)
        con.execute("BEGIN IMMEDIATE")
        try:
            n = 0
            for k in range(0, len(changed), 500):
                rowids = [c[0] for c in changed[k:k + 500]]
                n += con.execute(f"UPDATE documents SET fts_done=0, fts_version=0, entity_version=0 "
                                 f"WHERE rowid IN ({','.join('?' for _ in rowids)})", rowids).rowcount
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return n
