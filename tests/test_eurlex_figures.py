"""PARSER 6, THE FIGURES HALF, EUR-Lex's own part (architecture, 2026-11-28:
the plates' plumbing is RAGE's, app/rage/figures.py; the plugin walks its
texts): an image a Formex INCL.ELEMENT or an html <img> places leaves its
token where it stands, in reading order, in a footnote's text too; a
reader without `figures` leaves none (parser 5's bytes)."""
from __future__ import annotations

import re
from types import SimpleNamespace

from app.plugins.eurlex import formex, rendition
from tests.test_eurlex_package import FX, _items

BODY, WNAME = "L_1972160IT.01000101.xml", "L_1972160IT.01000101.doc.xml"
P1, P2, P3 = "L_1972160IT.01000102.tif", "L_1972160IT.01000103.jpg", "L_1972160IT.01000104.tif"
TOKENS = re.compile("\ue000([^\ue001\x1e]*)[^\ue001]*\ue001")


def _incl(name: str) -> bytes:
    return f'<INCL.ELEMENT FILEREF="{name}" TYPE="TIFF"/>'.encode()


def _items_with_plates() -> dict[str, bytes]:
    """The fixture act whose first article places P1, P2, then P1 again,
    and whose footnote places P3."""
    items = _items(FX)
    body = items[BODY]
    k = body.index(b">", body.index(b"<ALINEA", body.index(b"<ARTICLE"))) + 1
    items[BODY] = (body[:k] + _incl(P1) + b" testo " + _incl(P2) + b" " + _incl(P1)
                   + b'<NOTE NOTE.ID="E0001"><P>nota ' + _incl(P3) + b"</P></NOTE>" + body[k:])
    return items


def test_formex_leaves_a_token_per_appearance_in_reading_order_notes_too():
    act = formex.parse(_items_with_plates(), "31972R1473", figures=True)
    text = "\n".join(t for _l, t in formex.sections(act))
    assert TOKENS.findall(text) == [P1, P2, P1]
    assert [n for note in act.notes for n in TOKENS.findall(note["text"])] == [P3]
    assert " testo " in TOKENS.sub(" ", text), "the words around an image stay as they were"


def test_without_figures_formex_text_is_unchanged():
    plain = formex.parse(_items(FX), "31972R1473")
    with_refs = formex.parse(_items_with_plates(), "31972R1473")
    text = "\n".join(t for _l, t in formex.sections(with_refs))
    assert "" not in text and "" not in repr(with_refs.notes)
    assert len(formex.sections(plain)) == len(formex.sections(with_refs))


def test_an_html_img_leaves_its_token():
    note = SimpleNamespace(date_document=None, date_publication=None, expression_title="T")
    page = b'<html><body><p>Articolo 1</p><p>vedi <img src="img/f.png"/> qui</p></body></html>'
    act = rendition.parse_html({"x.html": page}, ["x.html"], lang="it", root="ACT", note=note, figures=True)
    assert TOKENS.findall("\n".join(t for _l, t in act.rendered_sections)) == ["f.png"]
    act = rendition.parse_html({"x.html": page}, ["x.html"], lang="it", root="ACT", note=note)
    assert "" not in repr(act.rendered_sections)


# ---- the record: the walk handed to RAGE's plates (#998) ------------------------------------

import hashlib  # noqa: E402

from app import plugin_io  # noqa: E402
from app.plugins.base import DiscoverState  # noqa: E402
from app.plugins.eurlex import SOURCE, rdf  # noqa: E402
from app.plugins.eurlex import source as src  # noqa: E402
from app.rage import figures as plates  # noqa: E402
from tests.test_eurlex_package import MTD, PKG, _package, _packaged_mtd, _scoped, _unit  # noqa: E402

TIFF, JPEG = b"II*\x00" + bytes(range(200)), b"\xff\xd8\xff\xe0" + b"j" * 300


def _doc(root, parser=SOURCE):
    (ref,) = parser.discover(DiscoverState(corpus_root=root))
    assert ref.error is None, ref.error
    return ref, plugin_io.canonical_doc(parser, {"native_id": ref.native_id, "path": ref.locator})


def test_the_record_names_each_appearance_sections_then_notes_and_reads_them_back(tmp_path):
    unit = _unit(tmp_path, {**_items_with_plates(), P1: TIFF, P2: JPEG, P3: TIFF + b"3",
                            rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    ref, doc = _doc(tmp_path)
    text = "\n".join(t for _l, t in doc.sections)
    assert text.count("[Illustration]") == 3 and "\ue000" not in text and plates.SENTINEL not in text
    assert sum(n["text"].count("[Illustration]") for n in doc.extra["notes"]) == 1
    assert [f.href for f in doc.figures] == [P1, P2, P1, P3] and [f.seq for f in doc.figures] == [0, 1, 2, 3]
    assert doc.figures[0].sha256 == hashlib.sha256(TIFF).hexdigest() and doc.figures[0].bytes == len(TIFF)
    assert all(f.carried for f in doc.figures)
    assert "\ue000" not in repr(doc.extra) and "\ue000" not in doc.title
    assert doc.origin_gaps == [], "the fixture's tree offers other forms: an unreferenced image is no gap"
    got = SOURCE.read_figures(unit, int(ref.native_id), {f.sha256: f.href for f in doc.figures})
    assert got[hashlib.sha256(JPEG).hexdigest()] == JPEG


def test_a_plate_in_a_package_is_named_from_its_member(tmp_path):
    files = {**_items_with_plates(), P1: TIFF, P2: JPEG, P3: TIFF + b"3"}
    unit = _unit(tmp_path, {PKG: _package(files), rdf.TREE_FILE: _scoped(_packaged_mtd())})
    ref, doc = _doc(tmp_path)
    assert [f.href for f in doc.figures] == [P1, P2, P1, P3]
    assert doc.figures[1].sha256 == hashlib.sha256(JPEG).hexdigest()
    got = dict(SOURCE.iter_figures(unit, int(ref.native_id), {doc.figures[0].sha256: P1}))
    assert got == {doc.figures[0].sha256: TIFF}


def test_an_image_the_text_places_and_the_unit_lacks_is_absent_with_no_marker(tmp_path):
    _unit(tmp_path, {**_items_with_plates(), P1: TIFF, P3: TIFF + b"3",
                     rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    _ref, doc = _doc(tmp_path)
    assert [f.href for f in doc.figures] == [P1, P1, P3]
    assert {"part": P2, "why": "absent"} in doc.origin_gaps


def test_parser_5_names_no_figure(tmp_path):
    _unit(tmp_path, {**_items_with_plates(), P1: TIFF, P2: JPEG, P3: TIFF + b"3",
                     rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    _ref, doc5 = _doc(tmp_path, SOURCE.at_parser_version(5))
    assert doc5.figures == [] and "[Illustration" not in "\n".join(t for _l, t in doc5.sections)


def test_held_plates_follow_the_orphan_rule():
    view = {"a.tif": TIFF, "b.tif": TIFF, "act.pdf": b"%PDF-1.4"}
    only = SimpleNamespace(items={"fmx4": [SimpleNamespace(name="w.doc.xml")]})
    assert src.held_plates(view, "fmx4", only) == ["a.tif", "b.tif"]
    other = SimpleNamespace(items={"fmx4": [SimpleNamespace(name="w")], "xhtml": [SimpleNamespace(name="x")]})
    assert src.held_plates(view, "fmx4", other) is None, "offered in another form"
    pdf = SimpleNamespace(items={"pdfa1b": [SimpleNamespace(name="act.pdf")]})
    assert src.held_plates(view, "pdfa1b", pdf) is None, "a PDF's page scans are not plates"


def test_an_html_alt_is_the_caption():
    note = SimpleNamespace(date_document=None, date_publication=None, expression_title="T")
    page = b'<html><body><p>Articolo 1</p><p>vedi <img src="img/f.png" alt="Mappa"/> qui</p></body></html>'
    act = rendition.parse_html({"x.html": page}, ["x.html"], lang="it", root="ACT", note=note, figures=True)
    text = "\n".join(t for _l, t in act.rendered_sections)
    assert "\ue000f.png\x1eMappa\ue001" in text
