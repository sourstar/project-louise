"""EuroVoc and the Publications Office's authority tables as a REFERENCE
SOURCE (06 §6.5): the labels of the concept ids an EUR-Lex record carries
(parser 4, app/plugins/eurlex/from_tree.py: `eurovoc:1073`,
`subject-matter:TRAN`, `dir-eu-legal-act:07203020`, `corporate-body:CONSIL`).

WHY A REFERENCE AND NOT THE RECORD. The record holds what the act's tree
asserts: "is about <concept>". The label is the vocabulary's, in the
vocabulary's release, and in thirty languages; the reader's language picks
one. So labels are facts by key (invariant 4: never a ranking), resolved
where a page or an index needs them (`labels`), never written into a record
— a new EuroVoc release changes no record.

THE ORIGIN FORM, and why (invariant 11; München's measures, 2026-11-16,
/mnt/tank/louise-measure/euvoc-2026-11-16, fixtures in tests/fixtures/eurovoc):
  * SKOS CORE, not SKOS AP ACT nor SKOS-XL: the core file holds every
    concept with its `prefLabel` and `altLabel` per language — what a label
    is — in 60.7 MB for EuroVoc 4.24 (7,536 concepts, 205,472 labels, 30
    languages) against 497 MB for AP ACT, whose extra is the Office's
    application profile (labels as resources, change notes), not labels.
  * The four files: `eurovoc_in_skos_core_concepts.rdf` (EuroVoc 4.24),
    `corporatebodies-skos.rdf` and `subject-matter-skos.rdf` (20260923-0),
    `dir-eu-legal-act-skos.rdf` (20171004-0), laid under
    `<ref_root>/eurovoc/` as downloaded (no conversion).
  * Two shapes, both read: EuroVoc's `rdf:Description` with `rdf:type
    skos:Concept`, and the authority tables' `skos:Concept` elements. A
    concept's key is its URI read by `from_tree.concept_id`, the function the
    records' ids come from, so the two agree by construction.
  * Bulk first: the files are whole releases; a new one is found through
    the dataset's DCAT record (data.europa.eu, `version_info`), at most once a
    day (OWED: that check; today the files are laid by hand).

LICENCE (invariant 9). Every distribution names the European Commission
reuse notice (Decision 2011/833/EU, `COM_REUSE`): reuse is authorised with
the source acknowledged — a condition, so YELLOW, as EUR-Lex (operator,
2026-11-08, A). Nothing is synced before an acknowledgement of its terms text
is recorded (`ack.json` beside the files: a timestamp and the sha256 of
`TERMS.txt`, the notice's bytes as the Office publishes them). The terms
bytes are OWED from the node; until they are laid, `held_back` says so and
the table stays empty — the records keep their ids, nothing breaks.

THE STANDARD, NOT ADOPTED: rdflib would read any RDF serialisation; the two
shapes the Office writes are flat and streamed here with the standard
library (ElementTree.iterparse) in bounded memory. Adopting it pays if the
Office changes its serialisation, which `read` then refuses loudly by
counting nothing.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Iterator

from ..plugins.base import LicenseClass, RefColumn, RefRecordBatch, RefSchema, RefSyncState, RefTable

SOURCE_ID = "eurovoc"
DIR = "eurovoc"
#: whom the labels are from, and the credit the reuse notice asks (operator,
#: 2026-11-16: "I accept the European Commission reuse notice, add the
#: credits"): on every result and card of a collection whose subjects are
#: EuroVoc's (app/credits.py)
PROVIDER = "Publications Office of the European Union — EuroVoc and the EU authority tables"
ATTRIBUTION = ("Subject and author labels: EuroVoc and the EU authority tables, © European Union, "
               "Publications Office of the European Union; reuse authorised under Commission "
               "Decision 2011/833/EU")
TERMS = "TERMS.txt"
ACK = "ack.json"
FILES = ("eurovoc_in_skos_core_concepts.rdf", "corporatebodies-skos.rdf", "subject-matter-skos.rdf",
         "dir-eu-legal-act-skos.rdf")

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_KINDS = {f"{{{SKOS}}}prefLabel": "pref", f"{{{SKOS}}}altLabel": "alt"}
_BATCH = 5_000

LABEL = RefTable(
    name="concept_label",
    columns=(
        RefColumn("id", "TEXT"),          # <key>|<lang>|<kind>|<n>
        RefColumn("key", "TEXT"),         # eurovoc:1073, corporate-body:CONSIL
        RefColumn("lang", "TEXT"),        # as the file writes it: it, en, pt …
        RefColumn("kind", "TEXT"),        # pref | alt
        RefColumn("label", "TEXT"),
    ),
    primary_key="id",
    indexes=("key",),
)


def read(path: Path) -> Iterator[dict]:
    """Every label of every concept in one SKOS file, streamed: rows of
    `LABEL`. A concept is a `skos:Concept` element or an `rdf:Description`
    typed `skos:Concept`; anything else (schemes, collections) is passed."""
    from ..plugins.eurlex.from_tree import concept_id
    concept = f"{{{SKOS}}}Concept"
    desc = f"{{{RDF}}}Description"
    rtype = f"{{{RDF}}}type"
    about = f"{{{RDF}}}about"
    resource = f"{{{RDF}}}resource"
    depth = 0
    for event, el in ET.iterparse(str(path), events=("start", "end")):
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth != 1:                     # a child of rdf:RDF, whole
            continue
        typed = el.tag == concept or (el.tag == desc and any(
            c.tag == rtype and c.attrib.get(resource) == concept[1:].replace("}", "") for c in el))
        uri = el.attrib.get(about)
        if typed and uri:
            key = concept_id(uri)
            seen: dict[tuple[str, str], int] = {}
            for c in el:
                kind = _KINDS.get(c.tag)
                text = (c.text or "").strip()
                if kind is None or not text:
                    continue
                lang = str(c.attrib.get(XML_LANG) or "").lower()
                n = seen.get((lang, kind), 0)
                seen[(lang, kind)] = n + 1
                yield {"id": f"{key}|{lang}|{kind}|{n}", "key": key, "lang": lang, "kind": kind,
                       "label": text}
        el.clear()


def terms_sha256(base: Path) -> str | None:
    p = base / TERMS
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def held_back(base: Path) -> str | None:
    """Why the labels are not synced, or None (the module docstring)."""
    digest = terms_sha256(base)
    if digest is None:
        return (f"{base}: no {TERMS} — the Commission reuse notice's terms (Decision 2011/833/EU), "
                f"byte-exact, are owed before the labels are used (invariant 9)")
    try:
        ack = json.loads((base / ACK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ack = {}
    if str(ack.get("terms_sha256") or "").replace("sha256:", "") != digest:
        return (f"{base}: the terms in {TERMS} are not acknowledged (louise dev refs-sync --src "
                f"<refs dir> --ack eurovoc), so the labels are not synced (invariant 9)")
    return None


def make_ack(base: Path, now: str) -> dict:
    digest = terms_sha256(base)
    if digest is None:
        raise FileNotFoundError(f"{base / TERMS}: no terms text to acknowledge")
    ack = {"at": now, "terms_sha256": f"sha256:{digest}"}
    (base / ACK).write_text(json.dumps(ack, sort_keys=True) + "\n", encoding="utf-8")
    return ack


class EurovocSource:
    source_id = SOURCE_ID
    ref_version = 1
    license_class = LicenseClass.YELLOW

    def schema(self) -> RefSchema:
        return RefSchema(source_id=self.source_id, tables=(LABEL,))

    def input_mtime(self, state: RefSyncState) -> float:
        """The newest of the files, the terms and the acknowledgement: a sync
        held back runs again once the terms are laid or acknowledged
        (refs/store.py `sync_source`)."""
        base = Path(state.ref_root) / DIR
        return max((p.stat().st_mtime for p in base.iterdir() if p.is_file()), default=0.0) \
            if base.is_dir() else 0.0

    def sync(self, state: RefSyncState) -> Iterator[RefRecordBatch]:
        base = Path(state.ref_root) / DIR
        if not base.is_dir() or held_back(base) is not None:
            return
        rows: list[dict] = []
        for name in FILES:
            path = base / name
            if not path.is_file():
                continue
            for row in read(path):
                rows.append(row)
                if len(rows) >= _BATCH:
                    yield RefRecordBatch(table=LABEL.name, rows=tuple(rows))
                    rows = []
        if rows:
            yield RefRecordBatch(table=LABEL.name, rows=tuple(rows))


def labels(store, keys: Iterable[str], lang: str, *, fallback: str = "en") -> dict[str, str]:
    """`{key: its preferred label in lang}` for the keys the vocabularies
    hold, in `fallback` where `lang` has none; a key they do not hold is
    absent (the caller shows the id). Bounded by the keys asked."""
    want = sorted({str(k) for k in keys if k})
    out: dict[str, str] = {}
    if not want or LABEL.name not in store.tables():
        return out
    con = store.con
    for i in range(0, len(want), 500):
        part = want[i:i + 500]
        marks = ",".join("?" for _ in part)
        got = con.execute(f"SELECT key, lang, label FROM {LABEL.name} WHERE key IN ({marks}) AND kind='pref' "
                          f"AND lang IN (?, ?) ORDER BY id", [*part, lang.lower(), fallback.lower()]).fetchall()
        best: dict[str, tuple[int, str]] = {}
        for r in got:
            rank = 0 if r[1] == lang.lower() else 1
            if r[0] not in best or rank < best[r[0]][0]:
                best[r[0]] = (rank, r[2])
        out.update({k: v for k, (_r, v) in best.items()})
    return out
