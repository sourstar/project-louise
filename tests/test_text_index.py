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
    out = text_index.settle(root, [a, b], log=lambda m: None)
    rel = f"{text_index.SHARED}/id1"
    assert (root / rel / "en" / "seg.store").read_bytes() == b"alpha"
    assert not text_index.group_dirs(a.fts_dir) and not text_index.group_dirs(b.fts_dir)
    assert (a.fts_dir / "_catalog").is_dir(), "the catalogue stays the collection's own"
    assert text_index.placement(root, "alpha")["dir"] == rel == text_index.placement(root, "beta")["dir"]
    assert text_index.role(b).reader and text_index.role(b).writer == "alpha"
    # beta's marks kept where equal to alpha's, reset where they differed
    got = dict(bcon.execute("SELECT native_id, fts_done FROM documents").fetchall())
    assert got == {"10001": 1, "10002": 0, "10003": 0}
    assert out["freed"] > 0
    again = text_index.settle(root, [a, b], log=lambda m: None)        # idempotent
    assert again["freed"] == 0 and (root / rel / "en" / "seg.store").read_bytes() == b"alpha"


def test_a_member_left_alone_takes_a_linked_copy_and_its_own_index_back(tmp_path, monkeypatch):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    rel = _share(root, ["alpha", "beta"], "alpha")
    _fake_group(root / rel, "en", b"shared")
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id2"})       # their slices differ now
    text_index.settle(root, [a, b], log=lambda m: None)
    for c in (a, b):
        assert text_index.placement(root, c.name) is None
        assert (c.fts_dir / "en" / "seg.store").read_bytes() == b"shared"
    assert os.stat(a.fts_dir / "en" / "seg.store").st_ino == os.stat(b.fts_dir / "en" / "seg.store").st_ino
    assert os.stat(a.fts_dir / "en" / ".tantivy-writer.lock").st_ino != \
        os.stat(b.fts_dir / "en" / ".tantivy-writer.lock").st_ino, "a lock is never one inode for two"
    assert not (root / rel).exists()


def test_a_shared_directory_nobody_is_placed_in_is_set_aside_never_adopted(tmp_path, monkeypatch):
    root, [(a, acon), (b, _)] = _discovered(tmp_path)
    stale = root / text_index.SHARED / "id1"
    _fake_group(stale, "en", b"stale")
    _fake_group(a.fts_dir, "en", b"alpha")
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id1"})
    out = text_index.settle(root, [a, b], log=lambda m: None)
    assert (stale / "en" / "seg.store").read_bytes() == b"alpha"
    assert any(o["dir"].split("/")[-1].startswith(".stale-id1") for o in out["orphans"])


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


def test_a_sister_carry_links_the_language_groups_and_copies_the_catalogue(tmp_path):
    from app.hold import carry
    src, dst = tmp_path / "a" / "index", tmp_path / "b" / "index"
    _fake_group(src, "en", b"text")
    (src / "_catalog").mkdir(parents=True)
    (src / "_catalog" / "c.store").write_bytes(b"cat")
    assert carry._sister_index(src, dst).startswith("linked")
    assert os.stat(src / "en" / "seg.store").st_ino == os.stat(dst / "en" / "seg.store").st_ino
    assert os.stat(src / "_catalog" / "c.store").st_ino != os.stat(dst / "_catalog" / "c.store").st_ino
    assert carry._sister_index(src, dst) == "present"


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
