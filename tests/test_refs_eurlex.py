"""The EU's acts as a reference source (app/refs/eurlex.py; 06 §6.5;
legal-fr-de-eu.md §4.5): acts and versions synced from the units, the
`celex` namespace and the `eli:eu:` keys found by `key_lookup`, citations in
the languages held resolved to the act by key — facts, never ranked."""
import time
from pathlib import Path

import pytest

from app import refs
from app.ask import lookup as lookup_mod
from app.plugins.base import Entity
from app.refs import eurlex as eref, store as refs_store

from tests.test_eurlex import ACT, CONS, DIR, _nid, _notice_for, _unit, build_corpus

TITLE = ("Proposal for a DIRECTIVE OF THE EUROPEAN PARLIAMENT AND OF THE COUNCIL on the reduction of the "
         "impact of certain plastic products on the environment")


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "refs"
    build_corpus(root / "eurlex")
    _unit(root / "eurlex", "fr", ACT, ACT, _notice_for(ACT))       # a second language of one act
    # 2016/679 and 95/46, for the citation forms: their units hold another
    # act's Formex, which the sync does not read (the notice and the wrapper's head)
    _unit(root / "eurlex", "en", "32016R0679", ACT, _notice_for("32016R0679"))
    _unit(root / "eurlex", "en", "31995L0046", DIR, _notice_for("31995L0046"))
    con = refs.connect(tmp_path / "refs.db")
    refs_store.sync_all(con, root)
    return refs.RefStore(con, [s.schema() for s in refs.REFS_REGISTRY.values()])


def test_the_acts_and_their_versions_are_synced_from_the_units(store):
    acts = {r["celex"]: r for r in store.rows("act")}
    assert set(acts) == {ACT, DIR, "32016R0679", "31995L0046"}, "a consolidation is a version, not an act"
    a = acts[ACT]
    # the sector is the notice's word: the re-keyed notice is a proposal's (sector 5)
    assert (a["sector"], a["type"], a["year"], a["language"], a["title"]) == ("5", "R", 2016, "en", TITLE)
    versions = store.rows("version", {"work_celex": ACT}, order_by="valid_from")
    assert [(v["celex"], v["language"], v["expression"]) for v in versions] == [
        (ACT, "en", "ORIGINAL"), (ACT, "fr", "ORIGINAL"), (CONS, "en", "CONSOLIDATED/20180608")]
    assert versions[0]["uid"] == f"eurlex:{_nid('2016-06-30', ACT, 'en', 'ORIGINAL')}"
    assert versions[2]["eli"] == "http://data.europa.eu/eli/reg/2016/1036/2018-06-08"
    assert versions[2]["unit"] == f"lang=en/2016/{CONS}"
    assert store.owner_of("act") == "eurlex-refs" and store.pk_of("version") == "uid"


def test_key_lookup_finds_each_form_a_record_writes():
    src = refs.REFS_REGISTRY["eurlex-refs"]
    assert src.key_lookup(f"celex:{ACT}") == ("act", "celex", ACT)
    assert src.key_lookup(f"celex:{CONS}") == ("version", "celex", CONS)
    assert src.key_lookup("eli:eu:reg/2016/1036/oj") == ("act", "eli", "http://data.europa.eu/eli/reg/2016/1036/oj")
    assert src.key_lookup("eli:eu:reg/2016/1036/2018-06-08") == (
        "version", "eli", "http://data.europa.eu/eli/reg/2016/1036/2018-06-08")
    assert src.key_lookup("eli:it:id/2003/11/15/003G0333") is None
    assert src.key_lookup("urn:nir:x") is None


def test_eli_refs_routes_eu_here_and_one_owner_per_namespace():
    eli_src = refs.REFS_REGISTRY["eli-refs"]
    assert eli_src.route("eli:eu:reg/2016/679/oj", refs.REFS_REGISTRY) is refs.REFS_REGISTRY["eurlex-refs"]
    assert lookup_mod.owner_of_namespace("celex") == "eurlex-refs"


def test_louise_lookup_answers_the_records_keys(store):
    got = lookup_mod.lookup(f"celex:{ACT}", store=store)
    assert [f["kind"] for f in got["facts"]] == ["act"] and got["facts"][0]["label"] == TITLE
    got = lookup_mod.lookup(f"celex:{CONS}", store=store)
    assert got["facts"][0]["fields"]["work_celex"] == ACT
    got = lookup_mod.lookup("eli:eu:reg/2016/1036/2018-06-08", store=store)
    assert got["facts"] and got["facts"][0]["source_id"] == "eurlex-refs"
    assert lookup_mod.lookup("celex:39999R9999", store=store)["unresolved"] == ["celex:39999R9999"]


@pytest.mark.parametrize("text", [
    "Regulation (EU) 2016/679", "Règlement (UE) 2016/679", "Verordnung (EU) 2016/679",
    "Regolamento (UE) 2016/679", "the regulation (EU) 2016/679 applies", "CELEX 32016R0679", "32016R0679",
])
def test_a_citation_of_2016_679_in_any_language_names_the_act(store, text):
    got = refs.RESOLVER.resolve(text, store)
    assert [e.entity_id for e in got] == ["celex:32016R0679"], text
    assert got[0].kind == "act" and got[0].source_id == "eurlex-refs"


@pytest.mark.parametrize("text, celex", [
    ("Directive 95/46/EC", "31995L0046"), ("Richtlinie 95/46/EG", "31995L0046"),
    ("direttiva 95/46/CE", "31995L0046"), ("Regulation (EU) 2016/1036", ACT),
    ("Regulation (EU) No 1036/2016", ACT), ("Directive (EU) 2019/790", DIR),
])
def test_the_older_numbering_and_the_other_types(store, text, celex):
    assert [e.entity_id for e in refs.RESOLVER.resolve(text, store)] == [f"celex:{celex}"], text


def test_an_article_stays_on_the_label_and_the_act_is_the_entity(store):
    got = refs.RESOLVER.resolve("Article 5 of Regulation (EU) 2016/679", store)
    assert [e.entity_id for e in got] == ["celex:32016R0679"] and got[0].label.endswith("— Article 5")
    assert eref.citations("article 6, paragraphe 1, point f) du règlement (UE) 2016/679") == [
        {"celex": "32016R0679", "art": "article 6, paragraphe 1, point f)"}]
    assert eref.citations("Artikel 6 der Verordnung (EU) 2016/679")[0]["art"] == "Artikel 6"


def test_a_consolidation_s_celex_names_its_act(store):
    assert [e.entity_id for e in refs.RESOLVER.resolve(f"CELEX {CONS}", store)] == [f"celex:{ACT}"]


def test_unknown_acts_and_plain_fractions_resolve_to_nothing(store):
    assert refs.RESOLVER.resolve("Regulation (EU) 2099/1", store) == []
    assert eref.citations("a ratio of 3/4 and 1/2 of the members") == []
    assert eref.citations("no digits here at all") == []


def test_expanding_an_act_pins_its_versions_as_facts(store):
    act = refs.RESOLVER.resolve("Regulation (EU) 2016/1036", store)[0]
    reached = refs.RESOLVER.expand_bounded(act, store, 10)
    assert len(reached) == 1 and reached[0].table == "version" and reached[0].total == 3


def test_the_scan_is_anchored_on_digits_and_fast():
    """It runs on every chunk at indexing: a long text with no citation
    costs one C-level search, and a dense one stays linear."""
    prose = "The Member States shall ensure that the measures are effective. " * 6_000
    t = time.perf_counter()
    assert eref.citations(prose) == []
    assert time.perf_counter() - t < 0.05
    dense = ("Article 5 of Regulation (EU) 2016/679 and Directive 95/46/EC; " * 2_000)
    t = time.perf_counter()
    assert len(eref.citations(dense)) == 2
    assert time.perf_counter() - t < 2.0


def test_the_record_writes_only_registered_ids_into_keys(tmp_path):
    from tests.test_eurlex import _doc
    root = build_corpus(tmp_path / "c")
    owners = {ns for s in refs.REFS_REGISTRY.values() for ns in (getattr(s, "namespaces", ()) or ())}
    for celex in (ACT, CONS, DIR):
        for key in _doc(root, celex).keys:
            ns = key.partition(":")[0]
            assert ns in owners, key
            if ns == "eli":
                assert refs.REFS_REGISTRY["eli-refs"].route(key, refs.REFS_REGISTRY) is refs.REFS_REGISTRY["eurlex-refs"]
            assert refs.REFS_REGISTRY["eurlex-refs"].key_lookup(key) is not None, key


def test_the_entity_is_an_entity():
    assert isinstance(Entity(entity_id="celex:1", kind="act", source_id="eurlex-refs"), Entity)
    assert Path(eref.__file__).name == "eurlex.py"
