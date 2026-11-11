"""The Ask page's "save pdf" (operator, 2026-11-11: "Next to 'read aloud' I
would like to have a 'save pdf', with the query, the answer and all the
references with the link to the original source. Page format portrait A4
but configurable in the settings to portrait letter"; layout proposed with
two sample files the same day; how it is made, choice A: the server makes
the file).

WHAT GOES IN IS WHAT THE READER SAW. The page sends the question as asked,
the answer as shown (in the language it is shown in) and, per entry, the
title and passage as shown with the published ones beside them where the
page translated them (`AskPdf`). The node does not search again: a second
search could rank differently, and a PDF that disagrees with the page it was
saved from is worse than none.

THE STANDARD (CLAUDE.md, "Do not reinvent the wheel"): HTML laid out by CSS
Paged Media (W3C: `@page`, its margin boxes, `counter(pages)`,
`string-set`), rendered by WeasyPrint (BSD-3, Python), which implements it
and writes PDF links. The standard alternative is the browser's own print to
PDF from the same HTML and CSS (the operator's choice B, not taken: a print
dialog, and page numbers that differ per browser); the template here would
serve it unchanged.

NOTHING IS FETCHED. The template draws no image and loads no stylesheet; the
fonts are the system's (fontconfig). WeasyPrint's url_fetcher (`_fetcher`)
refuses every URL, so neither a link in an answer nor a crafted request
makes the node fetch anything (invariant 5: no bytes move outside the
budget). Links are written into the PDF, never followed.

EVERY CHARACTER IS TEXT until a rule makes it markup: Jinja autoescapes the
template, and the answer's markdown is the Ask page's own subset (ask.html
`md`), ported here with the same escape-first order. Only http(s) links are
written; the node's reader link is built here from its parts.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jinja2
from markupsafe import Markup, escape
from pydantic import BaseModel, Field

#: the paper sizes the Settings offer, as CSS `@page size` names them; A4 is
#: the default (operator, 2026-11-11)
PAPER: dict[str, str] = {"a4": "A4", "letter": "letter"}
#: the margins each paper is laid out with: 20 mm on A4, 0.75 in on Letter
#: (the US convention; the text block is then the same 170-ish mm wide)
MARGIN: dict[str, str] = {"a4": "18mm 20mm 20mm 20mm", "letter": "0.7in 0.75in 0.8in 0.75in"}

#: what a request may carry — the page's own limits with room to spare
#: (results ≤ retrieval.MAX_RESULTS): a larger request is refused, not cut
MAX_REFERENCES = 200
MAX_QUESTION = 8_000
MAX_ANSWER = 200_000
MAX_PASSAGE = 40_000
MAX_SHORT = 2_000

#: PDFs laid out at once. Layout is CPU work on the web process's threads;
#: a burst waits its turn, bounded (`render` gives up after WAIT_S), rather
#: than taking every core from the search beside it
CONCURRENT = 2
WAIT_S = 30.0
_slots = threading.BoundedSemaphore(CONCURRENT)

#: right-to-left scripts' languages, for the passage's `dir` (WeasyPrint lays
#: out `dir="rtl"`; it does not guess from the text as a browser's "auto")
RTL = frozenset({"ar", "fa", "he", "ur", "ps", "sd", "ug", "yi", "dv", "ckb"})

_TEMPLATES = Path(__file__).parent / "templates"
_ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_TEMPLATES)),
                          autoescape=jinja2.select_autoescape(["html"]),
                          undefined=jinja2.StrictUndefined)


#: one short string of a list (a collection's name, what moved an entry)
Short = Annotated[str, Field(max_length=MAX_SHORT)]


class Busy(RuntimeError):
    """Every slot stayed taken for WAIT_S: the caller answers 503."""


class Unavailable(RuntimeError):
    """This image has no WeasyPrint (or its Pango): the caller answers 503
    and says what supplies it."""


class PdfReference(BaseModel):
    """One entry of the page's list, as the reader saw it. `n` is its [n]."""
    n: int = Field(ge=1, le=MAX_REFERENCES)
    title: str = Field(default="", max_length=MAX_SHORT)
    #: the published title, where the page shows a translation of it
    title_original: str | None = Field(default=None, max_length=MAX_SHORT)
    lang: str | None = Field(default=None, max_length=32)
    collection: str | None = Field(default=None, max_length=256)
    authors: str | None = Field(default=None, max_length=MAX_SHORT)
    chapter: str | None = Field(default=None, max_length=MAX_SHORT)
    passage: str = Field(default="", max_length=MAX_PASSAGE)
    #: the language the shown passage was translated INTO; None: as published
    translated_to: str | None = Field(default=None, max_length=32)
    origin_url: str | None = Field(default=None, max_length=4096)
    #: the node's reader for this entry: /read/<collection>/<book>?hl=<chunk>
    book: str | None = Field(default=None, max_length=256)
    chunk: str | None = Field(default=None, max_length=512)
    #: what a weight did to its place (invariant 4, rule 2), as the page says it
    moved_by: list[Short] = Field(default_factory=list, max_length=8)


class AskPdf(BaseModel):
    """What the page sends for one PDF (`POST /ask/pdf`)."""
    paper: Literal["a4", "letter"] = "a4"
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    answer: str = Field(default="", max_length=MAX_ANSWER)
    #: the language the answer is shown in; None: the model's own
    answer_lang: str | None = Field(default=None, max_length=32)
    #: the page's settings for this question, said in the header line
    mode: str | None = Field(default=None, max_length=32)
    results: int | None = Field(default=None, ge=0, le=10_000)
    reranked_from: int | None = Field(default=None, ge=0, le=100_000)
    collections: list[Short] = Field(default_factory=list, max_length=200)
    #: the reader's time zone (IANA), so the date is theirs
    tz: str | None = Field(default=None, max_length=64)
    references: list[PdfReference] = Field(default_factory=list, max_length=MAX_REFERENCES)


# ---- the answer's markdown: the Ask page's subset, escape-first ----------

_CODE = "\x00"
_ESCD = "\x01"


def _inline(raw: str, refs: frozenset[int]) -> str:
    """One line of markdown as HTML (ask.html `mdInline`), its [n] linked to
    reference n where the list has one. Escaped first: nothing the model
    wrote becomes markup unless a rule below makes it so."""
    codes: list[str] = []

    def keep_code(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"{_CODE}{len(codes) - 1}{_CODE}"
    s = re.sub(r"`([^`\n]+)`", keep_code, raw)
    s = re.sub(r"\\([*_`#~\[\]|>\\-])", lambda m: f"{_ESCD}{ord(m.group(1))}{_ESCD}", s)
    s = str(escape(s))
    s = re.sub(r"\[([^\[\]\n]{1,200})\]\(((?:https?://|mailto:)[^\s()<>]+)\)",
               lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = re.sub(r"(\*\*|__)(?=\S)([^\n]+?)(?<=\S)\1", r"<strong>\2</strong>", s)
    s = re.sub(r"~~(?=\S)([^\n]+?)(?<=\S)~~", r"<del>\1</del>", s)
    s = re.sub(r"(^|[^\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![\w*])", r"\1<em>\2</em>", s)
    s = re.sub(r"(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)", r"\1<em>\2</em>", s)
    s = _cite(s, refs)
    s = re.sub(f"{_ESCD}(\\d+){_ESCD}", lambda m: str(escape(chr(int(m.group(1))))), s)
    return re.sub(f"{_CODE}(\\d+){_CODE}", lambda m: f"<code>{escape(codes[int(m.group(1))])}</code>", s)


def _cite(html: str, refs: frozenset[int]) -> str:
    """[n] outside a link, as a link to reference n (ask.html
    `linkCitations`); a number the list does not have stays text. A run of
    them and the punctuation after it is one unbreakable span: "[1][3]."
    never splits across two lines."""
    def one(m: re.Match) -> str:
        n = m.group(1)
        return f'<a class="cite" href="#ref-{n}">[{n}]</a>' if int(n) in refs else m.group(0)

    def run(m: re.Match) -> str:
        return f'<span class="cites">{re.sub(r"\[(\d{1,4})\]", one, m.group(1))}{m.group(2)}</span>'
    parts = re.split(r"(<a [^>]*>.*?</a>)", html)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"((?:\[\d{1,4}\])+)([.,;:!?)]?)", run, parts[i])
    return "".join(parts)


def css_string(text: str) -> Markup:
    """`text` as a CSS string's contents, for the page's margin box: quotes
    and backslashes escaped, `<` as an escape so nothing closes the style
    element, control characters dropped."""
    out = []
    for ch in text:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ch == "<":
            out.append("\\3C ")
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(" ")
        else:
            out.append(ch)
    return Markup("".join(out))


def markdown_html(src: str, refs: frozenset[int] = frozenset()) -> Markup:
    """The answer as HTML: ask.html `md`'s blocks — fenced code, headings,
    rules, pipe tables, quotes, lists, paragraphs with line breaks — and
    `_inline`'s spans. THE STANDARD ALTERNATIVE is a CommonMark parser
    (markdown-it-py) with a sanitiser, the page's own note says when."""
    lines = str(src or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    quote_: list[str] = []
    lst: dict | None = None

    def inl(t: str) -> str:
        return _inline(t, refs)

    def cells(row: str) -> list[str]:
        row = re.sub(r"^\|", "", row.strip())
        row = re.sub(r"\|$", "", row)
        return [inl(c.strip()) for c in row.split("|")]

    def flush() -> None:
        nonlocal para, quote_, lst
        if para:
            out.append("<p>" + "<br>".join(inl(p) for p in para) + "</p>")
            para = []
        if lst:
            start = f' start="{lst["start"]}"' if lst["tag"] == "ol" and lst["start"] > 1 else ""
            out.append(f'<{lst["tag"]}{start}>' + "".join(f"<li>{inl(i)}</li>" for i in lst["items"])
                       + f'</{lst["tag"]}>')
            lst = None
        if quote_:
            out.append("<blockquote>" + "<br>".join(inl(q) for q in quote_) + "</blockquote>")
            quote_ = []

    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s*(```|~~~)", line)
        if m:
            flush()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(m.group(1)):
                code.append(lines[i])
                i += 1
            out.append(f"<pre><code>{escape(chr(10).join(code))}</code></pre>")
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$", line)
        if m:
            flush()
            n = min(6, len(m.group(1)) + 2)
            out.append(f"<h{n}>{inl(m.group(2))}</h{n}>")
            i += 1
            continue
        if re.match(r"^\s*([-*_])(?:\s*\1){2,}\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if (re.match(r"^\s*\|.*\|\s*$", line) and i + 1 < len(lines)
                and re.match(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$", lines[i + 1])):
            flush()
            h = "<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in cells(line)) + "</tr></thead><tbody>"
            i += 2
            while i < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                h += "<tr>" + "".join(f"<td>{c}</td>" for c in cells(lines[i])) + "</tr>"
                i += 1
            out.append(h + "</tbody></table>")
            continue
        m = re.match(r"^\s{0,3}>\s?(.*)$", line)
        if m:
            if para or lst:
                flush()
            quote_.append(m.group(1))
            i += 1
            continue
        m = re.match(r"^\s*[-*+•]\s+(.*)$", line)
        if m:
            if para or quote_ or (lst and lst["tag"] != "ul"):
                flush()
            lst = lst or {"tag": "ul", "items": [], "start": 1}
            lst["items"].append(m.group(1))
            i += 1
            continue
        m = re.match(r"^\s*(\d{1,4})[.)]\s+(.*)$", line)
        if m:
            if para or quote_ or (lst and lst["tag"] != "ol"):
                flush()
            lst = lst or {"tag": "ol", "items": [], "start": int(m.group(1))}
            lst["items"].append(m.group(2))
            i += 1
            continue
        if lst and re.match(r"^\s{2,}\S", line):
            lst["items"][-1] += " " + line.strip()
            i += 1
            continue
        if lst or quote_:
            flush()
        para.append(line.strip())
        i += 1
    flush()
    return Markup("".join(out))


def cited(answer: str) -> set[int]:
    """The [n] the answer cites, outside its code."""
    text = re.sub(r"```.*?(```|$)", " ", answer or "", flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)
    return {int(n) for n in re.findall(r"\[(\d{1,4})\]", text)}


# ---- links, names, the date ----------------------------------------------

def safe_url(url: str | None) -> str | None:
    """An origin link as written into the PDF: http(s) with a host, one
    line, else None (a javascript: or file: link is never written)."""
    u = (url or "").strip()
    if not u or any(c in u for c in "\r\n\t\x00 ") or len(u) > 4096:
        return None
    parts = urlsplit(u)
    return u if parts.scheme in ("http", "https") and parts.netloc else None


def reader_url(base: str, collection: str | None, book: str | None, chunk: str | None) -> str | None:
    """The node's reader at this entry's passage, built from its parts (as
    ask.html builds it): `<base>/read/<collection>/<book>?hl=<chunk>`."""
    if not (base and collection and book):
        return None
    url = f"{base.rstrip('/')}/read/{quote(collection, safe='')}/{quote(book, safe='')}"
    return url + (f"?hl={quote(chunk, safe='')}" if chunk else "")


def direction(lang: str | None) -> str:
    return "rtl" if (lang or "").split("-")[0].lower() in RTL else "ltr"


def language_name(code: str | None) -> str:
    """'it' → 'Italian', for the tags and the "translated from" note; the
    code itself where the table has no name."""
    from . import llm, translate
    c = (code or "").split("-")[0].lower()
    return translate.english_name(c) or llm.LANGUAGE_NAMES.get(c) or code or "?"


def slug(question: str, limit: int = 48) -> str:
    """The question as a file name's words: ASCII, lowercase, dashes;
    'answer' where nothing Latin is left (a question in Chinese)."""
    ascii_ = unicodedata.normalize("NFKD", question).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_.lower())
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > limit:
            break
        out = f"{out}-{w}" if out else w
    return out or "answer"


def reader_time(tz: str | None, now: datetime | None = None) -> datetime:
    """Now, in the reader's time zone; UTC where the page gave none or one
    the system does not know."""
    now = now or datetime.now(timezone.utc)
    try:
        return now.astimezone(ZoneInfo(tz)) if tz else now.astimezone(timezone.utc)
    except (ZoneInfoNotFoundError, ValueError):
        return now.astimezone(timezone.utc)


def filename(question: str, when: datetime) -> str:
    return f"louise-{when:%Y-%m-%d}-{slug(question)}.pdf"


# ---- the document ----------------------------------------------------------

def build_html(doc: AskPdf, *, base_url: str, node: str, when: datetime,
               site: str = "Project Louise") -> str:
    """The PDF's HTML (templates/pdf/answer.html): what `render` lays out,
    and what a test reads."""
    numbers = frozenset(r.n for r in doc.references)
    cites = cited(doc.answer)
    refs = []
    for r in sorted(doc.references, key=lambda r: r.n):
        shown_title = r.title.strip() or (r.title_original or "").strip() or f"#{r.n}"
        orig = (r.title_original or "").strip()
        refs.append({
            "n": r.n, "title": shown_title,
            "title_original": orig if orig and orig != shown_title else None,
            "lang": r.lang, "lang_name": language_name(r.lang) if r.lang else None,
            "collection": r.collection, "authors": (r.authors or "").strip() or None,
            "chapter": (r.chapter or "").strip() or None,
            "passage": r.passage.strip(), "dir": direction(r.translated_to or r.lang),
            "translated_from": (language_name(r.lang) if r.translated_to and r.translated_to != r.lang
                                else None),
            "origin_url": safe_url(r.origin_url),
            "reader_url": reader_url(base_url, r.collection, r.book, r.chunk),
            "moved_by": [m.strip() for m in r.moved_by if m.strip()],
            "cited": r.n in cites,
        })
    return _ENV.get_template("pdf/answer.html").render(
        paper=PAPER[doc.paper], margin=MARGIN[doc.paper],
        footer=css_string(f"{site} · {_short(doc.question.strip(), 90)}"), site=site,
        question=doc.question.strip(), question_short=_short(doc.question.strip(), 90),
        answer=markdown_html(doc.answer, numbers), answer_lang=doc.answer_lang,
        answer_lang_name=language_name(doc.answer_lang) if doc.answer_lang else None,
        dir=direction(doc.answer_lang), mode=doc.mode, results=doc.results,
        reranked_from=doc.reranked_from, collections=doc.collections,
        node=node, when=when, refs=refs, total=len(refs),
        cited_count=sum(1 for r in refs if r["cited"]))


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _refuse(url: str, *args, **kwargs):
    """What the PDF's fetcher does with a URL: refuse it (module docstring)."""
    raise ValueError(f"the PDF fetches nothing: {url[:80]!r}")


def _fetcher(weasyprint):
    """WeasyPrint's url_fetcher (from 70, a `URLFetcher`): `fetch` refuses
    every URL through `_refuse`, and no protocol is allowed besides."""
    class Refuse(weasyprint.urls.URLFetcher):
        def fetch(self, url, headers=None):
            return _refuse(url)
    return Refuse(allowed_protocols=())


def render(html: str) -> bytes:
    """The PDF of `build_html`'s page. Raises Busy when every slot stayed
    taken for WAIT_S."""
    if not _slots.acquire(timeout=WAIT_S):
        raise Busy(f"{CONCURRENT} PDFs are being laid out; asked again in a moment it will be made")
    try:
        try:
            import weasyprint
        except (ImportError, OSError) as e:      # OSError: Pango's libraries missing
            raise Unavailable("this image has no PDF library (WeasyPrint, with Pango and the Noto "
                              f"fonts): the platform image from main has it — {type(e).__name__}: {e}") from None
        return weasyprint.HTML(string=html, url_fetcher=_fetcher(weasyprint)).write_pdf()
    finally:
        _slots.release()


# ---- the other interfaces (operator, 2026-11-11: "Since we produce it
# internally, make it available through the other interfaces too") --------
#
# The page sends what its reader saw. The command line (`ask pdf`), the API
# (`POST /api/ask/pdf`) and MCP (`louise_pdf`) have no page: the PDF is made
# from Louise's own answer (`from_chat`: `chat.ask`'s answer and the sources
# it numbered, with the passages it showed) or from a client's own answer and
# the hits it used (`from_given`) — the same document, the same render.

def _cut(text, n: int) -> str:
    return str(text or "")[:n]


def _reader_parts(chunk_id: str | None) -> tuple[str | None, str | None]:
    """(collection, book) of a chunk id `<collection>:<book>:…`, as the Ask
    page reads it for its reader link; a book that is not a row number has
    no reader link."""
    parts = str(chunk_id or "").split(":")
    if len(parts) > 1 and parts[0] and parts[1].isdigit():
        return parts[0], parts[1]
    return None, None


def _names() -> dict[str, str]:
    """collection_id -> the local name this node holds it under."""
    from app import collections as collections_mod
    out: dict[str, str] = {}
    for c in collections_mod.load_all():
        cid = getattr(getattr(c, "codex", None), "collection_id", None)
        if cid:
            out[cid] = c.name
    return out


def _reference(n: int, hit: dict, names: dict[str, str]) -> PdfReference:
    collection, book = _reader_parts(hit.get("chunk_id"))
    name = names.get(hit.get("collection_id") or "") or hit.get("collection") or collection
    return PdfReference(
        n=n, title=_cut(hit.get("title") or hit.get("uid"), MAX_SHORT),
        lang=_cut(hit.get("lang"), 32) or None, collection=_cut(name, 256) or None,
        authors=_cut(hit.get("authors"), MAX_SHORT) or None, chapter=_cut(hit.get("chapter"), MAX_SHORT) or None,
        passage=_cut(hit.get("passage") if hit.get("passage") is not None else hit.get("text"), MAX_PASSAGE),
        origin_url=_cut(hit.get("origin_url"), 4096) or None,
        book=book, chunk=_cut(hit.get("chunk_id"), 512) if book else None)


def from_chat(question: str, out: dict, *, paper: str = "a4", tz: str | None = None,
              collections: list[str] | None = None) -> AskPdf:
    """Louise's own answer (`chat.ask(..., passages=True)`) as the PDF's
    input: every source it numbered, in its numbering, with the passage it
    was shown."""
    names = _names()
    passages = out.get("passages") or {}
    refs = []
    for e in (out.get("sources") or [])[:MAX_REFERENCES]:
        p = passages.get(str(e.get("n"))) or {}
        refs.append(_reference(int(e["n"]), {**e, "text": p.get("text"), "chapter": p.get("chapter")}, names))
    return AskPdf(paper=paper, question=_cut(question, MAX_QUESTION), answer=_cut(out.get("answer"), MAX_ANSWER),
                  answer_lang=out.get("answer_lang"), collections=[_cut(c, MAX_SHORT) for c in collections or []],
                  tz=tz, references=refs)


def from_given(question: str, answer: str, hits: list[dict], *, paper: str = "a4",
               tz: str | None = None, answer_lang: str | None = None,
               collections: list[str] | None = None) -> AskPdf:
    """A client's own answer and the hits it used (as `louise_search` gave
    them, or as the page shows them): [n] is a hit's own `n`, else its place
    in the list."""
    names = _names()
    refs = []
    for i, h in enumerate((hits or [])[:MAX_REFERENCES], start=1):
        if not isinstance(h, dict):
            continue
        n = h.get("n") if isinstance(h.get("n"), int) and 1 <= h.get("n") <= MAX_REFERENCES else i
        refs.append(_reference(n, h, names))
    return AskPdf(paper=paper, question=_cut(question, MAX_QUESTION), answer=_cut(answer, MAX_ANSWER),
                  answer_lang=answer_lang, collections=[_cut(c, MAX_SHORT) for c in collections or []],
                  tz=tz, references=refs)


def node_base() -> str:
    """The node's own address for the "On <title>" links where no request
    names one (the command line, MCP over stdio): platform.toml [node]
    address; '' — no such link — where it is unset."""
    from app import platform_config
    try:
        return (platform_config.load().peers.address or "").rstrip("/")
    except Exception:   # noqa: BLE001 — a link left out, never a PDF refused
        return ""


def make(doc: AskPdf, *, base_url: str = "") -> tuple[str, bytes]:
    """(file name, PDF) for `doc`, as every interface makes it."""
    from urllib.parse import urlsplit
    from app import site_title
    when = reader_time(doc.tz)
    node = urlsplit(base_url).netloc if base_url else site_title.title()
    html = build_html(doc, base_url=base_url, node=node or base_url, when=when, site=site_title.title())
    return filename(doc.question, when), render(html)
