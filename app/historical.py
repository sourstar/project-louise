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
  C. it points the model at another version the results already hold: an
     edition of the same work in the answer's language or in English ("a
     translation of it is source [k]"), or in its own language, a modern
     rendering of it ("a modern version of it is source [k]": a
     `same-work` of one language, 02 §2.10, amended 2026-11-16).

WHEN, per source, and only under Quality (Speed skips it all) — THE
HYBRID (operator's "B", 2026-11-16): whether a WORK is in an older stage
is an annotation, the `language-stage` claim (09 §9.5), attached to the hit
as a fact (`language_stage`, app/ask/annotations.py: a person's claim over
Wikidata's over the matcher's; the matcher reads the record alone). So:
  - a record with a claim is read by its claim — its stage, or not at all
    where a person said "not an older stage";
  - a record with none is read as older language only where its PASSAGE
    shows the period's markers (a modern book QUOTING Dante, read so only
    where it quotes enough to matter), or where the question asks about
    an older period and the passage has one marker;
  - a record whose language is a historical stage by its ISO 639-3 code
    (`enm`, `fro`, `gmh` …) is read as that stage.
No year decides it at answer time: the claim's matcher read the work's
first publication and its translator credit when it made the claim
(`stage_claim`, below).

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

import json
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
    #: "matcher", "wikidata", "human" (the claim's method); "text"
    #: (its passage), "question", "language code"
    why: str
    rules: list[str] = field(default_factory=list)
    translation_in: int | None = None    # the source number of its other version
    modern: bool = False                 # that version is a modern one, same language
    #: the readings of its period applied to the model's copy (app/reading.py):
    #: their version, "" where none applied
    glossary: str = ""
    #: what dated it, for a source read by its year ("its author's dates
    #: (1265–1321)"; app/reading.py `Year.by`)
    by: str = ""

    def badge(self) -> str:
        """The source's badge: what the reader sees beside it — the stage,
        by what (the claim's method, 09 §9.5), and what was done."""
        out = f"{self.stage}, " + (f"by {self.by}" if self.why == "year" and self.by
                                   else BY.get(self.why, "by " + self.why))
        if self.rules:
            out += ", spelling modernised for the model"
        if self.glossary:
            out += ", old words read with its period's glossary"
        if self.translation_in:
            out += (f", read with its modern version [{self.translation_in}]" if self.modern
                    else f", read with its translation [{self.translation_in}]")
        return out


#: what the badge says the stage was found by
BY = {"matcher": "by its text", "wikidata": "by Wikidata", "human": "by hand",
      "text": "by this passage", "question": "by this passage and the question",
      "year": "by its publication year",
      "language code": "by its language code"}
#: a claim's stage, named in plain words: the canonical ids the claims use
STAGE_NAMES: dict[str, str] = {
    "wikidata:Q135913388": "Old Italian",
    **{f"iso639-3:{code}": name for code, (_m, name) in STAGE_CODES.items()},
}


def markers(reader: Reader, text: str) -> int:
    """The period's markers in `text`: its words, its marker patterns, and
    every spelling one of its marker rules would modernise."""
    words = sum(1 for w in _WORD.findall(text.lower()) if w in reader.words)
    return (words + sum(len(r.pattern.findall(text)) for r in reader.rules if r.marker)
            + sum(len(p.findall(text)) for p in reader.patterns))


#: B's hand-written h- word list: where a year triggers the glossary, its
#: attested pairs take this rule's place (architecture, 2026-11-18, point 5)
H_RULE = "h- (huomo → uomo)"


def modernise(reader: Reader, text: str, skip: frozenset[str] = frozenset()) -> tuple[str, list[str]]:
    """B: the text with the period's spelling modernised, and the rules that
    changed something. A copy for the model to read; nothing else. `skip`:
    rules not applied (B's h- list where the glossary read the text)."""
    applied = []
    for r in reader.rules:
        if r.name in skip:
            continue
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


def stage_of(hit: dict[str, Any], *, period_asked: bool) -> tuple[Reader | None, str, str] | None:
    """(the reader, the stage named, why) for a source read as older
    language, or None. The record's claim first (`language_stage`, the
    fact app/ask/annotations.py attaches); the passage only where there is
    none. A stage code names its stage even where no reader of its modern
    language is held yet (A alone)."""
    lang = str(hit.get("lang") or "").lower()
    fact = hit.get("language_stage")
    if isinstance(fact, dict):
        stage = fact.get("stage")
        if stage is None:
            return None                         # a person said: not an older stage
        if lang in STAGE_CODES:
            lang = STAGE_CODES[lang][0]
        return READERS.get(lang), STAGE_NAMES.get(stage, stage), str(fact.get("method"))
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


def _translations(numbered: list[tuple[int, dict[str, Any]]], answer_lang: str | None
                  ) -> dict[int, tuple[int, bool]]:
    """C: source n → (the number of a source among these that is another
    edition of its work, whether it is a MODERN version): one in the
    source's own language that carries no stage of its own (a modern
    rendering, 02 §2.10 amended 2026-11-16) first, else one in the
    answer's language or in English."""
    want = {x for x in (answer_lang, "en") if x}
    by_uid = {str(h.get("uid")): (n, h) for n, h in numbered if h.get("uid")}
    out: dict[int, tuple[int, bool]] = {}
    for n, h in numbered:
        lang = str(h.get("lang") or "").lower()
        found: tuple[int, bool] | None = None
        for e in h.get("editions") or []:
            got = by_uid.get(str(e.get("uid")))
            if got is None or got[0] == n:
                continue
            k, other = got
            elang = str(e.get("lang") or other.get("lang") or "").lower()
            fact = other.get("language_stage")
            if elang == lang and not (isinstance(fact, dict) and fact.get("stage")):
                found = (k, True)
                break
            if found is None and elang in want and elang != lang:
                found = (k, False)
        if found is not None:
            out[n] = found
    return out


#: the system prompt's sentence when a source is read as older language
SYSTEM_RULE = (
    " Sources marked «older …» are written in an earlier stage of their language: read each word in "
    "the sense it had then, do not take a form of the period for an error or a modern word, and do "
    "not supply what the passage does not say. Where a source's spelling was modernised for you, it "
    "says so: its words are the source's own, its spelling modernised, and a quotation from it says "
    "so. Where a translation or a modern version of an older source is among the sources, use it to "
    "understand the original and cite both."
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
    glossed = _glossary(numbered)
    shown: dict[int, tuple[str, str]] = {}
    reads: list[Read] = []
    for n, h in numbered:
        got = stage_of(h, period_asked=period)
        g = glossed.get(n)
        if got is None and g is None:
            continue
        if got is None:
            # read by its year alone: the glossary's trigger (operator, 2026-11-18)
            got = (READERS.get(_lang(h)), f"{_LANG_NAMES.get(_lang(h), _lang(h))} of {g[0]}", "year")
        reader, stage, why = got
        text = g[1] if g is not None else str(h.get("text") or "")
        applied: list[str] = []
        if reader is not None:
            text, applied = modernise(reader, text, frozenset({H_RULE}) if g is not None else frozenset())
        suffix = f" · «{stage}»"
        year = first_published(h)
        if year is not None:
            suffix += f" · first published {year}"
        if applied:
            suffix += " · spelling modernised for reading (" + ", ".join(applied) + ")"
        if g is not None and g[2]:
            by = getattr(g[0], "by", "")
            suffix += " · old words read with its period's glossary, [in brackets]" + (f", dated by {by}" if by else "")
        other = pairs.get(n)
        if other is not None:
            suffix += (f" · a modern version of it is source [{other[0]}]" if other[1]
                       else f" · a translation of it is source [{other[0]}]")
        shown[n] = (suffix, text)
        reads.append(Read(n, stage, why, applied, other[0] if other else None,
                          bool(other and other[1]), g[2] if g is not None else "",
                          getattr(g[0], "by", "") if g is not None else ""))
    return shown, reads


_LANG_NAMES = {"it": "Italian"}


def _lang(h: dict[str, Any]) -> str:
    return str(h.get("lang") or "").lower()[:2]


def _glossary(numbered: list[tuple[int, dict[str, Any]]]) -> dict[int, tuple[int, str, str]]:
    """{n: (the record's year, the text with its period's readings, their
    version)} for the sources whose record states a year a reading holds,
    in the reading's language (app/reading.py). Nothing on any failure: no
    reading is the text as it was."""
    from . import reading
    if not reading.quality_on():
        return {}                               # under Speed: the glossary never runs (operator)
    try:
        hits = [h for _n, h in numbered]
        years = reading.years(hits)
        if not years:
            return {}
        found = reading.lookup(reading.words_of(str(hits[i].get("text") or "") for i in years))
    except Exception:   # noqa: BLE001 — the glossary is a help, never the answer's failure
        return {}
    out = {}
    for i, y in years.items():
        n, h = numbered[i]
        text, used = reading.apply(str(h.get("text") or ""), y, found, reading.CHAT, lang=_lang(h))
        if used:
            out[n] = (y, text, found.version)
    return out


def said(reads: list[Read]) -> str:
    """One line for the log: which sources were read so."""
    if not reads:
        return ""
    return "read as older language: " + "; ".join(f"[{r.n}] {r.badge()}" for r in reads)


def prepare(result: dict[str, Any], question: str, answer_lang: str | None = None, *,
            budget: int | None = None) -> tuple[dict[str, Any], list[Read]]:
    """The search's result as the answer's prompt reads it under Quality: a
    COPY whose older sources carry their `reading` (the head's words) and
    their text modernised for the model; the result itself, which the page
    and the PDF show, is untouched. Only the sources the answer is given
    are read (retrieval.answer_hits_shown), so a translation pointed at is
    one the model sees."""
    from . import retrieval
    hits = list(result.get("hits") or [])
    n = (retrieval.answer_hits_shown(hits, retrieval.ANSWER_SOURCE_CHARS if budget is None
                                      else budget) if hits else 0)
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


# --------------------------------------------------------------------------
# THE MATCHER: a whole record's stage, claimed (operator's "B" then "A",
# 2026-11-16: `language-stage` claims in gutenberg-book-editions, 09 §9.5)

#: the matcher's method, as its claims carry it (09 §9.5)
MATCHER = {"kind": "matcher", "id": "historical-markers", "version": 1}
#: the stages the matcher claims, by modern language: the stage's canonical
#: id and the year the standard's description ends it. Italian has no ISO
#: 639-3 stage; Wikidata's "Old Italian" (Q135913388: "from about 960 until
#: the founding of the Accademia della Crusca in 1582"). The 1582–1800
#: language has no item, so the matcher claims none for it: such a record is
#: read by its passages (`read_sources`), never by a claim of ours
STAGES: dict[str, tuple[str, int]] = {"it": ("wikidata:Q135913388", 1582)}
#: the medieval markers a record needs per 1,000 words to be claimed, and
#: the lower bar where the work's first publication is before the stage's end
DENSITY = 2.0
DENSITY_DATED = 0.5
CONFIDENCE, CONFIDENCE_DATED = "0.8", "0.9"
#: PG's own summary stating a first publication ("… published in 1605"):
#: marc520, as app/ask/works.py `first_published` (München's #695) reads it


def medieval_markers(reader: Reader, text: str) -> int:
    """The markers of the MEDIEVAL language alone — its words and its elided
    and split forms; not the Renaissance printing's spellings, which a
    1582–1800 book shares and the stage claimed does not cover."""
    words = sum(1 for w in _WORD.findall(text.lower()) if w in reader.words)
    return words + sum(len(p.findall(text)) for p in reader.patterns)


def _record_text(record: dict[str, Any]) -> str:
    return "\n".join(str(s[1]) for s in record.get("sections") or [] if len(s) > 1)


#: a translation STATED by the record's own title or summary (architecture,
#: 2026-11-18: "its title, subtitle, marc500 or marc520 STATES it").
#: Counted on the node's 1,112 Italian gutenberg records: 4 match, 2 of them
#: without a trl credit (54167 "…in Italiana tradotto", 40326 "Traduzione di
#: …"); PG's RDF holds no marc041, 240 or 500 for them.
_STATED = re.compile(r"\b(?:tradott[oa]|traduzione|versione\s+di|recat[oa]\s+in|volgarizzament[oi]|"
                     r"translated|translation)\b", re.IGNORECASE)


def is_translation(record: dict[str, Any]) -> bool:
    """The record's OWN CORPUS says it is a translation (architecture,
    2026-11-18): a translator among its credits (09 §9.5 `creators` and
    gutenberg's `extra.creators_detail`, MARC relator `trl`), or its title or
    its summary (marc500, marc520) stating it. A record with none of these is
    read as an original — the corpus cannot tell an unmarked translation
    (Scott's Lammermoor, 42881-42883) from one."""
    for a in (record.get("extra") or {}).get("creators_detail") or []:
        if isinstance(a, dict) and a.get("role") in ("trl", "translator"):
            return True
    if _TRANSLATED.search(" ".join(map(str, record.get("creators") or []))):
        return True
    marc = (record.get("extra") or {}).get("marc") or {}
    stated = [str(record.get("title") or ""), *(str(x) for k in ("500", "520") for x in (marc.get(k) or []))]
    return bool(_STATED.search(" ".join(stated)))


def record_first_published(record: dict[str, Any]) -> int | None:
    """The work's first publication as the record states it: a key of its
    own where a plugin writes one, else PG's summary (marc520)."""
    got = first_published(record)
    if got is not None:
        return got
    marc = (record.get("extra") or {}).get("marc") or {}
    from app.ask import works
    # THE one definition of a stated first publication (architecture, 2026-11-18)
    return works.stated_first_published([v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                                         for v in ((marc.get("520") or marc.get(520) or [])
                                                   if isinstance(marc, dict) else [])])


def stage_claim(record: dict[str, Any], contributor: str) -> dict[str, Any] | None:
    """The matcher's `language-stage` claim about one whole record, or None.
    Never for a translation, never for a language without a claimed stage,
    never for a work first published after the stage ends."""
    lang = str(record.get("lang") or "").lower()
    reader, stage = READERS.get(lang), STAGES.get(lang)
    if reader is None or stage is None or is_translation(record):
        return None
    obj, ends = stage
    text = _record_text(record)
    words = len(_WORD.findall(text))
    if words == 0:
        return None
    year = record_first_published(record)
    if year is not None and year >= ends:
        return None
    found = medieval_markers(reader, text)
    per_k = found * 1000 / words
    dated = year is not None
    if per_k < (DENSITY_DATED if dated else DENSITY):
        return None
    why = (f"{found} medieval markers in {words} words"
           + (f", first published {year}, before {ends}" if dated else ""))
    return {"type": "language-stage", "subject": record["uid"], "object": obj, "method": dict(MATCHER),
            "evidence": {"markers": found, "words": words, "per_1000_words": f"{per_k:.2f}",
                         "first_published": year, "why": why},
            "confidence": CONFIDENCE_DATED if dated else CONFIDENCE, "contributor": contributor}
