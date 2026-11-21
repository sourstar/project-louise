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

WHAT IS A HEADING (C11, docs/notes/eurlex-review-2026-11-21.md). Three
dialects: the OJ's XHTML (fmx2xhtml, CONVEX: `oj-ti-art`, `oj-doc-ti`),
the consolidated text's XHTML (`title-article-norm`, `title-annex-1`), and
the legacy HTML and the PDF, read by the headings' own words in the 24
languages (`_ARTICLE`, `_ANNEX`, the number first in et, fi, hu, lt, lv). In
every dialect a heading is NOT an article or an annex where it stands
  - in a table cell: an amending act's quoted article in a list item (32018R0825
    en: 12 "articles" against 5), a correlation table's "Articolul 12";
  - in a quotation: its text opens a quote mark, or its class says so
    (`oj-quotation-ti`: "ANNEX ‘ANNEX Ia");
  - nested inside the open article's own element (CONVEX's `art_1` div): a
    quoted article that is no list item (32016D1837's "‘Article 3");
  - by words alone (no element to tell): after a quoted heading, a heading
    of its kind numbered above it, until a line closes the quotation
    (32003L0051's '"Article 42e' then "Article 42f").
Its words stay in the article that quotes it.

THE STANDARDS NOT ADOPTED: an HTML5 tree builder (html5lib, lxml.html). The
standard library's `html.parser` reads both the fmx2xhtml XHTML and the
legacy HTML 4.01 without a new dependency. For PDF: pypdfium2, already the
node's (PDFium, Chrome's engine).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from .formex import Act, Annex, Article, norm

#: block elements: each opens and closes a line of text
_BLOCK = frozenset({"p", "div", "td", "th", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
                    "title", "dt", "dd", "blockquote", "pre", "caption", "br", "hr"})
_CELL = frozenset({"td", "th"})
_SKIP = frozenset({"script", "style"})
#: an article's heading in the 24 languages, alone on its line (the legacy
#: HTML and the PDF carry no class saying so): "Articolo 1", "Article 5",
#: "Artikel 2", "Artículo 3", "Artigo 4.o", "Άρθρο 1", "Член 1", "Článek 1",
#: "Članak 1", "Člen 1", "Artykuł 1", "Articolul 1", "Artikolu 1" (and the
#: legacy Maltese "L-Artikolu 1"), "Airteagal 1", "Artikkel 1", and the forms
#: that put the number first: "1 artikla", "1. cikk", "1. pants", "1 straipsnis"
_WORD_FIRST = (r"(?:Articolo|Article|Artikel|Artículo|Artigo|Άρθρο|Член|Článek|Článok|Članak|Člen|"
               r"Artykuł|Articolul|(?:L-)?Artikolu|Airteagal|Artikkel)")
#: an inserted article's mark, as the 24 languages write it (02016R1036-20180608
#: in each, C11): "14a" (cs da de en et ga mt pl ro sk sl sv), "14α" (el),
#: "14а" (bg, Cyrillic), "14 bis" (es fr it nl), "14.a" (hr), "14.o-A" (pt),
#: and number first "14 a artikla" (fi), "14a. cikk" (hu), "14.a pants" (lv),
#: "14a straipsnis" (lt)
_LATIN = r"(?:bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)"
_LETTER = r"[a-zα-ωа-я]"
_NUM = rf"\d+(?:{_LETTER}{{1,3}}|(?:\.?[º°o]|\.)?(?:\s*-?\s*(?:{_LATIN}|{_LETTER}))?)\.?"
_NUM_FIRST = rf"\d+{_LETTER}{{0,3}}\s*\.?\s*(?:{_LETTER}\s*\.?\s*)?(?:artikla|cikk|pants|straipsnis)"
#: the first article's French heading, "Article premier" / "Article 1er"
#: (2026-11-14, LEMUR's dry run: missed, French article 1 was folded into the
#: preamble — French found 0.593 of the articles), and a single article's
#: heading in the languages that name it so: "Article unique", "Articolo
#: unico", "Artículo único", "Artigo único"
_ORDINAL = r"(?:Article\s+(?:premier|1er)|(?:Article|Articolo|Artículo|Artigo)\s+(?:unique|unico|único))"
_ARTICLE = re.compile(rf"^(?:{_WORD_FIRST}\s+{_NUM}|{_ORDINAL}|{_NUM_FIRST})\s*$", re.IGNORECASE)
#: an annex's heading, alone on its line, after the articles: the word first
#: ("ANNEX I", "ANEXĂ" — ro's single annex —, "L-ANNESS I" — legacy mt), or
#: the number first, as et, hu, lv and lt write it ("I LISA", "I. MELLÉKLET",
#: "Ia PIELIKUMS", "I PRIEDAS"; 31992L0043, 32003L0087, 02016R1036-20180608)
_ANNEX_WORD = (r"(?:ALLEGATO|ANNEXE|ANNEX|ANHANG|ANEXO|ΠΑΡΑΡΤΗΜΑ|ПРИЛОЖЕНИЕ|PŘÍLOHA|PRÍLOHA|PRILOGA|PRILOG|"
               r"ZAŁĄCZNIK|ANEXA|ANEXĂ|(?:L-)?ANNESS|IARSCRÍBHINN|LIITE|BIJLAGE|BILAGA|BILAG|"
               r"LISA|MELLÉKLET|PIELIKUMS|PRIEDAS)")
_ANNEX_NUM_FIRST = r"(?:[IVXLC]+|\d+)[a-zа-я]?\.?\s+(?:LISA|MELLÉKLET|PIELIKUMS|PRIEDAS)"
_ANNEX = re.compile(rf"^(?:{_ANNEX_WORD}|{_ANNEX_NUM_FIRST})\b.{{0,40}}$")
#: the marks a quotation opens with, in the 24 languages' typography
#: (‘…’ en, «…» fr it es pt el, „…“ de bg cs hu lt pl ro sk sl, »…« da,
#: ”…” fi sv, and the legacy HTML's straight quotes)
_OPENING = ("‘", "“", "„", "«", "»", "”", "‚", "‹", "›", "'", '"')
#: a line that closes a quotation: a closing mark at its end, its stop after
#: it (the legacy HTML closes with a backtick: 31997L0011's "Article 4.`;")
_CLOSING = re.compile(r"[’'\"”»“«‘›‹`]\s*[.;,)]*\s*$")
#: a heading's number: arabic ("Article 10a", "14 a artikla"), else roman ("ANNEX IV", "Ia LISA")
_ARABIC = re.compile(r"(\d+)([^\s\d]*)")
_ROMAN_NUM = re.compile(r"(?:^|\s)([IVXLC]+)([a-z]?)(?=$|[\s.])")
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
#: the classes by dialect (the OJ's XHTML with "oj-" off, the consolidated text's)
_ART_CLS = {"oj": "ti-art", "cons": "title-article-norm"}
_STI_CLS = {"oj": "sti-art", "cons": "stitle-article-norm"}
_QUOTED_CLS = "quotation-ti"


def _number(heading: str) -> tuple[int, str] | None:
    """A heading's number as an ordered pair: "Article 10a" (10, "a"),
    "Artigo 14.o-A" (14, "oa"), "ANNEX IV" (4, ""), "ANNEX" None."""
    m = _ARABIC.search(heading)
    if m:
        return int(m[1]), re.sub(r"[\W\d_]", "", m[2]).lower()
    m = _ROMAN_NUM.search(heading)
    if m is None:
        return None
    vals = [_ROMAN[c] for c in m[1]]
    return sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals)), m[2]


@dataclass(frozen=True)
class _Line:
    """A line of text and where it stands."""
    cls: str                                  # the nearest classed block's first class, "oj-" off
    text: str
    classes: frozenset = frozenset()          # every class of that block, "oj-" off
    blocks: tuple = ()                        # the open blocks, outermost first: (serial, tag, id)
    cell: bool = False                        # inside a table cell

    @property
    def path(self) -> tuple[int, ...]:
        return tuple(b[0] for b in self.blocks)

    @property
    def parent(self) -> int:
        """The element holding the line's own block (0: the document)."""
        return self.blocks[-2][0] if len(self.blocks) >= 2 else 0


class _Reader(HTMLParser):
    """Lines of text (`_Line`) in reading order, every element's attributes,
    the head's title and metas. `serial` numbers the blocks from there, so
    the items of one manifestation never share a number."""

    def __init__(self, serial: int = 0) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[_Line] = []
        self.markup: list[list] = []
        self.metas: list[dict] = []
        self.title: list[str] = []
        self.serial = serial
        self._buf: list[str] = []
        # the open blocks: (tag, first class, every class, serial, id)
        self._classes: list[tuple[str, str, frozenset, int, str]] = []
        self._skip = 0
        self._in_title = False

    def _cls(self) -> tuple[str, frozenset]:
        return next(((c, cs) for _t, c, cs, _s, _i in reversed(self._classes) if c), ("", frozenset()))

    def _flush(self) -> None:
        text = norm("".join(self._buf))
        self._buf = []
        if text:
            cls, classes = self._cls()
            self.lines.append(_Line(cls, text, classes, tuple((s, t, i) for t, _c, _cs, s, i in self._classes),
                                    any(t in _CELL for t, *_ in self._classes)))

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
                classes = [re.sub(r"^oj-", "", c) for c in (a.get("class") or "").split()]
                self.serial += 1
                self._classes.append((tag, classes[0] if classes else "", frozenset(classes), self.serial,
                                      a.get("id") or ""))

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


@dataclass
class _Read:
    """What `_read` makes of an act's lines."""
    sections: list[tuple[str, str]]
    articles: list[Article]
    annexes: list[Annex]


def _dialect(lines: list[_Line]) -> str:
    """"oj" (the OJ's XHTML), "cons" (a consolidated text's XHTML) or
    "words" (legacy HTML, PDF: the headings' own words)."""
    if any(line.cls == _ART_CLS["oj"] for line in lines):
        return "oj"
    if any(line.cls == _ART_CLS["cons"] for line in lines):
        return "cons"
    return "words"


class _Layout:
    """The sections being laid out, line by line (`_read`)."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self.preamble: list[Any] = ["Preamble", []]
        self.sections: list[list[Any]] = [self.preamble]
        self.owners: list[tuple[Any, list[Any]]] = []        # (article or annex, its section)
        self.kind = "preamble"
        self.has_article = False            # an annex heading comes after an article
        self.partition = ""
        self.container: int | None = None   # the open article heading's own parent (0: the document)
        self.quote: str | None = None       # words: a quotation a quoted "article" / "annex" heading opened
        self.quoted: tuple[int, str] | None = None  # the number of its last heading

    # ---- what a line is ---------------------------------------------------------------------

    def heads(self, line: _Line) -> tuple[bool, bool]:
        """(an article heading, an annex heading)."""
        text, cls = line.text, line.cls
        free = not (line.cell or _QUOTED_CLS in line.classes or text.startswith(_OPENING))
        nested = self.container not in (None, 0) and self.container in line.path \
            and line.parent != self.container
        if self.dialect == "words":
            article, annex = bool(_ARTICLE.match(text)), bool(_ANNEX.match(text))
            if not line.cell and text.startswith(_OPENING):
                # A QUOTED heading opens a quotation: the headings of its kind
                # that follow, numbered above it, are the quoted text's until
                # it closes (`after`): 31997L0062's "'ANNEX I" then "ANNEX II",
                # 31997L0011's "'Article 3" … "'Article 10" then its own "Article 2"
                bare = text.lstrip("".join(_OPENING)).strip()
                kind = "article" if _ARTICLE.match(bare) else "annex" if _ANNEX.match(bare) else None
                if kind:
                    self.quote, self.quoted = kind, _number(bare)
            elif self.quote and (article and self.quote == "article" or annex and self.quote == "annex"):
                n = _number(text)
                if n is not None and self.quoted is not None and n > self.quoted:
                    self.quoted, article, annex = n, False, False   # the quotation's next heading
                else:
                    self.quote = None                               # the act's own: the quotation ended
        else:
            article = cls == _ART_CLS[self.dialect]
            annex = cls == "doc-ti" if self.dialect == "oj" else cls.startswith("title-annex")
        return article and free and not nested, annex and free and not nested and self.has_article

    def after(self, line: _Line) -> None:
        if self.quote and _CLOSING.search(line.text):
            self.quote = None

    # ---- where it goes ----------------------------------------------------------------------

    def article(self, line: _Line) -> None:
        label = f"{self.partition} › {line.text}" if self.partition else line.text
        self.sections.append([label, []])
        self.owners.append((Article(identifier=None, num=line.text, title="", partition=self.partition, body=""),
                            self.sections[-1]))
        self.kind, self.container = "article", line.parent
        self.has_article = True

    def subtitle(self, line: _Line) -> bool:
        if self.dialect == "words" or line.cls != _STI_CLS[self.dialect] or self.kind != "article" \
                or self.sections[-1][1] or line.cell:
            return False
        self.sections[-1][0] = f"{self.sections[-1][0]} — {line.text}"
        self.owners[-1][0].title = line.text
        return True

    def annex(self, line: _Line) -> None:
        if self.kind == "annex" and not self.sections[-1][1]:
            # a heading on several lines ("Progetto di" / "DECISIONE N. …" /
            # …): one heading, its lines joined
            self.sections[-1][0] = f"{self.sections[-1][0]} {line.text}"
            self.owners[-1][0].title = self.sections[-1][0]
        else:
            self.sections.append([line.text, []])
            self.owners.append((Annex(file=None, title=line.text, body=""), self.sections[-1]))
        # the articles that follow belong to it (an annexed act's own articles)
        self.partition = self.sections[-1][0]
        self.kind, self.container = "annex", None
        if self.quote == "article":
            self.quote = None

    def body(self, line: _Line) -> None:
        (self.preamble if self.kind == "preamble" else self.sections[-1])[1].append(line.text)

    # ---- the result -------------------------------------------------------------------------

    def read(self) -> _Read:
        for owner, sec in self.owners:
            owner.body = "\n".join(sec[1])
        out = [(label, "\n".join(body)) for label, body in self.sections if body or label != "Preamble"]
        return _Read(out, [o for o, _ in self.owners if isinstance(o, Article)],
                     [o for o, _ in self.owners if isinstance(o, Annex)])


def _read(lines: list[_Line]) -> _Read:
    """The act's sections, articles and annexes from its lines in reading
    order (module docstring: WHAT IS A HEADING)."""
    lay = _Layout(_dialect(lines))
    for line in lines:
        article, annex = lay.heads(line)
        if article:
            lay.article(line)
        elif not lay.subtitle(line):
            if annex:
                lay.annex(line)
            else:
                lay.body(line)
        lay.after(line)
    return lay.read()


def _sections(lines: list) -> tuple[list[tuple[str, str]], list[Article], list[Annex]]:
    """`(sections, articles, annexes)` from lines in reading order — `_Line`s,
    or `(class, text)` pairs (tools/chunker_languages.py)."""
    r = _read([x if isinstance(x, _Line) else _Line(x[0], x[1]) for x in lines])
    return r.sections, r.articles, r.annexes


def _act(root: str, body_file: str, lang: str, title: str, note, read: _Read,
         meta: dict, markup: dict) -> Act:
    act = Act(root=root, body_file=body_file, wrapper_file="", language=lang, title=title,
              date=note.date_document, publication_date=note.date_publication, consleg={},
              base_celex=None, recitals=[], articles=read.articles, annexes=read.annexes,
              meta=meta, notes=[], references=[], modifications=[], instructions=[], wrapper=None,
              markup=markup, rendered_sections=read.sections)
    return act


def parse_html(files: dict[str, bytes], names: list[str], *, lang: str, root: str, note) -> Act:
    """An XHTML or legacy HTML manifestation, its items in `names` order."""
    lines: list[_Line] = []
    markup: dict[str, list] = {}
    heads: dict[str, dict] = {}
    serial = 0
    for name in names:
        r = _Reader(serial)
        r.feed(files[name].decode("utf-8", errors="replace"))
        r.close()
        serial = r.serial
        lines += r.lines
        if r.markup:
            markup[name] = r.markup
        heads[name] = {k: v for k, v in (("title", norm("".join(r.title))), ("meta", r.metas)) if v}
    read = _read(lines)
    title = note.expression_title or next((x.text for x in lines if x.cls == "doc-ti"), "") or \
        next((h.get("title", "") for h in heads.values()), "")
    return _act(root, names[0], lang, title, note, read, {"head": heads, "form": "html"}, markup)


def parse_pdf(files: dict[str, bytes], names: list[str], *, lang: str, root: str, note) -> tuple[Act, int]:
    """A PDF manifestation: its text page by page (PDFium), its document
    information. Returns the act and how many pages had no text layer."""
    import pypdfium2 as pdfium
    lines: list[_Line] = []
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
                lines += [_Line("", norm(t)) for t in text.splitlines() if norm(t)]
        finally:
            doc.close()
    read = _read(lines)
    title = note.expression_title or ""
    return _act(root, names[0], lang, title, note, read, {"pdf": info, "form": "pdf"}, {}), blank
