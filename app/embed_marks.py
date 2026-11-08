"""Marking a document EMBEDDED in the manifest — one place for what three
writers did inline (the worker's embed stage twice, the old coordinator's
contribute) and one did not at all.

`share contribute`'s build (`hold.vectors_build.build`) wrote vectors and
never touched `documents`: the node had 372 in-scope documents with every
chunk in Lance and `embed_done = 0` (München, 2026-10-19) — the worker
would embed them again under `all local`, the Library counted them as
pending, a query's lazy path could re-embed them. A stage that writes a
document's last vector marks the document, AFTER the write (invariant 2:
marking first is the one ordering that loses data); and a document is
complete when every chunk the layers derive from its record has a vector
— `complete_uids` asks the vector table, never `documents.chunks`.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable, Sequence

from app import config


def mark_embedded(con, collection, uids: Iterable[str], *, synced: bool = True) -> int:
    """`embed_done = 1`, this `EMBED_VERSION`, `updated_at` now, for each uid;
    its imported-provenance rows go (the chunks are this peer's now) and
    its queue entry with them. `synced`: the rows just written carry a
    versioned collection's columns (the worker's writes do; a build through
    `embeddings.upsert_chunks` does not) — only then are they marked
    synced (app/validity.py). Returns how many rows the UPDATE touched."""
    from app import validity as validity_mod
    from app.bundles import ledger as bundle_ledger
    now = int(time.time())
    ids = [str(u) for u in uids]
    touched = 0
    for uid in ids:
        touched += con.execute(
            "UPDATE documents SET embed_done=1, embed_version=?, updated_at=? WHERE uid=?",
            (config.EMBED_VERSION, now, uid)).rowcount
        bundle_ledger.clear_verification(con, uid)
        con.execute("DELETE FROM embed_queue WHERE uid=?", (uid,))
    if synced and ids and validity_mod.declared(collection) is not None:
        native = []
        for u in ids:
            try:
                native.append(int(str(u).split(":", 1)[1]))
            except (IndexError, ValueError):
                continue
        if native:
            validity_mod.mark_synced(con, native)
    return touched


def complete_uids(codex, records: Sequence[dict],
                  vectors_for: Callable[[list[str]], dict[str, list]]) -> list[str]:
    """The uids among `records` whose every chunk has a vector here — the
    chunks through `layers.chunks_for_document` (invariant 1), the vectors
    through the caller's lookup. A record the layers give no chunk is
    complete (there is nothing to embed; the worker marks it done the same
    way), one with a chunk missing is not."""
    from app import layers as layers_mod
    per_uid: dict[str, list[str]] = {}
    for rec in records:
        uid = rec["uid"]
        row = {"uid": uid, "native_id": rec.get("native_id", 0)}
        sections = [tuple(s) for s in rec.get("sections") or []]
        per_uid[uid] = [c["id"] for c in layers_mod.chunks_for_document(codex, row, sections, title=rec.get("title"))]
    wanted = [i for ids in per_uid.values() for i in ids]
    held = vectors_for(wanted) if wanted else {}
    held = held or {}
    return [uid for uid, ids in per_uid.items() if all(i in held for i in ids)]


def mark_complete(con, collection, codex, records: Sequence[dict],
                  vectors_for: Callable[[list[str]], dict[str, list]], *,
                  only_unmarked: bool = True) -> int:
    """`complete_uids`, marked. `only_unmarked`: the rows with `embed_done =
    0` only (a reconcile that leaves the worker's own marks and their
    `updated_at` alone)."""
    uids = complete_uids(codex, records, vectors_for)
    if not uids:
        return 0
    if only_unmarked:
        pending: list[str] = []
        for i in range(0, len(uids), 500):
            part = uids[i:i + 500]
            got = con.execute(f"SELECT uid FROM documents WHERE embed_done=0 AND uid IN "
                              f"({','.join('?' * len(part))})", part).fetchall()
            pending.extend(r["uid"] for r in got)
        uids = pending
    if not uids:
        return 0
    return mark_embedded(con, collection, uids, synced=False)


def mark_present(con, collection, codex, records: Sequence[dict], present: Iterable[str]) -> int:
    """After a BUNDLE LANDS (app/share/accept.py `take`): the documents of
    the part whose every chunk now has a vector here — `present` the chunk
    ids the landing wrote or compared as duplicates, no table read — marked
    as the embed stage marks its own, `embed_done = 0` rows only. The
    Library's "embedded" figure read 57 % on a peer holding 93 % of the
    vectors (Perugia, 2026-10-20): fetched bundles landed vectors and marked
    nothing, so the count was the one-off `dev mark-embedded`'s last run.
    One short manifest transaction, AFTER the durable vector write
    (invariant 2): marking first is the one ordering that loses data."""
    have = set(present)
    own = not con.in_transaction
    if own:
        con.execute("BEGIN IMMEDIATE")
    try:
        n = mark_complete(con, collection, codex, records,
                          lambda ids: {i: True for i in ids if i in have}, only_unmarked=True)
        if own:
            con.execute("COMMIT")
        return n
    except BaseException:
        if own:
            con.execute("ROLLBACK")
        raise


# ---- NOT HELD: a document whose vectors are gone (2026-11-07) ----------------
#
# `embed_version` with `embed_done = 0` is what app/status.py's SUPERSEDED reads
# as "an earlier version's vectors are still held and searched" — the grey on
# the Library's dense bar. A writer that DELETES every vector of a document and
# leaves it not embedded therefore writes its version back to 0, as a document
# never embedded has: wikidata's re-creation (2026-11-03) wiped its 1.44 M bge
# vectors and cleared the marks, kept version 2, and the card painted 458,636
# documents of plain waiting work grey (München, 2026-11-07).
#
# ALWAYS AFTER THE DELETE IS DURABLE, never before: 0 is what `may_reuse` reads
# as "never completed — keep its slices", so a version zeroed while an older
# EMBED_VERSION's rows still exist would let the next embedding keep them. A
# stop between the delete and the reset leaves the version: the grey then
# overstates by those documents until they are embedded, which is harmless, and
# `louise dev superseded-repair` finds them.

#: uids per UPDATE of `mark_unheld`; rowids per window of `unheld_manifest`
UNHELD_UIDS = 500
UNHELD_WINDOW = 50_000

_NOT_HELD = "embed_done=0 AND COALESCE(embed_version,0)<>0"


def mark_unheld(con, uids: Iterable[str], *, window: int = UNHELD_UIDS) -> int:
    """`embed_version = 0` for each of `uids` that is not embedded
    (`embed_done = 0`: a declined -1 and a document marked meanwhile are
    left alone) — documents the CALLER has proven hold no vector here, after
    the delete committed (see above). One BEGIN IMMEDIATE per `window` of
    uids, by the primary key; `con` in autocommit (db.connect's). Returns
    how many rows changed; a second call changes none."""
    ids = sorted({str(u) for u in uids})
    n = 0
    for i in range(0, len(ids), max(1, window)):
        part = ids[i:i + max(1, window)]
        con.execute("BEGIN IMMEDIATE")
        try:
            n += con.execute(f"UPDATE documents SET embed_version=0 WHERE uid IN "
                             f"({','.join('?' * len(part))}) AND {_NOT_HELD}", part).rowcount
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return n


#: the window statement of `unheld_manifest`: the rowid range of the primary
#: key, nothing else (tests/test_superseded_repair.py checks its plan)
UNHELD_WINDOW_SQL = (f"UPDATE documents SET embed_version=0 WHERE rowid>? AND rowid<=? "
                     f"AND {_NOT_HELD}")


def unheld_manifest(con, *, window: int = UNHELD_WINDOW,
                    log: Callable[[str], None] | None = None) -> int:
    """Every document of this manifest that is not embedded, version 0 — for
    a manifest whose collection is PROVEN to hold no vector (`dev carry
    --unembedded`, A1h: `unembedded_why_not` found no row and no mark). In
    rowid windows, one BEGIN IMMEDIATE each, so a 76 M-row manifest
    (wikidata) is ~1,500 short writes and the worker's own interleave, as
    `embeddings.mark_for_reembed`. Idempotent and resumable without a
    cursor: what an interrupted run left is found by the next, and a second
    run finds nothing. Returns how many rows changed."""
    top = con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0
    step = max(1, window)
    n = 0
    for start in range(0, top, step):
        con.execute("BEGIN IMMEDIATE")
        try:
            n += con.execute(UNHELD_WINDOW_SQL, (start, start + step)).rowcount
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        if log and (start // step) % 200 == 199:
            log(f"{start + step:,} of {top:,} rowids, {n:,} version(s) cleared")
    return n


# ---- NOT DONE: a document whose vectors a delete took (2026-11-07) -----------
#
# `embed_done = 1` says every chunk of the document has a vector here: the
# embed stage never selects it again (only the version walk does, for an older
# EMBED_VERSION), and the Library counts it done. A delete that takes its rows
# and leaves the mark therefore leaves a document missing from dense search
# that nothing will ever embed again, without a word — share's withdraw of a
# faulty bundle ("return to without vectors", 03 §3.6), `hold collection forget
# --kinds vectors`, `hold vectors check --drop` and the worker's not-indexable
# withdraw all did. Each now clears the mark AFTER its delete is durable:
#
#   - a document left with NO row: embed_done 0, embed_version 0 — not
#     embedded, nothing earlier held (the NOT HELD rule above);
#   - one that keeps SOME rows: embed_done 0, its version kept — the rows left
#     are still held and searched (status.SUPERSEDED, the card's grey), and an
#     older version's must not be kept by `may_reuse` (0 would let it).
#
# Whether a row is left is the TABLE's answer, asked after the delete
# (`books_with_vectors`, strict); an answer that cannot be read clears the
# mark and keeps every version — "not done" is always true after a delete,
# "no row" only when proven. What the worker does next is the collection's
# embed setting: `all` (and `p2p`, for the parts rendezvous gives it) embeds
# them again, the rows left reused by (id, text); `on-demand` embeds what a
# search needs; `off` leaves them waiting — counted as waiting, never as done —
# and every node still takes peers' bundles in, which may bring them back.
#
# AFTER, not before: a mark cleared first is what the embed stage selects, and
# beside a running worker it could embed the document again before the delete
# took the rows it had just written — `embed_done = 1` with nothing behind it,
# the defect itself. A stop between the delete and the mark leaves the mark:
# each caller's re-run finds the same work (its docstring says how), and
# `louise dev superseded-repair --done-without-vectors` finds what is left.

_DROPPED_GONE = ("UPDATE documents SET embed_done=0, embed_version=0 WHERE uid IN ({marks}) "
                 "AND embed_done IN (0, 1) AND (embed_done=1 OR COALESCE(embed_version,0)<>0)")
_DROPPED_PART = "UPDATE documents SET embed_done=0 WHERE uid IN ({marks}) AND embed_done=1"


def _books_of(uids: Iterable[str]) -> dict[int, list[str]]:
    by_book: dict[int, list[str]] = {}
    for u in uids:
        try:
            by_book.setdefault(int(str(u).rsplit(":", 1)[1]), []).append(str(u))
        except (IndexError, ValueError):
            continue                       # no numeric book id: never a row of the table
    return by_book


def mark_dropped(con, collection, uids: Iterable[str], *, window: int = UNHELD_UIDS,
                 log: Callable[[str], None] | None = None) -> dict[str, int]:
    """After a delete took rows of each of `uids` — some or all — and is
    durable: each is not done (see above). The table is asked once which of
    them still hold a row; those holding none are not embedded, version 0;
    the others keep their version. A declined document (-1) is left as it
    is. One BEGIN IMMEDIATE per `window` of uids, by the primary key; `con`
    in autocommit. Returns `{"reset": marks cleared or versions zeroed,
    "unheld": of which no row left}`; a second call changes nothing."""
    from app import embeddings
    by_book = _books_of(sorted({str(u) for u in uids}))
    if not by_book:
        return {"reset": 0, "unheld": 0}
    held: set[int] | None
    try:
        held = embeddings.books_with_vectors(list(by_book), collection, strict=True,
                                             batch=max(1, window))
    except Exception as e:                 # noqa: BLE001 — not proven gone: not done, versions kept
        held = None
        if log:
            log(f"{len(by_book)} document(s)' rows could not be read after the delete "
                f"({type(e).__name__}: {e}): marked not embedded, their versions kept")
    gone = [u for b, us in by_book.items() if held is not None and b not in held for u in us]
    part = [u for b, us in by_book.items() if held is None or b in held for u in us]
    out = {"reset": 0, "unheld": 0}
    step = max(1, window)
    for sql, ids, key in ((_DROPPED_GONE, gone, "unheld"), (_DROPPED_PART, part, "reset")):
        for i in range(0, len(ids), step):
            chunk = ids[i:i + step]
            con.execute("BEGIN IMMEDIATE")
            try:
                n = con.execute(sql.format(marks=",".join("?" * len(chunk))), chunk).rowcount
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            out[key] += n
    out["reset"] += out["unheld"]
    return out


#: the window statement of `unmark_manifest`: the rowid range of the primary
#: key, nothing else (tests/hold/test_superseded_repair.py checks its plan)
UNMARK_WINDOW_SQL = ("UPDATE documents SET embed_done=0, embed_version=0 WHERE rowid>? AND rowid<=? "
                     "AND embed_done IN (0, 1) AND (embed_done=1 OR COALESCE(embed_version,0)<>0)")


def unmark_manifest(con, *, window: int = UNHELD_WINDOW,
                    log: Callable[[str], None] | None = None) -> int:
    """Every document of this manifest not embedded, version 0 — for a
    collection whose vector store is GONE WHOLE (`hold collection forget
    --kinds vectors`): no document holds a row, so none is done and none
    holds an earlier version. The declined (-1) keep theirs. Rowid windows,
    one BEGIN IMMEDIATE each, as `unheld_manifest`; idempotent and resumable
    without a cursor. Returns how many rows changed."""
    top = con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0
    step = max(1, window)
    n = 0
    for start in range(0, top, step):
        con.execute("BEGIN IMMEDIATE")
        try:
            n += con.execute(UNMARK_WINDOW_SQL, (start, start + step)).rowcount
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        if log and (start // step) % 200 == 199:
            log(f"{start + step:,} of {top:,} rowids, {n:,} document(s) marked not embedded")
    return n
