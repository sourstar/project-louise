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
                if model_load.unload(r.server, r.model) or _loaded(r.server, r.model) is False:
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
        missing = [(s, m) for s, m in _query_encoders(cols) if _loaded(s, m) is False]
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
        tts = _tts()
        if free is None or tts is None:
            return None
        used_by_question = set(_query_encoders(cols)) | {key for _r, key, _w in _answer_models(cols, speed=speed)}
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
                 background: bool = True) -> threading.Thread | None:
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

    def run() -> None:
        answer = _answer_models(cols, speed=speed)
        missing = [(role, key, warm) for role, key, warm in answer if _loaded(*key) is False]
        if missing:
            server = missing[0][1][0]
            sizes = [model_load.size_vram(*key) for _r, key, _w in missing]
            free = _free(server)
            need = None if free is None or any(z is None for z in sizes) else sum(sizes) - free
            encoders = [(s, m) for s, m in _query_encoders(cols)
                        if _loaded(s, m) is True and not model_pins.read_pinned(s, m)]
            encoders.sort(key=lambda k: -(model_load.size_vram(*k) or 0))
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
