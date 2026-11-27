"""WHAT A DOCUMENT'S WORK IS, kept beside it (docs/notes/fact-fiction-search.md;
operator, 2026-11-27: "The search itself shouldn't return novels when I'm
looking only for facts and vice versa").

The verdict is the record's own (app/retrieval.py `work_verdict`: its LCSH
headings, its PG category shelves, its LCC class), worked out from the
manifest's own `subjects` and `bookshelves` — never by re-reading the RDF —
and kept in three derived columns of `documents`:

- `work_kind`: 'literary' | 'opinion' | NULL (no verdict: a fact, or
  scripture, or a record that states nothing);
- `work_kind_basis`: the verdict's basis ("by its subject headings", "by
  its Gutenberg category", "by its Library of Congress class"), for the
  page's "left out, and why";
- `work_kind_v`: the rules' version it was computed by (`RULES_VERSION`).

WHO WRITES THEM. The rdf stage, for every record it writes (`row_values`).
The index maintainer, for the rows already there, in rowid WINDOWS from a
cursor kept per rules version (`maintain`): a round's cost scales with the
rows left, and a collection done for this version costs one control read.
A rule change bumps RULES_VERSION and the maintainer recomputes by itself —
plug and play, no command. Only for CLASSIFIED_PLUGINS (Gutenberg): every
other collection's records carry no kind, and count as facts.

Derived and re-creatable (invariant 6): an ALTER adds the columns, no rowid
moves, nothing is VACUUMed; the record itself is not touched.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

#: the rules' version: BUMPED EXPLICITLY with any change to
#: retrieval.work_verdict and the rules under it (invariant 3's spirit: no
#: silent re-pin); every row computed by another version is recomputed
RULES_VERSION = 2  # 2: periodicals of a bare genre and studied works give no verdict (architecture, 2026-11-27)
#: the rows one maintainer window covers, in its own BEGIN IMMEDIATE
WINDOW = 5_000
#: the maintainer's time on one collection, a round
BUDGET_S = 30.0
#: where the maintainer's place is kept (db control table), per collection's manifest
CURSOR_KEY = "work_kind_cursor"

KINDS = ("literary", "opinion")
_KIND_OF = {"a literary work": "literary", "a work of opinion or belief": "opinion"}


def classified(collection) -> bool:
    from app import retrieval
    return getattr(collection, "plugin", None) in retrieval.CLASSIFIED_PLUGINS


def compute(subjects: str | None, bookshelves: str | None) -> tuple[str | None, str | None]:
    """(kind, basis) of one record, from its own subjects and shelves."""
    from app import retrieval
    v = retrieval.work_verdict({"subjects": subjects or "", "bookshelves": bookshelves or ""})
    return (_KIND_OF.get(v["kind"]), v["basis"]) if v else (None, None)


def row_values(collection, subjects: str | None, bookshelves: str | None) -> tuple:
    """(work_kind, work_kind_basis, work_kind_v) for the rdf stage's UPDATE:
    computed for a classified collection, NULLs (and this version) else."""
    if not classified(collection):
        return (None, None, RULES_VERSION)
    return (*compute(subjects, bookshelves), RULES_VERSION)


# ---- the maintainer: the rows already there ----------------------------------

#: one window, by the table's own b-tree (rowid range), the rows not yet of this version
WINDOW_SQL = ("SELECT rowid, subjects, bookshelves FROM documents "
              "WHERE rowid > ? AND rowid <= ? AND work_kind_v IS NOT ?")


def maintain(con, collection, *, may_continue=lambda: True, budget_s: float | None = None,
             window: int | None = None) -> dict | None:
    """Recompute the rows of another rules version, one rowid window at a
    time for at most `budget_s`, each window in its own BEGIN IMMEDIATE;
    the cursor kept per version. None when there is nothing to do: not a
    classified collection, an empty manifest, or done for this version."""
    from app import db
    if not classified(collection):
        return None
    budget_s = BUDGET_S if budget_s is None else budget_s
    window = WINDOW if window is None else window
    try:
        state = json.loads(db.get_control(con, CURSOR_KEY, "") or "{}")
    except ValueError:
        state = {}
    if state.get("v") != RULES_VERSION:
        state = {"v": RULES_VERSION, "cursor": 0, "complete": False}
    if state.get("complete"):
        return None
    top = con.execute("SELECT max(rowid) FROM documents").fetchone()[0] or 0
    if top == 0:
        return None
    cursor, t0, written, windows = int(state["cursor"]), time.monotonic(), 0, 0
    while cursor < top and time.monotonic() - t0 < budget_s and may_continue():
        end = min(cursor + window, top)
        con.execute("BEGIN IMMEDIATE")
        try:
            rows = con.execute(WINDOW_SQL, (cursor, end, RULES_VERSION)).fetchall()
            con.executemany("UPDATE documents SET work_kind=?, work_kind_basis=?, work_kind_v=? WHERE rowid=?",
                            [(*compute(r[1], r[2]), RULES_VERSION, r[0]) for r in rows])
            cursor = end
            state = {**state, "cursor": cursor, "complete": cursor >= top}
            db.set_control(con, CURSOR_KEY, json.dumps(state))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        written += len(rows)
        windows += 1
    return {"windows": windows, "written": written, "cursor": cursor, "top": top,
            "complete": bool(state.get("complete")), "secs": round(time.monotonic() - t0, 1)}


# ---- the filter's read: the candidates alone ---------------------------------

#: book ids asked at once (SQLite's variable limit is far above; one plan for all)
CHUNK = 500


def kinds_of(con, collection, book_ids: Iterable[int]) -> dict[int, tuple[str, str | None]]:
    """{book_id: (kind, basis)} of the candidates that have a kind — read for
    them alone (`native_id IN (…)`, the (source, native_id) key), never a scan."""
    if not classified(collection):
        return {}
    ids = [str(i) for i in dict.fromkeys(book_ids)]
    out: dict[int, tuple[str, str | None]] = {}
    for i in range(0, len(ids), CHUNK):
        part = ids[i:i + CHUNK]
        for r in con.execute(
                f"SELECT native_id, work_kind, work_kind_basis FROM documents WHERE source=? "
                f"AND native_id IN ({','.join('?' * len(part))}) AND work_kind IS NOT NULL",
                [collection.plugin] + part):
            out[int(r[0])] = (r[1], r[2])
    return out


def keep(kind_asked: str, work_kind: str | None) -> bool:
    """Whether a candidate of `work_kind` stays in a `kind_asked` search:
    facts keep all but the literary (opinion stays, attributed); literature
    keeps the literary alone; all keeps everything."""
    if kind_asked == "facts":
        return work_kind != "literary"
    if kind_asked == "literature":
        return work_kind == "literary"
    return True


#: the search kinds (decision 1); "auto" is resolved from the question first
SEARCH_KINDS = ("facts", "literature", "all")
#: how much deeper the legs go when a filter will thin them (docs/notes/
#: fact-fiction-search.md §3: "it over-fetches where the filter thins a leg,
#: so the reranker still sees its depth"). Up front, not a second pass: a
#: second pass would embed the query again. To be measured on the judged set.
OVERFETCH = 2


def filters(collection, kind_asked: str) -> bool:
    """Whether a `kind_asked` search thins this collection's legs (and so
    fetches them `OVERFETCH` deeper)."""
    return kind_asked in ("facts", "literature") and classified(collection)


def filter_candidates(con, collection, candidates: list[dict], kind_asked: str,
                      want: int | None = None) -> tuple[list[dict], dict[str, Any]]:
    """The candidates a `kind_asked` search keeps, in their order, and what
    was left out: {count, by_basis}. With `want`, the walk stops once that
    many are kept: what is counted as left out is what the reranker would
    have seen, not what the deeper legs brought. A collection whose records
    carry no kind counts as facts (decision 3): kept by facts, left out of
    literature."""
    left: dict[str, Any] = {"count": 0, "by_basis": {}}
    if kind_asked not in ("facts", "literature"):
        return candidates, left
    if not classified(collection):
        if kind_asked == "literature":
            n = len(candidates) if want is None else min(len(candidates), want)
            left["count"] = n
            if n:
                left["by_basis"]["no literary works in this collection"] = n
            return [], left
        return candidates, left
    kinds = kinds_of(con, collection, (h.get("book_id") for h in candidates if h.get("book_id") is not None))
    kept = []
    for h in candidates:
        if want is not None and len(kept) >= want:
            break
        kind, basis = kinds.get(h.get("book_id"), (None, None))
        if keep(kind_asked, kind):
            kept.append(h)
        else:
            left["count"] += 1
            why = basis or "no literary verdict in its record"
            left["by_basis"][why] = left["by_basis"].get(why, 0) + 1
    return kept, left


def add_left(total: dict[str, Any], one: dict[str, Any]) -> dict[str, Any]:
    """Two left-out tallies summed (a federated search's collections)."""
    total["count"] = total.get("count", 0) + one["count"]
    by = total.setdefault("by_basis", {})
    for why, n in one["by_basis"].items():
        by[why] = by.get(why, 0) + n
    return total
