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

PAGE INSTEAD OF UNLOAD (operator, 2026-11-24, verbatim: "Extend page instead
of unload to the dense models"; platform.toml `[models] make_room = "page"`,
07 §7.8; THE DEFAULT since 2026-11-25, operator: "Set the new paging mode as
the default in the engine" — where the card's driver is one that was measured
to page, `PAGING_DRIVERS`; under any other the rule is today's unload, said
once). Measured on the B70 that morning (München,
docs/notes/backend-qualification-2026-11-21.md §3 phase 5, S5 part 2): with
the engine's VRAM check off and its buffers cut in 2 GiB pieces, a card
overcommitted by 3.5 GiB kept every model loaded — the xe driver moved the
idle ones' memory to host memory and back at their next use, 0.2–0.4 s more
on a small model's first request against the 7.5–8.7 s of a load after an
unload, 1.3–1.8 s on the 35B. So, in "page":
- a load WORK is waiting on (`before_load` under a counted use) that does
  not fit under the line unloads NOTHING where the host can hold the
  difference: MemAvailable less the shortfall stays at or above the node's
  floor (`page_floor_gib`). The load goes, and the driver chooses what
  leaves the card — by idleness and by whole buffers, not by this module's
  order. Said in the node's log, once a load;
- the host unread, or too short: today's rule, the oldest real use
  unloaded — the host's own OOM killer is what the floor stands above;
- NEVER FURTHER OVER THE CARD THAN WAS MEASURED, AND NEVER INTO A CONTEXT'S
  ROOM (the architecture session's blocking point, 2026-11-24). A model
  whose context is made on demand reserves it from the card at each
  request, and a reservation the card refuses KILLS its backend
  (`UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY` at ggml-sycl's pool: München,
  against the 35B's system-allocated pages). The driver pages lazily — it
  fills the card first — so no rule here can keep the working-memory
  reserve FREE on the card; what can be held is the envelope that was
  measured: the loaded models at their largest plus the load may exceed
  the card by `page_over_gib` at most (3; S5 held 3.5 with a 1.9 GiB
  context made on demand in it), and the host must keep its floor AND a
  context's worth above it (`resident.CONTEXT_RESERVE`), since a context
  made on a full card pushes as much of something else to the host. Past
  either, the unload. The bound is raised by a measurement, the engine's
  refusal made survivable by a patch — neither is here;
- THE CALLER SAYS IT (the architecture session's review): the keeper's own
  preload, a run check's and a gate's load keep today's rule — a preload is
  never urgent, and what is preloaded must not push what is in use off the
  card. Only `before_load` under counted work asks to page;
- a model whose paged load FAILED is remembered (`load_ended`) and gets its
  room by unloading the next time, for PAGE_AGAIN_S: an llama.cpp model
  whose file lacks `GGML_SYCL_BMG_VRAM_CHECK=0` refuses an allocation the
  card cannot hold, and would otherwise be let over the line and fail at
  every attempt;
- LocalAI's memory reclaimer must be OFF in this mode
  (deploy/vega/stage/localai_settings.py follows the setting): a card
  holding more than its size reads full at every tick. Its threshold stays,
  for this module's and the keeper's line.
What it does NOT do: choose the victim, page the keeper's preloads, or
answer for memory the driver cannot move.

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

#: a model whose paged load failed gets its room by unloading for this long
PAGE_AGAIN_S = 3600.0
GIB = 1 << 30
#: (server, model) let over the line by paging, awaiting its load's outcome
_paged: set[tuple[str, str]] = set()
#: (server, model) → when its paged load failed
_no_page: dict[tuple[str, str], float] = {}

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
    #: bytes over the line left to the driver to page (`make_room = "page"`)
    paged: int = 0


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


def reset() -> None:
    """Forget which loads were paged and which failed (tests)."""
    _paged.clear()
    _no_page.clear()
    _driver_said.clear()


def host_available() -> int | None:
    """The host's MemAvailable in bytes (/proc/meminfo: a container reads
    the host's), or None where it cannot be read. What the kernel says can be
    taken without swapping; a ZFS ARC is not in it, which errs on the safe
    side."""
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


#: the drivers under which idle models' memory was MEASURED to move to host
#: memory and back on a card committed over its size: xe (Intel Arc Pro B70,
#: S5 part 2, 2026-11-24). Under a driver that does not page, a load over the
#: card is a failed load or a killed backend — so there "page" unloads, as
#: before, and a driver joins this list by a measurement, never by its name.
PAGING_DRIVERS = ("xe",)

#: the drivers `_page` has said it does not page under (once each)
_driver_said: set[str] = set()


def rule() -> tuple[str, int, int]:
    """(`[models] make_room`, the host floor, the most over the card — both
    in bytes) as the node's file says now; a file that does not load is
    today's rule."""
    from app import platform_config
    try:
        m = platform_config.load().models
        return m.make_room, m.page_floor_gib * GIB, m.page_over_gib * GIB
    except Exception:   # noqa: BLE001 — unread: nothing changes
        return "unload", 0, 0


def committed(server: str) -> int | None:
    """The card as its loaded models could fill it: each at the LARGEST it
    was seen holding (a paged-out model reads small now, and a model whose
    context is made on demand holds it only while it works), those on
    another card left out; None where the runner gives no sizes."""
    from app import model_load, resident
    held = model_load.held_sizes(server, max_age_s=model_load.INTERACTIVE_TTL_S)
    if held is None:
        return None
    mem = resident._memory_now()
    return sum(max(int(size or 0), mem.sizes.get((server, m)) or 0, model_load.size_vram(server, m) or 0)
               for m, (_pid, size) in held.items() if (server, m) not in mem.elsewhere)


def _page(server: str, *, need: int, free: int, total: int, used: int, t: float, for_what: str,
          loading: Iterable[str]) -> Outcome | None:
    """PAGE INSTEAD OF UNLOAD (the module's docstring): the Outcome of a
    load let over the line, or None where today's rule applies."""
    from app import resident
    mode, floor, over_max = rule()
    if mode != "page":
        return None
    driver = resident.card_driver()             # as `make_room` has just read the card
    if driver not in PAGING_DRIVERS:
        if (driver or "") not in _driver_said:
            _driver_said.add(driver or "")
            _say("info", f"card: `[models] make_room` is \"page\", and this card's driver "
                         f"({driver or 'not read'}) was not measured to move idle models' memory to the host "
                         f"(measured: {', '.join(PAGING_DRIVERS)}): room is made by unloading, as before")
        return None
    keys = [(server, m) for m in loading]
    now = time.time()
    if any(now - _no_page.get(k, float("-inf")) < PAGE_AGAIN_S for k in keys):
        return None                               # said when it failed
    short = need - free
    reserve = resident.CONTEXT_RESERVE
    avail = host_available()
    held = committed(server)
    # what the driver counts now stands under the sum: memory on the card that is no model of this runner
    over = None if held is None else max(held, used) + need - total
    why = ("the runner gives no sizes to count the card's commitment from" if over is None else
           f"the card would be committed {over // MIB:,} MiB over its size, past the {over_max // MIB:,} MiB "
           f"that were measured to hold" if over > over_max else
           "the host's memory cannot be read" if avail is None else
           f"the host has {avail // MIB:,} MiB available and would be under its floor of {floor // MIB:,} MiB "
           f"plus a context's {reserve // MIB:,}" if avail - short - reserve < floor else None)
    if why is not None:
        _say("info", f"card: {for_what} is {short // MIB:,} MiB over the {t:.2f} line and is not paged — "
                     f"{why}: room is made by unloading, as before")
        return None
    _paged.update(keys)
    _say("info", f"card: nothing unloaded for {for_what} — {need // MIB:,} MiB needed, {max(0, free) // MIB:,} "
                 f"MiB free under the {t:.2f} line; the driver pages {short // MIB:,} MiB of idle models' memory "
                 f"to the host ({avail // MIB:,} MiB available, floor {floor // MIB:,}) (operator, 2026-11-24)")
    return Outcome(True, need, free, why="paged", paged=short)


def load_ended(server: str, model: str, loaded: bool) -> None:
    """The outcome of a cold load, told by `model_load.ensure_loaded`: a
    model let over the line by paging whose load failed gets its room by
    unloading for PAGE_AGAIN_S. Never raises."""
    try:
        from app import model_load
        key = (model_load.server_of(server), model)
        if key not in _paged:
            return
        _paged.discard(key)
        if loaded:
            _no_page.pop(key, None)
            return
        _no_page[key] = time.time()
        _say("warn", f"card: {model} did not load over the line (paged): for the next "
                     f"{PAGE_AGAIN_S / 60:.0f} min its room is made by unloading. An llama.cpp model pages only "
                     f"with GGML_SYCL_BMG_VRAM_CHECK=0 in its file (and GGML_SYCL_HOST_PINNED_MEM_2G=1, so "
                     f"that it leaves the card in pieces)")
    except Exception:   # noqa: BLE001 — a note, never a load's failure
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
              page: bool = False,
              sleep: Callable[[float], None] = time.sleep,
              clock: Callable[[], float] = time.monotonic) -> Outcome:
    """Room for `need` bytes on `server`'s card under the reclaimer's line,
    made by unloading the loaded models with the OLDEST real use first (the
    module's docstring). `horizon`: only models last used before it may go.
    `protect`: (server, model) that stay whatever their use (a question's own
    models). `exclude`: model ids of this server never considered (the model
    being loaded). `page`: the caller is a load work is waiting on, and may
    be let over the line where the node's rule is "page" (the docstring).
    Never raises."""
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
        if page:
            paged = _page(server, need=need, free=free, total=total, used=used, t=t, for_what=for_what,
                          loading=tuple(exclude))
            if paged is not None:
                return paged
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
    from app import model_use
    # only a load WORK waits on may page: Louise's own (a preload, a check, a gate) keeps today's rule
    return make_room_for(server, model, horizon=horizon_for(server, model), page=bool(model_use.counts()))
