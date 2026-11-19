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


def test_a_formex_file_stating_another_page_is_not_its_name():
    """32015R0207 de: the annex's content (its own BIB.INSTANCE: page 5,
    sequence 1) filed as the body's `…01000101.xml`; the DOCUMENT.REF inside
    names the wrapper's page and is not read."""
    from app.plugins.eurlex import formex
    annex = (b'<?xml version="1.0"?><ANNEX><BIB.INSTANCE><DOCUMENT.REF FILE="L_2015038DE.01000101.doc.xml">'
             b'<PAGE.FIRST>1</PAGE.FIRST><PAGE.SEQ>1</PAGE.SEQ><VOLUME.REF>01</VOLUME.REF></DOCUMENT.REF>'
             b'<NO.SEQ>0001.0001</NO.SEQ><PAGE.FIRST>5</PAGE.FIRST><PAGE.SEQ>1</PAGE.SEQ></BIB.INSTANCE>')
    assert formex.misnamed("L_2015038DE.01000501.xml", annex) is None
    assert "states page 5, its name page 1" in formex.misnamed("L_2015038DE.01000101.xml", annex)
    assert "holds tiff" in formex.misnamed("L_2015252EN.01004901.xml", b"II*\x00....")
    assert "holds markup" in formex.misnamed("L_2015252EN.01005202.tif", b"<?xml")
    assert formex.misnamed("CL2016R0679EN0000020.0001.xml", annex) is None, "a consolidation's name: not read"
    assert formex.misnamed(BODY, _items(FX)[BODY]) is None, "the fixture's act is its name's"


def test_the_check_counts_a_unit_whose_file_states_another_page(tmp_path):
    items = _items(FX)
    moved = items[BODY].replace(b"<PAGE.LAST>", b"<PAGE.LAST>", 1)
    # the act's own page restated as 9: another file's content under the body's name
    own = moved.split(b"</DOCUMENT.REF>", 1)
    moved = own[0] + b"</DOCUMENT.REF>" + own[1].replace(b"<PAGE.FIRST>1</PAGE.FIRST>", b"<PAGE.FIRST>9</PAGE.FIRST>", 1)
    _unit(tmp_path, CELEX, {**items, BODY: moved, rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    got = chk.check_chunk(chk.units(tmp_path, None), 3)
    assert got["misnamed"]["formex states another page or sequence"] == 1
    assert got["counts"]["units-with-a-file-not-its-name"] == 1 and got["counts"]["not-its-name it"] == 1


def test_a_unit_holding_an_included_document_is_counted_and_listed(tmp_path):
    from tests.test_eurlex_parser5 import INCLUDED, WRAPPER as W5, ACT as ACT5, _annex_including, _wrapper
    _unit(tmp_path, CELEX, {W5: _wrapper(["A.xml"], ["N.xml"]), "A.xml": ACT5,
                            "N.xml": _annex_including("T.xml"), "T.xml": INCLUDED,
                            rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    got = chk.check_chunk(chk.units(tmp_path, None), 3)
    assert got["counts"]["units-with-an-included-document"] == 1 and len(got["included"]) == 1
    assert got["examples"]["included document"][0].endswith(": T.xml")
