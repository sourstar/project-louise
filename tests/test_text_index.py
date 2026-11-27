"""Sisters share one text index (operator, 2026-11-10: choice A;
app/text_index.py, docs/notes/sister-shared-index.md): written by one sister,
read by the others, who copy its marks and never write it."""
import json
import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import collections as collections_mod, config, db, discovery, fts, text_index, worker

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "gutenberg-mini" / "corpus"


@pytest.fixture(autouse=True)
def _fresh():
    text_index.invalidate()
    text_index.reset_queues()
    yield
    text_index.close_writer_connections()
    text_index.invalidate()
    text_index.reset_queues()
    fts.close_all()


# ---- identity ----------------------------------------------------------------

def _codex(**kw):
    base = dict(space=object(), plugin="gutenberg", origin={"url": "o"},
                toolchain=SimpleNamespace(platform_image="img"), parser={"version": 1},
                cleaning_version=1, slicing={"order": "bytes"}, layers=({"chunker": "c", "v": 1},),
                update={"cadence": "monthly"}, profile=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _held(name, codex, slices="all"):
    return SimpleNamespace(name=name, codex=codex, plugin=codex.plugin, slices=slices, profile=None)


def test_the_identity_is_equal_for_sisters_and_differs_by_each_input():
    a = text_index.identity(_held("a", _codex()))
    assert a == text_index.identity(_held("b", _codex(space=object())))   # another encoder: same index
    assert a != text_index.identity(_held("b", _codex(layers=({"chunker": "c", "v": 2},))))
    assert a != text_index.identity(_held("b", _codex(update={"cadence": "weekly"})))
    assert a != text_index.identity(_held("b", _codex(), slices=["lang=it"]))
    assert a != text_index.identity(_held("b", _codex(parser={"version": 2})))


def test_a_sister_written_at_a_newer_codex_version_has_her_older_sister_s_identity():
    """gutenberg-nemo (2026-11-27): a sister written at version 11 of an older
    sister states a text input on her layers (version 5) and a JSONL
    container on her slicing (version 9); both are what the older Codex
    already meant, the same chunks and the same parts, so the same index."""
    old = _codex(layers=({"chunker": "c", "v": 1, "input": None},), slicing={"order": "bytes"})
    new = _codex(layers=({"chunker": "c", "v": 1, "input": {"form": "text"}},),
                 slicing={"order": "bytes", "container": "jsonl"})
    a = text_index.identity(_held("a", old))
    assert a == text_index.identity(_held("b", new))
    assert a == text_index.identity(_held("b", _codex(layers=({"chunker": "c", "v": 1, "input": None},),
                                                         slicing={"order": "bytes"}))), "unchanged when absent"
    assert a != text_index.identity(_held("b", _codex(layers=({"chunker": "c", "v": 1, "input": {"form": "pair"}},),
                                                         slicing={"order": "bytes"}))), "a pair input is another cut"
    assert a != text_index.identity(_held("b", _codex(layers=({"chunker": "c", "v": 1, "input": None},),
                                                         slicing={"order": "bytes", "container": "parquet"})))
    assert text_index.identity(_held("a", _codex(slicing=None))) == \
        text_index.identity(_held("b", _codex(slicing={"container": "jsonl"})))


def test_no_identity_without_a_codex_or_for_annotations():
    assert text_index.identity(SimpleNamespace(name="x", codex=None)) is None
    assert text_index.identity(_held("x", _codex(space=None))) is None


# ---- a root of held collections ------------------------------------------------

def _collection(root: Path, name: str, corpus: Path):
    (root / f"{name}.toml").write_text("", encoding="utf-8")     # held
    return collections_mod.Collection(
        name=name, plugin="gutenberg", profile=None, embed_mode_default=None,
        corpus_dir=corpus, manifest_path=root / name / "state" / "manifest.sqlite",
        fts_dir=root / name / "index", lance_dir=root / name / "lance")


def _discovered(tmp_path, names=("alpha", "beta")):
    root = tmp_path / "collections"
    root.mkdir()
    corpus = tmp_path / "corpus"
    shutil.copytree(FIXTURE_CORPUS, corpus)
    out = []
    for n in names:
        c = _collection(root, n, corpus)
        con = db.connect(c)
        discovery.sync_collection(con, c)
        con.execute("UPDATE documents SET rdf_done=1")
        con.commit()
        out.append((c, con))
    return root, out


def _share(root, names, writer):
    rel = f"{text_index.SHARED}/abc"
    (root / rel).mkdir(parents=True, exist_ok=True)
    for n in names:
        text_index.write_placement(root, n, rel, "abc")
    (root / rel / text_index.WRITER).write_text(json.dumps({"writer": writer}), encoding="utf-8")
    text_index.invalidate()
    return rel


def test_the_base_is_the_own_directory_until_a_placement_names_the_shared_one(tmp_path):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    assert text_index.base(a.fts_dir) == a.fts_dir
    rel = _share(root, ["alpha", "beta"], "alpha")
    assert text_index.base(a.fts_dir) == root / rel == text_index.base(b.fts_dir)
    assert fts.base_of(b) == root / rel


def test_the_writer_is_sticky_and_passes_on_when_it_is_no_longer_held(tmp_path):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _share(root, ["alpha", "beta"], "beta")
    assert text_index.role(a).reader and text_index.role(a).writer == "beta"
    assert not text_index.role(b).reader
    (root / "beta.toml").unlink()                 # forgotten: no longer held
    text_index.invalidate()
    assert not text_index.role(a).reader          # the next by name writes now
    assert json.loads((root / text_index.SHARED / "abc" / text_index.WRITER).read_text())["writer"] == "alpha"


# ---- the worker: the writer indexes, the reader copies ---------------------------

def _pending(con):
    return con.execute("SELECT * FROM documents WHERE fts_done=0 ORDER BY native_id LIMIT 1").fetchone()


def test_the_writer_indexes_once_and_the_reader_copies_its_marks_and_writes_nothing(tmp_path, monkeypatch):
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    _share(root, ["alpha", "beta"], "alpha")
    writes = []
    real = fts.index_books
    monkeypatch.setattr(fts, "index_books",
                        lambda entries, collection=None: writes.append(collection.name)
                        or real(list(entries), collection))
    # the reader selects no index work at all while its writer has none indexed
    assert worker._next_book(bcon, db.get_scope(bcon), embed_allowed=False, collection=b)[0] != "fts"
    assert worker._fts_copy_round(bcon, b) is False          # deferred, not an error
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE error IS NOT NULL").fetchone()[0] == 0
    # the writer indexes into the shared directory, once
    assert worker._execute_step(acon, a, "fts", _pending(acon)) is True
    assert writes == ["alpha"]
    assert fts.existing_groups(a) and not text_index.group_dirs(a.fts_dir)
    # its commit was pushed to the reader, which copies
    assert worker._fts_copy_round(bcon, b) is True
    mine = {r["uid"]: tuple(r) for r in bcon.execute(
        "SELECT uid, fts_done, fts_version, entity_version, chunks, tokens FROM documents")}
    theirs = {r["uid"]: tuple(r) for r in acon.execute(
        "SELECT uid, fts_done, fts_version, entity_version, chunks, tokens FROM documents")}
    assert mine == theirs and all(v[1] == 1 for v in mine.values())
    # the reader searches the writer's index, and never writes it
    assert fts.existing_groups(b) == fts.existing_groups(a)
    assert writes == ["alpha"]
    with pytest.raises(fts.ReaderWrite):
        fts.index_books([("en", 1, [])], b)


def test_a_reader_walks_its_pending_rows_once_without_any_push(tmp_path):
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    _share(root, ["alpha", "beta"], "alpha")
    worker._execute_step(acon, a, "fts", _pending(acon))
    text_index.reset_queues()                     # a restart: the pushes are lost
    text_index.walk_again(b)
    assert worker._fts_copy_round(bcon, b) is True
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=0").fetchone()[0] == 0
    assert worker._fts_copy_round(bcon, b) is False   # walked: nothing more, no spin


def test_a_different_record_version_is_never_copied(tmp_path):
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    _share(root, ["alpha", "beta"], "alpha")
    worker._execute_step(acon, a, "fts", _pending(acon))
    uid = bcon.execute("SELECT uid FROM documents ORDER BY uid LIMIT 1").fetchone()[0]
    bcon.execute("UPDATE documents SET mtime=mtime+1 WHERE uid=?", (uid,))   # the reader's is another version
    bcon.commit()
    text_index.walk_again(b)
    worker._fts_copy_round(bcon, b)
    assert bcon.execute("SELECT fts_done FROM documents WHERE uid=?", (uid,)).fetchone()[0] == 0
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 2


def test_same_record_prefers_the_content_hash_then_the_origin_hash_then_the_signature():
    row = dict(content_hash=None, txt_hash=None, path="p", mtime=1, size=2)
    assert text_index.same_record(row, dict(row))
    assert not text_index.same_record(row, {**row, "size": 3})
    assert not text_index.same_record({**row, "path": None}, {**row, "path": None})
    assert text_index.same_record({**row, "txt_hash": "t"}, {**row, "txt_hash": "t", "mtime": 9})
    assert not text_index.same_record({**row, "content_hash": "c"}, {**row, "content_hash": "d"})


def test_a_walk_takes_a_bounded_batch_and_goes_on_from_its_last_row(tmp_path, monkeypatch):
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    _share(root, ["alpha", "beta"], "alpha")
    worker._execute_step(acon, a, "fts", _pending(acon))
    monkeypatch.setattr(text_index, "COPY_BATCH", 1)
    text_index.reset_queues()
    text_index.walk_again(b)
    copied = 0
    for _ in range(10):
        if worker._fts_copy_round(bcon, b):
            copied += 1
    assert copied == 3, "one document a round, the walk going on from the last"
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=0").fetchone()[0] == 0


def test_the_queue_is_bounded_and_falls_back_to_a_walk(monkeypatch):
    monkeypatch.setattr(text_index, "QUEUE_MAX", 3)
    q = text_index.queue_of("/x/manifest.sqlite")
    q.walk_due = False
    q.push(["a", "b", "a", "c"])
    assert list(q.uids) == ["a", "b", "c"] and not q.walk_due
    q.push(["d"])
    assert not q.uids and q.walk_due and q.cursor == 0


# ---- boot: settle -------------------------------------------------------------

def _fake_group(index_dir: Path, group: str, payload: bytes = b"x") -> None:
    g = index_dir / group
    g.mkdir(parents=True, exist_ok=True)
    (g / "meta.json").write_text("{}", encoding="utf-8")
    (g / "seg.store").write_bytes(payload)
    (g / ".tantivy-writer.lock").write_bytes(b"")


def _with_identity(monkeypatch, idents: dict):
    monkeypatch.setattr(text_index, "identity", lambda c: idents.get(c.name))


def test_settle_forms_a_group_from_the_fuller_index_and_frees_the_other(tmp_path, monkeypatch):
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    acon.execute("UPDATE documents SET fts_done=1, chunks=3")          # alpha indexed all
    acon.commit()
    bcon.execute("UPDATE documents SET fts_done=1, chunks=3 WHERE native_id='10001'")
    bcon.execute("UPDATE documents SET fts_done=1, chunks=9 WHERE native_id='10002'")  # differs
    bcon.commit()
    _fake_group(a.fts_dir, "en", b"alpha")
    _fake_group(b.fts_dir, "en", b"beta")
    (a.fts_dir / "_catalog").mkdir()
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id1"})
    text_index.settle(root, [a, b], log=lambda m: None)
    rel = text_index.placement(root, "alpha")["dir"]
    assert rel != f"{text_index.SHARED}/id1" and text_index.folder_identity(root, rel) == "id1", \
        "a unique folder, the identity recorded inside it"
    assert (root / rel / "en" / "seg.store").read_bytes() == b"alpha"
    assert not text_index.group_dirs(a.fts_dir) and not text_index.group_dirs(b.fts_dir)
    assert (a.fts_dir / "_catalog").is_dir(), "the catalogue stays the collection's own"
    assert text_index.placement(root, "alpha")["dir"] == rel == text_index.placement(root, "beta")["dir"]
    assert text_index.role(b).reader and text_index.role(b).writer == "alpha"
    # beta's marks kept where equal to alpha's, reset where they differed
    got = dict(bcon.execute("SELECT native_id, fts_done FROM documents").fetchall())
    assert got == {"10001": 1, "10002": 0, "10003": 0}
    again = text_index.settle(root, [a, b], log=lambda m: None)        # idempotent
    assert again["freed"] == 0 and (root / rel / "en" / "seg.store").read_bytes() == b"alpha"


def test_the_join_compares_in_the_pools_processes_with_the_same_result(tmp_path, monkeypatch):
    """CLAUDE.md: multi-threading is a must — and threads gave nothing here
    (the per-row work holds the GIL), so the windows go to processes
    (`parallel.each_part`); the resets are the same as in one process."""
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    acon.execute("UPDATE documents SET fts_done=1, chunks=3")
    acon.commit()
    bcon.execute("UPDATE documents SET fts_done=1, chunks=3")
    bcon.execute("UPDATE documents SET chunks=9 WHERE native_id='10002'")
    bcon.commit()
    acon.close(), bcon.close()
    from app import config
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    got = text_index.reset_differing(b.manifest_path, a.manifest_path, window=1, log=lambda m: None)
    assert got == 1
    con = sqlite3.connect(b.manifest_path)
    assert dict(con.execute("SELECT native_id, fts_done FROM documents").fetchall()) == \
        {"10001": 1, "10002": 0, "10003": 1}
    con.close()


def test_a_member_whose_identity_differs_from_its_writer_s_leaves_to_an_empty_folder(tmp_path, monkeypatch):
    """A member whose identity now differs from its folder's WRITER's LEAVES,
    to an EMPTY folder of its own, EVERY mark of it reset, said in one line
    — nothing copied, nothing pruned (the coordinator's default, round 5);
    the writer stays. Nothing is copied back to `<name>/index`."""
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    bcon.execute("UPDATE documents SET fts_done=1")                 # its reader: alpha's marks
    bcon.commit()
    rel = _share(root, ["alpha", "beta"], "alpha")
    _fake_group(root / rel, "en", b"shared")
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id2"})       # their slices differ now
    said = []
    out = text_index.settle(root, [a, b], log=said.append)
    assert not out["skipped"] and out["left"] == ["beta"], out
    assert text_index.placement(root, "alpha")["dir"] == rel, "the writer stays"
    assert (root / rel / "en" / "seg.store").read_bytes() == b"shared"
    mine = text_index.placement(root, "beta")["dir"]
    assert mine != rel and not text_index.group_dirs(root / mine), "empty: nothing copied"
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 0, "every mark reset"
    assert acon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 3, "the writer's kept"
    assert len([m for m in said if "beta leaves" in m]) == 1, "said in one line"
    assert not text_index.role(b).reader and not text_index.role(a).reader, "each writes its own"
    for c in (a, b):
        assert not text_index.group_dirs(c.fts_dir), "nothing copied back"
    again = text_index.settle(root, [a, b], log=lambda m: None)
    assert text_index.placement(root, "beta")["dir"] == mine and not again["left"], "idempotent"


def _row(con, native):
    return con.execute("SELECT * FROM documents WHERE native_id=?", (native,)).fetchone()


def test_a_leaving_member_never_searches_a_slice_it_does_not_hold_and_indexes_its_own(tmp_path, monkeypatch):
    """beta read alpha's index; now beta holds 10001 and 10003, alpha 10001
    and 10002. Beta leaves to an empty folder, its marks reset: her searches
    never show 10002, and she indexes 10001 and her own 10003 herself —
    never in alpha's."""
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    rel = _share(root, ["alpha", "beta"], "alpha")
    acon.execute("UPDATE documents SET in_scope=0 WHERE native_id='10003'")       # not alpha's slice
    bcon.execute("UPDATE documents SET in_scope=0 WHERE native_id='10002'")       # not beta's
    acon.commit(), bcon.commit()
    while (row := acon.execute("SELECT * FROM documents WHERE fts_done=0 AND in_scope=1 "
                               "ORDER BY native_id LIMIT 1").fetchone()) is not None:
        assert worker._execute_step(acon, a, "fts", row) is True
    worker._fts_copy_round(bcon, b)
    assert _row(bcon, "10001")["fts_done"] == 1 and _row(bcon, "10003")["fts_done"] == 0
    g1, g2 = fts.group_for(_row(acon, "10001")["lang"]), fts.group_for(_row(acon, "10002")["lang"])
    g3 = fts.group_for(_row(bcon, "10003")["lang"])
    words = fts.book_chunks(g2, 10002, a)[0]["text"].split()[:3]
    assert fts.books_held(g2, [10002], b) > 0, "before: she reads alpha's"
    fts.close_all()
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id2"})
    out = text_index.settle(root, [a, b], log=lambda m: None)
    assert not out["skipped"] and out["left"] == ["beta"], out
    assert text_index.placement(root, "beta")["dir"] != rel and text_index.placement(root, "alpha")["dir"] == rel
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 0, "reset"
    assert fts.books_held(g2, [10002], b) == 0 and fts.books_held(g2, [10002], a) > 0
    assert not [h for h in fts.search(g2, " ".join(words), collection=b) if h["book_id"] == 10002], \
        "her searches never show a slice she does not hold"
    assert text_index.writes(b)
    for native in ("10001", "10003"):
        assert worker._execute_step(bcon, b, "fts", _row(bcon, native)) is True
    assert fts.books_held(g1, [10001], b) > 0 and fts.books_held(g3, [10003], b) > 0
    assert fts.books_held(g3, [10003], a) == 0 and fts.books_held(g2, [10002], b) == 0


def test_a_placed_member_s_own_copy_is_removed(tmp_path, monkeypatch):
    """Finding 2: a member placed already whose `<name>/index` still holds
    language groups (never read: its index is the folder's) loses them."""
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    rel = _share(root, ["alpha", "beta"], "alpha")
    _fake_group(root / rel, "en", b"shared")
    _fake_group(b.fts_dir, "en", b"leaked")
    (b.fts_dir / "_catalog").mkdir()
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id1"})
    text_index.settle(root, [a, b], log=lambda m: None)
    assert text_index.placement(root, "beta")["dir"] == rel
    assert not text_index.group_dirs(b.fts_dir) and (b.fts_dir / "_catalog").is_dir()


def test_a_placement_whose_directory_is_gone_is_dropped_and_its_marks_reset(tmp_path, monkeypatch):
    """Finding C: never an abort of the boot — dropped, reset, said."""
    import shutil
    root, [(a, acon), (b, _)] = _discovered(tmp_path)
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    rel = _share(root, ["alpha"], "alpha")
    shutil.rmtree(root / rel)
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id2"})
    said = []
    out = text_index.settle(root, [a, b], log=said.append)
    assert text_index.placement(root, "alpha") is None
    assert out["dropped"] == [{"collection": "alpha", "dir": rel, "reset": 3}]
    assert acon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 0
    assert any("which is gone: dropped" in m for m in said)


def test_the_writer_s_identity_keeps_its_folder_a_newcomer_joins_and_the_other_leaves(tmp_path, monkeypatch):
    """alpha, the writer, keeps its folder with gamma, who joins (her marks
    reconciled with alpha's); beta, whose identity differs, leaves to an
    empty folder, its marks reset. Nothing is copied: no room is needed."""
    root, [(a, acon), (b, bcon), (c, _)] = _discovered(tmp_path, names=("alpha", "beta", "gamma"))
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    bcon.execute("UPDATE documents SET fts_done=1")
    bcon.commit()
    rel = _share(root, ["alpha", "beta"], "alpha")
    _fake_group(root / rel, "en", b"x" * 5000)
    _with_identity(monkeypatch, {"alpha": "id2", "beta": "abc", "gamma": "id2"})
    out = text_index.settle(root, [a, b, c], log=lambda m: None)
    assert not out["skipped"], out["skipped"]
    assert text_index.placement(root, "alpha")["dir"] == rel == text_index.placement(root, "gamma")["dir"]
    assert text_index.folder_identity(root, rel) == "id2"
    mine = root / text_index.placement(root, "beta")["dir"]
    assert mine != root / rel and not text_index.group_dirs(mine), "empty: nothing copied"
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 0, "reset"
    assert (root / rel / "en" / "seg.store").read_bytes() == b"x" * 5000


def test_a_writer_whose_content_changed_leaves_empty_and_the_next_by_name_writes(tmp_path, monkeypatch):
    """Round 5, item 2: a writer whose content key changed leaves to an
    empty folder, its marks reset; the next member by name writes, as
    `writer()` falls back."""
    root, [(a, acon), (b, bcon), (c, _)] = _discovered(tmp_path, names=("alpha", "beta", "gamma"))
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    bcon.execute("UPDATE documents SET fts_done=1")                 # its reader: alpha's marks
    bcon.commit()
    rel = _share(root, ["alpha", "beta"], "alpha")
    text_index.write_placement(root, "alpha", rel, "abc", content="old-content")
    _fake_group(root / rel, "en", b"x" * 50)
    _with_identity(monkeypatch, {"alpha": "id2", "beta": "abc", "gamma": "id2"})
    monkeypatch.setattr(text_index, "content_key", lambda col: "new-content" if col.name == "alpha" else None)
    text_index.settle(root, [a, b, c], log=lambda m: None)
    new = root / text_index.placement(root, "alpha")["dir"]
    assert new != root / rel and new.is_dir() and not text_index.group_dirs(new), "started empty: nothing copied"
    assert acon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 0, "reset"
    assert text_index.group_dirs(root / rel), "the old group keeps its index for beta"
    assert text_index.placement(root, "beta")["dir"] == rel and not text_index.role(b).reader, \
        "beta, whose index it still describes, writes it now"


def test_a_folder_named_by_an_identity_is_never_adopted_nor_reused(tmp_path, monkeypatch):
    """A folder nobody is placed in, even one named by the identity, is
    never adopted: the group gets a NEW folder (a unique id), the old one
    is left as it is and said (orphans)."""
    root, [(a, acon), (b, _)] = _discovered(tmp_path)
    stale = root / text_index.SHARED / "id1"
    _fake_group(stale, "en", b"stale")
    _fake_group(a.fts_dir, "en", b"alpha")
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id1"})
    out = text_index.settle(root, [a, b], log=lambda m: None)
    rel = text_index.placement(root, "alpha")["dir"]
    assert root / rel != stale and (root / rel / "en" / "seg.store").read_bytes() == b"alpha"
    assert (stale / "en" / "seg.store").read_bytes() == b"stale"
    assert any(o["dir"] == f"{text_index.SHARED}/id1" for o in out["orphans"])


def test_a_lone_member_s_folder_is_never_given_to_another_group(tmp_path, monkeypatch):
    """Finding 1 of the third read: N left alone in the folder of identity
    X; M and M2 get identity X. They get a NEW folder; N keeps reading its
    own, which settle never sets aside."""
    root, [(n, _), (m, mcon), (m2, _)] = _discovered(tmp_path, names=("n", "m", "m2"))
    rel = f"{text_index.SHARED}/X"
    (root / rel).mkdir(parents=True)
    text_index.write_placement(root, "n", rel, "X")
    _fake_group(root / rel, "en", b"n's")
    _fake_group(m.fts_dir, "en", b"m's")
    mcon.execute("UPDATE documents SET fts_done=1")
    mcon.commit()
    _with_identity(monkeypatch, {"n": "Y", "m": "X", "m2": "X"})
    text_index.settle(root, [n, m, m2], log=lambda msg: None)
    assert text_index.placement(root, "n")["dir"] == rel and (root / rel / "en" / "seg.store").read_bytes() == b"n's"
    got = text_index.placement(root, "m")["dir"]
    assert got != rel and text_index.placement(root, "m2")["dir"] == got
    assert (root / got / "en" / "seg.store").read_bytes() == b"m's"


def test_a_lone_collection_keeps_its_own_index_untouched(tmp_path, monkeypatch):
    root, [(a, _)] = _discovered(tmp_path, names=("alpha",))
    _fake_group(a.fts_dir, "en")
    _with_identity(monkeypatch, {"alpha": "id1"})
    out = text_index.settle(root, [a], log=lambda m: None)
    assert out["groups"] == [] and text_index.placement(root, "alpha") is None
    assert not (root / text_index.SHARED).exists()


# ---- forget, carry, show ------------------------------------------------------

def test_forget_keeps_a_shared_index_while_a_member_is_held_and_removes_it_with_the_last(tmp_path):
    from app.hold import forget as forget_mod
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    acon.close(), bcon.close()
    rel = _share(root, ["alpha", "beta"], "alpha")
    _fake_group(root / rel, "en")
    out = forget_mod.forget(root, "alpha")
    assert out["text_index"]["kept"] == ["beta"] and (root / rel).is_dir()
    assert not text_index.role(b).reader, "beta writes it now"
    out = forget_mod.forget(root, "beta")
    assert out["text_index"]["removed"] and not (root / rel).exists()


def test_a_sister_carry_copies_the_catalogue_and_leaves_the_language_groups_where_they_lie(tmp_path):
    """Never a hard link: the groups are shared (`form_at_carry`), or, where
    the identities differ, copied — each file one name."""
    from app.hold import carry
    src, dst = tmp_path / "a" / "index", tmp_path / "b" / "index"
    _fake_group(src, "en", b"text")
    (src / "_catalog").mkdir(parents=True)
    (src / "_catalog" / "c.store").write_bytes(b"cat")
    assert "read where they lie" in carry._sister_index(src, dst, groups=False)
    assert not (dst / "en").exists() and (dst / "_catalog" / "c.store").read_bytes() == b"cat"
    assert os.stat(src / "_catalog" / "c.store").st_ino != os.stat(dst / "_catalog" / "c.store").st_ino
    assert carry._sister_index(src, dst, groups=False) == "present"
    other = tmp_path / "c" / "index"
    assert carry._sister_index(src, other, groups=True).startswith("copied")
    assert (other / "en" / "seg.store").read_bytes() == b"text"
    assert os.stat(src / "en" / "seg.store").st_ino != os.stat(other / "en" / "seg.store").st_ino
    assert all(f.stat().st_nlink == 1 for f in (tmp_path).rglob("*") if f.is_file())


# ---- a sister's group formed at her carry, by rename ------------------------

def test_a_sister_whose_sister_is_alone_forms_the_group_by_rename(tmp_path, monkeypatch):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"alpha")
    (a.fts_dir / "_catalog").mkdir()
    ino = os.stat(a.fts_dir / "en" / "seg.store").st_ino
    assert text_index.form_at_carry(root, "alpha", "beta", "id1", "id2") is None, "other identities"
    rel = text_index.form_at_carry(root, "alpha", "beta", "id1", "id1")
    assert text_index.is_shared(rel) and text_index.folder_identity(root, rel) == "id1"
    for c in (a, b):
        assert text_index.placement(root, c.name)["dir"] == rel and text_index.base(c.fts_dir) == root / rel
    assert os.stat(root / rel / "en" / "seg.store").st_ino == ino, "renamed: no byte copied"
    assert (a.fts_dir / "_catalog").is_dir(), "her catalogue stays her own"
    assert text_index.role(b).reader and text_index.role(b).writer == "alpha"
    assert text_index.form_at_carry(root, "alpha", "beta", "id1", "id1") == rel, "repeatable"


def test_forming_the_group_is_refused_while_another_process_holds_the_writer(tmp_path):
    import fcntl
    import time
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"alpha")
    fd = os.open(a.fts_dir / "en" / ".tantivy-writer.lock", os.O_RDWR)
    pid = os.fork()
    if pid == 0:
        fcntl.flock(fd, fcntl.LOCK_EX)
        time.sleep(30)
        os._exit(0)
    try:
        for _ in range(100):
            if text_index.writer_held(a.fts_dir):
                break
            time.sleep(0.05)
        with pytest.raises(text_index.WriterHeld, match="louise node stop"):
            text_index.form_at_carry(root, "alpha", "beta", "id1", "id1")
        assert text_index.placement(root, "alpha") is None and (a.fts_dir / "en" / "seg.store").is_file()
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        os.close(fd)


def test_the_index_digest_is_the_index_as_read_shared_or_not(tmp_path):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"same")
    (a.fts_dir / "_catalog").mkdir()
    (a.fts_dir / "_catalog" / "c").write_bytes(b"cat")
    rel = f"{text_index.SHARED}/abc"
    _fake_group(root / rel, "en", b"same")
    (b.fts_dir / "_catalog").mkdir(parents=True)
    (b.fts_dir / "_catalog" / "c").write_bytes(b"cat")
    text_index.write_placement(root, "beta", rel, "abc")
    assert text_index.index_digest(root, "alpha") == text_index.index_digest(root, "beta")


def test_describe_says_who_writes(tmp_path):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    assert text_index.describe(root, "alpha")["shared"] is None
    _share(root, ["alpha", "beta"], "alpha")
    d = text_index.describe(root, "beta")
    assert d["writer"] == "alpha" and "written by alpha" in d["says"]


# ---- the queries a reader adds (CLAUDE.md: plan-checked) ---------------------------

def test_the_reader_queries_use_the_integer_key_or_the_uid_index(tmp_path):
    root, [(a, acon), (_, _)] = _discovered(tmp_path)
    plans = {
        "walk": (text_index._R_WALK, (0, 10, 1, 1, 5)),
        "row": (text_index._R_ROW, ("x",)),
        "writer": (text_index._W_ROW, ("x",)),
        "copy": (text_index._COPY, (1, 1, 1, 1, 0, 1, "x", None, None, None, None, None)),
    }
    for name, (sql, args) in plans.items():
        plan = " ".join(r[-1] for r in acon.execute("EXPLAIN QUERY PLAN " + sql, args))
        assert "SCAN" not in plan and "TEMP B-TREE" not in plan and "LIST SUBQUERY" not in plan, (name, plan)


# ---- the third read of 74c95825: findings 2, 3, 4 ---------------------------

def test_a_sister_joining_a_folder_others_use_is_reconciled_and_its_writer_kept(tmp_path, monkeypatch):
    """Finding 2: her marks against the folder's writer's (reset where they
    differ), and `writer.json` left as it was."""
    root, [(a, acon), (b, bcon), (c, ccon)] = _discovered(tmp_path, names=("alpha", "beta", "gamma"))
    acon.execute("UPDATE documents SET fts_done=1, chunks=3")
    acon.commit()
    rel = _share(root, ["alpha", "beta"], "alpha")
    ccon.execute("UPDATE documents SET fts_done=1, chunks=9 WHERE native_id='10002'")   # differs
    ccon.commit()
    got = text_index.form_at_carry(root, "beta", "gamma", "id1", "id1")
    assert got == rel and json.loads((root / rel / text_index.WRITER).read_text())["writer"] == "alpha"
    marks = dict(ccon.execute("SELECT native_id, fts_done FROM documents").fetchall())
    assert marks["10002"] == 0, "reset where it differed from the writer's"


@pytest.mark.parametrize("cut", [1, 2])
def test_a_forming_killed_between_its_renames_is_finished_forward(tmp_path, monkeypatch, cut):
    """Finding 3: journaled before the first rename; a retry finishes the
    folder forward and nothing moved is lost (killed after the first of two
    group renames, and after the last, before the placements)."""
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"en")
    _fake_group(a.fts_dir, "it", b"it")
    real = os.rename
    done = []

    class Killed(Exception):
        pass

    def rename(x, y):
        real(x, y)
        done.append(y)
        if len(done) == cut:
            raise Killed()
    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(Killed):
        text_index.form_at_carry(root, "alpha", "beta", "id1", "id1")
    monkeypatch.setattr(os, "rename", real)
    assert text_index.placement(root, "alpha") is None, "nothing placed before the folder is whole"
    rel = text_index.form_at_carry(root, "alpha", "beta", "id1", "id1")
    assert (root / rel / "en" / "seg.store").read_bytes() == b"en"
    assert (root / rel / "it" / "seg.store").read_bytes() == b"it"
    for n in ("alpha", "beta"):
        assert text_index.placement(root, n)["dir"] == rel
    assert not list((root / text_index.SHARED / ".forming").glob("*.json")), "the journal is gone"
    assert len([d for d in (root / text_index.SHARED).iterdir() if not d.name.startswith(".")]) == 1


def test_a_group_whose_every_member_moves_keeps_its_folder_and_copies_nothing(tmp_path, monkeypatch):
    """Finding 4: every member of a folder now shares one new identity (with
    a newcomer): the folder is KEPT, retagged — no 54 GB copy."""
    root, [(a, acon), (b, _), (c, _)] = _discovered(tmp_path, names=("alpha", "beta", "gamma"))
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    rel = _share(root, ["alpha", "beta"], "alpha")
    _fake_group(root / rel, "en", b"x" * 50)
    ino = os.stat(root / rel / "en" / "seg.store").st_ino
    _with_identity(monkeypatch, {"alpha": "id9", "beta": "id9", "gamma": "id9"})
    out = text_index.settle(root, [a, b, c], log=lambda m: None)
    assert [g["how"] for g in out["groups"]] == ["keep"]
    for n in ("alpha", "beta", "gamma"):
        assert text_index.placement(root, n)["dir"] == rel
    assert text_index.folder_identity(root, rel) == "id9" and os.stat(root / rel / "en" / "seg.store").st_ino == ino
    assert all(text_index.placement(root, n)["identity"] == "id9" for n in ("alpha", "beta", "gamma")), \
        "the placements' identity refreshed"


def test_an_index_the_manifest_marks_populated_is_never_made_empty(tmp_path):
    """Finding 7: a search during a forming (or after a loss) never makes an
    empty index where the manifest marks documents of that group indexed."""
    root, [(a, acon), (_b, _)] = _discovered(tmp_path)
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    lang, book = acon.execute("SELECT lang, native_id FROM documents ORDER BY rowid LIMIT 1").fetchone()
    group = fts.group_for(lang)
    with pytest.raises(fts.IndexMissing, match="none is made"):
        fts.get_index(group, a)
    assert not (fts.base_of(a) / group / "meta.json").exists()
    assert fts.search(group, "anything", collection=a) == [], "the leg withheld, said (fts.search)"
    fts.delete_books(group, [int(book)], a)
    fts.delete_book(lang, int(book), a)
    assert not (fts.base_of(a) / group / "meta.json").exists(), "a delete makes no index: nothing is held there"
    fts.index_book(lang, int(book), [{"id": "x:0:1", "chapter": "", "text": "a b c"}], a)
    assert (fts.base_of(a) / group / "meta.json").is_file(), "a write makes it"
    fts._indexes.clear()
    shutil.rmtree(fts.base_of(a) / group)
    acon.execute("UPDATE documents SET fts_done=0")
    acon.commit()
    fts._marked_groups.clear()
    assert fts.get_index(group, a) is not None, "a group not marked yet is the writer's to make"


def test_a_marked_multilingual_document_marks_every_group_its_sections_can_be_in(tmp_path, monkeypatch):
    """Gap 7 of the fourth read: a `mul` document marked indexed marks each
    declared section language's group (and `other`): a read never makes an
    empty index there."""
    root, [(a, acon), (_b, _)] = _discovered(tmp_path)
    monkeypatch.setattr(fts, "section_languages", lambda collection: ("en", "de"))
    acon.execute("UPDATE documents SET lang=?, fts_done=0", (fts.MULTILINGUAL,))
    acon.execute("UPDATE documents SET fts_done=1 WHERE rowid=(SELECT MIN(rowid) FROM documents)")
    acon.commit()
    for group in fts.groups_for_lang(a, fts.MULTILINGUAL):
        with pytest.raises(fts.IndexMissing, match="none is made"):
            fts.get_index(group, a)
        assert not (fts.base_of(a) / group / "meta.json").exists(), group


# ---- the fourth read of cf91b9ac: settle and the journal ---------------------------

def test_a_collection_another_process_holds_is_not_settled(tmp_path, monkeypatch):
    """Finding 3: a collection whose write lock another process holds (a
    `hold` command, a sister being carried) is not settled this boot, said;
    nothing of its sister moves either."""
    import time
    from app.hold import lock as lock_mod
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"alpha")
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id1"})
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:                                            # another process holds beta
        lk = lock_mod.take(lock_mod.state_dir(root, "beta"), "beta", "a hold command")
        os.write(w, b"1")
        time.sleep(30)
        lk.release()
        os._exit(0)
    try:
        os.read(r, 1)
        said = []
        out = text_index.settle(root, [a, b], log=said.append)
        assert ("beta", "another process holds it (a hold command)") in out["skipped"]
        assert any("beta not settled this boot" in m for m in said)
        assert text_index.placement(root, "alpha") is None and text_index.placement(root, "beta") is None
        assert (a.fts_dir / "en" / "seg.store").read_bytes() == b"alpha", "nothing renamed"
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        os.close(r), os.close(w)


def test_one_identity_s_failure_is_said_and_the_others_settle(tmp_path, monkeypatch):
    """Finding 3: each identity is caught on its own (OperationalError
    included), said, and the rest settled; the next boot finishes the
    failed one's forming forward."""
    root, cols = _discovered(tmp_path, names=("alpha", "beta", "gamma", "delta"))
    (a, _), (b, _), (c, _), (d, _) = cols
    _fake_group(a.fts_dir, "en", b"alpha")
    _fake_group(c.fts_dir, "en", b"gamma")
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id1", "gamma": "id2", "delta": "id2"})
    real = text_index.reset_differing

    def locked(reader, wman, **kw):
        if Path(reader) == b.manifest_path:
            raise sqlite3.OperationalError("database is locked")
        return real(reader, wman, **kw)
    monkeypatch.setattr(text_index, "reset_differing", locked)
    said = []
    out = text_index.settle(root, [x for x, _ in cols], log=said.append)
    assert [s for s in out["skipped"] if s[0] == "alpha,beta"][0][1].startswith("OperationalError")
    assert text_index.placement(root, "gamma")["dir"] == text_index.placement(root, "delta")["dir"]
    assert any("alpha, beta not settled this boot" in m for m in said)
    monkeypatch.setattr(text_index, "reset_differing", real)
    again = text_index.settle(root, [x for x, _ in cols], log=lambda m: None)
    assert not again["skipped"], again["skipped"]
    rel = text_index.placement(root, "alpha")["dir"]
    assert text_index.placement(root, "beta")["dir"] == rel and (root / rel / "en" / "seg.store").read_bytes() == b"alpha"


def test_only_its_own_journals_are_resumed_and_a_stale_foreign_one_blocks_nothing(tmp_path):
    """Finding 3: a forming journal naming collections the caller does not
    settle is left alone, and an unreadable one is said: neither blocks a
    sister's carry nor the boot."""
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"alpha")
    jd = text_index._journal_dir(root)
    jd.mkdir(parents=True)
    foreign = jd / "f0reign.json"
    foreign.write_text(json.dumps({"rel": f"{text_index.SHARED}/f0reign", "source": str(tmp_path / "gone"),
                                   "groups": ["en"], "copy": False, "identity": "x", "content": None,
                                   "writer": "x", "place": ["x", "y"]}), encoding="utf-8")
    (jd / "broken.json").write_text("{not json", encoding="utf-8")
    rel = text_index.form_at_carry(root, "alpha", "beta", "id1", "id1")
    assert text_index.placement(root, "beta")["dir"] == rel
    out = text_index.settle(root, [a, b], log=lambda m: None)
    assert foreign.is_file() and not (root / text_index.SHARED / "f0reign").exists(), "left alone"
    assert any(s[0] == "broken.json" for s in out["skipped"]), "said"
    assert not (root / "x").exists(), "nothing made for a collection it does not settle"


def test_a_manifest_is_opened_to_write_and_never_created(tmp_path):
    """Finding 3: `mode=rw` — a collection made and not carried has no
    manifest, and none is made for it."""
    root, [(a, _), (_b, _)] = _discovered(tmp_path)
    missing = root / "sis" / "state" / "manifest.sqlite"
    missing.parent.mkdir(parents=True)
    with pytest.raises(sqlite3.OperationalError):
        text_index.reset_differing(missing, a.manifest_path, log=lambda m: None)
    with pytest.raises(sqlite3.OperationalError):
        text_index._reset_all(missing)
    assert not missing.exists()


def test_a_boot_skips_a_collection_with_no_manifest_and_makes_none(tmp_path, monkeypatch):
    root, [(a, _), (_b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"alpha")
    sis = _collection(root, "sis", a.corpus_dir)                         # made, not carried
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id9", "sis": "id1"})
    out = text_index.settle(root, [a, _b, sis], log=lambda m: None)
    assert ("sis", "no manifest yet (made, its carry not done)") in out["skipped"]
    assert not sis.manifest_path.exists() and text_index.placement(root, "alpha") is None
    assert (a.fts_dir / "en" / "seg.store").read_bytes() == b"alpha"


def test_a_group_s_record_a_journal_and_its_placements_are_synced_before_the_journal_goes(tmp_path, monkeypatch):
    """`group.json` and a forming's journal survive a power cut, and the
    journal is unlinked only once every placement it made is synced, the
    file then its directory (round 5, item 4)."""
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"alpha")
    events = []
    real_fsync, real_unlink = os.fsync, os.unlink
    monkeypatch.setattr(os, "fsync", lambda fd: events.append(("sync", os.readlink(f"/proc/self/fd/{fd}")))
                        or real_fsync(fd))
    monkeypatch.setattr(os, "unlink", lambda p, *a, **k: events.append(("unlink", str(p))) or real_unlink(p, *a, **k))
    rel = text_index.form_at_carry(root, "alpha", "beta", "id1", "id1")
    synced = [p for k, p in events if k == "sync"]
    jd = str(text_index._journal_dir(root))
    journal = [i for i, p in enumerate(synced) if p.startswith(jd + "/.") and p.endswith(".json.tmp")]
    assert journal and synced[journal[0] + 1] == jd, synced
    group = [i for i, p in enumerate(synced) if p == str(root / rel / f".{text_index.GROUP}.tmp")]
    assert group and synced[group[0] + 1] == str(root / rel), synced
    gone = next(i for i, (k, p) in enumerate(events) if k == "unlink" and p.startswith(jd + "/") and p.endswith(".json"))
    for n in ("alpha", "beta"):
        pf = str(text_index.placement_file(root, n))
        at = next(i for i, (k, p) in enumerate(events) if k == "sync" and p == pf.rsplit("/", 1)[0] + "/.text-index.json.tmp")
        assert at < gone and events[at + 1] == ("sync", pf.rsplit("/", 1)[0]), (n, events)


def test_an_open_journal_holds_its_collections_fts_and_makes_no_group_for_them(tmp_path):
    """Round 5, item 3: while an unfinished forming names a collection, the
    worker selects no fts work for it, a write refuses (a deferral, said),
    and no group is made for it; once finished, it indexes again."""
    root, [(a, acon), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"alpha")
    jd = text_index._journal_dir(root)
    jd.mkdir(parents=True)
    journal = jd / "x.json"
    journal.write_text(json.dumps({"rel": f"{text_index.SHARED}/x", "source": str(a.fts_dir), "groups": ["en"],
                                   "identity": "id1", "content": None, "writer": "alpha",
                                   "place": ["alpha", "beta"]}), encoding="utf-8")
    assert text_index.forming(a) and text_index.forming(b)
    assert worker._next_book(acon, db.get_scope(acon), embed_allowed=False, collection=a)[0] != "fts"
    assert not worker._ingest_pending(acon, a)
    with pytest.raises(fts.FormingOpen, match="being formed"):
        fts.index_books([("de", 1, [{"id": "x:0:1", "chapter": "", "text": "a b c"}])], a)
    with pytest.raises(fts.FormingOpen):
        fts.get_index("de", a)
    assert not (a.fts_dir / "de").exists() and not (fts.base_of(a) / "de").exists(), "no group made"
    row = acon.execute("SELECT * FROM documents WHERE fts_done=0 ORDER BY native_id LIMIT 1").fetchone()
    assert worker._execute_step(acon, a, "fts", row) is False, "deferred"
    assert acon.execute("SELECT COUNT(*) FROM documents WHERE error IS NOT NULL").fetchone()[0] == 0, \
        "never an error on a document"
    text_index.resume_forming(root, lambda m: None)
    assert not text_index.forming(a) and text_index.placement(root, "alpha")["dir"] == f"{text_index.SHARED}/x"
    assert worker._next_book(acon, db.get_scope(acon), embed_allowed=False, collection=a)[0] == "fts"


def test_a_carry_never_finishes_a_journal_naming_a_collection_the_worker_runs(tmp_path):
    """Round 5, item 4: `form_at_carry` does not resume a forming that
    names a member another process runs; it renames nothing and says so."""
    import time
    from app.hold import lock as lock_mod
    root, [(a, _), (b, _), (c, _)] = _discovered(tmp_path, names=("alpha", "beta", "gamma"))
    _fake_group(a.fts_dir, "en", b"alpha")
    jd = text_index._journal_dir(root)
    jd.mkdir(parents=True)
    (jd / "x.json").write_text(json.dumps({"rel": f"{text_index.SHARED}/x", "source": str(a.fts_dir),
                                           "groups": ["en"], "identity": "id1", "content": None,
                                           "writer": "alpha", "place": ["alpha", "gamma"]}), encoding="utf-8")
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:                                            # the worker runs gamma
        lk = lock_mod.take(lock_mod.state_dir(root, "gamma"), "gamma", "the node (worker)")
        os.write(w, b"1")
        time.sleep(30)
        lk.release()
        os._exit(0)
    try:
        os.read(r, 1)
        with pytest.raises(text_index.WriterHeld, match="gamma, which another process runs"):
            text_index.form_at_carry(root, "alpha", "beta", "id1", "id1")
        assert (a.fts_dir / "en" / "seg.store").read_bytes() == b"alpha", "nothing renamed"
        assert (jd / "x.json").is_file() and text_index.placement(root, "alpha") is None
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        os.close(r), os.close(w)
