"""THE WORKER'S AND THE WEB'S OWN LOG FILES (2026-11-21, O1 of the EUR-Lex
review): the worker kept no log file, and Docker's log dies with each
container — every deploy and the 01:56Z kernel panic erased the evidence of
2026-11-19/20 (what was parsing before each OOM kill).

Every line a process writes to its stdout and stderr goes on to the
container's log exactly as before, and to `<config.log_dir()>/<name>.log`
too, each line behind its UTC time (the lines carry only HH:MM:SS),
rotated at `config.LOG_FILE_BYTES` with `config.LOG_FILE_BACKUPS` kept.

WHO WRITES WHICH FILE — one writer per file, since the standard rotation
is not safe across processes:
  - `worker.log`: the worker's GUARD (app/worker_guard.py), which already
    reads every line of the worker's merged stdout and stderr, so the parse
    pool's children and native code are in it; the guard's own lines (a
    memory kill's "not relaunched") too. A worker run without its guard
    writes it itself (its own lines; its children's go to the container's
    log only); `WRITTEN_BY` in a child's environment says the guard does.
  - `web.log`: the web process (app/web_main.py), before uvicorn configures
    its logging, so uvicorn's handlers write through it.

THE STANDARD: Python's `logging.handlers.RotatingFileHandler` (size-based
rotation, `<name>.log.1` … `.N`). A log that cannot be written never stops
or slows the process: said once on the original stream, then the lines go
on to the container's log alone. The standardised alternative not taken:
Docker's `local`/`json-file` driver with `max-size` — it rotates, but its
files live and die with the container, which is the defect here; a syslog
or journald driver needs the host configured, which a TrueNAS app does not
give.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from . import config

#: set in a worker's environment by its guard, which writes its log file
WRITTEN_BY = "LOUISE_LOG_FILE_WRITTEN_BY"
#: a line longer than this (a progress bar's carriage returns, no newline)
#: is written as it stands rather than held
MAX_LINE = 64 << 10


def path(name: str) -> Path:
    return config.log_dir() / f"{name}.log"


class _Handler(logging.handlers.RotatingFileHandler):
    """The rotating file, whose failure is said once and never raised."""

    def __init__(self, file: Path, say: Callable[[str], None]) -> None:
        super().__init__(file, maxBytes=max(1, int(config.LOG_FILE_BYTES)),
                         backupCount=max(0, int(config.LOG_FILE_BACKUPS)), encoding="utf-8")
        self._say = say
        self.failures = 0
        fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
        fmt.converter = time.gmtime
        self.setFormatter(fmt)

    def handleError(self, record) -> None:     # noqa: N802 — logging's name
        self.failures += 1
        if self.failures == 1:
            e = sys.exc_info()[1]
            self._say(f"[log-file] {self.baseFilename} cannot be written ({type(e).__name__}: {e}); "
                      f"the lines go on to the container's log\n")


def open_lines(name: str, *, say: Callable[[str], None] | None = None) -> Callable[[str], None] | None:
    """A writer of whole lines to `<log_dir>/<name>.log`, or None — the file
    turned off (LOG_FILE_BYTES=0), or not to be opened (said once)."""
    say = say or (lambda m: (sys.__stderr__ or sys.stderr).write(m))
    if int(config.LOG_FILE_BYTES) <= 0:
        return None
    file = path(name)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        handler = _Handler(file, say)
    except OSError as e:
        say(f"[log-file] {file} cannot be opened ({type(e).__name__}: {e}); the lines go to the "
            f"container's log only\n")
        return None

    def line(text: str) -> None:
        handler.handle(logging.LogRecord(name, logging.INFO, __file__, 0, text, None, None))
    line.handler = handler                    # type: ignore[attr-defined] — for tests and `close`
    return line


class Tee:
    """A text stream that writes through to `stream`, unchanged, and hands
    each whole line to `line`. A line is gathered PER THREAD — `print` is two
    writes, the text and its end, and another thread's line may land between
    them — so the file holds whole lines, in the order they were finished."""

    def __init__(self, stream, line: Callable[[str], None]) -> None:
        self._stream, self._line = stream, line
        self._held = threading.local()
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        n = self._stream.write(s)
        if not s:
            return n
        buf = getattr(self._held, "buf", "") + s
        done: list[str] = []
        if "\n" in buf or len(buf) > MAX_LINE:
            *done, buf = buf.split("\n")
            if len(buf) > MAX_LINE:
                done, buf = [*done, buf], ""
        self._held.buf = buf
        if done:
            with self._lock:
                for text in done:
                    try:
                        self._line(text.rstrip("\r"))
                    except Exception:   # noqa: BLE001 — the file is a copy, never the process's fault
                        pass
        return n

    def flush(self) -> None:
        self._stream.flush()

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", None)

    @property
    def errors(self):
        return getattr(self._stream, "errors", None)

    def __getattr__(self, attr):              # fileno, isatty, buffer, …: the stream's own
        return getattr(self._stream, attr)


def tee_std(name: str) -> Path | None:
    """This process's stdout and stderr written through to `<name>.log` as
    well. The entry points call it (the guard, the web, a worker without its
    guard), never a library. Returns the file, or None (off or not opened)."""
    line = open_lines(name)
    if line is None:
        return None
    sys.stdout = Tee(sys.stdout, line)
    sys.stderr = Tee(sys.stderr, line)
    return path(name)
