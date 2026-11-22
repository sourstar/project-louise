"""The web gives back its freed heap once idle (app/idle_trim.py; operator,
2026-11-22: "Can we do B plus C after a timeout?", amended: "after 15
minutes without a question", configurable; München measured 677 MiB anon
warm, 237 MiB after a 12 ms malloc_trim, the next pass at the same speed).
Once per idle period, never per request; only a question resets it."""
from __future__ import annotations

import time

import pytest

from app import idle_trim, platform_config


def test_it_is_due_once_per_idle_period():
    idle_trim.note()
    now = time.monotonic()
    assert not idle_trim.due(now + 899, 900), "not before the timeout"
    assert idle_trim.due(now + 901, 900), "idle for the timeout: trim"
    assert not idle_trim.due(now + 9000, 900), "once per idle period, however long"
    idle_trim.note()
    now = time.monotonic()
    assert not idle_trim.due(now + 10, 900), "a question restarts the clock"
    assert idle_trim.due(now + 901, 900), "and the next idle period trims again"
    idle_trim.note()
    assert not idle_trim.due(time.monotonic() + 100_000, 0), "a timeout of 0 never trims"


def test_the_timeout_is_platform_tomls_with_900_by_default(isolated_config):
    p = platform_config.platform_path()
    p.unlink(missing_ok=True)
    assert idle_trim.timeout_s() == 900, "no file: 15 minutes"
    p.write_text("[web]\nidle_trim_s = 120\n", encoding="utf-8")
    assert idle_trim.timeout_s() == 120, "read at every check: no restart"
    p.write_text("[web]\nidle_trim_s = 0\n", encoding="utf-8")
    assert idle_trim.timeout_s() == 0
    p.write_text("[web]\nidle_trim_s = -5\n", encoding="utf-8")
    with pytest.raises(platform_config.schedule_mod.ScheduleError, match="idle_trim_s"):
        platform_config.load()
    assert idle_trim.timeout_s() == idle_trim.DEFAULT_S, "a file that does not read keeps the default"


@pytest.mark.parametrize("path,question", [
    ("/ask", True), ("/ask/query", True), ("/ask/relay/7/events", True), ("/ask/pdf", True),
    ("/api/search", True), ("/api/ask/pdf/abc", True), ("/v1/chat/completions", True),
    ("/library", False), ("/admin/library", False), ("/api/status", False), ("/api/events", False),
    ("/healthz", False), ("/askew", False), ("/api/searches", False),
])
def test_only_a_question_resets_the_clock(path, question):
    assert idle_trim.is_question(path) is question


def test_the_trim_is_glibcs_and_says_what_it_gave_back(monkeypatch):
    calls: list[int] = []

    class Libc:
        def malloc_trim(self, pad):
            calls.append(pad)
            return 1
    monkeypatch.setattr(idle_trim.ctypes, "CDLL", lambda name: Libc())
    monkeypatch.setattr(idle_trim, "timeout_s", lambda: 900.0)
    said: list[str] = []
    assert idle_trim.trim(log=said.append) is True and calls == [0]
    assert said[0].startswith("[web] no question for 900 s: malloc_trim(0)") and "MiB" in said[0]


def test_without_glibc_nothing_is_trimmed(monkeypatch):
    def missing(name):
        raise OSError("no libc.so.6")
    monkeypatch.setattr(idle_trim.ctypes, "CDLL", missing)
    assert idle_trim.trim(log=lambda line: None) is False


def test_the_real_trim_runs_here():
    assert idle_trim.trim(log=lambda line: None) in (True, False)


def test_a_question_notes_the_time_a_library_view_does_not_and_one_timer(isolated_config, monkeypatch):
    from fastapi.testclient import TestClient

    from app import warmup
    from app.api import app
    monkeypatch.setattr(warmup, "start", lambda *a, **k: None)   # not this test's (see test_lance_session)
    monkeypatch.setattr(idle_trim, "_started", False)
    started: list = []
    monkeypatch.setattr(idle_trim.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: started.append(kw["name"])})())
    with TestClient(app) as c:
        monkeypatch.setattr(idle_trim, "_last", 0.0)
        c.get("/healthz")
        c.get("/library")
        assert idle_trim._last == 0.0, "not a question: the clock runs on"
        c.post("/api/search", json={})
        assert idle_trim._last > 0, "a question: noted"
    with TestClient(app) as c:
        c.get("/healthz")
    assert started.count("idle-trim") == 1, "one timer thread per process"


def test_node_show_and_the_system_page_say_the_value_in_force(isolated_config, monkeypatch):
    from app import node_show, web
    platform_config.platform_path().write_text("[web]\nidle_trim_s = 600\n", encoding="utf-8")
    assert node_show.show()["web"] == {"idle_trim_s": 600.0}
    assert web._idle_trim_said() == "600 s without a question"
    platform_config.platform_path().write_text("[web]\nidle_trim_s = 0\n", encoding="utf-8")
    assert web._idle_trim_said() == "never"
