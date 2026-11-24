"""ProofreadPage's markup: a scan page's parts and its proofreading level,
and the `<pages index=… from=… to=…/>` transclusion that builds a text from
scan pages (docs/notes/wikisource-design.md §1, §3).

Standard used: ProofreadPage's own definitions (mediawiki.org/wiki/
Extension:Proofread_Page): a `proofread-page` revision is
`<noinclude>{header}</noinclude>{body}<noinclude>{footer}</noinclude>`, the
header opening with `<pagequality level="N" user="…" />`; levels 0 (without
text), 1 (not proofread), 2 (problematic), 3 (proofread), 4 (validated).
The `<pages>` tag takes `index`, `from`, `to`, `step`, `include`,
`exclude`, `fromsection`, `tosection` and `onlysection`; a section is marked
`<section begin="x" />…<section end="x" />` or, since 2022, `## x ##`.

What is not expanded is SAID, never guessed: an index whose pages are not
numbered (single image files), a page the dump does not hold, an attribute
this parser does not read. Each is an issue with the tag as written, and
the tag's text is not replaced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

_LEVEL = re.compile(r'<pagequality\s+level="(\d)"', re.I)
_PARTS = re.compile(r"\A<noinclude>(?P<header>.*?)</noinclude>(?P<body>.*?)(?:<noinclude>(?P<footer>.*)</noinclude>)?\s*\Z",
                    re.S)
_PAGES_TAG = re.compile(r"<pages\b(?P<attrs>[^>]*?)/?>(?:\s*</pages>)?", re.I)
_ATTR = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'/>]+))""")
#: what this parser reads of a <pages> tag; anything else is said
KNOWN = frozenset({"index", "from", "to", "step", "include", "exclude", "fromsection", "tosection",
                   "onlysection", "header"})


@dataclass(frozen=True)
class ScanPage:
    level: int | None
    header: str
    body: str
    footer: str


def parts(text: str) -> ScanPage:
    """A `proofread-page` revision's header, body, footer and level. A text
    without the noinclude frame is all body, its level None."""
    m = _PARTS.match(text)
    if not m:
        return ScanPage(None, "", text, "")
    lv = _LEVEL.search(m.group("header"))
    return ScanPage(int(lv.group(1)) if lv else None, m.group("header"), m.group("body"),
                    m.group("footer") or "")


@dataclass(frozen=True)
class PagesTag:
    raw: str
    attrs: dict[str, str]

    @property
    def index(self) -> str:
        return self.attrs.get("index", "").strip()


def tags(text: str) -> list[PagesTag]:
    """Every `<pages …/>` tag of a text, in order."""
    out = []
    for m in _PAGES_TAG.finditer(text):
        attrs = {a.lower(): (b if b is not None and b != "" else c if c else d)
                 for a, b, c, d in _ATTR.findall(m.group("attrs"))}
        out.append(PagesTag(m.group(0), attrs))
    return out


def _ints(spec: str) -> list[int]:
    """"1-3,5" → [1, 2, 3, 5]; refused (ValueError) when not that shape."""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
            out.extend(range(a, b + 1))
        else:
            out.append(int(part))
    return out


def numbers(tag: PagesTag, last: int | None = None) -> list[int]:
    """The scan page numbers a tag reads, in reading order: from..to by step,
    then include, less exclude — ProofreadPage's own order (sorted, unique).
    A tag without `to` reads to the index's last page, `last` (the highest
    page of that index the dump holds). ValueError when the numbers are not
    numbers (an index of image files), or `to` is missing and `last` unknown."""
    a = tag.attrs
    got: set[int] = set()
    if "from" in a or "to" in a:
        lo = int(a.get("from") or 1)
        if "to" not in a and last is None:
            raise ValueError("`to` is not given and the index's last page is not known")
        hi = int(a["to"]) if "to" in a else int(last)
        step = int(a.get("step") or 1)
        got.update(range(lo, hi + 1, max(step, 1)))
    if "include" in a:
        got.update(_ints(a["include"]))
    if "exclude" in a:
        got.difference_update(_ints(a["exclude"]))
    return sorted(got)


_SEC_BEGIN = re.compile(r'<section\s+begin\s*=\s*"?([^"/>]+?)"?\s*/>', re.I)
_SEC_END = re.compile(r'<section\s+end\s*=\s*"?([^"/>]+?)"?\s*/>', re.I)
_SEC_HASH = re.compile(r"^##\s*(.+?)\s*##\s*$", re.M)


def _sections(body: str) -> dict[str, tuple[int, int]]:
    """`{name: (start, end)}` of a scan page body's sections."""
    out: dict[str, tuple[int, int]] = {}
    for m in _SEC_BEGIN.finditer(body):
        e = _SEC_END.search(body, m.end())
        out[m.group(1).strip()] = (m.end(), e.start() if e and e.group(1).strip() == m.group(1).strip()
                                   else len(body))
    marks = list(_SEC_HASH.finditer(body))
    for i, m in enumerate(marks):
        out.setdefault(m.group(1).strip(), (m.end(), marks[i + 1].start() if i + 1 < len(marks) else len(body)))
    return out


def _cut(body: str, *, only: str | None = None, start: str | None = None, end: str | None = None) -> str:
    secs = _sections(body)
    if only:
        s = secs.get(only)
        return body[s[0]:s[1]] if s else ""
    lo = secs[start][0] if start and start in secs else 0
    hi = secs[end][1] if end and end in secs else len(body)
    return body[lo:hi]


def _clean(body: str) -> str:
    body = _SEC_BEGIN.sub("", body)
    body = _SEC_END.sub("", body)
    return _SEC_HASH.sub("", body)


@dataclass
class Expanded:
    text: str
    #: the scan pages read, in reading order: (title, level)
    used: list[tuple[str, int | None]] = field(default_factory=list)
    #: what was not expanded, with the tag as written
    issues: list[tuple[str, str]] = field(default_factory=list)
    indexes: list[str] = field(default_factory=list)


def expand(text: str, page_ns: str, lookup: Callable[[str], str | None],
           last: Callable[[str], int | None] = lambda _index: None) -> Expanded:
    """`text` with each `<pages/>` tag replaced by the bodies of the scan
    pages it names, joined by a newline: `lookup(title)` gives a scan page's
    revision text, or None where the dump does not hold it; `last(index)`
    the highest page number of that index the dump holds. `page_ns` is the
    wiki's own name of its scan-page namespace ("Pagina", "Page")."""
    out = Expanded("")
    pieces, pos = [], 0
    for m in _PAGES_TAG.finditer(text):
        tag = next(t for t in tags(m.group(0)))
        pieces.append(text[pos:m.start()])
        pos = m.end()
        unknown = sorted(set(tag.attrs) - KNOWN)
        if unknown:
            out.issues.append(("pages-attribute-unread", f"{', '.join(unknown)} in {tag.raw}"))
        if not tag.index:
            out.issues.append(("pages-without-index", tag.raw))
            pieces.append(tag.raw)
            continue
        try:
            nums = numbers(tag, last(tag.index))
        except ValueError as e:
            out.issues.append(("pages-not-numbered", f"{tag.raw}: {e}"))
            pieces.append(tag.raw)
            continue
        if tag.index not in out.indexes:
            out.indexes.append(tag.index)
        bodies = []
        for i, n in enumerate(nums):
            title = f"{page_ns}:{tag.index}/{n}"
            raw = lookup(title)
            if raw is None:
                out.issues.append(("page-missing", title))
                continue
            sp = parts(raw)
            body = sp.body
            if tag.attrs.get("onlysection"):
                body = _cut(body, only=tag.attrs["onlysection"])
            else:
                body = _cut(body, start=tag.attrs.get("fromsection") if i == 0 else None,
                            end=tag.attrs.get("tosection") if i == len(nums) - 1 else None)
            bodies.append(_clean(body).strip("\n"))
            out.used.append((title, sp.level))
        pieces.append("\n".join(bodies))
    pieces.append(text[pos:])
    out.text = "".join(pieces)
    return out
