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
takes in the 24 languages EUR-Lex publishes in (the grammar below, `_FORMS`,
each language's forms read in its own Official Journal and said where) into
the act's CELEX and looks it up:

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
    "Artikel 6 der Verordnung (EU) 2016/679", "článek 5 nařízení (EU)
    2016/679", "άρθρο 5 του κανονισμού (ΕΕ) 2016/679" — or after it, as
    Finnish, Estonian, Latvian, Lithuanian and Hungarian write it:
    "asetuksen (EU) 2016/679 5 artikla", "az (EU) 2016/679 rendelet 5.
    cikke" — kept on the entity's label; the entity is the act, as the
    record is.

It runs on every chunk at indexing, so it is DIGIT-ANCHORED: a text holding
no `<digit>/<digit>` and no CELEX shape is answered by one C-level search
(`_ANCHOR`), and the patterns are tried only at the number runs, looking
back a bounded window for the type word (`_BEFORE`, run as one anchored
match on the reversed window, `_BEFORE_REV`) — never at every character. A
pure function of the text and the tables (invariant 3).

THE STANDARD, NOT ADOPTED: EUR-Lex's own citation parser is a web service
(the "Find results by citation" search); a resolver over the network would
make indexing depend on europa.eu being up. The OJ references (`OJ L 119,
4.5.2016, p. 1`) name an issue and page, not an act, and are not resolved
here: the act they name is in the CDM as its publication, read when a
table holds it (OWED with the probe).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..plugins.base import (ArticleCitation, ArticleNotes, Entity, RefColumn, RefLink, RefLookup, RefRecordBatch, RefSchema,
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
    validity, from its notice and its wrapper's head — or, a parser-4 unit
    holding no notice (the Data Dump's, 2026-11-15), from its tree
    (app/plugins/eurlex/from_tree.py: the same fields): None when the unit is
    not whole or not readable."""
    from ..plugins import eli as eli_mod
    from ..plugins.eurlex import formex, from_tree, notice as notice_mod, rdf, source as src
    celex, lang = unit.name, unit.parent.parent.name.partition("=")[2]
    if lang not in src.LANGUAGES:
        return None
    history = unit.parent.parent.parent.name == src.HISTORY
    try:
        entries = src.unit_files(unit)
        names = {e.name for e in entries}
        if src.NOTICE in names:
            note = notice_mod.parse((unit / src.NOTICE).read_bytes())
            day = src._unit_day(unit, celex, entries)
        elif rdf.TREE_FILE in names:
            note = from_tree.notice(rdf.loads((unit / rdf.TREE_FILE).read_bytes()), src.lang3(lang), celex)
            day = src._unit_day(unit, celex, entries, True)
        else:
            return None
        try:
            head = src.wrapper_head(celex, entries)     # a package read through
        except formex.FormexError:
            head = None
        published = None
        if head is not None:
            got = src._PUB_DATE.search(head)
            published = formex.iso(got.group(1).decode()) if got else None
    except (OSError, ValueError, notice_mod.NoticeError, from_tree.TreeError):
        return None
    if day is None or note.celex != celex:
        return None
    expr = src.version_key(celex)
    if expr == "ORIGINAL":
        valid_from, work = src.original_start(note.entry_into_force, published, note.date_document), celex
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
                    "unit": (f"{src.HISTORY}/" if history else "") + src.unit_rel(lang, celex)},
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
    #: a pinned article's note: the English default (app/ask/articles.py)
    article_notes = ArticleNotes()
    #: an act held in several languages is pinned ONCE (app/ask/articles.py):
    #: in the query's language, else the first of these held, else the first
    #: in name order — the order the act's title is taken in (`sync`)
    language_preference = _TITLE_LANGUAGES

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
        roots = state.corpus_roots("eurlex")          # by the collections held (base.RefSyncState)
        if not roots:
            return
        acts: dict[str, tuple[int, dict]] = {}
        versions: list[dict] = []
        from ..plugins.eurlex.source import HISTORY
        # parser 4's history slice too (`history/lang=<l>/…`)
        bases = [b for root in roots for b in (root, root / HISTORY) if b.is_dir()]
        for lang_dir in (p for base in bases
                         for p in sorted(base.iterdir()) if p.is_dir() and p.name.startswith("lang=")):
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


# ---- the citation resolver, in the 24 languages ---------------------------------------
#
# EVIDENCE, NOT MEMORY (2026-11-21). Every form below was READ in the Official
# Journal's own text, in each of the 24 languages, of acts that cite other
# acts: 32016R0679 (Directive 95/46/EC, Regulation (EC) No 45/2001, Articles 5
# and 8 of Regulation (EU) No 182/2011), 32018R0825 (amends Regulation (EU)
# 2016/1036 and cites its articles), 32022R2065, 32023R1773 (implementing and
# delegated acts, articles of Regulation (EU) 2023/956), 32014D0512 (the CFSP
# marker, 23 languages) and 32023D1532 (the Irish one) — each fetched from
# Cellar (`publications.europa.eu/resource/celex/<CELEX>`, Accept
# `application/xhtml+xml`, Accept-Language the 3-letter code; the EUR-Lex front
# end answered a WAF challenge). Each language's row says where its forms are;
# tests/ask/test_article_citations_24.py holds five real citations a language,
# each with its source. The shapes the 24 languages give an article of an act:
#
#   * the article first, a connector, the act: "Article 5 of Regulation …"
#     (en fr de it es pt nl da sv bg el ro mt ga);
#   * the article first, the act in the genitive, no connector: "článek 5
#     nařízení (EU) č. 182/2011" (cs sk pl sl hr);
#   * the act first, the article after it: "asetuksen (EU) N:o 182/2011 5
#     artiklaa" (fi), "määruse (EL) nr 182/2011 artiklit 5" (et), "Regulas (ES)
#     Nr. 182/2011 5. pantu" (lv), "Reglamento (ES) Nr. 182/2011 5 straipsnis"
#     (lt), and in Hungarian the number BEFORE the type word: "a 182/2011/EU
#     rendelet 5. cikkét", "az (EU) 2016/1036 rendelet 5. cikkének (9)
#     bekezdése".
#
# A type word is read with its case endings and, where a language writes it
# there, what stands between it and the number: the institution ("nařízení
# Evropského parlamentu a Rady (EU) č.", "Direttiva tal-Kunsill 93/13/KEE",
# "Uredba Komisije (EU)") or the act's kind ("règlement d'exécution (UE)",
# "Regulamentul de punere în aplicare (UE)", "Регламент за изпълнение (ЕС)",
# "rozporządzenie wykonawcze Komisji (UE)"); a compound ("Durchführungs-
# verordnung", "täytäntöönpanoasetuksen", "gennemførelsesforordning") is read
# by its last part. What stands between the article and the type word: the
# paragraph and the point ("čl. 6 odst. 1 písm. f)", "člankom 28. stavkom 2.",
# "artigo 170.o, n.o 1,", "artikel 25.6"), the institution or the kind before
# the type word ("i Europa-Parlamentets og Rådets direktiv", "of Commission
# Implementing Regulation", "του εκτελεστικού κανονισμού").


@dataclass(frozen=True)
class _Forms:
    """One language's citation forms, each a regex read case folded."""
    lang: str
    R: str                      # the type words, inflected: regulation …
    L: str                      # directive …
    D: str                      # decision …
    gap: str = ""               # between the type word and the number (hu: the number and the type word)
    pre: str = ""               # between the article and the type word, after the connector
    art: str = ""               # the article's word, the article before the act
    conn: str = ""              # article → act, a word ("of"); none: the genitive
    glued: str = ""             # article → act, glued to the type word (mt "tar-Regolament")
    sub: str = ""               # a paragraph's, a point's word between the article and the act
    after: str = ""             # the article AFTER the act: its whole form
    type_after: bool = False    # the type word after the number (hu)
    source: str = ""            # where the forms were read


#: a Greek vowel with and without its tonos: a title is in capitals (no tonos),
#: a question may be typed without one
_TONOS = {c: g for g in ("αά", "εέ", "ηή", "ιίϊΐ", "οό", "υύϋΰ", "ωώ") for c in g}


def _tonos(pattern: str) -> str:
    out, depth = [], 0
    for ch in pattern:
        depth += (ch == "[") - (ch == "]")
        out.append(f"[{_TONOS[ch]}]" if depth == 0 and ch in _TONOS else ch)
    return "".join(out)


_FORMS: tuple[_Forms, ...] = (
    _Forms("en", R=r"regulations?", L=r"directives?", D=r"decisions?",
           pre=r"council|commission|implementing|delegated|framework",
           art=r"article|art\.", conn=r"of",
           sub=r"paragraphs?|subparagraphs?|points?|indents?|first|second|third|fourth|sentence",
           source="32016R0679 Art. 93(2) 'Article 5 of Regulation (EU) No 182/2011'; 32023R1773 rec. 18 "
                  "'Article 16 of Commission Implementing Regulation (EU) 2023/1070', Art. 5 'Article 170(1) of "
                  "Delegated Regulation (EU) 2015/2446'"),
    _Forms("fr", R=r"r[èe]glements?", L=r"directives?", D=r"d[ée]cisions?",
           gap=r"d['’]\s?ex[ée]cution|d[ée]l[ée]gu[ée]s?|cadre|du\s+conseil|de\s+la\s+commission"
               r"|du\s+parlement\s+européen\s+et\s+du\s+conseil",
           art=r"article|art\.", conn=r"du|de\s+la|des|de",
           sub=r"paragraphes?|alin[ée]as?|points?|premier|première|deuxième|troisième|tirets?|phrase",
           source="32016R0679 Art. 93(2) 'l'article 5 du règlement (UE) no 182/2011', rec. 19 'décision-cadre "
                  "2008/977/JAI'; 32023R1773 Art. 5 'l’article 170, paragraphe 1, du règlement délégué (UE) "
                  "2015/2446', title 'RÈGLEMENT D’EXÉCUTION (UE) 2023/1773'"),
    _Forms("de", R=r"verordnung(?:en)?", L=r"richtlinien?", D=r"beschl(?:uss(?:es)?|üsse)|entscheidung(?:en)?",
           pre=r"delegierten?",
           art=r"artikels?|art\.", conn=r"der|des",
           sub=r"absatz|abs\.|unterabsatz|uabs\.|buchstaben?|buchst\.|satz|nummer|nr\.|ziffer",
           source="32016R0679 Art. 93(2) 'Artikel 5 der Verordnung (EU) Nr. 182/2011', rec. 19 'des "
                  "Rahmenbeschlusses 2008/977/JI'; 32023R1773 Art. 5 'Artikel 170 Absatz 1 der delegierten "
                  "Verordnung (EU) 2015/2446', rec. 18 'Artikel 16 der Durchführungsverordnung (EU) 2023/1070'"),
    _Forms("it", R=r"regolament[oi]", L=r"direttiv[ae]", D=r"decision[ei]",
           gap=r"di\s+esecuzione|delegat[oi]|quadro|del\s+consiglio|della\s+commissione",
           art=r"articol[oi]|art\.", conn=r"del|della|dello|dei|delle|degli", glued=r"dell['’]",
           sub=r"paragraf[oi]|comm[ai]|letter[ae]|punt[oi]|primo|secondo|terzo",
           source="32016R0679 Art. 93(2) 'l'articolo 5 del regolamento (UE) n. 182/2011'; 32023R1773 rec. 18 "
                  "'all’articolo 16 del regolamento di esecuzione (UE) 2023/1070', Art. 5 'dell’articolo 170, "
                  "paragrafo 1, del regolamento delegato (UE) 2015/2446'"),
    _Forms("es", R=r"reglamentos?", L=r"directivas?", D=r"decisi[óo]n(?:es)?",
           gap=r"de\s+ejecuci[óo]n|delegados?|marco",
           art=r"art[íi]culos?|art\.", conn=r"del|de\s+la|de\s+los|de\s+las|de",
           sub=r"apartados?|letras?|p[áa]rrafos?|puntos?|guiones?",
           source="32016R0679 Art. 93(2) 'el artículo 5 del Reglamento (UE) n.o 182/2011'; 32023R1773 rec. 18 "
                  "'el artículo 16 del Reglamento de Ejecución (UE) 2023/1070', Art. 5 'el artículo 170, "
                  "apartado 1, del Reglamento Delegado (UE) 2015/2446'"),
    _Forms("pt", R=r"regulamentos?", L=r"dir(?:e|ec)tivas?", D=r"decis(?:ão|ao|ões|oes)",
           gap=r"de\s+execu[çc][ãa]o|delegados?|quadro",
           art=r"artigos?|art\.", conn=r"do|da|dos|das",
           sub=r"n\.?\s?[ºo°]|al[íi]neas?|par[áa]grafos?|pontos?|subpar[áa]grafos?",
           source="32016R0679 Art. 93(2) 'o artigo 5.o do Regulamento (UE) n.o 182/2011', rec. 19 "
                  "'Decisão-Quadro 2008/977/JAI'; 32023R1773 Art. 5 'o artigo 170.o, n.o 1, do Regulamento "
                  "Delegado (UE) 2015/2446', rec. 18 'o artigo 16.o do Regulamento de Execução (UE) 2023/1070'"),
    _Forms("nl", R=r"verordening(?:en)?", L=r"richtl(?:ij|ĳ)n(?:en)?", D=r"besluit(?:en)?",
           pre=r"gedelegeerde",
           art=r"artikel(?:en)?|art\.", conn=r"van(?:\s+de|\s+het)?",
           sub=r"lid|leden|punt(?:en)?|onder|alinea|eerste|tweede|derde|volzin",
           source="32016R0679 Art. 93(2) 'artikel 5 van Verordening (EU) nr. 182/2011', rec. 42 'Richtlĳn "
                  "93/13/EEG' (the OJ's ĳ ligature); 32023R1773 Art. 5 'artikel 170, lid 1, van Gedelegeerde "
                  "Verordening (EU) 2015/2446', rec. 18 'artikel 16 van Uitvoeringsverordening (EU) 2023/1070'"),
    _Forms("da", R=r"forordning(?:en|er|erne)?", L=r"direktiv(?:et|er|erne)?",
           D=r"afgørelse(?:n|r|rne)?|beslutning(?:en|er)?",
           pre=r"europa-parlamentets\s+og\s+rådets|rådets|kommissionens|delegerede?|delegeret",
           art=r"artikel|art\.", conn=r"i|af",
           sub=r"stk\.|litra|nr\.|afsnit|punktum",
           source="32016R0679 Art. 93(2) 'artikel 5 i forordning (EU) nr. 182/2011', rec. 103 'artikel 25, "
                  "stk. 6, eller artikel 26, stk. 4, i direktiv 95/46/EF', Art. 4(25) 'artikel 1, stk. 1, litra b), "
                  "i Europa-Parlamentets og Rådets direktiv (EU) 2015/1535'; 32023R1773 'gennemførelsesforordning'"),
    _Forms("sv", R=r"förordning(?:en|ar|arna)?", L=r"direktiv(?:et|en)?", D=r"beslut(?:et)?",
           pre=r"europaparlamentets\s+och\s+rådets|rådets|kommissionens|delegerade?",
           art=r"artikel|art\.", conn=r"i",
           sub=r"punkt(?:en)?|led(?:en)?|stycket|första|andra|tredje",
           source="32016R0679 Art. 93(2) 'artikel 5 i förordning (EU) nr 182/2011', rec. 103 'artikel 25.6 eller "
                  "26.4 i direktiv 95/46/EG', Art. 4(25) 'artikel 1.1 b i Europaparlamentets och rådets direktiv "
                  "(EU) 2015/1535'; 32023R1773 'genomförandeförordning'"),
    _Forms("cs", R=r"nařízení(?:m|ch)?", L=r"směrnic(?:e|i|í)?", D=r"rozhodnutí(?:m)?",
           gap=r"evropského\s+parlamentu\s+a\s+rady|rady|komise|v\s+přenesené\s+pravomoci"
               r"|evropské\s+centrální\s+banky",
           pre=r"prováděcí(?:ho|m)?|rámcov(?:é|ého|ém)",
           art=r"článek|článku|článkem|čl\.",
           sub=r"odst\.|písm\.|bod(?:u|ě)?|pododstav(?:ec|ce)|věta|věty",
           source="32016R0679 Art. 93(2) 'článek 5 nařízení (EU) č. 182/2011', rec. 172 'čl. 28 odst. 2 nařízení "
                  "(ES) č. 45/2001'; 32018R0825 rec. 21 'čl. 7 odst. 2a nařízení (EU) 2016/1036'; 32023R1773 "
                  "Art. 5 'čl. 170 odst. 1 nařízení v přenesené pravomoci (EU) 2015/2446'"),
    _Forms("sk", R=r"nariaden(?:ie|ia|í|iu|ím)", L=r"smernic(?:a|e|u|i|ou)|smerníc",
           D=r"rozhodnut(?:ie|ia|í|iu|ím)",
           gap=r"európskeho\s+parlamentu\s+a\s+rady|rady|komisie",
           pre=r"vykonávac(?:ie|ieho|om|iemu|í)|delegovan(?:é|ého|om|ému|ý)|rámcov(?:é|ého|om)",
           art=r"článok|článku|článkom|článkoch|čl\.",
           sub=r"ods\.|písm\.|bod(?:u|e)?|pododsek(?:u)?|veta|vety",
           source="32016R0679 rec. 103 'článku 25 ods. 6 alebo článku 26 ods. 4 smernice 95/46/ES', rec. 172 "
                  "'článkom 28 ods. 2 nariadenia (ES) č. 45/2001'; 32023R1773 rec. 18 'článku 16 vykonávacieho "
                  "nariadenia Komisie (EÚ) 2023/1070'"),
    _Forms("pl", R=r"rozporządze(?:nie|nia|niu|niem|ń)", L=r"dyrektyw(?:a|y|ie|ę|ą)?", D=r"decyz(?:ja|ji|ję|ją|je)",
           gap=r"parlamentu\s+europejskiego\s+i\s+rady|rady|komisji|wykonawcz(?:e|ego|ym|ej|a|ą)"
               r"|delegowan(?:e|ego|ym|ej)|ramow(?:a|ej|ą)",
           art=r"artykuł(?:u|em|e)?|art\.",
           sub=r"ust\.|lit\.|pkt|akapit|zdanie|tiret",
           source="32016R0679 rec. 103 'art. 25 ust. 6 lub art. 26 ust. 4 dyrektywy 95/46/WE', rec. 172 'art. 28 "
                  "ust. 2 rozporządzenia (WE) nr 45/2001'; 32023R1773 rec. 18 'art. 16 rozporządzenia "
                  "wykonawczego Komisji (UE) 2023/1070'"),
    _Forms("sl", R=r"uredb(?:a|e|i|o|ama|ah)", L=r"direktiv(?:a|e|i|o|ami|ama)", D=r"sklep(?:a|u|om|ov)?",
           gap=r"evropskega\s+parlamenta\s+in\s+sveta|sveta|komisije",
           pre=r"izvedben(?:a|e|i|ega|em)|delegiran(?:a|e|i|ega)|okvirn(?:i|ega|em)",
           art=r"člen(?:a|u|om)?|čl\.",
           sub=r"odstav(?:ek|ka)|točk(?:a|e|i)|pododstav(?:ek|ka)",
           source="32016R0679 Art. 93(2) 'člen 5 Uredbe (EU) št. 182/2011', rec. 103 'člena 25(6) ali člena 26(4) "
                  "Direktive 95/46/ES'; 32023R1773 rec. 2 'Izvedbeni uredbi Komisije (EU) 2018/2066'"),
    _Forms("hr", R=r"uredb(?:a|e|i|u|om|ama)", L=r"direktiv(?:a|e|i|u|om)", D=r"odluk(?:a|e|u|om)|odluci",
           gap=r"europskog\s+parlamenta\s+i\s+vijeća|vijeća|komisije",
           pre=r"provedben(?:a|e|oj|om)|delegiran(?:a|e|oj|om)|okvirn(?:a|e|oj|om)",
           art=r"član(?:ak|ka|ku|kom)|čl\.",
           sub=r"stav(?:ak|ka|ku|kom)|točk(?:a|e|i|om)|podstav(?:ak|ka|ku|kom)",
           source="32016R0679 Art. 93(2) 'članak 5. Uredbe (EU) br. 182/2011', rec. 103 'članka 25. stavka 6. i "
                  "članka 26. stavka 4. Direktive 95/46/EZ', rec. 172 'člankom 28. stavkom 2. Uredbe (EZ) br. "
                  "45/2001'; 32023R1773 rec. 2 'Provedbenoj uredbi Komisije (EU) 2018/2066'"),
    _Forms("bg", R=r"регламент(?:а|ът|и|ите)?", L=r"директив(?:а|ата|и|ите)", D=r"решени(?:е|ето|я|ята)",
           gap=r"за\s+изпълнение|на\s+комисията|на\s+съвета",
           pre=r"делегиран(?:ия|ият)?|рамково(?:то)?",
           art=r"член(?:а|ът)?", conn=r"от|на",
           sub=r"параграф|алинея|буква|точка",
           source="32016R0679 Art. 93(2) 'член 5 от Регламент (ЕС) № 182/2011', Art. 4(25) 'член 1, параграф 1, "
                  "точка б) от Директива (ЕС) 2015/1535'; 32023R1773 rec. 18 'член 16 от Регламент за изпълнение "
                  "(ЕС) 2023/1070', Art. 5 'член 175 от Делегиран регламент (ЕС) 2015/2446'"),
    _Forms("el", R=_tonos(r"κανονισμ(?:ός|ού|ό|οί|ών|ούς)"), L=_tonos(r"οδηγ(?:ία|ίας|ίες|ιών)"),
           D=_tonos(r"απόφασ(?:η|ης|εις|εως)|αποφάσε(?:ις|ων|ως)"),
           gap=_tonos(r"πλαίσι(?:ο|ου)|του\s+συμβουλίου|της\s+επιτροπής"),
           pre=_tonos(r"εκτελεστικ(?:ός|ού|ό|ή|ής)|κατ['’]\s?εξουσιοδότηση"),
           art=_tonos(r"άρθρ(?:ο|ου)"), conn=_tonos(r"του|της|των"),
           sub=_tonos(r"παράγραφ(?:ος|ο|ου)|παρ\.|στοιχεί(?:ο|ου)|εδάφι(?:ο|ου)|περίπτωση"),
           source="32016R0679 Art. 93(2) 'το άρθρο 5 του κανονισμού (ΕΕ) αριθ. 182/2011', Art. 4(25) 'του άρθρου 1 "
                  "παράγραφος 1 στοιχείο β) της οδηγίας (ΕΕ) 2015/1535', rec. 19 'απόφασης-πλαίσιο "
                  "2008/977/ΔΕΥ'; 32023R1773 rec. 18 'άρθρο 16 του εκτελεστικού κανονισμού (ΕΕ) 2023/1070'"),
    _Forms("ro", R=r"regulament(?:ul|ului|ele|elor|e)?", L=r"directiv(?:a|ei|ele|elor|ă|e)",
           D=r"decizi(?:a|ei|ile|ilor|e)",
           gap=r"de\s+punere\s+[îi]n\s+aplicare|delegat(?:e|ă|ul)?|cadru",
           art=r"articol(?:ul|ului)|art\.", conn=r"din|al|a|ale",
           sub=r"alineatul|alin\.|litera|lit\.|punctul|pct\.|paragraful|teza",
           source="32016R0679 Art. 93(2) 'articolul 5 din Regulamentul (UE) nr. 182/2011', Art. 4(25) "
                  "'articolul 1 alineatul (1) litera (b) din Directiva'; 32018R0825 rec. 21 'articolul 7 "
                  "alineatul (2a) din Regulamentul (UE) 2016/1036'; 32023R1773 rec. 18 'articolul 16 din "
                  "Regulamentul de punere în aplicare (UE) 2023/1070'"),
    _Forms("mt", R=r"regolament(?:i)?", L=r"direttiv(?:a|i)", D=r"deċiżjoni(?:jiet)?|decizjoni(?:jiet)?",
           gap=r"ta['’]\s?implimentazzjoni|delegat[i]?|qafas|kwadru|tal-kunsill|tal-kummissjoni"
               r"|tal-parlament\s+ewropew\s+u\s+tal-kunsill",
           art=r"artikolu|artikoli", conn=r"ta['’]", glued=r"ta[lċdnrstxżz]-",
           sub=r"paragrafu|punt|subparagrafu",
           source="32016R0679 Art. 93(2) 'l-Artikolu 5 tar-Regolament (UE) Nru 182/2011', Art. 4(25) 'tal-Artikolu "
                  "1(1) tad-Direttiva (UE) 2015/1535', rec. 42 'mad-Direttiva tal-Kunsill 93/13/KEE'; 32023R1773 "
                  "rec. 19 'fl-Artikolu 30 tar-Regolament ta’ Implimentazzjoni (UE) 2023/1070'"),
    _Forms("ga", R=r"rialach[áa]i?n", L=r"(?:dt|th|t)reo(?:ir|racha)", D=r"g?ch?inn(?:eadh|idh|t[íi])",
           gap=r"cur\s+chun\s+feidhme|tarmligthe|r[ée]ime",
           art=r"airteagal|airteagail", conn=r"de|den", glued=r"d['’]",
           sub=r"m[íi]r|pointe|fomh[íi]r",
           source="32016R0679 Art. 93(2) 'Airteagal 5 de Rialachán (AE) Uimh. 182/2011', Art. 4(25) "
                  "'d'Airteagal 1(1) de Threoir (AE) 2015/1535'; 32023R1773 rec. 18 'Airteagal 16 de Rialachán "
                  "Cur Chun Feidhme (AE) 2023/1070'; 32022D2319 'Airteagal 3, pointe 8, de Rialachán (AE) "
                  "2018/1725'; 32023D1532 'Cinneadh 2014/512/CBES'"),
    _Forms("hu", R=r"rendelet\w*", L=r"irányelv\w*", D=r"(?:keret)?határozat\w*", type_after=True,
           gap=r"európai\s+parlamenti\s+és\s+tanácsi|tanácsi|bizottsági|végrehajtási|felhatalmazáson\s+alapuló",
           after=r"\d+[a-z]?\.\s*cikk\w*",
           source="32016R0679 Art. 93(2) 'a 182/2011/EU rendelet 5. cikkét', rec. 172 'a 45/2001/EK rendelet 28. "
                  "cikkének (2) bekezdésével', Art. 4(25) 'az (EU) 2015/1535 európai parlamenti és tanácsi "
                  "irányelv (19) 1. cikke'; 32018R0825 'az (EU) 2016/1036 rendelet 5. cikkének (9) bekezdése'"),
    _Forms("fi", R=r"asetu(?:s|ksen|ksessa|ksella|ksesta|kseen|sta|kset|sten|ksiin|ksia)",
           L=r"direktiivi(?:n|ssä|llä|stä|in|ä|t|en|eihin)?",
           D=r"päätö(?:s|ksen|ksessä|ksellä|ksestä|kseen|stä|kset|sten)",
           after=r"\d+(?:\s?[a-z])?\s+artikl\w*",
           source="32016R0679 Art. 93(2) 'asetuksen (EU) N:o 182/2011 5 artiklaa', rec. 103 'direktiivin 95/46/EY "
                  "25 artiklan 6 kohdan', Art. 4(25) 'direktiivin (EU) 2015/1535 (19) 1 artiklan'; 32018R0825 "
                  "'asetuksen (EU) 2016/1036 7 artiklan 2 a kohdassa'"),
    _Forms("et", R=r"määrus(?:e|t|es|ega|ele|est|ed|i|te|tele)?", L=r"direktiiv(?:i|iga|is|ist|ile|id|e)?",
           D=r"otsus(?:e|t|es|ega|ele|est|ed)?",
           after=r"artik(?:kel|li|lit|lis|lile|liga|list)\s+\d+[a-z]?",
           source="32016R0679 Art. 93(2) 'määruse (EL) nr 182/2011 artiklit 5', rec. 103 'direktiivi 95/46/EÜ "
                  "artikli 25 lõike 6'; 32018R0825 rec. 21 'määruse (EL) 2016/1036 artikli 7 lõikes 2a'"),
    _Forms("lv", R=r"regul(?:a|as|u|ā|ai|ām|ās)", L=r"direktīv(?:a|as|u|ā|ai|ām|ās)",
           D=r"(?:pamat)?lēmum(?:s|a|u|ā|am|i|us)",
           after=r"\d+(?:\.?[a-z])?\.\s*pant\w*",
           source="32016R0679 Art. 93(2) 'Regulas (ES) Nr. 182/2011 5. pantu', rec. 103 'Direktīvas 95/46/EK 25. "
                  "panta 6. punktu'; 32018R0825 rec. 21 'Regulas (ES) 2016/1036 7. panta 2.a punktā'"),
    _Forms("lt", R=r"reglament(?:as|o|ą|u|e|ai|ui|ų|us|uose|ams)", L=r"direktyv(?:a|os|ą|oje|ai|as|oms|ų)",
           D=r"sprendim(?:as|o|ą|u|e|ai|ui|ų|us|ams)",
           after=r"\d+[a-z]?\s+straipsn\w*",
           source="32016R0679 Art. 93(2) 'Reglamento (ES) Nr. 182/2011 5 straipsnis', rec. 103 'Direktyvos "
                  "95/46/EB 25 straipsnio 6 dalimi'; 32018R0825 'Reglamento (ES) 2016/1036 5 straipsnio 9 dalį'"),
)

#: the organisation an act is "of", in each language and script, counted in the
#: texts above (and 32014D0512's CFSP, 32023D1532's Irish CBES): the Union, the
#: Community, the EEC, justice and home affairs, the CFSP, Euratom, and the
#: older Communities the grammar read before
_ORG_CODES = (
    "EU", "UE", "EÚ", "ΕΕ", "ЕС", "EL", "ES", "AE",
    "EC", "CE", "EG", "EF", "EY", "EÜ", "EK", "EB", "EZ", "WE", "ЕО", "ΕΚ", "KE",
    "EEC", "CEE", "EWG", "EEG", "EØF", "ETY", "EMÜ", "EGK", "EEK", "EEB", "EHS", "EGS", "EEZ", "ЕИО", "ΕΟΚ", "KEE",
    "JHA", "JAI", "JI", "GAI", "SVV", "RIA", "RIF", "JSK", "ΔΕΥ", "CGB", "PUP", "TI", "TVR", "IB", "ĠAI", "JBZ",
    "WSiSW", "PNZ", "YOS", "ПВР",
    "CFSP", "PESC", "GASP", "SZBP", "FUSP", "GUSP", "ОВППС", "ΚΕΠΠΑ", "ÜVJP", "YUTP", "ZVSP", "KKBP", "BUSP",
    "KĀDP", "PESK", "GBVB", "WPZiB", "SZVP", "CBES",
    "Euratom", "Euratomas", "Евратом", "Ευρατόμ", "EAEC", "EWR", "EGKS", "ECSC", "CECA",
)
#: letters three scripts share: the Official Journal mixes them inside one code
#: ("EС" with a Cyrillic С in bg, "EΚ" with a Greek Κ in el; 32016R0679, 32022R2065)
_LOOKALIKES = ("AΑА", "BΒВ", "CС", "EΕЕ", "HΗН", "IΙІ", "KΚК", "MΜМ", "NΝ", "OΟО", "PΡР", "TΤТ", "XΧХ", "YΥ",
               "ZΖ")
_GLYPH = {c: g for g in _LOOKALIKES for c in g}


def _spell(code: str) -> str:
    return "".join(f"[{_GLYPH[c]}]" if c in _GLYPH else f"[{_TONOS[c]}]" if c in _TONOS else re.escape(c)
                   for c in code)


_ORG = "|".join(_spell(c) for c in sorted(set(_ORG_CODES), key=lambda c: (-len(c), c)))
_ORGS = rf"(?:{_ORG})(?:\s*,\s*(?:{_ORG}))*"
#: the "No" before a number, as each language writes it: No, Nr., n., n.º (the
#: OJ's superscript º reads "n.o"), n°, nr, N:o, č., št., br., αριθ., №, Nru, Uimh.
_NO = r"(?:No|Nr|N:o|n\.?\s?[ºo°]|n°|n|č|št|br|" + _tonos("αριθ") + r"|№|Nru|Uimh)\.?"


def _union(field: str, forms=_FORMS) -> str:
    return "|".join(f"(?:{getattr(f, field)})" for f in forms if getattr(f, field))


_FIRST = tuple(f for f in _FORMS if not f.type_after)
_HU = next(f for f in _FORMS if f.type_after)
#: the look-back from a number run: the type word, what a language writes
#: between it and the number, its organisation, a "No"
_BEFORE_SRC = (
    rf"(?:(?P<R>{_union('R', _FIRST)})|(?P<L>{_union('L', _FIRST)})|(?P<D>{_union('D', _FIRST)}))"
    rf"(?:(?:\s+|-)(?:{_union('gap', _FIRST)}))*(?:\s*\((?P<org>{_ORGS})\))?(?:\s+(?P<no>{_NO}))?\s*")
#: (a "No" is a word of its own: "Richtlinien 2001/95/EG" is never "Richtlinie" and Italian "n")
_BEFORE = re.compile(_BEFORE_SRC + "$", re.IGNORECASE)


def _reversed(pattern: str) -> str:
    """`pattern` read right to left, for `match` on a reversed string: the
    regex subset the forms are written in — literals, escapes, classes,
    `(?:…)` and `(?P<name>…)` groups, alternation, the quantifiers `? * +
    {m,n}` (lazy or not). A look-around or an anchor is refused."""
    pos = 0

    def alt() -> str:
        nonlocal pos
        seqs = [seq()]
        while pos < len(pattern) and pattern[pos] == "|":
            pos += 1
            seqs.append(seq())
        return "|".join(seqs)

    def seq() -> str:
        nonlocal pos
        items = []
        while pos < len(pattern) and pattern[pos] not in "|)":
            item = atom()
            items.append(item + quantifier())
        return "".join(reversed(items))

    def atom() -> str:
        nonlocal pos
        c, start = pattern[pos], pos
        if c == "\\":
            pos += 2
        elif c == "[":
            pos += 1 + (pattern[pos + 1] == "^")
            pos += pattern[pos] == "]"
            while pattern[pos] != "]":
                pos += 2 if pattern[pos] == "\\" else 1
            pos += 1
        elif c == "(":
            head = next((h for h in ("(?:", "(?i:") if pattern.startswith(h, pos)), None)
            if head is None and pattern.startswith("(?P<", pos):
                head = pattern[pos:pattern.index(">", pos) + 1]
            if head is None:
                raise ValueError(f"not reversible at {pos}: {pattern[pos:pos + 12]!r}")
            pos += len(head)
            inner = alt()
            if pattern[pos] != ")":
                raise ValueError(f"unclosed group at {start}")
            pos += 1
            return head + inner + ")"
        elif c in "^$":
            raise ValueError(f"an anchor at {pos}")
        else:
            pos += 1
        return pattern[start:pos]

    def quantifier() -> str:
        nonlocal pos
        start = pos
        if pos < len(pattern) and pattern[pos] in "?*+":
            pos += 1
        elif pos < len(pattern) and pattern[pos] == "{":
            pos = pattern.index("}", pos) + 1
        else:
            return ""
        pos += pos < len(pattern) and pattern[pos] == "?"
        return pattern[start:pos]

    out = alt()
    if pos != len(pattern):
        raise ValueError(f"unbalanced at {pos}")
    return out


#: how `_BEFORE` is RUN: one anchored match on the reversed window, instead of
#: a search trying the 24 languages' type words at each of its positions —
#: 8.2 us a number run against 102, the mean over the 10,241 runs of the 143
#: harvested acts (2026-11-21); test_the_reversed_look_back_is_the_grammar
#: (tests/ask/test_article_citations_24.py) holds the two to the same answer
_BEFORE_REV = re.compile(_reversed(_BEFORE_SRC), re.IGNORECASE)
#: Hungarian: after the number run, what stands before the type word, the type word
#: — past a second organisation ("1101/2008/EK, Euratom … rendelet") and a footnote's call
_TYPE_AFTER = re.compile(
    rf"(?:\s*,\s*(?:{_ORG}))*\s*(?:\(\d{{1,3}}\)\s*)?(?:(?:{_HU.gap})\s+){{0,3}}"
    rf"(?:(?P<R>{_HU.R})|(?P<L>{_HU.L})|(?P<D>{_HU.D}))",
    re.IGNORECASE)
_ORG_BEFORE = re.compile(rf"\((?:{_ORGS})\)\s*$", re.IGNORECASE)
#: an article's number as the languages write it: "5", "5a", "14α", "14 bis",
#: "16.o" / "14.º-A" (pt), "5." (hr), its paragraph after a dot "25.6" (sv)
_LATIN_RE = r"bis|ter|quater|quinquies|sexies|septies|octies|novies|decies"
_ANUM = (rf"\d++(?:[a-zα-ωа-я]{{1,2}}(?![^\W\d_])|\s?-?\s?(?:{_LATIN_RE})(?![^\W\d_])(?!\s*\d)"
         rf"|\.?[ºo°](?:\s?-\s?[a-z](?![^\W\d_]))?)?(?:\.\d++[a-z]?)*\.?")
#: (a number is never split, `\d++`: "12" read as "1" and a token "2" made a
#: failing search try every split, 424 us on one German sentence; a Latin
#: ordinal before a number is a range, "Artikel 12 bis 15", never "12 bis")
#: what may stand between the article and the act: "(1)(a)", ", stk. 6,", "odst. 1
#: písm. f)", "25.6", "alineatul (1) litera (b)" — a closed vocabulary, never any
#: word (no connector reads the genitive languages' "článek 5 Smlouvy a nařízení")
_TOKEN = (rf"(?:\(\d+[a-z]?\)|\([a-z]{{1,4}}\)|[a-zα-ωа-я]{{1,3}}\)|\d++[a-z]?\)|\d++(?:-?[a-z])?\.?"
          rf"|\.\d++[a-z]?|[a-z](?![^\W\d_])|(?:{_union('sub')})(?![^\W\d_]))")
_ART_WORD = _union("art")
#: an article before the type word, within the window: its subdivisions read
#: (lazily, so a connector is never taken for a point), the connector where
#: the language has one, the institution or the act's kind before the type
#: word, a compound's first part ("Durchführungs-verordnung"); or, with a
#: connector, any short phrase, a word at a time (no "/": never across another act)
_ARTICLE = re.compile(
    rf"(?=[ačчάα])"            # every article word's first letter: the search skips the others at once
    rf"(?:(?P<art>(?:{_ART_WORD})\s*{_ANUM}(?:[\s,]*{_TOKEN}){{0,10}}?)\s*,?\s*(?:(?:{_union('conn')})\s+"
    rf"|{_union('glued')})?"
    rf"|(?P<lib>(?:{_ART_WORD})\s*{_ANUM}(?:[,\s]+(?:\d++(?:\.\d++)*[a-z]?|[^\s.;:/]++)){{0,10}}?)"
    rf"\s*,?\s+(?:(?:{_union('conn')})\s+|{_union('glued')}))"
    rf"(?:(?:the|la)\s+)?(?:(?:{_union('pre')})[\s-]+){{0,3}}[^\W\d_]*+$",
    re.IGNORECASE)
#: where an article may start: its word and the first digit of its number
_ART_START = re.compile(rf"(?=[ačчάα])(?:{_ART_WORD})\s*\d", re.IGNORECASE)


def _article_before(before: str, end: int) -> re.Match | None:
    """The article NEAREST the act, `before[:end]` ending at its type word:
    the last article word whose number, subdivisions and connector reach
    it. Never the first of a phrase: "Articolo 84 All'articolo 2, ultimo
    comma del regolamento (CEE, Euratom, CECA) n. 259/68" (a heading, then
    the sentence; tests/fixtures/eurlex/eurlex-dumpfirst 31972R1473) cites
    article 2, which the leftmost search read as 84. A list gives its last
    ("Article 25(6) or Article 26(4) of Directive 95/46/EC": 26), as the
    genitive languages always did. An anchored match at each article word,
    the nearest first — never a search at every position."""
    for start in reversed([m.start() for m in _ART_START.finditer(before, 0, end)]):
        m = _ARTICLE.match(before, start, end)
        if m is not None:
            return m
    return None


#: an article after the act (fi et lv lt hu), past a footnote's call "(19)"
#: and Hungarian's "az"
_ART_AFTER = re.compile(rf"\s*(?:\(\d{{1,3}}\)\s*)?(?:az?\s+)?(?P<art>{_union('after')})", re.IGNORECASE)
#: an article's word and number, whichever comes first
_ART_HEAD = re.compile(
    rf"(?P<word>{_ART_WORD}|artik(?:kel|li|lit|lis|lile|liga|list))\s*(?P<num>{_ANUM})"
    rf"|(?P<num2>\d+(?:\s?[a-z]|\.?[a-z])?)\.?\s*(?P<word2>cikk\w*|artikl\w*|pant\w*|straipsn\w*)",
    re.IGNORECASE)
_WINDOW = 120
#: the number run every non-CELEX citation holds: A/B, optionally /ORG
#: (digit first, so the scan skips to the digits in C; the look-behind a
#: leading `(?<![\d/])` held is `_occurrences`'s test of the character before —
#: the same matches, at half the cost: 1.14 s against 0.50 s over the 143 acts)
_NUMBERS = re.compile(rf"(?P<a>\d{{1,4}})/(?P<b>\d{{1,4}})(?:/(?P<suffix>(?i:{_ORG}))(?![^\W\d_]))?(?![\d/])")
#: (likewise: its `(?<![0-9A-Za-z])` is `_ALNUM`'s test in `_occurrences`)
_CELEX = re.compile(r"(?P<celex>[0-9]\d{4}[A-Z]{1,2}\d{4}(?:R\(\d{2}\))?(?:-\d{8})?)(?![0-9A-Za-z])")
_ALNUM = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
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


def _year_number(a: str, b: str, letter: str, *, no: bool, org: bool, after: bool) -> tuple[int, str] | None:
    """The act's year and number from its run "a/b". A "No" puts the number
    first ("(EC) No 1907/2006") — the year first where the number is none
    (the Polish and Latvian OJ's own "(UE) nr 2016/1036", 32018R0825); else
    the year comes first ("95/46/EC", "(EU) 2016/679") and a number first
    where that is no year ("Decision 768/2008/EC"). Hungarian writes an older
    regulation or decision with no "No", the number first ("a 45/2001/EK
    rendelet", "a 768/2008/EK határozat"): there the year is first only where
    a is a year and b is none ("a 2011/833/EU határozat")."""
    a_year_only = len(a) == 4 and _year(a) is not None and not (len(b) == 4 and _year(b) is not None)
    if no or (after and not org and letter != "L" and not a_year_only):
        order = ((b, a), (a, b))
    else:
        order = ((a, b), (b, a))
    for y, n in order:
        year = _year(y)
        if year is not None:
            return year, n
    return None


def _art_text(m: re.Match | None) -> str | None:
    if m is None:
        return None
    return _WS.sub(" ", m.group("art") or m.group("lib")).strip(" ,") or None


def _occurrences(text: str) -> list[tuple[int, str, str | None]]:
    """Every act a text names, `(position, celex, article text or None)`, in
    order of appearance, each time it is named."""
    if not text or not _ANCHOR.search(text):
        return []
    found: list[tuple[int, str, str | None]] = [
        (m.start(), m.group("celex"), None) for m in _CELEX.finditer(text)
        if not m.start() or text[m.start() - 1] not in _ALNUM]
    for m in _NUMBERS.finditer(text):
        if m.start() and (text[m.start() - 1] == "/" or text[m.start() - 1].isdecimal()):
            continue                                      # inside a longer run: "12345/6", "1/2/3"
        before = text[max(0, m.start() - _WINDOW):m.start()]
        b = _BEFORE_REV.match(before[::-1])
        if b is not None:
            letter = "R" if b.group("R") else "L" if b.group("L") else "D"
            got = _year_number(m.group("a"), m.group("b"), letter, no=bool(b.group("no")),
                               org=bool(b.group("org") or m.group("suffix")), after=False)
            art = _art_text(_article_before(before, len(before) - b.end())) \
                or _art_text(_ART_AFTER.match(text, m.end()))
        else:
            t = _TYPE_AFTER.match(text, m.end())          # hu: "a 182/2011/EU rendelet"
            if t is None:
                continue
            letter = "R" if t.group("R") else "L" if t.group("L") else "D"
            org = _ORG_BEFORE.search(text, max(0, m.start() - 40), m.start()) is not None
            got = _year_number(m.group("a"), m.group("b"), letter, no=False, org=org, after=True)
            art = _art_text(_ART_AFTER.match(text, t.end()))
        if got is None:
            continue
        year, number = got
        found.append((m.start(), f"3{year:04d}{letter}{int(number):04d}", art))
    return sorted(found, key=lambda t: t[0])


def citations(text: str) -> list[dict]:
    """The acts a text names: `{celex, art?}` each, in order of appearance,
    once."""
    out: list[dict] = []
    seen: set[str] = set()
    for _pos, celex, art in _occurrences(text):
        if celex not in seen:
            seen.add(celex)
            out.append({"celex": celex, "art": art})
    return out


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
# is invented here — an act is cited by its number or its CELEX. An acronym
# could be read only from DATA with a named source: a short or popular title
# the CDM notice carries. The notice as app/plugins/eurlex/notice.py reads it
# carries none (no short, popular or alternative title field, in the parser
# or the fixtures, 2026-11-12), so "GDPR" stays unpinned until one is read.


def article_citations(query: str, refs: RefLookup | None = None, facts=()) -> list[ArticleCitation]:
    """Every article the query cites of an act it names by number or CELEX,
    in any of the 24 languages ("Article 5 of Regulation (EU) 2016/679",
    "článek 5 nařízení (EU) 2016/679", "az (EU) 2016/679 rendelet 5. cikke"):
    the act's `celex:<CELEX>`, a `eurlex` record's own key, and the article
    in one spelling (`article_number`), each (act, article) once. The label
    is the query's own words for the article. Needs no reference data."""
    out: list[ArticleCitation] = []
    seen: set[tuple[str, str]] = set()
    for _pos, celex, art in _occurrences(query):
        m = _ART_HEAD.match(art or "")
        if m is None:
            continue
        if m.group("word"):
            num, label = m.group("num"), f"{m.group('word')} {m.group('num').rstrip('.')}"
        else:
            num, label = m.group("num2"), _WS.sub(" ", m.group(0))
        article = article_number(num)
        if article is None or (celex, article) in seen:
            continue
        seen.add((celex, article))
        out.append(ArticleCitation(keys=(f"celex:{celex}",), article=article, label=label))
    return out


#: a section's label (formex.sections): "[partition › ]Article 5 — title"
#: — the French Official Journal heads the first article "Article premier".
#: IN THE 24 LANGUAGES (2026-11-21, the C11 review: "Articolul 1" and "1. cikk"
#: read as no article, so a Romanian or Hungarian Work never pinned one): the
#: heading forms the XHTML reader reads (app/plugins/eurlex/rendition.py
#: `_WORD_FIRST`, `_NUM_FIRST`, `_ORDINAL`), so a label and a heading agree by
#: construction, and its number in ONE spelling (`article_number`)
from ..plugins.eurlex.rendition import _NUM as _R_NUM, _NUM_FIRST as _R_NUM_FIRST, \
    _WORD_FIRST as _R_WORD_FIRST  # noqa: E402
_LABEL = re.compile(
    rf"^\s*(?:Article\s+(?P<first>premier|1er)|(?:Article|Articolo|Artículo|Artigo)\s+(?P<only>unique|unico|único)"
    rf"|(?:{_R_WORD_FIRST}|Art\.)\s*(?P<num>{_R_NUM})"
    rf"|(?P<numfirst>{_R_NUM_FIRST}))(?=\s|$|[—–:,-])", re.IGNORECASE)
#: an inserted article's Latin ordinal, Greek and Cyrillic letter, as its
#: place in the alphabet: "14 bis" (es fr it nl), "14α" (el), "14а" (bg) are
#: "14a" (cs da de en … ), so a citation in one language finds the article in
#: another
_LATIN_ORDINALS = ("bis", "ter", "quater", "quinquies", "sexies", "septies", "octies", "novies", "decies")
_ALPHABETS = ("αβγδεζηθικλμ", "абвгдежзиклм")
_NUMBER = re.compile(r"(\d+)\s*(?:\.?\s*[º°]|\.o\b)?\s*[.\-]?\s*([^\W\d_]*)")


def article_number(text: str | None) -> str | None:
    """An article's number in one spelling: "14", "14a", "14 a", "14.a",
    "14α", "14а", "14 bis", "14-bis", "14.o-A", "14.º", "14a." → "14" or
    "14a"; None without a number."""
    m = _NUMBER.search(str(text or ""))
    if m is None:
        return None
    num, suffix = m.group(1), m.group(2).lower()
    if suffix == "er" and num == "1":
        return num        # French "article 1er" (32016R0679 Art. 4(25): "l'article 1er, paragraphe 1")
    if suffix in _LATIN_ORDINALS:
        return num + "abcdefghi"[_LATIN_ORDINALS.index(suffix)]
    for alphabet in _ALPHABETS:
        if suffix and all(ch in alphabet for ch in suffix):
            return num + "".join("abcdefghijkl"[alphabet.index(ch)] for ch in suffix)
    if suffix and len(suffix) <= 3 and suffix.isascii() and suffix.isalpha():
        return num + suffix
    return num            # a word after the number ("1 artikla", "5. cikk"): the number


def article_of_label(label: str | None) -> str | None:
    m = _LABEL.match(str(label or "").rsplit(" › ", 1)[-1])
    if m is None:
        return None
    if m.group("first") or m.group("only"):
        return "1"
    if m.group("numfirst"):
        return article_number(re.sub(r"(?i)\s*(?:artikla|cikk|pants|straipsnis)\s*$", "", m.group("numfirst")))
    return article_number(m.group("num"))
