"""Every model Ask uses preloaded on the card — A TRIAL (operator, 2026-11-08,
option A: "keep every model Ask uses resident on the card"; "The load all is
a test"), and its final rule the same day: "It applies to any collection, on
demand or all embedding preloads the model. Chat, speech, rerankers, madlad
are preloaded too, nothing is evicted only because it's idle, only if space
is claimed." Amended 2026-11-09 (operator, B): every searched collection's
QUERY encoder is preloaded whatever its embed — an `off` collection is still
searched — and its document encoder only where it embeds.

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
  else DEFAULT_THRESHOLD). Where it does not fit, THE ROOM IS MADE BY THE
  OLDEST REAL USE (operator, 2026-11-16: "some embedders have not been used
  in a long time; those ones shall always be evincted instead of the ones
  that are actively used"; app/model_evict.py): the unpinned models whose
  last REAL use (app/model_use.py — no warm, no check) is older than this
  model's own are unloaded, oldest first, until it fits; never one used
  more recently, so a preload never evicts what is in use (the MADLAD
  thrash of 2026-11-16: the reclaimer, counting this keeper's warms and the
  run check's texts as use, took MADLAD 9 times out of 10 minutes after its
  use, and this keeper loaded it back). Where even those would not make
  the room, nothing is unloaded and it waits, said once: "X stays
  unloaded: the card has room for it only after space is given back". A
  card or a setting that cannot be read decides nothing: the load is made.
  THIS CHANGES THE RULE OF 2026-11-08 ("preloaded, not pinned: the
  reclaimer evicts the least-recently-used model") where the reclaimer's
  LRU is not the true one: Louise chooses the victim, by true use, before
  the reclaimer has to; the reclaimer still acts on growth past its line.
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

    budget = threshold × total − the growth measured − the shared context budget − the eviction penalty

  TWO BUDGETS (operator, 2026-11-24): this one is for WEIGHTS, and the shared
  context budget is kept beside it (`shared_context`, CONTEXTS_AT_ONCE): a
  model loaded with `context_on_demand` counts here at its weights alone.

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
  nor for SETTLE_S after the node resumes (a benchmark window's exit empties
  and refills the card, 2026-11-23), nor where a runner gives no per-model
  sizes. Nothing measured in the
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
- A MODEL ON ANOTHER CARD COSTS THIS ONE NOTHING (docs/notes/stt-on-igpu.md,
  2026-11-11: speech to text on the processor's integrated Radeon, served by
  the same LocalAI). LocalAI's `size_vram` is the driver's per-process
  count on whatever card the backend holds, so an iGPU model's bytes would
  be counted against the B70's budget, and subtracted from the B70's count
  in the margin's sample. The GPU probe gives each process's pid as its own
  namespace numbers it (`ns_pid`, = `/system`'s backend pid): a model whose
  backend holds memory on another card and none on this one is remembered
  ON ANOTHER CARD (`Memory.elsewhere`, in the file) — counted at 0, left out
  of the margin's sum, never room-checked — until it is seen on this card.
  A probe that gives no `ns_pid` (one deploy behind) tells nothing apart:
  every model counts as before.
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
  reclaimer's LRU, as an `off` collection's document encoder does. Said once per
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
from typing import Any, Callable, Iterable, Mapping

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
#: THE WORKING MEMORY'S RESERVE (operator, 2026-11-23, B: "Whats left are
#: models and weights"): the budget counts the models at rest, their weights,
#: and never less than this is kept free under the line for what requests
#: grow (contexts made on demand, KV and compute buffers), however quiet the
#: margin's window was. A quiet 6 h measured a margin of 0, the budget filled
#: the card to the reclaimer's line, and the next burst evicted an idle model.
#: The size is the peak context held at once by the models running in the same
#: second, 3,262 MiB (docs/notes/shared-context.md §2, the card record of
#: 2026-11-10/11); a margin measured above it counts instead
CONTEXT_RESERVE = 3262 * MIB
#: TWO BUDGETS (operator, 2026-11-24: "I meant separate 'models and weights'
#: from the context, because we use a 'shared' context and it has to be
#: considered in a separated budget"). A model loaded with `context_on_demand`
#: makes its context per request and gives it back: the card's free memory is
#: the pool every such model draws on. So such a model counts in the WEIGHTS
#: budget at its weights alone, and the SHARED CONTEXT BUDGET is kept free
#: beside it: the contexts of the CONTEXTS_AT_ONCE largest such models of the
#: set, never less than CONTEXT_RESERVE. Two at once: the card record of
#: 2026-11-10/11 had two corpus encoders running in the same second 21.7 % of
#: the day and three or more 0.16 % (docs/notes/shared-context.md §2)
CONTEXTS_AT_ONCE = 2
#: an on-demand model idles at its WEIGHTS: 0.48, 0.57 and 0.47 of its running
#: size on the three measured (qwen3-embedding-4b 2,737 of 5,685 MiB,
#: nemotron-3-embed-1b 2,512 of 4,404, bge-m3 621 of 1,335; München, 11-12 and
#: 11-23), under REST_FLOOR by design. A reading under this share of the
#: largest is a model partly off the card, not its weights
ON_DEMAND_REST_FLOOR = 0.3
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
#: AFTER A RESUME THE CARD IS NOT YET LOUISE'S (2026-11-23): phase 4's exit
#: emptied the card, resumed the node, and reloaded the live set; 9 s after its
#: 35B container exited, the first beat read a margin of 16,759 MiB, and the
#: budget fell from 31,020 to 14,262 MiB for the window's 6 h, five encoders
#: out of it. No margin sample this long after the node turns from a mode that
#: preloads nothing to one that does (one bucket: the next one is its own)
SETTLE_S = 600.0
#: the margin is the peak of the samples this recent (restarts included)
MARGIN_WINDOW_S = 6 * 3600.0
#: one peak kept per this many seconds of the window
MARGIN_BUCKET_S = 600.0
#: the budget's priority by role: (tier, place within the tier); a model of
#: several roles takes the first of them (`tier_of`)
#: AT REST (docs/notes/ask-card-workflow.md spec 1, operator 2026-11-09): chat,
#: the translation model, text to speech, the rerankers, in that order; speech
#: to text and voice activity run on the CPU and cost the card nothing. The
#: translation model was the last tier (2026-11-08) and the reclaimer's LRU
#: then took it mid-question: 5.5–15.2 s cold loads per Italian Ask.
TIER_OF: dict[str, tuple[int, int]] = {
    "chat": (1, 0), "translation": (1, 1), "text to speech": (1, 2), "reranker": (1, 3),
    "speech to text": (1, 4), "voice activity": (1, 5), "query encoder": (2, 0), "encoder": (3, 0)}
TIERS = {1: "chat, translation, speech and rerankers", 2: "query encoders", 3: "document encoders"}


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
    global _last, _last_why, _last_switches, _memory, _out_said, _write_failed, _orphans_at, _pids_seen
    with _lock:
        _pids_seen = (frozenset(), frozenset())
        _last, _last_why, _last_switches = {}, None, Switches()
        _track.clear()
        _memory, _out_said, _write_failed = None, frozenset(), False
        _orphans_at = None
        _rested.clear()


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


def current() -> list[Resident]:
    """The set as the last beat resolved it (app/question_card.py frees the
    card of its document encoders when a question starts)."""
    with _lock:
        return list(_last.values())


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
            try:
                recipe = collections_mod.recipe_for(c)
            except Exception:   # noqa: BLE001 — no encoder declared: none to hold
                recipe = None
            if recipe is not None and getattr(recipe, "model", None):
                # EVERY SEARCHED COLLECTION'S QUERY ENCODER, whatever its embed
                # (operator, 2026-11-09, B: an `off` collection is still
                # searched, and each search of gutenberg paid qwen3-4b's 7.5 s
                # load); the document encoder only where it embeds
                q = embeddings._side(recipe, True)
                add(q.url, q.model, "query encoder", c.name,
                    lambda c=c, r=recipe: embeddings.warm_side(c, query=True, recipe=r))
                if mode in collections_mod.EMBEDDING_MODES:
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
    DOCUMENT encoder leaves the set now, not at the next beat, so a load
    behind the last beat does not make it — every model of the set only
    encoder roles want. Its query encoder stays: an `off` collection is
    still searched (operator, 2026-11-09, B), so a model that is anyone's
    query encoder (a symmetric space's one encoder included) is kept.
    Nothing is unloaded: it stays until space is claimed. A model another
    collection or another role still wants stays in the set. Returns the
    models dropped."""
    gone: list[str] = []
    with _lock:
        for key, r in list(_last.items()):
            if collection not in r.collections or not set(r.roles) <= ENCODER_ROLES \
                    or "query encoder" in r.roles:
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
    here = frozenset(p.ns_pid for p in d.processes if p.ns_pid is not None)
    there = frozenset(p.ns_pid for o in devices if o is not d for p in o.processes
                      if p.ns_pid is not None) - here
    global _pids_seen
    with _lock:
        _pids_seen = (here, there)
    return int(d.vram_total), int(d.used)


#: (backend pids on this card, backend pids on another card and not this
#: one), as the last `_card` read them — namespace pids, as `/system` gives
#: them; both empty where the probe gives none
_pids_seen: tuple[frozenset[int], frozenset[int]] = (frozenset(), frozenset())


def _elsewhere(r: "Resident") -> bool:
    """Whether `r` was last seen with its backend on another card."""
    mem = _memory_now()
    with _lock:
        return r.key in mem.elsewhere


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
    if size is None or _elsewhere(r):
        return None                     # on another card: nothing of this one to check
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
    #: (server, model) last seen with its backend on ANOTHER card than this
    #: one (the iGPU): it costs this card nothing (docs/notes/stt-on-igpu.md)
    elsewhere: set[tuple[str, str]] = field(default_factory=set)

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
                "total": self.total, "penalty": self.penalty, "penalty_at": self.penalty_at,
                "elsewhere": sorted(f"{s}|{m}" for s, m in self.elsewhere)}

    def copy(self) -> "Memory":
        return Memory(dict(self.peaks), dict(self.sizes), self.total, dict(self.rest), self.penalty,
                      self.penalty_at, set(self.elsewhere))


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
    for k in raw.get("elsewhere") or () if isinstance(raw.get("elsewhere"), list) else ():
        server, sep, model = str(k).partition("|")
        if sep and model:
            out.elsewhere.add((server, model))
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
    return min((TIER_OF.get(role, (3, 9)) for role in r.roles), default=(3, 9))


def _tier_says(tier: int) -> str:
    return f"tier {tier} of {len(TIERS)}: {TIERS.get(tier, '?')}"


def _size(r: Resident, mem: Memory) -> int | None:
    """The bytes the model holds on the card at most, as far as anyone saw:
    this process's `/system` readings and the file's; 0 for one seen loaded
    holding none; None never seen."""
    from app import model_load
    if r.key in mem.elsewhere:
        return 0                        # its bytes are on another card
    got = [v for v in (mem.sizes.get(r.key), model_load.size_vram(r.server, r.model)) if v is not None]
    if got:
        return max(got)
    return 0 if model_load.seen_loaded(r.server, r.model) else None


#: an on-demand model whose largest reading is within this share of its rest
#: was never seen working (its context adds ~45 % to qwen3-4b's 2,381 MiB)
ON_DEMAND_RUNNING_GROWTH = 0.05


def _rest_size(r: Resident, mem: Memory) -> int | None:
    """The bytes the model holds AT REST — the fewest any reading gave, as
    its load left it, before a request grew its buffers; one seen only at
    its largest (a file before at-rest sizes were kept) at that; 0 on the
    CPU; None never seen. What the budget counts."""
    from app import model_load
    largest = _size(r, mem)
    if r.key in mem.elsewhere:
        return 0
    if model_load.context_on_demand(r.server, r.model):
        # A model loaded with `context_on_demand` holds its weights and makes
        # its context per request (docs/notes/idle-context-brief.md §1.3). IT
        # COUNTS AT ITS WEIGHTS (operator, 2026-11-24, the two budgets above):
        # its context is the shared context budget's, kept free beside the
        # weights budget (`shared_context`), so the memory its next request
        # takes is spoken for there and never handed to another preload.
        # Until 2026-11-24 it counted at its running size (the review of
        # 2026-11-12): the three on the node were counted 5,554 MiB above what
        # they held, and that many MiB of weights stayed off the card.
        # ITS WEIGHTS ARE WHAT ITS PROCESS HELD AT REST (`_note_rested`), the
        # file's where this process has not seen it at rest. Never the fewest
        # reading of all (until 2026-11-24): a reading taken while a model
        # loads is under its weights and over the floor, and it stayed for
        # ever (the node: nemotron 2,229 MiB counted, 2,513 at rest).
        weights = _weights(r.server, r.model, mem, largest)
        return weights if weights else largest
    got = [v for v in (mem.rest.get(r.key), model_load.rest_vram(r.server, r.model)) if _whole(v, largest)]
    return min(got) if got else largest


def _whole(rest: int | None, largest: int | None, floor: float = REST_FLOOR) -> bool:
    """A reading the model gave WHOLE on the card: at least `floor` (REST_FLOOR;
    ON_DEMAND_REST_FLOOR for a model that idles at its weights) of the most
    it was seen holding (none known: taken as it is)."""
    return bool(rest) and (not largest or rest >= floor * largest)


#: AN ON-DEMAND MODEL IS AT REST when its backend process has held the same
#: bytes for this long (`model_load.steady_vram`) AND nobody on the node has
#: asked it anything for this long (`encoder_use`, written by the embeddings
#: client of every process). The bytes alone do not say it: a stream that
#: works without pause keeps its context from one request to the next
#: (app/rage/keep.py), and the model then holds its weights AND its context,
#: steadily. 10 s is past every keep (2 s at most), past the 5 s between two
#: writes of a model's last use, and past the parts of a load (the node,
#: 2026-11-24: weights in 0.2–0.8 s, the warm-up run in 1.5–3.5 s).
AT_REST_S = 10.0
#: (server, model) -> what its process held the LATEST time this process saw
#: it at rest (`_note_rested`). The latest, not the fewest: a wrong figure (a
#: model partly off the card; a context a request without a keep left held)
#: lasts until the next time it rests, never for ever. Read and written one
#: key at a time, WITHOUT `_lock`: `_rest_size` reads it under that lock.
_rested: dict[tuple[str, str], int] = {}


def _note_rested(keys: Iterable[tuple[str, str]], used: Mapping[tuple[str, str], float],
                 now: float) -> None:
    """Note what each of these models holds if it is at rest now (`AT_REST_S`):
    `used` the node's last request to each (epoch seconds), `now` the wall's."""
    from app import model_load
    for key in keys:
        got = model_load.steady_vram(*key)
        if got is None or got[1] < AT_REST_S:
            continue
        last = used.get(key)
        if last is not None and now - last < AT_REST_S:
            continue                       # asked a moment ago: its context may be live
        _rested[key] = got[0]


def _weights(server: str, model: str, mem: Memory, largest: int | None) -> int | None:
    """An on-demand model's weights on the card: what its backend process held
    the latest time this process saw it at rest (`_rested`), else what the
    file remembers; None when neither is a reading of the model whole."""
    for v in (_rested.get((server, model)), mem.rest.get((server, model))):
        if _whole(v, largest, ON_DEMAND_REST_FLOOR):
            return v
    return None


def _rest_floor(server: str, model: str) -> float:
    from app import model_load
    return ON_DEMAND_REST_FLOOR if model_load.context_on_demand(server, model) else REST_FLOOR


def _context_size(r: Resident, mem: Memory) -> int | None:
    """The context an on-demand model makes per request and gives back: the
    most it was seen holding less its weights. None for a model that keeps
    its context (it is in its own sizes), one on another card, and one never
    seen with its context — LocalAI's `size_vram` shows an on-demand model's
    weights alone (the node, 2026-11-23: bge-m3 651 MB idle and working), so
    only a reading from before the option, kept in the file, tells it."""
    from app import model_load
    if r.key in mem.elsewhere or not model_load.context_on_demand(r.server, r.model):
        return None
    largest, weights = _size(r, mem), _rest_size(r, mem)
    if not largest or not weights or largest <= weights * (1 + ON_DEMAND_RUNNING_GROWTH):
        return None
    return largest - weights


def shared_context(want: Iterable[Resident], mem: Memory,
                   floor: int = CONTEXT_RESERVE) -> tuple[int, tuple[tuple[str, int], ...]]:
    """THE SHARED CONTEXT BUDGET for the set, and the contexts it is made of:
    the CONTEXTS_AT_ONCE largest known contexts of its on-demand models,
    never less than `floor` (CONTEXT_RESERVE: a context never seen is held by
    it — the largest seen so far is 2,948 MiB)."""
    known = sorted(((c, r.model) for r in want if (c := _context_size(r, mem))), reverse=True)[:CONTEXTS_AT_ONCE]
    return max(int(floor), sum(c for c, _ in known)), tuple((m, c) for c, m in known)


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
    #: the shared context budget (`shared_context`), and the contexts it is made of
    reserve: int = 0
    contexts: tuple[tuple[str, int], ...] = ()

    @property
    def kept(self) -> int:
        """What is kept under the line beside the weights: the growth measured
        (the margin: what the models that keep their buffers grew by, and
        anything else on the card) AND the shared context budget."""
        return self.margin + self.reserve

    @property
    def budget(self) -> int | None:
        return None if self.line is None else self.line - self.kept - self.penalty

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
        return (f"the budget is {max(0, self.budget) // MIB:,} MiB for weights (the {self.threshold:.2f} "
                f"line {self.line // MIB:,} MiB − growth {self.margin // MIB:,} MiB, {how} − the shared "
                f"context budget {self.reserve // MIB:,} MiB, {self.context_says()}{pen})")

    def context_says(self) -> str:
        """What the shared context budget is made of, in words."""
        made = sum(c for _, c in self.contexts)
        if self.contexts and made >= self.reserve:
            return " + ".join(f"{m} {c // MIB:,}" for m, c in self.contexts)
        return ("its floor" + (f" (the contexts known, {made // MIB:,} MiB, are under it)" if self.contexts
                               else ": no on-demand context is known"))


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(0, seconds) / 60:.0f} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} days"


def plan(want: Iterable[Resident], mem: Memory, *, threshold: float,
         used: dict[tuple[str, str], float], now: float, reserved: int = 0,
         reserve: int | None = None) -> Plan:
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
    penalty = mem.penalty_now(now) + int(reserved)
    keep, contexts = shared_context(want, mem, CONTEXT_RESERVE if reserve is None else int(reserve))
    line = None if not mem.total else int(threshold * mem.total)
    budget = None if line is None else line - margin - keep - penalty
    uses: dict[int, list[float]] = {}
    for r in ranked:
        if used.get(r.key) is not None:
            uses.setdefault(tier_of(r)[0], []).append(used[r.key])
    taken, placed = 0, []
    head = Plan((), line, threshold, margin, measured, median, penalty, keep, contexts)
    for rank, r in enumerate(ranked, 1):
        tier, size, need, t = tier_of(r)[0], sizes[r.key], counted[r.key], used.get(r.key)
        seen = ("on another card: costs this one nothing" if r.key in mem.elsewhere else
                "never seen: counted at the set's median" if size is None and median else
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
    return Plan(tuple(placed), line, threshold, margin, measured, median, penalty, keep, contexts)


def _plan_now(want: Iterable[Resident], mem: Memory | None = None) -> Plan:
    """`plan` over what the node knows now: its memory, LocalAI's line (the
    strictest of the set's servers), the use rows — less what a running gate
    holds for its twin (`reserve`)."""
    want = list(want)
    mem = mem or _memory_now()
    with _lock:
        mem = mem.copy()
        # THE TWIN'S ROOM, held from the budget: what its gate unloaded is not
        # loaded back under it while the gate runs (app/gate_twin.py `make_room`)
        held = sum(_reserved.values())
    t = min((_threshold(s) for s in sorted({r.server for r in want})), default=DEFAULT_THRESHOLD) \
        if mem.total else DEFAULT_THRESHOLD
    return plan(want, mem, threshold=t, used=_last_used(), now=_wall(), reserved=held)


# ---- room held for a gate's twin (app/gate_twin.py) ---------------------------------

#: (server, twin) -> bytes a running gate holds on the card for its twin
_reserved: dict[tuple[str, str], int] = {}


def reserve(key: tuple[str, str], size: int) -> None:
    with _lock:
        _reserved[key] = int(size)


def unreserve(key: tuple[str, str]) -> None:
    with _lock:
        _reserved.pop(key, None)


def _held_now(servers: Iterable[str], mem: Memory,
              rested: Callable[[], None] | None = None) -> dict[tuple, int] | None:
    """Every model the set's runners hold, {(model, pid) or (server, model):
    (its bytes AT REST, its bytes in this reading, the context it makes on
    demand — 0 for one that keeps its own, or whose context was never seen)}
    — at rest: the fewest of this reading and what is known of it at rest —
    read fresh; None when one cannot say or gives no sizes. `rested` is called
    between the reading and the count: the models at rest in THIS reading
    are counted at what it shows (`_note_rested`)."""
    from app import model_load
    out: dict[tuple, tuple[int, int, int]] = {}
    read = {s: model_load.held_sizes(s, max_age_s=0.0) for s in sorted(set(servers))}
    if rested is not None and all(got is not None for got in read.values()):
        rested()
    for s, got in read.items():
        if got is None:
            return None
        for m, (pid, size) in got.items():
            k = (m, pid) if pid is not None else (s, m)
            largest = max([size] + [v for v in (mem.sizes.get((s, m)), model_load.size_vram(s, m)) if v])
            # an on-demand model at its weights, as `_rest_size` counts it: its
            # context is the shared budget's, taken out of the sample by the
            # caller (`_remember`), never counted in the model and in the margin
            floor = _rest_floor(s, m)
            known = ((_weights(s, m, mem, largest),) if floor != REST_FLOOR
                     else (mem.rest.get((s, m)), model_load.rest_vram(s, m)))
            whole = [v for v in (size, *known) if _whole(v, largest, floor)]
            # a model partly off the card counts whole: what it will hold again once used
            rest = (min(whole) if whole else largest) if size else 0
            context = (_context_size(Resident(s, m, (), ()), mem) or 0) if size else 0
            was = out.get(k, (0, 0, 0))
            out[k] = (max(rest, was[0]), max(int(size or 0), was[1]), max(context, was[2]))
    return out


def _settling(now: float | None = None) -> bool:
    """Whether the node RESUMED less than SETTLE_S ago: its last mode write
    turned a mode that preloads nothing into one that does (node_state's
    history; its clock is the wall's)."""
    from app import node_state
    hist = node_state.mode_history()
    if len(hist) < 2:
        return False
    last, prev = hist[-1], hist[-2]
    at = float(last.get("at") or 0)
    now = time.time() if now is None else now
    return (last.get("state") in PRELOADING_MODES and prev.get("state") not in PRELOADING_MODES
            and 0 <= now - at < SETTLE_S)


def _remember(want: list[Resident]) -> Memory:
    """The beat's part of the budget: the set's sizes as this process saw
    them merged into the memory (the largest and the fewest kept), the
    card's total, one sample of the margin — the card's count minus the
    at-rest sizes of what LocalAI lists loaded, read before and after it —
    unless this keeper's own load runs or the node resumed less than
    SETTLE_S ago; peaks older than the window dropped; the file written when
    anything changed."""
    from app import model_load
    mem = _memory_now()
    servers = {r.server for r in want}
    with _lock:
        loading = _warm_thread is not None and _warm_thread.is_alive()
    settling = _settling()
    card = sample = None
    moved: dict[tuple[str, str], bool] = {}
    if servers:
        with _lock:
            frozen = mem.copy()
        # which on-demand models are at rest in each reading: what they hold
        # then is their weights (`_weights`), noted BEFORE the reading is
        # counted, so the beat that first sees a model at rest already
        # counts it right
        used, at = _last_used(), _wall()
        on_demand = [r.key for r in want if _rest_floor(r.server, r.model) != REST_FLOOR]

        def rested() -> None:
            _note_rested(on_demand, used, at)
        before = None if loading or settling else _held_now(servers, frozen, rested)
        card = _card()
        with _lock:
            here, there = _pids_seen
        after = None if before is None or card is None else _held_now(servers, frozen, rested)
        if before is not None and after is not None and card is not None:
            held = dict(before)
            for k, v in after.items():
                was = held.get(k, (0, 0, 0))
                held[k] = (max(v[0], was[0]), max(v[1], was[1]), max(v[2], was[2]))
            # a model on another card: its bytes are not in this card's count
            mine = [v for k, v in held.items() if k[1] not in there]
            at_rest, read_now = sum(v[0] for v in mine), sum(v[1] for v in mine)
            # WHAT THE CARD HOLDS BEYOND LOCALAI'S OWN COUNT OF ITS MODELS is
            # taken for the contexts the loaded on-demand models have live at
            # this sample — as far as CONTEXTS_AT_ONCE of them can make —
            # and stays out of the growth: LocalAI counts such a model at its
            # weights, working or not (the node, 2026-11-23), and its context
            # has the shared budget apart, so counted here too a working
            # encoder's context would be kept twice. Anything past that is
            # growth, as before (a load in flight, a program outside LocalAI)
            beyond = max(0, card[1] - read_now)
            live = sum(sorted((v[2] for v in mine), reverse=True)[:CONTEXTS_AT_ONCE])
            sample = max(0, card[1] - at_rest - min(beyond, live))
        if card is not None:
            moved = _where(servers, here, there)
    now = _wall()
    with _lock:
        before_json = json.dumps(mem.as_json(), sort_keys=True)
        for key, other in moved.items():
            if other:
                mem.elsewhere.add(key)
            else:
                mem.elsewhere.discard(key)
        for r in want:
            if r.key in mem.elsewhere:
                continue                    # its readings are another card's
            v = _size(r, mem)
            if v is not None and v > mem.sizes.get(r.key, -1):
                mem.sizes[r.key] = v
        for k in [k for k, v in mem.rest.items() if not _whole(v, mem.sizes.get(k), _rest_floor(*k))]:
            del mem.rest[k]                            # read while partly off the card: not at rest
        for r in want:
            if r.key in mem.elsewhere:
                continue
            largest = mem.sizes.get(r.key)
            floor = _rest_floor(r.server, r.model)
            if floor != REST_FLOOR:
                # an on-demand model's weights: what its process held the
                # latest time it rested, REPLACING what the file had (a restart
                # counts it at its weights at once; a wrong figure lasts until
                # the next time it rests, never for ever)
                v = _rested.get(r.key)
                if _whole(v, largest, floor) and v != mem.rest.get(r.key):
                    mem.rest[r.key] = v
                continue
            v = model_load.rest_vram(r.server, r.model)
            if _whole(v, largest, floor) and v < mem.rest.get(r.key, v + 1):
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


def _where(servers: Iterable[str], here: frozenset[int], there: frozenset[int]) -> dict[tuple[str, str], bool]:
    """{(server, model): on another card} for every loaded model whose
    backend pid the probe placed — on another card and not this one (True),
    or on this one (False); a model the probe did not place is left out, and
    keeps what was last seen of it."""
    from app import model_load
    out: dict[tuple[str, str], bool] = {}
    for s in sorted(set(servers)):
        got = model_load.held_sizes(s) or {}
        for m, (pid, _size) in got.items():
            if pid in there:
                out[(model_load.server_of(s), m)] = True
            elif pid in here:
                out[(model_load.server_of(s), m)] = False
    return out


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


_orphans_at: float | None = None


def _question_now() -> bool:
    try:
        from app.rage import dispatch
        return dispatch.interactive_active()
    except Exception:   # noqa: BLE001 — an unreadable claim holds nothing back
        return False


def _release_orphan_pins(want: list[Resident], now: float) -> None:
    """Every ORPHAN_CHECK_S, the set's models pinned with no holder are
    unpinned (model_pins.release_orphans): preloaded, never pinned."""
    global _orphans_at
    from app import model_pins
    with _lock:
        if not want or (_orphans_at is not None and now - _orphans_at < model_pins.ORPHAN_CHECK_S):
            return
        _orphans_at = now
    try:
        model_pins.release_orphans([r.key for r in want])
    except Exception as e:   # noqa: BLE001 — a beat never raises past a model
        _say("error", f"resident: the orphan pin check failed ({type(e).__name__}: {e})")


def beat(served: Iterable[Any], *, embed_mode: Callable[[Any], str], state: str | None = None,
         stop: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """One beat of the worker's keeper: the set resolved, its changes said,
    the budget measured and filled (`_remember`, `plan`), the models of it
    in the budget the runner does not list loaded — and not backing off —
    loaded behind (`_warm_all`, which checks each one's budget and room
    again, and makes the room from the models used least recently, operator
    2026-11-16). Pins nothing; unloads only for a load's room. Returns what
    it did. Never raises
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
    _release_orphan_pins(want, now)
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
    # A PERSON'S QUESTION HOLDS THE CARD (docs/notes/ask-card-workflow.md):
    # nothing is loaded behind it — the question unloads the corpus encoders
    # and, after its search, its query encoders, and loads the answer's own
    # models itself (app/question_card.py); a beat that loaded them back
    # would undo it. The keeper resumes when the claim ends.
    question = _question_now()
    warming = _start_warm(due, stop) if due and not question else []
    return {"set": sorted(by_key), "warming": [r.key for r in warming], "question": question,
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
    before changed it) — where it is short, made by unloading the models
    whose last real use is older than this one's (`_make_room`,
    app/model_evict.py). `items` come in the budget's order, chat first
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
        made = None
        if got is not None and not got.fits:
            # THE OLDEST REAL USE GIVES WAY, AND ONLY TO A MORE RECENT ONE
            # (operator, 2026-11-16; app/model_evict.py): the room is made by
            # unloading the models last used before this one was — never one
            # used since, so a preload never evicts what is actively used
            made = _make_room(r, got)
        with _lock:
            t = _track.setdefault(r.key, _Track())
            short = got is not None and not got.fits and not (made is not None and made.fits)
            tell = short and not t.waiting
            t.waiting = short
        if short:
            if tell:
                _say("info", f"resident: {r.model} — {r.says()} — stays unloaded: the card has room for "
                             f"it only after space is given back ({got.says()})"
                             + (f"; {made.why}" if made is not None and made.why else ""))
            continue
        _say("info", f"resident: loading {r.model} — {r.says()} (not on the card"
                     + (f"; {got.says()}" if got is not None else "")
                     + (f"; room made: {', '.join(made.unloaded)} unloaded" if made is not None and made.unloaded
                        else "") + ")")
        t0 = time.monotonic()
        try:
            from app import model_evict, model_use
            # a preload is no use of the model (app/model_use.py), and its room
            # is made above: the cold-load hook does not make it again
            with model_use.not_work(), model_evict.room_made():
                r.warm()
        except Exception as e:   # noqa: BLE001 — a load never takes the keeper down
            _say("warn", f"resident: {r.model} — {r.says()} — not loaded after "
                         f"{time.monotonic() - t0:.1f} s ({type(e).__name__}: {str(e)[:200]}); "
                         f"tried again at the next beat")
            continue
        with _lock:
            _track.setdefault(r.key, _Track()).warmed_at = _now()


def _make_room(r: Resident, got: Room):
    """`model_evict.make_room` for the preload of `r`: horizon its own last
    real use (never used: nothing used gives way to it). Never raises."""
    from app import model_evict
    try:
        return model_evict.make_room(r.server, need=got.needs, horizon=_last_used().get(r.key),
                                     for_what=f"the preload of {r.model}", exclude=(r.model,))
    except Exception:   # noqa: BLE001 — nothing made: the room check's word stands
        return None


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
                "only within the budget (2026-11-08), which unloads nothing; every searched "
                "collection's query encoder, an `off` one's included (2026-11-09)",
        "switches": {ASK: sw.ask, SPEECH: sw.speech}, "mode": state, "preloads": ok, "why": why,
        "budget": {"says": p.says(), "threshold": p.threshold,
                   "line_mib": None if p.line is None else p.line // MIB,
                   "margin_mib": p.margin // MIB,
                   "margin": (f"measured: the peak of {samples} sample period(s) of "
                              f"{MARGIN_BUCKET_S / 60:.0f} min in the last {MARGIN_WINDOW_S / 3600:.0f} h"
                              if p.measured else
                              f"the default {DEFAULT_MARGIN // MIB:,} MiB: nothing measured in the last "
                              f"{MARGIN_WINDOW_S / 3600:.0f} h"),
                   "context_budget_mib": p.reserve // MIB,
                   "context_budget": (f"{p.reserve // MIB:,} MiB kept beside the weights for the shared "
                                      f"context the on-demand models draw on (operator, 2026-11-24): "
                                      f"{p.context_says()}; never under {CONTEXT_RESERVE // MIB:,} MiB"),
                   "contexts_mib": {m: c // MIB for m, c in p.contexts},
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
