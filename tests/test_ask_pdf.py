"""The Ask page's "save pdf" (app/ask_pdf.py; operator, 2026-11-11: "with the
query, the answer and all the references with the link to the original
source. Page format portrait A4 but configurable in the settings to portrait
letter"; choice A: the server makes the file). What goes in is what the
reader saw; every character is text until a rule makes it markup; only
http(s) origin links are written; nothing is fetched."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import api, ask_pdf
from app.ask_pdf import AskPdf, PdfReference

WHEN = datetime(2026, 11, 11, 17, 42, tzinfo=timezone.utc)
BASE = "https://ask.example.org"


def doc(**kw) -> AskPdf:
    refs = [
        PdfReference(n=1, title="Lighthouse of Alexandria", lang="en", collection="wikipedia",
                     chapter="History", passage="The tower was built in the third century BC.",
                     origin_url="https://en.wikipedia.org/wiki/Lighthouse_of_Alexandria",
                     book="2471318", chunk="wikipedia:2471318:3"),
        PdfReference(n=2, title="Lighthouse of Alexandria", title_original="Faro di Alessandria", lang="it",
                     collection="wikipedia", passage="Commissioned by Ptolemy I.", translated_to="en",
                     origin_url="javascript:alert(1)", book="8830114"),
        PdfReference(n=3, title="منارة الإسكندرية", lang="ar", collection="wikipedia",
                     passage="منارة الإسكندرية", origin_url="https://ar.wikipedia.org/wiki/x",
                     moved_by=["catalogue match: moved up from #5"]),
    ]
    base = dict(question="Who built the Lighthouse of Alexandria?", tz="Europe/Rome",
                answer="It was finished around 280 BC [1][2]. Its stone went to Qaitbay's fort [1].",
                answer_lang="en", mode="hybrid", results=3, reranked_from=50,
                collections=["wikipedia"], references=refs)
    base.update(kw)
    return AskPdf(**base)


def html_of(d: AskPdf) -> str:
    return ask_pdf.build_html(d, base_url=BASE, node="ask.example.org", when=ask_pdf.reader_time(d.tz, WHEN))


# ---- the answer's markdown: the page's subset, escape-first ----------------

def test_the_answer_is_text_until_a_rule_makes_it_markup():
    out = str(ask_pdf.markdown_html("**bold** <script>x</script> & *it*\n\n- one\n- two\n\n"
                                    "```\n<b>raw</b>\n```\n# Head\n[home](https://a.org) [bad](javascript:x)"))
    assert "<strong>bold</strong> &lt;script&gt;x&lt;/script&gt; &amp; <em>it</em>" in out
    assert "<ul><li>one</li><li>two</li></ul>" in out
    assert "<pre><code>&lt;b&gt;raw&lt;/b&gt;</code></pre>" in out
    assert "<h3>Head</h3>" in out
    assert '<a href="https://a.org">home</a>' in out and "javascript" not in out.split("[bad]")[0][-40:]
    assert "[bad](javascript:x)" in out, "a link that is not http(s) stays text"


def test_a_table_and_a_numbered_list_keep_their_shape():
    out = str(ask_pdf.markdown_html("| a | b |\n|---|---|\n| 1 | **2** |\n\n3. three\n4. four"))
    assert "<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td><strong>2</strong></td></tr></tbody></table>" in out
    assert '<ol start="3"><li>three</li><li>four</li></ol>' in out


def test_citations_link_to_their_reference_and_never_split():
    out = str(ask_pdf.markdown_html("Built [1][3]. Not [9]. Code `x [1]`.", frozenset({1, 3})))
    assert ('<span class="cites"><a class="cite" href="#ref-1">[1]</a>'
            '<a class="cite" href="#ref-3">[3]</a>.</span>') in out
    assert '<span class="cites">[9].</span>' in out, "a number the list does not have stays text"
    assert "<code>x [1]</code>" in out, "a citation inside code is code"
    assert ask_pdf.cited("a [1] `b [2]`\n```\n[3]\n```\nc [4]") == {1, 4}


# ---- links, names, the date -------------------------------------------------

@pytest.mark.parametrize("url,ok", [
    ("https://en.wikipedia.org/wiki/X", True), ("http://pubmed.ncbi.nlm.nih.gov/1/", True),
    ("javascript:alert(1)", False), ("file:///etc/passwd", False), ("https://", False),
    ("https://a.org/x y", False), ("data:text/html,x", False), (None, False)])
def test_only_http_links_with_a_host_are_written(url, ok):
    got = ask_pdf.safe_url(url)
    assert (got is not None and got == url) is ok


def test_the_reader_link_is_built_from_its_parts():
    assert (ask_pdf.reader_url(BASE + "/", "wiki pedia", "12/3", "c:1&x")
            == "https://ask.example.org/read/wiki%20pedia/12%2F3?hl=c%3A1%26x")
    assert ask_pdf.reader_url(BASE, "wikipedia", "7", None) == "https://ask.example.org/read/wikipedia/7"
    assert ask_pdf.reader_url(BASE, None, "7", None) is None


def test_the_file_is_named_by_date_and_question():
    assert ask_pdf.filename("Chi costruì il Faro d'Alessandria?", WHEN) == \
        "louise-2026-11-11-chi-costrui-il-faro-d-alessandria.pdf"
    assert ask_pdf.filename("亚历山大灯塔是谁建的？", WHEN) == "louise-2026-11-11-answer.pdf"
    assert len(ask_pdf.slug("word " * 40)) <= 48


def test_the_date_is_the_readers():
    assert ask_pdf.reader_time("Europe/Rome", WHEN).strftime("%H:%M %Z") == "18:42 CET"
    assert ask_pdf.reader_time("Not/AZone", WHEN).tzinfo == timezone.utc
    assert ask_pdf.reader_time(None, WHEN).tzinfo == timezone.utc


def test_the_footer_string_cannot_leave_its_css():
    s = str(ask_pdf.css_string('a "q" \\ </style><b>\nx'))
    assert s == 'a \\"q\\" \\\\ \\3C /style>\\3C b> x'


# ---- the document -----------------------------------------------------------

def test_the_page_says_what_the_reader_saw():
    html = html_of(doc())
    assert "size: A4 portrait" in html
    assert "Who built the Lighthouse of Alexandria?" in html
    assert "Answer in <b>English</b>" in html and "searched <b>wikipedia</b>" in html
    assert "<b>hybrid</b> search" in html and "<b>3</b> results, reranked from <b>50</b>" in html
    assert "References · 3, 2 cited in the answer" in html
    assert "— original title: Faro di Alessandria" in html and "translated from Italian" in html
    assert 'class="ref uncited" id="ref-3"' in html and "not cited in the answer" in html
    assert "catalogue match: moved up from #5" in html, "invariant 4, rule 2: what moved it is said"
    assert 'dir="rtl"' in html, "an Arabic passage runs right to left"
    assert "https://en.wikipedia.org/wiki/Lighthouse_of_Alexandria" in html
    assert "javascript" not in html and "this collection gives no link to the origin" in html
    assert "https://ask.example.org/read/wikipedia/2471318?hl=wikipedia%3A2471318%3A3" in html
    assert "18:42 CET" in html, "the reader's own time"


def test_letter_is_the_settings_other_paper():
    assert "size: letter portrait" in html_of(doc(paper="letter"))


def test_nothing_the_reader_sends_becomes_markup():
    evil = '<img src="http://169.254.169.254/x">'
    html = html_of(doc(question=f'Q {evil} "', answer=f"A {evil}",
                       references=[PdfReference(n=1, title=evil, passage=evil, chapter=evil,
                                                authors=evil, collection="c")]))
    assert "<img" not in html
    assert html.count("&lt;img") >= 5


def test_a_request_beyond_the_pages_limits_is_refused():
    with pytest.raises(ValueError):
        AskPdf(question="q", paper="a3")
    with pytest.raises(ValueError):
        AskPdf(question="q", references=[PdfReference(n=1)] * (ask_pdf.MAX_REFERENCES + 1))
    with pytest.raises(ValueError):
        AskPdf(question="")
    with pytest.raises(ValueError):
        AskPdf(question="q", collections=["x" * (ask_pdf.MAX_SHORT + 1)])
    with pytest.raises(ValueError):
        PdfReference(n=1, moved_by=["x" * (ask_pdf.MAX_SHORT + 1)])


def test_layouts_wait_their_turn_and_then_give_up(monkeypatch):
    monkeypatch.setattr(ask_pdf, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(ask_pdf, "WAIT_S", 0.05)
    ask_pdf._slots.acquire()
    try:
        with pytest.raises(ask_pdf.Busy):
            ask_pdf.render("<p>x</p>")
    finally:
        ask_pdf._slots.release()


# ---- the PDF itself (WeasyPrint, as the image installs it) -----------------

def test_the_pdf_is_on_the_paper_with_its_links():
    weasyprint = pytest.importorskip("weasyprint")
    for paper, width_mm in (("a4", 210.0), ("letter", 215.9)):
        page = weasyprint.HTML(string=html_of(doc(paper=paper))).render().pages[0]
        assert abs(page.width * 25.4 / 96 - width_mm) < 0.5, paper
    links = [ln[1] for p in weasyprint.HTML(string=html_of(doc())).render().pages for ln in p.links]
    assert "https://en.wikipedia.org/wiki/Lighthouse_of_Alexandria" in links
    assert "https://ask.example.org/read/wikipedia/8830114" in links
    assert not [u for u in links if "javascript" in str(u)]
    assert ask_pdf.render(html_of(doc())).startswith(b"%PDF-")


def test_nothing_is_fetched_while_the_pdf_is_made(monkeypatch):
    pytest.importorskip("weasyprint")
    asked = []

    def refuse(url, *a, **k):
        asked.append(url)
        raise ValueError("refused")
    monkeypatch.setattr(ask_pdf, "_refuse", refuse)
    pdf = ask_pdf.render('<p>x</p><img src="http://example.invalid/a.png">'
                         '<link rel="stylesheet" href="http://example.invalid/s.css">')
    assert pdf.startswith(b"%PDF-")
    assert asked and all(u.startswith("http://example.invalid/") for u in asked), \
        "every URL went to the refusing fetcher, none to the network"


# ---- the route and the page ---------------------------------------------------

def test_the_route_answers_a_download(isolated_config, monkeypatch):
    seen = {}

    def fake(html):
        seen["html"] = html
        return b"%PDF-1.7 stub"
    monkeypatch.setattr(ask_pdf, "render", fake)
    body = doc(paper="letter").model_dump()
    r = TestClient(api.app).post("/ask/pdf", json=body,
                                 headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "ask.example.org"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert r.headers["content-disposition"].startswith('attachment; filename="louise-')
    assert "who-built-the-lighthouse-of-alexandria.pdf" in r.headers["content-disposition"]
    assert "size: letter portrait" in seen["html"]
    assert "https://ask.example.org/read/wikipedia/2471318" in seen["html"], "the address the reader came by"


def test_the_route_says_why_it_cannot(isolated_config, monkeypatch):
    def busy(html):
        raise ask_pdf.Busy("2 PDFs are being laid out")
    monkeypatch.setattr(ask_pdf, "render", busy)
    c = TestClient(api.app)
    r = c.post("/ask/pdf", json=doc().model_dump())
    assert r.status_code == 503 and "being laid out" in r.json()["detail"]
    assert c.post("/ask/pdf", json={"question": "q", "paper": "a3"}).status_code == 422


def test_the_page_offers_the_button_and_the_paper(isolated_config):
    html = TestClient(api.app).get("/ask").text
    head = html[html.index('class="answer-head"'):html.index('id="answer" class="md"')]
    assert 'id="save-pdf"' in head and "save pdf" in head, "beside read aloud, in the answer's head"
    assert 'id="paper"' in html and '<option value="a4">A4</option><option value="letter">Letter</option>' in html
    assert "localStorage.setItem('louise-paper'" in html
    assert "fetch('/ask/pdf'" in html
