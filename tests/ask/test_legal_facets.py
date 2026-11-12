"""The four legal plugins' facets (05 §5.7; legal-chunking-retrieval.md §4
R2, operator's default A, 2026-11-12): status, act type, code or
collection, year, and the EU's subject matter — each a FILTER the reader
asks for, never a weight (invariant 4). Counted here on the records the
plugins parse from their real fixtures (tests/fixtures/<plugin>), read out
of the record parts as the index maintainer reads them
(app/ask/fieldvalues.py), and a search on a fixture collection restricted
by one end to end."""
from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import pytest

from app import collections as collections_mod, db, fts, plugin_io, query_service
from app.ask import facets, fieldvalues, filters
from app.hold import collection as col
from app.hold import template
from app.plugins.base import DiscoverState
from tests.ask.test_ranges import a_snapshot
from tests.deposit.test_deposit_identity import node  # noqa: F401
from tests.hold.conftest import pubmed


def _record(doc, native_id: str) -> dict:
    return {"uid": doc.uid, "native_id": native_id, "lang": doc.language, "title": doc.title,
            "creators": list(doc.creators), "date": doc.date, "subjects": list(doc.subjects),
            "keys": list(doc.keys), "sections": [list(s) for s in doc.sections], "extra": doc.extra,
            "origin": "fixture", "content_hash": "0" * 32, "origin_hash": "1" * 32}


def records_of(source, root: Path) -> list[dict]:
    out = []
    for ref in source.discover(DiscoverState(corpus_root=root)):
        if ref.error is None and ref.scope == "document":
            doc = plugin_io.canonical_doc(source, {"native_id": ref.native_id, "path": ref.locator})
            out.append(_record(doc, ref.native_id))
    return out


def _part(shard_dir: Path, records: list[dict]):
    from app.hold import shard as shard_mod
    from app.hold import snapshot as sn
    from app.pack import records as pack_records
    rel = shard_mod.shard_path("all", 1)
    info = pack_records.write_shard(shard_dir / rel, records)
    data = (shard_dir / rel).read_bytes()
    nids = sorted(int(r["native_id"]) for r in records)
    return sn.Shard(slice="all", n=1, root=shard_mod.pieces_root(data), records_root="sha256:" + "11" * 32,
                    first_id=str(nids[0]), last_id=str(nids[-1]), records=info.records, chunks=1,
                    chunks_root="sha256:" + "22" * 32, tokens=512, bytes=len(data))


def counted(source, records: list[dict], tmp_path: Path) -> dict[str, dict]:
    """Every facet the plugin declares, built from one part holding
    `records`: `{field: values}` (`bounds` for a range)."""
    codex = pubmed(facets=template.default_facets(source))
    snap = a_snapshot(codex, [_part(tmp_path / "shard", records)])
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE documents(native_id TEXT, lang TEXT, authors TEXT, subjects TEXT, title TEXT, "
                "in_scope INTEGER)")
    con.executemany("INSERT INTO documents VALUES(?,?,?,?,?,1)",
                    [(r["native_id"], r["lang"], "", "|".join(r["subjects"]), r["title"]) for r in records])
    idx = tmp_path / "index"
    built = facets.build(con, codex, idx, snapshot_id="2026-08", shard_dir=tmp_path / "shard", snapshot=snap)
    assert built == [f.field for f in codex.facets], "every declared facet is built"
    out = {}
    for f in codex.facets:
        got = facets.cached(idx, f.field, snapshot_id="2026-08")
        assert got["postings"]["built"] is True, (f.field, got)
        out[f.field] = got["bounds"] if f.kind == "range" else got["values"]
    return out


# ---- the four plugins, on their fixtures ------------------------------------------------------------

def test_normattiva(tmp_path):
    import tests.test_normattiva as t
    from app.plugins.normattiva import SOURCE, source as nsrc
    nsrc._zip_cache.clear()
    r = tmp_path / "normattiva"
    t._zip(r / "akn/M/leggi-costituzionali.zip", {**t.SARD_MEMBERS, t.LC_O: t.LC67, t.LC_V: t.LC67})
    t._zip(r / "akn/M/codici.zip", {t.M306: base64.b64encode(t.L306), t.MBUILT: t.BUILT})
    t._zip(r / "akn/M/regi-decreti.zip", {t.RD206_R: t.RD206_REPEALED, t.RD7_V1: t.BUILT, t.RD7_V2: t.BUILT,
                                          t.RD7_O: t.BUILT.replace(b"25/01/2020", b"25/01/1930")})
    t._zip(r / "akn/O/atti-normativi-abrogati-in-originale.zip", {t.LC_O: t.LC67, t.RD206_O: t.BUILT})
    got = counted(SOURCE, records_of(SOURCE, r), tmp_path)
    assert got == {
        "date": ["1861-03-17", "2020-01-02"],
        "extra.collection": {"leggi-costituzionali": 5, "regi-decreti": 4, "codici": 2,
                             "atti-normativi-abrogati-in-originale": 1},
        "extra.doc_type": {"DECRETO": 5, "LEGGE COSTITUZIONALE": 5, "LEGGE": 1, "REGIO DECRETO": 1},
        "extra.in_force": {"false": 2},
    }, "R.D. 206/1861's repeal state and its original from the repealed-acts zip: the origin's word only"


def test_legi(tmp_path):
    from app.plugins.legi import SOURCE, pack
    from tests.legi_fixtures import REAL, tarball
    pack.reset_caches()
    try:
        tarball(tmp_path / "legi" / "Freemium_legi_global_20250713-140000.tar.gz", REAL)
        got = counted(SOURCE, records_of(SOURCE, tmp_path / "legi"), tmp_path)
    finally:
        pack.reset_caches()
    assert got == {
        "date": [None, None],
        "extra.eli_title": {"Code des instruments monétaires et des médailles": 3,
                            "Code de l'action sociale et des familles": 1},
        "extra.eli_type_document": {"CODE": 5},
        "extra.in_force": {"false": 1, "true": 1},
        "extra.version_from": ["1952-06-29", "2004-10-26"],
    }, "a code has no DATE_TEXTE (DILA's placeholder): its articles' years are their versions' starts"


def test_eurlex(tmp_path):
    import tests.test_eurlex as t
    from app.plugins.eurlex import SOURCE, source as src
    root = t.build_corpus(tmp_path / "eurlex")
    force = b'<RESOURCE_LEGAL_IN-FORCE type="data"><VALUE>%s</VALUE></RESOURCE_LEGAL_IN-FORCE>'
    for celex, said in ((t.ACT, b"true"), (t.DIR, b"false")):
        p = root / src.unit_rel("en", celex) / src.NOTICE
        p.write_bytes(p.read_bytes().replace(b"<WORK>", b"<WORK>" + force % said, 1))
    got = counted(SOURCE, records_of(SOURCE, root), tmp_path)
    subjects = got.pop("subjects")
    assert got == {
        "date": ["2016-06-08", "2019-04-17"],
        "extra.in_force": {"true": 1, "false": 1},
        "extra.resource_type": {"PC": 2, "R": 1},
        "extra.sector": {"5": 2, "0": 1},
    }, "the consolidation's notice states no force: none is guessed"
    assert subjects["plastics"] == 2 and subjects["Environment"] == 2, "EuroVoc's concept and its domain"
    assert subjects["Waste management and clean technology"] == 2, "the directory code's label"


def test_neuris(tmp_path):
    import tests.neuris_support as t
    from app.plugins.neuris import SOURCE
    got = counted(SOURCE, records_of(SOURCE, t.corpus(tmp_path / "neuris")), tmp_path)
    assert got == {
        "date": ["1991-01-01", "2025-05-05"],
        "extra.abbreviation": {"RisAbk": 3, "MFT": 1, "FrSaftErfrischV": 1, "EmptyBodyTestG": 1,
                               "GeGuGe 2025": 1},
        "extra.eli_type_document": {"verordnung": 1},
        "extra.in_force": {"true": 7},
        "extra.legal_force": {"InForce": 7},
    }


def test_each_legal_plugin_declares_its_filters_and_never_a_weight():
    from app.plugins.eurlex import SOURCE as eu
    from app.plugins.legi import SOURCE as fr
    from app.plugins.neuris import SOURCE as de
    from app.plugins.normattiva import SOURCE as it
    for s in (it, fr, eu, de):
        fields = [f.field for f in template.default_facets(s)]
        assert "date" in fields and "extra.in_force" in fields, s.source_id
        assert all(f.kind in ("flat", "range") for f in template.default_facets(s)), \
            "a facet is a filter: nothing in a Codex's facets reorders a list (invariant 4)"


def test_a_value_is_a_scalar_as_json_writes_it():
    assert fieldvalues.scalars(True) == (["true"], 0) and fieldvalues.scalars(False) == (["false"], 0)
    assert fieldvalues.scalars(None) == ([], 0) and fieldvalues.scalars("") == ([], 0)
    assert fieldvalues.scalars(["LOI", " LOI ", 3, 2.5]) == (["LOI", "3", "2.5"], 0)
    assert fieldvalues.scalars({"a": 1}) == ([], 1) and fieldvalues.scalars([["x"]]) == ([], 1)


# ---- a search restricted by one ------------------------------------------------------------------

@pytest.fixture
def held(node, monkeypatch):  # noqa: F811
    """A held collection `it` of Normattiva's fixture acts: its part and
    snapshot in `shard/`, a manifest and a text index, the facet pass run."""
    import tests.test_normattiva as t
    from app.plugins.normattiva import SOURCE, source as nsrc
    nsrc._zip_cache.clear()
    r = node / "origin"
    t._zip(r / "akn/M/regi-decreti.zip", {t.RD206_R: t.RD206_REPEALED, t.RD7_V1: t.BUILT})
    t._zip(r / "akn/M/codici.zip", {t.M306: base64.b64encode(t.L306)})
    records = records_of(SOURCE, r)
    codex = pubmed(facets=template.default_facets(SOURCE))
    name = "it"
    d = col.shard_dir_of(node, name)
    snap = a_snapshot(codex, [_part(d, records)])
    (d / "snapshots").mkdir(parents=True)
    (d / "snapshots" / "2026-08.json").write_bytes(snap.canonical())
    c = collections_mod.Collection(
        name=name, plugin="pubmed", profile=None, embed_mode_default=None, corpus_dir=node / name / "corpus",
        manifest_path=node / name / "state" / "manifest.sqlite", fts_dir=node / name / "fts",
        lance_dir=node / name / "lancedb", codex=codex)
    con = db.connect(c)
    for rec in records:
        nid = int(rec["native_id"])
        con.execute("INSERT INTO documents(uid,source,native_id,path,lang,rdf_done,fts_done,updated_at) "
                    "VALUES(?,?,?,?,?,1,1,0)", (rec["uid"], "pubmed", rec["native_id"], f"/x/{nid}", "it"))
        fts.index_book("it", nid, [{"id": f"{rec['uid']}:0:1", "book_id": nid, "seq": 0, "chapter": "",
                                    "text": "legge decreto articolo", "tokens": 3}], c)
    con.commit()
    facets.build(con, codex, node / name / "index", snapshot_id="2026-08", shard_dir=d, snapshot=snap)
    monkeypatch.setattr(query_service, "resolve_collections", lambda names, **kw: [c])
    monkeypatch.setattr(query_service, "withheld_from", lambda names, **kw: [])
    yield {rec["extra"]["doc_type"] + (" repealed" if rec["extra"].get("in_force") is False else ""):
           int(rec["native_id"]) for rec in records}, codex, d
    con.close()
    fts.close_all()


def found(filters_):
    out = query_service.run_search(["it"], "articolo", 20, "fts", False, filters=filters_)
    return {int(h["book_id"]) for h in out["hits"]}


def test_a_search_is_restricted_by_a_legal_facet(held):
    ids, codex, d = held
    assert set(ids) == {"REGIO DECRETO repealed", "DECRETO", "LEGGE"}
    assert found(None) == set(ids.values())
    assert found({"extra.in_force": "false"}) == {ids["REGIO DECRETO repealed"]}
    assert found({"extra.in_force": {"not": "false"}}) == {ids["DECRETO"], ids["LEGGE"]}
    assert found({"extra.collection": "codici"}) == {ids["LEGGE"]}
    assert found({"extra.collection": "regi-decreti", "extra.doc_type": "DECRETO"}) == {ids["DECRETO"]}
    assert found({"extra.doc_type": "DECRETO LEGISLATIVO"}) == set(), "a value nobody holds admits nothing"
    assert found({"date": "2000.."}) == {ids["DECRETO"], ids["LEGGE"]}, "the year, a range"
    with pytest.raises(filters.FilterError, match="is a flat facet"):
        found({"extra.in_force": "2000..2005"})
    listed = facets.values(None, codex, d.parent / "index", "extra.collection", snapshot_id="2026-08")
    assert listed["values"] == {"regi-decreti": 2, "codici": 1} and "this peer's" in listed["note"]


def test_a_part_is_read_once_for_every_facet_and_not_again_at_its_root(held, monkeypatch):
    _ids, codex, d = held
    reads = []
    real = fieldvalues._read_part
    monkeypatch.setattr(fieldvalues, "_read_part",
                        lambda path, fields: reads.append(sorted(fields)) or real(path, fields))
    facets.build(None, codex, d.parent / "index", snapshot_id="2026-08", shard_dir=d,
                 snapshot=a_snapshot(codex, list(a_snapshot_shards(d))), fields=["extra.collection"])
    assert reads == [], "already read at its root"
    fresh = d.parent / "fresh-index"
    facets.build(None, codex, fresh, snapshot_id="2026-08", shard_dir=d,
                 snapshot=a_snapshot(codex, list(a_snapshot_shards(d))),
                 fields=["extra.collection", "extra.doc_type", "extra.in_force"])
    assert reads == [["extra.collection", "extra.doc_type", "extra.in_force"]], "one read for the three"


def a_snapshot_shards(d: Path):
    from app.hold import snapshot as sn
    return sn.load(d / "snapshots" / "2026-08.json").shards
