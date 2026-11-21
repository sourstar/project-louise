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
"""
from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any

DESIRED: dict[str, Any] = {
    "memory_reclaimer_enabled": True,
    "memory_reclaimer_threshold": 0.95,
    "watchdog_idle_enabled": False,
    "auto_upgrade_backends": False,
    "auto_upgrade_addons": False,
}
#: what GET answers that POST must not be handed back: v5.0.1's projection
#: marker while its add-on owner is on (core/http/endpoints/localai/settings.go)
NOT_SETTINGS = ("addon_host_settings_authority",)


def merged(current: dict[str, Any], desired: dict[str, Any] = DESIRED) -> dict[str, Any]:
    """`current` with the desired keys set, every other key untouched (and
    no projection marker, `NOT_SETTINGS`)."""
    out = {k: v for k, v in current.items() if k not in NOT_SETTINGS}
    out.update(desired)
    return out


def drift(current: dict[str, Any], desired: dict[str, Any] = DESIRED) -> dict[str, Any]:
    """The managed keys whose live value is not the desired one: key → live."""
    return {k: current.get(k) for k, v in desired.items() if current.get(k) != v}


def _call(url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def apply(base: str) -> dict[str, Any]:
    url = base.rstrip("/") + "/api/settings"
    current = _call(url)
    if all(current.get(k) == v for k, v in DESIRED.items()):
        return {k: current.get(k) for k in DESIRED} | {"changed": False}
    _call(url, merged(current))
    now = _call(url)
    wrong = {k: now.get(k) for k, v in DESIRED.items() if now.get(k) != v}
    if wrong:
        raise SystemExit(f"LocalAI did not take the settings: {wrong}")
    return {k: now.get(k) for k in DESIRED} | {"changed": True}


def check(base: str) -> dict[str, Any]:
    """The managed keys as they are live, and what differs; nothing written."""
    current = _call(base.rstrip("/") + "/api/settings")
    return {k: current.get(k) for k in DESIRED} | {"drift": drift(current)}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--check"]
    base = args[0] if args else "http://127.0.0.1:30286"
    if "--check" in sys.argv[1:]:
        got = check(base)
        print(got)
        sys.exit(1 if got["drift"] else 0)
    print(apply(base))
