"""MetSource — CorpusSource over The Metropolitan Museum of Art's Open Access:
every object the Met's collection API lists, its record and its images
(docs/notes/image-collections.md §8–§12; operator, 2026-11-28: "Met Open
Access", "The collection carry only the metadata and the images").

ONE RECORD PER OBJECT, `met:<objectID>`, `record_line` 2: the object's
metadata is the record, its images are its figures (09 §9.5), carried as the
Met's own bytes and named by sha256. Nothing in the record is written by
anyone but the Met (CLAUDE.md, the fundamental rule): no description, no
caption made here. A description of the images is another collection's
claim (an annotations collection), never this one's.

THE ORIGIN FORM, and why — invariant 11 wants it recorded beside the
plugin, because a verifier needs it to reproduce our records. The Met serves
two forms, both probed 2026-11-28:

  * THE API JSON PER OBJECT (`/public/collection/v1/objects/<id>`), kept
    WHOLE as served, is the origin unit every record is parsed from and
    `origin_hash` covers. It holds every column the Open Access CSV holds
    and more: the constituents as a list (role, ULAN, Wikidata, gender), the
    structured `measurements`, the tags with their AAT and Wikidata URLs,
    `primaryImage` and `additionalImages` (the image files), `objectURL`.
    Holding the CSV alone would hold less than the origin gives (invariant
    11), and would hold no image at all. The operator chose the JSON for
    EVERY object, those without images too (2026-11-28, "1st option": their
    structured measurements are in no other form).
  * THE OPEN ACCESS CSV (`MetObjects.csv`, metmuseum/openaccess, CC0,
    341,135,952 B on 2026-11-28) is the bulk. It is fetched FIRST and held
    in the corpus beside the objects (app/acquire/met.py), and no record is
    parsed from it: one row per object, several artists joined by `|`, no
    image URLs, no structured measurements.
  * THE IMAGES are the files `primaryImage` and `additionalImages` name, the
    originals as images.metmuseum.org serves them (not `primaryImageSmall`,
    the 600-pixel rendition: 30× smaller, measured 2026-11-28, so holding it
    would hold less). Each lands under its own file name beside the JSON,
    and is a figure in the order the JSON states.

WHAT `origin_hash` COVERS: the JSON's bytes as served. The images are not in
it: each is named in the record by its own sha256 and verified by it (09
§9.5's figures), and the JSON names each image's URL, so a re-fetch of the
unit (`records-vs-origin`) reproduces both.

WHAT IS KEPT: every key of the JSON. `title` is §9.5's `title`; the
constituents' names are `creators`, in the JSON's order; the tags' terms are
`subjects`; `objectBeginDate` gives `date` where it is a year 1–9999 (a date
BCE is no `YYYY`: then `date` is null and the object's dates stay in
`extra`). Every other key is `extra.<its own name>`, as the API names it,
the ones mapped above included (the full constituents, the tags with their
URLs, `objectBeginDate`). A key the API adds later is kept in
`extra.unlisted` and said as an Issue, never dropped: `extra`'s keys are
fixed for a collection's life (09 §9.5).
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

from app import chunking
from app.acquire import met as acquire_met
from app.backend import OriginForm, Template
from ..base import (PROTOCOL_VERSION, CanonicalDoc, DiscoverState, DocRef, Erasure, Figure, Issue,
                    LicenseClass, Modality, Mutability, PrivacyClass, RawDoc, UnindexableDocument)

#: every key the API's object answer carries (probed 2026-11-28, object
#: 436535), except `objectID` (the native id) and `title` (§9.5's own key)
API_KEYS = (
    "GalleryNumber", "accessionNumber", "accessionYear", "additionalImages", "artistAlphaSort",
    "artistBeginDate", "artistDisplayBio", "artistDisplayName", "artistEndDate", "artistGender",
    "artistNationality", "artistPrefix", "artistRole", "artistSuffix", "artistULAN_URL",
    "artistWikidata_URL", "city", "classification", "constituents", "country", "county",
    "creditLine", "culture", "department", "dimensions", "dynasty", "excavation", "geographyType",
    "isHighlight", "isPublicDomain", "isTimelineWork", "linkResource", "locale", "locus",
    "measurements", "medium", "metadataDate", "objectBeginDate", "objectDate", "objectEndDate",
    "objectName", "objectURL", "objectWikidata_URL", "period", "portfolio", "primaryImage",
    "primaryImageSmall", "region", "reign", "repository", "rightsAndReproduction", "river", "state",
    "subregion", "tags",
)
#: 09 §9.5: `extra`'s keys, fixed for the collection's life — the API's, and
#: `unlisted` for a key it adds later
EXTRA_KEYS = API_KEYS + ("unlisted",)
_MAPPED = {"objectID", "title"}

ISSUES = {
    "unlisted-field": "the object's JSON carries a key this parser does not list; kept in extra.unlisted",
    "no-title": "the object's JSON states no title",
}

#: the labelled fields the record's one section is written from, in order:
#: what the text index and the encoder see. The Met's own words only.
_TEXT_FIELDS = (
    ("Object", "objectName"), ("Artist", "artistDisplayName"), ("Artist's life", "artistDisplayBio"),
    ("Date", "objectDate"), ("Culture", "culture"), ("Period", "period"), ("Dynasty", "dynasty"),
    ("Reign", "reign"), ("Medium", "medium"), ("Dimensions", "dimensions"),
    ("Classification", "classification"), ("Department", "department"), ("Portfolio", "portfolio"),
    ("Geography", "geographyType"), ("City", "city"), ("State", "state"), ("County", "county"),
    ("Country", "country"), ("Region", "region"), ("Subregion", "subregion"), ("Locale", "locale"),
    ("Locus", "locus"), ("Excavation", "excavation"), ("River", "river"),
    ("Credit line", "creditLine"),
)

_MAGIC = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"),
          (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
          (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff"))


def media_type_of(data: bytes) -> str:
    """The file's own type, from its first bytes — never from the URL."""
    for magic, mt in _MAGIC:
        if data.startswith(magic):
            return mt
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _clean(value) -> str:
    """09 §9.5's text: NFC, LF newlines, no trailing whitespace on a line."""
    text = unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def section_text(obj: dict) -> str:
    """The record's one section: the title, then `Label: value` per stated
    field, then the tags. A pure function of the JSON."""
    lines = []
    title = _clean(obj.get("title"))
    if title:
        lines.append(title)
    for label, key in _TEXT_FIELDS:
        v = _clean(obj.get(key))
        if v:
            lines.append(f"{label}: {v}")
    terms = [_clean(t.get("term")) for t in (obj.get("tags") or []) if isinstance(t, dict)]
    terms = [t for t in terms if t]
    if terms:
        lines.append("Tags: " + ", ".join(terms))
    return "\n".join(lines)


def creators_of(obj: dict) -> list[str]:
    names: list[str] = []
    for c in obj.get("constituents") or []:
        n = _clean(c.get("name")) if isinstance(c, dict) else ""
        if n and n not in names:
            names.append(n)
    return names


def date_of(obj: dict) -> str | None:
    y = obj.get("objectBeginDate")
    return f"{y:04d}" if isinstance(y, int) and 1 <= y <= 9999 else None


def origin_hash(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


class MetSource:
    source_id = "met"
    protocol_version = PROTOCOL_VERSION
    #: 1 (2026-11-28): the first; no collection made from it yet.
    PARSER_VERSION = 1
    ISSUES = ISSUES
    EXTRA_KEYS = EXTRA_KEYS
    #: CC0: the Open Access data and the public-domain images; each object's
    #: own `isPublicDomain` and `rightsAndReproduction` stay in its record
    license_class = LicenseClass.GREEN
    corpus_provider = "The Metropolitan Museum of Art"
    corpus_license = "CC0-1.0"
    corpus_attribution = "The Metropolitan Museum of Art, Open Access (CC0)"
    privacy_class = PrivacyClass.PUBLIC
    #: a short record: one chunk per object (the `abstract` chunker)
    chunking_profile = chunking.ABSTRACT
    #: the Met revises its records and re-photographs its objects
    mutability = Mutability.MUTABLE
    backend = (OriginForm("each object's API JSON and its image files", Template.MEMBER),)
    #: 06 §6.2: the API serves the current record only
    origin_versions = "replaces"
    modality = Modality.TEXT
    erasure = Erasure.RETRACTABLE          # an object the Met no longer lists
    native_id_order = "numeric"            # the Met's objectID
    default_facets = [
        {"field": "date", "kind": "range", "separator": None, "authority": None},
        {"field": "extra.classification", "kind": "flat", "separator": None, "authority": None},
        {"field": "extra.culture", "kind": "flat", "separator": None, "authority": None},
        {"field": "extra.department", "kind": "flat", "separator": None, "authority": None},
        {"field": "extra.isPublicDomain", "kind": "flat", "separator": None, "authority": None},
    ]

    # -- the corpus ----------------------------------------------------------

    def discover(self, state: DiscoverState):
        """Every object whose JSON has landed (the unit's completion mark,
        landed after its images), in objectID order."""
        root = Path(state.corpus_root) / "objects"
        if not root.is_dir():
            return
        found: dict[int, Path] = {}
        for bucket in root.iterdir():
            if not (bucket.is_dir() and bucket.name.isdigit()):
                continue
            for d in bucket.iterdir():
                mark = d / acquire_met.MARK
                if d.name.isdigit() and mark.is_file() and int(d.name) // 1000 == int(bucket.name):
                    found[int(d.name)] = mark
        for oid in sorted(found):
            st = found[oid].stat()
            yield DocRef(source_id=self.source_id, native_id=str(oid), locator=str(found[oid]),
                         mtime=int(st.st_mtime), size=st.st_size)

    def fetch(self, ref: DocRef) -> RawDoc:
        path = Path(ref.locator)
        try:
            data = path.read_bytes()
        except OSError as e:
            raise UnindexableDocument(f"{path.name}: {type(e).__name__}: {e}") from e
        return RawDoc(ref=ref, raw_hash=origin_hash(data), path=path, data=data)

    def parse(self, raw: RawDoc) -> CanonicalDoc:
        try:
            obj = json.loads(raw.data)
        except ValueError as e:
            raise UnindexableDocument(f"object {raw.ref.native_id}: not JSON ({e})") from e
        if not isinstance(obj, dict) or str(obj.get("objectID")) != str(raw.ref.native_id):
            raise UnindexableDocument(f"object {raw.ref.native_id}: the JSON is another object's")
        issues: list[Issue] = []
        extra = {k: obj.get(k) for k in API_KEYS}
        unlisted = {k: obj[k] for k in sorted(obj) if k not in API_KEYS and k not in _MAPPED}
        extra["unlisted"] = unlisted
        if unlisted:
            issues.append(Issue("unlisted-field", ", ".join(unlisted)))
        title = _clean(obj.get("title"))
        if not title:
            issues.append(Issue("no-title"))
        text = section_text(obj)
        return CanonicalDoc(
            uid=f"{self.source_id}:{raw.ref.native_id}",
            title=title,
            creators=creators_of(obj),
            language="und",
            date=date_of(obj),
            subjects=[_clean(t.get("term")) for t in (obj.get("tags") or [])
                      if isinstance(t, dict) and _clean(t.get("term"))],
            keys=[],
            extra=extra,
            sections=[("", text)] if text else [],
            raw_hash=raw.raw_hash,
            figures=self._figures(Path(raw.ref.locator).parent, obj),
            issues=issues,
        )

    def _figures(self, unit: Path, obj: dict) -> list[Figure]:
        """One figure per image file the JSON names, in its order. The unit
        landed whole (its JSON last), so an image missing here is a damaged
        corpus, refused — never a record naming a plate it cannot carry."""
        out: list[Figure] = []
        for seq, (_url, name) in enumerate(acquire_met.image_names(obj)):
            p = unit / name
            try:
                data = p.read_bytes()
            except OSError as e:
                raise UnindexableDocument(f"object {obj.get('objectID')}: image {name} not held "
                                          f"({type(e).__name__})") from e
            out.append(Figure(href=name, media_type=media_type_of(data),
                              sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                              caption="", seq=seq))
        return out

    def read_figures(self, path: Path, native_id: int, hrefs: dict[str, str]) -> dict[str, bytes]:
        """`{sha256: href}` -> `{sha256: bytes}` for one object (plugin_io)."""
        unit = Path(path).parent if Path(path).name == acquire_met.MARK else Path(path)
        out: dict[str, bytes] = {}
        for digest, href in hrefs.items():
            data = (unit / href).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"object {native_id}: {href} is not the image the record names")
            out[digest] = data
        return out
