"""EuroVoc labels synced into the node's refs.db from München's excerpts
(tests/fixtures/eurovoc), for the tests that check a collection's own
information is all its index holds."""
from app import config, refs
from app.refs import eurovoc, store as refs_store
from tests.test_refs_eurovoc import _file


def labels_synced(tmp_path) -> None:
    base = tmp_path / "refsrc" / eurovoc.DIR
    base.mkdir(parents=True, exist_ok=True)
    _file(base, "eurovoc", "1073").replace(base / "eurovoc_in_skos_core_concepts.rdf")
    _file(base, "corporate-body", "CONSIL").replace(base / "corporatebodies-skos.rdf")
    (base / eurovoc.TERMS).write_bytes(b"the reuse notice")
    eurovoc.make_ack(base, "2026-11-16T10:00:00Z")
    con = refs.connect(config.refs_db_path())
    refs_store.sync_all(con, tmp_path / "refsrc")
    con.close()
