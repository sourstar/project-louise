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
    who = said(rec.get("killed"))
    return (f"HELD since {rec.get('at')}: the kernel killed {what} for memory"
            + (f" (the cgroup's peak {peak / 2 ** 30:.1f} GB)" if isinstance(peak, int) else "")
            + (f" — {who}" if who else "")
            + "; not relaunched until the cause is known (operator, 2026-11-19). Release with "
              "`louise node release-oom --cause \"…\"` or `--diagnostic \"…\"`; what it was doing is in "
            + str(path()))


# WHICH PROCESS, AND WHAT IT HELD (the lessons of EUR-Lex5.next, row A5): a
# kill's words — "not computed again until the cause is known" — fell on
# EVERY row of the batch in flight, and the two units that carried them were
# bystanders of 256 MiB each (measured). The hold already records the parts
# in flight and two samples of the cgroup's processes; `victim` reads the
# killed one off them, and `said` puts it in words for the hold, the guard's
# line and each row's deferral.

#: a process the kernel killed ends by SIGKILL
SIGKILLED = -9


def victim(details: dict[str, Any]) -> dict[str, Any] | None:
    """The process the kernel killed and the part it held, from what a hold
    records (`in_flight`, `processes_before`, `processes_now`); None where
    nothing says. In order of certainty:

      1. CERTAIN — a part in flight whose OWN process ended by SIGKILL: a
         large part has a process of its own, and `parallel.in_flight` reads
         its exit code. Every other part in flight is then a bystander.
      2. LIKELY — the process of the watcher's last sample that is gone now,
         the largest of them: the kernel kills the process with the highest
         badness, which among the pool's children is the one holding the most
         memory. With its part where that pid is a large part's; a child of
         the shared pool otherwise, whose part is one of those in flight and
         is not known (a broken pool ends its other children too, so "gone"
         alone names nobody)."""
    parts = [p for p in details.get("in_flight") or [] if isinstance(p, dict)]
    own = sorted((p for p in parts if p.get("exitcode") == SIGKILLED), key=lambda p: -(p.get("bytes") or 0))
    if own:
        part = own[0]
        return {"certain": True, "pid": part.get("pid"), "part": _part_of(part), "others_killed": len(own) - 1}
    before = details.get("processes_before") or {}
    now = {p.get("pid") for p in details.get("processes_now") or [] if isinstance(p, dict)}
    me = os.getpid()
    gone = sorted((p for p in before.get("processes") or []
                   if isinstance(p, dict) and p.get("pid") not in now and p.get("pid") != me),
                  key=lambda p: -(p.get("rss_anon") or 0))
    if not gone:
        return None
    dead = gone[0]
    part = next((p for p in parts if p.get("pid") is not None and p.get("pid") == dead.get("pid")), None)
    return {"certain": False, "pid": dead.get("pid"), "rss_anon": dead.get("rss_anon"),
            "sampled_at": before.get("at"), "part": _part_of(part) if part else None,
            "in_flight": len(parts)}


def _part_of(part: dict[str, Any]) -> dict[str, Any]:
    return {k: part.get(k) for k in ("collection", "kind", "rows", "bytes", "estimated_peak", "uids", "large")}


def _mib(n: Any) -> str:
    return f"{n / 2 ** 20:,.0f} MiB" if isinstance(n, int) else "size not read"


def said(v: dict[str, Any] | None) -> str:
    """`victim`'s answer in words; "" for None."""
    if not v:
        return ""
    part = v.get("part")
    held = ""
    if part:
        uids = [u[0] for u in part.get("uids") or [] if u and u[0]]
        held = (f"{part.get('kind') or 'a part'} of {part.get('collection')}: "
                f"{', '.join(map(str, uids[:3])) or 'its rows'}"
                f"{' and others' if (part.get('rows') or 0) > 3 else ''}, {_mib(part.get('bytes'))} of text, "
                f"estimated peak {_mib(part.get('estimated_peak'))}")
    if v.get("certain"):
        more = v.get("others_killed") or 0
        return (f"the process killed is pid {v.get('pid')}, the large part's own ({held}); every other "
                f"part in flight is a bystander" + (f" ({more} more large part(s) were killed too)" if more else ""))
    was = f"pid {v.get('pid')}, {_mib(v.get('rss_anon'))} anonymous at the last sample ({v.get('sampled_at')})"
    if part:
        return f"the process killed is most likely {was}, the large part's own ({held})"
    return (f"the process killed is most likely {was}, a child of the shared pool: which of the "
            f"{v.get('in_flight')} part(s) in flight it held is not known")


#: the victim as read at the first kill this process saw (`killed`)
_victim: dict[str, Any] | None = None


def victim_seen() -> dict[str, Any] | None:
    """The killed process as this process read it when it first saw the
    kill, while the parts were still in flight; None before, or unread."""
    return _victim


def _note_victim() -> None:
    global _victim
    try:
        from . import parallel
        _victim = victim({"in_flight": parallel.in_flight(), "processes_before": last_sample(),
                          "processes_now": processes()})
    except Exception:   # noqa: BLE001 — the hold and the stop never wait on this reading
        _victim = None


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
    _note_victim()              # FIRST, while its parts are still in flight
    if handler is not None:
        handler(base, now)
    return _fired


def _reset() -> None:
    """Tests: forget the baseline, the handler, the kill seen and the sample."""
    global _handler, _fired, _victim
    with _lock:
        _base.clear()
        _handler = None
        _fired = None
        _victim = None
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
