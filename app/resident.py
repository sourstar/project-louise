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
  capped at BACKOFF_CAP_S, and said each time with the model's tier; one
  that stayed longer than that starts the count again.

THE BUDGET — WHAT IS PRELOADED AT ALL (`plan`; the defect it fixes, measured
by the node, 2026-11-08 03:25). The room check judges one load against the
card as it is, and every load passed it ("needs 1,113 MiB, 8,572 MiB free
under the 0.95 line"). But this keeper preloaded EVERY model of the set, and
once the operator put every collection on on-demand the set itself was
larger than the card holds with its working memory: 11 models, 28.3–28.5 GB
resident on the 32.7 GB B70, and the others' load-time and compute peaks
took the card past the reclaimer's line (0.95 ≈ 31.0 GB): "Memory usage
exceeds threshold, evicting LRU backend usage_percent=95.9/95.3/95.2"
evicted vega-medcpt-query, silero, omnivoice and the chat model while not
busy; one question then paid two chat cold starts of 11–15 s, and the
back-off backed off the chat itself. So the set is fitted to a budget
first, which the system decides (RULE 2: no list typed):

    budget = threshold × total − the working margin − the eviction penalty

  and the set is counted AT REST: each model at the bytes its load left it
  holding, before a request grows its buffers.

- AT REST AND LARGEST (`_size`, `_rest_size`): per model, the fewest and
  the most bytes LocalAI's `/system` gave as its `size_vram`, a reading
  under REST_FLOOR of the largest being no rest — the model was partly off
  the card (2026-11-09) — (as
  app/gpu_models.py reads it: the driver's per-process count, compute
  buffers included), from this process's readings and the file's
  (`state/resident-budget.json`, so a restart keeps them). The budget sums
  the AT-REST sizes; the room check reads the largest, as it did.
- THE MARGIN IS THE GROWTH, MEASURED (`_remember`): each beat, the card's
  count (the GPU probe, as `room` reads it) minus the at-rest sizes of every
  model LocalAI lists loaded (`/system` read before and after the card, a
  model in either counted once by its backend pid) — so what requests grow
  on the card, a load in flight (LocalAI lists a model only once loaded),
  the driver's buffers and a program outside LocalAI are what it measures.
  Its peak per MARGIN_BUCKET_S is kept in the file for MARGIN_WINDOW_S
  (6 h); the margin is the peak of the window. No sample while this
  keeper's own load runs (that model is counted in the budget already),
  nor where a runner gives no per-model sizes. Nothing measured in the
  window: DEFAULT_MARGIN, 3.0 GiB — the node's night: ~28.4 GB at rest
  with 11 models, and the reclaimer read 95.9 % of 32.7 GB = 31.36 GB,
  ~2.96 GB of growth. With it, that night's set (28.4 + 3.0 > 31.0 GB)
  drops its lowest tier, MADLAD, and the rest stays. (A model rebuilt under
  the same name: its largest size in the file errs high, the room check's
  side of caution; its at-rest size errs low if it grew, and the margin
  then measures the difference as growth.)
- THE EVICTION PENALTY — FEEDBACK (`_penalize`; an AIMD controller, the
  shape of TCP's congestion control, RFC 5681, as the scheduler's re-index
  probe is, docs/notes/scheduling.md §11): a beat samples the card
  once a minute and can miss a 500 ms spike the reclaimer sees. The
  reclaimer's eviction is the loss signal: a model this keeper preloaded
  found off the card again within REEVICTED_WITHIN_S of its load (the
  back-off's own detection, `_due`) adds its at-rest size to a penalty on
  the margin, at once; the penalty HALVES after each PENALTY_HALVING_S
  (2 h) without another such eviction. Kept in the file; said ("the budget
  shrank by N MiB: X was evicted M min after its load") and shown.
- THE SET IS FILLED BY PRIORITY, highest first, each model at its at-rest
  size (one seen only at its largest: at that, on the side of caution); a
  model seen loaded holding no VRAM (on the CPU) costs 0 and always fits.
  The order is a PRIOR that use then refines (the node, 2026-11-08: three
  questions in 24 h, all probes; MADLAD on no question's path, only in
  warm-up):
    1. chat, rerankers, speech (text to speech, then speech to text and
       voice activity) — the answer's own models;
    2. QUERY encoders — on every search; a symmetric space's one encoder
       is its query encoder;
    3. DOCUMENT encoders that differ from their query encoder (pubmed's
       medcpt-article) — needed only to embed on demand;
    4. the translation model (MADLAD), last.
  Within a tier the most recently used first, from what the node already
  keeps (node_state `encoder_use`: each encoder's requests, and the
  translation model's, app/translate.py `_touch`); never used, after the
  used and THE SMALLER FIRST, so more of the tier fits. The fill is
  first-fit: a model that does not fit is passed over and a smaller one
  below may take what is left, so a lower tier only ever takes what the
  tiers above it left — tier 1 is never pushed out by another. The LOADS
  run in the same order (`_warm_all` takes them by rank): after a boot the
  question path is warm first, chat first, and MADLAD is never first.
- A MODEL NEVER SEEN counts at the MEDIAN of the set's known sizes rather
  than being left out until seen: left out, a model LocalAI has not served
  since the file began would never be preloaded, and its first question
  would pay the load this keeper exists for; at the median it is placed
  like a typical model of the set, neither taking the whole budget nor
  slipping in free, and its first load corrects it. With no size known at
  all it counts at nothing, and the plan is made again before each load,
  so a node with nothing on its card learns the sizes one load at a time.
- OUT OF THE BUDGET is not preloaded: the model loads at its first request,
  through that request's own warm as before, and then follows the
  reclaimer's LRU, as an `off` collection's encoder does. Said once per
  change ("not preloaded — the budget is N MiB (the 0.95 line M MiB −
  margin K MiB, measured over 6 h) …") and by `louise node resident show`.
- THE BUDGET UNLOADS NOTHING (operator: "nothing is evicted only because
  it's idle, only if space is claimed"): it decides what is preloaded and
  re-warmed, never what goes. The room check and the back-off are as they
  were, guarding each load.
- The card's total is kept in the file too: a probe that stops answering
  stops the margin's samples, not the budget. A card never read gives no
  budget, and the set is preloaded as before.

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

THE BUDGET'S STANDARD: a knapsack-style admission by priority — items
placed highest priority first against a capacity, each by its size — the
shape of Ollama's scheduler, which reserves a model's estimated VRAM before
it loads it (server/sched.go). The alternative is LocalAI's own eviction,
made size-aware: today its reclaimer evicts the least recently used model
once the card is past the threshold, after the fact and blind to what the
model is for (pkg/model/watchdog.go `evictLRUModel`). Adopting it would
take an upstream change — a priority each model's YAML declares, the
eviction by priority before recency, and a load admitted by its expected
size before it starts — and this budget could then go. It would pay once
programs other than Louise load models on the card, since only LocalAI
sees every load.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
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
#: the working margin — the growth over the at-rest sizes — while nothing is
#: measured in the window: the node, 2026-11-08, ~28.4 GB at rest with 11
#: models, the reclaimer reading 95.9 % of 32.7 GB = 31.36 GB: ~2.96 GB grown
DEFAULT_MARGIN = 3072 * MIB
#: the eviction penalty halves after each this long without another eviction
PENALTY_HALVING_S = 2 * 3600.0
#: A READING BELOW THIS SHARE OF THE MODEL'S LARGEST IS NOT "AT REST" — the
#: model was partly off the card (München, 2026-11-09: xe moves an
#: overcommitted card's buffers to host RAM, and LocalAI's `size_vram` then
#: reads what is left: Nemotron 31 of 4,404 MiB, MADLAD 2,671 of 8,005,
#: omnivoice 1,003 of 2,676, dar-legal-it 1,667 of 8,237). Counted at those,
#: the budget admitted 8 GB it did not have, and the "margin" read the
#: missing 12.5–17.5 GB as growth. Every reading taken with the model whole
#: was 0.75–1.0 of its largest (chat 0.79, qwen3-4b 0.91, bge-m3 0.90,
#: medcpt-query 0.75, rerank-bge 0.84, rerank-medcpt and medcpt-article
#: 1.0), every partial one 0.007–0.38: half separates them on both sides.
REST_FLOOR = 0.5            # = app/model_load.py REST_FLOOR, where the readings are taken
#: the margin's samples are kept under this basis; peaks of another (the
#: at-rest sizes before REST_FLOOR) are dropped when the file is read
MARGIN_BASIS = 2
#: the margin is the peak of the samples this recent (restarts included)
MARGIN_WINDOW_S = 6 * 3600.0
#: one peak kept per this many seconds of the window
MARGIN_BUCKET_S = 600.0
#: the budget's priority by role: (tier, place within the tier); a model of
#: several roles takes the first of them (`tier_of`)
TIER_OF: dict[str, tuple[int, int]] = {
    "chat": (1, 0), "reranker": (1, 1), "text to speech": (1, 2), "speech to text": (1, 3),
    "voice activity": (1, 4), "query encoder": (2, 0), "encoder": (3, 0), "translation": (4, 0)}
TIERS = {1: "chat, rerankers and speech", 2: "query encoders", 3: "document encoders",
         4: "translation"}


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
#: the budget's clock — the margin's window outlives the process (tests move it)
_wall: Callable[[], float] = time.time
#: the budget's memory as this process holds it (`_memory_now`), None until read
_memory: "Memory | None" = None
#: the models the event log last said are out of the budget
_out_said: frozenset[tuple[str, str]] = frozenset()
#: a write of the memory that failed, said once
_write_failed = False


def set_log(fn: Callable[[str, str], None]) -> None:
    """`fn(level, text)`; tests capture it."""
    global _say
    _say = fn


def reset() -> None:
    """Forget the last beat, every model's history and the budget's memory
    as this process holds it — the file stays, as a restart finds it (tests)."""
    global _last, _last_why, _last_switches, _memory, _out_said, _write_failed
    with _lock:
        _last, _last_why, _last_switches = {}, None, Switches()
        _track.clear()
        _memory, _out_said, _write_failed = None, frozenset(), False


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


# ---- the budget -------------------------------------------------------------------

@dataclass
class Memory:
    """What the budget keeps across restarts (`state/resident-budget.json`):
    the margin's peaks, the models' largest and at-rest sizes, the card's
    total, the eviction penalty."""
    #: bucket start (epoch s, a multiple of MARGIN_BUCKET_S) -> the largest margin sampled in it
    peaks: dict[int, int] = field(default_factory=dict)
    #: (server, model) -> the largest `size_vram` seen; 0: seen loaded, holding no VRAM
    sizes: dict[tuple[str, str], int] = field(default_factory=dict)
    #: the card's total bytes, as the probe last read it
    total: int | None = None
    #: (server, model) -> the fewest bytes of `size_vram` seen: the model at rest
    rest: dict[tuple[str, str], int] = field(default_factory=dict)
    #: the eviction penalty's bytes at `penalty_at` (epoch s, the last eviction)
    penalty: int = 0
    penalty_at: float | None = None

    def margin(self, now: float) -> tuple[int, bool]:
        """(the working margin, whether it was measured): the peak of the
        samples in the last MARGIN_WINDOW_S, else DEFAULT_MARGIN."""
        recent = [v for t, v in self.peaks.items() if t + MARGIN_BUCKET_S > now - MARGIN_WINDOW_S]
        return (max(recent), True) if recent else (DEFAULT_MARGIN, False)

    def penalty_now(self, now: float) -> int:
        """The eviction penalty, halved once per PENALTY_HALVING_S since the
        last eviction."""
        if not self.penalty or self.penalty_at is None:
            return 0
        halvings = int(max(0.0, now - self.penalty_at) // PENALTY_HALVING_S)
        return self.penalty >> halvings if halvings < 63 else 0

    def as_json(self) -> dict[str, Any]:
        return {"margin_basis": MARGIN_BASIS,
                "margin_peaks": {str(t): v for t, v in sorted(self.peaks.items())},
                "sizes": {f"{s}|{m}": v for (s, m), v in sorted(self.sizes.items())},
                "rest": {f"{s}|{m}": v for (s, m), v in sorted(self.rest.items())},
                "total": self.total, "penalty": self.penalty, "penalty_at": self.penalty_at}

    def copy(self) -> "Memory":
        return Memory(dict(self.peaks), dict(self.sizes), self.total, dict(self.rest), self.penalty,
                      self.penalty_at)


def memory_path() -> Path:
    from app import config
    return config.louise_root() / "state" / "resident-budget.json"


def read_memory(path: Path | None = None) -> Memory:
    """The file as the last beat wrote it; a missing or broken one is empty
    (nothing measured: the defaults), never an error."""
    try:
        raw = json.loads((path or memory_path()).read_bytes())
    except (OSError, ValueError):
        return Memory()
    out = Memory()
    if not isinstance(raw, dict):
        return out

    def count(v: Any) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
    # peaks sampled against another count of the models (before REST_FLOOR:
    # partial readings taken as at rest) measured that error, not growth
    for k, v in (raw.get("margin_peaks") or {}).items() if raw.get("margin_basis") == MARGIN_BASIS else ():
        if str(k).isdigit() and count(v) is not None:
            out.peaks[int(k)] = v
    for name, into in (("sizes", out.sizes), ("rest", out.rest)):
        for k, v in (raw.get(name) or {}).items():
            server, sep, model = str(k).partition("|")
            if sep and model and count(v) is not None:
                into[(server, model)] = v
    out.total = count(raw.get("total")) or None
    at = raw.get("penalty_at")
    if count(raw.get("penalty")) and isinstance(at, (int, float)) and not isinstance(at, bool):
        out.penalty, out.penalty_at = raw["penalty"], float(at)
    return out


def _write_memory(m: Memory) -> None:
    """Written whole and renamed; a failure is said once, and the budget
    goes on from what this process holds."""
    global _write_failed
    p = memory_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(".resident-budget.json.tmp")
        tmp.write_text(json.dumps(m.as_json(), sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        with _lock:
            tell, _write_failed = not _write_failed, True
        if tell:
            _say("warn", f"resident: the budget's memory is not written ({p}: {type(e).__name__}: "
                         f"{str(e)[:200]}); a restart starts its margin from the default")


def _memory_now() -> Memory:
    """This process's memory, read from the file the first time."""
    global _memory
    with _lock:
        if _memory is not None:
            return _memory
    got = read_memory()
    with _lock:
        if _memory is None:
            _memory = got
        return _memory


def tier_of(r: Resident) -> tuple[int, int]:
    """(tier, place within it) of the model's first role in the order."""
    return min((TIER_OF.get(role, (4, 9)) for role in r.roles), default=(4, 9))


def _tier_says(tier: int) -> str:
    return f"tier {tier} of {len(TIERS)}: {TIERS.get(tier, '?')}"


def _size(r: Resident, mem: Memory) -> int | None:
    """The bytes the model holds on the card at most, as far as anyone saw:
    this process's `/system` readings and the file's; 0 for one seen loaded
    holding none; None never seen."""
    from app import model_load
    got = [v for v in (mem.sizes.get(r.key), model_load.size_vram(r.server, r.model)) if v is not None]
    if got:
        return max(got)
    return 0 if model_load.seen_loaded(r.server, r.model) else None


def _rest_size(r: Resident, mem: Memory) -> int | None:
    """The bytes the model holds AT REST — the fewest any reading gave, as
    its load left it, before a request grew its buffers; one seen only at
    its largest (a file before at-rest sizes were kept) at that; 0 on the
    CPU; None never seen. What the budget counts."""
    from app import model_load
    largest = _size(r, mem)
    got = [v for v in (mem.rest.get(r.key), model_load.rest_vram(r.server, r.model)) if _whole(v, largest)]
    return min(got) if got else largest


def _whole(rest: int | None, largest: int | None) -> bool:
    """A reading the model gave WHOLE on the card: at least REST_FLOOR of
    the most it was seen holding (none known: taken as it is)."""
    return bool(rest) and (not largest or rest >= REST_FLOOR * largest)


def _last_used() -> dict[tuple[str, str], float]:
    """When each (server, model) last answered a request on this node
    (node_state `encoder_use`), read without creating or writing anything."""
    from app import node_state
    try:
        return node_state.peek(node_state.encoder_last_used, {}, error={}, table="encoder_use")
    except Exception:   # noqa: BLE001 — unread: no recency, the sizes order the tiers
        return {}


@dataclass(frozen=True)
class Placed:
    """One model of the set, in the budget's order."""
    resident: Resident
    rank: int                    # 1: the first placed
    tier: int
    size: int | None             # bytes at rest (0: no VRAM); None: never seen
    largest: int | None          # the most bytes seen (what the room check reads)
    counted: int                 # what the budget counts it at
    last_used: float | None      # epoch seconds of its last request on this node; None: none kept
    fits: bool
    why: str                     # in words, the budget included
    detail: str = ""             # out of the budget: why, the budget left out (the event log's line)


@dataclass(frozen=True)
class Plan:
    """The set in priority order against the budget; `line` None: the card
    was never read, and the budget decides nothing."""
    placed: tuple[Placed, ...]
    line: int | None
    threshold: float
    margin: int
    measured: bool
    median: int | None
    penalty: int = 0

    @property
    def budget(self) -> int | None:
        return None if self.line is None else self.line - self.margin - self.penalty

    @property
    def fitting(self) -> frozenset[tuple[str, str]]:
        return frozenset(p.resident.key for p in self.placed if p.fits)

    def says(self) -> str:
        if self.line is None:
            return "no budget: the card was never read, so every model of the set is preloaded as before"
        how = (f"measured over {MARGIN_WINDOW_S / 3600:.0f} h" if self.measured else
               "the default: nothing measured in the last "
               f"{MARGIN_WINDOW_S / 3600:.0f} h")
        pen = f" − {self.penalty // MIB:,} MiB for evictions" if self.penalty else ""
        return (f"the budget is {max(0, self.budget) // MIB:,} MiB (the {self.threshold:.2f} line "
                f"{self.line // MIB:,} MiB − margin {self.margin // MIB:,} MiB, {how}{pen})")


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(0, seconds) / 60:.0f} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} days"


def plan(want: Iterable[Resident], mem: Memory, *, threshold: float,
         used: dict[tuple[str, str], float], now: float) -> Plan:
    """THE BUDGET, filled: the set in priority order (`tier_of`; within a
    tier the most recently used first, then the never used, smaller first),
    each at its AT-REST size (never seen: the median of the set's known
    sizes, else nothing), placed first-fit under threshold × total − the
    margin − the eviction penalty. Pure: the caller reads the card, the
    threshold and the use."""
    want = list(want)
    sizes = {r.key: _rest_size(r, mem) for r in want}
    known = sorted(v for v in sizes.values() if v)
    median = int(statistics.median(known)) if known else None
    counted = {k: (v if v is not None else (median or 0)) for k, v in sizes.items()}

    def order(r: Resident):
        tier, place = tier_of(r)
        t = used.get(r.key)
        return (tier, place, 0 if t is not None else 1, -t if t is not None else counted[r.key], r.model,
                r.server)
    ranked = sorted(want, key=order)
    margin, measured = mem.margin(now)
    penalty = mem.penalty_now(now)
    line = None if not mem.total else int(threshold * mem.total)
    budget = None if line is None else line - margin - penalty
    uses: dict[int, list[float]] = {}
    for r in ranked:
        if used.get(r.key) is not None:
            uses.setdefault(tier_of(r)[0], []).append(used[r.key])
    taken, placed = 0, []
    head = Plan((), line, threshold, margin, measured, median, penalty)
    for rank, r in enumerate(ranked, 1):
        tier, size, need, t = tier_of(r)[0], sizes[r.key], counted[r.key], used.get(r.key)
        seen = ("never seen: counted at the set's median" if size is None and median else
                "never seen, nor any model of the set: counted at nothing until a load is seen"
                if size is None else "holds no VRAM" if size == 0 else "")
        detail = ""
        if budget is None:
            fits, why = True, "no budget: the card was never read"
        elif need == 0:
            fits, why = True, f"in the budget: costs the card nothing ({seen})"
        elif taken + need <= budget:
            taken += need
            fits, why = True, (f"in the budget: {taken // MIB:,} of {max(0, budget) // MIB:,} MiB taken "
                               f"with it" + (f" ({seen})" if seen else ""))
        else:
            tier_uses = uses.get(tier, [])
            when = ("never used here" if t is None else f"last used {_ago(now - t)} ago"
                    + (", the least recently used of its tier" if len(tier_uses) > 1 and t == min(tier_uses)
                       else ""))
            detail = (f"rank {rank} of {len(ranked)}, {_tier_says(tier)}, {when}; the models placed above "
                      f"it take {taken // MIB:,} MiB and it needs {need // MIB:,} MiB"
                      + (f" ({seen})" if seen else "")
                      + " — loaded at its first request, then left to the reclaimer's LRU")
            fits, why = False, f"not preloaded — {head.says()}; {detail}"
        placed.append(Placed(r, rank, tier, size, _size(r, mem), need, t, fits, why, detail))
    return Plan(tuple(placed), line, threshold, margin, measured, median, penalty)


def _plan_now(want: Iterable[Resident], mem: Memory | None = None) -> Plan:
    """`plan` over what the node knows now: its memory, LocalAI's line (the
    strictest of the set's servers), the use rows."""
    want = list(want)
    mem = mem or _memory_now()
    with _lock:
        mem = mem.copy()
    t = min((_threshold(s) for s in sorted({r.server for r in want})), default=DEFAULT_THRESHOLD) \
        if mem.total else DEFAULT_THRESHOLD
    return plan(want, mem, threshold=t, used=_last_used(), now=_wall())


def _held_now(servers: Iterable[str], mem: Memory) -> dict[tuple, int] | None:
    """Every model the set's runners hold, {(model, pid) or (server, model):
    its bytes AT REST} — the fewest of this reading, this process's and the
    file's — read fresh; None when one cannot say or gives no sizes."""
    from app import model_load
    out: dict[tuple, int] = {}
    for s in sorted(set(servers)):
        got = model_load.held_sizes(s, max_age_s=0.0)
        if got is None:
            return None
        for m, (pid, size) in got.items():
            k = (m, pid) if pid is not None else (s, m)
            largest = max([size] + [v for v in (mem.sizes.get((s, m)), model_load.size_vram(s, m)) if v])
            whole = [v for v in (size, mem.rest.get((s, m)), model_load.rest_vram(s, m)) if _whole(v, largest)]
            # a model partly off the card counts whole: what it will hold again once used
            rest = (min(whole) if whole else largest) if size else 0
            out[k] = max(rest, out.get(k, 0))
    return out


def _remember(want: list[Resident]) -> Memory:
    """The beat's part of the budget: the set's sizes as this process saw
    them merged into the memory (the largest and the fewest kept), the
    card's total, one sample of the margin — the card's count minus the
    at-rest sizes of what LocalAI lists loaded, read before and after it —
    unless this keeper's own load runs; peaks older than the window
    dropped; the file written when anything changed."""
    from app import model_load
    mem = _memory_now()
    servers = {r.server for r in want}
    with _lock:
        loading = _warm_thread is not None and _warm_thread.is_alive()
    card = sample = None
    if servers:
        with _lock:
            frozen = mem.copy()
        before = None if loading else _held_now(servers, frozen)
        card = _card()
        after = None if before is None or card is None else _held_now(servers, frozen)
        if before is not None and after is not None and card is not None:
            held = dict(before)
            for k, v in after.items():
                held[k] = max(v, held.get(k, 0))
            sample = max(0, card[1] - sum(held.values()))
    now = _wall()
    with _lock:
        before_json = json.dumps(mem.as_json(), sort_keys=True)
        for r in want:
            v = _size(r, mem)
            if v is not None and v > mem.sizes.get(r.key, -1):
                mem.sizes[r.key] = v
        for k in [k for k, v in mem.rest.items() if not _whole(v, mem.sizes.get(k))]:
            del mem.rest[k]                            # read while partly off the card: not at rest
        for r in want:
            largest = mem.sizes.get(r.key)
            v = model_load.rest_vram(r.server, r.model)
            if _whole(v, largest) and v < mem.rest.get(r.key, v + 1):
                mem.rest[r.key] = v
        if card is not None and card[0] != mem.total:
            mem.total = card[0]
        if sample is not None:
            b = int(now // MARGIN_BUCKET_S * MARGIN_BUCKET_S)
            if sample > mem.peaks.get(b, -1):
                mem.peaks[b] = sample
        for b in [b for b in mem.peaks if b + MARGIN_BUCKET_S <= now - MARGIN_WINDOW_S]:
            del mem.peaks[b]
        changed = json.dumps(mem.as_json(), sort_keys=True) != before_json
        snapshot = mem.copy()
    if changed:
        _write_memory(snapshot)
    return mem


def _say_budget(p: Plan) -> None:
    """The event log, once per change: the models that fell out of the
    budget, each with why, and the ones back in it."""
    global _out_said
    out = {pl.resident.key: pl for pl in p.placed if not pl.fits}
    with _lock:
        before = _out_said
        _out_said = frozenset(out)
    fell = [out[k] for k in out if k not in before]
    back = [pl for pl in p.placed if pl.fits and pl.resident.key in before]
    if fell:
        _say("info", f"resident: not preloaded — {p.says()}: "
                     + "; ".join(f"{pl.resident.model} — {pl.resident.says()} — {pl.detail}" for pl in fell))
    if back:
        _say("info", f"resident: back in the budget — {p.says()}: "
                     + ", ".join(f"{pl.resident.model} (rank {pl.rank})" for pl in back))


def _penalize(r: Resident, stayed_s: float) -> None:
    """THE FEEDBACK: a model this keeper preloaded was evicted `stayed_s`
    after its load — the reclaimer saw the card past its line, whatever a
    beat's sample missed. Its at-rest size is added to the penalty at once
    (what is left of it, halved per PENALTY_HALVING_S, plus this), kept in
    the file and said."""
    mem = _memory_now()
    now = _wall()
    with _lock:
        size = _rest_size(r, mem) or 0
        if not size:
            return
        mem.penalty, mem.penalty_at = mem.penalty_now(now) + size, now
        total, snapshot = mem.penalty, mem.copy()
    _write_memory(snapshot)
    _say("info", f"resident: the budget shrank by {size // MIB:,} MiB: {r.model} was evicted "
                 f"{stayed_s / 60:.0f} min after its load ({_tier_says(tier_of(r)[0])}) — the penalty "
                 f"on the margin is {total // MIB:,} MiB, halved after each "
                 f"{PENALTY_HALVING_S / 3600:.0f} h without another such eviction")


def _due(r: Resident, now: float) -> bool:
    """Whether `r`, found off the card, may be loaded now. A model this
    keeper loaded and finds off the card again within REEVICTED_WITHIN_S was
    evicted because space was claimed: it waits BACKOFF_FIRST_S, doubled at
    each repeat up to BACKOFF_CAP_S, and that is said — and the budget
    shrinks by its at-rest size (`_penalize`); one that stayed longer
    starts the count again."""
    said = evicted = None
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
                        f"{BACKOFF_CAP_S / 3600:.0f} h; {_tier_says(tier_of(r)[0])})")
                evicted = stayed
            else:
                t.strikes = 0
        due = now >= t.next_try
    if said:
        _say("info", said)
    if evicted is not None:
        _penalize(r, evicted)
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
    the budget measured and filled (`_remember`, `plan`), the models of it
    in the budget the runner does not list loaded — and not backing off —
    loaded behind (`_warm_all`, which checks each one's budget and room
    again). Pins nothing, unloads nothing. Returns what it did. Never raises
    past a model."""
    global _last, _last_why, _out_said
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
    on_time = {r.key for r in missing if _due(r, now)}   # an eviction shrinks the budget below
    p = None
    if want:
        p = _plan_now(want, _remember(want))
        _say_budget(p)
    else:
        with _lock:
            _out_said = frozenset()
    fits = p.fitting if p is not None else frozenset()
    rank = {pl.resident.key: pl.rank for pl in p.placed} if p is not None else {}
    wanted_now = sorted((r for r in missing if r.key in fits), key=lambda r: rank.get(r.key, 0))
    due = [r for r in wanted_now if r.key in on_time]
    warming = _start_warm(due, stop) if due else []
    return {"set": sorted(by_key), "warming": [r.key for r in warming],
            "backing_off": sorted(r.key for r in wanted_now if r.key not in on_time),
            "out_of_budget": sorted(k for k in by_key if k not in fits),
            "budget": None if p is None else p.budget,
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
                     f"claimed — each as the budget allows: " + "; ".join(f"{r.model} — {r.says()}"
                                                                          for r in added))
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
    app/model_load.py) — if it is still in the set, still not loaded, still
    in the budget (`plan`, made again before each load: the load before
    may have shown a size never seen), and the card has room for it under
    the reclaimer's line (`room`, read again before each load, since the one
    before changed it). `items` come in the budget's order, chat first
    (`beat`). The node's mode is asked again before each: a pause stops the
    loads at the next one."""
    for r in items:
        if stop() or not preloads()[0]:
            return
        with _lock:
            if r.key not in _last:
                continue                   # left the set meanwhile (`forget`, a beat)
            want = list(_last.values())
        if _loaded(r) is not False:
            continue                       # loaded meanwhile, or the runner cannot say
        try:
            p = _plan_now(want)
        except Exception:   # noqa: BLE001 — never the keeper: the beat's plan stands, the room check guards
            p = None
        if p is not None:
            _say_budget(p)
            if r.key not in p.fitting:
                continue                   # out of the budget now: loaded at its first request
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
    makes no other. Nothing is pinned to give back, nothing is unloaded; the
    budget's memory stays in its file for the next worker."""
    global _last, _out_said
    with _lock:
        _last = {}
        _track.clear()
        _out_said = frozenset()


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
    mode and whether it preloads, the budget — the line, the margin
    (measured over the window, or the default) and what is left — and every
    model of the set in the budget's order: its rank and tier, roles and
    collections, whether the runner lists it loaded, its at-rest and largest sizes and
    what the budget counts, whether it is in the budget and why, when it was
    last used, and whether LocalAI has it pinned and by whom (never this
    keeper: an embed batch's pin, or the operator's). The worker's file
    (`state/resident-budget.json`) read, nothing written: the same `plan`
    the worker fills."""
    from app import effective_config, model_load, node_state, platform_config
    sw = switches()
    state = node_state.peek_worker_state()
    ok, why = preloads(state)
    served, not_served = served_now()
    modes = {c.name: _mode_shown(effective_config, c) for c in served}
    want = wanted(served, embed_mode=lambda c: modes[c.name], sw=sw)
    holders = {(p["server"], p["model"]): sorted(p["holders"]) for p in model_pins.status()["pins"]}
    for r in want:
        model_load.loaded_models(r.server)       # this process's own reading: the sizes of what is loaded
    mem = read_memory()
    if not mem.total and want:
        card = _card()
        mem.total = card[0] if card else None
    p = _plan_now(want, mem)
    now = _wall()
    rows = []
    for pl in p.placed:
        r = pl.resident
        ids = model_load.loaded_models(r.server)
        rows.append({"model": r.model, "server": r.server, "roles": list(r.roles),
                     "collections": list(r.collections), "switch": r.switch,
                     "rank": pl.rank, "tier": pl.tier, "tier_says": TIERS.get(pl.tier),
                     "loaded": None if ids is None else r.model in ids,
                     "size_mib": None if pl.largest is None else pl.largest // MIB,
                     "rest_mib": None if pl.size is None else pl.size // MIB,
                     "counted_mib": pl.counted // MIB,
                     "in_budget": pl.fits, "why": pl.why,
                     "last_used": None if pl.last_used is None else
                     time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pl.last_used)),
                     "pinned": model_pins.read_pinned(r.server, r.model),
                     "holders": holders.get(r.key, [])})
    samples = sum(1 for t in mem.peaks if t + MARGIN_BUCKET_S > now - MARGIN_WINDOW_S)
    out: dict[str, Any] = {
        "file": str(platform_config.platform_path()),
        "rule": "operator, 2026-11-08: \"It applies to any collection, on demand or all embedding "
                "preloads the model. Chat, speech, rerankers, madlad are preloaded too, nothing is "
                "evicted only because it's idle, only if space is claimed\" — preloaded, not pinned: "
                "the reclaimer evicts the least-recently-used model when space is claimed; preloaded "
                "only within the budget (2026-11-08), which unloads nothing",
        "switches": {ASK: sw.ask, SPEECH: sw.speech}, "mode": state, "preloads": ok, "why": why,
        "budget": {"says": p.says(), "threshold": p.threshold,
                   "line_mib": None if p.line is None else p.line // MIB,
                   "margin_mib": p.margin // MIB,
                   "margin": (f"measured: the peak of {samples} sample period(s) of "
                              f"{MARGIN_BUCKET_S / 60:.0f} min in the last {MARGIN_WINDOW_S / 3600:.0f} h"
                              if p.measured else
                              f"the default {DEFAULT_MARGIN // MIB:,} MiB: nothing measured in the last "
                              f"{MARGIN_WINDOW_S / 3600:.0f} h"),
                   "penalty_mib": p.penalty // MIB,
                   "penalty": (f"{p.penalty // MIB:,} MiB for the reclaimer's evictions of preloaded models, "
                               f"the last at "
                               f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(mem.penalty_at or 0))}; "
                               f"halved after each {PENALTY_HALVING_S / 3600:.0f} h without another"
                               if p.penalty else "none: no preloaded model evicted lately"),
                   "budget_mib": None if p.budget is None else max(0, p.budget) // MIB,
                   "taken_mib": sum(pl.counted for pl in p.placed if pl.fits) // MIB,
                   "median_mib": None if p.median is None else p.median // MIB,
                   "memory": str(memory_path())},
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
