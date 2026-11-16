"""History on request (app/ask/history.py; operator, 2026-11-16: "No history
but implement fetch upon request with a limit on the number of fetches per
query and a timeout per fetch request. Return in the answer if a fetch was
not successful and why, so it never fails silently"; "Option B but fetch
triggered by a query has higher prio over the regular worker's fetching"):
an EU act's versions the collection lacks, fetched by the WORKER on the
answering process's request into its history slice — at most LIMIT a
question, each bounded, on a Cellar connection ahead of the regular pass,
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

from app.acquire import specs as _specs
BASE = _specs.ACQUIRE_REGISTRY["eurlex"].endpoints[0].url
ACT = "31979D0491"
Q = "storia dell'articolo 1 della decisione 79/491/CEE"
LANGS24 = tuple(f"lang={l}" for l in sorted(src.LANGUAGES))


def coll(corpus: Path, slices=LANGS24):
    return SimpleNamespace(name="eurlex4", plugin="eurlex", corpus_dir=corpus, slices=slices,
                           manifest_path=corpus.parent / "state" / "manifest.sqlite",
                           codex=SimpleNamespace(parser=SimpleNamespace(version=4)),
                           plugin_instance=lambda: SOURCE)


@pytest.fixture(autouse=True)
def acknowledged(monkeypatch):
    """The collection's terms acknowledged (invariant 9) unless a test says not."""
    from app import licensing
    monkeypatch.setattr(licensing, "state_for", lambda c, spec=None: licensing.ACKNOWLEDGED)


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
    assert out == {"facts": [], "fetches": [], "landed": {}} and not cellar_mock.sparql and not cellar_mock.gets
    out = _fetch(coll(tmp_path), "the history of data protection", history=True)
    assert out["fetches"] == [] and not cellar_mock.sparql, "no act cited: nothing asked"


def test_a_missing_version_lands_in_the_origin_first_in_line_and_is_pinned(tmp_path, cellar_mock):
    corpus = tmp_path / "origin"
    c = coll(corpus)
    out = _fetch(c, history=True)
    assert [(f["version"], f["status"], f["why"]) for f in out["fetches"]] == [(CONS[0], "fetched", None)]
    unit = corpus / src.HISTORY / src.unit_rel("it", CONS[0])
    assert (unit / rdf.TREE_FILE).is_file() and out["landed"] == {"eurlex4": [str(unit)]}
    # every read ran on a Cellar connection, bound to the transfer manager at
    # INTERACTIVE priority with the deadline's stop event (invariant 5)
    assert cellar_mock.gets and all(p == Priority.INTERACTIVE and s is not None for _u, p, s in cellar_mock.gets)
    # the collection's own corpus: discovered like any unit
    refs = [r for r in SOURCE.discover(DiscoverState(corpus_root=corpus)) if r.locator == str(unit)]
    assert len(refs) == 1 and refs[0].error is None and SOURCE.ref_of_unit(unit) == refs[0]
    (fact,) = out["facts"]
    assert "fetched now" in fact["label"] and fact["fields"]["in force from"] == "2006-10-25"
    assert fact["fields"]["text"] and fact["fields"]["fetched"] is True
    assert cellar_mock.logged == out["fetches"], "each fetch in the collection's event log"
    gets = len(cellar_mock.gets)
    assert _fetch(c, history=True)["fetches"] == [] and len(cellar_mock.gets) == gets, "held: nothing fetched"


def test_without_the_licence_acknowledgement_nothing_is_fetched(tmp_path, cellar_mock, monkeypatch):
    from app import licensing
    monkeypatch.setattr(licensing, "state_for", lambda c, spec=None: licensing.MISSING)
    monkeypatch.setattr(licensing, "license_class_for", lambda c, spec=None: "yellow")
    (f,) = _fetch(coll(tmp_path), history=True)["fetches"]
    assert f["status"] == "refused" and "invariant 9" in f["why"]
    assert not cellar_mock.sparql and not cellar_mock.gets


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


def test_the_question_s_whole_bound_ends_what_is_left(tmp_path, cellar_mock, monkeypatch):
    cellar_mock.listed = [f"0{ACT[1:]}-{y}0101" for y in (2001, 2003)]
    monkeypatch.setattr(history, "REQUEST_S", 0.0)
    (f,) = _fetch(coll(tmp_path), history=True)["fetches"]
    assert f["status"] == "timeout" and "whole bound" in f["why"] and not cellar_mock.sparql


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

    def fake(query, collections, *, as_of=None, history=False, store=None, query_lang=None):
        calls.append((query, as_of, history, query_lang))
        return {"facts": [{"entity_id": "celex:x~art1", "label": "fetched now"}],
                "fetches": [{"collection": "eurlex4", "act": ACT, "language": "it", "version": "v",
                             "status": "timeout", "why": "not done within 20 s"}]}
    monkeypatch.setattr(history, "ask", fake)

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
    monkeypatch.setattr(history, "ask", lambda *a, **kw: {"facts": [], "fetches": fetches})
    result: dict = {"hits": [], "facts": [], "query_lang": "it"}
    web._attach_history(result, web.AskQueryBody(query="Articolo 1 della decisione 79/491/CEE"))
    assert "history" not in result, "not a history question: nothing asked"
    web._attach_history(result, web.AskQueryBody(query=Q))
    html = web.TEMPLATES.env.get_template("_ask_results.html").render(result={**result, "timings": {}})
    assert "not done within 20 s" in html and "fetched now" in html
    assert "1 more version not fetched, at most 3 a question" in html and "v3" in html


# ---- the worker's half -----------------------------------------------------------------

def test_register_adds_exactly_the_given_units(isolated_config, tmp_path):
    """discovery.register: the units the worker placed, into the manifest as a
    scan writes them — and nothing else, whatever else the corpus holds."""
    from app import collections as collections_mod, discovery
    corpus = tmp_path / "origin"
    rel = Path(src.HISTORY) / src.unit_rel("it", CONS[0])
    unit = corpus / rel
    unit.parent.mkdir(parents=True)
    import shutil
    shutil.copytree(CONS[2], unit, ignore=shutil.ignore_patterns("notice.xml", "LEG_MTD*"))
    other = corpus / src.unit_rel("it", "31972R1473")
    other.mkdir(parents=True)
    (other / rdf.TREE_FILE).write_text("{}")
    c = collections_mod.Collection(
        name="eurlex4", plugin="eurlex", profile=None, embed_mode_default=None, corpus_dir=corpus,
        manifest_path=tmp_path / "state" / "manifest.sqlite", fts_dir=tmp_path / "fts",
        lance_dir=tmp_path / "lancedb")
    c.manifest_path.parent.mkdir(parents=True)
    con = db.connect(c)
    got = discovery.register(con, c, [SOURCE.ref_of_unit(unit), None])
    assert got["added"] == 1
    rows = con.execute("SELECT uid, path, in_scope, embed_done FROM documents").fetchall()
    assert [(r["path"], r["in_scope"], r["embed_done"]) for r in rows] == [(str(unit), 1, 0)]
    assert discovery.register(con, c, [SOURCE.ref_of_unit(unit)])["added"] == 0, "idempotent"
    con.close()


def test_a_request_is_taken_once_answered_and_read(tmp_path):
    rid = history.post({"query": "q"}, tmp_path)
    assert [r for r, _ in history.take(tmp_path)] == [rid] and history.take(tmp_path) == [], "claimed once"
    history.answer(rid, {"facts": [], "fetches": [{"status": "fetched"}]}, tmp_path)
    assert history.wait(rid, tmp_path) == {"facts": [], "fetches": [{"status": "fetched"}]}
    assert list(tmp_path.iterdir()) == [], "nothing left behind"


def test_a_request_no_worker_takes_is_withdrawn_and_said(tmp_path):
    rid = history.post({"query": "q"}, tmp_path)
    assert history.wait(rid, tmp_path, pickup_s=0.2, answer_s=5) == "not-taken"
    assert history.take(tmp_path) == [], "withdrawn: a worker starting later does not serve it"
    rid = history.post({"query": "q"}, tmp_path)
    history.take(tmp_path)
    assert history.wait(rid, tmp_path, pickup_s=0.1, answer_s=0.3) == "no-answer", "taken, never answered"
    assert history.sweep(tmp_path, now=time.time() + history.STALE_S + 1) == 1


def test_ask_posts_only_a_history_question_citing_an_eu_act_and_says_a_missing_worker(tmp_path):
    c = coll(tmp_path / "origin")
    assert history.ask("Articolo 1 della decisione 79/491/CEE", [c], where=tmp_path) == {"facts": [], "fetches": []}
    assert history.ask("the history of data protection", [c], history=True, where=tmp_path)["fetches"] == []
    plain = SimpleNamespace(name="normattiva", plugin="normattiva", codex=None)
    assert history.ask(Q, [plain], history=True, where=tmp_path)["fetches"] == []
    assert not list(tmp_path.glob("*.req")), "nothing posted"
    (f,) = history.ask(Q, [c], history=True, where=tmp_path, pickup_s=0.2)["fetches"]
    assert f["status"] == "unreachable" and "is it running" in f["why"]


def test_ask_gets_what_the_worker_served(tmp_path, monkeypatch):
    import threading
    c = coll(tmp_path / "origin")
    seen = []

    def serve(request, *, halt=None):
        seen.append(request)
        return {"facts": [{"label": "fetched now"}], "fetches": [{"status": "fetched"}]}
    monkeypatch.setattr(history, "serve", serve)

    def worker():
        for _ in range(50):
            for rid, request in history.take(tmp_path):
                history.serve_one(rid, request, tmp_path)
                return
            time.sleep(0.02)
    t = threading.Thread(target=worker)
    t.start()
    got = history.ask(Q, [c], history=True, query_lang="it", where=tmp_path)
    t.join()
    assert got == {"facts": [{"label": "fetched now"}], "fetches": [{"status": "fetched"}]}
    assert seen[0]["collections"] == ["eurlex4"] and seen[0]["history"] is True and seen[0]["query_lang"] == "it"


@pytest.mark.parametrize("mode, fetched, registered", [("running", True, True), ("acquire-only", True, False),
                                                       ("seed-only", False, False), ("paused", False, False)])
def test_serve_obeys_the_node_s_mode(tmp_path, monkeypatch, mode, fetched, registered):
    from app import collections as collections_mod, node_state
    c = coll(tmp_path / "origin")
    monkeypatch.setattr(node_state, "peek_worker_state", lambda: mode)
    monkeypatch.setattr(node_state, "profile_runs", lambda activity: True)
    monkeypatch.setattr(node_state, "paused_collections", lambda con=None: set())
    monkeypatch.setattr(collections_mod, "load_all", lambda: [c])
    monkeypatch.setattr(history, "fetch", lambda *a, **kw: {"facts": [], "fetches": [{"status": "fetched"}],
                                                             "landed": {"eurlex4": ["/u"]}})
    reg = []
    got = history.serve({"query": Q, "collections": ["eurlex4"], "history": True},
                        register=lambda col, units: reg.append((col.name, units)))
    if fetched:
        assert got["fetches"] == [{"status": "fetched"}]
    else:
        (f,) = got["fetches"]
        assert f["status"] == "refused" and mode in f["why"]
    assert bool(reg) is registered


def test_serve_refuses_a_paused_collection_and_names_one_not_held(tmp_path, monkeypatch):
    from app import collections as collections_mod, node_state
    monkeypatch.setattr(node_state, "peek_worker_state", lambda: "running")
    monkeypatch.setattr(node_state, "profile_runs", lambda activity: True)
    monkeypatch.setattr(node_state, "paused_collections", lambda con=None: {"eurlex4"})
    monkeypatch.setattr(collections_mod, "load_all", lambda: [coll(tmp_path)])
    got = history.serve({"query": Q, "collections": ["eurlex4", "gone"], "history": True})
    assert [(f["collection"], f["status"]) for f in got["fetches"]] == [("eurlex4", "refused"), ("gone", "error")]


def test_the_worker_registers_exactly_the_landed_units(isolated_config, tmp_path):
    """history._register: one manifest row per landed unit, placed by the
    slice pass — never a scan of the corpus."""
    import shutil
    from app import collections as collections_mod
    c = collections_mod.Collection(
        name="eurlex4", plugin="eurlex", profile=None, embed_mode_default=None, corpus_dir=tmp_path / "origin",
        manifest_path=tmp_path / "state" / "manifest.sqlite", fts_dir=tmp_path / "fts",
        lance_dir=tmp_path / "lancedb")
    c.manifest_path.parent.mkdir(parents=True)
    unit = c.corpus_dir / src.HISTORY / src.unit_rel("it", CONS[0])
    shutil.copytree(CONS[2], unit, ignore=shutil.ignore_patterns("notice.xml", "LEG_MTD*"))
    other = c.corpus_dir / src.unit_rel("it", "31972R1473")
    other.mkdir(parents=True)
    history._register(c, [unit])
    con = db.connect(c)
    assert [r["path"] for r in con.execute("SELECT path FROM documents")] == [str(unit)]
    con.close()


# ---- first in line ---------------------------------------------------------------------

class _OneSlot:
    """A manager with one connection to one endpoint, and the real gate."""
    from app.transfer.manager import TransferManager as _TM
    urgent = _TM.urgent
    urgent_waiting = _TM.urgent_waiting

    def __init__(self):
        import threading
        self._lock = threading.RLock()
        self._urgent: dict = {}
        self.sem = threading.BoundedSemaphore(1)

    def endpoint_semaphore(self, key, slots):
        return self.sem

    def endpoint_slots(self, endpoint, spec):
        return 1

    def cooled_for(self, key):
        return 0.0

    def pace(self, key, min_interval):
        return 0.0


def test_a_query_s_fetch_takes_the_next_connection_before_the_regular_pass():
    """Operator, 2026-11-16: "fetch triggered by a query has higher prio over
    the regular worker's fetching". The pass waits first; the query's fetch
    arrives later and is served first."""
    import threading
    from app.transfer import pool
    m = _OneSlot()
    ranked = [("https://cellar", SimpleNamespace(url="https://cellar"))]
    assert m.sem.acquire(blocking=False)            # the pass holds the one connection
    order = []

    def take(who, priority):
        got = pool.acquire_endpoint_slot(m, ranked, None, priority=priority, bound_s=10)
        order.append(who)
        time.sleep(0.05)
        got[2].release()
    bulk = threading.Thread(target=take, args=("pass", Priority.BACKFILL))
    bulk.start()
    time.sleep(0.3)                                 # the pass is waiting
    query = threading.Thread(target=take, args=("query", Priority.INTERACTIVE))
    query.start()
    time.sleep(0.3)                                 # the query is waiting too
    m.sem.release()
    query.join(5)
    bulk.join(5)
    assert order == ["query", "pass"]
    assert not m.urgent_waiting("https://cellar"), "the gate is lifted once it has its connection"
