"""hold.refit — a collection's encoder window changed OFFLINE (amendment
A1l; the operator, 2026-11-27, verbatim: "you fucking stop the node, delete
the vectors and the indexing of the only book that is affected by the
change, change the chunker offline, rewrite the codex and regenerate the
collection's identity, then replace it's identity everywhere. You restart
the node and it's done").

`louise hold collection refit <name> --layer-param max_tokens=N
--layer-param tokenizer=sha256:… [--dry-run]`, OFFLINE: under the worker's
node-wide lock (the worker holds it from boot) and every affected
collection's, refused (exit 3) while the worker runs — the app's containers
stopped (`midclt call app.stop louise-rag`; `node stop` alone is not it, the
restart policy brings the worker back), the refit run as an operator pass
with the worker's mounts and key (deploy/supervise-pass.sh). EVERYTHING THAT
CAN REFUSE IS DECIDED IN `plan`, before the first write. The `fixed` chunker's encoder window
keeps every window that fits byte for byte and id for id, so only a document
with a window over the limit is cut otherwise (gutenberg: PG 69207). Each
step recorded in `state/refit.json`, so a rerun after a crash continues
(invariant 2). THERE IS NO ROLLBACK IN CODE: the rollback is the host's ZFS
snapshot of every dataset the refit writes, taken first — the dry run names
each by its real path (`datasets`).

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
STEPS = ("reset", "codex", "parts", "identity", "others", "platform", "attest")
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
                             f"deletes the change set's index entries itself (RULE 1: a tool that needs the "
                             f"worker's writers asks for a stop): {STAY_DOWN}. Nothing was written")
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


class RefitRefused(CodexError):
    """A rule stands in the way; nothing was written (exit 2, or 3 where a
    later retry is the answer: the worker runs, an index writer is held)."""
    exit_code = 2


def _refused(why: str, code: int = 2) -> RefitRefused:
    e = RefitRefused(f"refit: {why}; nothing was written")
    e.exit_code = code
    return e


# ---- the worker, the key and the locks -------------------------------------------

#: how the node stays down (docs/notes/node-runbook.md §2): `node stop` alone
#: is not it — the containers' restart policy (unless-stopped) brings the
#: worker back
STAY_DOWN = ("stop the app's containers — on the node `midclt call app.stop louise-rag` (`louise node stop` "
             "alone is not enough: the restart policy, unless-stopped, brings the worker back) — run this as "
             "an operator pass with the worker's mounts and key (deploy/supervise-pass.sh), then "
             "`midclt call app.start louise-rag`")
#: where the refit runs: where the worker's key is
NO_KEY = ("no node key here (NODE_KEY_PATH): the refit signs the new snapshot and signs this node's "
          "attestations and RAGE settings again for the new id — run it where the worker's key is: on the node, "
          "as an operator pass with the worker's mounts (deploy/supervise-pass.sh), not in the web container, "
          "which holds the public half only")


def worker_runs(root) -> str | None:
    """Whether the worker runs, in words, or None: it holds the node-wide
    lock from boot, before any writer starts (app/hold/lock.py
    `WORKER_LOCK`, app/worker.py `main`) — a free lock means no worker — and
    a worker older than that lock holds every collection's write lock."""
    from . import collection as col, lock as lock_mod
    who = lock_mod.holder(lock_mod.worker_state(Path(root)), file=lock_mod.WORKER_LOCK)
    if who is not None:
        return f"{who.get('what') or 'a process'} (pid {who.get('pid', '?')}) holds the node-wide lock"
    for n, _c in col._held(Path(root)):
        who = lock_mod.holder(lock_mod.state_dir(Path(root), n))
        if who and who.get("what") == lock_mod.WORKER:
            return f"the worker holds {n}'s write lock"
    return None


def _running(why: str) -> RefitRefused:
    return _refused(f"{why}: the refit is offline only — {STAY_DOWN}", 3)


def take_locks(root, names) -> list:
    """The worker's node-wide lock, then every affected collection's write
    lock, sorted (as the sister carry takes both, app/louise.py), held for
    the whole run: a worker the restart policy brings back meanwhile
    refuses to start until the refit is done. Refused (exit 3) naming the
    holder."""
    from . import lock as lock_mod
    root = Path(root)
    what = "louise hold collection refit (offline)"
    held: list = []
    try:
        try:
            held.append(lock_mod.take_worker(root, what))
        except lock_mod.Held as e:
            raise _running(f"{e.holder.get('what') or 'another process'} (pid {e.holder.get('pid', '?')}) "
                           f"holds the node-wide lock") from None
        for n in sorted(set(names)):
            try:
                held.append(lock_mod.take(lock_mod.state_dir(root, n), n, what))
            except lock_mod.Held as e:
                if e.holder.get("what") == lock_mod.WORKER:
                    raise _running(f"the worker holds {n}'s write lock") from None
                raise
    except BaseException:
        for lk in reversed(held):
            lk.release()
        raise
    return held


# ---- every file naming an old id: what is done with it -----------------------------

REWRITTEN, REKEYED, RESIGNED, SET_ASIDE = "rewritten", "re-keyed", "signed again", "set aside"
#: directories set aside WHOLE when a file in them names the old id: all of
#: each is the old identity's, its binary members (vectors.parquet) included
WHOLE = {"state/bundles": "bundles of the old id (this peer's exports and its inbox): exported again under "
                          "the new id by the worker's contribution after restart",
         "state/refused-bundles": "own exports the ladder refused, under the old id"}
#: the refit's own markers: never swept
_OWN = (MARKER, TARGET_MARKER)


def _whole_of(rel: str) -> str | None:
    return next((d for d in WHOLE if rel.startswith(d + "/")), None)


def _skipped(rel: str, oh: str) -> bool:
    """What a sweep passes over: what the refit set aside, its own markers."""
    return rel.startswith((f"shard/{ASIDE}/", f"state/{ASIDE}/")) or rel in tuple(f"state/{m}" for m in _OWN)


def disposition(root: Path, name: str, rel: str, old_id: str, *, key) -> tuple[str, str]:
    """What the refit does with the file `rel` (from `name`'s folder) naming
    `old_id` — the collection's own old id or its target's: (what, in words).
    Refused (`RefitRefused`) for a file it cannot rewrite or does not know.
    Decided in `plan`, before any write; `rekey` dispatches on the same
    answer, so the two cannot differ."""
    from . import carry as carry_mod, line_carry, pin_run, records as rec, update_state
    from .cascade import PUBLISHED_DIR, WOKEN
    oh = _hex(old_id)
    if rel == "shard/codex.json":
        return REWRITTEN, "the new Codex"
    if rel.startswith("shard/snapshots/"):
        return SET_ASIDE, "the old Codex's snapshots: the new Codex's written in their place"
    if rel.startswith("shard/rage-settings/") and rel.endswith(".json"):
        try:
            pin_run._resignable(root / name / rel, key)
        except CodexError as e:
            raise _refused(f"{name}/{rel}: {str(e).removeprefix('pin-run: ')}") from None
        return RESIGNED, "this node's RAGE settings, its entries kept, signed for the new id"
    if rel in (f"state/{carry_mod.MARKER}", f"state/{line_carry.MARKER}", f"state/{rec.BUILD}/{rec.STAGING_CLAIM}"):
        return REKEYED, "re-pointed to the new id"
    if rel == f"state/{WOKEN}":
        return REKEYED, ("the cascade's record of the snapshots it ran against, keyed on the new id: the refit's "
                       "snapshot is not taken for a new publication")
    if rel.startswith(f"state/attestations/{oh}/"):
        return SET_ASIDE, "an attestation of the old id (this node's own over the latest snapshot signed again)"
    if rel == f"state/{update_state.FILE}":
        mk = update_state.read(root, name) or {}
        if not (mk.get("step") == "due" and not mk.get("moved")):
            raise _refused(f"{name}'s update of the old Codex is under way (step {mk.get('step')!r}, {rel}): "
                           f"finish it first")
        return SET_ASIDE, "an update of the old Codex that had done nothing (step due)"
    if rel.startswith(f"state/{PUBLISHED_DIR}/"):
        return SET_ASIDE, "the published marker of the old snapshot: the new one's written by its signature"
    if rel.startswith("state/share/"):
        return SET_ASIDE, "the seeder's served tree: made again at its next sync"
    whole = _whole_of(rel)
    if whole:
        return SET_ASIDE, WHOLE[whole]
    if _set_aside_rel(rel, oh):
        return SET_ASIDE, "the old id's checks, gate or fetch: run again for the new one"
    raise _refused(f"{name}/{rel} names {old_id[:23]}… and the refit does not know what to do with it")


#: what only the old id's checks, gate, fetch or publication wrote: set aside
def _set_aside_rel(rel: str, oldhex: str) -> bool:
    from . import pin_run
    from .cascade import PUBLISHED_DIR
    from app.share.fetch import CHAIN
    return (rel in {f"state/{pin_run._ORIGIN_TRIED}", f"state/{pin_run._ORIGIN_ACK}", "state/calibration.json",
                    *(f"state/{f}" for f in pin_run._SCHEDULED)}
            or rel.startswith(("state/calibration/", f"state/{CHAIN}/", f"state/{PUBLISHED_DIR}/")))


def _attestation_fate(body: dict | None, old_id: str, *, me: str | None, latest: str | None,
                      dropped: set[str]) -> tuple[str, str]:
    """An attestation of the old id: signed again for the new id only when
    it is this node's own `roots`, `records` or `chunks` PASS over the old
    LATEST snapshot (the one the refit makes again) and over no part the
    refit writes again; set aside otherwise (the independent read of
    d40a4731, 6)."""
    if body is None:
        return SET_ASIDE, "not an attestation this node can read"
    if body.get("key") != me:
        return SET_ASIDE, "another peer's"
    if body.get("check") not in ("roots", "records", "chunks") or body.get("result") != "pass":
        return SET_ASIDE, f"a {body.get('check')} {body.get('result')}: run again for the new id"
    if body.get("snapshot_hash") != latest:
        return SET_ASIDE, "over an older snapshot, which the refit does not make again"
    if set(body.get("shards") or ()) & dropped:
        return SET_ASIDE, "over a part the refit writes again: attested afresh"
    return RESIGNED, "this node's pass over the latest snapshot, signed for the new id"


def _read_attestation(path: Path) -> dict | None:
    from . import envelope as env
    try:
        return env.verify(env.ATTESTATION, json.loads(path.read_bytes()))
    except Exception:   # noqa: BLE001 — not one this node can read: set aside
        return None


# ---- the others' references, the build, the platform file ---------------------------

def _retargeted(held: Codex, old_target: str, new_target: str) -> Codex:
    from .codex import targets_of
    targets = tuple(dataclasses.replace(t, collection_id=new_target) if t.collection_id == old_target else t
                    for t in targets_of(held))
    return dataclasses.replace(held, annotates=targets)


def _references(root: Path, other: str, target: Codex, latest: str | None, changed: set[str]) -> list[str]:
    """Why `other`'s references to `target` refuse, or []. The refit makes
    the target's LATEST snapshot again under its id and no older one, so a
    `from_snapshot` or a claim's `target_snapshot` naming the latest stays
    true unchanged — the new snapshot keeps that id — and one naming an older
    snapshot refuses (nothing would answer it). A claim pointing at a RECORD
    holds: every record line is kept byte for byte, so the `content_hash` its
    `subject_hash`/`object_hash` pin is the same. A claim pointing at a CHUNK
    of a document the refit cuts otherwise refuses: that chunk id is gone."""
    from app.pack import records as pack_records
    from . import collection as col, seed
    from .annotations import CHUNK, ENTITY, _prefix, claim_of, end_uid, ends_of
    from .codex import targets_of
    _, held = col.resolve(root, other)
    mine = [t for t in targets_of(held) if t.collection_id == target.collection_id]
    out = [f"{other} annotates {target.collection_id[:23]}… from snapshot {t.from_snapshot}, older than the "
           f"latest {latest}: update {other} to the latest snapshot first"
           for t in mine if t.from_snapshot != latest]
    try:
        snap = seed.latest_snapshot(root, other)
    except CodexError:
        return out
    alone = len(targets_of(held)) == 1
    prefixes = {_prefix(u) for u in changed}
    shard = col.shard_dir_of(root, other)
    older, chunks = set(), []
    for s in snap.shards:
        p = shard / s.path
        for r in pack_records.iter_records(p) if p.is_file() else ():
            c = claim_of(r)
            ends = [(k, v) for k, v in ends_of(c) if k != ENTITY]
            if not ends or not (alone or _prefix(end_uid(*ends[0])) in prefixes):
                continue
            if c.get("target_snapshot") not in (None, latest):
                older.add(str(c["target_snapshot"]))
            chunks += [end_uid(k, v) for k, v in ends if k == CHUNK and end_uid(k, v) in changed]
    if older:
        out.append(f"{other}'s claims are checked against snapshot(s) {', '.join(sorted(older))} of the target, "
                   f"older than the latest {latest}: update {other} to the latest snapshot first")
    if chunks:
        out.append(f"{other}'s claims point at chunks of {', '.join(sorted(set(chunks))[:SHOWN])}, which the "
                   f"refit cuts otherwise: their chunk ids are gone; a refit with such claims is not built")
    return out


def _build_db(root: Path, name: str) -> Path:
    from app.pack import build as pack_build
    from . import records as rec
    return root / name / rec.STATE / rec.BUILD / pack_build.BUILD_DIR / pack_build.BUILD_DB


def _build_refusal(root: Path, name: str, snap) -> str | None:
    """The build database the refit writes its parts' rows into, and freezes
    the new snapshot from, must hold what the latest snapshot holds and
    nothing newer: the snapshot is made from all its rows, so a part built
    since would be published by the refit unasked."""
    p = _build_db(root, name)
    if snap is None:
        return None
    if not p.is_file():
        return f"{name} has no staging build database ({p}): its parts cannot be written again from a build's rows"
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        rows = {(r[0], int(r[1])): r[2] for r in con.execute("SELECT slice, n, root FROM shards")}
    finally:
        con.close()
    held = {(s.slice, int(s.n)): s.root for s in snap.shards}
    newer = sorted(k for k in rows if held.get(k) != rows[k])
    gone = sorted(k for k in held if k not in rows)
    if not newer and not gone:
        return None
    said = ", ".join(f"{sl} {n:05d}" for sl, n in (newer or gone)[:SHOWN])
    return (f"{name}'s build database holds {len(newer)} part(s) the latest snapshot {snap.snapshot_id} does not "
            f"({said})" + (f" and lacks {len(gone)} it lists" if gone else "")
            + ": publish or discard them first")


def _quarantine_listed(ids: dict[str, str]) -> list[str] | None:
    """platform.toml's `[node] quarantine_in_use_before` with every old id of
    `ids` replaced by its new one, or None when it names none."""
    from app import platform_config
    if not platform_config.platform_path().is_file():
        return None
    listed = list(platform_config.load().quarantine_in_use_before)
    if not any(i in ids for i in listed):
        return None
    return [ids.get(i, i) for i in listed]


def _quarantine_refusal(listed: list[str] | None) -> str | None:
    """The platform file's writer (`platform_config.set_keys`) tried on a
    copy: a file it would refuse to write refuses here, before anything."""
    import shutil
    import tempfile
    from app import platform_config
    from app.transfer import schedule as schedule_mod
    if listed is None:
        return None
    with tempfile.TemporaryDirectory(prefix="refit-platform-") as d:
        copy = Path(d) / "platform.toml"
        shutil.copyfile(platform_config.platform_path(), copy)
        try:
            platform_config.set_keys(copy, "node", {"quarantine_in_use_before": listed})
        except schedule_mod.ScheduleError as e:
            return (f"platform.toml's quarantine_in_use_before names the old id and cannot be rewritten by its "
                    f"writer ({e})")
    return None


def _rekey_quarantine(listed: list[str] | None, log) -> bool:
    from app import platform_config
    if listed is None:
        return False
    changed = platform_config.set_keys(platform_config.platform_path(), "node",
                                       {"quarantine_in_use_before": listed})
    if changed:
        log("[refit] platform.toml: quarantine_in_use_before names the new ids")
    return bool(changed)


def datasets(root: Path, name: str, others: list[str], *, platform: bool) -> list[tuple[str, str]]:
    """Every place the refit writes, resolved to its real path (each link
    followed — the vector table under LOUISE_LANCE_ROOT, a SHARD on its own
    dataset): the ZFS snapshot taken first, of the datasets holding these,
    is the rollback (the independent read of d40a4731, 4)."""
    from app import fts, node_state, platform_config, vector_place
    from . import collection as col, deposit, records as rec
    out: list[tuple[str, str]] = []

    def add(what: str, p) -> None:
        out.append((what, os.path.realpath(p)))
    for n in [name, *others]:
        add(f"{n}: state", root / n / rec.STATE)
        add(f"{n}: SHARD", col.shard_dir_of(root, n))
        add(f"{n}: collection file", deposit.held_file(root, n).parent)
    _, old = col.resolve(root, name)
    coll = rec.local_collection(root, name, old)
    add(f"{name}: text index", fts.base_of(coll))
    add(f"{name}: vector table", vector_place.table_dir(root, name))
    add(f"{name}: build area", root / name / rec.STATE / rec.BUILD)
    add("node.sqlite", node_state.path())
    if platform:
        add("platform.toml", platform_config.platform_path())
    return out


def effects(name: str, snap_id: str | None, others: list[str], listed: list[str] | None) -> list[str]:
    """What else changes, in words (the independent read of d40a4731, 9)."""
    out = [f"the manifest's coverage rows of the old space deleted (carry's re-pin)",
           f"the published marker of snapshot {snap_id} set aside; the new snapshot's is written by its "
           f"signature" if snap_id else "no snapshot published",
           f"the publisher's row of {name} in node.sqlite deleted: the new id is published afresh",
           f"at restart: search of {name} withheld until the worker's gate passes for the new space (the "
           f"calibration statements are the old space's, set aside), the documents reset cut, indexed and "
           f"embedded again, bundles exported again under the new id"]
    out += [f"{o}: re-keyed to the new id, with a new identity of its own" for o in others]
    if listed is not None:
        out.append("platform.toml: quarantine_in_use_before names the new ids (through its writer)")
    return out


# ---- the plan: everything that can refuse, before the first write --------------------

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
        raise _refused(f"{', '.join(wrong)} name(s) {old_id[:23]}… other than as an annotations target (a "
                       f"sister, a daughter): re-keying those is not built")
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


def _sweep_table(root: Path, name: str, old_id: str, *, key, latest: str | None, dropped: set[str],
                 refuse) -> dict[str, list[str]]:
    """Every file of `name` naming `old_id` with what is done with it."""
    from . import envelope as env, pin_run
    me = env.key_text(key.verify_key) if key is not None else None
    oh = _hex(old_id)
    out: dict[str, list[str]] = {}
    for rel in pin_run._sweep(root, name, old_id):
        if _skipped(rel, oh):
            continue
        try:
            what, said = disposition(root, name, rel, old_id, key=key)
            if rel.startswith(f"state/attestations/{oh}/"):
                what, said = _attestation_fate(_read_attestation(root / name / rel), old_id, me=me,
                                               latest=latest, dropped=dropped)
        except RefitRefused as e:
            refuse(str(e).removeprefix("refit: ").removesuffix("; nothing was written"))
            continue
        out[rel] = [what, said]
    return out


def plan(root, name: str, layers, *, key=None, window: int = WINDOW, processes: int | None = None,
         dry: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    """EVERYTHING THAT CAN REFUSE, decided before the first write (pin-run's
    rule, pin_run.py `plan`): the identity's rules (an official publication,
    a shared text index), the node key, the other collections naming the old
    id and their references, every file naming an old id known (re-keyed,
    signed again or set aside), the build database, the platform file, the
    text index's writers. Nothing written, no lock taken. With `dry`, every
    refusal is collected in `refusals` as (exit code, why) rather than the
    first raised."""
    from . import collection as col, records as rec
    root = Path(root)
    refusals: list[tuple[int, str]] = []

    def refuse(why: str, code: int = 2) -> None:
        if not dry:
            raise _refused(why, code)
        refusals.append((code, why))
    _, old = col.resolve(root, name)
    new = new_codex(old, layers)
    if dry:
        why = worker_runs(root)
        if why:
            refuse(f"{why}: the refit is offline only — {STAY_DOWN}", 3)
    for why in (official_publication(root, name, old), shared_index_refusal(root, name)):
        if why:
            refuse(why.removesuffix("; nothing was written"))
    if key is None:
        refuse(NO_KEY)
    try:
        others = _others(root, name, old.collection_id)
    except RefitRefused as e:
        refuse(str(e).removeprefix("refit: ").removesuffix("; nothing was written"))
        others = []
    snap = _snapshot_of(root, name, old)
    latest = {"id": snap.snapshot_id, "hash": snap.hash} if snap is not None else None
    why = _build_refusal(root, name, snap)
    if why:
        refuse(why)
    others_to, others_latest = {}, {}
    for o in others:
        _, ocx = col.resolve(root, o)
        others_to[o] = _retargeted(ocx, old.collection_id, new.collection_id).collection_id
        osnap = _snapshot_of(root, o, ocx)
        others_latest[o] = {"id": osnap.snapshot_id, "hash": osnap.hash} if osnap is not None else None
    ids = {old.collection_id: new.collection_id,
           **{col.resolve(root, o)[1].collection_id: others_to[o] for o in others}}
    listed = _quarantine_listed(ids)
    why = _quarantine_refusal(listed)
    if why:
        refuse(why)
    changed = _changed_of(root, name, old, new, window=window, processes=processes, log=log)
    if not changed:
        refuse("the new window cuts no document otherwise: there is nothing to refit")
    uids = {c[1] for c in changed}
    for o in others:
        for why in _references(root, o, old, latest["id"] if latest else None, uids):
            refuse(why)
    parts = parts_holding(col.shard_dir_of(root, name), snap, uids)
    dropped = {f"{old.collection_id}/{p['slice']}/{int(p['n']):05d}" for p in parts}
    sweep = {name: _sweep_table(root, name, old.collection_id, key=key, latest=(latest or {}).get("hash"),
                                dropped=dropped, refuse=refuse)}
    for o in others:
        own = col.resolve(root, o)[1].collection_id
        table = _sweep_table(root, o, old.collection_id, key=key, latest=None, dropped=set(), refuse=refuse)
        table.update(_sweep_table(root, o, own, key=key, latest=(others_latest[o] or {}).get("hash"),
                                  dropped=set(), refuse=refuse))
        sweep[o] = table
    if dry and changed:
        coll = rec._local_collection(root, name, old)
        try:
            _index_writers(coll, changed)
        except CodexError as e:
            refuse(str(e).removeprefix("refit: ").split("; Nothing")[0], 3)
        finally:
            _release_index_writers(coll)
    return {"name": name, "from": old.collection_id, "to": new.collection_id, "changed": changed,
            "parts": parts, "snapshot": latest["id"] if latest else None, "latest": latest, "others": others,
            "others_to": others_to, "others_latest": others_latest, "sweep": sweep, "quarantine": listed,
            "datasets": datasets(root, name, others, platform=listed is not None),
            "effects": effects(name, latest["id"] if latest else None, others, listed),
            "refusals": refusals, "codex": new}


# ---- the run ----------------------------------------------------------------------------

def read_marker(root, name: str) -> dict | None:
    p = Path(root) / name / "state" / MARKER
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _write_atomic(p: Path, data: bytes) -> None:
    """`data` at `p`, whole: a temporary file beside it, synced, renamed."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.refit-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _save(root, name: str, m: dict) -> None:
    _write_atomic(Path(root) / name / "state" / MARKER, json.dumps(m, sort_keys=True, indent=1).encode("utf-8"))


def _aside(src: Path, dst: Path) -> None:
    """`src` renamed to `dst` (never deleted, never over a file); nothing to
    do where `src` is gone and `dst` holds it."""
    if not src.exists() and not src.is_symlink():
        return
    if dst.exists():
        raise CodexError(f"refit: {dst} exists already; {src} is not set aside over it")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _replace_codex(shard: Path, old: Codex, new: Codex, sa: Path) -> None:
    """The Codex file replaced in ONE rename, never missing: the old bytes
    kept under the old id's folder first, then the new ones renamed over it
    (the independent read of d40a4731, 8). Repeatable."""
    from . import codex as cx
    here = shard / "codex.json"
    if here.is_file() and cx.loads(here.read_bytes()).collection_id == old.collection_id:
        if not (sa / "codex.json").is_file():
            _write_atomic(sa / "codex.json", here.read_bytes())
        _write_atomic(here, new.canonical())


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
    """The steps (module docstring), from where `state/refit.json` says,
    under the worker's node-wide lock and every affected collection's."""
    from . import records as rec
    root = Path(root)
    m = read_marker(root, name)
    if m is not None and m.get("done"):
        _file_marker(root, name, m)
        log(f"[refit] {name}: done already ({m['to'][:23]}…)")
        return m
    old = _predecessor(root, name, m)
    new = new_codex(old, layers)
    if m is not None and m["to"] != new.collection_id:
        raise CodexError(f"refit: {name} records a refit to {m['to']}; these params make {new.collection_id}")
    names = [name, *(m["others"] if m is not None else _others(root, name, old.collection_id))]
    locks = take_locks(root, names)
    coll = rec._local_collection(root, name, old)
    try:
        if key is None:
            raise _refused(NO_KEY)
        if m is None:
            p = plan(root, name, layers, key=key, window=window, processes=processes, log=log)
            # the index writers TAKEN before the marker is saved: decided first, saved after
            _index_writers(coll, p["changed"])
            m = {"kind": "louise-refit", "from": old.collection_id, "to": new.collection_id,
                 "changed": p["changed"], "parts": p["parts"], "latest": p["latest"], "others": p["others"],
                 "others_to": p["others_to"], "others_latest": p["others_latest"], "quarantine": p["quarantine"],
                 "steps": {}, "done": False}
            _save(root, name, m)
            log(f"[refit] {name}: {len(p['changed'])} document(s) cut otherwise "
                f"({', '.join(c[1] for c in p['changed'][:SHOWN])}), in {len(p['parts'])} part(s)")
            for line in p["effects"]:
                log(f"[refit] {name}: {line}")
        return _steps(root, name, old, new, m, key=key, now=now, window=window, log=log)
    finally:
        _release_index_writers(coll)
        for lk in reversed(locks):
            lk.release()


def _steps(root: Path, name: str, old: Codex, new: Codex, m: dict, *, key, now: str, window: int,
           log) -> dict[str, Any]:
    from app import db, embeddings
    from . import collection as col, records as rec
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
        _replace_codex(shard, old, new, sa)
        _aside(shard / "snapshots", sa / "snapshots")
        done("codex")
        log(f"[refit] {name}: the new Codex written, {new.collection_id[:23]}…; the old one and its "
            f"snapshots set aside in {sa}")
    if not steps.get("parts"):
        done("parts", snapshot=_parts(root, name, new, m, sa, key=key, now=now, log=log))
    if not steps.get("identity"):
        done("identity", **rekey(root, name, old, new, key=key, now=now,
                                 rewritten=[(p["slice"], p["n"]) for p in m["parts"]],
                                 latest=(m.get("latest") or {}).get("hash"), log=log))
    if not steps.get("others"):
        moved = {}
        for other in m["others"]:
            moved[other] = rekey_annotations(root, other, old.collection_id, new.collection_id, key=key, now=now,
                                             latest=(m["others_latest"].get(other) or {}).get("hash"), log=log)
        done("others", moved=moved)
    if not steps.get("platform"):
        done("platform", quarantine=_rekey_quarantine(m.get("quarantine"), log))
    if not steps.get("attest"):
        done("attest", **_attest(root, name, new, [p["path"] for p in m["parts"]], key, log))
    m["done"] = True
    _save(root, name, m)
    _file_marker(root, name, m)
    log(f"[refit] {name}: done — {new.collection_id[:23]}…; restart the node (`midclt call app.start "
        f"louise-rag`): the worker cuts, indexes and embeds the {len(changed)} document(s) and gates its run for "
        f"the new space, search of {name} withheld until it passes")
    return m


def _file_marker(root: Path, name: str, m: dict) -> None:
    """A finished refit's marker filed with what it set aside: nothing names
    the old id in the collection's state after it."""
    _aside(root / name / "state" / MARKER, root / name / "state" / ASIDE / _hex(m["from"]) / MARKER)


def _parts(root: Path, name: str, new: Codex, m: dict, sa: Path, *, key, now, log) -> str | None:
    """The parts holding a changed document written again from their own
    lines (the old version set aside), their build-database rows updated,
    then the new Codex's snapshot under the old LATEST id, every other
    part's entry and writer kept (`snapshot.create(writers_from=)`), signed."""
    from . import collection as col, records as rec, snapshot as sn
    latest = m.get("latest")
    if latest is None:
        log(f"[refit] {name}: it froze no snapshot: no part to write again")
        return None
    prev = sn.load(sa / "snapshots" / f"{latest['id']}.json")
    shard = col.shard_dir_of(root, name)
    t_coll = rec.local_collection(root, name, new)
    db_path = _build_db(root, name)
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


def rekey(root: Path, name: str, old: Codex, new: Codex, *, key, now: str, rewritten: list,
          latest: str | None, log) -> dict[str, Any]:
    """Every file under `name` naming the old id done as `disposition` says
    (decided at the plan): the collection file's `codex` line, the
    manifest's pin and cursors, this node's own attestations over the latest
    snapshot signed again, RAGE settings signed again, carry records and
    the cascade's record re-pointed; what only the old id's checks, gate,
    fetch, publication or bundles wrote set aside under state/.replaced/<old
    id>/. Held to empty after."""
    from app import collection_file
    from . import carry as carry_mod, deposit, pin_run
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
    out["resigned"] = _resign_attestations(root, name, old, new, key, rewritten, latest)
    _dispatch(root, name, old.collection_id, new.collection_id, ta, key=key)
    _forget_publisher(name)
    log(f"[refit] {name}: the old id replaced everywhere ({out})")
    return out


def _dispatch(root: Path, name: str, old_id: str, new_id: str, ta: Path, *, key) -> None:
    """Every file of `name` naming `old_id` done as `disposition` said at the
    plan — re-keyed to `new_id`, signed again, or set aside under `ta` (a
    directory of `WHOLE` at once) — then held to none left."""
    from . import pin_run
    from .cascade import WOKEN, write_woken
    oh = _hex(old_id)
    base = root / name
    wholes: set[str] = set()
    for rel in pin_run._sweep(root, name, old_id):
        if _skipped(rel, oh) or rel.startswith(f"state/attestations/{oh}/"):
            continue                                   # the old id's attestations: their folder, below
        p = base / rel
        what, _said = disposition(root, name, rel, old_id, key=key)
        whole = _whole_of(rel)
        if whole:
            wholes.add(whole)
        elif what == REKEYED and rel == f"state/{WOKEN}":
            body = json.loads(p.read_text(encoding="utf-8"))
            write_woken(root, name, json.loads(json.dumps(body).replace(old_id, new_id)))
        elif what == REKEYED:
            pin_run._repoint_json(p, old_id, new_id)
        elif what == RESIGNED:
            pin_run._resign_rage(p, old_id, new_id, key)
        elif what == SET_ASIDE:
            _aside(p, ta / rel)
        else:
            raise CodexError(f"refit: {rel} names {old_id[:23]}… after its step ({what})")
    for whole in sorted(wholes):
        _aside(base / whole, ta / whole)
    if (base / "state" / "attestations" / oh).exists():
        _aside(base / "state" / "attestations" / oh, ta / "state" / "attestations" / oh)
    left = [r for r in pin_run._sweep(root, name, old_id) if not _skipped(r, oh)]
    if left:
        raise CodexError(f"refit: {', '.join(left[:5])} still name {old_id[:23]}…")


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


def _resign_attestations(root: Path, name: str, old: Codex, new: Codex, key, rewritten: list,
                         latest: str | None) -> int:
    """This node's own attestations the plan said to sign again
    (`_attestation_fate`: a pass over the old LATEST snapshot and no part
    written again), signed for the new id and the new snapshot, each id in
    it re-keyed: quarantine reads them by the id. The others are set aside
    with the old id's folder, never signed again."""
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
        body = _read_attestation(f)
        if _attestation_fate(body, old.collection_id, me=me, latest=latest, dropped=dropped)[0] != RESIGNED:
            continue
        nb = json.loads(json.dumps(body).replace(old.collection_id, new.collection_id))
        nb.update(snapshot_hash=snap.hash, snapshot_id=snap.snapshot_id)
        doc = env.sign(env.ATTESTATION, nb, key)
        _write_atomic(out_dir / f"{_hex(env.body_id(doc['body']))}.json", cx.canonical_json(doc))
        n += 1
    return n


def rekey_annotations(root: Path, name: str, old_target: str, new_target: str, *, key, now: str,
                      latest: str | None, log) -> dict[str, Any]:
    """An annotations collection naming `old_target` in its Codex given the
    new target id: its Codex rewritten (a new identity of its own), every
    snapshot rewritten for it in order (its `previous` chained again, its
    members' hashes this Codex's) and signed again, and its old id replaced
    everywhere the same way (`rekey`). Resumable: a step done is seen."""
    from . import codex as cx, collection as col, shard as shard_mod, snapshot as sn
    from .annotations import files_of
    from .codex import targets_of
    shard = col.shard_dir_of(root, name)
    held = cx.loads((shard / "codex.json").read_bytes())
    mk = root / name / "state" / TARGET_MARKER
    m = json.loads(mk.read_text(encoding="utf-8")) if mk.is_file() else None
    if m is None:
        if not any(t.collection_id == old_target for t in targets_of(held)):
            return {"already": held.collection_id}
        m = {"from": held.collection_id, "to": _retargeted(held, old_target, new_target).collection_id}
        _write_atomic(mk, json.dumps(m, sort_keys=True).encode("utf-8"))
    old = held if held.collection_id == m["from"] else cx.loads(
        (shard / ASIDE / _hex(m["from"]) / "codex.json").read_bytes())
    new = _retargeted(old, old_target, new_target)
    sa = shard / ASIDE / _hex(old.collection_id)
    _replace_codex(shard, old, new, sa)
    if not (sa / "snapshots").exists():
        _aside(shard / "snapshots", sa / "snapshots")       # once: what is there after it is the new Codex's
    prev_hash = None
    (shard / "snapshots").mkdir(exist_ok=True)
    for p in sorted(q for q in (sa / "snapshots").glob("*.json") if not q.name.endswith(".sig.json")):
        s = sn.load(p)
        out = shard / shard_mod.snapshot_path(s.snapshot_id)
        ns = dataclasses.replace(s, collection_id=new.collection_id, previous=prev_hash,
                                 files=files_of(shard, s.snapshot_id, new))
        if not out.is_file():
            _write_atomic(out, ns.canonical())
        if key is not None:
            sn.sign_snapshot(root, name, sn.load(out), key, now=now)
        prev_hash = sn.load(out).hash
    got = rekey(root, name, old, new, key=key, now=now, rewritten=[], latest=latest, log=log)
    # ...and what names its TARGET's old id (the cascade's record of the runs)
    _dispatch(root, name, old_target, new_target, root / name / "state" / ASIDE / _hex(old.collection_id), key=key)
    _aside(mk, root / name / "state" / ASIDE / _hex(old.collection_id) / TARGET_MARKER)
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
