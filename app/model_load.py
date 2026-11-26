"""A model's cold start, kept out of the request that found it cold.

THE RULE (operator, 2026-10-09): "Timeouts shall not include cold start."
Found on the node: wikipedia's first search after idle loaded `vega-bge-m3`
and `louise-rerank-bge`; the reranker's 8 s budget (05 §5.6) ran out DURING
THE LOAD, so the list fell back unreranked — while warm the same search
takes 3.1 s in all, the rerank 1.7 s.

So, before a request to a model on a runner that can say what it holds —
LocalAI: `GET <server>/system` → `loaded_models[].id` — a model it does not
list is WARMED first: a minimal request of the same kind, bounded by the
model's LOAD timeout (`load_timeout_s`: the measured profile shipped in
`app/model_loads.json`, else `[models] load_timeout_s`, 07 §7.8). Only then
does the real request go, under its usual budget, which no longer holds the
load.

- ONE WARM PER (server, model) AT A TIME. A second caller that finds the
  same model cold waits for the first one's warm, as bounded as its own
  would have been, and never sends a second.
- `/system` IS ASKED AT MOST EVERY `SYSTEM_TTL_S` per server, so a hot path
  (the worker feeding the encoder) adds a cache lookup per request, not a
  request. LocalAI lists a model only once its load has finished (measured
  2026-10-09, docs/notes/model-profile.md), so a model someone else is
  loading reads as cold, and the warm simply joins that load.
- NOTHING CHANGES WHERE NOTHING IS KNOWN: a runner with no such route (404,
  a body that is not LocalAI's), an unreachable one, a request that names no
  model, a warm that fails other than by taking too long — the request goes
  exactly as it did before this existed, and meets its own fate.
- A LOAD PAST ITS BOUND raises `LoadTimeout` — an `httpx.ConnectTimeout`
  like the dispatch gate's own, so every caller already reads it as "not
  now": a deferral for embedding (never an error on a document), a visible
  fallback with the reason for a search's legs and its reranker.

Each cold start is recorded (`recent()`) and handed to a publisher thread
that writes it to the event log — never by the thread doing the work
(CLAUDE.md: telemetry is published by someone other than the worker).
"""
from __future__ import annotations

import contextvars
import json
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from contextlib import contextmanager
from typing import Callable, Iterator
from urllib.parse import urlsplit

import httpx

from . import httpc

#: how long one `/system` answer is trusted by the worker's hot path
SYSTEM_TTL_S = 2.0
#: how long it is trusted for a request a person waits on. In 2 s another
#: model's load can make LocalAI's reclaimer evict this one, and a check that
#: reads the older answer sends the request cold, its load then paid inside
#: its budget. A guard for the operator's rule of 2026-10-09; it was NOT the
#: cause of the 2026-10-25 rerank misses: the reranker was never evicted that
#: night, and the misses were HTTP 500s (STATUS.md). A burst — a query's
#: variants — shares one read; a single request (rerank, chat, translation)
#: asks with 0.
INTERACTIVE_TTL_S = 0.25
#: how long a server that has no `/system` (or not LocalAI's) is left alone
NO_ROUTE_TTL_S = 300.0
#: the bound on one `/system` read; measured 11 ms on the node
PROBE_TIMEOUT_S = 2.0
#: the measured profile's bounds, shipped with the code
BOUNDS_FILE = Path(__file__).with_name("model_loads.json")


#: What VeGa's wrapper answers a load it states no run for (vega_wrapper
#: `_refuse_load`), carried inside the runner's error: LocalAI answers the
#: load that failed with HTTP 500 "failed to load model … could not load
#: model (no success): VeGa: no run can be stated: <why>", and every request
#: after it with a 503 "load is in cooldown" that no longer says why
#: (München, 2026-11-02, reproduced). Waiting does not heal it: the model's
#: tree or YAML must change (CLAUDE.md invariant 12).
VEGA_NO_RUN = "VeGa: no run can be stated"


class LoadRefused(ConnectionError):
    """The platform refused to load the model: VeGa states no run for it.
    A ConnectionError, so the worker and a search read it as "not now" (no
    document is touched, the leg is withheld with this reason); a command
    exits 2, refused (app/louise.py), and `deploy/supervise-pass.sh` does
    not run it again. Seen only on the request that made the load — the
    warm — so it is raised there, for the warm's caller and every caller
    that waited on it, instead of the cooldown the real request would meet."""

    def __init__(self, message: str, *, model: str) -> None:
        super().__init__(message)
        self.model = model


def refused_load(model: str, response) -> LoadRefused | None:
    """The refusal in a runner's answer, or None."""
    status = getattr(response, "status_code", None)
    body = getattr(response, "text", "") or ""
    if status is None or status < 500 or VEGA_NO_RUN not in body:
        return None
    at = body.index(VEGA_NO_RUN)
    why = body[at:at + 400].split('","type"')[0].strip()
    return LoadRefused(f"{model}: {why} — the model platform refuses to state a run for it; "
                       f"nothing is written, and waiting does not change it", model=model)


class LoadTimeout(httpx.ConnectTimeout):
    """The model was still loading when its load bound ran out. A deferral
    for every caller, as `dispatch.GateTimeout` is; the message is the
    reason a fallback states."""

    def __init__(self, message: str, *, model: str, bound_s: float) -> None:
        super().__init__(message)
        self.model = model
        self.bound_s = bound_s


@dataclass
class ColdStart:
    """One warm: what was loaded, for whom, and how long it took."""
    time: float
    server: str
    model: str
    caller: str
    seconds: float
    bound_s: float
    outcome: str            # "loaded" · "timeout" · "failed: …" · a parked model's: "returned" · "not returned: …"


@dataclass
class _Flight:
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None
    seconds: float | None = None


_lock = threading.Lock()
_systems: dict[str, tuple[float, frozenset[str] | None, float]] = {}   # server -> (at, ids, ttl)
_pids: dict[str, dict[str, int]] = {}                                    # server -> {model: backend pid}
#: (server, model) -> the most bytes `/system` gave as the model's `size_vram`
#: (the driver's per-process count, as app/gpu_models.py reads it), KEPT after
#: the model goes: what the resident keeper's room check needs is the size of
#: a model that is not loaded (app/resident.py). The largest seen, because the
#: first reading after a load comes before its compute buffers grow.
_sizes: dict[tuple[str, str], int] = {}
#: server -> {model: bytes} as the LAST `/system` answer gave them (0: none on
#: the card): the resident budget's margin is the card's count minus their sum
#: (`held_sizes`, app/resident.py)
_held: dict[str, dict[str, int]] = {}
#: server -> {model: the backend's cpu_percent} as the LAST `/system` answer gave
#: it: whether a model is serving now, read from the same answer as its size
#: (app/model_evict.py, which leaves a busy one alone)
_cpu: dict[str, dict[str, float]] = {}
#: (server, model) -> the FEWEST bytes `/system` gave as its `size_vram`: the
#: model AT REST, as its load left it, before a request grows its buffers —
#: what the resident budget counts (app/resident.py, 2026-11-08)
_rest: dict[tuple[str, str], int] = {}
#: (server, model) -> (pid, bytes, first, last): the backend process `/system`
#: last showed holding the model, the bytes it held, and the first and the
#: last reading that showed THAT process holding THOSE bytes (`steady_vram`).
#: Whether it is at rest then is the resident keeper's to say, with the
#: node's record of the model's last request (app/resident.py `AT_REST_S`).
#: Not this row's `cpu_percent`: behind VeGa it is the wrapper's process,
#: 0.07–2.5 % whatever the engine does (scheduling.md, Perugia 2026-10-19)
_anchor: dict[tuple[str, str], tuple[int | None, int, float, float]] = {}
#: AS ITS LOAD LEFT IT (2026-11-25). (server, model) -> the backend pid of a
#: load THIS PROCESS SAW BEGIN — the reading before showed the model absent, or
#: under another pid — and (server, model) -> (pid, bytes): what that process
#: held the first time it was STEADY (the same bytes for LOAD_REST_S) after it.
#: The one reading of a model that keeps its context which is known to be the
#: model whole and at rest: the fewest reading (`_rest`) is not — on the node
#: in query mode the 35B, 21,449 MiB loaded, was read at 13,275 while the
#: driver held a slice of it in host memory (gtt 13.3 GiB), over half its
#: largest and so "at rest" by REST_FLOOR, and the keeper counted 8 GiB the
#: card did not have. A process that did not see the load says nothing here.
_load_seen: dict[tuple[str, str], int] = {}
_load_rest: dict[tuple[str, str], tuple[int, int]] = {}
#: how long a new load's bytes must stay the same to be "as its load left it":
#: past the parts of a load (weights 0.2–0.8 s, the warm-up 1.5–3.5 s; the
#: 35B's 99 s load changes at every reading), = app/resident.py AT_REST_S
LOAD_REST_S = 10.0
#: the clock the readings are timed on (a test's own)
_rest_clock = time.monotonic
#: = app/resident.py REST_FLOOR (that module's reasons; kept here, where the
#: readings are taken, so neither module imports the other at load)
REST_FLOOR = 0.5
#: (server, model) listed loaded at least once: a model that holds no VRAM
#: (one on the CPU) has no `_sizes` entry, and costs the card nothing
_seen: set[tuple[str, str]] = set()
#: (server, model) -> whether its load options carry llama.cpp's
#: `context_on_demand`, as `/system` LISTS them (`load_options`, our P1): such
#: a model holds its weights and creates its context per request
#: (docs/notes/idle-context-brief.md), so it has TWO sizes on the card, idle
#: and running, and takes a request's `keep_context_ms` (`context_on_demand`;
#: app/rage/keep.py, app/resident.py). Only a row that lists its options says
#: anything: the node's LocalAI (v4.12.1, upstream) lists none (München,
#: 2026-11-12), and a row without them is no "no"
_on_demand: dict[tuple[str, str], bool] = {}
#: the same, from the STATEMENT of the run its answers named: a validated
#: run's `engine.options` (VeGa's wrapper states the `ModelOptions` LocalAI
#: loaded it with; immutable per run id), noted where a write or a query is
#: checked (`note_run_options`, app/run_check.py). Where `/system` lists the
#: options too, `/system` decides: it is read now, the note is the last answer
_run_on_demand: dict[tuple[str, str], bool] = {}
#: the option, as a model's YAML `options:` names it or as the raw
#: llama-server flag LocalAI's backend passes through
ON_DEMAND_OPTION = "context_on_demand"
#: PARKING (docs/notes/parking-step-2.md §3.4, the server's 0027; 2026-11-25).
#: server -> the models whose `/system` row says `"parks": true` — its file
#: allows parking it: what the evictor reads to ask for `/backend/park`
#: rather than `/backend/shutdown`. Absent on a server without the patch, and
#: for a model that does not park: nothing here, and nothing changes
_parks: dict[str, frozenset[str]] = {}
#: server -> {model: (size_host, size_vram_needed)} of the models its last
#: `/system` answer listed `"state": "parked"`: a live process whose WEIGHTS
#: ARE IN HOST MEMORY. It is listed among the loaded models and IS NOT ON THE
#: CARD: its `size_vram` is what its process still holds there (321 MiB for
#: bge-m3), and coming back commits `size_vram_needed` again — a load of that
#: size that costs a copy, for which room is made as for a load
#: (`ensure_loaded`). Nothing is learnt of a model's size from such a row
_parked: dict[str, dict[str, tuple[int, int]]] = {}
#: A MODEL LISTED PARKED IS PARKED, FOR LOUISE — even in the one case where
#: LocalAI's record is stale (parking-step-2.md §7: a request that took its
#: client before a park and reached the backend after it is served by the
#: engine's own return, and the weights are on the card under a record that
#: says parked). Nothing here tells that case from a reading: a first cut did
#: ("nearer what the model holds loaded than what a park leaves"), and a
#: model whose at-rest size had been learnt from one partly paged reading was
#: taken for on the card while truly parked, which the keeper then un-parked
#: at every beat (the review of 2026-11-25, reproduced). The record heals by
#: the server's own design at the model's next request — its `LoadModel` is
#: answered "already loaded" — and until then Louise makes room for a return
#: that costs nothing, which errs on the side of room.
_probe_locks: dict[str, threading.Lock] = {}
_flights: dict[tuple[str, str], _Flight] = {}
_recent: deque[ColdStart] = deque(maxlen=100)
_bounds: dict[str, float] | None = None


def reset() -> None:
    """Forget every cached answer, warm and record (tests)."""
    global _bounds
    with _lock:
        _systems.clear()
        _pids.clear()
        _sizes.clear()
        _held.clear()
        _cpu.clear()
        _backends.clear()
        _rest.clear()
        _anchor.clear()
        _load_seen.clear()
        _load_rest.clear()
        _seen.clear()
        _on_demand.clear()
        _run_on_demand.clear()
        _parks.clear()
        _parked.clear()
        _probe_locks.clear()
        _flights.clear()
        _recent.clear()
        _behind.clear()
        _bounds = None


def server_of(url: str) -> str:
    """The runner's root: `/system` is LocalAI's at the server, whatever path
    the endpoint has (`…/v1/rerank`)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else url.rstrip("/")


def _fetch_system(server: str) -> frozenset[str] | None:
    """The ids LocalAI lists as loaded, or None when the server cannot say;
    each one's backend pid noted on the way (`backend_pid`)."""
    try:
        r = httpc.get(f"{server}/system", timeout=PROBE_TIMEOUT_S)
    except Exception:   # noqa: BLE001 — unreachable: nothing is known
        return None
    if r.status_code >= 500:
        return None                    # starting, failing: unknown for now, asked again soon
    if r.status_code != 200:
        raise _NoRoute()
    try:
        rows = r.json().get("loaded_models")
    except Exception:   # noqa: BLE001
        raise _NoRoute() from None
    if not isinstance(rows, list):
        raise _NoRoute()
    held = [m for m in rows if isinstance(m, dict) and isinstance(m.get("id"), str)]
    pids, sizes, now, cpu = {}, {}, {}, {}
    # the backend each loaded model runs on, where the listing names it (our P1)
    backends = {m["id"]: m["backend"] for m in held if isinstance(m.get("backend"), str) and m["backend"]}
    # only a row that LISTS its options says whether it was loaded on demand
    listed = {m["id"]: _options_on_demand(m["load_options"]) for m in held
              if isinstance(m.get("load_options"), dict)}
    parks = frozenset(m["id"] for m in held if m.get("parks") is True)
    parked = {}
    for m in held:
        pid = (m.get("process") or {}).get("pid") if isinstance(m.get("process"), dict) else None
        if isinstance(pid, int):
            pids[m["id"]] = pid
        proc = m.get("process") if isinstance(m.get("process"), dict) else {}
        try:
            cpu[m["id"]] = float(proc.get("cpu_percent") or 0.0)
        except (TypeError, ValueError):
            cpu[m["id"]] = 0.0
        size = m.get("size_vram")
        ok = isinstance(size, int) and not isinstance(size, bool) and size > 0
        now[m["id"]] = size if ok else 0
        away = _parked_row(m)
        if away is not None:
            parked[m["id"]] = away          # NOT on the card: no size is learnt from what is left of it
            continue
        if ok:
            sizes[(server, m["id"])] = size
    with _lock:
        before = _pids.get(server)          # None: this process's first reading of the server
        _pids[server] = pids
        _held[server] = now
        _cpu[server] = cpu
        _backends[server] = backends
        _parks[server] = parks
        _parked[server] = parked
        for m in parked:
            # its present state on the card begins again when it is back: what
            # a park leaves is never "the model at rest" (`steady_vram`)
            _anchor.pop((server, m), None)
        _seen.update((server, m) for m in now)
        for m in now:
            if m in listed:
                _on_demand[(server, m)] = listed[m]
            else:
                _on_demand.pop((server, m), None)     # this listing cannot say
        t = _rest_clock()
        for k, v in sizes.items():
            _sizes[k] = max(v, _sizes.get(k, 0))
            pid, a = pids.get(k[1]), _anchor.get(k)
            # another process, or bytes that moved: its present state begins here
            _anchor[k] = (pid, v, t, t) if a is None or a[0] != pid or a[1] != v else (pid, v, a[2], t)
            # a reading under REST_FLOOR of the largest: the model partly off
            # the card (app/resident.py REST_FLOOR, 2026-11-09), not at rest
            floor = REST_FLOOR * _sizes[k]
            if _rest.get(k, floor) < floor:
                del _rest[k]
            if v >= floor:
                _rest[k] = min(v, _rest.get(k, v))
            # A LOAD THIS PROCESS SAW BEGIN, and what it left (`rest_of_load`)
            if isinstance(pid, int):
                if before is not None and before.get(k[1]) != pid and _load_seen.get(k) != pid:
                    _load_seen[k] = pid
                    _load_rest.pop(k, None)
                a = _anchor[k]
                if (_load_seen.get(k) == pid and _load_rest.get(k, (None, 0))[0] != pid
                        and a[3] - a[2] >= LOAD_REST_S):
                    _load_rest[k] = (pid, v)
    return frozenset(m["id"] for m in held)


def no_system_route(server: str) -> bool:
    """The last `/system` answer of `server` said it has no such route (a
    runner that is not LocalAI): it lists no models and no options."""
    with _lock:
        got = _systems.get(server_of(server))
    return got is not None and got[2] == NO_ROUTE_TTL_S


def options_on_demand(load_options) -> bool:
    """`_options_on_demand` for a stated run's engine options (VeGa's
    `options`, the same proto names as `/system`'s `load_options`)."""
    return _options_on_demand(load_options)


def _options_on_demand(load_options) -> bool:
    """Whether a `/system` row's `load_options` (the `ModelOptions` LocalAI
    sent with LoadModel, proto names) switch `context_on_demand` on: an entry
    of `Options` — `context_on_demand`, `context_on_demand:true` — or the raw
    flag `--context-on-demand`. A runner that lists no options says no."""
    opts = load_options.get("Options") if isinstance(load_options, dict) else None
    if not isinstance(opts, list):
        return False
    on = False
    for o in opts:
        if not isinstance(o, str):
            continue
        name, _, value = o.strip().partition(":")
        if name.lstrip("-").replace("-", "_") == ON_DEMAND_OPTION:
            on = value.strip().lower() not in ("false", "0", "no", "off", "disabled")
    return on


def context_on_demand(url: str, model: str | None) -> bool:
    """Whether `model` at `url` was LOADED with `context_on_demand`: by the
    last `/system` answer that listed it WITH its options, else by the
    statement of the last validated run its answers named
    (`note_run_options`); False when neither says — a request then carries
    no keep (the engine keeps the context, as it always did), and its size is
    read as any model's."""
    if not model:
        return False
    key = (server_of(url), model)
    with _lock:
        listed = _on_demand.get(key)
        return listed if listed is not None else _run_on_demand.get(key, False)


def listed_on_demand(url: str, model: str | None) -> bool | None:
    """`/system`'s own word on `model`: True or False where its last listing
    gave the model's load options, None where it gave none (a LocalAI without
    P1, the node's) or never listed it. The gate reads this and not
    `context_on_demand`: a run noted earlier is not the run being gated."""
    if not model:
        return None
    with _lock:
        return _on_demand.get((server_of(url), model))


def note_run_options(url: str, model: str | None, options) -> None:
    """A validated run answered for `model` at `url`, and its statement's
    `engine.options` are `options` (VeGa's: LoadModel's `ModelOptions`, proto
    names): what `context_on_demand` says where `/system` lists no options.
    A statement that carries no options (an engine stated without them) says
    nothing, and leaves what was noted."""
    if not model or not isinstance(options, dict):
        return
    with _lock:
        _run_on_demand[(server_of(url), model)] = _options_on_demand(options)


def size_vram(server: str, model: str) -> int | None:
    """The bytes `model` held on the card the last times `/system` listed it
    (`_sizes`), or None when this process never saw it loaded."""
    with _lock:
        return _sizes.get((server_of(server), model))


def _parked_row(row: dict) -> tuple[int, int] | None:
    """(size_host, size_vram_needed) of a `/system` row that says the model
    is PARKED — `"state": "parked"` with its `"parked"` object — else None: a
    row that says nothing of it is a loaded model, as on a server without the
    patch."""
    if row.get("state") != "parked":
        return None
    got = row.get("parked") if isinstance(row.get("parked"), dict) else {}

    def count(v) -> int:
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0
    return count(got.get("size_host")), count(got.get("size_vram_needed"))


def parks(server: str) -> frozenset[str]:
    """The models `server`'s LAST `/system` answer said can be parked
    (`"parks": true`); no request. Empty on a server without the patch."""
    with _lock:
        return _parks.get(server_of(server), frozenset())


def parked(server: str) -> dict[str, tuple[int, int]]:
    """`{model: (size_host, size_vram_needed)}` of the models `server`'s LAST
    `/system` answer listed parked and that are not on the card (`_parked`);
    no request — read right after `held_sizes`, it is the same answer."""
    with _lock:
        return dict(_parked.get(server_of(server), {}))


def steady_vram(server: str, model: str) -> tuple[int, float] | None:
    """(bytes, seconds): what `model`'s backend process held at the last
    `/system` reading, and for how long the readings have shown the SAME
    process holding the SAME bytes, first such reading to last. A load, an
    unload and a context made or freed each begin it again. None when this
    process never read the model with a size."""
    with _lock:
        got = _anchor.get((server_of(server), model))
        return None if got is None else (got[1], got[3] - got[2])


def rest_vram(server: str, model: str) -> int | None:
    """The fewest bytes `model` held on the card in any `/system` reading of
    this process — at rest, as its load left it (`_rest`); None never seen
    holding any."""
    with _lock:
        return _rest.get((server_of(server), model))


def rest_of_load(server: str, model: str) -> int | None:
    """The bytes `model` held on the card AS ITS LOAD LEFT IT: the first
    steady reading of a load this process saw begin, while that same backend
    process still holds the model. None when this process did not see the
    load begin (it started with the model loaded), the load has not been
    steady yet, or the model has been loaded again since. Never lowered by a
    later reading: a smaller one from the same process is the model partly in
    host memory, not the model at rest."""
    with _lock:
        key = (server_of(server), model)
        got = _load_rest.get(key)
        if got is None or _pids.get(key[0], {}).get(model) != got[0]:
            return None
        return got[1]


def seen_loaded(server: str, model: str) -> bool:
    """Whether `/system` listed `model` loaded at least once in this
    process — with `size_vram` None, a model that holds no VRAM."""
    with _lock:
        return (server_of(server), model) in _seen


def held_sizes(server: str, max_age_s: float | None = None) -> dict[str, tuple[int | None, int]] | None:
    """`{model: (backend pid, bytes on the card)}` of what `server` holds,
    from an answer at most `max_age_s` old (`loaded_models`); None when it
    cannot say, or its answer carried no per-model sizes (a runner that is
    not LocalAI's). The resident budget's margin reads it (app/resident.py)."""
    server = server_of(server)
    ids = loaded_models(server, max_age_s)
    if ids is None:
        return None
    with _lock:
        now = _held.get(server)
        if now is None:
            return None
        pids = _pids.get(server, {})
        return {m: (pids.get(m), now.get(m, 0)) for m in ids}


def held_cpu(server: str) -> dict[str, float]:
    """`{model: cpu_percent}` of the backends as the LAST `/system` answer
    gave them — no request: read right after `held_sizes`, it is the same
    answer (app/model_evict.py)."""
    with _lock:
        return dict(_cpu.get(server_of(server), {}))


class _NoRoute(Exception):
    """The server answered, and not as LocalAI's `/system` does."""


def loaded_models(server: str, max_age_s: float | None = None) -> frozenset[str] | None:
    """What `server` holds, from an answer at most `max_age_s` old (default
    `SYSTEM_TTL_S`; a runner with no `/system` is not asked again for
    `NO_ROUTE_TTL_S` whatever is asked); None when it cannot say. One read
    per server at a time: a caller arriving during a read takes its answer."""
    def fresh_enough(got) -> bool:
        age = time.monotonic() - got[0]
        limit = got[2] if got[2] == NO_ROUTE_TTL_S or max_age_s is None else min(got[2], max_age_s)
        return age < limit

    with _lock:
        got = _systems.get(server)
        if got is not None and fresh_enough(got):
            return got[1]
        lock = _probe_locks.setdefault(server, threading.Lock())
    with lock:
        with _lock:
            got = _systems.get(server)
            if got is not None and fresh_enough(got):
                return got[1]
        try:
            ids, ttl = _fetch_system(server), SYSTEM_TTL_S
        except _NoRoute:
            ids, ttl = None, NO_ROUTE_TTL_S
        with _lock:
            _systems[server] = (time.monotonic(), ids, ttl)
        return ids


#: server -> {model: its backend's name} as the LAST `/system` answer gave them
_backends: dict[str, dict[str, str]] = {}


def backend_of(url: str, model: str | None, max_age_s: float | None = None) -> str | None:
    """The BACKEND `model` is loaded on at `url`'s runner, as `/system`
    names it (`loaded_models[].backend`: a Louise engine's package name
    carries the engine's id, e.g. `louise-sycl-f16-llama-cpp-d531d5d804f1-…`);
    None when the model is not loaded or the runner does not say. Only a
    LOADED model has one: the listing is of what runs."""
    if not model:
        return None
    server = server_of(url)
    ids = loaded_models(server, max_age_s)
    if not ids or model not in ids:
        return None
    with _lock:
        return _backends.get(server, {}).get(model)


def backend_pid(url: str, model: str | None, max_age_s: float = INTERACTIVE_TTL_S) -> int | None:
    """The pid of the backend that serves `model` on `url`'s runner, from an
    answer at most `max_age_s` old; None when it is not loaded or the runner
    cannot say. A different pid after a request than before it means the
    model was loaded again in between — a cold start that request paid."""
    if not model:
        return None
    server = server_of(url)
    ids = loaded_models(server, max_age_s)
    if not ids or model not in ids:
        return None
    with _lock:
        return _pids.get(server, {}).get(model)


def reloaded_since(url: str, model: str, pid: int | None) -> bool:
    """Was `model`, served by backend `pid` before a request, evicted since —
    gone from the runner now, or served by another backend? Asked fresh;
    False when nothing is known (no pid before, or a runner that cannot
    say)."""
    if pid is None or not model:
        return False
    server = server_of(url)
    ids = loaded_models(server, 0.0)
    if ids is None:
        return False
    if model not in ids:
        return True                       # evicted, and loading again or not
    with _lock:
        now = _pids.get(server, {}).get(model)
    return now is not None and now != pid


def note_cold_call(url: str, model: str, caller: str, seconds: float) -> None:
    """Record a cold start a request paid inside its own budget (the model
    was evicted between the check and the request): published like a warm's,
    so the event log says what the fallback's reason says."""
    _record(ColdStart(time.time(), server_of(url), model, caller, seconds, load_timeout_s(model),
                      "failed: evicted before the request, loaded inside its budget"))


def speaks_localai(server: str) -> bool | None:
    """Is `server` LocalAI? True when its `/system` answers as LocalAI's,
    False when it answers as something else (llama-server, an OpenAI-style
    service), None when it cannot be asked now. From `loaded_models`' cache."""
    if loaded_models(server) is not None:
        return True
    with _lock:
        got = _systems.get(server)
    return False if got is not None and got[2] == NO_ROUTE_TTL_S else None


#: the bound on one unload request
UNLOAD_TIMEOUT_S = 30.0


def activity(server: str) -> dict[str, float] | None:
    """`{model: cpu_percent}` of what a LocalAI runner holds, read fresh from
    `/system` (its `loaded_models[].process.cpu_percent`); None when the
    runner cannot say. A model whose backend burns no CPU is not serving."""
    try:
        r = httpc.get(f"{server}/system", timeout=PROBE_TIMEOUT_S)
        rows = r.json().get("loaded_models") if r.status_code == 200 else None
    except Exception:   # noqa: BLE001
        return None
    if not isinstance(rows, list):
        return None
    out: dict[str, float] = {}
    for m in rows:
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            proc = m.get("process") or {}
            try:
                out[m["id"]] = float(proc.get("cpu_percent") or 0.0)
            except (TypeError, ValueError):
                out[m["id"]] = 0.0
    return out


def unload(url: str, model: str | None) -> bool:
    """Ask a LocalAI runner to stop `model`'s backend (`POST <server>/backend/
    shutdown {"model": …}`), freeing its memory; it loads again on the next
    request. Only where the runner lists the model as loaded — a runner that
    cannot say (not LocalAI, unreachable) is left alone. True when it was
    stopped. Never raises (operator, 2026-10-14: an embedder whose collection
    does not embed everything must not hold the card)."""
    if not url or not model:
        return False
    server = server_of(url)
    ids = loaded_models(server)
    if ids is None or model not in ids:
        return False
    try:
        r = httpc.post(f"{server}/backend/shutdown", json={"model": model},
                       timeout=UNLOAD_TIMEOUT_S)
    except Exception:   # noqa: BLE001 — the runner keeps it; nothing breaks
        return False
    with _lock:
        _systems.pop(server, None)           # what it holds has changed
    if r.status_code == 200:
        _note_own(server, model)
    return r.status_code == 200


def _note_own(server: str, model: str) -> None:
    """Louise stopped or parked this model itself: said where the keeper's
    process reads it, so the stop is not taken for an eviction of the
    reclaimer's (app/own_unloads.py). Never raises."""
    try:
        from app import own_unloads
        own_unloads.note(server, model)
    except Exception:   # noqa: BLE001
        pass


#: what `park` answers with
PARKED, PARK_BUSY, PARK_STOP, PARK_GONE = "parked", "busy", "stop", "gone"
#: LocalAI follows a park to its end and gives a backend 60 s to answer it
#: (the server's 0027); ours waits a little longer than the server does
PARK_TIMEOUT_S = 75.0


def park(url: str, model: str | None) -> tuple[str, int]:
    """Ask a LocalAI runner to PARK `model` (`POST <server>/backend/park
    {"model": …}`, the server's 0027): its weights leave the card for host
    memory, its process stays, and the next request to it brings them back
    in the time of a copy. What came of it, and the bytes that left the card:

      PARKED     200 — `parked.size_vram_needed` bytes are off the card;
      PARK_BUSY  409 `model_busy` — it works, or a load, a return or a stop
                 of it is running: it is NOT stopped, the caller takes another;
      PARK_STOP  412 `cannot_park` / `parking_off` / `host_memory_floor`, a
                 server with no such route (404 with no such type), or any
                 answer that is none of these: nothing moved, and the caller
                 stops it instead if room is still wanted (`unload`);
      PARK_GONE  404 `model_not_loaded`, or 500 `model_stopped` (its backend
                 broke the park and LocalAI stopped it): it is off the card,
                 and no stop is sent after it.

    Never raises: a runner that cannot be reached is PARK_STOP, and `unload`
    then meets the same runner."""
    how, moved, _kind = park_asked(url, model)
    return how, moved


def park_asked(url: str, model: str | None) -> tuple[str, int, str]:
    """`park`, with the runner's own word for a refusal as a third value: the
    error's `type` (`model_busy`, `cannot_park`, `parking_off`,
    `host_memory_floor`, `model_not_loaded`, `model_stopped`), "" for a park
    that was made, "unreachable" where the runner did not answer, else
    "HTTP <status>". For a caller that must say WHY a model was not parked
    (the gate's park cycles, app/hold/embedder.py)."""
    if not url or not model:
        return PARK_STOP, 0, "unreachable"
    server = server_of(url)
    try:
        r = httpc.post(f"{server}/backend/park", json={"model": model}, timeout=PARK_TIMEOUT_S)
        status = r.status_code
        try:
            body = r.json()
        except Exception:   # noqa: BLE001 — an answer that is not JSON: only its status speaks
            body = None
    except Exception:   # noqa: BLE001
        return PARK_STOP, 0, "unreachable"
    body = body if isinstance(body, dict) else {}
    kind = (body.get("error") or {}).get("type") if isinstance(body.get("error"), dict) else None
    said = kind if isinstance(kind, str) and kind else f"HTTP {status}"
    with _lock:
        _systems.pop(server, None)           # what it holds may have changed
    if status == 200:
        _note_own(server, model)
        got = body.get("parked") if isinstance(body.get("parked"), dict) else {}
        moved = got.get("size_vram_needed")
        return PARKED, (moved if isinstance(moved, int) and not isinstance(moved, bool) and moved > 0 else 0), ""
    if status == 409 and kind == "model_busy":
        return PARK_BUSY, 0, said
    if status == 500 and kind == "model_stopped":
        _note_own(server, model)             # our park broke its backend and LocalAI stopped it: Louise's doing
        return PARK_GONE, 0, said
    if status == 404 and kind == "model_not_loaded":
        return PARK_GONE, 0, said            # gone before the park: not ours
    return PARK_STOP, 0, said


#: the engine's own counts are read in a moment, or not at all
COUNTS_TIMEOUT_S = 10.0


def park_counts(url: str, model: str | None) -> tuple[int, int, int] | None:
    """THE ENGINE'S OWN COUNT OF ITS PARKS AND RETURNS, since its load:
    (parks, unparks, parked) from `GET <server>/backend/monitor?model=…`
    (`memory.breakdown`; the park engine's `Status`, served by LocalAI) — "of
    weights that LEFT and that came BACK, never of attempts" (the engine's
    patch). A fresh process reads 0, 0, 0: counts that go DOWN are a new
    process, and that is how a return that failed and was answered by a load
    is told from a cycle — the answer is 200, the vectors the same, the run id
    the same (Tensor, parking-step-2.md §10–§11). A read is not a request: it
    brings nothing back and does not restart the idle second. NEVER built on
    `memory.total`, the process's virtual size (38.85 GB loaded and 4.73
    parked for a 0.6 GB model on the node). None where the runner gives no
    such counts: a model that cannot park, a server before the patch, a model
    not loaded, a runner that does not answer."""
    if not url or not model:
        return None
    try:
        r = httpc.get(f"{server_of(url)}/backend/monitor", params={"model": model}, timeout=COUNTS_TIMEOUT_S)
        if r.status_code != 200:
            return None
        got = (r.json().get("memory") or {}).get("breakdown")
    except Exception:   # noqa: BLE001 — unread
        return None
    if not isinstance(got, dict):
        return None
    out = []
    for key in ("parks", "unparks", "parked"):
        v = got.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        out.append(int(v))
    return out[0], out[1], out[2]


def _note_loaded(server: str, model: str) -> None:
    with _lock:
        got = _systems.get(server)
        if got is not None and got[1] is not None:
            _systems[server] = (got[0], got[1] | {model}, got[2])
        # one that came back from a park is on the card from here on; the
        # next reading says what it holds
        _parked.get(server, {}).pop(model, None)


def _profile_bounds() -> dict[str, float]:
    global _bounds
    if _bounds is None:
        try:
            raw = json.loads(BOUNDS_FILE.read_text(encoding="utf-8"))
            _bounds = {str(m): float(v["load_timeout_s"])
                       for m, v in (raw.get("models") or {}).items()
                       if isinstance(v, dict) and v.get("load_timeout_s")}
        except (OSError, ValueError, TypeError, AttributeError):
            _bounds = {}
    return _bounds


def load_timeout_s(model: str) -> float:
    """The model's load bound: its measured one (app/model_loads.json, from
    the node's profile), else the node's `[models] load_timeout_s`."""
    measured = _profile_bounds().get(model)
    if measured:
        return measured
    from . import platform_config
    try:
        return float(platform_config.load().models.load_timeout_s)
    except Exception:   # noqa: BLE001 — a broken platform.toml must not break a request
        return float(platform_config.ModelsConfig().load_timeout_s)


#: set inside `returns_on_request`
_return_on_request: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "model_load_return_on_request", default=False)


@contextmanager
def returns_on_request() -> Iterator[None]:
    """Inside, a PARKED model is not warmed first (`ensure_loaded`): room is
    made for it as for its load and the caller's OWN request brings the
    weights back. For the gate's park cycles (app/hold/embedder.py
    `park_cycles`): the answer held to the bytes must be the first one
    computed on the weights that came back, not the one after a warm whose
    vector nobody read. This thread only."""
    token = _return_on_request.set(True)
    try:
        yield
    finally:
        _return_on_request.reset(token)


def ensure_loaded(url: str, model: str | None, warm: Callable[[float], object], *,
                  caller: str, max_age_s: float | None = None) -> float | None:
    """Warm `model` on the runner behind `url` if the runner says it is not
    loaded. `warm(bound_s)` sends one minimal request bounded by `bound_s`,
    and returns the runner's response where it has one: an answer of 400 or
    more is a load that FAILED — never noted as loaded — and one carrying
    VeGa's refusal raises `LoadRefused` (München, 2026-11-02: a refused load
    was recorded "loaded" and the real request met the cooldown).
    The runner's answer may be `max_age_s` old: by default `SYSTEM_TTL_S`
    for corpus work and `INTERACTIVE_TTL_S` above it; a single request a
    person waits on (a rerank, a chat answer, a translation) passes 0.

    Returns the seconds this call spent waiting for the load, or None when
    nothing was waited for (loaded, or nothing known). Raises `LoadTimeout`
    when the load outlasted its bound."""
    if not model:
        return None
    if max_age_s is None:
        from .rage import dispatch
        max_age_s = INTERACTIVE_TTL_S if dispatch.current()[0] > dispatch.Level.CORPUS else SYSTEM_TTL_S
    server = server_of(url)
    ids = loaded_models(server, max_age_s)
    if ids is None:
        return None
    # A PARKED MODEL IS LISTED AND IS NOT ON THE CARD (`_parked`): its return
    # is a load of `size_vram_needed` that costs a copy, so room is made for
    # it as for a load and `warm` — one minimal request — brings it back
    # (LocalAI sends the `LoadModel` before the request goes). A return the
    # card refuses is HTTP 503 `no_room_on_device`: the model stays parked,
    # the request that follows is refused the same way and deferred
    back = model in ids and model in parked(server)
    if model in ids and not back:
        return None
    if back and _return_on_request.get():
        _make_room(server, model)
        with _lock:
            _systems.pop(server, None)       # the request that follows changes what it says
        return None
    key = (server, model)
    with _lock:
        flight = _flights.get(key)
        owner = flight is None
        if owner:
            flight = _flights[key] = _Flight()
    bound = load_timeout_s(model)
    if not owner:
        t0 = time.monotonic()
        if not flight.done.wait(bound):
            raise LoadTimeout(_reason(model, bound), model=model, bound_s=bound)
        if isinstance(flight.error, (LoadTimeout, LoadRefused)):
            raise flight.error
        return time.monotonic() - t0
    _make_room(server, model)
    t0 = time.monotonic()
    outcome = "failed: interrupted"
    try:
        answer = warm(bound)
        status = getattr(answer, "status_code", None)
        if isinstance(status, int) and status >= 400:
            refusal = refused_load(model, answer)
            if refusal is not None:
                flight.error = refusal
                outcome = f"refused: {str(refusal)[:200]}"
            else:
                # a failed load: the request goes as it did before and meets
                # its own fate (a cooldown is a busy answer, a deferral)
                outcome = f"failed: HTTP {status}"
        else:
            outcome = "loaded"
    except httpx.ConnectTimeout:
        outcome = "failed: the server did not accept a connection"    # down, not loading
    except httpx.TimeoutException:
        flight.error = LoadTimeout(_reason(model, bound), model=model, bound_s=bound)
        outcome = "timeout"
    except Exception as e:   # noqa: BLE001 — the request goes as it did before
        outcome = f"failed: {type(e).__name__}: {str(e)[:200]}"
    finally:
        flight.seconds = time.monotonic() - t0
        if outcome == "loaded":
            # before the flight ends: a caller arriving now finds it loaded,
            # never cold with no warm in flight
            _note_loaded(server, model)
        from app import model_evict
        model_evict.load_ended(server, model, outcome == "loaded")     # a paged load that failed
        with _lock:
            _flights.pop(key, None)
            if back:
                _systems.pop(server, None)   # parked or back: the next caller reads it, never this answer
        flight.done.set()
    if back:
        # not a cold start: the weights came back from host memory, or did not
        outcome = RETURNED if outcome == "loaded" else f"{RETURN_FAILED}{outcome}"
    _record(ColdStart(time.time(), server, model, caller, flight.seconds, bound, outcome))
    if flight.error is not None:
        raise flight.error
    return flight.seconds


def _make_room(server: str, model: str) -> None:
    """LOUISE FREES THE ROOM, NOT THE RECLAIMER (operator, 2026-11-16: the
    models not used in a long time are evicted instead of the ones actively
    used; app/model_evict.py): before this cold load, the loaded models with
    the OLDEST real use are unloaded until it fits under the reclaimer's
    line, so LocalAI's LRU — which Louise's own warms and checks keep
    refreshing — never has to choose. Never raises: whatever it cannot tell,
    the load goes as before."""
    try:
        from . import model_evict
        model_evict.before_load(server, model)
    except Exception:   # noqa: BLE001 — the load goes as before
        pass


#: the loads started behind a caller: (server, model) -> [thread, when it ended]
_behind: dict[tuple[str, str], list] = {}
#: how long after a load behind a caller ended its caller is told to go on:
#: the load was tried, and a model still not loaded is then the request's to
#: meet, bounded, as before — a load that keeps failing must not hold its
#: caller back for ever
BEHIND_GRACE_S = 60.0


def warm_behind(url: str, model: str | None, warm: Callable[[float], object], *,
                caller: str) -> bool:
    """`ensure_loaded` on a DAEMON THREAD, for a caller with other work to do
    while the model loads: True when the model may be asked now (loaded, a
    runner that cannot say, no model, or a load behind this caller that ended
    under `BEHIND_GRACE_S` ago); False while it loads behind the caller — the
    first call that finds it cold starts the load (one per server and model,
    `ensure_loaded`'s own flight), and the caller asks again on its next turn.

    Bounded as the inline warm is: the load by the model's `load_timeout_s`,
    the question by `/system`'s PROBE_TIMEOUT_S (cached SYSTEM_TTL_S)."""
    if not model:
        return True
    server = server_of(url)
    ids = loaded_models(server)
    if ids is None or model in ids:
        return True
    key = (server, model)
    with _lock:
        got = _behind.get(key)
        if got is not None and got[0].is_alive():
            return False
        if got is not None and got[1] is not None and time.monotonic() - got[1] < BEHIND_GRACE_S:
            return True
        thread = threading.Thread(target=_load_behind, args=(key, url, model, warm, caller),
                                  name=f"model-load-{model}", daemon=True)
        _behind[key] = [thread, None]
    thread.start()
    return False


def _load_behind(key: tuple[str, str], url: str, model: str,
                 warm: Callable[[float], object], caller: str) -> None:
    try:
        ensure_loaded(url, model, warm, caller=caller)
    except (LoadTimeout, LoadRefused):
        return                  # recorded as a cold start; the next request meets it again
    finally:
        with _lock:
            got = _behind.get(key)
            if got is not None:
                got[1] = time.monotonic()


def _reason(model: str, bound: float) -> str:
    return f"model loading took longer than {_seconds(bound)} s ({model})"


def _seconds(v: float) -> str:
    return str(int(v)) if math.isclose(v, round(v)) else f"{v:.1f}"


# ---- the record, and who publishes it ---------------------------------------

_queue: queue.Queue[ColdStart] = queue.Queue(maxsize=1000)
_publisher: threading.Thread | None = None


def recent() -> list[ColdStart]:
    with _lock:
        return list(_recent)


#: a parked model's return (`ensure_loaded`), in a ColdStart's `outcome`
RETURNED, RETURN_FAILED = "returned", "not returned: "


def message(c: ColdStart) -> str:
    if c.outcome == RETURNED:
        return (f"parked: {c.model} on {c.server} came back from host memory in {c.seconds:.2f} s "
                f"(for {c.caller})")
    if c.outcome.startswith(RETURN_FAILED):
        return (f"parked: {c.model} on {c.server} did not come back from host memory "
                f"({c.outcome[len(RETURN_FAILED):]}, after {c.seconds:.2f} s, for {c.caller}) — the request "
                f"went as before: refused for room it is deferred and the model stays parked; any other "
                f"failure and LocalAI stops it, and the request pays an ordinary load")
    if c.outcome == "loaded":
        what = f"loaded in {c.seconds:.1f} s"
    elif c.outcome.startswith("failed: evicted"):
        what = (f"was evicted between the check and the request, which then paid the load inside "
                f"its budget and gave up after {c.seconds:.1f} s")
    elif c.outcome == "timeout":
        what = f"still loading after {_seconds(c.bound_s)} s — the caller fell back or deferred"
    else:
        what = f"warm {c.outcome} after {c.seconds:.1f} s — the request went as before"
    return (f"cold start: {c.model} on {c.server} {what} (for {c.caller}; "
            f"load bound {_seconds(c.bound_s)} s)")


def _record(c: ColdStart) -> None:
    global _publisher
    with _lock:
        _recent.append(c)
        if _publisher is None or not _publisher.is_alive():
            _publisher = threading.Thread(target=_publish_forever, name="model-load-log",
                                          daemon=True)
            _publisher.start()
    try:
        _queue.put_nowait(c)
    except queue.Full:
        pass


def _publish_forever() -> None:
    while True:
        c = _queue.get()
        try:
            publish(c)
        except Exception:   # noqa: BLE001 — a log line must never take a thread down
            pass


def publish(c: ColdStart) -> None:
    """Write one cold start to the node's event log: the stdout line, and the
    `events` table of the first collection's manifest, as every node line is
    (db.NODE_STAGES)."""
    from contextlib import closing
    from . import collections as collections_mod, db
    text = message(c)
    level = "info" if c.outcome in ("loaded", RETURNED) else "warn"
    collections = collections_mod.load_all()
    if not collections:
        print(f"{time.strftime('%H:%M:%S')} [node {level}:models] {text}", flush=True)
        return
    with closing(db.connect(collections[0])) as con:
        db.log_event(con, level, "models", text)
