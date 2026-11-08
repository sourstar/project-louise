"""LegiSource — CorpusSource over DILA's LEGI open data (the consolidated
French law: codes, laws, ordinances, decrees, a selection of arrêtés, every
version of every article), on the generic ELI layer (../eli.py).
docs/notes/legal-fr-de-eu.md §2 and §7.1; legal-collections-nemotron.md
(the operator's decisions of 2026-11-08); legal-collection-template.md.

ONE RECORD PER ORIGIN FILE, of four kinds (decision 1). The origin is a
tree of XML files, one per object, and each kind says something no other
does, so a record per article alone would hold less than the origin gives
(invariant 11):

  * `article` — a LEGIARTI file, ONE VERSION OF ONE ARTICLE, with its
    validity (`version_from`, `work_key`: the platform's versions-per-work
    axis, app/validity.py) and its text as one section; its context (the
    text and headings above it), its sibling versions, its NOTA and its
    LIENS kept in `extra`;
  * `texte_version` — a TEXTE_VERSION file: a text's titles, état, dates,
    autorité, ministère in `extra`, and its VISAS, SIGNATAIRES, TP, NOTA,
    ABRO and RECT as sections;
  * `section_ta` — a SECTION_TA file: its TITRE_TA and COMMENTAIRE as
    sections, its structure (the articles and sub-sections it lists, each
    with its dates and état) and context in `extra`;
  * `textelr` — a TEXTELR file: a text's structure and its version chain,
    in `extra`; it has no text, so no chunk.

ONLY AN ARTICLE IS A VERSION. The profile's validity axis reads
`extra.version_from` and `extra.work_key`, which only an article (and
never a MODIFIE_MORT_NE one) carries; app/validity.py records nothing for
a record without them (`of_doc` → None), so a text, section or structure
record is never a version of any Work, never ends one and never hides
one. Its index columns are the default every record the version table
does not know reads as (`validity.current_columns`): current, from the day
its id sorts under. The catalogue leg admits only records of the version
table (validity.admit), so such a record reaches a search by its text and
vector legs, never by its title alone.

THE ORIGIN FORM, and why — invariant 11 wants it beside the plugin:

  * DILA's open-data bulk (`echanges.dila.gouv.fr/OPENDATA/LEGI/`), not
    the Légifrance (PISTE) API: one baseline tarball (the probe of
    2026-10-28: `Freemium_legi_global_20250713-140000.tar.gz`,
    1,171,027,316 B, Range served) and one increment per working day
    (`LEGI_YYYYMMDD-HHMMSS.tar.gz`, 1–8 MB), with no authentication,
    where the API needs OAuth2 credentials, terms per environment and
    quotas only visible inside the portal — one GET a day against
    thousands of authenticated calls.
  * The XML files as DILA writes them, not the HTML pages: one file per
    object, UTF-8, under a path built from its id; the four roots above.
    The text (CONTENU) is XHTML transitional plus DILA's tags; its markup
    is dropped (invariant 11 (a)), every element of the metadata kept.
  * THE STATE IS THE LATEST BASELINE, THEN EVERY INCREMENT DATED AFTER IT
    IN TIMESTAMP ORDER, each increment's files then its deletion lists
    (`liste_suppression_legi.dat`, `liste_suppression_legi_dossier.dat`)
    — a file is rewritten when the origin closes a version (its ETAT and
    DATE_FIN change), so no increment is ever skipped. An increment dated
    BEFORE the latest baseline is not applied because it is INSIDE that
    baseline (the baseline is the state the origin had built up to its
    own date): that is not a skipped increment.
  * ONE CONTAINER PACK PER TARBALL (app/container_pack.py, placed by
    app/derived.py; ./pack.py): a gzip stream cannot be read at a member,
    so each tarball is read once into Parquet. `origin` is
    `<tarball>#<member>`, `origin_hash` BLAKE2b-128 over the member's
    bytes exactly as the tarball holds them.
  * JORF — the originals as published, the only French files that carry
    an ELI (`ID_ELI`, `ELI_ALIAS`) — is SCOPE NOT YET TAKEN, not pruning:
    LEGI's consolidated versions carry no ELI, so this collection writes
    no `eli:fr:` key; the originals are another origin form, to add as
    its own decision (legal-fr-de-eu.md §2.1).
  * Known gaps of the origin, stated: JORF's open data drops images, so a
    formula published as an image is missing; nominative acts are left
    out; arrêtés are a selection (§2.1). An <img> the CONTENU holds is
    kept as its attributes in `extra.images`.

THE ID. `native_id = eli.expression_native_id(sort_day, (), <file id>)`
(decision 2): the file's own id (LEGIARTI…, LEGISCTA…) is unique at the
origin, so it is the whole key, and the day sorts it. A text's LEGITEXT id
names TWO files, its `texte/version/` and its `texte/struct/` (the fixtures
LEGITEXT000006070666 show both), so a text's key is the id qualified by
that folder's name — `LEGITEXT…/version`, `LEGITEXT…/struct`
(`record_key`); with the bare id the two files collided as one record
seen twice (found on the real fixtures, 2026-11-08). `sort_day` is
the file's DATE_DEBUT when it is a date inside the id's day range and not
a placeholder (2999-01-01 "open", 2222-02-22 "not known yet"); else the
text's DATE_TEXTE, else its DATE_PUBLI, each on the same terms; else the
FALLBACK DAY, 1500-01-01 — the layout's day 0, the one no origin date
uses: a record is never dropped for its dates (Issue `sort-day-fallback`).
A code's TEXTELR, TEXTE_VERSION and SECTION_TA state 2999-01-01 for both
of its text dates, so they sort there; their index columns then admit them
at every date, which is right for a record that states no validity of its
own. Every id is collision-checked (eli.CollisionGuard), never trusted.
The Work key — the smallest LEGIARTI of an article's chain (its own id and
every id its VERSIONS list) — is `extra.work_key`, the validity axis's
Work; a key that MOVED between two sightings of a file, or on which the
chain's own files disagree, is an Issue (`work-key-moved`,
`work-key-disagrees`; ./pack.py's facts). How often it moves on the real
archive is OWED from the node (legal-fr-de-eu.md §2.2), as is the busiest
start day (the id width, template §2) and what the ~241,000 empty ETATs are.

A RE-KEY IS A WITHDRAWAL AND A NEW RECORD. A placeholder date the origin
later fixes moves the file's sort day, so its id moves: the old id is
withdrawn (a DocRef with `deleted`, app/discovery.py: out of every index,
a tombstone, never a record that still answers) and the new one
discovered. The orchestrator never removes a document a scan does not
report, so every disappearance is reported: a file a deletion list names,
a file a new baseline no longer holds, an id that moved — by the sightings
of one scan, and across scans by the id map discovery keeps beside the
manifest (`legi_ids.sqlite`, state_dir; written once the scan's refs are
committed, like a cursor).

ONE RECORD PER ID. One id under two folders (legi.py keeps a table of
them): the newest sighting is the record — the later tarball, then the
later member mtime, then the path's bytes — and the other is an Issue
(`id-in-two-folders`) naming it.

VALIDITY, as Normattiva's (template §3): the start is DATE_DEBUT
(`version_from`), the end DERIVED by the node — the next version's start,
or, for a version whose ETAT ends it (ABROGE, ABROGE_DIFF, ANNULE, PERIME,
DISJOINT, TRANSFERE) and that has no later version, its own DATE_FIN,
stated by `closures()` against its own key: the origin's word, the
version's last one. The origin's DATE_FIN and ETAT are kept and compared
with the end derived from the version's own chain; a disagreement is an
Issue (`date-fin-disagrees`), never a silent choice. Quirks named:
2999-01-01 is "open"; a DATE_DEBUT of 2222-02-22 ("not known yet") is
kept as the start, so the version sorts after every dated one and no date
before 2222 admits it (Issue `placeholder-start`); a MODIFIE_MORT_NE
version (it never entered into force) is HELD as a record with its text
in `extra.mort_ne_text`, with no validity fields and no sections: it is in
no Work's chain, has no chunk, and is never returned by a search (Issue
`modifie-mort-ne`), and its siblings' derived ends ignore it. `in_force`
(09 §9.5): True on VIGUEUR, False on ABROGE, ANNULE, PERIME, DISJOINT,
TRANSFERE and MODIFIE_MORT_NE, absent otherwise (MODIFIE, VIGUEUR_DIFF,
ABROGE_DIFF: the article is in force or not by another version).

ORIGIN VERSIONS. `history`: keeping is the side that loses nothing. The
probe saw the baseline beside every increment since it; whether DILA
removes a period's increments at its next baseline (PubMed's
`history-until-baseline`) is owed — observed at the next baseline.

TERMS.txt is the Licence Ouverte 2.0's French text as Etalab publishes it,
byte for byte: `LO.md` of github.com/etalab/licence-ouverte at 48b983e6
(the file last changed at 05cd5ed0, 2022-03-19), sha256 a1deb9d7…5995.
Class green: no acknowledgement is needed (invariant 9); DILA's
personal-data warning (`OPENDATA/AVERTISSEMENT-Donnees_a_caractere_personnel.pdf`)
is owed, read from the node before the class is pinned.

Locator: `<tarball>::<member>`.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import tarfile
import zlib
from datetime import date
from pathlib import Path

from app.backend import OriginForm, Template

from app import chunking_legal
from .. import eli
from ..base import (CanonicalDoc, DiscoverState, DocRef, Edge, Erasure, Issue, LicenseClass,
                    Modality, Mutability, PROTOCOL_VERSION, PrivacyClass, RawDoc,
                    UnindexableDocument)
from . import pack
from . import xml as lx

_LOCATOR_SEP = "::"

BASELINE = re.compile(r"^Freemium_legi_global_(\d{8}-\d{6})\.tar\.gz$")
INCREMENT = re.compile(r"^LEGI_(\d{8}-\d{6})\.tar\.gz$")

#: the origin's placeholders: "open" and "not known yet" (legal-fr-de-eu.md §2.2)
OPEN_DAY = "2999-01-01"
UNKNOWN_DAY = "2222-02-22"
PLACEHOLDERS = frozenset((OPEN_DAY, UNKNOWN_DAY))
#: the id's day when the file states none it can sort under: day 0 of the layout
FALLBACK_DAY = eli.EPOCH.isoformat()

#: an ETAT that ends its article with this version, stated by the version itself
CLOSING = frozenset(("ABROGE", "ABROGE_DIFF", "ANNULE", "PERIME", "DISJOINT", "TRANSFERE"))
NOT_IN_FORCE = frozenset(("ABROGE", "ANNULE", "PERIME", "DISJOINT", "TRANSFERE", "MODIFIE_MORT_NE"))
MORT_NE = "MODIFIE_MORT_NE"
KINDS = ("article", "texte_version", "section_ta", "textelr")

PROFILE_JURISDICTION = "FR"

ISSUES = {
    "sort-day-fallback": "the file states no day its id can sort under (DATE_DEBUT, the text's "
                         "DATE_TEXTE and DATE_PUBLI all missing, placeholders or out of range): "
                         "it sorts under 1500-01-01",
    "placeholder-start": "an article version's DATE_DEBUT is the origin's 2222-02-22 (not known "
                         "yet): kept as its start, so no date before 2222 admits it",
    "start-unreadable": "an article version's DATE_DEBUT is not a date: it is held as a version "
                        "from its sort day",
    "modifie-mort-ne": "a MODIFIE_MORT_NE version (it never entered into force): held, its text in "
                       "extra, in no Work's chain and never returned by a search",
    "date-fin-disagrees": "the origin's DATE_FIN is not the end derived from the version's own "
                          "chain (the next version's start, or its own end for a closing ETAT)",
    "etat-empty": "an article version states no ETAT",
    "own-id-not-in-versions": "an article's VERSIONS does not list the article itself",
    "id-not-file-name": "the file's ID is not its file name's",
    "html-entity": "the member uses HTML entities XML does not define; they were read as their "
                   "characters",
    "id-in-two-folders": "the same id sits under two folders of the origin: the newest sighting "
                         "is the record, the other is named here",
    "work-key-moved": "the smallest LEGIARTI of the article's chain changed between two sightings "
                      "of the file: the validity Work moved",
    "work-key-disagrees": "files of one article chain name different smallest LEGIARTI ids",
}


def split_locator(locator: str) -> tuple[Path, str]:
    path, _, member = str(locator).rpartition(_LOCATOR_SEP)
    return Path(path), member


def _usable(day: str | None) -> bool:
    """An ISO day the id's layout can hold, and not one of the placeholders."""
    if not day or day in PLACEHOLDERS:
        return False
    try:
        eli._day_index(day)
    except ValueError:
        return False
    return True


def sort_day(fields: dict) -> tuple[str, str]:
    """`(day, which field it is)` — decision 2's rule (the module docstring)."""
    for name in ("date_debut", "date_texte", "date_publi"):
        if _usable(fields.get(name)):
            return fields[name], name
    return FALLBACK_DAY, "fallback"


def record_key(kind: str, file_id: str) -> str:
    """What names one origin FILE: its own id — except a text's, whose
    LEGITEXT id names two files, its version (`texte/version/`) and its
    structure (`texte/struct/`), qualified by that folder's name."""
    if kind == "texte_version":
        return f"{file_id}/version"
    if kind == "textelr":
        return f"{file_id}/struct"
    return file_id


def native_id_of(kind: str, file_id: str, fields: dict) -> int:
    return eli.expression_native_id(sort_day(fields)[0], (), record_key(kind, file_id))


def work_key(file_id: str, chain) -> str:
    """The smallest LEGIARTI of the chain, the file's own id included."""
    return min({file_id, *[c for c in chain if c]})


def _iso(day: str | None) -> bool:
    try:
        date.fromisoformat(str(day))
        return len(str(day)) == 10
    except ValueError:
        return False


def tarballs(root: Path) -> tuple[Path | None, list[Path], list[Path]]:
    """`(the latest baseline, the increments after it in timestamp order,
    the increments inside it)`. Without a baseline every increment applies."""
    from .. import walk
    bases, incs = [], []
    for p in walk.files(root, "*.tar.gz"):
        if (m := BASELINE.match(p.name)):
            bases.append((m.group(1), str(p), p))
        elif (m := INCREMENT.match(p.name)):
            incs.append((m.group(1), str(p), p))
    bases.sort()
    incs.sort()
    if not bases:
        return None, [p for _, _, p in incs], []
    ts = bases[-1][0]
    return (bases[-1][2], [p for t, _, p in incs if t > ts], [p for t, _, p in incs if t <= ts])


def _cursor_path(state: DiscoverState) -> Path | None:
    return None if state.state_dir is None else Path(state.state_dir) / "legi_scan_cursor.json"


#: bumped when discovery's output for the same tarballs changes: a cursor of
#: another version re-scans
DISCOVERY_VERSION = 1
IDS = "legi_ids.sqlite"


class LegiSource:
    source_id = "legi"
    ISSUES = ISSUES
    LOCATOR_SEP = _LOCATOR_SEP
    default_profile = {
        "validity": {"kind": "versions-per-work", "from": "extra.version_from",
                     "work": "extra.work_key"},
    }
    license_class = LicenseClass.GREEN          # Licence Ouverte 2.0 (TERMS.txt)
    corpus_provider = "DILA — Direction de l'information légale et administrative (Légifrance)"
    corpus_license = "etalab-2.0"
    corpus_attribution = ("DILA, base LEGI (https://echanges.dila.gouv.fr/OPENDATA/LEGI/), "
                          "Licence Ouverte 2.0; each record names its tarball and member, "
                          "the tarball its date")
    privacy_class = PrivacyClass.PUBLIC
    chunking_profile = "legal_article"            # the shared legal space (decision 8)
    #: the shared legal layer, its tokenizer and window (app/chunking_legal.py)
    default_layers = [chunking_legal.SHARED_LAYER]
    mutability = Mutability.MUTABLE             # a version is rewritten when the origin closes it
    backend = (OriginForm("the LEGI tarballs", Template.CONTAINER_PACK,
                          derived=("legi-pack", "legi-facts")),)
    protocol_version = PROTOCOL_VERSION
    #: 1 since 2026-11-08: no collection was ever created from it. 2 the
    #: same day (free, none created): a TEXTELR's structure is its sections,
    #: bodiless (`structure_sections`), the outline's labels.
    PARSER_VERSION = 2
    origin_versions = "history"
    modality = Modality.TEXT
    erasure = Erasure.RETRACTABLE               # a deletion list withdraws a file
    native_id_order = "numeric"
    default_facets: list = []

    # ---- discovery ------------------------------------------------------------------------

    def discover(self, state: DiscoverState):
        """The effective state (the module docstring), one ref per id, the
        withdrawals with it. A scan whose tarballs are those of the last
        complete one yields nothing (its cursor, written once the refs are
        committed)."""
        import json
        root = Path(state.corpus_root)
        if not root.is_dir():
            return
        base, incs, _inside = tarballs(root)
        order = ([base] if base else []) + incs
        if not order:
            return                      # an empty folder says nothing about the origin's state
        signature = {"version": DISCOVERY_VERSION,
                     "tarballs": [[str(p), p.stat().st_size, p.stat().st_mtime_ns] for p in order]}
        cursor = _cursor_path(state)
        if cursor is not None and order:
            try:
                if json.loads(cursor.read_text(encoding="utf-8")) == signature and all(
                        pack.facts_path(p.parent).is_file() for p in order):
                    return
            except (OSError, ValueError):
                pass

        #: path -> (seq, index, kind, record key, mtime, size, nid, work key | None, chain, member)
        cur: dict[str, tuple] = {}
        withdrawn: dict[int, tuple[str, str]] = {}      # nid -> (locator, why)
        facts: dict[str, list] = {}
        sighted_key: dict[str, str] = {}               # article id -> work key at its last sighting
        locators: list[str] = []
        #: the scan saw the origin's whole state: a baseline, every tarball
        #: read. Only then does an id the last scan held and this one does not
        #: mean the origin no longer holds it — a tarball that could not be
        #: read, or a folder of increments alone, says nothing of the rest
        whole = base is not None

        def fact(ident: str, code: str, detail: str) -> None:
            got = facts.setdefault(ident, [])
            if [code, detail] not in got:
                got.append([code, detail])

        for seq, tar in enumerate(order):
            try:
                pack.ensure(tar)                  # built here once; read below group by group
                deletions = pack.deletion_lines(tar)
                groups = pack.main_groups(tar)
            except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
                yield DocRef(source_id=self.source_id, native_id=tar.name, locator=str(tar),
                             scope="container",
                             error=f"unreadable tarball {tar.name}: {type(e).__name__}: {e}"[:200])
                whole = False
                continue
            locators.append(str(tar))
            for t in groups:
                cols = {c: t.column(c).to_pylist() for c in
                        ("index", "kind", "member", "path", "id", "mtime", "size",
                         "date_debut", "date_texte", "date_publi", "chain")}
                for i in range(t.num_rows):
                    kind = cols["kind"][i]
                    if kind not in KINDS:
                        continue
                    ident = record_key(kind, cols["id"][i])
                    fields = {"date_debut": cols["date_debut"][i], "date_texte": cols["date_texte"][i],
                              "date_publi": cols["date_publi"][i]}
                    nid = native_id_of(kind, cols["id"][i], fields)
                    chain = (cols["chain"][i] or "").split()
                    wk = work_key(ident, chain) if kind == "article" else None
                    path = cols["path"][i]
                    prior = cur.get(path)
                    if prior is not None and prior[6] != nid:
                        withdrawn[prior[6]] = (f"{order[prior[0]]}{_LOCATOR_SEP}{prior[9]}",
                                               f"re-keyed: {ident} sorts under another day now")
                    if wk is not None:
                        was = sighted_key.get(ident)
                        if was is not None and was != wk:
                            fact(ident, "work-key-moved", f"{was} at an earlier sighting, {wk} in {tar.name}")
                        sighted_key[ident] = wk
                    cur[path] = (seq, cols["index"][i], kind, ident, cols["mtime"][i], cols["size"][i],
                                 nid, wk, " ".join(chain), cols["member"][i])
            # this tarball's deletion lists, after its files (the docstring)
            for kind, lines in deletions:
                dossiers = []
                for line in lines:
                    p = pack.normal_path(line)
                    if kind == "deletion_dossier":
                        dossiers.append(p.rstrip("/") + "/")
                        continue
                    held = cur.pop(p, None)
                    if held is None and "/" not in p:
                        for q in [q for q, v in cur.items() if v[3].split("/", 1)[0] == p]:
                            held = cur.pop(q)
                    if held is not None:
                        withdrawn[held[6]] = (f"{order[held[0]]}{_LOCATOR_SEP}{held[9]}",
                                              f"deleted by {tar.name}'s liste_suppression_legi.dat")
                if dossiers:
                    prefixes = tuple(dossiers)
                    for q in [q for q in cur if q.startswith(prefixes)]:
                        held = cur.pop(q)
                        withdrawn[held[6]] = (f"{order[held[0]]}{_LOCATOR_SEP}{held[9]}",
                                              f"deleted by {tar.name}'s liste_suppression_legi_dossier.dat")

        # one record per id: the newest sighting; the others named as an Issue
        by_id: dict[str, list[str]] = {}
        for path, v in cur.items():
            by_id.setdefault(v[3], []).append(path)
        chosen: dict[str, tuple] = {}
        for ident, paths in by_id.items():
            if len(paths) > 1:
                paths.sort(key=lambda q: (-cur[q][0], -cur[q][4], q))
                for other in paths[1:]:
                    fact(ident, "id-in-two-folders", f"also {other} (in {order[cur[other][0]].name})")
            chosen[ident] = cur[paths[0]]
        cur.clear()                     # the memory back before the refs go out
        by_id.clear()
        # a chain's files that disagree on its Work key
        for ident, v in chosen.items():
            if v[2] != "article":
                continue
            for other in v[8].split():
                ov = chosen.get(other)
                if ov is not None and ov[2] == "article" and ov[7] != v[7]:
                    fact(ident, "work-key-disagrees",
                         f"its key {v[7]}; {other}, of its chain, names {ov[7]}")
        for folder in {Path(t).parent for t in locators}:
            pack.write_facts(folder, facts)

        current_nids: dict[int, str] = {}
        guard = eli.CollisionGuard()
        for ident, v in chosen.items():
            nid = v[6]
            other = guard.check(nid, (ident,))
            locator = f"{order[v[0]]}{_LOCATOR_SEP}{v[9]}"
            if other is not None:
                yield DocRef(source_id=self.source_id, native_id=str(nid), locator=locator,
                             scope="container",
                             error=f"native id {nid} collides: {other[0]} and {ident}")
                continue
            current_nids[nid] = ident
        # across scans: an id the last complete scan reported under another id, or no longer held
        held_before = _load_ids(state) if whole else {}
        for ident, (nid, locator) in held_before.items():
            v = chosen.get(ident)
            if v is None and nid not in current_nids:
                withdrawn.setdefault(nid, (locator, f"{ident}: no longer in the origin's state"))
            elif v is not None and v[6] != nid and nid not in current_nids:
                withdrawn.setdefault(nid, (locator, f"re-keyed: {ident} sorts under another day now"))
        for nid, ident in current_nids.items():
            v = chosen[ident]
            yield DocRef(source_id=self.source_id, native_id=str(nid),
                         locator=f"{order[v[0]]}{_LOCATOR_SEP}{v[9]}", mtime=v[4], size=v[5])
        for nid, (locator, why) in sorted(withdrawn.items()):
            if nid in current_nids:
                continue
            yield DocRef(source_id=self.source_id, native_id=str(nid), locator=locator,
                         error=f"withdrawn: {why}"[:200], deleted=True, extra={"reason": "withdrawn"})

        total = state.received
        ids_now = {ident: (nid, f"{order[chosen[ident][0]]}{_LOCATOR_SEP}{chosen[ident][9]}")
                   for nid, ident in current_nids.items()}
        written = []

        def settle() -> None:
            # the cursor and the id map only once every ref of the scan is
            # durable: an interrupted scan reports its withdrawals again
            if written or state.committed < total:
                return
            written.append(True)
            _save_ids(state, ids_now)
            if cursor is not None:
                tmp = cursor.with_name(cursor.name + ".tmp")
                tmp.write_text(json.dumps(signature), encoding="utf-8")
                tmp.replace(cursor)
        if whole:                       # a partial scan leaves both as they were: the next one rescans
            state.on_commit.append(settle)

    # ---- fetch and parse ------------------------------------------------------------------

    def fetch(self, ref: DocRef) -> RawDoc:
        tar, member = split_locator(ref.locator)
        try:
            data = pack.member_bytes(tar, member)
        except pack.NotInPack as e:
            raise UnindexableDocument(str(e)) from e
        except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
            raise UnindexableDocument(f"{tar.name}: {type(e).__name__}: {e}") from e
        return RawDoc(ref=ref, raw_hash=hashlib.blake2b(data, digest_size=16).hexdigest(), data=data)

    def parse(self, raw: RawDoc) -> CanonicalDoc:
        tar, member = split_locator(raw.ref.locator)
        data = raw.data or b""
        try:
            root, entities = lx.root_of(data)
        except lx.LegiXmlError as e:
            raise UnindexableDocument(f"{member}: {e}") from e
        kind = lx.ROOTS.get(root.tag)
        if kind is None:
            raise UnindexableDocument(f"{member}: root <{root.tag}> is none of LEGI's four")
        stem = member.rsplit("/", 1)[-1]
        file_id = stem[:-4] if stem.endswith(".xml") else stem
        fields = lx.index_fields(data)
        day, day_from = sort_day(fields)
        found: list[Issue] = []
        if day_from == "fallback":
            found.append(Issue("sort-day-fallback",
                               f"DATE_DEBUT {fields.get('date_debut')}, DATE_TEXTE {fields.get('date_texte')}, "
                               f"DATE_PUBLI {fields.get('date_publi')}"))
        if entities:
            found.append(Issue("html-entity", ", ".join(entities)))
        for code, detail in pack.facts_of(tar.parent, record_key(kind, file_id)):
            found.append(Issue(code, detail))
        # nothing computable from the record is written into it (02 §2.3):
        # its kind, its sort day, its derived end are read back from what is
        extra: dict = {"eli_jurisdiction": PROFILE_JURISDICTION}
        build = {"article": _article, "texte_version": _texte_version,
                 "section_ta": _section_ta, "textelr": _textelr}[kind]
        title, sections, keys, doc_date = build(root, file_id, fields, extra, found)
        own_id = extra.get("meta_commun", {}).get("ID") or extra.get("section_id")
        if own_id and own_id != file_id:
            found.append(Issue("id-not-file-name", f"ID {own_id}, file {file_id}"))
        return CanonicalDoc(
            uid=f"{self.source_id}:{raw.ref.native_id}",
            title=title, creators=[], language="fr", date=doc_date, subjects=[],
            keys=sorted({k for k in keys if k}),
            extra={k: v for k, v in extra.items() if v not in (None, "", [], {})},
            sections=sections, raw_hash=raw.raw_hash, issues=found)

    # ---- relations and closures -----------------------------------------------------------

    def relations(self, doc: CanonicalDoc):
        """06 §6.1's typed edges from the file's LIENS, named after ELI
        (eli-legal-plugins.md §5.4): `eli:amends` / `eli:repeals` /
        `eli:cites` where the file is the link's SOURCE, `eli:amended_by` /
        `eli:repealed_by` where it is the TARGET (the ontology's inverses) —
        ARTICLE TO ARTICLE where the origin gives an article id, else to the
        text. The direction (legal-fr-de-eu.md §2.4): a verb form (MODIFIE,
        ABROGE) and CITATION give the carrying file's role in `sens`; a noun
        form (MODIFICATION, ABROGATION) gives the LINKED element's. A citation
        of this file by another is the other file's own edge (LEGI writes the
        link on both sides): its passive form is kept in `extra.liens`, not
        made an edge. Every other `typelien` (CREATION, CODIFICATION,
        TRANSFERT, CONCORDE, …) stays in `extra.liens`. `to` is a canonical
        id left unresolved (05 §5.5): `legi:…`, `jorf:…`. One edge per (type,
        target)."""
        seen: set[tuple[str, str]] = set()
        for lien in (doc.extra or {}).get("liens") or []:
            got = edge_of(lien)
            if got is None or got in seen:
                continue
            seen.add(got)
            yield Edge(to=got[1], type=got[0])

    def closures(self, doc: CanonicalDoc):
        """app/validity.py's hook: a version whose ETAT ends its article and
        whose chain lists no later version states its own end, DATE_FIN,
        against its own key — the origin's last word on the article (the
        module docstring). Nothing else closes: a LIEN carries no date, and
        over-closing would mis-date an article silently."""
        x = doc.extra or {}
        if kind_of(x) != "article" or "version_from" not in x:
            return
        meta = x.get("meta_article") or {}
        etat, fin = str(meta.get("ETAT") or ""), str(meta.get("DATE_FIN") or "")
        own = next((k for k in doc.keys if k.startswith("legi:LEGIARTI")), None)
        if own and etat in CLOSING and _usable_end(fin) and next_start(x) is None:
            yield own, fin


def _usable_end(day) -> bool:
    return _iso(day) and day not in PLACEHOLDERS


def kind_of(extra: dict) -> str | None:
    """Which of the four kinds a record is, read from what it holds."""
    if "meta_article" in extra:
        return "article"
    if "meta_texte_version" in extra:
        return "texte_version"
    if "structure_ta" in extra or "titre_ta" in extra:
        return "section_ta"
    if "meta_texte_chronicle" in extra:
        return "textelr"
    return None


def chain(versions: list[dict]) -> list[dict]:
    """Every LIEN_ART of VERSIONS, with its VERSION's état."""
    out = []
    for v in versions or ():
        for c in v.get("children") or []:
            if c.get("tag") == "LIEN_ART":
                out.append({**c, "version_etat": v.get("etat")})
    return out


def next_start(extra: dict) -> str | None:
    """The start of the next version the article's chain lists after this
    one's, a MODIFIE_MORT_NE one never counted (it never entered into
    force): what ends this version, derived (app/validity.py does the same
    over the versions it holds)."""
    start = extra.get("version_from")
    if not start:
        return None
    starts = sorted({c.get("debut") for c in chain(extra.get("versions") or [])
                     if MORT_NE not in (c.get("etat"), c.get("version_etat"))
                     and _iso(c.get("debut")) and c.get("debut") > start})
    return starts[0] if starts else None


# ---- the four kinds ------------------------------------------------------------------------

def _id_key(ident: str | None) -> str | None:
    """`legi:` for a LEGI id, `jorf:` for a JORF one; None for anything else."""
    if not ident:
        return None
    if ident.startswith("LEGI"):
        return f"legi:{ident}"
    if ident.startswith("JORF"):
        return f"jorf:{ident}"
    return None


def _texte_extra(extra: dict, texte: dict, title: str | None) -> None:
    """ELI rule 5's fixed keys, from the text's attributes, where given."""
    extra["eli_type_document"] = texte.get("nature")
    extra["eli_number"] = texte.get("num")
    if _usable(texte.get("date_signature")):
        extra["eli_date_document"] = texte["date_signature"]
    if _usable(texte.get("date_publi")):
        extra["eli_date_publication"] = texte["date_publi"]
    extra["eli_id_local"] = texte.get("cid")
    extra["eli_title"] = title


def _title_at(ctx: dict, day: str | None) -> str:
    """The text's title in the version covering `day` (TITRE_TXT debut/fin),
    else its first title."""
    titles = ctx.get("titres_txt") or []
    for t in titles:
        if day and t.get("debut", "") <= day < (t.get("fin") or OPEN_DAY):
            return t.get("text", "")
    return titles[0].get("text", "") if titles else ""


def _article(root, file_id: str, fields: dict, extra: dict, found: list) -> tuple:
    meta = root.find("META")
    meta_commun = lx.fields(meta.find("META_COMMUN") if meta is not None else None)
    spec = meta.find("META_SPEC") if meta is not None else None
    meta_article = lx.fields(spec.find("META_ARTICLE") if spec is not None else None)
    ctx = lx.contexte(root.find("CONTEXTE"))
    versions = lx.links(root.find("VERSIONS"))
    liens = lx.links(root.find("LIENS"))
    nota_el = root.find("NOTA/CONTENU")
    body_el = root.find("BLOC_TEXTUEL/CONTENU")
    text = lx.render(body_el)
    num = str(meta_article.get("NUM") or "")
    etat = str(meta_article.get("ETAT") or "")
    debut = str(meta_article.get("DATE_DEBUT") or "")
    fin = str(meta_article.get("DATE_FIN") or "")
    texte = ctx.get("texte") or {}
    text_title = _title_at(ctx, debut)
    chain_ids = [c.get("id") for c in chain(versions) if str(c.get("id") or "").startswith("LEGIARTI")]
    if file_id not in chain_ids:
        found.append(Issue("own-id-not-in-versions", f"VERSIONS lists {', '.join(chain_ids) or 'none'}"))
    extra.update({
        "meta_commun": meta_commun, "meta_article": meta_article,
        "contexte": ctx, "versions": versions, "liens": liens,
        "nota": lx.render(nota_el), "images": lx.images(body_el) + lx.images(nota_el),
        eli.IN_FORCE: (True if etat == "VIGUEUR" else False if etat in NOT_IN_FORCE else None),
    })
    _texte_extra(extra, texte, text_title)
    other = [lx.tree(c) for c in root if c.tag not in
             ("META", "CONTEXTE", "VERSIONS", "NOTA", "BLOC_TEXTUEL", "LIENS")]
    if other:
        extra["other"] = other
    if not etat:
        found.append(Issue("etat-empty", file_id))
    sections: list[tuple[str, str]] = []
    if etat == MORT_NE:
        # held, never searchable (decision 3): no validity, no sections
        extra["mort_ne_text"] = text
        found.append(Issue("modifie-mort-ne", f"{debut} → {fin}"))
    else:
        extra["work_key"] = work_key(file_id, chain_ids)
        if _iso(debut):
            extra["version_from"] = debut
            if debut == UNKNOWN_DAY:
                found.append(Issue("placeholder-start", file_id))
        else:
            extra["version_from"] = sort_day(fields)[0]
            found.append(Issue("start-unreadable", f"DATE_DEBUT {debut!r}"))
        derived = next_start(extra) or (fin if etat in CLOSING and _usable_end(fin)
                                        else "a date (its ETAT closes it)" if etat in CLOSING
                                        else OPEN_DAY)
        if fin != derived:
            found.append(Issue("date-fin-disagrees", f"DATE_FIN {fin or '(none)'}, derived {derived} "
                                                     f"(ETAT {etat or '(none)'})"))
        if text:
            sections.append((f"Article {num}".strip(), text))
    title = " — ".join(x for x in (text_title, f"Article {num}".strip() if num else "") if x)
    keys = [f"legi:{file_id}", _id_key(texte.get("cid")),
            f"nor:{texte['nor']}" if texte.get("nor") else None]
    return title, sections, keys, extra.get("eli_date_document")


def _texte_version(root, file_id: str, fields: dict, extra: dict, found: list) -> tuple:
    meta = root.find("META")
    meta_commun = lx.fields(meta.find("META_COMMUN") if meta is not None else None)
    spec = meta.find("META_SPEC") if meta is not None else None
    chron = lx.fields(spec.find("META_TEXTE_CHRONICLE") if spec is not None else None)
    version_el = spec.find("META_TEXTE_VERSION") if spec is not None else None
    version = lx.fields(version_el)
    # the text's own LIENS (legi_texte_version.dtd: inside META_TEXTE_VERSION)
    # as an article's are: `liens`, which `relations` reads
    if version_el is not None and version_el.find("LIENS") is not None:
        version.pop("LIENS", None)
        extra["liens"] = lx.links(version_el.find("LIENS"))
    parts =("VISAS", "SIGNATAIRES", "TP", "NOTA", "ABRO", "RECT")
    sections = []
    for tag in parts:
        el = root.find(f"{tag}/CONTENU")
        body = lx.render(el)
        if body:
            sections.append((tag, body))
        imgs = lx.images(el)
        if imgs:
            extra.setdefault("images", []).extend({"part": tag, **i} for i in imgs)
    etat = str(version.get("ETAT") or "")
    title = str(version.get("TITREFULL") or version.get("TITRE") or "")
    extra.update({"meta_commun": meta_commun, "meta_texte_chronicle": chron,
                  "meta_texte_version": version,
                  eli.IN_FORCE: (True if etat == "VIGUEUR" else False if etat in NOT_IN_FORCE else None)})
    _texte_extra(extra, {"nature": meta_commun.get("NATURE"), "num": chron.get("NUM"),
                         "date_signature": chron.get("DATE_TEXTE"), "date_publi": chron.get("DATE_PUBLI"),
                         "cid": chron.get("CID")}, title)
    other = [lx.tree(c) for c in root if c.tag not in ("META", *parts)]
    if other:
        extra["other"] = other
    keys = [f"legi:{file_id}", _id_key(chron.get("CID")),
            f"nor:{chron['NOR']}" if chron.get("NOR") else None]
    return title, sections, keys, extra.get("eli_date_document")


def _section_ta(root, file_id: str, fields: dict, extra: dict, found: list) -> tuple:
    titre = lx.text_of(root.find("TITRE_TA"))
    commentaire = lx.text_of(root.find("COMMENTAIRE"))
    ctx = lx.contexte(root.find("CONTEXTE"))
    texte = ctx.get("texte") or {}
    text_title = _title_at(ctx, None)
    # TITRE_TA and COMMENTAIRE are the record's sections, not repeated here
    extra.update({"section_id": lx.text_of(root.find("ID")) or None,
                  "structure_ta": lx.links(root.find("STRUCTURE_TA")), "contexte": ctx})
    _texte_extra(extra, texte, text_title)
    other = [lx.tree(c) for c in root if c.tag not in
             ("ID", "TITRE_TA", "COMMENTAIRE", "STRUCTURE_TA", "CONTEXTE")]
    if other:
        extra["other"] = other
    sections = [(lbl, body) for lbl, body in (("TITRE_TA", titre), ("COMMENTAIRE", commentaire)) if body]
    title = " — ".join(x for x in (text_title, titre) if x)
    keys = [f"legi:{file_id}", _id_key(texte.get("cid")),
            f"nor:{texte['nor']}" if texte.get("nor") else None]
    return title, sections, keys, extra.get("eli_date_document")


def _textelr(root, file_id: str, fields: dict, extra: dict, found: list) -> tuple:
    meta = root.find("META")
    meta_commun = lx.fields(meta.find("META_COMMUN") if meta is not None else None)
    spec = meta.find("META_SPEC") if meta is not None else None
    chron = lx.fields(spec.find("META_TEXTE_CHRONICLE") if spec is not None else None)
    extra.update({"meta_commun": meta_commun, "meta_texte_chronicle": chron,
                  "versions": lx.links(root.find("VERSIONS")), "struct": lx.links(root.find("STRUCT"))})
    _texte_extra(extra, {"nature": meta_commun.get("NATURE"), "num": chron.get("NUM"),
                         "date_signature": chron.get("DATE_TEXTE"), "date_publi": chron.get("DATE_PUBLI"),
                         "cid": chron.get("CID")}, None)
    other = [lx.tree(c) for c in root if c.tag not in ("META", "VERSIONS", "STRUCT")]
    if other:
        extra["other"] = other
    title = " ".join(str(x) for x in (meta_commun.get("NATURE"), chron.get("NUM"), chron.get("CID") or file_id)
                     if x)
    keys = [f"legi:{file_id}", _id_key(chron.get("CID")),
            f"nor:{chron['NOR']}" if chron.get("NOR") else None]
    return title, structure_sections(extra.get("struct") or []), keys, extra.get("eli_date_document")


def structure_sections(struct: list[dict]) -> list[tuple[str, str]]:
    """A text's STRUCT as sections with NO body (parser 2, operator
    2026-11-08, piece 2): each section by its title as the origin writes it
    ("Chapitre I : Monnaies et médailles", "(abrogé)" after a repealed
    one), each article as "Article <num>". Nothing is chunked from them;
    the `legal_article` layer's outline is cut from their labels, so a
    question about a whole code reaches it (each article is its own
    record, app/chunking_legal.py)."""
    out: list[tuple[str, str]] = []
    for link in struct:
        etat = str(link.get("etat") or "")
        gone = " (abrogé)" if etat.startswith("ABROGE") else ""
        if link.get("tag") == "LIEN_SECTION_TA" and str(link.get("text") or "").strip():
            out.append((str(link["text"]).strip() + gone, ""))
        elif link.get("tag") == "LIEN_ART" and str(link.get("num") or "").strip():
            out.append((f"Article {str(link['num']).strip()}{gone}", ""))
    return out


# ---- LIENS → edges -------------------------------------------------------------------------

#: typelien → (what it is, whether `sens` names the carrying file's role)
_LINK_KINDS = {"MODIFIE": ("amend", True), "ABROGE": ("repeal", True), "CITATION": ("cite", True),
               "MODIFICATION": ("amend", False), "ABROGATION": ("repeal", False)}
_ACTIVE = {"amend": "eli:amends", "repeal": "eli:repeals", "cite": "eli:cites"}
_PASSIVE = {"amend": "eli:amended_by", "repeal": "eli:repealed_by"}


def edge_of(lien: dict) -> tuple[str, str] | None:
    """`(type, to)` for one LIEN, or None (the `relations` docstring)."""
    kind = _LINK_KINDS.get(str(lien.get("typelien") or "").upper())
    sens = str(lien.get("sens") or "").lower()
    if kind is None or sens not in ("source", "cible"):
        return None
    what, carrying = kind
    role = sens if carrying else ("cible" if sens == "source" else "source")
    etype = _ACTIVE.get(what) if role == "source" else _PASSIVE.get(what)
    target = _id_key(lien.get("id")) or _id_key(lien.get("cidtexte"))
    if etype is None or target is None:
        return None
    return etype, target


# ---- the id map, across scans --------------------------------------------------------------

def _ids_path(state: DiscoverState) -> Path | None:
    return None if state.state_dir is None else Path(state.state_dir) / IDS


def _load_ids(state: DiscoverState) -> dict[str, tuple[int, str]]:
    """What the last complete scan reported: id → (native id, locator)."""
    path = _ids_path(state)
    if path is None or not path.is_file():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[0]: (int(r[1]), r[2]) for r in con.execute("SELECT id, nid, locator FROM ids")}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def _save_ids(state: DiscoverState, ids: dict[str, tuple[int, str]]) -> None:
    """The scan's id map, written whole into a new file then replaced."""
    import os
    path = _ids_path(state)
    if path is None:
        return
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.execute("CREATE TABLE ids (id TEXT PRIMARY KEY, nid INTEGER NOT NULL, locator TEXT NOT NULL)")
        con.executemany("INSERT INTO ids VALUES (?,?,?)",
                        ((i, n, loc) for i, (n, loc) in ids.items()))
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)
