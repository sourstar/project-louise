"""LocalAI's runtime settings that Louise depends on, applied to the node's
LocalAI through its own API — so a reinstall or a redeploy of the app, which
starts from LocalAI's defaults, never quietly brings an old value back.

    python3 deploy/vega/stage/localai_settings.py [http://127.0.0.1:30286]

It reads the live settings (`GET /api/settings`), sets only the keys below,
writes the whole object back (`POST /api/settings`: a partial body is not
documented to leave the rest alone) and reads them again to verify. Nothing
is printed but the keys it manages, because the object also carries
`api_keys` and `p2p_token`. Applied live, no restart.

memory_reclaimer_threshold, 0.68 → 0.85 (operator, 2026-10-23): the WatchDog
evicts the least recently used model once the card passes it. Measured on the
B70 (32,656 MiB): all eight models Louise loads take 26,693 MiB (82 %) —
vega-qwen3-embedding-4b 8,707, louise-chat-qwen3-8b 7,363,
louise-translate-madlad 6,424, vega-dar-legal-it 1,819, louise-rerank-bge
1,243, louise-rerank-medcpt 424, vega-medcpt-query 391, vega-medcpt-article-ov
322 MiB. At 0.68 (22,206 MiB) the six resident models held the card so high
that the query embedder and the chat model evicted each other on every Ask
question (4.5–6.3 s cold starts each); at 0.85 both stay, and a question went
from 20.9 + 11.1 s to 15.0 + 5.1 s. The embedder's load-time peak still
reaches 85.9 %, so the translator is the one evicted on that load.

memory_reclaimer_threshold, 0.85 → 0.95 (operator, 2026-11-08: "New eviction
threshold at 95%"), for the trial of keeping every model Ask uses resident
("The load all is a test"). Measured on the B70 the night before (live
engine ea2417d, München): every model resident together takes 30,267 of
32,656 MiB (92.7 %) — madlad 7,879, chat qwen3.5-9b-q4-p0 5,791,
qwen3-embedding-4b 5,089, nemotron 3,917, omnivoice 2,287, dar-legal-it
1,667, bge-m3 1,180, the two rerankers 881 each, medcpt-query 359,
medcpt-article-ov 312 MiB. At 0.85 the reclaimer would evict a model at
every tick with all of them loaded; at 0.95 they stay with 2.3 % (≈750 MiB)
below the line, so a load-time peak above it still evicts the least
recently used unpinned model.

memory_reclaimer_threshold, 0.95 → 0.98 (operator, 2026-11-23, B: "Whats left
are models and weights"; "0.98" to München the same evening). The keeper's
budget (app/resident.py) counts the models' weights and keeps CONTEXT_RESERVE,
3,262 MiB, free under this line for working memory, so the line no longer has
to leave that room itself. 0.98 × 32,656 MiB = 32,002 MiB: a budget of 28,741
MiB for weights, the reserve above it, and 650 MiB between the line and the
card's full 100 % as the reclaimer's safety net. It ships with the reserve
(one deploy); alone it would widen the budget into the room the contexts need.

watchdog_idle_enabled → false (operator, 2026-11-08, relayed by the node:
"nothing is evicted only because it's idle, only if space is claimed").
LocalAI's idle watchdog unloaded the chat model after 5 minutes without a
request (watchdog_idle_timeout 5m0s), so the first question after a pause
paid its load. Space is claimed through the memory reclaimer above; the busy
watchdog (a hung backend) stays on.

auto_upgrade_backends, auto_upgrade_addons → false (operator, 2026-11-21, the
v5 swap: "auto-upgrade off"; invariant 12). The env var
LOCALAI_AUTO_UPGRADE_BACKENDS=false is NOT enough: LocalAI's runtime settings
(runtime_settings.json, written from its UI) win over it, and the node's
held `"auto_upgrade_backends": true` from 2026-11-08 — v4.12.1 never acted on
it, v5.0.1's upgrade checker did, the same morning: intel-sycl-f16-llama-cpp-
development, intel-sycl-f16-ced and intel-transformers were upgraded by
LocalAI itself, outside every gate (München). No Louise engine runs on them;
the setting is held here, and the deploy applies this file and fails where
LocalAI does not take it.

    python3 deploy/vega/stage/localai_settings.py --check [URL]

reads the live settings and exits 1 naming each managed key that differs,
changing nothing.

PAGE INSTEAD OF UNLOAD (operator, 2026-11-24; platform.toml `[models]
make_room = "page"`, app/model_evict.py): memory_reclaimer_enabled → false,
the threshold kept. A card that holds more than its size — the driver
paging idle models to host memory — reads full at every tick, and the
reclaimer would unload the least recently used model each time; the
threshold stays because Louise's keeper reads it for its budget's line.
`--platform FILE` names the node's platform.toml: THE FILE DECIDES, so that
a deploy applies and checks the mode the node is in instead of putting the
other back (the architecture session's review). Without it: "unload".
"""
from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any

DESIRED: dict[str, Any] = {
    "memory_reclaimer_enabled": True,
    "memory_reclaimer_threshold": 0.98,
    "watchdog_idle_enabled": False,
    "auto_upgrade_backends": False,
    "auto_upgrade_addons": False,
}
#: what GET answers that POST must not be handed back: v5.0.1's projection
#: marker while its add-on owner is on (core/http/endpoints/localai/settings.go)
NOT_SETTINGS = ("addon_host_settings_authority",)
#: the managed keys LocalAI's ADD-ON OWNER holds while it runs (v5.0.1
#: `addonHostSettingsKeys`): GET leaves them out, POST refuses them, 409 "…
#: is set on the add-on owner, not through /api/settings" — the deploy of
#: 2f26d6d, rolled back on it (München, 2026-11-22: the operator had add-ons
#: on again). The owner's own policy defaults to false
#: (core/services/addons/hostsettings/policy.go) and is set in LocalAI's
#: Settings, through the owner's revisioned protocol: here such a key is
#: neither written nor checked, and said to be the owner's.
OWNER_KEYS = ("auto_upgrade_addons",)


def owner_holds(current: dict[str, Any]) -> bool:
    """Is LocalAI's add-on owner running, holding OWNER_KEYS?"""
    return current.get("addon_host_settings_authority") == "owner_required"


#: `[models] make_room` of the node's platform.toml, set from `--platform`
MAKE_ROOM = "unload"


def make_room_of(platform: str | None) -> str:
    """`[models] make_room` in the file named, "unload" where it says
    nothing; a file that cannot be read or says another word stops the
    deploy — the reclaimer is never set by a guess."""
    if not platform:
        return "unload"
    import tomllib
    with open(platform, "rb") as f:
        mode = (tomllib.load(f).get("models") or {}).get("make_room", "unload")
    if mode not in ("unload", "page"):
        raise SystemExit(f"{platform}: [models] make_room is \"unload\" or \"page\", got {mode!r}")
    return mode


def desired_for(current: dict[str, Any], make_room: str | None = None) -> dict[str, Any]:
    """DESIRED for the node's `make_room` (the reclaimer off where idle
    models are paged), less what the add-on owner holds while it runs."""
    want = dict(DESIRED)
    if (make_room or MAKE_ROOM) == "page":
        want["memory_reclaimer_enabled"] = False
    if not owner_holds(current):
        return want
    return {k: v for k, v in want.items() if k not in OWNER_KEYS}


def merged(current: dict[str, Any], desired: dict[str, Any] | None = None) -> dict[str, Any]:
    """`current` with the desired keys set, every other key untouched (and
    no projection marker, `NOT_SETTINGS`, nor a key the add-on owner holds)."""
    desired = desired_for(current) if desired is None else desired
    out = {k: v for k, v in current.items() if k not in NOT_SETTINGS}
    out.update(desired)
    return out


def drift(current: dict[str, Any], desired: dict[str, Any] | None = None) -> dict[str, Any]:
    """The managed keys whose live value is not the desired one: key → live."""
    desired = desired_for(current) if desired is None else desired
    return {k: current.get(k) for k, v in desired.items() if current.get(k) != v}


def _said(current: dict[str, Any]) -> dict[str, Any]:
    out = {k: current.get(k) for k in desired_for(current)}
    if owner_holds(current):
        out["held_by_the_addon_owner"] = list(OWNER_KEYS)
    return out


def _call(url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def apply(base: str) -> dict[str, Any]:
    url = base.rstrip("/") + "/api/settings"
    current = _call(url)
    if not drift(current):
        return _said(current) | {"changed": False}
    _call(url, merged(current))
    now = _call(url)
    wrong = drift(now)
    if wrong:
        raise SystemExit(f"LocalAI did not take the settings: {wrong}")
    return _said(now) | {"changed": True}


def check(base: str) -> dict[str, Any]:
    """The managed keys as they are live, and what differs; nothing written."""
    current = _call(base.rstrip("/") + "/api/settings")
    return _said(current) | {"drift": drift(current)}


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--platform" in argv:
        at = argv.index("--platform")
        MAKE_ROOM = make_room_of(argv[at + 1] if at + 1 < len(argv) else None)
        del argv[at:at + 2]
    args = [a for a in argv if a != "--check"]
    base = args[0] if args else "http://127.0.0.1:30286"
    if "--check" in sys.argv[1:]:
        got = check(base)
        print(got)
        sys.exit(1 if got["drift"] else 0)
    print(apply(base))
