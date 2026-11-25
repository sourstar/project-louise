"""A wait says what it waits for, and asks for it (lessons of EUR-Lex5.next,
2026-11-25).

The operator: "Every problem encountered with a collection must become a
lesson learned reflected in RAGE", and "All the workaround made on the spot
must be reflected in RAGE so that the same collection made from scratch can
run smoothly". Four of that day's, each of which took a hand on the node:

- a scan cut short by `node pause <collection>` was never asked for again at
  its resume: the log said "resumes next scan" and the next scan was the
  interval's, hours away; the stages stood behind the slice scope (the pass at
  row 970,426 of 980,178) for thirty minutes, until the worker was restarted;
- that wait said one line, an hour before, with no number in it;
- a scan of 970 k units said nothing between "corpus scan" and "discovery
  done", for up to 55 minutes: its position was read with a debugger;
- "do not fetch" was written into a collection's file by hand: `hold config
  apply --set` refused the key and the spec did not list it.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import tomllib
from pathlib import Path

import pytest

from app import collections as collections_mod, db, discovery, louise, slice_scope, worker

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "gutenberg-mini" / "corpus"


@pytest.fixture(autouse=True)
def flags():
    yield
    worker._paused.clear()
    worker._acquire_only.clear()
    worker._take_owed_scans()
    worker._scan_owed_why.clear()
    with worker._discover_req_lock:
        worker._discover_requests.clear()
    worker._said.clear()


@pytest.fixture
def collection(isolated_config, tmp_path):
    corpus = tmp_path / "corpus_a"
    shutil.copytree(FIXTURE_CORPUS, corpus)
    return collections_mod.Collection(
        name="alpha", plugin="gutenberg", profile=None, embed_mode_default=None,
        corpus_dir=corpus, manifest_path=tmp_path / "m.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lance")


def _said(con) -> list[str]:
    return [r["message"] for r in con.execute("SELECT message FROM events ORDER BY id")]


# ---- a scan cut short by the collection's pause is its resume's -------------------------

def _scan_cut_by(monkeypatch, gone: threading.Event):
    def scan(c, coll, stop=None):
        gone.set()                        # `node pause <collection>` retires its threads
        assert stop.is_set()
        return {"interrupted": True}
    monkeypatch.setattr(discovery, "sync_collection", scan)


def test_a_scan_cut_short_by_its_collections_pause_is_asked_for_again_at_its_resume(collection, monkeypatch):
    con = db.connect(collection)
    gone = threading.Event()
    _scan_cut_by(monkeypatch, gone)
    worker._scan_only(con, collection, "interval", stop=worker._AnyStop(worker._stop, gone))
    assert any("the collection is paused; it is asked for again" in m for m in _said(con))

    # WHILE IT IS PAUSED it is not run this round: its scan stays owed, round after round
    for _ in range(3):
        assert worker._request_owed_scans([], {"alpha"}, True, {}, {}) == []
    assert worker._take_discovery_request("alpha") is None

    # AT ITS RESUME the scan is asked for — a plain scan, with its reason
    assert worker._request_owed_scans([collection], {"alpha"}, True, {"alpha": con}, {}) == ["alpha"]
    assert worker._take_discovery_request("alpha") == (worker.OWED_BY_PAUSE, False)
    assert worker._request_owed_scans([collection], {"alpha"}, True, {"alpha": con}, {}) == [], "asked once"


def test_a_collection_no_longer_held_owes_nothing(collection, monkeypatch):
    con = db.connect(collection)
    gone = threading.Event()
    _scan_cut_by(monkeypatch, gone)
    worker._scan_only(con, collection, "interval", stop=worker._AnyStop(worker._stop, gone))
    assert worker._request_owed_scans([], set(), True, {}, {}) == []        # forgotten meanwhile
    assert worker._take_owed_scans() == set() and worker._scan_owed_why == {}


def test_a_stopping_node_still_owes_nothing(collection, monkeypatch):
    # the next start scans: an owed scan is for a process that goes on
    con = db.connect(collection)
    stopping = threading.Event()
    monkeypatch.setattr(worker, "_stop", stopping)
    _scan_cut_by(monkeypatch, stopping)
    worker._scan_only(con, collection, "interval", stop=worker._AnyStop(stopping, threading.Event()))
    assert worker._take_owed_scans() == set()


def test_the_modes_owed_scan_keeps_its_own_reason(collection):
    con = db.connect(collection)
    worker._acquire_only.set()
    worker._scan_only(con, collection, "background fetch finished")
    worker._acquire_only.clear()
    assert worker._request_owed_scans([collection], {"alpha"}, True, {"alpha": con}, {}) == ["alpha"]
    assert worker._take_discovery_request("alpha") == (worker.OWED_BY_MODE, False)


# ---- the stages behind the slice scope say where it stands, and ask for its scan ----------

def _behind(con, cursor: int) -> None:
    """The pass applied for what the file says now, `cursor` rows in."""
    db.set_control(con, slice_scope.KEY, json.dumps(
        {"signature": json.dumps({"slices": None, "embed": None}), "cursor": cursor, "done": True}))


def test_the_wait_says_where_the_pass_is_and_asks_for_the_scan_it_needs(collection, monkeypatch):
    con = db.connect(collection)
    worker._scan_only(con, collection, "startup")                 # three documents
    _behind(con, 1)
    assert not slice_scope.ready(con, collection)
    assert slice_scope.waits(con, collection) == "the pass is at row 1 of 3"

    assert worker._wait_for_slice_scope(con, collection) is True, "every wait has its request"
    assert worker._take_discovery_request("alpha") == ("slice scope", False)
    line = [m for m in _said(con) if m.startswith("waiting for the slice scope")][-1]
    assert "the pass is at row 1 of 3" in line and "one is asked for now" in line
    assert "nothing is parsed, indexed or embedded until then" in line

    # ONCE A PERIOD, the line and the request together: every idle pass comes here, and a scan
    # that cannot run (the licence, the mode) must not be started again every few seconds
    before = len(_said(con))
    for _ in range(50):
        assert worker._wait_for_slice_scope(con, collection) is False
    assert len(_said(con)) == before and worker._take_discovery_request("alpha") is None
    # the period has passed, and a request still stands: none is added, the line says so
    worker._request_discovery("alpha", "interval", acquire=True)
    worker._said.clear()
    assert worker._wait_for_slice_scope(con, collection) is False
    assert "a scan is asked for" in _said(con)[-1]
    assert worker._take_discovery_request("alpha") == ("interval", True), "its own request, untouched"
    monkeypatch.setattr(worker, "scan_running", lambda name: True)
    worker._said.clear()                                          # the period has passed
    assert worker._wait_for_slice_scope(con, collection) is False, "a scan is running: none is asked for"
    assert "a scan is running" in _said(con)[-1]
    assert worker._take_discovery_request("alpha") is None


def test_changed_slices_are_said_as_what_they_are(collection):
    con = db.connect(collection)
    worker._scan_only(con, collection, "startup")
    db.set_control(con, slice_scope.KEY, json.dumps(
        {"signature": json.dumps({"slices": ["en"], "embed": None}), "cursor": 3, "done": True}))
    assert "not the ones last applied" in slice_scope.waits(con, collection)


# ---- a scan says where it is ------------------------------------------------------------------

def test_a_source_says_where_its_walk_is_and_the_platform_keeps_one_line_a_period(collection):
    con = db.connect(collection)
    p = discovery._Progress(con, every_s=0.0)
    assert p.source("at lang=fr/2010, 412,000 units walked") is True
    assert any(m.startswith("corpus scan: at lang=fr/2010, 412,000 units walked (") for m in _said(con))
    slow = discovery._Progress(con, every_s=3600.0)
    assert slow.source("too soon") is False and not any("too soon" in m for m in _said(con))


def test_the_orchestrator_says_how_many_units_it_has_taken(collection):
    con = db.connect(collection)
    p = discovery._Progress(con, every_s=0.0)
    counts = {"added": 7, "changed": 2}
    p.refs(511, counts)
    assert not any("units taken so far" in m for m in _said(con)), "looked at every 512th unit"
    p.refs(1024, counts)
    assert any("corpus scan: 1,024 units taken so far, 7 new, 2 changed" in m for m in _said(con))


def test_a_source_that_walks_in_silence_is_still_said_to_be_walking(collection, monkeypatch, capsys):
    # telemetry is published by someone other than the thread doing the work: the scan's own
    # thread is inside the source's walk, so the line comes from a heartbeat
    con = db.connect(collection)
    monkeypatch.setattr(discovery, "SAY_EVERY_S", 0.05)
    plugin = collection.plugin_instance()
    real = type(plugin).discover

    def slow(self, state):
        time.sleep(0.4)                          # sorting its ids, reading its dump
        yield from real(self, state)
    monkeypatch.setattr(type(plugin), "discover", slow)
    stats = discovery.sync_collection(con, collection)
    assert stats["added"] == 3
    out = capsys.readouterr().out
    assert "corpus scan: still walking; 0 units taken so far" in out
    assert "DiscoverState.say" in out, "the line names what the source can call"
    assert not [t for t in threading.enumerate() if t.name == "scan-progress"], "the heartbeat ends with the scan"


def test_the_source_is_handed_say_and_a_short_scan_says_nothing(collection, monkeypatch):
    con = db.connect(collection)
    plugin = collection.plugin_instance()
    real = type(plugin).discover
    seen: dict = {}

    def looking(self, state):
        seen["say"] = state.say
        state.say("walking")                     # under a period old: kept quiet
        yield from real(self, state)
    monkeypatch.setattr(type(plugin), "discover", looking)
    discovery.sync_collection(con, collection)
    assert callable(seen["say"])
    assert not any(m.startswith("corpus scan: walking") for m in _said(con)), \
        "a scan shorter than one period says nothing"


# ---- "do not fetch" is a key of the file ------------------------------------------------------

def test_do_not_fetch_is_set_by_the_command_and_listed(isolated_config, capsys):
    from app import collection_file
    assert "acquire_enabled" in collection_file.accepted()
    path = collections_mod.config_toml_path("gutenberg")
    assert louise.main(["hold", "config", "apply", "gutenberg", "--set", "acquire_enabled=true"]) == 0
    assert json.loads(capsys.readouterr().out)["written"]["acquire_enabled"] is True
    assert collections_mod.by_name("gutenberg").acquire_enabled is True
    assert louise.main(["hold", "config", "apply", "gutenberg", "--set", "acquire_enabled=false"]) == 0
    assert json.loads(capsys.readouterr().out)["written"]["acquire_enabled"] is False
    assert collections_mod.by_name("gutenberg").acquire_enabled is False
    # "default": the line goes and the collection's kind decides again
    assert louise.main(["hold", "config", "apply", "gutenberg", "--set", "acquire_enabled=default"]) == 0
    capsys.readouterr()
    assert "acquire_enabled" not in tomllib.loads(path.read_text(encoding="utf-8")) or \
        collections_mod.by_name("gutenberg").acquire_enabled is False
    before = path.read_text(encoding="utf-8")
    assert louise.main(["hold", "config", "apply", "gutenberg", "--set", "acquire_enabled=maybe"]) == 2
    assert path.read_text(encoding="utf-8") == before, "a value that does not read writes nothing"
