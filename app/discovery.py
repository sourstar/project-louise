"""Corpus discovery — idempotent per-collection sync against the manifest.

Cheap gate: (mtime, size) from the plugin's discover() pass. Only when that
changes do we call fetch() to hash content; only when the hash actually
differs do we reset downstream stage flags for that one document. A
no-change run is just a stat scan — discover() never touches file content.
"""
import contextlib
import dataclasses
import sqlite3
import time
from typing import Any

from . import db
from .plugins.base import DiscoverState, DocRef
from .plugins import cursor



def _clear_cursor_if_manifest_is_new(con, collection) -> bool:
    """Drop a scan cursor that has outlived its manifest.

    The cursor and the manifest are two halves of one fact — "these
    containers are already enumerated INTO THIS MANIFEST" — and nothing
    tied them together. They live in the same directory, so a manifest
    that is replaced while `state/` is kept leaves a cursor claiming work
    the new manifest never did, and every container it names is skipped as
    unchanged. Permanently: the skip writes no rows, so nothing ever
    disagrees with the cursor again.

    Found on the migrated pubmed collection, 2026-09-11: 150 containers of
    30,000 documents each — 4,500,000 documents, an exact round number
    because it is 150 whole files — were in the cursor and in neither the
    manifest nor any error log. The records build then ran against the
    short manifest and produced a SHARD that verified internally while
    being 11% incomplete, which is precisely the artefact docs/01 promise 8
    is about.

    One `LIMIT 1` read: an EMPTY manifest with a non-empty cursor is the
    signature, and it cannot be anything legitimate — a cursor is only
    ever written after rows were committed to the manifest beside it.
    """
    state_dir = collection.manifest_path.parent
    plugin = collection.plugin
    path = state_dir / f"{plugin}_scan_cursor.json"
    if not path.is_file():
        return False
    try:
        if con.execute("SELECT 1 FROM documents LIMIT 1").fetchone() is not None:
            return False
    except Exception:       # noqa: BLE001 — no documents table yet is "new"
        pass
    if not cursor.load(state_dir, plugin):
        return False
    cursor.clear(state_dir, plugin)
    db.log_event(con, "warn", "discover",
                 f"scan cursor for {plugin!r} named containers this manifest "
                 f"has no documents from — it outlived the manifest beside "
                 f"it. Cleared; this scan re-enumerates the whole corpus")
    return True


def _clear_cursor_if_relocated(con, collection, root) -> bool:
    """True when stored locators no longer live under the corpus root, in
    which case the scan cursor is dropped so discovery re-enumerates.

    Deliberately tolerant: a plugin whose locator is not a filesystem path
    under the root (or a collection with nothing discovered yet) simply
    reads as "not relocated" and nothing happens.
    """
    try:
        row = con.execute("SELECT path FROM documents "
                          "WHERE path IS NOT NULL LIMIT 1").fetchone()
    except sqlite3.Error:
        return False
    if row is None or not row["path"]:
        return False
    stored = str(row["path"])
    prefix = str(root)
    if stored.startswith(prefix):
        return False
    from .plugins import cursor as scan_cursor
    scan_cursor.clear(collection.manifest_path.parent, collection.plugin)
    db.log_event(con, "info", "discover",
                 f"corpus moved (stored locators point outside {prefix}); "
                 f"scan cursor cleared so locators can be refreshed")
    return True


# Refs per manifest transaction. sync_collection used to run in autocommit
# — one transaction per row — and the "COMMIT every 2000" below it never
# fired because nothing had BEGUN one. Measured live on wikivec: 1.5 M rows
# in 1,083 s, almost all of it SQLite per-statement commit overhead (the
# same economics rdf batching fixed: each dirty page hits the WAL once per
# batch instead of once per row). An interrupted batch re-yields next scan
# — the cursor only advances past containers whose rows are committed.
DISCOVER_BATCH_REFS = 2000


def sync_collection(con, collection, *, stop=None, adopt: bool = False) -> dict:
    """Enumerate the corpus and upsert what changed. Streams; commits every
    DISCOVER_BATCH_REFS refs and tells the plugin (DiscoverState.committed,
    on_commit) so its scan cursor can be saved as it goes. `stop` (an
    object with is_set(), e.g. the worker's SIGTERM event) ends the scan at
    the next ref: the batch so far is committed, the cursor covers exactly
    the containers whose rows are durable, and the result carries
    `interrupted=True` — the next scan resumes from there. Invariant 2: the
    resumable unit of a scan is one batch of refs.

    `adopt` is `louise dev carry --resign`'s and nothing else's (02 §2.4
    A1d): a document whose signature or locator moved takes the new ones as
    a fresh discovery would write them — `txt_hash` unset — and keeps every
    stage mark, because its text is provably the same text. Never fetches."""
    t0 = time.time()
    plugin = collection.plugin_instance()
    root = collection.corpus_dir
    if not root.is_dir():
        if getattr(collection, "slices", None) == ():
            # holds no slice (07 §7.8 `slices = []`): nothing was fetched,
            # so there is no origin yet and nothing to scan — not an error
            return {"seen": 0, "added": 0, "changed": 0, "missing_txt": 0,
                    "secs": 0.0}
        db.log_event(con, "error", "discover", f"corpus dir not found: {root}")
        return {"error": f"corpus dir not found: {root}"}

    # HAS THE CORPUS MOVED? Locators are absolute paths, and the scan cursor
    # is keyed on a container's BASENAME plus (mtime, size) — so a dataset
    # remounted somewhere else looks completely unchanged to it, every
    # container is skipped, and not one ref is emitted to carry the new
    # path. The stored locators then point at a mount that no longer exists
    # and every stage fails with FileNotFoundError.
    #
    # Observed live: renaming the genome collection's ZFS dataset blocked all
    # 17 of its documents, and rescanning did not help because the file
    # genuinely had not changed.
    #
    # One LIMIT 1 read answers it, so collections that have NOT moved pay a
    # single indexed row read and nothing else. Clearing the cursor is the
    # right hammer only here: re-enumeration is what regenerates the
    # locators, and the relocation branch below then updates them without
    # touching any stage flag (the bytes are the same bytes).
    _clear_cursor_if_relocated(con, collection, root)
    _clear_cursor_if_manifest_is_new(con, collection)

    state = DiscoverState(corpus_root=root,
                           state_dir=collection.manifest_path.parent,
                           snapshot=getattr(collection, "snapshot", None),
                           stop=stop)
    seen = 0
    # STREAMING with last-write-wins BY ORDER. A source may yield the same
    # uid more than once in one sweep (PubMed updatefiles revising a baseline
    # record; a delete tombstone after the record it retracts) — plugins
    # enumerate containers in sorted/chronological order, so applying refs in
    # arrival order gives the same final state the old buffered de-dup did:
    # the later ref hits the ordinary mtime/size→hash change gate and wins.
    # The buffer itself had to go: a cold-cursor scan of full PubMed is ~26M
    # refs — the "revisit at millions of records" moment the previous
    # docstring named, arrived. Progress commits as the scan streams, one
    # transaction per DISCOVER_BATCH_REFS, which makes an interrupted giant
    # scan resumable: rows written so far stand, and the plugin's cursor is
    # only advanced past containers whose rows are committed.
    counts = {"seen": 0, "added": 0, "changed": 0, "missing": 0,
              "container_errors": 0, "relocated": 0, "resigned": 0, "retired": 0,
              "superseded": 0, "withdrawn": 0}

    withdraw = withdrawer(collection)
    # a manifest empty when the scan begins holds no row a new id supersedes
    superseded = con.execute("SELECT 1 FROM documents LIMIT 1").fetchone() is not None
    interrupted = False
    applied = 0                 # refs durably written so far
    batch: list[DocRef] = []
    batch_uids: set[str] = set()
    refs = plugin.discover(state)
    for ref in refs:
        if stop is not None and stop.is_set():
            interrupted = True
            if hasattr(refs, "close"):
                refs.close()
            break
        uid = f"{ref.source_id}:{ref.native_id}"
        # A batch is decided against the manifest BEFORE its writes, so two
        # refs for one uid in the same batch (a PubMed revision following
        # its baseline record, a tombstone after the record it retracts)
        # would both plan as new. Flushing first keeps last-write-wins by
        # arrival order exact: the later ref sees the earlier one committed.
        if batch and (uid in batch_uids or len(batch) >= DISCOVER_BATCH_REFS):
            if not _apply_batch(con, plugin, batch, counts, stop, wait=True, adopt=adopt,
                                withdraw=withdraw, collection=collection, superseded=superseded):
                interrupted = True      # stop arrived while the manifest was contended
                batch = []
                break
            applied += len(batch)
            _signal_committed(state, applied)
            batch, batch_uids = [], set()
        batch.append(ref)
        batch_uids.add(uid)
        state.received = applied + len(batch)
    if batch:
        # the tail (or the batch a stop request cut short): one attempt if
        # stopping — a contended manifest must not hold SIGTERM past its
        # grace; what is not committed is simply re-enumerated next scan
        if _apply_batch(con, plugin, batch, counts, stop, wait=not interrupted, adopt=adopt,
                        withdraw=withdraw, collection=collection, superseded=superseded):
            applied += len(batch)
            _signal_committed(state, applied)
        else:
            interrupted = True          # not committed: the next scan re-enumerates it
    if not interrupted and (counts["added"] or counts["changed"]
                            or counts["relocated"] or counts["missing"]):
        # a pure stat pass changed nothing in_scope could depend on; scope
        # CONFIG changes apply through their own endpoint (api set_scope)
        apply_scope(con)
    stats = {"seen": counts["seen"], "added": counts["added"],
             "changed": counts["changed"], "missing_txt": counts["missing"],
             "secs": round(time.time() - t0, 1)}
    if counts["relocated"]:
        stats["relocated"] = counts["relocated"]
    if counts["resigned"]:
        stats["resigned"] = counts["resigned"]
    if counts["container_errors"]:
        stats["container_errors"] = counts["container_errors"]
    if counts["superseded"]:
        stats["superseded"] = counts["superseded"]
    if counts["withdrawn"]:
        stats["withdrawn"] = counts["withdrawn"]
    if interrupted:
        stats["interrupted"] = True
        db.log_event(con, "info", "discover",
                     f"discovery interrupted (stop requested) — progress committed, "
                     f"resumes next scan {stats}")
    else:
        db.log_event(con, "info", "discover", f"discovery done {stats}")
    # an update by cadence waiting on this scan of its new run hears that
    # it read the run whole (app/hold/update.py `note_scan`): after the
    # last commit, by whichever process scanned
    try:
        from .hold import update as update_mod
        update_mod.note_scan(collection, stats)
    except Exception as e:   # noqa: BLE001 — a scan never fails on its note
        db.log_event(con, "warn", "discover", f"the update marker was not told of this scan: {e}"[:300])
    return stats


def withdrawer(collection):
    """06 §6.1's deletion signal for `_apply_batch`: what the origin
    retracted, or a row a unit no longer yields, leaves every index it is in
    (app/unindexable.py) — before the batch writing its tombstone commits,
    so a stop in between re-enumerates and withdraws it again, never leaves
    one marked that still answers."""
    def withdraw(c, uids) -> None:
        from . import unindexable
        unindexable.withdraw_many(c, collection, uids)
    return withdraw


def register(con, collection, refs: list[DocRef], *, stop=None) -> dict:
    """The given refs into the manifest as a scan applies them
    (`_apply_batch`), and nothing else: a cost per ref, never a walk of the
    corpus — for units the worker itself just placed (app/ask/history.py
    `adopt`). A scope config that narrows anything is re-applied; the slice
    pass (app/slice_scope.py) is the caller's, as after a scan."""
    counts = {"seen": 0, "added": 0, "changed": 0, "missing": 0, "container_errors": 0, "relocated": 0,
              "retired": 0,
              "resigned": 0, "superseded": 0, "withdrawn": 0}
    refs = [r for r in refs if r is not None]
    if not refs:
        return counts
    plugin = collection.plugin_instance()
    # its retirements and its language moves withdraw from THIS collection's
    # indexes, as a scan's do (architecture's review of #799, 2026-11-23: called
    # without either, a retired row's index rows stayed, and a regroup read
    # the default collection's)
    if not _apply_batch(con, plugin, refs, counts, stop, wait=True,
                        withdraw=withdrawer(collection), collection=collection):
        counts["interrupted"] = True
        return counts
    scope = db.get_scope(con)
    if counts["added"] and any(scope.get(k) for k in ("languages", "book_ids", "sample_per_lang", "limit")):
        apply_scope(con)
    db.log_event(con, "info", "discover", f"registered {len(refs)} unit(s) placed by the worker: "
                                          f"added {counts['added']}, changed {counts['changed']}")
    return counts


def _signal_committed(state, n: int) -> None:
    state.committed = n
    for hook in state.on_commit:
        hook()


def _superseded(order, ref, row) -> bool:
    """LAST WRITE WINS BY THE ORIGIN'S ORDER, not by arrival. A plugin whose
    origin supersedes a document by a later unit (PubMed: a revision in a
    later update file, 06 §6.2) declares `origin_order(locator)`; a ref from
    an EARLIER place than the unit the row holds is superseded and changes
    nothing. Without it a cleared cursor — a relocation, the Library's
    rescan, a new manifest — re-applied every baseline record over its
    revision and then the revision over it again: two changes and a re-embed
    for every revised document, the old text served in between."""
    if order is None or row is None or not row["path"]:
        return False
    here, held = order(ref.locator), order(row["path"])
    return here is not None and held is not None and here < held


def _other_unit(order, ref, row) -> bool:
    """The ref names ANOTHER origin unit than the row (a PubMed record in a
    later update file), which is a content question whatever the two
    containers' (mtime, size) — equal by chance, they read as a relocation
    and the revision was never hashed. A moved corpus keeps every unit's
    place, so it is not this."""
    if order is None or row is None or not row["path"]:
        return False
    here, held = order(ref.locator), order(row["path"])
    return here is not None and held is not None and here != held


def _held_hash(plugin, ref, row) -> str | None:
    """THE HELD UNIT'S HASH where the row never recorded one: a row the first
    scan wrote has no `txt_hash` (discovery hashes on a LATER pass, when its
    signature moves), so the next run of a dated dump (02 §2.7, amended
    2026-11-17) read every such page as changed — re-parsed, re-indexed and
    re-embedded though not a byte of it moved. While the unit the row still
    names is on disk (the previous run stays until the next snapshot is
    published, 06 §6.2), its bytes are hashed and compared instead; gone, or
    unreadable, it proves nothing, and the page is changed, as before. For a
    plugin whose next run moves a unit's locator and nothing else
    (`ORIGIN_MOVES_ALONE`, wikipedia); PubMed's update files supersede by
    content and keep their own path (`_other_unit`, unchanged)."""
    if not getattr(plugin, "ORIGIN_MOVES_ALONE", False) or not row["path"] or row["path"] == ref.locator:
        return None
    try:
        held = plugin.fetch(dataclasses.replace(ref, locator=row["path"]))
    except Exception:   # noqa: BLE001 — the old unit unreadable: no evidence
        return None
    return held.raw_hash


def hash_one(plugin, task: dict) -> tuple[str, str | None]:
    """The parse pool's "discover-hash" work, one ref (app/parallel.py): the
    hash of the bytes `fetch` gives for the ref, and — where the row names
    another unit and never recorded a hash — the held unit's (`_held_hash`).
    A fetch of the ref that fails raises: the caller then fetches it here, in
    the scan's own process, and meets the same failure the serial scan did."""
    ref = task["ref"]
    raw = plugin.fetch(ref)
    held = task.get("held")
    return raw.raw_hash, (_held_hash(plugin, ref, {"path": held}) if held else None)


def _hashes(plugin, collection, need: list) -> dict[str, tuple[str, str | None]]:
    """THE HASHES A BATCH NEEDS, ON THE PARSE POOL (CLAUDE.md: multi-threading
    is a must): `uid -> (the ref's raw hash, the held unit's or None)` for
    every `(uid, ref, row)` whose signature moved. A dated dump's next run
    moves every page's signature, so its scan is ALL hashing — decompress
    the page's stream, re-serialise it, hash it, and the old run's copy too
    (`_held_hash`) — and on the scan's one thread it was ~1,700 pages/s
    whatever the node's cores (2026-11-17, 50,500 synthetic pages). The rows
    go to `parallel.compute` with the ref's locator as their `path`, so the
    pool cuts the batch along wikipedia's multistream chunks
    (`plugin_io.work_span`) and a chunk is decompressed once. In order, one
    answer per ref; a ref whose task failed is left out and fetched by the
    caller in-process. No pool (one process, the tests) or no `collection`
    (`register`): computed here, the same function."""
    if not need:
        return {}
    held_too = bool(getattr(plugin, "ORIGIN_MOVES_ALONE", False))
    tasks = [{"uid": uid, "native_id": ref.native_id, "path": ref.locator, "ref": ref,
              "held": (row["path"] if held_too and not row["txt_hash"] and row["path"]
                       and row["path"] != ref.locator else None)}
             for uid, ref, row in need]
    if collection is None:
        out = {}
        for t in tasks:
            # a failure is left out: fetched again by the caller, which raises as before
            with contextlib.suppress(Exception):
                out[t["uid"]] = hash_one(plugin, t)
        return out
    from . import parallel
    got = parallel.compute("discover-hash", collection, tasks)
    return {t["uid"]: tuple(v) for t, (ok, v) in zip(tasks, got) if ok}


def _forget_validity(con, uid: str) -> None:
    """A retired document's validity row out, its Work's chain recomputed
    (inside the batch's transaction)."""
    from app import validity
    validity.forget(con, uid)


def _apply_batch(con, plugin, batch: list, counts: dict, stop, *, wait: bool,
                 withdraw, collection, adopt: bool = False, superseded: bool = True) -> bool:
    """Apply one batch of refs to the manifest. Two phases, so the write
    lock is held only for the writes:

    1. DECIDE, holding nothing: read each document's row, and for one whose
       (mtime, size) moved call fetch() to hash it — seconds on a gz
       container or a parquet shard, which must never sit inside a
       transaction that rdf/fts are waiting on.
    2. WRITE under `BEGIN IMMEDIATE` — the RESERVED lock taken up front so a
       concurrent commit makes us WAIT (busy_timeout) rather than fail: a
       deferred BEGIN that had already read would fail its upgrade
       instantly (WAL snapshot conflict), which is exactly what happened the
       first time this scan ran beside the pipeline thread. An embed step
       elsewhere can hold the lock longer than busy_timeout, so a lock
       failure is retried (with `wait`) until it succeeds or `stop` says
       otherwise; `counts` are merged only on commit.

    A ref the origin's order supersedes (`_superseded`) is counted and
    changes nothing. A DELETION (`DocRef.deleted`, 06 §6.1) of a document
    the indexes hold is withdrawn from them by `withdraw` in the decide
    phase, before its tombstone is written, and its stage marks are reset so
    that a later re-add is indexed again.

    Returns False only when the batch could not be committed (stop while
    contended, `wait=False`, or a withdrawal the indexes refused); the
    caller then treats the scan as interrupted — nothing half-applied,
    nothing in the cursor past it."""
    now = int(time.time())
    local = {k: 0 for k in counts}
    writes: list[tuple[str, tuple]] = []
    order = getattr(plugin, "origin_order", None)
    to_withdraw: list[str] = []
    regroup: dict[str, list[int]] = {}       # group -> documents whose language left it
    # THE ROWS AND THE HASHES FIRST: every document's row read once, and the
    # fetch-and-hash of those whose signature moved done for the whole batch
    # at once on the parse pool (`_hashes`) — the per-ref decisions below are
    # the same, in the same order, from the same values (nothing is written
    # before phase 2, so a row read here is the row the loop would read)
    rows_of: dict[str, Any] = {}
    need: list = []
    for ref in batch:
        if ref.scope == "container" or ref.error:
            continue
        uid = f"{ref.source_id}:{ref.native_id}"
        row = con.execute("SELECT path,mtime,size,txt_hash,lang,fts_done,fts_version,native_id,error "
                          "FROM documents WHERE uid=?", (uid,)).fetchone()
        rows_of[uid] = row
        if row is None or adopt or _superseded(order, ref, row):
            continue
        if row["mtime"] != ref.mtime or row["size"] != ref.size or _other_unit(order, ref, row):
            need.append((uid, ref, row))
    hashed = _hashes(plugin, collection, need)
    for ref in batch:
        uid = f"{ref.source_id}:{ref.native_id}"
        if ref.scope == "container":
            # A container that could not be enumerated is a scan REPORT, not
            # a document (DocRef.scope). Storing it as one minted a
            # filename-keyed row nothing ever removed, which an operator's
            # "clear errors & retry" then fed to the rdf stage forever.
            # Logging it keeps it visible (the System page merges every
            # collection's events) and lets a later clean scan heal it by
            # simply not reporting it again.
            local["container_errors"] += 1
            db.log_event(con, "warn", "discover",
                         f"container not enumerable: {ref.error}", None)
            continue      # a scan report is not a document, so it is not "seen"
        local["seen"] += 1
        if ref.error:
            held = None
            if order is not None or getattr(ref, "deleted", False):
                held = con.execute(
                    "SELECT path,error,rdf_done,fts_done,embed_done FROM documents WHERE uid=?",
                    (uid,)).fetchone()
            if _superseded(order, ref, held):
                local["superseded"] += 1
                continue
            local["missing"] += 1
            if getattr(ref, "deleted", False):
                # the origin retracted it: out of every index it is in, and
                # its marks reset (a re-add is a new document to index). The
                # row keeps its locator — the last unit that supplied the
                # record, which its line in a part still names (02 §2.2)
                if held is not None and held["error"] is None and (
                        held["rdf_done"] or held["fts_done"] or held["embed_done"]):
                    to_withdraw.append(uid)
                elif held is not None and held["error"] == ref.error and not (
                        held["rdf_done"] or held["fts_done"] or held["embed_done"]):
                    continue                # tombstoned already: applied twice, nothing changes
                writes.append((
                    "INSERT INTO documents(uid,source,native_id,path,error,updated_at) "
                    "VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(uid) DO UPDATE SET error=excluded.error,rdf_done=0,fts_done=0,"
                    "embed_done=0,summary_done=0,updated_at=excluded.updated_at",
                    (uid, ref.source_id, ref.native_id, ref.locator, ref.error, now)))
                continue
            writes.append((
                "INSERT INTO documents(uid,source,native_id,path,error,updated_at) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET error=excluded.error",
                (uid, ref.source_id, ref.native_id, ref.locator, ref.error, now)))
            continue
        txt_name = ref.extra.get("txt_name")
        # `lang` WHEN THE PLUGIN CAN SUPPLY IT CHEAPLY, and it is worth an
        # explicit column rather than being left to the rdf stage.
        # `slicing.slice_hint` exists so the planning pass does not cost a
        # full parse per document, and gutenberg's hint is the manifest's
        # `lang` — which nothing set until the rdf stage, long after
        # `records build` needs it. Measured 2026-09-10 on 77,866 books:
        # the hint never fired, pass 1 parsed every book SINGLE-THREADED to
        # learn its language, 19.2 documents/s for 68 minutes, and pass 2
        # then parsed the same corpus again. Every other plugin's hint
        # answers from the locator or a derived index; gutenberg's was the
        # one that could not.
        lang = ref.extra.get("lang")
        row = rows_of[uid] if uid in rows_of else con.execute(
            "SELECT path,mtime,size,txt_hash,lang,fts_done,fts_version,native_id,error FROM documents "
            "WHERE uid=?",
            (uid,)).fetchone()
        if _superseded(order, ref, row):
            local["superseded"] += 1
            continue
        revived = row is not None and str(row["error"] or "").startswith("superseded")
        if revived:
            # a unit whose form came back to an id it had: that row is the
            # document again, derived afresh; its sibling is retired below
            writes.append((
                "UPDATE documents SET path=?,txt_name=?,mtime=?,size=?,txt_hash=NULL,error=NULL,retries=0,"
                "rdf_done=0,fts_done=0,embed_done=0,summary_done=0,updated_at=? WHERE uid=?",
                (ref.locator, txt_name, ref.mtime, ref.size, now, uid)))
            local["changed"] += 1
        if row is None:
            writes.append((
                "INSERT INTO documents(uid,source,native_id,path,txt_name,lang,"
                "mtime,size,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (uid, ref.source_id, ref.native_id, ref.locator, txt_name, lang,
                 ref.mtime, ref.size, now)))
            local["added"] += 1
        if row is None or revived:
            # ONE UNIT, ONE DOCUMENT (2026-11-21: EUR-Lex5 held 37,695 units
            # twice — a unit re-formed into a Formex package took its id from
            # the wrapper's OJ day, its old row the tree's, and nothing ever
            # retired the old one). A new id at a unit: the ids the plugin says
            # the same unit could have had, held at this very locator, are
            # retired — out of every index, their validity row forgotten,
            # tombstoned with the reason. Asked of new ids only: nothing in
            # steady state, a primary-key read each when a form changes.
            # asked only where a row can be held under another id: a manifest
            # empty when the scan began holds none (architecture's review of
            # #799, 2026-11-23: EUR-Lex's answer unpacks the package and
            # parses the tree again, doubling a first scan on its one thread)
            alts = getattr(plugin, "superseded_ids", None) if superseded else None
            for alt in (alts(ref) if alts is not None else ()):
                uid_alt = f"{ref.source_id}:{alt}"
                if uid_alt == uid:
                    continue
                held = con.execute("SELECT path,error,rdf_done,fts_done,embed_done FROM documents "
                                   "WHERE uid=?", (uid_alt,)).fetchone()
                if held is None or held["path"] != ref.locator or str(held["error"] or "").startswith(
                        "superseded"):
                    continue
                if held["rdf_done"] or held["fts_done"] or held["embed_done"]:
                    to_withdraw.append(uid_alt)
                writes.append((_forget_validity, (uid_alt,)))
                writes.append((
                    "UPDATE documents SET error=?,rdf_done=0,fts_done=0,embed_done=0,summary_done=0,"
                    "updated_at=? WHERE uid=?",
                    (f"superseded by {uid}: the same unit under another id (one unit, one document)",
                     now, uid_alt)))
                local["retired"] += 1
        elif adopt and (row["mtime"] != ref.mtime or row["size"] != ref.size
                        or row["path"] != ref.locator):
            # A1d: the new parser's signature and locator, as a fresh scan
            # would write them (no txt_hash), every stage mark kept — the
            # caller proves the text unchanged (hold/carry.py `resign`)
            writes.append((
                "UPDATE documents SET path=?,txt_name=?,lang=COALESCE(?,lang),"
                "mtime=?,size=?,txt_hash=NULL,updated_at=? WHERE uid=?",
                (ref.locator, txt_name, lang, ref.mtime, ref.size, now, uid)))
            local["resigned"] += 1
        elif (row["mtime"] != ref.mtime or row["size"] != ref.size
              or _other_unit(order, ref, row)):
            if uid in hashed:                       # the pool's (`_hashes`), no lock held
                raw_hash, held_of = hashed[uid]
            else:                                   # its task failed: here, as before
                raw_hash = plugin.fetch(ref).raw_hash
                held_of = None if row["txt_hash"] else _held_hash(plugin, ref, row)
            held_hash = row["txt_hash"] or held_of
            if raw_hash != held_hash:
                # A LANGUAGE THAT LEAVES ITS GROUP here, not in the rdf stage
                # (2026-11-21: discovery wrote the new `lang` in this same
                # UPDATE, so the rdf stage saw no move and the old group's
                # rows stayed — the pubmed duplicate by another path)
                old = row["lang"]
                if (lang and old and lang != old and (row["fts_done"] or (row["fts_version"] or 0) > 0)
                        and row["native_id"] is not None):
                    from app import fts
                    for g in set(fts.groups_for_lang(collection, old)) - set(fts.groups_for_lang(collection, lang)):
                        regroup.setdefault(g, []).append(int(row["native_id"]))
                writes.append((
                    "UPDATE documents SET path=?,txt_name=?,lang=COALESCE(?,lang),"
                    "mtime=?,size=?,txt_hash=?,"
                    "rdf_done=0,fts_done=0,embed_done=0,summary_done=0,error=NULL,"
                    "retries=0,updated_at=? WHERE uid=?",
                    (ref.locator, txt_name, lang, ref.mtime, ref.size,
                     raw_hash, now, uid)))
                local["changed"] += 1
            elif row["path"] != ref.locator:
                # the same bytes, re-stamped AND somewhere else: the locator
                # is refreshed now, as the relocated branch below would on
                # the next scan — until then the stale path kept the corpus
                # reading as moved, and every start re-enumerated it
                writes.append((
                    "UPDATE documents SET path=?,txt_name=?,mtime=?,size=?,txt_hash=?,error=NULL,"
                    "retries=0,updated_at=? WHERE uid=?",
                    (ref.locator, txt_name, ref.mtime, ref.size, raw_hash, now, uid)))
                local["relocated"] += 1
            else:
                writes.append(("UPDATE documents SET mtime=?,size=? WHERE uid=?",
                               (ref.mtime, ref.size, uid)))
        elif row["path"] != ref.locator:
                # RELOCATED, not changed. The locator is an absolute path into
                # the corpus, so remounting a dataset somewhere else — a
                # rename, a move to another pool — invalidates every stored
                # locator while mtime and size stay exactly as they were. The
                # branch above can never notice: it is keyed on content
                # identity, and location is not content.
                #
                # Observed live: renaming the genome collection's dataset left
                # all 17 documents pointing at the old mount, and every one
                # failed the embed stage with FileNotFoundError. Rescanning did
                # not help, because the file really was unchanged.
                #
                # Stage flags are deliberately NOT reset: the bytes are the
                # same bytes, so the parse, the index and the vectors all stay
                # valid. The error and the retry count ARE cleared — the only
                # thing wrong was where we were looking, and a document
                # blocked for that reason should unblock itself rather than
                # wait for someone to press a button.
            writes.append((
                "UPDATE documents SET path=?,txt_name=?,error=NULL,retries=0,"
                "updated_at=? WHERE uid=?",
                (ref.locator, txt_name, now, uid)))
            local["relocated"] += 1

    if to_withdraw and withdraw is not None:
        # BEFORE the tombstones commit (app/unindexable.py's order): a stop or
        # a crash in between leaves the batch uncommitted, the cursor short of
        # it, and the next scan withdraws again — idempotent — never a
        # tombstoned document that still answers a search
        try:
            withdraw(con, to_withdraw)
        except Exception as e:  # noqa: BLE001 — an index busy or broken: deferred, nothing written
            db.log_event(con, "warn", "discover",
                         f"{len(to_withdraw)} retracted document(s) could not leave the "
                         f"indexes yet ({type(e).__name__}: {e}); the batch is deferred "
                         f"and retried next scan"[:400])
            return False
        local["withdrawn"] += len(to_withdraw)
    if regroup:
        # the old group's rows go before the marks commit (#769's order): a
        # stop between leaves rows a later scan or verify finds again
        from app import fts
        try:
            for g, books in sorted(regroup.items()):
                fts.delete_books(g, books, collection)
        except Exception as e:  # noqa: BLE001 — an index busy: deferred, nothing written
            db.log_event(con, "warn", "discover",
                         f"{sum(map(len, regroup.values()))} document(s) whose language changed could not "
                         f"leave their old text-index group yet ({type(e).__name__}: {e}); deferred"[:400])
            return False
    waited = False
    while True:
        try:
            con.execute("BEGIN IMMEDIATE")
            for sql, params in writes:
                if callable(sql):
                    sql(con, *params)
                else:
                    con.execute(sql, params)
            con.execute("COMMIT")
            break
        except sqlite3.OperationalError as e:
            try:
                if con.in_transaction:
                    con.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            if not wait or (stop is not None and stop.is_set()):
                return False
            if not waited:
                db.log_event(con, "warn", "discover",
                             f"discovery waiting for the manifest ({e}); the batch "
                             f"is intact and will be retried")
                waited = True
            time.sleep(2)
    for k, v in local.items():
        counts[k] += v
    return True


def apply_scope(con):
    """Recompute documents.in_scope from the current scope config.

    native_id is TEXT (generic across plugins); Gutenberg's ids are numeric,
    so ordering/comparison here casts to INTEGER to match pre-refactor
    int-`id` behavior exactly. A future non-numeric-id plugin will need a
    per-plugin ordering hook — out of scope for P0.1 (single Gutenberg
    collection).
    """
    scope = db.get_scope(con)
    # WHERE in_scope=0, not unconditional: `SET in_scope=1` over a 33M-row
    # manifest rewrites every page and holds the write lock for MINUTES —
    # observed live as "database is locked" errors in every other thread
    # the moment the startup scans ran beside the pipelines. With nothing
    # scoped out (the common case) this touches zero rows via the
    # idx_documents_scope prefix. Semantics identical: widen, then narrow.
    con.execute("UPDATE documents SET in_scope=1 WHERE in_scope=0")
    if scope["languages"]:
        marks = ",".join("?" for _ in scope["languages"])
        con.execute(
            f"UPDATE documents SET in_scope=0 WHERE lang IS NOT NULL "
            f"AND lang NOT IN ({marks})", scope["languages"])
    if scope["book_ids"]:
        marks = ",".join("?" for _ in scope["book_ids"])
        con.execute(
            f"UPDATE documents SET in_scope=0 WHERE CAST(native_id AS INTEGER) "
            f"NOT IN ({marks})", scope["book_ids"])
    if scope["sample_per_lang"]:
        n = int(scope["sample_per_lang"])
        con.execute("""
            UPDATE documents SET in_scope=0 WHERE in_scope=1 AND uid NOT IN (
              SELECT uid FROM (
                SELECT uid, ROW_NUMBER() OVER (
                  PARTITION BY lang ORDER BY CAST(native_id AS INTEGER)) rn
                FROM documents WHERE in_scope=1) WHERE rn<=?)""", (n,))
    if scope["limit"]:
        con.execute("""
            UPDATE documents SET in_scope=0 WHERE in_scope=1 AND uid NOT IN (
              SELECT uid FROM documents WHERE in_scope=1
              ORDER BY CAST(native_id AS INTEGER) LIMIT ?)""",
            (int(scope["limit"]),))
    # the widening above undid the slices' narrowing (app/slice_scope.py): its
    # pass starts again, and the collection's stages wait for it
    from . import slice_scope
    slice_scope.invalidate(con)
