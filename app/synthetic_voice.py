"""A synthetic voice for a language, made by the node (operator, 2026-11-27:
"Add a spanish voice"; the rule of the same day: "Anything requiring a manual
edit because not covered by the API goes back to architecture for
implementation or fix").

`louise node speech synthetic <lang>` (07 §7.3; POST /api/node/speech/synthetic)
does in one command what München did by hand for it, en, fr and de on
2026-11-24 (/root/measure/synthetic-voices/ on the node):

1. THE REFERENCE CLIP, WITH NO VOICE: the node's text-to-speech model
   (`[speech] tts_model`) reads the language's sentence (`TEXTS`) with the
   request's `voice` ABSENT — so the model speaks in a voice of its own and no
   person's voice is in the clip — as a WAV;
2. THE CLIP HEARD BACK: the node's speech-to-text model (`[speech] stt_model`)
   transcribes it, and its words must be the sentence's (`HEARD_MIN`) — a
   draw that mumbles or says another language is never made a voice;
3. THE PROFILE: the clip and its sentence recorded on the model server
   (`POST /api/voice-profiles`), named "Synthetic <lang>" and described with
   how it was made, as on 11-24;
4. THE LINE: `[speech] synthetic_voices` given the language's voice, the
   others kept in their order (app/voice.py `write`, the file read back
   before it replaces the old one);
5. THE PING: one short synthesis in the voice (`speech.ping`), so the
   engine encodes the clip now and not in a reader's first sentence.

CANDIDATES ARE DESIGNED (München, 2026-11-27: three no-voice draws of
Spanish were one voice, the model's one default speaker, each 215,040
frames; Italian's six candidates of 11-24 were DESIGNED, and the operator
chose "Synthetic it C" by ear on 11-25): `--candidates N` makes N voices from
the first N of the 11-24 designs (`DESIGNS`), each sent as the request's
`instructions` with no voice, as on 11-24; `--designs "…; …"` gives them.
They are named "Synthetic <lang> A", "B", … with the first letters no
profile holds, each profile's description names its design, and no line is
written; `--use "<name>"` then sets the one chosen. One design
(`--designs "male, middle-aged, low pitch"`) makes one voice and sets it.
A draw that fails step 2 is discarded and said, and the same design drawn
again, at most `candidates + RETRIES` draws in all.

A profile is never deleted or replaced here: a name already held is refused
in words, and `--use` sets it.
"""
from __future__ import annotations

import difflib
import io
import json
import re
import time
import wave
from typing import Any, Callable

from app import httpc, platform_config, speech, voice

#: the sentence each language's reference clip reads. it, en, fr and de are
#: the 2026-11-24 texts verbatim (texts.json); es added 2026-11-27 in the
#: same words. A language not here is given with `--text` (and `--tag`)
TEXTS = {
    "it": "Questa è la voce sintetica di Louise per la lingua italiana. Leggo le risposte ad alta voce, "
          "con calma e con chiarezza, una frase dopo l'altra, senza fretta.",
    "en": "This is the synthetic voice of Louise for the English language. I read the answers aloud, "
          "calmly and clearly, one sentence after another, without any hurry.",
    "fr": "Voici la voix synthétique de Louise pour la langue française. Je lis les réponses à haute voix, "
          "calmement et clairement, une phrase après l'autre, sans me presser.",
    "de": "Dies ist die synthetische Stimme von Louise für die deutsche Sprache. Ich lese die Antworten "
          "laut vor, ruhig und deutlich, einen Satz nach dem anderen, ohne Eile.",
    "es": "Esta es la voz sintética de Louise para el idioma español. Leo las respuestas en voz alta, "
          "con calma y con claridad, una frase tras otra, sin prisa.",
}
#: the profile's language tag (the 11-24 profiles: it-IT, en-US, fr-FR, de-DE)
TAGS = {"it": "it-IT", "en": "en-US", "fr": "fr-FR", "de": "de-DE", "es": "es-ES"}
#: the voices a candidate is designed as: the 11-24 designs.json verbatim
#: (it-candidates/), sent as `instructions`; Italian's is C (the operator, 11-25)
DESIGNS = {"A": "male, elderly, low pitch", "B": "male, elderly, very low pitch",
           "C": "male, middle-aged, low pitch", "D": "female, elderly, low pitch",
           "E": "female, middle-aged, low pitch", "F": "male, elderly"}
#: a reference clip's length, in seconds: the 11-24 clips were 9.6 to 10.0 s
CLIP_S = (4.0, 30.0)
#: the share of the sentence's words the transcript must give back, in order
#: (difflib's ratio over the two word lists): parakeet gave the 11-24 clips
#: back word for word but for a stray mark; a draw in another language or a
#: mumbled one is far below
HEARD_MIN = 0.85
#: draws beyond the candidates asked, before the command gives up
RETRIES = 2
#: one reference clip's synthesis (11-24: 300 s), on a model that may load first
SYNTH_TIMEOUT_S = 300.0
MAX_CANDIDATES = len(DESIGNS)
_WORD = re.compile(r"\w+", re.UNICODE)


class SyntheticError(ValueError):
    """What stops the command, in words; nothing half-made is left behind it
    but the profiles it names."""


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text or "")]


def heard(said: str, transcript: str) -> float:
    """How much of `said` the transcript gives back, 0..1 (word lists)."""
    a, b = _words(said), _words(transcript)
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def clip_shape(data: bytes) -> dict[str, Any]:
    """A WAV's rate, channels, sample width and seconds; raises SyntheticError
    for anything that is not one."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    except Exception as e:  # noqa: BLE001 — said in words
        raise SyntheticError(f"the model's answer is not a WAV ({type(e).__name__})") from None
    return {"rate": rate, "channels": ch, "bits": 8 * width, "seconds": round(n / rate, 2) if rate else 0.0}


def names(lang: str, n: int, held: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """The profiles' names: "Synthetic es" for one voice, else "Synthetic es
    A", "B", … with the first letters no profile in `held` has."""
    base = f"Synthetic {lang}"
    if n == 1:
        return [base]
    out = [f"{base} {chr(ord('A') + i)}" for i in range(26) if f"{base} {chr(ord('A') + i)}" not in held]
    if len(out) < n:
        raise SyntheticError(f"the model server holds {26 - len(out)} profiles named {base!r} and a "
                             f"letter; {n} more have no letter left")
    return out[:n]


def _server_profiles(url: str) -> list[dict]:
    """The model server's voice profiles, read now (never `speech.profiles`'
    minute-old copy: a name made a moment ago must be seen)."""
    try:
        r = httpc.get(f"{url}/api/voice-profiles", timeout=10.0)
    except Exception as e:  # noqa: BLE001 — said in words
        raise SyntheticError(f"the model server at {url} did not answer its voice profiles "
                             f"({type(e).__name__})") from None
    if r.status_code != 200:
        raise SyntheticError(f"the model server at {url} answered {r.status_code} for its voice profiles: "
                             f"it holds none Louise can add to")
    return list((r.json() or {}).get("data") or [])


def _draw(url: str, model: str, text: str, lang: str, design: str | None = None) -> bytes:
    """Step 1: the sentence read with NO voice (the field absent), as a WAV;
    with a design, as the 11-24 designed draws: the design in `instructions`."""
    body = {"model": model, "input": text, "language": lang, "response_format": "wav"}
    if design:
        body["instructions"] = design
    with speech.pinned(url, [model], "synthetic-voice"):
        try:
            r = httpc.post(f"{url}/v1/audio/speech", json=body, timeout=SYNTH_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — said in words
            raise SyntheticError(f"{model} at {url} did not answer ({type(e).__name__})") from None
    if r.status_code != 200:
        raise SyntheticError(f"{model} at {url} answered {r.status_code}: {r.text[:200]}")
    return r.content


def _hear(url: str, model: str, clip: bytes, lang: str) -> str:
    """Step 2: the clip transcribed by the node's speech-to-text model."""
    from app import transcribe
    got = transcribe.transcribe(clip, f"synthetic-{lang}.wav", "audio/wav",
                                [("model", model), ("language", lang), ("response_format", "json")], url=url)
    if got.status != 200:
        raise SyntheticError(f"{model} at {url} answered {got.status} transcribing the clip: "
                             f"{got.body[:200].decode('utf-8', 'replace')}")
    try:
        return str((json.loads(got.body) or {}).get("text") or "")
    except ValueError:
        return got.body.decode("utf-8", "replace")


def _record(url: str, name: str, lang: str, tag: str, text: str, clip: bytes, model: str,
            design: str | None = None) -> dict:
    """Step 3: the profile, as POSTed on 11-24 (multipart)."""
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    designed = f", designed as \"{design}\"" if design else ""
    data = {"name": name,
            "description": (f"Synthetic voice for {lang}: the reference clip was generated by {model} on "
                            f"this node with no voice{designed}, {when}; no person is in it "
                            f"(louise node speech synthetic)"),
            "language": tag, "transcript": text, "consent_confirmed": "true"}
    files = {"audio": (f"synthetic-{lang}.wav", clip, "audio/wav")}
    try:
        r = httpc.post(f"{url}/api/voice-profiles", data=data, files=files, timeout=60.0)
    except Exception as e:  # noqa: BLE001 — said in words
        raise SyntheticError(f"the model server at {url} did not answer the new profile "
                             f"{name!r} ({type(e).__name__})") from None
    if r.status_code not in (200, 201):
        raise SyntheticError(f"the model server at {url} refused the profile {name!r} "
                             f"({r.status_code}: {r.text[:200]})")
    got = r.json() or {}
    if not str(got.get("voice") or "").startswith(speech.PROFILE_PREFIX):
        raise SyntheticError(f"the model server at {url} made {name!r} but named no voice for it: {got}")
    return got


def _set_line(lang: str, voice_uri: str) -> dict[str, Any]:
    """Step 4: `[speech] synthetic_voices` with `lang`'s voice, the others kept."""
    have = voice.synthetic()
    have[lang] = voice_uri
    line = ", ".join(f"{k}={v}" for k, v in have.items())
    return voice.write(platform_config.platform_path(), {"synthetic_voices": line})


def _settings(*, need_stt: bool) -> tuple[str, str, str | None]:
    try:
        s = voice.settings()
    except voice.LOAD_ERRORS as e:
        raise SyntheticError(f"{platform_config.platform_path()} does not load ({e})") from None
    if not s.tts_model:
        raise SyntheticError("no text-to-speech model is set on this node: `louise node speech set --tts <model>`")
    if need_stt and not s.stt_model:
        raise SyntheticError("no speech-to-text model is set on this node, and a clip is never made a voice "
                             "unheard: `louise node speech set --stt <model>`")
    return voice.server().rstrip("/"), s.tts_model, s.stt_model


def _lang(lang: str) -> str:
    out = (lang or "").strip().lower().replace("_", "-").split("-")[0]
    if not out.isalpha() or not 2 <= len(out) <= 3:
        raise SyntheticError(f"{lang!r} is not a language's ISO code (es, pt, nl, …)")
    return out


def make(lang: str, *, candidates: int = 1, designs: list[str] | None = None, text: str | None = None,
         tag: str | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Steps 1–5 for `lang`; with more than one voice (candidates or
    designs), steps 1–3 for each and no line. Raises SyntheticError, in
    words, for anything that stops it."""
    lang = _lang(lang)
    given = [d.strip() for d in (designs or []) if d and d.strip()]
    if not 1 <= (len(given) or candidates) <= MAX_CANDIDATES:
        raise SyntheticError(f"--candidates and --designs are 1 to {MAX_CANDIDATES} voices")
    if given and candidates not in (1, len(given)):
        raise SyntheticError(f"--candidates {candidates} and {len(given)} designs: give one or the other")
    plan: list[str | None] = (given or (list(DESIGNS.values())[:candidates] if candidates > 1 else [None]))
    text = (text or "").strip() or TEXTS.get(lang)
    if not text:
        raise SyntheticError(f"Louise has no sentence for {lang!r}: give the one its reference clip reads with "
                             f"--text \"…\" (two sentences, about ten seconds read aloud), and --tag "
                             f"{lang}-{lang.upper()} if that is not its region")
    tag = (tag or "").strip() or TAGS.get(lang) or f"{lang}-{lang.upper()}"
    url, tts, stt = _settings(need_stt=True)
    held = {str(p.get("name") or ""): p for p in _server_profiles(url)}
    wanted = names(lang, len(plan), set(held))
    taken = [n for n in wanted if n in held]
    if taken:
        raise SyntheticError(f"the model server already holds {', '.join(repr(n) for n in taken)}; nothing "
                             f"was made. Set it with `louise node speech synthetic {lang} --use \"{taken[0]}\"`")
    made: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    try:
        _draw_all(list(zip(wanted, plan)), made, discarded, url=url, tts=tts, stt=stt, text=text, lang=lang,
                  tag=tag, log=log)
    except SyntheticError as e:
        if made:
            raise SyntheticError(f"{e}; made before it, kept on the model server and set nowhere: "
                                 f"{', '.join(m['name'] for m in made)}") from None
        raise
    draws = len(made) + len(discarded)
    out: dict[str, Any] = {"language": lang, "tag": tag, "model": tts, "heard_by": stt, "sentence": text,
                           "made": made, "discarded": discarded, "draws": draws}
    if len(plan) == 1:
        out["line"] = _set_line(lang, made[0]["voice"])
        out["pinged"] = speech.ping(url, tts, made[0]["voice"], timeout=SYNTH_TIMEOUT_S)
        out["says"] = (f"every reader but an admin now hears {made[0]['name']} for {lang}; the page "
                       f"offers it at its next load")
    else:
        out["says"] = (f"{len(plan)} candidates made and none set: listen to them (an admin chooses any "
                       f"voice on the Ask page), then `louise node speech synthetic {lang} --use "
                       f"\"{made[0]['name']}\"` with the one chosen")
    return out


def _draw_all(wanted: list[tuple[str, str | None]], made: list[dict[str, Any]],
              discarded: list[dict[str, Any]], *, url: str, tts: str, stt: str, text: str, lang: str,
              tag: str, log: Callable[[str], None]) -> None:
    """Steps 1–3 for each (name, design) wanted, `made` and `discarded` filled
    as it goes; a design whose draw is discarded is drawn again."""
    draws = 0
    for name, design in wanted:
        while True:
            if draws >= len(wanted) + RETRIES:
                raise SyntheticError(
                    f"{draws} draws of {tts} and {len(discarded)} not heard as the sentence "
                    f"({'; '.join(d['why'] for d in discarded)}); made: "
                    f"{', '.join(m['name'] for m in made) or 'none'}")
            draws += 1
            clip = _draw(url, tts, text, lang, design)
            shape = clip_shape(clip)
            why = None
            if shape["channels"] != 1:
                why = f"{shape['channels']} channels, not one"
            elif not CLIP_S[0] <= shape["seconds"] <= CLIP_S[1]:
                why = f"{shape['seconds']} s, not {CLIP_S[0]:g} to {CLIP_S[1]:g}"
            transcript = "" if why else _hear(url, stt, clip, lang)
            score = 0.0 if why else heard(text, transcript)
            if why is None and score < HEARD_MIN:
                why = f"heard {score} of the sentence (at least {HEARD_MIN}): {transcript[:120]!r}"
            if why:
                discarded.append({"draw": draws, "design": design, "why": why})
                log(f"synthetic voice {lang}: draw {draws} discarded: {why}")
                continue
            got = _record(url, name, lang, tag, text, clip, tts, design)
            made.append({"name": name, "design": design, "voice": got["voice"], "seconds": shape["seconds"],
                         "rate": shape["rate"], "heard": score, "transcript": transcript})
            log(f"synthetic voice {lang}: {name} made ({shape['seconds']} s, heard {score}): {got['voice']}")
            break


def use(lang: str, name: str) -> dict[str, Any]:
    """`--use`: the profile named (or its voice) set as `lang`'s synthetic
    voice, and pinged."""
    lang = _lang(lang)
    url, tts, _stt = _settings(need_stt=False)
    want = (name or "").strip()
    found = [p for p in _server_profiles(url) if want in (p.get("name"), p.get("voice"), p.get("id"))]
    if not found:
        raise SyntheticError(f"the model server holds no voice profile {want!r}: "
                             f"`louise node speech synthetic {lang}` makes one")
    if len(found) > 1:
        raise SyntheticError(f"{len(found)} profiles are named {want!r}; give its voice "
                             f"({', '.join(str(p.get('voice')) for p in found)})")
    p = found[0]
    return {"language": lang, "name": p.get("name"), "voice": p.get("voice"),
            "line": _set_line(lang, str(p.get("voice"))),
            "pinged": speech.ping(url, tts, str(p.get("voice")), timeout=SYNTH_TIMEOUT_S)}
