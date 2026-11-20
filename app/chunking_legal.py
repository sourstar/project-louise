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
  * `header` (a parameter, inside `space_id`; operator 2026-11-08, piece 1
    "by design"): the record's TITLE (the act) and the section's LABEL (its
    place in the hierarchy and the article), "<title> › <label>", repeated
    at the head of every piece and counted inside the window — the headings
    G-DSR's graph fuses into each article (Louis et al., EACL 2023: BSARD
    mAP 35.3 → 47.1 from the hierarchy's headings and neighbours) written
    into the text the encoder reads, the deterministic form of contextual
    chunk headers (Anthropic, "Contextual Retrieval", 2024: −35 % failures);
  * `outline` (piece 2): one more chunk per record of two or more sections —
    the title and every section's label in order, the act's own index — so
    a question about a whole law reaches the act (Lima 2024's levels;
    G-DSR's section nodes); token-bounded like any piece, its chapter
    `outline`, after the articles so their sequence numbers stay put.

VERSION 2 (2026-11-08, from the first real Normattiva records cut by v1):
  * the header is not said twice — a section whose body begins with its
    label's last segment (Normattiva's "Art. 1. — (Delega …)", the body's
    own first line) has that line dropped from the PIECE, the header
    carrying it; the record keeps it (sections are not touched);
  * the outline is a TABLE OF CONTENTS: each partition written once, the
    articles under it indented, not every label's whole path on every
    line (legge 306/2003's outline repeated "CAPO I — DISPOSIZIONI
    GENERALI …" before each of its articles).
Version 1 cuts as it always did (invariant 3): a layer pinned to it gives
the same chunks.

BOUNDED (2026-11-20, no version: the pieces are byte for byte the same):
no encode reads more than SPAN_CHARS characters — a count and a token window
read the text in spans cut at a space between two non-spaces, which the
pinned tokenizer's pre-tokenization never crosses (`spans_are_exact`). One
encode of a 54.3 M-character text held 11.18 GB (EUR-Lex5's 32018R0688 el).

VERSION 3 (2026-11-08, München's profile of normattiva-nemo's records build:
`_count` under `_pack` was 87-92 % of every pool child's time, ~4.5 h for
341,959 records): units are PACKED LINEARLY. v2 re-encoded the whole growing
piece for every unit it added — quadratic in a text's units (a 399-comma
article: 402 encodes over 7.7 M characters). v3 counts each unit ONCE, packs
on the sum of the counts and the separator's, and holds every piece to the
bound by ONE exact count of the piece itself, giving a unit back while that
count is over (the sum can differ from the joined text's count where the
tokenizer merges across a join). So no piece is ever longer than the window,
as before, and a piece may be a little shorter than v2's where the sum
over-counts. Its cut differs from v2's, hence the version (invariant 3).

The TITLE is the record's (09 §9.5), passed by every caller that cuts
(`layers.chunks_for_document(..., title=)`, guarded by
tests/test_chunking_legal.py): invariant 3's "cleaned text" is
`[title, sections]`. A layer that asks for the header or the outline and is
given no title is refused, never cut otherwise.

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

CHUNK_VERSION = 3
VERSIONS = (1, 2, 3)
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


#: Nemotron-3-Embed-1B-BF16's tokenizer.json at revision c0c9fea9 (17,077,678
#: bytes; München, 2026-11-08), the encoder of the shared legal space
NEMOTRON_TOKENIZER = "sha256:797410dfb649a5b9ba92bc4fef7dbf4022d00e73de6867c4ac199a8846439421"
#: the space's ctx 8,191 (the model's context_size less one) less "passage: "
#: and a margin of 16 for the join and the special tokens
SHARED_MAX_TOKENS = 8175
#: THE SHARED LEGAL LAYER (docs/notes/legal-chunking-retrieval.md; operator
#: 2026-11-08, pieces 1 and 2 by design): the one definition the three legal
#: plugins declare as their `default_layers` and their recipes state byte for
#: byte (tests/test_chunking_legal.py) — the chunker is inside `space_id`
SHARED_LAYER = {"rule": "default",
                "chunker": {"id": NAME, "version": CHUNK_VERSION,
                            "params": {"tokenizer": NEMOTRON_TOKENIZER, "max_tokens": SHARED_MAX_TOKENS,
                                       "header": True, "outline": True}},
                "input": {"form": "text"}}


class LegalChunkerError(ValueError):
    """The layer's params are not usable."""


def _tokenizer(params: dict) -> Any:
    from app.plugins.wikidata import chunker as wd     # the one loader of a pinned tokenizer
    return wd.tokenizer_for(params.get("tokenizer"))


# THE BOUNDED ENCODE (2026-11-20, the second OOM hold; München's
# tools/measure/parse_peak.py): EUR-Lex5's 32018R0688 el, 128 MB, 54.3 M
# characters, its `chunks` rose 11.18 GB (83x its bytes; ~206 bytes a
# character) — and 32021R2017 el 5.18 GB for 23.6 M characters (~219 a
# character): the peak follows the TEXT, because `split` counted a whole
# section in one encode and `_windows` encoded a whole over-long sentence and
# listed every offset. Now no encode reads more than SPAN_CHARS: a text is
# read in spans cut at a plain space whose neighbours are not white space,
# and the spans' counts summed, their offsets shifted — the SAME numbers, by
# construction, for a tokenizer whose pre-tokenization is local there
# (`spans_are_exact`): no normalizer, its split a regex with no lookbehind
# that no match crosses at such a space (the pinned Nemotron-3-Embed-1B
# tokenizer: every alternative but `\s+(?!\S)` stops before a space, and
# that one cannot end at a space followed by a non-space), then ByteLevel
# without its own regex, and no added token holding a space; or a plain
# whitespace split. Any other tokenizer is encoded whole, as before. No
# chunker version moves: the pieces are byte for byte the pieces
# (invariant 3; tests/test_chunking_legal_spans.py against the pinned
# tokenizer, tools/measure/legal_chunk_identity.py on the node's documents).
# Standardised alternative, not taken: the tokenizers library's own batch
# or streaming encode has no bounded-memory count; a tokenizer-agnostic
# bound would need it.
SPAN_CHARS = 200_000
_SAFE_CUT = re.compile(r"(?<=\S) (?=\S)")
#: the pre-tokenizers whose pre-tokens never cross a space between two
#: non-spaces, and so whose encoding is the spans' (`spans_are_exact`)
_EXACT_SPLIT = ("[^\\r\\n\\p{L}\\p{N}]?[\\p{Lu}\\p{Lt}\\p{Lm}\\p{Lo}\\p{M}]*[\\p{Ll}\\p{Lm}\\p{Lo}\\p{M}]+|"
                "[^\\r\\n\\p{L}\\p{N}]?[\\p{Lu}\\p{Lt}\\p{Lm}\\p{Lo}\\p{M}]+[\\p{Ll}\\p{Lm}\\p{Lo}\\p{M}]*|"
                "\\p{N}| ?[^\\s\\p{L}\\p{N}]+[\\r\\n/]*|\\s*[\\r\\n]+|\\s+(?!\\S)|\\s+")
_exact: dict[int, bool] = {}


def spans_are_exact(tok: Any) -> bool:
    """Is `tok`'s encoding of a text the concatenation of its encodings of
    the text cut at a space between two non-spaces (`_SAFE_CUT`)? Read off
    the tokenizer's own declaration (`to_str`), once per tokenizer."""
    key = id(tok)
    got = _exact.get(key)
    if got is None:
        got = _declared_exact(tok)
        _exact[key] = got
    return got


def _declared_exact(tok: Any) -> bool:
    import json
    try:
        d = json.loads(tok.to_str())
    except Exception:   # noqa: BLE001 — a tokenizer that does not say: encoded whole
        return False
    if d.get("normalizer") is not None:
        return False
    if any(" " in (a.get("content") or "") for a in d.get("added_tokens") or []):
        return False
    pre = d.get("pre_tokenizer") or {}
    if pre.get("type") == "WhitespaceSplit":
        return True
    steps = pre.get("pretokenizers") if pre.get("type") == "Sequence" else None
    if not steps or len(steps) != 2:
        return False
    split, byte = steps
    return (split.get("type") == "Split" and (split.get("pattern") or {}).get("Regex") == _EXACT_SPLIT
            and split.get("behavior") == "Isolated" and not split.get("invert")
            and byte.get("type") == "ByteLevel" and not byte.get("use_regex") and not byte.get("add_prefix_space"))


def _spans(tok: Any, text: str) -> list[tuple[int, int]]:
    """(start, end) of the spans `text` is encoded in: itself where it is
    short or the tokenizer is not span-exact; else cut at the first safe
    space at or past every SPAN_CHARS (a run with no safe space is one span)."""
    if len(text) <= SPAN_CHARS or not spans_are_exact(tok):
        return [(0, len(text))]
    out: list[tuple[int, int]] = []
    start = 0
    while len(text) - start > SPAN_CHARS:
        m = _SAFE_CUT.search(text, start + SPAN_CHARS)
        if m is None:
            break
        out.append((start, m.start()))
        start = m.start()
    out.append((start, len(text)))
    return out


def _count(tok: Any, text: str) -> int:
    return sum(len(tok.encode(text[a:b], add_special_tokens=False).ids) for a, b in _spans(tok, text))


def _token_ends(tok: Any, text: str):
    """Each token's end offset in `text`, in order, a span at a time."""
    for a, b in _spans(tok, text):
        for _s, e in tok.encode(text[a:b], add_special_tokens=False).offsets:
            yield a + e


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
    whole text kept: the last resort for one sentence past the window.
    The tokens' ends are read a span at a time (`_token_ends`): a window is
    cut after its `budget`-th token where another token follows, the last
    one runs to the text's end — as when every offset was listed first."""
    out: list[str] = []
    begin = 0
    n = 0
    pending: int | None = None          # the end of a window's last token, cut once another follows
    for end in _token_ends(tok, text):
        if pending is not None:
            out.append(text[begin:pending])
            begin = pending
            pending = None
        n += 1
        if n % budget == 0:
            pending = end
    if n:
        out.append(text[begin:])
        begin = len(text)
    if begin < len(text) and text[begin:].strip():
        out[-1:] = [out[-1] + text[begin:]] if out else [text[begin:]]
    return [w for w in out if w.strip()] or [text]


def _windows_listed(tok: Any, text: str, budget: int) -> list[str]:
    """`_windows` as it was until 2026-11-20 — every offset listed from one
    encode of the whole text; kept as the reference the bounded one is held
    to (tests/test_chunking_legal_spans.py)."""
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


def _pack_linear(tok: Any, units: Sequence[str], budget: int, sep: str) -> list[tuple[str, int]]:
    """v3: `(piece, its exact token count)`, consecutive units joined while
    the sum of their counts (and the separators') fits; each piece of several
    units then held to the bound by its own exact count, a unit given back
    while it is over. One count per unit and about one per piece: linear in
    the text. A single unit past the budget is the caller's."""
    counts = [_count(tok, u) for u in units]
    sep_n = _count(tok, sep) if sep else 0
    out: list[tuple[str, int]] = []
    i, n = 0, len(units)
    while i < n:
        j, est = i + 1, counts[i]
        while j < n and est + sep_n + counts[j] <= budget:
            est += sep_n + counts[j]
            j += 1
        exact = counts[i] if j == i + 1 else _count(tok, sep.join(units[i:j]))
        while j - i > 1 and exact > budget:
            j -= 1
            exact = counts[i] if j == i + 1 else _count(tok, sep.join(units[i:j]))
        out.append((sep.join(units[i:j]), exact))
        i = j
    return out


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


def split(tok: Any, text: str, budget: int, level: int = 0, *, version: int = CHUNK_VERSION,
          count: int | None = None) -> list[str]:
    """`text` as pieces of at most `budget` tokens, cut at the highest level
    of its structure that makes them fit; joined, the pieces are the text.
    `version` 3 packs linearly (`_pack_linear`), 1 and 2 as they always did;
    `count`: the text's token count where the caller has it already."""
    count = count if count is not None else _count(tok, text)
    if count <= budget:
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
        return split(tok, text, budget, level + 1, version=version, count=count)
    out: list[str] = []
    if int(version) >= 3:
        for piece, n in _pack_linear(tok, units, budget, sep):
            out.extend(split(tok, piece, budget, level + 1, version=version, count=n) if n > budget
                       else [piece])
        return out
    for piece in _pack(tok, units, budget, sep):
        out.extend(split(tok, piece, budget, level + 1, version=version) if _count(tok, piece) > budget
                   else [piece])
    return out


OUTLINE = "outline"


def check(params: Any) -> tuple[int, bool, bool]:
    if not isinstance(params, dict):
        raise LegalChunkerError("params is a table")
    mt = params.get("max_tokens")
    if not isinstance(mt, int) or isinstance(mt, bool) or mt < 16:
        raise LegalChunkerError(f"max_tokens is an integer of at least 16, got {mt!r}")
    flags = []
    for k in ("header", "outline"):
        v = params.get(k, False)
        if not isinstance(v, bool):
            raise LegalChunkerError(f"{k} is true or false, got {v!r}")
        flags.append(v)
    unknown = sorted(set(params) - {"tokenizer", "max_tokens", "header", "outline"})
    if unknown:
        raise LegalChunkerError(f"unknown params {unknown}: they would enter space_id and cut nothing")
    return mt, flags[0], flags[1]


def header_of(title: str, label: str) -> str:
    """"<title> › <label>", either alone where the other is empty."""
    return " › ".join(x for x in ((title or "").strip(), (label or "").strip()) if x)


def body_after_head(label: str, text: str) -> str:
    """v2: the body without a first line that only repeats the label's last
    segment, which the header already says. A body that IS that line alone
    is kept whole: a chunk is never the header with nothing under it."""
    last = (label or "").split(" › ")[-1].strip()
    first, sep, rest = text.partition("\n")
    if last and sep and first.strip() == last and rest.strip():
        return rest.strip("\n")
    return text


def toc(labels: Sequence[str]) -> str:
    """v2's outline: the labels as a table of contents — a partition
    written once where it begins, each level below it indented two spaces."""
    out: list[str] = []
    prev: list[str] = []
    for label in labels:
        path = [x.strip() for x in label.split(" › ") if x.strip()]
        same = 0
        while same < min(len(prev), len(path) - 1) and prev[same] == path[same]:
            same += 1
        for depth in range(same, len(path)):
            out.append("  " * depth + path[depth])
        prev = path
    return "\n".join(out)


def _budget(tok: Any, head: str, max_tokens: int, uid: str) -> int:
    budget = max_tokens - (_count(tok, head) if head else 0)
    if budget < 32:
        raise LegalChunkerError(f"{uid}: the header {head.strip()!r} leaves {budget} tokens of {max_tokens}")
    return budget


def chunk_legal_article(uid: str, native_id, sections: Sequence[tuple[str, str]],
                        version=None, params=None, *, title: str | None = None) -> Iterator[dict]:
    from app.chunking import _mk
    max_tokens, header, outline = check(params or {})
    if (header or outline) and title is None:
        raise LegalChunkerError(f"{uid}: this layer writes the record's title into its chunks "
                                f"(header/outline) and the caller passed none")
    tok = _tokenizer(params or {})
    version = CHUNK_VERSION if version is None else version
    if version not in VERSIONS:
        raise LegalChunkerError(f"{uid}: legal_article has versions {list(VERSIONS)}, not {version!r}")
    seq = 0
    for label, body in sections:
        text = (body or "").strip()
        if not text:
            continue
        head = header_of(title or "", label) if header else ""
        if head and int(version) >= 2:
            text = body_after_head(label, text)
        head = f"{head}\n" if head else ""
        for piece in split(tok, text, _budget(tok, head, max_tokens, uid), version=int(version)):
            yield _mk(uid, native_id, seq, label or "", head + piece, version)
            seq += 1
    labels = list(dict.fromkeys(l.strip() for l, _b in sections if (l or "").strip()))
    if outline and len(labels) >= 2:
        head = f"{title.strip()}\n" if (title or "").strip() else ""
        index = toc(labels) if int(version) >= 2 else "\n".join(labels)
        for piece in split(tok, index, _budget(tok, head, max_tokens, uid), version=int(version)):
            yield _mk(uid, native_id, seq, OUTLINE, head + piece, version)
            seq += 1
