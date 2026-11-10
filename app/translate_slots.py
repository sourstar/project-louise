"""MADLAD's slots: how many segments the translation server decodes in one
wave (Friday list, operator 2026-11-07: "MADLAD slots configurable"; set to
4 by hand on the node the same week, from 7).

THE DESIRED STATE is the operator's, in platform.toml (Tier 2, invariant 10):

    [translate]
    slots = 4

`apply` makes the model server's own configuration say it, through
LocalAI's model-config API: it reads the model's `options`
(`GET /api/models/config-json/<model>`, as app/model_pins.py does), and
where its `parallel:N` differs it writes the list back with `parallel:<slots>`
(`PATCH /api/models/config-json/<model>`, a JSON merge whose arrays replace;
LocalAI then retires the running backend at the config's new revision, or
says `pending_cleanup` while it is busy, so the next load has the new
slots). Absent, nothing is written: the server's file stays as it was.

THE CLIENT READS WHAT THE SERVER HAS (RULE 2, the shape rule of
app/translate.py: determined from the server, never configured twice):
`concurrency` is the server's `parallel:N`, so a question's languages go out
as one wave of exactly the slots that decode them. Read once per TTL;
unreadable, the last known count, else app/translate.py's default.

STANDARD NOT ADOPTED, written down (CLAUDE.md): this is a one-key
reconciliation loop (Kubernetes' desired/observed pattern, as app/cpu_limit.py);
a declarative model store applied whole (LocalAI's gallery `models/apply`
with a pinned YAML) would replace it once more than one key of a model's
file is the node's to set.
"""
from __future__ import annotations

import threading
import time
from typing import Any

#: one call's bound: the model server answers its config in milliseconds
CALL_TIMEOUT_S = 10.0
#: how long a read of the server's slots is trusted
TTL_S = 600.0
#: the most slots a setting may ask for (MADLAD's KV pool is context_size
#: shared by the slots; 32 x 128 positions is already below a segment's need)
MAX_SLOTS = 32

_lock = threading.Lock()
_seen: dict[tuple[str, str], tuple[float, int]] = {}


def _parallel(options: Any) -> int | None:
    for o in options or ():
        k, sep, v = str(o).partition(":")
        if sep and k.strip() == "parallel":
            try:
                return int(v)
            except ValueError:
                return None
    return None


def _config(server: str, model: str) -> tuple[dict | None, str]:
    from app import httpc
    try:
        r = httpc.get(f"{server}/api/models/config-json/{model}", timeout=CALL_TIMEOUT_S)
    except Exception as e:   # noqa: BLE001 — said by the caller
        return None, f"{type(e).__name__}: {e}"[:300]
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        return dict(r.json() or {}), ""
    except ValueError as e:
        return None, f"not JSON: {e}"[:300]


def server_slots(server: str, model: str, *, now: float | None = None) -> int | None:
    """The slots `model` has on `server` now (its `parallel:N`); None where it
    cannot be read or states none."""
    now = time.monotonic() if now is None else now
    key = (server, model)
    with _lock:
        hit = _seen.get(key)
        if hit is not None and now - hit[0] < TTL_S:
            return hit[1]
    cfg, _why = _config(server, model)
    got = _parallel((cfg or {}).get("options")) if cfg is not None else None
    if got is not None and got >= 1:
        with _lock:
            _seen[key] = (now, got)
        return got
    with _lock:
        return hit[1] if hit is not None else None


def reset() -> None:
    with _lock:
        _seen.clear()


def apply(server: str, model: str, slots: int | None) -> dict[str, Any]:
    """Make `model`'s configuration on `server` say `parallel:<slots>`. Returns
    what it did: `unchanged`, `set` (with `from` and `pending_cleanup`),
    `skipped` (no setting) or `failed` (with `why`). Never raises."""
    if slots is None:
        return {"skipped": "[translate] slots is not set: the model's file is left as it is"}
    cfg, why = _config(server, model)
    if cfg is None:
        return {"failed": f"{model}: its configuration cannot be read ({why})"}
    options = [str(o) for o in (cfg.get("options") or [])]
    have = _parallel(options)
    if have == slots:
        return {"unchanged": slots}
    new = [o for o in options if o.partition(":")[0].strip() != "parallel"] + [f"parallel:{slots}"]
    from app import httpc
    try:
        r = httpc.patch(f"{server}/api/models/config-json/{model}", json={"options": new},
                        timeout=CALL_TIMEOUT_S)
    except Exception as e:   # noqa: BLE001 — said, and tried again at the next apply
        return {"failed": f"{model}: {type(e).__name__}: {e}"[:300]}
    if r.status_code != 200:
        return {"failed": f"{model}: the server refused the new slots (HTTP {r.status_code}): "
                          f"{r.text[:200]}"}
    try:
        pending = int((r.json() or {}).get("pending_cleanup") or 0)
    except ValueError:
        pending = 0
    with _lock:
        _seen.pop((server, model), None)
    return {"set": slots, "from": have, "pending_cleanup": pending}


def reconcile(stop, *, every: float = TTL_S, log=None, sleep=time.sleep,
              desired=None) -> None:
    """The worker's loop: at boot, then every `every` seconds, `apply` the
    platform file's `[translate] slots` to TRANSLATE_MODEL (an edit to the file
    reaches the server within one period, invariant 10). One GET a period
    when nothing differs; a failure is said once and tried again next period."""
    from app import config, model_load
    said: str | None = None
    while not stop():
        if config.TRANSLATE_URL and config.TRANSLATE_MODEL:
            try:
                want = desired() if desired is not None else _desired()
                got = apply(model_load.server_of(config.TRANSLATE_URL), config.TRANSLATE_MODEL, want)
            except Exception as e:   # noqa: BLE001 — a bad file is said, never a crash
                got = {"failed": f"[translate] slots: {e}"[:300]}
            line = _said(got)
            if line and line != said and log is not None:
                log("warn" if "failed" in got else "info", line)
            said = line
        sleep(every)


def _desired() -> int | None:
    from app import platform_config
    return platform_config.load().translate.slots


def _said(got: dict[str, Any]) -> str | None:
    from app import config
    if "set" in got:
        later = (f"; {got['pending_cleanup']} request(s) still on the old ones, cleaned after"
                 if got.get("pending_cleanup") else "")
        return (f"translation: {config.TRANSLATE_MODEL} slots {got.get('from')} -> {got['set']} "
                f"([translate] slots{later})")
    if "failed" in got:
        return f"translation: slots not applied: {got['failed']}"
    return None
