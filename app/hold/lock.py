"""The one writer per collection (07 §7.7): `state/lock` in the collection's
directory (§7.8). The node holds it for every collection it runs, for as
long as it runs it; a `hold` command that writes takes it or exits 3 naming
the holder — 2 for the four whose later retry is not the answer
(`collection forget`, `vectors check --drop`, `collection migrate --apply`,
`dev adopt`). `show`, `list`, `check` and `ask` never take it: a reader
never blocks a writer or is blocked by one (the state database runs in
write-ahead mode).

An advisory `flock` on the file. Two open descriptions contend, in one
process or two, on one host — two nodes on one machine are two roots and
share nothing (§7.7) — and the kernel drops it with its holder, so a crash
leaves no stale lock, only the holder's last words in the file for the next
taker to read. `node pause <collection>` makes the worker release it at the
end of the current unit; `node resume <collection>` makes it take it again
(app/worker.py). The pause waits for that release, bounded, and so does a
writer that finds a paused collection still the worker's (`take_waiting`).
"""
from __future__ import annotations

import fcntl
import json
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .codex import CodexError

LOCK = "lock"
STATE = "state"


class Held(CodexError):
    """Another writer holds the collection. `holder` is what its lock file
    says; `exit_code` is 3 (retry when it is done) or 2 (07 §7.7's four)."""

    def __init__(self, name: str, holder: dict[str, Any] | None, *, exit_code: int = 3,
                 waited: float | None = None):
        self.holder = dict(holder or {})
        self.exit_code = exit_code
        #: the seconds waited for the worker of a paused collection (`take_waiting`)
        self.waited = waited
        who = (f"{self.holder.get('what') or 'another writer'} (pid "
               f"{self.holder.get('pid', '?')} on {self.holder.get('host', '?')}, since "
               f"{self.holder.get('since', '?')})")
        if waited is not None:
            tail = (f"; its pause is recorded and still being applied: the worker lets go at the "
                    f"end of its current unit, not yet after {waited:g} s; retry when it has")
        else:
            tail = ("; `node pause <collection>` releases the node's" if exit_code == 2
                    else "; retry when it is done")
        super().__init__(f"{name!r} is held by {who} — one writer per collection "
                         f"(07 §7.7){tail}")


def state_dir(root: Path, name: str) -> Path:
    """`<root>/<name>/state`, where 07 §7.8 puts the lock (app/hold/records.py
    lays the directory out the same way)."""
    return Path(root) / name / STATE


def lock_path(state: Path) -> Path:
    return Path(state) / LOCK


class Lock:
    """A taken lock; `release` (or the `with`) gives it back and clears the
    holder's words. Releasing twice is nothing."""

    def __init__(self, name: str, path: Path, f) -> None:
        self.name, self.path, self._f = name, path, f

    def release(self) -> None:
        f, self._f = self._f, None
        if f is None:
            return
        try:
            f.seek(0)
            f.truncate(0)
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()

    @property
    def held(self) -> bool:
        return self._f is not None

    def __enter__(self) -> "Lock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def take(state: Path, name: str, what: str, *, exit_code: int = 3, file: str = LOCK) -> Lock:
    """The collection's write lock, now, or `Held` naming the holder. `what`
    is written into the file for whoever finds it taken. `file`: another
    lock beside it, by the same rules — the update pass's own
    (app/hold/update.py), so two passes of one collection never interleave
    while the collection's writers stay the node's."""
    p = Path(state) / file
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+b")            # never truncated by an opener that may lose the race
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.seek(0)
        raw = f.read()
        f.close()
        try:
            who = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            who = {}
        raise Held(name, who if isinstance(who, dict) else {}, exit_code=exit_code) from None
    f.seek(0)
    f.truncate(0)
    f.write(json.dumps({"what": what, "pid": os.getpid(), "host": socket.gethostname(),
                        "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                       sort_keys=True).encode("utf-8"))
    f.flush()
    return Lock(name, p, f)


#: THE WORKER'S NODE-WIDE LOCK (the independent read of d40a4731, 3): taken
#: by the worker at boot, before any writer starts, and held for its life
#: (app/worker.py `main`); an OFFLINE pass (`hold collection refit`) takes it
#: for its own run, so a free lock means no worker, and a worker the
#: container's restart policy brings back meanwhile refuses to start (exit 3)
#: until the pass is done. Under `<LOUISE_ROOT>/state/`, beside node.sqlite.
WORKER_LOCK = "worker.lock"
WORKER = "the node (worker)"


def worker_state(collections_root: Path) -> Path:
    """`<LOUISE_ROOT>/state`, from the collections root a command is given
    (`<LOUISE_ROOT>/collections`, 07 §7.8) — where the worker's lock is."""
    return Path(collections_root).parent / STATE


def take_worker(collections_root: Path, what: str) -> "Lock":
    """The node-wide worker lock, now, or `Held` naming its holder (exit 3)."""
    return take(worker_state(collections_root), "the node", what, file=WORKER_LOCK)


def holder(state: Path, *, file: str = LOCK) -> dict[str, Any] | None:
    """Who holds the collection's lock now, from its file, or None when
    nobody does. A probe: it takes and gives back the lock when free.
    `file`: another lock by the same rules (`WORKER_LOCK`)."""
    p = Path(state) / file
    if not p.exists():
        return None
    with open(p, "a+b") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            try:
                who = json.loads(f.read().decode("utf-8") or "{}")
            except ValueError:
                who = {}
            return who if isinstance(who, dict) else {}
        fcntl.flock(f, fcntl.LOCK_UN)
        return None


# ---- a paused collection's lock, waited for (docs/notes/manual-steps.md, 5) ----
#
# München, 2026-11-28: `node pause gutenberg` returned 0, and the jobs POSTed
# 1-5 s after it were refused, "held by the node (worker) … retry when it is
# done": the worker lets go at the end of the collection's current unit
# (app/worker.py `_hold_locks`, `retire_collection`), 80 s after the pause
# that time, so "by the book" was a wait of unknown length by hand.

#: how often a bounded wait probes the lock: a probe is one flock call, and
#: the unit waited for is seconds to a minute. Standard alternative, written
#: down: a blocking flock(LOCK_EX) under a SIGALRM timeout wakes at the
#: release itself; it TAKES the lock (the pause must not), cannot tell the
#: worker from another holder before it has it, and an alarm is the main
#: thread's only — it pays if a second's latency ever matters.
POLL_S = 1.0


def wait_while_held(state: Path, wait_s: float, *, still: Callable[[], bool] = lambda: True,
                    by: str = WORKER, poll_s: float = POLL_S, file: str = LOCK
                    ) -> dict[str, Any] | None:
    """Wait, at most `wait_s`, while `by` (the worker) holds the lock and
    `still()` is true — the collection's pause still recorded. None as soon
    as `by` no longer holds it: free, or another writer's, which is not
    waited for. `by`'s record when the bound passed or `still()` turned
    false first. A probe, never a take: the worker does not take a paused
    collection back (`_hold_locks`), so the caller takes it after."""
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        who = holder(state, file=file)
        if who is None or who.get("what") != by:
            return None
        left = deadline - time.monotonic()
        if left <= 0 or not still():
            return who
        time.sleep(min(poll_s, left))


def take_waiting(state: Path, name: str, what: str, *, wait_s: float, still: Callable[[], bool],
                 exit_code: int = 3, say: Callable[[str], None] | None = None,
                 poll_s: float = POLL_S) -> Lock:
    """`take`, except where THE WORKER holds the lock while `still()` — the
    collection's pause recorded, which the worker applies at the end of its
    current unit: then it is waited for, at most `wait_s`, said once through
    `say`. Any other holder, a collection not paused, or `wait_s` 0 is
    refused at once, as `take` refuses. Past the bound, `Held` with exit 3
    and `waited`: the pause is still being applied, so a retry is the
    answer — for 07 §7.7's four too, whose 2 says to pause first."""
    try:
        return take(state, name, what, exit_code=exit_code)
    except Held as e:
        if wait_s <= 0 or e.holder.get("what") != WORKER or not still():
            raise
    if say is not None:
        say(f"waiting for the worker to finish {name}'s current unit: its pause is recorded "
            f"(at most {wait_s:g} s)…")
    deadline = time.monotonic() + wait_s
    while True:
        who = wait_while_held(state, deadline - time.monotonic(), still=still, poll_s=poll_s)
        if who is None:
            try:
                return take(state, name, what, exit_code=exit_code)
            except Held as e:
                if e.holder.get("what") != WORKER:
                    raise                     # another writer took it meanwhile: refused as `take` is
                who = e.holder
                time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))
        if not still():
            raise Held(name, who, exit_code=exit_code)     # the pause withdrawn: refused as before it
        if time.monotonic() >= deadline:
            raise Held(name, who, exit_code=3, waited=wait_s)
