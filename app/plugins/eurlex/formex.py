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
A change that only turns a refused parse into a parse moves no record that
exists (an error is not an output): no bump.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

#: the wrapper's names: Formex 4 `<file>.doc.xml`, Formex 6 `<file>.doc.fmx.xml`
_WRAPPER = re.compile(r"\.doc(?:\.fmx)?\.xml$", re.IGNORECASE)

#: elements whose content is laid out as lines of their own
_BLOCKS = {"PARAG", "ALINEA", "LIST", "ITEM", "NP", "P", "SUBDIV", "GR.SEQ", "TBL", "GR.TBL", "DLIST",
           "DLIST.ITEM",
           "ROW", "CONTENTS", "DIVISION", "ARTICLE", "CONSID", "VISA", "QUOT.S", "GR.NOTES",
           "TXT.COL", "FORMULA.S", "CORPUS", "BLK", "GR.VISA", "GR.CONSID"}
#: inline elements standing for one character (U+ the CODE attribute)
_CHAR_MARKS = {"QUOT.START", "QUOT.END"}
#: elements whose words are kept apart from their neighbours' on one line
#: (2026-11-21: "SFCWHSCSpecific fuel consumption" — a DLIST's term and its
#: definition, a table's cells, glued)
_SPACED = {"CELL", "TERM", "DEFINITION", "DLIST.ITEM", "ROW", "ITEM", "TI.BLK", "STI.BLK"}
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


#: an OJ Formex file's name: its issue (`L_2015038DE`), volume, page and sequence
_OJ_PLACE = re.compile(r"^(?P<issue>[A-Z]_\d{7}[A-Z]{2})\.(?P<vol>\d{2})(?P<page>\d{4})(?P<seq>\d{2})\.xml$",
                       re.IGNORECASE)


def stated_place(name: str, head: bytes) -> tuple[str, int, int, int, int] | None:
    """(issue and volume, the name's page, its sequence, the page and the
    sequence the file's own BIB.INSTANCE states — its DOCUMENT.REF is the
    wrapper's and is not read); None where the name is not an OJ file's or
    the file states no page."""
    m = _OJ_PLACE.match(name)
    if m is None or item_kind(head) != "markup":
        return None
    bib = _BIB.search(head)
    if bib is None:
        return None
    own = _DOC_REF.sub(b"", bib.group(1))
    first, seq = _PAGE_FIRST.search(own), _PAGE_SEQ.search(own)
    if first is None:
        return None
    return (f"{m['issue'].upper()}.{m['vol']}", int(m["page"]), int(m["seq"]), int(first.group(1)),
            int(seq.group(1)) if seq is not None else 0)


def swapped(places: dict[str, tuple[str, int, int, int, int] | None]) -> list[str]:
    """The files of ONE unit that sit in each other's place (2026-11-19,
    32015R0207 de: the body's name holds the annex, which states page 5, the
    annex's name the body, page 1). Within one OJ issue and volume, files in
    the order of the page and sequence their names encode state pages and
    sequences in the same order: an inversion is a swap. What a Special
    Edition does keeps the order — its own page numbers (31968L0193 bg:
    +82), one offset per OJ issue its volume collects (31998D0379 bg: 2, 3,
    73, 100), a page more after a page break (31998D0436 bg: 153, 153, 154,
    154) — and is never named. A file alone in its issue cannot be judged."""
    by_issue: dict[str, list[tuple[tuple[int, int], tuple[int, int], str]]] = {}
    for name, p in places.items():
        if p is not None:
            by_issue.setdefault(p[0], []).append(((p[1], p[2]), (p[3], p[4]), name))
    out: set[str] = set()
    for files in by_issue.values():
        files.sort()
        for (_n1, s1, a), (_n2, s2, b) in zip(files, files[1:]):
            if s2 < s1:
                out.update((a, b))
    return sorted(out)


def not_its_name(files: dict[str, bytes], wrapper: ET.Element, wname: str) -> list[str]:
    """Why the act's own files are not the ones their names say — each a
    reason, none where they are (2026-11-19: the Data Dump's 9 acts of
    2014–15, 32015D1735 a TIFF as its body's `.xml`, 32015R0207 its annex
    under its body's name; Cellar serves them right). Only the files the
    act's own wrapper names: a Special Edition unit holds another act's
    files too, numbered in their own order."""
    named = [n for n in dict.fromkeys([*main_names(wrapper), *annex_names(wrapper)]) if n in files]
    why = [w for n in [wname, *named] if (w := misnamed(n, files[n][:_HEAD_BYTES])) is not None]
    # THE WRAPPER BY ITS CONTENT TOO (2026-11-21, 32013D0410 in 24 languages:
    # the dump's `…01005401.doc.xml` holds ANNEX VII, the wrapper is filed as
    # `…01005401.xml`, the ACT as `…01005801.xml`; Cellar serves them right)
    if wrapper.tag != "DOC":
        why.append(f"{wname} holds <{wrapper.tag}>, not a wrapper")
    for n, data in files.items():
        if not is_wrapper(n) and n.lower().endswith(".xml") and root_tag(data[:_HEAD_BYTES]) == "DOC":
            why.append(f"{n} holds a wrapper (<DOC>)")
    off = swapped({n: stated_place(n, files[n][:_HEAD_BYTES]) for n in named})
    if off:
        why.append("in each other's place: " + ", ".join(off))
    return why


#: what `misnamed` and `stated_place` read of a file
_HEAD_BYTES = 4096
_FIRST_ELEMENT = re.compile(rb"<([A-Za-z][A-Za-z0-9._-]*)[\s>/]")
_PROLOG = re.compile(rb"<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>", re.S)


def root_tag(head: bytes) -> str | None:
    """A file's first element's name, from its first bytes: after the XML
    declaration, processing instructions and comments; None for no markup."""
    m = _FIRST_ELEMENT.search(_PROLOG.sub(b"", head))
    return m.group(1).decode() if m else None


def misnamed(name: str, head: bytes) -> str | None:
    """Why a file is not the one its name says, from its first bytes
    (`head`, a few KB) alone: they are not what its extension holds
    (2026-11-19: the Data Dump files 32015D1735's TIFF as `…01004901.xml`).
    None where nothing says so. A Formex file under another Formex file's
    name is the unit's question (`stated_place`, `swapped`)."""
    import os
    want, got = EXTENSION_KIND.get(os.path.splitext(name)[1].lower()), item_kind(head)
    if want is not None and got != want:
        return f"{name} holds {got}, not {want}"
    return None


def is_package(name: str) -> bool:
    """An item named as a zip: the manifestation's files packed in one."""
    return bool(_PACKAGE.search(name or ""))


def unpack(files: dict[str, Any]) -> tuple[dict[str, bytes], dict[str, str]]:
    """The manifestation's files as the reader takes them: every item, and
    each package's members in its place. The package stays HELD as the
    origin served it (invariant 11: the unit is what Cellar gave, and
    records-vs-origin compares those bytes); only the reading opens it.
    Returns (the files to read, member → the package it came from).

    THE STANDARD: the zip (PKWARE APPNOTE), read by Python's own zipfile,
    each member's CRC-32 checked. A member that is not one plain file name,
    a package over PACKAGE_MAX, or a member whose name an item of other
    bytes already holds is refused: the reading must be one the origin's
    bytes determine. A value is a file's bytes, or a package's PATH (read
    from its file, never whole: see below)."""
    import io
    import os
    import zipfile
    # A PACKAGE GIVEN AS ITS PATH (2026-11-24, step 6 of EUR-Lex5.next's
    # restart: discovery's `wrapper_head` read every package WHOLE to date the
    # unit, figures included — up to 815 MB a unit, the first walk past 27
    # minutes on the SSD the stages share): the zip opened from its file, so
    # only what is read below leaves the disk, its central directory, its text
    # members and its figures' first bytes. The same checks, the same answers;
    # an I/O error of the file is an OSError, as reading it whole raised.
    magic = {n: _magic(b) for n, b in files.items() if is_package(n)}
    out = Members({n: (b if isinstance(b, bytes) else _whole(b)) for n, b in files.items()
                   if magic.get(n) != _ZIP_MAGIC})
    came: dict[str, str] = {}
    for name in sorted(files):
        data = files[name]
        if magic.get(name) != _ZIP_MAGIC:
            continue
        is_path = not isinstance(data, bytes)
        try:
            with zipfile.ZipFile(os.fspath(data) if is_path else io.BytesIO(data)) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                # WHAT IS READ is bounded, not what is held (2026-11-22, München:
                # 27 EUR-Lex5 packages refused "over 1 GiB" were TIFF figures,
                # 1.09-9.77 GB, beside 59-942 KB of XML; the act's text was lost
                # to a guard against what the reader never reads)
                if sum(i.file_size for i in infos if _read_whole(i.filename)) > PACKAGE_MAX:
                    raise FormexError(f"{name}: its text members are over {PACKAGE_MAX} bytes")
                for i in infos:
                    if not _MEMBER_NAME.match(i.filename) or i.filename in (".", ".."):
                        raise FormexError(f"{name}: member {i.filename!r} is not a plain file name")
                    if _read_whole(i.filename):
                        got = z.read(i)            # its CRC-32 checked
                    else:
                        # a figure: its first bytes only, what the checks of a
                        # name against its content read (misnamed, root_tag);
                        # its size from the central directory
                        with z.open(i) as f:
                            got = f.read(_HEAD_BYTES)
                    held = out.get(i.filename)
                    if held is not None and held[:len(got)] != got[:len(held)]:
                        raise FormexError(f"{name}: member {i.filename} differs from the item of that name")
                    out[i.filename] = got
                    out.sizes[i.filename] = i.file_size
                    came[i.filename] = name
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as e:
            raise FormexError(f"{name}: not a readable zip: {e}") from None
        except OSError as e:
            if is_path:
                raise                   # the file did not read: as its read_bytes raised
            raise FormexError(f"{name}: not a readable zip: {e}") from None
    return out, came


def _magic(data) -> bytes:
    """A file's first four bytes, from its bytes or its path."""
    if isinstance(data, bytes):
        return data[:4]
    with open(data, "rb") as f:
        return f.read(4)


def _whole(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class Members(dict):
    """`unpack`'s view: name → bytes, a package's figures by their first
    bytes only; `sizes` each package member's true size (the central
    directory's), which a record's `items` states."""

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.sizes: dict[str, int] = {}

    def size(self, name: str) -> int:
        return self.sizes.get(name, len(self[name]))


#: the members the reader reads WHOLE: its text (`unpack`)
_TEXT_MEMBERS = (".xml",)


def _read_whole(name: str) -> bool:
    return name.lower().endswith(_TEXT_MEMBERS)


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
                _note(c, col)
        elif c.tag in _CHAR_MARKS:
            parts.append(_char(c))
        elif c.tag in ("TBL", "GR.TBL"):
            # a table inside a line (a CELL): its rows in order, once
            parts.append(" " + " / ".join(_rows(c, col)) + " ")
        else:
            words = inline(c, col)
            if c.tag.startswith("REF.") and col is not None:
                col.reference(c, words)
            parts.append(" " + words + " " if c.tag in _BLOCKS or c.tag in _SPACED
                         else words + " " if c.tag in _NUMBERS else words)
        parts.append(c.tail or "")
    return norm("".join(parts))


def _note(e: ET.Element, col: Collect) -> None:
    """A footnote: its words to the record's notes; the processing
    instructions inside it (a consolidation's marks) collected as anywhere."""
    for x in e.iter():
        if x.tag is ET.ProcessingInstruction:
            col.pi(x)
    col.note(e, inline(e, None))


def _rows(tbl: ET.Element, col: Collect | None) -> list[str]:
    """A table's lines in document order, each row once: its TITLE, each
    ROW's cells joined by " | ", a BLK's title (TI.BLK) before its rows, a
    nested table's rows in place, anything else as its words."""
    out: list[str] = []

    def walk(e: ET.Element) -> None:
        for c in e:
            if not isinstance(c.tag, str):
                if col is not None:
                    col.pi(c)
            elif c.tag == "ROW":
                cells = [inline(cell, col) for cell in c if isinstance(cell.tag, str) and cell.tag == "CELL"]
                for x in c:
                    if not isinstance(x.tag, str) and col is not None:
                        col.pi(x)
                if any(cells):
                    out.append(" | ".join(cells))
            elif c.tag in ("CORPUS", "BLK", "TBL", "GR.TBL"):
                walk(c)
            elif c.tag in ("TI.BLK", "STI.BLK", "TITLE"):
                t = heading(c, col) if c.tag == "TITLE" else inline(c, col)
                if t:
                    out.append(t)
            elif c.tag == "NOTE":
                if col is not None:
                    _note(c, col)
            else:
                t = inline(c, col)
                if t:
                    out.append(t)
    walk(tbl)
    return out


def lines(e: ET.Element, depth: int, col: Collect, lead: str = "") -> list[str]:
    """An element's text as indented lines (three spaces a level)."""
    pad = "   " * depth
    tag = e.tag
    if tag == "NOTE":
        _note(e, col)
        return []
    if tag in ("TBL", "GR.TBL"):
        # the words before the table first (2026-11-21: "the row for tariff
        # quota … is replaced by the following:" was dropped before its TBL)
        return ([pad + lead] if lead else []) + [pad + r for r in _rows(e, col)]
    if tag in ("LIST", "DLIST"):
        out = [pad + lead] if lead else []
        for item in e:
            if not isinstance(item.tag, str):
                col.pi(item)
            else:
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
            _note(c, col)
        elif c.tag in _CHAR_MARKS:
            buf.append(_char(c))
        elif c.tag in _BLOCKS or c.tag in ("TITLE", "TI", "STI"):
            text = norm("".join(buf))
            if c.tag in ("LIST", "DLIST"):
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
            buf.append(f" {words} " if c.tag in _SPACED else words)
        buf.append(c.tail or "")
    text = norm("".join(buf))
    if text:
        out.append(pad + text)
    return out


def heading(e: ET.Element | None, col: Collect | None = None) -> str:
    """A TITLE's words: its TI, then its STI, joined by " — "."""
    if e is None:
        return ""
    if col is not None:
        for c in e:                         # a consolidation's mark beside the title's words
            if c.tag is ET.ProcessingInstruction:
                col.pi(c)
    if e.tag in ("TI", "STI", "TI.ART", "STI.ART"):
        kids = [c for c in e if isinstance(c.tag, str)]
        if kids and all(c.tag == "P" for c in kids) and not norm(e.text) and not any(
                norm(c.tail) for c in e):
            return " ".join(norm(inline(p, col)) for p in kids)
        return inline(e, col)
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
def _act_of(celex: str) -> str:
    """The act a CELEX is of: a consolidation's (`02016R1036-20180608` →
    `32016R1036`) and a corrigendum's (`32019L1922R(01)` → `32019L1922`)
    base act, as a wrapper's NO.CELEX may name either."""
    c = celex.strip()
    if c[:1] == "0" and len(c) > 1:
        c = "3" + c[1:].split("-", 1)[0]
    return re.sub(r"R\(\d+\)$", "", c)


#: an original's CELEX (sector 3) and its year
_ORIGINAL_YEAR = re.compile(r"^3(\d{4})")
#: the year of the OJ a wrapper's PUBLICATION.REF names
#: the OJ a wrapper is of, by its OWN NAME — "L_1998326HR.01003501.doc.xml"
#: is OJ L 326 of 1998, "P_1959007HR.01018501.doc.xml" OJ P 7 of 1959 (a
#: Croatian Special Edition's file keeps the original OJ's name) — else its
#: PUBLICATION.REF's date, that element exactly (2026-11-22, München: the old
#: `PUBLICATION\.REF\b` also matched PUBLICATION.REF.SE and ran on to the
#: act's adoption DATE, 1958-12-22, refusing 31959R0005 and 31964D0732 hr)
_OJ_NAME_YEAR = re.compile(r"^[A-Z]{1,2}_(\d{4})\d{3}")
_PUB_YEAR = re.compile(rb"<PUBLICATION\.REF(?=[\s>])[^>]*>(?:(?!</PUBLICATION\.REF>).)*?"
                       rb"<DATE ISO=\"(\d{4})\d{4}\"", re.S)


def oj_year(name: str, data: bytes) -> int | None:
    """The year of the OJ a wrapper is of (`_OJ_NAME_YEAR`, `_PUB_YEAR`)."""
    m = _OJ_NAME_YEAR.match(name.rsplit("/", 1)[-1])
    if m:
        return int(m.group(1))
    m = _PUB_YEAR.search(data)
    return int(m.group(1)) if m else None


def own_wrapper(names, celex: str | None, read, listed=None, adopted: str | None = None) -> str:
    """The wrapper of THIS act's text among a manifestation's items: the only
    one, or — several (München, 2026-11-16: a Croatian Special Edition
    volume, one fmx4 manifestation whose items are the files of two acts
    sharing its FIN.ID, 1,241 Croatian works) — the one whose NO.CELEX is the
    act's own, and only when exactly one is. The other act's files stay items
    of the unit (the origin lists them under this manifestation: invariant
    11); only this one is read. `read(name)` gives a wrapper's bytes.

    `listed`: the names the expression's own tree lists for its Formex
    manifestation (a package's members counted as listed). The ORIGIN'S
    LISTING OUTRANKS A LABEL inside one of its files (2026-11-22, München:
    31958D1006(02) en, the decision printed a second time at OJ P 17/1958
    p. 419, its wrapper's NO.CELEX saying (01); Cellar lists these exact
    files as (02)'s): a lone wrapper the tree lists is this act's whatever
    its NO.CELEX names — the caller says the disagreement — and an OJ older
    than the act still refuses it.

    `adopted`: the act's own date as its tree states it (`date_document`).
    An OJ older than both it and the CELEX's year is another act's
    (2026-11-23, München: 32000D0007(01) hr,
    adopted and published in 1999, its CELEX of 2000, refused; 6 hr units
    holding 1998, 2002 and 2003 packages for acts of 2004-11, and 32014R1322
    holding a 2013 Syria corrigendum, refused rightly either way)."""
    wrappers = sorted(n for n in names if is_wrapper(n))
    if not wrappers:
        raise FormexError("no wrapper (….doc.xml) among the manifestation's items")
    if len(wrappers) == 1:
        # ONE wrapper is this act's unless it says otherwise (2026-11-21: three
        # hr acts of 2009–11 hold a 1998 act's package): one naming only other
        # acts as its NO.CELEX, or an original's published in an OJ older than
        # the act's own year (its CELEX's), is another act's
        w = wrappers[0]
        if celex:
            data = read(w)
            stated = {m.group(1).decode() for m in _NO_CELEX.finditer(data)}
            in_tree = listed is not None and w in listed
            if stated and _act_of(celex) not in {_act_of(x) for x in stated} and not in_tree:
                raise FormexError(f"the only wrapper {w} names {', '.join(sorted(stated))}, not {celex}")
            year = _ORIGINAL_YEAR.match(celex)
            # the EARLIEST date known of the act: an OJ older than every one
            # of them is another act's. A date_document a notice gives wrong
            # (later than the act) never refuses the act's own wrapper
            known = [int(year.group(1))] if year else []
            if adopted and adopted[:4].isdigit():
                known.append(int(adopted[:4]))
            act_year = min(known) if known else None
            pub = oj_year(w, data)
            if year and act_year is not None and pub is not None and pub < act_year:
                raise FormexError(f"the only wrapper {w} is of an OJ of {pub}, older than the act "
                                  f"{celex}" + (f" (of {adopted})" if adopted else ""))
        return w
    want = (celex or "").encode()
    own = [n for n in wrappers if want and want in {m.group(1) for m in _NO_CELEX.finditer(read(n))}]
    if len(own) != 1:
        raise FormexError(f"{len(wrappers)} wrappers among the items ({', '.join(wrappers)}), "
                          f"{'none' if not own else len(own)} naming {celex or 'the act'} as its NO.CELEX")
    return own[0]


def wrapper_labels(data: bytes) -> set[str]:
    """The NO.CELEX a wrapper states (`own_wrapper`)."""
    return {m.group(1).decode() for m in _NO_CELEX.finditer(data)}


def wrapper_of(files: dict[str, bytes], celex: str | None = None, listed=None,
               adopted: str | None = None) -> tuple[str, ET.Element]:
    name = own_wrapper(files, celex, files.__getitem__, listed, adopted)
    return name, _parse(files[name], name)


def _date_iso(e: ET.Element | None) -> str | None:
    if e is None:
        return None
    return iso(e.attrib.get("ISO")) or iso(norm(e.text))


def _recitals(preamble: ET.Element | None, col: Collect) -> tuple[list[tuple[str, str]], dict]:
    """The preamble: each recital a section ("Whereas (1)"), a divided
    group's headings (DIV.CONSID: "1. PROCEDURE › 1.1. Initiation") before
    its recitals' labels (2026-11-21: 32019R1286's 219 recitals were in no
    section); any other paragraph of the preamble (an agreement's
    "CONSIDERING that …" written as P) a "Preamble" section of its own. The
    citations (GR.VISA) and the opening and closing formulas stay beside
    the text, as trees."""
    if preamble is None:
        return [], {}
    meta: dict[str, Any] = {}
    out: list[tuple[str, str]] = []
    loose: list[str] = []

    def consids(e: ET.Element, init: str, heads: list[str]) -> None:
        for c in e:
            if not isinstance(c.tag, str):
                col.pi(c)
            elif c.tag == "GR.CONSID.INIT":
                init = inline(c, col)
                meta["consid_init"] = init
            elif c.tag == "CONSID":
                num = norm("".join(n.text or "" for n in c.iter("NO.P")))
                own = " ".join(x for x in (init.rstrip(":").strip(), num) if x) or "Recital"
                label = " › ".join([*heads, own])
                col.section = label
                out.append((label, "\n".join(lines(c, 0, col))))
            elif c.tag == "DIV.CONSID":
                t = c.find("TITLE")
                head = heading(t, col) if t is not None else ""
                consids([x for x in c if x is not t], init, heads + ([head] if head else []))
            elif c.tag != "TITLE":
                col.section = " › ".join(heads) or "Preamble"
                text = "\n".join(lines(c, 0, col))
                if text:
                    out.append((col.section, text))
    for child in preamble:
        if not isinstance(child.tag, str):
            col.pi(child)
        elif child.tag == "PREAMBLE.INIT":
            meta["init"] = inline(child, col)
        elif child.tag == "GR.VISA":
            col.section = "preamble"
            meta.setdefault("visas", []).extend(inline(v, col) for v in child if v.tag == "VISA")
        elif child.tag == "GR.CONSID":
            consids(child, "", [])
        elif child.tag == "PREAMBLE.FINAL":
            meta["final"] = inline(child, col)
        else:
            col.section = "Preamble"
            loose += lines(child, 0, col)
    if loose:
        out.insert(0, ("Preamble", "\n".join(x for x in loose if x.strip())))
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
    section each: labelled by where it applies — the first paragraph of its
    DESCRIPTION ("On page 7, Article 2 is replaced by the following:") —
    its text what follows, in the origin's own words: the replacement the
    DESCRIPTION quotes (2026-11-21: a real corrigendum's text is inside
    DESCRIPTION, a QUOT.S, a table; only OLD/NEW.CORR were read and every
    Formex corrigendum read empty), then OLD.CORR / NEW.CORR where it has
    them."""
    out: list[tuple[str, str]] = []
    for i, c in enumerate(root.iter("CORRECTION"), 1):
        label, body = "", []
        where = c.find("DESCRIPTION")
        if where is not None:
            kids = [k for k in where if isinstance(k.tag, str)]
            first = kids[0] if kids and kids[0].tag == "P" else None
            label = norm(inline(first, col)) if first is not None else ""
            col.section = label or f"Correction {i}"
            for k in where:
                if not isinstance(k.tag, str):
                    col.pi(k)
                elif k is not first:
                    body += lines(k, 0, col)
            if first is None and (where.text or "").strip():
                body.insert(0, norm(where.text))
        for part in c:
            if not isinstance(part.tag, str):
                col.pi(part)
            elif part.tag != "DESCRIPTION":
                body += lines(part, 0, col)
        text = "\n".join(x for x in body if x.strip())
        if not text and label:
            label, text = f"Correction {i}", label     # a description alone is the correction
        out.append((label or f"Correction {i}", text))
    if not out:
        out = _contents(root, col)
    return out


def _contents(root: ET.Element, col: Collect) -> list[tuple[str, str]]:
    """Any other body (GENERAL …): every part of it in order — its CONTENTS
    by group (a GR.SEQ's TITLE labels its text), and what stands outside a
    group, a PROLOG before the CONTENTS included (2026-11-21: 32010Q0702(03)
    it, the consolidation's PROLOG was in no section because only CONTENTS
    was read where one existed) — so no word is left out."""
    out: list[tuple[str, str]] = []
    loose: list[str] = []

    def take(holder: ET.Element) -> None:
        nonlocal loose
        for c in holder:
            if not isinstance(c.tag, str):
                col.pi(c)
                continue
            if c.tag in ("BIB.INSTANCE", "TITLE"):
                continue
            if c.tag == "CONTENTS":
                take(c)
            elif c.tag == "GR.SEQ":
                if loose:
                    out.append(("Contents", "\n".join(x for x in loose if x.strip())))
                    loose = []
                t = c.find("TITLE")
                label = norm(heading(t, col)) if t is not None else ""
                col.section = label or "Contents"
                body = [x for part in c if isinstance(part.tag, str) and part is not t for x in lines(part, 0, col)]
                for part in c:
                    if not isinstance(part.tag, str):
                        col.pi(part)
                out.append((label or "Contents", "\n".join(x for x in body if x.strip())))
            else:
                col.section = "Contents"
                loose += lines(c, 0, col)
    take(root)
    if loose:
        out.append(("Contents", "\n".join(x for x in loose if x.strip())))
    return out


def _rendered(own: list | None, recitals: list, articles: list, more: list, annexes: list,
              extra: list | None = None, tail: list | None = None) -> list | None:
    """The sections where parser 5 reads more than an act's (a body that is
    not an act, other main files, the body's own parts outside its known
    blocks, an annexed document that is not an ANNEX): None where an act and
    its ANNEX files alone were read, so `sections` reads it as before.
    `tail`: the annexes and annexed documents in the wrapper's order, where
    one of them is not an ANNEX."""
    if own is None and not more and not extra and tail is None:
        return None
    out = list(own) if own is not None else list(recitals) + [
        (" › ".join(x for x in (a.partition, " — ".join(y for y in (a.num, a.title) if y)) if x)
         or "Enacting terms", a.body) for a in articles]
    return (out + list(extra or []) + list(more)
            + (list(tail) if tail is not None else [(a.title or "Annex", a.body) for a in annexes]))


#: an act's parts read as its text, the rest beside it
_READ_PARTS = {"BIB.INSTANCE", "TITLE", "PREAMBLE", "ENACTING.TERMS", "FINAL", "CONS.ANNEX", "FAM.COMP"}


def _body_parts(doc: ET.Element, col: Collect) -> list[tuple[str, str]]:
    """An act's parts outside its title, preamble, enacting terms, final
    provisions and annexes, as sections in their order (2026-11-21: an
    agreement by exchange of letters, COLL.LETTERS, read as nothing; a
    draft's GR.ANNOTATION): each LETTER a section under its own title, any
    other part a section of its own."""
    out: list[tuple[str, str]] = []
    for c in doc:
        if not isinstance(c.tag, str):
            col.pi(c)
            continue
        if c.tag in _READ_PARTS:
            continue
        if c.tag == "COLL.LETTERS":
            for i, letter in enumerate((x for x in c if isinstance(x.tag, str)), 1):
                t = letter.find("TITLE")
                label = (heading(t, col) if t is not None else "") or f"Letter {i}"
                col.section = label
                body = [x for k in letter if isinstance(k.tag, str) and k is not t for x in lines(k, 0, col)]
                out.append((label, "\n".join(x for x in body if x.strip())))
            continue
        label = {"GR.ANNOTATION": "Annotation"}.get(c.tag, c.tag)
        col.section = label
        text = "\n".join(x for x in lines(c, 0, col) if x.strip())
        if text:
            out.append((label, text))
    return out


def _document_sections(root: ET.Element, col: Collect) -> list[tuple[str, str]]:
    """Any Formex document read as sections under its own title: an act or
    an agreement by its recitals, articles and other parts, a corrigendum by
    its corrections, anything else by its contents (2026-11-21: an annexed
    draft decision — an ACT — and annexed joint declarations — GENERAL —
    were kept only as trees)."""
    t = root.find("TITLE")
    title = heading(t, col) if t is not None else ""
    if root.tag == "CORR":
        secs = _corrections(root, col)
    elif root.tag in ("ACT", "AGR"):
        recs, _meta = _recitals(root.find("PREAMBLE"), col)
        arts = _articles(root.find("ENACTING.TERMS"), col)
        secs = recs + [(" › ".join(x for x in (a.partition, " — ".join(y for y in (a.num, a.title) if y)) if x)
                        or "Enacting terms", a.body) for a in arts] + _body_parts(root, col)
        secs += [(a.title or "Annex", a.body) for a in (_annex(x, None, col) for x in root.iterfind("CONS.ANNEX"))]
    else:
        secs = _contents(root, col)
    if not secs:
        return [(title or root.tag, "")]
    return [(" › ".join(x for x in (title, label) if x) or root.tag, text) for label, text in secs]


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


#: a member Cellar repaired under a doubled name: `<the name the wrapper uses>---<its old name>`
_DOUBLED = "---"


def doubled_names(files: dict[str, bytes], wanted: list[str]) -> tuple[dict[str, bytes], dict[str, str]]:
    """The files, with each name the wrapper wants and the items lack read
    from the ONE member named `<that name>---…` (2026-11-26, 32015D1735 hr:
    Cellar's corrected package holds the body as
    `L_2015252HR.01004901.xml---L_2015252HR.01005202.tif.xml` and the annex
    as `…01005201.xml---…01005203.tif.xml`, the wrapper naming
    `…01004901.xml` and `…01005201.xml`; the act's text is there, under its
    doubled name). Returns the files to read and `{wanted name: member}`.
    The held names are not changed: nothing is renamed in the unit. Two
    members of one prefix resolve nothing."""
    out = dict(files)
    aliases: dict[str, str] = {}
    for want in wanted:
        if want in files:
            continue
        hits = [n for n in files if n.startswith(want + _DOUBLED)]
        if len(hits) == 1:
            out[want] = files[hits[0]]
            aliases[want] = hits[0]
    return out, aliases


def parse(files: dict[str, bytes], celex: str | None = None, listed=None, adopted: str | None = None) -> Act:
    """The manifestation's items by name → the act (`celex`'s, where the
    items hold more than one act: `own_wrapper`). Raises FormexError when
    the wrapper, the body or an annex it names is missing or unreadable."""
    wname, wrapper = wrapper_of(files, celex, listed, adopted)
    files, aliases = doubled_names(files, [*main_names(wrapper), *annex_names(wrapper)])
    # A FILE UNDER ANOTHER'S NAME is never read as what its name says: the
    # record would be another file's text in its place, every check passing
    wrong = not_its_name(files, wrapper, wname)
    if wrong:
        raise FormexError(f"a file is not the one its name says ({'; '.join(wrong)}): the unit is "
                          f"fetched again from the origin")
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
            for x in fam.iter():           # its marks too: the tree keeps no processing instruction
                if x.tag is ET.ProcessingInstruction:
                    col.pi(x)
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
    extra = _body_parts(doc, col) if rendered is None else []
    if rendered is not None:
        for c in doc:
            if not isinstance(c.tag, str):
                col.pi(c)                  # a mark between its parts (`_body_parts` takes them otherwise)
    final = _final(doc.find("FINAL"), col)
    if final is not None:
        meta["final"] = final
    for c in doc:
        if isinstance(c.tag, str) and c.tag not in known:
            meta.setdefault("other", []).append({c.tag: tree(c)})
    annexes = [_annex(a, bname, col) for a in doc.iterfind("CONS.ANNEX")]
    n_cons = len(annexes)
    missing = [n for n in mains if n not in files]
    # the other main files, read as text after the body, in the wrapper's order
    more: list[tuple[str, str]] = []
    for name in held_mains:
        if name == bname:
            continue
        other = parsed[name]
        markup[name] = attributes_of(other)
        meta.setdefault("main_documents", []).append({"file": name, other.tag: tree(other)})
        more += _document_sections(other, col)
    tail: list[tuple[str, str]] = []
    subs = False
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
            tail += _document_sections(a, col)
            subs = True
            continue
        annexes.append(_annex(a, name, col))
        tail.append((annexes[-1].title or "Annex", annexes[-1].body))
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
    read |= set(aliases.values())     # a doubled member IS the body or annex it stands for, read once
    for name in sorted(files):
        if name not in read and name.lower().endswith(".xml"):
            meta.setdefault("other_items", {})[name] = tree(_parse(files[name], name))
    if aliases:
        meta["doubled_names"] = dict(sorted(aliases.items()))    # the name read -> the member held
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
               rendered_sections=_rendered(rendered, recitals, articles, more, annexes, extra,
                                           [*[(a.title or "Annex", a.body) for a in annexes[:n_cons]], *tail]
                                           if subs else None),
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
