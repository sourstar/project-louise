"""`source_hash` on the vector table (09 §9.6, amended 2026-11-07, operator:
A; docs/notes/pubmed-updatefiles.md §10).

A pair layer's stored vector is kept for a chunk whose id and text are the
stored row's AND whose record's `content_hash` is the row's `source_hash`;
NULL never matches, a text layer keeps by text as before, and EMBED_VERSION
still decides whether anything is kept. `hold vectors source-hashes`
backfills a table written before the column, without the encoder,
resumably — and nothing hashed, signed, attested or shipped reads it.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa
import pytest

from app import db, embeddings, layers as layers_mod, vector_sources
from app.hold import land
from tests.hold.test_history_until_baseline import pubmed_root  # noqa: F401
from tests.test_embed_reuse import _pair, collection, encoder, pair_layer  # noqa: F401

H1, H2 = "11" * 16, "22" * 16
#: the test Codex's space (tests/hold/test_template.py SPACE)
DIM = 768


def _src(chunks, h):
    return layers_mod.with_source(chunks, h)


def _stored(coll) -> dict:
    tbl = embeddings._conn(coll).open_table(embeddings.TABLE)
    return {r["id"]: r for r in tbl.to_arrow().to_pylist()}


# ---------------------------------------------------------------- the keep

def test_a_pair_vector_is_kept_while_its_record_is_the_same(collection, encoder, pair_layer):
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection)
    assert encoder == [("T", "A")]
    (row,) = _stored(collection).values()
    assert row[embeddings.SOURCE_COLUMN] == bytes.fromhex(H1), "written in the vector's commit"
    version = embeddings._conn(collection).open_table(embeddings.TABLE).version
    encoder.clear()
    counts = embeddings.EmbedCounts()
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection, counts=counts)
    assert encoder == [] and (counts.embedded, counts.reused) == (0, 1)
    assert embeddings._conn(collection).open_table(embeddings.TABLE).version == version


def test_a_changed_abstract_is_encoded_again(collection, encoder, pair_layer):
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection)
    encoder.clear()
    embeddings.upsert_book(1, "en", _src(_pair("T", "A, corrected"), H2), collection)
    assert encoder == [("T", "A, corrected")]
    (row,) = _stored(collection).values()
    assert row[embeddings.SOURCE_COLUMN] == bytes.fromhex(H2)


def test_the_same_text_of_another_record_is_encoded_again(collection, encoder, pair_layer):
    """A record whose `content_hash` moved (a heading, a section the pair
    joins differently) is not this vector's record, whatever its text."""
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection)
    encoder.clear()
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H2), collection)
    assert encoder == [("T", "A")]


def test_null_never_matches(collection, encoder, pair_layer):
    embeddings.upsert_book(1, "en", _pair("T", "A"), collection)          # no source
    assert next(iter(_stored(collection).values()))[embeddings.SOURCE_COLUMN] is None
    encoder.clear()
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection)
    assert encoder == [("T", "A")], "a stored NULL is not this record"
    encoder.clear()
    embeddings.upsert_book(1, "en", _pair("T", "A"), collection)
    assert encoder == [("T", "A")], "a chunk without a source keeps nothing"


def test_one_text_two_pairs_never_shares_a_vector(collection, encoder, pair_layer):
    embeddings.upsert_book(1, "en", _src(_pair("A", "B\n\nC"), H1), collection)
    encoder.clear()
    embeddings.upsert_book(1, "en", _src(_pair("A\n\nB", "C"), H2), collection)
    assert encoder == [("A\n\nB", "C")]


def test_embed_version_still_decides(collection, encoder, pair_layer):
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection)
    encoder.clear()
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection, reuse=False)
    assert encoder == [("T", "A")], "vectors of another EMBED_VERSION are made again"


def test_the_batched_path_writes_the_source(collection, encoder, pair_layer):
    sink = embeddings.PendingWrites()
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection, sink=sink)
    embeddings.write_pending(sink.take([1]), collection)
    (row,) = _stored(collection).values()
    assert row[embeddings.SOURCE_COLUMN] == bytes.fromhex(H1)


def test_a_text_layer_keeps_by_text_as_before(collection, encoder):
    chunks = [{"id": "gutenberg:1:0:1", "seq": 0, "chapter": "", "text": "a"}]
    embeddings.upsert_book(1, "en", chunks, collection)
    encoder.clear()
    embeddings.upsert_book(1, "en", [dict(c) for c in chunks], collection)
    assert encoder == [], "no source needed for a text chunk"


# ---------------------------------------------------------------- an old table

def _old_table(coll, rows: int = 2, dim: int = 4) -> None:
    """A vector table as every node wrote it before the column."""
    d = lancedb.connect(str(coll.lance_dir))
    schema = pa.schema([pa.field("id", pa.string()), pa.field("book_id", pa.int64()),
                        pa.field("lang", pa.string()), pa.field("seq", pa.int64()),
                        pa.field("chapter", pa.string()), pa.field("text", pa.string()),
                        pa.field("vector", pa.list_(pa.float32(), dim))])
    d.create_table(embeddings.TABLE, schema=schema, data=[
        {"id": f"x:{100 + i}:0:1", "book_id": 100 + i, "lang": "en", "seq": 0, "chapter": "",
         "text": f"old {i}", "vector": [1.0, float(i), 0.0, 0.0]} for i in range(rows)])


def test_a_table_without_the_column_still_reads_and_writes(collection, encoder, pair_layer):
    Path(collection.lance_dir).mkdir(parents=True, exist_ok=True)
    _old_table(collection)
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection)
    tbl = embeddings._conn(collection).open_table(embeddings.TABLE)
    assert embeddings.SOURCE_COLUMN not in tbl.schema.names, "an old table keeps its schema"
    held = embeddings.held_chunks(collection, [1])
    assert {r[embeddings.SOURCE_COLUMN] for r in held.of(1).values()} == {None}
    encoder.clear()
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection)
    assert encoder == [("T", "A")], "no source held: re-encoded, never wrongly kept"
    # an import into it (a peer's bundle) drops the key and writes
    embeddings.upsert_chunks([{"id": "pubmed:2:0:1", "book_id": 2, "lang": "en", "seq": 0,
                               "chapter": "", "text": "x", "vector": [0.0, 1.0, 0.0, 0.0],
                               embeddings.SOURCE_COLUMN: bytes.fromhex(H2)}], collection)
    assert "pubmed:2:0:1" in embeddings.vectors_by_chunk_id(["pubmed:2:0:1"], collection)
    hits = embeddings._search_table(collection, [1.0, 0.0, 0.0, 0.0], 5, None, None, None)
    assert hits and all(embeddings.SOURCE_COLUMN not in h for h in hits)


def test_an_old_table_takes_the_batch_whole_and_writes_the_same_bytes(collection, tmp_path):
    """The old-schema write (every collection not backfilled) drops the
    column ONCE for the batch — one Arrow table, no copy per row — and what
    lands is byte for byte what the same rows without the key write (main's
    path: the list lancedb converts itself)."""
    from dataclasses import replace
    rng = np.random.default_rng(7)
    vecs = rng.standard_normal((40, 4)).astype(np.float16)

    def rows(with_source: bool) -> list[dict]:
        return [{"id": f"g:{i}:0:1", "book_id": i, "lang": "en", "seq": 0, "chapter": "",
                 "text": f"t{i}", "vector": vecs[i],
                 **({embeddings.SOURCE_COLUMN: bytes([i]) * 16 if i % 3 else None}
                    if with_source else {})} for i in range(40)]
    got = {}
    for name, with_source in (("old", False), ("new", True)):
        coll = replace(collection, lance_dir=tmp_path / name)
        coll.lance_dir.mkdir()
        lancedb.connect(str(coll.lance_dir)).create_table(embeddings.TABLE, schema=pa.schema([
            pa.field("id", pa.string()), pa.field("book_id", pa.int64()),
            pa.field("lang", pa.string()), pa.field("seq", pa.int64()),
            pa.field("chapter", pa.string()), pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float16(), 4))]))
        tbl = embeddings._conn(coll).open_table(embeddings.TABLE)
        shaped = embeddings._conform(tbl, rows(with_source))
        if with_source:
            assert isinstance(shaped, pa.Table), "one Arrow table for the batch, not a copy per row"
            assert set(shaped.schema.names) == set(tbl.schema.names)
            assert isinstance(embeddings.fit_source(tbl, rows(True)), pa.Table)
        else:
            assert isinstance(shaped, list), "rows without the key go as they are, as on main"
        embeddings.write_pending([embeddings.PendingDoc(1, rows(with_source), purge=False, seen=0)], coll)
        got[name] = embeddings._conn(coll).open_table(embeddings.TABLE).to_arrow().sort_by("id")
    assert got["new"].schema == got["old"].schema
    assert got["new"].equals(got["old"]), "the same bytes as the rows without the key"
    assert got["new"].column("vector").combine_chunks().flatten().to_numpy().tobytes() == \
        vecs[np.argsort([f"g:{i}:0:1" for i in range(40)], kind="stable")].tobytes()


def test_a_hit_never_carries_the_column(collection, encoder, pair_layer):
    embeddings.upsert_book(1, "en", _src(_pair("T", "A"), H1), collection)
    hits = embeddings._search_table(collection, [1.0, 0.0, 0.0, 0.0], 5, None, None, None)
    assert hits and all(embeddings.SOURCE_COLUMN not in h for h in hits)


# ---------------------------------------------------------------- the writers

def test_sixteen_zero_bytes_are_no_source():
    """A legacy-format table's NULL (`embeddings.NO_SOURCE`) never stands for
    a record, however it is spelled."""
    assert embeddings.source_bytes("00" * 16) is None and embeddings.source_bytes(bytes(16)) is None
    assert land._source_bytes("00" * 16) is None and land._source_bytes(H1) == bytes.fromhex(H1)
    at = pa.table({embeddings.SOURCE_COLUMN: pa.array([bytes(16), bytes.fromhex(H1), None],
                                                     embeddings.SOURCE_TYPE)})
    assert embeddings._sources_of(at) == [None, bytes.fromhex(H1), None]


def test_a_bundle_import_writes_the_possessed_records_hash():
    codex = type("C", (), {"space": type("S", (), {"dim": 2})()})()
    cut = {"pubmed:1:0:1": {"text": "t", "seq": 0, "chapter": "", "book_id": 1, "lang": "en",
                            "source": H1},
           "pubmed:2:0:1": {"text": "u", "seq": 0, "chapter": "", "book_id": 2, "lang": "en"}}
    rows, _ = land.rows_to_write(codex, [("pubmed:1:0:1", [1.0, 0.0]), ("pubmed:2:0:1", [0.0, 1.0])], cut)
    assert [r[land.SOURCE_COLUMN] for r in rows] == [bytes.fromhex(H1), None]
    assert land.SOURCE_COLUMN == embeddings.SOURCE_COLUMN


def test_the_cut_of_a_held_record_names_its_hash():
    from tests.hold.conftest import pubmed
    rec = {"uid": "pubmed:7", "native_id": "7", "lang": "en", "content_hash": H1,
           "sections": [["title", "T"], ["abstract", "A"]]}
    cut = land.cut(pubmed(), [rec])
    assert {c["source"] for c in cut.values()} == {H1}
    from app.hold import vectors_build
    assert {c["source"] for c in vectors_build.chunks_of(pubmed(), [rec])} == {H1}


def test_a_pair_parse_gives_the_records_build_hash(tmp_path):
    """The embed stage's parse hashes the record as `hold records build`
    does (09 §9.5), so a backfilled row and a freshly embedded one agree."""
    from app import plugin_io, record_source
    from app.hold import records as hold_records
    from app.plugins.pubmed import SOURCE
    from tests.test_pubmed_updatefiles import _article, _container
    c = _container(tmp_path / "pubmed26n0001.xml.gz", _article(31000001, "Widgets."))
    row = {"uid": "pubmed:31000001", "native_id": "31000001", "path": f"{c}::0"}
    sections, h = record_source.parsed_with_source(SOURCE, row, pair=True)
    doc = plugin_io.canonical_doc(SOURCE, row)
    assert h == hold_records.content_hash(doc.title or "", [[a or "", b or ""] for a, b in doc.sections])
    assert record_source.parsed_with_source(SOURCE, row, pair=False) == (
        plugin_io.sections(SOURCE, row), None), "any other parse is what it was"
    assert layers_mod.with_source([{"id": "a"}], None) == [{"id": "a"}]


# ---------------------------------------------------------------- the backfill, the invariants

@pytest.fixture
def held(tmp_path, pubmed_root):
    return _held(tmp_path, pubmed_root)


@pytest.fixture
def held_big(tmp_path, pubmed_root):
    """`held` with long documents' rows first: a 2.x fragment of 6,000+ rows."""
    return _held(tmp_path, pubmed_root, big=True)


def _held(tmp_path, pubmed_root, *, legacy: bool = False, big: bool = False):
    """A pubmed collection: SHARD, snapshot 2026-08, manifest, and a vector
    table as it was written before the column. Four records:

      31000001  embedded, unchanged since the build       -> its hash
      31000002  embedded, its origin bytes moved since    -> NULL
      31000003  not embedded (embed_done = 0)             -> NULL
      31000004  embedded, unchanged                       -> its hash
    """
    from tests.hold.test_history_until_baseline import _build_and_freeze, _source
    from tests.test_pubmed_updatefiles import _article, _container
    root = pubmed_root
    corpus = tmp_path / "corpus"
    _container(corpus / "pubmed26n0001.xml.gz", _article(31000001, "One."), _article(31000002, "Two."))
    _container(corpus / "pubmed26n0002.xml.gz", _article(31000003, "Three."), _article(31000004, "Four."))
    snap, _ = _build_and_freeze(root, corpus, "2026-08")
    local = _source(root, corpus)
    con = db.connect(local)
    con.execute("UPDATE documents SET embed_done=1, rdf_done=1, fts_done=1")
    con.execute("UPDATE documents SET txt_hash='ff' WHERE uid='pubmed:31000002'")
    con.execute("UPDATE documents SET embed_done=0 WHERE uid='pubmed:31000003'")
    con.commit()
    from app.hold import collection as col
    from app.pack import records as pack_records
    shard_dir = col.shard_dir_of(root, "pubmed")
    records = [r for e in snap.shards for r in pack_records.iter_records(shard_dir / e.path)]
    rows = []
    for rec in records:
        row = {"uid": rec["uid"], "native_id": rec["native_id"]}
        for c in layers_mod.chunks_for_document(local, row, [tuple(s) for s in rec["sections"]]):
            seed = int.from_bytes(hashlib.sha256(c["id"].encode()).digest()[:4], "big")
            v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
            rows.append({"id": c["id"], "book_id": int(rec["native_id"]), "lang": "en",
                         "seq": int(c["seq"]), "chapter": c["chapter"] or "", "text": c["text"],
                         "vector": (v / np.linalg.norm(v)).tolist()})
    Path(local.lance_dir).mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("id", pa.string()), pa.field("book_id", pa.int64()),
        pa.field("lang", pa.string()), pa.field("seq", pa.int64()),
        pa.field("chapter", pa.string()), pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), DIM))])
    hashes = {r["uid"]: r["content_hash"] for r in records}
    if not legacy:
        data = pa.Table.from_pylist(_filler(LONG, 2000) + rows if big else rows, schema=schema)
        lancedb.connect(str(local.lance_dir)).create_table(embeddings.TABLE, data=data, schema=schema)
        return root, local, con, snap, shard_dir, hashes, [r["id"] for r in rows]
    ids = _legacy_table(local, rows, schema)
    return root, local, con, snap, shard_dir, hashes, ids


#: filler documents beside the four records: three of 2,000 rows (a long
#: document's chunks, contiguous in one fragment) and 400 of 4
LONG, SHORT = (1, 2, 3), range(10, 410)


def _filler(books, per: int) -> list[dict]:
    out = []
    for b in books:
        for q in range(per):
            v = np.random.default_rng(b * 10_000 + q).standard_normal(DIM).astype(np.float32)
            out.append({"id": f"pubmed:{b}:{q}:1", "book_id": b, "lang": "en", "seq": q,
                        "chapter": "", "text": f"filler {b} {q}", "vector": v / np.linalg.norm(v)})
    return out


def _legacy_table(local, rows, schema) -> list[str]:
    """The vector table as pubmed's is on the node: the LEGACY file format
    (0.1), no stable row ids, several fragments, a vector index and the
    book_id index — and deletions as the embed stage's delete-then-insert
    leaves them: a long document's 2,000 contiguous rows (more than a row
    group: what Lance's merge refuses) and 20 short documents scattered,
    each written again into a fragment of its own."""
    import lance
    from app import vector_index
    path = str(Path(local.lance_dir) / f"{embeddings.TABLE}.lance")
    first = pa.Table.from_pylist(rows + _filler(LONG, 2000), schema=schema)
    lance.write_dataset(first, path, mode="create", data_storage_version="legacy",
                        max_rows_per_group=1024)
    lance.write_dataset(pa.Table.from_pylist(_filler(SHORT, 4), schema=schema), path, mode="append")
    ds = lance.dataset(path)
    again = [2] + list(range(20, 400, 19))
    ds.delete(f"book_id IN ({','.join(map(str, again))})")
    lance.write_dataset(pa.Table.from_pylist(_filler([2], 2000) + _filler(again[1:], 4), schema=schema),
                        path, mode="append")
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    vector_index.build_table(tbl, partitions=8)
    vector_index.build_scalar_table(tbl)
    got = lance.dataset(path)
    assert str(got.data_storage_version) == "0.1" and not got.has_stable_row_ids
    assert sum(1 for f in got.get_fragments() if f.num_deletions) == 2
    return sorted(tbl.to_arrow().column("id").to_pylist())


def _tree(d: Path) -> dict[str, bytes]:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def _what_travels(root, local, snap, ids, tmp: Path) -> dict:
    """Every digest-, root-, bundle- and search-facing output of the table."""
    from app.hold import export as exp
    from tests.hold.conftest import KEY
    out = {}
    for e in snap.shards:
        dest = tmp / f"bundle-{e.slice}-{e.n}"
        exp.export(root, "pubmed", shard=f"{e.slice}/{e.n:05d}", dest=dest,
                   vectors_for=lambda w: embeddings.vectors_by_chunk_id(w, collection=local),
                   embed=lambda texts: [[1.0] + [0.0] * (DIM - 1) for _ in texts],
                   key=KEY, created_at=1756713600)
        out[f"bundle {e.slice}/{e.n}"] = _tree(dest)
    got = embeddings.vectors_by_chunk_id(ids, collection=local)
    out["vectors"] = {k: np.asarray(v, dtype=np.float32).tobytes() for k, v in sorted(got.items())}
    hits = embeddings._search_table(local, [1.0] + [0.0] * (DIM - 1), 10, None, None, None)
    out["search"] = json.dumps([{k: (np.asarray(v).tolist() if k == "vector" else v)
                                 for k, v in sorted(h.items())} for h in hits], sort_keys=True)
    return out


def test_the_backfill_fills_what_it_can_prove_and_moves_nothing_that_travels(held, tmp_path):
    from app.hold import collection as col
    root, local, con, snap, shard_dir, hashes, ids = held
    codex_before = col.read(root, "pubmed")
    shard_before = _tree(shard_dir)
    before = _what_travels(root, local, snap, ids, tmp_path / "before")

    survey = vector_sources.survey(root, "pubmed", local, con)
    assert (survey["rows"], survey["without_column"], survey["snapshot"]) == (4, 1, "2026-08")
    state = tmp_path / "state-work"
    out = vector_sources.apply(root, "pubmed", local, con, state_dir=state)
    assert out["added"] == 1 and out["rows_with_source"] == 2
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    got = {r["book_id"]: r[embeddings.SOURCE_COLUMN] for r in tbl.to_arrow().to_pylist()}
    assert got == {31000001: bytes.fromhex(hashes["pubmed:31000001"]), 31000002: None,
                   31000003: None, 31000004: bytes.fromhex(hashes["pubmed:31000004"])}
    assert not any(state.iterdir()), "the work files go"

    # nothing hashed, signed, attested, shipped or searched reads it
    assert _what_travels(root, local, snap, ids, tmp_path / "after") == before
    assert _tree(shard_dir) == shard_before, "the SHARD — every root `hold check` reads"
    assert col.read(root, "pubmed").space_id == codex_before.space_id

    # idempotent
    assert vector_sources.apply(root, "pubmed", local, con, state_dir=state)["unchanged"] is True


def test_a_stopped_backfill_resumes_where_it_was(held, tmp_path, monkeypatch):
    root, local, con, snap, shard_dir, hashes, ids = held
    state = tmp_path / "state-work"
    real = vector_sources._add_column

    def killed(*a, **kw):
        raise KeyboardInterrupt("killed (test)")
    monkeypatch.setattr(vector_sources, "_add_column", killed)
    with pytest.raises(KeyboardInterrupt):
        vector_sources.apply(root, "pubmed", local, con, state_dir=state)
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    assert embeddings.SOURCE_COLUMN not in tbl.schema.names, "the table as it was"
    work = vector_sources._work(state)
    assert work.execute("SELECT COUNT(*) FROM done").fetchone()[0] == len(snap.shards)
    work.close()
    # the rerun reads no part again, and finishes
    monkeypatch.setattr(vector_sources, "_add_column", real)
    monkeypatch.setattr(vector_sources, "part_sources",
                        lambda *_a: pytest.fail("a part read twice"))
    out = vector_sources.apply(root, "pubmed", local, con, state_dir=state)
    assert out["rows_with_source"] == 2


def test_a_stop_between_parts_keeps_the_parts_done(held, tmp_path):
    root, local, con, snap, shard_dir, hashes, ids = held
    assert len(snap.shards) >= 2
    state = tmp_path / "state-work"
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        vector_sources.apply(root, "pubmed", local, con, state_dir=state, stop=stop)
    out = vector_sources.apply(root, "pubmed", local, con, state_dir=state)
    assert out["rows_with_source"] == 2


def test_a_kill_inside_lances_pass_leaves_the_table_and_the_rerun_completes(held, tmp_path, monkeypatch):
    """pylance 9.0.1's add_columns commits once, at its end: a pass that
    dies inside the batch function leaves the table at its version."""
    import lance
    root, local, con, snap, shard_dir, hashes, ids = held
    state = tmp_path / "state-work"
    real = lance.batch_udf

    def dying(**kw):
        wrap = real(**kw)

        def deco(fn):
            def boom(batch):
                raise RuntimeError("killed inside the pass (test)")
            return wrap(boom)
        return deco
    monkeypatch.setattr(lance, "batch_udf", dying)
    version = embeddings._conn(local).open_table(embeddings.TABLE).version
    with pytest.raises(Exception):
        vector_sources.apply(root, "pubmed", local, con, state_dir=state)
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    assert tbl.version == version and embeddings.SOURCE_COLUMN not in tbl.schema.names
    monkeypatch.setattr(lance, "batch_udf", real)
    assert vector_sources.apply(root, "pubmed", local, con, state_dir=state)["rows_with_source"] == 2


# ---------------------------------------------------------------- a table Lance cannot merge into

@pytest.fixture
def held_legacy(tmp_path, pubmed_root):
    return _held(tmp_path, pubmed_root, legacy=True)


def _sources(local, con, root):
    """phase 1's arrays, as `apply` loads them, for a direct merge"""
    from app.hold import collection as col
    from app.share import possession
    snap = possession.current_snapshot(Path(root), "pubmed")
    shard_dir = col.shard_dir_of(Path(root), "pubmed")
    kept = sorted(k for e in snap.shards
                  for k in vector_sources._judge(con, vector_sources.part_sources(str(shard_dir / e.path))))
    keys = np.array([k for k, _ in kept], dtype=np.int64)
    vals = np.frombuffer(b"".join(h for _, h in kept), dtype=np.uint8).reshape(len(kept), 16)
    return keys, vals


def _rows_by_id(local) -> dict:
    """Every row, every stored column but the new one, by chunk id."""
    at = embeddings._conn(local).open_table(embeddings.TABLE).to_arrow()
    cols = [c for c in at.schema.names if c != embeddings.SOURCE_COLUMN]
    out = {}
    for r in at.select(cols).to_pylist():
        r["vector"] = np.asarray(r["vector"], dtype=np.float32).tobytes()
        out[r["id"]] = r
    return out


QUERIES = [np.random.default_rng(100 + i).standard_normal(DIM).astype(np.float32) for i in range(8)]


def _answers(local) -> list:
    """The fixed query set through the search path (the index's nprobes and
    refine, `vector_index.query`), ids and distances."""
    return [[(h["id"], h["_distance"]) for h in
             embeddings._search_table(local, q / np.linalg.norm(q), 10, None, None, None)]
            for q in QUERIES]


def _plans(local) -> tuple[str, str]:
    from app import vector_index
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    ann = vector_index.query(tbl.search(QUERIES[0]), local).limit(10).explain_plan(True)
    scalar = tbl.search().where("book_id IN (1, 2, 31000001)", prefilter=True).limit(10).explain_plan(True)
    return ann, scalar


def _deleted(local) -> int:
    ds = embeddings._conn(local).open_table(embeddings.TABLE).to_lance()
    return sum(1 for f in ds.get_fragments() if f.num_deletions)


def test_a_legacy_table_with_deletions_refuses_the_merge(held_legacy):
    """THE NODE'S DEFECT, reproduced (2026-11-07): Lance's add_columns on a
    legacy-format table whose deletions empty a row group."""
    from app import vector_index
    root, local, con, *_ = held_legacy
    version = embeddings._conn(local).open_table(embeddings.TABLE).version
    with pytest.raises(OSError, match=vector_index.MERGE_REFUSED):
        embeddings.add_source_column(local, *_sources(local, con, root))
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    assert tbl.version == version and embeddings.SOURCE_COLUMN not in tbl.schema.names


def test_the_dry_run_says_the_compaction_and_what_it_rewrites(held_legacy):
    root, local, con, *_ = held_legacy
    out = vector_sources.survey(root, "pubmed", local, con)
    (t,) = out["tables"]
    c = t["compaction"]
    assert out["compaction_needed"] and c["needed"] and c["format"] == "0.1"
    assert c["fragments_with_deletions"] == 2 and c["deleted_rows"] == 2000 + 20 * 4
    assert c["rewrites_fragments"] >= 2 and c["rewrites_rows"] >= 2000 and c["room"]
    assert c["remaps_index_gib"] >= 0 and not c["stable_row_ids"]
    assert _deleted(local) == 2, "a dry run writes nothing"


def test_the_compacting_backfill_changes_nothing_that_matters(held_legacy, tmp_path):
    """Compaction, then the merge: the vectors byte for byte by chunk id,
    the search answers on a fixed query set, the vector index and the
    book_id index still used (the plans, the index statistics), and every
    bundle byte, the SHARD and space_id as they were."""
    from app import vector_index
    from app.hold import collection as col
    root, local, con, snap, shard_dir, hashes, ids = held_legacy
    rows_before, answers_before = _rows_by_id(local), _answers(local)
    travels_before = _what_travels(root, local, snap, ids, tmp_path / "before")
    shard_before, space_before = _tree(shard_dir), col.read(root, "pubmed").space_id
    ann, scalar = _plans(local)
    # the plans name the index each read goes through
    assert "ANNIvfPartition" in ann and "book_id_idx(BTree)" in scalar, (ann, scalar)
    assert vector_index.stats(local)["unindexed_rows"] == 0

    out = vector_sources.apply(root, "pubmed", local, con, state_dir=tmp_path / "work")
    assert out["added"] == 1 and out["rows_with_source"] == 2
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    assert embeddings.SOURCE_COLUMN in tbl.schema.names and _deleted(local) == 0

    # the legacy format stores a NULL as 16 zero bytes: read as NULL
    held = embeddings.held_chunks(local, [1, 31000001, 31000002]).by_book
    assert {r[embeddings.SOURCE_COLUMN] for r in held[1].values()} == {None}
    assert {r[embeddings.SOURCE_COLUMN] for r in held[31000002].values()} == {None}
    assert {r[embeddings.SOURCE_COLUMN] for r in held[31000001].values()} == \
        {bytes.fromhex(hashes["pubmed:31000001"])}
    assert _rows_by_id(local) == rows_before, "every vector, text and column, by chunk id"
    assert _answers(local) == answers_before, "the same answers, the same distances"
    ann, scalar = _plans(local)
    assert "ANNIvfPartition" in ann and "book_id_idx(BTree)" in scalar, \
        f"the indexes answer, no flat scan: {ann}\n{scalar}"
    st = vector_index.stats(local)
    assert st["indexed"] and st["unindexed_rows"] == 0 and st["book_id_indexed"]
    assert _what_travels(root, local, snap, ids, tmp_path / "after") == travels_before
    assert _tree(shard_dir) == shard_before and col.read(root, "pubmed").space_id == space_before


def test_a_stop_after_the_compaction_reruns_into_the_merge_alone(held_legacy, tmp_path, monkeypatch):
    from app import vector_index
    root, local, con, *_ = held_legacy
    state = tmp_path / "work"
    real = vector_sources._add_column

    def stopped(*a, **kw):
        raise KeyboardInterrupt("stopped after the compaction (test)")
    monkeypatch.setattr(vector_sources, "_add_column", stopped)
    with pytest.raises(KeyboardInterrupt):
        vector_sources.apply(root, "pubmed", local, con, state_dir=state)
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    assert _deleted(local) == 0 and embeddings.SOURCE_COLUMN not in tbl.schema.names
    # the rerun: no part read again, no compaction, the merge
    monkeypatch.setattr(vector_sources, "_add_column", real)
    monkeypatch.setattr(vector_sources, "part_sources", lambda *_a: pytest.fail("phase 1 redone"))
    monkeypatch.setattr(vector_index, "materialize_deletions",
                        lambda *a, **kw: pytest.fail("compacted twice"))
    assert vector_sources.apply(root, "pubmed", local, con, state_dir=state)["rows_with_source"] == 2
    assert not list(state.glob("*")), "the work files go"


def test_the_add_columns_checkpoint_is_the_tables_at_its_version(held, tmp_path):
    root, local, con, *_ = held
    a = vector_sources._checkpoint(tmp_path, local)
    embeddings.delete_book(31000004, local)                 # any commit moves it
    b = vector_sources._checkpoint(tmp_path, local)
    assert a != b and a.name.startswith("source-hashes.lance.v")
    (tmp_path / vector_sources.CHECKPOINT).write_text("")    # the name the node's first run left
    a.write_text("")
    vector_sources._clean(tmp_path)
    assert not list(tmp_path.glob("source-hashes*"))


def _with_a_deletion(local):
    """A later-format table with a document deleted and written again."""
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    row = tbl.search().where("book_id = 31000004").limit(10).to_arrow().to_pylist()
    tbl.delete("book_id = 31000004")
    tbl.add([{k: v for k, v in r.items() if not k.startswith("_")} for r in row])
    assert _deleted(local) == 1


def _with_a_deleted_head(local, rows: int):
    """A later-format table whose first fragment's first `rows` rows are
    deleted, as a document's delete-then-insert leaves them (pubmed on the
    node, 2026-11-07, format 2.1)."""
    import lance
    ds = embeddings._conn(local).open_table(embeddings.TABLE).to_lance()
    first = ds.get_fragments()[0]
    assert first.physical_rows > rows
    first_rows = first.to_table(columns=["id"], with_row_address=True)
    gone = [r["id"] for r in first_rows.to_pylist()
            if (r["_rowaddr"] & 0xFFFFFFFF) < rows]
    ids = ", ".join("'" + i.replace("'", "''") + "'" for i in gone)
    lance.dataset(ds.uri).delete(f"id IN ({ids})")
    return gone


def test_a_later_format_with_a_deleted_fragment_head_is_merged_in_longer_batches(
        held_big, tmp_path, monkeypatch):
    """THE NODE'S DEFECT, as it is (2026-11-07): pubmed is format 2.1, and
    Lance refuses the merge when a fragment's first read batch is all
    deleted. The planner reads the longest deleted head and the merge reads
    longer batches: no compaction, nothing rewritten, every row given its
    source."""
    from app import vector_index
    root, local, con, *_ = held_big
    _with_a_deleted_head(local, 1500)
    plan = vector_index.deletions_plan(local)
    assert plan["format"].startswith("2.") and plan["needed"] is False
    assert plan["longest_deleted_head"] >= 1500 and plan["merge_batch_rows"] == 2048
    # Lance's own default refuses it: the defect is real at 1,024
    with pytest.raises(OSError, match=vector_index.MERGE_REFUSED):
        embeddings.add_source_column(local, *_sources(local, con, root), batch_size=1024)
    survey = vector_sources.survey(root, "pubmed", local, con)
    assert survey["tables"][0]["compaction"]["merge_batch_rows"] == 2048
    fragments = [f.fragment_id for f in
                 embeddings._conn(local).open_table(embeddings.TABLE).to_lance().get_fragments()]
    monkeypatch.setattr(vector_index, "materialize_deletions",
                        lambda *a, **kw: pytest.fail("a v2 table rewritten for nothing"))
    out = vector_sources.apply(root, "pubmed", local, con, state_dir=tmp_path / "w")
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    assert embeddings.SOURCE_COLUMN in tbl.schema.names and out["rows_with_source"] > 0
    assert [f.fragment_id for f in tbl.to_lance().get_fragments()] == fragments, "no fragment rewritten"


def test_merge_batch_rows():
    from app import vector_index as vi
    assert [vi.merge_batch_rows(h) for h in (0, 1023, 1024, 1500, 4095, 4096)] == \
        [1024, 1024, 2048, 2048, 4096, 8192]


def test_a_later_format_merges_beside_its_deletions(held, tmp_path, monkeypatch):
    from app import vector_index
    root, local, con, *_ = held
    _with_a_deletion(local)
    assert vector_index.deletions_plan(local)["needed"] is False
    monkeypatch.setattr(vector_index, "materialize_deletions",
                        lambda *a, **kw: pytest.fail("a v2 table rewritten for nothing"))
    assert vector_sources.apply(root, "pubmed", local, con, state_dir=tmp_path / "w")["rows_with_source"] == 2
    assert _deleted(local) == 1, "its fragments as they were"


def test_a_refused_merge_is_compacted_and_tried_once_more(held, tmp_path, monkeypatch):
    from app import vector_index
    root, local, con, *_ = held
    _with_a_deletion(local)
    real, calls = vector_sources._add_column, []

    def refused_once(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(f"Not supported: {vector_index.MERGE_REFUSED}, run compaction (test)")
        return real(*a, **kw)
    monkeypatch.setattr(vector_sources, "_add_column", refused_once)
    assert vector_sources.apply(root, "pubmed", local, con, state_dir=tmp_path / "w")["rows_with_source"] == 2
    assert len(calls) == 2 and _deleted(local) == 0


def test_no_room_for_the_compaction_writes_nothing(held_legacy, tmp_path, monkeypatch, capsys):
    from app import louise, node_state, vector_index
    root, local, con, *_ = held_legacy
    version = embeddings._conn(local).open_table(embeddings.TABLE).version
    monkeypatch.setattr(vector_index, "free_bytes", lambda c: 1024)
    node_state.set_paused("pubmed", True, by="test")
    assert louise.main(["hold", "vectors", "source-hashes", "pubmed", "--root", str(root),
                        "--apply"]) == 2
    assert "GiB needed" in capsys.readouterr().err
    assert embeddings._conn(local).open_table(embeddings.TABLE).version == version


def test_the_command_is_a_dry_run_and_asks_for_a_pause(held, capsys):
    from app import louise, node_state
    root, local, con, snap, shard_dir, hashes, ids = held
    assert louise.main(["hold", "vectors", "source-hashes", "pubmed", "--root", str(root)]) == 0
    said = json.loads(capsys.readouterr().out)
    assert said["dry_run"] and said["without_column"] == 1
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    assert embeddings.SOURCE_COLUMN not in tbl.schema.names, "a dry run writes nothing"
    node_state.set_worker_state("running", by="test")
    assert louise.main(["hold", "vectors", "source-hashes", "pubmed", "--root", str(root),
                        "--apply"]) == 2, "not paused: refused"
    node_state.set_paused("pubmed", True, by="test")
    assert louise.main(["hold", "vectors", "source-hashes", "pubmed", "--root", str(root),
                        "--apply"]) == 0
    tbl = embeddings._conn(local).open_table(embeddings.TABLE)
    assert embeddings.SOURCE_COLUMN in tbl.schema.names
