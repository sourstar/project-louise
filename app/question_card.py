"""THE CARD FOR A PERSON'S QUESTION (docs/notes/ask-card-workflow.md; the
operator, 2026-11-09, verbatim: "Embedding models can be evicted to serve
the chat pipeline: the ultimate goal of project louise is to provide
answers").

The claim (app/rage/dispatch.py `question`) says a question is open; this
module is what the card does about it, on both sides:

- THE WORKER (`on_question`, `watch`): the moment a question takes the
  claim, the corpus pins go (`model_pins.release_for_question`) and the
  DOCUMENT ENCODERS are unloaded — LocalAI's backend shutdown
  (`model_load.unload`), not the reclaimer's LRU, which chose MADLAD
  (München, 2026-11-09). Idle ones first, then busy ones; LocalAI may
  refuse a busy one, so each is asked again UNLOAD_TRIES times,
  UNLOAD_RETRY_S apart, and the tries stop when the question ends. A model
  still pinned after the release is a gate's or a pass's and is never
  touched (spec 7); a model the question itself uses — a query encoder, a
  reranker, chat, speech — is not a corpus encoder here, whatever else it
  also encodes. The batch a shutdown cuts short comes back to the embed
  stage as a 5xx or a dropped connection: the endpoint's state, a deferral
  (embeddings._endpoint_fault), never a document's error.

- THE WEB (`before_search`, `after_search`): ON MEASURED ROOM ONLY
  (München's measurement of a2aaf67, 2026-11-09). Before the search the
  text-to-speech model goes only if the query encoders cannot fit even with
  what the question does not use given back (spec 3). After it the answer's
  missing models are loaded behind, in the order the answer meets them —
  chat, the translation model (not for a reader who chose Speed), text to
  speech, the rerankers — and a query encoder goes only if they need its
  room, the largest first (spec 4).

THE OLDEST REAL USE GOES FIRST (operator, 2026-11-16: "some embedders have
not been used in a long time; those ones shall always be evincted instead of
the ones that are actively used, either for embedding of for querying";
app/model_evict.py). Before either spec's own victim — the voice before the
search, the largest query encoder after it — the room is made from the
loaded models the question does not use, last really used longest ago
first. The question's query encoders were used seconds ago, so by that rule
they are the last to go; the spec's order applies only where nothing used
longer ago can give the room. Where it changes spec 4 ("a query encoder goes
only if they need its room, the largest first"), the operator's rule wins.

AHEAD OF THE STAGE (`load_ahead`; operator, 2026-11-24: "I want the
preemptive model load to be implemented"). The moment a question is asked
every model it will meet is known — chat, the translation model, its
collections' query encoders, their rerankers, the voice — and until now each
was loaded when its stage reached it, one cold load after the other inside
the search (4–5 query encoders at 5–14 s each: 24–32 s of a search, München
2026-11-09) and the reranker's inside it too. Now they are loaded AHEAD, at
once, in the order the question meets them, each as soon as the card has
MEASURED room for it under the reclaimer's line. The room is made first,
once, from the loaded models the question does NOT use, the oldest real use
first — the rule above, for as much of the plan as they can give — and
NEVER from a model the question uses: what does not fit yet waits for the
room the worker is giving back, and nothing later goes before it. The
evictions among the question's own models stay where they were
(`before_search`, `after_search`). The mechanism is the one a stage uses, the warm request
(`model_load.ensure_loaded`): a stage that reaches a model being loaded
ahead waits on the same load, never a second one. When LocalAI can "prepare"
a parked model (docs/notes/models-beyond-the-card.md, step 2), this plan
calls that instead.

The keeper (app/resident.py `beat`) loads nothing while the claim is held,
so nothing undoes this mid-question; the claim lingers
dispatch.QUESTION_LINGER_S after the question's last step (spec 6), then
corpus work resumes and loads its own encoders, and the keeper preloads
the query encoders again.

STANDARD NOT ADOPTED, written down (CLAUDE.md, "do not reinvent the wheel"):
Kubernetes-style priority preemption (PriorityClass, a lower-priority pod
evicted for a higher one) is the named form of this. It would pay with
several model servers to schedule across; here one LocalAI on one card is
asked directly, through its own unload call.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

from app import model_load, model_pins

#: a busy encoder LocalAI refused to stop is asked again this many times …
UNLOAD_TRIES = 4
#: … this far apart (a batch on the card is 0.2–3 s, measured on wikidata)
UNLOAD_RETRY_S = 2.0
#: a model whose backend uses more CPU than this (percent) reads busy
BUSY_CPU_PERCENT = 5.0
#: how often the worker's watcher reads the claim
WATCH_EVERY_S = 1.0

#: the roles a question uses: a model with any of them is never a corpus
#: encoder here (app/resident.py TIER_OF)
QUESTION_ROLES = frozenset({"chat", "translation", "text to speech", "reranker", "speech to text",
                            "voice activity", "query encoder"})

_lock = threading.Lock()
_freeing: list[threading.Thread | None] = [None]
_log: list[Callable[[str, str], None] | None] = [None]


def set_log(fn: Callable[[str, str], None]) -> None:
    _log[0] = fn


def _say(level: str, text: str) -> None:
    fn = _log[0]
    if fn is not None:
        try:
            fn(level, text)
            return
        except Exception:   # noqa: BLE001 — a log line never fails the card
            pass
    print(f"[{level}] {text}", flush=True)


def _question_now() -> bool:
    try:
        from app.rage import dispatch
        return dispatch.interactive_active()
    except Exception:   # noqa: BLE001 — an unreadable claim holds nothing back
        return False


# ---- the worker ------------------------------------------------------------------

def corpus_encoders(want: Iterable[Any]) -> list[Any]:
    """The models of the resident set (app/resident.py `Resident`) that only
    encode documents: what a question takes the card back from."""
    return [r for r in want if "encoder" in r.roles and not (set(r.roles) & QUESTION_ROLES)]


def _loaded(server: str, model: str) -> bool | None:
    ids = model_load.loaded_models(server, max_age_s=0.0)
    return None if ids is None else model in ids


def _give_back(server: str, model: str) -> bool:
    """One corpus encoder gives the card back to the question: PARKED where
    LocalAI says it parks AND its run is admitted to (`model_evict._may_park`:
    its weights to host memory, back in the time of a copy when embedding
    resumes — docs/notes/parking-step-2.md), stopped otherwise, as before
    (`model_evict.give_back`). True when its room is free; a busy one is
    asked again by the caller."""
    from app import model_evict
    held = model_load.held_sizes(server) or {}
    _pid, size = held.get(model, (None, 0))
    parked = model in model_load.parked(server)
    says = model in model_load.parks(server)
    # PARKED ONLY AS A RUN ADMITTED TO PARK, as the evictor parks (the second
    # review, 2026-11-26: this path parked every corpus encoder that says it parks)
    admitted = says and not parked and model_evict._may_park(server, model)
    v = model_evict.Loaded(model, int(size or 0), None, parks=admitted, parked=parked,
                           unadmitted=says and not parked and not admitted)
    return model_evict.give_back(server, v)[0] != model_evict.KEPT


def free_card(want: Iterable[Any], *, question: Callable[[], bool] = _question_now,
              sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """Unload the corpus encoders of `want` that are on the card and not
    pinned, idle first (`model_load.activity`), each busy one asked again up
    to UNLOAD_TRIES times while the question lasts. Returns the models
    unloaded. Never raises."""
    todo = []
    for r in corpus_encoders(want):
        try:
            if _loaded(r.server, r.model) is not True:
                continue
            if r.model in model_load.parked(r.server):
                continue                 # parked: its weights are off the card already
            if model_pins.read_pinned(r.server, r.model):
                _say("info", f"question: {r.model} stays on the card — pinned by a gate or a pass")
                continue
        except Exception:   # noqa: BLE001 — unreadable: left alone
            continue
        todo.append(r)
    if not todo:
        return []
    busy: dict[str, float] = {}
    for server in {r.server for r in todo}:
        try:
            busy.update(model_load.activity(server) or {})
        except Exception:   # noqa: BLE001 — no activity read: the order is the set's
            pass
    todo.sort(key=lambda r: busy.get(r.model, 0.0) > BUSY_CPU_PERCENT)
    done: list[str] = []
    t0 = time.monotonic()
    for attempt in range(UNLOAD_TRIES):
        left = []
        for r in todo:
            if not question():
                return done
            try:
                if _give_back(r.server, r.model) or _loaded(r.server, r.model) is False:
                    done.append(r.model)
                else:
                    left.append(r)
            except Exception:   # noqa: BLE001 — asked again on the next try
                left.append(r)
        todo = left
        if not todo:
            break
        if attempt + 1 < UNLOAD_TRIES:
            sleep(UNLOAD_RETRY_S)
    if done:
        _say("info", f"question: a person's question holds the card; corpus encoder(s) unloaded "
                     f"in {time.monotonic() - t0:.1f} s: {', '.join(done)} — the batch cut short is "
                     f"deferred, its documents untouched; corpus work loads them again when the "
                     f"question ends")
    if todo:
        _say("warn", f"question: {', '.join(r.model for r in todo)} still busy after {UNLOAD_TRIES} "
                     f"tries; left to the reclaimer")
    return done


def on_question(want: Callable[[], Iterable[Any]] | None = None) -> None:
    """A question has taken the claim: the corpus pins go at once, and the
    corpus encoders are unloaded on a thread of their own (the caller may be
    a corpus thread about to stand aside, which must not wait on it). One
    unload pass at a time."""
    try:
        model_pins.release_for_question()
    except Exception:   # noqa: BLE001
        pass
    if want is None:
        from app import resident
        want = resident.current
    with _lock:
        t = _freeing[0]
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=lambda: free_card(list(want())), name="question-card", daemon=True)
        _freeing[0] = t
    t.start()


def watch(stop: Callable[[], bool], *, every: float = WATCH_EVERY_S,
          want: Callable[[], Iterable[Any]] | None = None,
          question: Callable[[], bool] = _question_now,
          sleep: Callable[[float], None] = time.sleep) -> None:
    """The worker's watcher: `on_question` on each question's first second,
    whether or not corpus work is running to stand aside (an idle encoder
    the keeper preloaded holds the card as much as a busy one)."""
    was = False
    while not stop():
        now = question()
        if now and not was:
            on_question(want)
        was = now
        sleep(every)


# ---- the web ---------------------------------------------------------------------

def _query_encoders(collections: Iterable[Any]) -> list[tuple[str, str]]:
    from app import collections as collections_mod, embeddings
    out: list[tuple[str, str]] = []
    for c in collections:
        try:
            q = embeddings._side(collections_mod.recipe_for(c), True)
        except Exception:   # noqa: BLE001 — no encoder declared
            continue
        if q.url and q.model:
            key = (model_load.server_of(q.url), q.model)
            if key not in out:
                out.append(key)
    return out


def _tts() -> tuple[str, str] | None:
    try:
        from app import voice
        url, model = voice.server(), voice.settings_or_none().tts_model
    except Exception:   # noqa: BLE001 — no speech configured
        return None
    return (model_load.server_of(url), model) if url and model else None


def _free(server: str) -> int | None:
    """Bytes under the reclaimer's line on the card now (negative: past it);
    None when the probe cannot say."""
    from app import resident
    card = resident._card()
    if card is None:
        return None
    total, used = card
    return int(resident._threshold(server) * total) - used


def _lru_room(server: str, need: int, protect: set, for_what: str) -> bool:
    """Whether `need` bytes were made to fit under the line by unloading the
    loaded models the question does not use (`protect` stays), the OLDEST
    REAL USE FIRST (app/model_evict.py; operator, 2026-11-16: "some embedders
    have not been used in a long time; those ones shall always be evincted
    instead of the ones that are actively used"). The question's own query
    encoders were used seconds ago: by this rule they are the last to go, so
    only where nothing used longer ago can give the room does the spec's
    own order (spec 3: the voice; spec 4: the largest query encoder) apply,
    as before. Never raises."""
    try:
        from app import model_evict
        return bool(model_evict.make_room(server, need=need, horizon=time.time(), for_what=for_what,
                                          protect=protect).fits)
    except Exception:   # noqa: BLE001 — the spec's order then
        return False


def _answer_models(collections: Iterable[Any], *, speed: bool
                   ) -> list[tuple[str, tuple[str, str], Callable[[], object]]]:
    """(role, (server, model), warm) of what the answer meets, in that order:
    chat, the translation model (not under Speed), text to speech, the
    rerankers of the question's collections."""
    from app import collections as collections_mod, config, llm, retrieval, translate
    out: list[tuple[str, tuple[str, str], Callable[[], object]]] = []
    if config.CHAT_URL and config.CHAT_MODEL:
        out.append(("chat", (model_load.server_of(config.CHAT_URL), config.CHAT_MODEL), llm.warm))
    if not speed and config.TRANSLATE_URL and config.TRANSLATE_MODEL:
        out.append(("translation", (model_load.server_of(config.TRANSLATE_URL), config.TRANSLATE_MODEL),
                    translate.warm))
    tts = _tts()
    if tts is not None:
        from app import resident
        out.append(("text to speech", tts, resident._speech_warm(tts[0], tts[1], "tts", "text to speech")))
    seen: set[tuple[str, str]] = {key for _r, key, _w in out}
    for c in collections:
        try:
            r = collections_mod.reranker_for(c)
        except Exception:   # noqa: BLE001 — none declared
            continue
        if r is not None and r.url and r.model:
            key = (model_load.server_of(r.url), r.model)
            if key not in seen:
                seen.add(key)
                out.append(("reranker", key, lambda r=r: retrieval._warm_reranker(r)))
    return out


# ---- ahead of the stage ----------------------------------------------------------

#: loads started ahead at once. LocalAI loads two DIFFERENT models at the
#: same time — its loader holds no lock across a load, only a wait per model
#: id (pkg/model/loader.go `loadModel` at v5.0.1: "Load the model (this can
#: take a long time, no lock held)") — each backend is a process of its own,
#: and most of a cold load is not the card's: 2.0 s of LocalAI's poll sleep
#: and 1.5–3.5 s of first run in 4.3–6.6 s (the node unloaded, 2026-11-24;
#: docs/notes/models-beyond-the-card.md §5.2). What several at once cost
#: each other on the card is München's to measure
AHEAD_AT_ONCE = 4
#: how often the card's room is read again while models wait for it: the
#: worker is unloading the corpus encoders as the question starts (its
#: watcher sees the question within WATCH_EVERY_S, an unload takes 0.1–0.2 s
#: and a busy encoder is asked again for UNLOAD_TRIES x UNLOAD_RETRY_S)
AHEAD_EVERY_S = 0.5
#: ... for this long; after it the worker's unloads are over and the room
#: is read at AHEAD_SLOW_S, so a wait costs reads by its need, not its length
AHEAD_FAST_S = 10.0
AHEAD_SLOW_S = 2.0
#: after this long nothing more is started ahead: the stages load their own
AHEAD_FOR_S = 60.0

#: every question's loads ahead in flight in this process -> the bytes each
#: is counted at: two questions asked together measure the same card
_ahead_flying: dict[tuple[str, str], int] = {}


class Ahead:
    """A question's loads ahead. `stop` ends the STARTING of new ones (the
    search is over: `after_search` decides from there); a load in flight
    finishes, and `join` waits for it. `started` names the models asked
    for, in order."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.loads: list[threading.Thread] = []
        self.started: list[str] = []

    def stop(self) -> None:
        self._stop.set()

    def stopped(self) -> bool:
        return self._stop.is_set()

    def wait(self, seconds: float) -> bool:
        return self._stop.wait(seconds)

    def join(self, seconds: float) -> None:
        """Wait, `seconds` at most in all, for the loads already started: a
        model on its way is not one to make room for a second time."""
        end = time.monotonic() + seconds
        for t in [self.thread, *list(self.loads)]:
            if t is not None and t is not threading.current_thread():
                t.join(max(0.0, end - time.monotonic()))


def _warm_query_encoder(key: tuple[str, str], collections: list[Any]) -> None:
    """The query side of the first of `collections` whose query encoder is
    `key`, loaded as a search loads it (`embeddings.warm_side`)."""
    from app import collections as collections_mod, embeddings
    for c in collections:
        try:
            q = embeddings._side(collections_mod.recipe_for(c), True)
        except Exception:   # noqa: BLE001 — no encoder declared
            continue
        if q.url and q.model and (model_load.server_of(q.url), q.model) == key:
            embeddings.warm_side(c, query=True, caller="query encoder, ahead")
            return


def _size(key: tuple[str, str]) -> int | None:
    """What a load of the model is counted at, as every cold load counts it
    (`model_evict.need_of`: the most it was seen holding, by this process
    or in the keeper's file; 0 on another card); None never seen."""
    try:
        from app import model_evict
        return model_evict.need_of(*key)
    except Exception:   # noqa: BLE001 — unknown
        return None


def _plan(collections: list[Any], *, speed: bool) -> list[tuple[str, tuple[str, str], Callable[[], object]]]:
    """(role, (server, model), warm) of every model the question meets, IN
    THE ORDER IT MEETS THEM: chat (it reads the query's language before the
    search, and writes the answer after it), the translation model (not under
    Speed), the query encoders, the rerankers, text to speech. Each once."""
    answer = _answer_models(collections, speed=speed)
    first = [x for x in answer if x[0] in ("chat", "translation")]
    encoders = [("query encoder", key, lambda key=key: _warm_query_encoder(key, collections))
                for key in _query_encoders(collections)]
    last = [x for x in answer if x[0] == "reranker"] + [x for x in answer if x[0] == "text to speech"]
    out: list[tuple[str, tuple[str, str], Callable[[], object]]] = []
    seen: set[tuple[str, str]] = set()
    for role, key, warm in first + encoders + last:
        if key not in seen:
            seen.add(key)
            out.append((role, key, warm))
    return out


def _warm_ahead(key: tuple[str, str], warm: Callable[[], object]) -> None:
    """One load ahead. Its room was measured before it started, so the cold
    load's own hook (`model_evict.before_load`: the oldest real use unloaded
    until the model fits, the question's own models included) is told so
    and unloads nothing."""
    try:
        from app import model_evict
        with model_evict.room_made():
            warm()
    except Exception:   # noqa: BLE001 — its stage loads it, and says why not
        pass
    finally:
        with _lock:
            _ahead_flying.pop(key, None)


def _room_ahead(todo: list[tuple[str, tuple[str, str], Callable[[], object]]],
                protect: set[tuple[str, str]]) -> None:
    """THE PLAN'S ROOM, MADE ONCE, before any load is in flight (the card's
    reading is then exact): from the loaded models the question does NOT
    use, the oldest real use first (`_lru_room`, the call `before_search`
    and `after_search` make), for the longest run of the plan, from its
    start, that they can give — the whole of it, else one model fewer, and
    so on. `make_room` unloads nothing when it cannot give all it is asked.
    Without it a card full of other questions' models gave a load ahead no
    room at all, and each stage's own cold load unloaded them one by one."""
    sizes: list[int] = []
    for _role, key, _warm in todo:
        size = _size(key)
        if size is None:
            break                             # never seen: the plan is counted up to it
        sizes.append(size)
    if not sizes:
        return
    server = todo[0][1][0]
    free = _free(server)
    if free is None:
        return
    for n in range(len(sizes), 0, -1):
        need = sum(sizes[:n])
        if need <= free:
            return                            # as far as this it fits already
        if _lru_room(server, need, protect, "the question's models, loaded ahead"):
            return


def load_ahead(collections: Iterable[Any], *, speed: bool = False, background: bool = True) -> Ahead:
    """THE QUESTION'S MODELS, LOADED AHEAD OF THE STAGE THAT NEEDS THEM
    (module docstring). Room first, once, from what the question does not
    use (`_room_ahead`). Then in the order of use, AHEAD_AT_ONCE at a time,
    each only when the card has measured room for it; a model that does not
    fit yet waits for the room the worker is giving back, and nothing later
    goes before it. Stops starting when the search ends (`Ahead.stop`), when
    every model is loaded, or after AHEAD_FOR_S. Where the card or a size
    cannot be read, nothing is loaded ahead: the stages load as before.
    Never raises."""
    cols = list(collections)
    ahead = Ahead()

    def run() -> None:
        try:
            todo = _plan(cols, speed=speed)
        except Exception:   # noqa: BLE001 — no plan: the stages load as before
            return
        protect = {key for _role, key, _warm in todo}
        mine: list[threading.Thread] = []
        t0 = time.monotonic()
        made = False
        while todo and not ahead.stopped() and time.monotonic() < t0 + AHEAD_FOR_S:
            try:
                server = todo[0][1][0]
                ids = model_load.loaded_models(server, max_age_s=0.0)
                if ids is None:
                    return                    # the runner cannot say what it holds
                todo = [x for x in todo if x[1][1] not in ids or x[1][0] != server]
                if todo and not made:
                    made = True
                    with _lock:
                        quiet = not _ahead_flying
                    if quiet:
                        _room_ahead(todo, protect)
                # A LOAD IN FLIGHT TAKES ITS ROOM AS IT GOES: counted whole
                # until it is done. Those in flight are noted BEFORE the card
                # is read — one that ends in between is then counted twice,
                # never not at all
                with _lock:
                    before = dict(_ahead_flying)
                free = _free(server)
            except Exception:   # noqa: BLE001
                return
            if free is None:
                return                        # the card cannot say
            room = free - sum(before.values())
            mine = [t for t in mine if t.is_alive()]
            begun: list[str] = []
            now: set[tuple[str, str]] = set()
            while todo and len(mine) < AHEAD_AT_ONCE:
                role, key, warm = todo[0]
                size = _size(key)
                if size is None:
                    return                    # never seen loaded: nothing to tell its room from
                with _lock:
                    if key in _ahead_flying:  # on its way for another question
                        todo.pop(0)
                        continue
                    # another question's loads started since the card was read
                    others = sum(v for k, v in _ahead_flying.items() if k not in before and k not in now)
                    if size > room - others:
                        break                 # in the order of use: it waits, and nothing goes before it
                    _ahead_flying[key] = size
                room -= size                  # this beat's own, in flight or already done
                now.add(key)
                t = threading.Thread(target=_warm_ahead, args=(key, warm), name=f"question-ahead-{key[1]}",
                                     daemon=True)
                mine.append(t)
                ahead.loads.append(t)
                todo.pop(0)
                ahead.started.append(key[1])
                begun.append(f"{key[1]} ({role})")
                t.start()
            if begun:
                _say("info", f"question: {', '.join(begun)} loading ahead of the stage that needs "
                             f"{'it' if len(begun) == 1 else 'them'}, in the room measured "
                             f"({max(0, free) // 2**20:,} MiB free under the line)")
            every = AHEAD_EVERY_S if time.monotonic() < t0 + AHEAD_FAST_S else AHEAD_SLOW_S
            if todo and ahead.wait(every):
                break

    if not background:
        run()
        return ahead
    ahead.thread = threading.Thread(target=run, name="question-ahead", daemon=True)
    ahead.thread.start()
    return ahead


def before_search(collections: Iterable[Any], *, speed: bool = False) -> str | None:
    """THE TEXT-TO-SPEECH MODEL GOES ONLY WHEN THE QUERY ENCODERS CANNOT FIT
    OTHERWISE (spec 3), counted on the card as it will be: the free space
    under the reclaimer's line PLUS what the question does not use — the
    corpus encoders the worker is unloading as the search starts, any other
    idle model. München, 2026-11-09, on a2aaf67: counted on the card as it
    was, it unloaded omnivoice at the start of every Ask, and the first
    sentence then waited 14 s for it. Returns the model unloaded, or None.
    Never raises."""
    try:
        cols = list(collections)
        # THE SEARCH'S OWN SET (München, 2026-11-09, on c26cb21): the chat
        # model reads the query's language restriction and, under Quality,
        # MADLAD translates the query BEFORE the query encoders embed it
        # (app/language_plan.py `plan`); left out of the count, MADLAD loaded
        # inside the search onto a full card and the reclaimer took chat or
        # a query encoder (search 41-46 s against 24-32 s)
        search_set = list(_query_encoders(cols))
        search_set += [key for role, key, _w in _answer_models(cols, speed=speed)
                       if role == "chat" or role == "translation"]
        missing = [(s, m) for s, m in search_set if _loaded(s, m) is False]
        if not missing:
            return None
        needs = 0
        for s, m in missing:
            size = model_load.size_vram(s, m)
            if size is None:
                return None                   # never seen loaded: nothing to tell from
            needs += size
        server = missing[0][0]
        free = _free(server)
        if free is None:
            return None
        used_by_question = set(_query_encoders(cols)) | {key for _r, key, _w in _answer_models(cols, speed=speed)}
        # THE OLDEST REAL USE GIVES THE ROOM FIRST (operator, 2026-11-16;
        # app/model_evict.py): what the question does not use, last used
        # longest ago first — before the voice, and before the reclaimer
        if _lru_room(server, needs, used_by_question, "the search's own models"):
            return None
        tts = _tts()
        if tts is None:
            return None
        held = model_load.held_sizes(server, max_age_s=0.0) or {}
        freeable = sum(b for m, (_pid, b) in held.items() if (server, m) not in used_by_question)
        if needs <= free + freeable or _loaded(*tts) is not True or model_pins.read_pinned(*tts):
            return None
        if model_load.unload(*tts):
            _say("info", f"question: {tts[1]} unloaded before the search — its query encoders need "
                         f"{needs // 2**20:,} MiB, {max(0, free) // 2**20:,} MiB free under the line and "
                         f"{freeable // 2**20:,} MiB held by models the question does not use; loaded "
                         f"again for the reading")
            return tts[1]
    except Exception:   # noqa: BLE001 — the search goes on as before
        return None
    return None


def after_search(collections: Iterable[Any], *, speed: bool = False,
                 background: bool = True, ahead: Ahead | None = None) -> threading.Thread | None:
    """The search is over: the answer's models on the card (spec 4), on a
    thread of its own so the results are not held. THE QUERY ENCODERS STAY
    unless the answer's missing models do not fit beside them — then the
    largest go first, only as many as the room needs (München, 2026-11-09,
    on a2aaf67: unloaded after every search, each Ask paid 4–5 cold query
    encoders, 5–14 s each, 24–32 s of its search; a second question within
    the five minutes paid them again). Where the room cannot be told (no
    probe, a size never seen) they go, as the spec first said. Returns the
    thread (None when `background` is False)."""
    cols = list(collections)
    if ahead is not None:
        ahead.stop()                # the search is over: from here this decides, with its evictions

    def run() -> None:
        if ahead is not None:
            # a model already on its way has its room: counted missing, it
            # would cost a query encoder that room a second time
            ahead.join(AHEAD_FOR_S)
        answer = _answer_models(cols, speed=speed)
        missing = [(role, key, warm) for role, key, warm in answer if _loaded(*key) is False]
        if missing:
            server = missing[0][1][0]
            sizes = [model_load.size_vram(*key) for _r, key, _w in missing]
            free = _free(server)
            need = None if free is None or any(z is None for z in sizes) else sum(sizes) - free
            if need is not None and need > 0 and _lru_room(
                    server, sum(sizes), {key for _r, key, _w in answer}, "the answer's models"):
                need = 0                      # made from the models used longest ago
            encoders = [(s, m) for s, m in _query_encoders(cols)
                        if _loaded(s, m) is True and not model_pins.read_pinned(s, m)]
            encoders.sort(key=lambda k: -(model_load.size_vram(*k) or 0))
            if need is not None and need > 0:
                # the smallest one that gives the room alone, so the most stay
                # for the next question; else the largest first
                one = [k for k in encoders if (model_load.size_vram(*k) or 0) >= need]
                if one:
                    encoders = [one[-1]]
            gone = []
            for s, m in encoders:
                if need is not None and need <= 0:
                    break
                try:
                    if model_load.unload(s, m):
                        gone.append(m)
                        if need is not None:
                            need -= model_load.size_vram(s, m) or 0
                except Exception:   # noqa: BLE001 — left to the reclaimer
                    pass
            if gone:
                _say("info", f"question: query encoder(s) {', '.join(gone)} unloaded after the search — "
                             f"the answer's {', '.join(k[1] for _r, k, _w in missing)} need the room")
        for _role, _key, warm in missing:
            try:
                warm()
            except Exception:   # noqa: BLE001 — loaded at its first request instead
                pass
    if not background:
        run()
        return None
    t = threading.Thread(target=run, name="question-after-search", daemon=True)
    t.start()
    return t
