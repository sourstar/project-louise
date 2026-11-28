"""tools/measure/eurlex_images_split.py: each image in one row, by the parser's reading."""
from __future__ import annotations

from tests.test_eurlex_package import FX, MTD, _items, _scoped, _unit
from app.plugins.eurlex import rdf
from tools.measure import eurlex_images_split as split

TIFF = b"II*\x00" + b"\x00" * 64


def test_a_placed_image_and_an_orphan_are_two_rows(tmp_path):
    items = _items(FX)
    body = "L_1972160IT.01000101.xml"
    items[body] = items[body].replace(b"</ACT>", b'<P><INCL.ELEMENT FILEREF="L_1972160IT.01000102.tif" '
                                                b'TYPE="TIFF"/></P></ACT>', 1)
    unit = _unit(tmp_path, {**items, "L_1972160IT.01000102.tif": TIFF, "L_1972160IT.01000103.tif": TIFF * 2,
                            rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    got = split.unit_rows(unit, "it")
    assert got[("placed", "n", "loose")] == 1 and got[("placed", "bytes", "loose")] == len(TIFF)
    rest = sum(got[(r, "n", "loose")] for r in ("orphan", "other-form", "pdf-scan"))
    assert rest == 1 and got[("unreadable", "n", "loose")] == 0, got


def test_main_prints_a_total(tmp_path, capsys):
    _unit(tmp_path, {**_items(FX), rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    assert split.main([str(tmp_path), "--workers", "1"]) == 0
    assert "TOTAL" in capsys.readouterr().out
