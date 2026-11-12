"""The Italian acts as a REFERENCE SOURCE (06 §6.5; docs/notes/normattiva-multivigenza-review.md
§3.12; gazzetta-plugin.md §6): the lookup tables a citation resolves
against, and the namespaces — `urn` (URN:NIR) and `akn` — that let a
Normattiva record write its own ids into `keys` (05 §5.2: a plugin writes
only ids a reference source registers). Its `eli:it:` keys are the shared
`eli` owner's (app/refs/eli.py, since 2026-11-08), which routes them here by
jurisdiction (`eli_jurisdiction`); `key_lookup` finds a row by any of the
three ids, so `louise_lookup` answers each form a record's `keys` carry.

Two tables, synced from the acts' own metadata in the multivigente zips
(the plugin's corpus, mounted under `<ref_root>/normattiva`): `atto`, one
row per act keyed by its URN:NIR, with its ELI work URI, type, date,
number, title and codice redazionale; `versione`, one row per version
keyed by the record's uid, with the act's URN, the start of validity and
the Expression. The link `versione.urn → atto.urn` is what `expand`
follows, so a citation pins the act and its versions as facts — never
ranked (invariant 4).

The sync reads each member's HEAD only — the first 64 KB, where the FRBR
identifiers, the publication and the preface are — so 300,000 members
cost minutes, not the hours a full parse would. What the head says is
what the plugin's own parser says of the same bytes (the `urn:nir` alias,
the `eli` alias, `docType`, `docDate`, `docNumber`, `docTitle`); a member
stored as base64 is decoded first, as the plugin decodes it.

The resolver (`CitationResolver`) turns the common Italian forms of a
citation — "l. 306/2003", "legge 31 ottobre 2003, n. 306", "d.lgs. n. 82
del 2005", "legge costituzionale 26 febbraio 1948, n. 3" — into the acts
they name, by (type, number, year) against an index memoised on the
reference data's version, as the gene resolver does. It is a pure
function of the query text and the tables' content (invariant 3). An
article number in the citation is kept on the entity's label; the entity
is the act, as the record is.

A CODE CITED BY ITS ABBREVIATION — "art. 2043 c.c.", "art. 575 c.p.",
"art. 360 c.p.c." — is read from app/refs/normattiva_codes.json: SOURCE,
Normattiva open data, the pre-packaged collection "Codici"
(akn/M/codici.zip, 40 acts; the API's `collection-predefinite` answer of
2026-11-12, sha256 b8e2ad6f…, the file's `source` in full), its URNs and
titles as Normattiva lists them, with the citation forms of Italian legal
usage for each act (`code_citations`). An ANNEX code (the decree's own
articles, and for c.c. the preleggi, held BEFORE the code in one Work) pins
only the chunks of its annex (`annexes`, the labels' prefix before "-art.
N"). "Cost." is mapped to the Constitution's URN, which no pre-packaged
collection holds: it pins nothing until the Constitution is acquired.
"""
from __future__ import annotations

import base64
import binascii
import re
import threading
import zipfile
from pathlib import Path
from typing import Iterator

from ..plugins.base import (ArticleCitation, ArticleNotes, Entity, RefColumn, RefLink, RefLookup, RefRecordBatch, RefSchema,
                            RefSyncState, RefTable)

SOURCE_ID = "normattiva-refs"
#: `eli` moved to its one owner, app/refs/eli.py (legal-fr-de-eu.md §5.2,
#: 2026-11-08); this source declares the jurisdiction it holds instead
NAMESPACES = ("urn", "akn")
ELI_JURISDICTION = "it"

ATTO = RefTable(
    name="atto",
    columns=(
        RefColumn("urn", "TEXT"),           # urn:nir:stato:legge:2003-10-31;306 — the act's URN:NIR
        RefColumn("eli", "TEXT"),           # the ELI work URI, version-less
        RefColumn("akn", "TEXT"),           # akn:it/act/legge/stato/2003-10-31/306 — the Akoma Ntoso work path
        RefColumn("tipo", "TEXT"),          # the URN's type token: legge, decreto.legislativo, …
        RefColumn("data", "TEXT"),          # the act's own date
        RefColumn("anno", "INTEGER"),       # its year, for a citation that gives no day
        RefColumn("numero", "TEXT"),
        RefColumn("titolo", "TEXT"),
        RefColumn("denominazione", "TEXT"), # "LEGGE 31 ottobre 2003, n. 306"
        RefColumn("codice", "TEXT"),        # codice redazionale
        RefColumn("pubblicazione", "TEXT"), # the GU date
    ),
    primary_key="urn",
    indexes=("eli", "akn"),                # key_lookup reads both by value
)

VERSIONE = RefTable(
    name="versione",
    columns=(
        RefColumn("uid", "TEXT"),           # the record: normattiva:<expression native id>
        RefColumn("urn", "TEXT"),
        RefColumn("valid_from", "TEXT"),    # YYYY-MM-DD, the start of validity (the id's prefix)
        RefColumn("expression", "TEXT"),    # ORIGINAL | CONSOLIDATED/yyyymmdd
        RefColumn("member", "TEXT"),        # <zip>#<member>, the origin unit
    ),
    primary_key="uid",
)

LINK = RefLink(name="versioni", from_table="versione", from_column="urn",
               to_table="atto", to_column="urn")

HEAD = 64 << 10
_BATCH = 2_000

_URN = re.compile(r'<FRBRalias\s+name="urn:nir"\s+value="([^"]+)"')
_AKN = re.compile(r'<FRBRWork>\s*<FRBRthis\s+value="([^"]+)"', re.S)
_ELI = re.compile(r'<FRBRalias\s+name="eli"\s+value="([^"]+)"')
_DOC_TYPE = re.compile(r"<docType>(.*?)</docType>", re.S)
_DOC_DATE = re.compile(r'<docDate\s+date="([^"]+)"[^>]*>(.*?)</docDate>', re.S)
_DOC_NUMBER = re.compile(r"<docNumber>(.*?)</docNumber>", re.S)
_DOC_TITLE = re.compile(r"<docTitle>(.*?)</docTitle>", re.S)
_PUBLICATION = re.compile(r'<publication\s+[^>]*date="([^"]+)"')
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(fragment: str | None) -> str:
    return _WS.sub(" ", _TAGS.sub("", fragment or "")).strip()


def head_of(zf: zipfile.ZipFile, name: str, n: int = HEAD) -> bytes:
    """The member's first `n` bytes as XML: decoded where the origin stored
    the member as base64 (two of the forty codes)."""
    with zf.open(name) as f:
        data = f.read(n)
    if data.lstrip()[:1] == b"<" or data.startswith(b"\xef\xbb\xbf"):
        return data
    cut = data[: len(data) - len(data) % 4]
    try:
        return base64.b64decode(cut, validate=False)
    except (ValueError, binascii.Error):
        return data


def meta_of(head: bytes) -> dict | None:
    """What the head states about the act; None when it names no URN."""
    s = head.decode("utf-8", errors="replace")
    urn = _URN.search(s)
    if not urn:
        return None
    urn_value = urn.group(1).strip()
    eli = _ELI.search(s)
    date = _DOC_DATE.search(s)
    parts = urn_value.split(":")
    tipo = parts[3] if len(parts) > 4 else ""
    data = (date.group(1) if date else "") or (parts[4].split(";")[0] if len(parts) > 4 else "")
    numero = _text((_DOC_NUMBER.search(s) or [None, ""])[1] if _DOC_NUMBER.search(s) else "")
    if not numero and len(parts) > 4 and ";" in parts[4]:
        numero = parts[4].split(";", 1)[1]
    doc_type = _text(_DOC_TYPE.search(s).group(1)) if _DOC_TYPE.search(s) else ""
    date_text = _text(date.group(2)) if date else ""
    denominazione = " ".join(x for x in (doc_type, date_text) if x)
    if numero:
        denominazione = f"{denominazione}, n. {numero}" if denominazione else f"n. {numero}"
    pub = _PUBLICATION.search(s)
    return {
        "urn": urn_value,
        "eli": _eli_work(eli.group(1).strip()) if eli else "",
        "akn": (akn.group(1).split("/!", 1)[0].strip("/").replace("akn/", "akn:", 1)
                if (akn := _AKN.search(s)) and akn.group(1).lstrip("/").startswith("akn/") else ""),
        "tipo": tipo, "data": data,
        "anno": int(data[:4]) if data[:4].isdigit() else None,
        "numero": numero,
        "titolo": _text(_DOC_TITLE.search(s).group(1)) if _DOC_TITLE.search(s) else "",
        "denominazione": denominazione,
        "codice": _codice(eli.group(1)) if eli else "",
        "pubblicazione": pub.group(1) if pub else "",
    }


def _eli_work(alias: str) -> str:
    """`eli/id/2003/11/15/003G0333/CONSOLIDATED/20180206` → the work, versionless."""
    parts = alias.strip("/").split("/")
    for i, p in enumerate(parts):
        if p in ("ORIGINAL", "CONSOLIDATED"):
            parts = parts[:i]
            break
    return "http://www.normattiva.it/" + "/".join(parts)


def _codice(alias: str) -> str:
    parts = alias.strip("/").split("/")
    return parts[5] if len(parts) > 5 and parts[0] == "eli" and parts[1] == "id" else ""


class NormattivaRefSource:
    source_id = SOURCE_ID
    ref_version = 2            # 2: the `akn` column (2026-10-12)
    namespaces = NAMESPACES
    eli_jurisdiction = ELI_JURISDICTION
    #: a pinned article's note, in Italian (app/ask/articles.py)
    article_notes = ArticleNotes("testo vigente", "testo al {day}", "ultimo testo in vigore")

    def article_repealed(self, text: str) -> bool:
        return article_repealed(text)

    def key_lookup(self, canonical: str) -> tuple[str, str, str] | None:
        """`(table, column, value)` holding a canonical id the records'
        `keys` carry: the URN:NIR verbatim, the Akoma Ntoso work path
        verbatim (`akn:it/act/…`), the ELI by-id form (`eli:it:id/…`) as the
        absolute work URI the table stores. None for any other id."""
        ns = canonical.partition(":")[0].strip().lower()
        if ns == "urn":
            return ATTO.name, "urn", canonical
        if ns == "akn":
            return ATTO.name, "akn", canonical
        if ns == "eli":
            from . import eli as eli_refs
            from ..plugins.normattiva.source import PROFILE
            if eli_refs.jurisdiction_of(canonical) != ELI_JURISDICTION:
                return None
            return ATTO.name, "eli", PROFILE.absolute("eli/" + eli_refs.path_of(canonical))
        return None

    def schema(self) -> RefSchema:
        return RefSchema(source_id=self.source_id, tables=(ATTO, VERSIONE), links=(LINK,))

    def article_citations(self, query: str, refs: RefLookup | None = None,
                          facts=()) -> list[ArticleCitation]:
        return article_citations(query, refs, facts)

    def article_of_label(self, label: str | None) -> str | None:
        return label_token(label)

    def sync(self, state: RefSyncState) -> Iterator[RefRecordBatch]:
        """The zips under `<ref_root>/normattiva` — the plugin's corpus, as
        acquisition lays it out — read member by member, heads only."""
        from ..plugins import eli as eli_mod
        from ..plugins.normattiva.source import _MEMBER, version_key
        root = Path(state.ref_root) / "normattiva"
        if not root.is_dir():
            return
        acts: dict[str, dict] = {}
        versions: list[dict] = []
        from app.plugins import walk
        for zpath in walk.files(root, "*.zip"):        # through links (walk.py)
            try:
                zf = zipfile.ZipFile(zpath)
            except (zipfile.BadZipFile, OSError):
                continue
            with zf:
                for info in zf.infolist():
                    m = _MEMBER.match(info.filename)
                    if m is None:
                        continue
                    try:
                        meta = meta_of(head_of(zf, info.filename))
                    except (zipfile.BadZipFile, OSError):
                        continue
                    if meta is None:
                        continue
                    gu, codice, form, vig = m["gu"], m["codice"], m["form"], m["vig"]
                    valid_from = vig or gu
                    expr = version_key(form, vig)
                    nid = eli_mod.expression_native_id(valid_from, (gu, codice), expr)
                    if not meta["codice"]:
                        meta["codice"] = codice
                    if not meta["pubblicazione"]:
                        meta["pubblicazione"] = gu
                    acts.setdefault(meta["urn"], meta)
                    versions.append({"uid": f"normattiva:{nid}", "urn": meta["urn"],
                                     "valid_from": valid_from, "expression": expr,
                                     "member": f"{zpath.relative_to(root).as_posix()}#{info.filename}"})
                    if len(versions) >= _BATCH:
                        yield RefRecordBatch(table=VERSIONE.name, rows=tuple(versions))
                        versions = []
            if len(acts) >= _BATCH:
                yield RefRecordBatch(table=ATTO.name, rows=tuple(acts.values()))
                acts = {}
        if acts:
            yield RefRecordBatch(table=ATTO.name, rows=tuple(acts.values()))
        if versions:
            yield RefRecordBatch(table=VERSIONE.name, rows=tuple(versions))


# ---- the citation resolver ----------------------------------------------------------

#: how a citation names an act's type → the URN:NIR type token
_TYPES: tuple[tuple[str, str], ...] = (
    (r"legge\s+costituzionale|l\.\s*cost\.", "legge.costituzionale"),
    (r"decreto\s+legislativo|d\.\s*lgs\.?|dlgs|d\.\s*lg\.", "decreto.legislativo"),
    (r"decreto[-\s]legge|d\.\s*l\.|\bdl\b", "decreto.legge"),
    (r"decreto\s+del\s+presidente\s+della\s+repubblica|d\.\s*p\.\s*r\.|\bdpr\b",
     "decreto.del.presidente.della.repubblica"),
    (r"decreto\s+del\s+presidente\s+del\s+consiglio\s+dei\s+ministri|d\.\s*p\.\s*c\.\s*m\.|\bdpcm\b",
     "decreto.del.presidente.del.consiglio.dei.ministri"),
    (r"regio\s+decreto[-\s]legge|r\.\s*d\.\s*l\.", "regio.decreto.legge"),
    (r"regio\s+decreto|r\.\s*d\.", "regio.decreto"),
    # a consolidated text named by what it is, not by its legal form: the
    # form is the origin's to say ("testo unico … 13 novembre 1919, n. 2205"
    # is a regio decreto-legge), found by date and number (`resolve`)
    (r"testo\s+unico|t\.\s*u\.", "*"),
    (r"legge|\bl\.", "legge"),
)
_TYPE_RE = "|".join(f"(?:{p})" for p, _ in _TYPES)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
     "settembre", "ottobre", "novembre", "dicembre"))}
_MONTH_RE = "|".join(_MONTHS)
_SUFFIX = r"(?:[-\s]?(?:bis|ter|quater|quinquies))?"
_NUM = rf"(?P<num>\d+{_SUFFIX})"
#: "art. 5, comma 3, della" before an act: the article and comma kept for
#: the label, the preposition not
_ART = r"(?:(?P<art>art(?:icolo|\.)?\s*\d+(?:-\w+)?(?:\s*,?\s*comma\s*\d+)?)\s*,?\s*(?:del(?:la)?\s+)?)?"
#: what follows a dated citation's first digit, and a short one's number
_DAY_REST = rf"[°]?\s+(?P<month>{_MONTH_RE})\s+(?P<year>\d{{4}})\s*,?\s*n\.?\s*{_NUM}"
_YEAR_REST = rf"\s*(?:/|del\s+(?:\d{{1,2}}\s+(?:{_MONTH_RE})\s+)?|,\s*)(?P<year>\d{{4}})"
#: "legge 31 ottobre 2003, n. 306" — the full form; a few words of title may
#: stand between the form and the date ("legge sull'emigrazione testo unico
#: 13 novembre 1919, n. 2205", operator 2026-10-13: the citation went
#: unrecognised and the act was answered by ranking alone)
_CITE_DATED = re.compile(
    rf"{_ART}(?P<type>{_TYPE_RE})(?:\s+[^\s\d,;]+){{0,8}}?\s*,?\s+(?P<day>\d{{1,2}}){_DAY_REST}",
    re.IGNORECASE)
#: "l. 306/2003", "d.lgs. n. 82 del 2005", "d.lgs. 82 del 7 marzo 2005"
_CITE_SHORT = re.compile(rf"{_ART}(?P<type>{_TYPE_RE})\s*n?\.?\s*{_NUM}{_YEAR_REST}", re.IGNORECASE)

# ---- the scan: the patterns tried only where a citation can start --------------------
#
# (docs/notes/python-hot-spots.md B.) Both patterns open with an optional
# group and a case-insensitive alternation, so `re` has no first character
# to skip to and `finditer` tried them at EVERY character, 0.4 µs each: a
# 400 KB book cost ~140 ms, on every document of every collection, plus
# ~45 ms for a month pre-check.
#
# Every match holds a DIGIT RUN with a fixed tail: a short citation's
# number, then its year (`_YEAR_REST`); a dated one's day, then month, year
# and number (`_DAY_REST`). `_AT` finds those runs: it begins with `\d`, a
# class `re` skips to in C (2 ms a 400 KB book). What stands before the run
# is the article (`_ART`), the type and, dated, up to eight title words:
#  - no digit, so the run starts there (`(?<!\d\d)`);
#  - no `;`, and in a short citation only `[\w.,-]` and white space;
#  - at most 15 (short) or 23 (dated) runs of white space, one per `\s` on
#    the longest path: `_ART` 7, `_TYPE_RE` 6, then `\s*n?\.?\s*` 2 (short)
#    or eight title words and `\s*,?\s+` 10 (dated).
# `_*_BACK`, run back from the digits, reaches every start that allows; and
# every match begins "art" or with a type, so with one of the two-character
# openings `_START` lists. `_matches` tries the pattern there only, in
# order, resuming after each match: `finditer` minus positions where nothing
# can match — identical by construction, and tested so against the verbatim
# old scan (tests/test_citations_fast_path.py). Standard alternative:
# Google RE2, a linear-time engine; a new dependency with its own semantic
# differences, not needed: this is ~10x on a dense legal text, 45-75x on
# prose.
_DATED_AT = re.compile(rf"\d(?<!\d\d)(?=\d?{_DAY_REST})", re.IGNORECASE)
_SHORT_AT = re.compile(rf"\d(?<!\d\d)(?=\d*{_SUFFIX}{_YEAR_REST})", re.IGNORECASE)
#: a run either kind may hold, in one pass (the names dropped: a group
#: captures, it does not change what matches)
_AT = re.compile(
    r"\d(?<!\d\d)(?=" + re.sub(r"\(\?P<\w+>", "(?:", rf"\d?{_DAY_REST}|\d*{_SUFFIX}{_YEAR_REST}") + ")",
    re.IGNORECASE)
#: on the text REVERSED, from the run: what may stand before it
_DATED_BACK = re.compile(r"(?:[^\s;]*\s+){0,23}[^\s;]*")
_SHORT_BACK = re.compile(r"(?:[\w.,-]*\s+){0,15}[\w.,-]*")
#: ar, le, l., de, d., dl, dp, re, r., te, t. — the first letter a class `re`
#: can skip to, case-sensitive: a, d, l, r, t fold to their two cases alone
#: (every code point checked by the test)
_START = re.compile(r"(?-i:[aAdDlLrRtT])(?:(?<=a)(?=r)|(?<=[lrt])(?=[e.])|(?<=d)(?=[e.lp]))",
                    re.IGNORECASE)
_SCANS = ((_CITE_DATED, _DATED_AT, _DATED_BACK), (_CITE_SHORT, _SHORT_AT, _SHORT_BACK))


def _reach(back: re.Pattern, text: str, d: int) -> int:
    """Where `back` reaches, going back from `d`: run forward on the text
    before `d` reversed, a slice of it widened until the match stops inside
    it (or the slice is the whole text) — so where it stops is where it
    would stop on the whole text, without reversing all of it."""
    span = 512
    while True:
        lo = max(0, d - span)
        seg = text[lo:d][::-1]
        end = back.match(seg).end()
        if end < len(seg) or lo == 0:
            return d - end
        span *= 8


def _matches(pattern: re.Pattern, runs: list[int], back: re.Pattern, text: str) -> Iterator[re.Match]:
    """`pattern.finditer(text)`, exactly, given the digit runs every match of
    it holds (`runs`, ascending): the pattern is tried at each possible
    start before a run, never twice, never before the last match's end."""
    pos = done = 0
    for d in runs:
        if d <= pos:                # inside the last match: nothing before it to try
            done = d
            continue
        # `d + 1`: `_START` reads two characters; nothing starts at d itself
        for s in _START.finditer(text, max(_reach(back, text, d), pos, done), d + 1):
            q = s.start()
            if q >= d:
                break
            if q >= pos and (m := pattern.match(text, q)) is not None:
                yield m
                pos = m.end()
        done = d


def _month(name: str) -> int:
    """The month `_MONTH_RE` matched, by the pattern's own case rule. `re.I`
    folds ſ to s and ı, İ to i; `str.lower` leaves ſ and ı, and makes İ two
    characters, so "3 ſettembre 1765, n. 5" after an act's type was a
    KeyError: the whole document an fts error, the question a failed search
    (2026-11-02)."""
    got = _MONTHS.get(name.lower())
    if got is None:
        got = next(i for m, i in _MONTHS.items() if re.fullmatch(m, name, re.IGNORECASE))
    return got


def _type_token(text: str) -> str | None:
    for pattern, token in _TYPES:
        if re.fullmatch(pattern, text.strip(), re.I):
            return token
    return None


def citations(query: str) -> list[dict]:
    """The acts a text names: `{tipo, numero, anno, data?, art?}` each, in
    order of appearance, once — the dated form first where both name the
    same act, since it carries the date."""
    return [c for _, c in _found(query)]


def _found(query: str) -> list[tuple[int, dict]]:
    """`citations`, each with where its match starts."""
    found: list[tuple[int, dict]] = []
    seen: set[tuple] = set()
    anchors = [a.start() for a in _AT.finditer(query)]
    for pattern, at, back in _SCANS if anchors else ():
        runs = [d for d in anchors if at.match(query, d)]
        for m in _matches(pattern, runs, back, query):
            tipo = _type_token(m.group("type"))
            if tipo is None:
                continue
            data = None
            if pattern is _CITE_DATED:
                month = _month(m.group("month"))
                data = f"{int(m.group('year')):04d}-{month:02d}-{int(m.group('day')):02d}"
            num = _WS.sub("", m.group("num").replace(" ", "")).lower()
            key = (tipo, num, int(m.group("year")))
            if key in seen:
                continue
            seen.add(key)
            found.append((m.start(), {
                "tipo": tipo, "numero": num, "anno": int(m.group("year")), "data": data,
                "art": _text(m.group("art")).rstrip(" ,") if m.group("art") else None}))
    return sorted(found, key=lambda t: t[0])


_index_lock = threading.Lock()
_index_cache: tuple | None = None       # (stamp, {(tipo, numero, anno): [rows…]})


def _stamp(refs: RefLookup):
    return tuple(sorted((r["source_id"], r["ref_version"], r["synced_at"]) for r in refs.rows("ref_meta")))


def _acts_index(refs: RefLookup) -> dict:
    """`atto` keyed by (tipo, numero, anno), built once per reference-data
    version — the gene resolver's reasoning (`_symbol_index`)."""
    global _index_cache
    try:
        stamp = _stamp(refs)
    except Exception:   # noqa: BLE001 — no ref_meta: correctness over speed
        stamp = None
    with _index_lock:
        cached = _index_cache
    if stamp is not None and cached is not None and cached[0] == stamp:
        return cached[1]
    index: dict = {}
    try:
        rows = refs.rows("atto")
    except Exception:   # noqa: BLE001 — a store without the table (never synced): no acts
        rows = []
    for r in rows:
        key = (r["tipo"], str(r["numero"] or "").lower(), r["anno"])
        index.setdefault(key, []).append(r)
        # every act also by number and year alone, whatever its form: a dated
        # citation whose form is not the origin's finds it by its date
        index.setdefault(("*",) + key[1:], []).append(r)
    if stamp is not None:
        with _index_lock:
            _index_cache = (stamp, index)
    return index


def invalidate_cache() -> None:
    global _index_cache
    with _index_lock:
        _index_cache = None


class CitationResolver:
    """Citation text → the acts named (`atto` rows), as entities whose id
    is the act's URN:NIR (`urn:nir:stato:legge:2003-10-31;306` — namespace
    `urn`); `expand` follows the declared links like any resolver (the
    composite in app/refs delegates it)."""

    def resolve(self, query: str, refs: RefLookup) -> list[Entity]:
        found = citations(query)
        if not found:
            return []
        index = _acts_index(refs)
        out: list[Entity] = []
        seen: set[str] = set()
        for c in found:
            rows = index.get((c["tipo"], c["numero"], c["anno"]), []) if c["tipo"] != "*" else []
            if c["data"]:
                dated = [r for r in rows if r.get("data") == c["data"]]
                rows = dated or rows
                if not rows:
                    # the form the citation gives is not the origin's (a
                    # "legge" that is a regio decreto-legge, a "testo unico"):
                    # date and number name one act whatever its form
                    rows = [r for r in index.get(("*", c["numero"], c["anno"]), [])
                            if r.get("data") == c["data"]]
            for r in rows:
                # the URN verbatim IS the canonical id (05 §5.2): namespace
                # `urn`, the origin's identifier after it; the table keys on
                # the whole of it (resolver.row_of)
                eid = r["urn"]
                if eid in seen:
                    continue
                seen.add(eid)
                label = r.get("denominazione") or r["urn"]
                if c["art"]:
                    label = f"{label} — {c['art']}"
                out.append(Entity(entity_id=eid, kind=ATTO.name, source_id=SOURCE_ID, label=label))
        return out


# ---- the articles a query cites (legal-chunking-retrieval.md R1) ---------------------------
#
# Moved here unchanged from app/ask/articles.py, where it was the only
# grammar: the article pin is now one engine asking every legal reference
# source (`article_citations`), and this is Normattiva's.

#: AN ARTICLE THAT IS A WORD (2026-11-12, München's acceptance: 433
#: amendments into annexes whose articles are not numbers): a Roman numeral,
#: upper case as Normattiva writes it ("Allegato B-art. XI"), or one of the
#: names an article carries ("Articolo unico", "articolo transitorio",
#: "art. finale", "art. addizionale"). Taken only after "art.", "articolo"
#: as a word, or "art" and a space — never glued to a word ("arti").
_WORD_NAMES = r"(?-i:[IVXLC]+)|unico|transitorio|finale|addizionale"
_WORD_ART = r"(?:art\.|articolo\b|art(?=\s))\s*"
_WORD = rf"{_WORD_ART}({_WORD_NAMES})\b"
_NUMBER = (r"art(?:icolo|\.)?\s*(\d+)\s*[-_ ]?\s*"
           r"(bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)?\b")
#: "art. 12", "articolo 5-bis", "art.2 comma 3", "art. XI" → `12`, `5-bis`, `2`, `xi`
_TOKEN = re.compile(rf"{_NUMBER}|{_WORD}", re.I)
#: a label's article: "Legge sull'emigrazione-art. 12", "Art. 12.", "Articolo
#: 12", "Allegato B-art. XI", "Articolo unico" — the FIRST in the label
_LABEL = re.compile(rf"(?:^|[\s\-–])(?:{_NUMBER}|{_WORD})", re.I)
#: what stands before an act the scan found WITHOUT its article (`_ART` takes
#: only "art. N" right before the act's form): "art. 3 dell'allegato B del",
#: "art. XI dell'allegato B al", "articolo unico della", "art. 5, comma 2,
#: allegato 1, del" — read back from the act, never in the scan itself (the
#: text index's hot path, docs/notes/python-hot-spots.md B, is unchanged)
_ART_BEFORE = re.compile(
    rf"(?P<art>(?:art(?:icolo|\.)?\s*\d+(?:-\w+)?|{_WORD_ART}(?:{_WORD_NAMES})\b)"
    r"(?:\s*,?\s*comma\s*\d+)?)"
    r"(?:\s*,?\s*(?:dell['’]\s*|dell\s+|l['’]\s*)?allegato\s+(?P<annex>\d+|(?-i:[IVXLC]{2,})\b|[a-z]\b))?"
    r"\s*,?\s*(?:(?:del(?:la|lo)?|al(?:la|lo)?)\s+)?$", re.I)
#: how far back from an act `_ART_BEFORE` reads
_BEFORE = 120


def _num(digits: str, suffix: str | None) -> str:
    """"12" → `12`, "012" → `12`; but "01" stays `01`: c.p.p. holds an "Art.
    01." distinct from its "Art. 1." (the real member layout, 2026-11-12),
    so a lone leading zero is part of the article's name."""
    n = digits if (len(digits) == 2 and digits[0] == "0") else str(int(digits))
    return f"{n}-{suffix.lower()}" if suffix else n


def _tok(m: re.Match | None) -> str | None:
    if m is None:
        return None
    return _num(m.group(1), m.group(2)) if m.group(1) else m.group(3).lower()


def token(text: str | None) -> str | None:
    return _tok(_TOKEN.search(text or ""))


def label_token(label: str | None) -> str | None:
    return _tok(_LABEL.search(label or ""))


def article_before(query: str, pos: int) -> tuple[str, str] | None:
    """`(article as written, annex as cited)` standing right before the act
    found at `pos` ("art. XI dell'allegato B del …" → ("art. XI", "allegato
    B")); the annex "" where none is named; None where no article stands
    there."""
    m = _ART_BEFORE.search(query[max(0, pos - _BEFORE):pos])
    if m is None:
        return None
    return _text(m["art"]).rstrip(" ,"), (f"allegato {m['annex']}" if m["annex"] else "")


#: the URN:NIR forms a state act is filed under (the citation forms above,
#: and those a citation names only by title): a citation whose form is not
#: the origin's — "legge" for a regio decreto-legge, a "testo unico" — is
#: found by its date and number under each of them, one primary-key lookup
#: each, with no reference data needed (2026-10-14: the node had never
#: synced normattiva-refs, and the operator's query resolved to nothing)
FORMS = ("legge", "legge.costituzionale", "decreto.legislativo", "decreto.legge",
         "decreto.del.presidente.della.repubblica",
         "decreto.del.presidente.del.consiglio.dei.ministri", "regio.decreto.legge",
         "regio.decreto", "regio.decreto.legislativo", "decreto.legislativo.luogotenenziale",
         "decreto.luogotenenziale", "decreto", "decreto.ministeriale")


def _get(f, name: str):
    return getattr(f, name, None) if not isinstance(f, dict) else f.get(name)


def article_citations(query: str, refs: RefLookup | None = None, facts=()) -> list[ArticleCitation]:
    """Every article the query cites with its act: the act's URN under the
    citation's own form first, then every other form (`keys`, the first a
    collection holds); else the acts the query's pinned `atto` facts name
    with that article (`also`) — a citation with no date, or in a form
    filed under none of FORMS. Needs no reference data (`refs` unused)."""
    facts = list(facts)
    out: list[ArticleCitation] = []
    for pos, c in _found(query):
        art, annex = c.get("art"), ""
        if not art:
            before = article_before(query, pos)
            if before is None:
                continue
            art, annex = before
        tok = token(art)
        if not tok:
            continue
        keys: tuple[str, ...] = ()
        if c.get("data"):
            forms = [c["tipo"]] if c.get("tipo") not in (None, "*") else []
            forms += [f for f in FORMS if f not in forms]
            keys = tuple(f"urn:nir:stato:{form}:{c['data']};{c['numero']}" for form in forms)
        suffix = f"— {c['art']}"
        also: list[str] = []
        for f in facts:
            eid = _get(f, "entity_id")
            if _get(f, "kind") != "atto" or not eid or eid in also:
                continue
            # the resolver labels an act cited with its article "<title> — art. N";
            # one whose article stands before it (`article_before`) the scan did
            # not see, so its fact is the act's by year and number in its URN
            if (str(_get(f, "label") or "").endswith(suffix) if c.get("art")
                    else (f":{c['anno']}-" in eid and eid.endswith(f";{c['numero']}"))):
                also.append(eid)
        shown = tok if tok[:1].isdigit() else _TOKEN.search(art).group(3)
        out.append(ArticleCitation(keys=keys, article=tok,
                                   label=f"art. {shown}" + (f" dell'{annex}" if annex else ""),
                                   also=tuple(also), annex=annex))
    for cited in code_citations(query):
        if cited not in out:
            out.append(cited)
    return out


# ---- a code by its abbreviation: "art. 2043 c.c." (R1 follow-up c) -------------------------
#
# FROM DATA WITH A NAMED SOURCE: app/refs/normattiva_codes.json — the acts of
# Normattiva's pre-packaged open-data collection "Codici" (akn/M/codici.zip),
# their URNs and titles as the Normattiva open-data API lists them (the
# file's `source` names the call, its time and the answer's sha256), with
# the citation forms of Italian legal usage for each. Nothing else is read as
# a code. ANNEX codes (c.c., c.p., c.p.c., cod. nav., c.p.a., the three
# disp. att.) hold the approving decree's own articles — and, for c.c., the
# 31 preleggi — BEFORE the code, in the same Work, each labelled "Art. N."
# or "<annex>-art. N"; the code's articles are "<its annex>-art. N" (the
# real member layout, measured on the node 2026-11-12, codes-layout.json
# sha256 4c59a108…). So each code carries `annexes`: [] for a STRUCTURED
# code (the act is the code; a number match is unique), else the annex
# names its articles are labelled with, and a citation pins only those.
# The article's own URN (`…;262:2~art2043`) would be the standard key, but
# a chunk carries only its label: an attachment's FRBRthis is kept in the
# record's `attachments` (app/plugins/normattiva/source.py), not on the
# chunk the reader returns, and the decree's own articles have none, and
# whether every real attachment's FRBRthis names its article is unmeasured.
# Adopting it would take the FRBRthis per section in the chunk and a
# measurement on the 13 codes; until then the label prefix decides.
# "Cost." maps to the Constitution's URN; the Constitution is in none of
# Normattiva's pre-packaged collections (an acquisition gap,
# docs/notes/legal-chunking-retrieval.md §8), so "art. 3 Cost." pins
# nothing while it is not held.

CODES_FILE = Path(__file__).with_name("normattiva_codes.json")


def _abbrev_re(abbrev: str) -> str:
    """"c.p.c." → `c\\.\\s*p\\.\\s*c\\.`: a space after an inner dot is
    optional, a space in the form is any run of spaces; case folded."""
    parts = [re.escape(w) for w in abbrev.split()]
    return r"\s+".join(re.sub(r"\\\.(?=.)", r"\\.\\s*", p) for p in parts)


def _load_codes() -> tuple[dict[str, tuple[str, tuple[str, ...]]], re.Pattern]:
    import json
    data = json.loads(CODES_FILE.read_text(encoding="utf-8"))
    acts = data["codes"] + data["not_in_codici"]
    codes: dict[str, tuple[str, tuple[str, ...]]] = {}
    for code in acts:
        for a in code["abbrev"]:
            codes[re.sub(r"\s+", "", a.lower())] = (code["urn"], tuple(code.get("annexes") or ()))
    # longest first: "disp. att. c.p.c." before "c.p.c." before "c.p."
    forms = sorted({a for code in acts for a in code["abbrev"]}, key=len, reverse=True)
    alts = "|".join(_abbrev_re(a) for a in forms)
    pattern = re.compile(
        r"\bart(?:icolo|\.)?\s*(?P<num>\d+)\s*[-_ ]?\s*"
        r"(?P<suf>bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)?\s*,?\s*"
        rf"(?P<code>{alts})(?![\w.])", re.I)
    return codes, pattern


#: abbreviation (spaces removed, lower case) → (the code's URN, its annexes)
_CODES, _CODE_CITE = _load_codes()
#: abbreviation → the code's URN
CODE_URNS = {a: urn for a, (urn, _) in _CODES.items()}


def code_citations(query: str) -> list[ArticleCitation]:
    """"art. 2043 c.c.", "art. 360 c.p.c.", "art. 5 disp. att. c.c.": the
    article of the code the abbreviation names, keyed on the code's URN
    from the "Codici" table — the Work the collection holds under it — and,
    for an annex code, the annexes its articles are labelled with."""
    out: list[ArticleCitation] = []
    for m in _CODE_CITE.finditer(query or ""):
        got = _CODES.get(re.sub(r"\s+", "", m["code"].lower()))
        if got is None:
            continue
        urn, annexes = got
        tok = _num(m["num"], m["suf"])
        cited = ArticleCitation(keys=(urn,), article=tok, label=f"art. {tok}", annexes=annexes)
        if cited not in out:
            out.append(cited)
    return out


#: an article repealed in place: its chunk says so in its first lines
_REPEALED = re.compile(r"\bARTICOLO\s+ABROGATO\b|^\s*\(?\s*abrogato\s*\)?\s*\.?\s*$", re.I | re.M)


def article_repealed(text: str) -> bool:
    """Normattiva's "((ARTICOLO ABROGATO DAL …))" or a bare "(abrogato)"
    after the article's heading line (moved from app/ask/articles.py)."""
    body = text.split("\n", 1)[1] if "\n" in text else text
    return bool(_REPEALED.search(body[:300]))
