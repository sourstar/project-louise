"""The French texts as a reference source (app/refs/legi.py; 06 §6.5;
legal-fr-de-eu.md §2.5): texts, articles and sections synced from the
plugin's own effective state, every key a record writes found by
`key_lookup` and `louise_lookup`, the citation grammar resolved to the
article versions it names as facts, never ranked."""
from __future__ import annotations

import time

import pytest

from app import entities, refs
from app.plugins.legi import pack
from app.refs import legi as lref, store as refs_store
from tests.legi_fixtures import REAL, article, member, tarball, texte_version

CIV, CONSO, CGI, CPC = ("LEGITEXT000006070721", "LEGITEXT000006069565", "LEGITEXT000006069577",
                        "LEGITEXT000006070716")
COM, COMMUNES = "LEGITEXT000005634379", "LEGITEXT000006070162"
LOI = "LEGITEXT000006068624"
LOI_CID = "JORFTEXT000000886460"


def _codes() -> dict[str, bytes]:
    """Hand-made texts and articles (tests/fixtures/legi/README.md) at the
    places a baseline holds them, beside the real files."""
    out = dict(REAL)
    for ident, title, etat in ((CIV, "Code civil", "VIGUEUR"), (CONSO, "Code de la consommation", "VIGUEUR"),
                               (CGI, "Code général des impôts", "VIGUEUR"),
                               (CPC, "Code de procédure civile", "VIGUEUR"),
                               (COM, "Code de commerce", "VIGUEUR"), (COMMUNES, "Code des communes", "ABROGE")):
        out[member("texte_version", ident, ident)] = texte_version(ident, title, etat=etat)
    out[member("texte_version", LOI, LOI)] = texte_version(
        LOI, "Loi n° 78-17 du 6 janvier 1978 relative à l'informatique, aux fichiers et aux libertés",
        cid=LOI_CID, nature="LOI", num="78-17", nor="JUSX7700001L", date_texte="1978-01-06",
        date_publi="1978-01-07", debut="1978-01-07")
    arts = [
        ("LEGIARTI000032041571", CIV, "Code civil", "1240", "2016-10-01"),
        ("LEGIARTI000006419320", CIV, "Code civil", "1382", "1804-02-19"),
        ("LEGIARTI000032227262", CONSO, "Code de la consommation", "L121-1", "2016-07-01"),
        ("LEGIARTI000032227263", CONSO, "Code de la consommation", "L121-2", "2016-07-01"),
        ("LEGIARTI000032227264", CONSO, "Code de la consommation", "L121-3", "2016-07-01"),
        ("LEGIARTI000006303740", CGI, "Code général des impôts", "39 octies A", "1989-01-01"),
        ("LEGIARTI000006410103", CPC, "Code de procédure civile", "1", "1976-01-01"),
        ("LEGIARTI000037822953", LOI_CID, "Loi n° 78-17", "5", "2018-06-21"),
    ]
    for ident, cid, title, num, debut in arts:
        out[member("article", ident, cid if cid.startswith("LEGITEXT") else LOI)] = article(
            ident, num=num, debut=debut, cid=cid, title=title)
    return out


@pytest.fixture
def store(tmp_path):
    pack.reset_caches()
    root = tmp_path / "refs"
    tarball(root / "legi" / "Freemium_legi_global_20250713-140000.tar.gz", _codes())
    con = refs.connect(tmp_path / "refs.db")
    refs_store.sync_all(con, root)
    _forget_indexes()
    yield refs.RefStore(con, [s.schema() for s in refs.REFS_REGISTRY.values()])
    # the resolvers memoise on ref_meta's stamp, seconds-wide: a store made in
    # the same second by the next test would read this one's (no hgnc here)
    _forget_indexes()


def _forget_indexes() -> None:
    from app.refs import normattiva as nref, resolver
    lref.invalidate_cache()
    nref.invalidate_cache()
    resolver.invalidate_cache()


def test_the_texts_articles_and_sections_are_synced_from_the_plugin_s_state(store):
    t = {r["id"]: r for r in store.rows("texte")}
    assert t[CIV]["titre"] == "Code civil" and t[CIV]["nature"] == "CODE" and t[CIV]["cid"] == CIV
    loi = t[LOI]
    assert (loi["cid"], loi["nature"], loi["num"], loi["nor"], loi["date_texte"]) == \
        (LOI_CID, "LOI", "78-17", "JUSX7700001L", "1978-01-06")
    assert t[CGI]["sigle"] == "CGI" and t[CONSO]["mots"] == "consommation"
    a = store.get("article", "LEGIARTI000006398122")
    assert (a["cid"], a["num"], a["num_norm"], a["etat"], a["date_fin"]) == \
        ("LEGITEXT000006070666", "1", "1", "ABROGE", "2001-01-01")
    assert a["uid"].startswith("legi:") and a["member"].startswith("Freemium_legi_global_20250713-140000.tar.gz#legi/")
    assert store.get("article", "LEGIARTI000006303740")["num_norm"] == "39octiesa"
    assert store.get("section", "LEGISCTA000006135575")["titre"] == "Paragraphe II : Frappe des médailles."
    assert store.owner_of("article") == "legi-refs"


def test_the_initials_and_words_come_from_the_titles():
    assert lref.sigle("Code de l'entrée et du séjour des étrangers et du droit d'asile") == "CESEDA"
    assert lref.sigle("Code des relations entre le public et l'administration") == "CRPA"
    assert lref.sigle("Livre des procédures fiscales") == "LPF"
    assert lref.sigle("Code général des collectivités territoriales") == "CGCT"
    assert lref.sigle("Code de l'action sociale et des familles") == "CASF"
    assert lref.mots("Code de procédure civile") == "procedure civile"
    assert lref.norm_num("L. 121-1") == lref.norm_num("L121-1") == "l121-1"
    assert lref.norm_num("1er") == "1" and lref.norm_num("R*. 12") == "r*12"


@pytest.mark.parametrize("canonical,table,column", [
    ("legi:LEGIARTI000006398122", "article", "id"), ("legi:LEGISCTA000006135575", "section", "id"),
    (f"legi:{CIV}", "texte", "id"), (f"jorf:{LOI_CID}", "texte", "cid"), ("nor:JUSX7700001L", "texte", "nor"),
])
def test_every_key_a_record_writes_is_found(store, canonical, table, column):
    src = refs.REFS_REGISTRY["legi-refs"]
    assert src.key_lookup(canonical)[:2] == (table, column)
    from app.ask import lookup
    got = lookup.lookup(canonical, store=store)
    assert got["unresolved"] == [] and got["facts"][0]["kind"] == table
    assert got["facts"][0]["source_id"] == "legi-refs"


def test_an_eli_fr_key_is_routed_here_and_resolves_to_nothing_said_so(store):
    eli_refs = refs.REFS_REGISTRY["eli-refs"]
    assert eli_refs.route("eli:fr:loi/1978/1/6/78-17/jo/texte", refs.REFS_REGISTRY) is refs.REFS_REGISTRY["legi-refs"]
    from app.ask import lookup
    got = lookup.lookup("eli:fr:loi/1978/1/6/78-17/jo/texte", store=store)
    assert got == {"facts": [], "unresolved": ["eli:fr:loi/1978/1/6/78-17/jo/texte"]}
    assert lookup.lookup("legi:LEGIARTI000099999999", store=store)["unresolved"]


def _ids(store, text: str) -> set[str]:
    return {e.entity_id for e in refs.RESOLVER.resolve(text, store)}


@pytest.mark.parametrize("text,want", [
    ("article 1240 du code civil", {"legi:LEGIARTI000032041571"}),
    ("l'article 1240 du Code civil", {"legi:LEGIARTI000032041571"}),
    ("art. 1240 C. civ.", {"legi:LEGIARTI000032041571"}),
    ("C. civ., art. 1240", {"legi:LEGIARTI000032041571"}),
    ("article L. 121-1 du code de la consommation", {"legi:LEGIARTI000032227262"}),
    ("art. L121-1 C. consom.", {"legi:LEGIARTI000032227262"}),
    ("articles L. 121-1 à L. 121-3 du code de la consommation",
     {"legi:LEGIARTI000032227262", "legi:LEGIARTI000032227263", "legi:LEGIARTI000032227264"}),
    ("art. 39 octies A du CGI", {"legi:LEGIARTI000006303740"}),
    ("article 1er du code de procédure civile", {"legi:LEGIARTI000006410103"}),
    ("art. 1er C. pr. civ.", {"legi:LEGIARTI000006410103"}),
    ("article L. 121-2, I, 2° du code de la consommation", {"legi:LEGIARTI000032227263"}),
])
def test_a_code_article_in_any_of_its_forms(store, text, want):
    assert _ids(store, text) == want


@pytest.mark.parametrize("text", [
    "la loi n° 78-17 du 6 janvier 1978", "loi n°78-17", "Loi no 78-17 du 6 janvier 1978",
    "loi n° 1978-17",
])
def test_an_act_by_its_number_and_date(store, text):
    assert _ids(store, text) == {f"legi:{LOI}"}


def test_an_article_of_an_act_and_the_act_itself(store):
    assert _ids(store, "l'article 5 de la loi n° 78-17 du 6 janvier 1978") == \
        {"legi:LEGIARTI000037822953", f"legi:{LOI}"}
    assert _ids(store, "la loi du 6 janvier 1978") == {f"legi:{LOI}"}, "by its date alone, one text answers"


def test_a_name_two_codes_share_resolves_to_the_one_in_force_and_nothing_else(store):
    # "C. com." is the Code de commerce (in force) and the Code des communes (repealed)
    rows = lref.code_at(lref._index(store), " C. com.")[0]
    assert [r["id"] for r in rows] == [COM]
    assert _ids(store, "article 1240 du code pénal") == set(), "a code not held resolves to nothing"
    assert _ids(store, "article 99999 du code civil") == set()


def test_text_without_a_citation_costs_nothing_and_resolves_nothing(store):
    assert lref.citations("Le présent code régit les rapports entre les personnes.") == []
    assert _ids(store, "La loi est la même pour tous, en 1789 comme aujourd'hui.") == set()
    big = "Le texte des lois sans aucune citation numérotée. " * 20_000
    t0 = time.perf_counter()
    lref.citations(big)
    assert time.perf_counter() - t0 < 1.0, "the digit-anchored scan: prose without digits is one search"


def test_without_legi_the_resolver_returns_at_once(tmp_path):
    con = refs.connect(tmp_path / "empty.db")
    for s in refs.REFS_REGISTRY.values():
        refs_store.ensure_schema(con, s.schema())
    lref.invalidate_cache()
    store = refs.RefStore(con, [s.schema() for s in refs.REFS_REGISTRY.values()])
    assert lref.CitationResolver().resolve("article 1240 du code civil", store) == []


def test_expanding_a_text_pins_its_articles_as_facts(store):
    text = next(e for e in refs.RESOLVER.resolve("la loi n° 78-17 du 6 janvier 1978", store) if e.kind == "texte")
    reached = refs.RESOLVER.expand_bounded(text, store, 10)
    assert len(reached) == 1 and reached[0].table == "article" and reached[0].total == 1


def test_tagging_a_record_writes_the_texts_it_names(store, tmp_path):
    from app import collections as collections_mod, db
    c = collections_mod.Collection(
        name="legi", plugin="legi", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path, manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")
    con = db.connect(c)
    con.execute("INSERT INTO documents(uid,source,native_id,path,updated_at) VALUES(?,?,?,?,0)",
                ("legi:1", "legi", "1", "/x"))
    ids = entities.tag_document(con, "legi:1", "Code civil — Article 1241",
                                ["Comme le dispose l'article 1240 du code civil, et la loi n° 78-17."],
                                refs.RESOLVER, store)
    assert ids == sorted(["legi:LEGIARTI000032041571", f"legi:{LOI}"])
    assert lref.NAMESPACES == ("legi", "jorf", "nor") and lref.LegiRefSource.eli_jurisdiction == "fr"
