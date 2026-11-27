"""A WINDOW'S COUNT AGAINST ITS ROWS (architecture, 2026-11-27, the 2021 nl
window of EUR-Lex5.next: a page answered 3,063 rows against a count of
3,060 and was refused as "connection timed out, the answer cut", which it
was not). More rows than the count are counted once more; an answer
Virtuoso cut by its time limit (X-SQL-State S1TAT) is an episode, never
bindings; a window that failed in prepare is asked again after its hold."""
from __future__ import annotations

import json
import threading
import time

import pytest

from app.acquire import cellar, cellar_listing
from tests.acquire.test_eurlex_listing_phase import _no_cooling_left  # noqa: F401
from tests.acquire.test_eurlex_pipeline import URL, _setup

CODE_OF = {"NLD": "nl"}


def _rows(n):
    return [{"celex": {"value": f"32021R{i:04d}"}, "language": {"value": "http://x/NLD"},
             "last": {"value": "2026-11-20T00:00:00Z"}, "force": {"value": "true"}} for i in range(n)]


FIRST_MAX = "2026-11-20T00:00:00Z"
RECOUNT_MAX = "2026-11-27T09:00:00Z"


def _sparql(counts, pages):
    asked = []
    maxes = [FIRST_MAX, RECOUNT_MAX]

    def sparql(client, base, query, *, what="", charge=None):
        asked.append(what)
        if "COUNT(" in query:
            return [{"n": {"value": str(counts.pop(0))}, "max": {"value": maxes.pop(0)}}]
        return pages.pop(0) if pages else []
    return sparql, asked


def test_more_rows_than_the_count_re_counted_the_same_keep_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    sparql, asked = _sparql([3060, 3063], [_rows(3063)])
    monkeypatch.setattr(cellar, "sparql", sparql)
    got = list(cellar._window_files(object(), URL, 2021, ("nl",), CODE_OF, lambda n: None,
                                    place=lambda *a: False))
    assert len(got) == 3063 and any("re-count" in w for w in asked)
    data = json.loads(__import__("gzip").decompress(cellar_listing._path(URL, 2021, ("nl",), True).read_bytes()))
    assert data["n"] == 3063
    assert data["last"] == FIRST_MAX, "the first count's max: a row changed after its page is listed again"


def test_more_rows_than_the_count_re_counted_otherwise_is_said_as_what_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    sparql, _asked = _sparql([3060, 3060], [_rows(3063)])
    monkeypatch.setattr(cellar, "sparql", sparql)
    from app.acquire import backoff
    with pytest.raises(backoff.Deferred) as e:
        list(cellar._window_files(object(), URL, 2021, ("nl",), CODE_OF, lambda n: None, place=lambda *a: False))
    assert "more than its count 3060 (re-counted 3060)" in str(e.value)
    assert "timed out" not in str(e.value)
    assert not cellar_listing._path(URL, 2021, ("nl",), True).exists(), "nothing kept"


def test_an_answer_virtuoso_cut_by_its_time_limit_is_an_episode_never_bindings(monkeypatch):
    import httpx
    from app.acquire import backoff
    monkeypatch.setattr(backoff, "SCALE", 0.0)
    monkeypatch.setattr(backoff, "_sleep", lambda s: None)
    body = json.dumps({"results": {"bindings": [{"n": {"value": "12"}}]}}).encode()
    answers = []

    class Resp:
        status_code = 200
        headers = {"x-sql-state": "S1TAT"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self, n):
            return iter([body])

    def stream(self, *a, **k):
        answers.append(1)
        return Resp()
    monkeypatch.setattr(httpx.Client, "stream", stream)
    with pytest.raises(cellar.TransportError) as e:
        cellar.sparql(httpx.Client(), URL, "SELECT (COUNT(*) AS ?n) WHERE {}", what="the count of 2021 (nl)",
                      charge=lambda n: None)
    assert "S1TAT" in str(e.value) and len(answers) > 1, "retried as an episode, its 12 never taken"


def test_a_row_read_twice_across_pages_is_refused(tmp_path, monkeypatch):
    """Review of c4b47315, B: rows inserted before the OFFSET between pages
    make a later page re-read rows and miss the new ones."""
    from app.acquire import backoff
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    monkeypatch.setattr(cellar, "PAGE", 3, raising=False)
    rows = _rows(4)
    sparql, _asked = _sparql([4], [rows[:3], [rows[2]]])
    monkeypatch.setattr(cellar, "sparql", sparql)
    with pytest.raises(backoff.Deferred, match="rows moved between its pages"):
        list(cellar._window_files(object(), URL, 2021, ("nl",), CODE_OF, lambda n: None, place=lambda *a: False))
    assert not cellar_listing._path(URL, 2021, ("nl",), True).exists()


def test_a_recount_that_does_not_parse_is_said_as_such(tmp_path, monkeypatch):
    from app.acquire import backoff
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    answers = [[{"n": {"value": "3060"}, "max": {"value": FIRST_MAX}}], _rows(3063), [{"x": {}}]]
    monkeypatch.setattr(cellar, "sparql", lambda *a, **k: answers.pop(0))
    with pytest.raises(backoff.Deferred, match="not a count"):
        list(cellar._window_files(object(), URL, 2021, ("nl",), CODE_OF, lambda n: None, place=lambda *a: False))


def test_a_window_that_failed_in_prepare_waits_for_the_next_pass(tmp_path, monkeypatch):
    """Architecture's choice B (2026-11-27): no fetch-thread re-ask; its
    units wait, nothing asked of Cellar for it in this pass."""
    base, corpus, due = _setup(tmp_path, monkeypatch, (1970,))
    calls = []

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        calls.append(year)
        raise cellar.TransportError("cellar: 503 to the count of 1970 (an episode)")
    monkeypatch.setattr(cellar, "_window_files", window)
    units = cellar.TreeUnits()
    units._prepare_bulk(base, corpus, ["it"], due, "eu", URL)
    unit = {"celex": "31970R0001", "lang": "it"}
    t = time.time()
    monkeypatch.setattr(time, "time", lambda: t + cellar.WINDOW_FAILED_HOLD_S + 1)
    with pytest.raises(cellar.WindowNotListed):
        units._bulk_of(corpus, unit, due[0], corpus / due[0].rel_path)
    assert calls == [1970], "asked once, in prepare"


def test_a_repeat_inside_one_page_is_kept_a_repeat_across_pages_refused(tmp_path, monkeypatch):
    from app.acquire import backoff
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    rows = _rows(3)
    inner = rows + [rows[1]]                                      # two terms of one text, one page
    sparql, _ = _sparql([4], [inner])
    monkeypatch.setattr(cellar, "sparql", sparql)
    assert len(list(cellar._window_files(object(), URL, 2021, ("nl",), CODE_OF, lambda n: None,
                                         place=lambda *a: False))) == 4
    sparql, _ = _sparql([4], [rows[:2], [rows[1], rows[2]]])
    monkeypatch.setattr(cellar, "sparql", sparql)
    with pytest.raises(backoff.Deferred, match="on two pages"):
        list(cellar._window_files(object(), URL, 2022, ("nl",), CODE_OF, lambda n: None, place=lambda *a: False))


def test_the_background_end_drops_a_due_unit_the_newest_window_lacks(tmp_path, monkeypatch):
    """Review of b96023cb, 1: another process keeps (1970, it) anew, newer
    and without U, while the background listing runs; at its end U must have
    no date (Cellar's tree), not its old one (the bulk's)."""
    from tests.acquire.test_eurlex_pipeline import _gated
    base, corpus, due = _setup(tmp_path, monkeypatch, (1970, 1971))
    gates = {1970: threading.Event(), 1971: threading.Event()}
    _gated(monkeypatch, gates)
    units = cellar.TreeUnits()
    units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    gates[1970].set()
    units.ready(due[0], None)
    key = str(corpus.resolve())
    assert ("31970R0001", "it") in units._dates[key]
    time.sleep(0.05)
    cellar_listing.keep(URL, 1970, ("it",), 0, None, [], force=True, at=time.time() + 5)   # newer, without U
    gates[1971].set()
    thread, _e, _p = units._runs[key]
    thread.join(10)
    u = {"celex": "31970R0001", "lang": "it"}
    assert units._bulk_of(corpus, u, due[0], corpus / due[0].rel_path) == (None, None), \
        "absent from the newest window: Cellar's tree"
    units.close()


def test_the_background_merge_groups_the_dates_once(tmp_path, monkeypatch):
    """Review of ea6c550a: O(dates) window_year calls at the merge, not
    O(windows x dates) under the lock."""
    from tests.acquire.test_eurlex_pipeline import _gated
    years = range(1950, 1990)
    base, corpus, due = _setup(tmp_path, monkeypatch, years)
    gates = {y: threading.Event() for y in years}
    for g in gates.values():
        g.set()
    _gated(monkeypatch, gates)
    calls = [0]
    real = cellar.window_year

    def counting(celex):
        calls[0] += 1
        return real(celex)
    monkeypatch.setattr(cellar, "window_year", counting)
    units = cellar.TreeUnits()
    units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    thread, _e, _p = units._runs[str(corpus.resolve())]
    thread.join(20)
    units.close()
    windows, n = len(years), len(due)
    assert calls[0] < windows * n / 2, f"{calls[0]} window_year calls for {windows} windows of {n} units"


def test_a_repeat_across_pages_names_its_units(tmp_path, monkeypatch):
    from app.acquire import backoff
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    rows = _rows(3)
    sparql, _ = _sparql([4], [rows[:2], [rows[1], rows[2]]])
    monkeypatch.setattr(cellar, "sparql", sparql)
    with pytest.raises(backoff.Deferred, match=r"32021R0001 \(nl\)"):
        list(cellar._window_files(object(), URL, 2022, ("nl",), CODE_OF, lambda n: None, place=lambda *a: False))
