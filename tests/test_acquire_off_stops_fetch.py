"""`acquire_enabled = false` stops the fetch ALREADY RUNNING, at its next unit
(Legal and München, 2026-11-26: EUR-Lex5.next's went on asking Cellar for
4 min 45 s after the worker had read the change, 164 more files, until `node
pause` aborted it — with a newer bulk dump unused. CLAUDE.md, the bulk first:
"do not fetch" takes effect at once)."""
import dataclasses
import inspect
import shutil
import threading
import types
from pathlib import Path

import pytest

from app import collections as collections_mod, db, worker

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "gutenberg-mini" / "corpus"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(worker, "_acquire_off_events", {})
    worker._acquire_done.clear()
    yield
    worker._acquire_done.clear()
    worker._paused_aborts.clear()
    worker._acquire_threads.clear()


@pytest.fixture
def collection(isolated_config, tmp_path):
    corpus = tmp_path / "corpus_a"
    shutil.copytree(FIXTURE_CORPUS, corpus)
    return collections_mod.Collection(
        name="alpha", plugin="gutenberg", profile="demo", embed_mode_default=None,
        corpus_dir=corpus, manifest_path=tmp_path / "m.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lance", acquire_enabled=True)


def events(con) -> list[str]:
    return [r["message"] for r in con.execute("SELECT message FROM events")]


def test_a_fetch_in_flight_ends_at_its_unit_when_the_file_turns_acquisition_off(collection, monkeypatch):
    con = db.connect(collection)
    off = dataclasses.replace(collection, acquire_enabled=False)
    seen: dict[str, bool] = {}

    def a_long_fetch(con_, coll, spec, profile, stop=None):
        seen["before"] = stop.is_set()
        worker._apply_acquire_switch(con, collection, off)      # the worker applies the edited file
        seen["after"] = stop.is_set()
        return {"fetched": ["half.part"], "aborted": 4}

    monkeypatch.setattr(worker.acquire_engine, "run_acquisition", a_long_fetch)
    worker._acquire_worker(collection, None, "demo", "config-changed")
    assert seen == {"before": False, "after": True}
    assert "alpha" not in worker._paused_aborts, "turned off is not paused: nothing re-arms it"
    assert con.execute("SELECT COUNT(*) FROM documents WHERE error IS NOT NULL").fetchone()[0] == 0


def test_the_switch_is_said_once_with_what_it_stops(collection):
    con = db.connect(collection)
    off = dataclasses.replace(collection, acquire_enabled=False)
    release = threading.Event()
    t = threading.Thread(target=release.wait, args=(10,))
    t.start()
    worker._acquire_threads["alpha"] = t
    try:
        worker._apply_acquire_switch(con, collection, off)
    finally:
        release.set()
        t.join(5)
    said = [m for m in events(con) if "acquisition turned off" in m]
    assert len(said) == 1 and "the corpus fetch in flight end(s) at the next unit" in said[0]
    assert "nothing is marked failed" in said[0] and "still scanned, parsed, indexed and embedded" in said[0]
    worker._apply_acquire_switch(con, off, off)                  # no change: nothing said
    assert len([m for m in events(con) if "acquisition turned off" in m]) == 1


def test_turned_on_again_the_next_fetch_starts_and_is_not_stopped_by_the_old_switch(collection, monkeypatch):
    con = db.connect(collection)
    off = dataclasses.replace(collection, acquire_enabled=False)
    started: list[bool] = []
    monkeypatch.setitem(worker.ACQUIRE_REGISTRY, "gutenberg", types.SimpleNamespace(profiles={"demo": None}))
    monkeypatch.setattr(worker, "_acquire_worker",
                        lambda c, spec, profile, reason: started.append(worker._acquire_off_event(c.name).is_set()))
    worker._apply_acquire_switch(con, collection, off)
    assert worker._acquire_off_event("alpha").is_set()
    worker._acquire(con, off, "config-changed")
    assert started == [], "off: nothing starts"
    worker._apply_acquire_switch(con, off, collection)
    worker._acquire(con, collection, "config-changed")
    for t in list(worker._acquire_threads.values()):
        t.join(timeout=10)
    assert started == [False], "on again: the fetch starts with the switch lifted"


def test_off_and_on_while_the_stopped_fetch_unwinds_is_asked_again_and_not_in_six_hours(collection, monkeypatch):
    """`_acquire` returns silently while a fetch thread is alive; the next
    discovery is DISCOVER_INTERVAL_MIN away. The re-arm a pause's stop uses
    asks again once the thread is gone."""
    con = db.connect(collection)
    off = dataclasses.replace(collection, acquire_enabled=False)
    monkeypatch.setitem(worker.ACQUIRE_REGISTRY, "gutenberg", types.SimpleNamespace(profiles={"demo": None}))
    release = threading.Event()
    unwinding = threading.Thread(target=release.wait, args=(10,))
    unwinding.start()
    worker._acquire_threads["alpha"] = unwinding
    try:
        worker._apply_acquire_switch(con, collection, off)       # off: the fetch is told to stop
        worker._acquire(con, collection, "config-changed")       # on again before it has gone
        assert "alpha" in worker._paused_aborts, "asked again when the thread is gone"
        assert not worker._acquire_off_event("alpha").is_set(), "and the new fetch will not be stopped by the old word"
    finally:
        release.set()
        unwinding.join(5)


def test_every_fetch_stop_carries_the_switch():
    """The corpus fetch and the archive fetch: the omission that let seed-only
    download for ten hours (2026-09-13) was a stop that did not carry the
    pause; this one did not carry the file's own "do not fetch"."""
    for fn in (worker._acquire_worker, worker._archive_worker):
        assert "_acquire_off_event(collection.name)" in inspect.getsource(fn), fn.__name__


def test_the_documented_key_is_no_longer_called_not_a_key(isolated_config, tmp_path, capsys):
    assert "acquire_enabled" not in collections_mod.DEPRECATED_CODEX_KEYS
    monkey_path = tmp_path / "x.toml"
    collections_mod._WARNED.clear()
    collections_mod._warn_deprecated(monkey_path, {"acquire_enabled": False, "corpus_dir": "/x"})
    err = capsys.readouterr().err
    assert "`corpus_dir` is not a key" in err and "acquire_enabled" not in err
