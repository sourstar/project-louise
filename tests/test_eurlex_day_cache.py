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


def test_every_app_file_that_runs_to_derive_a_day_is_in_the_digest(tmp_path):
    """Architecture's reads of 2fdd1e31 (notice.py left out) and ac45c617 (a
    fixed list cannot fail for a module it does not name): `_unit_day` and the
    walk are run on the fixtures under a profiler, and every file of app/
    that ran must be hashed."""
    import os
    import sys
    from pathlib import Path
    from tests.test_eurlex_parser4 import UNITS
    root = Path(src.__file__).resolve().parents[3] / "app"
    ran: set[Path] = set()

    def prof(frame, event, arg):
        if event == "call":
            f = frame.f_code.co_filename
            if f.startswith(str(root)):
                ran.add(Path(f).resolve())
    for celex in UNITS:
        base = tmp_path / celex
        unit = _lay(base, celex)
        entries = sorted(os.scandir(unit), key=lambda e: e.name)
        sys.setprofile(prof)
        try:
            src._unit_day(unit, celex, entries, SOURCE.TREE_FACTS)
        finally:
            sys.setprofile(None)
    hashed = {p.resolve() for p in src._day_modules()}
    assert ran, "the profiler saw the derivation"
    assert ran <= hashed, f"derives the day, not hashed: {sorted(str(p) for p in ran - hashed)}"


def test_a_cache_that_cannot_be_opened_for_another_reason_is_left_alone(tmp_path, monkeypatch):
    import sqlite3
    _lay(tmp_path, "31972R1473")
    _ids(tmp_path)
    db = next(tmp_path.rglob("days.sqlite"))
    real = sqlite3.connect

    def emfile(*a, **k):
        e = sqlite3.OperationalError("unable to open database file")
        e.sqlite_errorname = "SQLITE_CANTOPEN"
        raise e
    monkeypatch.setattr(sqlite3, "connect", emfile)
    assert len(_ids(tmp_path)) == 1
    monkeypatch.setattr(sqlite3, "connect", real)
    assert not list(db.parent.glob("days.bad.*")), "a healthy cache is never moved aside"
    assert real(db).execute("SELECT count(*) FROM day").fetchone()[0] == 1


def test_a_transient_read_error_is_never_kept(tmp_path, monkeypatch):
    """Architecture's read of 2fdd1e31, 2: EIO on a first walk was kept as
    the unit's error and the unit stayed out of every walk."""
    _lay(tmp_path, "31972R1473")
    real = src._unit_day
    monkeypatch.setattr(src, "_unit_day", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "Input/output error")))
    assert _ids(tmp_path) == []
    monkeypatch.setattr(src, "_unit_day", real)
    assert len(_ids(tmp_path)) == 1, "healthy again: read again, not the kept error"


def test_a_corrupt_cache_is_said_and_made_anew(tmp_path, capsys):
    _lay(tmp_path, "31972R1473")
    _ids(tmp_path)
    db = next(tmp_path.rglob("days.sqlite"))
    for side in db.parent.glob("days.sqlite-*"):
        side.unlink()
    db.write_bytes(b"not a database at all" * 100)
    src._SAID.clear()
    assert len(_ids(tmp_path)) == 1
    assert "does not open" in capsys.readouterr().out
    assert list(db.parent.glob("days.bad.*.sqlite")), "moved aside, with its -wal and -shm"
    import sqlite3
    assert sqlite3.connect(db).execute("SELECT count(*) FROM day").fetchone()[0] == 1, "made anew, and kept"
