"""Speech for the programs that read Louise's answers aloud (operator,
2026-11-06: Open WebUI's voice on LocalAI's omnivoice, the Philippe voice
for Italian and any voice for the other languages; "it doesn't skip the
references like [7] but it pronounces them"; "keep the text to speech
pinned as long as there is text to process").

`POST /v1/audio/speech`, OpenAI's shape, is relayed to the model server's
own `/v1/audio/speech` after the three things only Louise knows:

- THE CITATIONS GO. "[3]", "[3] [4]", "[3]-[5]", "[gutenberg:123]" are
  references for the eye; read aloud they are noise. A client cleans the
  text before sending it, but not of these: Open WebUI drops markdown and
  footnotes, keeps "[7]", and turns two adjacent ones into a bare number
  (its link pattern reads "[3][4]" as a reference link). So `/v1` writes
  adjacent citations with a space between them (`spaced_citations`), and
  this module removes them.
  In French answers they come in more shapes ("[7 ; 8]", "[7 et 8]",
  "[ 7 ]", "[réf. 7]", "【7】"; operator, 2026-11-07: "pronounces the
  citations"): a bracket holding numbers and only the words that join or
  name them is a citation (`_is_citation`). A bracket with a number that is
  left in the text is said in a warn line, so the next shape is seen.
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
  released IDLE_S after the last.

THE LANGUAGE is told by the commonest words of the languages Louise's
collections hold — enough to tell the model which language it reads. THE
STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"): a language
identifier — lingua-py (n-gram models, 75 languages) or langid.py — is the
named tool; it is a new dependency and pays once voices exist for more
languages than these word lists cover, or once a short sentence picks the
wrong language.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from typing import Any

from app import config, httpc, model_pins

#: the pin is released this long after the last sentence: a client sends the
#: next sentence well within it, and a reader who stopped frees the card
IDLE_S = 120.0
#: one sentence's synthesis, on a model that may have to load first
CALL_TIMEOUT_S = 180.0
#: the profiles are read again after this long: one recorded meanwhile is used
PROFILES_TTL_S = 60.0
HOLDER = "speech"

# a bracket, with the space before it: "[…]", "\\[…\\]" (escaped markdown),
# "【…】" (the brackets some chat models cite with)
_BRACKET = re.compile(r"\s*(?:\\?\[([^\[\]\\\n]{1,48})\\?\]|【([^【】\n]{1,48})】)")
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
_RUN = re.compile("\x00(?:\\s*[-–—,;]?\\s*\x00)*")
_DIGIT_BRACKET = re.compile(r"[\[【][^\[\]【】]*\d[^\[\]【】]*[\]】]")
_SPACE_BEFORE_MARK = re.compile(r"\s+([.,;:!?»)])")

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
    """A bracket's content is a citation: a record's key, or numbers with
    only the words and marks that join or name them ("7", "7 ; 8",
    "7 et 8", "réf. 7", "3-5"). "[IV]", "[a]", "[art. 3]" are not."""
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


def speakable(text: str) -> str:
    """The text without its citations, spaced as prose: each citation, and a
    run of them joined by a dash or a comma ("[3]-[5]"), goes with the space
    before it."""
    def one(m: re.Match) -> str:
        return "\x00" if _is_citation(m.group(1) or m.group(2) or "") else m.group(0)
    out = _RUN.sub("", _BRACKET.sub(one, text.replace("\x00", "")))
    out = _SPACE_BEFORE_MARK.sub(r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    left = _DIGIT_BRACKET.search(out)
    if left:
        print(f"[speech] warn: a bracket with a number is read aloud: {left.group(0)!r} "
              f"(app/speech.py _is_citation)", file=sys.stderr)
    return out


def spaced_citations(text: str, tail: str = "") -> str:
    """Adjacent citations with a space between them ("[3][4]" → "[3] [4]"),
    and a citation followed by a parenthesis ("[3](" → "[3] ("), so a
    client's markdown cleaning does not read them as a link and keep the
    number alone.
    `tail`: what came before `text` in a stream, so a pair split across two
    chunks is spaced too."""
    if text[:1] in "[(" and text and re.search(r"\d\]$", tail):
        text = " " + text
    return re.sub(r"(?<=\d\])(?=\[\d|\()", " ", text)


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
_timer: threading.Timer | None = None


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


def _keep_loaded(url: str, model: str) -> None:
    """Pinned while sentences come; released IDLE_S after the last."""
    global _timer
    model_pins.hold(url, model, HOLDER)
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(IDLE_S, model_pins.release, args=(HOLDER,))
        _timer.daemon = True
        _timer.start()


def synthesize(body: dict[str, Any], url: str | None = None) -> tuple[int, bytes, str]:
    """(status, audio or error body, content type) for an OpenAI speech
    request, relayed to the model server."""
    url = (url or config.CHAT_URL).rstrip("/")
    text = speakable(str(body.get("input") or ""))
    model = str(body.get("model") or "")
    if not text:
        return 400, b'{"error": {"message": "no text to speak once its citations are removed"}}', \
            "application/json"
    out = {**body, "input": text}
    lang = language(text)
    voice = voice_for(body.get("voice") or None, profiles(url))
    if voice:
        out["voice"] = voice
    else:
        out.pop("voice", None)
    if lang and "language" not in out:
        out["language"] = lang
    if model:
        _keep_loaded(url, model)
    r = httpc.post(f"{url}/v1/audio/speech", json=out, timeout=CALL_TIMEOUT_S)
    return r.status_code, r.content, r.headers.get("content-type", "application/octet-stream")
