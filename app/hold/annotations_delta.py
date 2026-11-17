"""THE PINNED DELTA: an annotations update that reads from a target only the
records its snapshot chain changed (docs/notes/collection-updates.md §4,
operator 2026-11-17: "from the delta, never a full re-scan" — "re-check
only the claims with an end in the delta (changed, tombstoned), and re-pin
the rest without reading them").

WHAT THE PIN IS. Per CLAIM: every claim record carries `subject_hash`,
`object_hash` (its end records' content hashes) and `target_snapshot`
(02 §2.10, 09 §9.5), and `update` re-pins every claim to the target's newer
snapshot — so `target_snapshot` moves in every claim, every claim's
content hash moves, and EVERY PART IS REWRITTEN whatever the delta: the
claims file is read, checked, pinned and written whole, as before. What the
delta saves is the target: the content hash of each end record, which a
whole update reads from the target's parts (`read_target`: every part whose
id range holds a claimed uid, every frame that holds one), and which the
previous snapshot's claims already state for every record they name.

THE RULE. A record the target's snapshot chain does not name — in no
`added`, `changed` or `tombstoned` of any snapshot after the one the claims
are pinned to, up to the one the update checks against (09 §9.4; a chain
that skipped snapshots is read snapshot by snapshot, `LazyDelta` reading
each delta's span alone) — is held by the newer snapshot with the content
hash it had: a delta is computed BY CONTENT HASH (`compute_delta`,
`_later_delta`). Its hash is taken from the claims' pins. Every other end is
read from the target as a whole update reads it:
- a record the chain names (changed, gone, or back);
- a record no held claim pins: an end of a claim set aside last time (a
  method reads the live manifest, which runs ahead of the snapshot, so it
  names records the snapshot did not hold yet — `unheld="drop"`), of a new
  claim, a community's members (a community pins no hash), a relation's
  evidence chunk;
- every chunk end, whose record's text is re-cut (`_check_chunk`);
- every end of a target whose pin the claims do not say: no claim of the
  previous snapshot's first part names it and no method marker of this
  very snapshot does, or its claims are pinned to more than one snapshot.

WHAT MAKES IT EQUAL TO THE WHOLE RUN. The whole run's only use of the
target is the table it reads (`read_target`: for each end uid, held or not,
its content hash, a chunk end's text). This module builds that table — the
same digests, `found`, hashes and texts — and hands it to the same survey,
`check_pins` and `ClaimParts`, so every byte written and the result are the
whole run's. The inputs that make it so: (1) the claims file, read whole;
(2) the previous snapshot's claims, read whole (they are read anyway, for
the numbers); (3) per target, the snapshot the claims are pinned to — read
off EVERY claim's own `target_snapshot`, never taken on trust: a caller's
`since` that differs is noted and not used — and every snapshot after it up
to the one checked against, each held here; (4) the
deltas of those snapshots, the signed record of what changed. A record read
because a chunk end re-cuts it and that the claims pin with a hash the
chain says it still has is checked against the pin: a mismatch means a
delta that omitted a change, and refuses the update.

WHEN IT IS TAKEN: where it can pay. Measured (2026-11-17, synthetic, 3
processes; docs/notes/collection-updates.md §4 "Measured"): on wikipedia's
shape (2 M records of ~1.2 KB in 1,000 parts, 480 k star claims about 32 %
of them) the target read is ~10 % of a whole re-check (4.5 of 44.4 s) — the
rest is the claims, which every re-pin rewrites — and with 15 % of the
records changed nearly every 1,000-record frame still holds one to read:
the delta saves nothing (65.8 s against 65.9 s forced, under load; a first
form that split the ends in the survey was slower, 51.4 against 44.4 s). On
big records with a narrow delta (16 k records of 50 KB, 5 changed) it reads
1 record instead of 6,400 (1.5 s against 3.4 s). A frame is decompressed
whole for one wanted record, so a target is served from the delta only when
the claimed records its chain changed are expected in at most `MAX_FRAMES`
of its frames (`expected_frames`, from the deltas' lengths, before any uid
is hashed); otherwise it is read whole, and it says why.

Where nothing can be served, the update reads every end as the whole run
does, and says why (`Update.delta`).

Standardised alternative, not taken: a content-addressed cache of each
target record's hash by uid (a key-value store beside the snapshot) would
answer the same question without the claims' pins, at the cost of a second
copy of every claimed record's hash kept current; the pins already are that
copy, written by the update itself.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: a target is served from its delta only where the claimed records the
#: chain names are expected in at most this share of its frames — a frame is
#: decompressed whole for one wanted record (the module's measurement)
MAX_FRAMES = 0.5
#: claims of the previous snapshot's first part read to find a target's pin
#: before the claims are read whole (where the caller says none)
PROBE_CLAIMS = 256


class ReadWhole(Exception):
    """This target's ends are all read: the reason, in plain words."""


@dataclass
class Touched:
    """One target's records its chain names: their uid digests (sorted,
    once) and each one's place in the target's id order (`_uid_summary`;
    `placed` False where it has none — no part's range can hold it)."""
    digests: np.ndarray
    summaries: np.ndarray
    placed: np.ndarray
    snapshots: int
    #: of them, the ones held before — changed or tombstoned in a delta of
    #: the chain: what a claim made against the pin can name (an added one
    #: is read only where a claim held nowhere names it)
    held_before: int = 0


def chain(shard_tree: Path, pinned: str, to: str) -> list:
    """The deltas of the snapshots after `pinned` up to `to`, in order. Each
    must be held here: a chain is consecutive periods (09 §9.4), so a
    snapshot missing between them is a gap no delta covers, and the target
    is read whole."""
    from . import shard as shard_mod
    from . import snapshot as sn
    if to < pinned:
        raise ReadWhole(f"the claims are pinned to {pinned}, after {to}, the snapshot checked against")
    ids: list[str] = []
    s = pinned
    while s < to:
        s = sn.next_snapshot_id(s)
        ids.append(s)
        if len(ids) > 520:                      # ten years of weeks: not a chain
            raise ReadWhole(f"no chain of snapshots leads from {pinned} to {to}")
    if ids and ids[-1] != to:
        raise ReadWhole(f"{to} does not follow {pinned} in a chain of one cadence")
    out = []
    for sid in ids:
        p = Path(shard_tree) / shard_mod.snapshot_path(sid)
        if not p.is_file():
            raise ReadWhole(f"the snapshot {sid}, between {pinned} and {to}, is not held here: its delta "
                            f"is not known")
        out.append(sn.load(p).delta)
    return out


def chain_uids(shard_tree: Path, pinned: str, to: str, order: str = "bytes", deltas=None) -> Touched:
    """Every uid the deltas of the snapshots after `pinned` up to `to` name
    — added, changed, tombstoned (`chain`; or the `deltas` it gave)."""
    from .annotations_stream import _uid_summary, uid_digest
    deltas = chain(shard_tree, pinned, to) if deltas is None else deltas
    seen: dict[bytes, int | None] = {}
    before: set[bytes] = set()
    for d in deltas:
        for k, u in enumerate((*d.added, *d.changed, *(t.uid for t in d.tombstoned))):
            g = uid_digest(u)
            if g not in seen:
                seen[g] = _uid_summary(u, order)
                if k >= len(d.added):
                    before.add(g)
    keys = sorted(seen)
    dt = np.int64 if order == "numeric" else np.uint64
    return Touched(np.array(keys, dtype="S16").reshape(-1),
                   np.array([seen[k] or 0 for k in keys], dtype=dt),
                   np.array([seen[k] is not None for k in keys], dtype=bool), len(deltas), len(before))


def expected_frames(target_snap, touched: int, claims: int, frame_records: int) -> float:
    """The share of the target's frames expected to hold a claimed record
    the chain changed or withdrew, were the claimed records and the changes
    spread independently: each frame of `r` records holds one with
    probability 1 - (1 - c·t)^r — `t` the share of records changed or
    withdrawn, `c` the share claimed (at most two ends a claim)."""
    n = sum(int(s.records) for s in target_snap.shards)
    if not n or not touched:
        return 0.0
    c = min(1.0, 2.0 * claims / n)
    t = min(1.0, touched / n)
    r = min(frame_records, n / max(1, len(target_snap.shards)))
    return float(1.0 - (1.0 - c * t) ** r)


@dataclass
class Pins:
    """One target's servable pins: the records the claims pin and the chain
    does not name, sorted by uid digest, each with its content hash (16
    bytes; `odd` by digest where the pin is not 32 lowercase hex)."""
    digests: np.ndarray
    hashes: np.ndarray
    odd: dict[bytes, Any] = field(default_factory=dict)


@dataclass
class Served:
    """What the update may take from the delta: per target served, by its
    plugin, the records its chain names and (once the claims are read) the
    pins; per target, what was done or why every end is read."""
    touched: dict[str, Touched]
    assumed: dict[str, str]
    form: dict[str, dict[str, Any]]
    pins: dict[str, Pins] = field(default_factory=dict)


def _probe(plan, plugin: str) -> str | None:
    """The `target_snapshot` of the first claim of the previous snapshot's
    first part whose first end is `plugin`'s (within `PROBE_CLAIMS`); every
    claim's is compared once they are read (`settle`)."""
    from app.pack import records as pack_records

    from .annotations import _part_file, _prefix, claim_of, end_uid, ends_of
    if not plan.prev.shards:
        return None
    p = _part_file(plan.shard_tree, plan.prev.shards[0], None, plan.prev.snapshot_id)
    if p is None:
        return None
    for i, r in enumerate(pack_records.iter_records(p)):
        if i >= PROBE_CLAIMS:
            break
        c = claim_of(r)
        ends = ends_of(c)
        if ends and _prefix(end_uid(*ends[0])) == plugin and c.get("target_snapshot"):
            return str(c["target_snapshot"])
    return None


def _marker_pin(plan, target) -> str | None:
    """Where no claim says: the method marker's `against`, when the marker
    is this very snapshot's (app/hold/annotation_methods.py)."""
    from .annotation_methods import read_marker
    mk = read_marker(plan.root, plan.name)
    if mk is not None and mk.get("snapshot") == plan.prev.snapshot_id:
        got = (mk.get("against") or {}).get(target.name)
        return str(got) if got else None
    return None


def plan_delta(plan, since: Mapping[str, str], log=print) -> Served:
    """Per target, before any claim is read: the snapshot the claims are
    pinned to (the caller's `since` — target name or collection id — else
    the first claim's, else the marker's), the uids its chain names, and
    whether serving it can pay (`expected_frames`); every other target is
    read whole, said why."""
    from app.pack import records as pack_records

    from . import shard as shard_mod
    from . import snapshot as sn
    from .annotations import AnnotationsError
    names = {t.name for t in plan.targets} | {t.codex.collection_id for t in plan.targets}
    unknown = sorted(set(since) - names)
    if unknown:
        raise AnnotationsError(f"--since names {', '.join(unknown)}, which this collection does not annotate")
    claims = sum(int(s.records) for s in plan.prev.shards)
    out = Served({}, {}, {})
    for t in plan.targets:
        try:
            given = since.get(t.name, since.get(t.codex.collection_id))
            # the claims' own pin decides, never the caller's word: a chain read
            # from another snapshot would serve hashes from the wrong one
            at = _probe(plan, t.plugin) or _marker_pin(plan, t) or given
            if at is None:
                raise ReadWhole(f"the claims do not say which snapshot of {t.name} they are pinned to")
            order = (t.codex.slicing or {}).get("order", "bytes")
            deltas = chain(t.shard_tree, at, t.from_snapshot)
            to = t.shard_tree / shard_mod.snapshot_path(t.from_snapshot)
            if not to.is_file():
                raise ReadWhole(f"{t.name} holds no snapshot {t.from_snapshot}")
            # estimated from the deltas' lengths before a uid is hashed: what a
            # target read whole anyway costs nothing more here
            moved = sum(len(d.changed) + len(d.tombstoned) for d in deltas)
            f = expected_frames(sn.load(to), moved, claims, pack_records.FRAME_RECORDS)
            if f > MAX_FRAMES:
                raise ReadWhole(f"its snapshots from {at} to {t.from_snapshot} changed or withdrew "
                                f"{moved:,} records, about {f:.0%} of its frames holding a claimed one: "
                                f"a frame is read whole for one record, so the delta would read what the whole "
                                f"read reads")
            touched = chain_uids(t.shard_tree, at, t.from_snapshot, order, deltas=deltas)
            del deltas
        except ReadWhole as e:
            out.form[t.name] = {"whole": str(e)}
            continue
        out.touched[t.plugin] = touched
        out.assumed[t.plugin] = at
        out.form[t.name] = {"from": at, "to": t.from_snapshot, "snapshots": touched.snapshots,
                            "changed": len(touched.digests), "frames": round(f, 4)}
        if given is not None and given != at:
            out.form[t.name]["given"] = given       # the caller's `from`: not the claims' pin, not used
    return out


def settle(plan, served: Served, held) -> None:
    """Once the previous snapshot's claims are read (`held_claims(pins=
    True)`): each served target's pin confirmed against EVERY claim's
    `target_snapshot` — a target whose claims say another, or several, is
    read whole — and its servable pins: the records the claims pin, less the
    ones its chain names and the ones two claims pin differently."""
    raw = held.pins
    for t in plan.targets:
        if t.plugin not in served.touched:
            continue
        snaps = raw["snapshots"].get(t.plugin, set()) if raw is not None else {None}
        assumed = served.assumed[t.plugin]
        why = None
        if held.error is not None:
            why = f"the previous snapshot's claims cannot be read ({held.error})"
        elif snaps and snaps != {assumed}:
            why = (f"the claims are pinned to {t.name} "
                   f"{', '.join(sorted(str(s) for s in snaps))}, and the delta read starts at {assumed}")
        elif not snaps and _marker_pin(plan, t) != assumed:
            # no claim's first end is this target's: only the method's own marker says its pin
            why = f"the claims do not say which snapshot of {t.name} they are pinned to"
        if why is not None:
            served.form[t.name] = {"whole": why}
            del served.touched[t.plugin]
            continue
        p = _without(_pins_of(raw, t.plugin), served.touched[t.plugin].digests)
        served.pins[t.plugin] = p
        served.form[t.name]["pinned"] = int(len(p.digests) + len(p.odd))


def _pins_of(raw: Mapping[str, Any], plugin: str) -> Pins:
    """A plugin's pins, once a uid: a uid two claims pin with two different
    hashes (the claims disagree with one another) is not served."""
    d = np.frombuffer(bytes(raw["digests"].get(plugin, b"")), dtype="S16")
    h = np.frombuffer(bytes(raw["hashes"].get(plugin, b"")), dtype=np.uint8).reshape(-1, 16)
    if len(d):
        order = np.argsort(d, kind="stable")
        d, h = d[order], h[order]
        first = np.ones(len(d), dtype=bool)
        first[1:] = d[1:] != d[:-1]
        group = np.cumsum(first) - 1
        start = np.flatnonzero(first)
        bad = np.zeros(len(start), dtype=bool)
        np.logical_or.at(bad, group, (h != h[start[group]]).any(axis=1))
        keep = start[~bad]
        d, h = d[keep], h[keep]
    odd: dict[bytes, Any] = {}
    torn: set[bytes] = set()
    for dig, v in raw["odd"].get(plugin, []):
        if dig in odd and odd[dig] != v:
            torn.add(dig)
        odd.setdefault(dig, v)
    if odd:
        keys = np.array(sorted(odd), dtype="S16")
        if len(d):
            # a uid pinned both as hex and as something else; numpy drops an
            # S16 item's trailing zero bytes, padded back to the digest
            torn |= {bytes(k).ljust(16, b"\0") for k in keys[np.isin(keys, d)]}
        if torn and len(d):
            drop = np.isin(d, np.array(sorted(torn), dtype="S16"))
            d, h = d[~drop], h[~drop]
        odd = {k: v for k, v in odd.items() if k not in torn}
    return Pins(d, h, odd)


def _without(p: Pins, touched: np.ndarray) -> Pins:
    if not len(touched):
        return p
    keep = ~np.isin(p.digests, touched) if len(p.digests) else np.zeros(0, dtype=bool)
    odd = {k: v for k, v in p.odd.items() if not np.isin(np.array([k], dtype="S16"), touched)[0]}
    return Pins(p.digests[keep], p.hashes[keep], odd)


def table(target, ends, pins: Pins, touched: Touched, work: Path | None, count: dict[str, Any] | None = None):
    """The `read_target` table of `ends` (`annotations_stream.Table`) —
    the whole run's, digest for digest: the servable ends from the pins, and
    read from the target every other end and every chunk end, the parts
    placed by the chain's own uids where every end read is one of them or a
    chunk end (else every part is a candidate; only the frames that hold a
    wanted record are decompressed either way). A record read that the pins
    also state must agree with them, else the chain omitted a change and
    nothing is written. `count` adds the records pinned and read."""
    from . import annotations_stream as st
    from .annotations import AnnotationsError
    t = st.Table.empty(ends.digests)
    hashes = t.hashes.reshape(-1, 16)
    m = len(ends.digests)
    filled = np.zeros(m, dtype=bool)
    if m and len(pins.digests):
        at, hit = st._search(pins.digests, ends.digests)
        j = np.flatnonzero(hit)
        t.found[j] = 1
        hashes[j] = pins.hashes[at[j]]
        filled[j] = True
    for dig, v in pins.odd.items():
        at, hit = st._search(ends.digests, np.array([dig], dtype="S16"))
        if hit[0]:
            t.found[int(at[0])] = 1
            t.odd[int(at[0])] = v
            filled[int(at[0])] = True
    chunk = np.zeros(m, dtype=bool)
    if ends.text_for:
        cd = np.unique(np.array([st.uid_digest(u) for u in ends.text_for], dtype="S16"))
        chunk = np.isin(ends.digests, cd)
    want = ~filled | chunk
    read_digests = ends.digests[want]
    if count is not None:
        count["pinned"] = count.get("pinned", 0) + int(np.count_nonzero(filled & ~chunk))
        count["read"] = count.get("read", 0) + len(read_digests)
    if not len(read_digests):
        return t
    # where is each record to read: the chain's uids are placed; a chunk end's
    # uid is a string; anything else (a record no claim pinned) is not known
    rest = want & ~chunk & ~np.isin(ends.digests, touched.digests)
    summaries = None
    if not rest.any():
        order = getattr(ends.summaries, "dtype", None)
        _at, in_touched = st._search(touched.digests, read_digests) if len(touched.digests) else \
            (None, np.zeros(len(read_digests), dtype=bool))
        k = np.searchsorted(touched.digests, read_digests[in_touched]) if in_touched.any() else np.zeros(0, int)
        placed = touched.summaries[k][touched.placed[k]]
        from .annotations_stream import _uid_summary
        orders = (target.codex.slicing or {}).get("order", "bytes")
        extra = [s for s in (_uid_summary(u, orders) for u in ends.text_for) if s is not None]
        dt = order or (np.int64 if orders == "numeric" else np.uint64)
        summaries = np.unique(np.concatenate([placed.astype(dt), np.array(extra, dtype=dt)]))
    read = st.Ends(read_digests, summaries, ends.first, ends.text_for)
    got = st.read_target(target, read, work)
    at, hit = st._search(ends.digests, got.digests)
    if not hit.all():                           # pragma: no cover — `read` is drawn from `ends`
        raise AnnotationsError("the ends to read are not the claims' ends")
    rh = got.hashes.reshape(-1, 16)
    pinned = t.found[at].astype(bool)
    same = (hashes[at] == rh).all(axis=1) & (got.found.astype(bool) == pinned)
    for j, v in got.odd.items():                # a hash that is not hex: zero bytes on both sides
        if pinned[j] and t.odd.get(int(at[j])) != v:
            same[j] = False
    if (pinned & ~same).any():
        raise AnnotationsError(
            f"the target {target.name}'s snapshots from the claims' pin to {target.from_snapshot} do not name "
            f"a record whose content hash changed: its delta is not the record of what changed, and the "
            f"claims are not re-pinned from it — `hold annotations update --whole` reads every record")
    t.found[at] = got.found
    hashes[at] = rh
    for j in range(len(at)):
        t.odd.pop(int(at[j]), None)
    for j, v in got.odd.items():
        t.odd[int(at[j])] = v
    t.texts.update(got.texts)
    return t


def said(name: str, form: Mapping[str, Any]) -> str:
    """The update's line for what it read (`Update.delta`)."""
    per = "; ".join(f"{k} {v['from']}→{v['to']}: {v['changed']:,} changed in {v['snapshots']} snapshot(s)"
                    if "whole" not in v else f"{k}: every one read, {v['whole']}"
                    for k, v in form["targets"].items())
    if not form["pinned"]:
        return f"[annotations] {name}: every claimed record is read from its target ({per})"
    return (f"[annotations] {name}: from the delta — {form['pinned']:,} claimed record(s) re-pinned from the "
            f"claims' own pins, {form['read']:,} read from the target ({per})")
