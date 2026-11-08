"""The `legal_article` chunker (2026-11-08; docs/notes/legal-chunking-retrieval.md):
one chunk per article, a longer article split at its OWN structure, and no
chunk ever longer than the encoder's window — NOTHING IS TRUNCATED, NOTHING
DROPPED (operator, 2026-11-08: a chunk truncated at the window "is not
admissible, it would break the meaning of important parts").

The studies it follows (the note gives the numbers and the citations):
  * the unit is the article — every statute benchmark labels at article
    level (BSARD, COLIEE Task 3, JuriFindIT, GerLayQA) — and chunking by
    the document's own hierarchy beats fixed-size windows on legislation
    (NitiBench, EMNLP 2025; StrucChunk 2026; Lima 2024);
  * an article past the window is split at the next unit down its
    hierarchy, as the drafting manuals number them, packed back up to the
    window so no piece is needlessly short:
      1. the PARAGRAPH: an Italian comma and an EU paragraph `1.`, `2-bis.`
         (Circolare 20 aprile 2001; Joint Practical Guide), a German Absatz
         `(1)`, `(2a)` (Handbuch der Rechtsförmlichkeit), a French
         numbered part `I. —`, `II. –` (Guide de légistique);
      2. the POINT: `a)`, `(a)`, `aa)`, `1°`, `(i)`;
      3. the SENTENCE, at a full stop, semicolon or colon followed by a
         capital or a digit, never after the abbreviations statutes use
         (`art.`, `Abs.`, `n.`, `Nr.` …);
      4. last, for a single sentence still past the window, consecutive
         token windows of the pinned tokenizer — the text continues in the
         next chunk, it is never cut off;
  * `header` (a parameter, inside `space_id`): the section's label — the
    act and the article, as the plugin writes it — repeated at the head of
    every piece, the deterministic form of contextual chunk headers
    (Anthropic, "Contextual Retrieval", 2024: −35 % retrieval failures;
    Summary-Augmented Chunking, NLLP 2025). Counted inside the window.

The window is `max_tokens` tokens of the tokenizer the layer pins by the
sha256 of its tokenizer.json (as `wikidata-text`, app/plugins/wikidata/
chunker.py, which loads it); the recipe sets it to the space's `ctx` less
the document instruction's tokens, so what the encoder receives fits.

THE STANDARD, NOT ADOPTED WHOLE: LangChain's RecursiveCharacterTextSplitter
is the same recursion over separators (Chroma's evaluation found it
competitive); here the separators are the statutes' own numbering and the
bound is in the encoder's tokens, which it does not count. Late chunking
(Günther et al. 2024) is the measured alternative for long articles
(+0.5 to +6.5 nDCG@10) and needs the whole article in one window; it is
written down in the note, not built.

A pure function of (sections, version, params) and the pinned tokenizer
(invariant 3). Chunk ids `<uid>:<seq>:<version>`.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Sequence

CHUNK_VERSION = 1
NAME = "legal_article"

#: 1. a paragraph's number at the start of a line, in the four systems
PARAGRAPH = re.compile(
    r"^(?:\d+(?:-?[a-z]+)?\.(?:\s|$)"                     # 1.  2-bis.  3bis.   (IT, EU)
    r"|\(\d+[a-z]?\)\s"                                   # (1)  (2a)          (DE)
    r"|[IVXLC]+\.\s*[-–—]\s?)")                           # I. —  II. –        (FR)
#: 2. a point's mark at the start of a line
POINT = re.compile(
    r"^(?:[a-z]{1,2}\)\s"                                 # a)  aa)            (IT, DE, FR)
    r"|\([a-z]{1,4}\)\s"                                  # (a)  (iv)          (EU)
    r"|\d+°\s?"                                           # 1°                 (FR)
    r"|\d+\)\s)")                                         # 1)                 (IT numero)
#: 3. a sentence ends at . ; : followed by space and a capital, a digit or a
#: quote — not after the abbreviations legal text is full of
_SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-ZÀ-ÖØ-Þ0-9«\"„(])")
_ABBREVIATIONS = re.compile(
    r"(?:\b(?:art|artt|Art|Abs|Nr|n|nn|lett|co|comma|cod|c|cfr|Buchst|Satz|S|vgl|ggf|bzw|z\.B|"
    r"al|Al|para|paras|No|no|p|pp|ch|chap|Dir|Reg|Rn|Rdnr|Ziff|lit|ss|seq|etc|ca|ex|op|cit|"
    r"L|R|D|A|LO|Bd|Hrsg|St|Cass|Cost|gg|v|vs)\.|\b[A-Z]\.)$")


class LegalChunkerError(ValueError):
    """The layer's params are not usable."""


def _tokenizer(params: dict) -> Any:
    from app.plugins.wikidata import chunker as wd     # the one loader of a pinned tokenizer
    return wd.tokenizer_for(params.get("tokenizer"))


def _count(tok: Any, text: str) -> int:
    return len(tok.encode(text, add_special_tokens=False).ids)


def _lines_split(text: str, mark: re.Pattern) -> list[str]:
    """`text` cut before every line that starts with `mark`; the first unit
    is whatever precedes the first mark (an article's heading, an intro)."""
    units: list[str] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if cur and mark.match(line.lstrip()):
            units.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        units.append("\n".join(cur))
    return [u for u in units if u.strip()]


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        if _ABBREVIATIONS.search(text[start:m.start()]):
            continue
        out.append(text[start:m.start()])
        start = m.end()
    out.append(text[start:])
    return [s for s in out if s.strip()]


def _windows(tok: Any, text: str, budget: int) -> list[str]:
    """Consecutive windows of `budget` tokens, cut at token boundaries, the
    whole text kept: the last resort for one sentence past the window."""
    enc = tok.encode(text, add_special_tokens=False)
    offsets = list(enc.offsets)
    out: list[str] = []
    i = 0
    begin = 0
    while i < len(offsets):
        j = min(i + budget, len(offsets))
        end = offsets[j - 1][1] if j < len(offsets) else len(text)
        out.append(text[begin:end])
        begin = end
        i = j
    if begin < len(text) and text[begin:].strip():
        out[-1:] = [out[-1] + text[begin:]] if out else [text[begin:]]
    return [w for w in out if w.strip()] or [text]


def _pack(tok: Any, units: Sequence[str], budget: int, sep: str) -> list[str]:
    """Consecutive units joined while they fit: no piece shorter than it
    need be, none longer than the budget (a unit past it is the caller's)."""
    pieces: list[str] = []
    buf = ""
    for u in units:
        cand = f"{buf}{sep}{u}" if buf else u
        if buf and _count(tok, cand) > budget:
            pieces.append(buf)
            buf = u
        else:
            buf = cand
    if buf:
        pieces.append(buf)
    return pieces


def split(tok: Any, text: str, budget: int, level: int = 0) -> list[str]:
    """`text` as pieces of at most `budget` tokens, cut at the highest level
    of its structure that makes them fit; joined, the pieces are the text."""
    if _count(tok, text) <= budget:
        return [text]
    if level == 0:
        units, sep = _lines_split(text, PARAGRAPH), "\n"
    elif level == 1:
        units, sep = _lines_split(text, POINT), "\n"
    elif level == 2:
        units, sep = _sentences(text), " "
    else:
        return _windows(tok, text, budget)
    if len(units) <= 1:
        return split(tok, text, budget, level + 1)
    out: list[str] = []
    for piece in _pack(tok, units, budget, sep):
        out.extend(split(tok, piece, budget, level + 1) if _count(tok, piece) > budget else [piece])
    return out


def check(params: Any) -> tuple[int, bool]:
    if not isinstance(params, dict):
        raise LegalChunkerError("params is a table")
    mt = params.get("max_tokens")
    if not isinstance(mt, int) or isinstance(mt, bool) or mt < 16:
        raise LegalChunkerError(f"max_tokens is an integer of at least 16, got {mt!r}")
    header = params.get("header", False)
    if not isinstance(header, bool):
        raise LegalChunkerError(f"header is true or false, got {header!r}")
    return mt, header


def chunk_legal_article(uid: str, native_id, sections: Sequence[tuple[str, str]],
                        version=None, params=None) -> Iterator[dict]:
    from app.chunking import _mk
    max_tokens, header = check(params or {})
    tok = _tokenizer(params or {})
    version = CHUNK_VERSION if version is None else version
    seq = 0
    for label, body in sections:
        text = (body or "").strip()
        if not text:
            continue
        head = f"{label}\n" if header and label else ""
        budget = max_tokens - (_count(tok, head) if head else 0)
        if budget < 32:
            raise LegalChunkerError(f"{uid}: the header {label!r} leaves {budget} tokens of {max_tokens}")
        for piece in split(tok, text, budget):
            yield _mk(uid, native_id, seq, label or "", head + piece, version)
            seq += 1
