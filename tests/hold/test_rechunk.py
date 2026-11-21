"""tests/hold/test_rechunk.py — amendment A1k (PROPOSED 2026-11-21;
app/hold/rechunk.py, docs/notes/legal-chunker-v4.md §4.3): a collection carried
into a successor whose space differs ONLY by its layers' chunker version, every
vector whose chunk text the new version cuts again kept under its new id.

The fixture is the shared legal space's move in small: two gutenberg Codexes
cutting by `legal_article` (conftest's one-token-a-word stand-in for
Nemotron's tokenizer, a 40-token window, no header), the source at version 3
and the target at 4. Two books are English, which v4 cuts as v3; the third's
first chapter is Greek, whose sentences v3 never saw (its capitals are not
Latin-1) and cut by token windows, and v4 cuts at its sentences — into one
chunk more, so every English chunk after it moves to another `seq`. The
source's vectors are a fake encoder's, a function of the text."""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest

from app import chunking_legal as cl, config, db, embeddings, layers, record_source, spaces
from app.hold import carry, collection as col, lock as lock_mod, rechunk, records as rec, snapshot as sn
from app.hold.codex import CodexError
from tests.conftest import FIXTURE_CORPUS
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
from tests.hold.test_template import SPACE, recipe

quiet = lambda *_a, **_k: None                       # noqa: E731

#: 13 Greek sentences of 9 words: 117 tokens. v3 sees no sentence start and
#: cuts token windows of 40 (40, 40, 37); v4 packs 4 sentences a piece (36)
GREEK = " ".join(f"Η Επιτροπή εξετάζει το ζήτημα {i} του κανονισμού σήμερα." for i in range(13))


def _layer(version: int, max_tokens: int = 40) -> list[dict]:
    return [{"rule": "default",
             "chunker": {"id": "legal_article", "version": version,
                         "params": {"tokenizer": cl.NEMOTRON_TOKENIZER, "max_tokens": max_tokens,
                                    "header": False, "outline": False}},
             "input": {"form": "text"}}]


def _create(root, name, version: int, **over):
    return col.create_from_recipe(root, "gutenberg", recipe(layers=_layer(version), **over),
                                  platform_image=PLATFORM, embed=fake_embed,
                                  embed_query=fake_query_embed, name=name, key=KEY, now=NOW)


def _greek_corpus(tmp_path) -> Path:
    """The mini corpus, book 10003's first chapter in Greek (its second
    stays French)."""
    dest = tmp_path / "greek-corpus"
    shutil.copytree(FIXTURE_CORPUS, dest)
    p = dest / "10003" / "pg10003.txt.utf8"
    text = p.read_text(encoding="utf-8")
    first = text.index("Chapitre I\n") + len("Chapitre I\n")
    second = text.index("Chapitre II")
    p.write_text(text[:first] + "\n" + GREEK + "\n\n" + text[second:], encoding="utf-8")
    return dest


def _local(root, name):
    return rec._local_collection(root, name, col.read(root, name))


def _cut(coll, row) -> list[dict]:
    plugin = coll.plugin_instance()
    title, sections, source = record_source.title_sections_and_source(coll, dict(row), plugin, pair=False)
    return layers.with_source(layers.chunks_for_document(coll, dict(row), sections, title=title), source)


def _embedded(root, codex, name="v3") -> dict[str, np.ndarray]:
    """Every chunk of `name` embedded by the fake encoder, its documents
    marked embedded at this EMBED_VERSION, its space pinned; the index marks
    and a little of everything A1i empties. Returns id -> stored vector."""
    coll = _local(root, name)
    con = db.connect(coll)
    rows = []
    try:
        con.execute("INSERT INTO space(id, space_id, dim, pinned_at) VALUES(1, ?, ?, 0)",
                    (codex.space_id, codex.space.dim))
        for d in con.execute("SELECT * FROM documents ORDER BY rowid").fetchall():
            chunks = _cut(coll, d)
            vecs = fake_embed([c["text"] for c in chunks])
            rows += [{"id": c["id"], "book_id": int(d["native_id"]), "lang": d["lang"] or "und", "seq": c["seq"],
                      "chapter": c["chapter"] or "", "text": c["text"], "vector": np.asarray(v, dtype="float32")}
                     for c, v in zip(chunks, vecs)]
        con.execute("UPDATE documents SET embed_done=1, embed_version=?, fts_done=1, fts_version=3, "
                    "entity_version=2, chunks=5, tokens=900", (config.EMBED_VERSION,))
        con.execute("INSERT INTO chunk_hashes(text_hash, chunk_id, native_id) VALUES(x'01', 'x:1:0:3', 1)")
        con.execute("INSERT INTO control(key, value) VALUES('embed_gate', '{}')")
        con.commit()
    finally:
        con.close()
    embeddings.upsert_chunks(rows, collection=coll, dtype=codex.space.dtype)
    held = embeddings.held_chunks(coll, sorted({r["book_id"] for r in rows}))
    return {cid: r["vector"] for book in held.by_book.values() for cid, r in book.items()}


@pytest.fixture
def move(isolated_config, tmp_path):
    """`v3` built, embedded and pinned; `v4` its successor, its parts from
    v3's record lines — the steps before the carry (legal-chunker-v4.md
    §4.3, 1-3)."""
    corpus = _greek_corpus(tmp_path)
    root = tmp_path / "collections"
    v3 = _create(root, "v3", 3)
    rec.build(root, "v3", corpus_dir=corpus, records_per_shard=2, log=quiet)
    rec.write_validation(root, "v3")
    sn.create(root, "v3", snapshot_id="2026-11", log=quiet)
    v4 = _create(root, "v4", 4)
    vectors = _embedded(root, v3)
    return root, v3, v4, vectors


class Locks:
    """Both collections' write locks, held by this process as `dev carry
    --yes` holds them."""

    def __init__(self, root, names=("v3", "v4")):
        self.held = [lock_mod.take(lock_mod.state_dir(root, n), n, "test") for n in sorted(names)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for lk in self.held:
            lk.release()


def _carried(root, **kw):
    with Locks(root):
        out = carry.carry(root, "v3", "v4", copy=True, rechunked=True, apply=True, log=quiet)
        return out, rechunk.rekey(root, "v3", "v4", apply=True, log=quiet, **kw)


def _table(root, name="v4") -> dict[str, tuple]:
    """id -> (seq, chapter, text, vector bytes, source_hash) of a table."""
    coll = _local(root, name)
    con = db.connect(coll)
    try:
        books = [int(r[0]) for r in con.execute("SELECT native_id FROM documents")]
    finally:
        con.close()
    out = {}
    for book in embeddings.held_chunks(coll, books).by_book.values():
        for cid, r in book.items():
            out[cid] = (r["text"], np.asarray(r["vector"]).tobytes(), r.get(embeddings.SOURCE_COLUMN))
    tbl = embeddings._committed_table(embeddings._conn(coll))
    if tbl is not None:
        for r in tbl.search().select(["id", "seq", "chapter"]).limit(10_000).to_list():
            out[r["id"]] = (r["seq"], r["chapter"]) + out[r["id"]]
    return out


def _marks(root, name="v4") -> dict[str, tuple]:
    con = sqlite3.connect(root / name / "state" / "manifest.sqlite")
    try:
        return {r[0]: r[1:] for r in con.execute(
            "SELECT uid, embed_done, COALESCE(embed_version,0), fts_done, fts_version, entity_version, chunks, "
            "tokens, lang FROM documents")}
    finally:
        con.close()


def _cuts(root, name="v4") -> dict[str, list[dict]]:
    coll = _local(root, name)
    con = db.connect(coll)
    try:
        return {d["uid"]: _cut(coll, d) for d in con.execute("SELECT * FROM documents ORDER BY rowid").fetchall()}
    finally:
        con.close()


def _files(*dirs: Path) -> dict[str, str]:
    """Every file under `dirs` by the digest of what it holds — a SQLite
    database by its dump (a write in WAL mode reaches the main file only at a
    checkpoint, and a read-only reader leaves its `-wal`/`-shm` behind), the
    lock files aside, which taking a lock writes."""
    import hashlib
    out = {}
    for d in dirs:
        for p in sorted(Path(d).rglob("*")) if Path(d).exists() else ():
            if not p.is_file() or p.name == lock_mod.LOCK or p.name.endswith(("-wal", "-shm")):
                continue
            if p.suffix == ".sqlite":
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                try:
                    body = "\n".join(con.iterdump()).encode()
                finally:
                    con.close()
            else:
                body = p.read_bytes()
            out[str(p)] = hashlib.sha256(body).hexdigest()
    return out


# ---- the carry ------------------------------------------------------------------

def test_the_fixture_is_the_move_in_small(move):
    """The two spaces differ by their layers' version alone: the records are
    the same records but for the layers, the function is the same."""
    root, v3, v4, vectors = move
    from app.hold import seed
    assert v3.space_id != v4.space_id and carry.space_differences(v3, v4) == []
    assert seed.record_differences(v3, v4) == ["layers"] and carry.layers_version_only(v3, v4) is None
    assert all(cid.endswith(":3") for cid in vectors) and len(vectors) == 14


def test_unchanged_chunks_keep_their_vector_bytes_under_the_new_ids(move):
    """Every chunk whose text v4 cuts again takes the source's vector BYTES,
    its text and its record's source under its new id, seq and chapter —
    found by its text, never its position: the French chunks of book 10003
    move one seq down behind the Greek. The rest are left to the embed stage:
    a document whose every chunk carried is marked embedded, the Greek one is
    not and holds no earlier version."""
    root, v3, v4, vectors = move
    s_table = _table(root, "v3")
    _out, got = _carried(root)
    table, cuts = _table(root), _cuts(root)
    by_text = {s[2]: s for s in s_table.values()}
    carried = [c for chunks in cuts.values() for c in chunks if c["text"] in by_text]
    assert sorted(table) == sorted(c["id"] for c in carried), "every chunk whose text is held, and no other"
    for c in carried:
        seq, chapter, text, vec, source = table[c["id"]]
        assert c["id"].endswith(":4") and (seq, chapter, text) == (c["seq"], c["chapter"] or "", c["text"])
        assert vec == by_text[text][3], "the source's vector bytes, exactly"
    greek = next(u for u, chunks in cuts.items() if any("Επιτροπή" in c["text"] for c in chunks))
    moved = [c for c in cuts[greek] if c["text"] in by_text]
    assert moved and all(by_text[c["text"]][0] == c["seq"] - 1 for c in moved), "one seq down, kept by text"
    marks = _marks(root)
    for uid, m in marks.items():
        assert m[:2] == ((0, 0) if uid == greek else (1, config.EMBED_VERSION)), uid
    t = got["totals"]
    assert (t["documents"], t["carried"], t["to_embed"], t["documents_embedded"]) == (3, len(carried), 4, 2)
    assert got["languages"]["fr"]["to_embed"] == 4 and got["languages"]["fr"]["dropped"] == 3
    assert got["done"] and got["vectors"] == got["rows_written"] == len(carried)


def test_rows_the_embed_stage_would_not_keep_are_not_carried(move):
    """`may_reuse`, the embed stage's rule: a document whose vectors were
    made at an earlier EMBED_VERSION (or marked to be replaced) keeps none of
    them; a declined one stays declined."""
    root, *_ = move
    con = sqlite3.connect(root / "v3" / "state" / "manifest.sqlite")
    old, declined = [r[0] for r in con.execute("SELECT uid FROM documents WHERE lang='en' ORDER BY uid")]
    con.execute("UPDATE documents SET embed_version=? WHERE uid=?", (embeddings.REPLACED_VERSION, old))
    con.execute("UPDATE documents SET embed_done=-1 WHERE uid=?", (declined,))
    con.commit()
    con.close()
    _out, got = _carried(root)
    marks, table = _marks(root), _table(root)
    assert marks[old][:2] == (0, 0) and marks[declined][:2] == (-1, 0)
    assert not [cid for cid in table if cid.startswith((old + ":", declined + ":"))]
    en = got["languages"]["en"]
    assert en["documents_not_reusable"] == 2 and en["carried"] == 0 and en["to_embed"] == en["chunks"]


def test_the_embed_stage_then_embeds_only_what_was_not_carried(move, monkeypatch):
    """The Greek document, embedded by the embed stage's own writer: its
    carried rows kept by (id, text), its four new chunks — and only they —
    sent to the encoder."""
    root, _v3, _v4, _vectors = move
    _carried(root)
    coll = _local(root, "v4")
    con = db.connect(coll)
    try:
        row = next(r for r in con.execute("SELECT * FROM documents WHERE embed_done=0"))
        chunks = _cut(coll, row)
        sent: list[str] = []
        monkeypatch.setattr(embeddings, "_by_run", lambda c: False)     # the fixture states no run
        monkeypatch.setattr(embeddings, "embed_chunks",
                            lambda c, items, **kw: sent.extend(x["text"] for x in items) or fake_embed(
                                [x["text"] for x in items]))
        counts = embeddings.EmbedCounts()
        embeddings.upsert_book(int(row["native_id"]), row["lang"], chunks, coll, lock_con=con,
                               reuse=embeddings.may_reuse(row), counts=counts)
    finally:
        con.close()
    assert len(sent) == 4 and all("Επιτροπή" in t for t in sent)
    assert counts.reused == len(chunks) - 4


def test_the_text_index_is_cut_again_and_the_space_re_pinned(move):
    root, v3, v4, _vectors = move
    (root / "v3" / "index" / "_catalog").mkdir(parents=True)
    (root / "v3" / "index" / "_catalog" / "meta.json").write_text("the catalogue")
    (root / "v3" / "index" / "words").write_text("the text index")
    source_before = _files(root / "v3" / "state", root / "v3" / "lance")
    out, _got = _carried(root)
    assert not (root / "v4" / "index" / "words").exists(), "the text index stores the old ids"
    assert (root / "v4" / "index" / "_catalog" / "meta.json").read_text() == "the catalogue"
    for uid, m in _marks(root).items():
        assert m[2:7] == (0, 0, 0, 0, 0), f"{uid}: every index mark and count emptied"
    con = sqlite3.connect(root / "v4" / "state" / "manifest.sqlite")
    try:
        assert con.execute("SELECT space_id, dim FROM space").fetchone() == (v4.space_id, v4.space.dim)
        assert con.execute("SELECT COUNT(*) FROM chunk_hashes").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM control WHERE key='embed_gate'").fetchone()[0] == 0
    finally:
        con.close()
    coll = _local(root, "v4")
    con = db.connect(coll)
    try:
        spaces._validate_codex(con, coll)            # the boot check passes: not refused
    finally:
        con.close()
    held = json.loads((root / "v4" / "state" / carry.MARKER).read_text())
    assert held["rechunked"] is True and held["done"] is True
    assert _files(root / "v3" / "state", root / "v3" / "lance") == source_before, "the source is untouched"
    again = carry.carry(root, "v3", "v4", copy=True, rechunked=True, apply=False, log=quiet)
    assert again["carried"] == "earlier"
    with Locks(root):
        marks = _marks(root)
        carry.carry(root, "v3", "v4", copy=True, rechunked=True, apply=True, log=quiet)
        assert _marks(root) == marks, "run again, the carry never undoes the re-key's marks"


def test_the_cpu_reference_crosses_with_the_function(move):
    """The note's step 1: the reference is a function of the encoder side
    and the set, which A1k does not move."""
    from app.hold import cpu_reference as cpu_ref
    root, v3, _v4, _vectors = move
    run = {"driver": "cpu", "weights": v3.space.encoder.weights, "engine": "sha256:" + "cd" * 32}
    kept = cpu_ref.write(root, "v3", "document", run_id="sha256:" + "ab" * 32, run=run,
                         set_id=v3.space.calibration.set, vectors=[[1.0, 0.0], [0.0, 1.0]])
    out, _got = _carried(root)
    assert out["derived"]["cpu-reference"]["carried"] == 1
    assert (cpu_ref.directory(root, "v4", "document") / kept.name).read_bytes() == kept.read_bytes()


def test_a_versioned_collection_s_rows_carry_its_columns_and_its_chunk_hashes(move, monkeypatch):
    """Legal collections are versioned (validity per Work): the carried rows
    carry the target's validity columns, a complete document is marked synced
    with them and its chunk hashes recorded, as the embed stage does."""
    from app import validity
    root, _v3, _v4, _vectors = move
    monkeypatch.setattr(validity, "declared", lambda c: validity.Validity("extra.from", "extra.work"))
    _out, got = _carried(root)
    coll = _local(root, "v4")
    tbl = embeddings._committed_table(embeddings._conn(coll))
    assert set(embeddings.VALIDITY_COLUMNS) <= set(tbl.schema.names)
    con = sqlite3.connect(root / "v4" / "state" / "manifest.sqlite")
    try:
        hashes = {r[0] for r in con.execute("SELECT chunk_id FROM chunk_hashes")}
        embedded = {r[0] for r in con.execute("SELECT uid FROM documents WHERE embed_done=1")}
    finally:
        con.close()
    assert len(embedded) == 2 and hashes and all(h.endswith(":4") for h in hashes)
    assert {h.rsplit(":", 2)[0] for h in hashes} == embedded, "the complete documents' hashes alone"
    assert got["totals"]["documents_embedded"] == 2


# ---- the dry run ----------------------------------------------------------------

def test_the_dry_run_writes_nothing_and_counts_what_the_run_does(move):
    root, *_ = move
    before = _files(root / "v3", root / "v4")
    dry = rechunk.rekey(root, "v3", "v4", apply=False, log=quiet)
    assert _files(root / "v3", root / "v4") == before, "a dry run writes nothing"
    assert not dry["apply"] and dry["rows_written"] == 0
    _out, real = _carried(root)
    for k in ("languages", "totals", "skipped", "first_error"):
        assert dry[k] == real[k], k


def test_the_dry_run_agrees_with_the_drift_tool(move):
    """tools/measure/legal_chunker_drift.py on the source, at version 4: the
    same documents, chunks, chunks to embed and chunks dropped, per language
    (the source is fully embedded)."""
    from tools.measure import legal_chunker_drift as drift
    root, *_ = move
    dry = rechunk.rekey(root, "v3", "v4", apply=False, log=quiet)
    got = drift.measure(_local(root, "v3"), to_version=4, window=2, log=quiet)
    assert set(got["languages"]) == set(dry["languages"])
    for lang, d in got["languages"].items():
        mine = dry["languages"][lang]
        assert (d["documents"], d["chunks_to"], d["chunks_to_embed"], d["chunks_dropped"]) == \
            (mine["documents"], mine["chunks"], mine["to_embed"], mine["dropped"]), lang


# ---- resumable, bounded ------------------------------------------------------------

class Kill(Exception):
    """The process killed between a window's write and its cursor."""


def test_a_resume_after_a_stop_gives_the_uninterrupted_result(move, tmp_path):
    """A stop at the first window boundary, then the run again: the same rows,
    bytes and marks as one run without a stop (the same fixture in another
    root)."""
    root, *_ = move
    other = tmp_path / "other"
    shutil.copytree(root, other, symlinks=True)
    _carried(other, window=1)
    stop = threading.Event()
    stop.set()
    _out, first = _carried(root, window=1, stop=stop)
    assert first["stopped"] and not first["done"] and first["totals"]["documents"] == 1
    _out, second = _carried(root, window=1)
    assert second["done"] and not second["stopped"]
    assert _table(root) == _table(other) and _marks(root) == _marks(other)
    assert second["totals"] == rechunk.rekey(other, "v3", "v4", apply=False, log=quiet)["totals"]


def test_a_kill_between_the_write_and_the_cursor_is_undone_on_the_resume(move, tmp_path, monkeypatch):
    """Invariant 2: the window's range is committed before its write; killed
    after the write and before its cursor, the next run deletes what the
    window wrote and writes it again — never a row twice."""
    root, *_ = move
    other = tmp_path / "other"
    shutil.copytree(root, other, symlinks=True)
    _carried(other, window=1)
    real = rechunk._mark
    calls = {"n": 0}

    def dies(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise Kill("killed after the window's rows, before its marks and cursor")
        return real(*a, **kw)
    monkeypatch.setattr(rechunk, "_mark", dies)
    with pytest.raises(Kill):
        _carried(root, window=1)
    con = sqlite3.connect(root / "v4" / "state" / "manifest.sqlite")
    state = json.loads(con.execute("SELECT value FROM control WHERE key=?", (rechunk.REKEY_KEY,)).fetchone()[0])
    con.close()
    assert state["pending"] is not None, "the range was committed before the write"
    monkeypatch.setattr(rechunk, "_mark", real)
    _out, got = _carried(root, window=1)
    assert got["done"] and got["vectors"] == got["rows_written"]
    assert _table(root) == _table(other) and _marks(root) == _marks(other)


def test_a_large_window_reads_and_writes_in_bounded_groups_and_stops_at_its_end(move, monkeypatch):
    """One window over every document, groups of at most 4 new chunks (a
    longer document alone): no read of the source and no write of the target
    holds more than its group; a stop asked during the window takes effect
    at its end, and the result is the default's."""
    root, *_ = move
    dry = rechunk.rekey(root, "v3", "v4", apply=False, log=quiet)
    longest = max(len(c) for c in _cuts(root, "v3").values())
    reads, writes = [], []
    held, write = embeddings.held_chunks, embeddings.write_pending
    stop = threading.Event()

    def held_spy(coll, books):
        reads.append(len(books))
        stop.set()                                   # asked mid-window
        return held(coll, books)

    def write_spy(docs, *a, **kw):
        writes.append(sum(len(d.rows) for d in docs))
        return write(docs, *a, **kw)
    monkeypatch.setattr(embeddings, "held_chunks", held_spy)
    monkeypatch.setattr(embeddings, "write_pending", write_spy)
    _out, got = _carried(root, window=10_000, batch_rows=4, stop=stop)
    assert len(reads) == 3 and max(reads) == 1, "a group of documents a read, never the window"
    assert writes and max(writes) <= max(4, longest)
    assert got["done"] and not got["stopped"], "one window: the stop waits for its end"
    assert got["totals"] == dry["totals"]


def test_the_cut_runs_on_the_parse_pool_at_the_profile_s_cores(move, monkeypatch):
    from app import cpu_limit, parallel
    root, *_ = move
    seen = {}

    def each_part(fn, tasks, *, limit=None):
        seen.update(fn=fn, limit=limit, tasks=len(tasks))
        return (fn(*t) for t in tasks)
    monkeypatch.setattr(parallel, "each_part", each_part)
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(6, "day"))
    rechunk.rekey(root, "v3", "v4", apply=False, window=1, log=quiet)
    assert seen == {"fn": rechunk.cut_window, "limit": 6, "tasks": 3}
    rechunk.rekey(root, "v3", "v4", apply=False, window=1, processes=2, log=quiet)
    assert seen["limit"] == 2, "fewer when asked, never more"
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(0, "quiet"))
    with pytest.raises(CodexError, match="no core"):
        rechunk.rekey(root, "v3", "v4", apply=False, log=quiet)


def test_a_window_task_pickles_as_the_pool_sends_it(move):
    """`parallel.each_part` sends its tasks to spawned children: the task
    and its answer pickle, and the answer holds no chunk text (a hash a
    chunk). A child of its own cannot cut here — conftest's stand-in for the
    pinned tokenizer is registered in this process only."""
    import pickle
    root, *_ = move
    task = (_local(root, "v3"), _local(root, "v4"), 0, 3)
    got = rechunk.cut_window(*pickle.loads(pickle.dumps(task)))
    assert pickle.loads(pickle.dumps(got)) == got and len(got["docs"]) == 3
    assert all(isinstance(h, bytes) and len(h) == 16 for d in got["docs"] for _i, _s, _c, h, _p in d["chunks"])


# ---- the refusals: each writes nothing ------------------------------------------------

def _refused(root, match, **kw):
    before = _files(root / "v3", root / "v4")
    with Locks(root, kw.pop("names", ("v3", "v4"))) if kw.pop("locked", True) else _nolock():
        with pytest.raises(CodexError, match=match):
            carry.carry(root, "v3", kw.pop("to", "v4"), copy=kw.pop("copy", True), rechunked=True,
                        apply=True, log=quiet, **kw)
    assert _files(root / "v3", root / "v4") == before, "nothing written"
    assert not (root / "v4" / "state" / "manifest.sqlite").exists()


class _nolock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


@pytest.mark.parametrize("over,match", [
    ({"parser": True}, "parser"),
    ({"layers": _layer(4, max_tokens=41)}, "more than its version"),
    ({"layers": _layer(3), "work": {"tokens": 5_000_001}}, "nothing to re-chunk"),
    ({"space": {**SPACE, "encoder": {**SPACE["encoder"], "weights": "sha256:" + "5e" * 32}}}, "encoder.weights"),
    ({"space": {**SPACE, "query": {**SPACE["query"], "instruction": "query: "}}}, "query.instruction"),
    ({"space": {**SPACE, "query": {"encoder": {"weights": "sha256:" + "5f" * 32}, "instruction": None}}},
     "query.encoder.weights"),
    ({"space": {**SPACE, "pooling": "mean"}}, "pooling"),
    ({"space": {**SPACE, "normalise": False}}, "normalise"),
    ({"space": {**SPACE, "dtype": "f32"}}, "dtype"),
    ({"space": {**SPACE, "ctx": 1024}}, "ctx"),
])
def test_anything_else_that_differs_is_refused_and_nothing_written(move, over, match):
    """The records but for the layers, the layers but for their version, and
    the whole function (`space_differences`: the epoch, the cut and the
    document instruction, which a fixture cannot make here, among them)."""
    from dataclasses import replace

    from app.hold.codex import Parser
    root, v3, _v4, _vectors = move
    if over.get("parser"):
        col.create(root, replace(col.read(root, "v4"), parser=Parser("gutenberg", 99)), name="x")
    else:
        layers_ = over.pop("layers", _layer(4))
        col.create_from_recipe(root, "gutenberg", recipe(layers=layers_, **over), platform_image=PLATFORM,
                               embed=fake_embed, embed_query=fake_query_embed, name="x", key=KEY, now=NOW)
    before = _files(root / "v3", root / "x")
    with Locks(root, ("v3", "x")):
        with pytest.raises(CodexError, match=match):
            carry.carry(root, "v3", "x", copy=True, rechunked=True, apply=True, log=quiet)
    with pytest.raises(CodexError, match=match):
        rechunk.rekey(root, "v3", "x", apply=False, log=quiet)
    assert _files(root / "v3", root / "x") == before


def test_a_target_that_is_not_empty_is_refused(move):
    root, *_ = move
    (root / "v4" / "lance").mkdir(parents=True, exist_ok=True)
    (root / "v4" / "lance" / "something").write_text("x")
    _refused(root, "not empty")


def test_a_source_table_of_another_space_is_refused(move):
    root, *_ = move
    con = sqlite3.connect(root / "v3" / "state" / "manifest.sqlite")
    con.execute("UPDATE space SET space_id='sha256:' || hex(randomblob(32))")
    con.commit()
    con.close()
    _refused(root, "pinned to")
    with pytest.raises(CodexError, match="pinned to"):
        rechunk.rekey(root, "v3", "v4", apply=False, log=quiet)


def test_without_both_write_locks_nothing_is_written(move):
    root, *_ = move
    _refused(root, "does not hold the write lock of v3, v4", locked=False)
    _refused(root, "does not hold the write lock of v4", names=("v3",))
    with pytest.raises(CodexError, match="does not hold the write lock"):
        rechunk.rekey(root, "v3", "v4", apply=True, log=quiet)


def test_another_holder_of_a_lock_is_not_this_process(move, monkeypatch):
    root, *_ = move
    import os
    with Locks(root):
        assert carry.held_here(lock_mod.state_dir(root, "v3"))
        monkeypatch.setattr(os, "getpid", lambda: 1)
        assert not carry.held_here(lock_mod.state_dir(root, "v3")), "the worker's lock is not ours"


def test_a_move_another_mode_or_a_re_key_before_its_carry_is_refused(move):
    root, *_ = move
    _refused(root, "copies", copy=False)
    for mode in ({"sister": True}, {"relayered": True}, {"rebuilt": True}, {"recut": True},
                 {"resliced": True}, {"unembedded": True}, {"keep": "producer:x"}):
        _refused(root, "layers' version alone", **mode)
    with Locks(root):
        with pytest.raises(CodexError, match="records no finished"):
            rechunk.rekey(root, "v3", "v4", apply=True, log=quiet)
