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
