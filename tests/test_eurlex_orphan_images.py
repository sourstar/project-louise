"""ORPHAN IMAGES (operator, 2026-11-26: "either the same information are
offered in a different format or there is a reference to the image in the
text, both work, just signal when an image has no alternative form and no
reference in the text (orphan image). Add this check to the collections'
parsing consistency checks."). An image is placed where a Formex body names
it (INCL.ELEMENT FILEREF) or an html's <img src> does; it is offered in
another form where the expression's tree lists another manifestation, or
where the read PDF holds its page scans. Neither: the issue `orphan-image`,
said, the document parsed all the same."""
from __future__ import annotations

from types import SimpleNamespace

from app.plugins.eurlex import source as src
from app.plugins.eurlex.notice import Item

TIFF = b"II*\x00" + b"\x00" * 64
PDF = b"%PDF-1.6\n" + b"x" * 64


def _note(**forms):
    return SimpleNamespace(items={f: [Item(uri=f"u/{n}", name=n, size=None, order=None) for n in names]
                                  for f, names in forms.items()})


def test_an_image_the_body_includes_is_placed():
    view = {"L_1.000101.doc.xml": b"<DOC/>", "L_1.000101.xml": b'<ACT><INCL.ELEMENT FILEREF="L_1.000102.tif" '
            b'TYPE="TIFF" CONTENT="FORM"/></ACT>', "L_1.000102.tif": TIFF}
    assert src._orphan_images(view, "fmx4", _note(fmx4=list(view))) == []


def test_an_image_named_by_nothing_and_offered_in_no_other_form_is_an_orphan():
    view = {"L_1.000101.doc.xml": b'<DOC><REF.PHYS FILE="L_1.000102.tif"/></DOC>',   # a wrapper's listing places nothing
            "L_1.000101.xml": b"<ACT><P>text</P></ACT>", "L_1.000102.tif": TIFF, "L_1.000103.tif": TIFF}
    assert src._orphan_images(view, "fmx4", _note(fmx4=list(view))) == ["L_1.000102.tif", "L_1.000103.tif"]


def test_another_manifestation_of_the_expression_offers_it():
    view = {"L_1.000101.xml": b"<ACT/>", "L_1.000102.tif": TIFF}
    assert src._orphan_images(view, "fmx4", _note(fmx4=list(view), pdfa1a=["l_1.pdf"])) == []


def test_an_html_img_places_its_scan_and_an_unnamed_one_is_an_orphan():
    view = {"C_1.doc.html": b'<p class="image"><img src="./C_1.01002101.tif.jpg" alt="Image"/></p>',
            "C_1.01002101.tif.jpg": b"\xff\xd8\xff" + b"\x00" * 32, "C_1.01002201.tif.jpg": b"\xff\xd8\xff" + b"\x00" * 32}
    assert src._orphan_images(view, "html", _note(html=list(view))) == ["C_1.01002201.tif.jpg"]


def test_a_pdf_beside_its_page_scans_offers_them():
    """32007M4820 en: the decision's PDF and three TIFF page scans, one manifestation."""
    view = {"32007M4820_en.pdf": PDF, "MTF_EN0001.tif": TIFF, "MTF_EN0002.tif": TIFF}
    assert src._orphan_images(view, "pdf", _note(pdf=list(view))) == []


def test_the_issue_is_declared():
    assert "orphan-image" in src.ISSUES
