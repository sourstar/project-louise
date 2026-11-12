"""R1 of docs/notes/legal-chunking-retrieval.md §4 (operator's default A):
one article pin for every legal collection. Each legal reference source
cites articles in its own grammar (`article_citations`, `article_of_label`);
app/ask/articles.py pins, for each, the article's chunk in the version in
force at the date — a fact, never a ranking (invariant 4)."""
from __future__ import annotations

import pytest

from app import collections as collections_mod, db, discovery, refs, worker
from app.ask import articles
from app.plugins.base import RefSyncState
from app.refs import eurlex as eref, legi as lref, neuris as nref, normattiva as itref, store as refs_store
from tests import neuris_support as ns
from tests.ask import _articles_before_r1 as before
from tests.test_eurlex import ACT, build_corpus
from tests.test_legi import held  # noqa: F401 — the fixture
from tests.test_validity import O_ID, V12_ID, _run, versioned  # noqa: F401


def _stages(con, c, *stages):
    for stage in stages:
        flag = f"{stage}_done"
        while True:
            row = con.execute(f"SELECT * FROM documents WHERE {flag}=0 AND error IS NULL "
                              f"AND in_scope=1 LIMIT 1").fetchone()
            if row is None:
                break
            getattr(worker, f"stage_{stage}")(con, row, c)
            if con.in_transaction:
                con.execute("COMMIT")


def _store(tmp_path, root, *source_ids):
    con = refs.connect(tmp_path / "refs.db")
    for s in refs.REFS_REGISTRY.values():
        refs_store.ensure_schema(con, s.schema())
    for sid in source_ids:
        refs_store.sync_source(con, refs.REFS_REGISTRY[sid], RefSyncState(ref_root=root))
    _forget()
    return refs.RefStore(con, [s.schema() for s in refs.REFS_REGISTRY.values()])


def _forget():
    from app.refs import resolver
    lref.invalidate_cache()
    nref.invalidate_cache()
    itref.invalidate_cache()
    resolver.invalidate_cache()


def _collection(tmp_path, name, corpus):
    c = collections_mod.Collection(
        name=name, plugin=name, profile=None, embed_mode_default=None,
        corpus_dir=corpus, manifest_path=tmp_path / f"{name}.db",
        fts_dir=tmp_path / f"fts-{name}", lance_dir=tmp_path / f"lance-{name}")
    con = db.connect(c)
    discovery.sync_collection(con, c)
    _stages(con, c, "rdf", "fts")
    return c, con


# ---- the grammars ----------------------------------------------------------------------

def test_the_italian_grammar_is_normattiva_s_unchanged():
    [c] = itref.article_citations("art. 5 della legge 7 agosto 1990, n. 241")
    assert c.keys[0] == "urn:nir:stato:legge:1990-08-07;241" and len(c.keys) == len(itref.FORMS)
    assert (c.article, c.label, c.also) == ("5", "art. 5", ())
    [c] = itref.article_citations("art. 5 della legge 241/1990", facts=[
        {"kind": "atto", "entity_id": "urn:nir:stato:legge:1990-08-07;241", "label": "L. 241 — art. 5"},
        {"kind": "versione", "entity_id": "x", "label": "L. 241 — art. 5"}])
    assert c.keys == () and c.also == ("urn:nir:stato:legge:1990-08-07;241",), "no date: the act the facts resolved"
    assert itref.article_citations("articolo 5-bis del d.lgs. n. 82 del 2005")[0].article == "5-bis"
    # near-misses: no article, no act, not the word "art."
    for q in ("legge 241/1990", "arte 5 della legge 241/1990", "art. 5", "art. 2043 c.c."):
        assert itref.article_citations(q) == [], q
    assert itref.label_token("TITOLO I — X › Art. 1.") == "1" and articles.token is itref.token


def test_the_french_grammar_names_the_code_from_the_synced_titles(held):  # noqa: F811
    c, _ = held
    store = _store(c.corpus_dir.parent, c.corpus_dir.parent, "legi-refs")
    for q in ("article 9 du code des instruments monétaires et des médailles",
              "art. 9 du Code des instruments monétaires et des médailles"):
        [got] = lref.article_citations(q, store)
        assert set(got.keys) == {"legi:LEGIARTI000000000101", "legi:LEGIARTI000000000102"}, \
            "one citation per article chain, every version's id"
        assert (got.article, got.label) == ("9", "article 9")
    for q in ("article 9 du code civil", "article 9", "le code des instruments monétaires et des médailles"):
        assert lref.article_citations(q, store) == [], q
    assert lref.article_citations("article 9 du code des instruments monétaires et des médailles", None) == [], \
        "no reference data: no code can be named"
    assert lref.article_of_label("Article L121-1") == "l121-1" and lref.article_of_label("Section 2") is None


def test_the_german_grammar_reads_the_synced_abbreviations(tmp_path):
    root = tmp_path / "refs"
    ns.corpus(root / "neuris")
    store = _store(tmp_path, root, "neuris-refs")
    [c] = nref.article_citations("§ 2 Abs. 1 RisAbk", store)
    assert c.keys == ("eli:de:bund/bgbl-1/2020/s1234", "jurabk:RisAbk") and (c.article, c.label) == ("2", "§ 2")
    [c] = nref.article_citations("Art. 3 des GeGuGe 2025", store)
    assert c.keys[-1] == "jurabk:GeGuGe 2025" and (c.article, c.label) == ("3", "Art. 3")
    for q in ("§ 2 BGB", "RisAbk", "Paragraph 2 RisAbk"):
        assert nref.article_citations(q, store) == [], q
    assert nref.article_citations("§ 2 RisAbk", None) == []
    assert nref.article_of_label("Abschnitt 1 — X › § 1 — Anwendungsbereich") == "1"
    assert nref.article_of_label("Anlage T1 (zu § 4 Absatz 2)") is None


def test_the_eu_grammar_needs_no_reference_data_and_invents_no_alias():
    [c] = eref.article_citations("Article 5 of Regulation (EU) 2016/679")
    assert c.keys == ("celex:32016R0679",) and (c.article, c.label) == ("5", "Article 5")
    assert eref.article_citations("art. 17 du règlement (UE) 2016/679")[0].article == "17"
    assert eref.article_citations("Articolo 6 del regolamento (UE) 2016/679")[0].label == "Articolo 6"
    for q in ("art. 17 GDPR", "Regulation (EU) 2016/679", "Article 5 of the Treaty"):
        assert eref.article_citations(q) == [], q
    assert eref.article_of_label("TITLE I — X › Article 2 — Definitions") == "2"
    assert eref.article_of_label("Recital 26") is None


# ---- a held collection of each plugin pins the right article --------------------------

def test_a_french_article_is_pinned_in_the_version_in_force(held):  # noqa: F811
    c, _ = held
    store = _store(c.corpus_dir.parent, c.corpus_dir.parent, "legi-refs")
    q = "article 9 du code des instruments monétaires et des médailles"
    [now] = articles.pin(q, [c], store=store)
    assert "désormais" in now["fields"]["testo"] and now["label"].endswith("— article 9 (testo vigente)")
    [then] = articles.pin(q, [c], as_of=19990101, store=store)
    assert "anciennement" in then["fields"]["testo"] and then["fields"]["fino al"] == "2006-12-27"
    assert articles.pin(q, [c]) == [], "no refs store: the code cannot be named"


def test_a_german_provision_is_pinned_in_the_version_in_force(tmp_path, isolated_config):
    root = tmp_path / "refs"
    ns.corpus(root / "neuris")
    store = _store(tmp_path, root, "neuris-refs")
    c, _ = _collection(tmp_path, "neuris", root / "neuris")
    [f] = articles.pin("§ 1 Abs. 2 FrSaftErfrischV", [c], store=store)
    assert "› § 1 — Anwendungsbereich" in f["fields"]["testo"], "the chunk carries its context header (C2)"
    assert "§ 2 —" not in f["fields"]["testo"]
    assert f["entity_id"] == "eli:de:bund/bgbl-1/2000/s1016~art1"
    got = {articles.pin("§ 1 RisAbk", [c], as_of=d, store=store)[0]["fields"]["in vigore dal"]
           for d in (20200601, 20210601, 20220601)}
    assert got == {"2020-01-01", "2021-01-01", "2022-01-01"}, "each date its own version"


def test_an_eu_article_is_pinned_in_the_version_in_force(tmp_path, isolated_config):
    c, _ = _collection(tmp_path, "eurlex", build_corpus(tmp_path / "eurlex"))
    q = "Article 5 of Regulation (EU) 2016/1036"
    [f] = articles.pin(q, [c])
    assert "Article 5 — Initiation of proceedings" in f["fields"]["testo"]
    assert "Article 6 —" not in f["fields"]["testo"]
    assert f["entity_id"] == f"celex:{ACT}~art5" and f["fields"]["in vigore dal"] == "2018-06-08", \
        "no date: the shown version, the consolidation"
    assert articles.pin(q, [c], as_of=20170101)[0]["fields"]["in vigore dal"] != "2018-06-08"
    assert articles.pin("Article 999 of Regulation (EU) 2016/1036", [c]) == []


def test_a_query_naming_no_article_pins_nothing(tmp_path, isolated_config):
    root = tmp_path / "refs"
    ns.corpus(root / "neuris")
    store = _store(tmp_path, root, "neuris-refs")
    de, _ = _collection(tmp_path, "neuris", root / "neuris")
    eu, _ = _collection(tmp_path, "eurlex", build_corpus(tmp_path / "eurlex"))
    for q in ("Regulation (EU) 2016/1036", "FrSaftErfrischV", "Fruchtsaft Kennzeichnung",
              "dumping injury Union industry", ""):
        assert articles.pin(q, [de, eu], store=store) == [], q
        assert articles.citations(q, (), store) == [], q


# ---- Normattiva's live path: identical before and after ----------------------------------

ITALIAN = (
    "art. 8 della legge costituzionale 26 febbraio 1948, n. 3",
    "articolo 8 della legge costituzionale 26 febbraio 1948, n. 3",
    "art.8 della legge costituzionale 26 febbraio 1948, n. 3",
    "art. 8 del decreto-legge 26 febbraio 1948, n. 3",
    "art. 8 del testo unico 26 febbraio 1948, n. 3",
    "art. 8 della legge 27 febbraio 1948, n. 3",
    "art. 99 della legge costituzionale 26 febbraio 1948, n. 3",
    "art. 1 della legge costituzionale 26 febbraio 1948, n. 3 e art. 8 della legge costituzionale "
    "26 febbraio 1948, n. 3",
    "art. 8 della l. cost. n. 3/1948",
    "art. 8 della legge costituzionale n. 3 del 1948",
    "legge costituzionale 26 febbraio 1948, n. 3",
    "statuto speciale per la Sardegna",
)
#: an `atto` fact as the resolver pins it, for the citations with no date
FACTS = ({"entity_id": "urn:nir:stato:legge.costituzionale:1948-02-26;3", "kind": "atto",
          "label": "Statuto speciale per la Sardegna — art. 8", "source_id": "normattiva-refs"},)


@pytest.mark.parametrize("as_of", [None, 19990101, 20050101, 19400101])
def test_normattiva_s_pins_are_identical_before_and_after(versioned, as_of):  # noqa: F811
    c, con, _ = versioned
    _run(con, c, "rdf", "fts")
    nonempty = 0
    for q in ITALIAN:
        for facts in ((), FACTS):
            old = before.pin(q, [c], facts, as_of=as_of)
            assert articles.pin(q, [c], facts, as_of=as_of) == old, (q, facts, as_of)
            nonempty += bool(old)
    assert nonempty >= (6 if as_of != 19400101 else 0), "the set exercises the pin, not only its refusals"
