"""The CDM notice — Cellar's metadata of one work, in the XML Cellar serves
for `Accept: application/xml;notice=branch` (the Publications Office's
"current" notice format; op-cellar-git/eurlex-xml-format documents the
proposed successor, which this reader does not need).

A BRANCH notice holds one WORK, its expression in the requested language
(`EXPRESSION display="true"`, labels decoded in that language — `NOTICE
decoding="eng"`), the other expressions it names, and their manifestations.
Every element is one of four shapes, by its `type` attribute: `data` (a
`VALUE`), `date` (`VALUE`, `YEAR`, `MONTH`, `DAY`), `concept` (a `URI`,
`OP-CODE`, `IDENTIFIER`, `PREFLABEL`) and `link` (another work: its `URI`
and the `SAMEAS` aliases — `celex`, `eli`, `oj`, `cellar`).

The record keeps the notice WHOLE, as a tree (`formex.tree`: attributes,
text, children in the origin's order), because everything in it is the
origin's assertion about the act (invariant 11) — and reads out of it the
fields the plugin works with: identity (CELEX, ELI, sector, type, year,
number), the dates (of the document, of entry into force, the stated END
OF VALIDITY), the force status, the work a consolidation is based on, the
display expression's language and title, the subjects and authors, and the
links to other works by their CELEX (`relations`).

The element names read here are the CDM properties' local names in upper
case (`RESOURCE_LEGAL_ID_CELEX` is `cdm:resource_legal_id_celex`). The
names of the link properties for amendment, repeal and correction are
[S] (legal-fr-de-eu.md §4.4: "read on the probe") — an element of another
name is still kept in the tree, it only makes no edge.

Comments are dropped: the samples' "edited with XMLSpy" line is an editor's
banner, not the Office's assertion (invariant 11 (b)).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from .formex import norm, tree


class NoticeError(ValueError):
    """Not a CDM notice."""


#: the 24 official languages: ISO 639-1 (what Formex's LG.DOC writes) →
#: the Publications Office's authority code (what the notice's LANG and
#: the content negotiation's Accept-Language use)
LANGUAGES: dict[str, str] = {
    "bg": "BUL", "cs": "CES", "da": "DAN", "de": "DEU", "el": "ELL", "en": "ENG", "es": "SPA",
    "et": "EST", "fi": "FIN", "fr": "FRA", "ga": "GLE", "hr": "HRV", "hu": "HUN", "it": "ITA",
    "lt": "LIT", "lv": "LAV", "mt": "MLT", "nl": "NLD", "pl": "POL", "pt": "POR", "ro": "RON",
    "sk": "SLK", "sl": "SLV", "sv": "SWE",
}
CODE_OF = {v: k for k, v in LANGUAGES.items()}

_CELEX = re.compile(r"^[0-9A-Z][0-9]{4}[A-Z]{1,2}[0-9A-Z()_-]+$")


@dataclass
class Notice:
    kind: str                       # branch | object | tree | index
    decoding: str | None            # the language labels are decoded in (eng)
    celex: str | None
    eli: str | None                 # as the notice states it (data.europa.eu)
    sector: str | None
    resource_type: str | None       # RESOURCE_LEGAL_TYPE: R, L, D, …
    year: str | None
    number: str | None              # RESOURCE_LEGAL_NUMBER_NATURAL_CELEX
    date_document: str | None
    entry_into_force: list[str]
    end_of_validity: list[str]
    in_force: bool | None
    base_celex: str | None          # ACT_CONSOLIDATED_BASED_ON_RESOURCE_LEGAL
    display_language: str | None    # the display expression's LANG (ENG)
    expression_title: str | None
    last_modified: str | None
    subjects: list[str]
    creators: list[str]
    relations: list[tuple[str, str]] = field(default_factory=list)   # (TAG, celex), in order
    items: dict[str, list["Item"]] = field(default_factory=dict)      # manifestation type → its items
    whole: Any = None               # the notice, whole (formex.tree)
    #: the work's own DATE_PUBLICATION (an act without a Formex wrapper is
    #: dated by it: its OJ day, as a wrapper's PUBLICATION.REF dates one)
    date_publication: str | None = None


@dataclass(frozen=True)
class Item:
    """One file of a manifestation, as the notice lists it (`TECHMD`)."""
    uri: str                        # …/<cellar id>.<expr>.<manif>/DOC_<n>
    name: str | None                # STREAM_NAME: the file's own name
    size: int | None                # STREAM_SIZE
    order: int | None               # STREAM_ORDER


def _parse(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise NoticeError(f"the notice is not XML: {e}") from None


def _values(parent: ET.Element | None, tag: str) -> list[str]:
    if parent is None:
        return []
    out = []
    for e in parent.findall(tag):
        v = e.findtext("VALUE")
        if v is not None and norm(v):
            out.append(norm(v))
    return out


def _first(parent: ET.Element | None, tag: str) -> str | None:
    got = _values(parent, tag)
    return got[0] if got else None


def celex_of_link(e: ET.Element) -> list[str]:
    """The CELEX numbers a link element names: its own URI or a SAMEAS of
    TYPE celex (a language's `….ENG` suffix is the expression, not the work)."""
    out = []
    for uri in [e.find("URI")] + [s.find("URI") for s in e.findall("SAMEAS")]:
        if uri is None or norm(uri.findtext("TYPE")) != "celex":
            continue
        ident = norm(uri.findtext("IDENTIFIER"))
        if ident and "." not in ident and ident not in out:
            out.append(ident)
    return out


def eli_of_link(e: ET.Element) -> str | None:
    for uri in [e.find("URI")] + [s.find("URI") for s in e.findall("SAMEAS")]:
        if uri is not None and norm(uri.findtext("TYPE")) == "eli":
            v = norm(uri.findtext("VALUE"))
            if v:
                return v
    return None


def _labels(work: ET.Element, marker: str) -> list[str]:
    out = []
    for e in work:
        if isinstance(e.tag, str) and marker in e.tag:
            for p in e.iter("PREFLABEL"):
                text = norm(p.text)
                if text and text not in out:
                    out.append(text)
    return out


def parse(data: bytes) -> Notice:
    root = _parse(data)
    if root.tag != "NOTICE":
        raise NoticeError(f"the notice's root is {root.tag}, not NOTICE")
    work = root.find("WORK")
    if work is None:
        raise NoticeError("the notice holds no WORK")
    celex = _first(work, "RESOURCE_LEGAL_ID_CELEX")
    if celex is None:
        got = celex_of_link(work)
        celex = got[0] if got else None
    eli = _first(work, "ELI") or eli_of_link(work)
    in_force_text = (_first(work, "RESOURCE_LEGAL_IN-FORCE") or "").lower()
    in_force = {"true": True, "false": False, "1": True, "0": False}.get(in_force_text)
    base = None
    based = work.find("ACT_CONSOLIDATED_BASED_ON_RESOURCE_LEGAL")
    if based is not None:
        got = celex_of_link(based)
        base = got[0] if got else None
    # the branch's own expression: NOTICE/EXPRESSION as Cellar serves it
    # (52018PC0340_branch.xml); inside WORK, `display="true"`, in the
    # Office's hand-edited samples (EN-CELEX-02009R1223-20191218.xml)
    ex = root.find("EXPRESSION")
    if ex is None:
        ex = next((e for e in work.iter("EXPRESSION") if e.attrib.get("display") == "true"), None)
    display = title = None
    if ex is not None:
        display = _language(ex)
        title = _first(ex, "EXPRESSION_TITLE")
    relations: list[tuple[str, str]] = []
    for e in work:
        if not isinstance(e.tag, str) or e.attrib.get("type") != "link":
            continue
        for c in celex_of_link(e):
            if (e.tag, c) not in relations:
                relations.append((e.tag, c))
    return Notice(
        kind=root.attrib.get("type", ""), decoding=root.attrib.get("decoding"),
        celex=celex, eli=eli, sector=_first(work, "ID_SECTOR"),
        resource_type=_first(work, "RESOURCE_LEGAL_TYPE"), year=_first(work, "RESOURCE_LEGAL_YEAR"),
        number=_first(work, "RESOURCE_LEGAL_NUMBER_NATURAL_CELEX"),
        date_document=_first(work, "WORK_DATE_DOCUMENT"),
        entry_into_force=sorted(set(_values(work, "RESOURCE_LEGAL_DATE_ENTRY-INTO-FORCE"))),
        end_of_validity=sorted(set(_values(work, "RESOURCE_LEGAL_DATE_END-OF-VALIDITY"))),
        in_force=in_force, base_celex=base, display_language=display, expression_title=title,
        last_modified=_first(work, "LASTMODIFICATIONDATE"),
        date_publication=_first(work, "DATE_PUBLICATION"),
        subjects=_labels(work, "IS_ABOUT"), creators=_labels(work, "CREATED_BY_AGENT"),
        relations=relations, items=_items(root, work), whole=tree(root))


def _language(ex: ET.Element) -> str | None:
    """The expression's language as the authority code (ENG): its
    EXPRESSION_USES_LANGUAGE, else a LANG value of three letters (the
    hand-edited samples write `<LANG>ENG</LANG>`)."""
    uses = ex.find("EXPRESSION_USES_LANGUAGE")
    if uses is not None:
        code = norm(uses.findtext("IDENTIFIER") or uses.findtext("OP-CODE"))
        if code:
            return code.upper()
    for lang in ex.findall("LANG"):
        for text in [lang.text, lang.findtext("VALUE")]:
            t = norm(text)
            if len(t) == 3 and t.isalpha():
                return t.upper()
    return None


def _items(root: ET.Element, work: ET.Element) -> dict[str, list[Item]]:
    """Each manifestation's items, by its type (`fmx4`, `xhtml`, `pdf`), in
    the order the notice lists them — the names are what a fetch keeps."""
    out: dict[str, list[Item]] = {}
    manifestations = list(root.findall("MANIFESTATION")) or [
        m for ex in work.iter("EXPRESSION") if ex.attrib.get("display") == "true"
        for m in ex.findall("MANIFESTATION")]
    for m in manifestations:
        kind = m.attrib.get("manifestation-type") or _first(m, "MANIFESTATION_TYPE") \
            or _first(m, "TYPE") or ""
        items = out.setdefault(kind, [])
        for it in m.findall("MANIFESTATION_HAS_ITEM"):
            uri = norm(it.findtext("./URI/VALUE"))
            if not uri:
                continue
            tech = it.find("TECHMD")
            size = _first(tech, "STREAM_SIZE")
            order = _first(tech, "STREAM_ORDER")
            items.append(Item(uri=uri, name=_first(tech, "STREAM_NAME"),
                              size=int(size) if size and size.isdigit() else None,
                              order=int(order) if order and order.isdigit() else None))
    return out


def is_celex(text: str) -> bool:
    return bool(_CELEX.match(text or ""))


def work_day(data: bytes) -> str | None:
    """The work's own day, read only as far as it takes: its DATE_PUBLICATION,
    else its WORK_DATE_DOCUMENT, each a direct child of NOTICE/WORK. Discovery
    dates a unit without a Formex wrapper by it, and a 1 MB notice is never
    parsed whole for one date (every discovery pass reads every unit)."""
    import io
    path: list[str] = []
    found: dict[str, str] = {}
    try:
        for event, el in ET.iterparse(io.BytesIO(data), events=("start", "end")):
            if event == "start":
                path.append(el.tag)
                continue
            if len(path) == 3 and path[:2] == ["NOTICE", "WORK"] and el.tag in ("DATE_PUBLICATION",
                                                                               "WORK_DATE_DOCUMENT"):
                v = norm(el.findtext("VALUE") or "")
                if v:
                    found.setdefault(el.tag, v)
                if "DATE_PUBLICATION" in found:
                    break
            if len(path) == 2 and el.tag == "WORK":
                break
            path.pop()
            if len(path) > 2:
                continue
            el.clear()
    except ET.ParseError:
        return None
    return found.get("DATE_PUBLICATION") or found.get("WORK_DATE_DOCUMENT")
