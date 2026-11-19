"""tools/measure/eurlex_units_check.py: the held units' contents against their
names (32015D1735: a TIFF filed as the body's `.xml`, the body's XML as a
`.tif`), the wrapper's named files' roots, and the Formex packages lacked."""
from __future__ import annotations

from app.plugins.eurlex import rdf
from app.plugins.eurlex import source as src
from tests.acquire.test_eurlex_dump import FORMEX, MTD, _items
from tests.test_eurlex_package import PKG, XHTML, _packaged_mtd, _scoped
from tools.measure import eurlex_units_check as chk

CELEX, FX = FORMEX[0], FORMEX[2]
BODY = "L_1972160IT.01000101.xml"


def _unit(root, celex, files):
    unit = root / src.unit_rel("it", celex)
    unit.mkdir(parents=True)
    for n, b in files.items():
        (unit / n).write_bytes(b)
    return unit


def test_kinds_and_roots():
    assert chk.kind(b"II*\x00rest") == "tiff" and chk.kind(b"\xef\xbb\xbf<?xml") == "markup"
    assert chk.kind(b"%PDF-1.4") == "pdf" and chk.kind(b"") == "empty" and chk.kind(b"\x00\x01") == "other"
    assert chk.root_tag(b'<?xml version="1.0"?>\n<!-- c --><?PAGE NO="1"?><ACT xmlns="x">') == "ACT"


def test_a_swap_a_lacked_package_and_a_sound_unit_are_each_counted(tmp_path):
    items = _items(FX)
    sound = {**items, rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())}
    swapped = {**items, BODY: b"II*\x00" + b"\x00" * 64, "L_1972160IT.01000102.tif": items[BODY],
               rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())}
    _unit(tmp_path / "a", CELEX, sound)
    _unit(tmp_path / "b", CELEX, swapped)
    _unit(tmp_path / "c", CELEX, {XHTML: b"<html/>", rdf.TREE_FILE: _scoped(_packaged_mtd())})
    for root, want in (("a", {}), ("b", {".xml holds tiff": 1, ".tif holds markup": 1}), ("c", {})):
        got = chk.check_chunk(chk.units(tmp_path / root, None), 3)
        assert dict(got["misnamed"]) == want, root
        assert got["counts"]["units-with-a-misnamed-file"] == (1 if want else 0)
    b = chk.check_chunk(chk.units(tmp_path / "b", None), 3)
    assert b["roots"]["main other"] == 1 and b["examples"]["main is tiff"], b["roots"]
    c = chk.check_chunk(chk.units(tmp_path / "c", None), 3)
    assert dict(c["package_lacked"]) == {"it 1972": 1} and not chk.check_chunk(
        chk.units(tmp_path / "a", None), 3)["package_lacked"]
    assert PKG not in {p.name for p in (tmp_path / "c").rglob("*")}, "read-only"


def _oj(page: int, seq: int = 1, root: str = "ANNEX") -> bytes:
    return (f'<?xml version="1.0"?><{root}><BIB.INSTANCE><DOCUMENT.REF FILE="L_2015038DE.01000101.doc.xml">'
            f'<PAGE.FIRST>1</PAGE.FIRST><PAGE.SEQ>1</PAGE.SEQ></DOCUMENT.REF><PAGE.FIRST>{page}</PAGE.FIRST>'
            f'<PAGE.SEQ>{seq}</PAGE.SEQ></BIB.INSTANCE></{root}>').encode()


def test_a_file_s_place_is_read_against_the_order_of_its_issue():
    """32015R0207 de: the annex's content (its own BIB.INSTANCE: page 5)
    under the body's name, the body's (page 1) under the annex's — the order
    inverted. A Special Edition keeps the order whatever its numbering
    (München, 2026-11-19, bg): +82 on every file; one offset per OJ issue
    its volume collects; a page more after a page break."""
    from app.plugins.eurlex import formex
    body, annex = "L_2015038DE.01000101.xml", "L_2015038DE.01000501.xml"
    assert formex.stated_place(annex, _oj(5)) == ("L_2015038DE.01", 5, 1, 5, 1)
    assert formex.stated_place("CL2016R0679EN0000020.0001.xml", _oj(5)) is None, "a consolidation's name"

    def places(stated: dict[str, int]) -> dict:
        return {n: formex.stated_place(n, _oj(p)) for n, p in stated.items()}
    third = "L_2015038DE.01004301.xml"
    assert formex.swapped(places({body: 1, annex: 5, third: 43})) == []
    assert formex.swapped(places({body: 5, annex: 1, third: 43})) == [body, annex]
    se = {f"L_1968093BG.0100{p:02d}01.xml": p + 82 for p in (15, 21, 22)}
    assert formex.swapped(places(se)) == [], "a Special Edition's own numbering"
    several = {"L_1998097BG.01000101.xml": 3, "L_1998104BG.01000101.xml": 4, "L_1998169BG.01000201.xml": 75}
    assert formex.swapped(places(several)) == [], "an offset per OJ issue"
    drift = {f"L_1998143BG.0100{p:02d}01.xml": s for p, s in ((10, 163), (12, 165), (14, 168), (16, 170))}
    assert formex.swapped(places(drift)) == [], "a page more after a break keeps the order"
    assert formex.swapped(places({body: 9})) == [], "alone in its issue: not judged"
    assert "holds tiff" in formex.misnamed("L_2015252EN.01004901.xml", b"II*\x00....")
    assert "holds markup" in formex.misnamed("L_2015252EN.01005202.tif", b"<?xml")
    assert formex.misnamed(BODY, _items(FX)[BODY]) is None


def _swapped_unit_files() -> dict[str, bytes]:
    """The fixture's act with its body and an annex in each other's place,
    its wrapper naming both (32015R0207 de's shape), and another act's file
    of the same issue beside them, numbered its own way."""
    from tests.test_eurlex_parser5 import WRAPPER as W5, _wrapper
    items = _items(FX)
    body, annex = "L_1972160IT.01000101.xml", "L_1972160IT.01000501.xml"
    return {W5: _wrapper([body], [annex]), body: _oj(5, root="ANNEX").replace(b"2015038DE", b"1972160IT"),
            annex: items[BODY], "L_1972160IT.01000301.xml": _oj(90)}


def test_the_check_counts_a_unit_whose_files_are_in_each_other_s_place(tmp_path):
    _unit(tmp_path, CELEX, {**_swapped_unit_files(), rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    got = chk.check_chunk(chk.units(tmp_path, None), 3)
    assert got["misnamed"]["formex file in another's place"] == 2, got["misnamed"]
    assert got["counts"]["units-with-a-file-not-its-name"] == 1 and got["counts"]["not-its-name it"] == 1


def test_the_parser_refuses_a_unit_whose_files_are_in_each_other_s_place():
    """The record would be the annex's text as the body: refused with its
    reason, never read (the unit is fetched again from Cellar)."""
    import pytest
    from app.plugins.eurlex import formex
    with pytest.raises(formex.FormexError, match="in each other's place: L_1972160IT.01000101.xml, "
                                                 "L_1972160IT.01000501.xml"):
        formex.parse(_swapped_unit_files())
    tiff = {**_items(FX), BODY: b"II*\x00" + b"\x00" * 64}
    with pytest.raises(formex.FormexError, match="holds tiff"):
        formex.parse(tiff)


def test_a_unit_holding_an_included_document_is_counted_and_listed(tmp_path):
    from tests.test_eurlex_parser5 import INCLUDED, WRAPPER as W5, ACT as ACT5, _annex_including, _wrapper
    _unit(tmp_path, CELEX, {W5: _wrapper(["A.xml"], ["N.xml"]), "A.xml": ACT5,
                            "N.xml": _annex_including("T.xml"), "T.xml": INCLUDED,
                            rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    got = chk.check_chunk(chk.units(tmp_path, None), 3)
    assert got["counts"]["units-with-an-included-document"] == 1 and len(got["included"]) == 1
    assert got["examples"]["included document"][0].endswith(": T.xml")
