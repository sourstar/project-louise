"""EUR-Lex parser 5 (2026-11-18): EUR-Lex4's parse refused 11,486 documents.
Each kind as München's samples show it (/root/measure/eu4-samples.txt):
  * a corrigendum (CORR [BIB.INSTANCE, TITLE, CONTENTS.CORR [CORRECTION
    [DESCRIPTION, OLD.CORR, NEW.CORR]]]), 4,487;
  * a GENERAL body, 4,030 — 31968R0259's Special Edition splits the main
    document over several items, the first a table: every main file read;
  * an agreement (AGR: a preamble and articles, as an act), 31;
  * a start of validity the origin does not state, 3,415 (1958–62, HTML or
    PDF): provisional, the act's own date, and said — or absent, and said;
  * a file the wrapper names and the manifestation does not hold, 29."""
from __future__ import annotations

import dataclasses
import shutil

import pytest

from app import plugin_io
from app.plugins.base import DiscoverState
from app.plugins.eurlex import SOURCE, formex, from_tree
from app.plugins.eurlex import source as src
from tests.acquire.test_eurlex_dump import FORMEX

WRAPPER = "L_2000001EN.01000101.doc.xml"
BIB = ('<BIB.INSTANCE><DOCUMENT.REF FILE="L_2000001EN.toc.xml"/><DATE ISO="20000105">20000105</DATE>'
       '<LG.DOC>EN</LG.DOC><NO.DOC>1</NO.DOC></BIB.INSTANCE>')


def _wrapper(mains: list[str], annexes: list[str] = ()) -> bytes:
    refs = "".join(f'<REF.PHYS TYPE="DOC.XML" FILE="{n}"></REF.PHYS>' for n in mains)
    subs = "".join(f'<DOC.SUB.PUB><REF.PHYS TYPE="DOC.XML" FILE="{n}"></REF.PHYS></DOC.SUB.PUB>' for n in annexes)
    return ('<?xml version="1.0" encoding="UTF-8"?><DOC><BIB.DOC><NO.DOC TYPE="OJ"><TXT>2000/L 1/01</TXT>'
            '</NO.DOC></BIB.DOC><PUBLICATION.REF FILE="L_2000001EN.toc.xml"><COLL>L</COLL><NO.OJ>1</NO.OJ>'
            '<DATE ISO="20000105">20000105</DATE><LG.OJ>EN</LG.OJ></PUBLICATION.REF><FMX><DOC.MAIN.PUB '
            f'NO.SEQ="0001"><LG.DOC>EN</LG.DOC>{refs}</DOC.MAIN.PUB>{subs}</FMX></DOC>').encode()


CORR = ('<CORR>' + BIB + '<TITLE><TI><P>Corrigendum to Regulation (EC) No 1/2000</P></TI></TITLE>'
        '<CONTENTS.CORR>'
        '<CORRECTION><DESCRIPTION><P>On page 3, Article 2(1):</P></DESCRIPTION>'
        '<OLD.CORR><P>for: ‘30 days’,</P></OLD.CORR><NEW.CORR><P>read: ‘60 days’.</P></NEW.CORR></CORRECTION>'
        '<CORRECTION><DESCRIPTION><P>On page 4, Annex:</P></DESCRIPTION>'
        '<OLD.CORR><P>for: ‘ozone’,</P></OLD.CORR><NEW.CORR><P>read: ‘the ozone layer’.</P></NEW.CORR>'
        '</CORRECTION></CONTENTS.CORR></CORR>').encode()
TABLE = ('<GENERAL>' + BIB + '<TITLE><TI><P>Table of grades</P></TI></TITLE><CONTENTS>'
         '<GR.SEQ><TITLE><TI><P>Grades</P></TI></TITLE><TBL><CORPUS><ROW><CELL>A1</CELL><CELL>Director</CELL>'
         '</ROW></CORPUS></TBL></GR.SEQ><P>Applies from 1 January.</P></CONTENTS></GENERAL>').encode()
ACT = ('<ACT>' + BIB + '<TITLE><TI><P>Regulation No 1/2000</P></TI></TITLE><ENACTING.TERMS>'
       '<ARTICLE IDENTIFIER="001"><TI.ART>Article 1</TI.ART><ALINEA>The staff are graded.</ALINEA></ARTICLE>'
       '</ENACTING.TERMS></ACT>').encode()
AGR = ('<AGR>' + BIB + '<TITLE><TI><P>Agreement between the Community and X</P></TI></TITLE>'
       '<PREAMBLE><PREAMBLE.INIT>THE PARTIES,</PREAMBLE.INIT><PREAMBLE.FINAL>HAVE AGREED AS FOLLOWS:'
       '</PREAMBLE.FINAL></PREAMBLE><ENACTING.TERMS><ARTICLE IDENTIFIER="001"><TI.ART>Article 1</TI.ART>'
       '<ALINEA>The parties cooperate.</ALINEA></ARTICLE></ENACTING.TERMS></AGR>').encode()


def test_a_corrigendum_is_read_by_its_corrections():
    act = formex.parse({WRAPPER: _wrapper(["C.xml"]), "C.xml": CORR})
    assert act.root == "CORR" and act.title == "Corrigendum to Regulation (EC) No 1/2000"
    assert formex.sections(act) == [("On page 3, Article 2(1):", "for: ‘30 days’,\nread: ‘60 days’."),
                                    ("On page 4, Annex:", "for: ‘ozone’,\nread: ‘the ozone layer’.")]
    assert act.language == "en" and act.publication_date == "2000-01-05"


def test_every_main_file_is_read_and_the_act_is_the_body_wherever_it_stands():
    """31968R0259 (hr, bg, ro): the first main item a table (GENERAL), the
    act after it; parser 4 read the table alone and refused it."""
    act = formex.parse({WRAPPER: _wrapper(["T.xml", "A.xml"]), "T.xml": TABLE, "A.xml": ACT})
    assert act.root == "ACT" and act.body_file == "A.xml"
    labels = [l for l, _ in formex.sections(act)]
    # the other main file under its own title (2026-11-21: its title was in no section)
    assert labels == ["Article 1", "Table of grades › Grades", "Table of grades › Contents"], "in document order"
    text = dict(formex.sections(act))
    assert text["Table of grades › Grades"] == "A1 | Director"
    assert text["Table of grades › Contents"] == "Applies from 1 January."
    assert act.meta["main_documents"][0]["file"] == "T.xml", "the table kept whole too"


def test_a_general_document_alone_is_read_by_its_groups():
    act = formex.parse({WRAPPER: _wrapper(["T.xml"]), "T.xml": TABLE})
    assert act.root == "GENERAL" and act.title == "Table of grades"
    # in document order: the group, then the paragraph after it
    assert formex.sections(act) == [("Grades", "A1 | Director"), ("Contents", "Applies from 1 January.")]


def test_an_agreement_is_read_as_an_act():
    act = formex.parse({WRAPPER: _wrapper(["G.xml"]), "G.xml": AGR})
    assert act.root == "AGR" and [a.num for a in act.articles] == ["Article 1"]
    assert "The parties cooperate." in dict(formex.sections(act))["Article 1"]


def test_a_file_the_wrapper_names_and_the_manifestation_lacks_is_said_not_refused():
    act = formex.parse({WRAPPER: _wrapper(["A.xml"], ["X.xml"]), "A.xml": ACT})
    assert act.missing == ["X.xml"] and act.articles
    with pytest.raises(formex.FormexError, match="names the body A.xml"):
        formex.parse({WRAPPER: _wrapper(["A.xml"])})


def test_an_act_alone_reads_as_parser_4_read_it():
    act = formex.parse({WRAPPER: _wrapper(["A.xml"]), "A.xml": ACT})
    assert act.rendered_sections is None and act.missing == []


# ---- the start of validity the origin does not state ------------------------------------

def _unit_without_dates(tmp_path, monkeypatch, *, date_document):
    """The fixture's Italian act, its OJ date taken out of the wrapper and
    its tree stating no publication and no entry into force."""
    unit = tmp_path / src.unit_rel("it", FORMEX[0])
    shutil.copytree(FORMEX[2], unit, ignore=shutil.ignore_patterns("notice.xml", "LEG_MTD*"))
    wrapper = unit / "L_1972160IT.01000101.doc.xml"
    wrapper.write_bytes(wrapper.read_bytes().replace(b'<DATE ISO="19720716">19720716</DATE>', b""))
    real = from_tree.notice

    def undated(tree, lang3, celex=None):
        note = real(tree, lang3, celex)
        return dataclasses.replace(note, entry_into_force=[], date_publication=None, date_document=date_document)
    monkeypatch.setattr(from_tree, "notice", undated)
    (ref,) = SOURCE.discover(DiscoverState(corpus_root=tmp_path))
    assert ref.error is None
    return plugin_io.canonical_doc(SOURCE, {"native_id": ref.native_id, "path": ref.locator})


def test_no_start_stated_the_act_s_own_date_is_a_provisional_start_and_said(tmp_path, monkeypatch):
    doc = _unit_without_dates(tmp_path, monkeypatch, date_document="1972-06-30")
    assert doc.extra["valid_from"] == "1972-06-30"
    assert "no publication or entry into force" in doc.extra["valid_from_provisional"]
    assert "start-from-document-date" in [i.code for i in doc.issues]
    assert doc.extra["body_root"] == "ACT"
    from app import validity
    got = validity.of_doc(doc, validity.declared(SimpleCollection()))
    assert got["start_basis"] == doc.extra["valid_from_provisional"], "said on every result"


def test_no_date_at_all_the_record_is_held_and_takes_no_part_in_the_filter(tmp_path, monkeypatch):
    from app import validity
    doc = _unit_without_dates(tmp_path, monkeypatch, date_document=None)
    assert "valid_from" not in doc.extra and doc.sections
    assert "no-start-of-validity" in [i.code for i in doc.issues]
    assert validity.of_doc(doc, validity.declared(SimpleCollection())) is None


class SimpleCollection:
    plugin = "eurlex"

    def plugin_instance(self):
        return SOURCE


# ---- Formex inclusions (2026-11-19, 32021R1971) ------------------------------------------

def _annex_including(ref: str, *, listed: bool = True) -> bytes:
    incl = f'<INCL.ELEMENT FILEREF="{ref}" TYPE="FORMEX.DOC"/>'
    return ('<ANNEX>' + BIB.replace('</BIB.INSTANCE>', f'<INCLUSIONS>{incl}</INCLUSIONS></BIB.INSTANCE>'
                                    if listed else '</BIB.INSTANCE>')
            + f'<TITLE><TI><P>ANNEX I</P></TI></TITLE><CONTENTS><QUOT.S LEVEL="1">{incl}</QUOT.S>'
            '</CONTENTS></ANNEX>').encode()


INCLUDED = ('<ANNEX>' + BIB.replace('<NO.DOC>1</NO.DOC>', '<NO.DOC>1</NO.DOC><PAGE.FIRST>3</PAGE.FIRST>')
            + '<TITLE><TI><P>ANNEX I</P></TI></TITLE><CONTENTS><TBL COLS="2"><CORPUS>'
            '<ROW><CELL COL="1">C 101.00</CELL><CELL COL="2">Low default portfolios</CELL></ROW>'
            '<ROW><CELL COL="1">C 102.00</CELL><CELL COL="2">High default portfolios</CELL></ROW>'
            '</CORPUS></TBL></CONTENTS></ANNEX>').encode()


def test_an_included_formex_document_is_read_where_it_is_included():
    """32021R1971: Annex I's file only includes the 39 MB of its tables
    (`INCL.ELEMENT TYPE="FORMEX.DOC"`); the reader read none of them."""
    files = {WRAPPER: _wrapper(["A.xml"], ["N.xml"]), "A.xml": ACT, "N.xml": _annex_including("T.xml"),
             "T.xml": INCLUDED}
    act = formex.parse(files)
    text = dict(formex.sections(act))["ANNEX I"]
    assert "C 101.00 | Low default portfolios" in text and "C 102.00 | High default portfolios" in text
    assert text.count("C 101.00") == 1, "read once: the BIB.INSTANCE's INCLUSIONS listing is not read"
    assert [i["file"] for i in act.meta["inclusions"]] == ["T.xml"] and act.meta["inclusions"][0]["into"] == "N.xml"
    assert "bib_instance" in act.meta["inclusions"][0], "the included file's own record kept"
    assert "T.xml" not in (act.meta.get("other_items") or {}), "no longer kept as a tree beside the text"
    assert "T.xml" in act.markup


def test_an_included_file_the_manifestation_lacks_is_said_and_one_including_back_is_not_read_again():
    act = formex.parse({WRAPPER: _wrapper(["A.xml"], ["N.xml"]), "A.xml": ACT, "N.xml": _annex_including("T.xml")})
    assert act.missing == ["T.xml"]
    loop = _annex_including("N.xml", listed=False).replace(b"<ANNEX>", b"<ANNEX>", 1)
    act = formex.parse({WRAPPER: _wrapper(["A.xml"], ["N.xml"]), "A.xml": ACT, "N.xml": _annex_including("T.xml"),
                        "T.xml": loop})
    assert act.meta["inclusion_cycles"] == [{"file": "N.xml", "into": "T.xml"}]


def test_a_manifestation_without_inclusions_reads_as_before():
    plain = formex.parse({WRAPPER: _wrapper(["A.xml"]), "A.xml": ACT})
    assert "inclusions" not in plain.meta and plain.missing == []
