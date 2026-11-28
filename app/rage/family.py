"""A COLLECTION'S FAMILY, for what RAGE applies to all of it (operator,
2026-11-28: "An annotation refers to the corpus of a collection, thus applies
to all sisters and daughters, correct?" — answered A, then "Add this rule to
RAGE"; docs/notes/rage.md §6).

The family of a collection is every collection id linked to it, in either
direction and at any depth, by

  * its Codex's lineage (09 §9.3): `continues` (a daughter's mother),
    `derived_from` (a sister's sister), `replaces.codex` (a corrective
    sister's predecessor);
  * a records carry (`carry.records_twin_of`): the collection whose records
    this one holds byte for byte, by a carry this peer proved;
  * a re-creation (case F, `recreate.predecessor_of`): the collection it
    re-creates, recorded beside it, since its Codex may change what a
    sister's may not (the parser) and names no lineage.

Ids the node no longer holds — a mother retired, the Codex a refit replaced —
stay links: an annotations collection may name one, and its sisters are
reached through it.

What it is used for: an annotations collection (02 §2.10) names ONE target by
its `collection_id`, and its claims are about RECORDS, by uid. A claim about a
record holds wherever the family holds that record; each claim attached is
still checked against the text of the record it lands on (`annotations.
state_of`, `current` or `stale` by its content hash), so a relative whose
record changed shows the claim as stale, never as a fact of new text.

STANDARD NOT ADOPTED, written down (CLAUDE.md): this is the connected
component of a lineage graph — W3C PROV's `wasDerivedFrom` closure, or Git's
reachability over parents. A union-find over a dozen held collections is the
whole of it here; PROV would pay once lineage is published and walked across
peers (09 §9.4's chain), not before.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


def parents(codex) -> list[str]:
    """The ids a Codex names as its lineage (09 §9.3), in a fixed order."""
    out = [getattr(codex, "continues", None), getattr(codex, "derived_from", None)]
    rep = getattr(codex, "replaces", None)
    out.append(getattr(rep, "codex", None) if rep is not None else None)
    return [i for i in out if i]


def _links(root: Path, held: Iterable) -> list[tuple[str, str]]:
    from app.hold import carry
    edges: list[tuple[str, str]] = []
    for c in held:
        cid = getattr(getattr(c, "codex", None), "collection_id", None)
        if not cid:
            continue
        for p in parents(c.codex):
            edges.append((cid, p))
        twin = carry.records_twin_of(Path(root) / c.name / "state")
        if twin:
            edges.append((cid, twin))
        # a re-creation's predecessor (case F, app/hold/recreate.py): no lineage
        # in its Codex — it may change the parser — so recorded beside it
        from app.hold import recreate
        was = recreate.predecessor_of(Path(root) / c.name / "state")
        if was:
            edges.append((cid, was))
    return edges


def family(root, collection_id: str | None, held: Iterable, *,
           extra: Iterable[tuple[str, str]] = ()) -> tuple[str, ...]:
    """Every id in `collection_id`'s family among the links of `held` (the
    collections this node holds, served and locked) and `extra`: itself
    first, then the rest sorted. `()` for no id."""
    if not collection_id:
        return ()
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in [*_links(root, held), *extra]:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    me = find(collection_id)
    rest = sorted(i for i in list(parent) if i != collection_id and find(i) == me)
    return (collection_id, *rest)


def held_collections() -> list:
    """The collections whose lineage counts: every one held here, served or
    locked (a locked sister still links her family), annotations aside."""
    from app import collections as collections_mod
    try:
        served = collections_mod.load_all(skip_unreadable=True)[0]
    except Exception:                                   # noqa: BLE001 — names are a courtesy, as display names
        served = []
    try:
        locked = [c for c in collections_mod.load_locked() if not collections_mod.is_annotations(c)]
    except Exception:                                   # noqa: BLE001
        locked = []
    return [*served, *locked]
