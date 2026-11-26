"""THE UNIT'S DAY, KEPT (lessons C1/C4, 2026-11-25: the startup scan read
every unit, ~970 k on one thread, 791-963 s on the node). An unchanged unit
is only listed and statted; a changed file, or a change to the code that
derives the day, derives it again."""
from __future__ import annotations

import os

from app.plugins.base import DiscoverState
from app.plugins.eurlex import SOURCE
from app.plugins.eurlex import source as src
from tests.test_eurlex_parser4 import _lay


def _ids(root):
    return sorted(r.native_id for r in SOURCE.discover(DiscoverState(corpus_root=root)) if r.scope == "document")


def test_an_unchanged_unit_is_not_read_again(tmp_path, monkeypatch):
    _lay(tmp_path, "31972R1473")
    first = _ids(tmp_path)
    calls = []
    real = src._unit_day
    monkeypatch.setattr(src, "_unit_day", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert _ids(tmp_path) == first and calls == [], "kept: no unit read"


def test_a_changed_file_derives_the_day_again(tmp_path, monkeypatch):
    unit = _lay(tmp_path, "31972R1473")
    first = _ids(tmp_path)
    f = next(p for p in unit.iterdir() if p.name.endswith(".xml"))
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    calls = []
    real = src._unit_day
    monkeypatch.setattr(src, "_unit_day", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert _ids(tmp_path) == first and calls == [1]


def test_a_change_to_the_code_derives_every_day_again(tmp_path, monkeypatch):
    _lay(tmp_path, "31972R1473")
    _ids(tmp_path)
    monkeypatch.setattr(src, "_day_code_digest", lambda: "another-code")
    calls = []
    real = src._unit_day
    monkeypatch.setattr(src, "_unit_day", lambda *a, **k: calls.append(1) or real(*a, **k))
    _ids(tmp_path)
    assert calls == [1]


def test_a_cache_that_does_not_open_is_no_cache(tmp_path, monkeypatch):
    import sqlite3
    _lay(tmp_path, "31972R1473")
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
    assert len(_ids(tmp_path)) == 1
