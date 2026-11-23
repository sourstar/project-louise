"""V5 (the EUR-Lex5 review, 2026-11-21): a version fetched on request is
read on the parse pool, within what is left of the question's time, only
where the node's mode parses — and a version whose text is not pinned says
why, never a bare "fetched"."""
from __future__ import annotations

import time

from app.ask import history
from app.plugins.base import UnindexableDocument
from app.plugins.eurlex import SOURCE
from tests.ask.test_history import _fetch, acknowledged, cellar_mock, coll  # noqa: F401


def test_a_slow_read_does_not_outlast_the_question(tmp_path, cellar_mock, monkeypatch):
    monkeypatch.setattr(history, "_short_of_space", lambda corpus: None)
    monkeypatch.setattr(history, "REQUEST_S", 1.0)
    monkeypatch.setattr(history, "FETCH_TIMEOUT_S", 1.0)
    real = SOURCE.parse

    def slow(raw):
        time.sleep(3.0)                    # a 39 MB Formex unit: 5.9 s; its XHTML 24 s (STATUS 11-19)
        return real(raw)
    monkeypatch.setattr(SOURCE, "parse", slow)
    t0 = time.monotonic()
    out = _fetch(coll(tmp_path / "origin"), history=True)
    took = time.monotonic() - t0
    assert took <= history.REQUEST_S + 0.5, f"took {took:.1f} s, bound {history.REQUEST_S} s"
    (f,) = out["fetches"]
    assert f["status"] == "fetched" and "not read within the question's time" in f["why"]
    assert out["facts"] == [] and out["landed"], "landed, said, and no fact"


def test_a_refused_read_says_why(tmp_path, cellar_mock, monkeypatch):
    def refused(raw):
        raise UnindexableDocument("a file is not the one its name says: the unit is fetched again")
    monkeypatch.setattr(SOURCE, "parse", refused)
    monkeypatch.setattr(history, "_short_of_space", lambda corpus: None)
    out = _fetch(coll(tmp_path / "origin"), history=True)
    (f,) = out["fetches"]
    assert out["facts"] == [] and f["status"] == "fetched"
    assert "could not be read" in f["why"] and "not the one its name says" in f["why"]


def test_a_node_that_parses_nothing_lands_the_version_and_reads_nothing(tmp_path, cellar_mock, monkeypatch):
    called = []
    monkeypatch.setattr(SOURCE, "parse", lambda raw: called.append(1))
    monkeypatch.setattr(history, "_short_of_space", lambda corpus: None)
    out = _fetch(coll(tmp_path / "origin"), history=True, may_parse=False)
    (f,) = out["fetches"]
    assert not called and out["facts"] == [] and out["landed"]
    assert f["status"] == "fetched" and "parses nothing in its current mode" in f["why"]


def test_the_read_goes_through_the_parse_pool(tmp_path, cellar_mock, monkeypatch):
    from app import parallel
    kinds = []
    real = parallel.compute

    def spy(kind, collection, rows):
        kinds.append((kind, sorted(rows[0])))
        return real(kind, collection, rows)
    monkeypatch.setattr(parallel, "compute", spy)
    monkeypatch.setattr(history, "_short_of_space", lambda corpus: None)
    out = _fetch(coll(tmp_path / "origin"), history=True)
    assert kinds == [("doc", ["native_id", "path", "size", "uid"])] and out["facts"]
    assert out["fetches"][0]["why"] is None, "pinned: nothing to say"
