"""records-vs-origin asks a dated run of container files by the BYTE RANGES
a record reads (app/acquire/range_origin.py): Wikisource's multistream dump
by the streams of a work's pages, its three tables whole, served with Range
by the acquisition tests' mock origin on loopback — never the real network.

The copy places a record's unit (the plugin's `origin_ranges`), the bytes
are fetched once a check whichever records share them, through the budgeted
Range client, and compared with the copy's; the outcomes are 03 §3.2's."""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import plugin_io
from app.acquire import budget, range_origin
from app.acquire.specs import ACQUIRE_REGISTRY
from app.hold import check as chk
from app.plugins.base import DiscoverState
from app.plugins.wikisource import index as index_mod, locate, source as ws
from tests.acquire.mock_http_server import MockServer
from tests.wikisource_fixture import write_run

SRC = ws.WikisourceSource()
RUN = "20261101"
DIR = f"itwikisource/{RUN}"
DUMP = f"{DIR}/itwikisource-{RUN}-pages-articles-multistream.xml.bz2"
TABLES = [f"{DIR}/itwikisource-{RUN}-{t}" for t in locate.TABLE_UNITS]


@pytest.fixture
def copy(tmp_path, isolated_config, monkeypatch):
    from app.acquire import backoff
    monkeypatch.setattr(backoff, "SCALE", 0.0)
    root = tmp_path / "copy"
    write_run(root, run=RUN)
    return root


@pytest.fixture
def origin(tmp_path, copy):
    """The mock origin, serving its own copy of the run's files."""
    served = tmp_path / "served"
    shutil.copytree(copy / "itwikisource", served / "itwikisource",
                    ignore=shutil.ignore_patterns(".pack", "*.sqlite", "*.lock"))
    server = MockServer(served)
    yield server
    server.shutdown()


def _reader(copy: Path, url: str, run: str | None = RUN) -> range_origin.RangeOrigin:
    codex = SimpleNamespace(plugin="wikisource", origin=SimpleNamespace(spec={"url": url}))
    mo = range_origin.RangeOrigin(ACQUIRE_REGISTRY["wikisource"], codex, SRC)
    assert mo.prepare(copy, run) is None
    return mo


def _origins(copy: Path) -> dict[str, str]:
    """kind -> the record's origin, as the build writes it."""
    out = {}
    for r in SRC.discover(DiscoverState(corpus_root=copy, snapshot=RUN)):
        if not r.error:
            out.setdefault(r.extra["kind"], SRC.origin_of(r.locator, r.native_id, copy))
    return out


def _streams_of(copy: Path, origin_locator: str) -> list[tuple[int, int]]:
    return [(s, e) for _p, s, e in SRC.origin_ranges(origin_locator, copy, RUN).ranges()]


def test_the_plugin_says_what_a_record_reads_the_tables_whole_and_its_pages_streams(copy):
    work = _origins(copy)["work"]
    reads = SRC.origin_ranges(work, copy, RUN)
    assert [p.relative_to(copy).as_posix() for p in reads.whole] == TABLES
    assert reads.group == ("dump", "itwikisource")
    ranges = reads.ranges()
    assert ranges and all(p == copy / DUMP for p, _s, _e in ranges)
    known = set(index_mod.streams(copy / DUMP))
    assert {(s, e) for _p, s, e in ranges} <= known, "whole streams of the run's index, nothing else"
    # every page the record was parsed from is in one of them
    pages = SRC._unit(copy / DUMP, int(work.partition("|")[0].rpartition("#")[2]))
    works = index_mod.Works(index_mod.ensure(copy / DUMP))
    try:
        for p in pages:
            row = works.by_id(p.id)
            assert (row["start"], row["end"]) in {(s, e) for _p, s, e in ranges}, p.title
    finally:
        works.close()
    with pytest.raises(locate.PageNotInRun):
        SRC.origin_ranges(locate.origin("itwikisource", 999_999), copy, RUN).ranges()


def test_a_unit_is_weighed_by_its_streams_when_asked_with_the_tables_shared(copy, origin):
    mo = _reader(copy, origin.base_url)
    work = _origins(copy)["work"]
    got = mo.unit(work)
    assert got.unit == work.partition("|")[0] and got.size is None, "weighed only when the bound reaches it"
    assert [name for name, _n in got.shared] == TABLES
    assert all(n == (copy / name).stat().st_size for name, n in got.shared)
    assert got.group == ("dump", "itwikisource")
    assert mo.weigh(got.unit) == sum(e - s for s, e in _streams_of(copy, work)) > 0
    assert mo.bytes_read == 0, "weighing reads the copy, never the origin"
    assert mo.weigh("no such unit") == 0


def test_a_record_whose_bytes_are_the_origin_s_is_re_parsed_from_the_copy(copy, origin, monkeypatch):
    drawn: list[int] = []
    real = budget.acquire_bytes
    monkeypatch.setattr(budget, "acquire_bytes", lambda n: drawn.append(n) or real(n))
    mo = _reader(copy, origin.base_url)
    origins = _origins(copy)
    for o in origins.values():
        assert mo.materialise(o) == copy
    assert sum(drawn) == mo.bytes_read, "every byte drawn from the transfer budget (invariant 5)"
    # the tables whole, ONCE for the two records; each stream once
    for t in TABLES:
        size = (copy / t).stat().st_size
        half = (size + 1) // 2
        assert [r for r in origin.range_requests[t] if r != "bytes=0-0"] == [
            f"bytes=0-{half - 1}", f"bytes={half}-{size - 1}"], \
            (t, "whole, once, in two halves: a Range over the whole file may be answered 200")
    asked = [r for r in origin.range_requests[DUMP] if r != "bytes=0-0"]
    assert len(asked) == len(set(asked)), "no stream fetched twice"
    want = {f"bytes={s}-{e - 1}" for o in origins.values() for s, e in _streams_of(copy, o)}
    assert set(asked) == want
    mo.close()


@pytest.mark.parametrize("what", ["a stream", "a table", "a length"])
def test_bytes_that_differ_at_the_origin_are_changed_at_the_origin(copy, origin, what):
    work = _origins(copy)["work"]
    if what == "a stream":
        s, _e = _streams_of(copy, work)[0]
        p = origin.root / DUMP
        raw = bytearray(p.read_bytes())
        raw[s + 12] ^= 0xFF
        p.write_bytes(bytes(raw))
    elif what == "a table":
        p = origin.root / TABLES[1]
        raw = bytearray(p.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        p.write_bytes(bytes(raw))
    else:
        p = origin.root / DUMP
        p.write_bytes(p.read_bytes() + b"\\0")
    mo = _reader(copy, origin.base_url)
    outcome, why = mo.compare(work)
    assert outcome == "changed", (outcome, why)
    assert {"a stream": "are not the copy's", "a table": "is not the copy's",
            "a length": "not the file the copy holds"}[what] in why
    with pytest.raises(range_origin.OriginBytesChanged):
        mo.materialise(work)


@pytest.mark.parametrize("why, says", [("gone", "no longer serves run"), ("no ranges", "by Range"),
                                       ("silent", "did not answer")])
def test_an_origin_that_cannot_answer_is_unreachable_and_is_not_asked_again(copy, origin, why, says):
    url = origin.base_url
    if why == "gone":
        (origin.root / TABLES[0]).unlink()
    elif why == "no ranges":
        origin.no_ranges.add(TABLES[0])
    else:
        url = "http://127.0.0.1:9"
    mo = _reader(copy, url)
    origins = _origins(copy)
    outcome, said = mo.compare(origins["work"])
    assert outcome == "unreachable" and says in said, (outcome, said)
    asked = mo.requests
    assert mo.compare(origins["author"]) == ("unreachable", said) and mo.requests == asked
    with pytest.raises(chk.OriginAway, match=says):
        mo.materialise(origins["work"])


def test_a_record_the_copy_cannot_place_says_so_and_asks_nothing(copy, origin):
    mo = _reader(copy, origin.base_url)
    outcome, why = mo.compare(locate.origin("itwikisource", 999_999))
    assert outcome == "errored" and "PageNotInRun" in why and mo.requests == 0
    # no copy, or no run: every record unreachable for that reason
    codex = SimpleNamespace(plugin="wikisource", origin=SimpleNamespace(spec={"url": origin.base_url}))
    bare = range_origin.RangeOrigin(ACQUIRE_REGISTRY["wikisource"], codex, SRC)
    assert "no copy of the origin" in bare.prepare(copy / "nowhere", RUN) and bare.base is None
    assert bare.compare(_origins(copy)["work"])[0] == "unreachable"
    assert "names no run" in bare.prepare(copy, None)


def test_the_pool_re_parses_only_the_records_whose_bytes_are_the_origin_s(copy, origin):
    origins = _origins(copy)
    p = origin.root / DUMP
    raw = bytearray(p.read_bytes())
    author_streams = {a for a, _b in _streams_of(copy, origins["author"])}
    s = next(a for a, _b in _streams_of(copy, origins["work"]) if a not in author_streams)
    raw[s + 12] ^= 0xFF                      # a stream the work reads and the author does not
    p.write_bytes(bytes(raw))
    mo = _reader(copy, origin.base_url)
    handed: list[str] = []

    def local_many(items):
        for uid, _locator, _line in items:
            handed.append(uid)
            yield uid, "same", None
    got = list(mo.many_with(local_many)([("w", origins["work"], b"x"), ("a", origins["author"], b"y")]))
    assert handed == ["a"]
    assert sorted((uid, outcome) for uid, outcome, _why in got) == [("a", "same"), ("w", "changed")]
    assert "are not the copy's" in next(why for uid, _o, why in got if uid == "w")


def test_the_origin_is_chosen_by_the_spec_and_the_plugin_s_hook(monkeypatch):
    from app import louise
    from app.hold import origin_run
    codex = SimpleNamespace(plugin="wikisource", collection_id="c-ws", parser=SimpleNamespace(version=1),
                            origin=SimpleNamespace(spec={"url": "https://dumps.example", "delta": {"mode": "dated-dumps"}}))
    monkeypatch.setattr(origin_run, "named_run", lambda c: RUN)
    monkeypatch.setattr(louise, "_member_origins", {})
    mo = louise._member_origin(codex)
    assert isinstance(mo, range_origin.RangeOrigin) and mo.url == "https://dumps.example" and mo.max_conns == 2
    assert callable(mo.prepare) and callable(mo.weigh) and callable(mo.many_with)
    # a plugin of the same layout that names no ranges (wikipedia today) has no such origin
    codex = SimpleNamespace(plugin="wikipedia", collection_id="c-wp", parser=SimpleNamespace(version=6),
                            origin=SimpleNamespace(spec={"delta": {"mode": "dated-dumps"}}))
    assert range_origin.for_codex(codex) is None
    monkeypatch.setattr(origin_run, "named_run", lambda c: None)
    assert range_origin.for_codex(SimpleNamespace(plugin="wikisource", parser=SimpleNamespace(version=1),
                                                  origin=codex.origin)) is None, "a Codex naming no run"
    assert plugin_io.origin_ranges(object(), "x", ".") is None
