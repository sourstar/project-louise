"""EuroVoc and the authority tables as a reference source (app/refs/eurovoc.py):
the labels of the concept ids an EUR-Lex record carries, read from the SKOS
core files as the Publications Office publishes them (München's byte-exact
excerpts, tests/fixtures/eurovoc), synced only once the reuse notice's terms
are acknowledged (invariant 9), resolved by key in the reader's language."""
from pathlib import Path

from app import refs
from app.plugins.base import RefSyncState
from app.plugins.eurlex import from_tree
from app.refs import eurovoc, store as refs_store

FIX = Path(__file__).parent / "fixtures" / "eurovoc"
#: the end of the file, which an excerpt of its head stops short of
CLOSE = b"\n</rdf:RDF>\n"


def _file(tmp_path: Path, name: str, *excerpts: str) -> Path:
    """A well-formed file: the head (opening, namespaces, three concepts),
    then the concept excerpts named, then the root's close."""
    head = (FIX / f"{name}.head.rdf").read_bytes()
    body = b"".join((FIX / f"{name}.{e}.rdf").read_bytes() for e in excerpts)
    out = tmp_path / f"{name}.rdf"
    out.write_bytes(head + b"\n" + body + CLOSE)
    return out


def test_eurovoc_s_descriptions_and_the_tables_concepts_are_read_by_the_records_keys(tmp_path):
    rows = list(eurovoc.read(_file(tmp_path, "eurovoc", "1073")))
    keys = {r["key"] for r in rows}
    assert "eurovoc:1073" in keys and len(keys) == 4, keys
    it = {r["label"] for r in rows if r["key"] == "eurovoc:1073" and r["lang"] == "it" and r["kind"] == "pref"}
    assert it == {"formazione sul posto di lavoro"}
    assert {r["label"] for r in rows if r["key"] == "eurovoc:1073" and r["kind"] == "alt"
            and r["lang"] == "en"} >= {"on-the-job training"}
    for name, ident, key, label in (("corporate-body", "CONSIL", "corporate-body:CONSIL",
                                     "Consiglio dell’Unione europea"),
                                    ("subject-matter", "TRAN", "subject-matter:TRAN", "Trasporti"),
                                    ("dir-eu-legal-act", "07203020", "dir-eu-legal-act:07203020",
                                     "Accesso al mercato")):
        got = list(eurovoc.read(_file(tmp_path, name, ident)))
        mine = [r for r in got if r["key"] == key]
        assert any(r["lang"] == "it" and r["kind"] == "pref" and r["label"] == label for r in mine), name
        assert len({r["key"] for r in got}) == 4, f"{name}: the head's three and the one named"
    # the same key the records carry (from_tree.concept_id)
    assert from_tree.concept_id("http://publications.europa.eu/resource/authority/corporate-body/CONSIL") \
        == "corporate-body:CONSIL"


def _synced(tmp_path, *, terms: bytes | None, ack: bool):
    base = tmp_path / "refs" / eurovoc.DIR
    base.mkdir(parents=True)
    _file(base, "eurovoc", "1073").rename(base / "eurovoc_in_skos_core_concepts.rdf")
    _file(base, "corporate-body", "CONSIL").rename(base / "corporatebodies-skos.rdf")
    if terms is not None:
        (base / eurovoc.TERMS).write_bytes(terms)
        if ack:
            eurovoc.make_ack(base, "2026-11-16T10:00:00Z")
    con = refs.connect(tmp_path / "refs.db")
    refs_store.sync_all(con, tmp_path / "refs")
    return base, refs.RefStore(con, [s.schema() for s in refs.REFS_REGISTRY.values()])


def test_nothing_is_synced_before_the_terms_are_laid_and_acknowledged(tmp_path):
    base, store = _synced(tmp_path, terms=None, ack=False)
    assert "Decision 2011/833/EU" in eurovoc.held_back(base) and store.count("concept_label") == 0
    assert eurovoc.labels(store, ["eurovoc:1073"], "it") == {}, "the ids stay, nothing breaks"


def test_terms_not_acknowledged_are_refused(tmp_path):
    base, store = _synced(tmp_path / "a", terms=b"the reuse notice", ack=False)
    assert "not acknowledged" in eurovoc.held_back(base) and store.count("concept_label") == 0


def test_once_acknowledged_the_labels_resolve_by_key_in_the_reader_s_language(tmp_path):
    base, store = _synced(tmp_path, terms=b"the reuse notice", ack=True)
    assert eurovoc.held_back(base) is None and store.count("concept_label") > 0
    got = eurovoc.labels(store, ["eurovoc:1073", "corporate-body:CONSIL", "eurovoc:999999"], "it")
    assert got == {"eurovoc:1073": "formazione sul posto di lavoro",
                   "corporate-body:CONSIL": "Consiglio dell’Unione europea"}, "an id it lacks: absent"
    assert eurovoc.labels(store, ["eurovoc:1073"], "xx")["eurovoc:1073"] == "in-service training", \
        "English where the language has none"
    # a changed terms text is no longer the acknowledged one
    (base / eurovoc.TERMS).write_bytes(b"another notice")
    assert "not acknowledged" in eurovoc.held_back(base)


def test_registered_and_yellow():
    src = refs.REFS_REGISTRY["eurovoc"]
    assert src.license_class.value == "yellow" and src.schema().tables[0].indexes == ("key",)


def test_a_sync_held_back_runs_again_once_acknowledged(tmp_path):
    """refs/store.py short-circuits a source whose input did not move: the
    acknowledgement is part of the input."""
    import os
    base, store = _synced(tmp_path, terms=b"the reuse notice", ack=False)
    assert store.count("concept_label") == 0
    eurovoc.make_ack(base, "2026-11-16T10:00:00Z")
    later = os.stat(base / eurovoc.ACK).st_mtime + 5
    os.utime(base / eurovoc.ACK, (later, later))
    refs_store.sync_all(store.con, tmp_path / "refs")
    assert store.count("concept_label") > 0
