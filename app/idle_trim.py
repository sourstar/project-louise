"""The web gives back the heap it freed once it has been idle (operator,
2026-11-22: the web's budget "should be less than 1GB"; "Can we do B plus C
after a timeout?").

glibc keeps what a process frees in its arenas, for the next allocation, and
gives it back to the system only from the top of each heap. `malloc_trim(0)`
returns every free page the arenas hold. Measured on the live image (München,
8 collections x 3 queries, warm, MALLOC_ARENA_MAX=2 and one shared Lance
Session): 677 MiB anon, then 237 MiB after a trim of 12 ms; the next pass ran
at the same speed and grew to 354 MiB.

ONCE PER IDLE PERIOD, NEVER PER REQUEST: a timer thread trims when no
QUESTION has started or ended for `timeout_s()` seconds, and not again until a
question has come and the web has been idle once more. The operator's
amendment, 2026-11-22: "after 15 minutes without a question", configurable:
platform.toml's `[web] idle_trim_s` (desired state, invariant 10), 900 by
default, 0 never, read at every check so an edit needs no restart.
- A QUESTION is an Ask request (`QUESTION_PATHS`): the Ask page and its calls
  (`/ask`, `/ask/...`: a search, an answer, its relay, its translation, its
  voice, its pdf), and asking by machine (`/api/search`, `/api/ask/...`,
  `/v1/chat/completions`). The Library, the System page, the status and
  events routes and the rest do not reset the timer: a trim beside them
  costs them nothing but the allocator's locks for its milliseconds.
- Each question notes the time as it starts and as it ends (`IdleClock`,
  pure ASGI): two assignments.

THE STANDARD: glibc's own call (malloc.h), the one app/parallel.py already
makes for the parse children. Its alternative, `MALLOC_TRIM_THRESHOLD_`,
trims on every free above it: measured 243 MiB but 30-60 % slower on some
searches, so not taken. Not glibc (musl, macOS): no trim, nothing else
changes.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time

#: how often the timer looks, at most: a trim lands within this of the idle
#: timeout
CHECK_S = 30.0
#: what a question is: an Ask request, by its path's start (see above)
QUESTION_PATHS = ("/ask", "/api/search", "/api/ask/", "/v1/chat/completions")
#: the timeout where platform.toml cannot be read: the file's own default
DEFAULT_S = 900.0

_last = time.monotonic()
_trimmed = False
_started = False
_lock = threading.Lock()


def timeout_s() -> float:
    """THE RESOLUTION of the idle timeout (invariant 10: what runs and what
    is shown come from here): platform.toml's `[web] idle_trim_s`, 900 by
    default, 0 never. A file that does not read keeps the default: giving
    memory back is never a reason to fail."""
    from . import platform_config
    try:
        return float(platform_config.load().web.idle_trim_s)
    except Exception:   # noqa: BLE001 — the file's own error is said where it is loaded
        return DEFAULT_S


def is_question(path: str) -> bool:
    return any(path == p or path.startswith(p if p.endswith("/") else p + "/") or
               (p.endswith("/") and path == p[:-1]) for p in QUESTION_PATHS)


def note() -> None:
    """A question started or ended: the web is not idle, and the next idle
    period may trim again."""
    global _last, _trimmed
    _last = time.monotonic()
    _trimmed = False


def due(now: float, idle_s: float) -> bool:
    """Whether to trim now: idle for `idle_s`, and not trimmed since the last
    request. True claims the period: it is not due again until `note`."""
    global _trimmed
    with _lock:
        if idle_s <= 0 or _trimmed or now - _last < idle_s:
            return False
        _trimmed = True
        return True


def _rss_anon_kib() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("RssAnon:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def trim(log=None) -> bool:
    """glibc's malloc_trim(0), said in one line with the anon memory before
    and after. False where there is no glibc. Never raises."""
    log = log or (lambda line: print(line, file=sys.stderr, flush=True))
    try:
        fn = ctypes.CDLL("libc.so.6").malloc_trim
    except (OSError, AttributeError):
        return False
    before, t0 = _rss_anon_kib(), time.perf_counter()
    try:
        fn(0)
    except Exception:   # noqa: BLE001 — giving memory back is never a reason to fail
        return False
    ms, after = (time.perf_counter() - t0) * 1000, _rss_anon_kib()
    said = (f"anon {before // 1024} -> {after // 1024} MiB" if before is not None and after is not None
            else "anon not readable")
    log(f"[web] no question for {timeout_s():.0f} s: malloc_trim(0) gave back the freed heap, "
        f"{said} ({ms:.0f} ms)")
    return True


def _loop() -> None:
    while True:
        idle_s = timeout_s()
        time.sleep(min(idle_s, CHECK_S) if idle_s > 0 else CHECK_S)
        if due(time.monotonic(), idle_s):
            trim()


def start() -> bool:
    """The timer thread, once per process; it reads the timeout at every
    check, and a timeout of 0 never trims."""
    global _started
    with _lock:
        if _started:
            return False
        _started = True
    threading.Thread(target=_loop, name="idle-trim", daemon=True).start()
    return True


class IdleClock:
    """ASGI middleware: every QUESTION notes the time as it starts and as it
    ends (`note`); other requests pass untouched. Pure ASGI, as
    auth.ViewerGate, so a caller's disconnect still reaches the search."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_question(scope.get("path", "")):
            return await self.app(scope, receive, send)
        note()
        try:
            return await self.app(scope, receive, send)
        finally:
            note()
