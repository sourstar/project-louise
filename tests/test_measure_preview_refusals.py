"""tools/measure/preview_refusals.py: how many of a corpus's TIFF plates the
renderer's own reader refuses before decoding, and by which rule — the count
the second review found owed for the rules added after München's header
count (measuring.md §6: counted, not inferred)."""
from __future__ import annotations

import json
import zipfile

from tests.ask.test_preview import header_only, tiff
from tools.measure import preview_refusals


def test_every_tiff_loose_or_in_a_zip_is_judged_by_the_renderers_own_rules(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    (corpus / "a" / ".hidden").mkdir(parents=True)
    (corpus / "a" / "page.tif").write_bytes(tiff((600, 800), "1", "group4"))
    (corpus / "a" / "bomb.tiff").write_bytes(header_only(30_000, 30_000))
    (corpus / "a" / "twice.tif").write_bytes(header_only(entries=[(256, 4, 1, 64), (257, 4, 1, 64),
                                                                  (259, 3, 1, 8), (259, 3, 1, 5)]))
    (corpus / "a" / "markup.tif").write_bytes(b"<?xml version='1.0'?><not-an-image/>")   # 29 such on the node
    (corpus / "a" / ".hidden" / "skipped.tif").write_bytes(tiff((64, 64), "L", "tiff_lzw"))
    with zipfile.ZipFile(corpus / "pkg.zip", "w") as z:
        z.writestr("doc/fig1.tif", tiff((300, 200), "RGB", "tiff_lzw"), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("doc/fig2.tif", tiff((300, 200), "L", "tiff_adobe_deflate"), compress_type=zipfile.ZIP_STORED)
        z.writestr("doc/text.xml", "<act/>")
    (corpus / "broken.zip").write_bytes(b"PK\x03\x04 not really a zip")
    out = tmp_path / "r.json"
    assert preview_refusals.main([str(corpus), "--workers", "2", "--out", str(out)]) == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out) == got
    assert got["files_seen"] == 6, "the hidden folder is not walked"
    assert (got["tiffs"], got["admitted"], got["refused"], got["unreadable"]) == (5, 2, 3, 1)
    assert got["by_rule"] == {"over 400 MP declared": 1, "a tag twice or out of order": 1,
                              "a coding outside the list": 1}
    where = {e["where"] for ex in got["examples"].values() for e in ex}
    assert str(corpus / "pkg.zip") + "!doc/fig2.tif" in where, "a member is named where it lies"
    assert got["examples"]["unreadable here"][0]["where"] == str(corpus / "broken.zip")
    assert got["largest_admitted"]["where"].endswith(("page.tif", "fig1.tif"))
    assert got["largest_admitted"]["held_mib"] > 0
