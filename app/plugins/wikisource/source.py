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

THE SAME RUN'S TABLES (design note §2, bulk first; tables.py): what the
origin states about a page beside its text comes from the run's own
`categorylinks` (with `linktarget`, which names each category since
MediaWiki 1.45) and `page_props` SQL dumps, never from the page's wikitext
(a category a template adds is in the table and not in the text) and never
from another collection. For EVERY page a record reads — the work's page,
its subpages and the scan pages they transclude — `extra.pages` keeps its
category links (each row as the table states it) and its properties. The
WORK's categories are its own pages' — the work page's and its subpages':
a subpage is part of the work (a chapter, a poem), so what the origin
files it under is filed about the work, and the old wikitext reading took
them too; a scan page's (ProofreadPage's quality categories among them)
stay in its `extra.pages` entry. They are the record's `subjects` and
`extra.categories`, each name once, as MediaWiki shows it (the title with
spaces), sorted. The work page's Wikidata item is `extra.wikidata` and the
key `wikidata:Q…`; its other properties are `extra.page_props` (Wikipedia's
parser 5). An author's record takes its page's alike.

A record names its page, never bytes: `origin` is the work's (or the
author's) page id in the run-free name of each of the four files its unit
is read from — `<wiki>/pages-articles-multistream.xml.bz2#<page id>`, then
the run's page_props, categorylinks and linktarget alike, joined by `|`
(locate.py; Wikipedia's parser-6 form) — read in the run the snapshot names
(`origin_key`, app/keyed_origin.py); `extra.pages` lists every page used in
reading order with its revision id and the dump's `sha1`; `origin_hash` is
over those pages as ElementTree writes them (Wikipedia's `page_bytes`), then
each page's rows of page_props and categorylinks and the linktarget rows
they name, as the decompressed tables hold them (`frame`) — so every field
the record keeps of a page is covered. `records-vs-origin` resolves a
record in that run under an origin base, fetches and parses it again, and
compares the whole line (app/hold/check.py). A base to read is a download
of the run: no reader of this origin by Range exists yet, nor one of a
revision by its id once the run is gone (design note §1).

Locator (this node's, in the manifest): `<dump path>::<work page id>`; the
discovery signature covers the dump and the run's three tables.
"""
from __future__ import annotations

import bz2
import hashlib
import json
import re
from pathlib import Path

from app import derived
from app.backend import OriginForm, Template
from app.keyed_origin import KeyedOrigin
from ... import chunking
from ..base import (CanonicalDoc, DiscoverState, DocRef, Erasure, Issue, LicenseClass, Modality, Mutability,
                    PROTOCOL_VERSION, PrivacyClass, RawDoc)
from ..wikipedia import page_props as pp
from . import (dump as dump_mod, index as index_mod, locate, proofread, render, tables as tables_mod,
               work)

#: the collection's languages, their order fixed for the life of its ids
LANGS = ("it", "en", "fr", "de", "es")
LANG_ORDINAL = {lang: i for i, lang in enumerate(LANGS, 1)}
#: a native id is the language's ordinal and the page id, `ordinal * ID_BASE + page`
ID_BASE = 10 ** 10
#: each wiki's author namespace, by its own name (read in <siteinfo>); de keeps
#: its authors in the main namespace and has none
AUTHOR_NS = {"it": "Autore", "en": "Author", "fr": "Auteur", "es": "Autor"}
_MARK = b"\0wikisource-page\0"
#: a page's rows of a table, after the pages (`frame`); the tables' bytes
#: hold no NUL (mysqldump escapes it), nor does an XML page
_PROPS_MARK = b"\0page_props\0"
_LINKS_MARK = b"\0categorylinks\0"
_TARGET_MARK = b"\0linktarget\0"
_MARKS = (_MARK, _PROPS_MARK, _LINKS_MARK, _TARGET_MARK)
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


def _streams(dump: Path, rows: list[dict], read: set | None = None) -> dict[int, dump_mod.Page]:
    """Every page `rows` names, each stream read once. `read`: the streams
    read are added to it, `(start, end)` (`origin_ranges`)."""
    want: dict[tuple[int, int], set[int]] = {}
    for r in rows:
        want.setdefault((r["start"], r["end"]), set()).add(r["id"])
    if read is not None:
        read.update(want)
    out: dict[int, dump_mod.Page] = {}
    with open(dump, "rb") as f:
        for (start, end), ids in sorted(want.items()):
            f.seek(start)
            for p in dump_mod.pages_of_stream(bz2.decompress(f.read(end - start))):
                if p.id in ids:
                    out[p.id] = p
    return out


def _frame(mark: bytes, payload: bytes) -> bytes:
    return mark + len(payload).to_bytes(8, "big") + payload


def frame(pages: list[dump_mod.Page], rows: tables_mod.PageRows | None = None) -> bytes:
    """What `origin_hash` is over: the pages a record is parsed from, in
    reading order, each as ElementTree writes it; then, page by page in the
    same order, its page_props unit and its categorylinks unit where it has
    rows; then each linktarget row those links name, in the order first
    named. Every piece is its mark and its length (8 bytes, big-endian)."""
    out = [_frame(_MARK, p.xml) for p in pages]
    if rows is not None:
        for p in pages:
            if p.id in rows.props:
                out.append(_frame(_PROPS_MARK, rows.props[p.id]))
            if p.id in rows.links:
                out.append(_frame(_LINKS_MARK, rows.links[p.id]))
        out += [_frame(_TARGET_MARK, t) for t in rows.targets]
    return b"".join(out)


def unframe(data: bytes) -> tuple[list[dump_mod.Page], tables_mod.PageRows]:
    """`frame` read back: the pages, and their rows of the tables."""
    import xml.etree.ElementTree as ET
    pages: list[dump_mod.Page] = []
    rows = tables_mod.PageRows({}, {}, [])
    at = 0
    while at < len(data):
        mark = next((m for m in _MARKS if data.startswith(m, at)), None)
        if mark is None:
            raise ValueError("not a Wikisource unit: its frame is broken")
        at += len(mark)
        n = int.from_bytes(data[at:at + 8], "big")
        at += 8
        payload = data[at:at + n]
        if len(payload) != n:
            raise ValueError(f"a piece of {len(payload)} bytes, {n} declared")
        at += n
        if mark is _MARK:
            pages.append(dump_mod.page_of(ET.fromstring(payload)))
        elif mark is _TARGET_MARK:
            rows.targets.append(payload)
        else:
            (rows.props if mark is _PROPS_MARK else rows.links)[tables_mod.page_of_unit(payload)] = payload
    held = {p.id for p in pages}
    stray = sorted((set(rows.props) | set(rows.links)) - held)
    if stray:
        raise ValueError(f"table rows of pages {stray} the unit does not read")
    return pages, rows


def _signature(files) -> tuple[int, int]:
    """Discovery's (mtime, size) of a run: the dump's and its tables' — a
    record holds rows of each, so a new file must rebuild it
    (tests/origin_coverage.py)."""
    sts = [Path(f).stat() for f in files]
    return int(max(st.st_mtime for st in sts)), sum(st.st_size for st in sts)


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
    #: pack derived from the whole dump in one pass; each of the run's SQL
    #: tables is a gzip read from its pack (tables.py)
    backend = (OriginForm("the pages-articles multistream dump", Template.MEMBER),
               OriginForm("the works index", Template.DUMP_PACK, ("wikisource-works",)),
               OriginForm("the categorylinks SQL dump", Template.CONTAINER_PACK, ("wikisource-categorylinks",)),
               OriginForm("the linktarget SQL dump", Template.CONTAINER_PACK, ("wikisource-linktarget",)),
               OriginForm("the page_props SQL dump", Template.CONTAINER_PACK, ("wikisource-page-props",)))
    protocol_version = PROTOCOL_VERSION
    #: 1 (2026-11-23): the first; no collection exists (so the run's tables,
    #: categories and page properties, joined it without a bump)
    PARSER_VERSION = 1
    #: 06 §6.2: every page's earlier revisions stay fetchable by revision id
    origin_versions = "history"
    #: A RECORD'S ORIGIN NAMES ITS PAGE, resolved in the snapshot's run
    #: (app/keyed_origin.py; locate.py). The unit — the work's subpages, the
    #: scan pages it transcludes — is found through the works index the node
    #: builds of the run's dump, as discovery found it: `built`
    origin_key = KeyedOrigin(key="the record's page id (a work's page, an author's)",
                             index="the run's multistream index, and the works index the node builds of its dump",
                             built=True, since=1)
    origin_names_run = False
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
        "category-target-missing": "a category link names a target the run's linktarget does not hold as a "
                                   "category (the tables are dumped at different moments)",
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
            # THE RUN'S TABLES: a run without one of them waits — deferred,
            # never an error on its documents (its records cannot be whole yet)
            run_tables = tables_mod.tables_of(dump)
            missing = [p.name for p in run_tables.files() if not p.is_file()]
            if missing:
                yield DocRef(self.source_id, dump.name, str(dump), scope="container",
                             error=f"{', '.join(missing)} missing: {dump.name}'s works wait for the run's tables")
                continue
            try:
                tables_mod.ensure(run_tables)
            except Exception as e:  # noqa: BLE001 — a table that cannot be read: one report, never a dead scan
                yield DocRef(self.source_id, dump.name, str(dump), scope="container",
                             error=f"{dump.name}: the run's tables cannot be read "
                                   f"({type(e).__name__}: {e})"[:300])
                continue
            sig = _signature((dump, *run_tables.files()))
            try:
                works = index_mod.Works(index_mod.ensure(dump, log=print))
            except derived.Busy as e:
                yield DocRef(self.source_id, dump.name, str(dump), scope="container",
                             error=f"{e}: discovery resumes at the next scan")
                continue
            try:
                yield from self._refs(dump, lang, works, sig)
            finally:
                works.close()

    def _refs(self, dump: Path, lang: str, works: index_mod.Works, sig: tuple[int, int]):
        for root_title, _pages in sorted(work.works(works.main_titles()).items()):
            row = works.page(0, root_title)
            yield DocRef(self.source_id, str(native_id_of(lang, row["id"])), f"{dump}::{row['id']}",
                         mtime=sig[0], size=sig[1], extra={"lang": lang, "kind": "work", "title": root_title})
        ns = works.ns_named(AUTHOR_NS.get(lang, "")) if AUTHOR_NS.get(lang) else None
        if ns is not None:
            for (pid, title) in works._con.execute(
                    "SELECT id, title FROM pages WHERE ns = ? AND redirect IS NULL ORDER BY title", (ns,)):
                yield DocRef(self.source_id, str(native_id_of(lang, pid)), f"{dump}::{pid}",
                             mtime=sig[0], size=sig[1], extra={"lang": lang, "kind": "author", "title": title})

    # ---- a unit -----------------------------------------------------------------

    def _unit(self, dump: Path, page_id: int, read: set | None = None) -> list[dump_mod.Page]:
        """The pages a record is parsed from, in reading order: the work's
        pages (work.order), then each scan page in the order they read it.
        `read`: the dump's streams read for it are added, `(start, end)`."""
        works = index_mod.Works(index_mod.ensure(dump))
        try:
            row = works.by_id(page_id)
            if row is None:
                raise RuntimeError(f"page {page_id} is not in {dump.name}")
            if row["ns"] != 0:                       # an author's page: itself
                return [_streams(dump, [row], read)[page_id]]
            titles = [row["title"]] + works.subpages(row["title"])
            rows = [works.page(0, t) for t in titles]
            got = _streams(dump, rows, read)
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
            scan_pages = _streams(dump, scans, read)
            return [by_title[t] for t in ordered] + [scan_pages[r["id"]] for r in scans if r["id"] in scan_pages]
        finally:
            works.close()

    def fetch(self, ref: DocRef) -> RawDoc:
        """The unit's pages, then their rows of the run's tables (`frame`)."""
        dump, page_id = split_locator(ref.locator)
        pages = self._unit(dump, page_id)
        rows = tables_mod.rows_for(tables_mod.tables_of(dump), [p.id for p in pages])
        data = frame(pages, rows)
        return RawDoc(ref=ref, raw_hash=hashlib.blake2b(data, digest_size=16).hexdigest(), data=data)

    # ---- the record's origin, by page (app/keyed_origin.py; locate.py) ------------

    def origin_of(self, locator: str, native_id, base) -> str:
        """09 §9.5's `origin` for a manifest row: its page in the run-free
        names of the dump and the run's three tables — a pure function of
        the document's id and its locator's wiki."""
        from app import plugin_io
        if native_id is None:
            raise ValueError("a wikisource origin names its page: the record's native id is needed beside "
                             "its locator")
        dump, page_id = split_locator(locator)
        if plugin_io.relative_to_base(dump, Path(base)) is None:
            raise plugin_io.OriginOutsideBase(f"{dump} is not under the origin base {base}")
        lang, pid = decode_native_id(int(native_id))
        wiki = dump.name.split("-")[0]
        if pid != page_id or wiki != f"{lang}wikisource":
            raise ValueError(f"{locator}: the locator names page {page_id} of {wiki}, the record is page "
                             f"{pid} of {lang}wikisource")
        return locate.origin(wiki, pid)

    def local_of(self, origin: str, base, run: str | None) -> tuple[Path, str]:
        """`origin` read back as the manifest locator discovery writes
        (`<dump of the run>::<page id>`) under `base`: what `fetch` reads.
        A run whose dump is here and does not hold the page is said
        (`PageNotInRun`); one whose dump is not here is the caller's to find
        missing."""
        wiki, pid = locate.read(origin)
        dump = locate.run_file(Path(base), wiki, locate.need_run(run, origin), locate.DUMP_UNIT)
        if dump.is_file():
            works = index_mod.Works(index_mod.ensure(dump))
            try:
                if works.by_id(pid) is None:
                    raise locate.PageNotInRun(f"{origin}: run {run}'s dump does not hold page {pid}")
            finally:
                works.close()
        return dump, f"{dump}::{pid}"

    def origin_files(self, origin: str, base, run: str | None) -> list[Path]:
        """The files `origin`'s unit is read from in `run`: the dump and the
        run's three tables. No index is read (records-vs-origin's bound)."""
        wiki, _pid = locate.read(origin)
        run_ = locate.need_run(run, origin)
        return [locate.run_file(Path(base), wiki, run_, unit) for unit in locate.UNITS]

    def origin_ranges(self, origin: str, base, run: str | None):
        """What `origin`'s unit is read from in the copy at `base`, for an
        origin compared by Range (app/acquire/range_origin.py;
        plugin_io.OriginRanges): the run's three tables whole, and of the
        dump the streams of the pages `_unit` reads — found, when asked, by
        reading them in the copy, through its index of the run."""
        from app import plugin_io
        wiki, pid = locate.read(origin)
        run_ = locate.need_run(run, origin)
        dump = locate.run_file(Path(base), wiki, run_, locate.DUMP_UNIT)
        tables = tuple(locate.run_file(Path(base), wiki, run_, unit) for unit in locate.TABLE_UNITS)

        def ranges() -> list[tuple[Path, int, int]]:
            if not dump.is_file():
                raise FileNotFoundError(str(dump))
            read: set[tuple[int, int]] = set()
            try:
                self._unit(dump, pid, read)
            except RuntimeError as e:        # the page is not in this run's dump
                raise locate.PageNotInRun(f"{origin}: {e}") from None
            return [(dump, start, end) for start, end in sorted(read)]
        return plugin_io.OriginRanges(tables, ranges, ("dump", wiki))

    def run_of_locator(self, locator: str) -> str | None:
        return locate.run_of_locator(locator)

    def carried_origin(self, origin: str, native_id, version: int) -> str | None:
        """No parser before this one wrote a record: a line is carried only
        from itself."""
        return origin if int(version) == self.PARSER_VERSION else None

    # ---- the record ---------------------------------------------------------------

    def parse(self, raw: RawDoc) -> CanonicalDoc:
        lang, page_id = decode_native_id(int(raw.ref.native_id))
        pages, rows = unframe(raw.data)
        first = pages[0]
        uid = f"{self.source_id}:{raw.ref.native_id}"
        titles = tables_mod.titles_of(rows.targets)
        links = {pid: tables_mod.category_rows(u, titles) for pid, u in rows.links.items()}
        props = {pid: tables_mod.props_of(u) for pid, u in rows.props.items()}
        issues: list[Issue] = [Issue("category-target-missing", f"target {t}") for t in sorted(
            {r["target_id"] for rs in links.values() for r in rs if "title" not in r})]
        meta = [{"id": p.id, "ns": p.ns, "title": p.title, "rev_id": p.rev_id, "sha1": p.sha1,
                 "timestamp": p.timestamp, "editor": p.editor, "comment": p.comment,
                 **({"level": proofread.parts(p.text).level} if p.is_scan else {}),
                 **({"categories": links[p.id]} if p.id in links else {}),
                 **({"page_props": props[p.id]} if p.id in props else {})} for p in pages]
        # the record's page's properties, as Wikipedia's parser 5 holds them:
        # `wikidata` (its `wikibase_item`) and `page_props` (every other one)
        own = pp.fields_of(rows.props.get(first.id))
        if first.ns != 0:
            return self._author(uid, lang, first, meta, raw.raw_hash, _names(links.get(first.id, [])), own,
                                issues)
        work_pages = [p for p in pages if p.ns == 0]
        scans = {p.title: p.text for p in pages if p.is_scan}
        scan_name = next((p.title.split(":", 1)[0] for p in pages if p.is_scan), "")
        last = {}
        for t in scans:
            idx, _, n = t.split(":", 1)[1].rpartition("/")
            if n.isdigit():
                last[idx] = max(last.get(idx, 0), int(n))
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
        cats = _names([r for p in work_pages for r in links.get(p.id, [])])
        return CanonicalDoc(
            uid=uid, title=head.fields.get("title") or first.title,
            creators=[head.fields["author"]] if head.fields.get("author") else [],
            language=lang, date=head.fields.get("year") or None, subjects=cats, keys=_keys(own),
            extra={"kind": "work", "page_title": first.title, "page_id": first.id,
                   "header": head.to_dict(), "header_fields": head.fields, "categories": cats,
                   "indexes": indexes, "pages": meta,
                   "proofread": _proofread_summary(meta), **own},
            sections=sections, raw_hash=raw.raw_hash, issues=issues)

    def _author(self, uid, lang, page, meta, raw_hash, cats, own, issues) -> CanonicalDoc:
        name = page.title.split(":", 1)[1] if ":" in page.title else page.title
        text = render.to_text(page.text)
        return CanonicalDoc(uid=uid, title=name, creators=[], language=lang, date=None,
                            subjects=cats, keys=_keys(own),
                            extra={"kind": "author", "page_title": page.title, "page_id": page.id,
                                   "pages": meta, "template": _author_template(page.text),
                                   "categories": cats, **own},
                            sections=[("", text)] if text else [], raw_hash=raw_hash, issues=issues)

    def relations(self, doc: CanonicalDoc):
        return iter(())


def _scan_namespace(works: index_mod.Works) -> tuple[int | None, str]:
    """The wiki's scan-page namespace: the one whose pages carry ProofreadPage's
    `proofread-page` model (its name differs per wiki)."""
    row = works._con.execute("SELECT ns FROM pages WHERE model = ? LIMIT 1", (dump_mod.PAGE_MODEL,)).fetchone()
    if row is None:
        return None, ""
    return row[0], works.namespaces.get(row[0], "")


def _names(links: list[dict]) -> list[str]:
    """The categories of these category-link rows, each once, as MediaWiki
    shows them, sorted: what `subjects` and `extra.categories` hold."""
    return sorted({n for r in links if (n := tables_mod.category_name(r.get("title"))) is not None})


def _keys(own: dict) -> list[str]:
    """`wikidata:Q…` for the page's Wikidata item (09 §9.5 `keys`, 06 §6.5)."""
    qid = own.get("wikidata")
    return [f"wikidata:{qid}"] if isinstance(qid, str) and re.fullmatch(r"Q[1-9]\d*", qid) else []


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
