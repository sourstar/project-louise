"""`python -m app.worker_guard`: the worker's container command — the worker
run as a child, and KEPT DOWN after a memory kill (app/oom_hold.py; the
operator's rule of 2026-11-19, "A").

A process the kernel kills for memory gets SIGKILL and writes nothing, and
the cgroup's counter starts again with the container, so only a process
that outlives the worker can tell: this one. It is small (a pipe and a
loop) and is never the kernel's choice beside the worker.

  - a hold in force (`oom_hold.held`): no worker is started; the hold is
    said every HOLD_SAY_S, and its release is looked for every HOLD_POLL_S;
  - the worker ends by SIGKILL while the cgroup's `oom_kill` rose: the hold
    is written with the worker's last lines (what it was processing) and
    the cgroup's peak, and the worker stays down;
  - the worker exits EXIT_HELD: it wrote the hold itself (a child or another
    process of the cgroup killed for memory) and stays down;
  - any other end: the guard exits with the worker's code, and the restart
    policy acts as before — every other crash restarts on its own.
A SIGTERM or SIGINT is passed to the worker, which ends its unit (07 §7.7);
a stop while held ends the guard at once.
"""
from __future__ import annotations

import collections
import os
import signal
import subprocess
import sys
import threading
import time

from . import log_file, oom_hold

HOLD_POLL_S = 10.0
HOLD_SAY_S = 600.0
#: the worker's last lines kept for a hold: what it was processing
TAIL = 60


def run(argv: list[str] | None = None, *, command: list[str] | None = None,
        poll_s: float = HOLD_POLL_S, say_s: float = HOLD_SAY_S,
        stopping: threading.Event | None = None) -> int:
    stopping = stopping or threading.Event()
    child: list[subprocess.Popen] = []

    def on_signal(signum, _frame):
        stopping.set()
        for c in child:
            if c.poll() is None:
                c.send_signal(signum)
    old = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
    if oom_hold.oom_kills() is None:
        print(f"[worker-guard] {oom_hold.CGROUP_DIR}/memory.events does not read here (no cgroup v2): a "
              "memory kill cannot be told from another crash, and is restarted like one",
              file=sys.stderr, flush=True)
    try:
        return _loop(argv, command, poll_s, say_s, stopping, child)
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def _loop(argv, command, poll_s, say_s, stopping, child) -> int:
    said = 0.0
    while True:
        rec = oom_hold.held()
        if rec is not None:
            if stopping.is_set():
                return 0
            if time.monotonic() - said >= say_s or not said:
                print(f"[worker-guard] {oom_hold.describe(rec)}", file=sys.stderr, flush=True)
                said = time.monotonic()
            stopping.wait(poll_s)
            continue
        said = 0.0
        if stopping.is_set():
            return 0
        before = oom_hold.oom_kills()
        # unbuffered: a SIGKILL takes what a buffer held, and the last lines
        # are what the hold names
        # its lines reach the log file through `_relay` (app/log_file.py):
        # the worker does not write it again
        proc = subprocess.Popen(command or [sys.executable, "-m", "app.worker", *(argv or [])],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                env={**os.environ, "PYTHONUNBUFFERED": "1", log_file.WRITTEN_BY: "worker-guard"})
        child[:] = [proc]
        if stopping.is_set():               # a stop between the check and the start
            proc.send_signal(signal.SIGTERM)
        tail: collections.deque[str] = collections.deque(maxlen=TAIL)

        t = threading.Thread(target=_relay, args=(proc, tail), name="worker-guard-relay", daemon=True)
        t.start()
        rc = proc.wait()
        t.join(timeout=5.0)
        child[:] = []
        after = oom_hold.oom_kills()
        if rc == oom_hold.EXIT_HELD:
            # the worker wrote the hold itself; written here where it could
            # not (a full disk): the first hold in force is kept either way
            oom_hold.hold("cgroup", {"exit": rc, "oom_kill": [before, after],
                                     "memory_peak": oom_hold.memory_peak(), "last_lines": list(tail)})
            print("[worker-guard] the worker stopped itself on a memory kill in its cgroup: "
                  "not relaunched", file=sys.stderr, flush=True)
            continue
        if rc == -signal.SIGKILL and before is not None and after is not None and after > before:
            oom_hold.hold("worker", {"exit": "SIGKILL", "oom_kill": [before, after],
                                     "memory_peak": oom_hold.memory_peak(), "last_lines": list(tail)})
            print("[worker-guard] the kernel killed the worker for memory: not relaunched",
                  file=sys.stderr, flush=True)
            continue
        return rc if rc >= 0 else 128 - rc


def _relay(proc: subprocess.Popen, tail: collections.deque[str]) -> None:
    """The worker's lines to the container's log — and, through `main`'s
    `log_file.tee_std`, to the worker's log file — the last TAIL kept."""
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line.rstrip("\n"))


def main() -> int:
    # THE WORKER'S LOG FILE (O1, 2026-11-21): this process reads every line
    # the worker and its children write, and outlives them; its own lines (a
    # memory kill's hold) are the ones the file exists for
    log_file.tee_std("worker")
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
