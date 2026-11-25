"""EVERY LINE OF THE LOG, WRITTEN WHOLE — whoever prints it.

München, the worker's start of 2026-11-25: the "embedding goes on: …" lines
of NeuRIS and of wikidata on ONE line of the log, no newline between them,
and an empty line after. `print(text, flush=True)` writes the text and then
its newline as two writes; two threads that print in the same instant
interleave them. The worker prints from a dozen threads, in several forms
(`db.log_event`, its node lines, tracebacks, jobs' own `log=print`), and a
log read as a time series — the first benchmark (CLAUDE.md) — must have one
event a line.

`WholeLines` stands in for the process's stdout: what a thread writes is
kept, per thread, until its line is complete, and a complete line goes to
the real stream in ONE write, under a lock. A line is never cut by another
thread's, whatever printed it. Text with no newline yet (a progress dot)
waits for its newline: this is a log, not a terminal.

The standard alternative is the `logging` module, whose handlers lock per
record; the app's lines are plain prints by design (one stream, read by
`docker logs`), and adopting `logging` would mean passing every one of them
through it. This gives the same guarantee to the prints that exist.
"""
from __future__ import annotations

import sys
import threading
from typing import Any


class WholeLines:
    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self._local = threading.local()
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        buf = getattr(self._local, "buf", "") + s
        if "\n" in buf:
            done, _, rest = buf.rpartition("\n")
            with self._lock:
                self._raw.write(done + "\n")
            self._local.buf = rest
        else:
            self._local.buf = buf
        return len(s)

    def flush(self) -> None:
        with self._lock:
            self._raw.flush()

    def __getattr__(self, name: str) -> Any:        # isatty, fileno, encoding, …: the real stream's
        return getattr(self._raw, name)


def install() -> bool:
    """Make this process's stdout write whole lines. True when it was put in
    place now; False when it already was."""
    if isinstance(sys.stdout, WholeLines):
        return False
    sys.stdout = WholeLines(sys.stdout)
    return True
