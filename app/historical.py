"""Readers for older stages of a language: how the chat model is helped to
read a source written before the language took its modern form
(operator, 2026-11-16: "A, B, C, make it part of the quality search
settings and use them only if the returned results fall into the use
case (some recent books may have ancient language quotes, also use the
prompt as trigger)"; docs/notes/historical-languages.md).

WHAT A READER DOES, on the COPY the model reads — never the corpus, never
the record, never the passage the page shows (invariant 11):
  A. it names the stage — the source's head says "older Italian", and the
     system prompt says how to read such a source (Levchenko, IRCDL 2026:
     telling the model the period cut perplexity by ~58 % on 1600s
     Italian);
  B. it modernises the SPELLING of 1500–1800 printing (long s, h- before
     a vowel, -tione, the ampersand) and says it did, so the model does
     not take a form of the period for a word it does not know;
  C. it points the model at a translation the results already hold: an
     edition of the same work in the answer's language or in English is
     named beside the older source ("a translation of it is source [k]").

WHEN, per source, and only under Quality (Speed skips it all):
  - the passage itself reads as older language — markers of the period's
    spelling and of its words, counted per passage, so a modern book that
    QUOTES Dante is read so only where it quotes enough to matter;
  - the record's language is a historical stage by its ISO 639-3 code
    (`enm`, `fro`, `gmh` …: the standard's own codes, no list of ours);
  - the QUESTION asks about an older period (a century, a period name,
    a year before 1800): then a single marker in a passage is enough.

THE HEURISTIC IS OURS, AND SAID SO. No historical-Italian normaliser or
detector exists (docs/notes/historical-languages.md, "Italian"). The
standard alternatives, for when one pays: a trained normaliser
(Norma, cSMTiser — LGPL-3, trainable on old↔modern pairs; for German and
French ByT5 normalisers already exist, served over HTTP like
TRANSLATE_URL, invariant 7), and a lexicon of historical forms (MIDIA's
571,779 form → lemma pairs, once its availability is confirmed).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

#: ISO 639-3 codes of historical stages (the standard's own individual
#: languages): a record so coded is read as older language whatever its
#: text shows. ITALIAN HAS NONE in 639-3, which is why its passages are
#: read from their text.
STAGE_CODES: dict[str, tuple[str, str]] = {
    "ang": ("en", "Old English"), "enm": ("en", "Middle English"),
    "fro": ("fr", "Old French"), "frm": ("fr", "Middle French"),
    "goh": ("de", "Old High German"), "gmh": ("de", "Middle High German"),
    "osp": ("es", "Old Spanish"), "dum": ("nl", "Middle Dutch"),
}

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class Rule:
    """One spelling rule of B: a pattern of the period's printing and its
    modern form. `name` is what the model and the log are told."""
    name: str
    pattern: re.Pattern[str]
    repl: str
    #: a word-for-word table where the rule is a list of words (h- forms)
    table: dict[str, str] | None = None


@dataclass(frozen=True)
class Reader:
    lang: str                       # ISO 639-1 of the modern language
    stage: str                      # what the source's head says
    #: words of the period that modern prose does not use (medieval and
    #: Renaissance function words and verb forms), lower case
    words: frozenset[str]
    #: spelling of the period's printing, each also a marker
    rules: tuple[Rule, ...]
    #: forms of the period that are markers only, never rewritten (an
    #: elided article, a split preposition): they say "older", and a
    #: modern spelling of them is not one form but several
    patterns: tuple[re.Pattern[str], ...] = ()
    #: markers a passage needs before it is read as older language, when
    #: the question does not ask about a period (and when it does: 1)
    threshold: int = 2


def _rule(name: str, pattern: str, repl: str, flags: int = 0,
          table: dict[str, str] | None = None) -> Rule:
    return Rule(name, re.compile(pattern, flags), repl, table)


def _keep_case(word: str, modern: str) -> str:
    return modern[:1].upper() + modern[1:] if word[:1].isupper() else modern


#: h- before a vowel in Renaissance and Baroque printing (huomo, hauere,
#: historia): THE WORDS, not every h-, since "ha", "ho", "hanno" are modern
_H_WORDS = {
    "huomo": "uomo", "huomini": "uomini", "huom": "uom", "hauere": "avere", "hauer": "aver",
    "haueua": "aveva", "haueuano": "avevano", "hebbe": "ebbe", "hebbero": "ebbero",
    "haurebbe": "avrebbe", "hauesse": "avesse", "hauendo": "avendo", "hauuto": "avuto",
    "honore": "onore", "honori": "onori", "honesto": "onesto", "humano": "umano",
    "humana": "umana", "humile": "umile", "historia": "storia", "historie": "storie",
    "hoggi": "oggi", "hora": "ora", "hore": "ore", "huopo": "uopo",
}
_H_PATTERN = r"\b(" + "|".join(sorted(_H_WORDS, key=len, reverse=True)) + r")\b"

ITALIAN = Reader(
    lang="it", stage="older Italian",
    words=frozenset({
        # medieval (Duecento–Trecento) forms: Dante, Boccaccio, the stilnovo
        "sanza", "puote", "fia", "fieno", "fue", "fuoro", "dee", "deggio", "veggio", "aggio",
        "avea", "avean", "aveano", "facea", "dicea", "potea", "vedea", "cotale", "cotanto",
        "guari", "chente", "imperò", "conciossiacosaché", "mestieri", "avvegna", "allotta",
        "omai", "unquanco", "ancidere", "fora", "eglino", "elleno", "ched", "neente", "niuno",
        "veruno", "stea", "richesto", "adunque", "fiata", "fiate", "femina", "femine",
        "dimandò", "dimandare", "nimico", "nimici", "appo", "spirto", "anco", "uopo",
        # Renaissance–Baroque forms
        "doppo", "puotè", "istesso", "conciosia", "laonde", "imperocché", "perciocché",
    }),
    rules=(
        _rule("ſ → s", r"ſ", "s"),
        _rule("&/et → e", r"(?<=\s)(?:&|et)(?=\s)", "e"),
        # -tione / -ttione → -zione, never -stione (questione, gestione)
        _rule("-tione → -zione", r"(?<=[^\Ws])t?tion([ei])\b", r"zion\1"),
        _rule("h- (huomo → uomo)", _H_PATTERN, "", re.IGNORECASE, table=_H_WORDS),
    ),
    patterns=(
        # the elided article and pronoun of the verse: che 'l, e 'n, i' (io)
        re.compile(r"(?<!\w)['’](?:l|n|mpero\w*)\b"),
        re.compile(r"\bi['’](?=\s)"),
        # the preposition written apart from its article: de la, ne lo, a li
        re.compile(r"\b(?:de|ne|da|a|co)\s+(?:la|lo|le|li|l['’])\b", re.IGNORECASE),
    ),
)

#: one reader per modern language; the others are to come
#: (docs/notes/historical-languages.md: Italian first)
READERS: dict[str, Reader] = {ITALIAN.lang: ITALIAN}

#: a question about an older period: a century by number or by name, a
#: period, a year before 1800. Italian and English, the languages of the
#: readers held; a reader's language joins with its reader.
_ROMAN = r"(?:xix|xviii|xvii|xvi|xv|xiv|xiii|xii|xi|x|ix|viii|vii|vi|v|iv|iii|ii|i)"
_PERIOD = re.compile(
    r"\b(?:duecento|trecento|quattrocento|cinquecento|seicento|settecento|ottocento|"
    r"medioevo|medievale|medioevale|rinascimento|rinascimentale|barocco|volgare|"
    r"italiano antico|antico italiano|lingua antica|"
    r"medieval|middle ages|renaissance|baroque|early modern|old italian|archaic|"
    rf"{_ROMAN}\s*(?:secolo|sec\.)|secolo\s+{_ROMAN}|"
    r"(?:1[0-9]|[2-9])(?:th|st|nd|rd)[- ]century|1[0-7]\d\ds?)\b",
    re.IGNORECASE)


def asks_about_period(question: str) -> bool:
    """The question names an older period: the prompt as the trigger."""
    return bool(_PERIOD.search(question or ""))


@dataclass
class Read:
    """How one source was read: its stage, why, and the rules applied."""
    n: int
    stage: str
    why: str                         # "text", "language code", "question"
    rules: list[str] = field(default_factory=list)
    translation_in: int | None = None    # the source number of its translation


def markers(reader: Reader, text: str) -> int:
    """The period's markers in `text`: its words, and every spelling one of
    its rules would modernise."""
    words = sum(1 for w in _WORD.findall(text.lower()) if w in reader.words)
    return (words + sum(len(r.pattern.findall(text)) for r in reader.rules)
            + sum(len(p.findall(text)) for p in reader.patterns))


def modernise(reader: Reader, text: str) -> tuple[str, list[str]]:
    """B: the text with the period's spelling modernised, and the rules that
    changed something. A copy for the model to read; nothing else."""
    applied = []
    for r in reader.rules:
        if r.table is not None:
            table = r.table
            new = r.pattern.sub(lambda m: _keep_case(m.group(1), table[m.group(1).lower()]), text)
        else:
            new = r.pattern.sub(r.repl, text)
        if new != text:
            applied.append(r.name)
            text = new
    return text, applied


def stage_of(hit: dict[str, Any], *, period_asked: bool) -> tuple[Reader | None, str, str] | None:
    """(the reader, the stage named, why) for a source read as older
    language, or None. A stage code names its stage even where no reader
    of its modern language is held yet (A alone)."""
    lang = str(hit.get("lang") or "").lower()
    if lang in STAGE_CODES:
        modern, stage = STAGE_CODES[lang]
        return READERS.get(modern), stage, "language code"
    reader = READERS.get(lang)
    if reader is None:
        return None
    found = markers(reader, str(hit.get("text") or ""))
    if period_asked and found >= 1:
        return reader, reader.stage, "question"
    if found >= reader.threshold:
        return reader, reader.stage, "text"
    return None


def _translations(hits: list[dict[str, Any]], answer_lang: str | None) -> dict[int, int]:
    """C: source n → the number of a source among these that is another
    edition of its work, in the answer's language or in English."""
    want = {x for x in (answer_lang, "en") if x}
    by_uid = {str(h.get("uid")): i for i, h in enumerate(hits, 1) if h.get("uid")}
    out: dict[int, int] = {}
    for i, h in enumerate(hits, 1):
        for e in h.get("editions") or []:
            j = by_uid.get(str(e.get("uid")))
            if j is not None and j != i and str(e.get("lang") or "").lower() in want:
                out[i] = j
                break
    return out


#: the system prompt's sentence when a source is read as older language
SYSTEM_RULE = (
    " Sources marked «older …» are written in an earlier stage of their language: read each word in "
    "the sense it had then, do not take a form of the period for an error or a modern word, and do "
    "not supply what the passage does not say. Where a source's spelling was modernised for you, it "
    "says so; its words are the source's own. Where a translation of an older source is among the "
    "sources, use it to understand the original and cite both."
)


def read_sources(hits: Iterable[dict[str, Any]], question: str, answer_lang: str | None = None
                 ) -> tuple[dict[int, tuple[str, str]], list[Read]]:
    """For the answer's numbered sources: {n: (head suffix, text to read)}
    for each source read as older language, and how each was read."""
    hits = list(hits)
    period = asks_about_period(question)
    pairs = _translations(hits, answer_lang)
    shown: dict[int, tuple[str, str]] = {}
    reads: list[Read] = []
    for i, h in enumerate(hits, 1):
        got = stage_of(h, period_asked=period)
        if got is None:
            continue
        reader, stage, why = got
        text = str(h.get("text") or "")
        applied: list[str] = []
        if reader is not None:
            text, applied = modernise(reader, text)
        suffix = f" · «{stage}»"
        if applied:
            suffix += " · spelling modernised for reading (" + ", ".join(applied) + ")"
        if i in pairs:
            suffix += f" · a translation of it is source [{pairs[i]}]"
        shown[i] = (suffix, text)
        reads.append(Read(i, stage, why, applied, pairs.get(i)))
    return shown, reads


def said(reads: list[Read]) -> str:
    """One line for the reader and the log: which sources were read so."""
    if not reads:
        return ""
    parts = []
    for r in reads:
        bits = [r.stage, f"by its {r.why}" if r.why != "question" else "the question asks about the period"]
        if r.rules:
            bits.append("spelling modernised for reading")
        if r.translation_in:
            bits.append(f"with its translation [{r.translation_in}]")
        parts.append(f"[{r.n}] " + ", ".join(bits))
    return "Read as older language: " + "; ".join(parts) + "."


def prepare(result: dict[str, Any], question: str, answer_lang: str | None = None
            ) -> tuple[dict[str, Any], list[Read]]:
    """The search's result as the answer's prompt reads it under Quality: a
    COPY whose older sources carry their `reading` (the head's words) and
    their text modernised for the model; the result itself, which the page
    and the PDF show, is untouched. Only the sources the answer is given
    are read (retrieval.answer_hits_shown), so a translation pointed at is
    one the model sees."""
    from . import retrieval
    hits = list(result.get("hits") or [])
    n = retrieval.answer_hits_shown(hits) if hits else 0
    shown, reads = read_sources([{**h, "text": str(h.get("text") or "")[:1800]} for h in hits[:n]],
                                question, answer_lang)
    if not shown:
        return result, []
    out = []
    for i, h in enumerate(hits, 1):
        if i in shown:
            suffix, text = shown[i]
            h = {**h, "reading": suffix, "text": text}
        out.append(h)
    return {**result, "hits": out}, reads
