"""THE UNLOADS LOUISE MADE ITSELF, kept where every process of the node can
read them (`state/model-unloads.json`, beside the pin record).

WHY (München, 2026-11-25 22:40Z, embed mode, ten minutes after the switch).
A question took the card as it always does — "a person's question holds the
card; corpus encoder(s) unloaded" — and two encoders went with it. The
keeper, a beat later: "vega-dar-legal-it … off the card again 10 min after
its load: space is claimed, so it is not loaded again for 10 min", and "the
budget shrank by 8,453 MiB: vega-dar-legal-it was evicted 10 min after its
load … the penalty on the margin is 8,880 MiB, halved after each 2 h". The
keeper's back-off and its penalty are FEEDBACK ON THE RECLAIMER'S EVICTIONS
(app/resident.py: "the reclaimer's eviction is the loss signal" — it saw the
card past its line where a beat's sample did not). An unload Louise made —
for a question, for a load's room, at a change of mode, for a gate's twin —
is no such signal: Louise chose it, for a reason that says nothing of the
card's growth. Until now the keeper could not tell them apart: the question
runs in the web's process, the keeper in the worker's, and all the keeper
saw was a model it had loaded gone again within ten minutes.

So `model_load.unload` and `model_load.park` — the two calls every unload of
Louise's goes through — note the model here when the runner took the call,
and the keeper asks `since(key, seconds)` before it counts an eviction.

A FILE, not a table: the pin record's way (app/model_pins.py), read and
written under a lock file, replaced whole; a line per model, the newest
write only, pruned after KEEP_S. Losing it costs one wrong back-off, as
before. Standard alternative, written down: the runner saying WHO stopped a
model (a reason on LocalAI's `/backend/shutdown` and in its eviction's log
line, read back through `/system`) — an upstream change; it would pay once
programs other than Louise stop models on this runner.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

#: a note older than this says nothing any more (the keeper asks about the
#: last REEVICTED_WITHIN_S, ten minutes)
KEEP_S = 3600.0


def _path() -> Path:
    from app import config
    return config.louise_root() / "state" / "model-unloads.json"


def _key(server: str, model: str) -> str:
    return f"{server}|{model}"


def note(server: str, model: str, why: str = "", *, now: float | None = None) -> None:
    """Louise stopped (or parked) `model` at `server`, now. Never raises: a
    note that cannot be written costs the keeper one wrong back-off."""
    at = time.time() if now is None else now
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p.with_name(".model-unloads.lock"), "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                try:
                    st = json.loads(p.read_bytes())
                except (OSError, ValueError):
                    st = {}
                st = {k: v for k, v in st.items()
                      if isinstance(v, dict) and at - float(v.get("at", 0)) < KEEP_S}
                st[_key(server, model)] = {"at": at, "why": str(why)[:120]}
                tmp = p.with_name(".model-unloads.json.tmp")
                tmp.write_text(json.dumps(st, sort_keys=True), encoding="utf-8")
                os.replace(tmp, p)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except Exception:   # noqa: BLE001
        pass


def since(key: tuple[str, str], seconds: float, *, now: float | None = None) -> str | None:
    """Whether Louise itself stopped `key` = (server, model) within the last
    `seconds`: the note's reason ("" where none was given), else None. A
    record that cannot be read says None — the keeper then counts as before."""
    at = time.time() if now is None else now
    try:
        got = json.loads(_path().read_bytes()).get(_key(*key))
        if isinstance(got, dict) and 0 <= at - float(got.get("at", 0)) <= seconds:
            return str(got.get("why") or "")
    except Exception:   # noqa: BLE001 — no record: nothing known
        pass
    return None
