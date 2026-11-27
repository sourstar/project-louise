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


def _sparql(counts, pages):
    asked = []

    def sparql(client, base, query, *, what="", charge=None):
        asked.append(what)
        if "COUNT(" in query:
            return [{"n": {"value": str(counts.pop(0))}, "max": {"value": "2026-11-20T00:00:00Z"}}]
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


def test_more_rows_than_the_count_re_counted_otherwise_is_said_as_what_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    sparql, _asked = _sparql([3060, 3060], [_rows(3063)])
    monkeypatch.setattr(cellar, "sparql", sparql)
    with pytest.raises(cellar.TransportError) as e:
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


def test_a_window_that_failed_in_prepare_is_asked_again_after_its_hold(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, (1970,))
    calls = []
    after = int(time.time()) - 10 ** 7

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        calls.append(year)
        if len(calls) == 1:
            raise cellar.TransportError("cellar: 503 to the count of 1970 (an episode)")
        cellar_listing.keep(url, year, langs, 1, None, [["31970R0001", "it", after, "true"]], force=True)
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    units = cellar.TreeUnits()
    units._prepare_bulk(base, corpus, ["it"], due, "eu", URL)                 # 1970 fails
    unit = {"celex": "31970R0001", "lang": "it"}
    with pytest.raises(cellar.WindowNotListed):
        units._bulk_of(corpus, unit, due[0], corpus / due[0].rel_path)       # inside the hold: waits
    assert calls == [1970]
    t = time.time()
    monkeypatch.setattr(time, "time", lambda: t + cellar.WINDOW_FAILED_HOLD_S + 1)
    assert units._bulk_of(corpus, unit, due[0], corpus / due[0].rel_path)[1] == after, "asked again, listed"
    assert calls == [1970, 1970]
