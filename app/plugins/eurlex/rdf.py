"""Cellar's NON-INFERRED TREE (RDF/XML), read into a canonical form and
scoped to one language (invariant 11; München, 2026-11-14).

WHY. The CDM branch notice (notice.py) is what the record was built from,
and München compared it, triple by triple, with the work's non-inferred
tree over 1,698 Italian acts: 26,607 asserted triples are in the tree and
not in the notice (the OJ information number, the legal basis as printed,
the corrigendum flag, the embargo, an alternative ELI, EEA relevance, the
codified-version flag, signatories, "modifies", annotations…). The record
must hold them: `Accept: application/rdf+xml;notice=non-inferred-tree` on
`/resource/celex/{CELEX}` serves the tree (the Data Dump's MTD, the same
graph; its serialisation order is not stable, so it is compared as a graph,
never by its bytes).

THE FORM CELLAR WRITES, and the only one read (anything else is refused, so
nothing is lost by a reader that does not understand it): Jena's flat
RDF/XML — `rdf:RDF` of `rdf:Description` elements, each `rdf:about` a URI or
`rdf:nodeID` a blank node; each property an element whose value is
`rdf:resource`, a literal (`rdf:datatype`, `xml:lang`) or
`rdf:parseType="Literal"` (an XML literal). The blank nodes are `owl:Axiom`
annotations; their labels change from fetch to fetch, so an axiom is kept
by its CONTENT, never by its label.

THE SCOPE: a unit is one language's expression of a work. The tree is the
whole work, every language. Kept: every node but another language's
expression, its manifestations and their items (grouped through
`owl:sameAs`), and the axioms annotating those. Each of those belongs to its
own language's unit, so across the slices the whole tree is held (invariant
11: a slice is a part of the collection, never a loss), and this is the rule
written before it applies ("nothing is pruned unless written").

THE STANDARD, NOT ADOPTED: rdflib (an RDF/XML parser and graph
isomorphism). It would be a new dependency (CLAUDE.md: ask first), and
Cellar's flat form needs ~100 lines of the standard library. Adopting it
would pay if Cellar's writer changes form, which `read` refuses loudly.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
OWL = "http://www.w3.org/2002/07/owl#"
XML = "http://www.w3.org/XML/1998/namespace"
CDM = "http://publications.europa.eu/ontology/cdm#"
LANGUAGE = "http://publications.europa.eu/resource/authority/language/"
#: the unit's file holding the scoped tree, canonical JSON
TREE_FILE = "tree.json"


class RdfError(ValueError):
    """Not the flat RDF/XML Cellar writes: refused, never half-read."""


def _uri(tag: str) -> str:
    if not tag.startswith("{"):
        raise RdfError(f"a property without a namespace: {tag!r}")
    ns, _, local = tag[1:].partition("}")
    return ns + local


def _value(p: ET.Element) -> dict[str, str]:
    res = p.attrib.get(f"{{{RDF}}}resource")
    if res is not None:
        return {"@id": res}
    node = p.attrib.get(f"{{{RDF}}}nodeID")
    if node is not None:
        raise RdfError(f"a property pointing at a blank node ({node}): not Cellar's flat form")
    if p.attrib.get(f"{{{RDF}}}parseType") == "Literal":
        inner = (p.text or "") + "".join(ET.tostring(c, encoding="unicode") for c in p)
        return {"@value": inner, "@type": RDF + "XMLLiteral"}
    if len(p):
        raise RdfError(f"{p.tag}: a nested description: not Cellar's flat form")
    out = {"@value": p.text or ""}
    dt = p.attrib.get(f"{{{RDF}}}datatype")
    if dt:
        out["@type"] = dt
    lang = p.attrib.get(f"{{{XML}}}lang")
    if lang:
        out["@lang"] = lang
    known = {f"{{{RDF}}}datatype", f"{{{XML}}}lang"}
    other = set(p.attrib) - known
    if other:
        raise RdfError(f"{p.tag}: unknown attributes {sorted(other)}")
    return out


def _canon(props: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Properties sorted, values sorted by their JSON: one graph, one form."""
    return {k: sorted(v, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
            for k, v in sorted(props.items())}


def read(data: bytes) -> dict[str, Any]:
    """`{"nodes": {uri: {property: [value…]}}, "axioms": [{property: [value…]}…]}`,
    canonical: two fetches of one graph give one JSON."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise RdfError(f"not XML: {e}") from None
    if root.tag != f"{{{RDF}}}RDF":
        raise RdfError(f"the root is {root.tag}, not rdf:RDF")
    nodes: dict[str, dict[str, list[dict]]] = {}
    axioms: dict[str, dict[str, list[dict]]] = {}
    for d in root:
        if d.tag != f"{{{RDF}}}Description":
            raise RdfError(f"{d.tag}: only rdf:Description at the top (Cellar's flat form)")
        about = d.attrib.get(f"{{{RDF}}}about")
        node = d.attrib.get(f"{{{RDF}}}nodeID")
        if (about is None) == (node is None):
            raise RdfError("a description with neither or both of rdf:about and rdf:nodeID")
        target = nodes.setdefault(about, {}) if about is not None else axioms.setdefault(node, {})
        for p in d:
            target.setdefault(_uri(p.tag), []).append(_value(p))
    canon_axioms = [_canon(a) for a in axioms.values()]
    canon_axioms.sort(key=lambda a: json.dumps(a, sort_keys=True, ensure_ascii=False))
    return {"nodes": {u: _canon(p) for u, p in sorted(nodes.items())}, "axioms": canon_axioms}


def _ids(values: list[dict] | None) -> list[str]:
    return [v["@id"] for v in values or () if "@id" in v]


def scope(tree: dict[str, Any], lang3: str) -> dict[str, Any]:
    """The tree without another language's expression, its manifestations
    and items, and the axioms on them (module docstring)."""
    nodes: dict[str, dict] = tree["nodes"]
    # owl:sameAs groups (union-find over every node and every sameAs target)
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for u, props in nodes.items():
        find(u)
        for o in _ids(props.get(OWL + "sameAs")):
            parent[find(o)] = find(u)
    group_props: dict[str, dict[str, list[dict]]] = {}
    for u, props in nodes.items():
        g = group_props.setdefault(find(u), {})
        for k, v in props.items():
            g.setdefault(k, []).extend(v)

    # by the LINKS, not the declared type: a print manifestation carries no
    # rdf:type in the tree (31990R0048's BUL…SWE `.print`), so the type would
    # keep it in every language's unit
    other_expr = {g for g, p in group_props.items()
                  if _ids(p.get(CDM + "expression_uses_language"))
                  and LANGUAGE + lang3 not in _ids(p.get(CDM + "expression_uses_language"))}
    other_manif = {g for g, p in group_props.items()
                   if any(find(e) in other_expr for e in _ids(p.get(CDM + "manifestation_manifests_expression")))}
    # a manifestation part of another language's manifestation is that language's too
    grew = True
    while grew:
        more = {g for g, p in group_props.items() if g not in other_manif
                and any(find(m) in other_manif for m in _ids(p.get(CDM + "manifestation_part_of_manifestation")))}
        other_manif |= more
        grew = bool(more)
    other_item = {g for g, p in group_props.items()
                  if any(find(m) in other_manif for m in _ids(p.get(CDM + "item_belongs_to_manifestation")))}
    out_groups = other_expr | other_manif | other_item
    kept_nodes = {u: p for u, p in nodes.items() if find(u) not in out_groups}
    kept_axioms = [a for a in tree["axioms"]
                   if not any(find(s) in out_groups for s in _ids(a.get(OWL + "annotatedSource")))]
    return {"nodes": kept_nodes, "axioms": kept_axioms, "language": lang3}


def dumps(scoped: dict[str, Any]) -> bytes:
    """The unit's `tree.json`: canonical, so a re-fetch compares by bytes."""
    return (json.dumps(scoped, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def loads(data: bytes) -> dict[str, Any]:
    return json.loads(data.decode("utf-8"))
