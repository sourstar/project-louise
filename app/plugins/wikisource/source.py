"""WikisourceSource — CorpusSource over Wikisource's multistream dumps
(docs/notes/wikisource-design.md).

ONE COLLECTION, each language a slice (invariant 11: a language is a
structurally independent part, held and embedded or not, never a separate
collection). A record is a WORK: its main-namespace page, its subpages, and
the proofread scan pages they transclude (work.py, proofread.py); an
author's page (`Autore:`, `Author:`) is a record of its own kind.

THE ORIGIN FORM, and why — invariant 11 wants it recorded beside the plugin
(06 §6.4; the design note's §1, decided A). Wikimedia publishes each wiki as
XML dumps (plain and multistream), HTML dumps and the live API. Taken: the
dated MULTISTREAM `pages-articles-multistream.xml.bz2` of a pinned run, with
its index — the wikitext the origin versions by run, every namespace a work
reads (the main one, the scan pages', the indexes'), each page's
proofreading level and its header template as written. The transclusion of
scan pages is expanded by our parser from pages of the same dump, so the
text is the dump's own, versioned as the parser. Not taken: the HTML dumps
(pre-rendered and lossy, as Wikipedia's origin form found), the API (no
dated form a verifier can re-fetch; it is the per-unit path only for a
revision checked after its run is gone, §1), and our own OCR of the scans
(it would throw away the proofreading, which is the point of Wikisource).

A record names its pages, never bytes: `origin` is
`<wiki>/pages-articles-multistream.xml.bz2#<work page id>` (Wikipedia's
parser-6 form), read in the run the snapshot names; `extra.pages` lists
every page used in reading order with its revision id and the dump's
`sha1`; `origin_hash` is over those pages as ElementTree writes them
(Wikipedia's `page_bytes`), so every field the record keeps of a page is
covered.

Locator (this node's, in the manifest): `<dump path>::<work page id>`.
"""
from __future__ import annotations

import bz2
import hashlib
import json
from pathlib import Path

from app import derived
from app.backend import OriginForm, Template
from ... import chunking
from ..base import (CanonicalDoc, DiscoverState, DocRef, Erasure, Issue, LicenseClass, Modality, Mutability,
                    PROTOCOL_VERSION, PrivacyClass, RawDoc)
from . import dump as dump_mod, index as index_mod, proofread, render, work

#: the collection's languages, their order fixed for the life of its ids
LANGS = ("it", "en", "fr", "de", "es")
LANG_ORDINAL = {lang: i for i, lang in enumerate(LANGS, 1)}
#: a native id is the language's ordinal and the page id, `ordinal * ID_BASE + page`
ID_BASE = 10 ** 10
#: each wiki's author namespace, by its own name (read in <siteinfo>); de keeps
#: its authors in the main namespace and has none
AUTHOR_NS = {"it": "Autore", "en": "Author", "fr": "Auteur", "es": "Autor"}
_MARK = b"\0wikisource-page\0"
_NOT_AVAILABLE = "not available"


def native_id_of(lang: str, page_id: int) -> int:
    return LANG_ORDINAL[lang] * ID_BASE + int(page_id)


def decode_native_id(native_id: int) -> tuple[str, int]:
    n = int(native_id)
    return LANGS[n // ID_BASE - 1], n % ID_BASE


def lang_of_dump(name: str) -> str:
    """"itwikisource-20261101-…" → "it"."""
    wiki = name.split("-")[0]
    return wiki[: -len("wikisource")] if wiki.endswith("wikisource") else wiki


def split_locator(locator: str) -> tuple[Path, int]:
    path, _, page = locator.rpartition("::")
    return Path(path), int(page)


def _streams(dump: Path, rows: list[dict]) -> dict[int, dump_mod.Page]:
    """Every page `rows` names, each stream read once."""
    want: dict[tuple[int, int], set[int]] = {}
    for r in rows:
        want.setdefault((r["start"], r["end"]), set()).add(r["id"])
    out: dict[int, dump_mod.Page] = {}
    with open(dump, "rb") as f:
        for (start, end), ids in sorted(want.items()):
            f.seek(start)
            for p in dump_mod.pages_of_stream(bz2.decompress(f.read(end - start))):
                if p.id in ids:
                    out[p.id] = p
    return out


def frame(pages: list[dump_mod.Page]) -> bytes:
    """The pages a record is parsed from, in reading order, each as
    ElementTree writes it: what `origin_hash` is over."""
    return b"".join(_MARK + len(p.xml).to_bytes(8, "big") + p.xml for p in pages)


def unframe(data: bytes) -> list[dump_mod.Page]:
    import xml.etree.ElementTree as ET
    out, at = [], 0
    while at < len(data):
        if data[at:at + len(_MARK)] != _MARK:
            raise ValueError("not a Wikisource unit: its frame is broken")
        at += len(_MARK)
        n = int.from_bytes(data[at:at + 8], "big")
        at += 8
        out.append(dump_mod.page_of(ET.fromstring(data[at:at + n])))
        at += n
    return out


class WikisourceSource:
    source_id = "wikisource"
    license_class = LicenseClass.YELLOW        # CC BY-SA: attribution + share-alike (§5)
    corpus_provider = "Wikisource contributors"
    corpus_license = "CC-BY-SA-4.0"
    corpus_attribution = "Wikisource contributors, CC BY-SA 4.0"
    privacy_class = PrivacyClass.PUBLIC
    #: works are books, cut as gutenberg's are
    chunking_profile = "book"
    mutability = Mutability.MUTABLE            # pages are revised in place
    #: app/backend.py: a page is a member of a bz2 stream; the works index is a
    #: pack derived from the whole dump in one pass
    backend = (OriginForm("the pages-articles multistream dump", Template.MEMBER),
               OriginForm("the works index", Template.DUMP_PACK, ("wikisource-works",)))
    protocol_version = PROTOCOL_VERSION
    #: 1 (2026-11-23): the first; no collection exists
    PARSER_VERSION = 1
    #: 06 §6.2: every page's earlier revisions stay fetchable by revision id
    origin_versions = "history"
    modality = Modality.TEXT
    erasure = Erasure.RETRACTABLE
    native_id_order = "numeric"
    default_facets = [{"field": "lang", "kind": "flat", "separator": None, "authority": None}]
    default_layers = [
        {"chunker": {"id": chunking.CDC, "version": chunking.chunk_version_for(chunking.CDC),
                     "params": {"target": 4000, "min": 1000, "max": 8000}},
         "rule": "default"},
    ]
    ISSUES = {
        "pages-not-numbered": "a <pages> tag names an index whose pages are not numbered (image files)",
        "pages-without-index": "a <pages> tag names no index",
        "pages-attribute-unread": "a <pages> tag carries an attribute this parser does not read",
        "page-missing": "a scan page a work transcludes is not in the dump",
        "no-header": "the work page carries no header template",
    }

    # ---- discovery ----------------------------------------------------------

    def discover(self, state: DiscoverState):
        root = state.corpus_root
        if not root.is_dir():
            return
        from .. import walk
        dumps = walk.files(root, f"*{index_mod.DUMP_SUFFIX}")
        pin = getattr(state, "snapshot", None)
        if pin:
            dumps = [d for d in dumps if d.parent.name == pin]
        seen: set[str] = set()
        for dump in sorted(dumps):
            lang = lang_of_dump(dump.name)
            if lang not in LANG_ORDINAL:
                yield DocRef(self.source_id, dump.name, str(dump), scope="container",
                             error=f"{dump.name}: {lang!r} is not one of the collection's languages "
                                   f"({', '.join(LANGS)})")
                continue
            if lang in seen:
                yield DocRef(self.source_id, dump.name, str(dump), scope="container",
                             error=f"{dump.name}: a second {lang} dump in the corpus; pin one run")
                continue
            seen.add(lang)
            if not index_mod.index_of(dump).is_file():
                yield DocRef(self.source_id, dump.name, str(dump), scope="container",
                             error=f"multistream index missing for {dump.name}")
                continue
            try:
                works = index_mod.Works(index_mod.ensure(dump, log=print))
            except derived.Busy as e:
                yield DocRef(self.source_id, dump.name, str(dump), scope="container",
                             error=f"{e}: discovery resumes at the next scan")
                continue
            try:
                yield from self._refs(dump, lang, works)
            finally:
                works.close()

    def _refs(self, dump: Path, lang: str, works: index_mod.Works):
        for root_title, _pages in sorted(work.works(works.main_titles()).items()):
            row = works.page(0, root_title)
            yield DocRef(self.source_id, str(native_id_of(lang, row["id"])), f"{dump}::{row['id']}",
                         extra={"lang": lang, "kind": "work", "title": root_title})
        ns = works.ns_named(AUTHOR_NS.get(lang, "")) if AUTHOR_NS.get(lang) else None
        if ns is not None:
            for (pid, title) in works._con.execute(
                    "SELECT id, title FROM pages WHERE ns = ? AND redirect IS NULL ORDER BY title", (ns,)):
                yield DocRef(self.source_id, str(native_id_of(lang, pid)), f"{dump}::{pid}",
                             extra={"lang": lang, "kind": "author", "title": title})

    # ---- a unit -----------------------------------------------------------------

    def _unit(self, dump: Path, page_id: int) -> list[dump_mod.Page]:
        """The pages a record is parsed from, in reading order: the work's
        pages (work.order), then each scan page in the order they read it."""
        works = index_mod.Works(index_mod.ensure(dump))
        try:
            row = works.by_id(page_id)
            if row is None:
                raise RuntimeError(f"page {page_id} is not in {dump.name}")
            if row["ns"] != 0:                       # an author's page: itself
                return [_streams(dump, [row])[page_id]]
            titles = [row["title"]] + works.subpages(row["title"])
            rows = [works.page(0, t) for t in titles]
            got = _streams(dump, rows)
            by_title = {r["title"]: got[r["id"]] for r in rows if r["id"] in got}
            ordered = work.order(row["title"], list(by_title), lambda t: by_title[t].text)
            scan_ns, scan_name = _scan_namespace(works)
            scans: list[dict] = []
            seen: set[str] = set()
            for t in ordered:
                for tag in proofread.tags(by_title[t].text):
                    if not tag.index or scan_ns is None:
                        continue
                    try:
                        nums = proofread.numbers(tag, works.last_page(scan_ns, f"{scan_name}:{tag.index}"))
                    except ValueError:
                        continue
                    for n in nums:
                        title = f"{scan_name}:{tag.index}/{n}"
                        r = works.page(scan_ns, title)
                        if r is not None and title not in seen:
                            seen.add(title)
                            scans.append(r)
            scan_pages = _streams(dump, scans)
            return [by_title[t] for t in ordered] + [scan_pages[r["id"]] for r in scans if r["id"] in scan_pages]
        finally:
            works.close()

    def fetch(self, ref: DocRef) -> RawDoc:
        dump, page_id = split_locator(ref.locator)
        data = frame(self._unit(dump, page_id))
        return RawDoc(ref=ref, raw_hash=hashlib.blake2b(data, digest_size=16).hexdigest(), data=data)

    # ---- the record ---------------------------------------------------------------

    def parse(self, raw: RawDoc) -> CanonicalDoc:
        lang, page_id = decode_native_id(int(raw.ref.native_id))
        pages = unframe(raw.data)
        first = pages[0]
        uid = f"{self.source_id}:{raw.ref.native_id}"
        meta = [{"id": p.id, "ns": p.ns, "title": p.title, "rev_id": p.rev_id, "sha1": p.sha1,
                 "timestamp": p.timestamp, "editor": p.editor, "comment": p.comment,
                 **({"level": proofread.parts(p.text).level} if p.is_scan else {})} for p in pages]
        if first.ns != 0:
            return self._author(uid, lang, first, meta, raw.raw_hash)
        work_pages = [p for p in pages if p.ns == 0]
        scans = {p.title: p.text for p in pages if p.is_scan}
        scan_name = next((p.title.split(":", 1)[0] for p in pages if p.is_scan), "")
        last = {}
        for t in scans:
            idx, _, n = t.split(":", 1)[1].rpartition("/")
            if n.isdigit():
                last[idx] = max(last.get(idx, 0), int(n))
        issues: list[Issue] = []
        sections: list[tuple[str, str]] = []
        indexes: list[str] = []
        for p in work_pages:
            ex = proofread.expand(p.text, scan_name or "Page", scans.get, last.get)
            issues += [Issue(code, detail) for code, detail in ex.issues]
            indexes += [i for i in ex.indexes if i not in indexes]
            text = render.to_text(ex.text)
            if text:
                sections.append(("" if p.title == first.title else p.title[len(first.title) + 1:], text))
        head = work.header(first.text, lang)
        if head.template is None:
            issues.append(Issue("no-header", first.title))
        cats = sorted({c for p in work_pages for c in _categories(p.text)})
        return CanonicalDoc(
            uid=uid, title=head.fields.get("title") or first.title,
            creators=[head.fields["author"]] if head.fields.get("author") else [],
            language=lang, date=head.fields.get("year") or None, subjects=cats, keys=[],
            extra={"kind": "work", "page_title": first.title, "page_id": first.id,
                   "header": head.to_dict(), "header_fields": head.fields, "categories": cats,
                   "indexes": indexes, "pages": meta,
                   "proofread": _proofread_summary(meta)},
            sections=sections, raw_hash=raw.raw_hash, issues=issues)

    def _author(self, uid, lang, page, meta, raw_hash) -> CanonicalDoc:
        name = page.title.split(":", 1)[1] if ":" in page.title else page.title
        text = render.to_text(page.text)
        return CanonicalDoc(uid=uid, title=name, creators=[], language=lang, date=None,
                            subjects=sorted(_categories(page.text)), keys=[],
                            extra={"kind": "author", "page_title": page.title, "page_id": page.id,
                                   "pages": meta, "template": _author_template(page.text)},
                            sections=[("", text)] if text else [], raw_hash=raw_hash)

    def relations(self, doc: CanonicalDoc):
        return iter(())


def _scan_namespace(works: index_mod.Works) -> tuple[int | None, str]:
    """The wiki's scan-page namespace: the one whose pages carry ProofreadPage's
    `proofread-page` model (its name differs per wiki)."""
    row = works._con.execute("SELECT ns FROM pages WHERE model = ? LIMIT 1", (dump_mod.PAGE_MODEL,)).fetchone()
    if row is None:
        return None, ""
    return row[0], works.namespaces.get(row[0], "")


def _categories(text: str) -> list[str]:
    import re
    return [m.strip() for m in re.findall(r"\[\[\s*(?:Categoria|Category|Catégorie|Kategorie|Categoría)\s*:\s*"
                                          r"([^\]|]+)", text, re.I)]


def _author_template(text: str) -> dict | None:
    import mwparserfromhell
    for tpl in mwparserfromhell.parse(text).filter_templates(recursive=False):
        name = str(tpl.name).strip()
        if name.lower() in ("autore", "author", "auteur", "autor"):
            return {"template": name, "params": [[str(p.name).strip(), str(p.value).strip()] for p in tpl.params]}
    return None


def _proofread_summary(meta: list[dict]) -> dict:
    """How many of the work's scan pages are at each level (0–4), and whether
    every one is proofread (≥ 3): the selection's reading choice (§4)."""
    levels = [m["level"] for m in meta if "level" in m]
    counts = {str(k): levels.count(k) for k in sorted({lv for lv in levels if lv is not None})}
    return {"scan_pages": len(levels), "by_level": counts,
            "all_proofread": bool(levels) and all(lv is not None and lv >= 3 for lv in levels)}


def _json(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)
