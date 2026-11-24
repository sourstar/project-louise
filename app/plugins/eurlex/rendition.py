"""An act Cellar holds WITHOUT Formex: its XHTML, its legacy HTML or its PDF,
read into the same `formex.Act` the rest of the plugin reads (parser 3).

WHY (München, 2026-11-14, every Italian work in force in the Data Dump): Formex
covers 30,249 of 43,623 works; 12,360 more are XHTML and HTML, 786 HTML alone,
228 PDF alone (5 of them scans). A Formex-only plugin left all of them out, and
the listing asked for fmx4 alone. Invariant 11: the act is taken in the best
form the origin has (cellar.FORMS), never left out.

WHAT IS KEPT, every word and every attribute:
  - the TEXT in sections in reading order: a preamble, each numbered recital
    (as formex `_recitals`: "Whereas (1)"), each article (an article heading
    opens one), each annex (a document title after the articles), and the
    rest where it falls. Nothing is dropped. A heading the reader does not
    recognise leaves its words in the section before it;
  - the FOOTNOTES (the XHTML's `note`, the consolidated text's `footnote`,
    the legacy HTML's numbered run after the final formula or ending an
    annex) out of the text and into the record's notes, each with the
    section that calls it — where formex.Collect puts a Formex NOTE;
  - the MARKUP: every element's attributes in document order (`markup`,
    as formex.attributes_of keeps Formex's);
  - the HEAD: the title and every <meta> (legacy HTML's Dublin Core: the
    subjects, the OJ source, the description);
  - a PDF's document information (its metadata dictionary) and its text page
    by page. A page without a text layer (a scan) is said, `no-text-layer`,
    and the PDF is held in the unit whole.
Script and style contents are chrome, not the act (invariant 11 (a)).

WHAT IS A HEADING (C11, docs/notes/eurlex-review-2026-11-21.md). Three
dialects: the OJ's XHTML (fmx2xhtml, CONVEX: `oj-ti-art`, `oj-doc-ti`,
`oj-note`), the consolidated text's XHTML (`title-article-norm`,
`title-annex-1`, `footnote`), and the legacy HTML and the PDF, read by the
headings' own words in the 24 languages (`_ARTICLE`, `_ANNEX`, the number
first in et, fi, hu, lt, lv). In every dialect a heading is NOT an article
or an annex where it stands
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
from dataclasses import dataclass, field
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
#: a numbered recital's number, alone (an XHTML table's first cell) or
#: opening its paragraph (legacy HTML): "(12)"
_RECITAL = re.compile(r"^\((\d+)\)(?:\s+\S.*)?$")
#: THE FINAL FORMULA AND THE SIGNATURES are a section of their own, "Final",
#: never the last article's text: in the XHTML its element's class says so
#: (`oj-final`, `oj-signatory`; the older XHTML's `final`, `signatory`); in
#: the legacy HTML and the PDF, from the line that opens it to the next annex.
#: That line in the 24 languages (31992L0043's in 21, 32004R0726's in 22,
#: 32018R0825's in 24): "Done at", "Fait à", "Geschehen zu", "Fatto a",
#: "Hecho en", "Feito em", "Gedaan te", "Udfærdiget i", "Utfärdad i",
#: "Tehty", "Sporządzono w", "Kelt", "Adoptat la", "Съставено в", "Έγινε
#: στο", "Priimta", "Sastavljeno u", "Magħmul(a) fi", "Arna dhéanamh", and a
#: date after the year in it; cs, sk, sl "V(e) <city> [dne] <date>"; el, et,
#: lv the city and the date alone ("Βρυξέλλες, 21 Μαΐου 1992.", "Brüssel,
#: 21. mai 1992", "Briselē, 1992. gada 21. maijā")
_FINAL_CLS = frozenset({"final", "signatory"})
_FINAL = re.compile(
    r"^(?:(?:Done at|Fait à|Geschehen zu|Fatto a|Hecho en|Feito em|Gedaan te|Udfærdiget i|Utfärda[dt] i|Tehty|"
    r"Sporządzono w|Kelt|Adoptat la|Съставено в|Έγινε στ\S*|Priimta|Sastavljeno u|Magħmula? fi|Arna dhéanamh)"
    r"\s.*\b(?:1[89]|20)\d\d\b.*"
    r"|Ve? [^\W\d_][^\s,]*,? (?:dne )?\d{1,2}\. [^\W\d_]+ (?:1[89]|20)\d\d\.?"
    r"|[^\W\d_][^\s,]*(?: [^\W\d_][^\s,]*)?, (?:\d{1,2}\.? [^\W\d_]+ (?:1[89]|20)\d\d|(?:1[89]|20)\d\d\. gada "
    r"\d{1,2}\. [^\W\d_]+)\.?)$")
#: LEGACY FOOTNOTES. The legacy HTML marks no footnote: they are the run of
#: lines that ends a segment — the final formula's, or an annex's — numbered
#: from "(1)" or "[1]" on, most citing the Official Journal in the
#: language's own abbreviation ("(1) OJ No C 247 …", "[1] Úř. věst. C 247 …",
#: "(1) Gu n. C 187 …"; 31992L0043 in 21 languages, 32004R0726 in 22); a
#: line without a number inside the run continues the note above it. Only
#: lines without a letter may follow the run ("-----", "19920521").
_FOOTNOTE = re.compile(r"^[(\[](\d+)[)\]]\s*\S")
_OJ_CITE = re.compile(r"(?<![^\W\d_])(?:OJ|JO|ABl|GU|Gu|DO|PB|EFT|EUT|EGT|EYVL|EUVL|ΕΕ|Úř\. ?věst|Ú\. ?v|Dz\. ?U|HL|"
                      r"UL|ELT|EÜT|OV|OL|ĠU|ОВ|SL|IO)(?![a-zà-žα-ωа-я])")
_HAS_LETTER = re.compile(r"[^\W\d_]")
#: the marks a quotation opens with, in the 24 languages' typography
#: (‘…’ en, «…» fr it es pt el, „…“ de bg cs hu lt pl ro sk sl, »…« da,
#: ”…” fi sv, and the legacy HTML's straight quotes)
_OPENING = ("‘", "“", "„", "«", "»", "”", "‚", "‹", "›", "'", '"')
#: a line that closes a quotation: a stop, a closing mark, maybe a stop after
#: it, at its end (the legacy HTML closes with a backtick: 31997L0011's
#: "Article 4.`;"). A mark right after a word closes a quotation inside the
#: quoted text: 32004R0869's '(hereinafter referred to as the "IUU list").',
#: whose close let the quoted "Article 20" … "Article 20e" be articles
_CLOSING = re.compile(r"[.;:!?)\]]\s?[’'\"”»“«‘›‹`]\s*[.;,)]*\s*$")
#: a heading's number: arabic ("Article 10a", "14 a artikla"), else roman ("ANNEX IV", "Ia LISA")
_ARABIC = re.compile(r"(\d+)(\s?[^\W\d_](?![^\W\d_])|[^\s\d]*)")   # "20 d" as "20d" (32004R0869)
_ROMAN_NUM = re.compile(r"(?:^|\s)([IVXLC]+)([a-z]?)(?=$|[\s.])")
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
#: the classes by dialect (the OJ's XHTML with "oj-" off, the consolidated text's)
_ART_CLS = {"oj": "ti-art", "cons": "title-article-norm"}
_STI_CLS = {"oj": "sti-art", "cons": "stitle-article-norm"}
_NOTE_CLS = frozenset({"note", "footnote"})
_QUOTED_CLS = "quotation-ti"
#: DIVISIONS — part, title, chapter, section, sub-section — label the articles
#: under them, as Formex's DIVISION does ("CHAPTER II — Principles › Article
#: 5"). CONVEX says which by its element's id (`prt_I`, `prt_I.tis_II`,
#: `cpt_III.sct_1`; its title in `….tit_1`): the number line is not always
#: `oj-ti-section-1` (et, fi, hu, lt, lv: `oj-ti-grseq-1`, 32013R0575). The
#: older XHTML and the consolidated text by their classes, the legacy HTML
#: and the PDF by the heading's words; the level, there, by the word.
_DIV_ID = re.compile(r"(?:^|\.)(prt|tis|cpt|sct|sbs)_[^.]+$")
_DIV_LEVEL = {"prt": 1, "tis": 2, "cpt": 3, "sct": 4, "sbs": 5}
_DIV_HEAD_CLS = frozenset({"ti-section-1", "title-division-1"})
_DIV_TITLE_CLS = frozenset({"ti-section-2", "title-division-2"})
#: the division words of the 24 languages, by level (32013R0575's CONVEX in
#: each, read by its element ids; 32004R0726's legacy HTML in 22)
_DIV_WORDS = {
    5: ("Sub-section|Subsection|Sous-section|Unterabschnitt|Sottosezione|Subsección|Subsecção|Subseção|"
        "Onderafdeling|Pododdíl|Pododdiel|Podsekcja|alszakasz|Subsecțiunea|Subsecţiunea|Подраздел|Ενότητα|"
        "Ενοτητα|Underafdeling|Underavsnitt|Alajakso|Alajagu|apakšiedaļa|Poskirsnis|Subtaqsima|Pododdelek|"
        "Pododjeljak|Foroinn"),
    4: ("Section|Abschnitt|Sezione|Sección|Secção|Seção|Afdeling|Oddíl|Oddiel|Sekcja|szakasz|Secțiunea|"
        "Secţiunea|Раздел|Τμήμα|Τμημα|Avsnitt|Jakso|Jagu|iedaļa|Skirsnis|Taqsima|Oddelek|Odjeljak|Roinn"),
    3: ("Chapter|Chapitre|Kapitel|Capo|Capítulo|Hoofdstuk|Kapitola|Rozdział|Fejezet|Capitolul|Глава|Κεφάλαιο|"
        "Κεφαλαιο|Luku|Peatükk|Nodaļa|Skyrius|Kapitolu|Poglavje|Poglavlje|Caibidil"),
    2: ("Title|Titre|Titel|Titolo|Título|Hlava|Tytuł|Cím|Titlul|Дял|Τίτλος|Τιτλος|Afsnit|Avdelning|Osasto|"
        "Jaotis|Sadaļa|Antraštinė dalis|Titolu|Naslov|Glava|Teideal"),
    1: "Part|Partie|Teil|Parte|Deel|Část|Časť|Część|Rész|Partea|Част|Μέρος|Μερος|Del|Osa|Daļa|Dalis|Parti|Dio|Cuid",
}
#: its number: roman (Latin or Greek letters), arabic, a letter after it, a
#: dot ("TITLE I", "1. FEJEZET", "GLAVA I.", "TITEL IIa"); or spelled, each
#: word capitalised ("PART ONE", "ERSTER TEIL", "Μεροσ Δευτερο", "PARTEA A
#: DOUA", "CUID a hAON DÉAG": ro's and ga's "a" before it)
_DIV_NUM = r"(?:[IVXLCΙΧ]+|\d+)(?:\s?[a-zα-ωа-я])?\.?"
_DIV_ORD = r"(?:a\s+)?[^\W\d_]+(?:\s+[^\W\d_]+)?"
_DIVISION = {level: (re.compile(rf"^(?:(?:{words})\s+{_DIV_NUM}|{_DIV_NUM}\s+(?:{words}))$", re.IGNORECASE),
                     re.compile(rf"^(?:(?:{words})\s+{_DIV_ORD}|{_DIV_ORD}\s+(?:{words}))$", re.IGNORECASE))
             for level, words in sorted(_DIV_WORDS.items(), reverse=True)}


def _division_level(text: str) -> int | None:
    """The level a division heading's words give (1 part … 5 sub-section),
    None for a line that is no division heading."""
    for level, (numbered, spelled) in _DIVISION.items():
        if numbered.match(text):
            return level
        if spelled.match(text) and all(w[0].isupper() or w in ("a", "A") or (w[0] == "h" and w[1:2].isupper())
                                       for w in text.split()):
            return level
    return None


def _number(heading: str) -> tuple[int, str] | None:
    """A heading's number as an ordered pair: "Article 10a" and "Article 10
    a" (10, "a"), "Artigo 14.o-A" (14, "oa"), "ANNEX IV" (4, ""), "ANNEX" None."""
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
    within: frozenset = frozenset()           # the classes of every open block, "oj-" off
    anchors: tuple = ()                       # the <a id|name> it holds
    links: tuple = ()                         # the "#…" targets of its <a href>

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
        self._anchors: list[str] = []
        self._links: list[str] = []
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
                                    any(t in _CELL for t, *_ in self._classes),
                                    frozenset().union(*(cs for _t, _c, cs, _s, _i in self._classes)),
                                    tuple(self._anchors), tuple(self._links)))
            self._anchors, self._links = [], []

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
        if tag == "a":
            self._anchors += [v for k in ("id", "name") if (v := a.get(k))]
            if a.get("href", "").startswith("#"):
                self._links.append(a["href"][1:])
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
    recitals: list[tuple[str, str]] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


def _dialect(lines: list[_Line]) -> str:
    """"oj" (the OJ's XHTML), "cons" (a consolidated text's XHTML) or
    "words" (legacy HTML, PDF: the headings' own words)."""
    if any(line.cls == _ART_CLS["oj"] for line in lines):
        return "oj"
    if any(line.cls == _ART_CLS["cons"] for line in lines):
        return "cons"
    return "words"


@dataclass
class _Div:
    """A division heading while its articles are read."""
    level: int
    head: str
    element: int | None                      # its CONVEX element (None: the older forms, by level)
    where: tuple[list[Any], int]             # the section and the place it stands in
    title: list[str] = field(default_factory=list)
    used: bool = False                       # an article under it carries its label

    @property
    def label(self) -> str:
        return " — ".join(x for x in (self.head, " ".join(self.title)) if x)

    @property
    def lines(self) -> list[str]:
        return [x for x in (self.head, *self.title) if x]


class _Layout:
    """The sections being laid out, line by line (`_read`)."""

    def __init__(self, dialect: str, recitals: bool, footnotes: bool = False) -> None:
        self.dialect, self.recitals, self.footnotes = dialect, recitals, footnotes
        self.preamble: list[Any] = ["Preamble", []]
        self.sections: list[list[Any]] = [self.preamble]
        self.owners: list[tuple[Any, list[Any]]] = []        # (article or annex, its section)
        self.recs: list[list[Any]] = []                     # the recitals' sections
        self.notes: list[tuple[dict, tuple, list[Any]]] = []  # (note, its back links, where it stands)
        self.placed: dict[str, list[Any]] = {}              # an anchor -> the section its line went to
        self.kind = "preamble"
        self.has_article = False            # an annex heading comes after an article
        self.partition = ""
        self.container: int | None = None   # the open article heading's own parent (0: the document)
        self.rec: list[Any] | None = None   # the open recital: [its section, its element (None: a paragraph)]
        self.expected = 1                   # the next recital's number
        self.init = ""                      # the line before the first recital ("Whereas:")
        self.cur = self.preamble            # the section the last line went to
        self.quote: str | None = None       # words: a quotation a quoted "article" / "annex" heading opened
        self.quoted: tuple[int, str] | None = None  # the number of its last heading
        self.quoting = False                # words: any other quotation open (no division in it)
        self.divs: list[_Div] = []          # the divisions the next article stands in, outermost first
        self.titled: _Div | None = None     # the division whose title the next line may be
        self.unused: list[_Div] = []        # divisions no article took: their words go back as text

    # ---- divisions ------------------------------------------------------------------------------

    def _close(self, keep: int) -> None:
        """The divisions from `keep` on end: an unused one's words are text."""
        for d in self.divs[keep:]:
            if not d.used:
                self.unused.append(d)
        del self.divs[keep:]
        self.titled = None

    def division(self, line: _Line) -> bool:
        """A division's heading or title: it labels the articles under it
        and is no section's text. True where the line is one."""
        if any(d.element is not None and d.element not in line.path for d in self.divs):
            self._close(next(i for i, d in enumerate(self.divs) if d.element is not None
                             and d.element not in line.path))
        if self.kind not in ("preamble", "article", "annex"):
            return False
        text, cls = line.text, line.cls
        free = not (line.cell or _QUOTED_CLS in line.classes or text.startswith(_OPENING))
        nested = self.container not in (None, 0) and self.container in line.path \
            and line.parent != self.container
        el = next(((s, i) for s, _t, i in reversed(line.blocks) if _DIV_ID.search(i)), None)
        if el is not None:
            # CONVEX: the first line in a division's element is its heading,
            # its `….tit_N` child its title (the consolidated text's: the
            # `title-division-2` after it)
            serial, eid = el
            in_title = any(i.startswith(eid + ".tit_") for _s, _t, i in line.blocks)
            held = next((d for d in self.divs if d.element == serial), None)
            if held is not None:
                if in_title or (self.titled is held and cls in _DIV_TITLE_CLS):
                    held.title.append(text)
                    return True
                self.titled = None
                return False
            if nested or not free:
                return False
            level = _DIV_LEVEL[_DIV_ID.search(eid).group(1)]
            self._open(level, "" if in_title else text, serial)
            if in_title:
                self.divs[-1].title.append(text)
            return True
        if self.titled is not None and (cls in _DIV_TITLE_CLS if self.dialect != "words" else (
                not _ARTICLE.match(text) and not _ANNEX.match(text) and _division_level(text) is None
                and not re.search(r"[.;:,]$", text))):
            self.titled.title.append(text)
            if self.dialect == "words":
                self.titled = None                      # one line: the words' title
            return True
        self.titled = None
        if not free or nested or (self.dialect == "words" and (self.quote or self.quoting)):
            return False
        if self.dialect != "words" and cls not in _DIV_HEAD_CLS:
            return False
        level = _division_level(text)
        if level is None and self.dialect == "words":
            return False
        self._open(level or 9, text, None)
        return True

    def _open(self, level: int, head: str, element: int | None) -> None:
        keep = next((i for i, d in enumerate(self.divs) if d.level >= level), len(self.divs))
        self._close(keep)
        self.divs.append(_Div(level, head, element, (self.cur, len(self.cur[1]))))
        self.titled = self.divs[-1]

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
                # 31997L0011's "'Article 3" … "'Article 10" then its own "Article
                # 2". A quoted division's: its articles are the quotation's, the
                # first whatever its number (32004L0027's '"TITLE VIIIa' then
                # "Article 88a", 31998R0075's "'TITLE II" then "Article 856a")
                # (32004R0869's '"CHAPTER II CONTROL AND SURVEILLANCE …', its
                # title on its line). Any other quoted line holds divisions off
                # till its quotation closes (31998R0075's "'Privileged
                # operations" / "TITLE I" / "RETURNED GOODS`;")
                bare = text.lstrip("".join(_OPENING)).strip()
                words = bare.split()
                if _ARTICLE.match(bare) or _ANNEX.match(bare):
                    self.quote = "article" if _ARTICLE.match(bare) else "annex"
                    self.quoted = _number(bare)
                elif any(_division_level(" ".join(words[:k])) is not None for k in (len(words), 2, 3)):
                    self.quote, self.quoted = "article", None
                else:
                    self.quoting = True
            elif self.quote and (article and self.quote == "article" or annex and self.quote == "annex"):
                n = _number(text)
                if n is not None and (self.quoted is None or n > self.quoted):
                    self.quoted, article, annex = n, False, False   # the quotation's next heading
                else:
                    self.quote = None                               # the act's own: the quotation ended
        else:
            article = cls == _ART_CLS[self.dialect]
            annex = cls == "doc-ti" if self.dialect == "oj" else cls.startswith("title-annex")
        return article and free and not nested, annex and free and not nested and self.has_article

    def after(self, line: _Line) -> None:
        for a in line.anchors:
            self.placed[a] = self.cur
        if (self.quote or self.quoting) and _CLOSING.search(line.text):
            self.quote, self.quoting = None, False

    # ---- where it goes ----------------------------------------------------------------------

    def note(self, line: _Line, follows_note: bool) -> None:
        if self.notes and follows_note and not (line.anchors or line.links):
            self.notes[-1][0]["text"] += "\n" + line.text        # the same note's next line
            return
        self.notes.append(({"section": "", **({"@": {"id": line.anchors[0]}} if line.anchors else {}),
                            "text": line.text}, line.links, self.cur))

    def article(self, line: _Line) -> None:
        rec = self.rec
        if self.kind == "preamble" and rec is not None and rec[1] is None:
            # a paragraph's recital runs to the next: the enacting formula
            # ("HAVE ADOPTED THIS DIRECTIVE:", its last line ending so) and
            # what follows it is the preamble's
            body = rec[0][1]
            j = next((i for i in range(len(body) - 1, 0, -1) if body[i].endswith(":")), None)
            if j is not None:
                self.preamble[1] += body[j:]
                del body[j:]
        # its partition: the annex it stands in (an annexed act's), the
        # divisions, outermost first — Formex's "CHAPTER II — Principles › …"
        partition = " › ".join([self.partition] * bool(self.partition) + [d.label for d in self.divs])
        for d in self.divs:
            d.used = True
        self.titled = None
        label = f"{partition} › {line.text}" if partition else line.text
        self.sections.append([label, []])
        self.owners.append((Article(identifier=None, num=line.text, title="", partition=partition, body=""),
                            self.sections[-1]))
        self.kind, self.container, self.rec, self.cur = "article", line.parent, None, self.sections[-1]
        self.has_article = True

    def subtitle(self, line: _Line, after: str = "") -> bool:
        """The line after an article heading that titles it: by class; by
        words (legacy HTML, PDF), a short line, a capital first, ending
        without a stop, no heading, and not leading into the line `after` it
        — a list's point, a sentence going on in lower case ("Ai fini della
        presente direttiva si intende per" / "a) …", 31992L0043 it): "Article
        1" / "Subject matter", as Formex's STI.ART (32003L0087, 32003L0059,
        32004L0049, 31998L0020)."""
        if self.kind != "article" or self.sections[-1][1] or line.cell:
            return False
        text = line.text
        if self.dialect == "words":
            if len(text.split()) > 15 or re.search(r"[.;:,]$", text) or not text[:1].isupper() \
                    or _ARTICLE.match(text) or _ANNEX.match(text) or _division_level(text) is not None \
                    or _FINAL.match(text) or self.quote or not re.match(r"[^\W_]", after) \
                    or after[:1].islower() or re.match(r"(?:[a-z]|[ivx]+)\)|\([a-z]+\)", after) \
                    or _ARTICLE.match(after) or _ANNEX.match(after) or _FINAL.match(after):
                return False                        # (a title has its article's text after it)
        elif line.cls != _STI_CLS[self.dialect]:
            return False
        self.sections[-1][0] = f"{self.sections[-1][0]} — {line.text}"
        self.owners[-1][0].title = line.text
        return True

    def annex(self, line: _Line) -> None:
        self._close(0)
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
        self.kind, self.container, self.rec, self.cur = "annex", None, None, self.sections[-1]
        if self.quote == "article":
            self.quote = None

    def in_preamble(self, line: _Line) -> None:
        text, rec = line.text, self.rec
        if not self.recitals:
            self.preamble[1].append(text)
            self.cur = self.preamble
            return
        if rec is not None and rec[1] is not None and rec[1] not in line.path:
            rec = self.rec = None                       # its element closed
        m = _RECITAL.match(text)
        rct = next((s for s, _t, i in reversed(line.blocks) if i.startswith("rct_")), None)
        # a recital starts: CONVEX's `rct_N` element; else the next number, as
        # a table row's first cell (the row is the recital) or opening a
        # paragraph outside any open recital's element (it runs to the next)
        starts, element = False, None
        if rct is not None and (rec is None or rec[1] != rct):
            starts, element = True, rct
        elif m and int(m[1]) == self.expected and not (rec is not None and rec[1] is not None):
            starts = True
            element = next((s for s, t, _i in reversed(line.blocks) if t == "tr"), None) if line.cell else None
        if starts:
            if not self.recs:
                last = self.preamble[1][-1] if self.preamble[1] else ""
                self.init = last if last.endswith(":") else ""
            num = f"({m[1]})" if m else ""
            if m:
                self.expected = int(m[1]) + 1
            label = " ".join(x for x in (self.init.rstrip(":").strip(), num) if x) or "Recital"
            self.sections.append([label, [text]])
            self.recs.append(self.sections[-1])
            self.rec, self.cur = [self.sections[-1], element], self.sections[-1]
        elif rec is not None:
            body = rec[0][1]
            if len(body) == 1 and _RECITAL.match(body[0]) and " " not in body[0]:
                body[0] = f"{body[0]} {text}"           # "(1)" | its text: one line, as Formex's NP
            else:
                body.append(text)
            self.cur = rec[0]
        else:
            self.preamble[1].append(text)
            self.cur = self.preamble

    def final(self, line: _Line) -> bool:
        """The final formula opens here: a section of its own, "Final" (an
        annexed act's under its annex), till the next annex or article."""
        if self.kind != "article":
            return False
        if self.dialect == "words":
            if len(line.text) > 160 or not _FINAL.match(line.text):
                return False
        elif not line.within & _FINAL_CLS:
            return False
        self._close(0)
        self.sections.append([f"{self.partition} › Final" if self.partition else "Final", [line.text]])
        self.kind, self.container, self.rec, self.cur = "final", None, None, self.sections[-1]
        return True

    def body(self, line: _Line) -> None:
        if self.kind == "preamble":
            self.in_preamble(line)
        else:
            self.sections[-1][1].append(line.text)
            self.cur = self.sections[-1]

    # ---- the result -------------------------------------------------------------------------

    def _legacy_notes(self) -> list[dict]:
        """The legacy HTML's footnotes (`_FOOTNOTE`) out of the sections
        that end a segment — the one before each annex, and the last —
        into notes, each filed under the section holding its call "(n)"
        ("[n]"): the first after the previous note's, from the act's start
        (an annex's own: in the annex), else where the note stood."""
        annexes = {id(sec) for owner, sec in self.owners if isinstance(owner, Annex)}
        recitals = {id(sec) for sec in self.recs}
        out: list[dict] = []
        for k, sec in enumerate(self.sections):
            last = k + 1 == len(self.sections) or id(self.sections[k + 1]) in annexes
            if not last or sec is self.preamble or id(sec) in recitals:
                continue
            body = sec[1]
            end = len(body)
            while end and not _HAS_LETTER.search(body[end - 1]):
                end -= 1
            first, expect = None, None
            for i in range(end - 1, -1, -1):
                m = _FOOTNOTE.match(body[i])
                if m is None:
                    if expect is None and not _OJ_CITE.search(body[i]):
                        break                       # the segment ends with no footnote
                    continue                        # a note's next line
                n = int(m[1])
                if expect is not None and n != expect:
                    break
                if n == 1:
                    first = i
                    break
                expect = n - 1
            if first is None:
                continue
            marked = [x for x in body[first:end] if _FOOTNOTE.match(x)]
            cited = sum(1 for x in marked if _OJ_CITE.search(x))
            if not cited or (sec[0] != "Final" and 2 * cited < len(marked)):
                continue                            # numbered text, not notes citing the OJ
            block: list[list[str]] = []
            for x in body[first:end]:
                if _FOOTNOTE.match(x):
                    block.append([x])
                else:
                    block[-1].append(x)
            del body[first:end]
            # the calls: in the annex for an annex's notes, else from the start
            where = [sec] if id(sec) in annexes else self.sections[:k + 1]
            at, offset = 0, 0
            for note in block:
                n = _FOOTNOTE.match(note[0])[1]
                # "Commission (1),", "No 2377/90(17)", "December 2004(1)", "[1]"; not
                # "Article 5(1)" (a paragraph)
                call = re.compile(rf"(?:(?<=[^\s(\[])[ \u00a0]|(?<=[^\s\d(\[])|(?<=/\d\d)|(?<=\d{{4}}))"
                                  rf"[(\[]{n}[)\]]")
                label = sec[0]
                for j in range(at, len(where)):
                    m = call.search("\n".join(where[j][1]), offset if j == at else 0)
                    if m:
                        label, at, offset = where[j][0], j, m.end()
                        break
                out.append({"section": label, "text": "\n".join(note)})
        return out

    def read(self) -> _Read:
        self._close(0)
        for d in reversed(self.unused):                 # where they stood, the last first
            sec, at = d.where
            sec[1][at:at] = d.lines
        notes = [{**note, "section": next((self.placed[x] for x in links if x in self.placed), stands)[0]}
                 for note, links, stands in self.notes]
        if self.footnotes and self.dialect == "words":
            notes += self._legacy_notes()
        for owner, sec in self.owners:
            owner.body = "\n".join(sec[1])
        out = [(label, "\n".join(body)) for label, body in self.sections if body or label != "Preamble"]
        return _Read(out, [o for o, _ in self.owners if isinstance(o, Article)],
                     [o for o, _ in self.owners if isinstance(o, Annex)],
                     [(s[0], "\n".join(s[1])) for s in self.recs], notes)


def _read(lines: list[_Line], *, recitals: bool = True, footnotes: bool = True) -> _Read:
    """The act's sections, articles, annexes, recitals and footnotes from its
    lines in reading order (module docstring: WHAT IS A HEADING).

    RECITALS (`recitals`): in the preamble, each numbered recital is a
    section of its own, labelled as formex `_recitals` labels it — the
    preamble's line before the first ("Whereas:") and the number: "Whereas
    (1)" — where the XHTML marks one (CONVEX's `rct_N`, a table row whose
    first cell is "(1)") or, in legacy HTML, a paragraph opens with the next
    number. What follows a recital outside its element ("HAVE ADOPTED THIS
    REGULATION:") is the preamble's again. FOOTNOTES go to the notes, each
    with the section its call stands in (the note's back link); the legacy
    HTML's (`footnotes`: not a PDF's, whose notes stand at each page's foot)
    by `_FOOTNOTE`, filed under their call."""
    lay = _Layout(_dialect(lines), recitals, footnotes)
    follows_note = False
    for i, line in enumerate(lines):
        if line.classes & _NOTE_CLS:
            lay.note(line, follows_note)
            follows_note = True
            continue
        follows_note = False
        if lay.division(line):
            lay.after(line)
            continue
        article, annex = lay.heads(line)
        if article:
            lay.article(line)
        elif not lay.subtitle(line, lines[i + 1].text if i + 1 < len(lines) else ""):
            if annex:
                lay.annex(line)
            elif not lay.final(line):
                lay.body(line)
        lay.after(line)
    return lay.read()


def _sections(lines: list) -> tuple[list[tuple[str, str]], list[Article], list[Annex]]:
    """`(sections, articles, annexes)` from lines in reading order — `_Line`s,
    or `(class, text)` pairs (tools/chunker_languages.py) — recitals left in
    the preamble, as a PDF's (`_read` reads the rest)."""
    r = _read([x if isinstance(x, _Line) else _Line(x[0], x[1]) for x in lines], recitals=False, footnotes=False)
    return r.sections, r.articles, r.annexes


def _act(root: str, body_file: str, lang: str, title: str, note, read: _Read,
         meta: dict, markup: dict) -> Act:
    act = Act(root=root, body_file=body_file, wrapper_file="", language=lang, title=title,
              date=note.date_document, publication_date=note.date_publication, consleg={},
              base_celex=None, recitals=read.recitals, articles=read.articles, annexes=read.annexes,
              meta=meta, notes=read.notes, references=[], modifications=[], instructions=[], wrapper=None,
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
    # A PDF'S RECITALS STAY UNSPLIT, in its "Preamble" section, and its
    # footnotes in the text: its lines are the pages' printed lines, a
    # recital wrapped over several and each page's footnotes ("1", "GU L 281
    # …", 32004D0411 it) among them, and no element bounds either — a
    # recital cut there could end inside a footnote or another recital.
    # Articles, annexes, divisions and the final formula are read by their
    # words, as the legacy HTML's.
    read = _read(lines, recitals=False, footnotes=False)
    title = note.expression_title or ""
    return _act(root, names[0], lang, title, note, read, {"pdf": info, "form": "pdf"}, {}), blank
