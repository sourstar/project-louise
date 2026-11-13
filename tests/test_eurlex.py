"""EUR-Lex on the ELI layer, one record per version of one act in one
language (app/plugins/eurlex; legal-fr-de-eu.md §4; legal-collections-
nemotron.md): discovery over the units, the version id, the language read
from the origin, the body picked by name, every field kept, the sections,
the slices, the relations and the closures — against real Formex and real
notices (tests/fixtures/eurlex, its README for provenance).

No repository holds a notice and the Formex of the same act, so a unit is
put together here from the real Formex and a real notice RE-KEYED to the
Formex's act (`_notice_for`: identifiers replaced, every other byte the
Office's). The closures test builds the notices it needs (`_built`), as
tests/test_normattiva.py builds the shapes the census found."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app import chunking, collections as collections_mod, plugin_io
from app.pack import slicing
from app.plugins import eli
from app.plugins.base import DiscoverState, Mutability, UnindexableDocument
from app.plugins.eurlex import SOURCE, formex, notice, rdf
from app.plugins.eurlex import source as src

FIX = Path(__file__).parent / "fixtures" / "eurlex"
FORMEX = FIX / "formex"
CONS_NOTICE = (FIX / "notices" / "EN-CELEX-02009R1223-20191218.xml").read_bytes()
BRANCH_NOTICE = (FIX / "notices" / "52018PC0340_branch.xml").read_bytes()
OBJECT_NOTICE = (FIX / "notices" / "celex_32018R0196_object_work.xml").read_bytes()

ACT, CONS, DIR = "32016R1036", "02016R1036-20180608", "32019L0790"


RDF_DIR = Path(__file__).resolve().parent / "fixtures" / "eurlex" / "rdf"


def _notice_for(celex: str, base: bytes = BRANCH_NOTICE) -> bytes:
    """A real notice re-keyed to `celex` (the README says why)."""
    if base is CONS_NOTICE:
        m = src._CONSOLIDATED.match(celex)
        act, date = "3" + m["base"], m["date"]
        year, num = act[1:5], str(int(act[6:]))
        typ = {"R": "reg", "L": "dir", "D": "dec"}[act[5]]
        for old, new in ((b"02009R1223-20191218", celex.encode()),
                         (b"2009R1223/20191218", f"{m['base']}/{date}".encode()),
                         (b"2009R1223%2F20191218", f"{m['base']}%2F{date}".encode()),
                         (b"32009R1223", act.encode()),
                         (b"reg/2009/1223/2019-12-18", f"{typ}/{year}/{num}/{date[:4]}-{date[4:6]}-{date[6:]}".encode()),
                         (b"reg/2009/1223/oj", f"{typ}/{year}/{num}/oj".encode()),
                         (b"reg:2009:1223:oj", f"{typ}:{year}:{num}:oj".encode())):
            base = base.replace(old, new)
        return base
    return base.replace(b"52018PC0340", celex.encode())


def _unit(root: Path, lang: str, celex: str, formex_dir: str, notice_bytes: bytes) -> Path:
    unit = root / src.unit_rel(lang, celex)
    unit.mkdir(parents=True, exist_ok=True)
    for p in sorted((FORMEX / formex_dir).iterdir()):
        (unit / p.name).write_bytes(p.read_bytes())
    (unit / src.NOTICE).write_bytes(notice_bytes)
    # the completion mark (parser 3): the work's non-inferred tree, scoped
    rdf_file = RDF_DIR / f"{celex}.rdf"
    tree = rdf.read(rdf_file.read_bytes()) if rdf_file.is_file() else {"nodes": {}, "axioms": []}
    (unit / rdf.TREE_FILE).write_bytes(rdf.dumps(rdf.scope(tree, src.lang3(lang))))
    return unit


def build_corpus(root: Path) -> Path:
    """The acquisition's layout: the act, its consolidation, a directive,
    and a unit still landing (no notice: not a document yet)."""
    _unit(root, "en", ACT, ACT, _notice_for(ACT))
    _unit(root, "en", CONS, CONS, _notice_for(CONS, CONS_NOTICE))
    _unit(root, "en", DIR, DIR, _notice_for(DIR))
    landing = root / src.unit_rel("en", "32024R0903")
    landing.mkdir(parents=True)
    (landing / "L_202400903EN.doc.fmx.xml").write_bytes((FORMEX / "32024R0903" / "L_202400903EN.doc.fmx.xml").read_bytes())
    return root


@pytest.fixture
def corpus(tmp_path) -> Path:
    return build_corpus(tmp_path / "eurlex")


def _refs(root: Path) -> list:
    return list(SOURCE.discover(DiscoverState(corpus_root=root)))


def _doc(root: Path, celex: str, lang: str = "en"):
    ref = next(r for r in _refs(root) if r.locator.endswith(src.unit_rel(lang, celex)))
    return plugin_io.canonical_doc(SOURCE, {"native_id": ref.native_id, "path": ref.locator})


def _nid(day: str, celex: str, lang: str, expr: str) -> int:
    return eli.expression_native_id(day, (celex, lang), expr)


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text))


# ---- discovery -----------------------------------------------------------------------------

def test_every_version_is_a_record_in_start_order(corpus):
    refs = _refs(corpus)
    assert all(r.error is None for r in refs)
    assert [Path(r.locator).name for r in refs] == [ACT, CONS, DIR], "start-day order; the landing unit absent"
    ids = [int(r.native_id) for r in refs]
    assert ids == sorted(ids)
    # an original sorts by its OJ publication, a consolidation by its CELEX date
    assert ids[0] == _nid("2016-06-30", ACT, "en", "ORIGINAL")
    assert ids[1] == _nid("2018-06-08", CONS, "en", "CONSOLIDATED/20180608")
    assert ids[2] == _nid("2019-05-17", DIR, "en", "ORIGINAL")
    assert all(r.mtime and r.size for r in refs)


def test_the_language_is_in_the_id_so_slices_never_collide(corpus):
    _unit(corpus, "fr", ACT, ACT, _notice_for(ACT))
    refs = {(Path(r.locator).parent.parent.name, Path(r.locator).name): int(r.native_id) for r in _refs(corpus)}
    assert refs[("lang=en", ACT)] != refs[("lang=fr", ACT)]


def test_a_collision_is_refused_loudly(corpus, monkeypatch):
    monkeypatch.setattr(eli, "expression_native_id", lambda *a: 42)
    refs = _refs(corpus)
    held = [r for r in refs if r.scope == "document"]
    assert len(held) == 1 and Path(held[0].locator).name == CONS, "the first holder keeps the id"
    assert len([r for r in refs if r.error and "collides" in r.error]) == 2


# ---- the record ----------------------------------------------------------------------------

def test_the_original_is_a_record_with_its_identity_and_validity(corpus):
    doc = _doc(corpus, ACT)
    x = doc.extra
    assert doc.uid == f"eurlex:{_nid('2016-06-30', ACT, 'en', 'ORIGINAL')}"
    assert doc.language == "en" and x["language"] == "en" and x["language_code"] == "ENG"
    assert doc.title.startswith("Regulation (EU) 2016/1036 of the European Parliament and of the Council")
    assert doc.date == "2016-06-08"
    assert (x["version_key"], x["eli_version"], x["celex"], x["work_celex"]) == ("ORIGINAL", "ORIGINAL", ACT, ACT)
    assert x["validity_work"] == f"{ACT}/en"
    # the re-keyed notice states no entry into force: the OJ's publication starts it
    assert x["valid_from"] == "2016-06-30" == x["eli_date_publication"]
    assert x["body_file"] == "L_2016176EN.01002101.xml" and x["wrapper_file"] == "L_2016176EN.01002101.doc.xml"
    assert "authentic" not in x, "only a consolidation says it is not authentic"
    assert f"celex:{ACT}" in doc.keys and doc.keys == sorted(doc.keys)
    assert set(x) <= set(src.EXTRA_KEYS), sorted(set(x) - set(src.EXTRA_KEYS))


def test_the_consolidation_is_another_version_of_the_same_work(corpus):
    doc = _doc(corpus, CONS)
    x = doc.extra
    assert (x["version_key"], x["eli_version"], x["eli_version_date"]) == (
        "CONSOLIDATED/20180608", "CONSOLIDATED", "2018-06-08")
    assert x["valid_from"] == "2018-06-08", "the consolidation's date starts it"
    assert x["work_celex"] == ACT and x["validity_work"] == _doc(corpus, ACT).extra["validity_work"]
    assert x["authentic"] is False, "consolidated texts have no legal effect"
    assert x["consleg"]["START.DATE"] == "20180608" and x["consleg"]["END.DATE"] == "99999999"
    assert x["eli_work"] == "http://data.europa.eu/eli/reg/2016/1036/oj"
    assert x["eli_expression"] == "http://data.europa.eu/eli/reg/2016/1036/2018-06-08"
    assert doc.keys == sorted([f"celex:{ACT}", f"celex:{CONS}", "eli:eu:reg/2016/1036/2018-06-08",
                               "eli:eu:reg/2016/1036/oj"])
    # the amendment marks: which act changed which text
    mods = x["modifications"]
    assert {m["ACTIVE.DOC"] for m in mods if m["pi"] == "CLG.MDFO"} == {"32017R2321", "32018R0825"}
    assert len(mods) == 70 and mods[0]["section"].startswith("Article 2")
    assert doc.issues == []


def test_validity_takes_one_version_per_work_and_language(corpus):
    from app import validity
    v = validity.from_plugin(SOURCE)
    a, c = (validity.of_doc(_doc(corpus, x), v, SOURCE) for x in (ACT, CONS))
    assert a["work"] == c["work"] == f"{ACT}/en" and a["from"] == 20160630 and c["from"] == 20180608


def test_the_language_is_read_from_the_origin_and_a_mismatch_refused(corpus):
    _unit(corpus, "fr", ACT, ACT, _notice_for(ACT))
    with pytest.raises(UnindexableDocument, match="in 'en', the slice lang=fr"):
        _doc(corpus, ACT, "fr")


def test_a_notice_of_another_act_is_refused(corpus):
    unit = corpus / src.unit_rel("en", ACT)
    (unit / src.NOTICE).write_bytes(_notice_for(DIR))
    with pytest.raises(UnindexableDocument, match=f"the notice is {DIR}'s"):
        _doc(corpus, ACT)


def test_the_body_is_picked_by_name_never_by_order():
    for d, body in ((ACT, "L_2016176EN.01002101.xml"), (CONS, "CL2016R1036EN0020010.0001.xml"),
                    ("32024R0903", "L_202400903EN.000101.fmx.xml")):
        files = {p.name: p.read_bytes() for p in (FORMEX / d).iterdir()}
        forward = formex.parse(dict(sorted(files.items())))
        backward = formex.parse(dict(sorted(files.items(), reverse=True)))
        assert forward.body_file == backward.body_file == body
        assert formex.sections(forward) == formex.sections(backward)
    files = {p.name: p.read_bytes() for p in (FORMEX / ACT).iterdir()}
    del files["L_2016176EN.01002101.xml"]
    with pytest.raises(formex.FormexError, match="names the body L_2016176EN.01002101.xml"):
        formex.parse(files)
    files = {p.name: p.read_bytes() for p in (FORMEX / ACT).iterdir()}
    del files["L_2016176EN.01005101.xml"]
    with pytest.raises(formex.FormexError, match="names the annex"):
        formex.parse(files)


def test_formex_6_names_and_an_item_no_annex_is_kept():
    act = formex.parse({p.name: p.read_bytes() for p in (FORMEX / "32024R0903").iterdir()})
    assert act.wrapper_file == "L_202400903EN.doc.fmx.xml" and act.language == "en"
    assert set(act.meta["other_items"]) == {"L_202400903EN.toc.fmx.xml"}
    assert len(act.annexes) == 1 and act.annexes[0].file == "L_202400903EN.002601.fmx.xml"


# ---- every field kept (invariant 11) ---------------------------------------------------------

@pytest.mark.parametrize("celex", [ACT, CONS, DIR])
def test_no_word_of_any_item_or_of_the_notice_is_lost(corpus, celex):
    """Every word of every XML item and of the notice is in the record —
    its sections or its extra — and the wrapper and the notice are kept
    whole, as the origin wrote them (`formex.tree`)."""
    doc = _doc(corpus, celex)
    held = _words("\n".join(b for _, b in doc.sections) + json.dumps(doc.extra, ensure_ascii=False)
                  + " ".join(label for label, _ in doc.sections) + doc.title)
    unit = corpus / src.unit_rel("en", celex)
    for p in sorted(x for x in unit.iterdir() if x.name != rdf.TREE_FILE):
        root = ET.fromstring(p.read_bytes())
        missing = _words(" ".join(root.itertext())) - held
        assert not missing, f"{p.name}: {sorted(missing)[:20]}"
    assert doc.extra["cdm_notice"] == formex.tree(ET.fromstring((unit / src.NOTICE).read_bytes()))
    wrapper = unit / doc.extra["wrapper_file"]
    assert doc.extra["formex_wrapper"] == formex.tree(ET.fromstring(wrapper.read_bytes()))


def test_the_apparatus_leaves_the_text_and_stays_in_the_record(corpus):
    doc = _doc(corpus, ACT)
    x = doc.extra
    assert len(x["notes"]) == 10 and x["notes"][1]["section"] == "Whereas (1)"
    assert x["notes"][1]["text"].startswith("Council Regulation (EC) No 1225/2009 of 30 November 2009")
    assert not any("Position of the European Parliament of 10 May 2016" in b for _, b in doc.sections)
    assert x["references"][0] == {"section": "ANNEX I", "tag": "REF.DOC.OJ",
                                  "@": {"COLL": "L", "DATE.PUB": "20091222", "NO.OJ": "343", "PAGE.FIRST": "51"},
                                  "text": "OJ L 343, 22.12.2009, p. 51"}
    assert x["formex"]["preamble"]["visas"][0].startswith("Having regard to the Treaty on the Functioning")
    assert x["formex"]["final"]["text"] == ["This Regulation shall be binding in its entirety and directly "
                                            "applicable in all Member States."]
    assert [a["identifier"] for a in x["articles"]][:3] == ["001", "002", "003"]
    assert [a["file"] for a in x["annexes"]] == ["L_2016176EN.01005001.xml", "L_2016176EN.01005101.xml"]
    assert x["items"] == [{"name": n, "bytes": (FORMEX / ACT / n).stat().st_size}
                          for n in sorted(p.name for p in (FORMEX / ACT).iterdir())]


def test_the_notice_s_fields_are_read_from_its_real_shapes():
    cons = notice.parse(CONS_NOTICE)
    assert (cons.kind, cons.celex, cons.base_celex, cons.display_language) == (
        "branch", "02009R1223-20191218", "32009R1223", "ENG")
    assert cons.eli == "http://data.europa.eu/eli/reg/2009/1223/2019-12-18"
    assert cons.entry_into_force == ["2019-12-18"] and cons.last_modified == "2020-06-16"
    branch = notice.parse(BRANCH_NOTICE)
    assert branch.display_language == "ENG" and branch.end_of_validity == ["2019-06-05"]
    assert ("WORK_CITES_WORK", "32003L0004") in branch.relations
    assert ("RESOURCE_LEGAL_BASED_ON_RESOURCE_LEGAL", "12016E192") in branch.relations
    names = [i.name for i in branch.items["xhtml"]]
    assert names and all(names) and branch.items["xhtml"][0].size == 172528
    obj = notice.parse(OBJECT_NOTICE)
    assert obj.celex == "32018R0196" and src.eli_data_uri(obj.eli) == "http://data.europa.eu/eli/reg/2018/196/oj"


# ---- sections and chunks ---------------------------------------------------------------------

def test_recitals_articles_and_annexes_are_sections_in_reading_order(corpus):
    doc = _doc(corpus, ACT)
    labels = [label for label, _ in doc.sections]
    assert labels[:2] == ["Whereas (1)", "Whereas (2)"] and len(labels) == 32 + 25 + 2
    assert labels[32] == "Article 1 — Principles" and labels[-2:] == ["ANNEX I", "ANNEX II"]
    art3 = dict(doc.sections)["Article 3 — Determination of injury"]
    assert "2. A determination of injury shall be based on positive evidence" in art3
    assert "\n   (a) the volume of the dumped imports" in art3, "a point indented under its paragraph"
    assert "Regulation (EC) No 1225/2009 | This Regulation" in dict(doc.sections)["ANNEX II"]


def test_an_article_carries_its_partition(corpus):
    labels = [label for label, _ in _doc(corpus, DIR).sections]
    arts = [x for x in labels if "Article 1 " in x or x.endswith("Article 1 — Subject matter and scope")]
    assert arts and arts[0].startswith("TITLE I") and " › " in arts[0]


def test_one_chunk_per_section_with_the_shared_space_s_chunker(corpus):
    doc = _doc(corpus, ACT)
    from app import chunking_legal
    assert SOURCE.chunking_profile == "legal_article"
    assert chunking.chunker_name(SOURCE.chunking_profile, Mutability.MUTABLE) == chunking.LEGAL_ARTICLE
    chunks = list(chunking_legal.chunk_legal_article(doc.uid, 1, doc.sections, params=chunking_legal.SHARED_LAYER[
        "chunker"]["params"], title=doc.title))
    assert len(chunks) >= len([b for _, b in doc.sections if b.strip()])
    assert chunks[-1]["chapter"] == chunking_legal.OUTLINE


# ---- slices ----------------------------------------------------------------------------------

def test_the_slice_is_the_language_the_type_and_the_year(corpus):
    scheme = slicing.scheme_for("eurlex")
    assert scheme.keys == ("lang", "type", "year")
    for celex, want in ((ACT, "lang=en/type=R/year=2016"), (CONS, "lang=en/type=R/year=2018"),
                        (DIR, "lang=en/type=L/year=2019")):
        ref = next(r for r in _refs(corpus) if r.locator.endswith(celex))
        doc = _doc(corpus, celex)
        assert scheme.slice_of(doc, int(ref.native_id)) == want
        assert slicing.slice_hint("eurlex", {"path": ref.locator, "native_id": ref.native_id}) == want
        slicing.check_slice_name(want)


def test_every_language_is_a_slice_the_origin_delivers_whole():
    from app.acquire import spec as acquire_spec
    from app.acquire.specs import ACQUIRE_REGISTRY
    spec = ACQUIRE_REGISTRY["eurlex"]
    assert set(spec.slice_profiles) == {f"lang={c}" for c in src.LANGUAGES} and len(spec.slice_profiles) == 24
    assert acquire_spec.profile_for_slices(spec, ["lang=fr"]) == "fr"
    assert acquire_spec.profile_for_slices(spec, ["lang=en", "lang=fr", "lang=de", "lang=it"]) == "en,fr,de,it"
    assert acquire_spec.profile_for_slices(spec, ["lang=fr/type=R/year=2016"]) == "fr"
    assert acquire_spec.everything_profile(spec) == "all"
    assert acquire_spec.resolve_profile(spec, "fr").include == ("lang=fr/*",)


# ---- relations and closures ------------------------------------------------------------------

def _built(celex: str, *, in_force: str | None, end: str | None, links: dict[str, list[str]]) -> bytes:
    """A notice in Cellar's branch shape carrying the elements a closure
    reads (the README: no real notice of an act that states its end was
    found)."""
    rel = "".join(f'<{tag} type="link"><URI><VALUE>http://publications.europa.eu/resource/cellar/x</VALUE>'
                  f'<TYPE>cellar</TYPE></URI><SAMEAS><URI><VALUE>http://publications.europa.eu/resource/celex/{c}'
                  f'</VALUE><IDENTIFIER>{c}</IDENTIFIER><TYPE>celex</TYPE></URI></SAMEAS></{tag}>'
                  for tag, cs in links.items() for c in cs)
    force = f'<RESOURCE_LEGAL_IN-FORCE type="data"><VALUE>{in_force}</VALUE></RESOURCE_LEGAL_IN-FORCE>' if in_force else ""
    ends = (f'<RESOURCE_LEGAL_DATE_END-OF-VALIDITY type="date"><VALUE>{end}</VALUE>'
            f'</RESOURCE_LEGAL_DATE_END-OF-VALIDITY>') if end else ""
    return (f'<NOTICE decoding="eng" type="branch"><WORK><RESOURCE_LEGAL_ID_CELEX type="data"><VALUE>{celex}'
            f'</VALUE></RESOURCE_LEGAL_ID_CELEX>{force}{ends}{rel}</WORK><EXPRESSION><LANG type="data">'
            f'<VALUE>eng</VALUE></LANG><EXPRESSION_USES_LANGUAGE type="concept"><IDENTIFIER>ENG</IDENTIFIER>'
            f'</EXPRESSION_USES_LANGUAGE></EXPRESSION></NOTICE>').encode()


def test_the_cdm_links_are_eli_edges(corpus):
    unit = corpus / src.unit_rel("en", ACT)
    (unit / src.NOTICE).write_bytes(_built(ACT, in_force=None, end=None, links={
        "RESOURCE_LEGAL_REPEALS_RESOURCE_LEGAL": ["32009R1225"],
        "RESOURCE_LEGAL_AMENDS_RESOURCE_LEGAL": ["32009R1225", "32012R0765"],
        "WORK_CITES_WORK": ["12016E207"], "RESOURCE_LEGAL_BASED_ON_RESOURCE_LEGAL": ["12016E207"],
        "RESOURCE_LEGAL_CORRECTS_RESOURCE_LEGAL": ["32009R0001"], "WORK_RELATED_TO_WORK": ["52015PC0001"]}))
    edges = sorted((e.type, e.to) for e in SOURCE.relations(_doc(corpus, ACT)))
    assert edges == sorted([("eli:repeals", "celex:32009R1225"), ("eli:amends", "celex:32009R1225"),
                            ("eli:amends", "celex:32012R0765"), ("eli:cites", "celex:12016E207"),
                            ("eli:based_on", "celex:12016E207"), ("eli:corrects", "celex:32009R0001")])
    assert list(SOURCE.closures(_doc(corpus, ACT))) == [], "a repeal link carries no day: it closes nothing"
    cons_edges = {(e.type, e.to) for e in SOURCE.relations(_doc(corpus, CONS))}
    assert cons_edges == {("eli:consolidates", f"celex:{ACT}")}


@pytest.mark.parametrize("in_force, end, closes", [
    ("false", "2020-12-31", [(f"celex:{ACT}", "2020-12-31")]),   # the act's own word: closed
    ("true", "2030-12-31", []),                                  # a sunset, still in force: kept, not closed
    ("false", None, []),                                         # no day: open, labelled
    ("false", "9999-12-31", []),                                 # the CDM's "no end"
    (None, "2020-12-31", []),                                    # the force status not stated
])
def test_an_act_closes_on_its_own_word_only(corpus, in_force, end, closes):
    unit = corpus / src.unit_rel("en", ACT)
    (unit / src.NOTICE).write_bytes(_built(ACT, in_force=in_force, end=end, links={}))
    doc = _doc(corpus, ACT)
    assert list(SOURCE.closures(doc)) == closes
    codes = [i.code for i in doc.issues]
    assert ("not-in-force-without-end" in codes) == (in_force == "false" and end in (None, "9999-12-31"))
    assert all(c in src.ISSUES for c in codes)


def test_a_consolidation_closes_nothing(corpus):
    assert list(SOURCE.closures(_doc(corpus, CONS))) == []


def test_an_end_before_the_start_is_an_issue(corpus):
    unit = corpus / src.unit_rel("en", ACT)
    (unit / src.NOTICE).write_bytes(_built(ACT, in_force="false", end="2010-01-01", links={}))
    doc = _doc(corpus, ACT)
    assert "end-before-start" in [i.code for i in doc.issues]
    assert list(SOURCE.closures(doc)) == [], "an end before the start is contradicted by the start"


def test_an_original_starts_at_its_entry_into_force_never_before_publication():
    assert src.original_start(["2016-07-20"], "2016-06-30") == "2016-07-20"
    assert src.original_start(["2016-06-01", "2016-07-20"], "2016-06-30") == "2016-07-20"
    assert src.original_start(["2016-06-01"], "2016-06-30") == "2016-06-30"
    assert src.original_start([], "2016-06-30") == "2016-06-30"


# ---- the platform's view ---------------------------------------------------------------------

def test_registered_with_its_declarations():
    s = collections_mod.PLUGIN_REGISTRY["eurlex"]
    assert s is SOURCE and s.license_class.value == "yellow" and s.native_id_order == "numeric"
    assert s.default_profile["validity"] == {"kind": "versions-per-work", "from": "extra.valid_from",
                                             "work": "extra.validity_work"}
    assert s.default_profile["eli"]["jurisdiction"] == "EU"
    from app.hold import template
    assert template.default_slicing(s)["keys"] == ["lang", "type", "year"]
    from app import chunking_legal
    assert template.default_layers(s)[0].to_dict() == chunking_legal.SHARED_LAYER


def test_origin_coverage(corpus):
    """tests/origin_coverage.py: parse reads nothing fetch did not hash, and
    a touched file of the unit changes its discovery signature."""
    from tests.origin_coverage import assert_covered
    refs = [r for r in _refs(corpus) if r.scope == "document" and r.error is None]
    read = set()
    for ref in refs:
        read |= assert_covered(SOURCE, ref, corpus)
    assert read


def test_the_origin_hash_covers_every_file_of_the_unit(corpus):
    ref = next(r for r in _refs(corpus) if r.locator.endswith(ACT))
    before = SOURCE.fetch(ref).raw_hash
    unit = Path(ref.locator)
    (unit / "L_2016176EN.01005001.xml").rename(unit / "L_2016176EN.01005001.xml.bak")
    assert SOURCE.fetch(ref).raw_hash != before, "a renamed item is another unit"
    files = src.unframe(SOURCE.fetch(ref).data)
    assert "L_2016176EN.01005001.xml.bak" in files and src.NOTICE in files


def test_the_origin_is_the_unit_relative_to_the_corpus(corpus):
    ref = next(r for r in _refs(corpus) if r.locator.endswith(ACT))
    assert plugin_io.origin_locator(SOURCE, ref.locator, corpus) == f"lang=en/2016/{ACT}"


def test_worker_stages_run_end_to_end(tmp_path, corpus, isolated_config):
    from app import db, discovery, worker
    collection = collections_mod.Collection(
        name="eurlex", plugin="eurlex", profile=None, embed_mode_default=None,
        corpus_dir=corpus, manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")
    con = db.connect(collection)
    discovery.sync_collection(con, collection)
    assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
    uid = f"eurlex:{_nid('2016-06-30', ACT, 'en', 'ORIGINAL')}"
    row = con.execute("SELECT * FROM documents WHERE uid=?", (uid,)).fetchone()
    worker.stage_rdf(con, row, collection)
    row = con.execute("SELECT * FROM documents WHERE uid=?", (uid,)).fetchone()
    assert row["rdf_done"] == 1 and row["error"] is None
    chunks = worker._load_chunks(row, collection)
    assert len(chunks) >= 59 and chunks[0]["text"].startswith(row["title"])
    assert "(1) Council Regulation (EC) No 1225/2009" in chunks[0]["text"], "the header, then the recital"


def test_the_terms_are_the_legal_notice_verbatim_and_lf():
    """Yellow (operator, 2026-11-08, A): the terms a peer acknowledges hold
    the legal notice's Disclaimer and Copyright notice byte for byte — the
    zero-width spaces of the page kept — as fetched on the node (sha256
    7c58fabc…), and the whole file is what the recipe pins."""
    import hashlib
    from pathlib import Path
    from app.acquire import specs
    terms = (Path(__file__).resolve().parents[1] / "app" / "plugins" / "eurlex" / "TERMS.txt").read_bytes()
    assert b"\r" not in terms and specs.ACQUIRE_REGISTRY["eurlex"].terms_text_path.read_bytes() == terms
    assert hashlib.sha256(terms).hexdigest() == "080b40b605f79a9468dc9ddb532868ee067928ffea036990e410199c7cf613db"
    start = terms.index(b"-----\n") + len(b"-----\n")
    notice = terms[start:terms.index(b"-----\n", start)]
    assert hashlib.sha256(notice).hexdigest() == \
        "7c58fabcc73883e265e43551e404e4cddc336fcf71d7a97857cae92b7e6c7a27", "the notice, byte for byte"
    assert notice.decode("utf-8").count("​") == 8


@pytest.mark.parametrize("celex", [ACT, CONS, DIR])
def test_no_attribute_of_any_item_is_lost(corpus, celex):
    """Invariant 11, past the words: every attribute of every element of
    every XML item is in the record (München, 2026-11-14, comparing the
    forms: a consolidation's amending acts are `MOD.ACT TYPE= LEG.VAL=`
    and its corrigenda `CORRIG RELEVANT=`, markup rather than text)."""
    doc = _doc(corpus, celex)
    held = json.dumps(doc.extra, ensure_ascii=False)
    unit = corpus / src.unit_rel("en", celex)
    missing = []
    for p in sorted(x for x in unit.iterdir() if x.name != rdf.TREE_FILE):
        for el in ET.fromstring(p.read_bytes()).iter():
            for k, v in el.attrib.items():
                if json.dumps(k, ensure_ascii=False) not in held or json.dumps(v, ensure_ascii=False) not in held:
                    missing.append(f"{p.name}: {el.tag} {k}={v!r}")
    assert not missing, missing[:20]


def test_a_consolidation_keeps_its_amending_acts_with_their_attributes(corpus):
    doc = _doc(corpus, CONS)
    fam = json.dumps(doc.extra["formex"].get("fam_comp") or doc.extra, ensure_ascii=False)
    assert fam.count('"MOD.ACT"') >= 1 and '"LEG.VAL": "REG"' in fam and '"TYPE": "MOD"' in fam


@pytest.mark.parametrize("celex", [ACT, CONS, DIR])
def test_the_markup_is_every_attribute_of_the_body_and_annexes_in_document_order(corpus, celex):
    doc = _doc(corpus, celex)
    unit = corpus / src.unit_rel("en", celex)
    markup = doc.extra["markup"]
    assert doc.extra["body_file"] in markup
    for name, held in markup.items():
        root = ET.fromstring((unit / name).read_bytes())
        assert held == [[e.tag, dict(e.attrib)] for e in root.iter() if isinstance(e.tag, str) and e.attrib]
    assert "markup" in src.EXTRA_KEYS and src.EurlexSource.PARSER_VERSION == 3



@pytest.mark.parametrize("celex", [ACT, CONS, DIR])
def test_the_record_holds_the_unit_s_scoped_tree_whole(corpus, celex):
    """Parser 3: Cellar's non-inferred tree, scoped to the language (rdf.py),
    is in the record as the unit holds it — the assertions the branch notice
    lacks (München, 2026-11-14: 26,607 over 1,698 acts)."""
    doc = _doc(corpus, celex)
    unit = corpus / src.unit_rel("en", celex)
    assert doc.extra["cdm_rdf"] == rdf.loads((unit / rdf.TREE_FILE).read_bytes())
    assert doc.extra["form"] == "fmx4" and doc.extra["cdm_rdf"]["language"] == "ENG"
