"""The node's last WORKING STATE, kept for the development watchdog
(07 §7.3 `node checkpoint`; docs/notes/node-runbook.md §6a).

The operator, 2026-11-07: "when the limit of usage approaches, and you are
going to be frozen for more than 2 hours, put all the developments on hold
and resume the last working state before freezing." An agent cannot see its
own usage limit, so the node keeps the watch (the operator: "Build it"): a
development step SAVES the state the node works in — its mode, the
collections paused on their own, each held collection's embed setting as
its file states it, and the host's development units — and BEATS while it
works. When no beat has come for two hours, `watch` restores that state and
names the units for the host to stop (deploy/watchdog/louise-watchdog.sh).
A step run inside the container is recorded as its process (`run`, `beat
--dev-pid`; app/dev_processes.py) — stopping a unit that runs `docker exec`
does not stop what it started there — and the restore stops it first.

Restoring goes through the operator's own commands' functions and nothing
else (invariant 10): `node_state.set_worker_state` (`node pause|resume`),
`node_state.set_paused` (`node pause|resume <collection>`) and
`effective_config.set_and_apply` (`hold config apply <c> --set embed=…`).
It changes only what differs from the record and only for a collection held
both then and now: one held now and not then is left as it is, one held
then and not now is skipped, and a freeze — an operator decision — is
recorded and never changed.

The record is `<LOUISE_ROOT>/state/working-state.json`, beside node.sqlite,
written whole and renamed into place, every read-then-write under an
exclusive `flock` (a beat racing the watchdog's restore would otherwise
re-open the checkpoint it had just closed). Standardised alternative, not
taken: systemd's own watchdog (`WatchdogSec`, `sd_notify`) beats a process,
not a person's session across containers and hosts, and would need a
daemon to hold it; a timer and a file are what this needs.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from . import dev_processes, node_state

KIND, VERSION = "louise-working-state", 1
FILE_NAME = "working-state.json"
#: the operator's two hours
DEFAULT_AFTER_S = 2 * 3600.0
#: the longest unattended step an agent may declare (`beat --for`)
MAX_HOLD_S = 24 * 3600.0
#: a bounded wait for another checkpoint command (CLAUDE.md: never unbounded)
LOCK_WAIT_S = 30.0
#: what the host may stop: its own development units, by name. The
#: watchdog's own units are never one (stopping its service from inside it
#: would end the run that restores)
UNIT_RE = re.compile(r"louise-[A-Za-z0-9@._:-]{1,200}")
WATCHDOG_UNIT = "louise-watchdog"
#: the embed mode a file stating none runs (collections.DEFAULT_EMBED_MODE,
#: in the file's word)
DEFAULT_EMBED = "on-demand"

#: watch's exits (07 §7.3): 0, 1, 2, 3 as 07 §7.7 has them, and two of its own
EXIT_RESTORED, EXIT_EXPIRED_DRY = 10, 11


class Refused(ValueError):
    """A rule stood in the way before anything was done (exit 2)."""


class Busy(RuntimeError):
    """Another checkpoint command holds the record (exit 3: retry later)."""


def path() -> Path:
    """Beside the node's store: `<LOUISE_ROOT>/state/working-state.json`."""
    return node_state.path().parent / FILE_NAME


def _clock() -> float:
    return time.time()


def iso(at: float | None) -> str | None:
    if at is None:
        return None
    return datetime.fromtimestamp(float(at), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def span(seconds: float) -> str:
    """`2 h 05 min`, `40 min`, `12 s`: an age or a time left, said."""
    s = int(abs(seconds))
    if s < 60:
        return f"{s} s"
    h, m = divmod(s // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


_DURATION = re.compile(r"(\d+(?:\.\d+)?)([smhd])")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """`90s`, `45m`, `2h`, `1h30m`, `1d`; `0` alone. A bare number is
    refused: `--for 5` read as seconds would fire the watchdog early."""
    t = str(text).strip().lower()
    if t == "0":
        return 0.0
    parts = _DURATION.findall(t)
    if not parts or "".join(n + u for n, u in parts) != t:
        raise Refused(f"{text!r} is not a duration: a number with s, m, h or d, e.g. 90m or 1h30m")
    return sum(float(n) * _UNIT_S[u] for n, u in parts)


def parse_when(text: str) -> float:
    """An ISO 8601 time WITH its offset (`2026-11-07T18:00:00Z`, `…+01:00`):
    a naive time would be read in whichever zone the container has."""
    try:
        at = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        raise Refused(f"{text!r} is not an ISO 8601 time (e.g. 2026-11-07T18:00:00Z)") from None
    if at.tzinfo is None:
        raise Refused(f"{text!r} names no offset: give Z or +01:00, so it is one instant")
    return at.timestamp()


def check_units(units: Sequence[str]) -> list[str]:
    """The development units, each a host systemd unit named `louise-…`;
    the watchdog's own refused. Order kept, duplicates dropped."""
    out: list[str] = []
    for u in units:
        if not UNIT_RE.fullmatch(u):
            raise Refused(f"--dev-unit {u!r}: the watchdog stops the host's development units only, "
                          f"named louise-… (letters, digits, @ . _ : -)")
        if u == WATCHDOG_UNIT or u.startswith(WATCHDOG_UNIT + "."):
            raise Refused(f"--dev-unit {u!r} is the watchdog itself")
        if u not in out:
            out.append(u)
    return out


# ---- the record -----------------------------------------------------------------

@contextmanager
def _locked() -> Iterator[None]:
    """One checkpoint command at a time, across processes and containers:
    an exclusive `flock` on `<record>.lock`, waited for LOCK_WAIT_S at most
    (the pattern of app/derived.py `building`). The kernel drops it with
    its holder."""
    lock = path().with_name(FILE_NAME + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a+b") as f:
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Busy(f"{lock} is held by another checkpoint command; "
                               f"run this again") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read() -> dict[str, Any] | None:
    """The record, or None when there is none. One this platform cannot
    read is refused, never taken for "none": it may be an open checkpoint."""
    p = path()
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Refused(f"{p} cannot be read ({e})") from None
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise Refused(f"{p} is not JSON ({e}); `save --replace` writes a new one") from None
    st = rec.get("state") if isinstance(rec, dict) else None
    if (not isinstance(rec, dict) or rec.get("kind") != KIND or rec.get("version") != VERSION
            or rec.get("status") not in ("open", "closed")
            or not isinstance(rec.get("saved_at"), (int, float))
            or not isinstance(rec.get("last_beat"), (int, float))
            or not isinstance(rec.get("dev_units", []), list)
            or not isinstance(rec.get("dev_processes", []), list)
            or not all(isinstance(e, dict) and isinstance(e.get("pid"), int)
                       for e in rec.get("dev_processes", []))
            or not isinstance(st, dict) or not isinstance(st.get("mode"), str)
            or not all(isinstance(st.get(k), dict) for k in ("paused", "frozen", "held"))
            or not all(isinstance(v, dict) for v in st["held"].values())):
        raise Refused(f"{p} is not a working state this platform reads; `save --replace` writes a new one")
    return rec


def _write(rec: dict[str, Any]) -> None:
    """Whole, then renamed into place: a crash leaves the previous record."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{FILE_NAME}.{os.getpid()}.tmp")
    data = json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)
    try:
        d = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    except OSError:
        pass                # the rename is done; a directory fsync is best effort


# ---- the node's state, now ----------------------------------------------------------

def current() -> dict[str, Any]:
    """What a checkpoint records, read where the operator's commands write
    it: the mode, the collections paused on their own and frozen (each with
    who and when), and every held collection's embed setting as its file
    states it (`collection_file.stated_embed`: what `--set embed=…` writes).
    The store is read creating nothing (`node_state.peek`); an absent one
    reads as the worker reads it."""
    from . import collection_file, collections as collections_mod, db

    def facts(c) -> dict[str, Any]:
        return {"mode": db.worker_state(c),
                "paused": {n: node_state.pause_of(n, c) for n in sorted(node_state.paused_collections(c))},
                "frozen": {n: node_state.frozen_since(n, c) for n in sorted(node_state.frozen_collections(c))}}
    got = node_state.peek(facts, {"mode": "paused", "paused": {}, "frozen": {}}, error=None)
    if got is None:
        raise Refused(f"the node's store {node_state.path()} cannot be read: nothing is recorded or restored")
    held: dict[str, dict[str, Any]] = {}
    d = collections_mod.collections_dir()
    for p in sorted(d.glob("*.toml")) if d.is_dir() else ():
        try:
            held[p.stem] = {"embed": collection_file.stated_embed(p.stem), "codex": _codex_of(p)}
        except collection_file.SetRefused as e:
            held[p.stem] = {"unreadable": str(e), "codex": _codex_of(p)}
    got["held"] = held
    return got


def _codex_of(p: Path) -> str | None:
    """The collection a file holds (07 §7.8 `codex`): what a name meant at
    save, so a restore never applies one collection's state to another
    that took its name."""
    import tomllib
    try:
        with open(p, "rb") as f:
            got = tomllib.load(f).get("codex")
    except (OSError, ValueError):
        return None
    return str(got).removeprefix("sha256:") if got else None


def plan(saved: dict[str, Any], now: dict[str, Any]) -> dict[str, list]:
    """What a restore of `saved` would do to `now`: `changes` (the mode, a
    collection's pause, its embed setting), `skipped` (held then, not now;
    a file that did not or does not load), `untouched` (held now, not then)
    and `notes` (a freeze that differs: never changed here)."""
    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    untouched: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    if saved["mode"] != now["mode"]:
        changes.append({"what": "mode", "from": now["mode"], "to": saved["mode"]})
    held_then, held_now = saved["held"], now["held"]
    paused_then, paused_now = set(saved["paused"]), set(now["paused"])
    for name in sorted(set(held_then) | set(held_now) | paused_then | paused_now):
        then, cur = held_then.get(name), held_now.get(name)
        pause_differs = (name in paused_then) != (name in paused_now)
        if then is not None and cur is None:
            skipped.append({"collection": name, "why": "held at save, not now: nothing of it is restored"})
            continue
        if then is None and cur is not None:
            untouched.append({"collection": name, "why": "held now, not at save: left as it is"})
            continue
        if then is None:
            if pause_differs:
                skipped.append({"collection": name, "why": "no file holds it, at save or now: its pause "
                                                           "is left as it is"})
            continue
        # A NAME IS NOT A COLLECTION (München, 2026-11-10: a checkpoint saved
        # before pubmed9 was renamed over pubmed, restored a day later, paused
        # the new pubmed and set its embed off by name). Restored only where
        # the name holds the collection it held at save.
        if then.get("codex") is None or then.get("codex") != cur.get("codex"):
            why = (f"at save this name held {str(then['codex'])[:12]}…, now {str(cur.get('codex'))[:12]}…: "
                   f"another collection; nothing of it is restored by name"
                   if then.get("codex") else
                   "the checkpoint does not say which collection this name held at save (saved before "
                   "checkpoints recorded it): nothing of it is restored by name")
            if pause_differs or (then.get("embed") or DEFAULT_EMBED) != (cur.get("embed") or DEFAULT_EMBED):
                skipped.append({"collection": name, "why": why})
            continue
        if pause_differs:
            changes.append({"what": "pause", "collection": name, "from": name in paused_now,
                            "to": name in paused_then, "at_save": saved["paused"].get(name)})
        if "unreadable" in then:
            skipped.append({"collection": name, "why": f"its file did not load at save, so its embed "
                                                       f"setting is left as it is: {then['unreadable']}"})
        elif "unreadable" in cur:
            skipped.append({"collection": name, "why": f"its file does not load now, so its embed "
                                                       f"setting is left as it is: {cur['unreadable']}"})
        elif (then.get("embed") or DEFAULT_EMBED) != (cur.get("embed") or DEFAULT_EMBED):
            changes.append({"what": "embed", "collection": name, "from": cur.get("embed"),
                            "to": then.get("embed") or DEFAULT_EMBED, "stated_at_save": then.get("embed")})
    frozen_then, frozen_now = set(saved.get("frozen") or {}), set(now.get("frozen") or {})
    if frozen_then != frozen_now:
        notes.append({"what": "frozen", "at_save": sorted(frozen_then), "now": sorted(frozen_now),
                      "says": "a freeze is an operator decision: restore never freezes or unfreezes"})
    return {"changes": changes, "skipped": skipped, "untouched": untouched, "notes": notes}


def said(change: dict[str, Any]) -> str:
    """One change, in words."""
    if change["what"] == "mode":
        return f"mode {change['from']} -> {change['to']}"
    if change["what"] == "pause":
        word = {True: "paused", False: "not paused"}
        return f"{change['collection']}: {word[change['from']]} -> {word[change['to']]}"
    return f"{change['collection']}: embed {change['from'] or '(file states none)'} -> {change['to']}"


# ---- the verbs --------------------------------------------------------------------

def _compact(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    """The record a new save replaces, without its own `previous`."""
    if rec is None:
        return None
    return {k: rec.get(k) for k in ("status", "saved_at", "note", "dev_units", "dev_processes",
                                    "closed_at", "closed_by", "closed_how", "restored")}


def save(*, note: str = "", units: Sequence[str] = (), replace: bool = False, by: str = "",
         now: float | None = None) -> dict[str, Any]:
    """The current state recorded as the working state, the checkpoint
    open and beaten. Refused while one is open unless `replace` — the agent
    states the current state IS the working state; the open one's dev units
    and processes are carried then, since one forgotten would be left
    running."""
    now = _clock() if now is None else now
    units = check_units(list(units))
    with _locked():
        try:
            old = read()
        except Refused:
            if not replace:
                raise
            old = None
        if old is not None and old["status"] == "open":
            if not replace:
                raise Refused(f"a checkpoint is open since {iso(old.get('saved_at'))}"
                              + (f" ({old['note']})" if old.get("note") else "")
                              + ": `beat` keeps it, `close` ends it, `save --replace` makes the "
                                "current state the working state")
            units = check_units(list(old.get("dev_units") or []) + units)
        procs = list(old.get("dev_processes") or []) if old is not None and old["status"] == "open" else []
        rec = {"kind": KIND, "version": VERSION, "status": "open", "saved_at": now,
               "saved_by": by or "unnamed", "note": note or "", "last_beat": now,
               "hold_until": None, "dev_units": units, "dev_processes": procs, "state": current(),
               "closed_at": None, "closed_by": None, "closed_how": None,
               "restored": None, "restore_failed": None, "previous": _compact(old)}
        _write(rec)
        return rec


def beat(*, for_s: float | None = None, until: float | None = None, units: Sequence[str] = (),
         pids: Sequence[int] = (), now: float | None = None) -> dict[str, Any]:
    """The heartbeat refreshed. `for_s` / `until` declare a known-long
    unattended step: the watchdog waits for it as well, at most MAX_HOLD_S
    from now (`capped` says so); `for_s=0` withdraws one. `units` adds
    development units started since the save, `pids` processes of this
    container (a step `run` did not start). Refused with no open
    checkpoint: a beat that protected nothing would read as protection."""
    now = _clock() if now is None else now
    units = check_units(list(units))
    capped = False
    with _locked():
        rec = read()
        if rec is None or rec["status"] != "open":
            raise Refused("no checkpoint is open: `louise node checkpoint save` records the working "
                          "state first")
        rec["last_beat"] = now
        hold = (now + for_s) if for_s is not None else until
        if for_s == 0:
            rec["hold_until"] = None
        elif hold is not None:
            if hold <= now:
                raise Refused(f"--hold-until {iso(hold)} is not in the future")
            if hold > now + MAX_HOLD_S:
                hold, capped = now + MAX_HOLD_S, True
            rec["hold_until"] = hold
        rec["dev_units"] = check_units(list(rec.get("dev_units") or []) + units)
        for pid in pids:
            try:
                e = dev_processes.entry(int(pid), by="beat --dev-pid", now=now)
            except dev_processes.NotAStep as ex:
                raise Refused(f"--dev-pid {pid}: {ex}") from None
            _add_process(rec, e)
        _write(rec)
    return {**rec, "capped": capped}


def _add_process(rec: dict[str, Any], e: dict[str, Any]) -> None:
    procs = rec.setdefault("dev_processes", [])
    if not any(p["pid"] == e["pid"] and p.get("start") == e["start"] for p in procs):
        procs.append(e)


def run(argv: Sequence[str], *, note: str = "") -> int:
    """`node checkpoint run -- <command…>`: the command started as a child
    in its own session and recorded in the open checkpoint (pid, command
    line, start time) under the lock — no restore can close the checkpoint
    between the start and the record —, SIGTERM and SIGINT forwarded to it,
    its exit returned (128 + N for a signal N). Refused, nothing started,
    with no checkpoint open. It does NOT beat: the agent's beat is the
    liveness signal, and a step that beat for itself would keep a frozen
    agent's development running; a known-long step is declared with `beat
    --for`. At its end its entry is removed, best effort (a restore holding
    the record is stopping it itself)."""
    argv = list(argv)
    if not argv:
        raise Refused("nothing to run: `louise node checkpoint run -- <command…>`")
    child: list[subprocess.Popen] = []
    pending: list[int] = []

    def forward(signum: int, _frame: Any) -> None:
        if not child:
            pending.append(signum)              # arrived before the start: sent after it
            return
        try:
            os.kill(child[0].pid, signum)
        except ProcessLookupError:
            pass
    saved = {s: signal.signal(s, forward) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        with _locked():
            rec = read()
            if rec is None or rec["status"] != "open":
                raise Refused("no checkpoint is open: `louise node checkpoint save` records the working "
                              "state first; nothing was started")
            try:
                child.append(subprocess.Popen(argv, start_new_session=True))
            except OSError as ex:
                raise Refused(f"{argv[0]}: cannot be started: {ex}") from None
            p = child[0]
            try:
                e = dev_processes.entry(p.pid, note=note, by="run")
            except dev_processes.NotAStep:
                # ended before it was read: recorded from what was run, and gone
                e = {"pid": p.pid, "start": None, "cmdline": argv, "pgid": p.pid,
                     "ns": dev_processes.pid_namespace(), "note": note, "by": "run", "at": time.time()}
            try:
                _add_process(rec, e)
                _write(rec)
            except BaseException:
                p.terminate()                   # never a step running unrecorded
                p.wait(timeout=60)
                raise
        print(f"louise node checkpoint run: pid {p.pid} recorded in the checkpoint "
              f"({' '.join(argv)[:120]})", file=sys.stderr, flush=True)
        for signum in pending:
            forward(signum, None)
        rc = p.wait()
    finally:
        for s, h in saved.items():
            signal.signal(s, h)
    try:
        with _locked():
            rec = read()
            procs = (rec or {}).get("dev_processes") or []
            left = [x for x in procs if not (x["pid"] == e["pid"] and x.get("start") == e["start"])]
            if rec is not None and len(left) != len(procs):
                rec["dev_processes"] = left
                _write(rec)
    except (Busy, Refused, OSError):
        pass                                    # best effort: a dead entry reads as gone
    return 128 - rc if rc < 0 else rc


def close(*, by: str = "", now: float | None = None) -> dict[str, Any] | None:
    """Development finished and the node in a good state: nothing to
    restore. The dev units are not stopped. None when none was open."""
    now = _clock() if now is None else now
    with _locked():
        rec = read()
        if rec is None or rec["status"] != "open":
            return None
        rec.update(status="closed", closed_at=now, closed_by=by or "unnamed", closed_how="closed")
        _write(rec)
        return rec


def deadline(rec: dict[str, Any], after_s: float = DEFAULT_AFTER_S) -> float:
    """When the watchdog acts: `after_s` past the last beat, or the declared
    hold, whichever is later."""
    return max(float(rec["last_beat"]) + after_s, float(rec.get("hold_until") or 0.0))


def _execute(change: dict[str, Any], by: str) -> dict[str, Any]:
    """One change, through the operator command's own function."""
    from . import effective_config
    if change["what"] == "mode":
        node_state.set_worker_state(change["to"], by=by)
        return {}
    if change["what"] == "pause":
        node_state.set_paused(change["collection"], change["to"], by=by)
        return {}
    with contextlib.redirect_stdout(sys.stderr):     # the loader logs; stdout is the answer
        out = effective_config.set_and_apply(change["collection"], {"embed": change["to"]})
    return {"also": out["written"].get("also") or {}, "refused": out.get("refused") or []}


def _processes(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The recorded processes, each with its state now (a dry run, status)."""
    return [dev_processes.described(e) for e in rec.get("dev_processes") or []]


def _restore(rec: dict[str, Any], *, by: str, now: float,
             grace_s: float = dev_processes.STOP_GRACE_S) -> dict[str, Any]:
    """Under the lock: the recorded processes stopped FIRST (a step still
    writing would undo what is put back), then the plan carried out, the
    record closed — or, where a change failed, left open with the failure,
    for the next run. A process that outlives its SIGKILL is said, and does
    not hold the state back."""
    from . import collection_file
    procs = dev_processes.stop(rec.get("dev_processes") or [], grace_s=grace_s)
    p = plan(rec["state"], current())
    done, skipped, failed = [], list(p["skipped"]), []
    for change in p["changes"]:
        try:
            done.append({**change, **_execute(change, by)})
        except collection_file.SetRefused as e:
            # the file refuses the value: a retry cannot change that
            skipped.append({"collection": change["collection"], "why": f"embed not restored: {e}"})
        except Exception as e:                              # noqa: BLE001 — recorded, the rest goes on
            failed.append({**change, "error": f"{type(e).__name__}: {e}"})
    result = {"at": now, "by": by, "processes": procs, "changed": done, "skipped": skipped,
              "untouched": p["untouched"], "notes": p["notes"], "failed": failed}
    if failed:
        rec["restore_failed"] = result
    else:
        rec.update(status="closed", closed_at=now, closed_by=by, closed_how="restored",
                   restored=result, restore_failed=None)
    _write(rec)
    return result


def restore(*, by: str = "", dry_run: bool = False, now: float | None = None,
            grace_s: float = dev_processes.STOP_GRACE_S) -> dict[str, Any] | None:
    """The recorded processes stopped, the node put back to the open
    checkpoint's state, then the checkpoint closed. Idempotent: only what
    differs changes, and with none open it is None. A dry run returns the
    plan and the processes it would stop, and writes nothing."""
    now = _clock() if now is None else now
    with _locked():
        rec = read()
        if rec is None or rec["status"] != "open":
            return None
        if dry_run:
            return {"dry_run": True, "processes": _processes(rec), **plan(rec["state"], current())}
        return _restore(rec, by=by or "unnamed", now=now, grace_s=grace_s)


def watch(*, after_s: float = DEFAULT_AFTER_S, dry_run: bool = False, now: float | None = None,
          grace_s: float = dev_processes.STOP_GRACE_S) -> tuple[int, dict[str, Any]]:
    """The watchdog's one check: `(exit, what)`. With a checkpoint open and
    its deadline passed, the units to stop and the restore — or, dry, what
    it would do (EXIT_EXPIRED_DRY). 0 when there is nothing to do."""
    if after_s <= 0:
        raise Refused("--after must be longer than nothing")
    now = _clock() if now is None else now
    with _locked():
        rec = read()
        if rec is None or rec["status"] != "open":
            return 0, {"checkpoint": "none" if rec is None else "closed", "expired": False,
                       "units": [], "says": "no checkpoint is open: nothing to watch"}
        dl = deadline(rec, after_s)
        out = {"checkpoint": "open", "saved_at": iso(rec["saved_at"]), "note": rec.get("note") or "",
               "last_beat": iso(rec["last_beat"]), "age_s": round(now - rec["last_beat"]),
               "hold_until": iso(rec.get("hold_until")), "deadline": iso(dl), "after_s": after_s}
        if now < dl:
            return 0, {**out, "expired": False, "units": [],
                       "says": f"the last beat is {span(now - rec['last_beat'])} old; "
                               f"the watchdog acts in {span(dl - now)}"}
        out.update(expired=True, units=list(rec.get("dev_units") or []))
        why = (f"no agent beat since {iso(rec['last_beat'])} ({span(now - rec['last_beat'])}, "
               f"--after {span(after_s)})")
        if dry_run:
            return EXIT_EXPIRED_DRY, {**out, "restored": None, "processes": _processes(rec),
                                      "would": plan(rec["state"], current()),
                                      "says": f"{why}: the units to stop, and what restore would change"}
        result = _restore(rec, by=f"louise node checkpoint watch: {why}", now=now, grace_s=grace_s)
        if result["failed"]:
            return 1, {**out, "restored": result, "says": f"{why}: the restore FAILED in part; the "
                                                          f"checkpoint stays open and the next watch "
                                                          f"tries again"}
        return EXIT_RESTORED, {**out, "restored": result, "says": f"{why}: the working state restored"}


def status(*, after_s: float = DEFAULT_AFTER_S, now: float | None = None) -> dict[str, Any]:
    """The checkpoint, read: open or closed, when it was saved and last
    beaten, the deadline, the dev units, and what restore would change now.
    Writes nothing."""
    now = _clock() if now is None else now
    rec = read()
    if rec is None:
        return {"checkpoint": "none", "file": str(path()),
                "says": "no checkpoint has been saved on this node"}
    out: dict[str, Any] = {
        "checkpoint": rec["status"], "file": str(path()), "saved_at": iso(rec["saved_at"]),
        "saved_by": rec.get("saved_by"), "note": rec.get("note") or "",
        "last_beat": iso(rec["last_beat"]), "age_s": round(now - rec["last_beat"]),
        "hold_until": iso(rec.get("hold_until")), "dev_units": list(rec.get("dev_units") or []),
        "processes": _processes(rec), "state": rec["state"]}
    if rec["status"] == "open":
        dl = deadline(rec, after_s)
        out.update(deadline=iso(dl), after_s=after_s, expired=now >= dl,
                   would=plan(rec["state"], current()), restore_failed=rec.get("restore_failed"))
    else:
        out.update(closed_at=iso(rec.get("closed_at")), closed_by=rec.get("closed_by"),
                   closed_how=rec.get("closed_how"), restored=rec.get("restored"))
    return out


def lines(st: dict[str, Any], now: float | None = None) -> list[str]:
    """`status` for a person."""
    now = _clock() if now is None else now
    if st["checkpoint"] == "none":
        return [f"checkpoint: none ({st['file']})"]
    head = [f"checkpoint: {st['checkpoint']}, saved {st['saved_at']} by {st.get('saved_by') or 'unnamed'}"
            + (f" — {st['note']}" if st.get("note") else ""),
            f"last beat:  {st['last_beat']} ({span(st['age_s'])} ago)"
            + (f"; a step declared until {st['hold_until']}" if st.get("hold_until") else ""),
            "dev units:  " + (", ".join(st["dev_units"]) or "none")]
    head += [f"process:    {p['pid']} {p['state']}: {p['cmdline'][:100]}" for p in st.get("processes") or []]
    if st["checkpoint"] == "closed":
        head.append(f"closed:     {st.get('closed_at')} ({st.get('closed_how')}, by {st.get('closed_by')})")
        r = st.get("restored") or {}
        head += [f"  stopped: {p['pid']} {p['result']}: {p['says']}" for p in r.get("processes") or []]
        head += [f"  restored: {said(c)}" for c in r.get("changed") or []]
        return head
    left = datetime.fromisoformat(st["deadline"].replace("Z", "+00:00")).timestamp() - now
    head.append(f"deadline:   {st['deadline']} ("
                + (f"passed {span(left)} ago" if left <= 0 else f"in {span(left)}")
                + f", --after {span(st['after_s'])})")
    w = st["would"]
    head.append("restore would change:" + ("" if w["changes"] else " nothing"))
    head += [f"  {said(c)}" for c in w["changes"]]
    head += [f"  skipped: {s['collection']}: {s['why']}" for s in w["skipped"]]
    head += [f"  untouched: {u['collection']}: {u['why']}" for u in w["untouched"]]
    head += [f"  note: frozen now {n['now']}, at save {n['at_save']}: {n['says']}" for n in w["notes"]]
    if st.get("restore_failed"):
        head += [f"  the last restore FAILED: {said(f)}: {f['error']}" for f in st["restore_failed"]["failed"]]
    return head
