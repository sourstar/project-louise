"""The facts an annotations collection attaches to a search's results
(05 §5.3, amended 2026-10-09; 02 §2.10; docs/notes/annotation-collections.md).

A result whose record is the subject or the object of a claim held here
carries, under `annotations`, the record at the claim's other end — its uid,
title and language as the target holds it — with the claim's type, method,
confidence and contributor, the annotations collection and its trust level,
and the claim's `state` against the text the answer was read from. It is a
FACT placed beside the result, of the kind a reference source's row or a
figure is: never scored, never a leg, never fused (invariant 4). A client
may group a work's versions by it; the lists are not reordered for it.

Which claims apply: those of an annotations collection this peer holds and
may use (a copy in quarantine attaches nothing and is named under
`withheld`, as any copy is — 05 §5.3), whose `annotates.collection_id` is
the result's collection OR ANY OF ITS FAMILY — its mother, sisters and
daughters at any depth, by lineage and by a records carry (app/rage/family.py,
operator 2026-11-28) — read from a target snapshot at or after
`annotates.from_snapshot`; each claim still judged against the record it lands
on (`state`: current, or stale where that record's text changed).

COST: the claims of a copy are indexed once per snapshot
(`annotations.index_of`, keyed on the SHARD's path); per search, one manifest
query per target collection over the uids the attached claims name. It
scales with the results and their claims, never with either collection.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from app.hold import annotations as ann

FIELD = "annotations"


def _held(collections=None) -> list:
    """The annotations collections consulted: `collections`, else every one
    held — never one hidden from the viewer being answered (07 §7.8
    `hidden`: its claims are not attached, shown or walked for them)."""
    from app import collections as collections_mod
    return collections_mod.seen(collections_mod.load_annotations() if collections is None
                                else collections)


def _withheld(a) -> str | None:
    from app import copy_state, licensing
    try:
        if licensing.state_for(a) == licensing.MISSING:
            return "license not acknowledged (invariant 9)"
    except Exception as e:   # noqa: BLE001 — fail closed, as every gate does
        return f"license state cannot be judged ({type(e).__name__}: {e})"
    try:
        return copy_state.withholds(a)
    except Exception as e:   # noqa: BLE001
        return None if not copy_state.enforced() else f"quarantine: state cannot be derived ({e})"


def _trust(root: Path, a) -> str:
    from app import copy_state, identity
    from app.hold import equivalence, trust_file
    from app.share import levels, seeder
    from app.hold import collection as col
    try:
        own = identity.own()
    except Exception:   # noqa: BLE001
        own = None
    try:
        snap = seeder.cached_snapshot(col.shard_dir_of(root, a.name))
        bodies = copy_state.attestations(root, a.name, a.codex)
        return levels.level(a.codex, snap, bodies, (), own_key=own,
                            trust=trust_file.as_filter(root.parent),
                            reference_images=equivalence.reference_images(bodies, own, a.codex))["level"]
    except Exception:   # noqa: BLE001 — a level that cannot be computed is the publisher's word
        return levels.UNVERIFIED


def _target_rows(collection, uids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """uid -> {title, lang, content_hash} for the uids the target's manifest
    holds: one indexed query (`uid` is the primary key)."""
    from app import db
    uids = sorted(set(uids))
    out: dict[str, dict[str, Any]] = {}
    if not uids:
        return out
    with closing(db.connect(collection)) as con:
        for i in range(0, len(uids), 500):
            batch = uids[i:i + 500]
            for r in con.execute(
                    f"SELECT uid, title, lang, content_hash FROM documents WHERE uid IN "
                    f"({','.join('?' * len(batch))})", batch):
                out[r[0]] = {"title": r[1], "lang": r[2], "content_hash": r[3]}
    return out


def answers_for(root, name: str, collection_id: str | None, held=None) -> tuple[str, ...]:
    """The collection ids an annotations target may name for the collection
    held as `name`: its FAMILY (app/rage/family.py; operator, 2026-11-28: "An
    annotation refers to the corpus of a collection, thus applies to all
    sisters and daughters" — A, "Add this rule to RAGE"): itself, its mother,
    its sisters and daughters, at any depth, by its lineage and by a records
    carry (`carry.records_twin_of`, 02 §2.4 A1f). Each claim attached is
    still judged against the record it lands on (`state_of`). `held`: the
    collections whose links count (default: every one held here)."""
    from app.hold import carry
    from app.rage import family
    twin = carry.records_twin_of(Path(root) / name / "state")
    extra = [(collection_id, twin)] if collection_id and twin else []
    ids = family.family(root, collection_id, family.held_collections() if held is None else held,
                        extra=extra)
    return ids or tuple(i for i in (twin,) if i)


def target_among(codex, ids: Iterable[str]):
    """The annotations Codex's target entry naming the first of `ids` it
    annotates, or None."""
    from app.hold.codex import target_for
    return next((t for i in ids if (t := target_for(codex, i)) is not None), None)


#: the hit's `language_stage` (05 §5.3, amended 2026-11-16)
STAGE_FIELD = "language_stage"
#: which method's claim is read where several speak of one record: a
#: person's over Wikidata's over the matcher's (09 §9.5)
STAGE_PRECEDENCE = {"human": 0, "wikidata": 1, "matcher": 2}


def _attach_stage(h: dict[str, Any], a, snapshot_id: str | None, claims) -> None:
    """The record's `language-stage` fact: the claim that wins (a person's
    first), `stage` null where it says "not an older stage". One per hit;
    a winner already attached from another collection is kept unless this
    one's method outranks it."""
    best = min(claims, key=lambda cl: (STAGE_PRECEDENCE.get(cl.claim["method"]["kind"], 9), cl.uid))
    kind = best.claim["method"]["kind"]
    have = h.get(STAGE_FIELD)
    if have is not None and STAGE_PRECEDENCE.get(have["method"], 9) <= STAGE_PRECEDENCE.get(kind, 9):
        return
    h[STAGE_FIELD] = {"stage": best.claim["object"], "method": kind,
                      "collection_id": a.codex.collection_id, "snapshot_id": snapshot_id,
                      "claim": best.uid, "confidence": best.claim["confidence"]}


def attach(lists: list[dict[str, Any]], source_of: dict[int, Any], contexts: dict[str, dict],
           *, root: Path, collections=None) -> list[dict[str, Any]]:
    """Every hit gets `annotations` — `[]` where no held claim names its
    record. Returns the annotations collections withheld, in `withheld`'s
    shape (05 §5.3)."""
    by_coll: dict[str, list[dict[str, Any]]] = {}
    for lst in lists:
        for h in lst["hits"]:
            h[FIELD] = []
            c = source_of.get(id(h))
            if c is not None:
                by_coll.setdefault(c.name, []).append(h)
    held = _held(collections)
    if not held or not by_coll:
        return []
    withheld, usable = [], []
    from app.rage import family
    relatives = family.held_collections()      # read once a search: a dozen files, cached Codices
    targets = {i for n in by_coll if n in contexts
               for i in answers_for(root, n, contexts[n]["collection_id"], relatives)}
    from app.hold.codex import targets_of
    for a in held:
        if not any(t.collection_id in targets for t in targets_of(a.codex)):
            continue                    # about nothing this search returned: not consulted
        why = _withheld(a)
        if why:
            withheld.append({"collection": a.name, "collection_id": a.codex.collection_id,
                             "shards": "all", "reason": f"annotations: {why}"})
        else:
            usable.append(a)
    root = Path(root)
    from app.hold import collection as col
    for name, hits in by_coll.items():
        ctx = contexts.get(name) or {}
        ids = answers_for(root, name, ctx.get("collection_id"), relatives)
        mine = [a for a in usable
                if (t := target_among(a.codex, ids)) is not None
                and ann.applies_from(t.from_snapshot, ctx.get("snapshot_id"))]
        if not mine:
            continue
        found = []
        for a in mine:
            idx = ann.index_of(col.shard_dir_of(root, a.name))
            trust = None
            stages = getattr(idx, "stages", None) or {}
            for h in hits:
                if stages.get(h["uid"]):
                    _attach_stage(h, a, idx.snapshot_id, stages[h["uid"]])
                for cl in idx.by_target.get(h["uid"], ()):
                    if trust is None:
                        trust = _trust(root, a)
                    found.append((h, a, idx.snapshot_id, cl, trust))
        if not found:
            continue
        rows = _target_rows(source_of[id(hits[0])],
                            {u for _, _, _, cl, _ in found for u in (cl.claim["subject"], cl.claim["object"])})
        now = {u: r.get("content_hash") for u, r in rows.items()}
        for h, a, snap_id, cl, trust in found:
            c = cl.claim
            role = "subject" if c["subject"] == h["uid"] else "object"
            other = c["object"] if role == "subject" else c["subject"]
            row = rows.get(other)
            h[FIELD].append({
                "collection_id": a.codex.collection_id, "snapshot_id": snap_id, "claim": cl.uid,
                "type": c["type"], "relation": ann.edge_type(c["type"]), "role": role,
                "record": {"uid": other, "title": row.get("title") if row else None,
                           "lang": row.get("lang") if row else None, "held": row is not None},
                "subject_lang": c["subject_lang"], "object_lang": c["object_lang"],
                "method": c["method"], "confidence": c["confidence"],
                "contributor": c["contributor"], "state": ann.state_of(c, now),
                "trust_level": trust})
    return withheld
