"""tests/test_wikisource_origin.py — a Wikisource record's origin names its
page, and is read in the run the snapshot names (app/plugins/wikisource/
locate.py, source.py's hooks; app/keyed_origin.py; the design note §1).

What `records-vs-origin` does with them (app/hold/check.py): the record's
`origin` is resolved in the run under an origin base (`local_of`), fetched
and parsed again, and the whole line compared, `origin` (`origin_of`) and
`origin_hash` included."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import keyed_origin, plugin_io
from app.plugins.base import DiscoverState, DocRef
from app.plugins.wikisource import locate, source as ws
from tests.wikisource_fixture import PAGES, page, scan, write_run

SRC = ws.WikisourceSource()
RUN = "20261101"


def _refs(base: Path, **kw):
    write_run(base, **kw)
    return [r for r in SRC.discover(DiscoverState(corpus_root=base, snapshot=RUN)) if not r.error]


def _again(base: Path, ref, run: str = RUN):
    """What a verifier does with a record's origin under ITS base: the
    locator the run gives, fetched and parsed — no field of discovery's."""
    origin = SRC.origin_of(ref.locator, ref.native_id, Path(ref.locator.rpartition("::")[0]).parents[2])
    local, locator = SRC.local_of(origin, base, run)
    assert local.is_file()
    raw = SRC.fetch(DocRef(SRC.source_id, ref.native_id, locator))
    return origin, raw, SRC.parse(raw)


def test_the_plugin_declares_its_key_and_every_hook():
    k = keyed_origin.declared(SRC)
    assert k is not None and k.built and k.since == SRC.PARSER_VERSION == 1
    assert keyed_origin.missing_hooks(SRC) == [] and SRC.origin_names_run is False


def test_an_origin_names_the_page_in_the_runs_four_files_and_reads_back(tmp_path, isolated_config):
    work, author = _refs(tmp_path)
    for ref, pid in ((work, 31), (author, int(author.locator.rpartition("::")[2]))):
        origin = SRC.origin_of(ref.locator, ref.native_id, tmp_path)
        assert origin == "|".join(f"itwikisource/{u}#{pid}" for u in (
            "pages-articles-multistream.xml.bz2", "page_props.sql.gz", "categorylinks.sql.gz",
            "linktarget.sql.gz")), "its page, in the run-free name of each file its unit is read from"
        assert RUN not in origin, "no run in it: a line moves when its work does, not at every run"
        local, locator = SRC.local_of(origin, tmp_path, RUN)
        assert locator == ref.locator and local == Path(ref.locator.rpartition("::")[0]), \
            "read back in the run, it is the locator discovery wrote"
        assert SRC.origin_of(locator, ref.native_id, tmp_path) == origin
        assert SRC.run_of_locator(locator) == RUN
        assert SRC.carried_origin(origin, ref.native_id, 1) == origin
        assert SRC.carried_origin(origin, ref.native_id, 0) is None
        files = SRC.origin_files(origin, tmp_path, RUN)
        assert [f.name for f in files] == [f"itwikisource-{RUN}-{u}" for u in locate.UNITS]
        assert all(f.is_file() for f in files), "the dump and the run's three tables, as the fixture wrote them"
        unit, size = plugin_io.origin_unit(SRC, origin, tmp_path, run=RUN)[:2]
        assert size == sum(f.stat().st_size for f in files) and "itwikisource" in unit


def test_a_verifiers_own_download_of_the_run_gives_the_same_record(tmp_path, isolated_config):
    """`hold check --origin DIR`: another base, the same run. The record's
    origin resolves there to the same bytes: the same `origin_hash`, the
    same record, with nothing of the first node's paths or of discovery."""
    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    for ref in _refs(ours):
        doc = SRC.parse(SRC.fetch(ref))
        write_run(theirs)
        origin, raw, again = _again(theirs, ref)
        assert str(theirs) in raw.ref.locator and str(ours) not in raw.ref.locator
        assert raw.raw_hash == doc.raw_hash == again.raw_hash
        assert (again.uid, again.title, again.creators, again.sections, again.extra) == \
            (doc.uid, doc.title, doc.creators, doc.sections, doc.extra)
        assert SRC.origin_of(raw.ref.locator, ref.native_id, theirs) == origin, "and the same `origin`"


def test_the_run_is_what_is_read_another_run_moves_the_hash(tmp_path, isolated_config):
    """The same origin, resolved in a later run where a scan page changed:
    another `origin_hash` (the line moved), from that run's files."""
    work = _refs(tmp_path)[0]
    doc = SRC.parse(SRC.fetch(work))
    later = [p.replace("nacqui in Fiorenza.", "nacqui in Firenze.") for p in PAGES]
    assert later != PAGES, "the fixture's scan page carries the sentence this test changes"
    write_run(tmp_path, run="20261201", pages=later)
    _origin, raw, again = _again(tmp_path, work, run="20261201")
    assert "20261201" in raw.ref.locator and raw.raw_hash != doc.raw_hash
    assert again.sections != doc.sections and again.uid == doc.uid


def test_what_cannot_be_resolved_is_said(tmp_path, isolated_config):
    work = _refs(tmp_path)[0]
    origin = SRC.origin_of(work.locator, work.native_id, tmp_path)
    with pytest.raises(ValueError, match="read in a run"):
        SRC.local_of(origin, tmp_path, None)
    with pytest.raises(ValueError, match="read in a run"):
        SRC.origin_files(origin, tmp_path, None)
    gone = locate.origin("itwikisource", 999_999)
    with pytest.raises(locate.PageNotInRun, match="does not hold page 999999"):
        SRC.local_of(gone, tmp_path, RUN)
    assert issubclass(locate.PageNotInRun, LookupError)
    local, _locator = SRC.local_of(origin, tmp_path, "20250101")
    assert not local.exists(), "a run that is not under the base: the file is named, and found missing"
    with pytest.raises(ValueError, match="native id"):
        SRC.origin_of(work.locator, None, tmp_path)
    with pytest.raises(plugin_io.OriginOutsideBase):
        SRC.origin_of(work.locator, work.native_id, tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="the record is page"):
        SRC.origin_of(work.locator, ws.native_id_of("it", 32), tmp_path)


@pytest.mark.parametrize("text", [
    "itwiki/pages-articles-multistream.xml.bz2#31",                                   # wikipedia's
    "itwikisource/pages-articles-multistream.xml.bz2#31",                             # the dump alone
    "itwikisource/20261101/itwikisource-20261101-pages-articles-multistream.xml.bz2#31",   # a dated file
    locate.origin("itwikisource", 31).replace("categorylinks.sql.gz#31", "categorylinks.sql.gz#32"),
    "|".join(reversed(locate.origin("itwikisource", 31).split("|"))),                 # out of order
    locate.origin("itwikisource", 31).replace("itwikisource/linktarget", "enwikisource/linktarget"),
])
def test_only_its_own_form_is_read(text):
    with pytest.raises(ValueError):
        locate.read(text)


def test_the_form_is_unambiguous_per_wiki():
    assert locate.read(locate.origin("enwikisource", 31)) == ("enwikisource", 31)
    assert locate.origin("enwikisource", 31) != locate.origin("itwikisource", 31)
    assert locate.run_of_locator("/c/itwikisource/20261101/itwikisource-20261101-"
                                 "pages-articles-multistream.xml.bz2::31") == "20261101"
    assert locate.run_of_locator("/c/itwikisource/latest/x.bz2::31") is None
