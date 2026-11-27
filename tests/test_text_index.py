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
    """The static share: every other name reads `writer`'s own index."""
    rel = f"{writer}/index"
    (root / rel).mkdir(parents=True, exist_ok=True)
    for n in names:
        if n != writer:
            text_index.write_placement(root, n, rel, "abc", writer=writer)
    text_index.invalidate()
    return rel


def test_the_base_is_the_own_directory_until_a_placement_names_her_sister_s(tmp_path):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    assert text_index.base(b.fts_dir) == b.fts_dir
    rel = _share(root, ["alpha", "beta"], "alpha")
    assert text_index.base(a.fts_dir) == a.fts_dir == root / rel == text_index.base(b.fts_dir)
    assert fts.base_of(b) == a.fts_dir
    assert text_index.role(b).reader and text_index.role(b).writer == "alpha" and not text_index.role(a).reader


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
    assert fts.existing_groups(a) and text_index.group_dirs(a.fts_dir), "its own index"
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


# ---- helpers ---------------------------------------------------------------------

def _fake_group(index_dir: Path, group: str, payload: bytes = b"x") -> None:
    g = index_dir / group
    g.mkdir(parents=True, exist_ok=True)
    (g / "meta.json").write_text("{}", encoding="utf-8")
    (g / "seg.store").write_bytes(payload)
    (g / ".tantivy-writer.lock").write_bytes(b"")


def _with_identity(monkeypatch, idents: dict):
    monkeypatch.setattr(text_index, "identity", lambda c: idents.get(c.name))


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


def _row(con, native):
    return con.execute("SELECT * FROM documents WHERE native_id=?", (native,)).fetchone()


def test_a_sister_carry_copies_the_catalogue_and_leaves_the_language_groups_where_they_lie(tmp_path):
    """Never a hard link: the groups are read where they lie (`share`), or, where
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


# ---- the index as read ------------------------------------------------------------

def test_the_index_digest_is_the_index_as_read_shared_or_not(tmp_path):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _fake_group(a.fts_dir, "en", b"same")
    (a.fts_dir / "_catalog").mkdir()
    (a.fts_dir / "_catalog" / "c").write_bytes(b"cat")
    (b.fts_dir / "_catalog").mkdir(parents=True)
    (b.fts_dir / "_catalog" / "c").write_bytes(b"cat")
    text_index.write_placement(root, "beta", "alpha/index", "abc", writer="alpha")
    assert text_index.index_digest(root, "alpha") == text_index.index_digest(root, "beta")


def test_describe_says_who_writes(tmp_path):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    assert text_index.describe(root, "alpha")["shared"] is None
    _share(root, ["alpha", "beta"], "alpha")
    d = text_index.describe(root, "beta")
    assert d["writer"] == "alpha" and "written by alpha" in d["says"]
    assert text_index.describe(root, "alpha")["says"] == "its own, read by beta"


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


# ---- never an empty index where the marks say indexed ---------------------------

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


# ---- a manifest is never created ---------------------------------------------------

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


# ---- THE STATIC SHARE (the operator's default B, 2026-11-27) ------------------------

def _tree(root):
    """Every file under `root`: its path, inode, size and mtime — what a boot
    that moves, copies or resets nothing leaves as it was."""
    return {str(p.relative_to(root)): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


def test_the_share_reads_her_sister_s_own_index_her_marks_reconciled_once(tmp_path):
    """At her creation she reads `<sister>/index` by path, her marks
    reconciled against her sister's (reset where they differ); her own
    groups, an earlier carry's copy, go; her sister stays its only writer."""
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    acon.execute("UPDATE documents SET fts_done=1, chunks=3")
    acon.commit()
    bcon.execute("UPDATE documents SET fts_done=1, chunks=3")
    bcon.execute("UPDATE documents SET chunks=9 WHERE native_id='10002'")
    bcon.commit()
    _fake_group(a.fts_dir, "en", b"alpha")
    _fake_group(b.fts_dir, "en", b"stale")
    ino = os.stat(a.fts_dir / "en" / "seg.store").st_ino
    assert text_index.share(root, "alpha", "beta", "id1", log=lambda m: None) == "alpha/index"
    assert text_index.placement(root, "beta") == {"dir": "alpha/index", "identity": "id1", "writer": "alpha"}
    assert text_index.placement(root, "alpha") is None and text_index.readers(root, "alpha") == ["beta"]
    assert fts.base_of(b) == a.fts_dir and os.stat(a.fts_dir / "en" / "seg.store").st_ino == ino
    assert not text_index.group_dirs(b.fts_dir), "her own copy, never read again, gone"
    assert dict(bcon.execute("SELECT native_id, fts_done FROM documents").fetchall()) == \
        {"10001": 1, "10002": 0, "10003": 1}
    assert all(f.stat().st_nlink == 1 for f in root.rglob("*") if f.is_file()), "no hard link"


def test_the_reader_never_writes_her_sister_s_index(tmp_path):
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    _share(root, ["alpha", "beta"], "alpha")
    assert not text_index.writes(b) and text_index.writes(a)
    assert worker._next_book(bcon, db.get_scope(bcon), embed_allowed=False, collection=b)[0] != "fts"
    for call in (lambda: fts.index_books([("en", 1, [])], b), lambda: fts.rewrite_without("en", b)):
        with pytest.raises(fts.ReaderWrite):
            call()
    assert fts.delete_book("en", 1, b) is None and not text_index.group_dirs(a.fts_dir), "nothing written"


def test_a_divergence_withholds_her_text_leg_in_words_and_moves_nothing(tmp_path, monkeypatch):
    """At boot each placement is only CHECKED: her identity no longer her
    sister's, her text leg is withheld with the words (show, the search),
    and nothing is moved, copied or reset; equal again, it is served."""
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    bcon.execute("UPDATE documents SET fts_done=1")
    bcon.commit()
    _fake_group(a.fts_dir, "en", b"alpha")
    _share(root, ["alpha", "beta"], "alpha")
    before = {k: v for k, v in _tree(root).items() if "text-index.json" not in k}
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id2"})
    said = []
    out = text_index.settle(root, [a, b], log=said.append)
    words = "her index identity no longer matches alpha's: `louise hold collection unshare beta`"
    assert out["withheld"] == [("beta", words)] and any(words in m for m in said)
    assert {k: v for k, v in _tree(root).items() if "text-index.json" not in k} == before, "nothing moved"
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 3, "nothing reset"
    assert words in text_index.describe(root, "beta")["says"]
    with pytest.raises(fts.TextLegError, match="no longer matches alpha's"):
        fts.search("en", "anything", collection=b, raise_errors=True)
    assert worker._fts_copy_round(bcon, b) is False, "no mark copied either"
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id1"})
    again = text_index.settle(root, [a, b], log=lambda m: None)
    assert again["served"] == ["beta"] and text_index.withheld(b) is None


def test_unshare_gives_her_an_empty_index_of_her_own_her_marks_reset(tmp_path):
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    bcon.execute("UPDATE documents SET fts_done=1")
    bcon.commit()
    _fake_group(a.fts_dir, "en", b"alpha")
    (b.fts_dir / "_catalog").mkdir(parents=True)
    _share(root, ["alpha", "beta"], "alpha")
    said = []
    out = text_index.unshare(root, "beta", log=said.append)
    assert out["unshared"] and out["reset"] == 3 and any("no longer reads alpha/index" in m for m in said)
    assert text_index.placement(root, "beta") is None and not text_index.role(b).reader
    assert b.fts_dir.is_dir() and not text_index.group_dirs(b.fts_dir) and (b.fts_dir / "_catalog").is_dir()
    assert bcon.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0] == 0
    assert (a.fts_dir / "en" / "seg.store").read_bytes() == b"alpha", "her sister's untouched"


def test_unshare_needs_the_node_stopped_and_is_not_routed(tmp_path, monkeypatch):
    import time
    from app import api_commands, louise
    from app.hold import lock as lock_mod
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _share(root, ["alpha", "beta"], "alpha")
    monkeypatch.setattr(louise.col, "name_of", lambda r, n: n)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:                                            # the worker runs beta
        lk = lock_mod.take(lock_mod.state_dir(root, "beta"), "beta", "the node (worker)")
        os.write(w, b"1")
        time.sleep(30)
        lk.release()
        os._exit(0)
    try:
        os.read(r, 1)
        assert louise.main(["hold", "collection", "unshare", "beta", "--root", str(root)]) == 3
        assert text_index.placement(root, "beta") is not None, "nothing done"
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        os.close(r), os.close(w)
    with pytest.raises(api_commands.Refused, match="needs the node stopped"):
        api_commands.resolve(["hold", "collection", "unshare"])


def test_a_forget_hands_her_sister_s_index_to_its_one_reader_by_rename(tmp_path):
    from app.hold import forget as forget_mod
    root, [(a, acon), (b, bcon)] = _discovered(tmp_path)
    acon.close(), bcon.close()
    _fake_group(a.fts_dir, "en", b"alpha")
    ino = os.stat(a.fts_dir / "en" / "seg.store").st_ino
    _share(root, ["alpha", "beta"], "alpha")
    assert forget_mod.forget(root, "alpha", dry_run=True)["text_index"] == \
        {"to": "beta", "groups": ["en"], "dry_run": True}
    out = forget_mod.forget(root, "alpha")
    assert out["text_index"] == {"to": "beta", "groups": ["en"]} and not (root / "alpha").exists()
    assert os.stat(b.fts_dir / "en" / "seg.store").st_ino == ino, "renamed: she writes it now"
    assert text_index.placement(root, "beta") is None and text_index.writes(b)
    assert all(f.stat().st_nlink == 1 for f in root.rglob("*") if f.is_file()), "no hard link"


def test_a_forget_is_refused_while_two_sisters_read_its_index(tmp_path):
    from app.hold import forget as forget_mod
    from app.hold.codex import CodexError
    root, [(a, _), (b, _), (c, _)] = _discovered(tmp_path, names=("alpha", "beta", "gamma"))
    _fake_group(a.fts_dir, "en", b"alpha")
    _share(root, ["alpha", "beta", "gamma"], "alpha")
    with pytest.raises(CodexError, match="`louise hold collection unshare beta`; `louise hold collection "
                                         "unshare gamma`"):
        forget_mod.forget(root, "alpha")
    assert (root / "alpha.toml").is_file() and (a.fts_dir / "en" / "seg.store").is_file(), "nothing removed"


def test_an_earlier_version_s_group_is_refused_at_boot_never_migrated(tmp_path, monkeypatch):
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    old = root / text_index.EARLIER / "abc"
    _fake_group(old, "en", b"group")
    for n in ("alpha", "beta"):
        placement = text_index.placement_file(root, n)
        placement.write_text(json.dumps({"dir": f"{text_index.EARLIER}/abc", "identity": "abc"}),
                             encoding="utf-8")
    text_index.invalidate()
    before = _tree(root)
    _with_identity(monkeypatch, {"alpha": "abc", "beta": "abc"})
    said = []
    out = text_index.settle(root, [a, b], log=said.append)
    assert out["earlier"] and [n for n, _ in out["withheld"]] == ["alpha", "beta"]
    assert all("made by an earlier version" in why for _, why in out["withheld"])
    assert _tree(root) == before, "never migrated: nothing moved, nothing written"
    with pytest.raises(fts.IndexMissing, match="made by an earlier version"):
        fts.get_index("en", a)


def test_a_node_as_main_left_it_is_untouched_at_boot(tmp_path, monkeypatch):
    """Main's node layout: every collection on its own index, no placement
    (measured: no group on the node). The boot changes no byte of it."""
    root, [(a, acon), (b, _)] = _discovered(tmp_path)
    acon.execute("UPDATE documents SET fts_done=1")
    acon.commit()
    _fake_group(a.fts_dir, "en", b"alpha")
    _fake_group(b.fts_dir, "en", b"beta")
    _with_identity(monkeypatch, {"alpha": "id1", "beta": "id1"})       # equal: still nothing automatic
    before = _tree(root)
    out = text_index.settle(root, [a, b], log=lambda m: None)
    assert out["served"] == [] and out["withheld"] == [] and "earlier" not in out
    assert _tree(root) == before and not (root / text_index.EARLIER).exists()


def test_a_collection_a_sister_reads_is_not_renamed_from_under_her(tmp_path):
    from app.hold import rename as rename_mod
    from app.hold.codex import CodexError
    root, [(a, _), (b, _)] = _discovered(tmp_path)
    _share(root, ["alpha", "beta"], "alpha")
    with pytest.raises(CodexError, match="beta reads its text index where it lies"):
        rename_mod.rename(root, "alpha", "omega")
    assert (root / "alpha").is_dir()
