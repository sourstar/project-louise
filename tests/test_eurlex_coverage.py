"""INVARIANT 11 AS A TEST, NOT A REVIEW (2026-11-21, the EUR-Lex5 review):
every word of the Formex files an act's wrapper names — and of the files
they include — is in the record's sections, or in its notes, or in the
parts the reader keeps beside the text by design (the bibliographic
instance, a consolidation's family and production record, the act's
title, its citations, its preamble's opening and closing formulas, its
final clause and signatures). Nothing is dropped, nothing glued to its
neighbour. On real acts of every shape the review found text lost in:
divided recitals (32019R1286), an annexed act (32019D0117), an exchange of
letters (22019A0206(01)), an agreement's recitals as paragraphs
(22019A0524(01)), annexed joint declarations (22019A1029(01)), text before
a table (32019R2144, 32019R0998), block titles in tables (32016R0997,
32016D2367), definition lists (32017R2400), tables nested in cells
(32011R1169), a consolidation (02016R0679-20160504) and two corrigenda
(32019L1922R(01) cs, 32020Y0201(01)R(01) de). EUR-Lex content: the
Commission's reuse notice (acknowledged by the operator)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.plugins.eurlex import formex

FIX = Path(__file__).parent / "fixtures" / "eurlex" / "coverage"
CASES = sorted(p.name for p in FIX.iterdir() if p.is_dir())
_WS = re.compile(r"\s+")
#: what the reader keeps beside the sections, as trees in the record's extra
KEPT_BESIDE = ("BIB.INSTANCE", "FAM.COMP", "INFO.CONSLEG", "INFO.PROD", "GR.VISA", "PREAMBLE.INIT",
               "PREAMBLE.FINAL", "FINAL", "SIGNATURE", "SIGNATORY", "GR.CONSID.INIT")



def _nz(s: str | None) -> str:
    return _WS.sub(" ", (s or "").replace(" ", " ")).strip()


def _present(fragment: str, text: str) -> bool:
    """The fragment in the text as whole words: never glued to a neighbour."""
    left = r"(?<!\w)" if fragment[:1].isalnum() else ""
    right = r"(?!\w)" if fragment[-1:].isalnum() else ""
    return re.search(left + re.escape(fragment) + right, text) is not None


def _files(case: str) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (FIX / case).iterdir() if p.is_file()}


def _missing(case: str) -> list[tuple[str, str, str]]:
    files = _files(case)
    act = formex.parse(files)
    out = _nz(" \n ".join(f"{label} \n {body}" for label, body in formex.sections(act)))
    notes = _nz(" \n ".join(n.get("text", "") for n in act.notes))
    wname, wrapper = formex.wrapper_of(files)
    read = [n for n in dict.fromkeys(formex.main_names(wrapper) + formex.annex_names(wrapper)) if n in files]
    read += [i["file"] for i in act.meta.get("inclusions", [])]
    title_of_act = None
    lost: list[tuple[str, str, str]] = []
    for name in read:
        root = formex._parse(files[name], name)
        parents = {c: p for p in root.iter() for c in p}
        doc = root.find("CONS.DOC") if root.tag == "CONS.ACT" else root
        title_of_act = doc.find("TITLE") if name == act.body_file and doc is not None else title_of_act

        def ancestors(e):
            while e is not None:
                yield e
                e = parents.get(e)
        for e in root.iter():
            if not isinstance(e.tag, str):
                continue
            chain = list(ancestors(e))
            if any(a.tag in KEPT_BESIDE for a in chain) or (title_of_act is not None and title_of_act in chain):
                continue
            where = "/".join(a.tag for a in reversed(chain[:4]))
            for t in [e.text] + [c.tail for c in e]:
                t = _nz(t)
                if len(t) < 3 or not any(ch.isalnum() for ch in t):
                    continue
                if t not in out and t not in notes:
                    lost.append((name, where, t[:100]))
    return lost


@pytest.mark.parametrize("case", CASES)
def test_every_word_of_the_act_s_own_files_is_in_its_sections(case):
    lost = _missing(case)
    assert not lost, f"{case}: {len(lost)} fragment(s) in no section, e.g. {lost[:5]}"


def test_a_table_nested_in_a_cell_is_read_once():
    """32011R1169 Annex VII point 17: a table inside a cell was glued into
    the row, then its rows emitted a second time."""
    act = formex.parse(_files("32011R1169_en"))
    text = "\n".join(body for _l, body in formex.sections(act))
    assert "25 %25 %" not in text
    rows = [ln.strip() for ln in text.splitlines() if ln.strip()]
    dup = [r for r in set(rows) if rows.count(r) > 1 and len(r) > 40 and "|" in r]
    assert not dup, f"rows emitted twice: {dup[:3]}"


def test_a_corrigendum_reads_its_corrections():
    for case in ("32019L1922R01_cs", "32020Y0201-01R01_de"):
        act = formex.parse(_files(case))
        secs = formex.sections(act)
        assert secs and all(body.strip() for _l, body in secs), (case, secs)


def test_a_definition_list_s_terms_and_definitions_stand_apart():
    """32017R2400 Annex V: "SFCWHSCSpecific fuel consumption over WHSC" — a
    DLIST's term glued to its definition."""
    act = formex.parse(_files("32017R2400_en"))
    text = "\n".join(body for _l, body in formex.sections(act))
    assert "SFCWHSCSpecific" not in text and "SFCWHSC Specific fuel consumption" in text


def test_a_table_s_cells_stand_apart():
    act = formex.parse(_files("32019R0998_en"))
    text = "\n".join(body for _l, body in formex.sections(act))
    assert "ex07104000ex20058000" not in text and "ex07104000 ex20058000" in text


@pytest.mark.parametrize("case", CASES)
def test_every_consolidation_mark_is_kept(case):
    """A consolidation's CLG.MDFO/MDFC marks (which amending act changed
    which text): every one in the act's own files is in the record's
    modifications (2026-11-21: 02009R1223-20191218 kept 146 of 372 — marks
    in lists, tables, titles, notes and the consolidation's family lost)."""
    files = _files(case)
    act = formex.parse(files)
    _w, wrapper = formex.wrapper_of(files)
    read = [n for n in dict.fromkeys(formex.main_names(wrapper) + formex.annex_names(wrapper)) if n in files]
    read += [i["file"] for i in act.meta.get("inclusions", [])]
    marks = sum(1 for n in read for e in formex._parse(files[n], n).iter()
                if e.tag is ET.ProcessingInstruction and (e.text or "").startswith("CLG."))
    assert len(act.modifications) == marks


def test_a_lone_wrapper_of_another_act_is_not_this_act_s():
    """hr, 2026-11-21: 32009D0481, 32009D0482 and 32011D0350 each hold the
    same 1998 wrapper (L_1998326HR.01003501.doc.xml) and its GENERAL file:
    another act's package read as theirs. A lone wrapper naming other acts
    only, or of an OJ older than the act, is not its own; a consolidation's
    wrapper naming its base act, and a corrigendum's naming the act it
    corrects, are."""
    files = _files("32016R0997_en")
    w = next(n for n in files if formex.is_wrapper(n))
    assert formex.own_wrapper(files, "32016R0997", files.__getitem__) == w
    with pytest.raises(formex.FormexError, match="older than the act"):
        formex.own_wrapper(files, "32019R0001", files.__getitem__)
    other = {**files, w: files[w].replace(b"<BIB.DOC>", b"<BIB.DOC><NO.CELEX>31998D0777</NO.CELEX>", 1)}
    with pytest.raises(formex.FormexError, match="names 31998D0777, not 32016R0997"):
        formex.own_wrapper(other, "32016R0997", other.__getitem__)
    base = {**files, w: files[w].replace(b"<BIB.DOC>", b"<BIB.DOC><NO.CELEX>32016R0997</NO.CELEX>", 1)}
    assert formex.own_wrapper(base, "02016R0997-20200101", base.__getitem__) == w
    assert formex.own_wrapper(base, "32016R0997R(01)", base.__getitem__) == w


def test_a_wrapper_s_oj_is_read_from_its_name_never_from_the_act_s_date():
    """München, 2026-11-22: 31959R0005 and 31964D0732 hr (Croatian Special
    Edition packages) refused as "of an OJ of 1958/1963, older than the
    act": the year read was the act's ADOPTION date, the regex running from
    PUBLICATION.REF.SE to BIB.DOC's DATE. The OJ is the file's own name."""
    assert formex.oj_year("P_1959007HR.01018501.doc.xml", b"") == 1959
    assert formex.oj_year("L_1998326HR.01003501.doc.xml", b"") == 1998
    se = (b'<DOC><BIB.DOC><NO.CELEX>31959R0005</NO.CELEX><DATE ISO="19581222">22.12.1958</DATE></BIB.DOC>'
          b'<PUBLICATION.REF.SE><COLL>DD</COLL><DATE ISO="20130701"/></PUBLICATION.REF.SE></DOC>')
    assert formex.oj_year("wrapper.doc.xml", se) is None, "PUBLICATION.REF.SE is no PUBLICATION.REF"
    oj = b'<DOC><PUBLICATION.REF FILE="x"><COLL>L</COLL><DATE ISO="20160623">23.6.2016</DATE></PUBLICATION.REF></DOC>'
    assert formex.oj_year("wrapper.doc.xml", oj) == 2016
    files = _files("32016R0997_en")
    w = next(n for n in files if formex.is_wrapper(n))
    sen = {**{n: b for n, b in files.items() if n != w}, "P_1959007HR.01018501.doc.xml": se}
    assert formex.own_wrapper(sen, "31959R0005", sen.__getitem__) == "P_1959007HR.01018501.doc.xml"


def test_the_origin_s_listing_outranks_the_wrapper_s_label():
    """München, 2026-11-22: 31958D1006(02) en, the decision printed a second
    time (OJ P 17/1958 p. 419; (01) at p. 390), its wrapper's NO.CELEX
    saying (01), Cellar's tree listing these files as (02)'s. Listed by the
    tree: this act's. Not listed: refused as before. An OJ older than the
    act refuses it either way (the hr acts of 2009-11 holding a 1998 act's
    package)."""
    files = _files("32016R0997_en")
    w = next(n for n in files if formex.is_wrapper(n))
    other = {**files, w: files[w].replace(b"<BIB.DOC>", b"<BIB.DOC><NO.CELEX>32016R0997(01)</NO.CELEX>", 1)}
    with pytest.raises(formex.FormexError, match=r"names 32016R0997\(01\), not 32016R0997\(02\)"):
        formex.own_wrapper(other, "32016R0997(02)", other.__getitem__)
    assert formex.own_wrapper(other, "32016R0997(02)", other.__getitem__, listed={w}) == w
    assert formex.parse(other, "32016R0997(02)", listed={w}).articles
    with pytest.raises(formex.FormexError, match="older than the act"):
        formex.own_wrapper(other, "32019R0001", other.__getitem__, listed={w})
