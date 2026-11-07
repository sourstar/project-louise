"""A checkpoint's development PROCESSES (München, 2026-11-07: "stopping the
systemd unit does NOT stop a docker exec'd fetch"; app/dev_processes.py).

`node checkpoint run -- <command…>` starts a step as a child recorded in the
open checkpoint — pid, command line, start time — and forwards SIGTERM to
it; `beat --dev-pid` records one started otherwise. The restore stops each
recorded process FIRST: SIGTERM to the very process recorded (a reused pid,
a dead one, one in another container are not signalled), a bounded wait,
SIGKILL only if it is still there, each said. Real child processes here."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app import dev_processes, louise, node_state
from app import working_state as ws

#: a Louise pass, as far as a signal goes: SIGTERM -> its unit ends, exit 130
STEP = ("import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *a: sys.exit(130))\n"
        "open(sys.argv[1], 'w').close()\n"
        "time.sleep(120)\n")
#: one that does not stop when asked
STUBBORN = ("import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "open(sys.argv[1], 'w').close()\n"
            "time.sleep(120)\n")


@pytest.fixture
def started(tmp_path):
    """Start a step and wait until its handler is set; every one killed after."""
    procs: list[subprocess.Popen] = []

    def start(code: str = STEP, *extra: str) -> subprocess.Popen:
        ready = tmp_path / f"ready-{len(procs)}"
        p = subprocess.Popen([sys.executable, "-c", code, str(ready), *extra], start_new_session=True)
        procs.append(p)
        _until(ready.exists)
        return p
    yield start
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()


def _until(cond, timeout: float = 30.0) -> None:
    t = time.monotonic()
    while not cond():
        assert time.monotonic() - t < timeout, "timed out"
        time.sleep(0.02)


@pytest.fixture
def open_checkpoint(isolated_config):
    node_state.set_worker_state("running", by="test")
    ws.save(note="dev")


# ---- run ---------------------------------------------------------------------------

def test_run_records_the_step_forwards_sigterm_and_exits_with_its_code(open_checkpoint, tmp_path):
    ready = tmp_path / "ready"
    seen: dict = {}

    def the_watchdog_signals_run():
        _until(lambda: ready.exists() and (ws.read().get("dev_processes") or []))
        seen["procs"] = ws.read()["dev_processes"]
        os.kill(os.getpid(), signal.SIGTERM)        # to `run`, which forwards it to its child
    t = threading.Thread(target=the_watchdog_signals_run)
    t.start()
    before = signal.getsignal(signal.SIGTERM)
    rc = ws.run([sys.executable, "-c", STEP, str(ready)], note="the gate")
    t.join()
    assert rc == 130, "the child got SIGTERM, ended its unit and exited 130"
    (e,) = seen["procs"]
    assert e["cmdline"] == [sys.executable, "-c", STEP, str(ready)]
    assert e["note"] == "the gate" and e["by"] == "run" and isinstance(e["start"], int)
    assert e["pgid"] == e["pid"] and e["ns"] == dev_processes.pid_namespace()
    assert ws.read()["dev_processes"] == [], "its entry goes when it ends"
    assert signal.getsignal(signal.SIGTERM) is before, "the handler is given back"
    assert ws.read()["last_beat"] == ws.read()["saved_at"], "run does not beat"


def test_run_from_the_command_line(isolated_config, tmp_path, capsys):
    made = tmp_path / "made"
    argv = ["node", "checkpoint", "run", "--note", "x", "--", "sh", "-c", f"touch {made}; exit 7"]
    assert louise.main(argv) == 2 and not made.exists()
    assert "nothing was started" in capsys.readouterr().err
    ws.save()
    assert louise.main(argv) == 7 and made.exists()
    assert "recorded in the checkpoint" in capsys.readouterr().err
    assert ws.read()["dev_processes"] == []
    assert louise.main(["node", "checkpoint", "run", "--", "sh", "-c", "kill -9 $$"]) == 137
    assert louise.main(["node", "checkpoint", "run"]) == 2
    assert louise.main(["node", "checkpoint", "run", "--", "/no/such/step"]) == 2
    assert "cannot be started" in capsys.readouterr().err


# ---- the restore stops them first ----------------------------------------------------

def test_restore_stops_a_live_step_before_it_changes_the_state(open_checkpoint, started, monkeypatch):
    p = started()
    ws.beat(pids=[p.pid])
    node_state.set_worker_state("acquire-only", by="dev")
    at_mode_write: list = []
    real = node_state.set_worker_state

    def spy(*a, **kw):
        at_mode_write.append(p.poll())
        return real(*a, **kw)
    monkeypatch.setattr(node_state, "set_worker_state", spy)
    got = ws.restore(by="t", grace_s=30)
    (r,) = got["processes"]
    assert (r["pid"], r["result"], r["state"]) == (p.pid, "terminated", "running")
    assert at_mode_write == [130], "the step had ended (exit 130) before the mode was written"
    assert node_state.worker_state() == "running"
    assert ws.read()["restored"]["processes"] == got["processes"]


def test_a_dead_step_a_reused_pid_and_another_container_are_not_signalled(open_checkpoint, started):
    dead = started()
    ws.beat(pids=[dead.pid])
    dead.kill()
    dead.wait()
    reused, elsewhere = started(), started()
    ws.beat(pids=[reused.pid, elsewhere.pid])
    rec = ws.read()
    rec["dev_processes"][1]["start"] -= 1           # the pid names another process now
    rec["dev_processes"][2]["ns"] = "pid:[1]"       # recorded in another container
    ws._write(rec)
    assert [p["state"] for p in ws.status()["processes"]] == ["gone", "reused", "elsewhere"]
    got = ws.restore(by="t", grace_s=5)
    assert [r["result"] for r in got["processes"]] == ["gone", "reused", "elsewhere"]
    assert reused.poll() is None and elsewhere.poll() is None, "never signalled"
    assert "not signalled" in got["processes"][1]["says"]


def test_a_step_that_ignores_sigterm_is_killed_after_the_bound_and_said(open_checkpoint, started):
    p = started(STUBBORN)
    ws.beat(pids=[p.pid])
    t = time.monotonic()
    code, out = ws.watch(after_s=1, now=ws._clock() + 60, grace_s=0.5)
    took = time.monotonic() - t
    assert code == ws.EXIT_RESTORED
    (r,) = out["restored"]["processes"]
    assert r["result"] == "killed" and "SIGKILLed" in r["says"]
    assert took < 0.5 + dev_processes.KILL_WAIT_S, "bounded"
    assert p.wait(timeout=5) == -signal.SIGKILL


def test_a_dry_run_names_the_steps_and_signals_nothing(open_checkpoint, started, capsys):
    p = started()
    assert louise.main(["node", "checkpoint", "beat", "--dev-pid", str(p.pid)]) == 0
    assert _json(capsys)["dev_processes"][0]["pid"] == p.pid
    assert ws.restore(dry_run=True)["processes"][0]["state"] == "running"
    code, out = ws.watch(after_s=1, now=ws._clock() + 60, dry_run=True)
    assert code == ws.EXIT_EXPIRED_DRY and out["processes"][0]["pid"] == p.pid
    assert louise.main(["node", "checkpoint", "status"]) == 0
    assert f"process:    {p.pid} running" in capsys.readouterr().out
    assert p.poll() is None
    ws.save(replace=True)
    assert [e["pid"] for e in ws.read()["dev_processes"]] == [p.pid], "a replace carries them"


def test_the_node_itself_is_never_a_step(open_checkpoint, started, capsys):
    worker_like = started(STEP, "-m", "app.worker")
    for pid in (1, os.getpid(), worker_like.pid, 2 ** 22 + 7):
        assert louise.main(["node", "checkpoint", "beat", "--dev-pid", str(pid)]) == 2, pid
        assert "--dev-pid" in capsys.readouterr().err
    assert ws.read().get("dev_processes") == []


def _json(capsys) -> dict:
    import json
    return json.loads(capsys.readouterr().out)


def test_the_docs_say_how_an_agent_starts_a_step():
    root = Path(__file__).resolve().parents[1]
    for doc in ("deploy/watchdog/README.md", "docs/notes/node-runbook.md"):
        text = (root / doc).read_text(encoding="utf-8")
        assert "docker exec ix-louise-rag-worker-1" in text and "checkpoint run --" in text, doc
