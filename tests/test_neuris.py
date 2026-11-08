"""NeuRIS — German federal law, one record per expression (app/plugins/neuris;
docs/notes/legal-fr-de-eu.md §3): the LegalDocML.de record against the
portal's own fixtures (tests/fixtures/neuris/README.md: test data, a real
format), the origin unit and its hash, discovery and the id, the reference
source and its resolver, the relations, the registry and the recipe."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import tomllib

from app import chunking, plugin_io, refs
from app import collections as collections_mod
from app.acquire import specs
from app.ask import lookup as ask_lookup
from app.pack import slicing
from app.plugins import eli
from app.plugins.base import DiscoverState, Mutability, RefSyncState
from app.plugins.neuris import SOURCE, ldml
from app.plugins.neuris import source as nsrc
from app.refs import neuris as nref
from app.refs import store as refs_store
from tests import neuris_support as ns

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def corpus(tmp_path):
    return ns.corpus(tmp_path / "neuris-corpus")


def _refs(root):
    return list(SOURCE.discover(DiscoverState(corpus_root=root)))


def _doc(ref):
    return plugin_io.canonical_doc(SOURCE, {"native_id": ref.native_id, "path": ref.locator})


def _by_expression(root) -> dict:
    out = {}
    for r in _refs(root):
        if r.error is None:
            out["/".join(Path(r.locator).parent.parts[-8:])] = r
    return out


# ---- discovery and identity ---------------------------------------------------------------

def test_every_expression_is_a_record_in_id_order_and_a_date_out_of_range_is_refused(corpus):
    refs_ = _refs(corpus)
    good = [r for r in refs_ if r.error is None]
    bad = [r for r in refs_ if r.error is not None]
    assert len(good) == 7
    assert [int(r.native_id) for r in good] == sorted(int(r.native_id) for r in good)
    # the portal's test data has an expression dated 2920: refused loudly, never wrapped
    assert len(bad) == 1 and bad[0].scope == "container" and "2920-01-01" in bad[0].error
    for r in good:
        expression = "/".join(Path(r.locator).parent.parts[-8:])
        assert int(r.native_id) == nsrc.native_id_of(expression)
        assert eli.id_date(int(r.native_id)).isoformat() == expression.split("/")[5], \
            "the id sorts by the expression's point in time"


def test_the_id_is_the_eli_layers_and_is_collision_checked(corpus, monkeypatch):
    nid = nsrc.native_id_of(ns.S1016)
    assert nid == eli.expression_native_id("2023-04-26", ("bgbl-1", "2000", "s1016"), "2023-04-26/10/deu")
    monkeypatch.setattr(nsrc, "native_id_of", lambda expression: 42 << eli.HASH_BITS)
    refs_ = _refs(corpus)
    errors = [r for r in refs_ if r.error and "collides" in r.error]
    assert len(errors) == len(refs_) - 1, "the first holder keeps the id, every other is refused"


def test_the_latest_manifestation_is_the_records_and_an_incomplete_pair_waits(tmp_path):
    root = tmp_path / "corpus"
    ns.lay_out(root, ns.S1016)
    newer = ns.lay_out(root, ns.S1016, generated="2026-11-01")
    lonely = root / ns.N130 / "2025-08-31.zip"             # the zip landed, its JSON not yet
    lonely.parent.mkdir(parents=True)
    lonely.write_bytes(ns.zip_bytes(ns.N130))
    got = _refs(root)
    assert [Path(r.locator) for r in got] == [newer]


def test_origin_is_the_zip_under_the_corpus_and_reads_back(corpus):
    ref = _by_expression(corpus)[ns.S1016]
    origin = plugin_io.origin_locator(SOURCE, ref.locator, corpus)
    assert origin == f"{ns.S1016}/2023-04-26.zip"
    local, locator = plugin_io.local_locator(SOURCE, origin, corpus)
    assert locator == ref.locator and local.is_file()


def test_origin_coverage(corpus):
    """tests/origin_coverage.py: parse reads nothing fetch did not hash, and
    touching the JSON beside the zip changes the document's signature."""
    from tests.origin_coverage import assert_covered
    for ref in _by_expression(corpus).values():
        fetched = assert_covered(SOURCE, ref, corpus)
        assert {p.suffix for p in fetched} == {".zip", ".json"}


# ---- the origin unit's hash -----------------------------------------------------------------

def test_origin_hash_covers_the_members_not_the_zip_that_carried_them():
    api = json.dumps(ns.api_json(ns.S1016)).encode()
    a = ns.zip_bytes(ns.S1016)
    b = ns.zip_bytes(ns.S1016, reverse=True, compression=zipfile.ZIP_STORED, date_time=(2030, 1, 2, 3, 4, 6))
    assert a != b, "two differently built zips"
    ha = nsrc.origin_hash(nsrc.read_members(a), api)
    assert ha == nsrc.origin_hash(nsrc.read_members(b), api)
    assert len(ha) == 32 and all(c in "0123456789abcdef" for c in ha), "BLAKE2b-128 (09 §9.5)"
    # a member's bytes, a member's name, a member more, the JSON: each moves it
    members = nsrc.read_members(a)
    name = next(n for n in members if n.endswith("regelungstext-1.xml") and "anlage" not in n)
    assert nsrc.origin_hash({**members, name: members[name] + b" "}, api) != ha
    assert nsrc.origin_hash({k.replace("2023-04-26/r", "2023-04-27/r"): v for k, v in members.items()}, api) != ha
    assert nsrc.origin_hash({**members, "x/bild.png": b""}, api) != ha
    assert nsrc.origin_hash(members, api + b" ") != ha


def test_the_fetched_hash_is_the_records_and_a_rebuilt_zip_keeps_it(tmp_path):
    root = tmp_path / "corpus"
    z = ns.lay_out(root, ns.S1016)
    [ref] = _refs(root)
    raw = SOURCE.fetch(ref)
    doc = SOURCE.parse(raw)
    assert doc.raw_hash == raw.raw_hash
    z.write_bytes(ns.zip_bytes(ns.S1016, reverse=True, date_time=(2031, 5, 5, 5, 5, 6)))
    assert SOURCE.fetch(_refs(root)[0]).raw_hash == raw.raw_hash, "the origin builds the zip on request"


# ---- the record ---------------------------------------------------------------------------------

def test_the_record_carries_the_eli_set_the_keys_and_the_validity(corpus):
    doc = _doc(_by_expression(corpus)[ns.S1016])
    x = doc.extra
    assert doc.uid == f"neuris:{nsrc.native_id_of(ns.S1016)}"
    assert doc.language == "de" and doc.title.startswith("FrSaftErfrischV — Fiktive Fruchtsaft")
    assert doc.keys == sorted(["eli:de:bund/bgbl-1/2000/s1016", "jurabk:FrSaftErfrischV",
                               "jurabk:RisFrSaftErfrischV"])
    assert (x["eli_jurisdiction"], x["eli_work"], x["eli_expression"]) == \
        ("DE", "eli/bund/bgbl-1/2000/s1016", ns.S1016)
    assert (x["eli_number"], x["eli_version"], x["eli_version_date"]) == ("s1016", "10", "2023-04-26")
    assert (x["eli_date_document"], x["eli_date_publication"]) == ("2000-05-24", "2000-05-27")
    assert x["valid_from"] == "2023-04-29" and "valid_to_stated" not in x, "in force, no stated end"
    assert x["in_force"] is True and x["legal_force"] == "InForce"
    assert x["manifestation"] == "2023-04-26" and x["point_in_time"] == "2023-04-26"
    assert x["gazette"] == "BGBl I 2000, s1016" and x["alternate_name"].startswith("Fruchtsaft-")
    assert doc.date == "2000-05-24"
    assert set(x) <= set(nsrc.EXTRA_KEYS)


def test_everything_the_origin_asserts_is_kept(corpus):
    """Invariant 11: the API's answer whole, the meta block lossless, the
    ris: fields by name, the preface, every article's ids, every member."""
    ref = _by_expression(corpus)[ns.S1016]
    doc = _doc(ref)
    x = doc.extra
    assert x["api"] == ns.api_json(ns.S1016)
    xml = (ns.FIX / ns.S1016 / "2023-04-26" / "regelungstext-1.xml").read_bytes()
    from xml.etree import ElementTree as ET
    meta = next(e for e in ET.fromstring(xml).iter() if e.tag == ldml.LDML + "meta")

    def count(nodes):
        return sum(1 + count(n.get("children", [])) for n in nodes)
    assert count([x["meta"]]) == sum(1 for _ in meta.iter()), "every element of <meta>, once"
    guids = {e.get("GUID") for e in meta.iter() if e.get("GUID")}
    flat = json.dumps(x["meta"])
    assert all(g in flat for g in guids), "the origin's ids kept"
    ris = x["ris"]
    assert ris["abkuerzung"] == {"value": "RisFrSaftErfrischV", "refersTo": "interne-abkuerzung"}
    assert [s["type"] for s in ris["standangabe"]] == ["stand", "neuf", "hinweis"]
    assert ris["vollzitat"].startswith("Fiktive Fruchtsaft")
    assert x["frbr"]["expression"]["FRBRalias"]["nachfolgende-version-id"]
    assert x["preface"]["short_title"] == "Fruchtsaft- und Erfrischungsgetränkeverordnung"
    assert {"refersTo": "ausfertigung-datum", "date": "2000-05-27"} in x["preface"]["dates"]
    assert len(x["lifecycle"]) == 24 and x["notes"] and x["preamble"] and x["conclusions"]
    assert all(a.get("GUID") and a.get("period") for a in x["articles"])
    assert [m["name"].rsplit("/", 1)[-1] for m in x["members"]] == \
        ["anlage-regelungstext-1.xml", "regelungstext-1.xml"]
    assert x["annexes"][0]["title"].startswith("Anlage T1") and x["annexes"][0]["notes"]
    assert x["attachment_refs"][0]["href"].endswith("anlage-regelungstext-1.xml")


def test_images_and_the_pdf_are_members_of_the_record(corpus):
    x = _doc(_by_expression(corpus)[ns.S101]).extra
    assert sorted(m["name"].rsplit("/", 1)[-1] for m in x["members"]) == \
        ["anlage-regelungstext-1.xml", "bild_1.jpg", "bild_1.png", "dokument.pdf", "regelungstext-1.xml"]


def test_articles_carry_their_doknr_and_their_period(corpus):
    x = _doc(_by_expression(corpus)[ns.S1234[0]]).extra
    assert [a["doknr"] for a in x["articles"]] == ["DKNR000000000BJNE00010000", "DKNR000000000BJNE00020000"]
    assert all("valid" in a for a in x["articles"]), "each article's Fassung, resolved through the lifecycle"
    assert x["ris"]["doknr"][0] == {"source": "#art-z1", "value": "DKNR000000000BJNE00010000"}


def test_sections_are_paragraph_articles_then_each_anlage(corpus):
    doc = _doc(_by_expression(corpus)[ns.S1016])
    labels = [label for label, _ in doc.sections]
    assert labels[0] == "Abschnitt 1 — Anwendungsbereich › § 1 — Anwendungsbereich"
    assert labels[-1].startswith("Anlage T1 (zu § 4 Absatz 2")
    body = doc.sections[0][1]
    assert body.split("\n")[:2] == ["§ 1 — Anwendungsbereich",
                                    "(1) Die in Anlage 1 aufgeführten Erzeugnisse unterliegen dieser "
                                    "Verordnung, soweit sie zum gewerbsmäßigen Vertrieb bestimmt sind."]
    assert "Apfelsaft" in doc.sections[-1][1], "the annex's table, row by row"
    art = _doc(_by_expression(corpus)[ns.N130])
    assert [label for label, _ in art.sections] == \
        ["§ 1 — Gültiger Paragraph", "§ 2 — Außerkrafttreten", "§ 3 — Inkrafttreten"]


def test_no_text_of_the_body_or_the_annexes_is_lost(corpus):
    doc = _doc(_by_expression(corpus)[ns.S1016])
    text = " ".join(f"{label} {body}" for label, body in doc.sections).split()
    d = ns.FIX / ns.S1016 / "2023-04-26"
    for words in (ns.text_words((d / "regelungstext-1.xml").read_bytes(), "body"),
                  ns.text_words((d / "anlage-regelungstext-1.xml").read_bytes(), "mainBody")):
        missing = [w for w in words if w not in text]
        assert missing == [], missing[:10]


def test_an_empty_body_is_a_record_with_no_section(corpus):
    doc = _doc(_by_expression(corpus)[ns.N999])
    assert doc.sections == [] and doc.extra["valid_from"] == "2025-01-01"


def test_one_chunk_per_article_and_annex_then_the_outline(corpus):
    from app import chunking_legal
    doc = _doc(_by_expression(corpus)[ns.S1016])
    chunks = list(chunking_legal.chunk_legal_article(doc.uid, 1, doc.sections,
                                                     params=chunking_legal.SHARED_LAYER["chunker"]["params"],
                                                     title=doc.title))
    *articles, outline = chunks
    assert len(articles) == len([s for s in doc.sections if s[1].strip()])
    assert articles[0]["text"].startswith(f"{doc.title} › {doc.sections[0][0]}\n§ 1 — Anwendungsbereich")
    assert outline["chapter"] == chunking_legal.OUTLINE and outline["text"].startswith(doc.title + "\n")


def test_the_chunker_is_the_shared_spaces():
    from app.hold import template
    [layer] = template.default_layers(SOURCE)
    from app import chunking_legal
    assert layer.to_dict() == chunking_legal.SHARED_LAYER, "the shared legal layer"
    assert SOURCE.chunking_profile == "legal_article" and SOURCE.mutability is Mutability.MUTABLE


# ---- validity: the stated start and end ----------------------------------------------------------

def _redo(tmp_path, expression, **api_over):
    root = tmp_path / "c"
    ns.lay_out(root, expression, api=ns.api_json(expression, **api_over))
    return _doc(_refs(root)[0])


def test_a_stated_end_that_disagrees_is_an_issue_and_both_are_kept(tmp_path):
    doc = _redo(tmp_path, ns.N130, temporalCoverage="2025-05-06/2037-04-30")
    assert [i.code for i in doc.issues] == ["stated-end-disagrees"]
    assert doc.extra["valid_to_stated"] == "2037-04-30" and doc.extra["ris"]["ausserkraft"] == "2037-03-31"


def test_a_stated_start_that_disagrees_is_an_issue_and_the_apis_is_the_records(tmp_path):
    doc = _redo(tmp_path, ns.N130, temporalCoverage="2025-05-07/2037-03-31")
    assert [i.code for i in doc.issues] == ["stated-start-disagrees"]
    assert doc.extra["valid_from"] == "2025-05-07"


def test_no_stated_start_is_reported_and_never_guessed(corpus):
    doc = _doc(_by_expression(corpus)[ns.S101])
    assert "valid_from" not in doc.extra and [i.code for i in doc.issues] == ["no-stated-start"]


def test_a_conditional_entry_into_force_is_an_open_start_said_on_the_record(tmp_path):
    xml = (ns.FIX / ns.S101 / "1991-01-01" / "regelungstext-1.xml").read_text(encoding="utf-8")
    xml = xml.replace("</ris:abkuerzung>", "</ris:abkuerzung><ris:bedingtesInkrafttreten/>", 1)
    files = {p.name: p.read_bytes() for p in (ns.FIX / ns.S101 / "1991-01-01").iterdir()}
    files["regelungstext-1.xml"] = xml.encode("utf-8")
    root = tmp_path / "c"
    ns.lay_out(root, ns.S101, data=ns.zip_bytes(ns.S101, files=files))
    doc = _doc(_refs(root)[0])
    assert doc.extra["conditional_entry_into_force"] is True and "valid_from" not in doc.extra
    assert doc.issues == []


def test_the_force_status_is_the_origins_word(tmp_path):
    assert _redo(tmp_path, ns.N130, legislationLegalForce="NotInForce").extra["in_force"] is False
    assert "in_force" not in _redo(tmp_path / "b", ns.N130, legislationLegalForce="PartiallyInForce").extra


def test_the_versions_of_one_work_share_its_eli_and_follow_each_other(corpus):
    from app import validity
    v = validity.from_plugin(SOURCE)
    got = [validity.of_doc(_doc(_by_expression(corpus)[e]), v, SOURCE) for e in ns.S1234]
    assert {g["work"] for g in got} == {"eli/bund/bgbl-1/2020/s1234"}
    assert [g["from"] for g in got] == [20200101, 20210101, 20220101]


def test_an_annex_the_act_names_and_the_zip_lacks_is_an_issue(tmp_path):
    root = tmp_path / "c"
    ns.lay_out(root, ns.S1016, data=ns.zip_bytes(ns.S1016, drop=("anlage-regelungstext-1.xml",)))
    doc = _doc(_refs(root)[0])
    assert [i.code for i in doc.issues] == ["annex-missing"]
    assert all(i.code in SOURCE.ISSUES for i in doc.issues)


def test_the_expression_is_read_back_from_what_the_origin_returned(tmp_path):
    xml = (ns.FIX / ns.N130 / "2025-08-31" / "regelungstext-1.xml").read_text(encoding="utf-8")
    moved = xml.replace('value="eli/bund/bgbl-1/2025/130/2025-05-05/1/deu"',
                        'value="eli/bund/bgbl-1/2025/130/2025-05-05/2/deu"', 1)
    assert moved != xml
    root = tmp_path / "c"
    ns.lay_out(root, ns.N130, data=ns.zip_bytes(ns.N130, files={"regelungstext-1.xml": moved.encode()}))
    doc = _doc(_refs(root)[0])
    assert doc.extra["eli_expression"] == "eli/bund/bgbl-1/2025/130/2025-05-05/2/deu"
    assert [i.code for i in doc.issues] == ["expression-eli-mismatch"]


# ---- slicing -------------------------------------------------------------------------------

def test_the_slice_is_the_year_of_the_point_in_time_and_the_hint_agrees(corpus):
    scheme = slicing.scheme_for("neuris")
    for ref in _by_expression(corpus).values():
        doc = _doc(ref)
        want = scheme.slice_of(doc, int(ref.native_id))
        assert want == f"year={doc.extra['point_in_time'][:4]}"
        assert slicing.slice_hint("neuris", {"native_id": ref.native_id, "path": ref.locator}) == want
        slicing.check_slice_name(want)


# ---- relations and closures (the hand-made fixture) -------------------------------------------------

@pytest.fixture
def handmade(tmp_path):
    files = {p.name: p.read_bytes() for p in (ns.FIX / ns.N130 / "2025-08-31").iterdir()}
    files["regelungstext-1.xml"] = ns.HANDMADE.read_bytes()
    assert b"HAND-MADE" in files["regelungstext-1.xml"]
    root = tmp_path / "c"
    ns.lay_out(root, ns.N130, data=ns.zip_bytes(ns.N130, files=files),
               api=ns.api_json(ns.N130, main=ns.HANDMADE.read_bytes()))
    return _doc(_refs(root)[0])


def test_the_verweise_and_modifications_are_eli_edges(handmade):
    x = handmade.extra
    assert [v["typ"] for v in x["ris"]["verweise"]] == ["Zitierung", "Zitierung"]
    assert x["ris"]["verweise"][0]["targetWorkEli1"] == "eli/bund/bgbl-1/2000/s1016"
    edges = sorted((e.type, e.to) for e in SOURCE.relations(handmade))
    assert edges == sorted([
        ("eli:repeals", "eli:de:bund/bgbl-1/2020/s1234"),       # typed repeal, the whole act
        ("eli:amends", "eli:de:bund/bgbl-1/2000/s1016"),         # a substitution in an article
        ("eli:amends", "eli:de:bund/bgbl-1/1991/s101"),          # a repeal of a part amends
        ("eli:cites", "eli:de:bund/bgbl-1/2000/s1016"),          # ris:verweis by its Work ELI
        ("eli:cites", "jurabk:SGB 5"),                           # ris:verweis by abbreviation only
        ("eli:cites", "eli:de:bund/bgbl-1/1991/s101"),           # an <akn:ref> to an ELI
    ])


def test_only_a_typed_whole_act_repeal_closes_and_from_the_day_it_names(handmade):
    assert list(SOURCE.closures(handmade)) == [("eli:de:bund/bgbl-1/2020/s1234", "2022-01-01")]
    assert nsrc.modification_target({"destination": "eli/bund/bgbl-1/2020/s1234"}) == \
        ("eli:de:bund/bgbl-1/2020/s1234", None, True)
    assert nsrc.modification_target({"destination": "#art-z1"}) is None


def test_the_fixtures_carry_no_verweise(corpus):
    """Whether the origin fills them is owed from the node (the plugin's docstring)."""
    for ref in _by_expression(corpus).values():
        assert "verweise" not in _doc(ref).extra.get("ris", {})


# ---- the reference source, the keys and the resolver --------------------------------------------------

@pytest.fixture
def store(tmp_path):
    root = tmp_path / "refs"
    ns.corpus(root / "neuris")
    # an abbreviation with a space, as NeuRIS writes "SGB 5"
    ns.lay_out(root / "neuris", ns.S1016, api=ns.api_json(ns.S1016, abbreviation="SGB 5"))
    con = refs.connect(tmp_path / "refs.db")
    for s in refs.REFS_REGISTRY.values():
        refs_store.ensure_schema(con, s.schema())
    got = refs_store.sync_source(con, refs.REFS_REGISTRY["neuris-refs"], RefSyncState(ref_root=root))
    assert got["rows"] > 0
    nref.invalidate_cache()
    return refs.RefStore(con, [s.schema() for s in refs.REFS_REGISTRY.values()])


def test_the_acts_their_versions_and_abbreviations_are_synced_from_the_json(store):
    acts = {r["key"]: r for r in store.rows("gesetz")}
    assert set(acts) == {"eli:de:bund/bgbl-1/2000/s1016", "eli:de:bund/bgbl-1/2020/s1234",
                         "eli:de:bund/bgbl-1/2025/130", "eli:de:bund/bgbl-1/2025/999",
                         "eli:de:bund/bgbl-1/1991/s101"}
    a = acts["eli:de:bund/bgbl-1/2000/s1016"]
    assert (a["eli"], a["abbreviation"], a["date"], a["published"]) == \
        ("eli/bund/bgbl-1/2000/s1016", "SGB 5", "2000-05-24", "2000-05-27")
    versions = store.rows("fassung", {"work": "eli:de:bund/bgbl-1/2020/s1234"}, order_by="valid_from")
    assert [v["valid_from"] for v in versions] == ["2020-01-01", "2021-01-01", "2022-01-01"], \
        "2920 is outside the id's range: no record, no row"
    assert versions[0]["uid"] == f"neuris:{nsrc.native_id_of(ns.S1234[0])}"
    assert versions[0]["manifestation"] == f"{ns.S1234[0]}/2026-07-01.zip"
    assert {r["abk"] for r in store.rows("abkuerzung", {"work": "eli:de:bund/bgbl-1/2000/s1016"})} == \
        {"SGB 5", "RisFrSaftErfrischV"}
    assert store.owner_of("gesetz") == "neuris-refs"


def test_a_key_with_a_space_survives_record_refs_and_lookup(tmp_path, store):
    """05 §5.2 is silent on the characters an id holds and says "verbatim":
    `jurabk:SGB 5` is written, synced and looked up as the origin wrote it."""
    root = tmp_path / "c"
    ns.lay_out(root, ns.S1016, api=ns.api_json(ns.S1016, abbreviation="SGB 5"))
    doc = _doc(_refs(root)[0])
    assert "jurabk:SGB 5" in doc.keys
    for key in doc.keys:
        got = ask_lookup.lookup(key, store=store)
        assert got["unresolved"] == [] and got["facts"], key
    facts = ask_lookup.lookup("jurabk:SGB 5", store=store)["facts"]
    assert [f["fields"]["work"] for f in facts] == ["eli:de:bund/bgbl-1/2000/s1016"]
    assert facts[0]["source_id"] == "neuris-refs" and facts[0]["kind"] == "abkuerzung"
    assert ask_lookup.lookup("jurabk:SGB  5", store=store)["unresolved"] == ["jurabk:SGB  5"], \
        "verbatim: no normalisation either way"


def test_an_eli_key_is_routed_to_this_source_and_found(store):
    src = refs.REFS_REGISTRY["neuris-refs"]
    assert refs.REFS_REGISTRY["eli-refs"].route("eli:de:bund/bgbl-1/2000/s1016", refs.REFS_REGISTRY) is src
    assert src.key_lookup("eli:de:bund/bgbl-1/2000/s1016") == ("gesetz", "key", "eli:de:bund/bgbl-1/2000/s1016")
    assert src.key_lookup(f"eli:de:{ns.S1016[4:]}") == ("fassung", "eli", ns.S1016)
    assert src.key_lookup("eli:it:id/x") is None and src.key_lookup("urn:x") is None
    work = ask_lookup.lookup("eli:de:bund/bgbl-1/2000/s1016", store=store)
    assert work["facts"][0]["kind"] == "gesetz" and work["facts"][0]["label"].startswith("Fiktive Fruchtsaft")
    expr = ask_lookup.lookup(f"eli:de:{ns.S1016[4:]}", store=store)
    assert expr["facts"][0]["kind"] == "fassung"
    assert ask_lookup.owner_of_namespace("jurabk") == "neuris-refs"


@pytest.mark.parametrize("text,abk,label", [
    ("Nach § 2 Abs. 1 RisAbk gilt", "RisAbk", "RisAbk — § 2 Abs. 1"),
    ("Art. 1 RisAbk", "RisAbk", "RisAbk — Art. 1"),
    ("§ 5 Abs. 2 Satz 1 Nr. 3 GeGuGe 2025.", "GeGuGe 2025", "GeGuGe 2025 — § 5 Abs. 2 Satz 1 Nr. 3"),
    ("§§ 1 bis 3 FrSaftErfrischV", None, None),
    ("siehe § 12 Abs. 1 SGB 5, § 1 GeGuGe 2025", "SGB 5", "SGB 5 — § 12 Abs. 1"),
    ("Artikel 3 Buchst. a des EmptyBodyTestG", "EmptyBodyTestG", "EmptyBodyTestG — Artikel 3 Buchst. a"),
])
def test_a_citation_names_the_act_by_its_synced_abbreviation(store, text, abk, label):
    got = refs.RESOLVER.resolve(text, store)
    mine = [e for e in got if e.source_id == "neuris-refs"]
    if abk is None:                    # FrSaftErfrischV was replaced by "SGB 5" in this store
        assert mine == []
        return
    work = store.rows("abkuerzung", {"abk": abk})[0]["work"]
    assert mine[0].entity_id == work and mine[0].kind == "gesetz" and mine[0].label == label


def test_a_range_and_the_resolver_on_the_default_store(tmp_path):
    root = tmp_path / "refs"
    ns.corpus(root / "neuris")
    con = refs.connect(tmp_path / "refs.db")
    for s in refs.REFS_REGISTRY.values():
        refs_store.ensure_schema(con, s.schema())
    refs_store.sync_source(con, refs.REFS_REGISTRY["neuris-refs"], RefSyncState(ref_root=root))
    nref.invalidate_cache()
    st = refs.RefStore(con, [s.schema() for s in refs.REFS_REGISTRY.values()])
    [e] = nref.CitationResolver().resolve("§§ 1 bis 3 FrSaftErfrischV", st)
    assert e.entity_id == "eli:de:bund/bgbl-1/2000/s1016" and e.label == "FrSaftErfrischV — §§ 1 bis 3"
    reached = refs.RESOLVER.expand_bounded(e, st, 10)
    assert [r.table for r in reached] == ["fassung"] and reached[0].total == 1, \
        "the act's versions pinned as facts, by the declared link"


def test_the_resolver_never_reads_the_store_without_a_mark():
    class NoStore:
        def rows(self, *a, **k):
            raise AssertionError("the pre-check must answer first")
    assert nref.CitationResolver().resolve("Ein Text ohne Paragraphenzeichen. " * 1000, NoStore()) == []


def test_the_abbreviations_are_the_tables_never_typed_by_hand():
    import ast
    tree = ast.parse((ROOT / "app" / "refs" / "neuris.py").read_text(encoding="utf-8"))
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not strings & {"BGB", "GG", "StGB", "SGB V", "SGB 5", "ZPO"}, "no abbreviation in the code"
    assert nref.citations("§ 823 Abs. 1 BGB", {"BGB"}) == [{"abk": "BGB", "provision": "§ 823 Abs. 1", "at": 0}]
    assert nref.citations("Art. 1 GG", {"GG"})[0]["provision"] == "Art. 1"
    assert nref.citations("§ 5 Abs. 2 Satz 1 Nr. 3 StGB", {"StGB"})[0]["provision"] == "§ 5 Abs. 2 Satz 1 Nr. 3"
    assert nref.citations("§§ 1 bis 3 BGB", {"BGB"})[0]["provision"] == "§§ 1 bis 3"
    assert nref.citations("§ 823 Abs. 1 BGB", set()) == []


# ---- the registry and the recipe ------------------------------------------------------------------------

def test_the_plugin_its_spec_and_its_reference_source_are_registered():
    assert collections_mod.PLUGIN_REGISTRY["neuris"] is SOURCE
    assert specs.ACQUIRE_REGISTRY["neuris"] is specs.NEURIS_SPEC
    assert specs.NEURIS_SPEC.endpoints[0].url == "https://testphase.rechtsinformationen.bund.de"
    assert specs.NEURIS_SPEC.endpoints[0].max_conns == 2 and specs.NEURIS_SPEC.etiquette.max_conns == 2
    assert "neuris-refs" in refs.REFS_REGISTRY
    assert SOURCE.license_class.value == "green" and specs.NEURIS_SPEC.terms_text_path is None
    assert SOURCE.default_profile["validity"] == {"kind": "versions-per-work", "from": "extra.valid_from",
                                                  "work": "extra.eli_work"}
    from app.hold import template
    template.corpus_credit(SOURCE)


SHARED_SPACE = '''[space]
dim = 2048
dtype = "f16"
# 8191, not 8192: llama.cpp embeds at most context_size − 1 tokens, and the model runs
# context_size 8192 (München's dry run, 2026-11-08); wikidata's space on the same model is 8191
ctx = 8191
cut = "tokens"
pooling = "mean"
normalise = true
exactness = "exact"
epoch = "0"
evaluation = ""
# tolerance: measured by `new`, never typed

[space.calibration]
set = "sha256:7d06a2f6a4a773da01a517cb30571cac421753df3e30b20fca4716e5441fbf0f"

[space.calibration.quorum]
n = 16
min_pass = 12

[space.document]
instruction = "passage: "

[space.query]
instruction = "query: "

[space.encoder]
weights = "sha256:09596cedbdf2a0d164b37083794632be55dcb2aa2abcf02fb995dfdccbecbf2c"
label = "nvidia/Nemotron-3-Embed-1B-BF16 (c0c9fea9; f16 GGUF, llama.cpp 3f8af15a; vega-nemotron-3-embed-1b)"
# [space.encoder.engine]: read on the node with `louise hold embedder run --encoder http://192.168.178.10:30286 --api vega --model vega-nemotron-3-embed-1b`; `new` holds the endpoint to it.
'''


def test_the_recipe_carries_the_shared_space_block_byte_for_byte():
    text = (ROOT / "docs" / "notes" / "neuris-recipe.toml").read_text(encoding="utf-8")
    assert SHARED_SPACE in text
    assert "--space-from legi --cpu-model vega-nemotron-3-embed-1b-cpu" in text and "case e" in text.lower()


def test_the_recipe_loads_through_the_real_loader_as_a_version_11_codex():
    """With an engine as `hold embedder run` reads one (a dummy) and the
    tolerance `new` writes before its dry Codex: the block as it stands."""
    from app.hold import codex as cx
    from app.hold import collection as col
    r = tomllib.loads((ROOT / "docs" / "notes" / "neuris-recipe.toml").read_text(encoding="utf-8"))
    r["space"]["encoder"]["engine"] = {
        "name": "localai/llama-cpp", "build": "3f8af15a", "image": "sha256:" + "5d" * 32,
        "args": ["${BACKEND}/run.sh", "--addr", "127.0.0.1:${PORT}"],
        "env": {"GGML_SYCL_ENABLE_DNN": "0"}, "device": "Intel(R) Arc(TM) Pro B70 Graphics",
        "options": {"ContextSize": 8192, "Embeddings": True, "ModelFile": "${WEIGHTS}"}}
    r["space"].setdefault("tolerance", "0.99")
    c = col.dry_codex("neuris", r, platform_image="sha256:" + "1f" * 32)
    assert c.version == cx.DOCUMENT_VERSION == 11
    assert (c.space.dim, c.space.dtype, c.space.ctx, c.space.cut, c.space.exactness) == \
        (2048, "f16", 8191, "tokens", "exact")
    assert c.space.document_instruction == "passage: " and c.space.query.instruction == "query: "
    from app import chunking_legal
    assert [layer.to_dict() for layer in c.layers] == [chunking_legal.SHARED_LAYER]
    assert c.slicing["keys"] == ["year"] and c.profile["validity"]["from"] == "extra.valid_from"
    assert c.license.cls == "green" and c.origin.jurisdiction == "DE"


def test_worker_stages_run_end_to_end(tmp_path, corpus, isolated_config):
    """Discovery, the rdf stage (validity recorded, issues kept beside the
    record) and the chunks the text stages cut, through the worker's own path."""
    from app import db, discovery, worker
    collection = collections_mod.Collection(
        name="neuris", plugin="neuris", profile=None, embed_mode_default=None,
        corpus_dir=corpus, manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")
    con = db.connect(collection)
    discovery.sync_collection(con, collection)
    assert con.execute("SELECT COUNT(*) FROM documents WHERE error IS NULL").fetchone()[0] == 7
    uids = [f"neuris:{nsrc.native_id_of(e)}" for e in ns.S1234]
    for uid in uids + [f"neuris:{nsrc.native_id_of(ns.S1016)}"]:
        row = con.execute("SELECT * FROM documents WHERE uid=?", (uid,)).fetchone()
        worker.stage_rdf(con, row, collection)
    rows = con.execute("SELECT uid, valid_from, valid_to, shown FROM validity WHERE work=? ORDER BY valid_from",
                       ("eli/bund/bgbl-1/2020/s1234",)).fetchall()
    assert [(r["valid_from"], r["valid_to"], r["shown"]) for r in rows] == [
        (20200101, 20210101, 0), (20210101, 20220101, 0), (20220101, None, 1)], \
        "the end derived from the next version's start; the last open"
    row = con.execute("SELECT * FROM documents WHERE uid=?", (f"neuris:{nsrc.native_id_of(ns.S1016)}",)).fetchone()
    assert row["rdf_done"] == 1 and row["title"].startswith("FrSaftErfrischV")
    chunks = worker._load_chunks(row, collection)
    assert len(chunks) == 5 and chunks[-1]["chapter"] == "outline", "four articles, then the outline"
    assert chunks[0]["text"].startswith(row["title"]) and "§ 1 — Anwendungsbereich" in chunks[0]["text"]


#: HAND-MADE (tests/fixtures/neuris/README.md): a `rechtsetzungsdokument` in
#: the shape norm-rechtsetzungsdokument.xsd (ris-search@81b58f82) fixes, as
#: every manifestation zip the node probed carries one (München, 2026-11-08:
#: `rechtsetzungsdokument-1.xml`, 5,706 bytes, beside `regelungstext-1.xml`)
RECHTSETZUNGSDOKUMENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<akn:akomaNtoso xmlns:akn="http://rechtsinformationen.bund.de/schema/norm/0.1">
  <akn:documentCollection name="regelungsentwurf">
    <akn:meta eId="meta-1" GUID="00000000-0000-0000-0000-000000000001">
      <akn:identification eId="meta-1_ident-1" GUID="00000000-0000-0000-0000-000000000002" source="attributsemantik-noch-undefiniert">
        <akn:FRBRWork eId="meta-1_ident-1_frbrwork-1" GUID="00000000-0000-0000-0000-000000000003">
          <akn:FRBRthis eId="meta-1_ident-1_frbrwork-1_frbrthis-1" GUID="00000000-0000-0000-0000-000000000004" value="eli/bund/bgbl-1/2000/s1016/rechtsetzungsdokument-1"/>
        </akn:FRBRWork>
      </akn:identification>
    </akn:meta>
    <akn:collectionBody eId="rdokhauptteil-1" GUID="00000000-0000-0000-0000-000000000005">
      <akn:component eId="rdokhauptteil-1_rdokbestandteil-1" GUID="00000000-0000-0000-0000-000000000006">
        <akn:documentRef eId="rdokhauptteil-1_rdokbestandteil-1_verweis-1" GUID="00000000-0000-0000-0000-000000000007" href="regelungstext-1.xml" showAs="regelungstext"/>
      </akn:component>
      <akn:component eId="rdokhauptteil-1_rdokbestandteil-2" GUID="00000000-0000-0000-0000-000000000008">
        <akn:documentRef eId="rdokhauptteil-1_rdokbestandteil-2_verweis-1" GUID="00000000-0000-0000-0000-000000000009" href="anlage-regelungstext-1.xml" showAs="anlage-regelungstext"/>
      </akn:component>
    </akn:collectionBody>
  </akn:documentCollection>
</akn:akomaNtoso>
"""


def test_the_rechtsetzungsdokument_every_real_zip_carries_is_kept_never_read_as_an_annex(tmp_path):
    """Parser 1 read every XML member besides the main text as an annex, and
    refused the expression on the rechtsetzungsdokument (`<documentCollection>`,
    no `<doc>`) — so every real expression would have been unindexable."""
    root = tmp_path / "c"
    _gen, d = ns.directory(ns.S1016)
    files = {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    files["rechtsetzungsdokument-1.xml"] = RECHTSETZUNGSDOKUMENT
    ns.lay_out(root, ns.S1016, data=ns.zip_bytes(ns.S1016, files=files))
    doc = _doc(_refs(root)[0])
    x = doc.extra
    (rd,) = x["rechtsetzungsdokument"]
    assert rd["member"].endswith("rechtsetzungsdokument-1.xml") and rd["name"] == "regelungsentwurf"
    refs = [c["children"][0]["attrs"]["href"] for c in rd["components"]]
    assert refs == ["regelungstext-1.xml", "anlage-regelungstext-1.xml"]
    assert len(x["annexes"]) == 1, "the annex alone is an annex"
    assert not any("rechtsetzungsdokument" in s[0] for s in doc.sections), "it carries no text to embed"
    assert SOURCE.PARSER_VERSION == 2


def test_an_xml_member_of_a_root_not_named_here_is_kept_whole(tmp_path):
    root = tmp_path / "c"
    _gen, d = ns.directory(ns.S1016)
    files = {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    files["zusatz-1.xml"] = b'<x:root xmlns:x="urn:x"><x:a k="v">text</x:a></x:root>'
    ns.lay_out(root, ns.S1016, data=ns.zip_bytes(ns.S1016, files=files))
    doc = _doc(_refs(root)[0])
    (other,) = doc.extra["other_xml"]
    assert other["member"].endswith("zusatz-1.xml")
    assert other["root"]["children"][0] == {"tag": "a", "attrs": {"k": "v"}, "text": "text"}


def test_an_annex_named_by_its_absolute_ris_url_is_the_zip_member_of_that_name(tmp_path):
    """The real hrefs (München, 2026-11-08, the BtMG's three annexes) are
    absolute on another host than the API's —
    `https://www.ris.bund.de/eli/bund/…/2026-10-27/anlage-regelungstext-1.xml` —
    and name the zip member by their last segment: found there, never fetched."""
    import re
    root = tmp_path / "c"
    _gen, d = ns.directory(ns.S1016)
    files = {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    url = b"https://www.ris.bund.de/eli/bund/bgbl-1/2000/s1016/2023-04-26/10/deu/2023-04-26/anlage-regelungstext-1.xml"
    files["regelungstext-1.xml"], n = re.subn(rb'href="[^"]*anlage-regelungstext-1\.xml"',
                                             b'href="' + url + b'"', files["regelungstext-1.xml"])
    assert n >= 1, "the fixture names its annex"
    ns.lay_out(root, ns.S1016, data=ns.zip_bytes(ns.S1016, files=files))
    doc = _doc(_refs(root)[0])
    assert [i.code for i in doc.issues if i.code == "annex-missing"] == []
    assert doc.extra["attachment_refs"][0]["href"] == url.decode()
    assert len(doc.extra["annexes"]) == 1
    ns.lay_out(root, ns.S1016, data=ns.zip_bytes(ns.S1016, files=files, drop=("anlage-regelungstext-1.xml",)))
    assert [i.code for i in _doc(_refs(root)[0]).issues] == ["annex-missing"]
