"""EurlexSource — CorpusSource over EUR-Lex's repository, Cellar: the law of
the European Union, every version of every act, in every official language,
on the generic ELI layer (../eli.py). Normattiva's template
(docs/notes/legal-collection-template.md), per language
(docs/notes/legal-fr-de-eu.md §4; legal-collections-nemotron.md).

ONE COLLECTION, EVERY LANGUAGE A SLICE (operator, 2026-11-08: "EU same
entity, different languages"; "make every language downloadable and
embeddable separately"; CLAUDE.md invariant 11). Each of the 24 official
languages is the slice `lang=<code>` — ISO 639-1, as Formex writes it — cut
further by the CELEX type and the year (`lang=en/type=R/year=2016`; app/pack/
slicing.py `eurlex_slice`). The acquisition spec gives every language its
own profile (`slice_profiles`, app/acquire/specs.py EURLEX_SPEC), so a node
fetches only the languages its collection file lists in `slices`, and
`embed_slices` chooses which of those the encoder spends on (07 §7.8,
app/slice_scope.py) — Wikipedia's mechanism, nothing new. A language added
later is an edit of those two lists: nothing held is fetched again, nothing
is re-created, because the native id carries the language and no record of
one language depends on another's.

ONE RECORD PER VERSION OF ONE ACT IN ONE LANGUAGE. The Official Journal's
act (CELEX `3YYYYTNNNN`, `ORIGINAL`) and each consolidated version (`0` +
CELEX + `-YYYYMMDD`, `CONSOLIDATED/<yyyymmdd>`) are records of their own,
in each language Cellar holds them in Formex. A corrigendum (`…R(02)`) is
an OJ document of its own: a record, its own Work, linked `eli:corrects`.
Validity is Normattiva's versions-per-work (`default_profile`): the Work is
the ACT IN ONE LANGUAGE (`extra.validity_work` = `<CELEX of the act>/<lang>`,
a consolidated version's act read from the notice's
ACT_CONSOLIDATED_BASED_ON_RESOURCE_LEGAL, checked against its Formex
FAM.COMP), so the English and the French text of one act are two chains and
never displace each other. A version starts:

  * a consolidation, on the date its CELEX carries — EUR-Lex's definition:
    the day the latest included amendment applies, not the day the text was
    produced (§4.2); Formex's INFO.CONSLEG START.DATE is kept and compared;
  * an original, at its ENTRY INTO FORCE as the CDM notice states it
    (RESOURCE_LEGAL_DATE_ENTRY-INTO-FORCE), never before its publication in
    the OJ, and at the publication where the notice states none
    (`original_start` — Normattiva's rule, one rule for the three
    jurisdictions, §4.2: entry into force, not application).

Consolidated texts have NO LEGAL EFFECT; only the Official Journal is
authentic: every consolidated record says so (`authentic: false`).

THE END OF VALIDITY IS STATED AND KEPT (legal-fr-de-eu.md §5.1). The CDM
states an act's END OF VALIDITY and its IN-FORCE flag; both are kept as the
notice gives them (`end_of_validity`, `in_force`), and the derived end
(app/validity.py) is what the index uses. `closures()` closes an act FROM ITS
OWN WORD ONLY: its notice says it is no longer in force AND states the day
its validity ended — then that day (template §3: under-close, never
over-close). A repeal edge closes nothing: the CDM's repeal link carries no
day, and the repealing act's own start is not the repeal's (Regulation (EU)
2016/679 entered into force in 2016 and repealed Directive 95/46/EC from 25
May 2018; closing at the first would mis-date the second for two years). An
act in force with an end date stated (a sunset) is not closed early either;
the date is kept. What one record shows against itself is an Issue
(`ISSUES`); the comparison of the derived end with the stated one across
records is §5.1's shared check (node-side, not this plugin's).

THE ORIGIN FORM, and why — invariant 11 wants it recorded beside the
plugin, because a verifier needs it to reproduce our records:

  * CELLAR, the Publications Office's repository, by content negotiation on
    `/resource/celex/<CELEX>`: no authentication, every OJ act and every
    consolidated version (legal-fr-de-eu.md §4.1). Not the Data Dump (EU
    Login, acts in force only, no consolidations) and not the SOAP service
    (registration, a daily call limit).
  * FORMEX 4, not XHTML, for the TEXT: the OJ's own production XML carries
    the structure — divisions, articles, numbered paragraphs, points,
    recitals, annexes as files of their own, footnotes as elements — where
    the XHTML carries a layout of it. Consolidated versions have Formex too
    (the probe, §7.1: `CL2016R0679EN0000020.0001.xml`), and theirs adds the
    consolidation's own record: INFO.CONSLEG (start, end, last modification)
    and FAM.COMP (the base act and every amending act), and the amendment
    marks (`CLG.MDFO`: which act changed which text).
  * The CDM BRANCH NOTICE for the METADATA (`Accept: application/xml;
    notice=branch`, the language by Accept-Language): identity (CELEX, ELI),
    dates, force status, end of validity, subjects, authors, every link to
    another work (amends, repeals, cites, based on, corrects) — what Formex
    does not carry. It also names the manifestation's items: their names
    (STREAM_NAME), sizes and URIs, which is how the fetch names each file.
  * THE BODY BY NAME: the item the Formex wrapper (`….doc.xml`) names as
    DOC.MAIN.PUB, never the first or the second the origin lists (§7.1: the
    order differs between an act and its consolidation).
  * THE NON-INFERRED TREE beside the notice (parser 3, 2026-11-14:
    `Accept: application/rdf+xml;notice=non-inferred-tree`, rdf.py). München
    compared it triple by triple with the notice over 1,698 Italian acts:
    26,607 assertions are in the tree only (the OJ information number, the
    legal basis as printed, the corrigendum flag, the embargo, an alternative
    ELI, EEA relevance…). Kept scoped to the unit's language (another
    language's expression is in its own slice), canonical (Cellar's order is
    not stable), as `tree.json`, the unit's completion mark.
  * AN ACT WITHOUT FORMEX in a language is read from the next form Cellar
    holds (cellar.FORMS: xhtml, html, then the PDFs; rendition.py), never
    left out. München counted the Italian works in force (Data Dump of
    2026-11-08): Formex 30,249 of 43,623; 12,360 more with XHTML/HTML, 786
    HTML only, 228 PDF only (5 scans, held as PDFs, said `no-text-layer`).
    Before parser 3 the listing asked for fmx4 alone and these were missing.
    The form read is in the record (`extra.form`).
  * NOT the Data Dump as a transport, though its Formex is Cellar's byte for
    byte (30 acts, 82 files): no checksum is published, so a dump file could
    only be adopted on its size; it holds acts in force only; it needs a
    browser's EU Login weekly per language (eurlex-forms, 2026-11-14).

THE UNIT AND ITS ORIGIN. Per act version and language, a directory
`lang=<code>/<yyyy>/<CELEX>/` under the corpus root — `<yyyy>` the CELEX's
year, to keep directories small — holding the manifestation's files under
their own names and `notice.xml`, the branch notice, written whole (the
fetch stages the unit and swaps it in: app/acquire/cellar.py). The locator
is that directory; `origin_hash` is BLAKE2b-128 over every file of it in
name order, each framed by its name and length (`frame`), so a changed,
added or removed item changes the hash. `records-vs-origin` re-fetches one
unit by its Cellar URI (`cellar.CellarOrigin`).

THE ID (eli-legal-plugins.md §5.2; template §2): `eli.expression_native_id`
over the start day, (CELEX, language) and the version key — the language
inside the hash, so the slices never collide — collision-checked
(`CollisionGuard`). The day: a consolidation's CELEX date; an original's OJ
publication date as its wrapper states it (PUBLICATION.REF), so the id
sorts by arrival (02 §2.2) and discovery reads one small file per original
and no notice. THE BUSIEST START DAY, which the id's 45 bits of hash are
checked against, is OWED from the node (the SPARQL counts, §4.6); at
45 bits a day of 10⁶ versions expects 1.4·10⁻² collisions, and the guard
refuses one loudly.

EVERY FIELD IS KEPT (invariant 11), under keys fixed for the collection's
life (`EXTRA_KEYS`): the ELI set where it applies (eli.EXTRA_KEYS), the
Formex manifestation's every assertion beside the text (the wrapper whole,
the bibliographic instance, the consolidation's record and family, the
preamble and final provisions, each article's identifier, each annex's
file and instance, the footnotes, the cross-references, the amendment
marks), and the notice WHOLE. What leaves: wrapping (XML markup, the quote
marks' elements — their characters stay), the printed page breaks (`PAGE`
PIs) and an editor's banner comment (invariant 11 (a), (b)).

THIRD-PARTY MATERIAL (legal-fr-de-eu.md §4.7). EUR-Lex's reuse notice
(Decision 2011/833/EU; C(2019) 1655: CC BY 4.0 for content, CC0 for
metadata) excludes third-party content reproduced in acts — the
International Accounting Standards in their regulations, for one. No
pruning rule is written, so nothing is left out (invariant 11: nothing is
pruned unless written); which acts these are is OWED from the node, and
then the operator chooses: a yellow slice under their own terms, or a
written pruning rule in the guidelines and the Codex. The class is green
until then, and the terms text is owed too: europa.eu is not reachable from
the session that built this plugin and no copy of the legal notice's bytes
from the Office itself was found, so no TERMS.txt is shipped (a copy from
anyone else is not the origin's bytes).
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from app import chunking_legal
from .. import eli
from app.backend import OriginForm, Template
from ..base import (CanonicalDoc, DiscoverState, DocRef, Edge, Erasure, Issue, LicenseClass,
                    Modality, Mutability, PROTOCOL_VERSION, PrivacyClass, RawDoc,
                    UnindexableDocument)
from . import formex, notice as notice_mod, rdf as rdf_mod, rendition

NOTICE = "notice.xml"
LANGUAGES = notice_mod.LANGUAGES

#: `0YYYYTNNNN-YYYYMMDD` — a consolidated version's CELEX: sector 0, the
#: act's CELEX without its sector, the consolidation's date
_CONSOLIDATED = re.compile(r"^0(?P<base>\d{4}[A-Z]{1,2}[0-9A-Z()]+)-(?P<date>\d{8})$")
#: sector, year, the type letter(s) — what every CELEX opens with
_CELEX_HEAD = re.compile(r"^(?P<sector>[0-9A-Z])(?P<year>\d{4})(?P<type>[A-Z]{1,2})")
_LANG_DIR = re.compile(r"^lang=(?P<lang>[a-z]{2})$")
#: the wrapper's PUBLICATION.REF date, read from its head at discovery
_PUB_DATE = re.compile(rb"<PUBLICATION\.REF\b[^>]*>.*?<DATE\s+ISO=\"(\d{8})\"", re.S)
_HEAD = 16 << 10

#: The EU's ELI (eli-legal-plugins.md §5.3; legal-fr-de-eu.md §4.3): the
#: Publications Office's register, by type, year and natural number; the
#: OJ act is `/oj`, a consolidation its date, a language `/oj/<lang3>`.
PROFILE = eli.Profile(
    jurisdiction="EU", authority="eu", base="http://data.europa.eu/",
    uri_template="http://data.europa.eu/eli/{typedoc}/{year}/{natural_number}/oj",
    work_key=("celex",))
ELI_JURISDICTION = "eu"

#: the CDM's link properties that are typed edges (09 §9.14), after ELI's
#: own: amends, repeals, cites, based_on, corrects, consolidates
#: (data.europa.eu/eli/ontology). The CDM names for repeal and correction
#: are [S] (legal-fr-de-eu.md §4.4), to confirm on a live notice (OWED);
#: any other link is kept in `cdm_notice` and makes no edge.
RELATIONS = {
    "RESOURCE_LEGAL_AMENDS_RESOURCE_LEGAL": "eli:amends",
    "RESOURCE_LEGAL_REPEALS_RESOURCE_LEGAL": "eli:repeals",
    "RESOURCE_LEGAL_IMPLICITLY_REPEALS_RESOURCE_LEGAL": "eli:repeals",
    "WORK_CITES_WORK": "eli:cites",
    "RESOURCE_LEGAL_BASED_ON_RESOURCE_LEGAL": "eli:based_on",
    "RESOURCE_LEGAL_CORRECTS_RESOURCE_LEGAL": "eli:corrects",
    "ACT_CONSOLIDATED_BASED_ON_RESOURCE_LEGAL": "eli:consolidates",
}
#: what the CDM writes for "no end": an open validity, not a date
OPEN_END = "9999-12-31"

#: 09 §9.5's `extra` for this collection, FIXED for its life (invariant 11):
#: a value the origin does not give is left out, never invented
EXTRA_KEYS = tuple(k for k in eli.EXTRA_KEYS if k != "eli_uri_schema") + (
    eli.IN_FORCE,
    # identity and the version this record is
    "celex", "work_celex", "language", "language_code", "version_key", "valid_from",
    "validity_work", "authentic", "sector", "resource_type",
    # the dates and the force status, as the notice states them
    "date_document", "entry_into_force", "end_of_validity", "last_modified",
    # the Formex manifestation
    "items", "body_file", "wrapper_file", "formex_wrapper", "formex", "consleg",
    "articles", "annexes", "notes", "references", "modifications", "instructions",
    # every attribute of the body and the annexes, in document order (formex.attributes_of)
    "markup",
    # the form read, and the non-inferred tree scoped to the language (parser 3)
    "form", "cdm_rdf",
    # the CDM notice
    "notice_kind", "notice_decoding", "cdm_relations", "cdm_notice",
)

ISSUES = {
    "consolidation-start-disagrees": "a consolidated version's Formex INFO.CONSLEG START.DATE is not the "
                                     "date its CELEX carries; the CELEX's date starts its validity",
    "base-act-disagrees": "the act a consolidation is based on differs between the notice and the "
                          "Formex FAM.COMP; the notice's is the Work",
    "no-base-act": "a consolidated version names no act it is based on, in the notice or the "
                   "Formex; it is a Work of its own",
    "entry-into-force-before-publication": "the notice's entry into force precedes the OJ publication; "
                                           "validity starts at the publication",
    "end-before-start": "the notice's end of validity precedes the version's start",
    "not-in-force-without-end": "the notice says the act is not in force and states no end of "
                                "validity: it stays open-ended, labelled (under-closed)",
    "form-disagrees-with-celex": "the Formex body is a consolidation (CONS.ACT) and the CELEX an "
                                 "original, or the reverse; the CELEX decides the version",
}


def lang3(lang: str) -> str:
    """`en` → `ENG`: the code the notice and Accept-Language use."""
    try:
        return LANGUAGES[lang]
    except KeyError:
        raise ValueError(f"{lang!r} is not one of the 24 official languages") from None


def version_key(celex: str) -> str:
    """The origin's identifier of the version, from the CELEX: `ORIGINAL`,
    or `CONSOLIDATED/<yyyymmdd>` for a consolidated version (ELI's
    vocabulary, eli.version_of; rule 2: what Cellar names the document)."""
    m = _CONSOLIDATED.match(celex)
    return f"CONSOLIDATED/{m['date']}" if m else "ORIGINAL"


def celex_type(celex: str) -> str:
    """The type letter(s) after sector and year: `R`, `L`, `D`, …; for a
    consolidation, its act's."""
    m = _CONSOLIDATED.match(celex)
    head = _CELEX_HEAD.match("3" + m["base"] if m else celex)
    return head["type"] if head else "unknown"


def unit_rel(lang: str, celex: str) -> str:
    """`lang=en/2016/32016R1036` — the unit's directory under the corpus root."""
    return f"lang={lang}/{celex[1:5]}/{celex}"


def frame(files: dict[str, bytes]) -> bytes:
    """Every file of the unit in name order, each `<name>\\0<length>\\0<bytes>`:
    the bytes `origin_hash` covers. A name or a boundary cannot move without
    changing them."""
    out = bytearray()
    for name in sorted(files):
        data = files[name]
        out += name.encode("utf-8") + b"\0" + str(len(data)).encode() + b"\0" + data
    return bytes(out)


def unframe(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    i = 0
    while i < len(data):
        j = data.index(b"\0", i)
        k = data.index(b"\0", j + 1)
        name, n = data[i:j].decode("utf-8"), int(data[j + 1:k])
        files[name] = data[k + 1:k + 1 + n]
        i = k + 1 + n
    return files


def unit_files(unit: Path) -> list[os.DirEntry]:
    """The unit's files: everything in its directory but a transfer's
    leftovers (`.part`) and hidden entries."""
    with os.scandir(unit) as it:
        return sorted((e for e in it if e.is_file() and not e.name.startswith(".")
                       and not e.name.endswith(".part")), key=lambda e: e.name)


def original_start(entry_into_force: list[str], published: str | None) -> str | None:
    """An original's start of validity: the earliest entry into force the
    notice states on or after its OJ publication; the publication where it
    states none (nothing enters into force before it is published)."""
    later = sorted(d for d in entry_into_force if not published or d >= published)
    if later:
        return later[0]
    return published or (sorted(entry_into_force)[0] if entry_into_force else None)


def eli_data_uri(uri: str | None) -> str | None:
    """Cellar's alias `http://publications.europa.eu/resource/eli/reg/2018/196/oj`
    → the register's `http://data.europa.eu/eli/reg/2018/196/oj`; that form
    passes through."""
    if not uri:
        return None
    _, sep, path = uri.partition("/eli/")
    return PROFILE.absolute("eli/" + path.strip("/")) if sep else None


def eli_path(uri: str | None) -> str | None:
    """`http://data.europa.eu/eli/reg/2016/679/oj` → `reg/2016/679/oj`."""
    data = eli_data_uri(uri)
    return data.split("/eli/", 1)[1] if data else None


def eli_work_of(path: str | None) -> str | None:
    """A version's ELI path → its act's (`reg/2009/1223/2019-12-18` →
    `reg/2009/1223/oj`); the OJ form passes through."""
    if not path:
        return None
    parts = path.split("/")
    # exactly <type>/<year>/<number>/<oj | a date>: a longer path (a
    # corrigendum's) is a document of its own, not a version of the act
    if len(parts) == 4 and parts[1].isdigit():
        return "/".join(parts[:3] + ["oj"])
    return None


def _form_held(note, files: dict[str, bytes]) -> str | None:
    """The best form (cellar.FORMS) whose every listed item is in the unit."""
    from ...acquire.cellar import FORMS
    for f in FORMS:
        items = note.items.get(f) or []
        if items and all(it.name in files for it in items):
            return f
    return None


def _unit_day(unit: Path, celex: str, entries: list[os.DirEntry]) -> str | None:
    """The day the unit's id sorts under: a consolidation's CELEX date, an
    original's OJ publication date from its wrapper's head."""
    m = _CONSOLIDATED.match(celex)
    if m:
        return formex.iso(m["date"])
    for e in entries:
        if formex.is_wrapper(e.name):
            with open(e.path, "rb") as f:
                got = _PUB_DATE.search(f.read(_HEAD))
            return formex.iso(got.group(1).decode()) if got else None
    # an act without Formex (rendition.py): the notice's work, read only as
    # far as its date (notice.work_day)
    for e in entries:
        if e.name == NOTICE:
            with open(e.path, "rb") as f:
                return notice_mod.work_day(f.read())
    return None


class EurlexSource:
    source_id = "eurlex"
    ISSUES = ISSUES
    ELI_PROFILE = PROFILE
    #: 09 §9.3's `profile`: the ELI implementation and the validity axis of
    #: one record per version per language (app/validity.py)
    default_profile = {
        "eli": {"jurisdiction": PROFILE.jurisdiction, "authority": PROFILE.authority,
                "uri_template": PROFILE.uri_template, "work_key": list(PROFILE.work_key)},
        "validity": {"kind": "versions-per-work", "from": "extra.valid_from",
                     "work": "extra.validity_work"},
    }
    # YELLOW (operator, 2026-11-08, A): CC BY 4.0's attribution is a condition
    # (04 §4.4), as normattiva's; TERMS.txt quotes the legal notice verbatim
    license_class = LicenseClass.YELLOW    # CC BY 4.0 content, CC0 metadata (Decision 2011/833/EU)
    corpus_provider = "Publications Office of the European Union — EUR-Lex / Cellar"
    corpus_license = "CC-BY-4.0"
    corpus_attribution = ("© European Union, https://eur-lex.europa.eu, CC BY 4.0 (metadata CC0). "
                          "Only the Official Journal is authentic; consolidated texts have no legal effect")
    privacy_class = PrivacyClass.PUBLIC
    #: the shared legal space's chunker (legal-collections-nemotron.md: every
    #: legal plugin chunks with `legal_article`, legal-chunking-retrieval.md): one chunk per section — each
    #: recital, each article, each annex (formex.sections)
    chunking_profile = "legal_article"
    #: the shared legal layer, its tokenizer and window (app/chunking_legal.py)
    default_layers = [chunking_legal.SHARED_LAYER]
    #: the notice of an act changes in place (its force status, its end of
    #: validity, new links), and Cellar re-publishes a manifestation it corrects
    mutability = Mutability.MUTABLE
    #: app/backend.py: a record is a unit directory, read where it lies
    backend = (OriginForm("the Cellar units (Formex items and the branch notice)", Template.MEMBER),)
    protocol_version = PROTOCOL_VERSION
    #: 1 on 2026-11-08. 2 on 2026-11-14: every attribute of the body and the
    #: annexes kept (`markup`; invariant 11 past the words, formex.attributes_of).
    #: eurlex was created on 1 and nothing of it is built or published, so it
    #: is re-made on 2 (RULE 1), its fetched corpus kept
    #: 3 on 2026-11-14 (München's measures): Cellar's non-inferred tree kept
    #: (`cdm_rdf`, rdf.py: 26,607 assertions over 1,698 acts the notice lacks),
    #: and an act without Formex read from its XHTML, HTML or PDF
    #: (rendition.py; 13,374 of 43,623 Italian works in force had been left
    #: out). eurlex is re-made on 3, its fetched units completed, not refetched
    PARSER_VERSION = 3
    #: 06 §6.2: Cellar keeps every OJ act and every consolidated version at
    #: its own CELEX: a superseded version stays checkable, and is kept
    origin_versions = "history"
    modality = Modality.TEXT
    erasure = Erasure.RETRACTABLE          # a document Cellar withdraws
    native_id_order = "numeric"            # eli.expression_native_id: the start day first
    #: 05 §5.7 (legal-chunking-retrieval.md §4 R2, operator's default A,
    #: 2026-11-12): FILTERS a reader asks for, never a weight (invariant 4),
    #: on the record's own fields. Status: `in_force`, the notice's
    #: RESOURCE_LEGAL_IN-FORCE (`true`, `false`; absent where it says
    #: nothing); "not yet in force" is a date, answered by `--as-of`. Act
    #: type: `resource_type`, the notice's RESOURCE_LEGAL_TYPE (R, L, D, PC
    #: …), on every record where an ELI type is not. Collection: `sector`,
    #: the CELEX sector (3 legislation, 5 preparatory acts, 0 consolidated
    #: texts …). Year: `date`. Subject matter: `subjects`, the notice's
    #: IS_ABOUT labels — EuroVoc's concepts, domains, microthesauri and top
    #: terms with the directory codes and the subject matters, together, so
    #: no one authority is named.
    default_facets = [
        {"field": "date", "kind": "range", "separator": None, "authority": None},
        {"field": "extra.in_force", "kind": "flat", "separator": None, "authority": None},
        {"field": "extra.resource_type", "kind": "flat", "separator": None, "authority": None},
        {"field": "extra.sector", "kind": "flat", "separator": None, "authority": None},
        {"field": "subjects", "kind": "flat", "separator": None, "authority": None},
    ]

    # ---- discovery ---------------------------------------------------------------------------

    def discover(self, state: DiscoverState):
        """Every unit under `lang=<code>/<yyyy>/<CELEX>/` holding its notice —
        a unit without one is a fetch that did not finish and is not yet a
        document. Refs come out in native-id order, which is the start day's."""
        root = Path(state.corpus_root)
        if not root.is_dir():
            return
        guard = eli.CollisionGuard()
        held: dict[int, tuple[str, int, int]] = {}
        with os.scandir(root) as langs:
            lang_dirs = sorted((e for e in langs if e.is_dir()), key=lambda e: e.name)
        for ld in lang_dirs:
            m = _LANG_DIR.match(ld.name)
            if m is None or m["lang"] not in LANGUAGES:
                continue
            lang = m["lang"]
            with os.scandir(ld.path) as years:
                year_dirs = sorted((e for e in years if e.is_dir()), key=lambda e: e.name)
            for yd in year_dirs:
                with os.scandir(yd.path) as units:
                    unit_dirs = sorted((e for e in units if e.is_dir() and not e.name.startswith(".")),
                                       key=lambda e: e.name)
                for ud in unit_dirs:
                    unit, celex = Path(ud.path), ud.name
                    try:
                        entries = unit_files(unit)
                    except OSError as e:
                        yield DocRef(source_id=self.source_id, native_id=celex, locator=str(unit),
                                     scope="container", error=f"unreadable unit: {e}"[:200])
                        continue
                    names = {x.name for x in entries}
                    if NOTICE not in names or rdf_mod.TREE_FILE not in names:
                        # not landed yet, or landed before parser 3 kept its
                        # tree: the next fetch completes it (app/acquire/cellar.py)
                        continue
                    try:
                        day = _unit_day(unit, celex, entries)
                    except OSError as e:
                        day, err = None, str(e)
                    else:
                        err = "no consolidation date in its CELEX and no OJ date in its wrapper"
                    if day is None:
                        yield DocRef(source_id=self.source_id, native_id=celex, locator=str(unit),
                                     scope="container", error=f"{unit_rel(lang, celex)}: {err}"[:200])
                        continue
                    expr = version_key(celex)
                    try:
                        nid = eli.expression_native_id(day, (celex, lang), expr)
                    except ValueError as e:
                        yield DocRef(source_id=self.source_id, native_id=celex, locator=str(unit),
                                     scope="container", error=f"{unit_rel(lang, celex)}: {e}"[:200])
                        continue
                    other = guard.check(nid, (celex, lang, expr))
                    if other is not None:
                        yield DocRef(source_id=self.source_id, native_id=str(nid), locator=str(unit),
                                     scope="container",
                                     error=f"native id {nid} collides: {other} and {(celex, lang, expr)}")
                        continue
                    stats = [x.stat() for x in entries]
                    held[nid] = (str(unit), max(s.st_mtime_ns for s in stats) // 1_000_000_000,
                                 sum(s.st_size for s in stats))
        for nid in sorted(held):
            locator, mtime, size = held[nid]
            yield DocRef(source_id=self.source_id, native_id=str(nid), locator=locator,
                         mtime=mtime, size=size)

    # ---- fetch and parse ---------------------------------------------------------------------

    def fetch(self, ref: DocRef) -> RawDoc:
        unit = Path(ref.locator)
        try:
            files = {e.name: Path(e.path).read_bytes() for e in unit_files(unit)}
        except OSError as e:
            raise UnindexableDocument(f"{unit}: {type(e).__name__}: {e}") from e
        for need in (NOTICE, rdf_mod.TREE_FILE):
            if need not in files:
                raise UnindexableDocument(f"{unit}: no {need}: the unit did not land whole")
        data = frame(files)
        return RawDoc(ref=ref, raw_hash=hashlib.blake2b(data, digest_size=16).hexdigest(), data=data)

    def parse(self, raw: RawDoc) -> CanonicalDoc:
        unit = Path(raw.ref.locator)
        celex, lang = unit.name, unit.parent.parent.name.partition("=")[2]
        if lang not in LANGUAGES:
            raise UnindexableDocument(f"{unit}: not under lang=<one of the 24 languages>")
        files = unframe(raw.data or b"")
        try:
            note = notice_mod.parse(files.pop(NOTICE))
        except (KeyError, notice_mod.NoticeError) as e:
            raise UnindexableDocument(f"{unit}: {e}") from None
        try:
            tree = rdf_mod.loads(files.pop(rdf_mod.TREE_FILE))
        except (KeyError, ValueError) as e:
            raise UnindexableDocument(f"{unit}: the scoped tree did not read: {e}") from None
        found: list[Issue] = []
        form = "fmx4" if any(formex.is_wrapper(n) for n in files) else _form_held(note, files)
        root_tag = "CONS.ACT" if _CONSOLIDATED.match(celex) else "ACT"
        stated_lang = notice_mod.CODE_OF.get(note.display_language or "", lang)
        try:
            if form == "fmx4":
                act = formex.parse(files)
            elif form in ("xhtml", "html"):
                # in their names' order: an OJ item's name carries its page
                # (`…01001401` page 14, `…01001501` page 15), the notice's order
                # does not (32010D0699 lists the annexed draft first)
                names = sorted(it.name for it in note.items.get(form, []) if it.name in files)
                act = rendition.parse_html(files, names, lang=stated_lang, root=root_tag, note=note)
            elif form is not None:
                names = sorted(it.name for it in note.items.get(form, []) if it.name in files)
                act, blank = rendition.parse_pdf(files, names, lang=stated_lang, root=root_tag, note=note)
                if blank:
                    found.append(Issue("no-text-layer", f"{blank} page(s) of {', '.join(names)} hold no text "
                                                        f"(a scan): the PDF is held whole in the unit"))
            else:
                raise UnindexableDocument(f"{unit}: no Formex and no item of any form the notice lists "
                                          f"({', '.join(sorted(note.items))})")
        except formex.FormexError as e:
            raise UnindexableDocument(f"{unit}: {e}") from None
        # RULE 2: the record is what the origin returned. A unit whose
        # notice or text is another act, or another language, is refused —
        # never filed under the slice it was asked for
        if note.celex != celex:
            raise UnindexableDocument(f"{unit}: the notice is {note.celex}'s, the unit {celex}'s")
        if act.language != lang:
            raise UnindexableDocument(f"{unit}: the Formex text is in {act.language!r}, the slice lang={lang}")
        if note.display_language and note.display_language != lang3(lang):
            raise UnindexableDocument(f"{unit}: the notice's expression is {note.display_language}, "
                                      f"the slice lang={lang}")
        expr = version_key(celex)
        consolidated = expr != "ORIGINAL"
        if consolidated != (act.root == "CONS.ACT"):
            found.append(Issue("form-disagrees-with-celex", f"{act.body_file} is a {act.root}, the CELEX "
                                                           f"{celex} a{' consolidation' if consolidated else 'n original'}"))
        if consolidated:
            m = _CONSOLIDATED.match(celex)
            valid_from = formex.iso(m["date"])
            start = formex.iso(act.consleg.get("START.DATE"))
            if start and start != valid_from:
                found.append(Issue("consolidation-start-disagrees",
                                   f"INFO.CONSLEG START.DATE {start}, the CELEX {valid_from}"))
            work = note.base_celex or act.base_celex
            if note.base_celex and act.base_celex and note.base_celex != act.base_celex:
                found.append(Issue("base-act-disagrees",
                                   f"notice {note.base_celex}, Formex FAM.COMP {act.base_celex}"))
            if not work:
                found.append(Issue("no-base-act", celex))
                work = celex
        else:
            work = celex
            published = act.publication_date
            valid_from = original_start(note.entry_into_force, published)
            early = [d for d in note.entry_into_force if published and d < published]
            if early:
                found.append(Issue("entry-into-force-before-publication",
                                   f"entry into force {early[0]}, published {published}"))
        if not valid_from:
            raise UnindexableDocument(f"{unit}: no start of validity: no OJ date and no entry into force")
        ends = [d for d in note.end_of_validity if d != OPEN_END]
        if any(d < valid_from for d in ends):
            found.append(Issue("end-before-start", f"end of validity {min(ends)}, start {valid_from}"))
        if note.in_force is False and not ends:
            found.append(Issue("not-in-force-without-end", celex))

        # the act's ELI: the notice's own for an original; a consolidation's
        # is its act's by the register's rule (`reg/2009/1223/2019-12-18` is
        # a version of `reg/2009/1223/oj`), the date being the version's
        own_eli = eli_path(note.eli)
        work_eli = eli_work_of(own_eli)
        work_uri = PROFILE.absolute("eli/" + work_eli) if work_eli else None
        typedoc = own_eli.split("/", 1)[0] if own_eli else None
        cites = [c for t, c in note.relations if t == "WORK_CITES_WORK"]
        extra = {
            # ELI rule 5: the fixed keys (eli.EXTRA_KEYS)
            "eli_jurisdiction": PROFILE.jurisdiction,
            "eli_work": work_uri,
            "eli_expression": eli_data_uri(note.eli),
            "eli_type_document": typedoc,
            "eli_number": note.number,
            "eli_date_document": act.date,
            "eli_date_publication": act.publication_date,
            "eli_version": expr.split("/", 1)[0],
            "eli_version_date": valid_from if consolidated else None,
            "eli_id_local": celex,
            "eli_title": act.title,
            "eli_cites": cites,
            # 09 §9.5's force status, on the origin's word only
            eli.IN_FORCE: note.in_force,
            # the version this record is
            "celex": celex,
            "work_celex": work,
            "language": lang,
            "language_code": lang3(lang),
            "version_key": expr,
            "valid_from": valid_from,
            "validity_work": f"{work}/{lang}",
            "authentic": False if consolidated else None,
            "sector": note.sector or celex[:1],
            "resource_type": note.resource_type,
            "date_document": note.date_document,
            "entry_into_force": note.entry_into_force,
            "end_of_validity": note.end_of_validity,
            "last_modified": note.last_modified,
            # the Formex manifestation, every assertion beside the text
            "items": [{"name": n, "bytes": len(b)} for n, b in sorted(files.items())],
            "body_file": act.body_file,
            "wrapper_file": act.wrapper_file,
            "formex_wrapper": act.wrapper,
            "formex": act.meta,
            "consleg": act.consleg,
            "articles": [{"identifier": a.identifier, "num": a.num, "title": a.title,
                          "partition": a.partition} for a in act.articles],
            "annexes": [{"file": a.file, "title": a.title, **({"bib": a.bib} if a.bib else {})}
                        for a in act.annexes],
            "notes": act.notes,
            "references": act.references,
            "modifications": act.modifications,
            "instructions": act.instructions,
            "markup": act.markup,
            # the form the text was read from (app/acquire/cellar.py FORMS)
            "form": form,
            # Cellar's non-inferred tree, scoped to this language (rdf.py)
            "cdm_rdf": tree,
            # the CDM notice, whole
            "notice_kind": note.kind,
            "notice_decoding": note.decoding,
            "cdm_relations": [[t, c] for t, c in note.relations],
            "cdm_notice": note.whole,
        }
        keys = {f"celex:{celex}", f"celex:{work}"}
        if work_eli:
            keys.add(PROFILE.key(work_eli))
        if own_eli and own_eli != work_eli:
            keys.add(PROFILE.key(own_eli))
        return CanonicalDoc(
            uid=f"{self.source_id}:{raw.ref.native_id}",
            title=act.title or note.expression_title or celex,
            creators=list(note.creators),
            language=lang,
            date=act.date or note.date_document,
            subjects=list(note.subjects),
            # 05 §5.2: only ids a reference source registers — `celex`
            # (app/refs/eurlex.py) and `eli` (eli-refs, routing `eu` there);
            # sorted by bytes (09 §9.5)
            keys=sorted(keys),
            extra={k: v for k, v in extra.items() if v not in (None, "", [], {})},
            sections=formex.sections(act),
            raw_hash=raw.raw_hash,
            issues=found,
        )

    # ---- relations and closures --------------------------------------------------------------

    def relations(self, doc: CanonicalDoc):
        """06 §6.1's typed edges, named after ELI (09 §9.14): what THIS
        version's notice states about other works, by their CELEX — `to` a
        canonical id left unresolved (05 §5.5), `celex:<CELEX>`. One edge
        per (type, work)."""
        seen: set[tuple[str, str]] = set()
        for tag, celex in (doc.extra or {}).get("cdm_relations") or []:
            kind = RELATIONS.get(tag)
            if kind is None or (kind, celex) in seen:
                continue
            seen.add((kind, celex))
            yield Edge(to=f"celex:{celex}", type=kind)

    def closures(self, doc: CanonicalDoc):
        """app/validity.py's hook: an ORIGINAL act whose own notice says it
        is no longer in force and states the day its validity ended closes
        its Work from that day (the docstring: its own word, never a repeal
        edge's, never a sunset still in force). A consolidated version
        closes nothing: its act's notice speaks for the act."""
        x = doc.extra or {}
        if x.get("version_key") != "ORIGINAL" or x.get(eli.IN_FORCE) is not False:
            return
        start = str(x.get("valid_from") or "")
        ends = sorted(d for d in x.get("end_of_validity") or [] if d != OPEN_END and d >= start)
        if ends:
            yield f"celex:{x['celex']}", ends[0]


def slice_name(lang: str, celex: str, native_id: int) -> str:
    """`lang=en/type=R/year=2016`: the language, the CELEX type, the year of
    the id's day — a pure function of the record's path and id, so
    `slice_hint` answers from the manifest row and agrees by construction."""
    return f"lang={lang}/type={celex_type(celex)}/year={eli.id_date(int(native_id)).year:04d}"
