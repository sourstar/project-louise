"""tests/test_wikisource_source.py — the Wikisource source end to end on a
small it.wikisource run (app/plugins/wikisource/source.py;
docs/notes/wikisource-design.md): a work is its page, its subpages and the
scan pages they transclude, in reading order; an author's page is a record
of its own; redirects and disambiguations are none; each page's categories
and properties, the Wikidata item among them, are the same run's tables'."""
from __future__ import annotations

import pytest

from app.plugins.base import DiscoverState
from app.plugins.wikisource import source as ws
from tests.wikisource_fixture import page, write_run, PAGES

SRC = ws.WikisourceSource()


def _refs(tmp_path, **kw):
    write_run(tmp_path, **kw)
    return [r for r in SRC.discover(DiscoverState(corpus_root=tmp_path, snapshot="20261101")) if not r.error]


def _doc(ref):
    return SRC.parse(SRC.fetch(ref))


def test_discovery_gives_one_ref_per_work_and_per_author(tmp_path, isolated_config):
    refs = _refs(tmp_path)
    assert [(r.extra["kind"], r.extra["title"]) for r in refs] == [("work", "Vita"),
                                                                   ("author", "Autore:Benvenuto Cellini")]
    assert ws.decode_native_id(int(refs[0].native_id)) == ("it", 31)
    assert refs[0].locator.endswith("::31")


def test_a_work_reads_its_subpages_and_scan_pages_in_order(tmp_path, isolated_config):
    doc = _doc(_refs(tmp_path)[0])
    assert doc.uid == f"wikisource:{ws.native_id_of('it', 31)}"
    assert (doc.title, doc.creators, doc.date, doc.language) == ("Vita", ["Benvenuto Cellini"], "1728", "it")
    assert doc.sections == [("Libro primo", "Io Benvenuto\nnacqui in Fiorenza."),
                            ("Libro secondo", "Libro secondo.")], "the scan pages' bodies, never their frames"
    assert [p["title"] for p in doc.extra["pages"]] == [
        "Vita", "Vita/Libro primo", "Vita/Libro secondo", "Pagina:Vita.djvu/5", "Pagina:Vita.djvu/6",
        "Pagina:Vita.djvu/7"]
    assert [p.get("level") for p in doc.extra["pages"]][3:] == [4, 3, 4]
    assert doc.extra["proofread"] == {"scan_pages": 3, "by_level": {"3": 1, "4": 2}, "all_proofread": True}
    assert doc.extra["indexes"] == ["Vita.djvu"]
    assert doc.subjects == ["Autobiografie", "Testi del XVI secolo", "Testi in cui è citato Dante Alighieri"]
    assert doc.extra["header"]["template"] == "Intestazione"
    assert all({"rev_id", "sha1", "timestamp"} <= set(p) for p in doc.extra["pages"])


def test_origin_hash_is_the_units_and_moves_with_a_scan_page(tmp_path, isolated_config):
    ref = _refs(tmp_path)[0]
    a, b = SRC.fetch(ref), SRC.fetch(ref)
    assert a.raw_hash == b.raw_hash and a.data == b.data, "the same unit twice"
    changed = [p.replace("nacqui in Fiorenza.", "nacqui a Firenze.") for p in PAGES]
    other = tmp_path / "other"
    other.mkdir()
    ref2 = _refs(other, pages=changed)[0]
    assert SRC.fetch(ref2).raw_hash != a.raw_hash, "a transcluded page's change moves the work's origin_hash"


def test_an_author_page_is_its_own_record(tmp_path, isolated_config):
    doc = _doc(_refs(tmp_path)[1])
    assert doc.extra["kind"] == "author" and doc.title == "Benvenuto Cellini"
    assert doc.extra["template"]["template"] == "Autore"
    assert ["Anno di nascita", "1500"] in doc.extra["template"]["params"]


def test_a_disambiguation_page_is_no_record_and_a_missing_scan_page_is_said(tmp_path, isolated_config):
    pages = PAGES + [page(61, 0, "Rime", "{{Disambigua}}\n* [[Rime (Petrarca)]]"),
                     page(62, 0, "Lettere", '<pages index="Lettere.djvu" from=1 to=2 />')]
    refs = _refs(tmp_path, pages=pages)
    titles = [r.extra["title"] for r in refs]
    assert "Rime" not in titles and "Lettere" in titles
    doc = _doc(next(r for r in refs if r.extra["title"] == "Lettere"))
    assert [i.code for i in doc.issues] == ["page-missing", "page-missing", "no-header"]


def test_parse_reads_nothing_fetch_did_not_hash(tmp_path, isolated_config):
    from tests.origin_coverage import assert_covered
    ref = _refs(tmp_path)[0]
    assert_covered(SRC, ref, tmp_path,
                   discover_state=lambda: DiscoverState(corpus_root=tmp_path, snapshot="20261101"))


def test_the_acquisition_takes_a_runs_dump_index_and_tables_per_language_behind_the_terms():
    """Bulk first (design note §2): the dated dump, its index and the run's
    categorylinks, linktarget and page_props of each language, two
    connections, yellow, the plugin's terms byte-exact."""
    from fnmatch import fnmatch

    from app.acquire import specs
    from app.plugins.base import LicenseClass
    spec = specs.ACQUIRE_REGISTRY["wikisource"]
    files = [f.rel_path for f in spec.snapshot_files("20261101") if f.rel_path.startswith("itwikisource/")]
    assert files == [
        "itwikisource/20261101/itwikisource-20261101-pages-articles-multistream.xml.bz2",
        "itwikisource/20261101/itwikisource-20261101-pages-articles-multistream-index.txt.bz2",
        "itwikisource/20261101/itwikisource-20261101-categorylinks.sql.gz",
        "itwikisource/20261101/itwikisource-20261101-linktarget.sql.gz",
        "itwikisource/20261101/itwikisource-20261101-page_props.sql.gz"]
    assert all(any(fnmatch(f, pat) for pat in spec.filters) for f in files), "the filters let every file through"
    assert [f.rel_path for f in spec.endpoints[0].static_files if f.rel_path.startswith("itwikisource/")] == [
        f.replace("20261101", "latest") for f in files]
    assert spec.endpoints[0].max_conns == 2 and spec.license_class is LicenseClass.YELLOW
    assert spec.terms_text_path.read_bytes().startswith(b"Wikisource text is licensed under")
    assert spec.slice_profiles["lang=it"] == "it" and SRC.license_class is LicenseClass.YELLOW


# ---- the run's tables: categories and page properties ------------------------------

def _pages(doc):
    return {p["id"]: p for p in doc.extra["pages"]}


def test_categories_and_the_wikidata_item_come_from_the_runs_tables(tmp_path, isolated_config):
    """design note §2: categorylinks (its targets named by linktarget) and
    page_props of the SAME run, never the wikitext: a work's categories are
    its page's and its subpages', each name once; every page keeps its own
    rows as the tables state them."""
    work_ref, author_ref = _refs(tmp_path)
    doc = _doc(work_ref)
    assert doc.extra["categories"] == doc.subjects == [
        "Autobiografie", "Testi del XVI secolo", "Testi in cui è citato Dante Alighieri"]
    assert "Pagine SAL 100%" not in doc.subjects, "a scan page's category is its own, not the work's"
    pages = _pages(doc)
    assert pages[31]["categories"][0] == {
        "target_id": 1001, "title": "Autobiografie", "sortkey": "VITA\nAUTOBIOGRAFIE",
        "timestamp": "2025-02-17 16:21:49", "sortkey_prefix": "Vita", "type": "page", "collation_id": 1}
    assert [r["title"] for r in pages[31]["categories"]] == ["Autobiografie", "Testi_del_XVI_secolo"], \
        "each row as the origin states it: the title as linktarget keys it"
    assert pages[21]["categories"][0]["title"] == "Pagine_SAL_100%"
    assert (doc.extra["wikidata"], doc.keys, doc.extra["page_props"]) == (
        "Q900031", ["wikidata:Q900031"], {"defaultsort": "Vita"})
    assert pages[32]["page_props"] == {"wikibase_item": "Q900032"}, "a subpage's own item stays with it"
    assert "categories" not in pages[33] and "page_props" not in pages[33], "a page with no rows holds none"
    author = _doc(author_ref)
    assert (author.subjects, author.keys, author.extra["page_props"]) == (
        ["Autori del XVI secolo"], ["wikidata:Q900041"], {"page_image_free": "Cellini.jpg"})


def test_a_category_the_text_states_and_the_table_does_not_is_not_held(tmp_path, isolated_config):
    """The wikitext's `[[Categoria:…]]` is no longer read: the table is what
    the origin files the page under (a template's categories included)."""
    from tests.wikisource_fixture import LINKS
    doc = _doc(_refs(tmp_path, links=[r for r in LINKS if r[1] != "Autobiografie"])[0])
    assert "[[Categoria:Autobiografie]]" in next(p for p in PAGES if "<id>31</id>" in p)
    assert "Autobiografie" not in doc.subjects


def test_origin_hash_moves_with_a_works_category_row_and_property_and_with_nothing_else(tmp_path, isolated_config):
    from tests.wikisource_fixture import LINKS, PROPS

    def work_hash(name, **kw):
        d = tmp_path / name
        d.mkdir()
        raw = SRC.fetch(_refs(d, **kw)[0])
        return raw.raw_hash
    base = work_hash("base")
    assert work_hash("same") == base, "the same run, the same unit"
    assert work_hash("subpage", links=[(32, "Lettere", 1006, "")] + LINKS) != base, "a subpage's new category"
    assert work_hash("stamp", timestamp="2026-11-01 00:00:00") != base, "a row's column"
    renamed = [(p, "Autobiografie_e_memorie" if t == "Autobiografie" else t, lt, pre) for p, t, lt, pre in LINKS]
    assert work_hash("renamed", links=renamed) != base, "linktarget's row for one of its categories"
    assert work_hash("qid", props=[(31, "wikibase_item", "Q900099")] + PROPS[1:]) != base
    assert work_hash("author", links=LINKS[:-1], props=PROPS[:-1]) == base, "another record's rows"


def test_both_statement_layouts_give_the_same_record(tmp_path, isolated_config):
    """One INSERT a line (runs to 20260901) or one tuple a line (from
    20261001): the same rows; `origin_hash` is over the bytes as dumped."""
    docs = {}
    for layout in ("lines", "statement"):
        d = tmp_path / layout
        d.mkdir()
        docs[layout] = _doc(_refs(d, layout=layout, per_statement=3)[0])
    a, b = docs["lines"], docs["statement"]
    assert (a.subjects, a.keys, a.extra) == (b.subjects, b.keys, b.extra)


def test_a_category_target_linktarget_does_not_hold_is_kept_and_said(tmp_path, isolated_config):
    """The run's tables are dumped at different moments: a link to a category
    created between the two dumps names a target linktarget lacks. The row is
    kept with its target id, the name is not guessed, the build is told."""
    import gzip

    from tests.wikisource_fixture import LINKS, OTHER_TARGETS, q, sql
    dump = write_run(tmp_path)
    lt = sorted({(lt, 14, t) for _p, t, lt, _pre in LINKS if lt != 1002} | set(OTHER_TARGETS),
                key=lambda r: (r[1], r[2]))
    (dump.parent / "itwikisource-20261101-linktarget.sql.gz").write_bytes(
        gzip.compress(sql("linktarget", [f"({i},{ns},{q(t)})" for i, ns, t in lt])))
    ref = next(r for r in SRC.discover(DiscoverState(corpus_root=tmp_path, snapshot="20261101")) if not r.error)
    doc = _doc(ref)
    row = next(r for r in _pages(doc)[31]["categories"] if r["target_id"] == 1002)
    assert "title" not in row and "Testi del XVI secolo" not in doc.subjects
    assert [(i.code, i.detail) for i in doc.issues if i.code == "category-target-missing"] == [
        ("category-target-missing", "target 1002")]


def test_a_run_whose_table_is_missing_or_unreadable_waits(tmp_path, isolated_config):
    """Deferral, never an error on a document: the language's works wait
    for the run's tables, said once as the container's report."""
    dump = write_run(tmp_path)
    (dump.parent / "itwikisource-20261101-linktarget.sql.gz").unlink()
    refs = list(SRC.discover(DiscoverState(corpus_root=tmp_path, snapshot="20261101")))
    assert [r.scope for r in refs] == ["container"] and "linktarget.sql.gz missing" in refs[0].error
    # MediaWiki's categorylinks before 1.45 (`cl_to`, no linktarget): refused, not read as empty
    import gzip

    from tests.wikisource_fixture import sql
    old = tmp_path / "old"
    dump = write_run(old)
    cols = ("  `cl_from` int(8) unsigned NOT NULL DEFAULT 0,\n  `cl_to` varbinary(255) NOT NULL DEFAULT '',\n"
            "  `cl_sortkey` varbinary(230) NOT NULL DEFAULT '',\n  PRIMARY KEY (`cl_from`,`cl_to`)")
    (dump.parent / "itwikisource-20261101-categorylinks.sql.gz").write_bytes(
        gzip.compress(sql("categorylinks", ["(31,'Autobiografie','VITA')"], create=cols)))
    refs = list(SRC.discover(DiscoverState(corpus_root=old, snapshot="20261101")))
    assert [r.scope for r in refs] == ["container"] and "cl_to" in refs[0].error


def test_the_discovery_signature_covers_the_runs_tables(tmp_path, isolated_config):
    """A record holds rows of each table: a new file must rebuild it."""
    import os
    dump = write_run(tmp_path)
    def state():
        return DiscoverState(corpus_root=tmp_path, snapshot="20261101")
    before = {r.native_id: (r.mtime, r.size) for r in SRC.discover(state()) if not r.error}
    for name in ("categorylinks", "linktarget", "page_props"):
        f = dump.parent / f"itwikisource-20261101-{name}.sql.gz"
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime + 1000))
        after = {r.native_id: (r.mtime, r.size) for r in SRC.discover(state()) if not r.error}
        assert set(after) == set(before) and all(after[k] != before[k] for k in before), name
        os.utime(f, (st.st_atime, st.st_mtime))


def test_the_packs_live_in_the_cache_when_one_is_named(tmp_path, isolated_config, monkeypatch):
    from app import config, derived
    from app.plugins.wikisource import tables
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    dump = write_run(tmp_path / "corpus")
    run = tables.tables_of(dump)
    tables.ensure(run)
    for table, kind, origin in ((tables.CATEGORYLINKS, "wikisource-categorylinks", run.categorylinks),
                                (tables.LINKTARGET, "wikisource-linktarget", run.linktarget),
                                (tables.PAGE_PROPS, "wikisource-page-props", run.page_props)):
        assert table.ensure(origin).parent == derived.place(kind, origin.parent)
    assert tables.LINKTARGET.unit_of(run.linktarget, 7) is None, "a page title's target is no category"
    assert tables.LINKTARGET.unit_of(run.linktarget, 1001)[2] == b"(1001,14,'Autobiografie')"


def test_the_runs_packs_are_built_at_once_in_the_parse_pool(tmp_path, isolated_config, monkeypatch):
    """Multi-threading is a must: the three files, one gzip stream each, are
    packed by three processes; the parent then finds each fresh."""
    from app import config, parallel
    from app.plugins.wikisource import tables
    monkeypatch.setattr(config, "PARSE_PROCESSES", 3)
    run = tables.tables_of(write_run(tmp_path))
    built = []
    real = parallel.each_part
    monkeypatch.setattr(parallel, "each_part", lambda fn, tasks, **k: built.extend(tasks) or real(fn, tasks, **k))
    tables.ensure(run)
    assert [name for name, _ in built] == ["categorylinks", "linktarget", "page_props"]
    monkeypatch.setattr(tables.CATEGORYLINKS, "build", lambda *a: pytest.fail("built twice"))
    tables.ensure(run)
    assert [name for name, _ in built] == ["categorylinks", "linktarget", "page_props"], "fresh: no pass"
    assert tables.CATEGORYLINKS.unit_of(run.categorylinks, 32)[2].startswith(b"(32,")
