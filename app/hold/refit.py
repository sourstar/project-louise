"""hold.refit — a collection's encoder window changed OFFLINE (amendment
A1l; the operator, 2026-11-27, verbatim: "you fucking stop the node, delete
the vectors and the indexing of the only book that is affected by the
change, change the chunker offline, rewrite the codex and regenerate the
collection's identity, then replace it's identity everywhere. You restart
the node and it's done").

`louise hold collection refit <name> --layer-param max_tokens=N
--layer-param tokenizer=sha256:… [--dry-run]`, refused while the worker runs
(exit 3, `louise node stop` first). The `fixed` chunker's encoder window
keeps every window that fits byte for byte and id for id, so only a document
with a window over the limit is cut otherwise (gutenberg: PG 69207). Each
step recorded in `state/refit.json`, so a rerun after a crash continues
(invariant 2). THERE IS NO ROLLBACK IN CODE: the rollback is the host's ZFS
snapshot of /data and of the SHARD dataset, taken first (the dry run says
so).

  changes   every document cut at the old and the new layers in the parse
            pool; the ones cut otherwise, and the parts holding them.
            Refused when there is none.
  reset     for those documents only: their vector rows and text-index
            entries deleted, their marks emptied (embed, index, entities,
            counts), error and retries cleared — the worker redoes them at
            restart.
  codex     the new Codex written (a new collection_id), the old one and its
            snapshots set aside under <SHARD>/.replaced/<old id>/ (renamed).
  parts     the parts holding a changed document written again from their
            own record lines (a line holds no layer, 09 §9.5), the old
            versions set aside; the new Codex's snapshot, every other part's
            entry and writer kept, signed.
  identity  the old id replaced everywhere: the collection file's `codex`
            line, the manifest's pin and cursors, this node's own
            attestations re-signed for the new id and the rewritten parts
            attested (quarantine serves it), RAGE settings signed again, the
            carry records re-pointed; what only the old id's checks, gate,
            fetch or publication wrote set aside. Every OTHER collection
            naming the old id — an annotations collection's `annotates` —
            gets its Codex rewritten with the new target, a new identity of
            its own replaced the same way, its snapshots re-signed. Nothing
            names the old id after (pin-run's `_sweep`, held to empty).

Publication to the test peers is no reason to refuse (the operator: shared
for testing only, RULE 1); an OFFICIAL one is (`official_publication`), and
so is a text index shared with another collection (its identity names the
layers). The encoder side is unchanged, so no CPU reference and no gate run
here: the reference is the space's inputs', inherited, and the worker gates
its run for the new space at restart, as for any collection.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .codex import Codex, CodexError

MARKER = "refit.json"
ASIDE = ".replaced"
WINDOW = 500
SHOWN = 20
FIT_PARAMS = ("max_tokens", "tokenizer")
STEPS = ("reset", "codex", "parts", "identity", "others", "attest")
#: an annotations collection's own marker while its target is re-keyed
TARGET_MARKER = "refit-target.json"


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


def _hex(cid: str) -> str:
    return cid.split(":", 1)[-1]


def parts_holding(shard_dir: Path, snap, uids) -> list[dict]:
    """The record parts of `snap` holding any of `uids`, from each part's own
    trailing index (no record decoded), by threads."""
    from concurrent.futures import ThreadPoolExecutor
    from app.pack import records as pack_records
    want = set(uids)
    if not want or snap is None:
        return []

    def held(s) -> list[str]:
        p = Path(shard_dir) / s.path
        return sorted(want & set(pack_records.read_index(p).uids)) if p.is_file() else []
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="refit-parts") as ex:
        found = list(ex.map(held, snap.shards))
    return [{"slice": s.slice, "n": int(s.n), "path": s.path, "uids": got}
            for s, got in zip(snap.shards, found) if got]


def worker_runs(root, name: str) -> str | None:
    """Whether the worker runs, in words: it holds the write lock of every
    collection it runs (`the node (worker)`, app/worker.py). Refused offline
    work names it (exit 3)."""
    from . import collection as col, lock as lock_mod
    for n, _c in col._held(Path(root)):
        who = lock_mod.holder(lock_mod.state_dir(Path(root), n))
        if who and who.get("what") == "the node (worker)":
            return f"the worker runs (it holds {n}'s write lock): `louise node stop` first"
    return None


def _refuse_running(root, name: str) -> None:
    why = worker_runs(root, name)
    if why:
        e = CodexError(f"refit: {why}; this refit is offline only, and nothing was written")
        e.exit_code = 3                                    # type: ignore[attr-defined]
        raise e


def _others(root: Path, name: str, old_id: str) -> list[str]:
    """The other held collections naming `old_id`: annotations ones are
    re-keyed with it; any other (a sister, a daughter) refuses — not built."""
    from . import collection as col, pin_run
    from .codex import targets_of
    named = pin_run._naming(root, name, old_id)
    held = dict(col._held(root))
    wrong = [n for n in named if n not in held or getattr(held[n], "annotates", None) is None
             or not any(t.collection_id == old_id for t in targets_of(held[n]))]
    if wrong:
        raise CodexError(f"refit: {', '.join(wrong)} name(s) {old_id[:23]}… other than as an annotations "
                         f"target (a sister, a daughter): re-keying those is not built; nothing was written")
    return named


def _changed_of(root: Path, name: str, old: Codex, new: Codex, *, window: int, processes, log) -> list[list]:
    from . import records as rec
    s_coll = rec.local_collection(root, name, old)
    t_coll = planned_target(s_coll, new)
    changed: list[list] = []
    _changes(s_coll, t_coll, start=0, window=window, processes=processes, stop=None,
             each=lambda _hi, got: changed.extend(got["changed"]), log=log)
    return changed


def _snapshot_of(root: Path, name: str, codex: Codex):
    from . import seed
    try:
        snap = seed.latest_snapshot(root, name)
    except CodexError:
        return None
    return snap if snap.collection_id == codex.collection_id else None


def plan(root, name: str, layers, *, window: int = WINDOW, processes: int | None = None,
         log: Callable[[str], None] = print) -> dict[str, Any]:
    """Everything the run does, nothing written, no lock taken."""
    from . import collection as col
    root = Path(root)
    _, old = col.resolve(root, name)
    new = new_codex(old, layers)
    for why in (official_publication(root, name, old), shared_index_refusal(root, name)):
        if why:
            raise CodexError(f"refit: {why}; nothing was written")
    others = _others(root, name, old.collection_id)
    changed = _changed_of(root, name, old, new, window=window, processes=processes, log=log)
    snap = _snapshot_of(root, name, old)
    parts = parts_holding(col.shard_dir_of(root, name), snap, [c[1] for c in changed])
    return {"name": name, "from": old.collection_id, "to": new.collection_id, "changed": changed,
            "parts": parts, "snapshot": snap.snapshot_id if snap is not None else None, "others": others,
            "worker": worker_runs(root, name), "codex": new}


def read_marker(root, name: str) -> dict | None:
    p = Path(root) / name / "state" / MARKER
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _save(root, name: str, m: dict) -> None:
    p = Path(root) / name / "state" / MARKER
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(m, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _aside(src: Path, dst: Path) -> None:
    """`src` renamed to `dst` (never deleted, never over a file); nothing to
    do where `src` is gone and `dst` holds it."""
    if not src.exists() and not src.is_symlink():
        return
    if dst.exists():
        raise CodexError(f"refit: {dst} exists already; {src} is not set aside over it")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _predecessor(root: Path, name: str, m: dict | None) -> Codex:
    """The Codex the collection held before the refit in progress."""
    from . import codex as cx, collection as col
    if m is None:
        return col.read(root, name)
    p = col.shard_dir_of(root, name) / "codex.json"
    if p.is_file() and cx.loads(p.read_bytes()).collection_id == m["from"]:
        return cx.loads(p.read_bytes())
    return cx.loads((col.shard_dir_of(root, name) / ASIDE / _hex(m["from"]) / "codex.json").read_bytes())


def predecessor(root, name: str) -> Codex:
    m = read_marker(root, name)
    return _predecessor(Path(root), name, m if m is not None and not m.get("done") else None)


def run(root, name: str, layers, *, key=None, now: str, window: int = WINDOW, processes: int | None = None,
        log: Callable[[str], None] = print) -> dict[str, Any]:
    """The steps (module docstring), from where `state/refit.json` says."""
    from app import db, embeddings
    from . import codex as cx, collection as col, records as rec
    root = Path(root)
    _refuse_running(root, name)
    m = read_marker(root, name)
    if m is not None and m.get("done"):
        _file_marker(root, name, m)
        log(f"[refit] {name}: done already ({m['to'][:23]}…)")
        return m
    old = _predecessor(root, name, m)
    new = new_codex(old, layers)
    if m is not None and m["to"] != new.collection_id:
        raise CodexError(f"refit: {name} records a refit to {m['to']}; these params make {new.collection_id}")
    if m is None:
        p = plan(root, name, layers, window=window, processes=processes, log=log)
        if not p["changed"]:
            raise CodexError(f"refit: {name}: the new window cuts no document otherwise; nothing to refit, "
                             f"nothing was written")
        m = {"kind": "louise-refit", "from": old.collection_id, "to": new.collection_id, "changed": p["changed"],
             "parts": p["parts"], "others": p["others"], "steps": {}, "done": False}
        _save(root, name, m)
        log(f"[refit] {name}: {len(p['changed'])} document(s) cut otherwise "
            f"({', '.join(c[1] for c in p['changed'][:SHOWN])}), in {len(p['parts'])} part(s)")
    steps, changed, oh = m["steps"], m["changed"], _hex(old.collection_id)
    shard = col.shard_dir_of(root, name)
    sa = shard / ASIDE / oh

    def done(step: str, **info) -> None:
        steps[step] = {"done": True, **info}
        _save(root, name, m)

    if not steps.get("reset"):
        coll = rec._local_collection(root, name, old)
        _index_writers(coll, changed)
        con = db.connect(coll)
        try:
            for k in range(0, len(changed), max(1, window)):
                win = changed[k:k + max(1, window)]
                _commit(con, lambda: _reset(con, win))
                embeddings.delete_books([c[2] for c in win], coll, lock_con=con)
                _drop_index_entries(coll, win)
                _commit(con, lambda: _unversion(con, win))
        finally:
            con.close()
            _release_index_writers(coll)
        done("reset", documents=len(changed))
        log(f"[refit] {name}: {len(changed)} document(s) reset: their vector rows and index entries gone, "
            f"their marks, error and retries cleared")
    if not steps.get("codex"):
        here = shard / "codex.json"
        if here.is_file() and cx.loads(here.read_bytes()).collection_id == old.collection_id:
            _aside(here, sa / "codex.json")
        _aside(shard / "snapshots", sa / "snapshots")
        if not here.is_file():
            tmp = here.with_name("codex.json.refit")
            tmp.write_bytes(new.canonical())
            os.replace(tmp, here)
        done("codex")
        log(f"[refit] {name}: the new Codex written, {new.collection_id[:23]}…; the old one and its "
            f"snapshots set aside in {sa}")
    if not steps.get("parts"):
        snap = _parts(root, name, new, m, sa, key=key, now=now, log=log)
        done("parts", snapshot=snap)
    if not steps.get("identity"):
        done("identity", **rekey(root, name, old, new, key=key, now=now,
                                 rewritten=[(p["slice"], p["n"]) for p in m["parts"]], log=log))
    if not steps.get("others"):
        moved = {}
        for other in m["others"]:
            moved[other] = rekey_annotations(root, other, old.collection_id, new.collection_id, key=key, now=now,
                                             log=log)
        done("others", moved=moved)
    if not steps.get("attest"):
        done("attest", **_attest(root, name, new, [p["path"] for p in m["parts"]], key, log))
    m["done"] = True
    _save(root, name, m)
    _file_marker(root, name, m)
    log(f"[refit] {name}: done — {new.collection_id[:23]}…; restart the node: the worker cuts, indexes and "
        f"embeds the {len(changed)} document(s), and gates its run for the new space")
    return m


def _file_marker(root: Path, name: str, m: dict) -> None:
    """A finished refit's marker filed with what it set aside: nothing names
    the old id in the collection's state after it (`_sweep`)."""
    _aside(root / name / "state" / MARKER, root / name / "state" / ASIDE / _hex(m["from"]) / MARKER)


def _parts(root: Path, name: str, new: Codex, m: dict, sa: Path, *, key, now, log) -> str | None:
    """The parts holding a changed document written again from their own
    lines (the old version set aside), their build-database rows updated,
    then the new Codex's snapshot under the old latest id, every other
    part's entry and writer kept (`snapshot.create(writers_from=)`), signed."""
    from app.pack import build as pack_build
    from . import collection as col, records as rec, snapshot as sn
    snaps = sorted(p for p in (sa / "snapshots").glob("*.json") if not p.name.endswith(".sig.json")) \
        if (sa / "snapshots").is_dir() else []
    if not snaps:
        log(f"[refit] {name}: it froze no snapshot: no part to write again")
        return None
    prev = sn.load(snaps[-1])
    shard = col.shard_dir_of(root, name)
    t_coll = rec.local_collection(root, name, new)
    db_path = root / name / rec.STATE / rec.BUILD / pack_build.BUILD_DIR / pack_build.BUILD_DB
    if not db_path.is_file():
        raise CodexError(f"refit: {name} has no staging build database ({db_path}): its parts cannot be "
                         f"written again from a build's rows")
    for p in m["parts"]:
        here, kept = shard / p["path"], sa / "parts" / p["path"]
        tmp = here.with_name(f".{here.name}.refit")
        row = rebuild_part(t_coll, str(kept if kept.is_file() else here), str(tmp))
        if not kept.is_file():
            _aside(here, kept)
        os.replace(tmp, here)
        con = sqlite3.connect(db_path, isolation_level=None)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE shards SET records=?, chunks=?, bytes=?, sha256=?, first_uid=?, last_uid=?, "
                        "id_min=?, id_max=?, layers=?, written_at=?, root=?, records_root=?, chunks_root=? "
                        "WHERE slice=? AND n=?",
                        (row["records"], row["chunks"], row["bytes"], row["sha256"], row["first_uid"],
                         row["last_uid"], row["id_min"], row["id_max"], row["layers"], int(time.time()),
                         row["root"], row["records_root"], row["chunks_root"], p["slice"], int(p["n"])))
            con.execute("COMMIT")
        finally:
            con.close()
        log(f"[refit] {name}: {p['path']} written again ({row['chunks']} chunks)")
    rec.write_validation(root, name)
    snap = sn.create(root, name, snapshot_id=prev.snapshot_id, writers_from=prev, log=log)
    if key is not None:
        sn.sign_snapshot(root, name, snap, key, now=now)
    log(f"[refit] {name}: snapshot {snap.snapshot_id} for the new Codex, {snap.hash[:23]}…")
    return snap.hash


#: what only the old id's checks, gate, fetch or publication wrote: set aside
def _set_aside_rel(rel: str, oldhex: str) -> bool:
    from . import pin_run
    from .cascade import PUBLISHED_DIR
    from app.share.fetch import CHAIN
    return (rel in {f"state/{pin_run._ORIGIN_TRIED}", f"state/{pin_run._ORIGIN_ACK}", "state/calibration.json",
                    *(f"state/{f}" for f in pin_run._SCHEDULED)}
            or rel.startswith(("state/calibration/", f"state/{CHAIN}/", f"state/{PUBLISHED_DIR}/")))


def rekey(root: Path, name: str, old: Codex, new: Codex, *, key, now: str, rewritten: list,
          log) -> dict[str, Any]:
    """Every place naming the old id under `name` re-keyed to the new one
    (pin-run's `_sweep` and its re-keys): the collection file's `codex` line,
    the manifest's pin and cursors, this node's own attestations of the
    parts kept re-signed, RAGE settings signed again, carry records
    re-pointed; what only the old id's checks, gate, fetch or publication
    wrote set aside under state/.replaced/<old id>/. Held to empty after."""
    from app import collection_file
    from . import carry as carry_mod, deposit, line_carry, pin_run, records as rec, update_state
    oh = _hex(old.collection_id)
    base = root / name
    ta = base / "state" / ASIDE / oh
    out: dict[str, Any] = {}
    held = deposit.held_file(root, name)
    if held.is_file():
        out["file"] = collection_file.recodex(held, new.collection_id)
    manifest = base / "state" / "manifest.sqlite"
    if manifest.is_file() and getattr(new, "space", None) is not None:
        out["coverage_cleared"] = carry_mod._repin(manifest, old, new)
    out["control"] = pin_run._repoint_manifest(manifest, old.collection_id, new.collection_id)
    out["resigned"] = _resign_attestations(root, name, old, new, key, rewritten)
    for rel in pin_run._sweep(root, name, old.collection_id):
        if rel.startswith((f"shard/{ASIDE}/", f"state/{ASIDE}/", f"state/attestations/{oh}/")) \
                or rel in (f"state/{MARKER}", f"state/{TARGET_MARKER}"):
            continue                                   # the old id's attestations: their folder, below
        p = base / rel
        if rel in (f"state/{carry_mod.MARKER}", f"state/{line_carry.MARKER}",
                   f"state/{rec.BUILD}/{rec.STAGING_CLAIM}"):
            pin_run._repoint_json(p, old.collection_id, new.collection_id)
        elif rel.startswith("shard/rage-settings/") and rel.endswith(".json"):
            pin_run._resign_rage(p, old.collection_id, new.collection_id, key)
        elif rel == f"state/{update_state.FILE}":
            mk = update_state.read(root, name) or {}
            if not (mk.get("step") == "due" and not mk.get("moved")):
                raise CodexError(f"refit: {name}'s update of the old Codex is under way ({rel}): finish it first")
            _aside(p, ta / rel)
        elif _set_aside_rel(rel, oh):
            _aside(p, ta / rel)
        else:
            raise CodexError(f"refit: {rel} names {old.collection_id[:23]}… and the refit does not know what "
                             f"to do with it")
    if (base / "state" / "attestations" / oh).exists():
        _aside(base / "state" / "attestations" / oh, ta / "state" / "attestations" / oh)
    left = [r for r in pin_run._sweep(root, name, old.collection_id)
            if not r.startswith((f"shard/{ASIDE}/", f"state/{ASIDE}/"))
            and r not in (f"state/{MARKER}", f"state/{TARGET_MARKER}")]
    if left:
        raise CodexError(f"refit: {', '.join(left[:5])} still name {old.collection_id[:23]}…")
    _forget_publisher(name)
    log(f"[refit] {name}: the old id replaced everywhere ({out})")
    return out


def _forget_publisher(name: str) -> None:
    """node.sqlite's record of the publisher's last pass of `name`: the new
    id is published afresh."""
    from app import node_state
    if not node_state.path().exists():
        return
    con = sqlite3.connect(node_state.path(), timeout=60, isolation_level=None)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='control'").fetchone():
            con.execute("DELETE FROM control WHERE key=?", (f"publish:{name}",))
    finally:
        con.close()


def _resign_attestations(root: Path, name: str, old: Codex, new: Codex, key, rewritten: list) -> int:
    """This node's own attestations of `roots`, `records` and `chunks` over
    parts the refit kept byte for byte, signed again for the new id and its
    snapshot (each shard id re-keyed): quarantine reads them by the id. The
    others are set aside with the old id's folder."""
    from . import codex as cx, envelope as env, seed
    if key is None:
        return 0
    d = root / name / "state" / "attestations" / _hex(old.collection_id)
    if not d.is_dir():
        return 0
    snap = seed.latest_snapshot(root, name)
    me = env.key_text(key.verify_key)
    # 09 §9.8's record-check shard ids, `<collection_id>/<slice>/<n>`
    dropped = {f"{old.collection_id}/{sl}/{int(n):05d}" for sl, n in rewritten}
    out_dir = root / name / "state" / "attestations" / _hex(new.collection_id)
    n = 0
    for f in sorted(d.glob("*.json")):
        try:
            body = env.verify(env.ATTESTATION, json.loads(f.read_bytes()))
        except Exception:   # noqa: BLE001 — not one: set aside with the folder
            continue
        shards = list(body.get("shards") or [])
        if body.get("key") != me or body.get("check") not in ("roots", "records", "chunks") \
                or body.get("result") != "pass" or set(shards) & dropped:
            continue
        # every id in it the new one's (its shard ids, a detail naming one),
        # its snapshot the new one
        nb = json.loads(json.dumps(body).replace(old.collection_id, new.collection_id))
        nb.update(snapshot_hash=snap.hash, snapshot_id=snap.snapshot_id)
        doc = env.sign(env.ATTESTATION, nb, key)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{_hex(env.body_id(doc['body']))}.json").write_bytes(cx.canonical_json(doc))
        n += 1
    return n


def rekey_annotations(root: Path, name: str, old_target: str, new_target: str, *, key, now: str,
                      log) -> dict[str, Any]:
    """An annotations collection naming `old_target` in its Codex given the
    new target id: its Codex rewritten (a new identity of its own), every
    snapshot rewritten for it in order (its `previous` chained again, its
    members' hashes this Codex's) and signed again, and its old id replaced
    everywhere the same way (`rekey`). Resumable: a step done is seen."""
    from . import codex as cx, collection as col, shard as shard_mod, snapshot as sn
    from .codex import targets_of
    shard = col.shard_dir_of(root, name)
    held = cx.loads((shard / "codex.json").read_bytes())
    mk = root / name / "state" / TARGET_MARKER
    m = json.loads(mk.read_text(encoding="utf-8")) if mk.is_file() else None
    if m is None:
        if not any(t.collection_id == old_target for t in targets_of(held)):
            return {"already": held.collection_id}
        targets = tuple(dataclasses.replace(t, collection_id=new_target) if t.collection_id == old_target else t
                        for t in targets_of(held))
        new = dataclasses.replace(held, annotates=targets)
        m = {"from": held.collection_id, "to": new.collection_id}
        mk.write_text(json.dumps(m), encoding="utf-8")
    old = held if held.collection_id == m["from"] else cx.loads(
        (shard / ASIDE / _hex(m["from"]) / "codex.json").read_bytes())
    targets = tuple(dataclasses.replace(t, collection_id=new_target) if t.collection_id == old_target else t
                    for t in targets_of(old))
    new = dataclasses.replace(old, annotates=targets)
    sa = shard / ASIDE / _hex(old.collection_id)
    if held.collection_id == old.collection_id:
        _aside(shard / "codex.json", sa / "codex.json")
        _aside(shard / "snapshots", sa / "snapshots")
        tmp = shard / "codex.json.refit"
        tmp.write_bytes(new.canonical())
        os.replace(tmp, shard / "codex.json")
    prev_hash = None
    (shard / "snapshots").mkdir(exist_ok=True)
    for p in sorted(q for q in (sa / "snapshots").glob("*.json") if not q.name.endswith(".sig.json")):
        s = sn.load(p)
        out = shard / shard_mod.snapshot_path(s.snapshot_id)
        from .annotations import files_of
        ns = dataclasses.replace(s, collection_id=new.collection_id, previous=prev_hash,
                                 files=files_of(shard, s.snapshot_id, new))
        if not out.is_file():
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_bytes(ns.canonical())
            os.replace(tmp, out)
        if key is not None:
            sn.sign_snapshot(root, name, sn.load(out), key, now=now)
        prev_hash = sn.load(out).hash
    got = rekey(root, name, old, new, key=key, now=now, rewritten=[], log=log)
    _aside(mk, root / name / "state" / ASIDE / _hex(old.collection_id) / "refit-target.json")
    log(f"[refit] {name}: annotates {new_target[:23]}… now, {new.collection_id[:23]}…")
    return {"from": old.collection_id, "to": new.collection_id, **got}


def _attest(root: Path, name: str, new: Codex, parts: list[str], key, log) -> dict[str, Any]:
    """`roots`, `records` and `chunks` of the parts written again, attested
    for the new id (`check.attest_landed`), as quarantine's first test."""
    from . import check as chk, equivalence, seed
    if not parts:
        return {"parts": [], "why": "no part written again"}
    snap = seed.latest_snapshot(root, name)
    got = chk.attest_landed(root, name, new, snap, parts, key=key, platform_image=equivalence.running_image())
    failed = sorted(c for c, r in got["results"].items() if r == "fail")
    if failed:
        raise CodexError(f"refit: {', '.join(failed)} FAILED over the parts written again: restore the ZFS "
                         f"snapshot taken before the run")
    log(f"[refit] {name}: " + ", ".join(f"{k} {v.upper()}" for k, v in got["results"].items())
        + (f"; attested ({len(got['attestations'])})" if got["attestations"] else f"; not attested: {got['why']}"))
    return {"parts": got["parts"], "results": got["results"], "attestations": got["attestations"],
            "why": got["why"]}
