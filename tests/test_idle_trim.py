"""The web gives back its freed heap once idle (app/idle_trim.py; operator,
2026-11-22: "Can we do B plus C after a timeout?"; München measured 677 MiB
anon warm, 237 MiB after a 12 ms malloc_trim, the next pass at the same
speed). Once per idle period, never per request."""
from __future__ import annotations

import time

from app import config, idle_trim


def test_it_is_due_once_per_idle_period(monkeypatch):
    idle_trim.note()
    now = time.monotonic()
    assert not idle_trim.due(now + 299, 300), "not before the timeout"
    assert idle_trim.due(now + 301, 300), "idle for the timeout: trim"
    assert not idle_trim.due(now + 900, 300), "once per idle period, however long"
    idle_trim.note()
    now = time.monotonic()
    assert not idle_trim.due(now + 10, 300), "a request restarts the clock"
    assert idle_trim.due(now + 301, 300), "and the next idle period trims again"
    idle_trim.note()
    assert not idle_trim.due(time.monotonic() + 10_000, 0), "a timeout of 0 never trims"


def test_the_trim_is_glibcs_and_says_what_it_gave_back(monkeypatch):
    calls: list[int] = []

    class Libc:
        def malloc_trim(self, pad):
            calls.append(pad)
            return 1
    monkeypatch.setattr(idle_trim.ctypes, "CDLL", lambda name: Libc())
    said: list[str] = []
    assert idle_trim.trim(log=said.append) is True and calls == [0]
    assert said[0].startswith("[web] idle ") and "malloc_trim(0)" in said[0] and "MiB" in said[0]


def test_without_glibc_nothing_is_trimmed(monkeypatch):
    def missing(name):
        raise OSError("no libc.so.6")
    monkeypatch.setattr(idle_trim.ctypes, "CDLL", missing)
    assert idle_trim.trim(log=lambda line: None) is False


def test_the_real_trim_runs_here():
    assert idle_trim.trim(log=lambda line: None) in (True, False)


def test_every_request_notes_the_time_and_the_timer_starts_once(isolated_config, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import app
    monkeypatch.setattr(idle_trim, "_last", 0.0)
    monkeypatch.setattr(idle_trim, "_started", False)
    started: list = []
    monkeypatch.setattr(idle_trim.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: started.append(kw["name"])})())
    monkeypatch.setattr(config, "WEB_IDLE_TRIM_S", 300.0)
    with TestClient(app) as c:
        c.get("/healthz")
    assert idle_trim._last > 0, "the request was noted"
    with TestClient(app) as c:
        c.get("/healthz")
    assert started == ["idle-trim"], "one timer thread per process"
