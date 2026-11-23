"""tests/test_wikisource_source.py — the Wikisource source end to end on a
small it.wikisource run (app/plugins/wikisource/source.py;
docs/notes/wikisource-design.md): a work is its page, its subpages and the
scan pages they transclude, in reading order; an author's page is a record
of its own; redirects and disambiguations are none."""
from __future__ import annotations

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
    assert doc.extra["indexes"] == ["Vita.djvu"] and doc.subjects == ["Autobiografie"]
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


def test_the_acquisition_takes_a_runs_dump_and_index_per_language_behind_the_terms():
    """Bulk first (design note §2): the dated dump and its index of each
    language, two connections, yellow, the plugin's terms byte-exact."""
    from app.acquire import specs
    from app.plugins.base import LicenseClass
    spec = specs.ACQUIRE_REGISTRY["wikisource"]
    assert [f.rel_path for f in spec.snapshot_files("20261101") if f.rel_path.startswith("itwikisource/")] == [
        "itwikisource/20261101/itwikisource-20261101-pages-articles-multistream.xml.bz2",
        "itwikisource/20261101/itwikisource-20261101-pages-articles-multistream-index.txt.bz2"]
    assert spec.endpoints[0].max_conns == 2 and spec.license_class is LicenseClass.YELLOW
    assert spec.terms_text_path.read_bytes().startswith(b"Wikisource text is licensed under")
    assert spec.slice_profiles["lang=it"] == "it" and SRC.license_class is LicenseClass.YELLOW
