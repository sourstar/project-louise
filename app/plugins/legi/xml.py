"""LEGI's XML, read into plain structures (DILA's DTDs: Legilibre/dtd-legi
@ 2c64cbf, the four roots ARTICLE, TEXTE_VERSION, SECTION_TA, TEXTELR).

Two readers, kept apart on purpose:

- `index_fields(data)`: the few fields DISCOVERY needs (the root, the dates
  the id's day is taken from, an article's chain of versions), read from the
  bytes with regular expressions — microseconds a member, so the pack of a
  5.25 M-member baseline is not a full XML parse (app/plugins/legi/pack.py).
  The parse uses THIS function for the same fields, so the day discovery
  sorts an id under and the day the record states are one computation.
- `parse(data)`: the whole member, every element and attribute the origin
  wrote (invariant 11): the blocks the DTD names into named structures, any
  element the DTD does not name into `other`, generically, never dropped.

`render(elem)`: a CONTENU block (XHTML transitional plus DILA's tags) as
text: a paragraph, a line break, a list item, a table row each end a line,
cells joined by " | ". The markup is dropped (invariant 11 (a)); an image's
`src` and `alt` are kept beside the text by the caller.
"""
from __future__ import annotations

import html.entities
import re
from typing import Any
from xml.etree import ElementTree as ET

ROOTS = {"ARTICLE": "article", "TEXTE_VERSION": "texte_version", "SECTION_TA": "section_ta",
         "TEXTELR": "textelr"}

_ROOT = re.compile(rb"<(ARTICLE|TEXTE_VERSION|SECTION_TA|TEXTELR)[\s>/]")


def _first(pattern: bytes, data: bytes) -> str | None:
    m = re.search(pattern, data, re.S)
    if m is None:
        return None
    return m.group(1).decode("utf-8", "replace").strip() or None


def _block(data: bytes, tag: bytes) -> bytes:
    m = re.search(rb"<" + tag + rb"[\s>].*?</" + tag + rb">", data, re.S)
    return m.group(0) if m else b""


def index_fields(data: bytes) -> dict[str, Any]:
    """What discovery reads of a member: `kind` (None for a member whose root
    is none of the four), `date_debut` (an article's or a text version's own
    start), `date_texte` and `date_publi` (the text's), and for an article
    `chain`, the LEGIARTI ids its VERSIONS list (own id not added here)."""
    m = _ROOT.search(data[:4096])
    kind = ROOTS.get(m.group(1).decode()) if m else None
    out: dict[str, Any] = {"kind": kind, "date_debut": None, "date_texte": None,
                           "date_publi": None, "chain": []}
    if kind == "article":
        meta = _block(data, rb"META_ARTICLE")
        out["date_debut"] = _first(rb"<DATE_DEBUT>(.*?)</DATE_DEBUT>", meta)
        texte = re.search(rb"<TEXTE\s[^>]*>", _block(data, rb"CONTEXTE"))
        if texte:
            out["date_texte"] = _first(rb'\bdate_signature="([^"]*)"', texte.group(0))
            out["date_publi"] = _first(rb'\bdate_publi="([^"]*)"', texte.group(0))
        out["chain"] = sorted({i.decode() for i in re.findall(
            rb'<LIEN_ART\b[^>]*?\bid="(LEGIARTI\d+)"', _block(data, rb"VERSIONS"))})
    elif kind in ("texte_version", "textelr"):
        chron = _block(data, rb"META_TEXTE_CHRONICLE")
        out["date_texte"] = _first(rb"<DATE_TEXTE>(.*?)</DATE_TEXTE>", chron)
        out["date_publi"] = _first(rb"<DATE_PUBLI>(.*?)</DATE_PUBLI>", chron)
        if kind == "texte_version":
            out["date_debut"] = _first(rb"<DATE_DEBUT>(.*?)</DATE_DEBUT>",
                                       _block(data, rb"META_TEXTE_VERSION"))
    elif kind == "section_ta":
        texte = re.search(rb"<TEXTE\s[^>]*>", _block(data, rb"CONTEXTE"))
        if texte:
            out["date_texte"] = _first(rb'\bdate_signature="([^"]*)"', texte.group(0))
            out["date_publi"] = _first(rb'\bdate_publi="([^"]*)"', texte.group(0))
    return out


# ---- the whole member ---------------------------------------------------------------------

class LegiXmlError(ValueError):
    """A member that is not well-formed XML, even after the HTML entities."""


_ENTITY = re.compile(rb"&([A-Za-z][A-Za-z0-9]*);")
_XML_ENTITIES = {b"amp", b"lt", b"gt", b"quot", b"apos"}


def _html_entities(data: bytes) -> tuple[bytes, list[str]]:
    """`&nbsp;`-style HTML entities, which XML without its DTD does not
    define, replaced by their characters; the names replaced."""
    found: list[str] = []

    def sub(m: re.Match) -> bytes:
        name = m.group(1)
        if name in _XML_ENTITIES:
            return m.group(0)
        ch = html.entities.html5.get(name.decode() + ";")
        if ch is None:
            return m.group(0)
        found.append(name.decode())
        return ch.encode("utf-8")
    return _ENTITY.sub(sub, data), found


def root_of(data: bytes) -> tuple[ET.Element, list[str]]:
    """The member's root element and the HTML entity names that had to be
    replaced first (an Issue for the caller), or LegiXmlError."""
    try:
        return ET.fromstring(data), []
    except ET.ParseError as first:
        fixed, names = _html_entities(data)
        if not names:
            raise LegiXmlError(f"not well-formed XML: {first}") from None
        try:
            return ET.fromstring(fixed), sorted(set(names))
        except ET.ParseError as e:
            raise LegiXmlError(f"not well-formed XML: {e}") from None


def text_of(elem: ET.Element | None) -> str:
    """An element's whole text content, whitespace collapsed."""
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def attrs(elem: ET.Element) -> dict[str, Any]:
    """Every attribute, and the text where there is any (`text`)."""
    out: dict[str, Any] = dict(elem.attrib)
    t = text_of(elem)
    if t:
        out["text"] = t
    return out


def tree(elem: ET.Element) -> dict[str, Any]:
    """Any element, generically and whole: `{tag, attrs?, text?, children?}`
    — for what the DTD does not name, so nothing is dropped unread."""
    out: dict[str, Any] = {"tag": elem.tag}
    if elem.attrib:
        out["attrs"] = dict(elem.attrib)
    t = (elem.text or "").strip()
    if t:
        out["text"] = " ".join(t.split())
    kids = [tree(c) for c in elem]
    if kids:
        out["children"] = kids
    tail = (elem.tail or "").strip()
    if tail:
        out["tail"] = " ".join(tail.split())
    return out


def fields(elem: ET.Element | None) -> dict[str, Any]:
    """A META block as `{child tag: text}`, every child kept: a child with
    children of its own as its `tree`, a tag met twice as a list."""
    out: dict[str, Any] = {}
    repeated: set[str] = set()
    if elem is None:
        return out
    for c in elem:
        value: Any = tree(c) if len(c) else text_of(c)
        if c.attrib and not len(c):
            value = {"attrs": dict(c.attrib), "text": text_of(c)}
        if c.tag in repeated:
            out[c.tag].append(value)
        elif c.tag in out:
            out[c.tag] = [out[c.tag], value]
            repeated.add(c.tag)
        else:
            out[c.tag] = value
    return out


def contexte(elem: ET.Element | None) -> dict[str, Any]:
    """CONTEXTE: the parent text's attributes, its titles in each version
    (TITRE_TXT), and the nested headings (TM/TITRE_TM) down to this file."""
    if elem is None:
        return {}
    texte = elem.find("TEXTE")
    if texte is None:
        return {"other": [tree(c) for c in elem]}

    def tm(node: ET.Element) -> dict[str, Any]:
        return {"titres": [attrs(t) for t in node.findall("TITRE_TM")],
                **({"tm": [tm(c) for c in node.findall("TM")]} if node.find("TM") is not None else {})}
    out: dict[str, Any] = {"texte": dict(texte.attrib),
                           "titres_txt": [attrs(t) for t in texte.findall("TITRE_TXT")],
                           "tm": [tm(c) for c in texte.findall("TM")]}
    other = [tree(c) for c in texte if c.tag not in ("TITRE_TXT", "TM")]
    if other:
        out["other"] = other
    return out


def headings(ctx: dict[str, Any]) -> list[str]:
    """The headings from the text down to this file, the first title of
    each level (`contexte`'s TM chain)."""
    out: list[str] = []
    level = ctx.get("tm") or []
    while level:
        node = level[0]
        titles = node.get("titres") or []
        if titles and titles[0].get("text"):
            out.append(titles[0]["text"])
        level = node.get("tm") or []
    return out


def links(elem: ET.Element | None) -> list[dict[str, Any]]:
    """LIENS / VERSIONS / STRUCT children: each element's tag, attributes
    and text, in document order."""
    if elem is None:
        return []
    out = []
    for c in elem:
        row = {"tag": c.tag, **attrs(c)}
        kids = [{"tag": k.tag, **attrs(k)} for k in c]
        if kids:
            row["children"] = kids
        out.append(row)
    return out


_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote",
          "ul", "ol", "dl", "dt", "dd", "pre", "hr", "center"}
_CELL = {"td", "th"}


def render(elem: ET.Element | None) -> str:
    """A CONTENU block as text (the module's docstring)."""
    if elem is None:
        return ""
    lines: list[str] = []
    cur: list[str] = []

    def end_line() -> None:
        line = " ".join("".join(cur).split())
        if line:
            lines.append(line)
        cur.clear()

    def walk(e: ET.Element) -> None:
        tag = e.tag.lower() if isinstance(e.tag, str) else ""
        if tag in _BLOCK:
            end_line()
        if e.text:
            cur.append(e.text)
        first_cell = True
        for c in e:
            ctag = c.tag.lower() if isinstance(c.tag, str) else ""
            if ctag in _CELL:
                if not first_cell:
                    cur.append(" | ")
                first_cell = False
            walk(c)
            if c.tail:
                cur.append(c.tail)
        if tag in _BLOCK:
            end_line()
    walk(elem)
    end_line()
    return "\n".join(lines)


def images(elem: ET.Element | None) -> list[dict[str, str]]:
    """Every <img> of a CONTENU block, its attributes (src, alt, …)."""
    if elem is None:
        return []
    return [dict(i.attrib) for i in elem.iter() if isinstance(i.tag, str) and i.tag.lower() == "img"]
