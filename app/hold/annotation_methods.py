"""THE METHOD OF AN ANNOTATIONS COLLECTION, RE-RUN BY THE NODE (2026-11-12;
operator: "Can the annotations be updated without manually uploading
something???" — RULE 2; docs/notes/annotation-methods.md).

An annotations collection whose claims are a PURE FUNCTION of what the node
holds — wikipedia's `same-work` claims are the page_props items of the
wikipedia collection's own manifest (app/wiki_versions.py) — records its
METHOD in its collection file (`method = "wikipedia-versions"`, 07 §7.8;
node-local desired state, so the peer that publishes the collection is the
one that re-derives it, and a peer that adopted it takes the publisher's
snapshots as it takes any). Whenever the collection it annotates holds a
newer snapshot than the one its claims are pinned to, the worker
(`maintain`, from its claim index keeper, app/worker.py) runs the method
and then `update` with the method's whole output (02 §2.10: "a
deterministic method re-checks by re-running and publishing the next
snapshot"): the same uids kept, new claims numbered after, claims the
method no longer makes tombstoned `withdrawn`, every claim re-pinned to the
target's newer snapshot. A claim whose end the snapshot does not hold yet —
the method reads the live manifest, which runs ahead of the snapshot — is
set aside and named (`unheld="drop"`), and the next snapshot brings it in.

A hand-made claims file, or one an API or a language model wrote
(tools/annotations_from_matches.py), is NOT a method: those collections keep
`hold annotations update --claims`.

A MONTHLY METHOD (2026-11-17, operator: gutenberg-book-editions "as if a
monthly Wikipedia snapshot were downloaded"; app/gutenberg_editions.py):
`gutenberg-editions` re-derives the tiers that are a pure function of the
node's own gutenberg, wikipedia and wikidata collections and CARRIES every
other claim as it stands, so its output is still the whole claim set and
withdraws nothing it does not make. It is due once a period of the
collection's own chain (`Method.cadence`: the next snapshot's period is open
and the method has not run in it), not when its target moves; the snapshot
ids of every collection it read are recorded in `state/method.json`'s
`against`.

Each pass runs in PROCESSES OF ITS OWN (`derive_isolated`): wikipedia's
method peaks at 6.8 GiB (München, 2026-11-11) and its update at 2.7 GiB,
which live and die with their child beside the worker's 1.6-3.8 GB under
its 24 GiB cap. The node key is read in the update's child alone (a
consumer-role worker never holds the seed). A stop terminates the child;
the update's last writes — the snapshot and its signature — are held
against SIGTERM (`_held_against_stop`, `_stop_outside_last_writes`), and
everything before them is
resumable (02 §2.10's update keeps a claim's number by identity and reads
the previous claims by the roots the snapshot lists).

Standardised alternative, not taken: a workflow engine's "derived dataset"
(Airflow's datasets, Dagster's assets) re-runs a step when its input's
version moves. Adopting one would be a new service beside the worker for a
single trigger — the target's snapshot id — that the worker already reads;
it would pay once several derivations chain into one another.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .annotations import AnnotationsError

#: the state file a derivation writes once its update has returned:
#: `<root>/<name>/state/method.json`
MARKER = "method.json"
#: a child killed by a signal (the kernel's OOM killer) is tried again at the
#: keeper's next pass, this many times for one target snapshot, then said
KILLED_TRIES = 3
#: after a stop terminates a child, how long it may take to leave its last
#: writes (the snapshot and its signature) before it is killed
STOP_GRACE_S = 60.0


@dataclass(frozen=True)
class Method:
    """A way of making an annotations collection's whole claim set from the
    collection it annotates, deterministically."""
    name: str
    plugin: str                                  # the plugin of the ONE collection it annotates
    #: (target Collection, out Path, log) -> counts; with `reads_collection`,
    #: (Context, out Path, log) -> counts
    produce: Callable[..., dict[str, Any]]
    summary: str
    #: the claims it makes — their `type`s and their `method` (02 §2.10) — so
    #: that a collection whose claims are not its own is never handed to it:
    #: its output is the WHOLE claim set from then on, and a claim it does not
    #: make is withdrawn (`not_fitting`). Empty: any claim (a test's method)
    types: tuple[str, ...] = ()
    claim_method: Mapping[str, Any] | None = None
    #: the Library's checkbox (RULE 2: what it does, in plain words)
    label: str = ""
    #: SEVERAL claim methods (2026-11-17), each matched on the keys it names
    #: (`{"kind": "matcher", "id": "x"}` is every version of matcher x): the
    #: ones it makes, and the ones it CARRIES unchanged when the collection
    #: holds them — a claim of either kind fits (`not_fitting`)
    claim_methods: tuple[Mapping[str, Any], ...] = ()
    carries: tuple[Mapping[str, Any], ...] = ()
    #: "monthly": due once a period of the collection's own chain (`due`),
    #: whatever its target does; None: when the target holds a newer snapshot
    cadence: str | None = None
    #: the producer reads the annotations collection itself (to carry its
    #: claims) and the node's other collections: it is given a `Context`
    reads_collection: bool = False
    #: (Context, log) -> what a derivation would change, written nowhere:
    #: what `hold annotations derive --dry-run` shows beside `due`
    preview: Callable[..., dict[str, Any]] | None = None
    #: the plugins of the collections it reads besides its target, declared
    #: (docs/notes/collection-updates.md §4: whom a source's update wakes)
    inputs: tuple[str, ...] = ()
    #: THE DELTA FORM (collection-updates.md §4, `derive_from_delta`):
    #: (Context, deltas, out Path, log) -> counts — the claim set `produce`
    #: writes, byte for byte, made from what the sources' deltas touch
    #: (`deltas`: source collection name -> (from snapshot, to snapshot), each
    #: `from` the one the method last read), starting from its own last
    #: output (the collection's current snapshot); it raises `WholeRun` where
    #: it cannot apply. None: the method always runs whole, and says why
    delta: Callable[..., dict[str, Any]] | None = None
    #: why a method with no delta form runs whole, in plain words
    whole_because: str = ""


class WholeRun(Exception):
    """A delta form cannot apply this time: the method runs whole, and this
    says why."""


@dataclass(frozen=True)
class Context:
    """What a `reads_collection` producer is given: the collections root,
    the annotations collection and the one collection held here that answers
    for its target (`resolve`)."""
    root: Path
    name: str
    target: str


def _wikipedia_versions(collection, out: Path, log=print) -> dict[str, Any]:
    """wikipedia's article claims (app/wiki_versions.py): one `same-work`
    claim per non-shown version of every article of two versions or more,
    against the version shown. Reads the manifest (`native_id`, `tokens`)
    and the page_props packs, read-only; writes `out` whole (a temporary
    renamed over it)."""
    from app import db, wiki_versions
    con = db.connect(collection)                 # reads only: SELECTs in rowid windows
    try:
        m = wiki_versions.from_manifest(collection, con)
    finally:
        con.close()
    sizes = m["item_start"][1:] - m["item_start"][:-1]
    multi = sizes >= 2
    counts = {"articles": int(multi.sum()), "versions": int(sizes[multi].sum()),
              "claims": int((sizes[multi] - 1).sum())}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "wb") as f:
        for chunk in wiki_versions.claim_lines(m, plugin=collection.plugin):
            f.write(chunk)
    tmp.replace(out)
    log(f"[annotations] wikipedia-versions over {collection.name}: {counts['claims']:,} claims "
        f"({counts['articles']:,} articles of two versions or more)")
    return counts


def _gutenberg_editions(ctx: Context, out: Path, log=print) -> dict[str, Any]:
    """gutenberg's versions of a work (app/gutenberg_editions.py): the
    Wikidata tier from the node's own wikipedia and wikidata, the same-title
    and language-stage tiers from gutenberg's records where the collection
    holds them, every other claim carried; `out` written whole."""
    from app import gutenberg_editions
    return gutenberg_editions.produce(ctx.root, ctx.name, ctx.target, out, log=log)


def _gutenberg_editions_preview(ctx: Context, log=print) -> dict[str, Any]:
    from app import gutenberg_editions
    return gutenberg_editions.preview(ctx.root, ctx.name, ctx.target, log=log)


METHODS: dict[str, Method] = {
    "wikipedia-versions": Method(
        "wikipedia-versions", "wikipedia", _wikipedia_versions,
        "wikipedia's articles as works: every other language version same-work as the one shown "
        "(the largest; English on a tie), from the manifest and page_props",
        types=("same-work",), claim_method={"kind": "wikidata"},        # wiki_versions.METHOD
        label="update by itself when wikipedia holds a newer snapshot",
        whole_because=("its claims are read from the live manifest's lengths (`tokens`) and the page_props' "
                       "Wikidata items, and a snapshot's delta names neither: it names the records whose title "
                       "or text changed, not a page whose item moved in page_props, and the manifest runs ahead "
                       "of the snapshot; the update after it is re-pinned from the delta")),
    "gutenberg-editions": Method(
        "gutenberg-editions", "gutenberg", _gutenberg_editions,
        "gutenberg's editions and translations of a work, re-made every month from this node's own "
        "collections: the books Wikidata joins (their Wikipedia pages found in this node's wikipedia), "
        "the same-title and language-stage claims from gutenberg's records where the collection holds "
        "them; every other claim kept as it stands",
        types=("same-work", "translation-of", "language-stage"),
        claim_methods=({"kind": "wikidata"}, {"kind": "matcher", "id": "gutenberg-same-title"},
                       {"kind": "matcher", "id": "historical-markers"}),
        carries=({"kind": "matcher", "id": "louise-classical-matcher"},
                 {"kind": "matcher", "id": "gutenberg-editions-note"}, {"kind": "human"}),
        label="update by itself every month from the node's Gutenberg, Wikipedia and Wikidata",
        cadence="monthly", reads_collection=True, preview=_gutenberg_editions_preview,
        inputs=("wikipedia", "wikidata"),
        whole_because=("its Wikidata tier reads the node's live wikipedia manifest, the dump's redirects and its "
                       "page_props, and the live wikidata manifest, none of which a snapshot's delta names (a "
                       "page_props item that moved, a redirect retargeted); its gutenberg records are read once a "
                       "part's bytes (a part cache), so only the parts gutenberg re-issued are read, and the "
                       "update after it is re-pinned from the delta")),
}


def get(name: str) -> Method:
    try:
        return METHODS[name]
    except KeyError:
        raise AnnotationsError(f"method {name!r} is not one this node runs; its methods are "
                               f"{', '.join(sorted(METHODS))}") from None


def resolve(root: Path, name: str, method: Method) -> tuple[str, str]:
    """`(name, target)`: the collection's name and the one collection held
    here that answers for its one target (the target itself or its records
    twin, `annotations._target_holder`), which must be of the method's
    plugin."""
    from . import codex as cx, collection as col
    from .annotations import _target_holder, is_annotations
    name, codex = col.resolve(Path(root), name)
    if not is_annotations(codex):
        raise AnnotationsError(f"{name} is not an annotations collection (02 §2.10)")
    targets = cx.targets_of(codex)
    if len(targets) != 1:
        raise AnnotationsError(f"method {method.name} annotates one collection, and {name} "
                               f"annotates {len(targets)}")
    tname, tcodex = _target_holder(Path(root), targets[0].collection_id)
    if tcodex.plugin != method.plugin:
        raise AnnotationsError(f"method {method.name} annotates a {method.plugin} collection, and "
                               f"{name} annotates {tname}, a {tcodex.plugin} collection")
    return name, tname


#: the parts whose first claim `not_fitting` reads: spread over the snapshot,
#: so the cost is bounded whatever its size (wikipedia's: 246 parts)
SAMPLED_PARTS = 64


def not_fitting(root: Path, name: str, method: Method) -> str | None:
    """Why `method` cannot make `name`'s claims, in plain words — or None
    when it can: it annotates one collection of the method's plugin, and
    either the method made its current snapshot (`state/method.json`) or its
    claims are of the method's kind — the first claim of up to
    `SAMPLED_PARTS` parts spread over the current snapshot has one of the
    method's `types` and its `method`. The method's output becomes the whole
    claim set, so a collection of other claims (relations, editions by a
    matcher) would lose them: the CLI, the Library and the worker all ask
    this first (München, 2026-11-13: the Library's checkbox)."""
    from app.share import seeder
    from . import collection as col
    root = Path(root)
    try:
        name, _ = resolve(root, name, method)
    except AnnotationsError as e:
        return str(e)
    tree = col.shard_dir_of(root, name)
    snap = seeder.cached_snapshot(tree)
    if snap is None or not snap.shards:
        return None                         # nothing held to replace
    mk = read_marker(root, name)
    if mk is not None and mk.get("method") == method.name and mk.get("snapshot") == snap.snapshot_id:
        return None                         # it made them
    if not method.types and method.claim_method is None and not method.claim_methods and not method.carries:
        return None
    key = (str(tree), snap.hash, method.name)
    if key not in _FITS:
        _FITS[key] = _sampled(tree, snap, method, name)
    return _FITS[key]


#: `not_fitting`'s sample, per (shard tree, snapshot hash, method): a snapshot
#: never changes, and the keeper asks every pass until the first derivation
_FITS: dict[tuple[str, str, str], str | None] = {}


def _by_fits(method: Method, by: Any) -> bool:
    """Whether a claim's `method` is one `method` makes or carries: equal to
    its `claim_method`, or naming every key of one of its `claim_methods` /
    `carries` with the same value."""
    if method.claim_method is None and not method.claim_methods and not method.carries:
        return True
    if method.claim_method is not None and by == dict(method.claim_method):
        return True
    return isinstance(by, dict) and any(all(by.get(k) == v for k, v in p.items())
                                        for p in (*method.claim_methods, *method.carries))


def _methods_said(method: Method) -> str:
    said = [dict(method.claim_method)] if method.claim_method is not None else []
    said += [dict(p) for p in (*method.claim_methods, *method.carries)]
    return " or ".join(json.dumps(p, sort_keys=True) for p in said) or "{}"


#: claims read at the head of each sampled part when a method also CARRIES
#: claims it does not make: enough to find one of its own among them
SAMPLED_CLAIMS = 256


def _makes(method: Method, by: Any) -> bool:
    """Whether a claim's `method` is one `method` MAKES (not only carries)."""
    if method.claim_method is not None and by == dict(method.claim_method):
        return True
    return isinstance(by, dict) and any(all(by.get(k) == v for k, v in p.items())
                                        for p in method.claim_methods)


def _sampled(tree: Path, snap, method: Method, name: str) -> str | None:
    """Every sampled claim is one `method` makes or carries; and where it
    carries claims of others, at least one sampled claim is one it MAKES —
    else ticking it would add a tier of its own to a collection made of
    other claims (2026-11-17: a collection of the classical matcher's
    claims alone does not fit `gutenberg-editions`)."""
    from app.pack import records as pack_records
    from .annotations import _part_file, claim_of
    step = max(1, -(-len(snap.shards) // SAMPLED_PARTS))
    per_part = SAMPLED_CLAIMS if method.carries else 1
    own = False
    for shard in snap.shards[::step]:
        p = _part_file(tree, shard, None, snap.snapshot_id)
        if p is None:
            continue
        for i, r in enumerate(pack_records.iter_records(p)):
            if i >= per_part:
                break
            c = claim_of(r)
            kind_ok = not method.types or c.get("type") in method.types
            if not (kind_ok and _by_fits(method, c.get("method"))):
                return (f"{name}'s claims are not {method.name}'s: part {shard.n} holds a "
                        f"{c.get('type')!r} claim by {json.dumps(c.get('method'), sort_keys=True)}, and "
                        f"{method.name} makes {', '.join(method.types) or 'any'} claims by "
                        f"{_methods_said(method)}; "
                        + ("it is made for a collection of those, not this one" if method.carries
                           else "its output would withdraw them"))
            own = own or _makes(method, c.get("method"))
    if method.carries and not own:
        return (f"{name}'s sampled claims are all ones {method.name} would only carry, none it makes "
                f"({json.dumps([dict(m) for m in method.claim_methods], sort_keys=True)}): ticking it "
                f"would add a tier of its own to a collection made by another tool")
    return None


# --------------------------------------------------------------------------
# is it due?

def marker_path(root: Path, name: str) -> Path:
    return Path(root) / name / "state" / MARKER


def read_marker(root: Path, name: str) -> dict[str, Any] | None:
    try:
        got = json.loads(marker_path(root, name).read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _write_marker(root: Path, name: str, doc: dict[str, Any]) -> None:
    p = marker_path(root, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n")
    os.replace(tmp, p)


def newest_snapshot_id(shard_tree: Path) -> str | None:
    """The newest snapshot's id by its file's name — a listing, never a
    load (a first snapshot's delta lists every uid)."""
    d = Path(shard_tree) / "snapshots"
    try:
        ids = sorted(p.name[:-5] for p in d.glob("*.json") if not p.name.endswith(".sig.json"))
    except OSError:
        return None
    return ids[-1] if ids else None


def pinned_snapshot(shard_tree: Path) -> str | None:
    """The target snapshot the current claims are pinned to, read from the
    first claim of the current snapshot's first part (`target_snapshot`,
    02 §2.10): what a collection made before its method was recorded
    says, so its first pass after a deploy is not a wasted re-derivation."""
    from app.pack import records as pack_records
    from app.share import seeder
    from .annotations import _part_file, claim_of
    snap = seeder.cached_snapshot(shard_tree)
    if snap is None or not snap.shards:
        return None
    p = _part_file(Path(shard_tree), snap.shards[0], None, snap.snapshot_id)
    if p is None:
        return None
    for r in pack_records.iter_records(p):
        return claim_of(r).get("target_snapshot")
    return None


@dataclass(frozen=True)
class Due:
    """Whether the method is to run now, and why, in plain words."""
    name: str
    method: str
    target: str                  # the collection held here that answers for the target
    target_snapshot: str | None  # its newest snapshot
    pinned: str | None           # the target snapshot the claims are pinned to
    current: str | None          # this collection's current snapshot
    due: bool
    reason: str
    #: a monthly method's: the period open now, what a failure is remembered by
    period: str | None = None
    #: not due, and waiting for something to say once (None: the target's pin
    #: differs from its newest snapshot)
    waiting: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def open_period(cadence: str, now: datetime) -> str:
    """The period open at `now` (09 §9.4): the one a snapshot written now
    is named for at the earliest — November's is 2026-11 from 1 November."""
    from . import snapshot as sn
    return sn.next_snapshot_id(sn.snapshot_id_for(now, cadence))


def due(root: Path, name: str, method_name: str, *, clock: datetime | None = None) -> Due:
    """Is a derivation due: the target holds a newer snapshot than the one
    the claims are pinned to (or the method changed), and this collection's
    next snapshot's period is open (one update a period, 09 §9.4). A MONTHLY
    method's (`cadence`, 2026-11-17): that period is open and the method has
    not run in it (`_due_by_period`), whatever its target holds."""
    from . import collection as col
    from .annotations import update_snapshot_id
    root = Path(root)
    method = get(method_name)
    name, tname = resolve(root, name, method)
    why = not_fitting(root, name, method)
    if why is not None:
        raise AnnotationsError(why)
    tsnap = newest_snapshot_id(col.shard_dir_of(root, tname))
    tree = col.shard_dir_of(root, name)
    current = newest_snapshot_id(tree)
    mk = read_marker(root, name)
    if mk is not None and mk.get("snapshot") == current and mk.get("method") == method.name:
        pinned = (mk.get("against") or {}).get(tname, mk.get("target_snapshot"))
    else:
        pinned = pinned_snapshot(tree)

    def out(is_due: bool, reason: str, **kw) -> Due:
        return Due(name, method.name, tname, tsnap, pinned, current, is_due, reason, **kw)
    if method.cadence is not None:
        return _due_by_period(method, name, current, mk, clock or datetime.now(timezone.utc), out)
    if current is None:
        return out(False, f"{name} holds no snapshot: `create` makes the first")
    if tsnap is None:
        return out(False, f"the target {tname} holds no snapshot to derive the claims from")
    if pinned == tsnap and (mk is None or mk.get("method") in (None, method.name)):
        return out(False, f"the claims are pinned to {tname} {tsnap}, its newest snapshot")
    try:
        update_snapshot_id(current, clock or datetime.now(timezone.utc))
    except AnnotationsError as e:
        return out(False, f"{tname} {tsnap} is newer than {pinned or 'the claims'}' pin, and {e}")
    return out(True, f"{tname} holds {tsnap}, and the claims are pinned to {pinned or 'none'}")


def _due_by_period(method: Method, name: str, current: str | None, mk: dict[str, Any] | None,
                   now: datetime, out) -> Due:
    """A monthly method's due rule: this collection's next snapshot's period
    is open (`update_snapshot_id`, one update a period) and the method has
    not already run in the period open now — it ran when the marker names
    the period and either wrote that period's snapshot or wrote nothing (a
    chain behind by more than one period catches up one run at a time, and
    a run with nothing to write is not repeated in its period)."""
    from . import snapshot as sn
    from .annotations import update_snapshot_id
    period = open_period(method.cadence or "monthly", now)
    if current is None:
        return out(False, f"{name} holds no snapshot: `create` makes the first", period=period, waiting=False)
    ran = mk is not None and mk.get("method") == method.name and mk.get("period") == period
    try:
        nxt = update_snapshot_id(current, now)
    except AnnotationsError as e:
        return out(False, f"{name} {current} is this period's snapshot: {e}", period=period, waiting=not ran)
    if ran and (not mk.get("written", True) or mk.get("snapshot") == period):
        return out(False, f"{method.name} ran in {period} already ({mk.get('at')}, "
                          f"{'snapshot ' + str(mk.get('snapshot')) if mk.get('written', True) else 'nothing to write'})"
                          f": the next run is in {sn.next_snapshot_id(period)}", period=period, waiting=False)
    return out(True, f"{name}'s next snapshot, {nxt}, can be written in {period}, and {method.name} has not "
                     f"run in it", period=period, waiting=False)


# --------------------------------------------------------------------------
# the derivation

def _target_collection(name: str):
    """The target as the node loads it — its manifest, its origin."""
    from app import collections as collections_mod
    for c in collections_mod.load_all():
        if c.name == name:
            return c
    raise AnnotationsError(f"the target {name} is not a collection this node runs (its file is "
                           f"refused or absent): `hold collection show {name}` says why")


#: beside the claims file a producer wrote: its counts, `against` among them
#: (what `finish` records in the marker: every collection the method read)
COUNTS_SUFFIX = ".counts.json"
#: `finish`'s update from the delta, each target pinned where its claims say
#: (`annotations.update(since=…)`): the default of every derivation
FROM_PINS: Mapping[str, str] = MappingProxyType({})


def _counts_path(claims: Path) -> Path:
    return Path(claims).with_name(Path(claims).name + COUNTS_SUFFIX)


def produce(root: Path, name: str, method_name: str, out: Path, *, log=print,
            deltas: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The method's whole claim set, written to `out`; its counts beside it.
    With `deltas` (source name -> (from, to)), by its delta form where it
    has one and it applies (`delta_applies`, `WholeRun`) — the same bytes —
    and `counts["form"]` saying which ran and why."""
    method = get(method_name)
    name, tname = resolve(Path(root), name, method)
    ctx = Context(Path(root), name, tname)
    counts: dict[str, Any] | None = None
    form: dict[str, Any] = {"method": "whole", "why": None}
    if deltas is not None:
        why = delta_applies(Path(root), name, method, deltas)
        if why is None:
            try:
                counts = method.delta(ctx, {k: tuple(v) for k, v in deltas.items()}, Path(out), log)
                form = {"method": "delta", "why": None}
            except WholeRun as e:
                why = str(e)
        if counts is None:
            form["why"] = why
            log(f"[annotations] {name}: {method.name} runs whole — {why}")
    if counts is None:
        if method.reads_collection:
            counts = method.produce(ctx, Path(out), log)
        else:
            counts = method.produce(_target_collection(tname), Path(out), log)
    if deltas is not None:
        counts = {**counts, "form": form}
    _counts_path(out).write_text(json.dumps(counts, sort_keys=True))
    return counts


def delta_applies(root: Path, name: str, method: Method, deltas: Mapping[str, Any]) -> str | None:
    """Why `method`'s delta form cannot make `name`'s next claims from
    `deltas`, in plain words — or None when it can: it has one; its last
    run made the collection's current snapshot (the claims it starts from
    are its own output); and every collection that run read either has a
    delta starting at the snapshot it read, or has not moved since."""
    from . import collection as col
    if method.delta is None:
        return method.whole_because or f"{method.name} has no delta form"
    mk = read_marker(root, name)
    if mk is None:
        return f"no previous run of {method.name} to start from"
    if mk.get("method") != method.name:
        return f"the claims were made by {mk.get('method')!r}, not {method.name}: a method change runs whole"
    current = newest_snapshot_id(col.shard_dir_of(root, name))
    if mk.get("snapshot") != current:
        return (f"{name}'s current snapshot {current} is not {method.name}'s last output "
                f"({mk.get('snapshot')})")
    read = dict(mk.get("against") or {})
    for src, (frm, _to) in ((k, tuple(v)) for k, v in deltas.items()):
        if src in read and read[src] != frm:
            return f"the delta of {src} starts at {frm}, and {method.name} last read {src} {read[src]}"
    for src, was in read.items():
        if src in deltas:
            continue
        try:
            now = newest_snapshot_id(col.shard_dir_of(root, src))
        except Exception:   # noqa: BLE001 — a source gone: nothing to start from
            now = None
        if now != was:
            return f"{src} moved from {was} to {now}, and no delta of it is given"
    return None


def preview(root: Path, name: str, method_name: str, *, log=print) -> dict[str, Any] | None:
    """What a derivation would change (the method's `preview`), written
    nowhere; None for a method that shows none."""
    method = get(method_name)
    if method.preview is None:
        return None
    name, tname = resolve(Path(root), name, method)
    why = not_fitting(Path(root), name, method)
    if why is not None:
        raise AnnotationsError(why)
    return method.preview(Context(Path(root), name, tname), log)


def finish(root: Path, name: str, method_name: str, claims: Path, *, key=None, now: str | None = None,
           log=print, guard=None, clock: datetime | None = None, dry_run: bool = False,
           against: Sequence[Sequence[str]] | None = None,
           since: Mapping[str, str] | None = FROM_PINS) -> dict[str, Any]:
    """`update` with the method's output — set aside what the target's
    snapshot does not hold yet — and the marker written once it returned.
    The update is re-pinned FROM THE DELTA wherever the claims' own pins
    allow (`since`; app/hold/annotations_delta.py): the same bytes as
    reading every claimed record, which `since=None` does."""
    from . import annotations as ann
    method = get(method_name)
    got = ann.update(Path(root), name, claims=Path(claims), key=key, now=now, log=log, clock=clock,
                     dry_run=dry_run, unheld="drop", private=True, guard=guard,
                     against=[tuple(a) for a in against] if against else None,
                     since=None if since is None else dict(since))
    out = got.to_dict()
    if not dry_run:
        try:
            read = json.loads(_counts_path(claims).read_text()).get("against") or {}
        except (OSError, ValueError, AttributeError):
            read = {}
        doc = {"method": method_name, "snapshot": got.snapshot_id, "against": {**read, **got.against},
               "at": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "added": got.added, "changed": got.changed, "tombstoned": got.tombstoned,
               "set_aside": len(got.dropped)}
        if method.cadence is not None:
            doc["period"] = open_period(method.cadence, clock or datetime.now(timezone.utc))
            doc["written"] = got.snapshot is not None
        _write_marker(Path(root), name, doc)
        out["against"] = doc["against"]
    return out


def derive(root: Path, name: str, method_name: str, *, key=None, now: str | None = None, log=print,
           guard=None, clock: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """The method, then the update, in THIS process (`hold annotations
    derive`; the worker runs `derive_isolated`)."""
    from . import annotations_stream as st
    with st.workspace(Path(root), f"method.{name}") as work:
        counts = produce(root, name, method_name, work / "claims.jsonl", log=log)
        got = finish(root, name, method_name, work / "claims.jsonl", key=key, now=now, log=log,
                     guard=guard, clock=clock, dry_run=dry_run)
    return {"method": method_name, "produced": counts, **got}


#: set while the update's last writes run (`_held_against_stop`), and when a
#: stop arrived during them
_LAST_WRITES = {"running": False, "stop": False}


def _stop_outside_last_writes() -> None:
    """A CHILD'S STOP: SIGTERM and SIGINT exit 130 at once, except during the
    update's last writes, after which they do. A HANDLER, not a signal mask:
    the disposition is the process's, so the signal lands whichever thread
    the kernel hands it to — a mask is per thread, and numpy's BLAS threads,
    started at import before any code of ours runs, took the default action
    (the first test of this died mid-block). The handler runs in the main
    thread, which is the one doing the writes, between two bytecodes."""
    def on_signal(signum, frame) -> None:
        if _LAST_WRITES["running"]:
            _LAST_WRITES["stop"] = True
        else:
            os._exit(130)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)


@contextlib.contextmanager
def _held_against_stop():
    """The update's last writes — the snapshot and its signature — held
    against a stop (`_stop_outside_last_writes`): one that arrives meanwhile
    takes effect when they are done."""
    _LAST_WRITES["running"] = True
    try:
        yield
    finally:
        _LAST_WRITES["running"] = False
        if _LAST_WRITES["stop"]:
            os._exit(130)


def _said(error: str, fn, *args) -> None:
    """A child's body: its refusal written where the parent reads it
    (`_run`), so the worker's one line names the reason, not an exit code."""
    try:
        fn(*args)
    except Exception as e:   # noqa: BLE001 — every refusal, said by the parent
        Path(error).write_text(f"{type(e).__name__}: {e}")
        sys.exit(3)
    sys.exit(0)


def _produce_child(root: str, name: str, method_name: str, out: str, error: str,
                   deltas_json: str = "null") -> None:
    from app import parallel
    _stop_outside_last_writes()
    parallel.watch_parent()
    deltas = json.loads(deltas_json)
    _said(error, lambda: produce(Path(root), name, method_name, Path(out), log=_child_log, deltas=deltas))


def _node_key(name: str):
    """The node's signing key, read in the update's child alone (the worker
    never holds it); None — the snapshot written unsigned (09 §9.4) — when
    there is none."""
    try:
        from app.bundles import keys as node_keys
        return node_keys.load_signing_key()
    except Exception:   # noqa: BLE001 — no key: the snapshot is held unsigned
        _child_log(f"[annotations] {name}: no node key: the snapshot is written unsigned")
        return None


def _finish_child(root: str, name: str, method_name: str, claims: str, result: str, error: str,
                  against_json: str = "null", since_json: str = "{}") -> None:
    from app import parallel
    _stop_outside_last_writes()
    parallel.watch_parent()

    def body() -> None:
        got = finish(Path(root), name, method_name, Path(claims), key=_node_key(name), log=_child_log,
                     guard=_held_against_stop, against=json.loads(against_json),
                     since=json.loads(since_json))
        Path(result).write_text(json.dumps(got, sort_keys=True))
    _said(error, body)


def _recheck_child(root: str, name: str, result: str, error: str, against_json: str, since_json: str) -> None:
    """Claims given by hand, re-checked (`update` with no claims file), from
    the delta: the update's child of `derive_from_delta` for a collection
    with no method."""
    from app import parallel

    from . import annotations as ann
    _stop_outside_last_writes()
    parallel.watch_parent()

    def body() -> None:
        against = json.loads(against_json)
        got = ann.update(Path(root), name, key=_node_key(name), log=_child_log, guard=_held_against_stop,
                         against=[tuple(a) for a in against] if against else None, since=json.loads(since_json))
        Path(result).write_text(json.dumps(got.to_dict(), sort_keys=True))
    _said(error, body)


def _child_log(m: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [node info:annotations] {m}", flush=True)


class Stopped(Exception):
    """The worker stopped during a derivation; nothing is lost (resumable)."""


def _run(target, args, *, stop, poll_s: float, label: str, error: Path | None = None) -> None:
    import multiprocessing
    p = multiprocessing.get_context("spawn").Process(target=target, args=args, name=label, daemon=False)
    p.start()
    while True:
        p.join(poll_s)
        if p.exitcode is not None:
            break
        if stop is not None and stop():
            p.terminate()
            p.join(STOP_GRACE_S)
            if p.exitcode is None:
                p.kill()
                p.join(10)
            raise Stopped(f"the worker stopped during {label}")
    if p.exitcode != 0:
        said = error.read_text() if error is not None and error.is_file() else None
        raise ChildFailed(p.exitcode, said or (f"{label} exited {p.exitcode}"
                                               + (" (killed: out of memory?)" if p.exitcode < 0 else "")))


class ChildFailed(AnnotationsError):
    def __init__(self, exitcode: int, message: str) -> None:
        super().__init__(message)
        self.exitcode = exitcode


def derive_isolated(root: Path, name: str, method_name: str, *, stop=None,
                    poll_s: float = 1.0) -> dict[str, Any]:
    """`derive` in two PROCESSES OF THEIR OWN, one after the other — the
    method, then the update — in a workspace this process holds (`sweep`
    leaves it while it lives)."""
    from . import annotations_stream as st
    root = Path(root)
    with st.workspace(root, f"method.{name}") as work:
        claims, result, error = work / "claims.jsonl", work / "result.json", work / "error.txt"
        _run(_produce_child, (str(root), name, method_name, str(claims), str(error)), stop=stop,
             poll_s=poll_s, label=f"annotations-method {name}", error=error)
        _run(_finish_child, (str(root), name, method_name, str(claims), str(result), str(error)),
             stop=stop, poll_s=poll_s, label=f"annotations-update {name}", error=error)
        return json.loads(result.read_text())


def _targets_of(root: Path, name: str) -> list[tuple[str, set[str]]]:
    """Each target of the annotations collection `name` as the collection
    held here that answers for it (`_target_holder`), with every name a
    caller may give it: the holder's, the target's collection id, and the
    target's own local name where it is held."""
    from . import codex as cx
    from . import collection as col
    from .annotations import _target_holder
    _, codex = col.resolve(root, name)
    out = []
    for a in cx.targets_of(codex):
        tname, _tcodex = _target_holder(root, a.collection_id)
        aliases = {tname, a.collection_id}
        with contextlib.suppress(Exception):     # not held under its own id: its twin answers
            aliases.add(col.resolve(root, a.collection_id)[0])
        out.append((tname, aliases))
    return out


def derive_from_delta(root: Path, name: str, method_name: str | None, deltas: Mapping[str, Any], *,
                      stop=None, poll_s: float = 1.0, log=print) -> dict[str, Any]:
    """THE CASCADE'S STEP for one annotations collection woken by its
    sources' new snapshots (docs/notes/collection-updates.md §4): its next
    snapshot, made FROM THE DELTA wherever that gives the whole run's bytes,
    in PROCESSES OF THEIR OWN as `derive_isolated` — the same stop (a stop
    terminates the child, `Stopped`; the update's last writes are held
    against it, `_held_against_stop`) and the same failures (`ChildFailed`,
    a negative exit code for a child the kernel killed, which the caller
    tries again up to `KILLED_TRIES` times, as `maintain` does).

    `deltas`: each source collection woken for -> (from snapshot, to
    snapshot). `method_name` None: claims given by hand, RE-CHECKED — the
    update reads from each target only the records its snapshots since the
    claims' pin name (app/hold/annotations_delta.py). Otherwise the method
    runs by its delta form where it has one and it applies (`delta_applies`:
    no previous run of it, a method change, a source moved with no delta
    given…), else whole, said why; and its update is re-pinned from the delta
    the same way. A target named in `deltas` is checked against its `to`;
    its chain is read from the snapshot the claims themselves are pinned to
    (a `from` that differs is noted, not used: the claims' pin is what their
    hashes are of), and a snapshot missing between them reads it whole. The
    result is `derive_isolated`'s, with `form`: how the method ran and why,
    and what the update read."""
    from . import annotations_stream as st
    from . import collection as col
    from .annotations import is_annotations
    root = Path(root)
    deltas = {str(k): [str(v[0]), str(v[1])] for k, v in dict(deltas or {}).items()}
    if method_name is None:
        name, codex = col.resolve(root, name)
        if not is_annotations(codex):
            raise AnnotationsError(f"{name} is not an annotations collection (02 §2.10)")
    else:
        method = get(method_name)
        name, _tname = resolve(root, name, method)
        why = not_fitting(root, name, method)
        if why is not None:
            raise AnnotationsError(why)
    against, since = [], {}
    for tname, aliases in _targets_of(root, name):
        key = next((k for k in sorted(aliases) if k in deltas), None)
        if key is not None:
            against.append([tname, deltas[key][1]])
            since[tname] = deltas[key][0]
    with st.workspace(root, f"method.{name}") as work:
        claims, result, error = work / "claims.jsonl", work / "result.json", work / "error.txt"
        if method_name is None:
            _run(_recheck_child, (str(root), name, str(result), str(error), json.dumps(against), json.dumps(since)),
                 stop=stop, poll_s=poll_s, label=f"annotations-recheck {name}", error=error)
            produced = None
            mform: dict[str, Any] = {"method": None, "why": "claims given by hand: re-checked, no method to run"}
        else:
            _run(_produce_child, (str(root), name, method_name, str(claims), str(error), json.dumps(deltas)),
                 stop=stop, poll_s=poll_s, label=f"annotations-method {name}", error=error)
            produced = json.loads(_counts_path(claims).read_text())
            mform = produced.pop("form", None) or {"method": "whole", "why": None}
            _run(_finish_child, (str(root), name, method_name, str(claims), str(result), str(error),
                                 json.dumps(against), json.dumps(since)),
                 stop=stop, poll_s=poll_s, label=f"annotations-update {name}", error=error)
        got = json.loads(result.read_text())
    form = {"method": mform["method"], "method_why": mform["why"], "update": got.get("delta")}
    upd = got.get("delta") or {}
    log(f"[annotations] {name}: "
        + (f"{method_name} ran {mform['method']}" + (f" ({mform['why']})" if mform["why"] else "")
           if method_name else "claims given by hand, re-checked")
        + f"; the update re-pinned {upd.get('pinned', 0):,} claimed record(s) from the delta and read "
          f"{upd.get('read', 0):,}: snapshot {got.get('snapshot_id')}")
    return {"method": method_name, "produced": produced, **got, "form": form}


def maintain(root: Path, *, stop=None, log=print, failed: dict[str, Any] | None = None,
             clock: datetime | None = None, run=None) -> list[dict[str, Any]]:
    """Every annotations collection held here whose file records a method,
    derived where it is due — the cascade's round (app/hold/cascade_keeper.py,
    the ONE place that decides: a method due, a source published, a wake
    pending) restricted to the collections with a method. Bounded — one
    collection at a time, `stop()` asked between them and inside each
    child's wait; a failure is said once per (collection, method, target
    snapshot or period) and left until one of them changes, except a child
    killed by a signal (the OOM killer), tried again `KILLED_TRIES` times.
    `run(root, name, method_name, stop=)` is the derivation (the cascade's
    is `derive_from_delta`)."""
    from . import cascade_keeper
    step = None if run is None else (
        lambda root_, name, method_name, deltas, stop=None: run(root_, name, method_name, stop=stop))
    return cascade_keeper.keep_round(root, stop=stop, log=log, failed=failed, clock=clock, run=step,
                                     methods_only=True)
