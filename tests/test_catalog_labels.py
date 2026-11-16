"""Concept ids are written to the catalog with their labels (app/catalog.py,
2026-11-16): an EUR-Lex record on parser 4 names its subjects and authors by
id (`eurovoc:1073`, `corporate-body:CONSIL`), and the catalog leg finds it
by the label in the document's language, as it found the notice's labels.
The labels are EuroVoc's reference (app/refs/eurovoc.py); a change of them
writes the rows carrying ids again."""
import pytest

from app import catalog, collections as collections_mod, config, db, refs
from app.refs import eurovoc, store as refs_store
from tests.test_refs_eurovoc import _file

ROWS = [  # n, lang, title, authors, subjects
    (1, "it", "Regolamento n. 1473/72 del Consiglio", "corporate-body:CONSIL",
     "eurovoc:1073|subject-matter:PRIV"),
    (2, "it", "Parere della Commissione", "corporate-body:COM", "eurovoc:999999"),
]


def _labels(tmp_path, terms=b"the reuse notice"):
    base = tmp_path / "refsrc" / eurovoc.DIR
    base.mkdir(parents=True, exist_ok=True)
    _file(base, "eurovoc", "1073").replace(base / "eurovoc_in_skos_core_concepts.rdf")
    _file(base, "corporate-body", "CONSIL").replace(base / "corporatebodies-skos.rdf")
    (base / eurovoc.TERMS).write_bytes(terms)
    eurovoc.make_ack(base, "2026-11-16T10:00:00Z")
    con = refs.connect(config.refs_db_path())
    refs_store.sync_all(con, tmp_path / "refsrc")
    con.close()
    catalog._STAMP.clear()


@pytest.fixture
def col(isolated_config):
    c = collections_mod.default_collection()
    con = db.connect(c)
    for n, lang, title, authors, subjects in ROWS:
        con.execute("INSERT INTO documents(uid,source,native_id,path,lang,title,authors,subjects,"
                    "rdf_done,updated_at) VALUES(?,?,?,?,?,?,?,?,1,0)",
                    (f"eurlex:{n}", "eurlex", str(n), f"/f/{n}", lang, title, authors, subjects))
    catalog._STAMP.clear()
    yield c, con
    con.close()


def _ids(hits):
    return sorted(h["book_id"] for h in hits or [])


def test_a_concept_id_is_found_by_its_label_in_the_document_s_language(col, tmp_path):
    c, con = col
    _labels(tmp_path)
    assert catalog.backfill(con, c, budget_s=60)["complete"]
    assert _ids(catalog.search(con, c, "formazione sul posto di lavoro", 10)) == [1]
    assert _ids(catalog.search(con, c, "Consiglio dell'Unione europea", 10)) == [1]
    assert _ids(catalog.search(con, c, "Parere", 10)) == [2], "an id the labels lack: as it was"


def test_without_labels_the_ids_are_written_as_they_are_and_a_sync_writes_them_again(col, tmp_path):
    c, con = col
    assert catalog.labels_stamp() is None
    assert catalog.backfill(con, c, budget_s=60)["complete"]
    assert _ids(catalog.search(con, c, "formazione", 10)) == []
    assert catalog.backfill(con, c, budget_s=60) is None, "complete, nothing new"
    _labels(tmp_path)
    again = catalog.backfill(con, c, budget_s=60)
    assert again is not None and again["complete"], "the labels came: the rows are written again"
    assert _ids(catalog.search(con, c, "formazione", 10)) == [1]
    assert catalog.backfill(con, c, budget_s=60) is None


def test_a_collection_without_concept_ids_is_not_rewritten_when_the_labels_come(isolated_config, tmp_path):
    c = collections_mod.default_collection()
    con = db.connect(c)
    con.execute("INSERT INTO documents(uid,source,native_id,path,lang,title,authors,subjects,rdf_done,"
                "updated_at) VALUES('gutenberg:1','gutenberg','1','/f/1','en','Moby Dick','Melville',"
                "'Whaling -- Fiction',1,0)")
    catalog._STAMP.clear()
    assert catalog.backfill(con, c, budget_s=60)["complete"]
    _labels(tmp_path)
    assert catalog.backfill(con, c, budget_s=60) is None
    con.close()


def test_the_check_for_concept_ids_is_asked_once_per_labels_sync(isolated_config, tmp_path, monkeypatch):
    """A per-round cost never scales with the corpus: the answer is kept."""
    c = collections_mod.default_collection()
    con = db.connect(c)
    con.execute("INSERT INTO documents(uid,source,native_id,path,lang,title,rdf_done,updated_at) "
                "VALUES('gutenberg:1','gutenberg','1','/f/1','en','Moby Dick',1,0)")
    catalog._STAMP.clear()
    assert catalog.backfill(con, c, budget_s=60)["complete"]
    _labels(tmp_path)
    asked = []
    real = catalog._uses_concepts
    monkeypatch.setattr(catalog, "_uses_concepts", lambda con_: asked.append(1) or real(con_))
    for _ in range(3):
        assert catalog.backfill(con, c, budget_s=60) is None
    assert asked == [1]
    con.close()
