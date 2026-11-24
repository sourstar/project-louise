"""AN OOM KILL IS NOT RELAUNCHED UNTIL ITS CAUSE IS KNOWN (operator,
2026-11-19, verbatim: "the very first time a worker is killed because of
OOM, it is not relauched until the cause has been determined or diagnostics
has been added"; CLAUDE.md, after 43 OOM kills of the worker in one day,
each restart meeting the same 180 MB EUR-Lex acts). The operator chose "A":
the worker stays down.

THE HOLD is `<LOUISE_ROOT>/state/oom-hold.json`, written the first time the
kernel kills for memory in the worker's cgroup:
  - the worker's main process: by its guard (app/worker_guard.py), the
    container's command, which runs the worker as its child and sees it end
    by SIGKILL while the cgroup's `memory.events` `oom_kill` rose — a
    process killed by SIGKILL writes nothing of its own;
  - one of its pool children, or any other process in its cgroup: by the
    worker's watcher (`watch`), which reads the same counter, writes the
    hold with what the parse pool had in flight, and stops the worker with
    EXIT_HELD.
While it is present the guard starts no worker and says why. It is lifted
by `louise node release-oom --cause TEXT` (the cause determined: what it was
processing, the measured peak) or `--diagnostic TEXT` (what was added that
will name it the next time); the hold and its release are kept, one line
each, in `state/oom-holds.jsonl`. The operator passes have their own,
beside their supervisor's hold file (deploy/supervise-pass.sh).

Standardised alternative, not taken: Kubernetes' CrashLoopBackOff restarts
a container it killed for memory with a growing delay, which is the
behaviour the operator ruled out — the restart met the same document each
time; systemd's `OOMPolicy=stop` stops a unit the kernel killed for memory
and leaves it down, which is this, for a unit rather than a container.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HOLD_FILE = "oom-hold.json"
LOG_FILE = "oom-holds.jsonl"
#: the worker's exit when it stopped itself because of a memory kill in its
#: cgroup (EX_TEMPFAIL): its guard keeps it down
EXIT_HELD = 75
CGROUP_DIR = "/sys/fs/cgroup"


def _state_dir() -> Path:
    from . import config
    return config.louise_root() / "state"


def path() -> Path:
    return _state_dir() / HOLD_FILE


def _event(name: str, cgroup: str | None = None) -> int | None:
    try:
        for line in Path(cgroup or CGROUP_DIR, "memory.events").read_text(encoding="ascii").splitlines():
            key, _, value = line.partition(" ")
            if key == name:
                return int(value)
    except (OSError, ValueError):
        return None
    return None


def oom_kills(cgroup: str | None = None) -> int | None:
    """The cgroup's `memory.events` `oom_kill`: how many processes the kernel
    killed in it for memory; None where it does not read (no cgroup v2)."""
    return _event("oom_kill", cgroup)


def memory_peak(cgroup: str | None = None) -> int | None:
    try:
        return int(Path(cgroup or CGROUP_DIR, "memory.peak").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def held() -> dict[str, Any] | None:
    """The hold in force, or None."""
    try:
        return json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def hold(kind: str, details: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Record the hold — the FIRST kill's: a hold already in force is kept,
    since the first is the one whose cause is owed. Atomic."""
    have = held()
    if have is not None:
        return have
    rec = {"kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now)),
           **details}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _log({"held": rec})
    return rec


def release(*, cause: str | None = None, diagnostic: str | None = None,
            now: float | None = None) -> dict[str, Any]:
    """Lift the hold, naming which: the cause determined, or the diagnostic
    added. Exactly one of them, in words."""
    if bool(cause) == bool(diagnostic):
        raise ValueError("a release names the cause determined (--cause) or the diagnostic added "
                         "(--diagnostic), one of them, in words")
    rec = held()
    if rec is None:
        raise ValueError("no OOM hold is in force on this node")
    entry = {"released": rec, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now)),
             **({"cause": cause} if cause else {"diagnostic": diagnostic})}
    _log(entry)
    path().unlink(missing_ok=True)
    return entry


def _log(entry: dict[str, Any]) -> None:
    p = _state_dir() / LOG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def describe(rec: dict[str, Any]) -> str:
    """The hold in plain words, for the guard's line and `node show`."""
    what = {"worker": "the worker's main process", "cgroup": "a process in the worker's memory cgroup "
            "(a parse-pool child, a check, a derivation)"}.get(rec.get("kind"), str(rec.get("kind")))
    peak = rec.get("memory_peak")
    return (f"HELD since {rec.get('at')}: the kernel killed {what} for memory"
            + (f" (the cgroup's peak {peak / 2 ** 30:.1f} GB)" if isinstance(peak, int) else "")
            + "; not relaunched until the cause is known (operator, 2026-11-19). Release with "
              "`louise node release-oom --cause \"…\"` or `--diagnostic \"…\"`; what it was doing is in "
            + str(path()))


# THE PROCESS'S BASELINE: the counter as this process first read it — at its
# first pool, or at the worker's watcher — and the handler the worker set.
# A pool that sees a child die (BrokenProcessPool) asks `killed()` at once:
# the kill is then known before anything is computed again, not 5 s later.
_lock = threading.Lock()
_base: dict[str, int | None] = {}
_handler: Callable[[int, int], None] | None = None
_fired: tuple[int, int] | None = None


def baseline(cgroup: str | None = None) -> int | None:
    """The cgroup's `oom_kill` as this process first read it."""
    key = cgroup or CGROUP_DIR
    with _lock:
        if key not in _base:
            _base[key] = oom_kills(key)
        return _base[key]


def on_kill(handler: Callable[[int, int], None] | None) -> None:
    """What the first kill does in this process (the worker: hold and stop);
    with none, `killed()` only answers, and the pass's exit says it."""
    global _handler
    _handler = handler


def killed(cgroup: str | None = None) -> tuple[int, int] | None:
    """(before, after) once the cgroup's `oom_kill` has risen past this
    process's baseline, else None. The first time it has, the handler runs —
    once, whichever thread asked."""
    global _fired
    base = baseline(cgroup)
    if base is None:
        return None
    if _fired is not None:
        return _fired
    now = oom_kills(cgroup)
    if now is None or now <= base:
        return None
    with _lock:
        if _fired is not None:
            return _fired
        _fired = (base, now)
        handler = _handler
    if handler is not None:
        handler(base, now)
    return _fired


def _reset() -> None:
    """Tests: forget the baseline, the handler, the kill seen and the sample."""
    global _handler, _fired
    with _lock:
        _base.clear()
        _handler = None
        _fired = None
        _sample.clear()


#: the processes sampled last, for a hold: which one held what (2026-11-20:
#: nine kills, and which were the main process and which its children was
#: read off the kernel's log by hand)
_sample: dict[str, Any] = {}


def processes(cgroup: str | None = None, *, proc: str = "/proc") -> list[dict[str, Any]]:
    """Every process of the cgroup (`cgroup.procs`) with its anonymous memory
    (RssAnon), its `oom_score_adj` and its command line's first words,
    largest first; [] where the cgroup does not list them."""
    try:
        pids = [int(x) for x in Path(cgroup or CGROUP_DIR, "cgroup.procs").read_text(encoding="ascii").split()]
    except (OSError, ValueError):
        return []
    out = []
    for pid in pids:
        try:
            status = Path(proc, str(pid), "status").read_text(encoding="ascii", errors="replace")
            anon = next((int(line.split()[1]) * 1024 for line in status.splitlines()
                         if line.startswith("RssAnon:")), None)
            adj = int(Path(proc, str(pid), "oom_score_adj").read_text(encoding="ascii").strip())
            cmd = Path(proc, str(pid), "cmdline").read_bytes().split(b"\0")
        except (OSError, ValueError):
            continue                        # gone meanwhile
        out.append({"pid": pid, "rss_anon": anon, "oom_score_adj": adj,
                    "cmd": " ".join(c.decode("utf-8", "replace") for c in cmd[:4] if c)[:120]})
    return sorted(out, key=lambda p: -(p["rss_anon"] or 0))


def last_sample() -> dict[str, Any]:
    """The watcher's last sample: when, and `processes()` then."""
    return dict(_sample)


def watch(stop, handler: Callable[[int, int], None], *, every_s: float = 5.0,
          cgroup: str | None = None) -> None:
    """The worker's watcher thread: `handler(before, after)` the first time
    the cgroup's `oom_kill` rises past this process's baseline (`killed`,
    which a pool asks too, at a child's death); returns then, or at `stop`.
    Every `every_s` it samples the cgroup's processes (`processes`), so the
    hold says which held what just before the kill. Where the counter does
    not read, it watches nothing."""
    on_kill(handler)
    if baseline(cgroup) is None:
        return
    while not stop.wait(every_s):
        if killed(cgroup) is not None:
            return
        _sample.update({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "processes": processes(cgroup)})
