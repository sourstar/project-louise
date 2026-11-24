"""app/deadman.py: a process that one thread holds leaves by itself.

The node, 2026-11-24: one C call (a regex that never ended) held the worker's
GIL for 21 minutes, and nothing inside the process could stop it. Each case
runs in a child with a deadline, since what is tested is a process that
does not come back."""
from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

HELD = """
import re, sys
from app import deadman
assert deadman.arm({t}, who="the test")
print("armed", flush=True)
def held_in_one_call():
    # every way of cutting forty a's into runs, tried in one C call: the GIL never leaves
    re.match(r"(a+)+$", "a" * 40 + "b")
held_in_one_call()
print("came back", flush=True)
"""

BUSY = """
import time
from app import deadman
assert deadman.arm({t}, who="the test")
end = time.monotonic() + {t} * 3
n = 0
while time.monotonic() < end:        # busy, in Python: the GIL is given up, the timer set again
    n += 1
time.sleep({t} * 1.5)                # and idle
print("still here", flush=True)
"""


def _run(code: str, limit_s: float, **env):
    import os
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=limit_s, env={**os.environ, **env})
    return p.returncode, p.stdout, p.stderr, time.monotonic() - t0


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="no interval timer on this platform")
def test_a_process_held_by_one_call_prints_its_stacks_and_leaves():
    code, out, err, took = _run(HELD.format(t=2.0), 60)
    assert out.split() == ["armed"], f"it came back, or never armed: {out!r} {err[-500:]}"
    assert code == -signal.SIGALRM, f"left by the switch's own signal: {code}\n{err[-800:]}"
    assert took < 20, f"left after {took:.1f} s"
    assert "armed for the test" in err and "exit 142" in err, "what will happen is said when it is armed"
    assert "held_in_one_call" in err and "most recent call first" in err, \
        f"the stacks say where it was held:\n{err[-1500:]}"


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="no interval timer on this platform")
def test_a_busy_and_then_idle_process_is_left_alone():
    code, out, err, _ = _run(BUSY.format(t=1.0), 60)
    assert code == 0 and out.split() == ["still", "here"], f"{code} {out!r}\n{err[-800:]}"


def test_it_is_switched_off_by_its_setting(monkeypatch):
    from app import deadman
    monkeypatch.setenv(deadman.ENV, "0")
    assert deadman.setting() == 0.0 and deadman.arm(who="the test") is False and deadman.armed() == 0.0
    monkeypatch.setenv(deadman.ENV, "not a number")
    assert deadman.setting() == deadman.DEFAULT_S and deadman.setting(120) == 120.0
    monkeypatch.setenv(deadman.ENV, "45")
    assert deadman.setting(120) == 45.0


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="no interval timer on this platform")
def test_disarmed_it_gives_the_timer_and_the_signal_back():
    from app import deadman
    try:
        assert deadman.arm(300.0, who="the test") and deadman.armed() == 300.0
        assert deadman.arm(5.0) is True and deadman.armed() == 300.0, "armed once: the first setting stands"
        assert 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 300.0
    finally:
        deadman.disarm()
    assert deadman.armed() == 0.0 and signal.getitimer(signal.ITIMER_REAL)[0] == 0.0
