"""THE PHRASES THAT COVER THE WAIT (operator, 2026-11-26; docs/notes/
hands-free-voice.md §7, Decision 5A, decided):

  "a 'waiting music' phrase, likely a clip from a proper set, to cover the
   search time, something like 'let me check' … maybe adding something like
   'uhm', or something that shows that it's doing something"
  "when a query has been answered make one or two phrases for the next cycle
   (one or two so you always have 2 phrases ready, just in case), prepare
   the phrases in the main 5 languages and per voice. Each time you
   regenerate the used ones."

THE SET: phrase TEXTS per language in two kinds — SHORT, said the instant a
question is taken (under a second), and LONG, for a long wait (about two
seconds, where the "uhm" lives) — one file per language in
app/filler_phrases/, for the five main languages (the first five of app/web.py
RESULT_LANGUAGES). `load` checks it: both kinds, 4–6 texts each, none in
both, none that claims a result ("found it", "ecco", "voilà" …: `CLAIMS`),
each read as written (`speech.speakable`) and in its own file's language
(`speech.language`), so a reader who is not an admin hears it in the
synthetic voice of that language. `texts(lang)` gives them (the echo guard,
§4, knows what the node itself says).

THE POOL: per voice and language, ONE clip of each kind READY. A clip is
played once: reported used (`used`), it is deleted and its kind REMADE — a
NEW synthesis, never one of app/speech.py's kept clips (their key includes
the words, so a "fresh" clip of a text that comes back would be the old
one: `speech.synthesize_timed(keep=False)`), of a text drawn from the set
that is neither the text just used nor the other ready clip's.

WHICH VOICE: the one the answer would be read in for this reader and this
language — `speech.reader_voice`, the one resolution: an admin's chosen
recorded profile (the page's, the account's), else the node's `tts_voice`;
any other reader the node's synthetic voice of the language.

THE KEY: a pool is keyed on the voice's PHYSICAL identity — a profile's id
with its recording as the model server lists it (`voice_ping.clip_of`: its
size, its length, the profile's `updated_at`; LocalAI gives no hash), never
its display name — on the language, the speech model, the node's steps, and
the ENGINE that made the clips (`model_load.backend_of`, as the kept clips
since #937). A clip another engine made is never served: its pool is simply
not the one looked in, and the pool of this engine is made lazily. Where the
engine cannot be known (the model not loaded), nothing is listed — whatever
is kept could be another engine's — and the first clip made says which
engine answers. A clip listed under a known engine and fetched by its id
once the model was evicted is served; one whose engine is known to differ
is not, and goes.

WHERE: derived, re-creatable data, under LOUISE_CACHE_DIR through
app/derived.py (invariant 6). No fast drive named: nothing is kept, nothing
offered — the page's tone covers the wait, as for a pool that is short.

WHEN, MADE: (a) LAZILY, when `GET /ask/voice/fillers` is asked for a voice
and language whose pool is short — the request answers at once with what
is ready, possibly nothing; (b) after a report of used clips, the used kinds.
ONE worker thread a process (the web's), one synthesis at a time, at most
one job per pool queued. Before each synthesis it waits, bounded by
DEFER_MAX_S, until this process has run no step of a question for QUIET_S
(`dispatch.quiet_s`): never during a search, an answer being written or a
sentence read aloud. A job still deferred at the bound is dropped, not
failed: the next ask asks again. A synthesis that fails is one warn line
under `models` and is tried again at the next ask, RETRY_AFTER_S later at the
earliest: never a loop. The synthesis is speech's own (`synthesize_timed`):
the model pinned as a reading pins it, the question's claim held while it
runs, as for any sentence.

THE STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"):
`concurrent.futures.ThreadPoolExecutor(max_workers=1)` is the named
one-at-a-time worker; it is not used because its threads are joined at
interpreter exit, and a job waiting for the card to be quiet would hold a
SIGTERM for up to DEFER_MAX_S. One daemon thread draining a queue and ending
when idle — app/model_pins.py's log writer — is the pattern used.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import re
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import config

#: the two kinds, in the order they are played (the short at once, the long at ~7 s)
KINDS = ("short", "long")
#: the five main languages: the first five of app/web.py RESULT_LANGUAGES
LANGUAGES = ("en", "de", "fr", "it", "es")
#: the set, one file per language
SET_DIR = Path(__file__).with_name("filler_phrases")
#: how many texts a kind holds: enough for a fresh draw, few enough to hear each (M5)
PER_KIND = (4, 6)
#: a SHORT text is under a second spoken, a LONG one about two: words at most
MOST_WORDS = {"short": 3, "long": 10}
#: WHAT CLAIMS A RESULT BEFORE THERE IS ONE, as the start of a word (casefolded):
#: none may appear in any text of the set, in any language
CLAIMS = {
    "en": ("found", "here it is", "here is", "here's", "there it is", "got it", "done", "ready"),
    "de": ("gefunden", "hier ist", "hier sind", "da ist", "fertig"),
    "fr": ("trouvé", "voilà", "voici", "prêt", "c'est fait"),
    "it": ("trovat", "ecco", "fatto", "pronto"),
    "es": ("encontr", "aquí está", "aquí tienes", "listo", "hecho"),
}
#: the derived kind the clips live under (app/derived.py)
DERIVED_KIND = "speech-fillers"
#: the route a clip is fetched from (app/web.py)
ROUTE = "/ask/voice/fillers"
#: a clip's id: its pool's 16 hex and its own 16
_ID = re.compile(r"[0-9a-f]{32}")
#: a synthesis starts once this process has run no step of a question for this long
QUIET_S = 5.0
#: a job waits for that at most this long, then is dropped (the next ask asks again)
DEFER_MAX_S = 600.0
#: how often a waiting job looks again
POLL_S = 1.0
#: a pool whose synthesis failed is not asked again before this
RETRY_AFTER_S = 60.0
#: the worker ends after this long with nothing to do
IDLE_EXIT_S = 5.0
#: at most this many ids in one report
MOST_IDS = 16


class SetError(ValueError):
    """The set of phrases does not read, or breaks one of its rules."""


@dataclass(frozen=True)
class Want:
    """One pool asked for: the model server, the speech model, the voice
    (None: the model's own) and the language."""
    url: str
    model: str
    voice: str | None
    lang: str


# ---- the set ---------------------------------------------------------------------------------

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


def _check(lang: str, kind: str, text: str, where: Path) -> None:
    from app import speech
    said = f"{where}: {kind} {text!r}"
    if not text.strip():
        raise SetError(f"{where}: an empty {kind} text")
    if speech.speakable(text) != text:
        raise SetError(f"{said} is not read as written (brackets, markdown, spaces): "
                       f"the model would say {speech.speakable(text)!r}")
    if speech.language(text) not in (None, lang):
        raise SetError(f"{said} reads as {speech.language(text)}, not {lang}: a reader who is not an admin "
                       f"would hear it in another language's voice")
    words = len(_WORD.findall(text))
    if words > MOST_WORDS[kind]:
        raise SetError(f"{said} has {words} words, a {kind} one at most {MOST_WORDS[kind]}")
    low = text.casefold()
    for claim in (c for cs in CLAIMS.values() for c in cs):
        if re.search(r"(?<!\w)" + re.escape(claim), low):
            raise SetError(f"{said} claims a result before there is one ({claim!r})")


def load(directory: Path = SET_DIR) -> dict[str, dict[str, tuple[str, ...]]]:
    """The set, `{lang: {"short": (texts), "long": (texts)}}`, for every one
    of LANGUAGES, checked (module docstring). Raises SetError."""
    out: dict[str, dict[str, tuple[str, ...]]] = {}
    for lang in LANGUAGES:
        path = Path(directory) / f"{lang}.toml"
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
            raise SetError(f"{path} does not read ({e})") from None
        if set(raw) != set(KINDS):
            raise SetError(f"{path}: the kinds are {', '.join(KINDS)}, not {', '.join(sorted(raw)) or 'none'}")
        kinds: dict[str, tuple[str, ...]] = {}
        for kind in KINDS:
            texts = raw[kind]
            if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                raise SetError(f"{path}: {kind} is a list of texts")
            if not PER_KIND[0] <= len(texts) <= PER_KIND[1]:
                raise SetError(f"{path}: {kind} has {len(texts)} texts, from {PER_KIND[0]} to {PER_KIND[1]}")
            if len(set(texts)) != len(texts):
                raise SetError(f"{path}: {kind} names a text twice")
            for t in texts:
                _check(lang, kind, t, path)
            kinds[kind] = tuple(texts)
        both = set(kinds["short"]) & set(kinds["long"])
        if both:
            raise SetError(f"{path}: {sorted(both)} is both short and long")
        out[lang] = kinds
    return out


_set: dict[str, dict[str, tuple[str, ...]]] | None = None
_set_lock = threading.Lock()


def phrases() -> dict[str, dict[str, tuple[str, ...]]]:
    """The repository's set, read and checked once a process."""
    global _set
    with _set_lock:
        if _set is None:
            _set = load()
        return _set


def texts(lang: Any, kind: str | None = None) -> tuple[str, ...]:
    """Every text of the set in `lang` (its primary subtag), of `kind` or of
    both, short first; () for a language the set does not hold. What the
    node itself says while a question is searched: the echo guard's to know."""
    from app import speech
    got = phrases().get(speech.primary(lang) or "")
    if got is None:
        return ()
    return tuple(t for k in KINDS if kind in (None, k) for t in got[k])


# ---- the pools -------------------------------------------------------------------------------

def _root() -> Path | None:
    """Where the pools live, or None: no fast drive is named (invariant 6)."""
    from app import derived
    return derived.place(DERIVED_KIND, Path(config.louise_root()))


def voice_identity(voice: str | None, held: list[dict]) -> str:
    """What a voice IS, physically: a profile the model server holds by its
    id AND its recording (`voice_ping.clip_of`: size, length, `updated_at`),
    so a clip replaced under the same id is another voice; a model's own
    named voice by that name; none, the model's own."""
    from app import voice_ping
    if not voice:
        return "(the model's own voice)"
    for p in held:
        if p.get("voice") == voice:
            return f"{voice}|{voice_ping.clip_of(p)}"
    return str(voice)


def opaque(identity: str) -> str:
    """A voice's identity as the page is given it: 16 hex, nothing it can read."""
    return hashlib.blake2b(identity.encode("utf-8"), digest_size=8).hexdigest()


def pool_key(want: Want, identity: str, engine: str, steps: int | None) -> str:
    """The pool's name: 16 hex of everything a clip of it is made from."""
    return hashlib.sha256(json.dumps({"url": want.url, "model": want.model, "voice": identity,
                                      "lang": want.lang, "engine": engine, "steps": steps},
                                     sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _steps(node: Any) -> int | None:
    from app import platform_config
    try:
        return platform_config.tts_steps(node.tts_steps)
    except Exception:   # noqa: BLE001 — a setting that does not read: speech refuses it the same way
        return None


USED = "used.json"
_lock = threading.Lock()


#: a clip's audio is `<id>.<ext>`, the ext its content type's subtype
_EXT = re.compile(r"[a-z0-9+-]{1,16}")


def _meta(path: Path) -> dict | None:
    """A clip's JSON, where it names this very clip and its audio is there."""
    try:
        got = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or got.get("id") != path.stem or not _EXT.fullmatch(str(got.get("ext"))):
        return None
    return got if (path.parent / f"{got['id']}.{got['ext']}").is_file() else None


def _clips(pool: Path) -> dict[str, dict]:
    """The ready clip of each kind in `pool`: a clip is ready once its JSON
    is written beside its audio; of two of one kind, the older."""
    out: dict[str, dict] = {}
    try:
        names = sorted(p for p in pool.iterdir() if p.suffix == ".json" and _ID.fullmatch(p.stem))
    except OSError:
        return out
    for p in names:
        m = _meta(p)
        if m is None or m.get("kind") not in KINDS:
            continue
        have = out.get(m["kind"])
        if have is None or float(m.get("made") or 0) < float(have.get("made") or 0):
            out[m["kind"]] = m
    return out


def _used(pool: Path) -> dict[str, str]:
    try:
        got = json.loads((pool / USED).read_bytes())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in got.items() if k in KINDS and isinstance(v, str)} if isinstance(got, dict) else {}


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _remove(pool: Path, meta: dict) -> None:
    (pool / f"{meta['id']}.json").unlink(missing_ok=True)
    (pool / f"{meta['id']}.{meta['ext']}").unlink(missing_ok=True)


def _avoid(pool: Path | None, kind: str, have: dict[str, dict]) -> set[str]:
    """The texts a new clip of `kind` may not say: the one just used, and
    every ready clip's."""
    used = _used(pool).get(kind) if pool is not None else None
    return {m["text"] for m in have.values()} | ({used} if used else set())


_rng = random.Random()


def _draw(lang: str, kind: str, avoid: set[str]) -> str:
    choices = [t for t in phrases()[lang][kind] if t not in avoid]
    return _rng.choice(choices or list(phrases()[lang][kind]))


def _may_hear(meta: dict, admin: bool) -> bool:
    """A clip in a recorded voice is an admin's only: any other reader hears
    a clip only in the voice they are read to in, for its language."""
    if admin:
        return True
    from app import speech, voice as node_voice
    node = node_voice.settings_or_none()
    held = speech.profiles(str(meta.get("url") or node_voice.server()))
    return speech.reader_voice(meta.get("lang"), held, node, admin=False) == (True, meta.get("voice"))


def ready(lang: Any, *, asked: Any = None, account_voice: str | None = None,
          admin: bool = True) -> dict[str, Any]:
    """`GET /ask/voice/fillers?lang=`: `{"lang", "voice", "ready": [{"id",
    "kind", "url"}]}` — the clips ready in the voice this reader's answer
    would be read in (`speech.reader_voice`) for `lang`, short first; `voice`
    that voice as an opaque id, null where nothing is offered (a language the
    set does not hold, no speech model, no voice this reader may hear). A pool
    short of a kind is made in the background (`schedule`); this answers at
    once with what is there."""
    from app import model_load, speech, voice as node_voice
    code = speech.primary(lang) or ""
    out: dict[str, Any] = {"lang": code, "voice": None, "ready": []}
    if code not in LANGUAGES:
        return out
    node = node_voice.settings_or_none()
    if not node.tts_model:
        return out
    url = node_voice.server()
    held = speech.profiles(url)
    read, voice = speech.reader_voice(code, held, node, asked=asked, account_voice=account_voice, admin=admin)
    if not read:
        return out
    identity = voice_identity(voice, held)
    out["voice"] = opaque(identity)
    root = _root()
    if root is None:
        return out
    want = Want(url, node.tts_model, voice, code)
    engine = model_load.backend_of(url, node.tts_model)
    have: dict[str, dict] = {}
    if engine:
        with _lock:
            have = _clips(root / pool_key(want, identity, engine, _steps(node)))
        out["ready"] = [{"id": have[k]["id"], "kind": k, "url": f"{ROUTE}/{have[k]['id']}"}
                        for k in KINDS if k in have]
    if len(have) < len(KINDS):
        schedule(want)
    return out


def _find(clip_id: Any) -> tuple[Path, dict] | None:
    if not isinstance(clip_id, str) or not _ID.fullmatch(clip_id):
        return None
    root = _root()
    if root is None:
        return None
    pool = root / clip_id[:16]
    meta = _meta(pool / f"{clip_id}.json")
    return None if meta is None else (pool, meta)


def clip(clip_id: Any, *, admin: bool = True) -> tuple[bytes, str] | None:
    """`GET /ask/voice/fillers/{id}`: (audio, content type), or None — no
    such clip, one this reader may not hear, one the node's speech model no
    longer makes, or one whose engine is known to be another (that one goes)."""
    from app import model_load, voice as node_voice
    found = _find(clip_id)
    if found is None:
        return None
    pool, meta = found
    node = node_voice.settings_or_none()
    if meta.get("url") != node_voice.server() or meta.get("model") != node.tts_model or not _may_hear(meta, admin):
        return None
    engine = model_load.backend_of(meta["url"], meta["model"])
    if engine and engine != meta.get("engine"):
        with _lock:
            _remove(pool, meta)
        return None
    try:
        return (pool / f"{meta['id']}.{meta['ext']}").read_bytes(), str(meta.get("ctype") or "audio/wav")
    except OSError:
        return None


def used(ids: list[Any], *, admin: bool = True) -> int:
    """`POST /ask/voice/fillers/used`: each clip played is deleted, its text
    noted as the one just used, and its kind remade in the background.
    Returns how many went; an id unknown, or of a clip this reader may not
    hear, is passed over."""
    n = 0
    for clip_id in list(ids)[:MOST_IDS]:
        found = _find(clip_id)
        if found is None or not _may_hear(found[1], admin):
            continue
        pool, meta = found
        with _lock:
            try:
                _remove(pool, meta)
                was = _used(pool)
                was[meta["kind"]] = meta["text"]
                _write_atomic(pool / USED, json.dumps(was, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            except OSError:
                continue                         # a fast drive that cannot be written: the clip stays
        n += 1
        schedule(Want(meta["url"], meta["model"], meta.get("voice"), meta["lang"]))
    return n


# ---- the worker ------------------------------------------------------------------------------

_jobs: "queue.Queue[Want]" = queue.Queue()
_pending: set[Want] = set()
_failed: dict[Want, float] = {}
_worker: threading.Thread | None = None
_stop = threading.Event()


def _say(level: str, text: str) -> None:
    from app import model_pins
    model_pins.node_event(level, text)


def schedule(want: Want) -> bool:
    """`want`'s pool made up in the background; False when it is already
    queued, or failed less than RETRY_AFTER_S ago. Never waits."""
    global _worker
    with _lock:
        if want in _pending or time.monotonic() - _failed.get(want, -1e18) < RETRY_AFTER_S:
            return False
        _pending.add(want)
        _jobs.put(want)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, name="speech-fillers", daemon=True)
            _worker.start()
    return True


def _drain() -> None:
    """One job after the other; ends once idle IDLE_EXIT_S, or at a stop."""
    global _worker
    idle = 0.0
    while not _stop.is_set():
        try:
            want = _jobs.get(timeout=0.25)
        except queue.Empty:
            idle += 0.25
            if idle < IDLE_EXIT_S:
                continue
            with _lock:
                if _jobs.empty():
                    _worker = None
                    return
            idle = 0.0
            continue
        idle = 0.0
        # off the queued set before it runs: a report while it runs queues the pool again
        with _lock:
            _pending.discard(want)
        try:
            _fill(want)
        except Exception as e:   # noqa: BLE001 — a job never ends the worker
            _fail(want, f"{type(e).__name__}: {e}")
        finally:
            _jobs.task_done()
    with _lock:
        if _worker is threading.current_thread():
            _worker = None


def _fail(want: Want, why: str) -> None:
    with _lock:
        _failed[want] = time.monotonic()
    _say("warn", f"speech fillers: {_voice_said(want)} ({want.lang}) not made — {why}; asked again at the "
                 f"next ask, {RETRY_AFTER_S:.0f} s from now at the earliest")


def _voice_said(want: Want) -> str:
    from app import speech
    if not want.voice:
        return "the model's own voice"
    for p in speech.profiles(want.url):
        if p.get("voice") == want.voice and p.get("name"):
            return str(p["name"])
    return want.voice


def _quiet() -> bool:
    """True once this process has run no step of a question for QUIET_S;
    False at DEFER_MAX_S or at a stop: the job is deferred."""
    from app.rage import dispatch
    deadline = time.monotonic() + DEFER_MAX_S
    while True:
        q = dispatch.quiet_s()
        if q >= QUIET_S:
            return True
        now = time.monotonic()
        if now >= deadline or _stop.is_set():
            return False
        _stop.wait(max(0.01, min(QUIET_S - q, deadline - now, POLL_S)))


def _fill(want: Want) -> None:
    """`want`'s pool made up to one clip of each kind, one synthesis at a
    time. Each kind once, and once more where the engine was not known
    before the first: that clip says which engine answers, and is dropped
    where that engine's pool already has its kind."""
    from app import model_load, speech, voice as node_voice
    for _ in range(len(KINDS) + 1):
        if not _quiet():
            return                                   # deferred: the next ask asks again
        node = node_voice.settings_or_none()
        root = _root()
        if root is None or node.tts_model != want.model or node_voice.server() != want.url:
            return                                   # no fast drive, or another model now: another pool
        held = speech.profiles(want.url)
        # the voice still the one speech reads in for it (a profile removed, a node voice set since)
        if speech.reader_voice(None, held, node, asked=want.voice, admin=True)[1] != want.voice:
            return
        identity, steps = voice_identity(want.voice, held), _steps(node)
        engine = model_load.backend_of(want.url, want.model)
        pool = root / pool_key(want, identity, engine, steps) if engine else None
        with _lock:
            have = _clips(pool) if pool is not None else {}
            missing = [k for k in KINDS if k not in have]
            if not missing:
                _failed.pop(want, None)
                return
            kind = missing[0]
            text = _draw(want.lang, kind, _avoid(pool, kind, have))
        body: dict[str, Any] = {"model": want.model, "input": text, "language": want.lang}
        if want.voice:
            body["voice"] = want.voice
        t0 = time.monotonic()
        status, content, ctype, _timing = speech.synthesize_timed(body, want.url, admin=True, keep=False)
        took = time.monotonic() - t0
        if status != 200 or not content or not str(ctype).startswith("audio/"):
            _fail(want, f"the speech model answered {status} ({str(ctype).split(';')[0]})")
            return
        # read afresh: a model the call loaded is not in the listing read before it
        made_by = model_load.backend_of(want.url, want.model, max_age_s=0.0)
        if not made_by:
            _fail(want, "the model server does not say which engine made the clip, so it is not kept")
            return
        pool = root / pool_key(want, identity, made_by, steps)
        ctype = str(ctype).split(";")[0].strip().lower()
        ext = ctype.split("/", 1)[-1]
        ext = ext if _EXT.fullmatch(ext) else "audio"
        unwritten: OSError | None = None
        with _lock:
            have = _clips(pool)
            if kind in have or text in _avoid(pool, kind, have):
                continue                             # made before the engine was known: that pool has it
            clip_id = pool.name + os.urandom(8).hex()
            meta = {"id": clip_id, "kind": kind, "text": text, "lang": want.lang, "voice": want.voice,
                    "url": want.url, "model": want.model, "engine": made_by, "ctype": ctype, "ext": ext,
                    "made": time.time()}
            try:
                pool.mkdir(parents=True, exist_ok=True)
                # the audio first, its JSON after: a clip is ready once both are there (`_clips`)
                _write_atomic(pool / f"{clip_id}.{ext}", content)
                _write_atomic(pool / f"{clip_id}.json",
                              json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"))
                _failed.pop(want, None)
            except OSError as e:
                unwritten = e
        if unwritten is not None:
            _fail(want, f"the fast drive cannot be written ({unwritten})")
            return
        _say("info", f"speech fillers: {_voice_said(want)} ({want.lang}) {kind} {text!r} made in {took:.1f} s "
                     f"under {made_by}")


# ---- tests -----------------------------------------------------------------------------------

def wait_idle(timeout: float = 10.0) -> bool:
    """Tests: True once every job queued has run."""
    end = time.monotonic() + timeout
    while _jobs.unfinished_tasks:
        if time.monotonic() >= end:
            return False
        time.sleep(0.01)
    return True


def reset() -> None:
    """Tests: the worker stopped and everything this process remembers forgotten."""
    global _set, _worker
    _stop.set()
    w = _worker
    if w is not None:
        w.join(timeout=5.0)
    with _lock:
        while True:
            try:
                _jobs.get_nowait()
                _jobs.task_done()
            except queue.Empty:
                break
        _pending.clear()
        _failed.clear()
        _worker = None
    _stop.clear()
    with _set_lock:
        _set = None
