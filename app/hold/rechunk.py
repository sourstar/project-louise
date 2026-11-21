"""hold.rechunk — amendment A1k (PROPOSED 2026-11-21, for the architecture
session; docs/notes/legal-chunker-v4.md §4.3): the vectors of a collection
carried into its successor whose space differs ONLY by its layers' chunker
VERSION, every vector whose chunk text the new version cuts again kept
under its new chunk id.

    louise dev carry <from> <to> --rechunked [--dry-run | --yes] [--window N] [--processes N]

RULE 1. The chunk id carries the chunker version (`<uid>:<seq>:<version>`, 06
§6.3) and `space_id` the layers (09 §9.3), so moving `legal_article` from 3 to
4 moves every id and the space. The function from cleaned text to vectors does
not move: the weights, dim, dtype, ctx, cut, pooling, normalisation, both
instructions and the epoch are equal (`carry.space_differences` empty), so the
same text has the same vector (02 §2.4) — the rule 06 §6.3 already applies
within a space, by chunk hash, when a record's chunks are cut again. By the
book the target would embed every chunk again; here only the chunks whose
text the source holds no vector for reach the encoder, through the target's
own embed stage.

THE PASS, after `carry --rechunked` copied the manifest (every embed and index
mark emptied, the space re-pinned; hold/carry.py):
  1. the source's documents are walked by rowid in windows from a cursor; each
     window is a task of the parse pool (`parallel.each_part`, at most the
     cores the node profile allows): every in-scope document without an error
     cut at the TARGET's layers exactly as the stages cut it
     (`record_source.title_sections_and_source`, `layers.chunks_for_document`,
     `layers.with_source` — parallel.py's "chunks" kind). A child returns
     each chunk's id, seq, chapter and the BLAKE2b-128 of its text, never the
     text;
  2. in this process, a bounded group of documents at a time (`batch_rows`):
     the source's rows of the group, read by `book_id` (`embeddings.
     held_chunks`); a new chunk whose text hash is the hash of a row the same
     document holds (never by position: `seq` moves when a cut before it
     does) takes that row's vector BYTES, the row's text and the record's
     `source_hash`, under its new id, seq and chapter, the target's validity
     columns beside; a pair chunk also needs the row made for this very
     record (`embeddings._kept`). Only rows of a document whose vectors are
     current are read (`embeddings.may_reuse`, as the embed stage keeps them);
  3. the rows are written by the embed stage's batch writer
     (`embeddings.write_pending`, Lance's append under the vector write lock),
     then, in one BEGIN IMMEDIATE: each document whose every chunk carried
     and that was embedded in the source marked embedded
     (`embed_marks.mark_embedded`, its validity columns synced; a versioned
     collection's chunk hashes recorded as the embed stage records them), the
     counts and the cursor. Every other document stays as the copy left it,
     not embedded and version 0, for the embed stage, which keeps the rows
     carried here by (id, text) and embeds the rest — and, in a versioned
     collection, carries across versions of a Work as always (06 §6.3).

INVARIANT 2. Before a window's first write its range is committed as
`pending`; the window's marks and cursor are committed after its last write.
A stop or a kill in between leaves `pending`: the next run first deletes what
the window's documents may hold in the target's table (delete-then-insert
keyed on the document, `embeddings.delete_books`), then cuts the window again.
SIGTERM is honoured at a window boundary (`stop`). The end requires the
target's table to hold exactly the rows carried (`CarryMismatch` otherwise).
Nothing here VACUUMs, nothing moves a byte over the network; a pass reads the
source and writes the target, both local.

`--dry-run` (apply=False) runs the same refusals and the same pass and writes
nothing: its counts are the real run's (tests/hold/test_rechunk.py), and on a
fully embedded source they are tools/measure/legal_chunker_drift.py's.

THE STANDARDS. 06 §6.3's carry by chunk hash, with the hash `chunks_root`
folds (09 §9.4); Lance's own append through the embed stage's writer
(`write_pending`) and delete-then-insert by `book_id` on a resume. Written
down, not taken: Lance's `merge_insert("id")` (an upsert keyed on the chunk
id) would make a resume idempotent without the pending range, but it joins on
`id`, which carries no index, so every call reads the whole target table — a
per-window cost that grows with the table (CLAUDE.md); it pays if Lance
indexes `id`, or as one call over the whole table. Lance's `update` of `id`
on a copy of the table (legal-chunker-v4.md M2-B) cannot set per-row values
(`seq`, `chapter`) and keeps the rows v4 no longer cuts. A sort-merge of the
source's table against the cut, as `carry.keep_producer_scan` does, reads
every row once and sequentially: it pays if the pilot shows the per-window
read by `book_id` dominating (the report times the three phases).
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .codex import CodexError

#: the re-key's cursor in the target's `control` table
REKEY_KEY = "carry_rechunked"
#: documents a task of the parse pool
WINDOW = 500
#: rows read from the source and written to the target at a time, at most
#: (a document longer than this is a group of its own): a few tens of MB of
#: vectors and texts at the shared legal space's 2,048 dimensions
BATCH_ROWS = 8192
#: the counters, per language
FIELDS = ("documents", "chunks", "carried", "to_embed", "dropped", "documents_embedded",
          "documents_to_embed", "documents_not_reusable", "errors")


def _quiet(_m: str) -> None:
    return None


# ---- the cut, in a child of the parse pool ---------------------------------

def cut_window(source, target, lo: int, hi: int) -> dict:
    """One task of the pass: the source's documents of rowids (lo, hi], each
    in scope and without an error cut at the TARGET's layers as the stages cut
    it (parallel.py's "chunks" kind). Per document its manifest row's fields
    the pass needs and, per chunk, `(id, seq, chapter, text hash, pair)`.
    Module-level, so it pickles; reads the source's manifest read-only."""
    from app import db, embeddings, layers, record_source
    con = db.read_only_connect(source)
    if con is None:
        return {"docs": [], "skipped": {"no manifest": 1}, "errors": {}}
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT rowid, * FROM documents WHERE rowid > ? AND rowid <= ? ORDER BY rowid", (lo, hi))]
    finally:
        con.close()
    plugin = target.plugin_instance()
    pair = layers.has_pair_layer(target)
    docs: list[dict] = []
    skipped: Counter = Counter()
    errors: dict[str, list] = {}
    for row in rows:
        if row.get("error") is not None or not row.get("in_scope"):
            skipped["out of scope or errored"] += 1
            continue
        lang = row.get("lang") or "?"
        try:
            title, sections, source_hash = record_source.title_sections_and_source(
                target, row, plugin, pair=pair)
            chunks = layers.with_source(layers.chunks_for_document(target, row, sections, title=title),
                                        source_hash)
        except Exception as e:   # noqa: BLE001 — counted per language; the stage meets it as by the book
            got = errors.setdefault(lang, [0, None])
            got[0] += 1
            got[1] = got[1] or f"{row['uid']}: {type(e).__name__}: {e}"[:300]
            continue
        docs.append({"rowid": int(row["rowid"]), "uid": row["uid"], "native_id": int(row["native_id"]),
                     "lang": row.get("lang"), "embed_done": int(row.get("embed_done") or 0),
                     "embed_version": row.get("embed_version"),
                     "source": embeddings.chunk_source(chunks) if chunks else None,
                     "chunks": [(c["id"], int(c["seq"]), c.get("chapter") or "",
                                 embeddings.chunk_text_hash(c["text"]), c.get("fields") is not None)
                                for c in chunks]})
    return {"docs": docs, "skipped": dict(skipped), "errors": errors}


# ---- the source's rows ----------------------------------------------------------

def _held(coll, books: list[int]):
    """`embeddings.held_chunks` for `books`, halved where one read cannot
    return a group's rows (it refuses rather than answer short)."""
    from app import embeddings
    try:
        return embeddings.held_chunks(coll, books).by_book
    except RuntimeError as e:
        if "more than one read returns" not in str(e) or len(books) <= 1:
            raise
        half = len(books) // 2
        return {**_held(coll, books[:half]), **_held(coll, books[half:])}


def _groups(docs: list[dict], batch_rows: int):
    """The documents in groups of at most `batch_rows` new chunks (a longer
    document alone), in order."""
    group: list[dict] = []
    n = 0
    for d in docs:
        k = len(d["chunks"])
        if group and n + k > batch_rows:
            yield group
            group, n = [], 0
        group.append(d)
        n += k
    if group:
        yield group


def _reusable(d: dict) -> bool:
    """May the source document's rows stand for its chunks? Embedded or
    begun (not declined), at the current EMBED_VERSION or never completed —
    the embed stage's own rule (`embeddings.may_reuse`)."""
    from app import embeddings
    return d["embed_done"] in (0, 1) and embeddings.may_reuse({"embed_version": d["embed_version"]})


def _carry_group(group: list[dict], held: dict, dtype, columns: dict, per: dict) -> tuple[list, list]:
    """The group's rows for the target and its complete documents, the
    counters updated. A row is the embed stage's (`embeddings._rows_for`)."""
    import numpy as np

    from app import embeddings
    pending, complete = [], []
    for d in group:
        c = per.setdefault(d["lang"] or "?", Counter())
        c["documents"] += 1
        c["chunks"] += len(d["chunks"])
        ok = _reusable(d)
        if not ok:
            c["documents_not_reusable"] += 1
        stored = [(embeddings.chunk_text_hash(r["text"]), r)
                  for r in (held.get(d["native_id"], {}) if ok else {}).values()]
        by_hash: dict[bytes, dict] = {}
        for h, r in stored:
            by_hash.setdefault(h, r)
        source = d["source"]
        cols = columns.get(d["native_id"]) or {}
        rows = []
        for cid, seq, chapter, h, pair in d["chunks"]:
            r = by_hash.get(h)
            # a text chunk by its text; a pair chunk only by the row made for
            # this very record (its source_hash the record's, `embeddings._kept`)
            if r is None or (pair and (source is None or r.get(embeddings.SOURCE_COLUMN) != source)):
                continue
            rows.append({"id": cid, "book_id": d["native_id"], "lang": d["lang"] or "und", "seq": seq,
                         "chapter": chapter, "text": r["text"], "vector": np.asarray(r["vector"], dtype=dtype),
                         embeddings.SOURCE_COLUMN: source, **{k: int(v) for k, v in cols.items()}})
        new = {h for _cid, _s, _ch, h, _p in d["chunks"]}
        c["carried"] += len(rows)
        c["to_embed"] += len(d["chunks"]) - len(rows)
        c["dropped"] += sum(1 for h, _r in stored if h not in new)
        if len(rows) == len(d["chunks"]) and d["embed_done"] == 1:
            c["documents_embedded"] += 1
            complete.append((d, rows, cols))
        else:
            c["documents_to_embed"] += 1
        if rows:
            pending.append((d, rows))
    return pending, complete


def _seen_of(coll, books) -> dict[int, int]:
    """The target table's version per document, read just before a write:
    nothing else writes the target meanwhile (both write locks), so the embed
    writer's check of a foreign write (`_unexplained_since`) passes without a
    read of the table — which, on a column no index covers yet, would cost
    the whole table each window."""
    from app import embeddings, vector_parts
    if vector_parts.ensure_split(coll):
        out: dict[int, int] = {}
        for value, part_books in vector_parts.route(coll, books).items():
            t = embeddings._committed_table(embeddings._conn(vector_parts.view(coll, value)))
            out.update(dict.fromkeys((int(b) for b in part_books), t.version if t is not None else 0))
        return out
    t = embeddings._committed_table(embeddings._conn(coll))
    return dict.fromkeys((int(b) for b in books), t.version if t is not None else 0)


# ---- the pass ---------------------------------------------------------------

def _processes(asked: int | None) -> tuple[int | None, str]:
    """The pool's bound: the node profile's cores (app/cpu_limit.py), `asked`
    when fewer; None is the pool's own size."""
    from app import cpu_limit
    lim = cpu_limit.current()
    cores = lim.cores
    if asked is not None:
        cores = asked if cores is None else min(int(asked), cores)
    return cores, lim.says()


def _report(state: dict) -> dict:
    langs = {lang: {f: int(v.get(f, 0)) for f in FIELDS} for lang, v in sorted(state["langs"].items())}
    return {"languages": langs, "totals": {f: sum(v[f] for v in langs.values()) for f in FIELDS}}


def _commit(con, fn) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        fn()
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise


def rekey(root, from_ref: str, to_ref: str, *, apply: bool = False, window: int = WINDOW,
          batch_rows: int = BATCH_ROWS, processes: int | None = None, stop=None,
          log: Callable[[str], None] = print) -> dict[str, Any]:
    """The A1k pass (module docstring). `apply=False`: the refusals and the
    whole pass, nothing written. `apply=True`: after `carry --rechunked`,
    with both write locks held by this process; resumable from its cursor."""
    from app import db, embed_marks, embeddings, parallel, validity

    from . import carry as carry_mod, collection as col, records as rec
    root = Path(root)
    from_name, src = col.resolve(root, from_ref)
    to_name, to = col.resolve(root, to_ref)
    sp, tp = carry_mod._paths(root, from_name), carry_mod._paths(root, to_name)
    pair = [src.collection_id, to.collection_id]
    if apply:
        carry_mod.refuse_without_locks(root, (from_name, to_name), "carry --rechunked")
        marker = tp["state"] / carry_mod.MARKER
        held = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
        if not (held.get("rechunked") and held.get("done") and held.get("from") == src.collection_id
                and held.get("to") == to.collection_id):
            raise CodexError(f"carry --rechunked: {to_name} records no finished `carry --rechunked` from "
                             f"{from_name} ({marker}); nothing was written")
    else:
        # every refusal of the carry, nothing written (its dry run)
        carry_mod.carry(root, from_name, to_name, copy=True, rechunked=True, apply=False, log=_quiet)
    carry_mod.refuse_unless_pinned(sp["lance"], sp["manifest"], src, from_name)
    cores, says = _processes(processes)
    if cores == 0:
        raise CodexError(f"carry --rechunked: the node allows no core now ({says}); --processes N runs it "
                         f"anyway. Nothing was written")
    s_coll = rec.local_collection(root, from_name, src)            # its data is used: gated (invariant 9)
    # the target's records are the source's byte for byte (the carry's
    # refusals); its licence acknowledgement is carried before a real run
    t_coll = (rec.local_collection if apply else rec._local_collection)(root, to_name, to)
    sc = sqlite3.connect(f"file:{sp['manifest'].as_posix()}?mode=ro", uri=True)
    try:
        top = int(sc.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0)
    finally:
        sc.close()
    fresh = {"rowid": 0, "langs": {}, "rows": 0, "pending": None, "done": False,
             "skipped": {}, "first_error": {}}
    con = db.connect(t_coll) if apply else None
    try:
        state = carry_mod._cursor(con, REKEY_KEY, pair, fresh) if apply else {**fresh, "pair": pair}
        if apply and state.get("pending"):
            _purge(con, t_coll, state, log=log, name=to_name)
        dtype = _dtype(to)
        seconds = Counter()
        tasks = [(s_coll, t_coll, lo, min(lo + int(window), top))
                 for lo in range(int(state["rowid"]), top, max(1, int(window)))]
        if not state.get("done"):
            log(f"[rechunk] {from_name} → {to_name}: {len(tasks)} window(s) of {window} rowid(s) from "
                f"{state['rowid']} of {top}, at most {cores or parallel.processes()} process(es) ({says})"
                + ("" if apply else "; a dry run: nothing written"))
        from app.hold import rechunk as me                     # by name: a spawned child imports it
        stopped = False
        t_wait = time.monotonic()
        results = parallel.each_part(me.cut_window, tasks, limit=cores) if not state.get("done") else iter(())
        try:
            for k, ((_s, _t, lo, hi), got) in enumerate(zip(tasks, results), 1):
                seconds["cut"] += time.monotonic() - t_wait
                per = {lang: Counter(v) for lang, v in state["langs"].items()}
                skipped = Counter(state.get("skipped") or {})
                skipped.update(got["skipped"])
                first_error = dict(state.get("first_error") or {})
                for lang, (n, msg) in got["errors"].items():
                    per.setdefault(lang, Counter())["errors"] += n
                    first_error.setdefault(lang, msg)
                complete: list = []
                written = 0
                for group in _groups(got["docs"], int(batch_rows)):
                    t0 = time.monotonic()
                    books = sorted({d["native_id"] for d in group if _reusable(d)})
                    held = _held(s_coll, books) if books else {}
                    columns = validity.columns_for(con, t_coll, [d["native_id"] for d in group]) if apply else {}
                    seconds["read"] += time.monotonic() - t0
                    pending, done_docs = _carry_group(group, held, dtype, columns, per)
                    complete += done_docs
                    if not apply or not pending:
                        continue
                    t0 = time.monotonic()
                    if written == 0 and not state.get("pending"):
                        # the window's range FIRST: a stop after a write and
                        # before its cursor is undone by the next run (`_purge`)
                        state = {**state, "pending": [lo, hi]}
                        _commit(con, lambda: db.set_control(con, REKEY_KEY, json.dumps(state, sort_keys=True)))
                    seen = _seen_of(t_coll, [d["native_id"] for d, _r in pending])
                    embeddings.write_pending([embeddings.PendingDoc(d["native_id"], rows, purge=False,
                                                                    seen=seen[d["native_id"]])
                                              for d, rows in pending], t_coll, lock_con=con)
                    written += sum(len(rows) for _d, rows in pending)
                    seconds["write"] += time.monotonic() - t0
                state = {**state, "rowid": hi, "pending": None, "rows": int(state["rows"]) + written,
                         "langs": {lang: dict(v) for lang, v in per.items()}, "skipped": dict(skipped),
                         "first_error": first_error}
                if apply:
                    _commit(con, lambda: _mark(con, t_coll, complete, state, embed_marks, embeddings,
                                               validity, db))
                if k % 20 == 0 or k == len(tasks):
                    t = _report(state)["totals"]
                    log(f"[rechunk] {to_name}: {k}/{len(tasks)} window(s), {t['documents']} document(s), "
                        f"{t['carried']} chunk(s) carried, {t['to_embed']} to embed")
                if stop is not None and stop.is_set() and k < len(tasks):
                    stopped = True
                    log(f"[rechunk] {to_name}: stopped at rowid {hi} (a window boundary); run it again "
                        f"to resume")
                    break
                t_wait = time.monotonic()
        finally:
            close = getattr(results, "close", None)
            if close is not None:
                close()                                        # no part left queued
        if not stopped and not state.get("done"):
            state = {**state, "done": True}
            if apply:
                _commit(con, lambda: db.set_control(con, REKEY_KEY, json.dumps(state, sort_keys=True)))
    finally:
        if con is not None:
            con.close()
    out = {"from": from_name, "to": to_name, "apply": apply, "done": bool(state.get("done")),
           "stopped": stopped, **_report(state), "skipped": state.get("skipped") or {},
           "first_error": state.get("first_error") or {}, "rows_written": int(state["rows"]),
           "seconds": {k: round(v, 1) for k, v in seconds.items()}}
    t = out["totals"]
    if apply and out["done"]:
        rows_now = embeddings.stats(t_coll)["vectors"]
        out["vectors"] = rows_now
        if rows_now != int(state["rows"]):
            raise carry_mod.CarryMismatch(
                f"carry --rechunked: {to_name}'s table holds {rows_now} rows and {state['rows']} were "
                f"carried — rows another writer put there, or a resume that did not undo its window")
    log(f"[rechunk] {from_name} → {to_name}: {t['documents']} document(s), {t['chunks']} chunk(s) at "
        f"{to_name}'s layers: {t['carried']} carried, {t['to_embed']} to embed, {t['dropped']} of "
        f"{from_name}'s not cut again; {t['documents_embedded']} document(s) "
        + ("marked embedded" if apply else "would be marked embedded")
        + (f", {t['errors']} could not be cut" if t["errors"] else "")
        + ("" if apply else "; nothing written (a dry run)")
        + (". Before use: the gate for its space, then `hold check "
           f"{to_name} --checks vectors-sample`; the worker indexes every document and embeds the "
           f"{t['to_embed']} chunk(s)" if apply and out["done"] else ""))
    return out


def _dtype(codex):
    import numpy as np
    return np.float16 if codex.space.dtype == "f16" else np.float32


def _mark(con, t_coll, complete: list, state: dict, embed_marks, embeddings, validity, db) -> None:
    """Inside one BEGIN IMMEDIATE, after the window's rows are durable: its
    complete documents marked embedded as the embed stage marks them — each
    only while its validity columns are the ones its rows were written with
    (V6), then synced; a versioned collection's chunk hashes recorded — then
    the cursor and the counts."""
    if complete:
        embed_marks.mark_embedded(con, t_coll, [d["uid"] for d, _rows, _cols in complete],
                                  columns={d["uid"]: cols for d, _rows, cols in complete if cols})
        if validity.declared(t_coll) is not None:
            for d, rows, _cols in complete:
                embeddings.record_chunk_hashes(con, d["native_id"], rows)
    db.set_control(con, REKEY_KEY, json.dumps(state, sort_keys=True))


def _purge(con, t_coll, state: dict, *, log, name: str) -> None:
    """A window stopped between its first write and its cursor: what its
    documents may hold in the target's table deleted (invariant 2's
    delete-then-insert, keyed on the document), then `pending` cleared, so the
    window is cut and written again from nothing."""
    from app import db, embeddings
    lo, hi = state["pending"]
    books = [int(r[0]) for r in con.execute(
        "SELECT native_id FROM documents WHERE rowid > ? AND rowid <= ?", (int(lo), int(hi)))]
    embeddings.delete_books(books, t_coll, lock_con=con)
    state["pending"] = None
    _commit(con, lambda: db.set_control(con, REKEY_KEY, json.dumps(state, sort_keys=True)))
    log(f"[rechunk] {name}: the window of rowids ({lo}, {hi}] stopped before its cursor: its "
        f"{len(books)} document(s)' rows deleted, the window goes again")
