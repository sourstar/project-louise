"""A DEAD-MAN'S SWITCH: a process that one thread holds leaves by itself.

On 2026-11-24 the worker stopped for 21 minutes: one pipeline thread sat in a
single C call (a regex that never ended), and CPython's GIL never left it. No
other thread ran — no collection embedded, no log line, SIGTERM was not
honoured — and nothing inside the process could stop the call. An agent
found it with py-spy and restarted the container by hand (CLAUDE.md: "Louise
survives a crash without an agent"; a process that needs an agent to come
back is a defect).

THE SWITCH is the operating system's interval timer and the standard
library's fault handler, both of which work without the GIL:

- `faulthandler.register(SIGALRM, all_threads=True, chain=True)`: at the
  signal a C handler writes every thread's stack to stderr, then the
  signal's default action ends the process (by SIGALRM: exit 142 as a
  container reports it — docs/07's exit codes);
- `signal.setitimer(ITIMER_REAL, T)` sends that signal T seconds from now;
- a daemon thread sets the timer AGAIN every T / REARM_PARTS. It needs the
  GIL to do so. While any thread can run, the timer never fires; when one
  C call has held the process for T, it does.

The container's restart policy then starts the process again, and the stacks
in its log say where it was held — what py-spy said on 2026-11-24, with no
one there. What was being parsed is in app/parse_journal.py, which keeps the
restart from walking into the same document.

THE STANDARD named, and why not a variant (CLAUDE.md, "do not reinvent"):
`faulthandler.dump_traceback_later(T, exit=True)` is the library's own
watchdog and would do the same in one call, but it leaves with exit 1, which
docs/07 gives to a failed command; a signal's exit is this event's alone.
An outside watchdog (systemd's WatchdogSec=, a container HEALTHCHECK with an
orchestrator that restarts on it) is the usual standard where one exists:
the node runs plain `docker compose`, which does not restart an unhealthy
container. Adopting one would take a supervisor in front of the containers.

WHAT IT COSTS: a process frozen from OUTSIDE for longer than T — `docker
pause`, SIGSTOP — meets its timer at the thaw and leaves (the clock is the
wall's). Stop such a process instead, or set LOUISE_DEADMAN_S=0 for the time.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time

#: seconds one thread may hold a process before it leaves; LOUISE_DEADMAN_S
#: overrides, 0 switches it off. Ten minutes: nothing a healthy worker does
#: holds the GIL in one call for a tenth of that (its long calls — SQLite,
#: Lance, HTTP, sleep — release it), and the node's 21 minutes become 10.
DEFAULT_S = 600.0
#: the timer is set again this many times within its own length
REARM_PARTS = 6
ENV = "LOUISE_DEADMAN_S"

_lock = threading.Lock()
_armed = 0.0
_stop = threading.Event()


def setting(default: float = DEFAULT_S) -> float:
    """LOUISE_DEADMAN_S where it is a number, else `default`."""
    raw = os.environ.get(ENV)
    if raw is None or not raw.strip():
        return float(default)
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(default)


def armed() -> float:
    """The seconds the switch is armed for; 0 when it is not."""
    return _armed


def arm(seconds: float | None = None, *, default: float = DEFAULT_S, who: str = "") -> bool:
    """Arm the switch for this process, once: `seconds`, or LOUISE_DEADMAN_S,
    or `default`. False where it is off (0) or the platform has no interval
    timer. Never a reason not to start."""
    global _armed
    t = float(seconds) if seconds is not None else setting(default)
    if t <= 0 or not hasattr(signal, "setitimer") or not hasattr(signal, "SIGALRM"):
        return False
    with _lock:
        if _armed:
            return True
        try:
            import faulthandler
            faulthandler.register(signal.SIGALRM, all_threads=True, chain=True)
            signal.setitimer(signal.ITIMER_REAL, t)
        except (ValueError, OSError, AttributeError, RuntimeError):
            return False
        _armed = t
        _stop.clear()
    threading.Thread(target=_rearm, args=(t,), name="deadman", daemon=True).start()
    print(f"{time.strftime('%H:%M:%S')} [node info:deadman] armed{' for ' + who if who else ''}: if one thread "
          f"holds this process for {t:.0f} s, every thread's stack is printed and the process leaves by "
          f"itself (SIGALRM, exit 142); {ENV}=0 switches it off", file=sys.stderr, flush=True)
    return True


def _rearm(t: float) -> None:
    # the wait gives the GIL up; setting the timer again needs it back
    while not _stop.wait(t / REARM_PARTS):
        try:
            signal.setitimer(signal.ITIMER_REAL, t)
        except (ValueError, OSError):
            return


def disarm() -> None:
    """The timer cleared and the handler given back (tests; a clean end)."""
    global _armed
    with _lock:
        if not _armed:
            return
        _stop.set()
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            import faulthandler
            faulthandler.unregister(signal.SIGALRM)
        except (ValueError, OSError, AttributeError, RuntimeError):
            pass
        _armed = 0.0
