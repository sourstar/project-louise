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
WITHIN ONE BOOT ITS AGE IS THE BOOT'S OWN CLOCK (`uptime`, CLOCK_BOOTTIME:
the same in every runc container, never stepped): a wall clock stepped back
made a live claim read as stale, and another process took it while its
holder fetched (the third read, run). The wall clock (`ts`) dates a claim
that names no boot, and is kept for whoever reads the row.

ONE WRITER AT A TIME: a claim is taken under BEGIN IMMEDIATE (read, check and
write in one transaction), renewed and given back only where it is still the
holder's own (one compare-and-set statement) — read then written apart, a
holder could overwrite the claim another had just taken.

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
import sqlite3
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


def uptime() -> float | None:
    """Seconds since this boot of the host's kernel (CLOCK_BOOTTIME: shared by
    every container of the host, never stepped, counting a suspend). None
    where it cannot be read."""
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError, ValueError):
        return None


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
                       "boot": boot_id(), "up": uptime()})


def _age(claim: dict, now: float) -> float:
    """How old `claim` is: on the boot's own clock where it was made in this
    boot (`uptime`: never stepped), on the wall clock otherwise."""
    made_in, this = claim.get("boot"), boot_id()
    up, up_now = claim.get("up"), uptime()
    if made_in and this and made_in == this and isinstance(up, (int, float)) and up_now is not None \
            and up_now >= float(up):
        return up_now - float(up)
    # another boot, no boot named, or a boot clock that reads it in the future
    # (a time namespace — CRIU): the wall clock's word
    return now - float(claim.get("ts", 0))


def held(claim: dict | None, now: float | None = None) -> bool:
    """Is `claim` held — by any process, this one included: not stale, and
    made in this boot of the host where it names one? THE one reading of a
    claim's life (the Library card's "in progress" reads it too)."""
    if not claim:
        return False
    now = time.time() if now is None else now
    made_in, this = claim.get("boot"), boot_id()
    if made_in and this and made_in != this:
        # MADE IN ANOTHER BOOT: its holder is dead for certain, whatever its age
        return False
    return _age(claim, now) <= STALE_AFTER_SECONDS      # stale: the holder died or hung


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
    return max(1.0, STALE_AFTER_SECONDS - _age(claim, now) + 5)


_HOLDER_IS = ("CASE WHEN json_valid(value) THEN json_extract(value, '$.holder') END = ?")


def _set_if_mine(con, value: str) -> bool:
    """Write `value` to the claim's row only where the claim is this
    process's: one statement, so nothing taken meanwhile is overwritten."""
    cur = con.execute(f"UPDATE control SET value=? WHERE key=? AND {_HOLDER_IS}", (value, CLAIM_KEY, _me()))
    return bool(cur.rowcount)


@contextmanager
def acquisition_claim(con, source_id: str, force: bool = False):
    """Claim acquisition for `source_id`, or raise AcquisitionBusy.

    Released on the way out even if the body raises, so a crash inside
    run_acquisition does not leave the claim held for the full stale window.
    """
    def refuse_if_live(existing: dict | None, now: float) -> None:
        if not force and _is_live(existing, now):
            age = _age(existing, now)
            raise AcquisitionBusy(
                f"{source_id}: acquisition already running in "
                f"{existing.get('holder') or 'pid ' + str(existing.get('pid'))} "
                f"(claimed {int(age)}s ago). Wait for it to finish, or re-run once the "
                f"claim goes stale after {STALE_AFTER_SECONDS}s.",
                retry_in=STALE_AFTER_SECONDS - age + 5,
                holder=str(existing.get("holder") or f"pid {existing.get('pid')}"), held_for=int(age))
    # A LIVE CLAIM REFUSES WITHOUT THE WRITE LOCK (the fourth read, run: under
    # BEGIN IMMEDIATE first, a refusal on a manifest a scan streams into waited
    # out the busy timeout and came back as OperationalError, not as the
    # refusal — no retry was armed and the fetch was lost: D6 again). Only a
    # claim that looks free is taken, and read again, under the lock
    refuse_if_live(read_claim(con), time.time())
    # read, check and write in ONE transaction (the module's docstring); a
    # caller already inside one keeps it, as before
    own_tx = not con.in_transaction
    if own_tx:
        con.execute("BEGIN IMMEDIATE")
    try:
        now = time.time()                 # after the lock's wait: the record's time is the write's
        refuse_if_live(read_claim(con), now)
        db.set_control(con, CLAIM_KEY, _record(source_id, now))
        if own_tx:
            con.execute("COMMIT")
    except BaseException:
        if own_tx and con.in_transaction:
            con.execute("ROLLBACK")
        raise
    try:
        yield
    finally:
        try:
            _set_if_mine(con, "")
        except sqlite3.OperationalError:
            # the manifest busy past its timeout: the claim goes stale by itself
            # (and with the boot), and the pass's own result or error stands —
            # raised here, it replaced both (the fourth read, run)
            pass


def refresh_claim(con, source_id: str, last_refresh: float) -> float:
    """Keep a long-running acquisition's claim from going stale under it.
    Returns the (possibly unchanged) last-refresh timestamp. Renews only a
    claim that is still this process's (`_set_if_mine`)."""
    now = time.time()
    if 0 <= now - last_refresh < _REFRESH_EVERY:
        return last_refresh           # a clock stepped back refreshes at once
    _set_if_mine(con, _record(source_id, now))
    return now
