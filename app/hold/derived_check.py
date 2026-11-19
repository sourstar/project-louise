"""VERIFY-AND-RESET: what the worker derived from its own parse, checked
against what it would derive now (2026-11-19; architecture's repair for the
copies reconciled before the A1c re-derive, relayed by München: pubmed's
41.4 M on parser 9 and wikidata's 75.9 M on parser 3, whose predecessors are
no longer held, so no snapshot comparison is possible).

For every document the stages marked done, on the parse pool
(`parallel.compute_pages`, the worker's own `chunks` and `metadata`):
  * the chunks the fts stage would write now (`worker._load_chunks`, from
    the document's current record — one parse) against the text index's
    stored chunks (`fts.book_chunks`, every group the document can sit in),
    by id, heading and text;
  * the fields the rdf stage writes on the document (title, authors,
    language, subjects, shelves) against the manifest's;
  * where the plugin declares validity, the start the metadata states now
    against the validity row (start, work, basis, text, force).
Only what differs has its marks reset (`carry.REDERIVE_MARKS`): it stays in
search and in the in-force filter until its stages replace its rows
(architecture's review of #767); only a document whose text-index group
changes loses its old group's rows at once. Its vectors are the records' and
stay. A dry run
counts, with its rate and the time the whole pass would take; `--sample`
checks every n-th document to measure first.

THE STANDARD, NOT ADOPTED: a content-addressed derivation log (each index row
naming the record digest it came from) would make this a lookup, not a
re-derivation; it pays once the indexes carry provenance, which they do not
today."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterator

#: documents a page (the pool's unit is a part of a page)
PAGE = 2_000
#: the rdf stage's fields on the document, as `_rdf_one` writes them
_DOC_FIELDS = ("title", "authors", "lang", "subjects", "bookshelves")


def _groups(collection, lang: str | None) -> list[str]:
    from app import fts
    lang = lang or "und"
    if lang == fts.MULTILINGUAL:
        return list(dict.fromkeys([fts.group_for(l) for l in fts.section_languages(collection)]
                                  + [fts.group_for(lang)]))
    return [fts.group_for(lang)]


def _stored_chunks(collection, row) -> set[tuple[str, str, str]]:
    from app import fts
    out: set[tuple[str, str, str]] = set()
    for g in _groups(collection, row["lang"]):
        for c in fts.book_chunks(g, int(row["native_id"]), collection):
            if str(c["chunk_id"]).startswith(f"{row['uid']}:"):
                out.add((str(c["chunk_id"]), c.get("chapter") or "", c.get("text") or ""))
    return out


def _now_chunks(chunks) -> set[tuple[str, str, str]]:
    return {(str(c["id"]), c.get("chapter") or "", c.get("text") or "") for c in chunks or ()}


def _validity_now(meta: dict) -> tuple | None:
    v = meta.get("validity")
    if not v:
        return None
    return (int(v["from"]), str(v["work"]), v.get("start_basis"), int(bool(v.get("has_text"))),
            v.get("in_force"))


def _validity_held(con, uid: str) -> tuple | None:
    r = con.execute("SELECT valid_from, work, start_basis, has_text, in_force FROM validity WHERE uid=?",
                    (uid,)).fetchone()
    return None if r is None else (int(r[0]), str(r[1]), r[2], int(r[3]), r[4])


#: the pass's cursor in the manifest's control table, when it writes (invariant 2)
CURSOR_KEY = "derived_check"


def _pages(con, *, every: int = 1, limit: int | None = None, start: int = 0) -> Iterator[list]:
    """The documents the stages marked done, a page at a time from a rowid
    cursor; `every`: one in that many (a sample)."""
    last, taken = start, 0
    while True:
        rows = con.execute("SELECT rowid, * FROM documents WHERE rowid > ? AND rdf_done=1 AND fts_done=1 "
                           "AND error IS NULL ORDER BY rowid LIMIT ?", (last, PAGE * every)).fetchall()
        if not rows:
            return
        last = int(rows[-1]["rowid"])
        page = rows[::every]
        if limit is not None:
            page = page[:max(0, limit - taken)]
        if page:
            taken += len(page)
            yield page
        if limit is not None and taken >= limit:
            return


def verify(root, name: str, *, apply: bool = False, every: int = 1, limit: int | None = None,
           again: bool = False, stop=None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The pass (the module docstring). Returns its counts and its rate.
    Writing, it resumes from its cursor (one page a commit; `again` starts
    over); a dry run or a sample keeps none."""
    import json
    from contextlib import closing
    from app import db, fts, parallel, validity as validity_mod
    from . import collection as col, records as rec
    from .carry import REDERIVE_MARKS
    root = Path(root)
    name, codex = col.resolve(root, name)
    coll = rec.local_collection(root, name, codex)
    declared = validity_mod.declared(coll) is not None
    counts = {"checked": 0, "chunks": 0, "document": 0, "validity": 0, "failed": 0, "reset": 0, "regrouped": 0}
    t0 = time.monotonic()
    with closing(db.connect(coll)) as con:
        total = int(con.execute("SELECT COUNT(*) FROM documents WHERE rdf_done=1 AND fts_done=1 "
                                "AND error IS NULL").fetchone()[0])
        keep = apply and every == 1 and limit is None
        held = json.loads(db.get_control(con, CURSOR_KEY) or "{}") if keep and not again else {}
        if held.get("for") != codex.collection_id:
            held = {}
        if held.get("done"):
            log(f"[rederive] {name}: the verify pass is done ({held}); --again to start over")
            return {"collection": name, **held, "applied": apply}
        start = int(held.get("rowid") or 0)
        counts.update({k: int(held.get(k) or 0) for k in counts})
        pages = list(_pages(con, every=every, limit=limit, start=start)) if limit is not None \
            else _pages(con, every=every, start=start)
        # compute_pages reads pages AHEAD (its window full across pages): each
        # page it takes waits in `handed`, and comes back out with its result
        from collections import deque
        handed: deque = deque()

        def feed():
            for p in pages:
                handed.append(p)
                yield p
        for got in parallel.compute_pages("chunks", coll, feed(), stop=stop):
            page = handed.popleft()
            metas = parallel.compute("metadata", coll, page)
            moved: list[str] = []
            regrouped: list[tuple] = []          # (row, the groups it leaves)
            for row, (ok, chunks), (ok_m, meta) in zip(page, got, metas):
                counts["checked"] += 1
                if not ok or not ok_m:
                    counts["failed"] += 1          # the stage's own error, when its turn comes
                    moved.append(row["uid"])
                    continue
                why = None
                if _now_chunks(chunks) != _stored_chunks(coll, row):
                    why = "chunks"
                elif any((meta.get(k) or None) != (row[k] or None) for k in _DOC_FIELDS if k in row.keys()):
                    why = "document"
                elif declared and _validity_now(meta) != _validity_held(con, row["uid"]):
                    why = "validity"
                if why:
                    counts[why] += 1
                    moved.append(row["uid"])
                    left = set(_groups(coll, row["lang"])) - set(_groups(coll, meta.get("lang")))
                    if left:
                        regrouped.append((row, sorted(left)))
            if apply and moved:
                # THE MARKS ONLY (architecture's review of #767): a moved document
                # stays in search and in the in-force filter until its stages
                # replace its rows — the fts stage deletes then inserts in its
                # group, the rdf stage writes or forgets its validity row — never
                # out of them for the hours a pass over 41 M documents takes
                # but a document whose text-index group changes: its re-index
                # writes the new group alone, so the old group's rows go now —
                # BEFORE its marks (architecture's review of #769): a stop between
                # leaves its marks done and its stored chunks gone, which the next
                # pass finds and resets; the other order would leave the old rows
                # for good, a document whose fts is not done never selected again
                for row, groups in regrouped:
                    for g in groups:
                        fts.delete_books(g, [int(row["native_id"])], coll)
                con.execute("BEGIN IMMEDIATE")
                for i in range(0, len(moved), 500):
                    part = moved[i:i + 500]
                    con.execute(f"UPDATE documents SET {REDERIVE_MARKS} WHERE uid IN "
                                f"({','.join('?' * len(part))})", part)
                con.execute("COMMIT")
                counts["reset"] += len(moved)
                counts["regrouped"] += len(regrouped)
            if keep:
                con.execute("BEGIN IMMEDIATE")
                db.set_control(con, CURSOR_KEY, json.dumps({"for": codex.collection_id,
                                                            "rowid": int(page[-1]["rowid"]), **counts}))
                con.execute("COMMIT")
            if stop is not None and stop.is_set():
                break
        else:
            if keep:
                con.execute("BEGIN IMMEDIATE")
                db.set_control(con, CURSOR_KEY, json.dumps({"for": codex.collection_id, "done": True,
                                                            **counts}))
                con.execute("COMMIT")
    secs = time.monotonic() - t0
    rate = counts["checked"] / secs if secs > 0 else 0.0
    out = {"collection": name, **counts, "documents_done": total, "seconds": round(secs, 1),
           "per_second": round(rate, 1),
           "whole_pass_hours": round(total / rate / 3600, 1) if rate else None, "applied": apply}
    log(f"[rederive] {name}: {counts['checked']} checked in {secs:.0f} s ({rate:.0f}/s); differ — chunks "
        f"{counts['chunks']}, document {counts['document']}, validity {counts['validity']}, failed "
        f"{counts['failed']}; {'reset ' + str(counts['reset']) if apply else 'nothing written'}; the whole pass "
        f"~{out['whole_pass_hours']} h")
    return out
