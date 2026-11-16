"""The plates in the Ask page's "save pdf" (operator, 2026-11-16: "the results
pdf is not showing the pictures shown in the results"; app/ask_pdf.py). A
plate the page drew is drawn from this node's own bytes by the figure
route's own function and display rule, as a `data:` image the fetcher serves
from the page itself; anything it cannot draw is listed with the reason; no
URL is ever fetched."""
from __future__ import annotations

import struct
import zlib
from datetime import datetime, timezone

import pytest

from app import ask_pdf
from app.ask import figures as figures_mod
from app.ask_pdf import AskPdf, PdfPlate, PdfReference

WHEN = datetime(2026, 11, 16, 17, 0, tzinfo=timezone.utc)


def png(w: int = 2, h: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = png()


def plate(**kw) -> PdfPlate:
    base = dict(kind="img", collection="gutenberg", uid="1342", hash="sha256:ab" + "0" * 62,
                caption="Frontispiece")
    base.update(kw)
    return PdfPlate(**base)


def html_of(*plates: PdfPlate) -> str:
    d = AskPdf(question="Who drew the frontispiece?", answer="See [1].",
               references=[PdfReference(n=1, title="Pride and Prejudice", collection="gutenberg",
                                        passage="[Illustration: Frontispiece]", plates=list(plates))])
    return ask_pdf.build_html(d, base_url="https://ask.example.org", node="ask.example.org", when=WHEN)


@pytest.fixture
def held(monkeypatch):
    """The plates this node holds, by hash: what `figure_bytes` reads."""
    store: dict[str, tuple[bytes, str]] = {}

    def read(collection, uid, hash_):
        if hash_ not in store:
            raise figures_mod.FigureNotServed(404, f"{hash_[:12]}… is not held here yet")
        return store[hash_]
    monkeypatch.setattr(ask_pdf, "_plate_bytes", read)
    return store


def test_a_plate_the_page_drew_is_drawn_from_the_nodes_own_bytes(held):
    p = plate()
    held[p.hash] = (PNG, "image/png")
    html = html_of(p)
    assert 'src="data:image/png;base64,' in html and "Frontispiece" in html


def test_a_plate_the_route_would_not_show_inline_is_listed_with_why(held):
    p = plate()
    held[p.hash] = (b"<svg/>", "image/png")          # the record says png; the bytes are not
    html = html_of(p)
    assert "data:" not in html and "not displayable" in html and "Frontispiece" in html


def test_a_plate_not_held_is_listed_with_the_routes_reason(held):
    html = html_of(plate())
    assert "data:" not in html and "not drawn" in html and "is not held here yet" in html


def test_the_plates_bytes_of_one_pdf_are_bounded(held, monkeypatch):
    monkeypatch.setattr(ask_pdf, "MAX_PLATE_BYTES", len(PNG) + 10)
    a, b = plate(hash="sha256:" + "a" * 64), plate(hash="sha256:" + "b" * 64)
    held[a.hash] = held[b.hash] = (PNG, "image/png")
    html = html_of(a, b)
    assert html.count("data:image/png;base64,") == 1 and "are spent" in html


def test_a_link_is_written_only_as_http_and_the_rest_says_why(held):
    html = html_of(plate(kind="link", href="https://www.gutenberg.org/files/1342/1342-h/images/f.jpg"),
                   plate(kind="link", href="javascript:alert(1)"),
                   plate(kind="link", href="data:image/png;base64,AAAA"),
                   plate(kind="none", note="not available: no page of the origin is known"))
    assert "https://www.gutenberg.org/files/1342/1342-h/images/f.jpg" in html
    assert "javascript" not in html and "data:image" not in html
    assert "no page of the origin is known" in html


def test_what_the_reader_sends_about_a_plate_stays_text(held):
    evil = '<img src="http://169.254.169.254/x">'
    html = html_of(plate(caption=evil, collection=evil), plate(kind="none", caption=evil, note=evil))
    assert "<img" not in html and "&lt;img" in html


@pytest.mark.parametrize("url,ok", [
    ("data:image/png;base64," + __import__("base64").b64encode(PNG).decode(), True),
    ("data:image/jpeg;base64,/9j/", True),
    ("data:image/svg+xml;base64,PHN2Zy8+", False), ("data:text/html,<b>x</b>", False),
    ("data:image/png;base64,***", False), ("http://example.invalid/a.png", False)])
def test_the_fetcher_serves_only_a_plates_data_image(url, ok):
    assert (ask_pdf._data_image(url) is not None) is ok


def test_a_drawn_plate_is_in_the_pdf_and_nothing_is_fetched(held, monkeypatch):
    pytest.importorskip("weasyprint")
    asked = []

    def refuse(url, *a, **k):
        asked.append(url)
        raise ValueError("refused")
    monkeypatch.setattr(ask_pdf, "_refuse", refuse)
    p = plate()
    held[p.hash] = (png(40, 30), "image/png")
    pdf = ask_pdf.render(html_of(p))
    assert pdf.startswith(b"%PDF-") and b"/Subtype /Image" in pdf
    assert asked == [], "the plate came from the page itself, nothing went to a URL"


def test_the_apis_hits_bring_their_plates():
    hit = {"title": "Pride and Prejudice", "uid": "1342", "source_collection": "gutenberg",
           "figures": [{"hash": "sha256:" + "a" * 64, "caption": "Frontispiece", "state": "held"},
                       {"hash": "sha256:" + "b" * 64, "caption": "Map", "state": "not-held"},
                       {"hash": "sha256:" + "c" * 64, "caption": None, "state": "not-carried"}]}
    plates = ask_pdf._reference(1, hit, {}).plates
    assert [p.kind for p in plates] == ["img", "none", "none"]
    assert plates[0].collection == "gutenberg" and plates[0].uid == "1342"
    assert plates[1].note == "not held on this node" and plates[2].note == "not carried by this collection"


def test_the_page_sends_the_plates_it_shows():
    from pathlib import Path
    page = (Path(ask_pdf.__file__).parent / "templates" / "ask.html").read_text(encoding="utf-8")
    results = (Path(ask_pdf.__file__).parent / "templates" / "_ask_results.html").read_text(encoding="utf-8")
    assert "plates:pdfPlates(h)" in page and "function pdfPlates(h)" in page
    assert results.count("data-caption=") == 3, "every plate the page cannot draw names its caption"
