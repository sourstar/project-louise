"""HANDS-FREE: what an utterance heard with the microphone left on is
(docs/notes/hands-free-voice.md §3–5, step 1; operator, 2026-11-26: "keep
the mic on, summoning the chat by starting a phrase with its name
'Philippe...', and possibly pause or stop it 'Philippe wait/stop/hold on',
resume, or ask a follow up question"; decided 2026-11-27: 1A, 2A, 3).

Decision 1A — THE NAME ON THE TEXT: the node's own STT writes the utterance
and its FIRST WORD says whether it was addressed, matched against the
name's written forms (the STT spells "Philippe" Philip, Filippo, Felipe…),
case- and accent-folded. A leading "hey"/"ok"/"ehi" is allowed before it.
The standardized alternative is a phonetic key (Double Metaphone), which
needs a library; a wake-word model (openWakeWord over Wyoming, 1B) is the
fallback if M2 or M4 fail.

Decision 2A — THE ECHO GUARD: the microphone stays open while the answer
is read; an utterance whose words are, for the most part, words of the
sentences just read (`said`, sent by the page) is the node's own voice and
is dropped, whatever it starts with.

Decision 3 — THE COMMANDS, a short closed list after the name (`COMMANDS`):
wait, resume, stop; the name alone is `listen` (the next utterance needs
no name); anything else is a question. CONTINUATION: within the window the
page keeps after an addressed utterance (`follow`), an utterance is taken
without the name.

PRIVACY: an utterance that is not addressed is never returned, logged,
kept or traced: `heard` gives no text for it, and nothing here writes one.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: the name when the node sets none (`[speech] wake_name`)
DEFAULT_NAME = "Philippe"
#: the written forms the STT gives a name, folded; seeded for the default
#: (hands-free-voice.md §3), to be grown from M2's measurement
FORMS = {"philippe": ("philippe", "philip", "phillip", "philipp", "philipe", "filippo", "filipe",
                      "felipe", "filip", "phillipe", "philippa")}
#: words a reader may say before the name
LEAD = ("hey", "hei", "ehi", "ok", "okay", "oh", "ehm")
#: the commands, folded, by language as Decision 3 lists them
COMMANDS: dict[str, tuple[str, ...]] = {
    "wait": ("wait", "hold on", "wait a moment", "one moment", "pause",
             "aspetta", "un momento", "pausa", "attendi",
             "attends", "attendez", "un instant", "pause"),
    "resume": ("resume", "go on", "continue", "carry on",
               "continua", "vai avanti", "riprendi", "vai",
               "vas y", "continue", "continuez", "reprends"),
    "stop": ("stop", "stop it", "enough", "basta", "fermati", "ferma", "arrete", "arretez", "stoppe"),
}
#: a share of an utterance's words found in what was just read, at or over
#: which it is the node's own voice (Decision 2A)
ECHO_SHARE = 0.8
#: an utterance this short (in words) is never taken for an echo: "stop"
#: said over a sentence that holds "stop"
ECHO_MIN_WORDS = 3


def fold(text: str) -> str:
    """Lower case, accents off, punctuation to spaces, spaces collapsed."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^\w]+", " ", t).strip()


def forms_of(name: str | None) -> tuple[str, ...]:
    """The written forms of `name`, folded: the seeded list for a known
    name, the name itself otherwise."""
    key = fold(name or DEFAULT_NAME)
    return FORMS.get(key, (key,))


@dataclass(frozen=True)
class Heard:
    addressed: bool
    #: "wait" | "resume" | "stop" | "listen" | None (a question)
    command: str | None = None
    #: the question, without the name; None for a command or an unaddressed utterance
    text: str | None = None
    #: why it was dropped, for the page's counts — never its words
    dropped: str | None = None

    def as_dict(self) -> dict:
        return {"addressed": self.addressed, "command": self.command, "text": self.text,
                **({"dropped": self.dropped} if self.dropped else {})}


def _echo(words: list[str], said: str) -> bool:
    if len(words) < ECHO_MIN_WORDS or not said:
        return False
    read = set(fold(said).split())
    return sum(w in read for w in words) / len(words) >= ECHO_SHARE


def _run(words: list[str], said: str) -> bool:
    """Whether `words` are an unbroken run of the words of `said`."""
    if not said or not words:
        return False
    read = fold(said).split()
    n = len(words)
    return any(read[i:i + n] == words for i in range(len(read) - n + 1))


def _rest(text: str, skip: int) -> str:
    """`text` without its first `skip` words, and the separators after them."""
    parts = re.split(r"(\s+)", text.strip())
    words = [i for i, p in enumerate(parts) if p and not p.isspace()]
    if skip >= len(words):
        return ""
    return "".join(parts[words[skip]:]).lstrip(" ,.;:!?-–—").strip()


def _command(rest: str) -> str | None:
    f = fold(rest)
    for cmd, phrases in COMMANDS.items():
        if f in phrases:
            return cmd
    return None


def heard(text: str, *, name: str | None = None, follow: bool = False, said: str = "") -> Heard:
    """What the utterance `text` is: addressed by the name (or `follow`, in
    the continuation window), a command or a question; the node's own voice
    (`said`, what was just read) or not addressed is dropped, with no text.

    THE NAME AND A COMMAND COME FIRST, never taken for an echo (architecture's
    read of 535b2f68, 2: "Philip, go on" over a reading of "Philip the
    Second would go on…" was dropped — history answers are full of Philip,
    Felipe, Philipp) — UNLESS the whole utterance, name included, is an
    unbroken run of the words just read (`_run`: a true echo). The echo
    share counts only the words AFTER the lead and the name."""
    words = fold(text).split()
    if not words:
        return Heard(False, dropped="nothing said")
    # A TRUE ECHO, whatever it holds (the operator's default): the whole
    # utterance, name included, an unbroken run of the words just read —
    # the node reading "…Philippe, stop…" aloud
    if _run(words, said):
        return Heard(False, dropped="echo")
    forms = forms_of(name)
    at = 1 if words[0] in LEAD and len(words) > 1 else 0
    if words[at] in forms:
        rest = _rest(text, at + 1)
    elif follow:
        rest = text.strip()
    else:
        return Heard(False, dropped="echo" if _echo(words, said) else "not addressed")
    if not fold(rest):
        return Heard(True, command="listen")
    cmd = _command(rest)
    if cmd:
        return Heard(True, command=cmd)
    if _echo(fold(rest).split(), said):
        return Heard(False, dropped="echo")
    return Heard(True, text=rest)
