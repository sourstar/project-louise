"""The Library cards' p2p lines are computed in one child process
(app/library_p2p.py; 2026-11-22, München: a warm Library 10.56 s with the
refreshes in the web process, 1.27 s without them: pure Python holding the
GIL every request waits for)."""
from __future__ import annotations

import os
import signal
import time
from types import SimpleNamespace

import pytest

from app import config, library_p2p, web


@pytest.fixture
def child(monkeypatch):
    monkeypatch.setattr(config, "LIBRARY_P2P_CHILD", True)
    yield
    if library_p2p._ex is not None:
        library_p2p._ex.shutdown(wait=True, cancel_futures=True)
    library_p2p._ex = None
    library_p2p.LOST = None
    library_p2p._last_peak_kib = None


NOBODY = SimpleNamespace(name="nobody", manifest_path="/nowhere/nobody/state/manifest.sqlite")


def test_the_line_is_computed_in_one_child_that_stays(child):
    assert library_p2p.compute(NOBODY) is None, "a collection the child cannot find has no line"
    pids = set(library_p2p._ex._processes)
    assert pids and os.getpid() not in pids, "computed in another process"
    assert library_p2p._last_peak_kib and library_p2p._last_peak_kib > 0, "its peak, kept for the diagnostic"
    library_p2p.compute(NOBODY)
    assert set(library_p2p._ex._processes) == pids, "the same child: its views stay warm"


def test_a_child_that_dies_is_not_started_again_and_is_named(child, capsys):
    library_p2p.compute(NOBODY)
    (pid,) = library_p2p._ex._processes
    os.kill(pid, signal.SIGKILL)
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    got = library_p2p.compute(NOBODY)
    assert got["lost"]["collection"] == "nobody" and got["lost"]["peak"].endswith("MiB")
    err = capsys.readouterr().err
    assert "[library] nobody:" in err and "not started again until the web restarts" in err
    ex = library_p2p._ex
    assert library_p2p.compute(NOBODY) == got, "said on every card from now on"
    assert library_p2p._ex is ex and all(p.exitcode is not None for p in ex._processes.values()), \
        "no new child alive (an OOM kill is not relaunched)"
    assert capsys.readouterr().err == "", "said once in the log"


def test_without_the_child_the_line_is_computed_here(monkeypatch):
    monkeypatch.setattr(config, "LIBRARY_P2P_CHILD", False)
    seen = []
    monkeypatch.setattr(library_p2p, "compute_here", lambda c: seen.append(c.name) or {"parts": 3})
    assert web._p2p_compute(NOBODY) == {"parts": 3} and seen == ["nobody"]
    assert library_p2p._ex is None


@pytest.mark.parametrize("line,says", [
    ({"lost": {"collection": "wikipedia", "at": "2026-11-22 18:00:00", "peak": "812 MiB"}},
     ("p2p: not counted — the process that counts it ended while counting wikipedia at 2026-11-22 18:00:00 "
      "(its last peak: 812 MiB), and is not started again until the web restarts")),
    (web.P2P_COUNTING, "p2p: the coverage of its parts is being counted; it shows on a later view"),
])
def test_the_card_says_what_its_line_is(line, says, isolated_config, monkeypatch):
    from fastapi.testclient import TestClient

    from app import node_state, spaces
    from app.api import app
    monkeypatch.setattr(spaces, "resolve_embed_model_hash", lambda *a: ("abc123", False))
    monkeypatch.setattr(web, "_ping", lambda url: True)
    monkeypatch.setattr(web, "_p2p_summary", lambda c: line)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    node_state.set_worker_state("running")
    with TestClient(app) as c:
        page = c.get("/admin/library", headers={"X-Admin-Token": "sekrit"}).text
    assert says in " ".join(page.split())


def test_an_answer_not_back_in_time_stops_that_child_and_a_new_one_counts(child, monkeypatch, capsys):
    monkeypatch.setattr(library_p2p, "ANSWER_S", 0.5)
    hung = SimpleNamespace(name="hung", manifest_path="/nowhere/hung/state/manifest.sqlite")
    real = library_p2p._in_child
    monkeypatch.setattr(library_p2p, "_in_child", _sleeps)
    t0 = time.monotonic()
    assert library_p2p.compute(hung) is None, "no line this time"
    assert time.monotonic() - t0 < 5, "the wait is bounded"
    err = capsys.readouterr().err
    assert "[library] hung: the Library's p2p count did not answer" in err and "a new one counts" in err
    assert library_p2p.LOST is None, "a stop for not answering is not a death"
    assert library_p2p._ex is None, "the next count starts a new child"
    monkeypatch.setattr(library_p2p, "_in_child", real)
    assert library_p2p.compute(NOBODY) is None and library_p2p._ex is not None
    assert any(p.exitcode is None for p in library_p2p._ex._processes.values()), "a new child, alive"


def _sleeps(name, manifest_path):
    time.sleep(30)
    return None, 1
