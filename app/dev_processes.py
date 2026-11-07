"""A development step's PROCESSES inside the container, for the checkpoint's
restore (app/working_state.py; 07 §7.3 `node checkpoint run`).

München, 2026-11-07: "stopping the systemd unit does NOT stop a docker
exec'd fetch" — the docker CLI does not forward the signal, and most
development steps are `docker exec <container> python -m app.louise …`.
So a checkpoint records the process itself, in the container's own pid
namespace, and the restore stops it from there: SIGTERM (a Louise pass
finishes its unit, commits and exits 130, 07 §7.7), a bounded wait, then
SIGKILL only if it is still there, and each said.

A pid is reused, so a process is named by three facts read from /proc at
registration: its pid, its command line and its start time (`starttime`,
field 22 of /proc/<pid>/stat, clock ticks since boot), plus the pid
namespace it lives in — a pid recorded in the web container names nothing in
the worker's. Only a process all of them still match is signalled. The node
itself — pid 1, the worker, the web, this command — is never one.
"""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Any, Callable

#: what a step is given to finish its unit (07 §7.7) before SIGKILL
STOP_GRACE_S = 300.0
#: how long a SIGKILLed process is waited for, to be said gone
KILL_WAIT_S = 5.0
#: the node's own processes, by their command line: never a dev step
_NODE = (("-m", "app.worker"), ("app.api:app",), ("node", "start"))


class NotAStep(ValueError):
    """A pid that is not a development step this node may stop."""


def pid_namespace() -> str:
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return ""


def facts(pid: int) -> tuple[str, int, list[str], int] | None:
    """`(state, start, cmdline, pgid)` of `pid` in this pid namespace, or
    None when there is none. `state` is /proc's letter (`Z` a zombie)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_bytes().decode("utf-8", "replace")
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    rest = stat[stat.rindex(")") + 2:].split()       # the name may hold spaces and parentheses
    cmd = raw[:-1].split(b"\0") if raw.endswith(b"\0") else (raw.split(b"\0") if raw else [])
    return rest[0], int(rest[19]), [a.decode("utf-8", "replace") for a in cmd], int(rest[2])


def _is_node(cmdline: list[str]) -> bool:
    joined = " ".join(cmdline)
    return any(" ".join(n) in joined for n in _NODE)


def entry(pid: int, *, note: str = "", by: str = "", now: float | None = None) -> dict[str, Any]:
    """What a checkpoint records of `pid`. NotAStep for no such process, a
    zombie, or one of the node's own."""
    if pid <= 1 or pid in (os.getpid(), os.getppid()):
        raise NotAStep(f"pid {pid} is {'the container init' if pid <= 1 else 'this command'}: never a step")
    got = facts(pid)
    if got is None or got[0] == "Z":
        raise NotAStep(f"pid {pid}: no such process in this container")
    state, start, cmdline, pgid = got
    if _is_node(cmdline):
        raise NotAStep(f"pid {pid} is the node itself ({' '.join(cmdline)[:80]}): never a step")
    return {"pid": pid, "start": start, "cmdline": cmdline, "pgid": pgid, "ns": pid_namespace(),
            "note": note, "by": by, "at": time.time() if now is None else now}


def state(e: dict[str, Any]) -> str:
    """`running` (the very process recorded), `gone`, `reused` (another
    process holds the pid now) or `elsewhere` (another pid namespace)."""
    if e.get("ns") and e["ns"] != pid_namespace():
        return "elsewhere"
    got = facts(int(e["pid"]))
    if got is None or got[0] == "Z":
        return "gone"
    if got[1] != e.get("start") or got[2] != e.get("cmdline"):
        return "reused"
    return "running"


_SAYS = {"gone": "not running: nothing to stop",
         "reused": "its pid is another process's now (command line or start time differ): not signalled",
         "elsewhere": "recorded in another container's pid namespace: not reachable from here, "
                      "stop it in that container"}


def described(e: dict[str, Any]) -> dict[str, Any]:
    """One recorded process, as status and a dry run say it."""
    return {"pid": e["pid"], "cmdline": " ".join(e.get("cmdline") or [])[:200], "note": e.get("note") or "",
            "state": state(e)}


def stop(entries: list[dict[str, Any]], *, grace_s: float = STOP_GRACE_S, kill_wait_s: float = KILL_WAIT_S,
         sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """Each recorded process still running: SIGTERM, together, then one
    shared wait of at most `grace_s`, then SIGKILL — its process group
    where it leads one (a step started by `run`) — for what is still there.
    One report per entry: `result` is terminated, killed, still-running
    (after SIGKILL), refused (no permission), or the state that kept it
    from being signalled."""
    report: list[dict[str, Any]] = []
    live: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for e in entries:
        r = described(e)
        report.append(r)
        if r["state"] != "running":
            r.update(result=r["state"], says=_SAYS[r["state"]])
            continue
        try:
            os.kill(int(e["pid"]), signal.SIGTERM)
        except ProcessLookupError:
            r.update(result="gone", says=_SAYS["gone"])
            continue
        except PermissionError as ex:
            r.update(result="refused", says=f"SIGTERM refused: {ex}")
            continue
        live.append((e, r))
    t0 = time.monotonic()
    while live:
        still = []
        for e, r in live:
            if state(e) == "running":
                still.append((e, r))
            else:
                waited = time.monotonic() - t0
                r.update(result="terminated", says=f"ended {waited:.0f} s after SIGTERM")
        live = still
        if not live or time.monotonic() - t0 >= grace_s:
            break
        sleep(min(0.2, max(0.0, grace_s - (time.monotonic() - t0))))
    for e, r in live:
        pid, pgid = int(e["pid"]), int(e.get("pgid") or 0)
        try:
            if pgid == pid:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            r.update(result="terminated", says=f"ended {grace_s:.0f} s after SIGTERM")
            continue
        except PermissionError as ex:
            r.update(result="refused", says=f"still running {grace_s:.0f} s after SIGTERM; SIGKILL refused: {ex}")
            continue
        t1 = time.monotonic()
        while state(e) == "running" and time.monotonic() - t1 < kill_wait_s:
            sleep(0.1)
        if state(e) == "running":
            r.update(result="still-running", says=f"still there {kill_wait_s:.0f} s after SIGKILL "
                                                  f"(SIGTERM {grace_s:.0f} s before)")
        else:
            r.update(result="killed", says=f"still running {grace_s:.0f} s after SIGTERM: SIGKILLed")
    return report
