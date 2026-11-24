"""The act's facts read from its NON-INFERRED TREE (rdf.py) — the CDM's own
assertions, the branch notice's source — instead of from the notice (parser
4, 2026-11-15).

WHY. Operator, 2026-11-15, B: "Build every in-force act, in all 24
languages, from the Data Dump alone": no Cellar request, so no notice (the
dump holds none). The dump's MTD holds each work's tree, the graph Cellar
serves as `notice=non-inferred-tree` (München's three Italian units: the
held tree and the MTD's, both scoped, are one canonical JSON), and the notice
is that graph PLUS what the notice service adds from elsewhere. Measured on
31972R1473 and 31978A0060 (every leaf value of the notice looked up in the
scoped tree), the notice adds only:
  * other works' assertions about this one — the inverse links
    (`…_CONSOLIDATED_BY_…`, `…_CORRECTED_BY_…`) and the EMBEDDED_NOTICEs of
    the linked works (consolidations, OJ issues): each held in that work's
    own unit and record;
  * the other languages' expressions: each in its own language's unit;
  * the vocabularies' labels and hierarchy (EuroVoc's PREFLABEL, the
    directory code's levels): the concepts' information, not the act's — the
    act's assertion is "is about <concept>", which the tree holds;
  * the items' stream metadata (STREAM_SIZE, STREAM_ORDER, the techmd
    label): the size is the held file's own; the name is in the tree (an
    item's `owl:sameAs`, `celex/<CELEX>.<LANG>.<form>.<name>`).
So a unit of the tree and the items holds every assertion the origin makes
about the act (invariant 11), and the record reads it from there.

SUBJECTS AND AUTHORS ARE CONCEPT IDS (`eurovoc:1073`, `subject-matter:TRAN`,
`corporate-body:CONSIL`), not the notice's labels: the label is the
vocabulary's, in the vocabulary's version. Labels in the reader's language
are the vocabulary's to give, by key, as a reference source does (05 §5.2):
EuroVoc and the Office's authority tables are published whole (OWED: that
reference source, from their SKOS dumps — bulk first).

THE STANDARD, NOT ADOPTED: a SPARQL engine over the graph (rdflib's). The
CDM's properties read here are ~20 direct ones on one work, its expression
and its items; a query engine is a dependency for that (CLAUDE.md: ask
first), and would pay if a record ever needed paths across works.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote

from . import rdf
from .notice import Item, Notice

CDM = rdf.CDM
RDF_TYPE = rdf.RDF + "type"
SAME = rdf.OWL + "sameAs"
CMR_MODIFIED = "http://publications.europa.eu/ontology/cdm/cmr#lastModificationDate"
_RESOURCE = "http://publications.europa.eu/resource/"
_CELEX_URI = _RESOURCE + "celex/"
_AUTHORITY = _RESOURCE + "authority/"
_EUROVOC = "http://eurovoc.europa.eu/"


class TreeError(ValueError):
    """The tree names no work, or another act than the unit's."""


def _vals(props: dict, local: str) -> list[str]:
    """The literal values of `cdm:<local>`, stripped, in the tree's order."""
    out = []
    for v in props.get(CDM + local) or ():
        s = (v.get("@value") if "@value" in v else v.get("@id")) or ""
        s = s.strip()
        if s and s not in out:
            out.append(s)
    return out


def _first(props: dict, local: str) -> str | None:
    got = _vals(props, local)
    return got[0] if got else None


def _ids(props: dict, prop: str) -> list[str]:
    return [v["@id"] for v in props.get(prop) or () if "@id" in v]


def celex_of(uri: str) -> str | None:
    """`…/resource/celex/31968R0259%2801%29` → `31968R0259(01)` (and
    `11965F%2FTXT` → `11965F/TXT`, as the notice names it); an expression's or
    a manifestation's (`….ITA`, `….ITA.fmx4`) is no work's."""
    if not uri.startswith(_CELEX_URI):
        return None
    raw = uri[len(_CELEX_URI):]
    c = unquote(raw)
    return c if c and "." not in c and "/" not in raw else None


def concept_id(uri: str) -> str:
    """A concept's key: `eurovoc:1073`, `subject-matter:TRAN`,
    `dir-eu-legal-act:07203020`, `corporate-body:CONSIL`; any other URI as
    it is."""
    if uri.startswith(_EUROVOC):
        return "eurovoc:" + uri[len(_EUROVOC):].strip("/")
    if uri.startswith(_AUTHORITY):
        table, _, code = uri[len(_AUTHORITY):].strip("/").partition("/")
        if table and code:
            return f"{table}:{code}"
    return uri


def _latest(values: list[str]) -> str | None:
    def at(v: str):
        try:
            t = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return t if t.tzinfo is not None else None
    dated = [(at(v), v) for v in values]
    dated = [(t, v) for t, v in dated if t is not None]
    return max(dated)[1] if dated else (values[0] if values else None)


def _work(group_props: dict[str, dict], celex: str | None) -> tuple[str, dict]:
    works = sorted((g, p) for g, p in group_props.items() if p.get(CDM + "resource_legal_id_celex"))
    if celex is not None:
        mine = [(g, p) for g, p in works if celex in _vals(p, "resource_legal_id_celex")]
        if mine:
            return mine[0]
        raise TreeError(f"the tree states no work of CELEX {celex}"
                        + (f" (it states {', '.join(_vals(works[0][1], 'resource_legal_id_celex'))})"
                           if works else ""))
    if not works:
        raise TreeError("the tree states no work (no cdm:resource_legal_id_celex)")
    return works[0]


def notice(tree: dict[str, Any], lang3: str, celex: str | None = None) -> Notice:
    """The fields the record reads (notice.Notice's), from a tree scoped to
    `lang3` (rdf.scope). `celex`: the unit's; the tree must state that work."""
    find, gp = rdf.groups(tree)
    wg, work = _work(gp, celex)
    lang_uri = rdf.LANGUAGE + lang3
    expr = next(((g, p) for g, p in sorted(gp.items())
                 if lang_uri in _ids(p, CDM + "expression_uses_language")
                 and any(find(w) == wg for w in _ids(p, CDM + "expression_belongs_to_work"))), None)
    own = _first(work, "resource_legal_id_celex")
    relations: list[tuple[str, str]] = []
    for prop in sorted(work):
        if not prop.startswith(CDM):
            continue
        for uri in _ids(work, prop):
            c = celex_of(uri)
            if c and c != own and (prop[len(CDM):].upper(), c) not in relations:
                relations.append((prop[len(CDM):].upper(), c))
    subjects = [concept_id(u) for prop in sorted(work) if prop.startswith(CDM) and "is_about" in prop
                for u in _ids(work, prop)]
    items: dict[str, list[Item]] = {}
    stamps = [v.get("@value", "") for v in work.get(CMR_MODIFIED) or ()]
    if expr is not None:
        eg = expr[0]
        manifs = {g: p for g, p in gp.items()
                  if any(find(e) == eg for e in _ids(p, CDM + "manifestation_manifests_expression"))}
        # the unit's own modification: its expression's and manifestations' too
        for props in [expr[1], *manifs.values()]:
            stamps += [v.get("@value", "") for v in props.get(CMR_MODIFIED) or ()]
        for mg, mp in sorted(manifs.items()):
            form = _first(mp, "manifestation_type") or ""
            listed = items.setdefault(form, [])
            marker = f".{lang3}.{form}."
            for ig, ip in sorted(gp.items()):
                if not any(find(m) == mg for m in _ids(ip, CDM + "item_belongs_to_manifestation")):
                    continue
                uris = [ig] + _ids(ip, SAME)
                cellar = next((u for u in uris if "/resource/cellar/" in u), ig)
                name = next((unquote(u.split(marker, 1)[1]) for u in uris if marker in u), None)
                listed.append(Item(uri=cellar, name=name, size=None, order=None))
    in_force = {"true": True, "false": False, "1": True, "0": False}.get(
        (_first(work, "resource_legal_in-force") or "").lower())
    base = next((c for c in map(celex_of, _ids(work, CDM + "act_consolidated_based_on_resource_legal")) if c),
                None)
    return Notice(
        kind="tree", decoding=None, celex=own, eli=_first(work, "resource_legal_eli"),
        sector=_first(work, "resource_legal_id_sector"), resource_type=_first(work, "resource_legal_type"),
        year=_first(work, "resource_legal_year"), number=_first(work, "resource_legal_number_natural_celex"),
        date_document=_first(work, "work_date_document"),
        entry_into_force=sorted(set(_vals(work, "resource_legal_date_entry-into-force"))),
        end_of_validity=sorted(set(_vals(work, "resource_legal_date_end-of-validity"))),
        in_force=in_force, base_celex=base,
        display_language=lang3 if expr is not None else None,
        expression_title=_first(expr[1], "expression_title") if expr is not None else None,
        last_modified=_latest([v.get("@value", "") for v in work.get(CMR_MODIFIED) or ()]),
        date_publication=_first(work, "date_publication"),
        subjects=list(dict.fromkeys(subjects)),
        creators=list(dict.fromkeys(concept_id(u) for u in _ids(work, CDM + "work_created_by_agent"))),
        relations=relations, items={f: v for f, v in items.items() if f}, whole=None,
        modified=_latest([v for v in stamps if v]))


def work_day(tree: dict[str, Any], celex: str | None = None) -> str | None:
    """The work's own day, as notice.work_day reads it: its date of
    publication, else the date of the document."""
    _find, gp = rdf.groups(tree)
    try:
        _g, work = _work(gp, celex)
    except TreeError:
        return None
    return _first(work, "date_publication") or _first(work, "work_date_document")
