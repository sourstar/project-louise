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
TOKENS = re.compile("([^]*)")


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
