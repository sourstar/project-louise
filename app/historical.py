"""Readers for older stages of a language: how the chat model is helped to
read a source written before the language took its modern form
(operator, 2026-11-16: "A, B, C, make it part of the quality search
settings and use them only if the returned results fall into the use
case (some recent books may have ancient language quotes, also use the
prompt as trigger)"; docs/notes/historical-languages.md; reviewed by the
architecture session the same day, through München).

WHAT A READER DOES, on the COPY the model reads — never the corpus, never
the record, never the passage the page shows (invariant 11):
  A. it names the stage — the source's `reading` says "older Italian",
     and the system prompt says how to read such a source (Levchenko,
     IRCDL 2026: telling the model the period cut perplexity by ~58 % on
     1600s Italian);
  B. it modernises the SPELLING of 1500–1800 printing (long s, h- before
     a vowel, -tione) and says it did, so the model does not take a form
     of the period for a word it does not know. "&" is never rewritten,
     nor "et" before al., cetera, seq. (Procter & Gamble et al.);
  C. it points the model at a translation the results already hold: an
     edition of the same work in the answer's language or in English is
     named beside the older source ("a translation of it is source [k]").

WHEN, per source, and only under Quality (Speed skips it all):
  - the passage itself reads as older language — markers of the period's
    words and spelling, counted per passage, so a modern book that QUOTES
    Dante is read so only where it quotes enough to matter;
  - the record's language is a historical stage by its ISO 639-3 code
    (`enm`, `fro`, `gmh` …: the standard's own codes, no list of ours);
  - the work was first published before the reader's `until`
    (`first_published`, the one date field a hit carries: the WORK's
    first publication, never PG's release or the edition's year), and
    this edition is the work in its own language — no translator among
    its credits, and its language the work's (`work_lang`) where that is
    known; where it is not known, the year lowers the bar to one marker
    instead, so a modern Italian Shakespeare is not read as 1600s Italian;
  - the QUESTION asks about an older period (a century, a period name,
    a year before 1800): then a single marker in a passage is enough.

ONE FUNCTION FOR BOTH ANSWER PATHS (architecture, 2026-11-16): `prepare`
for api._answer_messages, `read_sources` for app/ask/chat.py's tool
results, so Quality means the same on both. The reader sees which sources
were read so as a BADGE on the source (`reading` in api.source_entries),
never as a line in the answer: speech would speak it, the PDF print it,
and the model did not write it.

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
    modern form. `name` is what the model and the log are told; `marker`
    says whether a match also counts as a sign of older language (not
    "et": Latin and modern citation use it too)."""
    name: str
    pattern: re.Pattern[str]
    repl: str
    #: a word-for-word table where the rule is a list of words (h- forms)
    table: dict[str, str] | None = None
    marker: bool = True


@dataclass(frozen=True)
class Reader:
    lang: str                       # ISO 639-1 of the modern language
    stage: str                      # what the source's reading says
    #: words of the period that modern prose does not use, lower case
    words: frozenset[str]
    #: spelling of the period's printing
    rules: tuple[Rule, ...]
    #: forms of the period that are markers only, never rewritten (an
    #: elided article, a split preposition): a modern spelling of them is
    #: not one form but several
    patterns: tuple[re.Pattern[str], ...] = ()
    #: markers a passage needs when nothing else says it is older
    threshold: int = 2
    #: a work first published before this year is older language
    until: int = 1800


def _keep_case(word: str, modern: str) -> str:
    return modern[:1].upper() + modern[1:] if word[:1].isupper() else modern


#: h- before a vowel in Renaissance and Baroque printing (huomo, hauere,
#: historia): THE WORDS, not every h-, since "ha", "ho", "hanno" are modern
_H_WORDS = {
    "huomo": "uomo", "huomini": "uomini", "huom": "uom", "hauere": "avere", "hauer": "aver",
    "hauea": "avea", "haueano": "aveano", "haueua": "aveva", "haueuano": "avevano",
    "hebbe": "ebbe", "hebbero": "ebbero", "haurebbe": "avrebbe", "hauesse": "avesse",
    "hauendo": "avendo", "hauuto": "avuto", "honore": "onore", "honori": "onori",
    "honesto": "onesto", "humano": "umano", "humana": "umana", "humile": "umile",
    "historia": "storia", "historie": "storie", "hoggi": "oggi", "hora": "ora", "hore": "ore",
}
_H_PATTERN = re.compile(r"\b(" + "|".join(sorted(_H_WORDS, key=len, reverse=True)) + r")\b",
                        re.IGNORECASE)

ITALIAN = Reader(
    lang="it", stage="older Italian",
    words=frozenset({
        # medieval (Duecento–Trecento) forms: Dante, Boccaccio, the stilnovo.
        # NOT dee (goddesses), uopo (all'uopo, everyday legal Italian), anco,
        # fora: modern too (architecture's review, 2026-11-16)
        "sanza", "puote", "fia", "fieno", "fue", "fuoro", "deggio", "veggio", "aggio",
        "avea", "avean", "aveano", "facea", "dicea", "potea", "vedea", "cotale", "cotanto",
        "guari", "chente", "imperò", "conciossiacosaché", "mestieri", "avvegna", "allotta",
        "omai", "unquanco", "ancidere", "eglino", "elleno", "ched", "neente", "niuno",
        "veruno", "stea", "richesto", "adunque", "fiata", "fiate", "femina", "femine",
        "dimandò", "dimandare", "nimico", "nimici", "appo", "spirto",
        # Renaissance–Baroque forms
        "doppo", "puotè", "istesso", "conciosia", "laonde", "imperocché", "perciocché",
    }),
    rules=(
        Rule("ſ → s", re.compile("ſ"), "s"),
        # "et" for "e", never "&" and never et al. / et cetera / et seq.;
        # not a marker: Latin and citations use it in modern prose
        Rule("et → e", re.compile(r"(?<=\s)et(?=\s)(?!\s+(?:al\b|cetera\b|seq\b))"), "e",
             marker=False),
        # -tione / -ttione → -zione, never -stione (questione, gestione)
        Rule("-tione → -zione", re.compile(r"(?<=[^\Ws])t?tion([ei])\b"), r"zion\1"),
        Rule("h- (huomo → uomo)", _H_PATTERN, "", table=_H_WORDS),
    ),
    patterns=(
        # the elided article and pronoun of the verse: che 'l, e 'n, ch'i'
        re.compile(r"(?<!\w)['’](?:l|n|mpero\w*)\b"),
        re.compile(r"\bi['’](?=\s)"),
        # the preposition written apart from its article — LOWER CASE and
        # before a lower-case word, so Pedro de la Cruz is not one
        re.compile(r"\b(?:de|ne|da|a|co) (?:la|lo|le|li) (?=[a-zà-ù])|\b(?:de|ne|da|co) l['’](?=[a-zà-ù])"),
    ),
)

#: one reader per modern language; the others are to come
#: (docs/notes/historical-languages.md: Italian first)
READERS: dict[str, Reader] = {ITALIAN.lang: ITALIAN}

_ROMAN = r"(?:xix|xviii|xvii|xvi|xv|xiv|xiii|xii|xi|x|ix|viii|vii|vi|v|iv|iii|ii|i)"
#: a question about an older period: a century by number or by name, a
#: period, a year before 1800. Italian and English, the languages of the
#: readers held; a reader's language joins with its reader.
_PERIOD = re.compile(
    r"\b(?:duecento|trecento|quattrocento|cinquecento|seicento|settecento|ottocento|"
    r"medioevo|medievale|medioevale|rinascimento|rinascimentale|barocco|volgare|"
    r"italiano antico|antico italiano|lingua antica|"
    r"medieval|middle ages|renaissance|baroque|early modern|old italian|archaic|"
    rf"{_ROMAN}\s*(?:secolo|sec\.)|secolo\s+{_ROMAN}|"
    r"(?:1[0-9]|[2-9])(?:th|st|nd|rd)[- ]century|1[0-7]\d\ds?)\b",
    re.IGNORECASE)

#: a translator among a hit's credits (Gutenberg's display string names
#: each non-author by role, app/plugins/gutenberg/rdf.py `_ROLE_LABEL`)
_TRANSLATED = re.compile(r"\((?:translator|trl)\)|\btranslated by\b|\btradott[oa] da\b",
                         re.IGNORECASE)


def asks_about_period(question: str) -> bool:
    """The question names an older period: the prompt as the trigger."""
    return bool(_PERIOD.search(question or ""))


@dataclass
class Read:
    """How one source was read: its stage, why, and the rules applied."""
    n: int
    stage: str
    why: str                         # "text", "language code", "year", "question"
    rules: list[str] = field(default_factory=list)
    translation_in: int | None = None    # the source number of its translation

    def badge(self) -> str:
        """The source's badge: what the reader sees beside it."""
        out = self.stage
        if self.rules:
            out += ", spelling modernised for the model"
        if self.translation_in:
            out += f", read with its translation [{self.translation_in}]"
        return out


def markers(reader: Reader, text: str) -> int:
    """The period's markers in `text`: its words, its marker patterns, and
    every spelling one of its marker rules would modernise."""
    words = sum(1 for w in _WORD.findall(text.lower()) if w in reader.words)
    return (words + sum(len(r.pattern.findall(text)) for r in reader.rules if r.marker)
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


def first_published(hit: dict[str, Any]) -> int | None:
    """The WORK's first publication year (`first_published`, architecture
    2026-11-16), where a source states it; None otherwise."""
    v = hit.get("first_published")
    if isinstance(v, bool) or v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _own_language(hit: dict[str, Any], lang: str) -> bool | None:
    """This edition is the work in its own language: False for a
    translation, True where the work's language is known to be this one,
    None where that is not known."""
    credits = " ".join(str(hit.get(k) or "") for k in ("authors", "credits"))
    if _TRANSLATED.search(credits):
        return False
    work_lang = str(hit.get("work_lang") or "").lower()
    if work_lang:
        return work_lang == lang
    return None


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
    year = first_published(hit)
    if year is not None and year < reader.until:
        own = _own_language(hit, lang)
        if own:
            return reader, reader.stage, "year"
        if own is None and found >= 1:
            return reader, reader.stage, "year"
    if period_asked and found >= 1:
        return reader, reader.stage, "question"
    if found >= reader.threshold:
        return reader, reader.stage, "text"
    return None


def _translations(numbered: list[tuple[int, dict[str, Any]]], answer_lang: str | None) -> dict[int, int]:
    """C: source n → the number of a source among these that is another
    edition of its work, in the answer's language or in English."""
    want = {x for x in (answer_lang, "en") if x}
    by_uid = {str(h.get("uid")): n for n, h in numbered if h.get("uid")}
    out: dict[int, int] = {}
    for n, h in numbered:
        for e in h.get("editions") or []:
            k = by_uid.get(str(e.get("uid")))
            if k is not None and k != n and str(e.get("lang") or "").lower() in want:
                out[n] = k
                break
    return out


#: the system prompt's sentence when a source is read as older language
SYSTEM_RULE = (
    " Sources marked «older …» are written in an earlier stage of their language: read each word in "
    "the sense it had then, do not take a form of the period for an error or a modern word, and do "
    "not supply what the passage does not say. Where a source's spelling was modernised for you, it "
    "says so: its words are the source's own, its spelling modernised, and a quotation from it says "
    "so. Where a translation of an older source is among the sources, use it to understand the "
    "original and cite both."
)


def read_sources(numbered: Iterable[tuple[int, dict[str, Any]]], question: str,
                 answer_lang: str | None = None
                 ) -> tuple[dict[int, tuple[str, str]], list[Read]]:
    """For the sources the model is given, each with its number:
    {n: (the head's words, the text to read)} for each source read as
    older language, and how each was read."""
    numbered = list(numbered)
    period = asks_about_period(question)
    pairs = _translations(numbered, answer_lang)
    shown: dict[int, tuple[str, str]] = {}
    reads: list[Read] = []
    for n, h in numbered:
        got = stage_of(h, period_asked=period)
        if got is None:
            continue
        reader, stage, why = got
        text = str(h.get("text") or "")
        applied: list[str] = []
        if reader is not None:
            text, applied = modernise(reader, text)
        suffix = f" · «{stage}»"
        year = first_published(h)
        if year is not None:
            suffix += f" · first published {year}"
        if applied:
            suffix += " · spelling modernised for reading (" + ", ".join(applied) + ")"
        if n in pairs:
            suffix += f" · a translation of it is source [{pairs[n]}]"
        shown[n] = (suffix, text)
        reads.append(Read(n, stage, why, applied, pairs.get(n)))
    return shown, reads


def said(reads: list[Read]) -> str:
    """One line for the log: which sources were read so."""
    if not reads:
        return ""
    return "read as older language: " + "; ".join(f"[{r.n}] {r.badge()} (by its {r.why})"
                                                  for r in reads)


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
    shown, reads = read_sources(
        [(i, {**h, "text": str(h.get("text") or "")[:1800]}) for i, h in enumerate(hits[:n], 1)],
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
