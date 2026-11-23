"""Wikitext → the text a record's sections hold (docs/notes/
wikisource-design.md §3; invariant 11 (a): markup may go, words may not).
Part of the parser's version: a change here changes records (invariant 3).

The rules, each deterministic:
- text, bold and italic: their words;
- a link: its label, else its target; a category, file or interwiki link:
  nothing (the categories are the record's subjects, from the run's
  `categorylinks`);
- a template: nothing for the header, navigation and table-of-contents
  templates (`DROP`; the header is kept whole in `extra.header`); else its
  positional parameters, each rendered, joined by a space, but for one that
  is only a size, a number or a colour (`_OPTION`) — formatting templates
  carry their text there (`{{Centrato|CAPITOLO I}}`, `{{Sc|Dante}}`);
- `<ref>…</ref>`: a marker `[n]` where it stands, and the note's text after
  the section as `[n] …` — the author's notes are words of the work;
- `<br>`: a line break; any other tag: its contents; comments, arguments and
  `<references/>`: nothing.

Standard used: mwparserfromhell's node tree (the parser Wikipedia's plugin
uses), walked once.
"""
from __future__ import annotations

import html
import re

import mwparserfromhell
from mwparserfromhell.nodes import (Argument, Comment, ExternalLink, Heading, HTMLEntity, Tag, Template,
                                    Text, Wikilink)

#: templates that carry no words of the work (lowercased names)
DROP = frozenset({
    "intestazione", "header", "textdaten", "encabezado", "qualità", "qualita", "conteggio pagine",
    "indice sommario", "indice", "ts", "nop", "pagina", "pg", "rule", "gap", "dotted toc line", "toc",
    "defaultsort", "ordinamento", "authority control", "nav", "navigazione", "progetto", "sal",
    "textquality", "nopb", "pb",
})
_OPTION = re.compile(r"\A\s*(?:[\d.,]+\s*(?:%|em|px|pt)?|#[0-9a-f]{3,8}|[a-z]+=\S*)\s*\Z", re.I)
_SKIP_LINK = re.compile(r"\A\s*:?\s*(?:category|categoria|catégorie|kategorie|categoría|file|image|immagine|"
                        r"fichier|datei|archivo|[a-z]{2,3}(?:-[a-z]+)?)\s*:", re.I)
_LINES = re.compile(r"\n{3,}")


class _Notes:
    def __init__(self) -> None:
        self.items: list[str] = []


def _render(code, notes: _Notes) -> str:
    out: list[str] = []
    for node in code.nodes:
        if isinstance(node, Text):
            out.append(str(node.value))
        elif isinstance(node, (Comment, Argument)):
            continue
        elif isinstance(node, HTMLEntity):
            out.append(html.unescape(str(node)))
        elif isinstance(node, Heading):
            out.append("\n" + _render(node.title, notes).strip() + "\n")
        elif isinstance(node, Wikilink):
            title = str(node.title)
            if _SKIP_LINK.match(title) and not title.lstrip().startswith(":"):
                continue
            out.append(_render(node.text, notes) if node.text is not None else title.lstrip(":").split("#")[0])
        elif isinstance(node, ExternalLink):
            out.append(_render(node.title, notes) if node.title is not None else "")
        elif isinstance(node, Template):
            name = str(node.name).strip().lower().replace("_", " ")
            if name in DROP or name.startswith("#"):
                continue
            words = []
            for p in node.params:
                if p.showkey:
                    continue
                v = _render(p.value, notes).strip()
                if v and not _OPTION.match(v):
                    words.append(v)
            out.append(" ".join(words))
        elif isinstance(node, Tag):
            tag = str(node.tag).lower()
            if tag == "ref":
                notes.items.append(_render(node.contents, notes).strip() if node.contents is not None else "")
                out.append(f"[{len(notes.items)}]")
            elif tag in ("br",):
                out.append("\n")
            elif tag in ("references", "noinclude"):
                continue
            elif node.contents is not None:
                out.append(_render(node.contents, notes))
        else:
            out.append(str(node))
    return "".join(out)


def to_text(wikitext: str) -> str:
    """The section text of a page's (expanded) wikitext: the words, then the
    notes `[n] …` after them."""
    notes = _Notes()
    body = _render(mwparserfromhell.parse(wikitext), notes)
    lines = [ln.rstrip() for ln in body.split("\n")]
    text = _LINES.sub("\n\n", "\n".join(lines)).strip()
    if notes.items:
        text += "\n\n" + "\n".join(f"[{i}] {n}" for i, n in enumerate(notes.items, 1))
    return text
