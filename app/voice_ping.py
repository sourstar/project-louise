"""A VOICE'S ENCODING, MADE BEFORE A READER WAITS FOR IT (operator, 2026-11-25).

  "upon voice swap seng a ping to make the model re-encode"
  "The only voice ping purpose is to do the re-encode for voices without it
   cached to avoid the extra delay upon the first usage"

The speech engine keeps, beside a voice profile's clip, the clip's ENCODING,
bound to the clip and to the engine's build. A voice with no encoding the
engine can read pays for it in its first sentence: 0.7–0.8 s for an 11 s
clip, 2.2–2.8 s for a 27.75 s one, about 3.2 s for the node's synthetic
voices (München, on the node, 2026-11-25). A voice has none in two ways:

1. ITS CLIP IS NEW — a profile just recorded, or its clip replaced in place
   under the same id (done twice on the node);
2. THE ENGINE IS ANOTHER BUILD — every kept encoding is unreadable at once.

A PING is one short synthesis in the voice (app/speech.py `ping`: POST
/v1/audio/speech {"model", "input": "OK", "voice"}), and it is sent to a voice
ONLY when that voice has no encoding under this engine with this clip. It is
not a warm — the keeper's warm of the speech model names no voice and is
enough (measured) — and it is not sent because a voice was selected.

THE RULE, with nothing added to the wire and nothing changed in the engine:
the node keeps, per voice, WHAT IT WAS LAST PINGED UNDER — the engine (the
speech model's `backend` as `GET /system` lists it for a LOADED model: an
engine's package name carries the engine's id) and the clip (its size, its
length and the profile's `updated_at`, as `GET /api/voice-profiles` lists
them; LocalAI gives no hash, and whoever replaces a clip in place writes
those) — and pings a voice whose pair is new. The voices it looks at are the
ones the node is set to use: `[speech] tts_voice` (else the voice a sentence
with none asked gets), each of `synthetic_voices`, and each account's kept
voice.

A lost record costs one harmless ping (the engine reads its file in 0.01 s
and says "OK"); a ping not sent costs a reader the one encoding it was meant
to spare. Nothing can go wrong, only slow once.

WHEN: beside the keeper's beat in the worker, looked at every CHECK_EVERY_S;
only while the speech model is LOADED (a ping never loads it: that would be
a warm), only while the node's mode preloads (a paused node's card is the
operator's), never under a reader's question, one voice after the other,
each bounded, off the keeper's thread. A ping that fails is tried again
after RETRY_AFTER_S.

The record is `voice_pings` in `state/node.sqlite`: an observation, like
when an encoder was last used — not desired state, and nothing a restart
needs to reset.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import closing
from typing import Any, Callable

KEY = "voice_pings"
#: how often the voices are looked at; a look with nothing new is three reads
CHECK_EVERY_S = 60.0
#: a voice whose ping failed is not asked again before this
RETRY_AFTER_S = 600.0

_lock = threading.Lock()
_looked = -1e9
_flying = False
_failed: dict[str, float] = {}       # voice -> monotonic time of its last failed ping


def reset() -> None:
    """What this process remembers of its looks (tests)."""
    global _looked, _flying
    with _lock:
        _looked, _flying = -1e9, False
        _failed.clear()


def clip_of(profile: dict) -> str:
    """What a profile's clip is known by: its size, its length and when the
    profile was last written. Another clip under the same id changes it."""
    audio = profile.get("audio") if isinstance(profile.get("audio"), dict) else {}
    return f"{audio.get('size_bytes')}/{audio.get('duration_ms')}/{profile.get('updated_at')}"


def wanted(speech_cfg: Any, account_voices: list[str], profiles: list[dict]) -> list[str]:
    """The voice profiles the node is set to use, each once, in the order a
    reader meets them: the node's voice (the one a sentence with none asked
    gets, app/speech.py `voice_for`), each language's synthetic voice, each
    account's kept voice. Only PROFILES the server holds: a model's own named
    voice has no clip to encode, and a profile since removed is nobody's."""
    from app import platform_config, speech
    held = {p.get("voice") for p in profiles if p.get("voice")}
    try:
        synthetic = list(platform_config.synthetic_voices(speech_cfg.synthetic_voices).values())
    except Exception:   # noqa: BLE001 — a setting that does not read names no voice
        synthetic = []
    asked = [speech.voice_for(speech_cfg.tts_voice or None, profiles), *synthetic, *account_voices]
    out: list[str] = []
    for v in asked:
        if v and v in held and v not in out:
            out.append(v)
    return out


def due(voices: list[str], profiles: list[dict], engine: str, record: dict) -> list[tuple[str, str]]:
    """(voice, clip) of each voice with no ping recorded under this engine
    and this clip."""
    by_voice = {p.get("voice"): p for p in profiles}
    out = []
    for v in voices:
        clip = clip_of(by_voice[v])
        last = record.get(v) if isinstance(record.get(v), dict) else {}
        if last.get("engine") != engine or last.get("clip") != clip:
            out.append((v, clip))
    return out


def read(con) -> dict:
    from app import db
    try:
        got = json.loads(db.get_control(con, KEY) or "{}")
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def _note(voice: str, engine: str, clip: str, held: set[str]) -> None:
    """One ping recorded; the records of profiles the server no longer holds
    go with it, so the record is as long as the server's list at most."""
    from app import db, node_state
    with closing(node_state.connect()) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            rec = {v: e for v, e in read(c).items() if v in held}
            rec[voice] = {"engine": engine, "clip": clip, "at": time.time()}
            db.set_control(c, KEY, json.dumps(rec, sort_keys=True))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise


def _account_voices() -> list[str]:
    from app import users
    try:
        return [u.voice for u in users.load() if u.voice and not u.revoked]
    except Exception:   # noqa: BLE001 — a users file that does not read keeps no voice
        return []


def look(*, question: Callable[[], bool] = lambda: False, stop: Callable[[], bool] = lambda: False,
         say: Callable[[str, str], None] | None = None, now: float | None = None) -> dict[str, Any]:
    """ONE LOOK: the voices with no encoding under the engine the speech
    model is loaded on are pinged, one after the other, each recorded at its
    200. Returns {"engine", "wanted", "pinged", "failed", "why"}. Never
    raises past a voice."""
    from app import model_load, node_state, speech, voice
    out: dict[str, Any] = {"engine": None, "wanted": [], "pinged": [], "failed": [], "why": None}
    s = voice.settings_or_none()
    url = voice.server()
    if not url or not s.tts_model:
        out["why"] = "no text-to-speech model is set"
        return out
    ids = model_load.loaded_models(model_load.server_of(url))
    if not ids or s.tts_model not in ids:
        out["why"] = "the speech model is not loaded: a ping never loads it"
        return out
    # a server that does not name its backends: the clip alone is the key
    engine = model_load.backend_of(url, s.tts_model) or ""
    out["engine"] = engine
    profiles = speech.profiles(url)
    voices = wanted(s, _account_voices(), profiles)
    out["wanted"] = voices
    if not voices:
        out["why"] = "the node is set to no recorded voice"
        return out
    with closing(node_state.connect()) as c:
        record = read(c)
    held = {p.get("voice") for p in profiles if p.get("voice")}
    names = {p.get("voice"): str(p.get("name") or p.get("voice")) for p in profiles}
    t_now = time.monotonic() if now is None else now
    for v, clip in due(voices, profiles, engine, record):
        if stop() or question():
            out["why"] = "a reader's question holds the card: the rest at the next look"
            break
        with _lock:
            if t_now - _failed.get(v, -1e9) < RETRY_AFTER_S:
                continue
        t = time.monotonic()
        ok = speech.ping(url, s.tts_model, v)
        took = time.monotonic() - t
        if ok:
            _note(v, engine, clip, held)
            out["pinged"].append(v)
            with _lock:
                _failed.pop(v, None)
            if say:
                say("info", f"voice ping: {names.get(v, v)} has its encoding under "
                            f"{engine or 'this engine'} ({took:.1f} s): its first sentence pays none")
        else:
            out["failed"].append(v)
            with _lock:
                _failed[v] = t_now
            if say:
                say("warn", f"voice ping: {names.get(v, v)} did not answer 200 ({took:.1f} s): its first "
                            f"sentence encodes its clip; asked again in {RETRY_AFTER_S / 60:.0f} min")
    return out


def beat(*, preloads: bool, question: Callable[[], bool] = lambda: False,
         stop: Callable[[], bool] = lambda: False, say: Callable[[str, str], None] | None = None,
         start: Callable[[Callable[[], None]], None] | None = None) -> bool:
    """The keeper's beat calls this: at most every CHECK_EVERY_S, where the
    node's mode preloads and no question is being answered, ONE look is
    started off the caller's thread (a ping is a second or three, and a
    server that hangs holds only its own thread). True when a look started."""
    global _looked, _flying
    if not preloads or question():
        return False
    now = time.monotonic()
    with _lock:
        if _flying or now - _looked < CHECK_EVERY_S:
            return False
        _looked, _flying = now, True

    def run() -> None:
        global _flying
        try:
            look(question=question, stop=stop, say=say)
        except Exception as e:   # noqa: BLE001 — a look never ends the keeper
            if say:
                say("error", f"voice ping: the look failed ({type(e).__name__}: {e})")
        finally:
            with _lock:
                _flying = False
    if start is not None:
        start(run)
    else:
        threading.Thread(target=run, daemon=True, name="voice-ping").start()
    return True
