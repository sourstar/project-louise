"""Advisory single-writer claim for acquisition.

`louise hold records fetch` and the background worker can both call
run_acquisition on the same collection with nothing preventing overlap. Two
processes would then race on the same `.part` files and on os.replace, and
both would burn origin bandwidth fetching identical bytes — the opposite of
the etiquette the transfer manager exists to enforce.

The race pre-dates P0.7, but P0.7 widens it: the delta gate is now evaluated
once up front rather than immediately before each file, so the overlap window
grows from "the same file happened to be next in both processes" to "the whole
pending list".

ADVISORY, not a lock. It lives in the existing `control` table (Tier 3:
runtime state, ephemeral, never desired state — CLAUDE.md invariant 10), holds
pid + timestamp, and goes stale on its own so a hard-killed process cannot
wedge acquisition forever. A stale claim is simply taken over. This is the same
posture as the coordinator leases in architecture-v2.md §7: collisions are made
harmless rather than impossible.

A CLAIM NAMES THE BOOT IT WAS MADE IN (2026-11-26, München's D6): the worker
a hardware reset killed held the claim, 179 s old, and its successor's
startup fetch was refused for the rest of the 300 s. A claim of another boot
of the host's kernel (`boot_id`) has a dead holder for certain, whatever its
age; one that names no boot (made before this) is judged by its age alone.
No clock is read for it: a clock stepped after a boot moves `btime`, never
the boot's id.

THE STANDARD ALTERNATIVE, written down: an flock on a per-collection lock
file, as the collection's write lock is (app/hold/lock.py) — the kernel drops
it with its holder, so a dead holder is known at once, a reset or an OOM kill
alike, with no staleness to wait out. It would replace this row and its
refresh, and the Library card's "in progress" would read the lock instead.
It pays the next time a dead holder's claim costs a wait that matters: an
OOM-killed fetch (no reset) still waits up to STALE_AFTER_SECONDS here.
"""
import json
import os
import time
from contextlib import contextmanager

from .. import db

CLAIM_KEY = "acquire_claim"
# Generous relative to a fetch loop iteration, short enough that a killed
# process never blocks the next run for long.
STALE_AFTER_SECONDS = 300
_REFRESH_EVERY = 60


class AcquisitionBusy(RuntimeError):
    """Another live process is already acquiring for this collection.
    `retry_in`: the seconds until its claim goes stale, if it is not renewed
    (the caller asks again then: a refusal is a deferral, never a failure)."""

    def __init__(self, message: str, retry_in: float = float(STALE_AFTER_SECONDS), *,
                 holder: str = "", held_for: int = 0) -> None:
        super().__init__(message)
        self.retry_in = max(1.0, float(retry_in))
        self.holder = holder
        self.held_for = held_for


def boot_id() -> str | None:
    """THIS BOOT of the host's kernel (`/proc/sys/kernel/random/boot_id`): the
    same in every container a runc runtime (Docker, the node's) starts on the
    host, another after every boot. None where it cannot be read. Under a
    runtime with a kernel of its own (gVisor, Kata) each sandbox has its own:
    every writer of a manifest is on one kernel already (invariant 6, /data is
    a local filesystem), and a node that ran its passes in such sandboxes —
    or in LXC/Incus containers (TrueNAS "Instances"), whose liblxc gives each
    its own — would have to drop this rule."""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
            got = f.read().strip()
    except OSError:
        return None
    return got or None


def read_claim(con) -> dict | None:
    raw = db.get_control(con, CLAIM_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _me() -> str:
    """This process on this host: the pid AND its PID namespace
    (`model_pins.process_tag`). A pid alone names a process only inside its
    container, and the worker and a supervised `hold records fetch` pass
    can each be pid 1 in their own: each read the other's claim as its own
    and both fetched the same pending list (the cross-container collision
    #582 fixed for leases and pins, 2026-11-12)."""
    from .. import model_pins
    return model_pins.process_tag()


def _mine(claim: dict | None) -> bool:
    """Is `claim` this process's? A claim from before the tag (pid only) is
    read as `pid N`, which the tag of a process outside a namespace equals."""
    if not claim:
        return False
    return claim.get("holder", f"pid {claim.get('pid')}") == _me()


def _record(source_id: str, now: float) -> str:
    return json.dumps({"pid": os.getpid(), "holder": _me(), "ts": now, "source_id": source_id,
                       "boot": boot_id()})


def held(claim: dict | None, now: float | None = None) -> bool:
    """Is `claim` held — by any process, this one included: not stale, and
    made in this boot of the host where it names one? THE one reading of a
    claim's life (the Library card's "in progress" reads it too)."""
    if not claim:
        return False
    now = time.time() if now is None else now
    ts = float(claim.get("ts", 0))
    if now - ts > STALE_AFTER_SECONDS:
        return False          # stale: the holder died or hung; take it over
    if ts > now + 2 * _REFRESH_EVERY:
        # MADE AFTER NOW by more than two refreshes: the clock was stepped back
        # since, and a live holder rewrites its claim within one refresh of any
        # step (`refresh_claim`) — this one did not
        return False
    made_in, this = claim.get("boot"), boot_id()
    if made_in and this and made_in != this:
        # MADE IN ANOTHER BOOT: its holder is dead for certain, whatever its age
        return False
    return True


def _is_live(claim: dict | None, now: float) -> bool:
    """Held by ANOTHER process: what refuses this one."""
    return held(claim, now) and not _mine(claim)


def busy_for(con, now: float | None = None) -> float | None:
    """The seconds until another process's live claim goes stale if it is not
    renewed (a little more), or None when no other process holds one: what a
    refused fetch reads before it is asked again — never a fetch, never a scan,
    while a live holder renews (the read of 2026-11-26: a holder renewing
    every 60 s cost a full discovery every five minutes)."""
    now = time.time() if now is None else now
    claim = read_claim(con)
    if not _is_live(claim, now):
        return None
    return max(1.0, STALE_AFTER_SECONDS - (now - float(claim.get("ts", now))) + 5)


@contextmanager
def acquisition_claim(con, source_id: str, force: bool = False):
    """Claim acquisition for `source_id`, or raise AcquisitionBusy.

    Released on the way out even if the body raises, so a crash inside
    run_acquisition does not leave the claim held for the full stale window.
    """
    now = time.time()
    existing = read_claim(con)
    if not force and _is_live(existing, now):
        held_for = int(now - float(existing.get("ts", now)))
        raise AcquisitionBusy(
            f"{source_id}: acquisition already running in "
            f"{existing.get('holder') or 'pid ' + str(existing.get('pid'))} "
            f"(claimed {held_for}s ago). Wait for it to finish, or re-run once the "
            f"claim goes stale after {STALE_AFTER_SECONDS}s.",
            retry_in=STALE_AFTER_SECONDS - (now - float(existing.get("ts", now))) + 5,
            holder=str(existing.get("holder") or f"pid {existing.get('pid')}"), held_for=held_for)
    db.set_control(con, CLAIM_KEY, _record(source_id, now))
    try:
        yield
    finally:
        if _mine(read_claim(con)):
            db.set_control(con, CLAIM_KEY, "")


def refresh_claim(con, source_id: str, last_refresh: float) -> float:
    """Keep a long-running acquisition's claim from going stale under it.
    Returns the (possibly unchanged) last-refresh timestamp."""
    now = time.time()
    if 0 <= now - last_refresh < _REFRESH_EVERY:
        return last_refresh           # a clock stepped back refreshes at once: its ts is in the future
    if _mine(read_claim(con)):
        db.set_control(con, CLAIM_KEY, _record(source_id, now))
    return now
