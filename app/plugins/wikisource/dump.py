"""The pages of a MediaWiki XML dump, as Wikisource's records read them.

A page is read with every field the record names it by (docs/notes/
wikisource-design.md §1): its id, namespace, title, the revision id, the
dump's own `<sha1>` of the revision text (base 36, MediaWiki's), its
content model — ProofreadPage's `proofread-page` and `proofread-index` say
which pages are scan pages and indexes on every wiki, whatever the
namespace is called there — and its text. A redirect is read as one, never
as text.

Standard used: the MediaWiki export format (export-0.11.xsd), read with the
standard library's ElementTree; the namespace URI is taken from the
document, so 0.10 and 0.11 dumps read alike.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterator

#: ProofreadPage's content models (extension.json): a scan page, an index
PAGE_MODEL = "proofread-page"
INDEX_MODEL = "proofread-index"


@dataclass(frozen=True)
class Page:
    id: int
    ns: int
    title: str
    rev_id: int
    sha1: str            # the dump's <sha1>: base-36 SHA-1 of the revision text
    model: str
    text: str
    redirect: str | None = None

    @property
    def is_scan(self) -> bool:
        return self.model == PAGE_MODEL

    @property
    def is_index(self) -> bool:
        return self.model == INDEX_MODEL


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name: str):
    return next((c for c in el if _local(c.tag) == name), None)


def page_of(el) -> Page:
    """A `<page>` element as a Page (its last revision: a pages-articles dump
    holds one)."""
    rev = [c for c in el if _local(c.tag) == "revision"]
    if not rev:
        raise ValueError(f"page {(_child(el, 'title').text or '')!r} has no revision")
    r = rev[-1]
    text_el = _child(r, "text")
    redirect = _child(el, "redirect")
    return Page(id=int(_child(el, "id").text), ns=int(_child(el, "ns").text),
                title=_child(el, "title").text or "",
                rev_id=int(_child(r, "id").text),
                sha1=(_child(r, "sha1").text or "").strip(),
                model=(_child(r, "model").text or "wikitext").strip() if _child(r, "model") is not None
                else "wikitext",
                text=(text_el.text or "") if text_el is not None else "",
                redirect=redirect.get("title") if redirect is not None else None)


_OPEN = re.compile(rb"<mediawiki\b[^>]*>")


def pages_of_stream(data: bytes) -> Iterator[Page]:
    """Every page of one decompressed multistream stream: a run of `<page>`
    elements with no root (the dump's first stream is its header and holds
    none), parsed inside a synthetic root."""
    body = _OPEN.sub(b"", data).replace(b"</mediawiki>", b"")
    start = body.find(b"<page>")
    if start < 0:
        return
    root = ET.fromstring(b"<pages>" + body[start:] + b"</pages>")
    for el in root:
        if _local(el.tag) == "page":
            yield page_of(el)


_NAMESPACE = re.compile(r'<namespace key="(-?\d+)"[^>]*?(?:/>|>([^<]*)</namespace>)')


def namespaces(header: str) -> dict[int, str]:
    """`{key: name}` of the wiki's namespaces, from the dump's `<siteinfo>`
    (its own names: "Autore", "Author", "Auteur"…); the articles' is ""."""
    return {int(k): (v or "") for k, v in _NAMESPACE.findall(header)}
