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

Which claims apply (02 §2.10 and 05 §5.3, amended 2026-11-11, architecture
session with the operator): those of an annotations collection this peer
holds and may use (a copy in quarantine attaches nothing and is named under
`withheld`, as any copy is — 05 §5.3) about a record of the result's
collection, decided PER CLAIM BY HASH, never by comparing collections: the
claim's ends name their records by uid, whose prefix is the plugin
(09 §9.3), so a claim about `normattiva:7` is looked up for a result
`normattiva:7` of ANY held normattiva collection — the one `annotates`
names, a sister holding the same records (02 §2.7), or that sister alone on
a peer that never held the target's Codex — and its `state` is the result's
collection's own content hashes against `subject_hash` / `object_hash`:
`current` where the record is the one the claim was made about, `stale`
where it has moved. That comparison is the one this module made before the
amendment; no Codex field, no sister declaration and no whole-collection
comparison is read. Where the result's collection IS a target `annotates`
names (or its carry twin, whose snapshots are the target's chain), the
answer must be read from a snapshot at or after that target's
`from_snapshot`; elsewhere the ids are another collection's cadence
(09 §9.4) and the hash alone decides.

COST: the claims of a copy are indexed once per snapshot
(`annotations.index_of`, keyed on the SHARD's path); per search, one manifest
query per result collection over the uids the attached claims name. It
scales with the results and their claims, never with either collection. The
amendment adds no query: a copy whose claims name none of the results'
plugins (`ClaimIndex.plugins`) is not consulted, and one that does is
consulted by the same uid lookups as before (tools/measure/annotation_read.py
times it).
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.hold import annotations as ann

FIELD = "annotations"


def _held(collections=None) -> list:
    if collections is not None:
        return list(collections)
    from app import collections as collections_mod
    return collections_mod.load_annotations()


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


def answers_for(root, name: str, collection_id: str | None) -> tuple[str, ...]:
    """The collection ids an annotations target may name for the collection
    held as `name`: its own, and the one a same-records carry brought its
    records from (`carry.records_twin_of`, its own `state/carry.json`) —
    claims about those records are claims about these (02 §2.4 A1f)."""
    from app.hold import carry
    twin = carry.records_twin_of(Path(root) / name / "state")
    return tuple(dict.fromkeys(i for i in (collection_id, twin) if i))


def target_among(codex, ids: Iterable[str]):
    """The annotations Codex's target entry naming the first of `ids` it
    annotates, or None."""
    from app.hold.codex import target_for
    return next((t for i in ids if (t := target_for(codex, i)) is not None), None)


def plugins_of(root: Path, a) -> frozenset[str] | None:
    """The plugins whose records `a`'s claims name (`ClaimIndex.plugins`),
    from its index of the current snapshot; None where the index cannot say
    (a disk index built before 2026-11-11): its lookups then decide."""
    from app.hold import collection as col
    try:
        idx = ann.index_of(col.shard_dir_of(Path(root), a.name))
    except Exception:   # noqa: BLE001 — unreadable here: judged by `annotates` alone
        return frozenset()
    return getattr(idx, "plugins", None)


def is_about(root: Path, a, ids: Iterable[str], plugins: Iterable[str]) -> bool:
    """Can `a`'s claims name a record of a collection whose ids (its own,
    its carry twin's) are `ids` and whose records are of `plugins`? Yes when
    `annotates` names one of the ids, or when its claims name the plugin
    (02 §2.10, amended 2026-11-11: any held collection of the plugin, each
    claim then decided by hash)."""
    from app.hold.codex import targets_of
    ids = set(ids)
    if any(t.collection_id in ids for t in targets_of(a.codex)):
        return True
    named = plugins_of(root, a)
    return named is None or bool(named & set(plugins))


def applicable(root: Path, usable: Iterable, ids: Sequence[str], plugins: Iterable[str],
               read_snapshot: str | None) -> list:
    """The usable annotations collections whose claims may apply to an answer
    of the collection `ids` names, read from `read_snapshot`: a named
    target's (or its twin's) from its `from_snapshot` on, any other held
    collection of a plugin the claims name from any snapshot — each claim
    then decided by hash (`annotations.state_of`). No snapshot named: none
    (05 §5.3)."""
    if read_snapshot is None:
        return []
    plugins = set(plugins)
    out = []
    for a in usable:
        t = target_among(a.codex, ids)
        if t is not None:
            if ann.applies_from(t.from_snapshot, read_snapshot):
                out.append(a)
        elif is_about(root, a, (), plugins):
            out.append(a)
    return out


def _plugin_of(uid: str) -> str:
    return str(uid).split(":", 1)[0]


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
    root = Path(root)
    withheld, usable = [], []
    targets = {i for n in by_coll if n in contexts
               for i in answers_for(root, n, contexts[n]["collection_id"])}
    returned = {_plugin_of(h["uid"]) for hits in by_coll.values() for h in hits}
    for a in held:
        if not is_about(root, a, targets, returned):
            continue                    # about nothing this search returned: not consulted
        why = _withheld(a)
        if why:
            withheld.append({"collection": a.name, "collection_id": a.codex.collection_id,
                             "shards": "all", "reason": f"annotations: {why}"})
        else:
            usable.append(a)
    from app.hold import collection as col
    for name, hits in by_coll.items():
        ctx = contexts.get(name) or {}
        ids = answers_for(root, name, ctx.get("collection_id"))
        mine = applicable(root, usable, ids, {_plugin_of(h["uid"]) for h in hits}, ctx.get("snapshot_id"))
        if not mine:
            continue
        found = []
        for a in mine:
            idx = ann.index_of(col.shard_dir_of(root, a.name))
            trust = None
            for h in hits:
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
