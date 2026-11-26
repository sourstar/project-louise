"""Speech for the programs that read Louise's answers aloud (operator,
2026-11-06: Open WebUI's voice on LocalAI's omnivoice, one recorded voice
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
- ONE VOICE FOR EVERY LANGUAGE, FOR AN ADMIN (operator, 2026-11-07: "Use
  the same omnivoice voice for all the languages, let's see how it goes",
  replacing 11-06's voice per language). The voice asked for is used
  whatever the text's language; none asked, the first profile recorded in
  LocalAI (`/api/voice-profiles`). Never the model's own voice where a
  profile exists: OmniVoice without one is a different speaker at every
  sentence, which is what French got while it had no French profile. The
  text's language still goes as `language`, so the voice speaks it.
- A RECORDED VOICE IS READ TO AN ADMIN ONLY (operator, 2026-11-16: "limit
  the cloned voices to the admin account … For the users account allow only
  synthetic voices according to the language spoken"). Any other reader is
  read to in the node's synthetic voice for the text's language (`[speech]
  synthetic_voices`, app/voice.py), the first one named where the language
  has none; the voice the request or the account names is not used. With
  no synthetic voice held, nothing is read to them (409, the reason said).
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
collections hold — enough to tell the model which language it reads, and,
for a reader who is not an admin, which synthetic voice reads it. THE
STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"): a language
identifier — lingua-py (n-gram models, 75 languages) or langid.py — is the
named tool; it is a new dependency (the operator's word first) and pays
once voices exist for more languages than these word lists cover, or once
these lists name the WRONG language. On 2026-11-24 they named NONE for a
plain German sentence; the lists were made longer, and a sentence too short
for them is still told by the answer it is part of.
"""
from __future__ import annotations

import io
import re
import threading
import time
import wave
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from app import config, httpc, model_load, model_pins, platform_config

#: the pin is released this long after the last sentence: a client sends the
#: next sentence well within it, and a reader who stopped frees the card
IDLE_S = 120.0
#: one sentence's synthesis, on a model that may have to load first
CALL_TIMEOUT_S = 180.0
#: the profiles are read again after this long: one recorded meanwhile is used
PROFILES_TTL_S = 60.0
#: a voice that names one of the model server's profiles
PROFILE_PREFIX = "localai://voice-profiles/"
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

# THE COMMONEST WORDS of each language: its articles, prepositions, pronouns,
# auxiliaries and conjunctions, and the letters left of an elision ("l'", "d'",
# "qu'"). Some seventy each since 2026-11-24; there were twenty, and a German
# sentence with none of them but "das" — "Das Dekret tritt am Tag nach seiner
# Veröffentlichung im Amtsblatt in Kraft." — had no language, so a reader who
# is not an admin heard it in the first synthetic voice, the Italian one
# (München, #721's first hour live).
_WORDS = {
    "it": "il lo la i gli le un uno una di a da in con su per tra fra e ed o ma che non è sono era ha hanno del dello "
          "della dei degli delle al allo alla ai agli alle dal dalla dai nel nello nella nei nelle sul sulla come più "
          "anche se si ci questo questa questi queste quello quella essere stato stata suo sua loro cui dove quando "
          "perché ogni l d c viene vengono può deve",
    "en": "the of and to a in is that it was for on are with as his they be at this which by from or an have had not "
          "but what all were when we there can their has been would who will more if no so about its into than them "
          "only other some could these may then do any such our over shall must should",
    "de": "der die das und ist nicht ein eine einer eines einem einen zu den von mit sich des auf für dem wird im am in "
          "an als auch es nach bei aus um wie oder aber wurde wurden werden sind war hat haben durch über unter zur zum "
          "vom bis noch nur wenn dass sie er wir ich seiner seine ihrer ihre dieser diese dieses kann muss gilt",
    "fr": "le la les et est des un une du que pas pour dans qui sur au aux avec ce sont de en à il elle ils elles ne se "
          "par plus ou mais comme être été a ont son sa ses leur leurs cette ces où nous vous je on y dont entre sans "
          "sous aussi fait l d qu n j c s peut doit",
    "es": "el la los las y es que un una de del en por con para no se su al lo como más pero sus le ya o fue este esta "
          "ha sí porque cuando muy sin sobre también hasta hay donde quien desde todo nos durante todos uno les ni "
          "contra otros ese eso ante ellos e esto antes él son fueron ser está están entre puede debe",
    "pt": "o a os as e é que um uma do da em por com para não se dos das no na nos nas ao aos à às mais como mas foi "
          "ele ela seu sua ou ser quando muito há já está eu também só pelo pela até isso entre era depois sem mesmo "
          "ter seus quem esse eles estão foram essa num nem suas são pode deve",
}
_LEXICON = {lang: frozenset(words.split()) for lang, words in _WORDS.items()}
#: in how many of the languages each word is one of the commonest: a word of
#: one language alone counts 1 for it, "in" (it, en, de) a third for each
_SHARED: dict[str, int] = {}
for _lex in _LEXICON.values():
    for _w in _lex:
        _SHARED[_w] = _SHARED.get(_w, 0) + 1
#: letters only one of these languages writes: at most LETTERS_MOST for a text
_LETTERS = {"de": "äöüß", "es": "ñ¿¡", "pt": "ãõ", "fr": "œ"}
LETTERS_MOST = 2.0
#: a language is named with this much for it, and this much more than for
#: the next: one word of its own and a shared one, never one word alone
LANGUAGE_NEED = 1.25
LANGUAGE_LEAD = 1.0


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


# MARKDOWN IS LAYOUT, NEVER WORDS (operator, 2026-11-12: "asterisks are
# read"): the chat model writes Markdown — the page shows it formatted — and
# its marks are not read. A link is read as its text, a table row as its
# cells; headings, quotes, list bullets, rules, fences, backticks, emphasis
# marks go, and any asterisk left with them.
_MD_LINK = re.compile(r"!?\[([^\[\]\n]{1,200})\]\((?:[^()\s]|\([^()\s]*\))+\)")
_MD_ESCAPE = re.compile(r"\\([*_#`~>|-])")
_MD_FENCE = re.compile(r"^[ \t]*(?:```|~~~)[^\n]*$", re.M)
_MD_RULE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)
_MD_TABLE_SEP = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$", re.M)
_MD_TABLE_ROW = re.compile(r"^[ \t]*\|(.*)\|[ \t]*$", re.M)
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.M)
_MD_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.M)
_MD_BULLET = re.compile(r"^[ \t]*[-*+•][ \t]+", re.M)
_MD_STRONG = re.compile(r"(\*\*|__|~~)(?=\S)(.+?)(?<=\S)\1")
_MD_EM_STAR = re.compile(r"\*(?=\S)(.+?)(?<=\S)\*")
_MD_EM_UNDER = re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)")


def unmarked(text: str) -> str:
    """`text` without its Markdown: what a reader hears of what the page
    shows formatted."""
    t = _MD_LINK.sub(r"\1", text)
    t = _MD_FENCE.sub("", _MD_RULE.sub("", _MD_TABLE_SEP.sub("", t)))
    t = _MD_TABLE_ROW.sub(lambda m: ", ".join(c.strip() for c in m.group(1).split("|") if c.strip()), t)
    t = _MD_BULLET.sub("", _MD_QUOTE.sub("", _MD_HEADING.sub("", t)))
    t = _MD_ESCAPE.sub(lambda m: "" if m.group(1) == "*" else m.group(1), t)
    for _ in range(3):                          # nested: ***both***, **a *b* c**
        t = _MD_EM_UNDER.sub(r"\1", _MD_EM_STAR.sub(r"\1", _MD_STRONG.sub(r"\2", t)))
    return re.sub(r"\n{3,}", "\n\n", t.replace("`", "").replace("*", ""))


def speakable(text: str) -> str:
    """The text without its Markdown (`unmarked`) and without anything in
    square brackets, spaced as prose: each bracket, and a run of them joined
    by a dash or a comma, goes with the space before it; an initial's or a
    title's full stop before a name goes too (`_STOP_BEFORE_NAME`)."""
    text = unmarked(text)
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


def language_scores(text: str) -> dict[str, float]:
    """What speaks for each language of `_WORDS` in `text`: its commonest
    words, each shared among the languages it is common in (`_SHARED`), and
    the letters it alone writes (`_LETTERS`)."""
    low = text.casefold()
    words = re.findall(r"[^\W\d_]+", low)
    out = {lang: sum(1.0 / _SHARED[w] for w in words if w in lex) for lang, lex in _LEXICON.items()}
    for lang, letters in _LETTERS.items():
        out[lang] += min(LETTERS_MOST, float(sum(low.count(ch) for ch in letters)))
    return out


def language(text: str) -> str | None:
    """The text's language among those `_WORDS` knows, or None when no
    language clearly leads (LANGUAGE_NEED, LANGUAGE_LEAD): a name, a date, a
    sentence too short to tell. The caller then asks the answer it is part
    of (`context`), and failing that says none."""
    score = sorted(((v, lang) for lang, v in language_scores(text).items()), reverse=True)
    (best, lang), (runner, _other) = score[0], score[1]
    # rounded: the shares are thirds and fifths, and 5/3 - 2/3 is not 1.0 in floats
    if round(best, 6) < LANGUAGE_NEED or round(best - runner, 6) < LANGUAGE_LEAD:
        return None
    return lang


def primary(tag: Any) -> str | None:
    """A language tag as the speech engine reads one: its PRIMARY SUBTAG, lower
    case ("en-GB", "en_US" -> "en"; "it-IT" -> "it"); None for none. The engine
    knows languages, not countries: a tag with a region is no language to it
    (it resolves to none), and a cloned voice then speaks in its recording's
    (München, 2026-11-25, read in the engine's own language map)."""
    return str(tag or "").strip().replace("_", "-").split("-")[0].lower() or None


def spoken_language(text: str, context: str = "", asked: Any = None) -> str | None:
    """THE LANGUAGE A SENTENCE IS SPOKEN IN (operator, 2026-11-25: "the
    language/country tag must be aligned to the language to speak, otherwise
    the voice has always a thick italian accent"). In this order:
      1. the sentence's OWN language, where it is clear (`language`);
      2. the language of the answer it is part of (`context`, which the page
         sends when it shows the answer in its original language);
      3. the tag the request carries — the page's answer language, a
         client's own — as its primary subtag.
    Until that day the request's tag came first: the Ask page sends the
    language the answer was ASKED in, and an English answer on a page set to
    Italian went to the model tagged Italian — English words in an Italian
    mouth. The tag is now what is left for a sentence too short to tell
    ("Dante.", a date) with no answer around it. None: nothing says."""
    return language(text) or (language(context) if context else None) or primary(asked)


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


def held_profile(voice: str, profiles: list[dict]) -> bool:
    """False only for a voice that names one of the model server's profiles
    when the server lists profiles and not that one; a model's own named
    voice ("alloy") and a server without profiles are taken as asked."""
    if not str(voice).startswith(PROFILE_PREFIX):
        return True
    held = {p.get("voice") for p in profiles if p.get("voice")}
    return not held or voice in held


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


#: the bound on one voice ping: a clip's encoding and a second of synthesis
PING_TIMEOUT_S = 60.0


def ping(url: str, model: str, voice: str, timeout: float = PING_TIMEOUT_S) -> bool:
    """One short synthesis IN THE VOICE (app/voice_ping.py): the engine
    encodes the profile's clip where it holds no encoding of it it can read,
    and writes it beside the clip; where it holds one, it reads it. True for
    a 200: the next sentence in that voice pays no encoding. The answer's
    audio is not kept. Never raises."""
    try:
        r = httpc.post(f"{url}/v1/audio/speech", json={"model": model, "input": "OK", "voice": voice},
                       timeout=timeout)
    except Exception:   # noqa: BLE001 — not pinged: asked again later
        return False
    return r.status_code == 200


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


#: a reading's sentence waits this long for the one before it to ARRIVE;
#: past it that one is lost (its fetch failed) and this one goes
TURN_ARRIVE_S = 5.0
#: a reading not heard from for this long is forgotten
TURN_FORGET_S = 600.0


class Turns:
    """A READING'S SENTENCES GO TO THE MODEL SERVER IN THE PAGE'S ORDER, one
    at a time (München, 2026-11-08): the speech backend makes one clip at a
    time, and of two sent at the same instant it made the SECOND first, so
    the first sound waited for both (sentence 0, 25.6 s, of which 15.0 s was
    sentence 1). The page numbers each sentence of a reading (the
    `Louise-Reading: <id>/<n>` header); sentence n is sent once n - 1 is
    back, so the backend never chooses. The page still keeps two in flight:
    the next one waits HERE, not a round trip away.

    Bounded (CLAUDE.md: no wait without a bound): sentence n - 1 not arrived
    within TURN_ARRIVE_S is lost and n goes; arrived, it is waited for at
    most CALL_TIMEOUT_S, its own bound. A sequencer with client-assigned
    tickets, the order HTTP/1.1 pipelining keeps for responses (RFC 9112
    §9.3.2); the standard alternative, a priority on the backend's own queue,
    is the backend's to offer, and LocalAI offers none."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._readings: dict[str, dict] = {}

    @contextmanager
    def turn(self, reading: str | None, n: int | None):
        if reading is None or n is None:
            yield 0.0
            return
        t0 = time.monotonic()
        with self._cv:
            self._forget(t0)
            st = self._readings.setdefault(reading, {"arrived": set(), "done": set(), "seen": t0})
            st["arrived"].add(n); st["seen"] = t0
            while n > 0 and (n - 1) not in st["done"]:
                waited = time.monotonic() - t0
                if (n - 1) not in st["arrived"] and waited >= TURN_ARRIVE_S:
                    break
                if waited >= CALL_TIMEOUT_S:
                    break
                bound = TURN_ARRIVE_S if (n - 1) not in st["arrived"] else CALL_TIMEOUT_S
                self._cv.wait(max(0.01, bound - waited))
        try:
            yield round((time.monotonic() - t0) * 1000.0, 1)
        finally:
            with self._cv:
                st["done"].add(n); st["seen"] = time.monotonic()
                self._cv.notify_all()

    def _forget(self, at: float) -> None:
        for k in [k for k, v in self._readings.items() if at - v["seen"] > TURN_FORGET_S]:
            del self._readings[k]


_TURNS = Turns()
_READING = re.compile(r"\s*([A-Za-z0-9_-]{1,64})/(\d{1,5})(/last)?\s*")


def reading_turn(header: str | None) -> tuple[str | None, int | None]:
    """`Louise-Reading: <id>/<n>` → (id, n); (None, None) for none or a
    malformed one (the sentence then goes as it comes)."""
    m = _READING.fullmatch(header or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


def reading_last(header: str | None) -> bool:
    """`Louise-Reading: <id>/<n>/last`: the answer has ended and this is its
    last sentence (the log says so; the question's claim still lingers
    dispatch.QUESTION_LINGER_S after it, docs/notes/ask-card-workflow.md)."""
    m = _READING.fullmatch(header or "")
    return bool(m and m.group(3))


#: synthesized sentences kept on the fast drive (LOUISE_CACHE_DIR, through
#: app/derived.py; none kept where no fast drive is named): at most this many,
#: the least recently read dropped first
CLIP_CACHE_FILES = 5000
_clip_puts = [0]


def _clip_dir():
    from pathlib import Path
    from app import derived
    return derived.place("speech-clips", Path(config.louise_root()))


def _clip_key(url: str, out: dict, engine: str) -> str:
    """The kept clip's name: the request (model, voice, language, words,
    steps) AND the engine that made it — LocalAI's backend for the model, as
    `/system` names it (`model_load.backend_of`), whose package name carries
    the engine's id. Another engine's clip is another clip (2026-11-26: the
    fade that cut the last word at a full stop was the engine's; once fixed,
    no clip of the old engine may be answered for it)."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps({"request": {"url": url, **out}, "engine": engine}, sort_keys=True,
                                     ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _clip_get(key: str) -> tuple[bytes, str] | None:
    d = _clip_dir()
    if d is None:
        return None
    for f in d.glob(f"{key}.*"):
        try:
            data = f.read_bytes()
            f.touch()                                   # the most recently read
        except OSError:
            return None
        return data, "audio/" + f.suffix[1:]
    return None


def _clip_put(key: str, data: bytes, ctype: str) -> None:
    """Kept whole or not at all (written aside, then renamed); a fast drive
    that cannot be written keeps nothing and fails nothing."""
    import os
    d = _clip_dir()
    if d is None:
        return
    sub = str(ctype).split(";")[0].split("/", 1)[-1].strip() or "wav"
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{key}.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, d / f"{key}.{sub}")
        _clip_puts[0] += 1
        if _clip_puts[0] % 100 == 0:
            files = sorted((f for f in d.iterdir() if not f.name.startswith(".")),
                           key=lambda f: f.stat().st_mtime)
            for f in files[:max(0, len(files) - CLIP_CACHE_FILES)]:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def synthesize(body: dict[str, Any], url: str | None = None, *,
               admin: bool = True) -> tuple[int, bytes, str]:
    """(status, audio or error body, content type) for an OpenAI speech
    request, relayed to the model server; the model and the voice the
    node's (`[speech]`, app/voice.py) where the request names none."""
    return synthesize_timed(body, url, admin=admin)[:3]


def synthetic_for(lang: str | None, voices: dict[str, str], profiles: list[dict]) -> str | None:
    """The synthetic voice a reader who is not an admin hears: the one the
    node names for `lang`, else the first named; a profile the server no
    longer holds is passed over. None: no synthetic voice is held."""
    first = [voices[lang]] if lang in voices else []
    for v in first + [v for k, v in voices.items() if k != lang]:
        if held_profile(v, profiles):
            return v
    return None


def reader_voice(lang: str | None, held: list[dict], node: Any, *, asked: Any = None,
                 account_voice: str | None = None, admin: bool = True) -> tuple[bool, str | None]:
    """THE VOICE A READER IS READ TO IN, for a sentence spoken in `lang` —
    the ONE resolution (`synthesize_timed`; the phrases that cover the wait,
    app/fillers.py). An admin: the voice asked for (the request's — the Ask
    page's choice, a client's own), else the one the account keeps, unless
    it names a profile the server no longer holds; else the node's
    `tts_voice`; else the first profile recorded (`voice_for`). Any other
    reader: the node's synthetic voice of `lang` (`synthetic_for`), whatever
    is asked. (True, voice) — None being the model's own; (False, None):
    nothing is read to this reader (no synthetic voice held)."""
    from app import voice as node_voice
    if admin:
        mine = next((v for v in (asked or None, account_voice or None) if v and held_profile(v, held)), None)
        return True, voice_for(mine or node.tts_voice or None, held)
    # the voice of that same language: voice and tag never disagree
    voice = synthetic_for(lang, node_voice.synthetic(node), held)
    return voice is not None, voice


#: what a recorded voice is told to speak when no language is known: OmniVoice's
#: auto ("none": the engine reads the language from the text), never the
#: profile's own tag. LocalAI v4.11.0 (core/http/endpoints/localai/tts.go) uses a
#: request's `language` and, where it has none, the profile's, which is the
#: language the RECORDING is in ("it-IT"); OmniVoice conditions the whole
#: synthesis on that one tag, and an English sentence came out with a thick
#: Italian accent (operator, 2026-11-23: "change it accordingly to the language
#: they have to speak")
AUTO = "none"
#: how much of the answer the page sends as `context` is read for its language
CONTEXT_CHARS = 2000


#: what a reader who is not an admin is told when no synthetic voice is held
NO_SYNTHETIC = (b'{"error": {"message": "no synthetic voice is set on this node, and a recorded '
                b'voice is read to an admin only"}}')


#: a reading whose last sentence (`/last`) has not come is in progress until
#: this long without one of its sentences — a reader who stopped it, a page
#: gone; while its answer is still written the answer's own call is live
READING_OPEN_S = 6.0


class Readings:
    """WHAT IS BEING READ ALOUD (the phrases that cover the wait are made
    only between readings, app/fillers.py): every sentence a reader asks for,
    from its turn's wait to its clip, and each reading the page numbers
    (`Louise-Reading`) from its first sentence to its last (`/last`), or to
    READING_OPEN_S after the latest. Node-wide in fact: speech is answered by
    the web process alone."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live = 0
        self._ended = float("-inf")
        self._open: dict[str, float] = {}

    @contextmanager
    def sentence(self, reading: str | None, last: bool) -> Iterator[None]:
        with self._lock:
            self._live += 1
            if reading is not None:
                self._open[reading] = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self._live -= 1
                now = self._ended = time.monotonic()
                if reading is not None:
                    if last:
                        self._open.pop(reading, None)
                    else:
                        self._open[reading] = now

    def quiet_s(self) -> float:
        """How long nothing has been read: 0.0 while a sentence is made or a
        reading is in progress, inf when nothing was ever read."""
        with self._lock:
            if self._live:
                return 0.0
            now = time.monotonic()
            for r in [r for r, at in self._open.items() if now - at >= READING_OPEN_S]:
                del self._open[r]
            return 0.0 if self._open else max(0.0, now - self._ended)


_READINGS = Readings()


def reading_quiet_s() -> float:
    """`Readings.quiet_s` of this node's readings."""
    return _READINGS.quiet_s()


def synthesize_timed(body: dict[str, Any], url: str | None = None, *,
                     reading: str | None = None, n: int | None = None,
                     last: bool = False, account_voice: str | None = None,
                     admin: bool = True, keep: bool = True,
                     background: bool = False) -> tuple[int, bytes, str, list]:
    """`synthesize` and its `Server-Timing` (W3C; 2026-11-08, to measure the
    reading's start and its pauses): `turn` the wait for the reading's
    sentence before (`Turns`), `synth` the model server's call in ms, `audio`
    the clip's length in ms where it is a WAV. `admin` False: the reader is
    not an admin, and hears the synthetic voice of the text's language only
    (module docstring); every route a reader reaches says which it is.
    `keep` False: a NEW synthesis, never answered from the kept clips nor
    kept among them — a phrase that covers the wait is made fresh each time
    it is remade (app/fillers.py), and the kept clip of the same words would
    be the very clip just played. `background` True: work for no reader now
    (those phrases) — no question's claim, the lowest level, and no reading
    (`Readings`)."""
    if background:
        return _synthesize_timed(body, url, account_voice=account_voice, admin=admin, keep=keep,
                                 background=True)
    with _READINGS.sentence(reading, last):
        return _synthesize_timed(body, url, reading=reading, n=n, last=last, account_voice=account_voice,
                                 admin=admin, keep=keep)


def _synthesize_timed(body: dict[str, Any], url: str | None = None, *,
                      reading: str | None = None, n: int | None = None,
                      last: bool = False, account_voice: str | None = None,
                      admin: bool = True, keep: bool = True,
                      background: bool = False) -> tuple[int, bytes, str, list]:
    from app import voice as node_voice
    url = (url or config.CHAT_URL).rstrip("/")
    text = speakable(str(body.get("input") or ""))
    node = node_voice.settings_or_none()
    model = str(body.get("model") or "") or (node.tts_model or "")
    if not text:
        return 400, b'{"error": {"message": "no text to speak once its citations are removed"}}', \
            "application/json", []
    # `context`: the answer so far (app/static/voice.js), Louise's alone and
    # never relayed. A sentence too short to tell its language is spoken in
    # the language of the answer it is part of
    context = str(body.get("context") or "")[:CONTEXT_CHARS]
    out = {k: v for k, v in body.items() if k != "context"}
    out["input"] = text
    if model:
        out["model"] = model
    # the language the sentence is SPOKEN in, never a tag taken on trust
    # (`spoken_language`): what is sent is a language the engine knows
    lang = spoken_language(text, context, body.get("language"))
    if lang:
        out["language"] = lang
    else:
        out.pop("language", None)
    held = profiles(url)
    # the reader's voice — the request's (the Ask page's choice, a client's
    # own), else the one the asker's account keeps (app/users.py) — unless it
    # names a profile the server no longer holds — a browser remembering a
    # voice since removed (app/voice.py `voices`): then the node's, never an
    # error. Any other reader: the synthetic voice of the language (`reader_voice`)
    read, voice = reader_voice(lang, held, node, asked=body.get("voice"), account_voice=account_voice,
                               admin=admin)
    if not read:
        return 409, NO_SYNTHETIC, "application/json", []
    if voice:
        out["voice"] = voice
        if str(voice).startswith(PROFILE_PREFIX) and not out.get("language"):
            out["language"] = AUTO
    else:
        out.pop("voice", None)
    # THE STEPS OF A SYNTHESIS ARE THE NODE'S (`[speech] tts_steps`, operator
    # 2026-11-25): `params.flow_steps` of the request, over whatever a client
    # sent — a reader chooses no cost on the card. Unset, nothing is sent and
    # the model's own default stands. In the kept clip's key with the rest.
    steps = platform_config.tts_steps(node.tts_steps)
    if steps is not None:
        out["params"] = {**(out.get("params") if isinstance(out.get("params"), dict) else {}),
                         "flow_steps": str(steps)}
    # NUMBERS AND DATES IN THE TEXT'S OWN WORDS (operator, 2026-11-08, A): the
    # one voice reads digits as it learned them, in Italian (app/speech_numbers.py)
    from app import speech_numbers
    said = str(out.get("language") or "").split("-")[0].split("_")[0].lower() or None
    out["input"] = speech_numbers.verbalize(out["input"], said)
    # THE CLIP KEPT (operator, 2026-11-09: "keep the buffering available even
    # when I stop/start the speech"): the same request — model, voice,
    # language, words — answered from the fast drive, at once and in any order
    # — and only one the SAME ENGINE made (`_clip_key`). Not known, the model
    # not loaded yet, nothing is read from the drive: whatever is kept could be
    # another engine's; what the model makes is kept under the engine that
    # answered, read once it has.
    engine = model_load.backend_of(url, model or None) if keep else None
    kept = _clip_get(_clip_key(url, out, engine)) if engine else None
    if kept is not None:
        clip = audio_ms(kept[0])
        return 200, kept[0], kept[1], [("cache", 1.0)] + ([("audio", clip)] if clip is not None else [])
    with _TURNS.turn(reading, n) as waited:
        status, content, ctype, timing = _synthesize_call(url, model, out, last=last, background=background)
    if keep and status == 200 and str(ctype).startswith("audio/"):
        made_by = engine or model_load.backend_of(url, model or None)
        if made_by:
            _clip_put(_clip_key(url, out, made_by), content, ctype)
    return status, content, ctype, ([("turn", waited)] if reading is not None else []) + timing


def _synthesize_call(url: str, model: str | None, out: dict, *, last: bool = False,
                     background: bool = False) -> tuple[int, bytes, str, list]:
    t0 = time.perf_counter()
    # A PERSON'S QUERY, ITS LAST STEP (2026-11-08, München: one synthesis took
    # 0.81 of its clip's length alone and up to 1.49 beside the corpus
    # embedders): the call holds the node's interactive claim, so corpus
    # work stands aside from the card and the governor pins its clock, as for
    # a rerank (app/rage/dispatch.py). The claim, not the gate: speech and
    # chat share the endpoint and not the work (the operator: "Chat and TTS
    # use different GPU resources"), so speech never queues behind
    # an answer being written.
    from app.rage import dispatch
    # THE READING IS ONE QUESTION ON THE CARD (operator, 2026-11-09: "pause
    # the embeddings … the chat model … is not competing with voice, thus they
    # run together"): the claim lingers after each sentence, longer than the
    # page waits between two, so the corpus stays aside for the whole
    # reading, not only inside each synthesis.
    # The 5-minute rule supersedes both (docs/notes/ask-card-workflow.md spec
    # 6): the claim lingers dispatch.QUESTION_LINGER_S after every sentence,
    # the last one included; `last` is kept for the page and the log.
    # WORK FOR NO READER NOW (`background`: the phrases that cover the wait,
    # app/fillers.py) holds NO claim and no linger — the corpus does not stand
    # aside, the encoders keep their pins, the clock is not held — and says
    # the lowest level there is
    held = (dispatch.working_as(dispatch.Level.CORPUS) if background
            else _question_claim(dispatch))
    with held:
        if not model:
            r = httpc.post(f"{url}/v1/audio/speech", json=out, timeout=CALL_TIMEOUT_S)
        else:
            with pinned(url, [model]):
                r = httpc.post(f"{url}/v1/audio/speech", json=out, timeout=CALL_TIMEOUT_S)
    if r.status_code == 200:
        from app import model_use
        model_use.touch(url, model or out.get("model"))     # a sentence spoken (app/model_use.py)
    timing = [("synth", round((time.perf_counter() - t0) * 1000.0, 1))]
    clip = audio_ms(r.content) if r.status_code == 200 else None
    if clip is not None:
        timing.append(("audio", clip))
    return r.status_code, r.content, r.headers.get("content-type", "application/octet-stream"), timing


@contextmanager
def _question_claim(dispatch: Any) -> Iterator[None]:
    """A reader's sentence: the question's claim, at the chat level."""
    with dispatch.question(), dispatch.interactive(dispatch.Level.CHAT):
        yield
