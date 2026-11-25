"""LOCALAI'S MEMORY RECLAIMER FOLLOWS THE NODE'S RULE FOR A FULL CARD
(operator, 2026-11-25: "A for paging" — in query mode nothing is unloaded to
make room while system memory allows).

Where idle models are PAGED (`app/model_evict.py` `rule()`: `[models]
make_room = "page"`, and query mode whatever the file says) the card holds
more than its size and reads full at every tick; LocalAI's reclaimer would
then unload the least recently used model each time — the node,
2026-11-25, 15:57Z, a minute after the operator's switch to query mode:
vega-medcpt-article-ov "off the card again 4 min after its load: space is
claimed", with `memory_reclaimer_enabled` true. So:

    memory_reclaimer_enabled  =  (the rule in force is not "page")

It was the deploy's alone to set (deploy/vega/stage/localai_settings.py
`--platform`: THE FILE DECIDES) — and the file cannot say which mode the
node is in. The keeper's beat now holds it to the rule in force, so a switch
of mode moves it within a beat and a deploy that put the file's value back
is corrected at the next one. The threshold is never touched: the keeper's
budget reads it for its line.

HOW, exactly as the stage script does: LocalAI's settings are read whole
(`GET /api/settings`), the one key is set, the object is written back whole
(`POST`: a partial body is not documented to leave the rest alone) and read
again to verify. THE OBJECT CARRIES `api_keys` AND THE P2P TOKEN (invariant
8): it passes through this function and nowhere else — nothing of it is
kept, said or put in an error but the one key. The projection marker and
the keys LocalAI's add-on owner holds are left out of the write, as there.

The standard alternative, written down: a LocalAI setting per model state —
the parking server part's `park_host_floor_bytes` (docs/notes/
parking-step-2.md §3.4, 5) is written the same way, by the keeper; with
parking live the reclaimer's own accounting could know a parked model and
this switch would not be needed.
"""
from __future__ import annotations

from typing import Any

KEY = "memory_reclaimer_enabled"
#: THE HOST MEMORY THAT MUST STAY AVAILABLE ONCE A MODEL'S WEIGHTS ARE PARKED
#: THERE, in bytes (the server's 0027; docs/notes/parking-step-2.md §3.4, 5):
#: LocalAI reads it before EACH park; 0, its default, means nothing is parked.
#: Never set by hand — the keeper writes the node's one host floor
#: (`[models] page_floor_gib`, app/resident.py `_parked_follow`), the same way
PARK_FLOOR_KEY = "park_host_floor_bytes"
#: as deploy/vega/stage/localai_settings.py: a marker LocalAI's GET adds while
#: its add-on owner is on, and the keys that owner holds (POST refuses them)
NOT_SETTINGS = ("addon_host_settings_authority",)
OWNER_KEYS = ("auto_upgrade_addons",)
TIMEOUT_S = 10.0


class NotTaken(RuntimeError):
    """LocalAI did not take the setting. Its message never carries the
    settings object."""


def wanted(rule: str) -> bool:
    """Whether the reclaimer is ON under this make-room rule."""
    return rule != "page"


def _body(current: dict[str, Any], value: Any, key: str = KEY) -> dict[str, Any]:
    held = OWNER_KEYS if current.get("addon_host_settings_authority") == "owner_required" else ()
    out = {k: v for k, v in current.items() if k not in NOT_SETTINGS and k not in held}
    out[key] = value
    return out


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b       # True is not 1: a switch and a count are told apart


def set_enabled(server: str, enabled: bool) -> bool:
    """LocalAI's reclaimer turned on or off at `server`; True when it was
    changed, False when it already was so. Raises `NotTaken` — naming the
    status or the kind of failure, never a byte of the object — when the
    settings cannot be read, written or verified."""
    return set_key(server, KEY, bool(enabled))


def set_park_floor(server: str, floor_bytes: int) -> bool:
    """LocalAI's park floor (`PARK_FLOOR_KEY`) set to `floor_bytes` at
    `server`; True when it was changed. Raises `NotTaken` as `set_enabled`
    does — on a server without the park patch the key does not read back,
    and that is what it says."""
    return set_key(server, PARK_FLOOR_KEY, int(floor_bytes))


def set_key(server: str, key: str, value: Any) -> bool:
    """ONE key of LocalAI's settings set to `value`, the rest written back as
    it was read (the module's docstring); True when it was changed, False
    when it already was so. Raises `NotTaken`."""
    from app import httpc
    url = f"{server.rstrip('/')}/api/settings"

    def read() -> dict[str, Any]:
        try:
            r = httpc.get(url, timeout=TIMEOUT_S)
            status = r.status_code
            got = r.json() if status == 200 else None
        except Exception as e:   # noqa: BLE001 — the exception may quote the answer: only its kind is said
            raise NotTaken(f"its settings could not be read ({type(e).__name__})") from None
        if not isinstance(got, dict):
            raise NotTaken(f"its settings could not be read (HTTP {status})")
        return got
    current = read()
    if _same(current.get(key), value):
        return False
    try:
        r = httpc.post(url, json=_body(current, value, key), timeout=TIMEOUT_S)
        status = r.status_code
    except Exception as e:   # noqa: BLE001 — as above
        raise NotTaken(f"its settings could not be written ({type(e).__name__})") from None
    if status >= 400:
        raise NotTaken(f"its settings were refused (HTTP {status})")
    if not _same(read().get(key), value):
        raise NotTaken(f"it answered HTTP {status} and {key} is unchanged")
    return True
