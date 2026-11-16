"""THE CARD'S EVICTION: THE OLDEST REAL USE GOES FIRST, AND LOUISE CHOOSES IT
(operator, 2026-11-16, verbatim: "Evinction policy: some embedders have not
been used in a long time; those ones shall always be evincted instead of the
ones that are actively used, either for embedding of for querying. A crowded
GPU is a good benchmark for an effective evinction policy").

WHY (München, the LocalAI log of 2026-11-16, 12 h): LocalAI's memory
reclaimer (threshold 0.95) evicts the unpinned model with the oldest
`lastUsed` (pkg/model/watchdog.go `evictLRUModel`), and `lastUsed` moves on
ANY request — Louise's own preload warms (app/resident.py) and the worker's
run check of sixteen texts per encoder at every start included. It evicted
louise-translate-madlad 9 times out of 10, each a few minutes after its last
real use, while models nobody had asked for in hours stayed on the card
(omnivoice-cpp 1.7 GB, the rerankers, vega-medcpt-*, vega-qwen3-embedding-4b
2.9 GB, vega-dar-legal-it 2.3 GB); the keeper then loaded MADLAD back, and
the next load evicted it again — a thrash.

THE RULE, HERE. Before Louise causes a load that needs room on the card,
Louise frees the room itself, so the reclaimer never has to choose:
- `make_room` reads the card (the GPU probe, as app/resident.py `room` does),
  the reclaimer's line (LocalAI's `memory_reclaimer_threshold`), and ONE
  `/system` (every loaded model's bytes and its backend's CPU); the record of
  REAL use (app/model_use.py: node_state `encoder_use`, written by work only,
  never by a warm or a check); and Louise's pin record (app/model_pins.py
  `recorded`, a local file).
- The candidates are the models loaded on that runner that Louise knows —
  in the resident set (`resident.current`) or with a use row — and are NOT
  pinned (a gate's, a pass's, an embed batch's: never touched), not busy
  (cpu over BUSY_CPU_PERCENT), not on another card (unloading them frees
  nothing here), not a gate's twin, not the model being loaded, not in
  `protect`. A model Louise does not know (the operator's 35B loaded by
  hand) is never touched: it is left to the reclaimer, as before.
- They go OLDEST REAL USE FIRST — never used counts as the oldest; between
  equals the larger first, so fewer go — until the load fits under the
  line. Only those whose last real use is OLDER THAN THE HORIZON may go:
  for work (a document embed, a search, an answer) the horizon is now; for
  Louise's own loads — the keeper's preload, a run check's, a gate's — it
  is the loaded model's own last real use, so a preload never evicts a model
  used more recently than the model it loads (that ends the MADLAD thrash),
  and a model never used preloads only into free room.
- Where even all of them would not make the room, NOTHING is unloaded
  (their reloads would be paid for nothing), and the caller is told.
- Each victim's pin is read once from LocalAI before its unload (a pin
  LocalAI holds that Louise's record does not — the operator's): one call
  per model unloaded, never one per model considered.
- After the unloads the driver's count is waited for, ROOM_WAIT_S at most
  (a load into memory not yet given back would overcommit the card, which
  xe does silently — as app/gate_twin.py `make_room` waits).

Nothing here unloads for being idle: only a load that needs the room asks
(operator, 2026-11-08: "nothing is evicted only because it's idle, only if
space is claimed"). The node's mode wins: paused or seed-only, nothing is
unloaded (app/resident.py `preloads`).

THE STANDARD: LRU eviction by TRUE access — the least-recently-used policy of
the CLOCK/LRU page-replacement family (Corbató 1968; Linux's active/inactive
lists), applied by the client because the runner's own LRU counts accesses
that are not work. THE STANDARD ALTERNATIVE, written down: LocalAI's
reclaimer made to ignore a request flagged as a warm or a probe (a header
its watchdog reads before `Mark`ing the model used), so its own LRU is the
true one — an upstream change (docs/notes/localai-bugs.md); it would pay
once programs other than Louise load models on the card, since only
LocalAI sees every load. Then this module could go.
"""
from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

MIB = 1 << 20
#: a backend whose process uses more CPU than this (percent) reads busy
#: (= app/question_card.py BUSY_CPU_PERCENT)
BUSY_CPU_PERCENT = 5.0
#: after the unloads, the driver's count is waited for this long at most …
ROOM_WAIT_S = 10.0
#: … read this often
ROOM_POLL_S = 0.5

#: the keeper made the room for this load itself: the cold-load hook does not
_room_made: contextvars.ContextVar[bool] = contextvars.ContextVar("model_evict_room_made", default=False)


@contextmanager
def room_made() -> Iterator[None]:
    token = _room_made.set(True)
    try:
        yield
    finally:
        _room_made.reset(token)


@dataclass(frozen=True)
class Loaded:
    """One model on the card, as one `/system` answer and the use record give it."""
    model: str
    size: int                    # bytes on the card now: what unloading it gives back
    last_used: float | None      # epoch s of its last real use; None never used
    busy: bool = False


@dataclass(frozen=True)
class Outcome:
    """What `make_room` did. `fits` None: it could not be told (the load goes
    as before)."""
    fits: bool | None
    need: int | None = None
    free: int | None = None
    unloaded: tuple[str, ...] = ()
    why: str = ""


def eligible(loaded: Iterable[Loaded], horizon: float | None) -> list[Loaded]:
    """THE ORDER: the models that may go, oldest real use first (never used
    first of all), the larger first between equals. Only a model whose last
    real use is older than `horizon` (None: no model at all — the model to
    load was never used, so nothing used gives way to it, and a never-used
    one is no older than it); never a busy one. Pure."""
    if horizon is None:
        return []
    out = [v for v in loaded if not v.busy and v.size > 0
           and (v.last_used is None or v.last_used < horizon)]
    return sorted(out, key=lambda v: (v.last_used if v.last_used is not None else float("-inf"),
                                      -v.size, v.model))


def victims(loaded: Iterable[Loaded], *, need: int, free: int, horizon: float | None) -> list[Loaded] | None:
    """The fewest of `eligible`, in its order, that make `need` fit in
    `free`: [] when it fits already, None when even all of them would not
    make it. Pure."""
    if need <= free:
        return []
    out, freed = [], 0
    for v in eligible(loaded, horizon):
        out.append(v)
        freed += v.size
        if free + freed >= need:
            return out
    return None


def _ago(now: float, t: float | None) -> str:
    if t is None:
        return "never used"
    s = max(0.0, now - t)
    return (f"last used {s / 60:.0f} min ago" if s < 3600 else
            f"last used {s / 3600:.1f} h ago" if s < 2 * 86400 else f"last used {s / 86400:.0f} days ago")


def _say(level: str, text: str) -> None:
    from app import resident
    try:
        resident._say(level, text)
    except Exception:   # noqa: BLE001 — a log line never fails a load
        pass


def need_of(server: str, model: str) -> int | None:
    """The most bytes the model was seen holding on the card — this
    process's `/system` readings and the resident budget's file — or None
    never seen; 0 when its backend is on another card (it costs this one
    nothing)."""
    from app import model_load, resident
    mem = resident._memory_now()
    key = (model_load.server_of(server), model)
    if key in mem.elsewhere:
        return 0
    got = [v for v in (model_load.size_vram(*key), mem.sizes.get(key)) if v]
    return max(got) if got else None


def make_room(server: str, *, need: int, horizon: float | None, for_what: str,
              protect: Iterable[tuple[str, str]] = (), exclude: Iterable[str] = (),
              sleep: Callable[[float], None] = time.sleep,
              clock: Callable[[], float] = time.monotonic) -> Outcome:
    """Room for `need` bytes on `server`'s card under the reclaimer's line,
    made by unloading the loaded models with the OLDEST real use first (the
    module's docstring). `horizon`: only models last used before it may go.
    `protect`: (server, model) that stay whatever their use (a question's own
    models). `exclude`: model ids of this server never considered (the model
    being loaded). Never raises."""
    from app import gate_twin, model_load, model_pins, model_use, resident
    server = model_load.server_of(server)
    try:
        card = resident._card()
        if card is None:
            return Outcome(None, need, why="the card cannot be read")
        total, used = card
        t = resident._threshold(server)
        free = int(t * total) - used
        if need <= free:
            return Outcome(True, need, free)
        # THE one `/system` read — or the cold load's own, a moment ago
        held = model_load.held_sizes(server, max_age_s=model_load.INTERACTIVE_TTL_S)
        if held is None:
            return Outcome(None, need, free, why=f"{server}/system gives no per-model sizes")
        cpu = model_load.held_cpu(server)
        pins = model_pins.recorded()
        if pins is None:
            return Outcome(None, need, free, why="the pin record cannot be read")
        use = model_use.last_used()
        known = {r.key for r in resident.current()} | set(use)
        mem = resident._memory_now()
        keep = {tuple(k) for k in protect} | {(server, m) for m in exclude}
        loaded = [Loaded(m, int(size or 0), use.get((server, m)), cpu.get(m, 0.0) > BUSY_CPU_PERCENT)
                  for m, (_pid, size) in held.items()
                  if (server, m) in known and (server, m) not in keep and (server, m) not in pins
                  and (server, m) not in mem.elsewhere and not gate_twin.is_twin(m)]
    except Exception as e:   # noqa: BLE001 — nothing known: the load goes as before
        return Outcome(None, need, why=f"{type(e).__name__}: {str(e)[:200]}")
    now = time.time()
    order = eligible(loaded, horizon)
    if free + sum(v.size for v in order) < need:
        newer = [v for v in loaded if v not in order and v.size > 0]
        return Outcome(False, need, free, why=(
            f"{for_what} needs {need // MIB:,} MiB, {max(0, free) // MIB:,} MiB free under the "
            f"{t:.2f} line, and the models that may give way to it hold "
            f"{sum(v.size for v in order) // MIB:,} MiB"
            + (f" — {', '.join(v.model for v in newer)} were used more recently, are busy, or hold none"
               if newer else "")))
    gone: list[Loaded] = []
    freed = 0
    for v in order:
        if free + freed >= need:
            break
        try:
            if model_pins.read_pinned(server, v.model):
                continue                         # a pin Louise's record does not hold: the operator's
            if model_load.unload(server, v.model):
                gone.append(v)
                freed += v.size
        except Exception:   # noqa: BLE001 — left on the card; the next one is asked
            continue
    if gone:
        _say("info", f"card: unloaded {', '.join(f'{v.model} ({_ago(now, v.last_used)}, {v.size // MIB:,} MiB)' for v in gone)} "
                     f"for {for_what} — {need // MIB:,} MiB needed, {max(0, free) // MIB:,} MiB were free "
                     f"under the {t:.2f} line; the oldest real use goes first (operator, 2026-11-16)")
    if free + freed < need:
        return Outcome(False, need, free, tuple(v.model for v in gone),
                       f"{for_what}: {(free + freed) // MIB:,} of {need // MIB:,} MiB made")
    # NEVER LOADED BEFORE THE DRIVER SHOWS IT (xe overcommits silently)
    deadline = clock() + ROOM_WAIT_S
    while True:
        try:
            card = resident._card()
        except Exception:   # noqa: BLE001
            card = None
        if card is None or int(t * card[0]) - card[1] >= need or clock() >= deadline:
            break
        sleep(ROOM_POLL_S)
    return Outcome(True, need, free, tuple(v.model for v in gone))


def make_room_for(server: str, model: str, *, horizon: float | None,
                  protect: Iterable[tuple[str, str]] = (), **kw) -> Outcome:
    """`make_room` for loading `model`: its largest known size (`need_of`);
    never seen, nothing is told and nothing unloaded."""
    need = need_of(server, model)
    if need is None:
        return Outcome(None, why=f"{model} was never seen loaded: its size is not known")
    if need == 0:
        return Outcome(True, 0)
    return make_room(server, need=need, horizon=horizon, for_what=model, protect=protect,
                     exclude=(model,), **kw)


def horizon_for(server: str, model: str) -> float | None:
    """For work, now; for Louise's own load (inside `model_use.not_work`),
    the model's own last real use (None: never used)."""
    from app import model_load, model_use
    if model_use.counts():
        return time.time()
    return model_use.last_used().get((model_load.server_of(server), model))


def before_load(server: str, model: str) -> Outcome | None:
    """The cold-load hook (`model_load.ensure_loaded`): room made for
    `model` before its load, unless the keeper made it already or the
    node's mode unloads nothing. Never raises."""
    if _room_made.get():
        return None
    from app import resident
    if need_of(server, model) in (None, 0):
        return None                              # nothing to tell from, before any read
    from app import node_state
    try:
        if not resident.preloads(node_state.peek_worker_state())[0]:
            return None                          # paused, seed-only: nothing unloaded
    except Exception:   # noqa: BLE001
        return None
    return make_room_for(server, model, horizon=horizon_for(server, model))
