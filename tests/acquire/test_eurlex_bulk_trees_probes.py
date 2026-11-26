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


def test_P5_a_fingerprint_that_no_longer_holds_never_refreshes_the_rows_kept_since(tmp_path, monkeypatch):
    """The probe's race, as its outcome: another pass kept the window anew
    (`new`, the unit changed at 5000) after this pass asked the fingerprint
    `old`; `vouched` for `old` must not put a fresh `at` on rows it did not
    see — nor the old rows back."""
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    cellar_listing.keep(URL, 1972, ("it",), 1, "old", [[CELEX, "it", 100, "true"]], force=True)
    cellar_listing.keep(URL, 1972, ("it",), 1, "new", [[CELEX, "it", 5000, "true"]], force=True)
    cellar_listing.vouched(URL, 1972, ("it",), 1, "old", force=True)
    dates, _ = cellar_listing.coverage({(CELEX, "it")}, {"it": 0.0}, URL)
    assert dates[(CELEX, "it")] == 5000, "the changed unit reads changed: Cellar's tree"
