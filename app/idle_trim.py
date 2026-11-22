"""The web gives back the heap it freed once it has been idle (operator,
2026-11-22: the web's budget "should be less than 1GB"; "Can we do B plus C
after a timeout?").

glibc keeps what a process frees in its arenas, for the next allocation, and
gives it back to the system only from the top of each heap. `malloc_trim(0)`
returns every free page the arenas hold. Measured on the live image (München,
8 collections x 3 queries, warm, MALLOC_ARENA_MAX=2 and one shared Lance
Session): 677 MiB anon, then 237 MiB after a trim of 12 ms; the next pass ran
at the same speed and grew to 354 MiB.

ONCE PER IDLE PERIOD, NEVER PER REQUEST: a timer thread trims when no request
has started or ended for `config.WEB_IDLE_TRIM_S` (5 min; 0 never), and not
again until a request has come and the web has been idle once more. Every
request notes the time (`IdleClock`, pure ASGI): two assignments. A stream
left open (the Library's events) is not a request coming or going, so the
web can be idle beside it; the trim takes the allocator's locks, so an
allocation meanwhile waits its milliseconds.

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

from . import config

#: how often the timer looks, at most: a trim lands within this of the idle
#: timeout
CHECK_S = 30.0

_last = time.monotonic()
_trimmed = False
_started = False
_lock = threading.Lock()


def note() -> None:
    """A request started or ended: the web is not idle, and the next idle
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
    log(f"[web] idle {config.WEB_IDLE_TRIM_S:.0f} s: malloc_trim(0) gave back the freed heap, {said} ({ms:.0f} ms)")
    return True


def _loop(idle_s: float) -> None:
    while True:
        time.sleep(min(idle_s, CHECK_S))
        if due(time.monotonic(), idle_s):
            trim()


def start() -> bool:
    """The timer thread, once per process; nothing when the timeout is 0."""
    global _started
    idle_s = float(config.WEB_IDLE_TRIM_S)
    with _lock:
        if _started or idle_s <= 0:
            return False
        _started = True
    threading.Thread(target=_loop, args=(idle_s,), name="idle-trim", daemon=True).start()
    return True


class IdleClock:
    """ASGI middleware: every HTTP request notes the time as it starts and as
    it ends (`note`). Pure ASGI, as auth.ViewerGate, so a caller's disconnect
    still reaches the search."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        note()
        try:
            return await self.app(scope, receive, send)
        finally:
            note()
