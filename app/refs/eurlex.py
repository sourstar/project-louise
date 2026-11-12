"""The acts of the European Union as a REFERENCE SOURCE (06 §6.5;
legal-fr-de-eu.md §4.5): the tables a citation resolves against and the
namespace `celex` that lets an EUR-Lex record write its own ids into
`keys` (05 §5.2). Its `eli:eu:` keys are the shared `eli` owner's
(app/refs/eli.py), which routes them here by jurisdiction
(`eli_jurisdiction = "eu"`); `key_lookup` finds a row by either id, so
`louise_lookup` answers each form a record's `keys` carry.

Two tables, synced from the plugin's corpus (mounted under
`<ref_root>/eurlex`, the acquisition's layout `lang=<code>/<yyyy>/<CELEX>/`):
`act`, one row per act keyed by its CELEX, with its ELI (the register's
work URI), sector, type letters, year, natural number, date, title (in the
first language held, English first) and its force status as its notice
states it; `version`, one row per record — a version of an act in one
language — keyed by the record's uid, with its own CELEX, its act's, the
language, the start of validity, the version key and its own ELI. The link
`version.work_celex → act.celex` is what `expand` follows: a citation pins
the act and its versions as facts, never ranked (invariant 4).

The sync reads each unit's notice and the head of its Formex wrapper — the
parts the plugin's own discovery and parse read for the same fields, by
the same functions — never the text.

The resolver (`CitationResolver`) turns the forms a citation of an EU act
takes in the languages held (§4.5) into the act's CELEX and looks it up:

  * since 2015 one numbering, year first: "Regulation (EU) 2016/679",
    "Règlement (UE) 2016/679", "Verordnung (EU) 2016/679", "Regolamento
    (UE) 2016/679", "Directive (EU) 2019/790";
  * before: a regulation by number and year, "Regulation (EC) No
    1907/2006", "(EEC) No 1408/71"; a directive or decision by year and
    number, "Directive 95/46/EC", "directive 2006/123/CE", "Richtlinie
    2006/123/EG", "direttiva 2006/123/CE", "Decision 2011/833/EU";
  * the CELEX itself, "CELEX 32016R0679" or bare, a consolidation's
    (`02016R0679-20160504`) naming its act;
  * an article before the act — "Article 5 of Regulation (EU) 2016/679",
    "article 6, paragraphe 1, point f) du règlement (UE) 2016/679",
    "Artikel 6 der Verordnung (EU) 2016/679" — kept on the entity's label;
    the entity is the act, as the record is.

It runs on every chunk at indexing, so it is DIGIT-ANCHORED: a text holding
no `<digit>/<digit>` and no CELEX shape is answered by one C-level search
(`_ANCHOR`), and the patterns are tried only at the number runs, looking
back a bounded window for the type word (`_BEFORE`) — never at every
character. A pure function of the text and the tables (invariant 3).

THE STANDARD, NOT ADOPTED: EUR-Lex's own citation parser is a web service
(the "Find results by citation" search); a resolver over the network would
make indexing depend on europa.eu being up. The OJ references (`OJ L 119,
4.5.2016, p. 1`) name an issue and page, not an act, and are not resolved
here: the act they name is in the CDM as its publication, read when a
table holds it (OWED with the probe).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..plugins.base import (ArticleCitation, Entity, RefColumn, RefLink, RefLookup, RefRecordBatch, RefSchema,
                            RefSyncState, RefTable)

SOURCE_ID = "eurlex-refs"
NAMESPACES = ("celex",)
ELI_JURISDICTION = "eu"

ACT = RefTable(
    name="act",
    columns=(
        RefColumn("celex", "TEXT"),          # 32016R0679
        RefColumn("eli", "TEXT"),            # http://data.europa.eu/eli/reg/2016/679/oj
        RefColumn("sector", "TEXT"),
        RefColumn("type", "TEXT"),           # the CELEX type letters: R, L, D, …
        RefColumn("year", "INTEGER"),
        RefColumn("number", "TEXT"),         # the natural number, as the notice states it
        RefColumn("date", "TEXT"),           # the act's own date
        RefColumn("title", "TEXT"),
        RefColumn("language", "TEXT"),       # the title's language
        RefColumn("in_force", "INTEGER"),
        RefColumn("end_of_validity", "TEXT"),
    ),
    primary_key="celex",
    indexes=("eli",),
)

VERSION = RefTable(
    name="version",
    columns=(
        RefColumn("uid", "TEXT"),            # eurlex:<native id>
        RefColumn("celex", "TEXT"),          # its own: 32016R1036, 02016R1036-20180608
        RefColumn("work_celex", "TEXT"),     # its act's
        RefColumn("language", "TEXT"),
        RefColumn("valid_from", "TEXT"),
        RefColumn("expression", "TEXT"),     # ORIGINAL | CONSOLIDATED/yyyymmdd
        RefColumn("eli", "TEXT"),            # its own ELI, as the notice states it
        RefColumn("unit", "TEXT"),           # lang=<code>/<yyyy>/<CELEX>, the origin unit
    ),
    primary_key="uid",
    indexes=("celex", "eli"),
)

LINK = RefLink(name="versions", from_table="version", from_column="work_celex",
               to_table="act", to_column="celex")

_BATCH = 2_000
#: the language an act's title is taken in, when the corpus holds several
_TITLE_LANGUAGES = ("en", "fr", "de", "it")


def unit_meta(unit: Path) -> dict | None:
    """What the plugin's discovery and parse say of one unit's identity and
    validity, from its notice and its wrapper's head: None when the unit is
    not whole or not readable."""
    from ..plugins import eli as eli_mod
    from ..plugins.eurlex import formex, notice as notice_mod, source as src
    celex, lang = unit.name, unit.parent.parent.name.partition("=")[2]
    if lang not in src.LANGUAGES:
        return None
    try:
        entries = src.unit_files(unit)
        names = {e.name for e in entries}
        if src.NOTICE not in names:
            return None
        note = notice_mod.parse((unit / src.NOTICE).read_bytes())
        day = src._unit_day(unit, celex, entries)
        wrapper = next((e.path for e in entries if formex.is_wrapper(e.name)), None)
        published = None
        if wrapper is not None:
            got = src._PUB_DATE.search(Path(wrapper).read_bytes()[:src._HEAD])
            published = formex.iso(got.group(1).decode()) if got else None
    except (OSError, notice_mod.NoticeError):
        return None
    if day is None or note.celex != celex:
        return None
    expr = src.version_key(celex)
    if expr == "ORIGINAL":
        valid_from, work = src.original_start(note.entry_into_force, published), celex
    else:
        valid_from, work = formex.iso(celex[-8:]), note.base_celex or celex
    try:
        nid = eli_mod.expression_native_id(day, (celex, lang), expr)
    except ValueError:
        return None
    own = src.eli_path(note.eli)
    work_eli = src.eli_work_of(own)
    head = src._CELEX_HEAD.match(celex)
    ends = [d for d in note.end_of_validity if d != src.OPEN_END]
    return {
        "version": {"uid": f"eurlex:{nid}", "celex": celex, "work_celex": work, "language": lang,
                    "valid_from": valid_from, "expression": expr,
                    "eli": src.PROFILE.absolute("eli/" + own) if own else None,
                    "unit": src.unit_rel(lang, celex)},
        "act": None if expr != "ORIGINAL" else {
            "celex": celex, "eli": src.PROFILE.absolute("eli/" + work_eli) if work_eli else None,
            "sector": note.sector or celex[:1], "type": src.celex_type(celex),
            "year": int(head["year"]) if head else None,
            "number": note.number, "date": note.date_document, "title": note.expression_title,
            "language": lang, "in_force": None if note.in_force is None else int(note.in_force),
            "end_of_validity": ends[0] if ends else None},
    }


class EurlexRefSource:
    source_id = SOURCE_ID
    ref_version = 1
    namespaces = NAMESPACES
    eli_jurisdiction = ELI_JURISDICTION

    def key_lookup(self, canonical: str) -> tuple[str, str, str] | None:
        """`(table, column, value)` holding a canonical id the records' `keys`
        carry: `celex:<CELEX>` — an act's in `act`, a consolidation's in
        `version` — and `eli:eu:<path>` — an act's `…/oj` in `act`, a
        version's in `version` — as the absolute URI the tables store."""
        ns, _, rest = canonical.partition(":")
        ns = ns.strip().lower()
        if ns == "celex" and rest:
            from ..plugins.eurlex.source import _CONSOLIDATED
            return (VERSION.name if _CONSOLIDATED.match(rest) else ACT.name), "celex", rest
        if ns == "eli":
            from . import eli as eli_refs
            from ..plugins.eurlex.source import PROFILE
            if eli_refs.jurisdiction_of(canonical) != ELI_JURISDICTION:
                return None
            path = eli_refs.path_of(canonical).strip("/")
            table = ACT.name if path.endswith("/oj") else VERSION.name
            return table, "eli", PROFILE.absolute("eli/" + path)
        return None

    def schema(self) -> RefSchema:
        return RefSchema(source_id=self.source_id, tables=(ACT, VERSION), links=(LINK,))

    def article_citations(self, query: str, refs: RefLookup | None = None,
                          facts=()) -> list[ArticleCitation]:
        return article_citations(query, refs, facts)

    def article_of_label(self, label: str | None) -> str | None:
        return article_of_label(label)

    def sync(self, state: RefSyncState) -> Iterator[RefRecordBatch]:
        """Every unit under `<ref_root>/eurlex`: a version row each, an act
        row per original — its title in the first of `_TITLE_LANGUAGES`
        held, else the first language in name order."""
        root = Path(state.ref_root) / "eurlex"
        if not root.is_dir():
            return
        acts: dict[str, tuple[int, dict]] = {}
        versions: list[dict] = []
        for lang_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("lang=")):
            lang = lang_dir.name.partition("=")[2]
            rank = _TITLE_LANGUAGES.index(lang) if lang in _TITLE_LANGUAGES else len(_TITLE_LANGUAGES)
            for year in sorted(p for p in lang_dir.iterdir() if p.is_dir()):
                for unit in sorted(p for p in year.iterdir() if p.is_dir() and not p.name.startswith(".")):
                    meta = unit_meta(unit)
                    if meta is None:
                        continue
                    versions.append(meta["version"])
                    act = meta["act"]
                    if act is not None and (act["celex"] not in acts or rank < acts[act["celex"]][0]):
                        acts[act["celex"]] = (rank, act)
                    if len(versions) >= _BATCH:
                        yield RefRecordBatch(table=VERSION.name, rows=tuple(versions))
                        versions = []
        if versions:
            yield RefRecordBatch(table=VERSION.name, rows=tuple(versions))
        rows = [a for _, a in acts.values()]
        for i in range(0, len(rows), _BATCH):
            yield RefRecordBatch(table=ACT.name, rows=tuple(rows[i:i + _BATCH]))


# ---- the citation resolver ----------------------------------------------------------

#: the type word → the CELEX type letter, in the languages §4.5 names and
#: their neighbours (es, pt, nl); case folded
_TYPE_WORDS: tuple[tuple[str, str], ...] = (
    (r"regulation|r[èe]glement|verordnung|regolamento|reglamento|regulamento|verordening", "R"),
    (r"directive|directiva|richtlinie|direttiva|diretiva|richtlijn", "L"),
    (r"decision|d[ée]cision|beschluss|entscheidung|decisione|decisi[óo]n|decis[ãa]o|besluit", "D"),
)
_TYPE_RE = "|".join(f"(?:{p})" for p, _ in _TYPE_WORDS)
#: the organisation an act is "of": (EU), (EC), (EEC), (UE), (CE), (EG), …
_ORG = r"EU|EC|EEC|UE|CE|CEE|EG|EWG|EWR|EGKS|ECSC|CECA|Euratom|EURATOM|EAEC|CFSP|PESC|GASP|JHA|JAI"
#: the look-back from a number run: the type word, its organisation, a "No"
_BEFORE = re.compile(
    rf"(?P<type>{_TYPE_RE})\s+(?:\((?P<org>{_ORG})\)\s*)?(?P<no>(?:No|Nr|n|n°|nº|N°)\.?\s*)?$",
    re.IGNORECASE)
#: an article before the type word, within the window
_ARTICLE = re.compile(
    r"(?P<art>(?:Article|Artikel|articolo|article|art[íi]culo|artigo|Art\.)\s+\d+[a-z]?"
    r"(?:\s*\(\d+\))?(?:\s*\([a-z]\))?(?:[,\s]+[^.;:]{0,60}?)?)"
    r"\s*,?\s+(?:of|du|de\s+la|de|der|des|del|della|dello|dell'|van|do|da)(?:\s+(?:the|la))?\s*$",
    re.IGNORECASE)
_WINDOW = 120
#: the number run every non-CELEX citation holds: A/B, optionally /ORG
_NUMBERS = re.compile(rf"(?<![\d/])(?P<a>\d{{1,4}})/(?P<b>\d{{1,4}})(?:/(?P<suffix>{_ORG}))?(?![\d/])")
_CELEX = re.compile(r"(?<![0-9A-Za-z])(?P<celex>[0-9]\d{4}[A-Z]{1,2}\d{4}(?:R\(\d{2}\))?(?:-\d{8})?)"
                    r"(?![0-9A-Za-z])")
#: the fast pre-check: a digit run a citation of either kind must hold
_ANCHOR = re.compile(r"\d/\d|\d[A-Z]{1,2}\d{4}")
_WS = re.compile(r"\s+")


def _year(text: str) -> int | None:
    n = int(text)
    if len(text) == 4:
        return n if 1950 <= n <= 2100 else None
    if len(text) == 2:
        return 1900 + n if n >= 50 else 2000 + n
    return None


def citations(text: str) -> list[dict]:
    """The acts a text names: `{celex, art?}` each, in order of appearance,
    once."""
    if not text or not _ANCHOR.search(text):
        return []
    found: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for m in _CELEX.finditer(text):
        celex = m.group("celex")
        if celex not in seen:
            seen.add(celex)
            found.append((m.start(), {"celex": celex, "art": None}))
    for m in _NUMBERS.finditer(text):
        lo = max(0, m.start() - _WINDOW)
        before = text[lo:m.start()]
        b = _BEFORE.search(before)
        if b is None:
            continue
        letter = next(t for p, t in _TYPE_WORDS if re.fullmatch(p, b.group("type"), re.IGNORECASE))
        a_txt, b_txt = m.group("a"), m.group("b")
        if b.group("no"):
            number, year = a_txt, _year(b_txt)          # "(EC) No 1907/2006"
        elif m.group("suffix") or b.group("org") or letter != "R":
            year, number = _year(a_txt), b_txt          # "95/46/EC", "(EU) 2016/679"
        else:
            year, number = _year(a_txt), b_txt          # "Regulation 2016/679"
            if year is None:
                number, year = a_txt, _year(b_txt)
        if year is None:
            continue
        celex = f"3{year:04d}{letter}{int(number):04d}"
        art = _ARTICLE.search(before[:b.start()])
        label_art = _WS.sub(" ", art.group("art")).strip(" ,") if art else None
        if celex in seen:
            continue
        seen.add(celex)
        found.append((m.start(), {"celex": celex, "art": label_art}))
    return [c for _, c in sorted(found, key=lambda t: t[0])]


def _act_of(refs: RefLookup, celex: str) -> dict | None:
    """The act a CELEX names: its own row, or — a consolidation's — its act's."""
    from ..plugins.eurlex.source import _CONSOLIDATED
    try:
        if _CONSOLIDATED.match(celex):
            rows = refs.rows(VERSION.name, {"celex": celex}, limit=1)
            if not rows:
                return None
            celex = rows[0]["work_celex"]
        return refs.get(ACT.name, celex)
    except Exception:   # noqa: BLE001 — a store without the tables (never synced): no acts
        return None


class CitationResolver:
    """Citation text → the acts named (`act` rows), as entities whose id is
    `celex:<CELEX>`; `expand` follows the declared link to the versions
    (the composite in app/refs delegates it)."""

    def resolve(self, query: str, refs: RefLookup) -> list[Entity]:
        out: list[Entity] = []
        got: set[str] = set()
        for c in citations(query):
            row = _act_of(refs, c["celex"])
            if row is None or row["celex"] in got:
                continue
            got.add(row["celex"])
            label = row.get("title") or row["celex"]
            if c["art"]:
                label = f"{label} — {c['art']}"
            out.append(Entity(entity_id=f"celex:{row['celex']}", kind=ACT.name, source_id=SOURCE_ID,
                              label=label))
        return out


# ---- the articles a query cites (legal-chunking-retrieval.md R1) ---------------------------
#
# No alias table: "art. 17 GDPR" names no act this grammar can read, and none
# is invented here — an act is cited by its number or its CELEX.

#: the word for "article" in the languages the grammar reads (`_ARTICLE`)
_ART_WORD = r"(?:Article|Artikel|Articolo|Artículo|Articulo|Artigo|Art\.)"
_ART_NUM = re.compile(rf"(?P<word>{_ART_WORD})\s*(?P<num>\d+[a-z]?)", re.IGNORECASE)


def article_citations(query: str, refs: RefLookup | None = None, facts=()) -> list[ArticleCitation]:
    """Every article the query cites of an act it names by number or CELEX
    ("Article 5 of Regulation (EU) 2016/679", "art. 17 du règlement (UE)
    2016/679"): the act's `celex:<CELEX>`, a `eurlex` record's own key.
    Needs no reference data."""
    out: list[ArticleCitation] = []
    for c in citations(query):
        m = _ART_NUM.match(c["art"] or "")
        if m is None:
            continue
        out.append(ArticleCitation(keys=(f"celex:{c['celex']}",), article=m.group("num").lower(),
                                   label=f"{m.group('word')} {m.group('num')}"))
    return out


#: a section's label (formex.sections): "[partition › ]Article 5 — title"
_LABEL = re.compile(rf"^\s*{_ART_WORD}\s*(?P<num>\d+[a-z]?)\b", re.IGNORECASE)


def article_of_label(label: str | None) -> str | None:
    m = _LABEL.match(str(label or "").rsplit(" › ", 1)[-1])
    return m.group("num").lower() if m else None
