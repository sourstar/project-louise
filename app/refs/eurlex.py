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
    R: str = ""                 # the type words, inflected: regulation …
    L: str = ""                 # directive …
    D: str = ""                 # decision …
    gap: str = ""               # between the type word and the number (hu: the number and the type word)
    pre: str = ""               # between the article and the type word, after the connector
    art: str = ""               # the article's word, the article before the act
    conn: str = ""              # article → act, a word ("of"); none: the genitive
    glued: str = ""             # article → act, glued to the type word (mt "tar-Regolament")
    sub: str = ""               # a paragraph's, a point's word between the article and the act
    after: str = ""             # the article AFTER the act: its whole form
    num_first: str = ""         # the article's word AFTER its number, the article before the act ("5. člen")
    num_word: str = ""          # the article's word after its number, the article after the act ("5. cikke")
    conj: str = ""              # and, or: what joins a list's articles, a coordination's acts
    range: str = ""             # a range's word between its two ends ("Articles 3 to 7")
    each: str = ""              # an article "of each of" the coordinated acts that follow
    link: str = ""              # a word between the conjunction and the next article's word ("o el artículo")
    opener: str = ""            # a range's word before its first number ("articoli da 3 a 7")
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
           art=r"articles?", conn=r"of", conj=r"and|or", range=r"to",
           sub=r"paragraphs?|subparagraphs?|points?|indents?|first|second|third|fourth|sentence",
           source="32016R0679 Art. 93(2) 'Article 5 of Regulation (EU) No 182/2011'; 32023R1773 rec. 18 "
                  "'Article 16 of Commission Implementing Regulation (EU) 2023/1070', Art. 6 'Article 170(1) of "
                  "Delegated Regulation (EU) 2015/2446'"),
    _Forms("fr", R=r"r[èe]glements?", L=r"directives?", D=r"d[ée]cisions?",
           gap=r"d['’]\s?ex[ée]cution|d[ée]l[ée]gu[ée]s?|cadre|du\s+conseil|de\s+la\s+commission"
               r"|du\s+parlement\s+européen\s+et\s+du\s+conseil",
           art=r"articles?", conn=r"du|de\s+la|des|de", conj=r"et|ou", range=r"à", link=r"de\s+l['’]|l['’]|aux?",
           sub=r"paragraphes?|alin[ée]as?|points?|premier|première|deuxième|troisième|tirets?|phrase",
           source="32016R0679 Art. 93(2) 'l'article 5 du règlement (UE) no 182/2011', rec. 19 'décision-cadre "
                  "2008/977/JAI'; 32023R1773 Art. 6 'l’article 170, paragraphe 1, du règlement délégué (UE) "
                  "2015/2446', title 'RÈGLEMENT D’EXÉCUTION (UE) 2023/1773'"),
    _Forms("de", R=r"verordnung(?:en)?", L=r"richtlinien?", D=r"beschl(?:uss(?:es)?|üsse)|entscheidung(?:en)?",
           pre=r"delegierten?",
           art=r"artikel(?:n|s)?", conn=r"der|des", conj=r"und|oder", range=r"bis",
           link=r"des|der|den|dem",
           sub=r"absatz|abs\.|unterabsatz|uabs\.|buchstaben?|buchst\.|satz|nummer|nr\.|ziffer",
           source="32016R0679 Art. 93(2) 'Artikel 5 der Verordnung (EU) Nr. 182/2011', rec. 19 'des "
                  "Rahmenbeschlusses 2008/977/JI'; 32023R1773 Art. 6 'Artikel 170 Absatz 1 der delegierten "
                  "Verordnung (EU) 2015/2446', rec. 18 'Artikel 16 der Durchführungsverordnung (EU) 2023/1070'"),
    _Forms("it", R=r"regolament[oi]", L=r"direttiv[ae]", D=r"decision[ei]",
           gap=r"di\s+esecuzione|delegat[oi]|quadro|del\s+consiglio|della\s+commissione",
           art=r"articol[oi]", conn=r"del|della|dello|dei|delle|degli", glued=r"dell['’]",
           conj=r"e|o", range=r"a", link=r"dell['’]|all['’]|l['’]|agli|degli", opener=r"da",
           sub=r"paragraf[oi]|comm[ai]|letter[ae]|punt[oi]|primo|secondo|terzo",
           source="32016R0679 Art. 93(2) 'l'articolo 5 del regolamento (UE) n. 182/2011'; 32023R1773 rec. 18 "
                  "'all’articolo 16 del regolamento di esecuzione (UE) 2023/1070', Art. 6 'dell’articolo 170, "
                  "paragrafo 1, del regolamento delegato (UE) 2015/2446'"),
    _Forms("es", R=r"reglamentos?", L=r"directivas?", D=r"decisi[óo]n(?:es)?",
           gap=r"de\s+ejecuci[óo]n|delegados?|marco",
           art=r"art[íi]culos?", conn=r"del|de\s+la|de\s+los|de\s+las|de", conj=r"y|o", range=r"a", link=r"el|los",
           sub=r"apartados?|letras?|p[áa]rrafos?|puntos?|guiones?",
           source="32016R0679 Art. 93(2) 'el artículo 5 del Reglamento (UE) n.o 182/2011'; 32023R1773 rec. 18 "
                  "'el artículo 16 del Reglamento de Ejecución (UE) 2023/1070', Art. 6 'el artículo 170, "
                  "apartado 1, del Reglamento Delegado (UE) 2015/2446'"),
    _Forms("pt", R=r"regulamentos?", L=r"dir(?:e|ec)tivas?", D=r"decis(?:ão|ao|ões|oes)",
           gap=r"de\s+execu[çc][ãa]o|delegados?|quadro",
           art=r"artigos?", conn=r"do|da|dos|das", conj=r"e|ou", range=r"a", link=r"nos?|os?|aos?",
           sub=r"n\.?\s?[ºo°]|al[íi]neas?|par[áa]grafos?|pontos?|subpar[áa]grafos?",
           source="32016R0679 Art. 93(2) 'o artigo 5.o do Regulamento (UE) n.o 182/2011', rec. 19 "
                  "'Decisão-Quadro 2008/977/JAI'; 32023R1773 Art. 6 'o artigo 170.o, n.o 1, do Regulamento "
                  "Delegado (UE) 2015/2446', rec. 18 'o artigo 16.o do Regulamento de Execução (UE) 2023/1070'"),
    _Forms("nl", R=r"verordening(?:en)?", L=r"richtl(?:ij|ĳ)n(?:en)?", D=r"besluit(?:en)?",
           pre=r"gedelegeerde",
           art=r"artikel(?:en)?", conn=r"van(?:\s+de|\s+het)?", conj=r"en|of", range=r"tot\s+en\s+met", link=r"de",
           sub=r"lid|leden|punt(?:en)?|onder|alinea|eerste|tweede|derde|volzin",
           source="32016R0679 Art. 93(2) 'artikel 5 van Verordening (EU) nr. 182/2011', rec. 42 'Richtlĳn "
                  "93/13/EEG' (the OJ's ĳ ligature); 32023R1773 Art. 6 'artikel 170, lid 1, van Gedelegeerde "
                  "Verordening (EU) 2015/2446', rec. 18 'artikel 16 van Uitvoeringsverordening (EU) 2023/1070'"),
    _Forms("da", R=r"forordning(?:en|er|erne)?", L=r"direktiv(?:et|er|erne)?",
           D=r"afgørelse(?:n|r|rne)?|beslutning(?:en|er)?",
           pre=r"europa-parlamentets\s+og\s+rådets|rådets|kommissionens|delegerede?|delegeret",
           art=r"artikel", conn=r"i|af", conj=r"og|eller",
           sub=r"stk\.|litra|nr\.|afsnit|punktum",
           source="32016R0679 Art. 93(2) 'artikel 5 i forordning (EU) nr. 182/2011', rec. 106 'artikel 25, "
                  "stk. 6, eller artikel 26, stk. 4, i direktiv 95/46/EF', Art. 4(25) 'artikel 1, stk. 1, litra b), "
                  "i Europa-Parlamentets og Rådets direktiv (EU) 2015/1535'; 32023R1773 'gennemførelsesforordning'"),
    _Forms("sv", R=r"förordning(?:en|ar|arna)?", L=r"direktiv(?:et|en)?", D=r"beslut(?:et)?",
           pre=r"europaparlamentets\s+och\s+rådets|rådets|kommissionens|delegerade?",
           art=r"artikel|artiklarna|artiklar", conn=r"i", conj=r"och|eller",
           sub=r"punkt(?:en)?|led(?:en)?|stycket|första|andra|tredje",
           source="32016R0679 Art. 93(2) 'artikel 5 i förordning (EU) nr 182/2011', rec. 106 'artikel 25.6 eller "
                  "26.4 i direktiv 95/46/EG', Art. 4(25) 'artikel 1.1 b i Europaparlamentets och rådets direktiv "
                  "(EU) 2015/1535'; 32023R1773 'genomförandeförordning'"),
    _Forms("cs", R=r"nařízení(?:m|ch)?", L=r"směrnic(?:e|i|í)?", D=r"rozhodnutí(?:m)?",
           gap=r"evropského\s+parlamentu\s+a\s+rady|rady|komise|v\s+přenesené\s+pravomoci"
               r"|evropské\s+centrální\s+banky",
           pre=r"prováděcí(?:ho|m)?|rámcov(?:é|ého|ém)",
           art=r"článek|článku|článkem|článcích|články|článků|čl\.",
           conj=r"a|nebo", range=r"až",
           sub=r"odst\.|písm\.|bod(?:u|ě)?|pododstav(?:ec|ce)|věta|věty",
           source="32016R0679 Art. 93(2) 'článek 5 nařízení (EU) č. 182/2011', rec. 172 'čl. 28 odst. 2 nařízení "
                  "(ES) č. 45/2001'; 32018R0825 rec. 21 'čl. 7 odst. 2a nařízení (EU) 2016/1036'; 32023R1773 "
                  "Art. 6 'čl. 170 odst. 1 nařízení v přenesené pravomoci (EU) 2015/2446'"),
    _Forms("sk", R=r"nariaden(?:ie|ia|í|iu|ím)", L=r"smernic(?:a|e|u|i|ou)|smerníc",
           D=r"rozhodnut(?:ie|ia|í|iu|ím)",
           gap=r"európskeho\s+parlamentu\s+a\s+rady|rady|komisie",
           pre=r"vykonávac(?:ie|ieho|om|iemu|í)|delegovan(?:é|ého|om|ému|ý)|rámcov(?:é|ého|om)",
           art=r"článok|článku|článkom|článkoch|články|článkov",
           conj=r"a|alebo", range=r"až",
           sub=r"ods\.|písm\.|bod(?:u|e)?|pododsek(?:u)?|veta|vety",
           source="32016R0679 rec. 106 'článku 25 ods. 6 alebo článku 26 ods. 4 smernice 95/46/ES', rec. 172 "
                  "'článkom 28 ods. 2 nariadenia (ES) č. 45/2001'; 32023R1773 rec. 18 'článku 16 vykonávacieho "
                  "nariadenia Komisie (EÚ) 2023/1070'"),
    _Forms("pl", R=r"rozporządze(?:nie|nia|niu|niem|ń|niach|niami|niom)", L=r"dyrektyw(?:a|y|ie|ę|ą)?", D=r"decyz(?:ja|ji|ję|ją|je)",
           gap=r"parlamentu\s+europejskiego\s+i\s+rady|rady|komisji|wykonawcz(?:e|ego|ym|ej|a|ą)"
               r"|delegowan(?:e|ego|ym|ej)|ramow(?:a|ej|ą)",
           art=r"artykuł(?:u|em|e|y|ów)?|art\.", conj=r"i|lub|oraz",
           sub=r"ust\.|lit\.|pkt|akapit|zdanie|tiret",
           source="32016R0679 rec. 106 'art. 25 ust. 6 lub art. 26 ust. 4 dyrektywy 95/46/WE', rec. 172 'art. 28 "
                  "ust. 2 rozporządzenia (WE) nr 45/2001'; 32023R1773 rec. 18 'art. 16 rozporządzenia "
                  "wykonawczego Komisji (UE) 2023/1070'"),
    _Forms("sl", R=r"uredb(?:a|e|i|o|ama|ah)", L=r"direktiv(?:a|e|i|o|ami|ama)", D=r"sklep(?:a|u|om|ov)?",
           gap=r"evropskega\s+parlamenta\s+in\s+sveta|sveta|komisije",
           pre=r"izvedben(?:a|e|i|ega|em)|delegiran(?:a|e|i|ega)|okvirn(?:i|ega|em)",
           art=r"člen(?:a|u|om|i|ov|e)?", conj=r"in|ali", range=r"do",
           sub=r"odstav(?:ek|ka)|točk(?:a|e|i)|pododstav(?:ek|ka)",
           source="32016R0679 Art. 93(2) 'člen 5 Uredbe (EU) št. 182/2011', rec. 106 'člena 25(6) ali člena 26(4) "
                  "Direktive 95/46/ES'; 32023R1773 rec. 2 'Izvedbeni uredbi Komisije (EU) 2018/2066'"),
    _Forms("hr", R=r"uredb(?:a|e|i|u|om|ama)", L=r"direktiv(?:a|e|i|u|om)", D=r"odluk(?:a|e|u|om)|odluci",
           gap=r"europskog\s+parlamenta\s+i\s+vijeća|vijeća|komisije",
           pre=r"provedben(?:a|e|oj|om)|delegiran(?:a|e|oj|om)|okvirn(?:a|e|oj|om)",
           art=r"član(?:ak|ka|ku|kom|ci|aka|cima|ke)", conj=r"i|ili", range=r"do", opener=r"od",
           sub=r"stav(?:ak|ka|ku|kom)|točk(?:a|e|i|om)|podstav(?:ak|ka|ku|kom)",
           source="32016R0679 Art. 93(2) 'članak 5. Uredbe (EU) br. 182/2011', rec. 106 'članka 25. stavka 6. i "
                  "članka 26. stavka 4. Direktive 95/46/EZ', rec. 172 'člankom 28. stavkom 2. Uredbe (EZ) br. "
                  "45/2001'; 32023R1773 rec. 2 'Provedbenoj uredbi Komisije (EU) 2018/2066'"),
    _Forms("bg", R=r"регламент(?:а|ът|и|ите)?", L=r"директив(?:а|ата|и|ите)", D=r"решени(?:е|ето|я|ята)",
           gap=r"за\s+изпълнение|на\s+комисията|на\s+съвета",
           pre=r"делегиран(?:ия|ият)?|рамково(?:то)?",
           art=r"член(?:а|ът|ове)?", conn=r"от|на", conj=r"и|или",
           sub=r"параграф|алинея|буква|точка",
           source="32016R0679 Art. 93(2) 'член 5 от Регламент (ЕС) № 182/2011', Art. 4(25) 'член 1, параграф 1, "
                  "точка б) от Директива (ЕС) 2015/1535'; 32023R1773 rec. 18 'член 16 от Регламент за изпълнение "
                  "(ЕС) 2023/1070', Art. 6 'член 175 от Делегиран регламент (ЕС) 2015/2446'"),
    _Forms("el", R=_tonos(r"κανονισμ(?:ός|ού|ό|οί|ών|ούς)"), L=_tonos(r"οδηγ(?:ία|ίας|ίες|ιών)"),
           D=_tonos(r"απόφασ(?:η|ης|εις|εως)|αποφάσε(?:ις|ων|ως)"),
           gap=_tonos(r"πλαίσι(?:ο|ου)|του\s+συμβουλίου|της\s+επιτροπής"),
           pre=_tonos(r"εκτελεστικ(?:ός|ού|ό|ή|ής)|κατ['’]\s?εξουσιοδότηση"),
           art=_tonos(r"άρθρ(?:ο|ου|α|ων)"), conn=_tonos(r"του|της|των"), conj=_tonos(r"και|ή"), range=_tonos(r"έως"),
           link=_tonos(r"του|της|στο|στα|τα|το"),
           sub=_tonos(r"παράγραφ(?:ος|ο|ου)|παρ\.|στοιχεί(?:ο|ου)|εδάφι(?:ο|ου)|περίπτωση"),
           source="32016R0679 Art. 93(2) 'το άρθρο 5 του κανονισμού (ΕΕ) αριθ. 182/2011', Art. 4(25) 'του άρθρου 1 "
                  "παράγραφος 1 στοιχείο β) της οδηγίας (ΕΕ) 2015/1535', rec. 19 'απόφασης-πλαίσιο "
                  "2008/977/ΔΕΥ'; 32023R1773 rec. 18 'άρθρο 16 του εκτελεστικού κανονισμού (ΕΕ) 2023/1070'"),
    _Forms("ro", R=r"regulament(?:ul|ului|ele|elor|e)?", L=r"directiv(?:a|ei|ele|elor|ă|e)",
           D=r"decizi(?:a|ei|ile|ilor|e)",
           gap=r"de\s+punere\s+[îi]n\s+aplicare|delegat(?:e|ă|ul)?|cadru",
           art=r"articol(?:ul|ului|ele|elor)", conn=r"din|al|a|ale", conj=r"și|şi|sau", link=r"al|ale|la",
           sub=r"alineatul|alin\.|litera|lit\.|punctul|pct\.|paragraful|teza",
           source="32016R0679 Art. 93(2) 'articolul 5 din Regulamentul (UE) nr. 182/2011', Art. 4(25) "
                  "'articolul 1 alineatul (1) litera (b) din Directiva'; 32018R0825 rec. 21 'articolul 7 "
                  "alineatul (2a) din Regulamentul (UE) 2016/1036'; 32023R1773 rec. 18 'articolul 16 din "
                  "Regulamentul de punere în aplicare (UE) 2023/1070'"),
    _Forms("mt", R=r"regolament(?:i)?", L=r"direttiv(?:a|i)", D=r"deċiżjoni(?:jiet)?|decizjoni(?:jiet)?",
           gap=r"ta['’]\s?implimentazzjoni|delegat[i]?|qafas|kwadru|tal-kunsill|tal-kummissjoni"
               r"|tal-parlament\s+ewropew\s+u\s+tal-kunsill",
           art=r"artikolu|artikoli", conn=r"ta['’]", glued=r"ta[lċdnrstxżz]-", conj=r"u|jew", range=r"sa", link=r"l-|il-",
           sub=r"paragrafu|punt|subparagrafu",
           source="32016R0679 Art. 93(2) 'l-Artikolu 5 tar-Regolament (UE) Nru 182/2011', Art. 4(25) 'tal-Artikolu "
                  "1(1) tad-Direttiva (UE) 2015/1535', rec. 42 'mad-Direttiva tal-Kunsill 93/13/KEE'; 32023R1773 "
                  "rec. 19 'fl-Artikolu 30 tar-Regolament ta’ Implimentazzjoni (UE) 2023/1070'"),
    _Forms("ga", R=r"rialach[áa]i?n", L=r"(?:dt|th|t)reo(?:ir|racha)", D=r"g?ch?inn(?:eadh|idh|t[íi])",
           gap=r"cur\s+chun\s+feidhme|tarmligthe|r[ée]ime",
           art=r"[ht]?airteag(?:al|ail)", conn=r"de|den", glued=r"d['’]", conj=r"agus|nó", range=r"go",
           sub=r"m[íi]r|pointe|fomh[íi]r",
           source="32016R0679 Art. 93(2) 'Airteagal 5 de Rialachán (AE) Uimh. 182/2011', Art. 4(25) "
                  "'d'Airteagal 1(1) de Threoir (AE) 2015/1535'; 32023R1773 rec. 18 'Airteagal 16 de Rialachán "
                  "Cur Chun Feidhme (AE) 2023/1070'; 32022D2319 'Airteagal 3, pointe 8, de Rialachán (AE) "
                  "2018/1725'; 32023D1532 'Cinneadh 2014/512/CBES'"),
    _Forms("hu", R=r"rendelet\w*", L=r"irányelv\w*", D=r"(?:keret)?határozat\w*", type_after=True,
           gap=r"európai\s+parlamenti\s+és\s+tanácsi|tanácsi|bizottsági|végrehajtási|felhatalmazáson\s+alapuló",
           after=r"(?:\d+[a-z]?\.?(?:\s*(?:[–—-]|,|és|vagy)\s*(?:az?\s+)?)?)*\d+[a-z]?\.\s*cikk\w*"
                 r"(?:\s*\(\d+[a-z]?\))?(?:\s*,?\s*(?:és|vagy)\s+(?:az?\s+)?(?:\d+[a-z]?\.?(?:\s*(?:[–—-]|,|és)\s*)?)*"
                 r"\d+[a-z]?\.\s*cikk\w*(?:\s*\(\d+[a-z]?\))?)*",
           num_word=r"cikk\w*", sub=r"bekezdés\w*|pont\w*", conj=r"és|vagy|illetve",
           source="32016R0679 Art. 93(2) 'a 182/2011/EU rendelet 5. cikkét', rec. 172 'a 45/2001/EK rendelet 28. "
                  "cikkének (2) bekezdésével', Art. 4(25) 'az (EU) 2015/1535 európai parlamenti és tanácsi "
                  "irányelv (19) 1. cikke'; 32018R0825 'az (EU) 2016/1036 rendelet 5. cikkének (9) bekezdése'"),
    _Forms("fi", R=r"asetu(?:s|ksen|ksessa|ksella|ksesta|kseen|sta|kset|sten|ksiin|ksia)",
           L=r"direktiiv(?:i(?:n|ssä|llä|stä|in|ä|t|en)?|eihin|ejä|eissä|ien)",
           D=r"päätö(?:s|ksen|ksessä|ksellä|ksestä|kseen|stä|kset|sten)",
           after=r"\d+(?:\s?[a-z](?![^\W\d_]))?(?:\s*(?:[–—-]|,|ja|tai)\s*\d+(?:\s?[a-z](?![^\W\d_]))?)*\s+artikl[ao]\w*"
                 r"(?:\s+\d+(?:\s?[a-z](?![^\W\d_]))?\s+(?:ala)?kohd\w*)*(?:\s*,?\s*(?:ja|tai)\s+\d+(?:\s?[a-z](?![^\W\d_]))?"
                 r"(?:\s*(?:[–—-]|,|ja)\s*\d+)*\s+artikl[ao]\w*(?:\s+\d+(?:\s?[a-z](?![^\W\d_]))?\s+(?:ala)?kohd\w*)*)*",
           num_word=r"artikl[ao]\w*", sub=r"(?:ala)?kohd\w*", conj=r"ja|tai",
           source="32016R0679 Art. 93(2) 'asetuksen (EU) N:o 182/2011 5 artiklaa', rec. 106 'direktiivin 95/46/EY "
                  "25 artiklan 6 kohdan', Art. 4(25) 'direktiivin (EU) 2015/1535 (19) 1 artiklan'; 32018R0825 "
                  "'asetuksen (EU) 2016/1036 7 artiklan 2 a kohdassa'"),
    _Forms("et", R=r"määrus(?:e|t|es|ega|ele|est|ed|i|te|tele|tes|test)?", L=r"direktiiv(?:i|iga|is|ist|ile|id|e)?",
           D=r"otsus(?:e|t|es|ega|ele|est|ed)?",
           after=r"artik(?:kel|li\w*)\s+\d+[a-z]?(?:\s*(?:[–—-]|,|ja|või)\s*\d+[a-z]?)*(?:\s+(?:lõi|punkt)\w*\s+\d+[a-z]?)*"
                 r"(?:\s*,?\s*(?:ja|või)\s+artik(?:kel|li\w*)\s+\d+[a-z]?(?:\s*(?:[–—-]|,|ja)\s*\d+[a-z]?)*"
                 r"(?:\s+(?:lõi|punkt)\w*\s+\d+[a-z]?)*)*",
           sub=r"lõi\w*|punkt\w*", conj=r"ja|või",
           source="32016R0679 Art. 93(2) 'määruse (EL) nr 182/2011 artiklit 5', rec. 106 'direktiivi 95/46/EÜ "
                  "artikli 25 lõike 6'; 32018R0825 rec. 21 'määruse (EL) 2016/1036 artikli 7 lõikes 2a'"),
    _Forms("lv", R=r"regul(?:a|as|u|ā|ai|ām|ās)", L=r"direktīv(?:a|as|u|ā|ai|ām|ās)",
           D=r"(?:pamat)?lēmum(?:s|a|u|ā|am|i|us)",
           after=r"\d+(?:\.?[a-z])?\.(?:\s*(?:[–—-]|,|un|vai)\s*\d+(?:\.?[a-z])?\.)*\s*pant\w*"
                 r"(?:\s+\d+(?:\.?[a-z])?\.\s*(?:apakš)?punkt\w*)*(?:\s*,?\s*(?:un|vai)\s+\d+(?:\.?[a-z])?\."
                 r"(?:\s*(?:[–—-]|,|un)\s*\d+(?:\.?[a-z])?\.)*\s*pant\w*(?:\s+\d+(?:\.?[a-z])?\.\s*(?:apakš)?punkt\w*)*)*",
           num_word=r"pant\w*", sub=r"(?:apakš)?punkt\w*|daļ\w*", conj=r"un|vai",
           source="32016R0679 Art. 93(2) 'Regulas (ES) Nr. 182/2011 5. pantu', rec. 106 'Direktīvas 95/46/EK 25. "
                  "panta 6. punktu'; 32018R0825 rec. 21 'Regulas (ES) 2016/1036 7. panta 2.a punktā'"),
    _Forms("lt", R=r"reglament(?:as|o|ą|u|e|ai|ui|ų|us|uose|ams)", L=r"direktyv(?:a|os|ą|oje|ai|as|oms|ų)",
           D=r"sprendim(?:as|o|ą|u|e|ai|ui|ų|us|ams)",
           after=r"\d+[a-z]?(?:\s*(?:[–—-]|,|ir|bei|arba)\s*\d+[a-z]?)*\s+straipsn\w*(?:\s+\d+\s+(?:dal|punkt)\w*)*"
                 r"(?:\s*,?\s*(?:ir|bei|arba)\s+\d+[a-z]?(?:\s*(?:[–—-]|,|ir|bei)\s*\d+[a-z]?)*\s+straipsn\w*"
                 r"(?:\s+\d+\s+(?:dal|punkt)\w*)*)*",
           num_word=r"straipsn\w*", sub=r"dal\w*|punkt\w*", conj=r"ir|bei|arba",
           source="32016R0679 Art. 93(2) 'Reglamento (ES) Nr. 182/2011 5 straipsnis', rec. 106 'Direktyvos "
                  "95/46/EB 25 straipsnio 6 dalimi'; 32018R0825 'Reglamento (ES) 2016/1036 5 straipsnio 9 dalį'"),
)

#: QUESTION FORMS (operator, 2026-11-21: "add them, marked as question forms"):
#: what a reader TYPES and the Official Journal never writes. None of these
#: stands before an article's number in the five acts' text in its language
#: (counted 2026-11-21: of the abbreviations only cs "čl." and pl "art." do,
#: and they are in `_FORMS`). Kept apart from the evidenced forms, never mixed
#: into them, each tested (tests/ask/test_article_citations_24.py, TYPED).
#: The old seven languages' "art." (en fr de it es pt nl) was read before the
#: evidence rule: here, as what it is.
_QUESTION_FORMS: tuple[_Forms, ...] = (
    _Forms("en", art=r"art\.", source="the OJ writes 'Article 5'"),
    _Forms("en", each=r"each\s+of", source="the OJ writes no 'of each of' before coordinated acts in the five "
                                           "acts; the operator's words (2026-11-21)"),
    _Forms("fr", art=r"art\.", source="the OJ writes 'article 5'"),
    _Forms("de", art=r"art\.", source="the OJ writes 'Artikel 5'"),
    _Forms("it", art=r"art\.", source="the OJ writes 'articolo 5'"),
    _Forms("es", art=r"art\.", source="the OJ writes 'artículo 5'"),
    _Forms("pt", art=r"art\.", source="the OJ writes 'artigo 5.o'"),
    _Forms("nl", art=r"art\.", source="the OJ writes 'artikel 5'"),
    _Forms("da", art=r"art\.", source="the OJ writes 'artikel 5'"),
    _Forms("sv", art=r"art\.", source="the OJ writes 'artikel 5'"),
    _Forms("ro", art=r"art\.", source="the OJ writes 'articolul 5'"),
    _Forms("mt", art=r"art\.", source="the OJ writes 'l-Artikolu 5'"),
    _Forms("sk", art=r"čl\.", source="the OJ writes 'článok 5' (only cs's OJ writes 'čl.')"),
    _Forms("hr", art=r"čl\.", source="the OJ writes 'članak 5.'"),
    _Forms("sl", art=r"čl\.", num_first=r"člen(?:a|u|om)?",
           source="the OJ writes 'člen 5'; Slovenian law numbers first, '5. člen'"),
    _Forms("bg", art=r"чл\.", source="the OJ writes 'член 5'"),
    _Forms("el", art=_tonos(r"άρθρ?\."), source="the OJ writes 'άρθρο 5'"),
    _Forms("lt", after=r"\d+[a-z]?\s+str\.", num_word=r"str\.", source="the OJ writes '5 straipsnis'"),
)
_ALL = _FORMS + _QUESTION_FORMS

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
_ART_WORD = _union("art", _ALL)
_OPENER = _union("opener")
#: an article whose number comes first, before the act (sl "5. člen Uredbe")
_NUM_FIRST = rf"\d++[a-z]?\.\s*(?:{_union('num_first', _ALL)})(?![^\W\d_])"
#: an article before the type word, within the window: its subdivisions read
#: (lazily, so a connector is never taken for a point), the connector where
#: the language has one, the institution or the act's kind before the type
#: word, a compound's first part ("Durchführungs-verordnung"); or, with a
#: connector, any short phrase, a word at a time (no "/": never across another act)
_HEAD = rf"(?:(?:{_ART_WORD})\s*(?:(?:{_OPENER})\s+)?{_ANUM}|{_NUM_FIRST})"
#: what joins a list of articles: a conjunction (and its article, "o el") or a
#: comma before the next article's word; a conjunction or a range's dash
#: between two numbers; a range's opener ("da 3 a 7", "od 3. do 7."). An
#: article's word only after a conjunction or a comma: "Articolo 84
#: All'articolo 2" is a heading and a sentence, never a list
_JOIN = rf"{_union('conj')}|{_union('range')}"
_LIST_TOKEN = (rf"(?:(?:{_JOIN})\s+(?:(?:{_union('link')})\s*)?|,\s*)"
               rf"(?:{_ART_WORD})(?=\s*(?:(?:{_OPENER})\s+)?\d)"
               rf"|(?:{_JOIN})(?![^\W\d_])|(?:{_OPENER})(?=\s+\d)|[–—-]")
#: after the connector: "of each of" (the coordinated acts that follow cite
#: the article too), "the", the institution or the kind, a compound's first part
_TAIL = (rf"(?:(?P<each>{_union('each', _ALL)})\s+)?(?:(?:the|la)\s+)?(?:(?:{_union('pre')})[\s-]+){{0,3}}"
         rf"[^\W\d_]*+$")
_ARTICLE = re.compile(
    rf"(?=[ačчάα\d])"          # every article word's first letter, or a number first
    rf"(?P<art>{_HEAD}(?:[\s,]*(?:{_TOKEN}|{_LIST_TOKEN})){{0,16}}?)\s*,?\s*(?:(?:{_union('conn')})\s+"
    rf"|{_union('glued')})?{_TAIL}",
    re.IGNORECASE)
_ARTICLE_PHRASE = re.compile(
    rf"(?=[ačчάα])(?P<lib>(?:{_ART_WORD})\s*{_ANUM}(?:[,\s]+(?:\d++(?:\.\d++)*[a-z]?|[^\s.;:/]++)){{0,10}}?)"
    rf"\s*,?\s+(?:(?:{_union('conn')})\s+|{_union('glued')}){_TAIL}",
    re.IGNORECASE)
#: where an article may start: its word and the first digit of its number
_ART_START = re.compile(rf"(?=[ačчάα\d])(?:(?:{_ART_WORD})\s*(?:(?:{_OPENER})\s+)?\d|{_NUM_FIRST})", re.IGNORECASE)


def _article_before(before: str, end: int) -> re.Match | None:
    """The article — or the LIST of articles — before the act, `before[:end]`
    ending at its type word. A list read in the closed vocabulary of
    `_ARTICLE` (numbers, paragraphs, conjunctions, an article's word after
    a conjunction) starts at its FIRST article: "Article 25(6) or Article
    26(4) of Directive 95/46/EC" is both (operator, 2026-11-21). Else the
    nearest article whose free phrase and connector reach the act — never
    the first of a phrase: "Articolo 84 All'articolo 2, ultimo comma del
    regolamento (CEE, Euratom, CECA) n. 259/68" (a heading, then the
    sentence; tests/fixtures/eurlex/eurlex-dumpfirst 31972R1473) cites
    article 2. Anchored matches at the article words, never a search: the
    list's, from the nearest article back, stop at the first that fails —
    every tail of a list is a list, so nothing before a failure is one."""
    starts = [m.start() for m in _ART_START.finditer(before, 0, end)]
    first = None
    for start in reversed(starts):
        m = _ARTICLE.match(before, start, end)
        if m is None:
            break
        first = m
    if first is not None:
        return first
    for start in reversed(starts):
        m = _ARTICLE_PHRASE.match(before, start, end)
        if m is not None:
            return m
    return None


#: an article after the act (fi et lv lt hu), past a footnote's call "(19)"
#: and Hungarian's "az"
_ART_AFTER = re.compile(rf"\s*(?:\(\d{{1,3}}\)\s*)?(?:az?\s+)?(?P<art>{_union('after', _ALL)})", re.IGNORECASE)
#: an article text read item by item (`_list_articles`): an article's word
#: before its number (et "artikli" after the act included), a number, an
#: article's word after its numbers, a paragraph's or point's word, a call
#: in brackets
_PARSE = re.compile(
    rf"\([^()]*\)"              # a call in brackets, wherever it stands ("25(6)")
    rf"|(?<!\w)(?:"           # every other item starts a word: the scan skips the inside of words
    rf"(?P<artw>(?:{_ART_WORD}|artik(?:kel|li\w*))(?=\s*(?:(?:{_OPENER})\s+)?\d))"
    rf"|(?P<num>{_ANUM})"
    rf"|(?P<numw>(?:{_union('num_word', _ALL)}|{_union('num_first', _ALL)})(?![^\W\d_]))"
    rf"|(?P<sub>(?:{_union('sub')})(?![^\W\d_])))",
    re.IGNORECASE)
#: the article words whose number takes a dot, an ordinal ("6. cikk", "6. pants", "6. člen")
_DOTTED = re.compile(r"cikk|pant|člen", re.IGNORECASE)


def _list_articles(art: str) -> list[tuple[str, str]]:
    """Every article an article text cites, in order, `(label, number)`:
    "Articles 6 and 8" → 6, 8; "Article 25(6) or Article 26(4)" → 25, 26;
    "artikel 25, stk. 6, eller artikel 26, stk. 4" → 25, 26 (a number after
    a paragraph's word is the paragraph's); "6. és 8. cikkében" → 6, 8 (the
    numbers before an article's word are its articles; "25 artiklan 6
    kohdan": 6 is the paragraph's).

    A RANGE gives its two ends, never the articles between: "Articles 3 to
    7" → 3, 7. What lies between is not written — "between 5 and 8" may hold
    5a, 6a, a 7 repealed or none — and an article the text does not name
    would be invented (a collection holds only what its corpus says)."""
    out: list[tuple[str, str]] = []
    pending: list[str] = []
    level, word = None, ""
    for m in _PARSE.finditer(art):
        kind = m.lastgroup
        if kind == "artw":
            pending, level, word = [], "art", m.group("artw")
        elif kind == "num":
            if level == "art":
                out.append((f"{word} {m.group('num').rstrip('.')}", m.group("num")))
            elif level is None:
                pending.append(m.group("num"))
        elif kind == "numw":
            w = m.group("numw")
            dot = "." if _DOTTED.match(w) else ""
            out += [(f"{n.rstrip('.')}{dot} {w}", n) for n in pending]
            pending, level = [], None
        elif kind == "sub":
            pending = []
            if level == "art":
                level = "sub"
    return out
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
    groups = m.groupdict()
    return _WS.sub(" ", groups.get("art") or groups.get("lib") or "").strip(" ,") or None


#: what joins two acts of a coordination, from the first one's number to the
#: next one's: a footnote's call, a comma or and/or (never a range's word),
#: Hungarian's article, the next one's organisation and "No" — "Regulations
#: (EU) 2019/1148 (8), (EU) 2019/1150 (9), (EU) 2021/784 (10) and (EU)
#: 2021/1232", "Directives 2001/29/EC (21), 2004/48/EC (22) and (EU)
#: 2019/790" (32022R2065), "regolamenti (UE) 2019/1148 (8)(UE) 2019/1150",
#: "Verordnungen (EU) 2019/1148, (8), (EU) 2019/1150"
_COORD = re.compile(
    rf"\s*(?:\(\d{{1,3}}\)\s*)?(?P<sep>,|(?:{_union('conj')})(?![^\W\d_]))?\s*(?:\(\d{{1,3}}\)\s*,?\s*)?"
    rf"(?:(?P<sep2>{_union('conj')})\s+)?(?:az?\s+)?(?:\((?P<org>{_ORGS})\)\s*)?(?:(?P<no>{_NO})\s*)?",
    re.IGNORECASE)


def _coordinated(text: str, start: int, end: int) -> re.Match | None:
    """`text[start:end]`, between two number runs, joins two acts: a
    separator or the next one's organisation, nothing else."""
    if end <= start:
        return None
    m = _COORD.fullmatch(text, start, end)
    return m if m is not None and (m.group("sep") or m.group("sep2") or m.group("org")) else None


def _occurrences(text: str) -> list[tuple[int, str, str | None]]:
    """Every act a text names, `(position, celex, article text or None)`, in
    order of appearance, each time it is named."""
    if not text or not _ANCHOR.search(text):
        return []
    found: list[tuple[int, str, str | None]] = [
        (m.start(), m.group("celex"), None) for m in _CELEX.finditer(text)
        if not m.start() or text[m.start() - 1] not in _ALNUM]
    runs = [m for m in _NUMBERS.finditer(text)
            if not (m.start() and (text[m.start() - 1] == "/" or text[m.start() - 1].isdecimal()))]
    acts: list[tuple[str, tuple[int, str], str | None] | None] = []    # per run: letter, (year, number), art
    hu: list[bool] = []         # per run: its type named after it, Hungarian's way
    prev = None     # the act a coordination continues: (its run's end, its letter, the article "of each",
    #                 the coordination's first act)
    for i, m in enumerate(runs):
        before = text[max(0, m.start() - _WINDOW):m.start()]
        b = _BEFORE_REV.match(before[::-1])
        act, hu_type = None, False
        if b is not None:
            letter = "R" if b.group("R") else "L" if b.group("L") else "D"
            got = _year_number(m.group("a"), m.group("b"), letter, no=bool(b.group("no")),
                               org=bool(b.group("org") or m.group("suffix")), after=False)
            a = _article_before(before, len(before) - b.end())
            art = _art_text(a) or _art_text(_ART_AFTER.match(text, m.end()))
            each = art if a is not None and a.groupdict().get("each") else None
            act = (letter, got, art) if got else None
            prev = (m.end(), letter, each, i) if got else None
        elif (t := _TYPE_AFTER.match(text, m.end())) is not None:      # hu: "a 182/2011/EU rendelet"
            letter = "R" if t.group("R") else "L" if t.group("L") else "D"
            org = _ORG_BEFORE.search(text, max(0, m.start() - 40), m.start()) is not None
            got = _year_number(m.group("a"), m.group("b"), letter, no=False, org=org, after=True)
            act = (letter, got, _art_text(_ART_AFTER.match(text, t.end()))) if got else None
            prev = None
            hu_type = True
        elif prev is not None and (c := _coordinated(text, prev[0], m.start())) is not None:
            # the next act of a coordination: the first one's type. An article
            # with the list — before it, or after it as fi et lv lt write it —
            # is the FIRST act's (operator, 2026-11-21), "of each of" all of theirs
            letter, each, head = prev[1], prev[2], prev[3]
            got = _year_number(m.group("a"), m.group("b"), letter, no=bool(c.group("no")),
                               org=bool(c.group("org") or m.group("suffix")), after=False)
            after = _art_text(_ART_AFTER.match(text, m.end()))
            if after and not each and acts[head] is not None and acts[head][2] is None:
                acts[head] = (*acts[head][:2], after)
            act = (letter, got, each) if got else None
            prev = (m.end(), letter, each, head) if got else None
        else:
            prev = None
        acts.append(act)
        hu.append(act is not None and hu_type)
    # Hungarian names the type once, after the last act: "az (EU) 2017/2394 és
    # az (EU) 2019/1020 rendeletet" (32022R2065) — each act before it, its type
    for i in range(len(runs) - 2, -1, -1):
        nxt = acts[i + 1]
        if acts[i] is None and nxt is not None and hu[i + 1] \
                and _coordinated(text, runs[i].end(), runs[i + 1].start()) is not None:
            m = runs[i]
            org = _ORG_BEFORE.search(text, max(0, m.start() - 40), m.start()) is not None
            got = _year_number(m.group("a"), m.group("b"), nxt[0], no=False, org=org, after=True)
            if got:          # the article after the type word: the coordination's first act's
                acts[i], hu[i] = (nxt[0], got, nxt[2]), True
                acts[i + 1] = (*nxt[:2], None)
    for m, act in zip(runs, acts):
        if act is not None:
            letter, (year, number), art = act
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
    "článek 5 nařízení (EU) 2016/679", "az (EU) 2016/679 rendelet 5. cikke"),
    each of a list ("Articles 6 and 8 of …", a range's two ends): the act's
    `celex:<CELEX>`, a `eurlex` record's own key, and the article in one
    spelling (`article_number`), each (act, article) once, in the query's
    order (app/ask/articles.py pins MAX_ARTICLES of them). The label is the
    query's own words for the article. Needs no reference data."""
    out: list[ArticleCitation] = []
    seen: set[tuple[str, str]] = set()
    for _pos, celex, art in _occurrences(query):
        for label, num in _list_articles(art or ""):
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
