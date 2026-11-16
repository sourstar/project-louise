"""History on request (app/ask/history.py; operator, 2026-11-16: "No history
but implement fetch upon request with a limit on the number of fetches per
query and a timeout per fetch request. Return in the answer if a fetch was
not successful and why, so it never fails silently"; "The only new part is
the fetch on demand"): an EU act's versions the collection lacks, fetched at
ask time into its history slice — at most LIMIT a question, each bounded,
every byte through the budget — and every one not fetched said with its
reason. Never on an ordinary search."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import db, query_service
from app.acquire import cellar
from app.acquire.spec import TransportError
from app.ask import history, results
from app.plugins.base import DiscoverState
from app.plugins.eurlex import SOURCE, from_tree, rdf
from app.plugins.eurlex import source as src
from app.transfer.bucket import Priority
from app.transfer.manager import current_handle
from tests.acquire.test_eurlex_dump import CONS

BASE = "https://cellar.test"
ACT = "31979D0491"
Q = "storia dell'articolo 1 della decisione 79/491/CEE"
LANGS24 = tuple(f"lang={l}" for l in sorted(src.LANGUAGES))


def coll(corpus: Path, slices=LANGS24):
    return SimpleNamespace(name="eurlex4", plugin="eurlex", corpus_dir=corpus, slices=slices,
                           codex=SimpleNamespace(parser=SimpleNamespace(version=4)),
                           plugin_instance=lambda: SOURCE)


@pytest.fixture
def cellar_mock(monkeypatch):
    """Cellar: the act's versions as `versions_of` lists them, and the one
    consolidation the fixtures hold (01979D0491-20061025, Italian) as its
    tree and items. Records what was asked, and the binding each read ran in."""
    state = SimpleNamespace(listed=[CONS[0]], sparql=[], gets=[], logged=[])
    # the fixture's MTD file is another work's (the dump test's); its scoped
    # tree is the consolidation's own, served as read
    served: dict[str, bytes] = {cellar.resource_url(BASE, CONS[0]): (CONS[2] / rdf.TREE_FILE).read_bytes()}
    monkeypatch.setattr(cellar, "_read_tree", lambda data, celex: rdf.loads(data))
    scoped = rdf.loads((CONS[2] / rdf.TREE_FILE).read_bytes())
    for it in from_tree.notice(scoped, "ITA", CONS[0]).items["fmx4"]:
        served[it.uri] = (CONS[2] / it.name).read_bytes()

    def sparql(client, base, query, *, what="", charge=None):
        state.sparql.append(query)
        return [{"celex": {"value": c}} for c in state.listed]

    def get(client, url, headers, what, *, charge, ok=(200,)):
        h = current_handle()
        state.gets.append((url, getattr(h, "priority", None), getattr(h, "stop", None)))
        charge(len(served[url]))
        return SimpleNamespace(content=served[url], headers={})
    monkeypatch.setattr(cellar, "sparql", sparql)
    monkeypatch.setattr(cellar, "_get", get)
    monkeypatch.setattr(history, "_endpoint", lambda: BASE)
    monkeypatch.setattr(history, "_log", lambda cs, fetches: state.logged.extend(fetches))
    return state


def _fetch(c, query=Q, **kw):
    kw.setdefault("query_lang", "it")
    return history.fetch(query, [c], client=object(), **kw)


def test_words_ask_for_the_past_in_each_language_and_a_plain_citation_does_not():
    for q in (Q, "history of Article 5 of Regulation (EU) 2016/679", "previous versions of Article 5 GDPR",
              "historique de l'article 17 du règlement (UE) 2016/679", "frühere Fassung des Artikels 5",
              "Fassungen von Artikel 6 der Verordnung (EU) 2016/679", "versioni dell'articolo 5"):
        assert history.asks(q), q
    for q in ("Article 5 of Regulation (EU) 2016/679", "art. 17 du règlement (UE) 2016/679",
              "Articolo 1 della decisione 79/491/CEE", "storage of personal data"):
        assert not history.asks(q), q


def test_an_ordinary_question_asks_the_origin_nothing(tmp_path, cellar_mock):
    out = history.fetch("Articolo 1 della decisione 79/491/CEE", [coll(tmp_path)], client=object())
    assert out == {"facts": [], "fetches": []} and not cellar_mock.sparql and not cellar_mock.gets
    out = _fetch(coll(tmp_path), "the history of data protection", history=True)
    assert out == {"facts": [], "fetches": []} and not cellar_mock.sparql, "no act cited: nothing asked"


def test_a_missing_version_lands_in_the_history_slice_bound_to_the_budget_and_is_pinned(tmp_path, cellar_mock):
    corpus = tmp_path / "origin"
    out = _fetch(coll(corpus), history=True)
    assert [(f["version"], f["status"], f["why"]) for f in out["fetches"]] == [(CONS[0], "fetched", None)]
    unit = corpus / src.HISTORY / src.unit_rel("it", CONS[0])
    assert (unit / rdf.TREE_FILE).is_file()
    # every read ran bound to the transfer manager at INTERACTIVE priority,
    # with the deadline's stop event (invariant 5; the per-fetch timeout)
    assert cellar_mock.gets and all(p == Priority.INTERACTIVE and s is not None for _u, p, s in cellar_mock.gets)
    # the collection's own corpus: discovered, parsed and indexed like any unit
    refs = [r for r in SOURCE.discover(DiscoverState(corpus_root=corpus)) if r.locator == str(unit)]
    assert len(refs) == 1 and refs[0].error is None
    (fact,) = out["facts"]
    assert "fetched now" in fact["label"] and fact["fields"]["in force from"] == "2006-10-25"
    assert fact["fields"]["text"] and fact["fields"]["fetched"] is True
    assert cellar_mock.logged == out["fetches"], "each fetch in the collection's event log"
    # asked again: held, nothing fetched
    gets = len(cellar_mock.gets)
    assert _fetch(coll(corpus), history=True)["fetches"] == [] and len(cellar_mock.gets) == gets


def test_at_most_limit_fetches_a_question_newest_first_and_the_rest_said(tmp_path, cellar_mock, monkeypatch):
    cellar_mock.listed = [f"0{ACT[1:]}-{y}0101" for y in (2001, 2003, 2005, 2007, 2009)]
    asked = []
    monkeypatch.setattr(cellar, "fetch_unit_from_tree", lambda base, celex, lang, dest, **kw: asked.append(celex))
    out = _fetch(coll(tmp_path), history=True)
    assert asked == [f"0{ACT[1:]}-{y}0101" for y in (2009, 2007, 2005)]
    assert [f["status"] for f in out["fetches"]] == ["fetched"] * 3 + ["limit"] * 2
    assert all(f"at most {history.LIMIT}" in f["why"] for f in out["fetches"][3:])


def test_as_of_fetches_only_the_version_in_force_that_day(tmp_path, cellar_mock, monkeypatch):
    cellar_mock.listed = [f"0{ACT[1:]}-{d}" for d in ("20010101", "20061025", "20100101")]
    asked = []
    monkeypatch.setattr(cellar, "fetch_unit_from_tree", lambda base, celex, lang, dest, **kw: asked.append(celex))
    _fetch(coll(tmp_path), "Articolo 1 della decisione 79/491/CEE", as_of=20080101)
    assert asked == [f"0{ACT[1:]}-20061025"]
    asked.clear()
    assert _fetch(coll(tmp_path), "Articolo 1 della decisione 79/491/CEE", as_of=19990101)["fetches"] == []
    assert asked == [], "before every consolidation: the original, held, is in force"


def test_wanted_versions_skips_the_held_and_needs_a_question():
    listed = ["01979D0491-20010101", "01979D0491-20061025"]
    assert history.wanted_versions(listed, {"01979D0491-20061025"}, 20080101, False) == []
    assert history.wanted_versions(listed, {"01979D0491-20010101"}, None, True) == ["01979D0491-20061025"]
    assert history.wanted_versions(listed, set(), None, False) == []


def test_a_fetch_past_its_deadline_is_said_timeout(tmp_path, cellar_mock, monkeypatch):
    monkeypatch.setattr(history, "FETCH_TIMEOUT_S", 0.2)

    def slow(base, celex, lang, dest, **kw):
        while True:                   # a read that keeps coming, honouring the bound stop
            cellar._stop_check()
            time.sleep(0.01)
    monkeypatch.setattr(cellar, "fetch_unit_from_tree", slow)
    t0 = time.monotonic()
    out = _fetch(coll(tmp_path), history=True)
    assert time.monotonic() - t0 < 2.0
    (f,) = out["fetches"]
    assert f["status"] == "timeout" and "within 0 s" in f["why"] and out["facts"] == []


@pytest.mark.parametrize("error, status", [
    (TransportError("cellar: HTTP 404 for the tree of 01979D0491-20061025"), "not-found"),
    (TransportError("cellar: HTTP 403 for the tree of 01979D0491-20061025"), "refused"),
    (TransportError("cellar: ConnectError: [Errno 111] Connection refused"), "unreachable"),
    (TransportError("cellar: ConnectError: name resolution failed"), "unreachable"),
    (TransportError("cellar: ReadTimeout: timed out"), "timeout"),
    (ValueError("a tree that does not read"), "error"),
])
def test_every_failure_is_said_with_its_reason(tmp_path, cellar_mock, monkeypatch, error, status):
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(cellar, "fetch_unit_from_tree", fail)
    (f,) = _fetch(coll(tmp_path), history=True)["fetches"]
    assert f["status"] == status and str(error) in f["why"]


def test_a_listing_that_fails_is_said(tmp_path, cellar_mock, monkeypatch):
    def down(*a, **kw):
        raise TransportError("cellar: ConnectError: unreachable host")
    monkeypatch.setattr(cellar, "versions_of", down)
    (f,) = _fetch(coll(tmp_path), history=True)["fetches"]
    assert f["status"] == "unreachable" and f["version"] is None and "could not be listed" in f["why"]


def test_a_language_not_held_is_refused_and_a_held_history_slice_asks_nothing(tmp_path, cellar_mock):
    (f,) = _fetch(coll(tmp_path, slices=("lang=fr",)), history=True)["fetches"]
    assert f["status"] == "refused" and "lang=it" in f["why"] and not cellar_mock.sparql
    out = _fetch(coll(tmp_path, slices=LANGS24 + ("history/lang=it",)), history=True)
    assert out["fetches"] == [] and not cellar_mock.sparql, "the history slice held: its versions are held"


def test_below_the_free_space_floor_nothing_is_fetched(tmp_path, cellar_mock, monkeypatch):
    from app import disk_guard
    monkeypatch.setattr(disk_guard, "usage", lambda p: {"path": str(p), "free": 1, "total": 10, "floor": 5,
                                                        "short": True})
    (f,) = _fetch(coll(tmp_path), history=True)["fetches"]
    assert f["status"] == "refused" and "floor" in f["why"] and not cellar_mock.gets


def test_only_eurlex_on_parser_4_is_asked(tmp_path, cellar_mock):
    old = coll(tmp_path)
    old.codex = SimpleNamespace(parser=SimpleNamespace(version=3))
    other = SimpleNamespace(name="normattiva", plugin="normattiva", corpus_dir=tmp_path, slices=None, codex=None)
    assert _fetch(old, history=True)["fetches"] == [] and _fetch(other, history=True)["fetches"] == []
    assert not cellar_mock.sparql


# ---- the search ------------------------------------------------------------------------

def test_the_search_fetches_only_for_a_history_or_as_of_question(tmp_path, monkeypatch):
    legacy = SimpleNamespace(name="gutenberg", plugin="gutenberg", codex=None)
    monkeypatch.setattr(query_service, "resolve_collections", lambda names, **kw: [legacy])
    monkeypatch.setattr(db, "connect", lambda c: sqlite3.connect(":memory:"))
    calls = []

    def fake(query, collections, *, as_of=None, history=False, store=None, query_lang=None, client=None):
        calls.append((query, as_of, history, query_lang))
        return {"facts": [{"entity_id": "celex:x~art1", "label": "fetched now"}],
                "fetches": [{"collection": "eurlex4", "act": ACT, "language": "it", "version": "v",
                             "status": "timeout", "why": "not done within 20 s"}]}
    monkeypatch.setattr(history, "fetch", fake)

    def run(names, query, k, mode, translate, **kw):
        return {"hits": [], "reranked": False, "timings": {}, "query_lang": "it"}
    plain = results.search("Articolo 1 della decisione 79/491/CEE", root=tmp_path, run=run)
    assert calls == [] and plain["history"] is None, "an ordinary search asks nothing"
    out = results.search(Q, root=tmp_path, run=run)
    assert calls[-1] == (Q, None, True, "it")
    assert out["facts"][-1]["label"] == "fetched now"
    assert out["history"] == {"requested": True, "limit": history.LIMIT, "timeout_s": history.FETCH_TIMEOUT_S,
                              "fetches": [{"collection": "eurlex4", "act": ACT, "language": "it", "version": "v",
                                           "status": "timeout", "why": "not done within 20 s"}]}
    results.search("Articolo 1 della decisione 79/491/CEE", root=tmp_path, run=run, history=True)
    assert calls[-1][2] is True


def test_the_ask_page_shows_every_fetch_and_why(monkeypatch):
    from app import web
    monkeypatch.setattr(query_service, "resolve_collections", lambda names, **kw: [])
    fetches = [{"collection": "eurlex4", "act": ACT, "language": "it", "version": "v1", "status": "fetched",
                "why": None},
               {"collection": "eurlex4", "act": ACT, "language": "it", "version": "v2", "status": "timeout",
                "why": "not done within 20 s"},
               {"collection": "eurlex4", "act": ACT, "language": "it", "version": "v3", "status": "limit",
                "why": "at most 3 fetches a question"}]
    monkeypatch.setattr(history, "fetch", lambda *a, **kw: {"facts": [], "fetches": fetches})
    result: dict = {"hits": [], "facts": [], "query_lang": "it"}
    web._attach_history(result, web.AskQueryBody(query="Articolo 1 della decisione 79/491/CEE"))
    assert "history" not in result, "not a history question: nothing asked"
    web._attach_history(result, web.AskQueryBody(query=Q))
    html = web.TEMPLATES.env.get_template("_ask_results.html").render(result={**result, "timings": {}})
    assert "not done within 20 s" in html and "fetched now" in html
    assert "1 more version not fetched, at most 3 a question" in html and "v3" in html
