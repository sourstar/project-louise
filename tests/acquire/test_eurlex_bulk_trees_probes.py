"""Architecture's probes against 13743848 (2026-11-26), each asserting a
defect it ran; kept here INVERTED, as the regressions of b11f3c5c."""
from __future__ import annotations

import gzip
import json
from types import SimpleNamespace

import pytest

from app.acquire import cellar, cellar_listing, datadump, eurlex_dump, work_trees
from app.acquire.spec import HeldBack, RemoteFile
from tests.acquire.test_eurlex_bulk_trees import CELEX, CREATED, FX, MEMBER, MTD, _statement, _zip

URL = "https://cellar.test"


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    monkeypatch.setattr(work_trees, "_BULK", {})
    base, corpus = tmp_path / "datadump", tmp_path / "corpus"
    _statement(base / "datadump-it", "it")
    _zip(base / "datadump-it" / "LEG_MTD.zip", {MEMBER: (FX / MTD).read_bytes()})
    eurlex_dump._stamp(corpus, {"it": CREATED})
    return base, corpus


def _unit(celex=CELEX):
    return RemoteFile(rel_path=cellar.rel_path("it", celex), mtime=None, remote=cellar.encode(celex, "it"))


def test_P1_env_unset_with_the_dump_stamped_holds_the_pass_back(tmp_path, monkeypatch):
    base, corpus = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(datadump, "_DUMPS", datadump.Dumps(None))
    monkeypatch.setattr(eurlex_dump, "build_newer", lambda *a, **k: None)
    f = _unit()
    with pytest.raises(HeldBack):
        cellar.TreeUnits().prepare([f], [f], SimpleNamespace(corpus_dir=corpus, name="eu"), SimpleNamespace(url=URL))


def _sparql(n, last, pages):
    asked = []

    def sparql(client, base, query, *, what="", charge=None):
        asked.append(what)
        if "COUNT(*)" in query:
            return [{"n": {"value": str(n)}, **({"max": {"value": last}} if last else {})}]
        return pages.pop(0) if pages else []
    return sparql, asked


@pytest.mark.parametrize("fmt", ["main", "a783fd1c"])
def test_P2_a_window_kept_before_shape_is_paged_once_kept_anew_and_then_covers(tmp_path, monkeypatch, fmt):
    base, corpus = _setup(tmp_path, monkeypatch)
    data = {"n": 1, "last": None, "rows": [[CELEX, "it", 100, "true"]]}
    if fmt == "a783fd1c":
        data.update(year=1972, languages=["it"])
    p = cellar_listing._path(URL, 1972, ("it",), True)
    p.parent.mkdir(parents=True)
    p.write_bytes(gzip.compress(json.dumps(data).encode()))
    page = [{"celex": {"value": CELEX}, "language": {"value": "http://x/ITA"},
             "last": {"value": "1970-01-01T00:01:40Z"}, "force": {"value": "true"}}]
    sparql, asked = _sparql(1, None, [page])
    monkeypatch.setattr(cellar, "sparql", sparql)
    units = cellar.TreeUnits()
    f = _unit()
    units._prepare_bulk(base, corpus, ["it"], [f], "eu", URL)
    assert json.loads(gzip.decompress(p.read_bytes()))["shape"] == cellar_listing.SHAPE, "kept anew"
    assert units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, f, corpus / f.rel_path)[1] == 100
    asked.clear()
    units._prepare_bulk(base, corpus, ["it"], [f], "eu", URL)
    assert asked == [], "covered now: nothing asked again"


def test_P3_a_waiting_unit_leaves_the_asked_first_list_so_the_complete_listing_runs(tmp_path, monkeypatch):
    base, corpus = _setup(tmp_path, monkeypatch)
    eurlex_dump._record_incomplete(corpus, {"it": {CELEX: "true"}})
    units = cellar.TreeUnits()
    key = str(corpus.resolve())
    units._bulk[key] = {"it": object()}
    units._after[key], units._cover[key], units._dates[key] = {"it": 1000.0}, set(), {}
    f = _unit()
    with pytest.raises(cellar.WindowNotListed):
        units.fetch(SimpleNamespace(url=URL), f, corpus / f.rel_path)
    assert eurlex_dump.incomplete_due(corpus, ("it",)) == [], "waits out its episode"
    from app.acquire import specs
    from app.acquire.spec import Filter
    monkeypatch.setattr(cellar, "listing", lambda *a, **k: ["the complete listing"])
    spec = specs.EURLEX_SPEC
    got = cellar.TreeInventory(corpus, "eu").list(spec.endpoints[0], spec, Filter(include=("lang=it/*",), exclude=()))
    assert got == ["the complete listing"]


def test_P4_a_stop_mid_listing_keeps_what_was_listed_and_the_next_pass_resumes(tmp_path, monkeypatch):
    base, corpus = _setup(tmp_path, monkeypatch)
    calls = []

    def sparql(client, base_, query, *, what="", charge=None):
        calls.append(what)
        if "1973" in what:
            raise cellar.TransferAborted("cellar: stopped between requests")
        if "COUNT(*)" in query:
            return [{"n": {"value": "1"}}]
        return [{"celex": {"value": CELEX}, "language": {"value": "http://x/ITA"},
                 "last": {"value": "1970-01-01T00:01:40Z"}, "force": {"value": "true"}}]
    monkeypatch.setattr(cellar, "sparql", sparql)
    units = cellar.TreeUnits()
    due = [_unit(CELEX), _unit("31973R0002")]
    with pytest.raises(cellar.TransferAborted):
        units._prepare_bulk(base, corpus, ["it"], due, "eu", URL)
    dates, cover = cellar_listing.coverage({(CELEX, "it")}, {"it": 0.0}, URL)
    assert (1972, "it") in cover and dates[(CELEX, "it")] == 100
    calls.clear()
    monkeypatch.setattr(cellar, "sparql", lambda *a, **k: (calls.append(k.get("what")), [{"n": {"value": "0"}}])[1])
    units._prepare_bulk(base, corpus, ["it"], due, "eu", URL)
    assert all("1972" not in str(w) for w in calls), calls


def test_X1_vouched_racing_keep_never_puts_old_rows_back(tmp_path, monkeypatch):
    """Architecture's X1: a child runs vouched("old") and pauses INSIDE the
    lock after its read; the parent keeps "new" meanwhile. With the flock the
    parent waits and the rows end "new"; without it the old rows came back."""
    import os
    import time
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    cellar_listing.keep(URL, 1972, ("it",), 1, "old", [[CELEX, "it", 100, "true"]], force=True)
    read_r, read_w = os.pipe()
    pid = os.fork()
    if pid == 0:                                                  # the child: vouched, paused after its read
        try:
            real = cellar_listing.json.loads

            def loads(b, *a, **k):
                out = real(b, *a, **k)
                os.write(read_w, b"r")
                time.sleep(0.5)
                return out
            cellar_listing.json.loads = loads
            cellar_listing.vouched(URL, 1972, ("it",), 1, "old", force=True)
        finally:
            os._exit(0)
    os.read(read_r, 1)                                            # the child holds the lock, has read
    t0 = time.monotonic()
    cellar_listing.keep(URL, 1972, ("it",), 1, "new", [[CELEX, "it", 5000, "true"]], force=True)
    waited = time.monotonic() - t0
    os.waitpid(pid, 0)
    dates, _ = cellar_listing.coverage({(CELEX, "it")}, {"it": 0.0}, URL)
    assert dates[(CELEX, "it")] == 5000, "the old rows came back"
    assert waited > 0.2, "keep waited for the child's lock"


def test_P5_a_fingerprint_that_no_longer_holds_leaves_the_window_as_kept(tmp_path, monkeypatch):
    import gzip as _gz
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    cellar_listing.keep(URL, 1972, ("it",), 1, "new", [[CELEX, "it", 5000, "true"]], force=True, at=123.0)
    cellar_listing.vouched(URL, 1972, ("it",), 1, "old", force=True, at=999.0)
    p = cellar_listing._path(URL, 1972, ("it",), True)
    data = json.loads(_gz.decompress(p.read_bytes()))
    assert data["at"] == 123.0 and data["rows"] == [[CELEX, "it", 5000, "true"]]


def test_an_oserror_while_listing_holds_the_pass_back(tmp_path, monkeypatch):
    """Review of 7eb82ba1, 1: ENOSPC in `keep` left prepare as an OSError, the
    engine went on "not prepared", every tree asked of Cellar."""
    base, corpus = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(datadump, "_DUMPS", datadump.Dumps(base))
    monkeypatch.setattr(eurlex_dump, "build_newer", lambda *a, **k: None)

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cellar, "_window_files", full)
    f = _unit()
    with pytest.raises(HeldBack):
        cellar.TreeUnits().prepare([f], [f], SimpleNamespace(corpus_dir=corpus, name="eu"), SimpleNamespace(url=URL))


def test_a_covered_window_is_asked_again_once_a_day_and_its_units_come_back_at_once(tmp_path, monkeypatch):
    import time
    base, corpus = _setup(tmp_path, monkeypatch)
    eurlex_dump._record_incomplete(corpus, {"it": {CELEX: "true"}})
    eurlex_dump.mark_deferred(corpus, "it", CELEX, "waits for its window")
    created = datadump._stamp(CREATED).timestamp()
    cellar_listing.keep(URL, 1972, ("it",), 1, None, [[CELEX, "it", 100, "true"]], force=True, at=created + 10)
    asked = []

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        asked.append((year, langs))
        cellar_listing.vouched(url, year, langs, 1, None, force=True, at=time.time())
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    units = cellar.TreeUnits()
    units._prepare_bulk(base, corpus, ["it"], [_unit()], "eu", URL)
    assert asked == [(1972, ("it",))], "known whole more than a day ago: its fingerprint asked again"
    assert [c for _, c, _ in eurlex_dump.incomplete_due(corpus, ("it",))] == [CELEX], "listed: due at once"
    asked.clear()
    units._prepare_bulk(base, corpus, ["it"], [_unit()], "eu", URL)
    assert asked == [], "known whole within the day: nothing asked"
