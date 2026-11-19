"""Formex 4 — the Official Journal's production XML — read into the act
the plugin records (app/plugins/eurlex/source.py).

A Formex MANIFESTATION is several files: the wrapper (`….doc.xml`, or
`….doc.fmx.xml` since Formex 6) that names every other one, the body (an
`ACT`, or a `CONS.ACT` for a consolidated version) and, for an original,
one `ANNEX` file per annex. THE BODY IS PICKED BY NAME, never by the order
the origin lists the items in (legal-fr-de-eu.md §7.1: Cellar listed the
body first for a consolidation and second for the act): it is the file
the wrapper's `DOC.MAIN.PUB/REF.PHYS/@FILE` names, and every annex is a
`DOC.SUB.PUB` of type ANNEX by its file name.

What is read, and where it goes:

  * the TEXT — recitals, articles, annexes — becomes the record's
    sections (`sections`), in reading order, with its structure kept as
    lines: an article's heading, its numbered paragraphs at the margin,
    each list item indented three spaces a level, a table one row a line
    with its cells joined by " | ". Inline markup (`HT`, `DATE`, the
    `QUOT.START`/`QUOT.END` marks, rendered as the characters their CODE
    names) is wrapping (invariant 11 (a)); its words stay.
  * the APPARATUS — footnotes (`NOTE`), the cross-references (`REF.DOC.*`)
    — leaves the embedded text and is kept whole in the record (`notes`,
    `references`), like Normattiva's notes.
  * EVERYTHING ELSE the files assert — the bibliographic instance, the
    consolidation's own dates (`INFO.CONSLEG`: start, end, last
    modification), the production record, the family of the consolidation
    (`FAM.COMP`: the base act and every modifying act, each with its CELEX,
    OJ reference and title), the title, the citations and preamble, the
    final provisions and signatures, the wrapper in full — is kept as a
    tree (`tree`), keys and order as the origin wrote them. An element this
    reader does not know is kept the same way (`other`), never dropped.

Processing instructions (`<?PAGE NO='2'?>`, the printed page breaks) and
comments are the OJ's page chrome and are not read (invariant 11 (b)).

A change to anything that reaches `sections` or `extra` bumps the plugin's
PARSER_VERSION.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

#: the wrapper's names: Formex 4 `<file>.doc.xml`, Formex 6 `<file>.doc.fmx.xml`
_WRAPPER = re.compile(r"\.doc(?:\.fmx)?\.xml$", re.IGNORECASE)

#: elements whose content is laid out as lines of their own
_BLOCKS = {"PARAG", "ALINEA", "LIST", "ITEM", "NP", "P", "SUBDIV", "GR.SEQ", "TBL", "GR.TBL",
           "ROW", "CONTENTS", "DIVISION", "ARTICLE", "CONSID", "VISA", "QUOT.S", "GR.NOTES",
           "TXT.COL", "FORMULA.S", "CORPUS", "BLK", "GR.VISA", "GR.CONSID"}
#: inline elements standing for one character (U+ the CODE attribute)
_CHAR_MARKS = {"QUOT.START", "QUOT.END"}
_PI_ATTR = re.compile(r'([A-Za-z_.:-]+)="([^"]*)"')
#: the number of a numbered unit, followed by a space where it stands inline
_NUMBERS = {"NO.P", "NO.PARAG"}
_WS = re.compile(r"[ \t\r\n\u00a0]+")


class FormexError(ValueError):
    """The files are not a Formex manifestation this reader can take."""


def is_wrapper(name: str) -> bool:
    return bool(_WRAPPER.search(name))


#: a Formex manifestation Cellar serves as ONE item, a zip of its files (the
#: OJ's act-by-act publication: 32024R1689 → `OJABA_L_202401689_ENG.fmx4.zip`;
#: 32015D1735, repackaged 2026-11-19 → `32015D1735.eng.fmx4.zip`)
_PACKAGE = re.compile(r"\.zip$", re.IGNORECASE)
_ZIP_MAGIC = b"PK\x03\x04"
#: a package's members, uncompressed, at most (a damaged or hostile zip is refused)
PACKAGE_MAX = 1 << 30
#: a member's name as a unit holds files: one plain file name
_MEMBER_NAME = re.compile(r"^[^/\\\x00]+$")


#: what a file's first bytes are
_MAGIC = ((b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"%PDF", "pdf"), (b"\xff\xd8\xff", "jpeg"),
          (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF8", "gif"), (b"PK\x03\x04", "zip"))
#: what a file of that extension holds
EXTENSION_KIND = {".xml": "markup", ".html": "markup", ".htm": "markup", ".xhtml": "markup", ".tif": "tiff",
                  ".tiff": "tiff", ".pdf": "pdf", ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif",
                  ".zip": "zip"}
#: an OJ Formex file's name: `<coll>_<year><no><lg>.<volume><page><sequence>.xml`
_OJ_FILE = re.compile(r"^[A-Z]_\d{4}\d{3}[A-Z]{2}\.(?P<vol>\d{2})(?P<page>\d{4})(?P<seq>\d{2})\.xml$", re.IGNORECASE)
_BIB = re.compile(rb"<BIB\.INSTANCE>(.*?)(?:</BIB\.INSTANCE>|\Z)", re.S)
_DOC_REF = re.compile(rb"<DOCUMENT\.REF\b.*?</DOCUMENT\.REF>", re.S)
_PAGE_FIRST = re.compile(rb"<PAGE\.FIRST>\s*(\d+)\s*</PAGE\.FIRST>")
_PAGE_SEQ = re.compile(rb"<PAGE\.SEQ>\s*(\d+)\s*</PAGE\.SEQ>")


def item_kind(head: bytes) -> str:
    """What a file's first bytes say it is: tiff, pdf, … or markup."""
    if not head:
        return "empty"
    for magic, name in _MAGIC:
        if head.startswith(magic):
            return name
    if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return "markup"
    return "other"


def stated_place(name: str, head: bytes) -> tuple[int, int] | None:
    """(page, sequence) an OJ Formex file's own BIB.INSTANCE states, LESS the
    page and sequence its name encodes: (0, 0) for a file where its name
    says; None where the name is not an OJ file's or the file states none.
    A Special Edition numbers its own pages: every file of the unit then
    differs by the same offset (31968L0193 bg: +82), which `swapped` reads
    as numbering, never as a swap."""
    m = _OJ_FILE.match(name)
    if m is None or item_kind(head) != "markup":
        return None
    bib = _BIB.search(head)
    if bib is None:
        return None
    own = _DOC_REF.sub(b"", bib.group(1))
    first, seq = _PAGE_FIRST.search(own), _PAGE_SEQ.search(own)
    if first is None:
        return None
    return (int(first.group(1)) - int(m["page"]),
            (int(seq.group(1)) - int(m["seq"])) if seq is not None else 0)


def swapped(places: dict[str, tuple[int, int] | None]) -> list[str]:
    """The files of ONE unit whose stated place is not where their name says,
    against the unit's own numbering: the offset most of its files share
    (`stated_place`). 32015R0207 de: the body +4, the annex -4, every other
    file 0 — those two. A tie is read against (0, 0) where a tied offset is
    it; otherwise every file of the unit is named.""" 
    from collections import Counter
    offs = {n: o for n, o in places.items() if o is not None}
    if len(set(offs.values())) <= 1:
        return []
    ranked = Counter(offs.values()).most_common()
    top = [o for o, k in ranked if k == ranked[0][1]]
    if len(top) == 1:
        usual = top[0]
    elif (0, 0) in top:
        usual = (0, 0)          # a tie: a file where its name says is the numbering
    else:
        return sorted(offs)
    return sorted(n for n, o in offs.items() if o != usual)


def misnamed(name: str, head: bytes) -> str | None:
    """Why a file is not the one its name says, from its first bytes
    (`head`, a few KB) alone: they are not what its extension holds
    (2026-11-19: the Data Dump files 32015D1735's TIFF as `…01004901.xml`).
    None where nothing says so. A Formex file under another Formex file's
    name is the unit's question (`stated_place`, `swapped`): an OJ file states
    the page and sequence its name encodes (`…01000501.xml`: page 5, sequence
    1) in its own BIB.INSTANCE (the DOCUMENT.REF inside is the wrapper's)."""
    import os
    want, got = EXTENSION_KIND.get(os.path.splitext(name)[1].lower()), item_kind(head)
    if want is not None and got != want:
        return f"{name} holds {got}, not {want}"
    return None


def is_package(name: str) -> bool:
    """An item named as a zip: the manifestation's files packed in one."""
    return bool(_PACKAGE.search(name or ""))


def unpack(files: dict[str, bytes]) -> tuple[dict[str, bytes], dict[str, str]]:
    """The manifestation's files as the reader takes them: every item, and
    each package's members in its place. The package stays HELD as the
    origin served it (invariant 11: the unit is what Cellar gave, and
    records-vs-origin compares those bytes); only the reading opens it.
    Returns (the files to read, member → the package it came from).

    THE STANDARD: the zip (PKWARE APPNOTE), read by Python's own zipfile,
    each member's CRC-32 checked. A member that is not one plain file name,
    a package over PACKAGE_MAX, or a member whose name an item of other
    bytes already holds is refused: the reading must be one the origin's
    bytes determine."""
    import io
    import zipfile
    out = {n: b for n, b in files.items() if not (is_package(n) and b[:4] == _ZIP_MAGIC)}
    came: dict[str, str] = {}
    for name in sorted(files):
        data = files[name]
        if not (is_package(name) and data[:4] == _ZIP_MAGIC):
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                if sum(i.file_size for i in infos) > PACKAGE_MAX:
                    raise FormexError(f"{name}: its members are over {PACKAGE_MAX} bytes")
                for i in infos:
                    if not _MEMBER_NAME.match(i.filename) or i.filename in (".", ".."):
                        raise FormexError(f"{name}: member {i.filename!r} is not a plain file name")
                    got = z.read(i)
                    if i.filename in out and out[i.filename] != got:
                        raise FormexError(f"{name}: member {i.filename} differs from the item of that name")
                    out[i.filename] = got
                    came[i.filename] = name
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError) as e:
            raise FormexError(f"{name}: not a readable zip: {e}") from None
    return out, came


def norm(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def tree(e: ET.Element) -> Any:
    """An element as plain data, nothing dropped: `@` the attributes, `#`
    the text, each child under its tag (a list where the tag repeats), in
    the origin's order. A leaf without attributes is its text."""
    kids = list(e)
    text = norm("".join([e.text or ""] + [c.tail or "" for c in kids]))
    if not kids and not e.attrib:
        return text
    if text and kids:
        # mixed content ("Done at Strasbourg, <DATE>8 June 2016</DATE>."):
        # the words in their order, beside the children that carry them
        text = norm("".join(e.itertext()))
    out: dict[str, Any] = {}
    if e.attrib:
        out["@"] = dict(e.attrib)
    if text:
        out["#"] = text
    grouped: dict[str, list] = {}
    for c in kids:
        if isinstance(c.tag, str):
            grouped.setdefault(c.tag, []).append(tree(c))
    for tag, values in grouped.items():
        out[tag] = values[0] if len(values) == 1 else values
    return out


@dataclass
class Collect:
    """What the text renderer sets aside while it lays out a section."""
    section: str = ""
    notes: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    modifications: list[dict] = field(default_factory=list)
    instructions: list[dict] = field(default_factory=list)

    def pi(self, e: ET.Element) -> None:
        """A processing instruction. A consolidation's `CLG.MDFO`/`CLG.MDFC`
        open and close the text an amending act changed (`ACTIVE.DOC` its
        CELEX, `ACTION` what it did): entity information, kept. `PAGE` is
        the printed page break, chrome (invariant 11 (b)). Any other is kept."""
        if e.tag is not ET.ProcessingInstruction:
            return
        target, _, rest = (e.text or "").partition(" ")
        if target == "PAGE":
            return
        attrs = dict(_PI_ATTR.findall(rest))
        entry = {"section": self.section, "pi": target, **attrs}
        (self.modifications if target.startswith("CLG.") else self.instructions).append(entry)

    def note(self, e: ET.Element, inline_text: str) -> None:
        self.notes.append({"section": self.section, **({"@": dict(e.attrib)} if e.attrib else {}),
                           "text": inline_text})

    def reference(self, e: ET.Element, inline_text: str) -> None:
        self.references.append({"section": self.section, "tag": e.tag,
                                **({"@": dict(e.attrib)} if e.attrib else {}), "text": inline_text})


def _char(e: ET.Element) -> str:
    code = e.attrib.get("CODE", "")
    try:
        return chr(int(code, 16))
    except ValueError:
        return ""


def inline(e: ET.Element, col: Collect | None = None) -> str:
    """The element's words on one line: footnotes out (to `col`), quote
    marks as characters, every other element's text in place."""
    parts: list[str] = [e.text or ""]
    for c in e:
        if not isinstance(c.tag, str):
            if col is not None:
                col.pi(c)
            parts.append(c.tail or "")
            continue
        if c.tag == "NOTE":
            if col is not None:
                col.note(c, inline(c, None))
        elif c.tag in _CHAR_MARKS:
            parts.append(_char(c))
        else:
            words = inline(c, col)
            if c.tag.startswith("REF.") and col is not None:
                col.reference(c, words)
            parts.append(" " + words + " " if c.tag in _BLOCKS
                         else words + " " if c.tag in _NUMBERS else words)
        parts.append(c.tail or "")
    return norm("".join(parts))


def lines(e: ET.Element, depth: int, col: Collect, lead: str = "") -> list[str]:
    """An element's text as indented lines (three spaces a level)."""
    pad = "   " * depth
    tag = e.tag
    if tag == "NOTE":
        col.note(e, inline(e, None))
        return []
    if tag in ("TBL", "GR.TBL"):
        out: list[str] = []
        for child in e:
            if child.tag == "TITLE":
                out.append(pad + inline(child, col))
            elif child.tag == "CORPUS":
                for row in child.iter("ROW"):
                    cells = [inline(cell, col) for cell in row if cell.tag == "CELL"]
                    if any(cells):
                        out.append(pad + " | ".join(cells))
            elif child.tag in ("TBL", "GR.TBL"):
                out += lines(child, depth, col)
            elif isinstance(child.tag, str):
                text = inline(child, col)
                if text:
                    out.append(pad + text)
        return out
    if tag == "LIST":
        out = []
        for item in e:
            if isinstance(item.tag, str):
                out += lines(item, depth, col)
        return out
    numbered = {"NP": "NO.P", "PARAG": "NO.PARAG"}.get(tag)
    out = []
    buf: list[str] = [lead + " "] if lead else []
    if numbered is None:
        buf.append(e.text or "")
    for c in e:
        if not isinstance(c.tag, str):
            col.pi(c)
            buf.append(c.tail or "")
            continue
        if numbered is not None and c.tag == numbered:
            buf.append(inline(c, col) + " ")
            buf.append(c.tail or "")
            continue
        if c.tag == "NOTE":
            col.note(c, inline(c, None))
        elif c.tag in _CHAR_MARKS:
            buf.append(_char(c))
        elif c.tag in _BLOCKS or c.tag in ("TITLE", "TI", "STI"):
            text = norm("".join(buf))
            if c.tag == "LIST":
                if text:
                    out.append(pad + text)
                out += lines(c, depth + 1, col)
            elif c.tag in ("TITLE", "TI", "STI"):
                if text:
                    out.append(pad + text)
                t = heading(c, col)
                if t:
                    out.append(pad + t)
            else:
                sub = lines(c, depth, col, lead=text)
                if text and not sub:
                    out.append(pad + text)
                out += sub
            buf = []
        else:
            words = inline(c, col)
            if c.tag.startswith("REF."):
                col.reference(c, words)
            buf.append(words)
        buf.append(c.tail or "")
    text = norm("".join(buf))
    if text:
        out.append(pad + text)
    return out


def heading(e: ET.Element | None, col: Collect | None = None) -> str:
    """A TITLE's words: its TI, then its STI, joined by " — "."""
    if e is None:
        return ""
    if e.tag in ("TI", "STI", "TI.ART", "STI.ART"):
        return " ".join(norm(inline(p, col)) for p in e) if len(e) and all(
            c.tag == "P" for c in e) else inline(e, col)
    parts = [heading(c, col) for c in e if c.tag in ("TI", "STI")]
    if not parts:
        return inline(e, col)
    return " — ".join(p for p in parts if p)


@dataclass
class Article:
    identifier: str | None
    num: str
    title: str
    partition: str
    body: str


@dataclass
class Annex:
    file: str | None
    title: str
    body: str
    bib: Any = None


@dataclass
class Act:
    """One Formex manifestation, read."""
    root: str                           # ACT | CONS.ACT
    body_file: str
    wrapper_file: str
    language: str                       # LG.DOC, lower case, as the origin wrote it
    title: str
    date: str | None                    # the act's own date, ISO
    publication_date: str | None        # the OJ's date (the wrapper's PUBLICATION.REF), ISO
    consleg: dict                       # INFO.CONSLEG's attributes (a consolidation)
    base_celex: str | None              # FAM.COMP's NO.CELEX (a consolidation): the act consolidated
    recitals: list[tuple[str, str]]
    articles: list[Article]
    annexes: list[Annex]
    meta: dict                          # every other assertion, as trees
    notes: list[dict]
    references: list[dict]
    modifications: list[dict]           # a consolidation's CLG.MDFO/MDFC marks, in order
    instructions: list[dict]            # any other processing instruction but PAGE
    wrapper: Any                        # the wrapper, whole
    #: every element's ATTRIBUTES in the body and each annex item, in
    #: document order: `{file: [[tag, {name: value}], …]}` (`attributes_of`)
    markup: dict = field(default_factory=dict)
    #: the files the wrapper names that the manifestation does not hold
    #: (parser 5: said on the record, never the record refused)
    missing: list = field(default_factory=list)
    #: an act read from another form (rendition.py): its sections as read
    rendered_sections: list | None = None


def iso(d: str | None) -> str | None:
    """`20160608` → `2016-06-08`; None for anything else."""
    d = (d or "").strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return None


def _parse(data: bytes, name: str) -> ET.Element:
    """The file as a tree WITH its processing instructions (a consolidation's
    modification marks are PIs; `Collect.pi`)."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_pis=True))
    try:
        parser.feed(data)
        return parser.close()
    except ET.ParseError as e:
        raise FormexError(f"{name}: not XML: {e}") from None


def _include(root: ET.Element, files: dict[str, bytes], name: str, ancestors: frozenset,
             found: dict) -> None:
    """FORMEX INCLUSIONS READ IN PLACE (2026-11-19; 32021R1971: Annex I's file,
    876 bytes, is `<QUOT.S><INCL.ELEMENT FILEREF="…01000302.xml"
    TYPE="FORMEX.DOC"/></QUOT.S>` — the 39 MB of tables, 2,687 pages, are the
    included file's, and the reader read none of them). Each
    `INCL.ELEMENT TYPE="FORMEX.DOC"` in the text is replaced, where it stands,
    by the included document's content (its root's children but its
    BIB.INSTANCE, as a CONTENTS block), itself resolved the same way. The one
    in BIB.INSTANCE/INCLUSIONS is the listing of the same inclusion and is
    not read. The included file's BIB.INSTANCE and markup are kept
    (`found`: invariant 11); a file it names and the manifestation lacks is
    said (`missing`); one including itself back is not read again."""
    parents = {c: p for p in root.iter() for c in p}
    for el in list(root.iter("INCL.ELEMENT")):
        if el.attrib.get("TYPE") != "FORMEX.DOC":
            continue
        parent = parents.get(el)
        if parent is None or parent.tag == "INCLUSIONS":
            continue
        ref = el.attrib.get("FILEREF") or ""
        if ref not in files:
            if ref:
                found["missing"].append(ref)
            continue
        if ref in ancestors:
            found["cycles"].append({"file": ref, "into": name})
            continue
        sub = _parse(files[ref], ref)
        _include(sub, files, ref, ancestors | {ref}, found)
        bib = sub.find("BIB.INSTANCE")
        found["included"].append({"file": ref, "into": name, "root": sub.tag,
                                  **({"bib_instance": tree(bib)} if bib is not None else {})})
        found["markup"][ref] = attributes_of(sub)
        block = ET.Element("CONTENTS")
        for c in sub:
            if c.tag != "BIB.INSTANCE":
                block.append(c)
        block.tail = el.tail
        at = list(parent).index(el)
        parent.remove(el)
        parent.insert(at, block)


def body_name(wrapper: ET.Element) -> str:
    """The body's file, BY NAME: DOC.MAIN.PUB's REF.PHYS."""
    for ref in wrapper.iterfind("./FMX/DOC.MAIN.PUB/REF.PHYS"):
        name = ref.attrib.get("FILE")
        if name:
            return name
    raise FormexError("the wrapper names no DOC.MAIN.PUB/REF.PHYS file: no body to read")


def main_names(wrapper: ET.Element) -> list[str]:
    """Every file of the main document, BY NAME, in the wrapper's order:
    DOC.MAIN.PUB's REF.PHYS (parser 5: a Special Edition splits an act's
    main document over several items — 31968R0259 hr/bg/ro, its first a
    table, the act after it — and only the first was read)."""
    out = []
    for ref in wrapper.iterfind("./FMX/DOC.MAIN.PUB/REF.PHYS"):
        name = ref.attrib.get("FILE")
        if name and name not in out:
            out.append(name)
    return out


def annex_names(wrapper: ET.Element) -> list[str]:
    out = []
    for sub in wrapper.iterfind("./FMX/DOC.SUB.PUB"):
        for ref in sub.iterfind("REF.PHYS"):
            if ref.attrib.get("FILE"):
                out.append(ref.attrib["FILE"])
    return out


_NO_CELEX = re.compile(rb"<NO\.CELEX>\s*([^<\s]+)\s*</NO\.CELEX>")


def own_wrapper(names, celex: str | None, read) -> str:
    """The wrapper of THIS act's text among a manifestation's items: the only
    one, or — several (München, 2026-11-16: a Croatian Special Edition
    volume, one fmx4 manifestation whose items are the files of two acts
    sharing its FIN.ID, 1,241 Croatian works) — the one whose NO.CELEX is the
    act's own, and only when exactly one is. The other act's files stay items
    of the unit (the origin lists them under this manifestation: invariant
    11); only this one is read. `read(name)` gives a wrapper's bytes."""
    wrappers = sorted(n for n in names if is_wrapper(n))
    if not wrappers:
        raise FormexError("no wrapper (….doc.xml) among the manifestation's items")
    if len(wrappers) == 1:
        return wrappers[0]
    want = (celex or "").encode()
    own = [n for n in wrappers if want and want in {m.group(1) for m in _NO_CELEX.finditer(read(n))}]
    if len(own) != 1:
        raise FormexError(f"{len(wrappers)} wrappers among the items ({', '.join(wrappers)}), "
                          f"{'none' if not own else len(own)} naming {celex or 'the act'} as its NO.CELEX")
    return own[0]


def wrapper_of(files: dict[str, bytes], celex: str | None = None) -> tuple[str, ET.Element]:
    name = own_wrapper(files, celex, files.__getitem__)
    return name, _parse(files[name], name)


def _date_iso(e: ET.Element | None) -> str | None:
    if e is None:
        return None
    return iso(e.attrib.get("ISO")) or iso(norm(e.text))


def _recitals(preamble: ET.Element | None, col: Collect) -> tuple[list[tuple[str, str]], dict]:
    if preamble is None:
        return [], {}
    meta: dict[str, Any] = {}
    out: list[tuple[str, str]] = []
    for child in preamble:
        if child.tag == "PREAMBLE.INIT":
            meta["init"] = inline(child, col)
        elif child.tag == "GR.VISA":
            col.section = "preamble"
            meta.setdefault("visas", []).extend(inline(v, col) for v in child if v.tag == "VISA")
        elif child.tag == "GR.CONSID":
            init = ""
            for c in child:
                if c.tag == "GR.CONSID.INIT":
                    init = inline(c, col)
                    meta["consid_init"] = init
                elif c.tag == "CONSID":
                    num = norm("".join(n.text or "" for n in c.iter("NO.P")))
                    label = " ".join(x for x in (init.rstrip(":").strip(), num) if x) or "Recital"
                    col.section = label
                    out.append((label, "\n".join(lines(c, 0, col))))
                elif isinstance(c.tag, str):
                    meta.setdefault("other", []).append({c.tag: tree(c)})
        elif child.tag == "PREAMBLE.FINAL":
            meta["final"] = inline(child, col)
        elif isinstance(child.tag, str):
            meta.setdefault("other", []).append({child.tag: tree(child)})
    return out, meta


def _articles(terms: ET.Element | None, col: Collect) -> list[Article]:
    out: list[Article] = []
    if terms is None:
        return out

    def walk(e: ET.Element, partition: list[str]) -> None:
        for c in e:
            if not isinstance(c.tag, str):
                col.pi(c)
            elif c.tag == "DIVISION":
                title = heading(c.find("TITLE"), col)
                walk(c, partition + ([title] if title else []))
            elif c.tag == "ARTICLE":
                num = heading(c.find("TI.ART"), col)
                sti = heading(c.find("STI.ART"), col)
                part = " › ".join(partition)
                label_head = " — ".join(x for x in (num, sti) if x) or "Article"
                col.section = " › ".join(x for x in (part, label_head) if x)
                body: list[str] = [label_head]
                for b in c:
                    if not isinstance(b.tag, str):
                        col.pi(b)
                    elif b.tag not in ("TI.ART", "STI.ART"):
                        body += lines(b, 0, col)
                out.append(Article(c.attrib.get("IDENTIFIER"), num, sti, part, "\n".join(body)))
            elif c.tag == "TITLE":
                continue
            elif isinstance(c.tag, str):
                # enacting terms outside an article (a GR.SEQ, a lone ALINEA):
                # kept as a section of their own, never dropped
                col.section = " › ".join(partition) or "Enacting terms"
                text = "\n".join(lines(c, 0, col))
                if text:
                    out.append(Article(None, "", "", " › ".join(partition), text))
    walk(terms, [])
    return out


def _annex(e: ET.Element, file: str | None, col: Collect) -> Annex:
    title = heading(e.find("TITLE"), col)
    col.section = title or "Annex"
    body: list[str] = []
    for c in e:
        if not isinstance(c.tag, str):
            col.pi(c)
        elif c.tag not in ("TITLE", "BIB.INSTANCE"):
            body += lines(c, 0, col)
    bib = e.find("BIB.INSTANCE")
    return Annex(file, title, "\n".join(body), tree(bib) if bib is not None else None)


def _final(e: ET.Element | None, col: Collect) -> Any:
    if e is None:
        return None
    col.section = "final"
    out: dict[str, Any] = {"text": [inline(p, col) for p in e if p.tag == "P"]}
    for c in e:
        if c.tag != "P" and isinstance(c.tag, str):
            out.setdefault(c.tag, []).append(tree(c))
    return out


_ACT_KNOWN = {"BIB.INSTANCE", "TITLE", "PREAMBLE", "ENACTING.TERMS", "FINAL"}
#: the roots read as an act (parser 5: AGR, an agreement, has its shape)
_ACT_ROOTS = ("ACT", "CONS.ACT", "AGR")


def _corrections(root: ET.Element, col: Collect) -> list[tuple[str, str]]:
    """A corrigendum's corrections (CORR/CONTENTS.CORR/CORRECTION), one
    section each: labelled by where it applies (DESCRIPTION), its text the
    old reading then the new, in the origin's own words."""
    out: list[tuple[str, str]] = []
    for i, c in enumerate(root.iter("CORRECTION"), 1):
        where = c.find("DESCRIPTION")
        label = norm(inline(where, col)) if where is not None else ""
        body: list[str] = []
        for part in c:
            if isinstance(part.tag, str) and part.tag != "DESCRIPTION":
                body += lines(part, 0, col)
        out.append((label or f"Correction {i}", "\n".join(x for x in body if x.strip())))
    if not out:
        out = _contents(root, col)
    return out


def _contents(root: ET.Element, col: Collect) -> list[tuple[str, str]]:
    """Any other body (GENERAL …): its CONTENTS (or the root's own blocks)
    by group — a GR.SEQ's TITLE labels its text — and what stands outside a
    group as one section, so no word is left out."""
    holder = root.find("CONTENTS")
    holder = holder if holder is not None else root
    out: list[tuple[str, str]] = []
    loose: list[str] = []
    for c in holder:
        if not isinstance(c.tag, str) or c.tag in ("BIB.INSTANCE", "TITLE"):
            continue
        if c.tag == "GR.SEQ":
            t = c.find("TITLE")
            label = norm(heading(t, col)) if t is not None else ""
            body = [x for part in c if isinstance(part.tag, str) and part is not t for x in lines(part, 0, col)]
            out.append((label or "Contents", "\n".join(x for x in body if x.strip())))
        else:
            loose += lines(c, 0, col)
    if loose:
        out.insert(0, ("Contents", "\n".join(x for x in loose if x.strip())))
    return out


def _rendered(own: list | None, recitals: list, articles: list, more: list, annexes: list) -> list | None:
    """The sections where parser 5 reads more than an act's (a body that is
    not an act, other main files): None where an act alone was read, so
    `sections` reads it as before."""
    if own is None and not more:
        return None
    out = list(own) if own is not None else list(recitals) + [
        (" › ".join(x for x in (a.partition, " — ".join(y for y in (a.num, a.title) if y)) if x)
         or "Enacting terms", a.body) for a in articles]
    return out + list(more) + [(a.title or "Annex", a.body) for a in annexes]
_CONS_KNOWN = {"INFO.CONSLEG", "INFO.PROD", "CONS.DOC"}
_CONS_DOC_KNOWN = {"BIB.INSTANCE", "FAM.COMP", "TITLE", "PREAMBLE", "ENACTING.TERMS", "FINAL",
                   "CONS.ANNEX"}


def attributes_of(root: ET.Element) -> list[list]:
    """`[[tag, {name: value}], …]`: every element of `root` (itself first)
    that carries attributes, in document order. INVARIANT 11 PAST THE WORDS
    (München, 2026-11-14, comparing EUR-Lex's forms; the test that read every
    attribute of every item): the text renderer keeps each element's words
    but dropped its attributes, and those are assertions about the act — a
    date's ISO form (DATE ISO), a reference's Official Journal coordinates
    (REF.DOC.OJ COLL, NO.OJ, DATE.PUB, PAGE.FIRST), a paragraph's identifier
    (PARAG IDENTIFIER), a list's numbering (LIST TYPE), a table's columns
    (TBL COLS, CELL COL), the act's own flags (ACT NNC). Typography (HT TYPE,
    QUOT.START CODE) is kept too: the line between markup and assertion is
    not drawn by guess. The n-th entry of a tag is the n-th element of that
    tag in the item, so a reader aligns them with the text."""
    return [[e.tag, dict(e.attrib)] for e in root.iter() if isinstance(e.tag, str) and e.attrib]


def parse(files: dict[str, bytes], celex: str | None = None) -> Act:
    """The manifestation's items by name → the act (`celex`'s, where the
    items hold more than one act: `own_wrapper`). Raises FormexError when
    the wrapper, the body or an annex it names is missing or unreadable."""
    wname, wrapper = wrapper_of(files, celex)
    mains = main_names(wrapper)
    if not mains:
        raise FormexError("the wrapper names no DOC.MAIN.PUB/REF.PHYS file: no body to read")
    held_mains = [n for n in mains if n in files]
    if not held_mains:
        raise FormexError(f"the wrapper names the body {mains[0]}, which is not among the items")
    parsed = {n: _parse(files[n], n) for n in held_mains}
    found: dict[str, Any] = {"missing": [], "cycles": [], "included": [], "markup": {}}
    for n, tr in parsed.items():
        _include(tr, files, n, frozenset({n}), found)
    # THE BODY (parser 5): the first main file shaped as an act — an ACT, a
    # CONS.ACT, an AGR (an agreement: a preamble and articles, as an ACT) —
    # else the first; every other main file is read as text after it
    bname = next((n for n in held_mains if parsed[n].tag in _ACT_ROOTS), held_mains[0])
    root = parsed[bname]
    markup: dict[str, list] = {bname: attributes_of(root)}
    col = Collect()
    pub = wrapper.find("./PUBLICATION.REF/DATE")
    meta: dict[str, Any] = {}
    consleg: dict = {}
    base_celex = None
    rendered: list[tuple[str, str]] | None = None
    if root.tag in ("ACT", "AGR"):
        doc = root
        known = _ACT_KNOWN
    elif root.tag == "CONS.ACT":
        info = root.find("INFO.CONSLEG")
        consleg = dict(info.attrib) if info is not None else {}
        prod = root.find("INFO.PROD")
        if prod is not None:
            meta["info_prod"] = tree(prod)
        doc = root.find("CONS.DOC")
        if doc is None:
            raise FormexError(f"{bname}: a CONS.ACT without CONS.DOC")
        for c in root:
            if c.tag not in _CONS_KNOWN and isinstance(c.tag, str):
                meta.setdefault("other", []).append({c.tag: tree(c)})
        known = _CONS_DOC_KNOWN
        fam = doc.find("FAM.COMP")
        if fam is not None:
            meta["fam_comp"] = tree(fam)
            got = fam.find("./BIB.DATA/NO.CELEX")
            base_celex = norm(got.text) if got is not None else None
    else:
        # parser 5: a body that is not an act — a corrigendum (CORR: its
        # corrections, each the place, the old reading and the new), a
        # GENERAL document (its CONTENTS by group), any other root — read as
        # its own sections, every element kept as a tree (invariant 11)
        doc = root
        known = {"BIB.INSTANCE", "TITLE"}
        rendered = _corrections(root, col) if root.tag == "CORR" else _contents(root, col)
    bib = doc.find("BIB.INSTANCE")
    if bib is not None:
        meta["bib_instance"] = tree(bib)
    lang = norm(bib.findtext("LG.DOC")) if bib is not None else ""
    if not lang:
        lang = norm(wrapper.findtext("./FMX/DOC.MAIN.PUB/LG.DOC"))
    if not lang:
        raise FormexError(f"{bname}: no LG.DOC — the language is the origin's to state")
    title_el = doc.find("TITLE")
    title = heading(title_el, col) if title_el is not None else ""
    title = " ".join(norm(inline(p, col)) for p in title_el.iter("P")) if title_el is not None \
        and title_el.find(".//P") is not None else title
    if title_el is not None:
        meta["title"] = tree(title_el)
    recitals, preamble = _recitals(doc.find("PREAMBLE"), col)
    if preamble:
        meta["preamble"] = preamble
    articles = _articles(doc.find("ENACTING.TERMS"), col)
    final = _final(doc.find("FINAL"), col)
    if final is not None:
        meta["final"] = final
    for c in doc:
        if isinstance(c.tag, str) and c.tag not in known:
            meta.setdefault("other", []).append({c.tag: tree(c)})
    annexes = [_annex(a, bname, col) for a in doc.iterfind("CONS.ANNEX")]
    missing = [n for n in mains if n not in files]
    # the other main files, read as text after the body, in the wrapper's order
    more: list[tuple[str, str]] = []
    for name in held_mains:
        if name == bname:
            continue
        other = parsed[name]
        markup[name] = attributes_of(other)
        meta.setdefault("main_documents", []).append({"file": name, other.tag: tree(other)})
        more += _corrections(other, col) if other.tag == "CORR" else _contents(other, col)
    for name in annex_names(wrapper):
        if name not in files:
            # parser 5: a file the wrapper names and the origin's manifestation
            # does not hold (31983R0707 hr, 29 units): said, not refused
            missing.append(name)
            continue
        a = _parse(files[name], name)
        _include(a, files, name, frozenset({name}), found)
        markup[name] = attributes_of(a)
        if a.tag != "ANNEX":
            meta.setdefault("sub_documents", []).append({"file": name, a.tag: tree(a)})
            continue
        annexes.append(_annex(a, name, col))
    # every other XML item the manifestation holds (the OJ issue's `toc`):
    # kept as a tree, never dropped; a non-XML item (an image) is held in
    # the unit and named in the record's `items`
    if found["included"]:
        meta["inclusions"] = found["included"]
    if found["cycles"]:
        meta["inclusion_cycles"] = found["cycles"]
    markup.update(found["markup"])
    missing += [n for n in dict.fromkeys(found["missing"]) if n not in missing]
    read = {wname, *mains, *annex_names(wrapper), *(i["file"] for i in found["included"])}
    for name in sorted(files):
        if name not in read and name.lower().endswith(".xml"):
            meta.setdefault("other_items", {})[name] = tree(_parse(files[name], name))
    date = _date_iso(bib.find("DATE")) if bib is not None else None
    if root.tag == "CONS.ACT":
        base_date = doc.find("./FAM.COMP/BIB.DATA/DATE")
        date = _date_iso(base_date) or date
    return Act(root=root.tag, body_file=bname, wrapper_file=wname, language=lang.lower(),
               title=title, date=date, publication_date=_date_iso(pub), consleg=consleg,
               base_celex=base_celex, recitals=recitals, articles=articles, annexes=annexes,
               meta=meta, notes=col.notes, references=col.references,
               modifications=col.modifications, instructions=col.instructions,
               wrapper=tree(wrapper), markup={k: v for k, v in markup.items() if v},
               rendered_sections=_rendered(rendered, recitals, articles, more, annexes),
               missing=missing)


def sections(act: Act) -> list[tuple[str, str]]:
    """`[(label, text)]` in reading order: each recital, each article (its
    partition before its heading), each annex. One section a unit, because
    each is what a citation names ("recital 26", "Article 5", "Annex II")."""
    if act.rendered_sections is not None:
        return list(act.rendered_sections)
    out = list(act.recitals)
    for a in act.articles:
        head = " — ".join(x for x in (a.num, a.title) if x)
        out.append((" › ".join(x for x in (a.partition, head) if x) or "Enacting terms", a.body))
    out += [(a.title or "Annex", a.body) for a in act.annexes]
    return out
