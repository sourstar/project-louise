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


def body_name(wrapper: ET.Element) -> str:
    """The body's file, BY NAME: DOC.MAIN.PUB's REF.PHYS."""
    for ref in wrapper.iterfind("./FMX/DOC.MAIN.PUB/REF.PHYS"):
        name = ref.attrib.get("FILE")
        if name:
            return name
    raise FormexError("the wrapper names no DOC.MAIN.PUB/REF.PHYS file: no body to read")


def annex_names(wrapper: ET.Element) -> list[str]:
    out = []
    for sub in wrapper.iterfind("./FMX/DOC.SUB.PUB"):
        for ref in sub.iterfind("REF.PHYS"):
            if ref.attrib.get("FILE"):
                out.append(ref.attrib["FILE"])
    return out


def wrapper_of(files: dict[str, bytes]) -> tuple[str, ET.Element]:
    names = sorted(n for n in files if is_wrapper(n))
    if not names:
        raise FormexError("no wrapper (….doc.xml) among the manifestation's items")
    if len(names) > 1:
        raise FormexError(f"two wrappers among the items: {names}")
    return names[0], _parse(files[names[0]], names[0])


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


def parse(files: dict[str, bytes]) -> Act:
    """The manifestation's items by name → the act. Raises FormexError when
    the wrapper, the body or an annex it names is missing or unreadable."""
    wname, wrapper = wrapper_of(files)
    bname = body_name(wrapper)
    if bname not in files:
        raise FormexError(f"the wrapper names the body {bname}, which is not among the items")
    root = _parse(files[bname], bname)
    markup: dict[str, list] = {bname: attributes_of(root)}
    col = Collect()
    pub = wrapper.find("./PUBLICATION.REF/DATE")
    meta: dict[str, Any] = {}
    consleg: dict = {}
    base_celex = None
    if root.tag == "ACT":
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
        raise FormexError(f"{bname}: the body is a {root.tag}, not an ACT or a CONS.ACT")
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
    for name in annex_names(wrapper):
        if name not in files:
            raise FormexError(f"the wrapper names the annex {name}, which is not among the items")
        a = _parse(files[name], name)
        markup[name] = attributes_of(a)
        if a.tag != "ANNEX":
            meta.setdefault("sub_documents", []).append({"file": name, a.tag: tree(a)})
            continue
        annexes.append(_annex(a, name, col))
    # every other XML item the manifestation holds (the OJ issue's `toc`):
    # kept as a tree, never dropped; a non-XML item (an image) is held in
    # the unit and named in the record's `items`
    read = {wname, bname, *annex_names(wrapper)}
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
               wrapper=tree(wrapper), markup={k: v for k, v in markup.items() if v})


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
