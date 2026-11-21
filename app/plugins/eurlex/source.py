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
    (RESOURCE_LEGAL_DATE_ENTRY-INTO-FORCE) — before its publication in the
    OJ where the notice says so (D1 of the EUR-Lex5 review, 2026-11-21,
    choice B: a decision takes effect on notification to its addressee,
    Art. 297(2) TFEU; 129,887 acts), never before the act's own date (no
    act enters into force before it is adopted) — and at the publication
    where the notice states none (`original_start`; §4.2: entry into force,
    not application).

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
    consolidated version (legal-fr-de-eu.md §4.1). The Data Dump is not the
    ORIGIN (EU Login, acts in force only, no consolidations; it is a
    transport for items, below) and neither is the SOAP service
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
  * A FORMEX PACKAGE IS FORMEX (2026-11-19): Cellar serves some
    manifestations as ONE item, a zip of the wrapper, the body and the
    figures (the OJ's act-by-act publication, 32024R1689
    `OJABA_L_202401689_ENG.fmx4.zip`). The zip is held as served — the
    bytes the origin gave, which records-vs-origin compares — and read
    through (`formex.unpack`); the record's `items` name each member and
    the package it came in. Before, `cellar.form_of` saw no wrapper among
    the item names and read those acts from XHTML.
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
  * PARSER 4 (2026-11-15, operator, B: "Build every in-force act, in all 24
    languages, from the Data Dump alone"): the record's facts are read from
    the TREE (from_tree.py), and a unit is the tree and the items alone — no
    notice. Measured on München's units: every field the record took from
    the notice is the same from the tree; what the notice adds is other
    works' assertions (inverse links, embedded notices), the vocabularies'
    labels and hierarchy, and the items' stream metadata — none of it this
    act's (invariant 11). Subjects and authors are concept ids
    (`eurovoc:1073`); their labels are the vocabularies' (a reference source
    from EuroVoc's and the authority tables' dumps, OWED). The DATA DUMP
    builds the units of every act it holds (app/acquire/eurlex_dump.py: the
    MTD's tree scoped to the language, the items from the language's zips),
    byte for byte the unit Cellar gives for the same form, which
    `records-vs-origin` re-fetches from Cellar (`cellar.fetch_unit_from_tree`:
    the tree, then the items it names). CELLAR stays the origin. What the
    dump lacks is the HISTORY slice (`history/lang=<l>/…`): consolidated
    versions, and acts not in force when first held; optional to hold and to
    embed. Cellar is asked per unit only once the dump of every language held
    is built in (`cellar.CellarInventory.held_back`), only for what it lacks
    (a unit held and current costs no request, `cellar.TreeUnits`), and a
    newer dump is built first (`eurlex_dump.build_newer`).
    The notice-based reading below is parser 3's, kept for its Codex.
  * The DATA DUMP is a TRANSPORT for items, never the origin (amended
    2026-11-14, operator: "every time there is a bulk download that saves a
    relevant amount of single requests to the source, we start from the bulk
    download first"; it replaces "not the Data Dump as a transport"). An
    item of an act in force is read from the dump when the notice lists it
    by that name and size under the same WORK and its manifestation was not
    modified since the dump was made (app/acquire/datadump.py); the notice,
    the tree and everything else come from Cellar, which `records-vs-origin`
    re-fetches. The date is the guarantee (operator, 2026-11-14: "Assume
    that cellar always updates the date when a file is changed"): no
    checksum is published, and the ETag is a modification time.

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
from . import formex, from_tree, notice as notice_mod, rdf as rdf_mod, rendition

NOTICE = "notice.xml"
LANGUAGES = notice_mod.LANGUAGES
#: the corpus root's folder of the HISTORY slice (parser 4): `history/lang=<l>/<yyyy>/<CELEX>/`
HISTORY = "history"

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
    "celex", "work_celex", "language", "language_code", "text_language", "version_key", "valid_from",
    "valid_from_provisional", "body_root",
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
    # parser 4: the unit is held in the history slice (`history/lang=<l>/…`)
    "history",
)

ISSUES = {
    "consolidation-start-disagrees": "a consolidated version's Formex INFO.CONSLEG START.DATE is not the "
                                     "date its CELEX carries; the CELEX's date starts its validity",
    "base-act-disagrees": "the act a consolidation is based on differs between the notice and the "
                          "Formex FAM.COMP; the notice's is the Work",
    "no-base-act": "a consolidated version names no act it is based on, in the notice or the "
                   "Formex; it is a Work of its own",
    "entry-into-force-before-publication": "the notice's entry into force precedes the OJ publication "
                                           "(a decision takes effect on notification, Art. 297(2) "
                                           "TFEU); validity starts at the entry into force",
    "entry-into-force-before-adoption": "the notice states an entry into force before the act's own "
                                        "date; that date starts nothing (no act is in force before "
                                        "it is adopted)",
    "end-before-start": "the notice's end of validity precedes the version's start",
    "not-in-force-without-end": "the notice says the act is not in force and states no end of "
                                "validity: it stays open-ended, labelled (under-closed)",
    "form-disagrees-with-celex": "the Formex body is a consolidation (CONS.ACT) and the CELEX an "
                                 "original, or the reverse; the CELEX decides the version",
    "no-text-layer": "page(s) of the act's PDF hold no text (a scan): the PDF is held whole in the "
                     "unit, its text is what the other pages give",
    "start-from-document-date": "no publication and no entry into force is stated: the act's own date "
                                "is its provisional start, and says so (valid_from_provisional)",
    "no-start-of-validity": "no publication, entry into force or date of the act is stated: the record "
                            "is held and takes no part in the validity filter",
    "item-not-held": "the Formex wrapper names a file the expression's manifestation does not list: "
                     "the record holds the rest, and names it",
    "formex-not-this-act": "the expression's Formex holds no wrapper of this act (none naming its "
                           "CELEX, or only other acts'): the text is read from the next form the "
                           "tree lists; the Formex items are held beside it",
    "text-in-another-language": "the origin files a text in another language under this language's "
                                "expression: held in this language's slice, the record's language "
                                "the text's (operator, 2026-11-16, B)",
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


def original_start(entry_into_force: list[str], published: str | None,
                   adopted: str | None = None) -> str | None:
    """An original's start of validity: the earliest entry into force the
    notice states on or after the act's own date `adopted` — whether before
    its OJ publication or after (D1, 2026-11-21, choice B: a decision takes
    effect on notification, Art. 297(2) TFEU) — and the publication where it
    states none such. An entry into force before the act's own date starts
    nothing: no act is in force before it is adopted."""
    stated = sorted(d for d in entry_into_force if not adopted or d >= adopted)
    if stated:
        return stated[0]
    return published or (sorted(entry_into_force)[0] if entry_into_force and not adopted else None)


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


def _form_held(note, files: dict[str, bytes], skip: tuple[str, ...] = ()) -> str | None:
    """The best form (cellar.FORMS) whose every listed item is in the unit."""
    from ...acquire.cellar import FORMS
    for f in FORMS:
        if f in skip:
            continue
        items = note.items.get(f) or []
        if items and all(it.name in files for it in items):
            return f
    return None


def wrapper_head(celex: str, entries: list[os.DirEntry]) -> bytes:
    """The head (`_HEAD` bytes) of the act's own Formex wrapper among the
    unit's files — inside its package where the manifestation is one zip
    (formex.unpack). Raises FormexError where there is none of this act's."""
    paths = {e.name: e.path for e in entries}
    if any(formex.is_package(n) for n in paths):
        held = {n: Path(p).read_bytes() for n, p in paths.items() if formex.is_package(n) or formex.is_wrapper(n)}
        view = formex.unpack(held)[0]
        return view[formex.own_wrapper(view, celex, view.__getitem__)][:_HEAD]
    own = formex.own_wrapper(paths, celex, lambda n: Path(paths[n]).read_bytes())
    with open(paths[own], "rb") as f:
        return f.read(_HEAD)


def _wrapper_by_content(celex: str, entries: list[os.DirEntry]) -> bytes | None:
    """The head of the XML file whose content is this act's Formex wrapper
    (root DOC: the only one, or the one naming the act as its NO.CELEX),
    whatever its name; None where none is."""
    heads = []
    for e in entries:
        if not e.name.lower().endswith(".xml"):
            continue
        with open(e.path, "rb") as f:
            head = f.read(_HEAD)
        if formex.root_tag(head[:formex._HEAD_BYTES]) == "DOC":
            heads.append(head)
    if len(heads) == 1:
        return heads[0]
    own = [h for h in heads if f"<NO.CELEX>{celex}</NO.CELEX>".encode() in h]
    return own[0] if len(own) == 1 else None


def _unit_day(unit: Path, celex: str, entries: list[os.DirEntry], tree_facts: bool = False) -> str | None:
    """The day the unit's id sorts under: a consolidation's CELEX date, an
    original's OJ publication date from its wrapper's head. An act without
    Formex: its work's own day, from the notice (parser 3) or from the tree
    (parser 4: the same date, `from_tree.work_day`)."""
    m = _CONSOLIDATED.match(celex)
    if m:
        return formex.iso(m["date"])
    try:
        head = wrapper_head(celex, entries)
    except formex.FormexError:
        head = None                        # no wrapper, or none this act's: its work's own day below
    if head is not None:
        got = _PUB_DATE.search(head)
        if got:
            return formex.iso(got.group(1).decode())
        # A FILE UNDER THE WRAPPER'S NAME THAT IS NO WRAPPER (2026-11-21:
        # 32013D0410's dump files, 24 languages: ANNEX VII as `….doc.xml`, the
        # wrapper as the body's `.xml`): the day is read from the file that IS
        # this act's wrapper by its content — the OJ day Cellar's own unit
        # states, so the act's id stays when the unit is fetched again — and
        # the act is LISTED, its parse refusing it with the reason, never left
        # out of every scan with a warning; else the work's own day below
        got = _PUB_DATE.search(_wrapper_by_content(celex, entries) or b"")
        if got:
            return formex.iso(got.group(1).decode())
    if tree_facts:
        for e in entries:
            if e.name == rdf_mod.TREE_FILE:
                with open(e.path, "rb") as f:
                    return from_tree.work_day(rdf_mod.loads(f.read()), celex)
        return None
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
    #: the parse pool's large-document lane (app/parallel.py): what a parse
    #: of one unit holds per byte of its `size` (the unit's files, summed at
    #: discovery). MEASURED 2026-11-19 (München, its own container): 32021R2017
    #: el, 204,218,240 bytes, peak 5,248,708,608 to chunk (25.7x), 2,055,856,128
    #: for its metadata (10.1x) — the larger, rounded up
    PARSE_PEAK_PER_BYTE = 26
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
    #: 4 on 2026-11-15 (operator, B: "Build every in-force act, in all 24
    #: languages, from the Data Dump alone"): the record's facts are read from
    #: the unit's TREE (from_tree.py), never from a notice, so a unit is the
    #: tree and the items — what the dump holds — and a notice a unit still
    #: holds is kept whole and read for nothing; subjects and authors are
    #: concept ids; the history slice (`history/lang=<l>/…`, `extra.history`).
    #: Parser 3 is kept for the collection whose Codex names it (EurlexSourceV3).
    #: 5 on 2026-11-18 (EUR-Lex4's parse: 11,486 documents refused, and the
    #: LEMUR dry run's French): a body that is not an act is read — an
    #: agreement (AGR) as an act, a corrigendum (CORR) by its corrections, a
    #: GENERAL document by its groups — every main file of a Special Edition
    #: read, not the first; a file the wrapper names and the manifestation does
    #: not hold said, not refused; a start of validity the origin does not
    #: state provisional and said (the act's own date), or absent; "Article
    #: premier" and a single article's heading are articles (rendition.py);
    #: `extra.body_root`, `extra.valid_from_provisional`. Parser 4 is not
    #: carried: EUR-Lex4, the one Codex naming it, published nothing and is
    #: re-created as EUR-Lex5 (RULE 1, 02 §2.4 A1c).
    PARSER_VERSION = 5
    #: the facts from the tree (4) or from the notice (3)
    TREE_FACTS = True
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

    def at_parser_version(self, version: int) -> "EurlexSource | None":
        """This plugin parsing at `version`, for a collection whose Codex
        names it (`collections.plugin_at`): 4 is this one, 3 the one that read
        the facts from the notice (`EurlexSourceV3`)."""
        return {5: self, 3: _V3}.get(int(version))

    # ---- discovery ---------------------------------------------------------------------------

    def _lang_dirs(self, root: Path) -> list[os.DirEntry]:
        """`lang=<code>` folders under the corpus root and, parser 4, under
        its `history/` (the history slice)."""
        out: list[os.DirEntry] = []
        for base in (root, root / HISTORY) if self.TREE_FACTS else (root,):
            if not base.is_dir():
                continue
            with os.scandir(base) as langs:
                out += sorted((e for e in langs if e.is_dir()), key=lambda e: e.name)
        return out

    def discover(self, state: DiscoverState):
        """Every unit under `lang=<code>/<yyyy>/<CELEX>/` (and, parser 4,
        `history/lang=<code>/…`) holding its tree — and, parser 3, its notice:
        a unit without them is a fetch that did not finish and is not yet a
        document. Refs come out in native-id order, which is the start day's."""
        root = Path(state.corpus_root)
        if not root.is_dir():
            return
        guard = eli.CollisionGuard()
        held: dict[int, tuple[str, int, int]] = {}
        for ld in self._lang_dirs(root):
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
                    if (NOTICE not in names and not self.TREE_FACTS) or rdf_mod.TREE_FILE not in names:
                        # not landed yet, or landed before parser 3 kept its
                        # tree: the next fetch completes it (app/acquire/cellar.py)
                        continue
                    try:
                        day = _unit_day(unit, celex, entries, self.TREE_FACTS)
                    except (OSError, ValueError) as e:
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

    def superseded_ids(self, ref: DocRef) -> list[str]:
        """The ids this unit could have had under another of its forms
        (discovery retires a row held under one at this locator: one unit,
        one document — 2026-11-21, the 37,695 EUR-Lex5 units held twice). A
        unit is dated by its wrapper's OJ day where it holds one, by its
        tree's work day otherwise: a unit that gained or lost its Formex
        (a re-form into a package, a rendition replaced) moved between the
        two. Both are computed; the other one is the answer."""
        unit = Path(ref.locator)
        celex, lang = unit.name, unit.parent.parent.name.partition("=")[2]
        if lang not in LANGUAGES or _CONSOLIDATED.match(celex):
            return []
        try:
            entries = unit_files(unit)
        except OSError:
            return []
        days: set[str] = set()
        try:
            head = wrapper_head(celex, entries)
            got = _PUB_DATE.search(head)
            if got:
                days.add(formex.iso(got.group(1).decode()))
        except (formex.FormexError, OSError):
            pass
        for e in entries:
            if e.name == rdf_mod.TREE_FILE:
                try:
                    with open(e.path, "rb") as f:
                        d = from_tree.work_day(rdf_mod.loads(f.read()), celex)
                except (OSError, ValueError):
                    d = None
                if d:
                    days.add(d)
        out = []
        for d in sorted(x for x in days if x):
            try:
                nid = str(eli.expression_native_id(d, (celex, lang), version_key(celex)))
            except ValueError:
                continue
            if nid != str(ref.native_id):
                out.append(nid)
        return out

    def ref_of_unit(self, unit: Path) -> DocRef | None:
        """The DocRef discovery gives one landed unit — its id from its day,
        CELEX, language and version, as `discover` computes it — or None
        where it is not whole (a unit fetched on request, app/ask/history.py)."""
        unit = Path(unit)
        celex, lang = unit.name, unit.parent.parent.name.partition("=")[2]
        if lang not in LANGUAGES:
            return None
        try:
            entries = unit_files(unit)
        except OSError:
            return None
        names = {x.name for x in entries}
        if rdf_mod.TREE_FILE not in names or (NOTICE not in names and not self.TREE_FACTS):
            return None
        try:
            day = _unit_day(unit, celex, entries, self.TREE_FACTS)
            nid = eli.expression_native_id(day, (celex, lang), version_key(celex)) if day else None
        except (OSError, ValueError):
            return None
        if nid is None:
            return None
        stats = [x.stat() for x in entries]
        return DocRef(source_id=self.source_id, native_id=str(nid), locator=str(unit),
                      mtime=max(s.st_mtime_ns for s in stats) // 1_000_000_000,
                      size=sum(s.st_size for s in stats))

    # ---- fetch and parse ---------------------------------------------------------------------

    def fetch(self, ref: DocRef) -> RawDoc:
        unit = Path(ref.locator)
        try:
            files = {e.name: Path(e.path).read_bytes() for e in unit_files(unit)}
        except OSError as e:
            raise UnindexableDocument(f"{unit}: {type(e).__name__}: {e}") from e
        for need in (rdf_mod.TREE_FILE,) if self.TREE_FACTS else (NOTICE, rdf_mod.TREE_FILE):
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
        history = self.TREE_FACTS and unit.parent.parent.parent.name == HISTORY
        held = None                     # the notice the unit holds, kept whole
        if NOTICE in files or not self.TREE_FACTS:
            try:
                held = notice_mod.parse(files.pop(NOTICE))
            except (KeyError, notice_mod.NoticeError) as e:
                raise UnindexableDocument(f"{unit}: {e}") from None
        try:
            tree = rdf_mod.loads(files.pop(rdf_mod.TREE_FILE))
        except (KeyError, ValueError) as e:
            raise UnindexableDocument(f"{unit}: the scoped tree did not read: {e}") from None
        if self.TREE_FACTS:
            try:
                note = from_tree.notice(tree, lang3(lang), celex)
            except from_tree.TreeError as e:
                # RULE 2: a tree of another act is refused, never filed here
                raise UnindexableDocument(f"{unit}: {e}") from None
        else:
            note = held
        found: list[Issue] = []
        # a Formex manifestation served as one zip is read through it, the zip
        # held as served (formex.unpack; 32024R1689, 32015D1735)
        try:
            view, packed = formex.unpack(files)
        except formex.FormexError as e:
            raise UnindexableDocument(f"{unit}: {e}") from None
        form = "fmx4" if any(formex.is_wrapper(n) for n in view) else _form_held(note, files)
        if form == "fmx4":
            try:
                formex.own_wrapper(view, celex, view.__getitem__)
            except formex.FormexError as e:
                # no wrapper of THIS act (München, 2026-11-16: 32011R0540 hr,
                # 32004D0382 bg/ro …): its text from the next form the tree
                # lists and the unit holds (cellar.next_form); the reason said
                alt = _form_held(note, files, skip=("fmx4",))
                if alt is not None:
                    found.append(Issue("formex-not-this-act", f"{e}: the text is read from {alt}"))
                    form = alt
        root_tag = "CONS.ACT" if _CONSOLIDATED.match(celex) else "ACT"
        stated_lang = notice_mod.CODE_OF.get(note.display_language or "", lang)
        try:
            if form == "fmx4":
                act = formex.parse(view, celex)
                for name in act.missing:
                    found.append(Issue("item-not-held", f"{act.wrapper_file} names {name}"))
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
        # notice or tree is another act's, or another language's expression,
        # is refused — never filed under the slice it was asked for
        if note.celex != celex:
            raise UnindexableDocument(f"{unit}: the notice is {note.celex}'s, the unit {celex}'s")
        if note.display_language and note.display_language != lang3(lang):
            raise UnindexableDocument(f"{unit}: the notice's expression is {note.display_language}, "
                                      f"the slice lang={lang}")
        # A TEXT IN ANOTHER LANGUAGE UNDER THIS ONE'S EXPRESSION (operator,
        # 2026-11-16, B; München: L_2015192IT.01001901, Council Implementing
        # Decision (EU) 2015/1182 in English as the Italian expression — it 5,
        # hr 4): held, as the origin files it, in this language's slice, with
        # the TEXT's language as the record's and the issue on it — never
        # refused (invariant 11: nothing the origin gives is left out) and
        # never passed off as this language's text. Only where the origin's
        # own expression is this one (the tree's), and the text's language
        # is one of the 24.
        text_lang = act.language
        if text_lang != lang:
            if not note.display_language or text_lang not in LANGUAGES:
                raise UnindexableDocument(f"{unit}: the Formex text is in {text_lang!r}, the slice lang={lang}")
            found.append(Issue("text-in-another-language",
                               f"the origin's {lang3(lang)} expression holds a text in {text_lang} "
                               f"({act.body_file} states LG.DOC {text_lang.upper()})"))
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
            # the OJ date the Formex wrapper states, else the work's own
            # publication date in its tree (parser 5: a Special Edition
            # wrapper states PUBLICATION.REF.SE, not PUBLICATION.REF)
            published = act.publication_date or note.date_publication
            valid_from = original_start(note.entry_into_force, published, note.date_document)
            adopted = note.date_document
            before = [d for d in note.entry_into_force if adopted and d < adopted]
            early = [d for d in note.entry_into_force if published and d < published and d not in before]
            if before:
                found.append(Issue("entry-into-force-before-adoption",
                                   f"entry into force {before[0]}, the act of {adopted}"))
            if early:
                found.append(Issue("entry-into-force-before-publication",
                                   f"entry into force {early[0]}, published {published}"))
        # NO START STATED (parser 5; 3,415 units of 1958–62 read from HTML or a
        # PDF, refused by parser 4): held, never refused (invariant 11). As
        # NeuRIS parser 5 (operator, 2026-11-15: "we use the publishing date
        # as draft"), a start the origin does not state is PROVISIONAL and
        # says so: the act's own date where neither a publication nor an
        # entry into force is stated (`valid_from_provisional`, on every
        # result through validity.start_basis); with no date at all, the
        # record takes no part in the validity filter, and says why.
        provisional = None
        if not valid_from and note.date_document:
            valid_from = note.date_document
            provisional = "the act's own date: no publication or entry into force is stated"
            found.append(Issue("start-from-document-date", f"{celex}: {valid_from}"))
        if not valid_from:
            found.append(Issue("no-start-of-validity", f"{celex}: no publication, entry into force or "
                                                       f"date of the act stated"))
        ends = [d for d in note.end_of_validity if d != OPEN_END]
        if valid_from and any(d < valid_from for d in ends):
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
            # the text's own language where the origin filed another under this one
            "text_language": text_lang if text_lang != lang else None,
            "version_key": expr,
            "valid_from": valid_from,
            "valid_from_provisional": provisional,
            # the body's root as the Formex states it (parser 5: ACT,
            # CONS.ACT, AGR, CORR, GENERAL …): what kind of document it is
            "body_root": act.root if form == "fmx4" else None,
            "validity_work": f"{work}/{lang}",
            "authentic": False if consolidated else None,
            "sector": note.sector or celex[:1],
            "resource_type": note.resource_type,
            "date_document": note.date_document,
            "entry_into_force": note.entry_into_force,
            "end_of_validity": note.end_of_validity,
            "last_modified": note.last_modified,
            # the Formex manifestation, every assertion beside the text
            "items": [{"name": n, "bytes": len(b)} for n, b in sorted(files.items())]
                     + [{"name": n, "bytes": len(view[n]), "in": packed[n]} for n in sorted(packed)],
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
            # the CDM notice, whole, where the unit holds one (parser 4: read for nothing)
            "notice_kind": held.kind if held is not None else None,
            "notice_decoding": held.decoding if held is not None else None,
            "cdm_relations": [[t, c] for t, c in note.relations],
            "cdm_notice": held.whole if held is not None else None,
            # parser 4: the history slice (slice_name)
            "history": True if history else None,
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
            language=text_lang,
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


def slice_name(lang: str, celex: str, native_id: int, history: bool = False) -> str:
    """`lang=en/type=R/year=2016`: the language, the CELEX type, the year of
    the id's day — a pure function of the record's path and id, so
    `slice_hint` answers from the manifest row and agrees by construction.
    A unit of the history slice (parser 4, `history/lang=<l>/…`) is under
    `history/`: `history/lang=en/type=R/year=2016`, so `lang=en` alone holds
    none of it and `history` holds every language's (slice_scope.is_held)."""
    name = f"lang={lang}/type={celex_type(celex)}/year={eli.id_date(int(native_id)).year:04d}"
    return f"{HISTORY}/{name}" if history else name


class EurlexSourceV3(EurlexSource):
    """Parser 3, kept for the collection whose Codex names it (invariant 3):
    the facts read from the notice, which every unit must hold; no history
    slice."""
    PARSER_VERSION = 3
    TREE_FACTS = False


_V3 = EurlexSourceV3()
