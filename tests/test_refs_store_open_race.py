"""Openers of the refs store at once (2026-11-21): the column check and its
ALTER ran with no lock (the second ALTER: "duplicate column name"), and a
store not yet in WAL refused its switch to WAL at once, "database is locked"
(SQLite runs no busy handler for a journal-mode change). The ALTER checked
again under BEGIN IMMEDIATE; the switch retried, bounded."""
from __future__ import annotations

import sqlite3
import threading

from app.refs import store


def _old_store(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ref_meta (source_id TEXT PRIMARY KEY, ref_version INTEGER, synced_at INTEGER)")
    con.commit()
    con.close()


def test_many_openers_at_once_add_the_column_once(tmp_path):
    for round_ in range(40):
        path = tmp_path / f"refs{round_}.sqlite"
        _old_store(path)
        gate, errors = threading.Barrier(8), []

        def open_it():
            gate.wait()
            try:
                store.connect(path).close()
            except Exception as e:   # noqa: BLE001 — collected
                errors.append(e)
        threads = [threading.Thread(target=open_it) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info('ref_meta')")]
        assert cols.count("input_mtime") == 1


def test_the_stale_check_is_checked_again_under_the_lock(tmp_path, monkeypatch):
    path = tmp_path / "refs.sqlite"
    _old_store(path)
    store.connect(path).close()                     # the column is there now
    seen = iter([False, True])                      # the first, unlocked read is stale
    monkeypatch.setattr(store, "_has_input_mtime", lambda con: next(seen))
    store.connect(path).close()                     # no ALTER: no "duplicate column name"
