"""A collection holds only the information contained in its corpus (CLAUDE.md,
2026-11-16): the catalog writes a document's title, authors and subjects as
the manifest holds them, even where a reference holds labels for its concept
ids — a reference is read at ask time as facts, never written into a
collection's index (the index-time expansion of #692 withdrawn)."""
from app import catalog, collections as collections_mod, db
from tests.test_catalog_labels_fixture import labels_synced


def test_a_concept_id_is_written_as_the_record_holds_it(isolated_config, tmp_path):
    c = collections_mod.default_collection()
    con = db.connect(c)
    con.execute("INSERT INTO documents(uid,source,native_id,path,lang,title,authors,subjects,rdf_done,"
                "updated_at) VALUES('eurlex:1','eurlex','1','/f/1','it','Regolamento del Consiglio',"
                "'corporate-body:CONSIL','eurovoc:1073',1,0)")
    labels_synced(tmp_path)
    assert catalog.backfill(con, c, budget_s=60)["complete"]
    assert not catalog.search(con, c, "formazione sul posto di lavoro", 10), \
        "no label of the reference in the collection's index"
    assert [h["book_id"] for h in catalog.search(con, c, "Regolamento", 10)] == [1]
    con.close()


def test_a_catalog_written_with_labels_is_rebuilt_once_from_the_manifest(isolated_config, tmp_path):
    import json
    c = collections_mod.default_collection()
    con = db.connect(c)
    con.execute("INSERT INTO documents(uid,source,native_id,path,lang,title,authors,subjects,rdf_done,"
                "updated_at) VALUES('eurlex:1','eurlex','1','/f/1','it','Regolamento','','eurovoc:1073',1,0)")
    assert catalog.backfill(con, c, budget_s=60)["complete"]
    # the state #692 left: written with a labels sync
    path = catalog.path_for(c) / catalog.STATE
    state = json.loads(path.read_text())
    path.write_text(json.dumps({**state, "labels": 1763280000}))
    again = catalog.backfill(con, c, budget_s=60)
    assert again is not None and again["complete"], "rebuilt"
    assert "labels" not in json.loads(path.read_text())
    assert catalog.backfill(con, c, budget_s=60) is None, "once"
    con.close()
