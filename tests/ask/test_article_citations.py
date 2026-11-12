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
    for q in ("legge 241/1990", "arte 5 della legge 241/1990", "art. 5", "art. 2043 c.x."):
        assert itref.article_citations(q) == [], q
    # a code by its abbreviation (follow-up c): an addition, from the Codici table
    [c] = itref.article_citations("art. 2043 c.c.")
    assert (c.keys, c.article, c.label) == (("urn:nir:stato:regio.decreto:1942-03-16;262",), "2043", "art. 2043")
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
    assert "désormais" in now["fields"]["testo"] and now["label"].endswith("— article 9 (texte en vigueur)")
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


# ---- R1 follow-ups (b), (c), (d), 2026-11-12 ----------------------------------------------

def _eu_three_languages(root):
    """ACT in en, fr and de: the real Formex and notice re-languaged, as the
    acquisition lays out three `lang=` slices of one act."""
    from tests.test_eurlex import _notice_for, _unit
    for lang, l3 in (("en", b"ENG"), ("fr", b"FRA"), ("de", b"DEU")):
        unit = _unit(root, lang, ACT, ACT, _notice_for(ACT).replace(b">ENG<", b">" + l3 + b"<"))
        up = lang.upper().encode()
        for p in unit.glob("*.xml"):
            if p.name != "notice.xml":
                p.write_bytes(p.read_bytes().replace(b"<LG.DOC>EN", b"<LG.DOC>" + up)
                              .replace(b"<LG.OJ>EN", b"<LG.OJ>" + up))
    return root


def test_an_eu_article_is_pinned_once_in_the_query_s_language(tmp_path, isolated_config):
    c, con = _collection(tmp_path, "eurlex", _eu_three_languages(tmp_path / "eurlex"))
    lang_of = {r["uid"]: r["lang"] for r in con.execute("SELECT uid, lang FROM documents")}
    assert sorted(lang_of.values()) == ["de", "en", "fr"], "three languages held"
    q = "Article 5 of Regulation (EU) 2016/1036"
    for query_lang, want in (("fr", "fr"), ("de", "de"), ("en", "en"), (None, "en"), ("it", "en")):
        got = articles.pin(q, [c], query_lang=query_lang)
        assert len(got) == 1, (query_lang, [g["fields"]["versione"] for g in got])
        assert lang_of[got[0]["fields"]["versione"]] == want, \
            "the query's language; else the first of the source's preference held"
        assert got[0]["label"].endswith("— Article 5 (text in force)")


#: a Codici member per code: R.D. 262/1942 (c.c.), 1398/1930 (c.p.), 1443/1940
#: (c.p.c.) — tests/test_normattiva.py's BUILT act re-keyed to each, its
#: first article renumbered to the one each test cites
_CODES = (("REGIO DECRETO_19420316_262/1942-04-04_042U0262_VIGENZA_2025-06-01_V0.xml",
           "urn:nir:stato:regio.decreto:1942-03-16;262", "042U0262", "2043", "Codice civile"),
          ("REGIO DECRETO_19301019_1398/1930-10-26_030U1398_VIGENZA_2025-06-01_V0.xml",
           "urn:nir:stato:regio.decreto:1930-10-19;1398", "030U1398", "575", "Codice penale"),
          ("REGIO DECRETO_19401028_1443/1940-10-28_040U1443_VIGENZA_2025-06-01_V0.xml",
           "urn:nir:stato:regio.decreto:1940-10-28;1443", "040U1443", "360", "Codice di procedura civile"))


def _codici(root):
    from app.plugins.normattiva import source as nsrc
    from tests.test_normattiva import BUILT, _zip
    nsrc._zip_cache.clear()
    members = {}
    for member, urn, codice, art, title in _CODES:
        members[member] = (BUILT.replace(b"urn:nir:stato:decreto:2020-01-02;3", urn.encode())
                           .replace(b"/akn/it/act/decreto/stato/2020-01-02/3/", f"/akn/it/act/{codice}/".encode())
                           .replace(b"20G00003", codice.encode())
                           .replace(b"<docType>DECRETO</docType>", b"<docType>REGIO DECRETO</docType>")
                           .replace(b"<docNumber>3</docNumber>", f"<docNumber>{urn.rsplit(';', 1)[1]}</docNumber>".encode())
                           .replace(b"Norme di prova.", title.encode())
                           .replace(b"Art. 5-bis.", f"Art. {art}.".encode())
                           .replace(b"art_5-bis", f"art_{art}".encode()))
    _zip(root / "akn/M/codici.zip", members)
    return root


def test_a_code_cited_by_its_abbreviation_pins_its_article(tmp_path, isolated_config):
    c, _ = _collection(tmp_path, "normattiva", _codici(tmp_path / "normattiva"))
    for q, (_, urn, _, art, _title) in zip(("art. 2043 c.c.", "art. 575 c.p.", "art. 360 c.p.c."), _CODES):
        [f] = articles.pin(q, [c])
        assert f["entity_id"] == f"{urn}~art{art}", q
        assert f["fields"]["testo"].startswith(f"Art. {art}."), q
        assert f["label"].startswith("REGIO DECRETO") and f", n. {urn.rsplit(';', 1)[1]}" in f["label"], q
        assert f["label"].endswith(f"— art. {art} (testo vigente)"), q
    # the table's forms: the long one, optional inner spaces, a suffix
    assert articles.pin("ai sensi dell'articolo 2043 cod. civ.", [c])[0]["entity_id"].endswith("~art2043")
    assert articles.pin("art. 360 c. p. c.", [c])[0]["entity_id"].endswith("~art360")
    [cit] = itref.article_citations("art. 5-bis disp. att. c.c.")
    assert cit.keys == ("urn:nir:stato:regio.decreto:1942-03-30;318",) and cit.article == "5-bis"
    # near-misses: another code's article, forms not in the table, no article
    for q in ("art. 2043 c.p.", "art. 2043 cc", "art. 360 c.p.c", "art. 2043 c.c.x", "c.c. 2043",
              "art. 3 Cost.", "art. 3 della Costituzione"):
        assert articles.pin(q, [c]) == [], q
    assert itref.article_citations("art. 3 Cost.") == [], "Cost. is in no table: not pinned"


def test_the_codes_table_is_data_with_its_source():
    import json
    data = json.loads(itref.CODES_FILE.read_text(encoding="utf-8"))
    assert "Normattiva open data" in data["source"] and "b8e2ad6f" in data["source"]
    assert len(data["codes"]) == 13
    assert not any("cost" in a.lower() for x in data["codes"] for a in x["abbrev"])
    assert itref.CODE_URNS["c.p.c."] == "urn:nir:stato:regio.decreto:1940-10-28;1443"
    assert itref.CODE_URNS["disp.att.c.p.p."] == "urn:nir:stato:decreto.legislativo:1989-07-28;271"


def test_gdpr_stays_unpinned_no_short_title_is_carried(tmp_path, isolated_config):
    """The CDM notice as the plugin reads it carries no short or popular
    title, so no acronym is read from data and none is invented."""
    from app.plugins.eurlex import notice
    assert not [k for k in notice.Notice.__dataclass_fields__
                if any(w in k for w in ("short", "popular", "alternative", "acronym"))]
    c, _ = _collection(tmp_path, "eurlex", build_corpus(tmp_path / "eurlex"))
    assert eref.article_citations("art. 17 GDPR") == []
    assert articles.pin("art. 17 GDPR", [c]) == []


def test_each_source_says_what_is_repealed_and_the_note_s_words():
    it, fr, de, eu = (refs.REFS_REGISTRY[s] for s in ("normattiva-refs", "legi-refs", "neuris-refs",
                                                       "eurlex-refs"))
    gone = "Art. 6.\n((ARTICOLO ABROGATO DAL D.LGS. 1 GENNAIO 2021, N. 1))"
    assert it.article_repealed(gone) and it.article_repealed("Art. 6.\n(abrogato)")
    assert not it.article_repealed("Art. 6.\nIl comma 2 dell'art. 9 è abrogato dalla legge n. 1.")
    assert articles.repealed(it, gone) and not any(articles.repealed(s, gone) for s in (fr, de, eu)), \
        "absent = never: Normattiva's marker is Normattiva's"
    assert [s.article_notes.in_force for s in (it, fr, de, eu)] == \
        ["testo vigente", "texte en vigueur", "geltende Fassung", "text in force"]
    assert [s.article_notes.last for s in (it, fr, de, eu)] == \
        ["ultimo testo in vigore", "dernier texte en vigueur", "letzte geltende Fassung", "last text in force"]
    assert it.article_notes.as_of.format(day="1999-01-01") == "testo al 1999-01-01"
    assert eu.language_preference[0] == "en"


def test_the_fact_s_keys_are_the_api_s_whatever_the_source(held):  # noqa: F811
    c, _ = held
    store = _store(c.corpus_dir.parent, c.corpus_dir.parent, "legi-refs")
    q = "article 9 du code des instruments monétaires et des médailles"
    [now] = articles.pin(q, [c], store=store)
    assert now["label"].endswith("— article 9 (texte en vigueur)")
    assert {"testo", "in vigore dal", "fino al", "versione", "chunk"} <= set(now["fields"]) <= \
        {"testo", "in vigore dal", "fino al", "versione", "chunk", "modificato da"}
    [then] = articles.pin(q, [c], as_of=19990101, store=store)
    assert then["label"].endswith("(texte au 1999-01-01)")
