"""A COLLECTION YIELDS BEFORE THE KERNEL ACTS (lessons of EUR-Lex5.next, A4).

The operator, 2026-11-25: "All the workaround made on the spot must be
reflected in RAGE so that the same collection made from scratch can run
smoothly". The workaround: an operator's monitor read the worker cgroup's
anonymous memory every ten seconds and paused the heavy collection over
21,500 MiB, resuming it under 18,000 (the Legal session's rule, against a
24 GiB cap) — an agent standing guard, against "Louise survives without an
agent". This is that rule in the worker itself.

WHAT IT IS NOT: a measured cure. The monitor's rule NEVER FIRED (München,
2026-11-25: the highest anonymous reading under it was 17,412 MiB), so
nobody has seen a yield bring the memory down. It is the same precaution,
moved to where it needs no hand, and it says what it does with its numbers
so the first time it fires is also the first measurement.

THE RULE. The worker's cgroup is read every CHECK_EVERY_S (`memory.stat`'s
`anon`, against `memory.max`):
- at or over HIGH (87.5 % of the limit: 21.0 of 24 GiB) the collection that
  holds the most of the parse pool IN FLIGHT yields: its pipeline thread
  stands down after its unit, its parts in flight finish, nothing new of it
  starts. Which one: by its parts' estimated peaks, then the bytes they
  parse, then their number (`parallel.in_flight`);
- still over HIGH after ESCALATE_S with a yield in force: the next heaviest
  yields too;
- under LOW (73 %: 17.9 GiB) every yielding collection goes on;
- A YIELD IS BOUNDED: one that has lasted MAX_YIELD_S with nothing of its
  collection left in flight and the memory still over LOW did not bring it
  down — the memory is not the pool's (a pass run inside the worker's
  container: the one real kill, 2026-11-23, was that) — so the collection
  goes on and the line says so. A pipeline thread never waits without a
  bound (CLAUDE.md).
With nothing in flight and the memory over HIGH there is nobody to yield:
said once an episode. No cgroup limit to read: nothing here does anything.

It adds to the pool's own admission (app/parallel.py: a part with a declared
peak is admitted against the measured room at 85 %), which holds back the
parts that SAY how large they are; this holds back a collection whose parts
do not, or say too little. It replaces neither the OOM rule (a kill is not
relaunched until its cause is known, app/oom_hold.py) nor the pool's.

Standardised alternative, not taken: cgroup v2's own `memory.high` throttles
a group at a line, and a child group per collection would let the kernel do
this — it needs the worker to manage sub-cgroups (delegation the app's
container does not have today) and it throttles by reclaim, which for
anonymous memory without swap is a stall, not a yield. It pays if the
worker's children ever get groups of their own (the note in parallel.py).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

CGROUP_DIR = "/sys/fs/cgroup"
#: of the cgroup's limit: the monitor's 21,500 and 18,000 MiB against 24 GiB
HIGH_FRACTION = 0.875
LOW_FRACTION = 0.73
CHECK_EVERY_S = 10.0
#: a yield in force this long with the memory still over HIGH: the next heaviest too
ESCALATE_S = 120.0
#: a yield that did not bring the memory down ends after this
MAX_YIELD_S = 600.0
MIB = 1 << 20

_lock = threading.Lock()
_yielding: dict[str, dict[str, Any]] = {}      # collection -> {"since", "anon", "limit", "why"}
_said_nobody = False
#: collections whose yield ended at its bound in this episode: not asked to
#: yield again until the memory has been under LOW once — a yield that did
#: nothing is not repeated every ten minutes
_spent: set[str] = set()


def reset() -> None:
    global _said_nobody
    with _lock:
        _yielding.clear()
        _spent.clear()
        _said_nobody = False


def anonymous(cgroup: str | None = None) -> tuple[int, int] | None:
    """(the cgroup's anonymous memory, its limit) in bytes; None where no
    limit reads (no cgroup v2, `memory.max` "max") or the file does not."""
    base = Path(cgroup or CGROUP_DIR)
    try:
        raw = (base / "memory.max").read_text(encoding="ascii").strip()
        if raw == "max":
            return None
        limit = int(raw)
        for line in (base / "memory.stat").read_text(encoding="ascii").splitlines():
            key, _, value = line.partition(" ")
            if key == "anon":
                return int(value), limit
    except (OSError, ValueError):
        return None
    return None


def weights(in_flight: list[dict]) -> dict[str, tuple[int, int, int]]:
    """collection -> (estimated peak, bytes parsed, parts) of what the pool
    holds for it now: the order collections yield in, heaviest first."""
    out: dict[str, list[int]] = {}
    for part in in_flight:
        name = part.get("collection")
        if not name:
            continue
        w = out.setdefault(name, [0, 0, 0])
        w[0] += int(part.get("estimated_peak") or 0)
        w[1] += int(part.get("parse_bytes") or part.get("bytes") or 0)
        w[2] += 1
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


def yields(name: str) -> dict[str, Any] | None:
    """The yield in force for this collection, or None."""
    with _lock:
        got = _yielding.get(name)
        return dict(got) if got else None


def yielding() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _yielding.items()}


def _mib(n: int) -> str:
    return f"{n // MIB:,}"


def look(*, memory: Callable[[], tuple[int, int] | None] = anonymous,
         in_flight: Callable[[], list[dict]] | None = None,
         say: Callable[[str], None] | None = None, now: float | None = None) -> dict[str, Any]:
    """ONE LOOK at the worker's memory: who yields, who goes on. Returns
    {"anon", "limit", "yielded": [names], "resumed": [names]}. Never raises
    past a reading."""
    global _said_nobody
    from . import parallel
    say = say or (lambda m: None)
    out: dict[str, Any] = {"anon": None, "limit": None, "yielded": [], "resumed": []}
    got = memory()
    if got is None:
        return out
    anon, limit = got
    out["anon"], out["limit"] = anon, limit
    high, low = int(limit * HIGH_FRACTION), int(limit * LOW_FRACTION)
    t = time.monotonic() if now is None else now
    held = weights((in_flight or parallel.in_flight)())
    with _lock:
        current = dict(_yielding)
    if anon < low:
        _said_nobody = False
        with _lock:
            _spent.clear()                      # the episode is over
        for name, y in current.items():
            say(f"memory: {name} goes on — the worker holds {_mib(anon)} MiB anonymous, under "
                f"{_mib(low)} of {_mib(limit)}; it yielded for {t - y['since']:.0f} s")
            out["resumed"].append(name)
        with _lock:
            for name in out["resumed"]:
                _yielding.pop(name, None)
        return out
    # a yield that did not bring the memory down: bounded
    for name, y in current.items():
        if t - y["since"] >= MAX_YIELD_S and name not in held:
            say(f"memory: {name} goes on — it yielded for {t - y['since']:.0f} s with nothing of it left in "
                f"the parse pool and the worker still holds {_mib(anon)} MiB anonymous (over {_mib(low)}): "
                f"the memory is not this collection's, and not the pool's if nothing is in flight "
                f"(a pass run inside the worker's container?)")
            out["resumed"].append(name)
    with _lock:
        for name in out["resumed"]:
            _yielding.pop(name, None)
            _spent.add(name)
            current.pop(name, None)
        spent = set(_spent)
    if out["resumed"]:
        _said_nobody = True                     # the line above has said whose the memory is not
    if anon < high:
        return out
    newest = max((y["since"] for y in current.values()), default=None)
    if newest is not None and t - newest < ESCALATE_S:
        return out                              # a yield is in force: give it its time
    candidates = sorted(((w, name) for name, w in held.items()
                         if name not in current and name not in spent), reverse=True)
    if not candidates:
        if not current and not _said_nobody:
            _said_nobody = True
            say(f"memory: the worker holds {_mib(anon)} MiB anonymous, over {_mib(high)} of {_mib(limit)}, "
                f"and nothing is in the parse pool: there is no collection to yield — the memory is not a "
                f"part's (the main process, or a pass run inside the worker's container)")
        return out
    (peak, parsed, parts), name = candidates[0]
    why = (f"it has the most in the parse pool — {parts} part{'s' if parts != 1 else ''}, "
           + (f"{_mib(peak)} MiB estimated" if peak else f"{_mib(parsed)} MiB to parse"))
    with _lock:
        _yielding[name] = {"since": t, "anon": anon, "limit": limit, "why": why}
    out["yielded"].append(name)
    say(f"memory: {name} yields — the worker holds {_mib(anon)} MiB anonymous, over {_mib(high)} of "
        f"{_mib(limit)}; {why}. Its parts in flight finish and nothing new of it starts until the worker "
        f"is under {_mib(low)} MiB"
        + (f" (already yielding: {', '.join(sorted(current))})" if current else ""))
    return out


def said(name: str) -> str | None:
    """The line a yielding collection's own log carries, or None."""
    y = yields(name)
    if y is None:
        return None
    return (f"yielding to the worker's memory: {_mib(y['anon'])} of {_mib(y['limit'])} MiB anonymous when "
            f"it began; {y['why']}. Nothing new starts until the worker is under "
            f"{_mib(int(y['limit'] * LOW_FRACTION))} MiB, or {MAX_YIELD_S / 60:.0f} min have passed with "
            f"nothing of it in flight (app/memory_yield.py)")


def watch(stop: threading.Event, say: Callable[[str], None] | None = None) -> None:
    """The worker's watcher thread: a look every CHECK_EVERY_S until `stop`.
    On a thread of its own (telemetry is published by someone other than the
    thread doing the work); where no limit reads it ends at once."""
    if anonymous() is None:
        return
    while not stop.wait(CHECK_EVERY_S):
        try:
            look(say=say)
        except Exception as e:   # noqa: BLE001 — a look never ends the watcher
            if say:
                say(f"memory: the look failed ({type(e).__name__}: {e})")
