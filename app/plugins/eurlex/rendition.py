"""An act Cellar holds WITHOUT Formex: its XHTML, its legacy HTML or its PDF,
read into the same `formex.Act` the rest of the plugin reads (parser 3).

WHY (München, 2026-11-14, every Italian work in force in the Data Dump): Formex
covers 30,249 of 43,623 works; 12,360 more are XHTML and HTML, 786 HTML alone,
228 PDF alone (5 of them scans). A Formex-only plugin left all of them out, and
the listing asked for fmx4 alone. Invariant 11: the act is taken in the best
form the origin has (cellar.FORMS), never left out.

WHAT IS KEPT, every word and every attribute:
  - the TEXT in sections in reading order: a preamble, each article (an
    article heading opens one), each annex (a document title after the
    articles), and the rest where it falls. Nothing is dropped. A heading the
    reader does not recognise leaves its words in the section before it;
  - the MARKUP: every element's attributes in document order (`markup`,
    as formex.attributes_of keeps Formex's);
  - the HEAD: the title and every <meta> (legacy HTML's Dublin Core: the
    subjects, the OJ source, the description);
  - a PDF's document information (its metadata dictionary) and its text page
    by page. A page without a text layer (a scan) is said, `no-text-layer`,
    and the PDF is held in the unit whole.
Script and style contents are chrome, not the act (invariant 11 (a)).

THE STANDARDS NOT ADOPTED: an HTML5 tree builder (html5lib, lxml.html). The
standard library's `html.parser` reads both the fmx2xhtml XHTML and the
legacy HTML 4.01 without a new dependency. For PDF: pypdfium2, already the
node's (PDFium, Chrome's engine).
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from .formex import Act, Annex, Article, norm

#: block elements: each opens and closes a line of text
_BLOCK = frozenset({"p", "div", "td", "th", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
                    "title", "dt", "dd", "blockquote", "pre", "caption", "br", "hr"})
_SKIP = frozenset({"script", "style"})
#: an article's heading in the 24 languages, alone on its line (the legacy
#: HTML and the PDF carry no class saying so): "Articolo 1", "Article 5",
#: "Artikel 2", "Artículo 3", "Artigo 4.o", "Άρθρο 1", "Член 1", "Článek 1",
#: "Članak 1", "Člen 1", "Artykuł 1", "Articolul 1", "Artikolu 1", "Airteagal 1",
#: "Artikkel 1", and the forms that put the number first: "1 artikla",
#: "1. cikk", "1. pants", "1 straipsnis"
_WORD_FIRST = (r"(?:Articolo|Article|Artikel|Artículo|Artigo|Άρθρο|Член|Článek|Článok|Članak|Člen|"
               r"Artykuł|Articolul|Artikolu|Airteagal|Artikkel)")
_NUM = r"\d+\s*(?:[.º°o]+|\.?o)?(?:\s*(?:bis|ter|quater|quinquies|sexies|[a-z]))?"
_ARTICLE = re.compile(rf"^(?:{_WORD_FIRST}\s+{_NUM}|\d+\s*\.?\s*(?:artikla|cikk|pants|straipsnis))\s*$", re.I)
#: an annex's heading, alone on its line, after the articles
_ANNEX = re.compile(r"^(?:ALLEGATO|ANNEX|ANHANG|ANEXO|ANNEXE|ΠΑΡΑΡΤΗΜΑ|ПРИЛОЖЕНИЕ|PŘÍLOHA|PRÍLOHA|"
                    r"PRILOG|PRILOGA|ZAŁĄCZNIK|ANEXA|ANNESS|IARSCRÍBHINN|LISA|LIITE|MELLÉKLET|"
                    r"PIELIKUMS|PRIEDAS|BIJLAGE|BILAG|BILAGA)\b.{0,40}$")


class _Reader(HTMLParser):
    """Lines of text `(class, text)` in reading order, every element's
    attributes, the head's title and metas."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[tuple[str, str]] = []
        self.markup: list[list] = []
        self.metas: list[dict] = []
        self.title: list[str] = []
        self._buf: list[str] = []
        self._classes: list[tuple[str, str]] = []      # (tag, class) of the open blocks
        self._skip = 0
        self._in_title = False

    def _cls(self) -> str:
        return next((c for _t, c in reversed(self._classes) if c), "")

    def _flush(self) -> None:
        text = norm("".join(self._buf))
        self._buf = []
        if text:
            self.lines.append((self._cls(), text))

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if a:
            self.markup.append([tag, a])
        if tag in _SKIP:
            self._skip += 1
        if tag == "meta":
            self.metas.append(a)
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK:
            self._flush()
            if tag not in ("br", "hr"):
                cls = re.sub(r"^oj-", "", (a.get("class") or "").split()[0]) if a.get("class") else ""
                self._classes.append((tag, cls))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in _BLOCK and tag not in ("br", "hr"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK and tag not in ("br", "hr"):
            self._flush()
            for i in range(len(self._classes) - 1, -1, -1):
                if self._classes[i][0] == tag:
                    del self._classes[i:]
                    break

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
            return
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _sections(lines: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[Article], list[Annex]]:
    """`(sections, articles, annexes)` from lines in reading order: by the
    XHTML's classes where it has them (`ti-art`, `sti-art`, `doc-ti`), else by
    the headings' own words (`_ARTICLE`, `_ANNEX`)."""
    classed = any(c == "ti-art" for c, _ in lines)
    sections: list[list[Any]] = [["Preamble", []]]
    owners: list[tuple[Any, list[Any]]] = []             # (article or annex, its section)
    kind = "preamble"
    partition = ""
    for cls, text in lines:
        is_article = cls == "ti-art" if classed else bool(_ARTICLE.match(text))
        is_annex = (cls == "doc-ti" if classed else bool(_ANNEX.match(text))) and \
            any(isinstance(o, Article) for o, _ in owners)
        if is_article:
            label = f"{partition} › {text}" if partition else text
            sections.append([label, []])
            owners.append((Article(identifier=None, num=text, title="", partition=partition, body=""),
                           sections[-1]))
            kind = "article"
            continue
        if classed and cls == "sti-art" and kind == "article" and not sections[-1][1]:
            sections[-1][0] = f"{sections[-1][0]} — {text}"
            owners[-1][0].title = text
            continue
        if is_annex:
            if kind == "annex" and not sections[-1][1]:
                # a heading on several lines ("Progetto di" / "DECISIONE N. …" /
                # …): one heading, its lines joined
                sections[-1][0] = f"{sections[-1][0]} {text}"
                owners[-1][0].title = sections[-1][0]
            else:
                sections.append([text, []])
                owners.append((Annex(file=None, title=text, body=""), sections[-1]))
            # the articles that follow belong to it (an annexed act's own articles)
            partition = sections[-1][0]
            kind = "annex"
            continue
        sections[-1][1].append(text)
    for owner, sec in owners:
        owner.body = "\n".join(sec[1])
    out = [(label, "\n".join(body)) for label, body in sections if body or label != "Preamble"]
    return (out, [o for o, _ in owners if isinstance(o, Article)],
            [o for o, _ in owners if isinstance(o, Annex)])


def _act(root: str, body_file: str, lang: str, title: str, note, sections, articles, annexes,
         meta: dict, markup: dict) -> Act:
    act = Act(root=root, body_file=body_file, wrapper_file="", language=lang, title=title,
              date=note.date_document, publication_date=note.date_publication, consleg={},
              base_celex=None, recitals=[], articles=articles, annexes=annexes, meta=meta,
              notes=[], references=[], modifications=[], instructions=[], wrapper=None,
              markup=markup, rendered_sections=sections)
    return act


def parse_html(files: dict[str, bytes], names: list[str], *, lang: str, root: str, note) -> Act:
    """An XHTML or legacy HTML manifestation, its items in `names` order."""
    lines: list[tuple[str, str]] = []
    markup: dict[str, list] = {}
    heads: dict[str, dict] = {}
    for name in names:
        r = _Reader()
        r.feed(files[name].decode("utf-8", errors="replace"))
        r.close()
        lines += r.lines
        if r.markup:
            markup[name] = r.markup
        heads[name] = {k: v for k, v in (("title", norm("".join(r.title))), ("meta", r.metas)) if v}
    sections, articles, annexes = _sections(lines)
    title = note.expression_title or next((t for c, t in lines if c == "doc-ti"), "") or \
        next((h.get("title", "") for h in heads.values()), "")
    return _act(root, names[0], lang, title, note, sections, articles, annexes,
                {"head": heads, "form": "html"}, markup)


def parse_pdf(files: dict[str, bytes], names: list[str], *, lang: str, root: str, note) -> tuple[Act, int]:
    """A PDF manifestation: its text page by page (PDFium), its document
    information. Returns the act and how many pages had no text layer."""
    import pypdfium2 as pdfium
    lines: list[tuple[str, str]] = []
    info: dict[str, Any] = {}
    blank = 0
    for name in names:
        doc = pdfium.PdfDocument(files[name])
        try:
            info[name] = {"pages": len(doc), **{k: v for k, v in doc.get_metadata_dict().items() if v}}
            for i in range(len(doc)):
                page = doc[i]
                text = page.get_textpage().get_text_range()
                if not text.strip():
                    blank += 1
                lines += [("", norm(t)) for t in text.splitlines() if norm(t)]
        finally:
            doc.close()
    sections, articles, annexes = _sections(lines)
    title = note.expression_title or ""
    return _act(root, names[0], lang, title, note, sections, articles, annexes,
                {"pdf": info, "form": "pdf"}, {}), blank
