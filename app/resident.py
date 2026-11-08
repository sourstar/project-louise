"""Every model Ask uses preloaded on the card — A TRIAL (operator, 2026-11-08,
option A: "keep every model Ask uses resident on the card"; "The load all is
a test"), and its final rule the same day: "It applies to any collection, on
demand or all embedding preloads the model. Chat, speech, rerankers, madlad
are preloaded too, nothing is evicted only because it's idle, only if space
is claimed."

WHY. Two things took the question path's models off the card, and each cost
the first question after a pause its load: LocalAI's idle watchdog (5 min)
unloaded the chat model (~6 s), and the worker unloaded the embedders of the
collections that do not embed everything (`worker._release_embedder`,
2026-10-14; `worker._sweep_unheld_embedders`' IDLE_BEATS), so the first
search in gutenberg or wikipedia paid its query encoder's load. Those rules
held because the card did not fit everything; measured by the node
(München, 2026-11-07, live engine), with every model resident the B70 holds
30,267 of 32,656 MiB (92.7 %), host RAM peak 41.6 of 63.4 GB. Now NOTHING IS
UNLOADED FOR BEING IDLE: LocalAI's idle watchdog is off (3a00b65), and the
worker's two unload paths unload no model a held collection names (amended
2026-11-08).

PRELOADED, NOT PINNED: the reclaimer evicts the least-recently-used model
when space is claimed (operator 2026-11-08). LocalAI's memory reclaimer,
past `memory_reclaimer_threshold` (0.95, 9cd77b3;
deploy/vega/stage/localai_settings.py), evicts the least recently used
model it may — never a pinned one (pkg/model/watchdog.go `evictLRUModel`, at
53cd6c5, v4.11.0 and v4.12.1). Pinning the set would leave space claimed
nothing to evict: the operator's 35B loaded for an evaluation, or a corpus
encoder's load-time peak, would fail instead of evicting. So this module
pins nothing (app/model_pins.py is the processing's, as it was: an embed
batch pins its encoder while it runs, operator 2026-10-23). It LOADS what
the set lacks, each through its caller's own warm — `llm.warm`,
`translate.warm`, `retrieval._warm_reranker`, `embeddings.warm_side` — or,
for speech, one minimal request; every one through
`model_load.ensure_loaded`, bounded by the model's load bound, one at a
time, on one thread (`_warm_all`), never inside a transaction.

A LOAD THAT CANNOT STAY IS NOT MADE: two guards, so the keeper never thrashes
against the reclaimer.
- THE ROOM CHECK (`room`): a model not loaded is loaded only if its last
  known size (`model_load.size_vram`, LocalAI's `/system` `size_vram` as
  app/gpu_models.py reads it, remembered per model; never seen: allowed)
  fits under threshold × total − used — the card as its driver counts it,
  read by the GPU probe (app/gpu_probe.py through app/gpu_memory.py's
  query; the worker has no card of its own, invariant 7), the threshold
  read from LocalAI's `GET /api/settings` (`memory_reclaimer_threshold`,
  else DEFAULT_THRESHOLD). Otherwise it waits, said once: "X stays
  unloaded: the card has room for it only after space is given back". A
  card or a setting that cannot be read decides nothing: the load is made.
- THE BACK-OFF (`_due`): a model this keeper loaded found off the card again
  within REEVICTED_WITHIN_S is not loaded again for 10 min, then 20, 40 …,
  capped at BACKOFF_CAP_S, and said each time; one that stayed longer than
  that starts the count again.

THE SET IS THE SYSTEM'S (CLAUDE.md RULE 2), never a list typed — `wanted`,
from the resolutions the question path itself uses:
- the chat model: CHAT_URL / CHAT_MODEL, in LocalAI's dialect (llm.warm's
  condition);
- the translation model (MADLAD): TRANSLATE_URL / TRANSLATE_MODEL;
- each served collection's reranker that names its model
  (`collections.reranker_for`; RERANK_URL names none);
- each served collection's ENCODERS — the document model and the query
  model (`embeddings._side`) — where its embed mode, as the worker applies
  it (the Library's override, else the file), is on-demand, all or p2p
  (`collections.EMBEDDING_MODES`); `off` is not preloaded: its query
  encoder loads at its first search and stays until space is claimed. A
  switch to `off` drops it from the set at once (`forget`), unloading
  nothing;
- the speech models, `[speech]` stt_model, vad_model and tts_model, on the
  node's model server (app/voice.py `settings` and `server`).
SERVED is the worker's: a collection its boot check did not refuse and
`node pause <collection>` has not paused. `node freeze` keeps it served.
The keeper beats every SETTLE_BEAT_S on a thread of its own, so a long run
check on the settle thread never delays a preload.

THE SWITCHES, Tier 2 (invariant 10), platform.toml, both true by default:

    [models]
    keep_ask_resident = true   # chat, translation, rerankers, encoders
    preload_speech = true      # the [speech] models

False: that part is not preloaded. Nothing is unloaded for being idle
whatever they say (the operator's rule is not the trial's). Shown by
`louise node resident show`; no dialog (RULE 2).

THE NODE'S MODE WINS. Paused or seed-only — the node's own, or a profile's
`mode` (the stricter wins, node_state.runs) — is "the node suspended because
the operator needs the GPU" (operator, 2026-10-23): nothing is preloaded,
and nothing is unloaded either. Running and acquire-only preload: acquire-
only embeds nothing for the corpus but serves every search, and a profile
never limits a query — in the quiet windows "downloads, seeding and
searches go on" and "the searches it serves run at full speed"
(docs/notes/node-profiles-design.md decision 6).

THE STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"): LocalAI's
own preload (`LOAD_TO_MEMORY`, the models loaded at its start). It is a list
the operator writes and keeps in step with the collections by hand (RULE 2),
it follows neither an embed mode nor the node's mode, it loads once — a
model the reclaimer evicts stays off until a request — and it is skipped
without a word under `max_active_backends = 1` (localai-bugs.md B5). It
pays once the set stops changing with the collections and a reload after
an eviction is no longer wanted.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app import model_pins

#: the node modes (node_state.MODE_RUNS) under which the set is preloaded
PRELOADING_MODES = frozenset({"running", "acquire-only"})
ASK, SPEECH = "keep_ask_resident", "preload_speech"
#: the roles a collection's embed mode decides (`forget`)
ENCODER_ROLES = frozenset({"query encoder", "encoder"})
#: one speech warm: a tenth of a second of silence, 16 kHz
_SILENCE = 1600
#: the reclaimer's line when LocalAI's settings do not say: the node's
#: (deploy/vega/stage/localai_settings.py, operator 2026-11-08)
DEFAULT_THRESHOLD = 0.95
#: the bound on one `GET /api/settings` (a beat's room check)
SETTINGS_TIMEOUT_S = 2.0
#: a model this keeper loaded, off the card again this soon: space is claimed
REEVICTED_WITHIN_S = 600.0
#: the first back-off after a repeat eviction, doubled each time, and its cap
BACKOFF_FIRST_S, BACKOFF_CAP_S = 600.0, 7200.0
MIB = 1 << 20


@dataclass(frozen=True)
class Switches:
    ask: bool = True
    speech: bool = True
    #: the platform file did not load: the last good reading (else the defaults) is used
    error: str | None = None

    def on(self, switch: str) -> bool:
        return self.ask if switch == ASK else self.speech


@dataclass(frozen=True)
class Resident:
    """One model of the set: where, what for, for which collections."""
    server: str
    model: str
    roles: tuple[str, ...]
    collections: tuple[str, ...] = ()
    switch: str = ASK
    warm: Callable[[], object] | None = field(default=None, compare=False, repr=False)

    @property
    def key(self) -> tuple[str, str]:
        return (self.server, self.model)

    def says(self) -> str:
        """"query encoder, encoder (gutenberg, wikipedia)"."""
        return ", ".join(self.roles) + (f" ({', '.join(self.collections)})" if self.collections else "")


@dataclass(frozen=True)
class Room:
    """What a load would take and what the card leaves under the line."""
    needs: int                 # bytes: the model's last known size
    free: int                  # bytes: threshold × total − used (negative: past the line)
    threshold: float

    @property
    def fits(self) -> bool:
        return self.needs <= self.free

    def says(self) -> str:
        return (f"needs {self.needs // MIB:,} MiB, {max(0, self.free) // MIB:,} MiB free under "
                f"the {self.threshold:.2f} line")


@dataclass
class _Track:
    """One model's preload history in this process."""
    warmed_at: float | None = None      # `_now()` when this keeper last loaded it
    strikes: int = 0                    # repeat evictions in a row
    next_try: float = 0.0               # not loaded again before this `_now()`
    waiting: bool = False               # "stays unloaded" said, until it fits


_lock = threading.Lock()
_last_switches = Switches()
#: the last beat's set by key — what the loads behind it may still load
_last: dict[tuple[str, str], Resident] = {}
#: per model of the set: its loads, repeat evictions and wait for room
_track: dict[tuple[str, str], _Track] = {}
#: why the last beat preloaded nothing, said once
_last_why: str | None = None
_warm_thread: threading.Thread | None = None
_say: Callable[[str, str], None] = model_pins.node_event
#: the back-off's clock (tests move it)
_now: Callable[[], float] = time.monotonic


def set_log(fn: Callable[[str, str], None]) -> None:
    """`fn(level, text)`; tests capture it."""
    global _say
    _say = fn


def reset() -> None:
    """Forget the last beat and every model's history (tests)."""
    global _last, _last_why, _last_switches
    with _lock:
        _last, _last_why, _last_switches = {}, None, Switches()
        _track.clear()


def switches() -> Switches:
    """`[models] keep_ask_resident` and `preload_speech`, through the node's
    one reader of platform.toml (`platform_config.load`); a file that does
    not load keeps the last good reading, and says why."""
    global _last_switches
    from app import platform_config
    try:
        m = platform_config.load().models
    except Exception as e:   # noqa: BLE001 — a broken file never changes what is held
        with _lock:
            last = _last_switches
        return Switches(last.ask, last.speech, f"{platform_config.platform_path()} does not load "
                                               f"({type(e).__name__}: {str(e)[:200]}); the last reading kept")
    got = Switches(bool(m.keep_ask_resident), bool(m.preload_speech))
    with _lock:
        _last_switches = got
    return got


def wanted(served: Iterable[Any], *, embed_mode: Callable[[Any], str],
           sw: Switches | None = None) -> list[Resident]:
    """THE resident set, in the order a question meets its models; one entry
    per (server, model), its roles and collections merged. `served`: the
    collections searched; `embed_mode(c)`: the mode the worker applies."""
    from app import collections as collections_mod, config, embeddings, llm, model_load, retrieval, translate
    sw = sw or switches()
    found: dict[tuple[str, str], Resident] = {}

    def add(url: str | None, model: str | None, role: str, collection: str | None,
            warm: Callable[[], object] | None, switch: str = ASK) -> None:
        if not url or not model:
            return
        key = (model_load.server_of(url), model)
        got = found.get(key)
        if got is None:
            found[key] = Resident(key[0], model, (role,), (collection,) if collection else (), switch, warm)
            return
        roles = got.roles + ((role,) if role not in got.roles else ())
        names = got.collections + ((collection,) if collection and collection not in got.collections else ())
        found[key] = Resident(got.server, model, roles, names, got.switch, got.warm or warm)

    if sw.ask:
        if config.TRANSLATE_URL and config.TRANSLATE_MODEL:
            add(config.TRANSLATE_URL, config.TRANSLATE_MODEL, "translation", None, translate.warm)
        for c in served:
            try:
                mode = embed_mode(c)
            except Exception:   # noqa: BLE001 — the file's word then
                mode = c.embed_mode_default or collections_mod.DEFAULT_EMBED_MODE
            if mode in collections_mod.EMBEDDING_MODES:
                try:
                    recipe = collections_mod.recipe_for(c)
                except Exception:   # noqa: BLE001 — no encoder declared: none to hold
                    recipe = None
                if recipe is not None and getattr(recipe, "model", None):
                    q = embeddings._side(recipe, True)
                    add(q.url, q.model, "query encoder", c.name,
                        lambda c=c, r=recipe: embeddings.warm_side(c, query=True, recipe=r))
                    add(recipe.url, recipe.model, "encoder", c.name,
                        lambda c=c, r=recipe: embeddings.warm_side(c, query=False, recipe=r))
            r = collections_mod.reranker_for(c)
            if r is not None and r.model:
                add(r.url, r.model, "reranker", c.name, lambda r=r: retrieval._warm_reranker(r))
        if config.CHAT_API == "openai" and config.CHAT_URL and config.CHAT_MODEL:
            add(config.CHAT_URL, config.CHAT_MODEL, "chat", None, llm.warm)
    if sw.speech:
        from app import voice
        s = voice.settings_or_none()
        url = voice.server()
        for role, model, kind in (("speech to text", s.stt_model, "stt"),
                                  ("voice activity", s.vad_model, "vad"),
                                  ("text to speech", s.tts_model, "tts")):
            if url and model:
                add(url, model, role, None, _speech_warm(url, model, kind, role), SPEECH)
    return list(found.values())


def _speech_warm(server: str, model: str, kind: str, role: str) -> Callable[[], object]:
    """The speech models have no warm of their own (app/speech.py and
    app/transcribe.py load on the reader's first sentence or clip): one
    minimal request of the model's kind, through `ensure_loaded`."""
    def send(bound_s: float):
        from app import httpc
        if kind == "tts":
            return httpc.post(f"{server}/v1/audio/speech", json={"model": model, "input": "OK"},
                              timeout=bound_s)
        if kind == "vad":
            return httpc.post(f"{server}/v1/vad", json={"model": model, "audio": [0.0] * _SILENCE},
                              timeout=bound_s)
        from app import transcribe
        return httpc.post(f"{server}/v1/audio/transcriptions",
                          files={"file": ("warm.wav", transcribe.wav(b"\x00\x00" * _SILENCE), "audio/wav")},
                          data={"model": model}, timeout=bound_s)

    def warm() -> object:
        from app import model_load
        return model_load.ensure_loaded(server, model, send, caller=f"resident {role}", max_age_s=0.0)
    return warm


def forget(collection: str) -> list[str]:
    """`collection` was switched to `off` (`worker._release_embedder`): its
    encoders leave the set now, not at the next beat, so a load behind the
    last beat does not make them — every model of the set only its encoder
    roles want. Nothing is unloaded: it stays until space is claimed. A
    model another collection or another role still wants stays in the set.
    Returns the models dropped."""
    gone: list[str] = []
    with _lock:
        for key, r in list(_last.items()):
            if collection not in r.collections or not set(r.roles) <= ENCODER_ROLES:
                continue
            rest = tuple(n for n in r.collections if n != collection)
            if rest:
                _last[key] = Resident(r.server, r.model, r.roles, rest, r.switch, r.warm)
            else:
                del _last[key]
                _track.pop(key, None)
                gone.append(r.model)
    if gone:
        _say("info", f"resident: {', '.join(gone)} no longer preloaded — {collection}'s embed is off; "
                     f"left loaded until LocalAI needs the space")
    return gone


def _loaded(r: Resident) -> bool | None:
    """Whether the runner lists the model as loaded; None when it cannot say."""
    from app import model_load
    ids = model_load.loaded_models(r.server)
    return None if ids is None else r.model in ids


def _card() -> tuple[int, int] | None:
    """(total, used) bytes of the card the models are on, as its driver
    counts them — asked of the GPU probe (GET /gpu), which reads them with
    app/gpu_memory.py's query; None when no probe is set or it cannot say.
    Of several cards, the one whose processes hold the most."""
    from app import config, gpu
    if not config.GPU_PROBE_URL:
        return None
    try:
        devices = gpu.from_json(gpu._get_json(config.GPU_PROBE_URL.rstrip("/") + "/gpu")).devices
    except Exception:   # noqa: BLE001 — the card unread: the room check decides nothing
        return None
    known = [d for d in devices if d.vram_total and d.used is not None]
    if not known:
        return None
    d = max(known, key=lambda d: d.attributed)
    return int(d.vram_total), int(d.used)


def _threshold(server: str) -> float:
    """LocalAI's `memory_reclaimer_threshold`, from `GET /api/settings`
    (that object also carries `api_keys` and the p2p token: nothing of it
    but this number is kept or said, docs/notes/node-runbook.md), else
    DEFAULT_THRESHOLD."""
    from app import httpc
    try:
        r = httpc.get(f"{server}/api/settings", timeout=SETTINGS_TIMEOUT_S)
        v = r.json().get("memory_reclaimer_threshold") if r.status_code == 200 else None
    except Exception:   # noqa: BLE001 — unread: the node's line
        v = None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1:
        return float(v)
    return DEFAULT_THRESHOLD


def room(r: Resident) -> Room | None:
    """What loading `r` would take against what the card leaves under the
    reclaimer's line; None when it cannot be told — the model never seen
    loaded (its first load is allowed) or the card unread — and the load is
    then made, as before."""
    from app import model_load
    size = model_load.size_vram(r.server, r.model)
    if size is None:
        return None
    card = _card()
    if card is None:
        return None
    total, used = card
    t = _threshold(r.server)
    return Room(size, int(t * total) - used, t)


def _due(r: Resident, now: float) -> bool:
    """Whether `r`, found off the card, may be loaded now. A model this
    keeper loaded and finds off the card again within REEVICTED_WITHIN_S was
    evicted because space was claimed: it waits BACKOFF_FIRST_S, doubled at
    each repeat up to BACKOFF_CAP_S, and that is said; one that stayed
    longer starts the count again."""
    said = None
    with _lock:
        t = _track.setdefault(r.key, _Track())
        if t.warmed_at is not None:
            stayed = now - t.warmed_at
            t.warmed_at = None
            if stayed < REEVICTED_WITHIN_S:
                t.strikes += 1
                wait = min(BACKOFF_FIRST_S * 2 ** (t.strikes - 1), BACKOFF_CAP_S)
                t.next_try = now + wait
                said = (f"resident: {r.model} — {r.says()} — off the card again {stayed / 60:.0f} min "
                        f"after its load: space is claimed, so it is not loaded again for "
                        f"{wait / 60:.0f} min (back-off {t.strikes}, at most "
                        f"{BACKOFF_CAP_S / 3600:.0f} h)")
            else:
                t.strikes = 0
        due = now >= t.next_try
    if said:
        _say("info", said)
    return due


def preloads(state: str | None = None) -> tuple[bool, str]:
    """(whether the node's mode preloads the set now, why, in words).
    `state`: the node's mode as read by the caller (a command peeks it);
    None reads it. The profile's mode counts too: the stricter wins."""
    from app import cpu_limit, node_state
    if state is None:
        try:
            state = node_state.worker_state()
        except Exception:   # noqa: BLE001 — unreadable: preloads nothing, as every gate reads it
            state = "unknown"
    if state not in PRELOADING_MODES:
        return False, (f"the node is {state}: the GPU is the operator's (2026-10-23), so nothing is "
                       f"preloaded for Ask, and nothing unloaded")
    lim = cpu_limit.current()
    if lim.mode is not None and lim.mode not in PRELOADING_MODES:
        return False, (f"profile {lim.profile} holds the node to {lim.mode}"
                       + (f" until {lim.until:%H:%M}" if lim.until else "")
                       + ": nothing is preloaded for Ask, and nothing unloaded")
    return True, (f"the node is {state}" + (f", profile {lim.profile} ({lim.mode})" if lim.mode else "")
                  + ": the set is preloaded")


def beat(served: Iterable[Any], *, embed_mode: Callable[[Any], str], state: str | None = None,
         stop: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """One beat of the worker's keeper: the set resolved, its changes said,
    the models of it the runner does not list loaded — and not backing off —
    loaded behind (`_warm_all`, which checks each one's room). Pins nothing,
    unloads nothing. Returns what it did. Never raises past a model."""
    global _last, _last_why
    sw = switches()
    ok, why = preloads(state)
    want = wanted(served, embed_mode=embed_mode, sw=sw) if ok else []
    by_key = {r.key: r for r in want}
    with _lock:
        before = dict(_last)
        said = _last_why
        _last = by_key
        for gone in [k for k in _track if k not in by_key]:
            del _track[gone]
    _say_changes(before, by_key, sw, ok, why, said)
    if want:
        with _lock:
            _last_why = None
    now = _now()
    missing = [r for r in want if r.warm is not None and _loaded(r) is False]
    due = [r for r in missing if _due(r, now)]
    warming = _start_warm(due, stop) if due else []
    return {"set": sorted(by_key), "warming": [r.key for r in warming],
            "backing_off": sorted(r.key for r in missing if r not in due),
            "preloads": ok, "why": why}


def _say_changes(before: dict, now: dict, sw: Switches, ok: bool, why: str,
                 said: str | None) -> None:
    """The event log: what entered the set and what left it, each model with
    its roles and collections — only what changed since the last beat."""
    global _last_why
    added = [now[k] for k in now if k not in before]
    gone = [before[k] for k in before if k not in now]
    if added:
        _say("info", f"resident (trial, operator 2026-11-08): preloading {len(added)} model(s) for Ask, "
                     f"not pinned — the reclaimer evicts the least recently used when space is "
                     f"claimed: " + "; ".join(f"{r.model} — {r.says()}" for r in added))
    if gone:
        if not ok:
            reason = why
        else:
            off = [s for s, on in ((ASK, sw.ask), (SPEECH, sw.speech)) if not on]
            reason = (f"[models] {' and '.join(f'{s} = false' for s in off)}, or no served collection "
                      f"uses them any more (removed, paused, or its embed set to off)" if off else
                      "no served collection uses them any more (removed, paused, or its embed set to off)")
        _say("info", "resident: no longer preloaded " + "; ".join(f"{r.model} — {r.says()}" for r in gone)
                     + f": {reason}; left loaded until LocalAI needs the space")
    if not now and not ok and said != why:
        with _lock:
            _last_why = why
        if not gone:
            _say("info", f"resident: nothing preloaded — {why}")


def _start_warm(missing: list[Resident], stop: Callable[[], bool]) -> list[Resident]:
    """The loads behind the beat, on ONE thread at a time: a beat that finds
    one running leaves the rest to the next beat."""
    global _warm_thread
    with _lock:
        if _warm_thread is not None and _warm_thread.is_alive():
            return []
        _warm_thread = threading.Thread(target=_warm_all, args=(missing, stop),
                                        name="resident-warm", daemon=True)
        t = _warm_thread
    t.start()
    return missing


def _warm_all(items: list[Resident], stop: Callable[[], bool]) -> None:
    """Each model not loaded, loaded, one after the other, through its
    caller's warm (bounded by its load bound; a cold start is logged by
    app/model_load.py) — if it is still in the set, still not loaded, and
    the card has room for it under the reclaimer's line (`room`, read again
    before each load, since the one before changed it). The node's mode is
    asked again before each: a pause stops the loads at the next one."""
    for r in items:
        if stop() or not preloads()[0]:
            return
        with _lock:
            if r.key not in _last:
                continue                   # left the set meanwhile (`forget`, a beat)
        if _loaded(r) is not False:
            continue                       # loaded meanwhile, or the runner cannot say
        got = room(r)
        with _lock:
            t = _track.setdefault(r.key, _Track())
            tell = got is not None and not got.fits and not t.waiting
            t.waiting = got is not None and not got.fits
        if got is not None and not got.fits:
            if tell:
                _say("info", f"resident: {r.model} — {r.says()} — stays unloaded: the card has room for "
                             f"it only after space is given back ({got.says()})")
            continue
        _say("info", f"resident: loading {r.model} — {r.says()} (not on the card"
                     + (f"; {got.says()}" if got is not None else "") + ")")
        t0 = time.monotonic()
        try:
            r.warm()
        except Exception as e:   # noqa: BLE001 — a load never takes the keeper down
            _say("warn", f"resident: {r.model} — {r.says()} — not loaded after "
                         f"{time.monotonic() - t0:.1f} s ({type(e).__name__}: {str(e)[:200]}); "
                         f"tried again at the next beat")
            continue
        with _lock:
            _track.setdefault(r.key, _Track()).warmed_at = _now()


def clear() -> None:
    """The set forgotten (a worker that stops): a load behind the last beat
    makes no other. Nothing is pinned to give back, nothing is unloaded."""
    global _last
    with _lock:
        _last = {}
        _track.clear()


def wait_warm(timeout: float) -> None:
    """The loads behind the last beat, waited for up to `timeout` (tests)."""
    with _lock:
        t = _warm_thread
    if t is not None:
        t.join(timeout)


# ---- the command line ------------------------------------------------------------

def served_now() -> tuple[list[Any], dict[str, str]]:
    """(the collections the node serves, {name: why not} for the others) as
    a command sees them, reading and creating nothing of the node's state:
    the files as the node loads them, its boot check read-only
    (`health._boot_refusals`), its paused collections peeked."""
    from app import collections as collections_mod, health, node_show, node_state
    got = collections_mod.load_all(skip_unreadable=True)
    loaded, unreadable = got if isinstance(got, tuple) else (got, [])
    out: dict[str, str] = {}
    for bad in unreadable:
        name, why = (bad if isinstance(bad, tuple) else (str(bad), "does not load"))[:2]
        out[str(name)] = f"refused: {why}"
    refused = health._boot_refusals(loaded)
    paused = set(node_state.peek(node_show._paused, []))
    served = []
    for c in loaded:
        if c.name in refused:
            out[c.name] = f"refused: {refused[c.name]}"
        elif c.name in paused:
            out[c.name] = "paused (`node pause`)"
        else:
            served.append(c)
    return served, out


def show() -> dict[str, Any]:
    """`louise node resident show`: the switches as the node reads them, the
    mode and whether it preloads, and every model of the set — its roles and
    collections, whether the runner lists it loaded, its last known size,
    and whether LocalAI has it pinned and by whom (never this keeper: an
    embed batch's pin, or the operator's)."""
    from app import effective_config, model_load, node_state, platform_config
    sw = switches()
    state = node_state.peek_worker_state()
    ok, why = preloads(state)
    served, not_served = served_now()
    modes = {c.name: _mode_shown(effective_config, c) for c in served}
    want = wanted(served, embed_mode=lambda c: modes[c.name], sw=sw)
    holders = {(p["server"], p["model"]): sorted(p["holders"]) for p in model_pins.status()["pins"]}
    rows = []
    for r in want:
        ids = model_load.loaded_models(r.server)
        size = model_load.size_vram(r.server, r.model)
        rows.append({"model": r.model, "server": r.server, "roles": list(r.roles),
                     "collections": list(r.collections), "switch": r.switch,
                     "loaded": None if ids is None else r.model in ids,
                     "size_mib": None if size is None else size // MIB,
                     "pinned": model_pins.read_pinned(r.server, r.model),
                     "holders": holders.get(r.key, [])})
    out: dict[str, Any] = {
        "file": str(platform_config.platform_path()),
        "rule": "operator, 2026-11-08: \"It applies to any collection, on demand or all embedding "
                "preloads the model. Chat, speech, rerankers, madlad are preloaded too, nothing is "
                "evicted only because it's idle, only if space is claimed\" — preloaded, not pinned: "
                "the reclaimer evicts the least-recently-used model when space is claimed",
        "switches": {ASK: sw.ask, SPEECH: sw.speech}, "mode": state, "preloads": ok, "why": why,
        "models": rows,
        "embed_off": sorted(n for n, m in modes.items() if m not in _embedding_modes()),
        "not_served": not_served}
    if sw.error:
        out["switches_note"] = sw.error
    return out


def _mode_shown(effective_config, c) -> str:
    """The embed mode as `hold config show` resolves it; the file's word
    when the state database cannot be read."""
    from app import collections as collections_mod
    try:
        return str(effective_config._embed_mode(c)["value"])
    except Exception:   # noqa: BLE001 — one collection, not the show
        return c.embed_mode_default or collections_mod.DEFAULT_EMBED_MODE


def _embedding_modes() -> tuple[str, ...]:
    from app import collections as collections_mod
    return collections_mod.EMBEDDING_MODES
