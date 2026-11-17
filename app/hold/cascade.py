"""THE ANNOTATIONS CASCADE: what a published snapshot wakes, in what order,
and what each woken collection says (docs/notes/collection-updates.md §4;
the keeper that runs it is app/hold/cascade_keeper.py).

THE OLD RUN'S GATE, read-only — the one question the update by cadence asks
before it deletes the origin's previous run from the corpus (§3; 06 §6.2;
CLAUDE.md invariant 11, the operator's words of 2026-11-17: "Delete the
monthly snapshots once used"): has the snapshot built from the next run been
PUBLISHED, and has the cascade it triggers STARTED? Never before both.

- PUBLISHED: `<shard>/snapshots/<id>.json` and its signatures' sidecar
  `<id>.sig.json` both exist (09 §9.4: a snapshot without a verified
  signature is held in staging by every peer, not promoted).
- STARTED: for every annotations collection held here that DECLARES this
  collection (`annotators`: its Codex `annotates` names it, or the method
  its file records names its plugin among the method's `inputs`), its
  `state/woken.json` has `sources[<collection_id>] == <snapshot_id>` and
  `state` in ("running", "done", "failed"). Vacuously true when none
  declares it. A wake still queued (`waiting`) has not started.

`woken.json` (the cascade's, §4): `{"sources": {"<collection_id>":
"<snapshot_id>", …}, "state": "waiting|running|done|failed", "started":
iso|null, "finished": iso|null, "error": str|null, "tries": int}`, and what
the keeper adds to say it in plain words: `says`, `by` (the sources whose
snapshot woke it, by name), `period` (a monthly method's own period),
`method`, `last` (the previous run's sources and end), `stopped`, `crashes`.

THE PUBLISHED MARKER, `<root>/<name>/state/published/<snapshot_id>.json`:
`{"kind": "louise-published", "version": 1, "collection_id", "snapshot_id",
"snapshot": <its hash>, "delta": {"added", "changed", "tombstoned"} | null,
"at", "by": "signature" | "backfill"}`. Written by the ONE place a snapshot
is signed (`snapshot.sign_snapshot`), whoever publishes it — `snapshot
create`, the update pass, an annotations create or update, a re-creation —
after the signature's durable write, and only once per snapshot hash; a
snapshot signed with no marker (signed before this, or adopted from a peer
with its sidecar) is backfilled from a stat and its sidecar's own `snapshot`
field (`backfill`), never a re-hash. Node-local state (07 §7.8's `state/`),
never shared and never hashed.

WHO IS WOKEN: `declarations` — the one graph the gate, the keeper and the
refusal of a cycle read. An edge S → A when annotations collection A
declares S: a target of A's Codex `annotates` as the held collection that
answers for it (the target itself or its records twin, 02 §2.4 A1f), or a
collection of a plugin its method names in `inputs`. Nothing else is
touched. The order is Kahn's algorithm's (A. B. Kahn, "Topological sorting
of large networks", CACM 1962): a collection comes after every source it
declares; what Kahn's leaves over is a cycle, named (`a_loop`).
"""
from __future__ import annotations

import heapq
import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WOKEN = "woken.json"
#: the states in which the cascade has STARTED for a source snapshot
STARTED = ("running", "done", "failed")
#: the states of a wake not finished: what a collection declaring this one
#: waits for (cascade_keeper)
PENDING = ("waiting", "running")
PUBLISHED_DIR = "published"
PUBLISHED_KIND = "louise-published"
#: a listing or a file whose mtime is this close to when it was read is not
#: trusted on its stamp alone (git's "racy" rule: a timestamp's granularity is
#: a kernel tick, so two writes inside one look the same)
RACY_NS = 2_000_000_000


@dataclass(frozen=True)
class Gate:
    open: bool
    says: str
    published: bool = False
    waiting: tuple[str, ...] = field(default_factory=tuple)     # annotations not started yet

    def to_dict(self) -> dict[str, Any]:
        return {"open": self.open, "says": self.says, "published": self.published,
                "waiting": list(self.waiting)}


def published(shard_dir, snapshot_id: str) -> bool:
    d = Path(shard_dir) / "snapshots"
    return (d / f"{snapshot_id}.json").is_file() and (d / f"{snapshot_id}.sig.json").is_file()


def woken(root, name: str) -> dict[str, Any] | None:
    """An annotations collection's `state/woken.json`, or None (absent, or
    not the cascade's shape: never read as started)."""
    try:
        got = json.loads(woken_path(root, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and isinstance(got.get("sources"), dict) else None


def woken_path(root, name: str) -> Path:
    return Path(root) / name / "state" / WOKEN


def write_woken(root, name: str, doc: Mapping[str, Any]) -> None:
    """`woken.json` whole and atomically (fsync, rename): written at every
    change of state, before a run's durable commits and after them
    (invariant 2)."""
    from .update_state import write_text_atomic
    write_text_atomic(woken_path(root, name), json.dumps(dict(doc), sort_keys=True, indent=1) + "\n")


def annotators(root, collection_id: str) -> list[str]:
    """The annotations collections held under `root` that DECLARE the
    collection `collection_id`, by name: their Codex `annotates` names it
    (or the collection it is a records twin of), or their method's `inputs`
    name its plugin — `declarations`, the one graph."""
    return declarations(root).declaring(collection_id)


def started(root, name: str, collection_id: str, snapshot_id: str) -> bool:
    w = woken(root, name)
    return (w is not None and w["sources"].get(collection_id) == snapshot_id
            and w.get("state") in STARTED)


def old_run_gate(root, name: str, codex, snapshot_id: str) -> Gate:
    """May the origin's run before `snapshot_id`'s leave the corpus now?"""
    from . import collection as col
    if not published(col.shard_dir_of(Path(root), name), snapshot_id):
        return Gate(False, f"waiting for snapshot {snapshot_id} to be published (its signature, "
                           f"snapshots/{snapshot_id}.sig.json)")
    waiting = tuple(a for a in annotators(root, codex.collection_id)
                    if not started(root, a, codex.collection_id, snapshot_id))
    if waiting:
        return Gate(False, f"waiting for the annotations cascade of {snapshot_id} to start: "
                           f"{', '.join(waiting)} not woken yet", published=True, waiting=waiting)
    return Gate(True, f"snapshot {snapshot_id} is published and the annotations that declare "
                      f"{name} have started from it", published=True)


# --------------------------------------------------------------------------
# the published marker

def published_path(root, name: str, snapshot_id: str) -> Path:
    return Path(root) / name / "state" / PUBLISHED_DIR / f"{snapshot_id}.json"


def read_published(root, name: str, snapshot_id: str) -> dict[str, Any] | None:
    try:
        got = json.loads(published_path(root, name, snapshot_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and got.get("kind") == PUBLISHED_KIND else None


def _iso(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _delta_counts(snapshot) -> dict[str, int] | None:
    """The delta's counts where they are in memory — a snapshot just made —
    and None for a delta still in its file (`LazyDelta`, a loaded one: a
    first snapshot's lists every uid, and a marker is not worth reading it)."""
    from . import snapshot as sn
    d = getattr(snapshot, "delta", None)
    if d is None or (isinstance(d, sn.LazyDelta) and d._delta is None):
        return None
    return {"added": len(d.added), "changed": len(d.changed), "tombstoned": len(d.tombstoned)}


def _write_published(root, name: str, doc: dict[str, Any]) -> dict[str, Any]:
    from .update_state import write_text_atomic
    have = read_published(root, name, doc["snapshot_id"])
    if have is not None and have.get("snapshot") == doc["snapshot"]:
        return have                                    # once per snapshot: the first word stays
    write_text_atomic(published_path(root, name, doc["snapshot_id"]),
                      json.dumps(doc, sort_keys=True, indent=1) + "\n")
    return doc


def note_published(root, name: str, snapshot, *, now: str | None = None) -> dict[str, Any]:
    """The snapshot is PUBLISHED: its marker, written by `sign_snapshot`
    after the signature's durable write — idempotent (a snapshot's marker
    is written once per hash; a second signature, a certifier's, keeps it)."""
    return _write_published(root, name, {
        "kind": PUBLISHED_KIND, "version": 1, "collection_id": snapshot.collection_id,
        "snapshot_id": snapshot.snapshot_id, "snapshot": snapshot.hash,
        "delta": _delta_counts(snapshot), "at": now or _iso(), "by": "signature"})


def backfill(root, name: str, collection_id: str, snapshot_id: str) -> dict[str, Any] | None:
    """The marker of a snapshot signed with none (signed before markers
    existed, or adopted from a peer with its sidecar): a stat, the sidecar's
    own `snapshot` field and, where the update pass made it, its marker's
    counts — never a re-hash, never the snapshot's delta read. None when the
    snapshot is not signed or its sidecar does not parse."""
    from . import collection as col
    from . import shard as shard_mod
    from . import update_state
    have = read_published(root, name, snapshot_id)
    if have is not None:
        return have
    sig = col.shard_dir_of(Path(root), name) / shard_mod.snapshot_sig_path(snapshot_id)
    try:
        side = json.loads(sig.read_bytes())
        st = sig.stat()
    except (OSError, ValueError):
        return None
    h = side.get("snapshot") if isinstance(side, dict) else None
    if not isinstance(h, str) or not h:
        return None
    counts = None
    mk = update_state.read(root, name)
    if mk is not None and mk.get("period") == snapshot_id:
        c = ((mk.get("steps") or {}).get("snapshot") or {}).get("counts") or {}
        if c.get("hash") == h:
            counts = {k: int(c.get(k) or 0) for k in ("added", "changed", "tombstoned")}
    return _write_published(root, name, {
        "kind": PUBLISHED_KIND, "version": 1, "collection_id": collection_id, "snapshot_id": snapshot_id,
        "snapshot": h, "delta": counts, "at": _iso(st.st_mtime), "by": "backfill"})


# --------------------------------------------------------------------------
# a SHARD's snapshots, listed once while its directory does not change

@dataclass(frozen=True)
class Listing:
    ids: tuple[str, ...]            # every snapshot id held, sorted
    signed: frozenset[str]          # those with a signatures' sidecar

    @property
    def newest(self) -> str | None:
        return self.ids[-1] if self.ids else None

    @property
    def newest_signed(self) -> str | None:
        return next((s for s in reversed(self.ids) if s in self.signed), None)


_EMPTY = Listing((), frozenset())
#: snapshots directory -> (its stamp, when read, the listing)
_LISTINGS: dict[str, tuple[tuple, int, Listing]] = {}


def _stamp(p: Path) -> tuple | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def listing(shard_dir) -> Listing:
    """The snapshot ids under `<shard>/snapshots` and which are signed: one
    stat of the directory while it is unchanged (a snapshot, a sidecar
    written or renamed into it moves its mtime), a listing when it moved —
    and when its mtime was within `RACY_NS` of the last listing, which is
    then taken again (git's racy-timestamp rule)."""
    d = Path(shard_dir) / "snapshots"
    key = str(d)
    stamp = _stamp(d)
    if stamp is None:
        _LISTINGS.pop(key, None)
        return _EMPTY
    hit = _LISTINGS.get(key)
    if hit is not None and hit[0] == stamp and hit[1] - stamp[2] > RACY_NS:
        return hit[2]
    read_at = time.time_ns()
    try:
        names = [p.name for p in d.iterdir()]
    except OSError:
        return _EMPTY
    ids = tuple(sorted(n[:-5] for n in names if n.endswith(".json") and not n.endswith(".sig.json")
                       and not n.startswith(".")))
    signed = frozenset(n[:-len(".sig.json")] for n in names if n.endswith(".sig.json"))
    got = Listing(ids, signed & frozenset(ids))
    _LISTINGS[key] = (stamp, read_at, got)
    return got


# --------------------------------------------------------------------------
# who is woken: the declarations, one graph

@dataclass(frozen=True)
class Declarations:
    """Every held collection by name, and per annotations collection the
    held collections it declares (source name -> "annotates" | "input")."""
    held: Mapping[str, Any]
    sources: Mapping[str, Mapping[str, str]]
    methods: Mapping[str, str | None]
    #: per annotations collection: the collection ids its Codex `annotates`
    targets: Mapping[str, frozenset[str]]
    #: collection id -> the held collections answering for it (itself, its twins)
    holders: Mapping[str, tuple[str, ...]]

    def dependents(self, source: str) -> list[str]:
        """The annotations collections that declare `source`, by name."""
        return sorted(a for a, srcs in self.sources.items() if source in srcs)

    def declaring(self, collection_id: str) -> list[str]:
        """The annotations collections that declare the collection
        `collection_id`: through a held collection answering for it, or
        naming it in `annotates` when none is held."""
        held = set(self.holders.get(collection_id, ()))
        held |= {n for n, c in self.held.items() if getattr(c, "collection_id", None) == collection_id}
        return sorted(a for a, srcs in self.sources.items()
                      if held & set(srcs) or collection_id in self.targets.get(a, ()))

    def edges(self) -> dict[str, set[str]]:
        """source -> the annotations collections declaring it."""
        out: dict[str, set[str]] = {}
        for a, srcs in self.sources.items():
            for s in srcs:
                out.setdefault(s, set()).add(a)
        return out

    def order(self) -> tuple[list[str], list[list[str]]]:
        """Every collection of the graph in topological order (`kahn`), and
        the cycles it cannot order, each named as a loop of declarations."""
        nodes = set(self.sources) | {s for srcs in self.sources.values() for s in srcs}
        edges = self.edges()
        order, left = kahn(nodes, edges)
        return order, loops(edges, left)


def kahn(nodes: Iterable[str], edges: Mapping[str, Iterable[str]]) -> tuple[list[str], set[str]]:
    """KAHN'S ALGORITHM (A. B. Kahn, CACM 5(11), 1962): repeatedly take a
    node no remaining edge points into; the smallest name first, so the
    order is the same every round. What is left once none can be taken is
    on a cycle, or after one."""
    indeg = {n: 0 for n in nodes}
    for s, outs in edges.items():
        indeg.setdefault(s, 0)
        for t in outs:
            indeg[t] = indeg.get(t, 0) + 1
    ready = [n for n, d in indeg.items() if d == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        n = heapq.heappop(ready)
        order.append(n)
        for t in sorted(edges.get(n, ())):
            indeg[t] -= 1
            if indeg[t] == 0:
                heapq.heappush(ready, t)
    return order, {n for n, d in indeg.items() if d > 0}


def a_loop(edges: Mapping[str, Iterable[str]], left: set[str], start: str | None = None) -> list[str]:
    """One cycle among the nodes Kahn's left, as the chain of declarations
    it is — [a, b, a]: a declares b, b declares a. Every node left has a
    predecessor left (else Kahn's would have taken it), so walking
    predecessors from any of them meets a node twice."""
    preds: dict[str, set[str]] = {}
    for s, outs in edges.items():
        for t in outs:
            preds.setdefault(t, set()).add(s)
    n = start if start is not None else min(left)
    path, seen = [n], {n: 0}
    while True:
        cands = sorted(p for p in preds.get(n, ()) if p in left)
        if not cands:
            return []
        n = cands[0]
        if n in seen:
            return path[seen[n]:] + [n]
        seen[n] = len(path)
        path.append(n)


def loops(edges: Mapping[str, Iterable[str]], left: set[str]) -> list[list[str]]:
    """Every distinct cycle reached from what Kahn's left (each once)."""
    out, covered = [], set()
    for n in sorted(left):
        if n in covered:
            continue
        loop = a_loop(edges, left, n)
        if loop and not set(loop) <= covered:
            out.append(loop)
        covered |= set(loop) | {n}
    return out


def said(loop: Iterable[str]) -> str:
    return " → ".join(loop)


#: root -> (the stamps it was built from, the graph)
_DECLS: dict[str, tuple[tuple, Declarations]] = {}


def reset() -> None:
    """Forget the graph and the listings (a test's, or a process's)."""
    _DECLS.clear()
    _LISTINGS.clear()


def _stamps(root: Path) -> tuple | None:
    """What the graph is a function of, as stamps: every held Codex, every
    records-twin marker, every collection file. None when one of them moved
    too recently to be trusted on its stamp (`RACY_NS`)."""
    from app import collections as collections_mod

    from . import carry
    out = []
    now = time.time_ns()
    paths = []
    try:
        dirs = sorted(Path(root).iterdir()) if Path(root).is_dir() else []
    except OSError:
        dirs = []
    for d in dirs:
        paths += [d / "shard" / "codex.json", d / "state" / carry.MARKER]
    try:
        paths += sorted(collections_mod.collections_dir().glob("*.toml"))
    except OSError:
        pass
    for p in paths:
        st = _stamp(p)
        if st is not None and now - st[2] <= RACY_NS:
            return None
        out.append((str(p), st))
    return tuple(out)


def declarations(root, *, methods: Mapping[str, str | None] | None = None,
                 sources: Mapping[str, Iterable[str]] | None = None) -> Declarations:
    """The graph of what each annotations collection held under `root`
    declares (module docstring): rebuilt when a Codex, a twin marker or a
    collection file moved, else the last one. `methods` and `sources` give
    a proposed declaration (name -> method; name -> target collection ids)
    to check before it is written (`cycle_through`); then nothing is cached."""
    from app import collections as collections_mod

    from . import carry
    from . import collection as col
    from .codex import is_annotations, targets_of
    root = Path(root)
    proposed = methods is not None or sources is not None
    stamps = None if proposed else _stamps(root)
    hit = _DECLS.get(str(root))
    if stamps is not None and hit is not None and hit[0] == stamps:
        return hit[1]
    held = dict(col._held(root))
    files: dict[str, str | None] = {}
    try:
        for c in collections_mod.load_annotations():
            files[c.name] = getattr(c, "method", None)
    except Exception:   # noqa: BLE001 — a listing that cannot be read declares no method
        files = {}
    files.update(methods or {})
    holders: dict[str, list[str]] = {}
    by_plugin: dict[str, list[str]] = {}
    for n, c in held.items():
        if getattr(c, "collection_id", None):
            holders.setdefault(c.collection_id, []).append(n)
        if not is_annotations(c):
            twin = carry.records_twin_of(root / n / "state")
            if twin:
                holders.setdefault(twin, []).append(n)
        plugin = getattr(c, "plugin", None)
        if plugin:
            by_plugin.setdefault(plugin, []).append(n)
    targets: dict[str, frozenset[str]] = {}
    out: dict[str, dict[str, str]] = {}
    annotations = [n for n, c in held.items() if is_annotations(c)] + \
        [n for n in (sources or {}) if n not in held]
    for a in annotations:
        ids = frozenset(sources[a]) if sources is not None and a in sources else \
            frozenset(t.collection_id for t in targets_of(held[a]))
        targets[a] = ids
        srcs: dict[str, str] = {}
        for cid in sorted(ids):
            for s in holders.get(cid, ()):
                if s != a:
                    srcs[s] = "annotates"
        m = files.get(a)
        if m:
            from . import annotation_methods as am
            method = am.METHODS.get(m)
            for p in (method.inputs if method is not None else ()):
                for s in by_plugin.get(p, ()):
                    if s != a:
                        srcs.setdefault(s, "input")
        out[a] = srcs
    got = Declarations(held=held, sources=out, methods={a: files.get(a) for a in out}, targets=targets,
                       holders={k: tuple(v) for k, v in holders.items()})
    if stamps is not None:
        _DECLS[str(root)] = (stamps, got)
    return got


def cycle_through(root, name: str, *, method: str | None = None,
                  targets: Iterable[str] | None = None) -> list[str] | None:
    """The loop of declarations that `name` would close with `method` (its
    file's `method`, the Library's box; None: the one its file records) or
    `targets` (the collection ids an annotations `create` names), as
    [a, b, a]; None when it closes none. Asked BEFORE anything is written,
    so a cycle is refused where it is declared."""
    decl = declarations(root, methods={name: method} if method is not None else {},
                        sources={name: tuple(targets)} if targets is not None else None)
    # breadth first along "declares" from `name`: the shortest way back to it
    came: dict[str, str | None] = {name: None}
    frontier = [name]
    while frontier:
        nxt = []
        for n in frontier:
            for s in sorted(decl.sources.get(n, ())):
                if s == name:
                    loop = [s, n]
                    while came[n] is not None:
                        n = came[n]
                        loop.append(n)
                    return loop[::-1]
                if s not in came:
                    came[s] = n
                    nxt.append(s)
        frontier = nxt
    return None


def refusal(name: str, loop: list[str]) -> str:
    """The words of a cycle refused: the loop, named."""
    return (f"{name} would close a cycle of declarations: {said(loop)} (each declares the next — it annotates "
            f"it or its method reads it), so none of them could run after the others (the cascade's order, "
            f"docs/notes/collection-updates.md §4); declare one of them without the other")


# --------------------------------------------------------------------------
# a source behind its period

#: how many periods a source may be behind before the walk stops counting
#: (a period a month: ~33 years; a loop bound, never reached)
PERIODS_WALKED = 400


def current_period(newest: str, codex, now: datetime) -> str:
    """The source's CURRENT PERIOD: the latest period whose cutoff has
    passed (09 §9.4), walked from its newest snapshot's — that one itself
    while the next one's cutoff is still ahead."""
    from . import snapshot as sn
    period = newest
    for _ in range(PERIODS_WALKED):
        nxt = sn.next_snapshot_id(period)
        if now < sn.cutoff_instant(nxt, cadence=codex.update.cadence, day=codex.update.day,
                                   clock=codex.origin.clock):
            break
        period = nxt
    return period


def update_in_flight(root, name: str, codex=None, *, now: datetime | None = None) -> str | None:
    """Why a dependent waits for `name`, in plain words, or None (operator,
    2026-11-17: dependents never derive from an old dump).

    A declared source that HAS an update by cadence — an `update` block, a
    dated origin and a completion check (`update.not_updated` None) — makes
    its dependents wait while it holds no PUBLISHED snapshot (the snapshot
    and its signature) of its CURRENT PERIOD (`current_period`: the latest
    whose cutoff has passed), which the update pass builds from the run that
    period names (`update.complete_run`). That covers an update in flight,
    one failed and being retried, one waiting for a human, one refused at an
    old parser ("re-create wikipedia at parser 6 first") and one not begun.
    There is no time cap: it waits until the source catches up, and says
    why. A source with no update by cadence never makes anyone wait. Read
    from the listing (a stat while it is unchanged) and, behind, the update
    marker: never the origin, never a part."""
    from . import collection as col
    from . import update as update_mod
    from . import update_state
    if codex is None:
        return None
    try:
        if update_mod.not_updated(codex) is not None:
            return None
        lst = listing(col.shard_dir_of(Path(root), name))
        if lst.newest is None:
            return None                      # its first snapshot is built by hand
        period = current_period(lst.newest, codex, now or datetime.now(timezone.utc))
        if period in lst.signed:
            return None                      # caught up: published (`_publish_newest` backfills its marker)
        refused = update_mod.origin_names_run(codex)
        parser = update_mod.current_parser(codex) if refused else None
    except Exception:   # noqa: BLE001 — a source that cannot be read is said, and waited for
        return f"waits for {name}'s snapshot of its current period (its state cannot be read)"
    if refused:
        return f"waits for {name}'s {period} snapshot: re-create {name} at parser {parser} first"
    if period == lst.newest:
        return f"waits for {name}'s snapshot {period} to be published (signed)"
    mk = update_state.read(root, name) or {}
    failure = mk.get("failure") or {}
    mp = str(mk.get("period") or "")
    if mp and mp <= period and mk.get("step") not in (None, "done"):
        then = f"; then up to {period}" if mp != period else ""
        if failure:
            tries = int(failure.get("tries") or 0)
            if tries >= update_mod.MAX_TRIES:
                return (f"waits for {name}'s update {mp}: step {failure.get('step')} failed {tries} times "
                        f"({failure.get('error')}) and waits for a human (`louise hold collection update "
                        f"{name} --retry`){then}")
            return (f"waits for {name}'s update {mp}: step {failure.get('step')} failed ({tries} of "
                    f"{update_mod.MAX_TRIES}), tried again next night{then}")
        wait = (mk.get("waiting") or {}).get("says")
        return f"waits for {name}'s update {mp}, at step {mk.get('step')}" + (f" ({wait})" if wait else "") + then
    return f"waits for {name}'s update {period} to begin"


# --------------------------------------------------------------------------
# shown: `hold collection show`, the Library card, `node show`

def _hhmm(iso: str | None) -> str:
    from .update import _local_hhmm
    return _local_hhmm(iso)


def says_woken(w: Mapping[str, Any] | None) -> str | None:
    """An annotations collection's line: "woken by wikipedia 2026-11 —
    running, started 03:12", "— done 03:41", or why it waits."""
    if w is None:
        return None
    by = w.get("by") or {}
    head = ("woken by " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) if by
            else f"due in its period {w['period']}" if w.get("period")
            else "updated against " + ", ".join(sorted(str(v) for v in (w.get("sources") or {}).values())))
    state = w.get("state")
    if state == "running":
        tail = f"running, started {_hhmm(w.get('started'))}" + (
            " (stopped with the worker; resumes at its next start)" if w.get("stopped") else "")
    elif state == "done":
        tail = f"done {_hhmm(w.get('finished'))}"
    elif state == "waiting":
        tail = f"waiting: {w.get('says') or 'queued'}"
    elif state == "failed":
        tail = f"failed {_hhmm(w.get('finished'))}: {w.get('error')}" + (f"; {w['says']}" if w.get("says") else "")
    else:
        tail = str(state)
    return f"{head} — {tail}"


def says_source(root, name: str, codex, decl: Declarations | None = None) -> str | None:
    """A source's line: "woke 2 annotations (snapshot 2026-11)" — of the
    annotations collections that declare it, those woken by its newest
    published snapshot — or None when none declares it or nothing is
    published."""
    from . import collection as col
    decl = decl or declarations(root)
    deps = decl.dependents(name)
    if not deps:
        return None
    pub = listing(col.shard_dir_of(Path(root), name)).newest_signed
    if pub is None:
        return None
    cid = codex.collection_id
    woke = [a for a in deps if ((woken(root, a) or {}).get("sources") or {}).get(cid) == pub]
    n = f"{len(woke)}" if len(woke) == len(deps) else f"{len(woke)} of {len(deps)}"
    return f"woke {n} annotations (snapshot {pub})"


def shown(root, name: str, codex) -> dict[str, Any] | None:
    """The cascade's lines for one collection, in plain words (RULE 2): as
    a source, and as an annotations collection; None when neither applies.
    Markers only, never a manifest or a part; never raises."""
    from .codex import is_annotations
    try:
        decl = declarations(root)
        out: dict[str, Any] = {}
        src = says_source(root, name, codex, decl)
        if src:
            out["source"] = src
            out["woke"] = decl.dependents(name)
        if is_annotations(codex):
            w = woken(root, name)
            if w is not None:
                out["woken"] = says_woken(w)
                out["state"] = w.get("state")
        if not out:
            return None
        out["says"] = "; ".join(v for k, v in out.items() if k in ("source", "woken"))
        return out
    except Exception as e:   # noqa: BLE001 — a show never fails on the cascade
        return {"says": f"the cascade's state unknown ({type(e).__name__}: {e})"}


def shown_all(root) -> list[dict[str, Any]]:
    """`node show`'s lines: every held collection the cascade has a word
    about, by name."""
    try:
        decl = declarations(root)
    except Exception as e:   # noqa: BLE001
        return [{"name": None, "says": f"the cascade's state unknown ({type(e).__name__}: {e})"}]
    out = []
    for n in sorted(decl.held):
        got = shown(root, n, decl.held[n])
        if got:
            out.append({"name": n, **got})
    return out


def now_iso(clock: datetime | None = None) -> str:
    return (clock or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
