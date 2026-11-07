"""`louise dev superseded-repair <collection> [--apply]` — the documents a
manifest counts as SUPERSEDED (app/status.py: "an earlier version, searched
until re-embedded", grey on the Library's dense bar) that hold no vector,
given back the version a document never embedded has (07 §7.3; 2026-11-07).

WHY. SUPERSEDED reads `embed_done = 0 AND embed_version <> 0` as "an earlier
version's vectors are still held". A wipe that deleted the vectors and cleared
the marks but kept the version broke that: wikidata's re-creation (operator,
2026-11-03: "Wipe wikidata vectors, use Nemotron instead, rule 1") deleted its
1.44 M bge vectors, cleared the marks, kept embed_version 2, and `carry
--unembedded` carried the manifest; the card then painted 458,636 documents of
plain waiting work grey — 0 of 50 sampled held a row, against gutenberg's
11,109 real ones, 50 of 50 holding (München, 2026-11-07). The writers in the
app now reset the version once a document's last row is gone
(app/embed_marks.py, "NOT HELD"); this repairs a manifest written before.

HOW. The candidates (`status.SUPERSEDED['embed']`) in ROWID WINDOWS: at most
`span` rowids read by the primary key and at most `per_query` candidates a
window, then ONE vector-table question per window (`books_with_vectors`,
strict: `book_id IN (…)` projected to `book_id`, distinct; a split store asks
each part its documents route to). Read-only unless `apply`; then, per window,
one short BEGIN IMMEDIATE sets `embed_version = 0` on the candidates that hold
no row, guarded `embed_done = 0 AND embed_version = <the value read>` with the
whole predicate re-checked inside the UPDATE, and writes the cursor. No read is
made inside the write, and none is long: the window's SELECT is bounded by
`span`.

NO STOP NEEDED. Beside a running worker every outcome is right: a document the
worker embeds meanwhile is marked `embed_done = 1` and the guard leaves it; one
whose first slices it writes after the question was asked holds rows of the
CURRENT version, which version 0 (`may_reuse`: "never completed — keep its
slices") keeps — exactly what the reset means. Rows of an older version never
appear after the question. A store that cannot answer raises (strict): never
read as "holds nothing".

RESUMABLE. The cursor is the manifest's `control` row CURSOR_KEY, valid only
for the manifest at the path it names (a copied manifest starts over); a
finished cursor starts the next run over, which then resets nothing — the
repair is idempotent. SIGINT/SIGTERM end it after the window in flight.

MEASURED (2026-11-07, a development container: 4 cores, SQLite 3.45.1,
pylance 9.0.1). The fixture: a manifest of 1,200,000 documents (186 MB), one
in 20 a candidate (60,000, in every window), the first 10,000 of them holding
5 rows each and the other 50,000 none; a vector table of 1,000,000 rows
(200,000 books x 5, dim 16, f16), 50 fragments, BTREE on `book_id`. Medians
of 15:
  - the window's SELECT, capped at 1,000 candidates (20,000 rowids there):
    2.7-3.7 ms; a whole 50,000-rowid window read: 11.7 ms;
  - the question for 1,000 books: 8.5 ms when none holds a row (wikidata's
    case), 12.5 ms when all do, 5 rows each (gutenberg's);
  - the write, 1,000 guarded UPDATEs and the cursor in one BEGIN IMMEDIATE:
    2.6 ms — the longest the worker can wait on this pass;
  - the whole manifest: 60 windows, 0.77 s dry, 1.25 s applied.
For wikidata (~76 M rows, ~420 k candidates): at least 1,520 windows of
50,000 rowids (~18 s of reads at these rows' size) and at least 420 capped
windows' questions and writes (~5 s) — ~25 s here; the node's rows are wider
and its pages colder, so the read costs about what the card's own full scan
of the manifest costs, minutes, never a lock held longer than one window's
write. The run prints what it took (`seconds`).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

#: rowids one window reads, by the primary key
SPAN = 50_000
#: candidates kept per window at most, and so book ids per question; fewer on a
#: table of many fragments — its filter costs memory per value per fragment
#: (carry._delete_batch: ~1 GB a question at most) — so a dense stretch is cut
#: into windows of this many
PER_QUERY = 1_000
#: the manifest's control row holding the cursor
CURSOR_KEY = "superseded_repair"
#: uids shown of each kind
SAMPLE = 10


def window_sql() -> str:
    """The window's read: the candidates among `span` rowids, by the INTEGER
    PRIMARY KEY (`NOT INDEXED`: the scope index would answer `in_scope=1` and
    sort the rest in a TEMP B-TREE), in rowid order, at most :n of them."""
    from app import status
    return ("SELECT rowid, uid, native_id, embed_version FROM documents NOT INDEXED "
            f"WHERE rowid>:lo AND rowid<=:hi AND {status.SUPERSEDED['embed']} "
            "ORDER BY rowid LIMIT :n")


def reset_sql() -> str:
    """One candidate's reset: the row by its rowid, the version as it was read,
    and the whole predicate again — `embed_done = 0` among it — so a document
    marked or changed since the read is left as it is."""
    from app import status
    return ("UPDATE documents SET embed_version=0 WHERE rowid=:rowid AND embed_version=:version "
            f"AND {status.SUPERSEDED['embed']}")


def _fresh(path: str) -> dict[str, Any]:
    return {"manifest": path, "rowid": 0, "windows": 0, "candidates": 0, "holding": 0,
            "without": 0, "reset": 0, "skipped": 0, "done": False}


def _cursor(con, path: str) -> dict[str, Any]:
    """The run to resume: this manifest's unfinished cursor, else a fresh one —
    a cursor naming another manifest (the file was copied) or a finished pass
    starts over."""
    from app import db
    try:
        got = json.loads(db.get_control(con, CURSOR_KEY) or "{}")
    except (TypeError, ValueError):
        got = {}
    if not isinstance(got, dict) or got.get("manifest") != path or got.get("done"):
        return _fresh(path)
    return {**_fresh(path), **got}


def repair(collection, *, apply: bool = False, span: int = SPAN, per_query: int | None = None,
           sample: int = SAMPLE, stop: Callable[[], bool] | None = None,
           log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Walk `collection`'s superseded candidates and say which hold vectors;
    with `apply`, reset the version of those that hold none (see the module).
    `per_query` None: PER_QUERY, fewer on a table of many fragments. Returns
    the pass's counts, a sample of each kind, and how long it took."""
    from app import config, db, embeddings, spaces
    from .carry import _delete_batch
    if per_query is None:
        per_query = min(PER_QUERY, _delete_batch(embeddings.fragment_count(collection)))
    say = log or (lambda _m: None)
    path = str(Path(collection.manifest_path).resolve())
    lance_dir = Path(collection.lance_dir)
    params = {"max": config.MAX_RETRIES}
    select, update = window_sql(), reset_sql()
    t0 = time.monotonic()
    shown: dict[str, list[str]] = {"without_vectors": [], "holding_vectors": []}
    con = db.connect(collection)
    try:
        state = _cursor(con, path) if apply else _fresh(path)
        resumed = int(state["rowid"])
        if resumed:
            say(f"resuming at rowid {resumed:,} ({state['candidates']:,} candidate(s) walked before)")
        top = int(con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0)
        # a store PROVEN to hold no row at the start is asked nothing: a row
        # written after this read is the worker's, of the current version,
        # which a reset keeps (the module, "NO STOP NEEDED") — read once, not
        # a window (a split store pays ~2.5 ms a part, status.holds_no_vector)
        empty = spaces.known_lance_rows(lance_dir) == 0
        interrupted = False
        while state["rowid"] < top:
            if stop is not None and stop():
                interrupted = True
                break
            lo = int(state["rowid"])
            hi = lo + max(1, span)
            rows = con.execute(select, {**params, "lo": lo, "hi": hi,
                                        "n": max(1, per_query)}).fetchall()
            if len(rows) >= max(1, per_query):
                hi = int(rows[-1]["rowid"])            # a dense stretch: the next window goes on from here
            by_book: dict[int, list] = {}
            skipped = 0
            for r in rows:
                try:
                    by_book.setdefault(int(r["native_id"]), []).append(r)
                except (TypeError, ValueError):
                    skipped += 1                       # no numeric book id: not judged, not reset
            if not by_book or empty:
                held: set[int] = set()
            else:
                held = embeddings.books_with_vectors(list(by_book), collection, strict=True,
                                                     batch=len(by_book))
            holding = [r for b, rs in by_book.items() if b in held for r in rs]
            without = [r for b, rs in by_book.items() if b not in held for r in rs]
            for kind, got in (("without_vectors", without), ("holding_vectors", holding)):
                shown[kind].extend(r["uid"] for r in got[:max(0, sample - len(shown[kind]))])
            nxt = {**state, "rowid": hi, "windows": state["windows"] + 1,
                   "candidates": state["candidates"] + len(rows),
                   "holding": state["holding"] + len(holding),
                   "without": state["without"] + len(without),
                   "skipped": state["skipped"] + skipped}
            if apply:
                con.execute("BEGIN IMMEDIATE")
                try:
                    changed = con.executemany(update, [
                        {**params, "rowid": int(r["rowid"]), "version": r["embed_version"]}
                        for r in without]).rowcount if without else 0
                    nxt["reset"] = state["reset"] + max(0, changed)
                    db.set_control(con, CURSOR_KEY, json.dumps(nxt, sort_keys=True))
                    con.execute("COMMIT")
                except BaseException:
                    if con.in_transaction:
                        con.execute("ROLLBACK")
                    raise
            state = nxt
            if state["windows"] % 100 == 0:
                say(f"rowid {state['rowid']:,} of {top:,}: {state['candidates']:,} candidate(s), "
                    f"{state['holding']:,} holding vectors, {state['without']:,} without"
                    + (f", {state['reset']:,} reset" if apply else "")
                    + f" ({time.monotonic() - t0:.0f} s)")
        if not interrupted:
            state["done"] = True
            if apply:
                con.execute("BEGIN IMMEDIATE")
                try:
                    db.set_control(con, CURSOR_KEY, json.dumps(state, sort_keys=True))
                    con.execute("COMMIT")
                except BaseException:
                    if con.in_transaction:
                        con.execute("ROLLBACK")
                    raise
    finally:
        con.close()
    out: dict[str, Any] = {
        "collection": collection.name, "manifest": path, "apply": apply,
        "resumed_from_rowid": resumed, "rowid": int(state["rowid"]), "top_rowid": top,
        "windows": state["windows"], "candidates": state["candidates"],
        "holding_vectors": state["holding"], "without_vectors": state["without"],
        "skipped": state["skipped"], "store_empty": empty, "per_query": per_query,
        "done": bool(state["done"]), "interrupted": interrupted,
        "seconds": round(time.monotonic() - t0, 1), "sample": shown}
    if apply:
        out["reset"] = state["reset"]
        # marked by the worker, or changed otherwise, between the read and the write
        out["changed_meanwhile"] = state["without"] - state["reset"]
    return out


def summary(out: dict[str, Any]) -> str:
    """One line for stderr: what the pass found and did."""
    did = (f"{out['reset']:,} reset to embed_version 0"
           + (f" ({out['changed_meanwhile']:,} changed meanwhile, left)" if out.get("changed_meanwhile") else "")
           if out["apply"] else f"{out['without_vectors']:,} would be reset (--apply)")
    return (f"[superseded-repair] {out['collection']}: {out['candidates']:,} superseded candidate(s), "
            f"{out['holding_vectors']:,} holding vectors (kept), {out['without_vectors']:,} without; "
            f"{did}; {out['windows']:,} window(s), {out['seconds']} s"
            + ("; interrupted — run it again to resume" if out["interrupted"] else ""))
