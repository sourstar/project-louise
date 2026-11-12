"""THE GATE TWIN: a gate measures on a load of its own (operator, 2026-11-12:
"gating a collection cannot monopolize the GPU").

WHY A SECOND LOAD. The gate's passes are measured on a model nothing else
asks meanwhile: the one-text-at-a-time fingerprints, the fresh loads (the
first text after a reload) and the 300 fresh contexts the engine counts
(app/hold/embedder.py). On the shared load that meant every other
collection on the model stood aside for the whole gate — legi's, 11 minutes
on 2026-11-12, normattiva-nemo and wikidata waiting. On a second load of the
same model the gate owns its counts, its reloads and its batches, and the
corpus keeps the main load: a share of the card, never the whole model.

THE SAME RUN, BY CONSTRUCTION. The twin is the model's own LocalAI config
under another name, `<model>--gate`. A run's identity is what the wrapper
launched — the backend's tree, its args with the port a placeholder, env,
device, options and the weights (deploy/vega/vega_wrapper.py `state_run`) —
and never the config's name, so the twin states the main load's run id
(measured on the node, 2026-11-12: every field identical). The gate still
holds them to it: a twin that states another run is not measured on.

HOW IT IS MADE (RULE 2: the node determines it, nobody writes a file). The
main config is read through LocalAI's model-config API (`GET
/api/models/config-json/<model>`, as app/model_pins.py reads it) and written
back under the twin's name with `POST /models/import` — a YAML body, and
JSON is YAML. It is listed at once (no reload); an unchanged twin is not
written again. The twin is unpinned and stopped after the gate (`POST
/backend/shutdown`), which gives its VRAM back (2,390 MiB for Nemotron f16).

STANDARD, WRITTEN DOWN (CLAUDE.md): this is a blue/green replica in the
serving sense — a second instance of the same artifact taking one kind of
traffic — and LocalAI's own config store is the registry. Its multi-instance
routing (one config, several backends) would replace the alias once LocalAI
routes a request to a chosen instance.
"""
from __future__ import annotations

import json
from typing import Any

from . import httpc

SUFFIX = "--gate"
#: one call's bound: the model server answers its config in milliseconds
CALL_TIMEOUT_S = 15.0
#: keys of a config that are the server's bookkeeping, not the load
_NOT_THE_LOAD = ("name", "pinned")


class TwinUnavailable(Exception):
    """No twin can be had: the reason, in words."""


def name_of(model: str) -> str:
    return f"{model}{SUFFIX}"


def is_twin(model: str | None) -> bool:
    return bool(model) and str(model).endswith(SUFFIX)


def _config(server: str, model: str) -> dict[str, Any] | None:
    r = httpc.get(f"{server.rstrip('/')}/api/models/config-json/{model}", timeout=CALL_TIMEOUT_S)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise TwinUnavailable(f"the model server did not give {model}'s config: HTTP {r.status_code}")
    got = r.json()
    if not isinstance(got, dict):
        raise TwinUnavailable(f"the model server gave {model}'s config as {type(got).__name__}")
    return got


def _load(config: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in config.items() if k not in _NOT_THE_LOAD}


def ensure(server: str, model: str) -> str:
    """The twin of `model` on `server`, made or refreshed from the model's
    config; its name. TwinUnavailable when the server cannot say or write it."""
    try:
        main = _config(server, model)
        if main is None:
            raise TwinUnavailable(f"the model server holds no config for {model}")
        twin = name_of(model)
        held = _config(server, twin)
        if held is not None and _load(held) == _load(main):
            return twin
        want = {**main, "name": twin, "pinned": False}
        r = httpc.post(f"{server.rstrip('/')}/models/import",
                       content=json.dumps(want, sort_keys=True).encode("utf-8"),
                       headers={"Content-Type": "application/x-yaml"}, timeout=CALL_TIMEOUT_S)
        if r.status_code != 200:
            raise TwinUnavailable(f"the model server did not take the twin's config: HTTP "
                                  f"{r.status_code} {r.text[:200]}")
        return twin
    except TwinUnavailable:
        raise
    except Exception as e:                              # noqa: BLE001 — the server away, a body unread
        raise TwinUnavailable(f"the model server could not be asked: {type(e).__name__}: {e}") from e


def release(server: str, twin: str) -> None:
    """Stop the twin's backend, giving its memory back. Never raises: a twin
    left loaded is evicted by the reclaimer like any unpinned model."""
    try:
        httpc.post(f"{server.rstrip('/')}/backend/shutdown", json={"model": twin},
                   timeout=CALL_TIMEOUT_S)
    except Exception:                                   # noqa: BLE001
        pass
