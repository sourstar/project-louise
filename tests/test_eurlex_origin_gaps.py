"""PARSER 6: 09 §9.5's `origin_gaps` (the operator's rule B, 2026-11-28: "keep
the record, mark it as degraded, mark the parts as missing"), read from the
unit's OWN files so a verifier reproduces it from the corpus — and THE SAME
READING as the fetch's judge (architecture's review, B1: `formex.gaps`, one
function): a record states gaps exactly where `judge_unit` says "not whole"."""
from __future__ import annotations

import pytest

from app import plugin_io
from app.acquire import eurlex_dump
from app.pack import records as pack_records
from app.plugins.base import DiscoverState, UnindexableDocument
from app.plugins.eurlex import SOURCE, formex, rdf
from app.plugins.eurlex import source as src
from tests.test_eurlex_package import (CELEX, FX, MTD, PKG, XHTML, _items, _packaged_mtd,  # noqa: F401
                                       _scoped, _shifted_files, _unit, serial)
from tests.test_eurlex_parser4 import _docs, _lay

WNAME, BODY = "L_1972160IT.01000101.doc.xml", "L_1972160IT.01000101.xml"
A1, A2 = "L_1972160IT.01001001.xml", "L_1972160IT.01001201.xml"


def _annex(page: int) -> bytes:
    return (f'<?xml version="1.0"?><ANNEX><BIB.INSTANCE><PAGE.FIRST>{page}</PAGE.FIRST><PAGE.SEQ>1</PAGE.SEQ>'
            f'</BIB.INSTANCE><TITLE><TI><P>ALLEGATO {page}</P></TI></TITLE><CONTENTS><P>testo {page}</P>'
            f'</CONTENTS></ANNEX>').encode()


def _with_annexes(first: bytes, second: bytes, *, drop: str | None = None) -> dict[str, bytes]:
    """The fixture act with two annexes its wrapper names (pages 10 and 12)."""
    items = _items(FX)
    subs = "".join(f'<DOC.SUB.PUB NO.SEQ="000{i}"><LG.DOC>IT</LG.DOC><REF.PHYS TYPE="DOC.XML" FILE="{n}">'
                   f'</REF.PHYS></DOC.SUB.PUB>' for i, n in ((2, A1), (3, A2)))
    items[WNAME] = items[WNAME].replace(b"</DOC.MAIN.PUB>", b"</DOC.MAIN.PUB>" + subs.encode(), 1)
    items[A1], items[A2] = first, second
    if drop:
        del items[drop]
    return items


def _record(corpus):
    (ref,) = SOURCE.discover(DiscoverState(corpus_root=corpus))
    assert ref.error is None, ref.error
    try:
        return plugin_io.canonical_doc(SOURCE, {"native_id": ref.native_id, "path": ref.locator})
    except UnindexableDocument:
        return None


UNITS = {
    "whole": lambda: {**_items(FX), rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())},
    "two annexes whole": lambda: {**_with_annexes(_annex(10), _annex(12)),
                                  rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())},
    # bg 32006R1828's shape: two named files, each holding the other's page
    "in each other's place": lambda: {**_with_annexes(_annex(12), _annex(10)),
                                      rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())},
    "an annex absent": lambda: {**_with_annexes(_annex(10), _annex(12), drop=A2),
                                rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())},
    # 32015D1735's shape, an annex: a TIFF under an .xml name
    "an annex not of its kind": lambda: {**_with_annexes(_annex(10), b"II*\x00" + b"\x00" * 64),
                                         rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())},
    # 32015D1735 itself: the BODY a TIFF (no act to read: no record)
    "the body not of its kind": lambda: {**_items(FX), BODY: b"II*\x00" + b"\x00" * 64,
                                         rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())},
    # 32013D0410's 24 languages: the wrapper's name holds an ANNEX (no record)
    "the wrapper under another name": lambda: {**_shifted_files(), rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())},
    # the dump build's "to re-form" class: the tree lists a package the unit
    # lacks, the record read from the XHTML it holds
    "a lacked package": lambda: {XHTML: b"<html><body><p>Articolo 1</p></body></html>",
                                 rdf.TREE_FILE: _scoped(_packaged_mtd())},
}


@pytest.mark.parametrize("shape", sorted(UNITS))
def test_the_record_and_the_judge_agree(tmp_path, shape):
    """B1: `judge_unit(u)["whole"] == (doc.origin_gaps == [])` on every unit
    that has a record; one with no record is never judged whole."""
    unit = _unit(tmp_path, UNITS[shape]())
    judged = eurlex_dump.judge_unit(unit, "it", CELEX)
    doc = _record(tmp_path)
    if doc is None:
        assert not judged["whole"], (shape, judged)
        return
    assert judged["whole"] == (doc.origin_gaps == []), (shape, judged, doc.origin_gaps)
    assert {g["part"]: g["why"] for g in doc.origin_gaps} == judged["gaps"], shape
    assert pack_records.origin_gaps(doc.origin_gaps) == doc.origin_gaps, "the format's own shape"


def test_files_in_each_other_s_place_are_each_another_s_and_set_aside(tmp_path):
    """bg 32006R1828: kept, the two files SET ASIDE (never one's text under
    the other's name), each stated as the other's."""
    _unit(tmp_path, UNITS["in each other's place"]())
    doc = _record(tmp_path)
    assert doc.origin_gaps == [{"part": A1, "why": f"another's: {A2}"}, {"part": A2, "why": f"another's: {A1}"}]
    text = "\n".join(str(s) for s in doc.sections)
    assert "testo 10" not in text and "testo 12" not in text, "neither read under the other's name"
    assert any(i.code == "files-not-their-names" for i in doc.issues)


def test_a_file_not_of_its_kind_is_unreadable_and_an_absent_one_absent(tmp_path):
    """B2: the misnamed → "unreadable" branch; an absent annex "absent"."""
    _unit(tmp_path / "a", UNITS["an annex not of its kind"]())
    assert _record(tmp_path / "a").origin_gaps == [{"part": A2, "why": "unreadable"}]
    _unit(tmp_path / "b", UNITS["an annex absent"]())
    assert _record(tmp_path / "b").origin_gaps == [{"part": A2, "why": "absent"}]


def test_a_lacked_package_is_absent_in_the_record_read_from_another_form(tmp_path):
    """B1 (a): the record read from the XHTML states the package its tree lists."""
    _unit(tmp_path, UNITS["a lacked package"]())
    assert _record(tmp_path).origin_gaps == [{"part": PKG, "why": "absent"}]


def test_the_wrapper_reasons_are_gaps_and_not_its_name_reads_them():
    """B1 (b): `not_its_name` is `formex.gaps`' reasons — the wrapper that
    is not a <DOC> unreadable, the .xml holding the wrapper another's."""
    files = _shifted_files()
    wrapper = formex._parse(files[WNAME], WNAME)
    got = formex.gaps(files, wrapper, WNAME)
    assert got[WNAME][0] == "unreadable" and got[BODY][0] == f"another's: {WNAME}"
    assert formex.not_its_name(files, wrapper, WNAME) == [
        f"{WNAME} holds <ANNEX>, not a wrapper", f"{BODY} holds a wrapper (<DOC>)"]


def test_a_whole_unit_states_no_gap_and_parser_5_states_none(tmp_path):
    _lay(tmp_path, "31972R1473")
    (_ref, doc), = _docs(SOURCE, tmp_path).values()
    assert doc.origin_gaps == []
    (_ref, doc5), = _docs(SOURCE.at_parser_version(5), tmp_path).values()
    assert doc5.origin_gaps == []


def test_parser_5_still_refuses_files_in_each_other_s_place(tmp_path):
    """Parser 5 unchanged: the set-aside is parser 6's."""
    _unit(tmp_path, UNITS["in each other's place"]())
    (ref,) = SOURCE.discover(DiscoverState(corpus_root=tmp_path))
    with pytest.raises(UnindexableDocument, match="not the one its name says"):
        plugin_io.canonical_doc(SOURCE.at_parser_version(5), {"native_id": ref.native_id, "path": ref.locator})


@pytest.mark.parametrize("shape", sorted(UNITS))
def test_the_dump_build_s_ledger_agrees_too(tmp_path, shape):
    """B4: `incomplete_due` judges a recorded unit by `judge_unit`, so the
    ledger says whole exactly where the record states no gap."""
    import json
    unit = _unit(tmp_path, UNITS[shape]())
    (tmp_path / eurlex_dump.INCOMPLETE).write_text(json.dumps(
        {"it": {CELEX: {"in_force": "true", "reform": True}}}))
    due = eurlex_dump.incomplete_due(tmp_path, {"it"})
    entry = eurlex_dump.incomplete(tmp_path)["it"][CELEX]
    judged = eurlex_dump.judge_unit(unit, "it", CELEX)
    assert entry["whole"] == judged["whole"] and bool(due) == (not judged["whole"]), (shape, entry, judged)


def test_the_judge_reads_a_large_body_by_its_head_only(tmp_path, monkeypatch):
    """B3: bodies reach 180 MB (2026-11-19's OOM kills). The judge reads every
    file but the wrapper by its first bytes, loose or in a package."""
    from tests.test_eurlex_package import _package
    big = _items(FX)[BODY] + b"<!--" + b"x" * (8 << 20) + b"-->"
    loose = _unit(tmp_path / "loose", {**_items(FX), BODY: big, rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    packed = _unit(tmp_path / "packed", {PKG: _package({**_items(FX), BODY: big}),
                                         rdf.TREE_FILE: _scoped(_packaged_mtd())})
    held = eurlex_dump.held_for_judging(src.unit_files(loose))
    assert len(held[BODY]) == formex._HEAD_BYTES and held[WNAME] == _items(FX)[WNAME]
    view, _ = formex.unpack(eurlex_dump.held_for_judging(src.unit_files(packed)), read_whole=formex.is_wrapper)
    assert len(view[BODY]) == formex._HEAD_BYTES, "a package's body member: its head only"
    for u in (loose, packed):
        assert eurlex_dump.judge_unit(u, "it", CELEX)["whole"], u


def test_the_dump_build_lands_a_unit_lacking_a_named_part_and_leaves_it_to_the_api(tmp_path, serial):
    """B4 on the bulk path: the wrapper names an annex the dump does not hold.
    Landed (its text is there), recorded, asked once, then degraded — never
    "whole" in the ledger while its record states the annex absent."""
    import json
    import zipfile
    from tests.test_eurlex_package import UUID
    folder = tmp_path / "dump" / "datadump-it"
    folder.mkdir(parents=True)
    (folder / "datadump.json").write_text(json.dumps({"language": "it", "created": "2026-11-15T01:00:00+01:00"}))
    with zipfile.ZipFile(folder / "LEG_IT_FMX.zip", "w") as z:
        for n, b in _with_annexes(_annex(10), _annex(12), drop=A2).items():
            z.writestr(f"{UUID}/fmx4/{n}", b)
    with zipfile.ZipFile(folder / "LEG_MTD.zip", "w") as z:
        z.writestr(f"{UUID}/tree_non_inferred.rdf", (FX / MTD).read_bytes())
    corpus = tmp_path / "corpus"
    got = eurlex_dump.build(tmp_path / "dump", corpus, ["it"], log=lambda m: None)["it"]
    assert got["built"] == 1 and got["dump-lacks-a-part"] == 1, got
    assert [c for _l, c, _f in eurlex_dump.incomplete_due(corpus, {"it"})] == [CELEX]
    # (the fixture's tree lists neither annex, so the build lands neither)
    assert {"part": A2, "why": "absent"} in _record(corpus).origin_gaps
