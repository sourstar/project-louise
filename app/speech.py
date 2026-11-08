"""Speech for the programs that read Louise's answers aloud (operator,
2026-11-06: Open WebUI's voice on LocalAI's omnivoice, the Philippe voice
for Italian and any voice for the other languages; "it doesn't skip the
references like [7] but it pronounces them"; "keep the text to speech
pinned as long as there is text to process").

`POST /v1/audio/speech`, OpenAI's shape, is relayed to the model server's
own `/v1/audio/speech` after the three things only Louise knows:

- NOTHING IN SQUARE BRACKETS IS READ (operator, 2026-11-07: "citations
  shall be skipped in the speech"). In an answer a bracket is a citation,
  whatever the language writes inside it: "[3]", "[3]-[5]", "[7 ; 8]",
  "[7 et 8]", "[ 7 ]", "[réf. 7]", "[gutenberg:123]", "\\[7\\]", "【7】".
  No list of shapes to keep up: every bracket goes, and a run of them
  joined by a dash or a comma with it. A client cleans the text before
  sending it, but not of these: Open WebUI drops markdown and footnotes,
  keeps "[7]", and reads "[3][4]" or "[3](…)" as a link, keeping the bare
  "3" — so `/v1` writes a space after a citation followed by a bracket or
  a parenthesis (`spaced_citations`), and this module removes the brackets.
- ONE VOICE FOR EVERY LANGUAGE (operator, 2026-11-07: "Use the same
  omnivoice philippe voice for all the languages, let's see how it goes",
  replacing 11-06's voice per language). The voice asked for is used
  whatever the text's language; none asked, the first profile recorded in
  LocalAI (`/api/voice-profiles`). Never the model's own voice where a
  profile exists: OmniVoice without one is a different speaker at every
  sentence, which is what French got while it had no French profile. The
  text's language still goes as `language`, so the voice speaks it.
- THE MODEL STAYS LOADED WHILE IT SPEAKS. A client sends an answer one
  sentence at a time, and LocalAI's reclaimer may evict a model between two
  calls; the model is pinned (app/model_pins.py) from the first sentence and
  released IDLE_S after the end of the last (`pinned`, which the
  microphone's models use too: app/transcribe.py).
- THE NODE'S MODEL AND VOICE WHEN THE REQUEST NAMES NONE (2026-11-07): the
  Ask page sends the text alone, and `[speech] tts_model` / `tts_voice`
  (app/voice.py, the one resolution) fill them in. A client that names its
  own (Open WebUI) keeps it.

THE LANGUAGE is told by the commonest words of the languages Louise's
collections hold — enough to tell the model which language it reads. THE
STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"): a language
identifier — lingua-py (n-gram models, 75 languages) or langid.py — is the
named tool; it is a new dependency and pays once voices exist for more
languages than these word lists cover, or once a short sentence picks the
wrong language.
"""
from __future__ import annotations

import io
import re
import threading
import time
import wave
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from app import config, httpc, model_pins

#: the pin is released this long after the last sentence: a client sends the
#: next sentence well within it, and a reader who stopped frees the card
IDLE_S = 120.0
#: one sentence's synthesis, on a model that may have to load first
CALL_TIMEOUT_S = 180.0
#: the profiles are read again after this long: one recorded meanwhile is used
PROFILES_TTL_S = 60.0
HOLDER = "speech"

# every bracket, with the space before it: "[…]", "\\[…\\]" (escaped
# markdown), "【…】" (the brackets some chat models cite with)
_BRACKET = re.compile(r"\s*(?:\\?\[[^\[\]\n]{0,160}?\\?\]|【[^【】\n]{0,160}】)")
# a run of removed brackets joined by a dash or a comma: "[3]-[5]", "[3], [4]"
_RUN = re.compile("\x00(?:\\s*[-–—,;]?\\s*\x00)*")
# a citation the stream must keep apart from a "[" or "(" after it
_CITE_THEN = re.compile(r"\[([^\[\]\n]{1,48})\](?=[\[(])")
# a record's key: "[gutenberg:123]"
_KEY = re.compile(r"[a-z][\w.-]*:[^\]\s]+")
# the words that join or name the numbers of a citation, in the languages
# Louise answers in: "[7 et 8]", "[réf. 7]", "[fonte 3]", "[n° 7]"
_CITE_WORDS = frozenset("""
    and or to see cf ref refs source sources doc docs no nr n p pp
    et ou à a réf voir
    e ed o al rif vedi fonte fonti
    und oder bis siehe quelle quellen
    y fuente fuentes
    nº
""".split())
_CITE_MARKS = frozenset(" \t,;:.-–—/&+°º")
_SPACE_BEFORE_MARK = re.compile(r"\s+([.,;:!?»)])")
# A FULL STOP THAT ENDS NO SENTENCE (operator, 2026-11-08: "G. Cesare" read
# with a long pause after "G."): an initial — one letter — or a title before
# a name ("Prof. Rossi", "Dott. Bianchi", "St. Paul") loses its stop, which
# the model reads as a sentence's end. The cost: a sentence that really ends
# in a single capital ("…la vitamina C. Poi") loses its pause.
_STOP_BEFORE_NAME = re.compile(r"(?<![\w.])([^\W\d_]{1,5})\.(?=\s+[^\W\d_])")
#: the titles written before a name, in the languages Louise answers in
_TITLES = frozenset("""
    dr mr mrs ms prof st jr sr
    sig sigg dott ing avv arch geom rag on
    mme mlle mgr pr
    hr fr frl
    sra srta dña
""".split())

_WORDS = {
    "it": "il lo la gli le di che è e un una per non con del della sono nel alla questo anche",
    "en": "the of and to is in that it was for on are with as his they be at this which",
    "de": "der die und das ist nicht ein eine zu den von mit sich des auf für dem wird",
    "fr": "le la les et est des un une du que pas pour dans qui sur au avec ce sont",
    "es": "el la los las y es que un una de del en por con para no se su al",
    "pt": "o a os as e é que um uma do da em por com para não se dos das",
}
_LEXICON = {lang: frozenset(words.split()) for lang, words in _WORDS.items()}


def _is_citation(inner: str) -> bool:
    """A bracket's content is a citation (for the stream's spacing): a
    record's key, or numbers with only the words and marks that join or
    name them ("7", "7 ; 8", "7 et 8", "réf. 7", "3-5"). A link's text,
    "[Dante](…)", is not one and is left as written."""
    s = inner.strip()
    if _KEY.fullmatch(s):
        return True
    if not any(c.isdigit() for c in s):
        return False
    words = re.findall(r"[^\W\d_]+", s.casefold())
    if any(w not in _CITE_WORDS for w in words):
        return False
    rest = re.sub(r"[^\W\d_]+|\d+", "", s)
    return all(c in _CITE_MARKS for c in rest)


def _no_stop(m: re.Match) -> str:
    """`_STOP_BEFORE_NAME`'s replacement: the stop goes after an initial (one
    capital letter) or a title (`_TITLES`) before a capitalised word."""
    word, after = m.group(1), m.string[m.end():].lstrip()[:1]
    initial = len(word) == 1 and word.isupper()
    if (initial or word.casefold() in _TITLES) and after.isupper():
        return word
    return m.group(0)


def speakable(text: str) -> str:
    """The text without anything in square brackets, spaced as prose: each
    bracket, and a run of them joined by a dash or a comma, goes with the
    space before it; an initial's or a title's full stop before a name goes
    too (`_STOP_BEFORE_NAME`)."""
    out = _RUN.sub("", _BRACKET.sub("\x00", text.replace("\x00", "")))
    out = _SPACE_BEFORE_MARK.sub(r"\1", out)
    out = _STOP_BEFORE_NAME.sub(_no_stop, out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def spaced_citations(text: str, tail: str = "") -> str:
    """A space after a citation followed by a bracket or a parenthesis
    ("[3][4]" → "[3] [4]", "[réf. 3](" → "[réf. 3] ("), so a client's
    markdown cleaning does not read the pair as a link and keep the bare
    number. `tail`: what came before `text` in a stream (TAIL characters),
    so a citation split across two chunks is spaced too."""
    whole = tail + text
    out, last = [], 0
    for m in _CITE_THEN.finditer(whole):
        at = m.end() - len(tail)
        if at >= 0 and _is_citation(m.group(1)):
            out += [text[last:at], " "]
            last = at
    out.append(text[last:])
    return "".join(out)


#: the stream's tail `spaced_citations` needs: a citation's length
TAIL = 64


def language(text: str) -> str | None:
    """The text's language among those `_WORDS` knows, or None when no
    language clearly leads."""
    words = re.findall(r"[^\W\d_]+", text.casefold())
    score = sorted(((sum(w in lex for w in words), lang) for lang, lex in _LEXICON.items()),
                   reverse=True)
    best, runner = score[0], score[1]
    if best[0] < 2 or best[0] == runner[0]:
        return None
    return best[1]


def voice_for(asked: str | None, profiles: list[dict]) -> str | None:
    """THE ONE VOICE, whatever the language (operator, 2026-11-07): the
    voice asked for; none asked, the first profile recorded; no profile,
    the model's own (None)."""
    if asked:
        return asked
    for p in sorted(profiles, key=lambda p: str(p.get("created_at") or "")):
        if p.get("voice"):
            return p.get("voice")
    return None


_profiles: tuple[float, list[dict]] = (0.0, [])
_lock = threading.Lock()


def profiles(url: str) -> list[dict]:
    """The model server's voice profiles, read at most every PROFILES_TTL_S;
    none when it has no such API."""
    global _profiles
    now = time.monotonic()
    with _lock:
        at, got = _profiles
        if now - at < PROFILES_TTL_S:
            return got
    try:
        r = httpc.get(f"{url}/api/voice-profiles", timeout=5.0)
        got = list((r.json() or {}).get("data") or []) if r.status_code == 200 else []
    except Exception:   # noqa: BLE001 — no profiles: the voice as asked
        got = []
    with _lock:
        _profiles = (now, got)
    return got


_timers: dict[str, threading.Timer] = {}


def _arm(holder: str) -> None:
    """`holder`'s pins released IDLE_S from now, unless a call re-arms it."""
    with _lock:
        old = _timers.pop(holder, None)
        if old is not None:
            old.cancel()
        t = threading.Timer(IDLE_S, model_pins.release, args=(holder,))
        t.daemon = True
        _timers[holder] = t
        t.start()


@contextmanager
def pinned(url: str, models: Iterable[str | None], holder: str = HOLDER) -> Iterator[None]:
    """`with pinned(url, [model], holder):` around one call of a run of them
    (the sentences of an answer, the clips a reader speaks): each model
    pinned as the call starts, and the holder released IDLE_S after the END
    of the last call — a call longer than IDLE_S is never unpinned under it."""
    for m in models:
        if m:
            model_pins.hold(url, m, holder)
    _arm(holder)
    try:
        yield
    finally:
        _arm(holder)


def audio_ms(data: bytes) -> float | None:
    """A WAV clip's length in ms; None for any other audio."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return round(1000.0 * w.getnframes() / w.getframerate(), 1)
    except Exception:   # noqa: BLE001 — not a WAV (or a broken one): no length
        return None


def synthesize(body: dict[str, Any], url: str | None = None) -> tuple[int, bytes, str]:
    """(status, audio or error body, content type) for an OpenAI speech
    request, relayed to the model server; the model and the voice the
    node's (`[speech]`, app/voice.py) where the request names none."""
    return synthesize_timed(body, url)[:3]


def synthesize_timed(body: dict[str, Any], url: str | None = None) -> tuple[int, bytes, str, list]:
    """`synthesize` and its `Server-Timing` (W3C; 2026-11-08, to measure the
    reading's start and its pauses): `synth` the model server's call in ms,
    `audio` the clip's length in ms where it is a WAV."""
    from app import voice as node_voice
    url = (url or config.CHAT_URL).rstrip("/")
    text = speakable(str(body.get("input") or ""))
    node = node_voice.settings_or_none()
    model = str(body.get("model") or "") or (node.tts_model or "")
    if not text:
        return 400, b'{"error": {"message": "no text to speak once its citations are removed"}}', \
            "application/json", []
    out = {**body, "input": text}
    if model:
        out["model"] = model
    lang = language(text)
    voice = voice_for(body.get("voice") or node.tts_voice or None, profiles(url))
    if voice:
        out["voice"] = voice
    else:
        out.pop("voice", None)
    if lang and "language" not in out:
        out["language"] = lang
    # NUMBERS AND DATES IN THE TEXT'S OWN WORDS (operator, 2026-11-08, A): the
    # one voice reads digits as it learned them, in Italian (app/speech_numbers.py)
    from app import speech_numbers
    said = str(out.get("language") or "").split("-")[0].split("_")[0].lower() or None
    out["input"] = speech_numbers.verbalize(out["input"], said)
    t0 = time.perf_counter()
    if not model:
        r = httpc.post(f"{url}/v1/audio/speech", json=out, timeout=CALL_TIMEOUT_S)
    else:
        with pinned(url, [model]):
            r = httpc.post(f"{url}/v1/audio/speech", json=out, timeout=CALL_TIMEOUT_S)
    timing = [("synth", round((time.perf_counter() - t0) * 1000.0, 1))]
    clip = audio_ms(r.content) if r.status_code == 200 else None
    if clip is not None:
        timing.append(("audio", clip))
    return r.status_code, r.content, r.headers.get("content-type", "application/octet-stream"), timing
