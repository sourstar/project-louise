"""A stop ends a command at 130 and leaves no child computing for nobody
(invariant 2; München, 2026-11-08: one SIGTERM to `hold collection new` gave
exit 143 at once and left 18 parse children re-parented to the container's
init, at 31-36 % of a core each, until killed by hand)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import louise, parallel

ROOT = Path(__file__).resolve().parents[1]

needs_default_sigterm = pytest.mark.skipif(
    signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None),
    reason="the runner handles SIGTERM itself")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                            # a zombie is not computing
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def test_a_pool_child_whose_parent_dies_stops(tmp_path):
    """The parent (a pool's owner) dies with a task in flight: its child
    notices within the poll and exits, instead of finishing for nobody."""
    pidfile = tmp_path / "child.pid"
    middle = textwrap.dedent(f"""
        import os, sys, time, multiprocessing
        sys.path.insert(0, {str(ROOT)!r})
        from concurrent.futures import ProcessPoolExecutor
        from app import parallel
        if __name__ == "__main__":
            ex = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"),
                                     initializer=parallel.watch_parent)
            pid = ex.submit(os.getpid).result()
            open({str(pidfile)!r}, "w").write(str(pid))
            ex.submit(time.sleep, 120)            # a long unit in flight
            time.sleep(0.5)
            os._exit(0)                           # the parent dies: no shutdown
    """)
    script = tmp_path / "middle.py"
    script.write_text(middle)
    subprocess.run([sys.executable, str(script)], check=True, timeout=60)
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 4 * parallel.ORPHAN_POLL_S + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.2)
    alive = _alive(child)
    if alive:
        os.kill(child, signal.SIGKILL)
    assert not alive, "the orphaned child is still computing"


@needs_default_sigterm
def test_sigterm_to_a_command_with_no_handler_of_its_own_ends_it_at_130(monkeypatch, tmp_path):
    def fn(_a):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)
        return 0
    with pytest.raises(louise.Stopped):
        louise._run_releasing(SimpleNamespace(fn=fn))
    assert signal.getsignal(signal.SIGTERM) in (signal.SIG_DFL, None), "the handler before it, restored"
    monkeypatch.setattr(louise, "_run_releasing", lambda a: (_ for _ in ()).throw(louise.Stopped("SIGTERM")))
    (tmp_path / "collections").mkdir()
    assert louise.main(["hold", "collection", "show", "x", "--root", str(tmp_path / "collections")]) == 130


@needs_default_sigterm
def test_a_graceful_step_gets_the_stop_and_the_handlers_come_back():
    before = signal.getsignal(signal.SIGTERM)
    with louise._graceful_stop() as stop:
        os.kill(os.getpid(), signal.SIGTERM)
        deadline = time.monotonic() + 2
        while not stop.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stop.is_set(), "the step is told, and finishes its unit"
    assert signal.getsignal(signal.SIGTERM) == before
