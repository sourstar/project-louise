"""THE A1c RE-DERIVE (2026-11-19). `carry --rebuilt` copies the manifest —
its rdf/fts marks, its validity table — and the text index; `reconcile`
reset only `embed_done`, and only where `content_hash` moved. But the
worker's validity, catalogue and text-index rows come from its OWN parse,
so a record whose parse moved kept the old parser's (München, confirmed on
the node: NeuRIS5's 51 records without a validity row, Normattiva ~1.5 %
with parser 9's "— null" headings in its text index).

Architecture's rule (relayed by München, the same day): a document has
MOVED when its record line differs from the source snapshot's, `extra`
included, provenance (`origin`, `origin_hash`) aside. A moved document's
rdf/fts marks are reset and its text-index, catalogue and validity rows
deleted; its vectors follow `content_hash` alone. `rederive` is the repair
where no source snapshot is left: every document derived again."""
from __future__ import annotations

import pytest

from app import db, embeddings, fts, validity
from app.hold import carry
from tests.hold.test_carry import _build_and_freeze, _local, _rebuilt_pair, pair, quiet  # noqa: F401


def _indexed(root, name):
    """Every document's text-index rows, as the fts stage writes them."""
    coll = _local(root, name)
    con = db.connect(coll)
    try:
        rows = con.execute("SELECT * FROM documents ORDER BY rowid").fetchall()
        con.execute("UPDATE documents SET rdf_done=1, fts_done=1")
        con.commit()
    finally:
        con.close()
    for r in rows:
        fts.index_book(r["lang"] or "en", int(r["native_id"]),
                       [{"id": f"{r['uid']}:0:1", "chapter": "", "text": "a b c"}], coll)
    return coll, rows


def _reconciled(pair, corpus_dir, monkeypatch, *, moved_index: int | None = None,  # noqa: F811
                revise_text: bool = False):
    """The rebuilt pair, carried, indexed, built and frozen; optionally one
    record whose parse moved outside its content (its parse digest changed
    on the target's side) or whose text was revised at the origin."""
    root, old, re_, uids, ids = _rebuilt_pair(pair)
    carry.carry(root, "old", "re", rebuilt=True, copy=True, apply=True, log=quiet)
    coll, rows = _indexed(root, "re")
    if revise_text:
        book = corpus_dir / uids[0].split(":")[1]
        txt = next(book.glob("*.txt*"))
        data = txt.read_bytes()
        i = data.index(b"*** END OF")
        txt.write_bytes(data[:i] + b"A paragraph revised upstream.\n\n" + data[i:])
        con = db.connect(coll)
        try:
            con.execute("UPDATE documents SET mtime=mtime+1 WHERE uid=?", (uids[0],))
            con.commit()
        finally:
            con.close()
    _build_and_freeze(root, "re", corpus_dir)
    moved_outside = uids[moved_index] if moved_index is not None else None
    if moved_outside is not None:
        real = carry._part_content

        def moved(path, with_origin):
            got = real(path, with_origin)
            return [(u, ch, oh, (lh + b"x") if with_origin and u == moved_outside else lh)
                    for u, ch, oh, lh in got]
        monkeypatch.setattr(carry, "_part_content", moved)
    out = carry.reconcile(root, "old", "re", window=1, log=quiet)
    return root, coll, uids, ids, rows, out


def _marks(coll, uid):
    con = db.connect(coll)
    try:
        return tuple(con.execute("SELECT rdf_done, fts_done, embed_done FROM documents WHERE uid=?",
                                 (uid,)).fetchone())
    finally:
        con.close()


def _held(coll, row) -> bool:
    return fts.books_held(fts.group_for(row["lang"] or "en"), [int(row["native_id"])], coll) > 0


def test_an_unchanged_record_is_untouched(pair, corpus_dir, monkeypatch):  # noqa: F811
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch)
    assert (out["reset"], out["rederived"]) == (0, 0), out
    assert all(_marks(coll, u)[:2] == (1, 1) for u in uids) and all(_held(coll, r) for r in rows)


def test_a_field_outside_the_text_moved_the_document_is_derived_again_its_vectors_kept(
        pair, corpus_dir, monkeypatch):  # noqa: F811
    """A heading or a date in `extra` changed, the text not: its derived rows
    go and come back from the new parse; its vectors stay."""
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch, moved_index=1)
    victim = uids[1]
    assert (out["reset"], out["rederived"]) == (0, 1), out
    assert _marks(coll, victim)[:2] == (0, 0), "derived again by the worker"
    left = set(embeddings.chunk_ids_of_books([int(u.split(":")[1]) for u in uids], collection=coll))
    assert left == set(ids), "every vector kept: no content moved"
    row = next(r for r in rows if r["uid"] == victim)
    assert not _held(coll, row), "its old text-index rows deleted, never served meanwhile"
    assert all(_held(coll, r) for r in rows if r["uid"] != victim), "the unchanged untouched"
    assert all(_marks(coll, u)[:2] == (1, 1) for u in uids if u != victim)


def test_the_text_moved_both_are_done_again(pair, corpus_dir, monkeypatch):  # noqa: F811
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch, revise_text=True)
    victim = uids[0]
    assert out["reset"] == 1 and out["rederived"] >= 1, out
    assert _marks(coll, victim) == (0, 0, 0), "re-derived and embedded again"
    left = set(embeddings.chunk_ids_of_books([int(u.split(":")[1]) for u in uids], collection=coll))
    assert left == {c for c in ids if not c.startswith(victim + ":")}
    assert not _held(coll, next(r for r in rows if r["uid"] == victim))


def test_rederive_is_the_repair_where_no_source_is_left(pair, corpus_dir, monkeypatch):  # noqa: F811
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch)
    dry = carry.rederive(root, "re", log=quiet)
    assert dry["would_reset"] == len(uids) and all(_marks(coll, u)[:2] == (1, 1) for u in uids), "a dry run"
    embedded = {u: _marks(coll, u)[2] for u in uids}
    got = carry.rederive(root, "re", apply=True, window=1, log=quiet)
    assert got["reset"] == len(uids) and all(_marks(coll, u)[:2] == (0, 0) for u in uids)
    assert {u: _marks(coll, u)[2] for u in uids} == embedded, "the embed marks untouched: vectors kept"
    assert carry.rederive(root, "re", apply=True, log=quiet)["reset"] == len(uids), "done: not redone"
    assert carry.rederive(root, "re", apply=True, again=True, log=quiet)["reset"] == len(uids)


# ---- the validity rows ----------------------------------------------------------------

@pytest.fixture
def vcon(isolated_config, tmp_path):
    from app import collections as collections_mod
    c = collections_mod.Collection(name="v", plugin="eurlex", profile=None, embed_mode_default=None,
                                   corpus_dir=tmp_path / "origin", manifest_path=tmp_path / "state" / "m.sqlite",
                                   fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")
    c.manifest_path.parent.mkdir(parents=True)
    con = db.connect(c)
    for i, uid in enumerate(("eurlex:1", "eurlex:2"), 1):
        con.execute("INSERT INTO documents(uid, source, native_id, path, rdf_done, fts_done, embed_done) "
                    "VALUES(?,?,?,?,1,1,1)", (uid, "eurlex", str(i), f"/p/{i}"))
    con.commit()
    yield c, con
    con.close()


def _meta(start: int):
    return {"from": start, "work": "W/it", "has_text": True, "in_force": 1, "keys": ["celex:W"],
            "closes": [], "start_basis": None}


def test_a_start_added_writes_its_row_and_a_start_gone_takes_it_away(vcon):
    c, con = vcon
    validity.record(con, "eurlex:1", 1, _meta(20000101), day=20260101)
    assert con.execute("SELECT valid_to FROM validity WHERE uid='eurlex:1'").fetchone()[0] is None
    validity.record(con, "eurlex:2", 2, _meta(20100101), day=20260101)   # a start added: its row
    assert con.execute("SELECT valid_to FROM validity WHERE uid='eurlex:1'").fetchone()[0] == 20100101
    moved = validity.forget(con, "eurlex:2", day=20260101)              # its start gone
    assert con.execute("SELECT COUNT(*) FROM validity WHERE uid='eurlex:2'").fetchone()[0] == 0
    assert tuple(con.execute("SELECT valid_to, shown FROM validity WHERE uid='eurlex:1'").fetchone()) == (None, 1)
    assert moved == ["eurlex:1"], "its neighbour's end and shown moved back"
    assert validity.forget(con, "eurlex:2") == [], "none held: nothing"


def test_the_rdf_stage_takes_a_row_away_when_the_parse_states_no_start(vcon):
    """`_rdf_one` only ever upserted: a version a new parser dates no more
    kept the old one's row."""
    from app import worker
    c, con = vcon
    validity.record(con, "eurlex:1", 1, _meta(20000101), day=20260101)
    row = con.execute("SELECT * FROM documents WHERE uid='eurlex:1'").fetchone()
    meta = {"title": "t", "authors": "", "lang": "it", "subjects": "", "bookshelves": "", "validity": None}
    worker._rdf_one(con, row, c, None, 0, meta)
    assert con.execute("SELECT COUNT(*) FROM validity WHERE uid='eurlex:1'").fetchone()[0] == 0


# ---- verify-and-reset (app/hold/derived_check.py) -------------------------------------

def test_verify_resets_only_what_differs_from_what_the_worker_would_derive_now(pair, corpus_dir, monkeypatch):  # noqa: F811
    """pubmed and wikidata (no predecessor held): each document's chunks as
    the fts stage would write them now against the text index's; only the
    one that differs is reset and loses its rows."""
    from app import worker
    from app.hold import derived_check
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch)
    # the document's fields and its index as the rdf and fts stages write them
    # today, then one document's index rows stale
    from app import parallel
    con = db.connect(coll)
    try:
        for r, (ok, meta) in zip(rows, parallel.compute("metadata", coll, rows)):
            assert ok
            con.execute("UPDATE documents SET title=?, authors=?, lang=?, subjects=?, bookshelves=? WHERE uid=?",
                        (meta["title"], meta["authors"], meta["lang"], meta["subjects"], meta["bookshelves"],
                         r["uid"]))
        con.commit()
    finally:
        con.close()
    for r in rows:
        fts.index_book(r["lang"] or "en", int(r["native_id"]), worker._load_chunks(r, coll), coll)
    stale = rows[0]
    fts.index_book(stale["lang"] or "en", int(stale["native_id"]),
                   [{"id": f"{stale['uid']}:0:1", "chapter": "Art. 1. — null", "text": "old"}], coll)
    dry = derived_check.verify(root, "re", log=quiet)
    assert (dry["checked"], dry["chunks"], dry["document"], dry["reset"]) == (len(rows), 1, 0, 0), dry
    assert dry["per_second"] > 0 and dry["whole_pass_hours"] is not None
    got = derived_check.verify(root, "re", apply=True, log=quiet)
    assert got["reset"] == 1 and _marks(coll, stale["uid"])[:2] == (0, 0)
    assert _held(coll, stale), "still searchable until its re-index replaces its rows (architecture, #767)"
    assert all(_held(coll, r) for r in rows[1:])
    assert all(_marks(coll, r["uid"])[:2] == (1, 1) for r in rows[1:])
    assert derived_check.verify(root, "re", log=quiet)["chunks"] == 0, "the reset one is no longer done"
    # its re-index replaces the stale rows (delete-then-insert in its group)
    fts.index_book(stale["lang"] or "en", int(stale["native_id"]), worker._load_chunks(stale, coll), coll)
    chunks = fts.book_chunks(fts.group_for(stale["lang"] or "en"), int(stale["native_id"]), coll)
    assert chunks and all(c["text"] != "old" for c in chunks)


def test_a_document_whose_text_index_group_changes_leaves_no_row_in_the_old_group(pair, corpus_dir, monkeypatch):  # noqa: F811
    """Its re-index writes the new group alone: the old group's rows go at
    once, and only for such a document."""
    from app import worker
    from app.hold import derived_check
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch)
    from app import parallel
    moved = rows[0]
    metas = parallel.compute("metadata", coll, rows)
    new_lang = metas[0][1]["lang"]
    old_lang = "de" if fts.group_for(new_lang) != "de" else "fr"
    con = db.connect(coll)
    try:
        for r, (ok, meta) in zip(rows, metas):
            lang = old_lang if r["uid"] == moved["uid"] else meta["lang"]   # the manifest's, from an old parse
            con.execute("UPDATE documents SET title=?, authors=?, lang=?, subjects=?, bookshelves=? WHERE uid=?",
                        (meta["title"], meta["authors"], lang, meta["subjects"], meta["bookshelves"], r["uid"]))
        con.commit()
        rows = con.execute("SELECT * FROM documents ORDER BY rowid").fetchall()
    finally:
        con.close()
    for r in rows:
        fts.index_book(r["lang"], int(r["native_id"]), worker._load_chunks(r, coll), coll)
    old_group = fts.group_for(old_lang)
    assert old_group != fts.group_for(new_lang) and fts.books_held(old_group, [int(moved["native_id"])], coll) > 0
    got = derived_check.verify(root, "re", apply=True, log=quiet)
    # its rows are not in the group its language names now: its chunks differ there
    assert (got["chunks"], got["regrouped"], got["reset"]) == (1, 1, 1), got
    assert fts.books_held(old_group, [int(moved["native_id"])], coll) == 0, "no row left in the old group"
    assert all(_held(coll, r) for r in rows[1:]), "the others untouched"


def test_a_stop_between_the_old_group_s_delete_and_the_marks_is_caught_by_the_next_pass(
        pair, corpus_dir, monkeypatch):  # noqa: F811
    """Architecture's review of #769: the old group's rows go BEFORE the marks
    commit. A failure between the two leaves the marks done and the stored
    chunks gone; the next pass finds them gone, resets the document, and no
    old-group row is left."""
    from app import parallel, worker
    from app.hold import derived_check
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch)
    moved = rows[0]
    metas = parallel.compute("metadata", coll, rows)
    new_lang = metas[0][1]["lang"]
    old_lang = "de" if fts.group_for(new_lang) != "de" else "fr"
    con = db.connect(coll)
    try:
        for r, (ok, meta) in zip(rows, metas):
            lang = old_lang if r["uid"] == moved["uid"] else meta["lang"]
            con.execute("UPDATE documents SET title=?, authors=?, lang=?, subjects=?, bookshelves=? WHERE uid=?",
                        (meta["title"], meta["authors"], lang, meta["subjects"], meta["bookshelves"], r["uid"]))
        con.commit()
        rows = con.execute("SELECT * FROM documents ORDER BY rowid").fetchall()
    finally:
        con.close()
    for r in rows:
        fts.index_book(r["lang"], int(r["native_id"]), worker._load_chunks(r, coll), coll)
    old_group = fts.group_for(old_lang)
    real = fts.delete_books

    def delete_then_stop(group, ids, collection=None):
        real(group, ids, collection)
        raise RuntimeError("stopped between the delete and the marks")
    monkeypatch.setattr(fts, "delete_books", delete_then_stop)
    with pytest.raises(RuntimeError, match="between the delete and the marks"):
        derived_check.verify(root, "re", apply=True, log=quiet)
    assert fts.books_held(old_group, [int(moved["native_id"])], coll) == 0
    assert _marks(coll, moved["uid"])[:2] == (1, 1), "the marks not committed"
    monkeypatch.setattr(fts, "delete_books", real)
    got = derived_check.verify(root, "re", apply=True, again=True, log=quiet)
    assert got["reset"] >= 1 and _marks(coll, moved["uid"])[:2] == (0, 0), "the next pass resets it"
    assert fts.books_held(old_group, [int(moved["native_id"])], coll) == 0, "no old-group row left"


def _now_fields(coll, rows, *, lang_of=None):
    """The rdf stage's fields written as it would now (the manifest's lang
    the parse's), `lang_of(row, lang)` overriding the language."""
    from app import parallel
    con = db.connect(coll)
    try:
        for r, (ok, meta) in zip(rows, parallel.compute("metadata", coll, rows)):
            lang = lang_of(r, meta["lang"]) if lang_of else meta["lang"]
            con.execute("UPDATE documents SET title=?, authors=?, lang=?, subjects=?, bookshelves=? WHERE uid=?",
                        (meta["title"], meta["authors"], lang, meta["subjects"], meta["bookshelves"], r["uid"]))
        con.commit()
        return con.execute("SELECT * FROM documents ORDER BY rowid").fetchall()
    finally:
        con.close()


def _other_lang(lang: str) -> str:
    return "de" if fts.group_for(lang) != "de" else "fr"


def test_rows_left_in_the_group_of_a_language_the_manifest_no_longer_states_are_found_and_go(
        pair, corpus_dir, monkeypatch):  # noqa: F811
    """München, pubmed 2026-11-19: the manifest's lang already pl, its rows in
    "en" (written when it was en). Read in the "other" group alone, verify
    found nothing, reset it, and the re-index left the "en" rows (~297k
    documents twice). Now its rows are found where they are and go."""
    from app import worker
    from app.hold import derived_check
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch)
    rows = _now_fields(coll, rows)
    moved = rows[0]
    old_lang = _other_lang(moved["lang"])
    old, new = fts.group_for(old_lang), fts.group_for(moved["lang"])
    nid = int(moved["native_id"])
    fts.delete_books(new, [nid], coll)
    for r in rows:     # the fts stage wrote it when its language was another
        fts.index_book(old_lang if r["uid"] == moved["uid"] else r["lang"], int(r["native_id"]),
                       worker._load_chunks(r, coll), coll)
    assert fts.books_held(old, [nid], coll) > 0 and fts.books_held(new, [nid], coll) == 0
    got = derived_check.verify(root, "re", apply=True, log=quiet)
    assert (got["chunks"], got["regrouped"], got["reset"], got["strays"]) == (1, 1, 1, 0), got
    assert fts.books_held(old, [nid], coll) == 0, "its old rows gone"
    # the fts stage's turn: the new group alone holds it
    fts.index_book(moved["lang"], nid, worker._load_chunks(moved, coll), coll)
    assert fts.books_held(new, [nid], coll) > 0 and fts.books_held(old, [nid], coll) == 0


def test_a_document_held_twice_loses_the_old_group_s_rows_and_keeps_its_marks(pair, corpus_dir, monkeypatch):  # noqa: F811
    """The ~297k already re-indexed: right in their own group, and in "en"
    too. The "en" rows go; nothing is derived again."""
    from app import worker
    from app.hold import derived_check
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch)
    rows = _now_fields(coll, rows)
    for r in rows:
        fts.index_book(r["lang"], int(r["native_id"]), worker._load_chunks(r, coll), coll)
    twice = rows[0]
    nid, old_lang = int(twice["native_id"]), _other_lang(twice["lang"])
    old = fts.group_for(old_lang)
    idx = fts.get_index(old, coll)       # its old copy, written beside the new one
    writer, lock = fts._writer((str(fts.base_of(coll)), old), idx)
    with lock:
        fts._add_one(writer, fts.pystem_algorithm(old), old_lang, nid, worker._load_chunks(twice, coll))
        writer.commit()
    assert fts.books_held(old, [nid], coll) > 0 and fts.books_held(fts.group_for(twice["lang"]), [nid], coll) > 0
    dry = derived_check.verify(root, "re", log=quiet)
    assert (dry["chunks"], dry["strays"], dry["regrouped"]) == (0, 1, 1), dry
    got = derived_check.verify(root, "re", apply=True, log=quiet)
    assert (got["reset"], got["strays"]) == (0, 1), got
    assert fts.books_held(old, [nid], coll) == 0 and fts.books_held(fts.group_for(twice["lang"]), [nid], coll) > 0
    assert _marks(coll, twice["uid"])[:2] == (1, 1), "nothing to derive again"


def test_a_writer_waits_for_the_worker_s_transaction_and_a_stop_ends_the_wait(monkeypatch):
    import sqlite3
    import threading
    from app.hold import derived_check
    monkeypatch.setattr(derived_check.time, "sleep", lambda s: None)

    class Busy:
        def __init__(self, fails):
            self.fails, self.calls = fails, 0

        def execute(self, sql):
            self.calls += 1
            if self.calls <= self.fails:
                raise sqlite3.OperationalError("database is locked")
    con = Busy(3)
    derived_check._begin(con, None)
    assert con.calls == 4, "waited, then began"
    stop = threading.Event()
    stop.set()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        derived_check._begin(Busy(1), stop)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        derived_check._begin(type("E", (), {"execute": lambda self, q: (_ for _ in ()).throw(
            sqlite3.OperationalError("no such table"))})(), None)


def test_a_stop_while_the_profile_pauses_the_pool_ends_the_pass_as_a_stop(pair, corpus_dir, monkeypatch):  # noqa: F811
    """München: a SIGTERM ended the pass with exit 1 — `compute_pages` raises
    `Interrupted` when its stop is set while the node profile pauses it. The
    pass stops at its page boundary; its cursor is not marked done."""
    import json
    import threading
    from app import parallel
    from app.hold import derived_check
    root, coll, uids, ids, rows, out = _reconciled(pair, corpus_dir, monkeypatch)

    def interrupted(kind, collection, pages, *, stop=None, say=None):
        raise parallel.Interrupted()
        yield  # pragma: no cover
    monkeypatch.setattr(parallel, "compute_pages", interrupted)
    stop = threading.Event()
    stop.set()
    got = derived_check.verify(root, "re", apply=True, stop=stop, log=quiet)
    assert got["checked"] == 0
    con = db.connect(coll)
    try:
        held = json.loads(db.get_control(con, derived_check.CURSOR_KEY) or "{}")
    finally:
        con.close()
    assert not held.get("done"), "stopped, not done"


# ---- the documents a list names (2026-11-20, EUR-Lex5's included documents) ----------

def _set(coll, sql, *args):
    con = db.connect(coll)
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def test_a_listed_document_is_derived_again_its_vectors_the_records(pair, corpus_dir, monkeypatch):  # noqa: F811
    root, coll, uids, _ids, _rows, _out = _reconciled(pair, corpus_dir, monkeypatch)
    _set(coll, "UPDATE documents SET embed_done=1")
    listed = [uids[0], "", "gutenberg:no-such-uid"]
    dry = carry.rederive_listed(root, "re", listed, log=quiet)
    assert (dry["listed"], dry["documents"], dry["unmatched"], dry["would_reset"]) == (2, 1, 1, 1)
    assert "records" in dry["vectors"] and _marks(coll, uids[0]) == (1, 1, 1), "a dry run"
    got = carry.rederive_listed(root, "re", listed, apply=True, log=quiet)
    assert got["reset"] == 1 and _marks(coll, uids[0]) == (0, 0, 1), "its vectors are the records': kept"
    assert all(_marks(coll, u) == (1, 1, 1) for u in uids[1:]), "nothing it does not list"


def test_without_records_a_listed_embedded_document_is_embedded_again(pair, corpus_dir, monkeypatch):  # noqa: F811
    """EUR-Lex5 holds no records: its vectors are the worker's cut of its own
    parse, and a parse that now reads an included annex cuts other chunks.
    The check lists unit PATHS from its own mount; they match under the
    slice folder."""
    root, coll, uids, _ids, _rows, _out = _reconciled(pair, corpus_dir, monkeypatch)
    monkeypatch.setattr(carry, "latest_snapshot", lambda root, name: None)
    _set(coll, "UPDATE documents SET embed_done=1")
    _set(coll, "UPDATE documents SET path='/data/collections/re/origin/lang=it/2021/32021R1971' WHERE uid=?", uids[0])
    _set(coll, "UPDATE documents SET path='/data/collections/re/origin/history/lang=de/2015/32015D1735', "
               "embed_done=0 WHERE uid=?", uids[1])
    listed = ["/corpus/lang=it/2021/32021R1971", "/corpus/history/lang=de/2015/32015D1735",
              "/corpus/lang=de/2015/32015D1735"]          # the base slice's: not held here
    got = carry.rederive_listed(root, "re", listed, apply=True, log=quiet)
    assert (got["documents"], got["unmatched"], got["embedded"], got["reset"]) == (2, 1, 1, 2)
    assert "embedded again" in got["vectors"]
    assert _marks(coll, uids[0]) == (0, 0, 0), "embedded: embedded again from its new chunks"
    assert _marks(coll, uids[1]) == (0, 0, 0), "not embedded: left not embedded"
    assert all(_marks(coll, u) == (1, 1, 1) for u in uids[2:])


def test_dev_rederive_ids_takes_no_whole_collection_flags(tmp_path, capsys):
    from app import louise
    ids = tmp_path / "ids.txt"
    ids.write_text("eurlex:1\n")
    for extra in (["--verify"], ["--again"], ["--every", "2"], ["--limit", "5"]):
        assert louise.main(["dev", "rederive", "--root", str(tmp_path), "x", "--ids", str(ids), *extra]) == 2
    assert "takes no --verify" in capsys.readouterr().err
