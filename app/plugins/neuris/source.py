"""NeurisSource — CorpusSource over German federal law as the federal
government's legal information portal publishes it (NeuRIS,
`rechtsinformationen.bund.de`, the test phase: the only host that answers,
docs/notes/legal-fr-de-eu.md §7.1), on the generic ELI layer (../eli.py) and
Normattiva's Akoma Ntoso reader (../normattiva/akn.py, wrapped by ldml.py).

The operator's decisions (2026-10-28 D1: NeuRIS now, gesetze-im-internet
NOT used; 2026-11-08: legi, neuris and eurlex on Nemotron-3-Embed-1B in ONE
shared space; docs/notes/legal-collections-nemotron.md) and the build's
(2026-11-08), each beside the code that keeps it:

ONE RECORD PER EXPRESSION (§3.3): every expression of every act NeuRIS lists
is a record — the act in one version, `eli/bund/bgbl-1/{year}/{natId}/
{pointInTime}/{version}/deu`. A question about the law on a date is answered
by validity, a versions-per-work profile like Normattiva's (`default_profile`,
app/validity.py): the record carries its START (`valid_from`), the end is
derived node-side from the next version's start. The origin ALSO states an
end — `ris:ausserkraft`, the `temporalCoverage` of the API's answer, the
`ausserkrafttreten` events and the `temporalData` periods — and every one of
them is KEPT (`valid_to_stated`, `ris`, `lifecycle`, `periods`) and compared
where the origin states two: a disagreement is an Issue (§5.1), never
resolved in silence. A conditional entry into force
(`bedingtesInkrafttreten`) with no date is an open start, said on the record
(`conditional_entry_into_force`) and never guessed; a version with no stated
start takes no part in the validity filter and is reported.

HISTORY ARRIVES AS NEURIS PUBLISHES IT. NeuRIS's own pages list historical
versions as planned and today's laws "in der jeweils aktuell gültigen
Fassung" (§3.1): what is held is every expression NeuRIS lists, from our
first snapshot on, and each one it adds later. That is SCOPE, stated on the
card — not a pruning: nothing the origin gives is left out, and no earlier
version exists anywhere we could take it from (gesetze-im-internet is
current-only and not used, D1).

THE ORIGIN FORM, and why — invariant 11 wants it recorded beside the
plugin, because a verifier needs it to reproduce our records:

  * LegalDocML.de XML, NOT the HTML the API also serves, nor gesetze-im-
    internet's gii-norm XML. LegalDocML.de is an Akoma Ntoso profile (FRBR
    Work / Expression / Manifestation, lifecycle, temporalData, the eIds of
    every article and paragraph, and NeuRIS's `ris:` metadata — abkuerzung,
    inkraft, ausserkraft, vollzitat, standangabe, doknr, verweise): the only
    form that carries the structure and the metadata together, and the one
    Normattiva's reader already reads. The HTML is a rendering of it.
  * THE MANIFESTATION ZIP, not `regelungstext-1.xml` alone: the annexes are
    FILES OF THEIR OWN (`anlage-regelungstext-N.xml`), as are the images and
    the PDF, and fetching the main text alone would drop them (invariant
    11). The zip is the API's one answer that carries all of them
    (`/v1/legislation/eli/…/deu/{generated}.zip`, "XML and attachments").
  * PLUS THE PER-EXPRESSION JSON (`/v1/legislation/eli/…/deu`): it repeats
    the listing item and adds `risAbbreviation` and the parts — the
    abbreviations the citations name, the Ausfertigung and Verkündung dates,
    the Gazette reference, the stated validity, the force status. Kept
    whole (`api`).
  * Both are stored as fetched under the corpus root by the expression's
    ELI, named by the manifestation's generation date
    (app/acquire/neuris_listing.py), so a regenerated manifestation is a new
    file and an unchanged one is the same path.

WHAT `origin_hash` COVERS. The zip is built ON THE FLY (the portal's
ZipManager writes each entry with the time of the request), so its bytes
differ on every fetch while its members do not: `origin_hash` is BLAKE2b-128
over the zip's MEMBERS — sorted by name, each name and its bytes
length-prefixed — as Normattiva's covers a member's bytes and not the zip's.
The record is parsed from the expression's JSON too, so the JSON is one more
member of the unit (`EXPRESSION_JSON`, a name no zip member can have): a
JSON the origin revised is a revised origin unit, read as "changed at the
origin" by `records-vs-origin`, never as a record that differs from an
unchanged one. `records-vs-origin` re-fetches the zip and the JSON
(`--origin DIR`, laid out as acquisition lays them) and compares members,
not zip bytes. `origin` is the zip's path under the corpus root; the JSON is
its sibling by name.

ONE MANIFESTATION PER EXPRESSION. Where the corpus holds two of one
expression (a regenerated manifestation beside the one fetched before), the
latest generation date is the record's; the older stays on disk, undiscovered.
Whether the API still serves an older manifestation is owed from the node; until
it is known to, the origin REPLACES (`origin_versions`), so a superseded part
version is not kept as if it could be checked against the source.

IDENTITY (eli-legal-plugins.md, rules 1–3; legal-fr-de-eu.md §3.3). The Work
is the ELI up to the natural id (`eli/bund/bgbl-1/2000/s1016`; `natId` is
`s{page}` before 2023 and the issue number since), the Expression its
`{pointInTime}/{version}/deu`, READ BACK from what the origin returned (the
FRBRExpression's `FRBRuri`; a path that disagrees is an Issue). `native_id`
is `eli.expression_native_id(pointInTime, (agent, year, natId),
"{pointInTime}/{version}/{language}")`: the id sorts by the expression's
point in time, which the PATH carries, so discovery opens nothing and
arrival stays monotonic (02 §2.2); validity starts at the stated start, its
own column (as a Normattiva original's does). Collision-checked, never
trusted (`eli.CollisionGuard`); a point in time outside the id's day range
is refused loudly (the portal's test data has expressions dated 2920).

KEYS (05 §5.2: only ids a reference source registers). The Work's ELI in
`eli.Profile.key` form, `eli:de:bund/bgbl-1/2000/s1016` (the `eli` owner,
app/refs/eli.py, routes `de` to app/refs/neuris.py), and `jurabk:<abbr>` for
the API's `abbreviation` (the official one where it exists, else the
ris-abbreviation) and its `risAbbreviation` — the two forms the reference
source syncs, from the same JSON. AN ABBREVIATION IS WRITTEN VERBATIM, SPACES
INCLUDED (`jurabk:SGB 5`): 05 §5.2 makes a canonical id "the origin's own
identifier verbatim" and says nothing more of the characters an id may hold,
so nothing is normalised, and the record → refs → lookup round trip is
tested with a space (tests/test_neuris.py).

EXTRA — every field the origin asserts, keys fixed for the collection's life
(EXTRA_KEYS): the ELI keys of eli.EXTRA_KEYS that apply; the API's answer
whole (`api`) and its fields by name; the whole `meta` block lossless
(identification, lifecycle, temporalData, notes, proprietary — ldml.node);
the FRBR values; the lifecycle events; the periods resolved; NeuRIS's
`ris:` fields by the schema's names and the Regelungstext metadata; the
preface (long and short title, its inlines and dates), the preamble and
the conclusions; every article's ids, period and doknr; every annex's
metadata and notes; the `documentRef`s; every member of the zip with its
size and hash, the images and the PDF included. The force status
(`legislationLegalForce`) is the origin's word ON THE DAY OF THE FETCH —
"currently in force" — kept as given, and `in_force` follows it (09 §9.5).

SECTIONS (`chunking_profile = "akn_article"`, the shared space's chunker):
one per § / Art. with its text — the number and heading, then each
paragraph "(1) …" at the margin and its points indented — then one per
Anlage, its title and its text (tables row by row). An annex the XML holds
no text for is a section with an empty body: present, not chunked. The
preamble, the conclusions and the editorial notes are the act's apparatus,
kept in `extra` and never embedded (as Normattiva's are). A paragraph
longer than the chunker's limit is cut by CDC within itself: the chunker's
comma rule is Italian ("1."), and changing it would move the shared space.

RELATIONS (06 §6.1, ELI names): `eli:cites` for every act a `ris:verweis`
names (`targetWorkEli1/2`, else its `bezugsnorm*Jurabk`) and every `<ref>`
to an ELI; `eli:repeals` / `eli:amends` for the acts an active
modification names (akn.py's `analysis`). The fixtures carry no
`ris:verweise` and no modifications: whether the origin fills them is OWED
from the node; the code is tested on a hand-made, marked fixture.
`closures()` as Normattiva's: the acts this version states it repeals in
full, from the day the destination names or this version's own start.

LICENCE: laws and ordinances are official works without copyright, § 5 (1)
UrhG; NeuRIS states no data licence anywhere — its Impressum
(ris-search@81b58f82 `frontend/src/pages/impressum/content.md`) carries a
liability disclaimer only, its code is GPL-3.0 (§3.6). Class GREEN. The
terms text is OWED: no Nutzungsbedingungen exist in the portal's public
source to record byte-exact, and none is invented.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from app.acquire.neuris_listing import EXPRESSION_ELI
from app.backend import OriginForm, Template

from .. import eli
from ..base import (
    PROTOCOL_VERSION,
    CanonicalDoc,
    DiscoverState,
    DocRef,
    Edge,
    Erasure,
    Issue,
    LicenseClass,
    Modality,
    Mutability,
    PrivacyClass,
    RawDoc,
    UnindexableDocument,
)
from . import ldml

#: Germany's federal ELI as NeuRIS writes it. The origin writes its ELIs
#: RELATIVE (`FRBRuri` `eli/bund/…`) and asserts no absolute base, so none is
#: invented: `eli_work` is the origin's own string.
PROFILE = eli.Profile(
    jurisdiction="DE", authority="bund", base="",
    uri_template="eli/bund/{agent}/{year}/{naturalIdentifier}/{pointInTime}/{version}/{language}",
    work_key=("agent", "year", "naturalIdentifier"))

#: the name the expression's JSON is hashed under, beside the zip's members:
#: a zip member's name is a path (`eli/bund/…`), never this
EXPRESSION_JSON = "#expression.json"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAIN = re.compile(r"(?:^|/)regelungstext-(\d+)\.xml$")
_FORCE = {"InForce": True, "NotInForce": False}
_WORK_ELI = re.compile(r"eli/bund/[a-z0-9-]+/\d{4}/[A-Za-z0-9_-]+")

#: 06 §6.1's Issues: what the parse met in the origin and worked around
ISSUES = {
    "stated-start-disagrees": "the API's temporalCoverage starts on another day than the "
                              "XML's ris:inkraft; the API's start is the record's",
    "stated-end-disagrees": "the API's temporalCoverage ends on another day than the XML's "
                            "ris:ausserkraft; both are kept",
    "no-stated-start": "neither the API nor the XML states when this version came into force, "
                       "and no conditional entry into force is declared; the version takes no "
                       "part in the validity filter",
    "expression-eli-mismatch": "the FRBRExpression the XML returns is not the expression its "
                               "path and the API name; the XML's is the record's (eli rule 2)",
    "annex-missing": "the act's attachments name a file the manifestation zip does not hold",
}

#: every key a record's `extra` may carry — fixed for the collection's life
#: (invariant 11, 09 §9.5); a value the origin does not give is left out
EXTRA_KEYS = (
    # eli.EXTRA_KEYS that apply (rule 5) and 09 §9.5's force status
    "eli_jurisdiction", "eli_work", "eli_expression", "eli_type_document", "eli_number",
    "eli_date_document", "eli_date_publication", "eli_version", "eli_version_date",
    "eli_title", "eli_cites", eli.IN_FORCE,
    # the expression and its validity
    "point_in_time", "manifestation", "valid_from", "valid_to_stated",
    "conditional_entry_into_force", "temporal_coverage", "legal_force",
    # the API's answer: its fields by name, and the whole of it
    "name", "abbreviation", "ris_abbreviation", "alternate_name", "gazette", "api",
    # the XML: LegalDocML.de's meta lossless, and its fields read
    "act_name", "meta", "frbr", "lifecycle", "periods", "ris", "regtxt", "proprietary_other",
    "notes", "analysis", "active_modifications", "passive_modifications", "references",
    "preface", "doc_title", "preface_notes", "preamble", "conclusions",
    "articles", "article_notes", "attachment_refs", "annexes", "other_texts", "members",
    # parser 2: the manifestation's `rechtsetzungsdokument`, and any XML
    # member of a root the grammar does not name here, kept whole
    "rechtsetzungsdokument", "other_xml",
)


def split_path(zpath: Path, root: Path) -> tuple[str, str] | None:
    """`(expression ELI, generation date)` of a stored manifestation zip,
    from its path under the corpus root; None for any other file."""
    try:
        rel = zpath.relative_to(root).as_posix()
    except ValueError:
        return None
    head, _, name = rel.rpartition("/")
    stem = name[:-4] if name.endswith(".zip") else ""
    if not _DATE.match(stem) or not EXPRESSION_ELI.match(head):
        return None
    return head, stem


def path_parts(expression: str) -> dict:
    m = EXPRESSION_ELI.match(expression)
    if m is None:
        raise ValueError(f"not an expression ELI: {expression!r}")
    return m.groupdict()


def work_of(expression: str) -> str:
    """`eli/bund/bgbl-1/2000/s1016/2023-04-26/10/deu` → `eli/bund/bgbl-1/2000/s1016`."""
    return "/".join(expression.split("/")[:5])


def eli_key(eli_path: str) -> str | None:
    """The canonical id of the Work an ELI names (any of its levels,
    relative or under a host): `eli:de:bund/bgbl-1/2000/s1016`."""
    m = _WORK_ELI.search(str(eli_path or ""))
    return PROFILE.key(m.group(0)[len("eli/"):]) if m else None


def native_id_of(expression: str) -> int:
    p = path_parts(expression)
    return eli.expression_native_id(p["pit"], (p["agent"], p["year"], p["nat"]),
                                    f"{p['pit']}/{p['version']}/{p['lang']}")


def read_members(data: bytes) -> dict[str, bytes]:
    """Every file member of the zip, by name; a name twice is refused (which
    of the two would the record be?)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            out: dict[str, bytes] = {}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.filename in out:
                    raise UnindexableDocument(f"the zip holds {info.filename} twice")
                out[info.filename] = zf.read(info)
            return out
    except (zipfile.BadZipFile, OSError, EOFError) as e:
        raise UnindexableDocument(f"not a readable zip: {type(e).__name__}: {e}") from e


def origin_hash(members: dict[str, bytes], expression_json: bytes) -> str:
    """BLAKE2b-128 over the origin unit: the zip's members and the
    expression's JSON, sorted by name, each name and its bytes
    length-prefixed — independent of how the zip that carried them was
    built (its order, its timestamps, its compression)."""
    h = hashlib.blake2b(digest_size=16)
    for name, data in sorted({**members, EXPRESSION_JSON: expression_json}.items()):
        nb = name.encode("utf-8")
        h.update(len(nb).to_bytes(8, "big"))
        h.update(nb)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def _pack(expression_json: bytes, zip_bytes: bytes) -> bytes:
    """The pair as `fetch` read it, for `parse`: the JSON length-prefixed,
    then the zip (the plugin's own RawDoc form)."""
    return len(expression_json).to_bytes(8, "big") + expression_json + zip_bytes


def _unpack(data: bytes) -> tuple[bytes, bytes]:
    n = int.from_bytes(data[:8], "big")
    return data[8:8 + n], data[8 + n:]


def coverage(text: str | None) -> tuple[str | None, str | None]:
    """ISO 8601 interval `2023-04-29/..` → (start, end); `..` is open."""
    start, _, end = str(text or "").partition("/")
    return (start if _DATE.match(start) else None), (end if _DATE.match(end) else None)


def _blocks_text(blocks) -> str:
    """Paragraphs at the margin ("(1) …"), points indented three spaces a
    level; a table's rows joined " | " (akn.py's)."""
    out = []
    for b in blocks:
        indent = "   " * b.depth
        parts = b.text.split("\n") if b.text else [""]
        first = f"{b.num} {parts[0]}".strip() if b.num else parts[0]
        out.append(indent + first)
        out.extend(indent + p for p in parts[1:])
    return "\n".join(line for line in out if line.strip())


def article_line(article) -> str:
    return " — ".join(x for x in (article.num, article.heading) if x)


def article_sections(act) -> list[tuple[str, str]]:
    out = []
    for a in act.articles:
        line = article_line(a)
        label = " › ".join(x for x in (a.partition, line) if x) or "Text"
        body = "\n".join(x for x in (line, _blocks_text(a.blocks)) if x)
        out.append((label, body))
    return out


def _is_main(name: str) -> int | None:
    m = _MAIN.search(name)
    return int(m.group(1)) if m and not name.rsplit("/", 1)[-1].startswith("anlage-") else None


class NeurisSource:
    source_id = "neuris"
    ISSUES = ISSUES
    ELI_PROFILE = PROFILE
    EXTRA_KEYS = EXTRA_KEYS
    #: 09 §9.3's `profile`, copied into the Codex at `create`: the ELI
    #: implementation and the validity axis, one record per expression
    default_profile = {
        "eli": {"jurisdiction": PROFILE.jurisdiction, "authority": PROFILE.authority,
                "uri_template": PROFILE.uri_template, "work_key": list(PROFILE.work_key)},
        "validity": {"kind": "versions-per-work", "from": "extra.valid_from",
                     "work": "extra.eli_work"},
    }
    license_class = LicenseClass.GREEN     # § 5 UrhG; no data licence stated (docstring)
    corpus_provider = ("Bundesministerium der Justiz und für Verbraucherschutz — "
                       "Rechtsinformationsportal des Bundes (NeuRIS), DigitalService GmbH des Bundes")
    corpus_license = "urhg-5-amtliche-werke"
    corpus_attribution = ("Rechtsinformationen des Bundes (rechtsinformationen.bund.de), "
                          "Testphase — amtliche Werke, § 5 UrhG")
    privacy_class = PrivacyClass.PUBLIC
    chunking_profile = "akn_article"
    #: a manifestation is regenerated and an expression's metadata revised
    mutability = Mutability.MUTABLE
    backend = (OriginForm("the manifestation zips and their expressions' JSON", Template.MEMBER),)
    protocol_version = PROTOCOL_VERSION
    #: 1 (2026-11-08): the first; no collection made from it yet.
    #: 2 (2026-11-08, the same day, nothing made from 1): an XML member is read
    #: by what its root is — the `rechtsetzungsdokument` every real zip
    #: carries (München's probe) was read as an annex, and refused every
    #: expression; it is kept in `extra.rechtsetzungsdokument` now
    PARSER_VERSION = 2
    #: 06 §6.2: an expression stays listed, a manifestation is regenerated in
    #: place; an older one is not known to stay fetchable (owed from the node)
    origin_versions = "replaces"
    modality = Modality.TEXT
    erasure = Erasure.RETRACTABLE          # an expression the origin withdraws
    native_id_order = "numeric"            # eli.expression_native_id: the point in time first
    default_facets: list = []

    def discover(self, state: DiscoverState):
        """Every stored manifestation zip under the corpus root whose JSON
        is beside it — an expression whose pair is not complete yet is being
        fetched, and appears at the next scan; the latest generation date of
        an expression is its record's. In native-id order."""
        root = Path(state.corpus_root)
        if not root.is_dir():
            return
        from .. import walk
        latest: dict[str, tuple[str, Path]] = {}
        for zpath in walk.files(root / "eli", "*.zip"):
            got = split_path(zpath, root)
            if got is None:
                continue
            expression, generated = got
            if not zpath.with_suffix(".json").is_file():
                continue
            held = latest.get(expression)
            if held is None or generated > held[0]:
                latest[expression] = (generated, zpath)
        guard = eli.CollisionGuard()
        refs: dict[int, DocRef] = {}
        for expression in sorted(latest):
            generated, zpath = latest[expression]
            try:
                nid = native_id_of(expression)
            except ValueError as e:
                yield DocRef(source_id=self.source_id, native_id=expression, locator=str(zpath),
                             scope="container", error=f"{expression}: {e}"[:200])
                continue
            p = path_parts(expression)
            other = guard.check(nid, (p["agent"], p["year"], p["nat"], p["pit"], p["version"], p["lang"]))
            if other is not None:
                yield DocRef(source_id=self.source_id, native_id=str(nid), locator=str(zpath),
                             scope="container",
                             error=f"native id {nid} collides: {'/'.join(other)} and {expression}"[:200])
                continue
            jst, zst = zpath.with_suffix(".json").stat(), zpath.stat()
            refs[nid] = DocRef(source_id=self.source_id, native_id=str(nid), locator=str(zpath),
                               mtime=int(max(zst.st_mtime, jst.st_mtime)),
                               size=zst.st_size + jst.st_size)
        for nid in sorted(refs):
            yield refs[nid]

    def fetch(self, ref: DocRef) -> RawDoc:
        zpath = Path(ref.locator)
        try:
            zip_bytes = zpath.read_bytes()
            json_bytes = zpath.with_suffix(".json").read_bytes()
        except OSError as e:
            raise UnindexableDocument(f"{zpath.name}: {type(e).__name__}: {e}") from e
        members = read_members(zip_bytes)
        return RawDoc(ref=ref, raw_hash=origin_hash(members, json_bytes), path=zpath,
                      data=_pack(json_bytes, zip_bytes))

    def parse(self, raw: RawDoc) -> CanonicalDoc:
        zpath = Path(raw.ref.locator)
        expression = "/".join(zpath.parent.parts[-8:])
        generated = zpath.stem
        if not EXPRESSION_ELI.match(expression) or not _DATE.match(generated):
            raise UnindexableDocument(f"{zpath}: not stored by an expression's ELI")
        json_bytes, zip_bytes = _unpack(raw.data or b"")
        try:
            api = json.loads(json_bytes)
        except ValueError as e:
            raise UnindexableDocument(f"{zpath.with_suffix('.json').name}: not JSON: {e}") from e
        if not isinstance(api, dict):
            raise UnindexableDocument(f"{zpath.with_suffix('.json').name}: not a JSON object")
        members = read_members(zip_bytes)
        mains = sorted((n for n in members if _is_main(n) is not None), key=lambda n: (_is_main(n), n))
        if not mains:
            raise UnindexableDocument(f"{zpath.name}: the zip holds no regelungstext-N.xml")
        try:
            main = ldml.parse_main(members[mains[0]])
            others = [(n, ldml.parse_main(members[n])) for n in mains[1:]]
            # every other XML member, by what its root IS (parser 2): an annex
            # (`anlage-regelungstext-N.xml`, a LegalDocML.de `doc`), the
            # `rechtsetzungsdokument` naming the parts (`documentCollection`),
            # or a root the grammar does not name here — each read and kept,
            # never skipped and never a reason to refuse the expression
            # (parser 1 read every one as an annex and refused the
            # rechtsetzungsdokument every real zip carries; München's probe)
            kinds = {n: ldml.kind_of(members[n]) for n in members if n not in mains and n.endswith(".xml")}
            annex_names = sorted((n for n, k in kinds.items() if k == "doc"), key=ldml.annex_order)
            annexes = [ldml.parse_annex(n, members[n]) for n in annex_names]
            collections_ = [ldml.parse_collection(n, members[n])
                            for n in sorted(n for n, k in kinds.items() if k == "collection")]
            other_xml = [{"member": n, "root": ldml.node(ldml._parse_xml(members[n]))}
                         for n in sorted(n for n, k in kinds.items() if k in ("act", "other"))]
        except ldml.LdmlError as e:
            raise UnindexableDocument(f"{zpath.name}: {e}") from e
        found: list[Issue] = []

        work = main.frbr.get("work", {})
        expr = main.frbr.get("expression", {})
        returned = str(expr.get("FRBRuri") or "")
        if returned and returned != expression:
            found.append(Issue("expression-eli-mismatch",
                               f"FRBRExpression {returned}, path {expression}"))
        eli_expression = returned or expression
        eli_work = str(work.get("FRBRuri") or work_of(expression))
        p = path_parts(expression)

        ris = main.ris
        tc_start, tc_end = coverage(api.get("temporalCoverage"))
        inkraft = ris.get("inkraft") or None
        ausserkraft = ris.get("ausserkraft") or None
        conditional = bool(ris.get("bedingtesInkrafttreten"))
        if tc_start and inkraft and tc_start != inkraft:
            found.append(Issue("stated-start-disagrees", f"temporalCoverage {tc_start}, ris:inkraft {inkraft}"))
        if tc_end and ausserkraft and tc_end != ausserkraft:
            found.append(Issue("stated-end-disagrees", f"temporalCoverage {tc_end}, ris:ausserkraft {ausserkraft}"))
        valid_from = tc_start or inkraft
        if not valid_from and not conditional:
            found.append(Issue("no-stated-start", f"{expression}: no temporalCoverage start, no ris:inkraft"))

        names = set(members)
        for ref in main.attachment_refs:
            want = str(ref.get("href") or "").rsplit("/", 1)[-1]
            if want and not any(n == want or n.endswith("/" + want) for n in names):
                found.append(Issue("annex-missing", f"{want} named by the act, not in {zpath.name}"))

        ex_work = api.get("exampleOfWork") if isinstance(api.get("exampleOfWork"), dict) else {}
        part_of = ex_work.get("isPartOf") if isinstance(ex_work.get("isPartOf"), dict) else {}
        abbreviation = str(api.get("abbreviation") or "").strip()
        ris_abbreviation = str(api.get("risAbbreviation") or "").strip()
        name = str(api.get("name") or "").strip()
        ausfertigung = next((e.get("date") for e in main.lifecycle
                             if e.get("refersTo") == "ausfertigung" and e.get("type") == "generation"), None)
        date_document = ex_work.get("legislationDate") or ausfertigung or None
        act = main.act

        extra = {
            "eli_jurisdiction": PROFILE.jurisdiction,
            "eli_work": eli_work,
            "eli_expression": eli_expression,
            "eli_type_document": main.regtxt.get("typ"),
            "eli_number": work.get("FRBRnumber"),
            "eli_date_document": date_document,
            "eli_date_publication": ex_work.get("datePublished") or (work.get("FRBRdate") or {}).get("date"),
            "eli_version": expr.get("FRBRversionNumber") or p["version"],
            "eli_version_date": p["pit"],
            "eli_title": name or act.doc_title,
            "eli_cites": act.cites,
            eli.IN_FORCE: _FORCE.get(str(api.get("legislationLegalForce") or "")),
            "point_in_time": p["pit"],
            "manifestation": generated,
            "valid_from": valid_from,
            "valid_to_stated": tc_end or ausserkraft,
            "conditional_entry_into_force": conditional or None,
            "temporal_coverage": api.get("temporalCoverage"),
            "legal_force": api.get("legislationLegalForce"),
            "name": name,
            "abbreviation": abbreviation,
            "ris_abbreviation": ris_abbreviation,
            "alternate_name": api.get("alternateName"),
            "gazette": part_of.get("name"),
            "api": api,
            "act_name": main.act_name,
            "meta": main.meta,
            "frbr": main.frbr,
            "lifecycle": main.lifecycle,
            "periods": main.periods,
            "ris": ris,
            "regtxt": main.regtxt,
            "proprietary_other": main.proprietary_other,
            "notes": main.notes,
            "analysis": main.analysis,
            "active_modifications": act.active_modifications,
            "passive_modifications": act.passive_modifications,
            "references": act.references,
            "preface": main.preface,
            "doc_title": act.doc_title,
            "preface_notes": act.preface_notes,
            "preamble": act.preamble,
            "conclusions": act.conclusions,
            "articles": main.articles,
            "article_notes": [{"article": a.eid or a.num, "notes": a.notes}
                              for a in act.articles if a.notes],
            "attachment_refs": main.attachment_refs,
            "annexes": [{k: v for k, v in {
                "member": an.member, "title": an.title, "doc_name": an.doc_name, "frbr": an.frbr,
                "meta": an.meta, "preface": an.preface, "notes": an.notes, "cites": an.cites,
                "has_text": any(b.text.strip() for b in an.blocks)}.items()
                if v not in (None, "", [], {})} for an in annexes],
            "rechtsetzungsdokument": collections_,
            "other_xml": other_xml,
            "other_texts": [{"member": n, "frbr": o.frbr, "meta": o.meta, "ris": o.ris,
                             "preface": o.preface, "articles": o.articles} for n, o in others],
            "members": [{"name": n, "size": len(b),
                         "blake2": hashlib.blake2b(b, digest_size=16).hexdigest()}
                        for n, b in sorted(members.items())],
        }
        sections = article_sections(act)
        for n, o in others:
            sections += [(f"{n.rsplit('/', 1)[-1]} › {label}", body)
                         for label, body in article_sections(o.act)]
        sections += [(an.title or an.member.rsplit("/", 1)[-1], _blocks_text(an.blocks))
                     for an in annexes]
        keys = {k for k in (eli_key(eli_work),) if k}
        keys |= {f"jurabk:{a}" for a in (abbreviation, ris_abbreviation) if a}
        extra = {k: v for k, v in extra.items() if v not in (None, "", [], {})}
        unknown = sorted(set(extra) - set(EXTRA_KEYS))
        if unknown:                                   # a key outside the fixed set is a defect here
            raise UnindexableDocument(f"extra keys outside EXTRA_KEYS: {unknown}")
        return CanonicalDoc(
            uid=f"{self.source_id}:{raw.ref.native_id}",
            title=" — ".join(x for x in (abbreviation, name or act.doc_title) if x),
            creators=[],
            language="de",
            date=date_document,
            subjects=[],
            keys=sorted(keys),
            extra=extra,
            sections=sections,
            raw_hash=raw.raw_hash,
            issues=found,
        )

    def relations(self, doc: CanonicalDoc):
        """06 §6.1's typed edges, by ELI property names (09 §9.14), between
        Works: `eli:repeals` for an act an active modification typed
        `repeal` strikes as a whole, `eli:amends` for one its other
        modifications touch; `eli:cites` for every act a `ris:verweis` names
        — by its Work ELI (`targetWorkEli1/2`), else by the abbreviation
        (`bezugsnorm*Jurabk`, `jurabk:` namespace) — and every `<ref>` to an
        ELI. `to` is a canonical id left unresolved (05 §5.5). One edge per
        (type, act)."""
        x = doc.extra or {}
        own = eli_key(str(x.get("eli_work") or ""))
        seen: set[tuple[str, str]] = set()

        def edge(kind: str, to: str | None):
            if to and to != own and (kind, to) not in seen:
                seen.add((kind, to))
                return Edge(to=to, type=kind)
            return None

        for mod in x.get("active_modifications") or []:
            target = modification_target(mod)
            if target is None:
                continue
            key, _, whole = target
            e = edge("eli:repeals" if whole and _is_repeal(mod) else "eli:amends", key)
            if e:
                yield e
        for v in (x.get("ris") or {}).get("verweise") or []:
            targets = [eli_key(v.get(k)) for k in ("targetWorkEli1", "targetWorkEli2") if v.get(k)]
            if not any(targets):
                targets = [f"jurabk:{str(v[k]).strip()}" for k in ("bezugsnorm1Jurabk", "bezugsnorm2Jurabk")
                           if str(v.get(k) or "").strip()]
            for t in targets:
                e = edge("eli:cites", t)
                if e:
                    yield e
        for href in x.get("eli_cites") or []:
            e = edge("eli:cites", eli_key(str(href)))
            if e:
                yield e

    def closures(self, doc: CanonicalDoc):
        """app/validity.py's hook (06 §6.1; normattiva's rule): the acts THIS
        version states it repeals in full — an active modification typed
        `repeal` whose destination is a whole act — from the day the
        destination's expression names or, where it names none, from this
        version's own start. Typed otherwise, or aimed at a part, it closes
        nothing (under-closing is labelled; over-closing mis-dates)."""
        x = doc.extra or {}
        start = str(x.get("valid_from") or "")
        seen: set[str] = set()
        for mod in x.get("active_modifications") or []:
            target = modification_target(mod)
            if target is None or not target[2] or not _is_repeal(mod):
                continue
            key, day, _ = target
            day = day or start
            if not day or key in seen:
                continue
            seen.add(key)
            yield key, day


_DEST = re.compile(r"(?P<work>eli/bund/[a-z0-9-]+/\d{4}/[A-Za-z0-9_-]+)"
                   r"(?:/(?P<pit>\d{4}-\d{2}-\d{2})/\d+/[a-z]{3}(?:/\d{4}-\d{2}-\d{2})?)?"
                   r"(?P<rest>/[^#]*)?(?P<frag>#.*)?$")


def modification_target(mod: dict) -> tuple[str, str | None, bool] | None:
    """What an active modification points at: `(the Work's canonical id, the
    day the destination expression names, whether it names the whole act)`.
    Whole: no fragment and nothing below the document (`regelungstext-N`,
    `.xml`); a destination into an article (`/art-z1`, `#…`) is a part."""
    href = str(mod.get("destination") or "").strip()
    i = href.find("eli/bund/")
    if i < 0:
        return None
    m = _DEST.match(href[i:])
    if m is None:
        return None
    rest = (m.group("rest") or "").strip("/")
    whole = not m.group("frag") and (not rest or re.fullmatch(r"regelungstext-\d+(?:\.xml)?", rest) is not None)
    return PROFILE.key(m.group("work")[len("eli/"):]), m.group("pit"), whole


def _is_repeal(mod: dict) -> bool:
    return str(mod.get("type") or "").strip().lower() == "repeal"
