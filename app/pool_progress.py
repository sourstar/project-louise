"""A BATCH IN FLIGHT SAYS ON WHAT (the lessons of EUR-Lex5.next, G3's first row).

München, on the node, 2026-11-25 09:57Z: EUR-Lex5.next's pipeline thread sat
in its fts stage for nine minutes and more, one pool child at 98 % of a core
on a 136 MB act (bg 32018R0688), ten children idle, the collection's counts
still — and nothing in the log said a batch was in flight, or on what. The
scheduler's line read "0% in 9 turns" for every collection. An operator's
monitor had been the only thing that knew ("hang", "done not rising").

"A wait says on what" (the lessons' rule, #885) applies to a stage waiting on
its own pool. The pool already records every part in flight
(`parallel.in_flight`, kept for a memory kill's hold). This reads it: a part
that has been in flight for SAY_AFTER_S is said, and again every SAY_EVERY_S
while it stays — the collection, the stage, how many parts the pool holds for
it, the oldest's unit and size, how long, and for a large part that it is
ALONE in the large lane (one large unit at a time in the whole worker,
parallel.py `_large_lane`) and how long its answer is still waited for.
When it has answered, one line says how long it took.

Telemetry is published by someone other than the thread doing the work
(CLAUDE.md): a watcher thread of the worker's, a look every CHECK_EVERY_S.
It reads two dictionaries under the pool's lock; it changes nothing — what
runs when is exactly what it was.

Not built here, and named so it is not forgotten: the monitor's other rule,
"done not rising" (a collection whose counts have not moved for N minutes
with work pending and NOTHING in flight), needs the manifests' counts and is
the next row of the same lesson.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

#: a part in flight this long is said
SAY_AFTER_S = 60.0
#: and again this often while it stays
SAY_EVERY_S = 60.0
CHECK_EVERY_S = 10.0
MIB = 1 << 20

_lock = threading.Lock()
#: (collection, kind, the oldest part's first uid, its since) -> when it was last said
_said: dict[tuple, float] = {}


def reset() -> None:
    with _lock:
        _said.clear()


def _span(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} s"
    return f"{seconds / 60:.0f} min"


def _size(n: int) -> str:
    return f"{n / MIB:,.1f} MiB" if n >= MIB // 10 else f"{n:,} bytes"


def look(*, in_flight: Callable[[], list[dict]] | None = None,
         say: Callable[[str], None] | None = None, now: float | None = None) -> list[str]:
    """ONE LOOK at the pool: the lines said (also returned). Per collection
    and stage, the OLDEST part decides; the others are counted."""
    from . import parallel
    say = say or (lambda m: None)
    t = time.monotonic() if now is None else now
    groups: dict[tuple[str, str], list[dict]] = {}
    for part in (in_flight or parallel.in_flight)():
        if part.get("collection") and part.get("since") is not None:
            groups.setdefault((part["collection"], str(part.get("kind"))), []).append(part)
    out: list[str] = []
    live: set[tuple] = set()
    for (name, kind), parts in sorted(groups.items()):
        oldest = min(parts, key=lambda p: p["since"])
        uid = (oldest.get("uids") or [[None, None]])[0][0]
        key = (name, kind, uid, oldest["since"])
        live.add(key)
        age = t - oldest["since"]
        with _lock:
            last = _said.get(key)
            if age < SAY_AFTER_S or (last is not None and t - last < SAY_EVERY_S):
                continue
            _said[key] = t
        rows = int(oldest.get("rows") or 0)
        weight = int(oldest.get("parse_bytes") or oldest.get("bytes") or 0)
        line = (f"{name} {kind}: {len(parts)} part{'s' if len(parts) != 1 else ''} in the parse pool; "
                f"the oldest for {_span(age)} — {uid}"
                + (f" and {rows - 1} more" if rows > 1 else "") + f", {_size(weight)}")
        if oldest.get("large"):
            line += (" — ALONE in the large lane (one large unit at a time in the whole worker; the others "
                     "of its batch are deferred to a later round)")
            until = oldest.get("until")
            if until is not None:
                left = until - t
                line += (f"; its answer is waited for {_span(left)} more" if left > 0
                         else f"; {_span(-left)} past the time its size is given")
            line += ". The collection's pipeline waits for it, its other stages behind it"
        say(line)
        out.append(line)
    # a part that has answered: said once, with how long it took, if it had been said at all
    with _lock:
        gone = [k for k in _said if k not in live]
        for k in gone:
            _said.pop(k, None)
    for name, kind, uid, since in gone:
        line = f"{name} {kind}: {uid} has left the parse pool after {_span(t - since)}"
        say(line)
        out.append(line)
    return out


def watch(stop: threading.Event, say: Callable[[str], None] | None = None) -> None:
    """The worker's watcher thread: a look every CHECK_EVERY_S until `stop`."""
    while not stop.wait(CHECK_EVERY_S):
        try:
            look(say=say)
        except Exception as e:   # noqa: BLE001 — a look never ends the watcher
            if say:
                say(f"the pool's progress look failed ({type(e).__name__}: {e})")
