"""Wikipedia parser 6: a record's `origin` names the PAGE, resolved in the
snapshot's run through the origin's own index (09 §9.5 amended 2026-11-17,
operator: "Choice B"; app/plugins/wikipedia/locate.py, stream_index.py).

The form, both forms resolving to the bytes discovery names, and the run's
lookup table: derived, placed by the index's physical identity, rebuilt when
the index changes, answering a lookup without a scan."""
from __future__ import annotations

import bz2
import os
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from app import collections as collections_mod
from app import derived, plugin_io
from app.plugins.base import DiscoverState
from app.plugins.wikipedia import locate, page_props, stream_index
from app.plugins.wikipedia import source as wsrc
from app.plugins.wikipedia.ids import native_id_of
from app.plugins.wikipedia.source import WikipediaSource, WikipediaSourceV5

RUN = "20261001"
V6, V5 = WikipediaSource(), WikipediaSourceV5()


def _page(pid: int, title: str, text: str) -> str:
    return (f"<page><title>{escape(title)}</title><ns>0</ns><id>{pid}</id><revision><id>{pid}00</id>"
            f"<timestamp>2026-09-30T12:00:00Z</timestamp><text>{escape(text)}</text></revision></page>\n")


def write_run(corpus: Path, run: str, streams, *, wiki: str = "trwiki", props=()) -> Path:
    """`<corpus>/<wiki>/<run>/`: one bz2 stream per list of pages, the index
    as Wikimedia writes it (`offset:page_id:title`), the run's page_props."""
    from tests import wikipedia_props
    d = corpus / wiki / run
    d.mkdir(parents=True, exist_ok=True)
    base = f"{wiki}-{run}-pages-articles-multistream"
    body, index, at = b"", [], 0
    for pages in streams:
        blob = bz2.compress("".join(_page(*p) for p in pages).encode("utf-8"))
        index += [f"{at}:{p[0]}:{p[1]}\n" for p in pages]
        body += blob
        at += len(blob)
    (d / f"{base}.xml.bz2").write_bytes(body)
    (d / f"{base}-index.txt.bz2").write_bytes(bz2.compress("".join(index).encode("utf-8")))
    wikipedia_props.write(d / f"{base}.xml.bz2", props)
    return d


STREAMS = [[(5, "Ankara", "Ankara is a city."), (7, "Kategori:Şehirler", "a category")],
           [(9, "Rize: çay", "Rize grows tea.")],
           [(12, "Van", "Van is a lake."), (14, "Muş", "Muş is a town.")]]
PROPS = [(5, "wikibase_item", "Q3640"), (9, "wikibase_item", "Q483078"), (9, "page_image_free", "Rize.jpg")]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    for cache in (wsrc._stream_cache,):
        cache.clear()
    root = tmp_path / "origin"
    write_run(root, RUN, STREAMS, props=PROPS)
    return root


def _refs(root: Path) -> dict[str, object]:
    (root.parent / "state").mkdir(exist_ok=True)
    state = DiscoverState(corpus_root=root, state_dir=root.parent / "state")
    state.snapshot = RUN
    return {r.native_id: r for r in V6.discover(state) if r.scope == "document" and not r.error}


# ---- the form ---------------------------------------------------------------

def test_parser_6_names_the_page_and_parser_5_the_byte_range(corpus):
    refs = _refs(corpus)
    nid = str(native_id_of("tr", 9))
    loc = refs[nid].locator
    new = plugin_io.record_origin(V6, loc, corpus, nid)
    old = plugin_io.record_origin(V5, loc, corpus, nid)
    assert new == "trwiki/pages-articles-multistream.xml.bz2#9|trwiki/page_props.sql.gz#9"
    assert old.startswith(f"trwiki/{RUN}/trwiki-{RUN}-pages-articles-multistream.xml.bz2#")
    assert "|" in old and f"trwiki/{RUN}/trwiki-{RUN}-page_props.sql.gz#" in old
    assert locate.page_origin(old, 9) == new, "the new origin is a pure function of the old and the page id"
    assert V6.carried_origin(old, nid, 5) == new and V6.carried_origin(new, nid, 6) == new
    assert V6.carried_origin(old, nid, 4) is None, "a version the carry does not know is parsed"
    assert locate.is_page_form(new) and not locate.is_page_form(old)
    # a page with no page_props rows has one piece
    nid12 = str(native_id_of("tr", 12))
    assert plugin_io.record_origin(V6, refs[nid12].locator, corpus, nid12) == \
        "trwiki/pages-articles-multistream.xml.bz2#12"
    with pytest.raises(ValueError, match="native id"):
        plugin_io.record_origin(V6, loc, corpus, None)


def test_the_form_is_unambiguous_per_wiki_and_per_unit():
    a = locate.page_origin("dewiki/20261001/dewiki-20261001-pages-articles-multistream.xml.bz2#1::2", 9)
    b = locate.page_origin("trwiki/20261001/trwiki-20261001-pages-articles-multistream.xml.bz2#1::2", 9)
    c = locate.page_origin("trwiki/20261001/trwiki-20261001-page_props.sql.gz#3::4", 9)
    assert len({a, b, c}) == 3, "the same page id in two wikis, and the page's two units, are three origins"
    archive = ("https://huggingface.co/datasets/x/resolve/abc/data/tr/000.parquet#3::5|"
               "https://huggingface.co/datasets/x/resolve/abc/data/tr/001.parquet#0::2")
    props = "trwiki/20261001/trwiki-20261001-page_props.sql.gz#30::90"
    assert locate.page_origin(f"{archive}|{props}", 9) == f"{archive}|trwiki/page_props.sql.gz#9", \
        "the archive's row ranges are stable already, and are left as they are"
    with pytest.raises(ValueError, match="names page 8"):
        locate.page_origin("trwiki/page_props.sql.gz#8", 9)
    with pytest.raises(ValueError, match="not a piece"):
        locate.page_origin("somewhere/else#1", 9)


# ---- the resolver: both forms ------------------------------------------------------------

def test_both_forms_resolve_to_the_bytes_discovery_names(corpus):
    refs = _refs(corpus)
    assert len(refs) == 5, "every page (no siteinfo header: the English namespace list)"
    for nid, ref in refs.items():
        new = plugin_io.record_origin(V6, ref.locator, corpus, nid)
        old = plugin_io.record_origin(V5, ref.locator, corpus, nid)
        for plugin in (V6, V5):
            for origin in (new, old):
                local, path = plugin_io.local_locator(plugin, origin, corpus, run=RUN)
                assert path == ref.locator and local.is_file(), (plugin.PARSER_VERSION, origin)
        assert plugin_io.local_locator(V6, old, corpus)[1] == ref.locator, "the byte range needs no run"
        a = V6.fetch(ref)
        b = V6.fetch(type(ref)(source_id=ref.source_id, native_id=nid,
                               locator=plugin_io.local_locator(V6, new, corpus, run=RUN)[1]))
        assert a.data == b.data and a.raw_hash == b.raw_hash
    # the last stream runs to the end of the file, as discovery writes it
    last = refs[str(native_id_of("tr", 14))].locator
    assert last.split("|")[0].endswith("::0")


def test_a_page_form_origin_without_a_run_or_outside_it_is_said(corpus):
    new = "trwiki/pages-articles-multistream.xml.bz2#9"
    with pytest.raises(ValueError, match="read in a run"):
        plugin_io.local_locator(V6, new, corpus)
    with pytest.raises(locate.PageNotInRun, match="does not list page 99"):
        plugin_io.local_locator(V6, "trwiki/pages-articles-multistream.xml.bz2#99", corpus, run=RUN)
    with pytest.raises(locate.PageNotInRun, match="no row of page 12"):
        plugin_io.local_locator(V6, "trwiki/page_props.sql.gz#12", corpus, run=RUN)
    with pytest.raises(FileNotFoundError):
        plugin_io.local_locator(V6, new, corpus, run="20261101")
    unit, size = plugin_io.origin_unit(V6, f"{new}|trwiki/page_props.sql.gz#9", corpus, run=RUN)
    assert unit == (f"trwiki/{RUN}/trwiki-{RUN}-pages-articles-multistream.xml.bz2+"
                    f"trwiki/{RUN}/trwiki-{RUN}-page_props.sql.gz") and size > 0


def test_the_codexs_parser_decides_which_form_a_collection_writes():
    registry = collections_mod.PLUGIN_REGISTRY["wikipedia"]
    assert registry.PARSER_VERSION == 6
    assert collections_mod.plugin_at(registry, 5).PARSER_VERSION == 5
    assert collections_mod.plugin_at(registry, 5).origin_names_run is True
    assert collections_mod.plugin_at(registry, 6).origin_names_run is False
    assert collections_mod.plugin_at(registry, 4) is None


# ---- the run's lookup table -----------------------------------------------------------------

def _index(d: Path, wiki: str = "trwiki", run: str = RUN) -> Path:
    return d / f"{wiki}-{run}-pages-articles-multistream-index.txt.bz2"


def test_the_table_answers_each_page_with_its_stream(corpus):
    refs = _refs(corpus)
    index = _index(corpus / "trwiki" / RUN)
    for ref in refs.values():
        page = int(ref.extra["page_id"])
        start, end = (int(x) for x in ref.locator.split("|")[0].split("::")[1:])
        assert stream_index.stream_of(index, page) == (start, end)
    assert stream_index.stream_of(index, 7) is not None, "every line of the index, a category too"
    assert stream_index.stream_of(index, 6) is None and stream_index.stream_of(index, 10**9) is None


def test_the_table_is_derived_placed_by_physical_identity_and_rebuilt_when_the_index_moves(
        tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.CACHE_DIR", tmp_path / "cache")
    a = write_run(tmp_path / "one", RUN, STREAMS)
    b = write_run(tmp_path / "two", RUN, [[(5, "Ankara", "x")], [(9, "Rize", "y"), (12, "Van", "z")]])
    built: list[Path] = []
    real = stream_index.build
    monkeypatch.setattr(stream_index, "build", lambda o, d: built.append(d) or real(o, d))
    pa_ = stream_index.ensure(_index(a))
    pb = stream_index.ensure(_index(b))
    assert pa_ != pb and pa_.name == pb.name, "one file name in two corpora: two tables"
    assert pa_.parent == derived.place("wikipedia-stream-index", _index(a).parent), "under LOUISE_CACHE_DIR"
    assert str(pa_).startswith(str(tmp_path / "cache" / "wikipedia-stream-index"))
    assert stream_index.stream_of(_index(a), 9) != stream_index.stream_of(_index(b), 9)
    # a link to the same folder is the same physical file: the same table
    os.symlink(a, tmp_path / "link")
    assert stream_index.ensure(_index(tmp_path / "link")) == pa_
    assert len(built) == 2
    # the index re-written: the table is rebuilt, and answers the new index
    write_run(tmp_path / "one", RUN, [[(9, "Rize", "y")], [(5, "Ankara", "x")]])
    st = _index(a).stat()
    os.utime(_index(a), ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert stream_index.ensure(_index(a)) == pa_ and len(built) == 3
    assert stream_index.stream_of(_index(a), 9)[0] == 0
    assert stream_index.ensure(_index(a)) == pa_ and len(built) == 3, "fresh: not built again"


def test_a_lookup_reads_one_row_group_never_the_index(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    monkeypatch.setattr(stream_index, "ROW_GROUP", 4)
    streams = [[(p, f"P{p}", "t") for p in range(s * 3 + 1, s * 3 + 4)] for s in range(10)]
    d = write_run(tmp_path / "o", RUN, streams)
    pack = stream_index.ensure(_index(d))
    import pyarrow.parquet as pq
    assert pq.read_metadata(pack).num_row_groups == 8
    monkeypatch.setattr(stream_index, "read_columns", lambda *_: pytest.fail("the index read again"))
    stream_index.TABLE._groups.clear()
    groups_read = []
    real = pq.ParquetFile.read_row_group
    monkeypatch.setattr(pq.ParquetFile, "read_row_group", lambda self, g, *a, **k: groups_read.append(g)
                        or real(self, g, *a, **k))
    assert stream_index.stream_of(_index(d), 17) == stream_index.lookup(pack, 17)
    assert stream_index.stream_of(_index(d), 18) is not None
    assert set(groups_read) == {4}, "pages 17 and 18 share one row group, read once"


def test_a_page_listed_twice_refuses_the_table(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    d = write_run(tmp_path / "o", RUN, [[(5, "A", "x")], [(6, "B", "y")]])
    _index(d).write_bytes(bz2.compress(b"0:5:A\n90:6:B\n120:5:A again\n"))
    with pytest.raises(stream_index.StreamIndexError, match="key 5 is listed twice"):
        stream_index.ensure(_index(d))


def test_the_index_is_read_as_discovery_reads_it(tmp_path):
    """A line whose numbers are not numbers is skipped and ends no stream;
    a title may hold colons; an empty title is a title."""
    d = tmp_path / "trwiki" / RUN
    d.mkdir(parents=True)
    idx = _index(d)
    idx.write_bytes(bz2.compress(b"10:1:A: b\nx:2:bad\n10:3:\n40:4:C\n40:5\n")
                    + bz2.compress(b"70:6:D\n"))
    page, start, end = stream_index.read_columns(idx)
    assert page.tolist() == [1, 3, 4, 5, 6] and start.tolist() == [10, 10, 40, 40, 70]
    assert end.tolist() == [40, 40, 70, 70, 0]


def test_page_props_pack_and_the_index_table_are_both_declared(tmp_path):
    from app import backend
    forms = {f.name: f for f in backend.forms_of(V6)}
    assert forms["the multistream index"].derived == ("wikipedia-stream-index",)
    assert forms["the page_props SQL dump"].derived == ("wikipedia-page-props",)
    assert page_props.unit_of  # the page_props table the resolver reads, keyed on pp_page
