"""What a Wikisource work is, and in which order its pages are read
(docs/notes/wikisource-design.md §3). Part of the parser's version: a
change to any rule here changes records (invariant 3).

- A WORK is a main-namespace page with no "/" in its title, with every
  subpage (`Title/…`) under it. A subpage whose work page is missing makes a
  work of its own, rooted at its shallowest ancestor the dump holds.
- ITS ORDER is depth first from the work page. A page's children are read in
  the order its own text first mentions them — a link `[[/Chapter]]` or
  `[[Title/Chapter]]`, or a template parameter `=Chapter` (it.wikisource's
  `{{Indice sommario|nome=Chapter}}`) — then the ones it does not mention, by
  title (code points). A page's children are the pages whose nearest
  ancestor in the dump it is.
- ITS FIELDS come from the work page's header template (it `Intestazione`,
  en `header`, de `Textdaten`, es `Encabezado`); the whole call is kept, every
  parameter as written, beside the few fields read from it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import mwparserfromhell


def norm(title: str) -> str:
    """A title as MediaWiki stores it: underscores are spaces."""
    return title.replace("_", " ").strip()


def works(titles: list[str]) -> dict[str, list[str]]:
    """`{work title: [every page of the work, the work's own first]}` for the
    main-namespace titles of a dump (redirects already out). The pages after
    the first are in no particular order: `order` reads them."""
    held = set(titles)
    out: dict[str, list[str]] = {}
    for t in sorted(held):
        segs = t.split("/")
        root = next(("/".join(segs[:i]) for i in range(1, len(segs) + 1) if "/".join(segs[:i]) in held), t)
        out.setdefault(root, [root])
        if t != root:
            out[root].append(t)
    return out


def _parent(title: str, held: set[str], root: str) -> str:
    segs = title.split("/")
    for i in range(len(segs) - 1, 0, -1):
        p = "/".join(segs[:i])
        if p in held:
            return p
    return root


def _first_mention(text: str, parent: str, child: str) -> int | None:
    rel = child[len(parent) + 1:]
    t = norm(text)
    pats = [r"\[\[\s*(?:" + re.escape(child) + r"|/" + re.escape(rel) + r")\s*(?:[|\]#]|/\s*\])",
            r"=\s*" + re.escape(rel) + r"\s*(?:[|}\n])"]
    found = [m.start() for p in pats for m in [re.search(p, t)] if m]
    return min(found) if found else None


def order(root: str, pages: list[str], text_of) -> list[str]:
    """The work's pages in reading order (the rule above); `text_of(title)`
    gives a page's wikitext."""
    held = set(pages)
    children: dict[str, list[str]] = {p: [] for p in pages}
    for p in pages:
        if p != root:
            children[_parent(p, held, root)].append(p)
    out: list[str] = []

    def walk(node: str) -> None:
        out.append(node)
        text = text_of(node) or ""
        kids = children[node]
        named = sorted((pos, c) for c in kids if (pos := _first_mention(text, node, c)) is not None)
        rest = sorted(set(kids) - {c for _, c in named})
        for _, c in named:
            walk(c)
        for c in rest:
            walk(c)
    walk(root)
    return out


#: each language's header template (lowercased) and the fields read from it
HEADERS: dict[str, tuple[str, dict[str, str]]] = {
    "it": ("intestazione", {"Titolo": "title", "Nome e cognome dell'autore": "author",
                            "Anno di pubblicazione": "year", "Nome e cognome del traduttore": "translator",
                            "Lingua originale del testo": "original_language"}),
    "en": ("header", {"title": "title", "author": "author", "year": "year", "translator": "translator"}),
    "de": ("textdaten", {"TITEL": "title", "AUTOR": "author", "ERSCHEINUNGSJAHR": "year",
                         "ÜBERSETZER": "translator"}),
    "es": ("encabezado", {"titulo": "title", "autor": "author", "año": "year", "traductor": "translator"}),
}


@dataclass(frozen=True)
class Header:
    template: str | None
    #: every parameter as written, in order
    params: list[tuple[str, str]] = field(default_factory=list)
    #: title, author, year, translator… where the template gives them
    fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"template": self.template, "params": [[k, v] for k, v in self.params]}


def header(wikitext: str, lang: str) -> Header:
    """The work page's header template: its every parameter and the fields
    read from it. No such template: an empty Header (counted by the build)."""
    want = HEADERS.get(lang)
    if want is None:
        return Header(None)
    name, mapping = want
    code = mwparserfromhell.parse(wikitext)
    for tpl in code.filter_templates(recursive=False):
        if str(tpl.name).strip().lower().replace("_", " ") != name:
            continue
        params = [(str(p.name).strip(), str(p.value).strip()) for p in tpl.params]
        lower = {k.lower(): k for k in mapping}
        fields = {mapping[lower[k.lower()]]: v for k, v in params if k.lower() in lower and v}
        return Header(str(tpl.name).strip(), params, fields)
    return Header(None)
