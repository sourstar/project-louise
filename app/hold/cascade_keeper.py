"""THE CASCADE'S KEEPER: every annotations collection that DECLARES a source
is woken when the source publishes a snapshot, and updated — in dependency
order, transitively, without an agent (docs/notes/collection-updates.md §4;
operator, 2026-11-17: "a signal to wake up all the annotations relying on
that collection's updates … once wikipedia has completed its one").

ONE PLACE DECIDES. A round (`keep_round`, the worker's claim index keeper,
app/worker.py) walks the held collections in Kahn's order (app/hold/
cascade.py `Declarations.order`) and, for each annotations collection, asks:

  woken      a source it declares has PUBLISHED a snapshot newer than the one
             its last run read (the source's `state/published/<id>.json`,
             backfilled from a signature that has none) — `by`
  due        its METHOD is due (`annotation_methods.due`: the target holds a
             newer snapshot than the claims' pin, or a monthly method's period
             is open and it has not run in it) — today's trigger of
             `maintain`, folded in here; `maintain` is this round restricted
             to the collections with a method
  pending    its `woken.json` names a wake not done (waiting, or failed and to
             be tried again)

Any of the three makes it RUN ONCE, against every declared source's newest
snapshot, unless it must wait — said in `woken.json` (`waiting`, `says`):

  - a source it declares is itself an annotations collection whose own wake
    is pending: it runs after it (the order, transitively);
  - a source it declares has an update by cadence and holds no published
    snapshot of its current period (`cascade.update_in_flight`: the update
    in flight, failed and retried, waiting for a human, refused at an old
    parser, or not begun) — with NO time cap (operator, 2026-11-17:
    dependents never derive from an old dump; it replaced the 24 h wait for
    an update to begin): it does not burn its one snapshot of the period on
    the first source while another is behind;
  - its period's snapshot is already written (one snapshot a period, 09 §9.4,
    `annotations.update_snapshot_id`): "one snapshot a period: runs when
    <next period> opens".

THE RUN, in a child process of its own (`annotation_methods.
derive_from_delta`): a method collection's method by its delta form where it
applies, else whole and said why, then its update re-pinned from the delta;
a collection of claims given by hand (its claims file held here) re-checked,
`update(since=…)`. The child's stop and OOM handling are annotation_methods'
(`_held_against_stop`, `Stopped`, `ChildFailed`); a child killed by a signal
is tried again `KILLED_TRIES` times, any other failure is said once and left
until a source moves (per process, as `maintain` did), and a worker that
died during a run three times over (`woken.json` found `running`, not
stopped) is failed for good until a source moves — so a run that takes the
worker down cannot loop it. A stop leaves `running` with `stopped`, and the
next start resumes it with the SAME sources; a completed run is never redone
for the same source snapshots.

`woken.json` is written atomically at every change of state — before the
child starts (`running`) and after its durable commits (`done`, `failed`) —
and the update's own snapshot is published by the child's signature, whose
marker wakes the collections declaring THIS one (transitive).

THE COST OF A ROUND: stats and small JSON reads (markers), never a manifest
or a part, once nothing changed — each collection's decision is a function
of its `facts` (the markers' contents, the snapshot listings, the open
period), and is not taken again while they are the same
(tests/hold/test_cascade_keeper.py's cost test). The first round after a
start reads, per collection not run by the cascade yet, the first claims of
its first part (the claims' pin), once.

Standardised alternative, not taken: a workflow engine's dataset-triggered
DAG (Airflow's datasets, Dagster's asset sensors) is this graph and these
wakes; it is a service beside the worker for a graph of a few nodes whose
edges the Codexes and the collection files already declare. It would pay
once the derivations span nodes.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import cascade

#: collection directory -> (facts, outcome, failure key): the last decision,
#: not taken again while its facts are the same
_MEMO: dict[str, tuple[tuple, str, str | None]] = {}
#: (shard tree, snapshot id) -> {target plugin: the snapshot its claims are pinned to}
_PINS: dict[tuple[str, str], dict[str, str]] = {}
#: the loops said, per root
_LOOPS: dict[str, tuple] = {}


def reset() -> None:
    _MEMO.clear()
    _PINS.clear()
    _LOOPS.clear()


def _default_run(log):
    from . import annotation_methods as am

    def run(root, name, method, deltas, stop=None):
        return am.derive_from_delta(root, name, method, deltas, stop=stop, log=log)
    return run


def keep_round(root, *, stop: Callable[[], bool] | None = None, log=print, failed: dict | None = None,
               clock: datetime | None = None, run=None, methods_only: bool = False) -> list[dict[str, Any]]:
    """One round of the cascade (module docstring): what ran, per
    collection, as `maintain` returned it. `run(root, name, method, deltas,
    stop=)` is the step (`derive_from_delta`); `failed` the said-once
    memory, the caller's for the process's life."""
    root = Path(root)
    failed = {} if failed is None else failed
    now = clock or datetime.now(timezone.utc)
    run = run or _default_run(log)
    decl = cascade.declarations(root)
    for s in sorted({s for srcs in decl.sources.values() for s in srcs}):
        _publish_newest(root, s, decl.held.get(s))
    order, cycles = decl.order()
    refused = _refused(root, decl, order, cycles, log)
    done: list[dict[str, Any]] = []
    for a in order:
        if a not in decl.sources or a in refused:
            continue
        if methods_only and not decl.methods.get(a):
            continue
        if stop is not None and stop():
            break
        got = _one(root, a, decl, now=now, failed=failed, log=log, run=run, stop=stop)
        if got == "stopped":
            break
        if isinstance(got, dict):
            done.append(got)
    return done


def _publish_newest(root: Path, name: str, codex) -> None:
    """A source's newest signed snapshot has its published marker: a stat,
    and the backfill where it has none."""
    from . import collection as col
    if codex is None:
        return
    pub = cascade.listing(col.shard_dir_of(root, name)).newest_signed
    if pub is not None and not cascade.published_path(root, name, pub).is_file():
        cascade.backfill(root, name, codex.collection_id, pub)


def _refused(root: Path, decl, order: list[str], cycles: list[list[str]], log) -> set[str]:
    """The annotations collections Kahn's could not order — on a cycle or
    after one — refused this round, and said once while they stay so (the
    files' writers refuse a cycle where it is declared; this is for one
    written by hand)."""
    left = set(decl.sources) - set(order)
    key = tuple(tuple(c) for c in cycles)
    if left and _LOOPS.get(str(root)) != key:
        _LOOPS[str(root)] = key
        loops = "; ".join(cascade.said(c) for c in cycles) or "?"
        log(f"[annotations] not run, their declarations form a cycle ({loops}; each declares the next): "
            f"{', '.join(sorted(left))} — the cascade's order cannot put one after the other; "
            f"`louise hold config apply <name> --set method=` or the Library's box takes a method off")
    elif not left:
        _LOOPS.pop(str(root), None)
    return left


# --------------------------------------------------------------------------
# one collection

def _sources(root: Path, decl, a: str) -> dict[str, dict[str, Any]]:
    """Per declared source: its id, plugin, how it is declared, its newest
    snapshot, its newest PUBLISHED one (signed, with its marker)."""
    from . import collection as col
    out = {}
    for s, via in sorted(decl.sources[a].items()):
        codex = decl.held.get(s)
        lst = cascade.listing(col.shard_dir_of(root, s))
        pub = lst.newest_signed
        if pub is not None and not cascade.published_path(root, s, pub).is_file():
            pub = None
        out[s] = {"id": codex.collection_id, "plugin": getattr(codex, "plugin", None), "via": via, "codex": codex,
                  "newest": lst.newest, "published": pub,
                  "annotations": s in decl.sources}
    return out


def _facts(root: Path, a: str, method: str | None, current: str | None, srcs: Mapping[str, dict],
           now: datetime) -> tuple:
    """What a decision is a function of, read from markers alone."""
    from . import annotation_methods as am
    from . import snapshot as sn
    w = cascade.woken(root, a)
    mk = am.read_marker(root, a) if method else None
    per = tuple((s, f["newest"], f["published"], cascade.update_in_flight(root, s, f["codex"], now=now),
                 (cascade.woken(root, s) or {}).get("state") if f["annotations"] else None)
                for s, f in srcs.items())
    opened = sn.snapshot_id_for(now, sn.cadence_of(current)) if current else None
    return (json.dumps(w, sort_keys=True), json.dumps(mk, sort_keys=True), current, method, per, opened,
            am.open_period("monthly", now) if method else None)


def _pins(tree: Path, current: str) -> dict[str, str]:
    """{target plugin: target_snapshot} from the first claims of the current
    snapshot's first part — what a collection never run by the cascade is
    pinned to; once per snapshot (`annotations_delta._probe`'s reading)."""
    key = (str(tree), current)
    if key in _PINS:
        return _PINS[key]
    from app.pack import records as pack_records
    from app.share import seeder

    from .annotations import _part_file, _prefix, claim_of, end_uid, ends_of
    from .annotations_delta import PROBE_CLAIMS
    out: dict[str, str] = {}
    try:
        snap = seeder.cached_snapshot(tree)
        p = _part_file(tree, snap.shards[0], None, snap.snapshot_id) if snap is not None and snap.shards else None
        for i, r in enumerate(pack_records.iter_records(p) if p is not None else ()):
            if i >= PROBE_CLAIMS:
                break
            c = claim_of(r)
            ends = ends_of(c)
            if ends and c.get("target_snapshot"):
                out.setdefault(_prefix(end_uid(*ends[0])), str(c["target_snapshot"]))
    except Exception:   # noqa: BLE001 — no pin read: the Codex's from_snapshot answers
        out = {}
    _PINS[key] = out
    return out


def _baseline(root: Path, a: str, decl, s: str, f: Mapping[str, Any], w, mk, current: str) -> str | None:
    """The snapshot of `s` that `a`'s last run read: its `woken.json` (a run
    done, or the one before a pending wake), its method's marker, the
    claims' own pin, the Codex's `from_snapshot` — None when nothing says."""
    from . import collection as col
    from .codex import targets_of
    if w is not None:
        ran = (w.get("sources") if w.get("state") == "done" else (w.get("last") or {}).get("sources")) or {}
        if f["id"] in ran:
            return str(ran[f["id"]])
    if mk is not None and mk.get("snapshot") == current:
        got = (mk.get("against") or {}).get(s)
        if got:
            return str(got)
    if f["via"] != "annotates":
        return None
    pin = _pins(col.shard_dir_of(root, a), current).get(f["plugin"] or "")
    if pin:
        return pin
    for t in targets_of(decl.held[a]):
        if s in decl.holders.get(t.collection_id, ()):
            return t.from_snapshot
    return None


def _key(a: str, method: str | None, wanted: Mapping[str, str], period: str | None) -> str:
    return f"{a}@{method}@{json.dumps(dict(wanted), sort_keys=True)}@{period or ''}"


def _gave_up(w, wanted, period) -> bool:
    """A run the worker died during `KILLED_TRIES` times over, for these
    very sources: failed for good, until a source moves."""
    from .annotation_methods import KILLED_TRIES
    return (w is not None and w.get("state") == "failed" and w.get("sources") == dict(wanted)
            and (w.get("period") or None) == (period or None) and int(w.get("crashes") or 0) >= KILLED_TRIES)


def _still_failed(failed: dict, key: str | None, w, wanted, period) -> bool:
    from .annotation_methods import KILLED_TRIES
    seen = failed.get(key) if key else None
    return (isinstance(seen, str) or (isinstance(seen, int) and seen >= KILLED_TRIES)
            or _gave_up(w, wanted, period))


def _write(root: Path, a: str, w, doc: dict[str, Any]) -> dict[str, Any]:
    """`woken.json`, written only when it changes what it says."""
    same = ("sources", "state", "says", "by", "period", "error", "tries", "stopped", "crashes")
    if w is not None and all(w.get(k) == doc.get(k) for k in same):
        return w
    cascade.write_woken(root, a, doc)
    return doc


def _last_of(w) -> dict[str, Any] | None:
    if w is None:
        return None
    if w.get("state") == "done":
        return {"sources": w.get("sources"), "finished": w.get("finished"), "snapshot": w.get("snapshot")}
    return w.get("last")


def _one(root: Path, a: str, decl, *, now: datetime, failed: dict, log, run, stop) -> Any:
    """Decide for `a`, and run it when it is to run: the result `maintain`
    returned, "stopped", or None."""
    from . import annotation_methods as am
    from . import collection as col
    from . import snapshot as sn
    from .annotations import CLAIMS_FILE, AnnotationsError, update_snapshot_id
    method = decl.methods.get(a)
    if method is None and not (root / a / "origin" / CLAIMS_FILE).is_file():
        return None                         # held from a peer: it takes its publisher's snapshots
    current = cascade.listing(col.shard_dir_of(root, a)).newest
    if current is None:
        return None                         # `create` makes the first
    srcs = _sources(root, decl, a)
    if not srcs:
        return None
    facts = _facts(root, a, method, current, srcs, now)
    memo_key = str(root / a)
    memo = _MEMO.get(memo_key)
    w = cascade.woken(root, a)
    if memo is not None and memo[0] == facts:
        if memo[1] in ("idle", "waiting"):
            return None
        if memo[1] == "failed" and _still_failed(failed, memo[2], w, (w or {}).get("sources") or {},
                                                 (w or {}).get("period")):
            return None

    def settle(outcome: str, key: str | None = None) -> None:
        _MEMO[memo_key] = (_facts(root, a, method, current, srcs, now), outcome, key)
    mk = am.read_marker(root, a) if method else None
    newest = {s: f["newest"] for s, f in srcs.items() if f["newest"]}
    wanted = {srcs[s]["id"]: sid for s, sid in newest.items()}
    base = {s: _baseline(root, a, decl, s, f, w, mk, current) for s, f in srcs.items()}

    # a run the worker died during (or was stopped in): resumed, the same sources
    if w is not None and w.get("state") == "running":
        crashes = int(w.get("crashes") or 0) + (0 if w.get("stopped") else 1)
        if crashes >= am.KILLED_TRIES:
            doc = {**w, "state": "failed", "finished": cascade.now_iso(), "stopped": False, "crashes": crashes,
                   "error": f"the worker stopped {crashes} times during it (out of memory?)",
                   "says": "not tried again until a source it declares publishes another snapshot"}
            _write(root, a, w, doc)
            log(f"[annotations] {a}: the run against "
                f"{', '.join(f'{k} {v}' for k, v in sorted((w.get('by') or {}).items())) or 'its sources'} "
                f"failed: {doc['error']}; {doc['says']}")
            settle("failed")
            return None
        return _go(root, a, decl, w, method, dict(w.get("sources") or {}), dict(w.get("by") or {}),
                   w.get("period"), base, srcs, crashes=crashes, reason="resumed: "
                   + ("stopped with the worker" if w.get("stopped") else "the worker stopped during it"),
                   due=None, failed=failed, log=log, run=run, stop=stop, settle=settle)

    by = {s: f["published"] for s, f in srcs.items()
          if f["published"] and base[s] is not None and f["published"] > base[s]}
    d = None
    if method:
        try:
            d = am.due(root, a, method, clock=now)
        except Exception as e:   # noqa: BLE001 — said once, the others go on
            key = f"{a}@{method}@due:{type(e).__name__}:{e}"
            if key not in failed:
                failed[key] = str(e)
                log(f"[annotations] {a}: method {method} cannot be checked: {e}")
            settle("idle")
            return None
        if d.due and d.period is None and d.target_snapshot:
            by.setdefault(d.target, d.target_snapshot)
    pending = w is not None and w.get("state") in ("waiting", "failed")
    if pending:
        by = {**(w.get("by") or {}), **by}
    if not (by or pending or (d is not None and d.due)):
        if d is not None:
            dkey = f"{a}@{method}@{d.period or d.target_snapshot}"
            waiting = d.waiting if d.waiting is not None else d.pinned != d.target_snapshot
            if waiting and failed.get(dkey + "@said") is None:
                failed[dkey + "@said"] = d.reason
                log(f"[annotations] {a}: not derived yet — {d.reason}")
        settle("idle")
        return None
    period = d.period if d is not None and d.period else None
    key = _key(a, method, wanted, period)
    if _still_failed(failed, key, w, wanted, period):
        settle("failed", key)
        return None

    # the waits, said in woken.json: a wake queued is not started (the gate)
    waits = []
    for s, f in srcs.items():
        if f["annotations"]:
            up = cascade.woken(root, s)
            if up is not None and up.get("state") in cascade.PENDING:
                waits.append(f"waits for {s}, whose own update is {up.get('state')}"
                             + (f" ({up['says']})" if up.get("says") else ""))
        why = cascade.update_in_flight(root, s, f["codex"], now=now)
        if why:
            waits.append(why)
    if not waits:
        try:
            update_snapshot_id(current, now)
        except AnnotationsError:
            waits.append(f"one snapshot a period: runs when {sn.next_snapshot_id(current)} opens")
    if waits:
        _write(root, a, w, {"sources": wanted, "state": "waiting", "started": None, "finished": None,
                            "error": None, "tries": int((w or {}).get("tries") or 0) if pending else 0,
                            "by": by, "period": period, "method": method, "says": "; ".join(waits),
                            "last": _last_of(w)})
        settle("waiting")
        return None
    reason = (d.reason if d is not None and d.due else
              "woken by " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) if by else "a wake pending")
    return _go(root, a, decl, w, method, wanted, by, period, base, srcs, crashes=0, reason=reason, due=d,
               failed=failed, log=log, run=run, stop=stop, settle=settle)


def _go(root: Path, a: str, decl, w, method, wanted: dict, by: dict, period, base, srcs, *, crashes: int,
        reason: str, due, failed: dict, log, run, stop, settle) -> Any:
    """The step: `woken.json` at `running` (durably, before the child), the
    child, then `done` or `failed` (after its durable commits)."""
    from . import annotation_methods as am
    key = _key(a, method, wanted, period)
    same = w is not None and w.get("sources") == wanted and w.get("state") != "done"
    tries = (int(w.get("tries") or 0) if same else 0) + 1
    started = cascade.now_iso()
    doc = {"sources": wanted, "state": "running", "started": started, "finished": None, "error": None,
           "tries": tries, "by": by, "period": period, "method": method, "says": reason,
           "last": _last_of(w), **({"crashes": crashes} if crashes else {})}
    _write(root, a, w, doc)
    by_id = {f["id"]: s for s, f in srcs.items()}
    deltas = {}
    for cid, to in wanted.items():
        s = by_id.get(cid)
        if s is None:
            continue
        frm = base.get(s)
        if frm is not None or srcs[s]["via"] == "annotates":
            deltas[s] = [frm or to, to]
    if method:
        log(f"[annotations] {a}: deriving with {method} — {reason}")
    else:
        log(f"[annotations] {a}: re-checking its claims — {reason}")
    try:
        got = run(root, a, method, deltas, stop=stop)
    except am.Stopped:
        _write(root, a, doc, {**doc, "stopped": True, "says": "stopped with the worker; resumes at its next start"})
        settle("running")
        return "stopped"
    except Exception as e:   # noqa: BLE001 — said once; a source's next snapshot is tried
        killed = isinstance(e, am.ChildFailed) and e.exitcode < 0
        seen = failed.get(key)
        fdoc = {**doc, "state": "failed", "finished": cascade.now_iso(), "error": f"{type(e).__name__}: {e}"[:1000]}
        if killed:
            failed[key] = (seen if isinstance(seen, int) else 0) + 1
            if failed[key] < am.KILLED_TRIES:
                _write(root, a, doc, {**fdoc, "says": f"tried again at the next pass ({failed[key]} of "
                                                      f"{am.KILLED_TRIES})"})
                log(f"[annotations] {a}: {e}; tried again at the next pass ({failed[key]} of {am.KILLED_TRIES})")
                settle("failed", key)
                return None
        else:
            failed[key] = f"{type(e).__name__}: {e}"
        _write(root, a, doc, {**fdoc, "says": "not tried again until a source it declares publishes another "
                                              "snapshot" + (" or its next period" if period else "")})
        if method and due is not None:
            when = (f"in {due.period}" if due.period else f"against {due.target} {due.target_snapshot}")
            log(f"[annotations] {a}: method {method} {when} failed ({e}); its claims stay "
                f"pinned to {due.pinned} until `louise hold annotations derive {a}` succeeds or "
                + ("the next period" if due.period else "the target's next snapshot"))
        else:
            log(f"[annotations] {a}: the update against "
                f"{', '.join(f'{k} {v}' for k, v in sorted(by.items())) or 'its sources'} failed ({e}); "
                f"its claims stay as they are until a source it declares publishes another snapshot")
        settle("failed", key)
        return None
    _write(root, a, doc, {**doc, "state": "done", "finished": cascade.now_iso(), "stopped": False,
                          "snapshot": got.get("snapshot_id"), "written": got.get("written"),
                          "says": None})
    if method and due is not None and due.due:
        when = (f"in {due.period}, against {', '.join(f'{k} {v}' for k, v in (got.get('against') or {}).items())}"
                if due.period else f"against {due.target} {due.target_snapshot}")
    else:
        when = "against " + ", ".join(f"{k} {v}" for k, v in (got.get("against") or {}).items())
    unheld = due.target_snapshot if due is not None else "the targets' snapshots"
    log(f"[annotations] {a}: {'derived with ' + method if method else 're-checked'} {when}: snapshot "
        f"{got.get('snapshot_id')} — {got.get('added', 0):,} added, "
        f"{got.get('changed', 0):,} re-pinned or changed, {got.get('tombstoned', 0):,} withdrawn, "
        f"{got.get('dropped_count', 0):,} set aside (not in {unheld} yet)")
    _MEMO.pop(str(root / a), None)
    return {"collection": a, **got}
