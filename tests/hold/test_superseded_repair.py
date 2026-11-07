"""`louise dev superseded-repair` — documents counted as an earlier version
still held (app/status.py SUPERSEDED, the Library's grey) that hold no vector.

München, 2026-11-07: wikidata's card painted 458,636 documents grey; 0 of 50
sampled held a row (its re-creation wiped the vectors, cleared the marks and
kept embed_version 2), while gutenberg's 11,109 were real, 50 of 50 holding.
The fixture is both in small: candidates with rows (gutenberg's, kept) and
without (wikidata's, reset), beside every kind of document the repair must
leave alone.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from app import collections as collections_mod, config, db, embed_marks, embeddings, status
from app.hold import superseded_repair as repair_mod

NAME = "gutenberg"
E = 7
#: (kind, embed_done, embed_version, error, holds vectors) — round-robin by rowid
KINDS = (("without", 0, 2, None, False), ("held", 0, 2, None, True), ("without", 0, 2, None, False),
         ("done", 1, E, None, True), ("without", 0, 2, None, False), ("waiting", 0, 0, None, False),
         ("declined", -1, 2, None, False), ("unindexable", 0, 2, "no txt file", False),
         # marked embedded, no vector: what a delete left before it cleared the mark
         ("gone", 1, E, None, False),
         # marked embedded with no text to embed (chunks 0): rightly holds nothing
         ("empty", 1, E, None, False))
CHUNKLESS = {"empty"}
ROUNDS = 10
WITHOUT, HELD = 3 * ROUNDS, 1 * ROUNDS
GONE, DONE_HELD = 1 * ROUNDS, 1 * ROUNDS


@pytest.fixture
def coll(isolated_config):
    c = collections_mod.by_name(NAME)
    con = db.connect(c)
    vectors = []
    i = 0
    for _ in range(ROUNDS):
        for kind, done, version, error, held in KINDS:
            i += 1
            con.execute(
                "INSERT INTO documents(uid,source,native_id,path,in_scope,rdf_done,fts_done,embed_done,"
                "embed_version,chunks,error) VALUES(?,?,?,?,1,1,1,?,?,?,?)",
                (f"{NAME}:{i}", NAME, str(i), f"/x/{i}", done, version,
                 0 if kind in CHUNKLESS else 2, error))
            if held:
                vectors += [{"id": f"{NAME}:{i}:{s}:1", "book_id": i, "lang": "en", "seq": s,
                             "chapter": "", "text": "t", "vector": np.zeros(768, dtype="float32")}
                            for s in range(2)]
    con.close()
    embeddings.upsert_chunks(vectors, collection=c, dtype="f16")
    return c


def _kinds() -> dict[str, str]:
    return {f"{NAME}:{r * len(KINDS) + k + 1}": KINDS[k][0]
            for r in range(ROUNDS) for k in range(len(KINDS))}


def _rows(c) -> dict[str, tuple[int, int]]:
    con = db.connect(c)
    try:
        return {r[0]: (int(r[1]), int(r[2] or 0))
                for r in con.execute("SELECT uid, embed_done, embed_version FROM documents")}
    finally:
        con.close()


def _superseded(c) -> int:
    con = db.connect(c)
    try:
        return status.bounded(con, stored=None)["embed"].superseded
    finally:
        con.close()


def _cursor(c):
    con = db.connect(c)
    try:
        raw = db.get_control(con, repair_mod.CURSOR_KEY)
    finally:
        con.close()
    return json.loads(raw) if raw else None


def test_a_dry_run_counts_both_kinds_and_writes_nothing(coll):
    before = _rows(coll)
    assert _superseded(coll) == WITHOUT + HELD, "the defect: the wiped counted as held"
    out = repair_mod.repair(coll, span=7)
    assert (out["candidates"], out["holding_vectors"], out["without_vectors"]) == (WITHOUT + HELD, HELD, WITHOUT)
    assert out["apply"] is False and "reset" not in out and out["done"]
    kinds = _kinds()
    assert {kinds[u] for u in out["sample"]["without_vectors"]} == {"without"}
    assert {kinds[u] for u in out["sample"]["holding_vectors"]} == {"held"}
    assert len(out["sample"]["without_vectors"]) == repair_mod.SAMPLE
    assert _rows(coll) == before and _cursor(coll) is None, "read-only by default"


def test_apply_resets_only_the_documents_without_vectors_and_is_idempotent(coll):
    out = repair_mod.repair(coll, apply=True, span=7)
    assert (out["reset"], out["changed_meanwhile"]) == (WITHOUT, 0)
    kinds = _kinds()
    after = _rows(coll)
    for uid, kind in kinds.items():
        want = {"without": (0, 0), "held": (0, 2), "done": (1, E), "waiting": (0, 0),
                "declined": (-1, 2), "unindexable": (0, 2), "gone": (1, E), "empty": (1, E)}[kind]
        assert after[uid] == want, (uid, kind, after[uid])
    assert _superseded(coll) == HELD, "what is left grey holds its vectors (gutenberg's)"
    assert _cursor(coll)["done"] is True
    again = repair_mod.repair(coll, apply=True, span=7)
    assert (again["candidates"], again["reset"], again["resumed_from_rowid"]) == (HELD, 0, 0), \
        "a finished pass starts over and resets nothing"
    assert _rows(coll) == after


def test_a_document_the_worker_marks_meanwhile_is_left_as_marked(coll, monkeypatch):
    """The guard and the predicate inside the UPDATE: a candidate embedded
    between the window's read and its write keeps the worker's mark."""
    real = embeddings.books_with_vectors
    marked = []

    def worker_meanwhile(books, collection=None, **kw):
        if not marked:
            victim = next(u for u, k in _kinds().items() if k == "without")
            other = sqlite3.connect(collection.manifest_path, timeout=30)
            other.execute("UPDATE documents SET embed_done=1, embed_version=? WHERE uid=?", (E, victim))
            other.commit()
            other.close()
            marked.append(victim)
        return real(books, collection, **kw)
    monkeypatch.setattr(embeddings, "books_with_vectors", worker_meanwhile)
    out = repair_mod.repair(coll, apply=True, span=1000)
    assert (out["reset"], out["changed_meanwhile"]) == (WITHOUT - 1, 1)
    assert _rows(coll)[marked[0]] == (1, E), "the worker's mark and version stand"


def test_it_resumes_from_its_cursor(coll, monkeypatch):
    """Stopped after two windows of 8 rowids, the cursor names rowid 16; the
    next run asks nothing below it and ends with the counts of one pass."""
    checks = iter(range(10 ** 6))
    first = repair_mod.repair(coll, apply=True, span=8, stop=lambda: next(checks) >= 2)
    assert first["interrupted"] and not first["done"] and first["rowid"] == 16
    assert _cursor(coll)["rowid"] == 16 and _cursor(coll)["done"] is False
    real, asked = embeddings.books_with_vectors, []

    def recorded(books, collection=None, **kw):
        asked.extend(books)
        return real(books, collection, **kw)
    monkeypatch.setattr(embeddings, "books_with_vectors", recorded)
    second = repair_mod.repair(coll, apply=True, span=8)
    assert second["resumed_from_rowid"] == 16 and second["done"]
    assert asked and min(asked) > 16, "nothing below the cursor is asked again"
    assert (second["candidates"], second["reset"]) == (WITHOUT + HELD, WITHOUT), "the pass's totals"
    assert _superseded(coll) == HELD


def test_a_cursor_written_for_another_manifest_starts_over(coll):
    """Keyed on the manifest's path: a copied manifest brings its cursor along."""
    con = db.connect(coll)
    db.set_control(con, repair_mod.CURSOR_KEY, json.dumps(
        {**repair_mod._fresh("/elsewhere/state/manifest.sqlite"), "rowid": 10 ** 6, "reset": 99}))
    con.close()
    out = repair_mod.repair(coll, apply=True)
    assert (out["resumed_from_rowid"], out["reset"]) == (0, WITHOUT)
    assert _cursor(coll)["manifest"] == str(Path(coll.manifest_path).resolve())


def test_a_question_is_sized_from_the_tables_fragments(coll, monkeypatch):
    """A filter costs memory per value per fragment (carry._delete_batch): a
    table of a million fragments is asked 100 books at a time, and the pass
    still sees every candidate."""
    assert repair_mod.repair(coll)["per_query"] == repair_mod.PER_QUERY
    monkeypatch.setattr(embeddings, "fragment_count", lambda _c: 10 ** 6)
    out = repair_mod.repair(coll)
    assert out["per_query"] == 100 and out["without_vectors"] == WITHOUT


def test_a_store_holding_no_row_is_asked_nothing(coll, monkeypatch):
    """A table wiped whole: every candidate holds nothing, proven once."""
    embeddings._dbs.pop(str(coll.lance_dir), None)
    shutil.rmtree(coll.lance_dir)

    def never(*_a, **_k):
        raise AssertionError("an empty store is not asked")
    monkeypatch.setattr(embeddings, "books_with_vectors", never)
    out = repair_mod.repair(coll, apply=True)
    assert out["store_empty"] and out["reset"] == WITHOUT + HELD


def test_a_store_that_cannot_answer_resets_nothing(coll, monkeypatch):
    before = _rows(coll)

    def broken(*_a, **kw):
        assert kw.get("strict") is True
        raise OSError("the store cannot be read")
    monkeypatch.setattr(embeddings, "books_with_vectors", broken)
    with pytest.raises(OSError):
        repair_mod.repair(coll, apply=True)
    assert _rows(coll) == before


def test_a_strict_question_raises_where_the_holder_pass_reads_nothing(coll, monkeypatch):
    """`books_with_vectors`: the holder pass's "nothing held" on an unreadable
    store costs a part bundled again; a caller that resets on it says strict."""
    class Broken:
        def search(self):
            raise OSError("fragment missing")

    class Conn:
        def table_names(self):
            return [embeddings.TABLE]

        def open_table(self, _name):
            return Broken()
    monkeypatch.setattr(embeddings, "_conn", lambda _c: Conn())
    assert embeddings.books_with_vectors([1, 2], coll) == set()
    with pytest.raises(OSError):
        embeddings.books_with_vectors([1, 2], coll, strict=True)


def test_a_question_asks_in_batches_and_reads_each_book_once(coll):
    held = {int(u.split(":")[1]) for u, k in _kinds().items() if k in ("held", "done")}
    every = list(range(1, ROUNDS * len(KINDS) + 1))
    assert embeddings.books_with_vectors(every, coll, strict=True, batch=len(every)) == held
    assert embeddings.books_with_vectors(every, coll, strict=True, batch=3) == held


def test_the_window_statements_keep_their_plans(coll):
    """CLAUDE.md: selection queries plan-checked — the primary key, no TEMP
    B-TREE, no LIST SUBQUERY, no scan of the corpus."""
    con = db.connect(coll)
    p = {"max": config.MAX_RETRIES}
    stmts = [
        (repair_mod.window_sql(), {**p, "lo": 0, "hi": 10, "n": 5}, "INTEGER PRIMARY KEY (rowid>? AND rowid<?)"),
        (repair_mod.reset_sql(), {**p, "rowid": 1, "version": 2}, "INTEGER PRIMARY KEY (rowid=?)"),
        (embed_marks.UNHELD_WINDOW_SQL, (0, 10), "INTEGER PRIMARY KEY (rowid>? AND rowid<?)"),
        ("UPDATE documents SET embed_version=0 WHERE uid IN (?,?) AND " + embed_marks._NOT_HELD,
         ("a", "b"), "(uid=?)"),
        (repair_mod.window_sql("done"), {**p, "lo": 0, "hi": 10, "n": 5},
         "INTEGER PRIMARY KEY (rowid>? AND rowid<?)"),
        (repair_mod.reset_sql("done"), {**p, "rowid": 1, "version": 2}, "INTEGER PRIMARY KEY (rowid=?)"),
        (embed_marks.UNMARK_WINDOW_SQL, (0, 10), "INTEGER PRIMARY KEY (rowid>? AND rowid<?)"),
        (embed_marks._DROPPED_GONE.format(marks="?,?"), ("a", "b"), "(uid=?)"),
        (embed_marks._DROPPED_PART.format(marks="?,?"), ("a", "b"), "(uid=?)"),
    ]
    for sql, params, want in stmts:
        plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, params))
        assert want in plan, (sql, plan)
        for bad in ("TEMP B-TREE", "LIST SUBQUERY", "SCAN documents"):
            assert bad not in plan, (sql, plan)
    con.close()


def test_unheld_manifest_clears_every_not_embedded_version_in_windows(coll):
    """`carry --unembedded`'s pass: short windows, the declined and the
    embedded left alone, nothing found the second time."""
    con = db.connect(coll)
    n = embed_marks.unheld_manifest(con, window=7)
    assert n == WITHOUT + HELD + ROUNDS, "every embed_done = 0 with a version, the unindexable too"
    assert embed_marks.unheld_manifest(con, window=7) == 0
    con.close()
    kinds = _kinds()
    after = _rows(coll)
    assert all(after[u] == (-1, 2) for u, k in kinds.items() if k == "declined")
    assert all(after[u] == (1, E) for u, k in kinds.items() if k == "done")


def test_mark_unheld_leaves_a_marked_document_alone(coll):
    kinds = _kinds()
    done = next(u for u, k in kinds.items() if k == "done")
    without = [u for u, k in kinds.items() if k == "without"]
    con = db.connect(coll)
    assert embed_marks.mark_unheld(con, [done, *without], window=4) == WITHOUT
    assert embed_marks.mark_unheld(con, without) == 0
    con.close()
    assert _rows(coll)[done] == (1, E)


def test_the_command_runs_dry_then_applies(coll, capsys):
    from app import louise
    root = str(collections_mod.louise_root() / "collections")
    assert louise.main(["dev", "superseded-repair", NAME, "--root", root]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert Path(dry["manifest"]) == Path(coll.manifest_path).resolve(), "the node's own manifest"
    assert (dry["without_vectors"], dry["apply"]) == (WITHOUT, False)
    assert louise.main(["dev", "superseded-repair", NAME, "--root", root, "--apply"]) == 0
    got = capsys.readouterr()
    assert json.loads(got.out)["reset"] == WITHOUT
    assert f"{WITHOUT} reset to embed_version 0" in got.err
    assert _superseded(coll) == HELD


# ---- --done-without-vectors: marked embedded, holding no vector ---------------

def _done_cursor(c):
    con = db.connect(c)
    try:
        raw = db.get_control(con, repair_mod.DONE_CURSOR_KEY)
    finally:
        con.close()
    return json.loads(raw) if raw else None


def test_done_mode_finds_the_documents_marked_embedded_that_hold_nothing(coll):
    """Share's withdraw, `forget --kinds vectors`, `vectors check --drop`
    left such documents before 2026-11-07: never embedded again, missing
    from dense search without a word. A dry run counts them and writes
    nothing; --apply marks exactly them not embedded, version 0 — not the
    ones holding rows, not the ones with no text (chunks 0) — under its own
    cursor, and a second run finds none."""
    before = _rows(coll)
    dry = repair_mod.repair(coll, mode="done", span=7)
    assert (dry["mode"], dry["candidates"], dry["holding_vectors"], dry["without_vectors"]) == \
        ("done", GONE + DONE_HELD, DONE_HELD, GONE)
    kinds = _kinds()
    assert {kinds[u] for u in dry["sample"]["without_vectors"]} == {"gone"}
    assert {kinds[u] for u in dry["sample"]["holding_vectors"]} == {"done"}
    assert _rows(coll) == before and _done_cursor(coll) is None
    assert "would be marked not embedded" in repair_mod.summary(dry)

    out = repair_mod.repair(coll, mode="done", apply=True, span=7)
    assert (out["reset"], out["changed_meanwhile"]) == (GONE, 0)
    after = _rows(coll)
    for uid, kind in kinds.items():
        assert after[uid] == ((0, 0) if kind == "gone" else before[uid]), (uid, kind)
    assert _done_cursor(coll)["done"] is True and _cursor(coll) is None, "its own cursor"
    again = repair_mod.repair(coll, mode="done", apply=True, span=7)
    assert (again["candidates"], again["reset"]) == (DONE_HELD, 0)


def test_done_mode_leaves_a_document_re_embedded_meanwhile(coll, monkeypatch):
    """The guard: a candidate whose version changed between the read and the
    write (the worker's re-embed marked it at another version) is left."""
    real = embeddings.books_with_vectors
    victim = next(u for u, k in _kinds().items() if k == "gone")

    def worker_meanwhile(books, collection=None, **kw):
        other = sqlite3.connect(collection.manifest_path, timeout=30)
        other.execute("UPDATE documents SET embed_version=? WHERE uid=?", (E + 1, victim))
        other.commit()
        other.close()
        return real(books, collection, **kw)
    monkeypatch.setattr(embeddings, "books_with_vectors", worker_meanwhile)
    out = repair_mod.repair(coll, mode="done", apply=True, span=1000)
    assert (out["reset"], out["changed_meanwhile"]) == (GONE - 1, 1)
    assert _rows(coll)[victim] == (1, E + 1)


def test_done_mode_on_a_store_holding_no_row_marks_every_one(coll, monkeypatch):
    embeddings._dbs.pop(str(coll.lance_dir), None)
    shutil.rmtree(coll.lance_dir)

    def never(*_a, **_k):
        raise AssertionError("an empty store is not asked")
    monkeypatch.setattr(embeddings, "books_with_vectors", never)
    out = repair_mod.repair(coll, mode="done", apply=True)
    assert out["store_empty"] and out["reset"] == GONE + DONE_HELD


def test_the_command_takes_done_without_vectors(coll, capsys):
    from app import louise
    root = str(collections_mod.louise_root() / "collections")
    assert louise.main(["dev", "superseded-repair", NAME, "--root", root, "--done-without-vectors"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert (dry["mode"], dry["without_vectors"], dry["apply"]) == ("done", GONE, False)
    assert louise.main(["dev", "superseded-repair", NAME, "--root", root, "--done-without-vectors",
                        "--apply"]) == 0
    got = capsys.readouterr()
    assert json.loads(got.out)["reset"] == GONE
    assert f"[done-without-vectors] {NAME}: " in got.err
    assert f"{GONE} marked not embedded, embed_version 0" in got.err


# ---- embed_marks: after a delete -------------------------------------------------

def test_mark_dropped_asks_the_table_after_the_delete(coll):
    """No row left: not embedded, version 0. Rows left: not done, version
    kept. A declined document is left; a second call changes nothing."""
    kinds = _kinds()
    held = next(u for u, k in kinds.items() if k == "done")      # holds rows
    gone = next(u for u, k in kinds.items() if k == "gone")      # holds none
    declined = next(u for u, k in kinds.items() if k == "declined")
    con = db.connect(coll)
    got = embed_marks.mark_dropped(con, coll, [held, gone, declined], window=1)
    assert got == {"reset": 2, "unheld": 1}
    assert embed_marks.mark_dropped(con, coll, [held, gone, declined]) == {"reset": 0, "unheld": 0}
    con.close()
    after = _rows(coll)
    assert (after[held], after[gone], after[declined]) == ((0, E), (0, 0), (-1, 2))


def test_mark_dropped_on_an_unreadable_table_clears_the_marks_and_keeps_the_versions(coll, monkeypatch):
    kinds = _kinds()
    gone = next(u for u, k in kinds.items() if k == "gone")

    def broken(*_a, **_k):
        raise OSError("the store cannot be read")
    monkeypatch.setattr(embeddings, "books_with_vectors", broken)
    said: list[str] = []
    con = db.connect(coll)
    assert embed_marks.mark_dropped(con, coll, [gone], log=said.append) == {"reset": 1, "unheld": 0}
    con.close()
    assert _rows(coll)[gone] == (0, E), "not done is always true after a delete; no row, only when proven"
    assert said and "versions kept" in said[0]


def test_unmark_manifest_clears_every_mark_and_version_but_the_declined(coll):
    con = db.connect(coll)
    n = embed_marks.unmark_manifest(con, window=7)
    assert embed_marks.unmark_manifest(con, window=7) == 0
    con.close()
    kinds = _kinds()
    after = _rows(coll)
    assert all(after[u] == (-1, 2) for u, k in kinds.items() if k == "declined")
    assert all(v == (0, 0) for u, v in after.items() if kinds[u] != "declined")
    assert n == sum(1 for k in kinds.values() if k not in ("declined", "waiting"))
