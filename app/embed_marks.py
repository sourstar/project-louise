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
        per_uid[uid] = [c["id"] for c in layers_mod.chunks_for_document(codex, row, sections)]
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
