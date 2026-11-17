"""THE UPDATE BY CADENCE — `louise hold collection update <name> [--dry-run]`
(docs/notes/collection-updates.md §1–§3; operator, 2026-11-17: "Monthly
full dumps at night", plug and play from the Codex's `update` block).

A collection keeps its identity as its origin moves (02 §2.7, amended
2026-11-17, B): its next snapshot follows the origin's next run under the
same `collection_id`, and names that run (09 §9.4 `origin_run`). The pass is
GENERIC — every step belongs to the platform; a plugin declares only how its
origin says a run is complete (`update_completion`; wikipedia's is
Wikimedia's dumpstatus.json, app/plugins/wikipedia/completion.py). A plugin
that declares none is not updated by this pass, and the pass says so.

THE STEPS, each committed and only then recorded in the marker
(`state/update.json`, app/hold/update_state.py — invariant 2):

  due       the period after the newest snapshot's has closed (09 §9.4), and
            the origin says the run its cutoff names — the last run before
            00:00 on `update.day` in `origin.clock` (02 §2.2) — is complete
            for every slice held
  fetch     every held slice's files of that run, through the acquisition
            engine: the one budget (invariant 5), the origin's etiquette, a
            free-space check first, resumable at the byte
  run       the collection reads the new run (`moved`, the current run of
            app/hold/origin_run.py), and its scan re-points what is the same,
            re-opens what changed and withdraws what the run no longer holds
  records   `records build` carrying every line whose origin bytes are
            unchanged (`--records-from` itself; a line whose locator alone
            moved is taken with its new `origin`, app/pack/build.py)
  snapshot  `snapshot create`: the delta of the previous one, only the parts
            that changed re-issued, `origin_run` the new run; signed where
            this node has a key
  embed     what changed, by the node's own embed stage (vectors carried by
            content and source hash, #371); waited for, never done here
  old-run   the previous run's files deleted from the corpus once the new
            snapshot is published and the annotations cascade it triggers has
            started (app/hold/cascade.py) — never before (06 §6.2)

A crash resumes at the step the marker names; under deploy/supervise-pass.sh
or the worker's update keeper (app/worker.py) the pass is simply run again.
A stop finishes the current unit and exits 130. A step that FAILS is said in
plain words (`hold collection show`), retried at the next night, at most
MAX_TRIES times, and then waits for a human (`--retry`). WAITING — for the
origin, for the node's scan or embedding, for the gate — is not failing:
exit 3, nothing counted (07 §7.7; CLAUDE.md "Deferral is not failure").

The exits, as deploy/supervise-pass.sh reads them: 0 done or nothing due;
1 a step failed (counted); 2 refused (no completion check, waits for a
human, the licence); 3 waiting; 130 stopped.

Standardised alternative, not taken: a workflow engine's resumable DAG
(Airflow's tasks with retries, Dagster's runs) is this marker and its
steps; adopting one means a service beside the worker for one linear chain
per collection, and it would pay once several collections' updates chain
into each other — which is the annotations cascade's graph (§4), not this.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import origin_run, update_state

STEPS = ("due", "fetch", "run", "records", "snapshot", "embed", "old-run")
#: the steps that write what the node's worker writes (the manifest's index
#: marks, the text index, the SHARD): they take the collection's write lock
#: (07 §7.7), which the node holds while it runs the collection
LOCKED = ("run", "records", "snapshot")
#: what each step does, as app/node_state.py's table of what a mode runs
#: names it; None: a look, nothing run
ACTIVITY = {"due": "fetch", "fetch": "fetch", "run": "parse", "records": "parse",
            "snapshot": "parse", "embed": None, "old-run": "fetch"}
MAX_TRIES = 3
EXIT_DONE, EXIT_FAILED, EXIT_REFUSED, EXIT_WAITING, EXIT_STOPPED = 0, 1, 2, 3, 130
#: the embed step's bounded count of what is left (status.py's WAITING_CAP)
EMBED_COUNT_CAP = 1_000_000
UPDATE_LOCK = "update.lock"


class _Waiting(Exception):
    """Not now, and nothing wrong: exit 3."""


class _Refused(Exception):
    """A rule stands in the way: exit 2."""


class _Failed(Exception):
    """The step failed: said, counted, retried at the next night."""


class _Stopped(Exception):
    """A stop, honoured at the unit: exit 130."""


@dataclass
class Outcome:
    state: str                       # done | not-due | waiting | stopped | failed | refused | dry-run | updated
    says: str
    exit: int
    period: str | None = None
    run: str | None = None
    step: str | None = None
    plan: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


# --------------------------------------------------------------------------
# what a collection's update is, read off its Codex and the node

def hook_of(codex):
    """The plugin's completion check (`update_completion`), or None."""
    from app import collections as collections_mod
    plugin = collections_mod.PLUGIN_REGISTRY.get(getattr(codex, "plugin", None))
    return getattr(plugin, "update_completion", None)


def not_updated(codex) -> str | None:
    """Why this pass does not update a collection, in plain words; None
    when it does."""
    from .codex import is_annotations
    if is_annotations(codex):
        return "an annotations collection is updated by its method or its claims, not by its origin"
    if getattr(codex, "update", None) is None:
        return "its Codex declares no `update`"
    if not origin_run.is_dated(codex):
        return (f"its origin ({codex.plugin}) is not an origin of dated dumps; the update by "
                f"cadence follows dated runs (09 §9.17 `delta`)")
    if hook_of(codex) is None:
        return (f"the {codex.plugin} plugin declares no completion check (`update_completion`), "
                f"so this pass cannot tell when the origin's run is complete")
    return None


def _plugin_at_codex(codex):
    """The plugin parsing at the Codex's parser (`collections.plugin_at`):
    what the collection's records were written by and are read with."""
    from app import collections as collections_mod
    plugin = collections_mod.PLUGIN_REGISTRY.get(getattr(codex, "plugin", None))
    version = getattr(getattr(codex, "parser", None), "version", None)
    if plugin is None or version is None:
        return plugin
    return collections_mod.plugin_at(plugin, int(version)) or plugin


def origin_names_run(codex) -> bool:
    """The plugin, AT THE CODEX'S PARSER, declares that each record's
    `origin` names the dated file and byte range of its run
    (`origin_names_run`): a new run would re-issue every part. Wikipedia's
    parser 5 does; its parser 6 names the page, resolved in the snapshot's
    run through the origin's own index (operator, 2026-11-17, "Choice B";
    collection-updates.md §6). The pass plans such a collection
    (`--dry-run`) and never runs it: it is re-created at the parser that
    names the page first."""
    return bool(getattr(_plugin_at_codex(codex), "origin_names_run", False))


def current_parser(codex) -> int | None:
    """The plugin's own parser version, which a refused collection is
    re-created at."""
    from app import collections as collections_mod
    plugin = collections_mod.PLUGIN_REGISTRY.get(getattr(codex, "plugin", None))
    return int(plugin.PARSER_VERSION) if plugin is not None and hasattr(plugin, "PARSER_VERSION") else None


def origin_form_wait(root, name: str, codex) -> str:
    """The refusal of a collection whose origin names the run, in plain
    words, with what a run would re-issue: the newest snapshot's parts and
    their bytes (its header, kept once per file, app/share/seeder.py)."""
    from app.share import seeder
    try:
        snap = seeder.current_snapshot(Path(root) / name / "shard")
    except Exception:   # noqa: BLE001 — the refusal is said whatever the snapshot reads
        snap = None
    size = (f" ({name}: {len(snap.shards):,} parts, {sum(int(s.bytes) for s in snap.shards) / 2**30:.1f} GiB "
            f"to every peer)" if snap is not None else "")
    now = current_parser(codex)
    at = getattr(getattr(codex, "parser", None), "version", "?")
    return (f"not updated by this pass: every part would be re-issued{size} because each record's origin "
            f"names the dated file and byte range (parser {at}); re-create at parser {now} first, whose "
            f"origin names the page (collection-updates.md §6)")


def held_slices(collection, spec) -> list[str]:
    """The slices the node holds and the origin delivers whole."""
    from app.acquire import spec as acquire_spec
    slices = getattr(collection, "slices", None)
    if slices in (None, ()):
        slices = acquire_spec.slices_for_profile(spec, getattr(collection, "profile", None))
    if slices == "all":
        return sorted(spec.slice_profiles)
    out = []
    for s in slices or ():
        key = acquire_spec.delivered_slice(spec, s)
        if key is not None and key not in out:
            out.append(key)
    return sorted(out)


def next_period(root, name: str, codex) -> tuple[str, str, datetime] | None:
    """(the newest snapshot's id, the next one's, its cutoff) — None when
    the collection holds no snapshot yet (its first is built by hand)."""
    from . import snapshot as sn
    newest = origin_run.newest_snapshot(Path(root) / name / "shard")
    if newest is None:
        return None
    sid = newest.name[:-5]
    nxt = sn.next_snapshot_id(sid)
    cutoff = sn.cutoff_instant(nxt, cadence=codex.update.cadence, day=codex.update.day,
                               clock=codex.origin.clock)
    return sid, nxt, cutoff


def run_before(runs, cutoff: datetime) -> str | None:
    """02 §2.2's cutoff rule for dated dumps: the last run before the
    instant. A run is dated by the day it began, so a run of the cutoff's
    own day is after it."""
    day = cutoff.strftime("%Y%m%d")
    held = [r for r in runs if origin_run.RUN_RE.match(str(r)) and str(r) < day]
    return max(held) if held else None


def night_of(now: datetime) -> str:
    """The night a moment belongs to: the evening's date (a night spans
    midnight)."""
    return (now.astimezone() - timedelta(hours=12)).date().isoformat()


def _iso(t: datetime | None = None) -> str:
    return (t or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(run: str) -> str:
    return f"{run[:4]}-{run[4:6]}-{run[6:8]}" if origin_run.RUN_RE.match(run or "") else str(run)


def _count(n: int) -> str:
    n = int(n)
    if n < 10_000:
        return f"{n:,}"
    if n < 10_000_000:
        return f"{n / 1000:.0f} k"
    return f"{n / 1e6:.1f} M"


def _gb(n: int | None) -> str:
    return "?" if n is None else f"{n / 1e9:.0f}" if n >= 10e9 else f"{n / 1e9:.1f}"


# --------------------------------------------------------------------------
# the pass

@dataclass
class _Ctx:
    root: Path
    name: str
    codex: Any
    spec: Any
    hook: Any
    collection: Any
    now: datetime
    stop: threading.Event
    log: Callable[[str], None]
    fetch_records: Callable
    fetch_runs: Callable
    locks: list = field(default_factory=list)

    def take_lock(self, what: str):
        """The collection's write lock (07 §7.7), held to the end of the pass."""
        from . import lock as lock_mod
        if self.locks:
            return self.locks[0]
        lk = lock_mod.take(lock_mod.state_dir(self.root, self.name), self.name, what)
        self.locks.append(lk)
        return lk

    def release_lock(self) -> None:
        while self.locks:
            self.locks.pop().release()


def run(root, name: str, *, now: datetime | None = None, stop: threading.Event | None = None,
        log: Callable[[str], None] | None = None, dry_run: bool = False, retry: bool = False,
        fetch_records: Callable | None = None, fetch_runs: Callable | None = None) -> Outcome:
    """The pass, from the step the marker names to as far as it can go now."""
    from app import collections as collections_mod
    from app.acquire import completion

    from . import collection as col
    from . import lock as lock_mod
    root = Path(root)
    name, codex = col.resolve(root, name)
    say = log or (lambda m: print(f"[update] {name}: {m}", file=sys.stderr, flush=True))
    why = not_updated(codex)
    if why is not None:
        return Outcome("refused", f"not updated by this pass: {why}", EXIT_REFUSED)
    collection = collections_mod.by_name(name)
    if collection is None or Path(collection.manifest_path).resolve() != \
            (root / name / "state" / "manifest.sqlite").resolve():
        return Outcome("refused", f"{name} is not a collection this node runs from {root} (its file "
                                  f"under collections/ names it); the update fetches into its corpus",
                       EXIT_REFUSED)
    if getattr(collection, "source", "origin") == "shard":
        return Outcome("refused", "a copy held from peers is updated by `share fetch`, not from the origin",
                       EXIT_REFUSED)
    ctx = _Ctx(root=root, name=name, codex=codex, spec=origin_run.spec_of(codex), hook=hook_of(codex),
               collection=collection, now=now or datetime.now(timezone.utc),
               stop=stop or threading.Event(), log=say,
               fetch_records=fetch_records or completion.records, fetch_runs=fetch_runs or completion.runs)
    if dry_run:
        # it still PLANS a collection whose origin names the run: what a run
        # would fetch is what the operator's decision is sized by
        return _dry_run(ctx)
    if origin_names_run(codex):
        return Outcome("refused", origin_form_wait(root, name, codex), EXIT_REFUSED)
    # ONE PASS AT A TIME per collection: the worker's keeper and an
    # operator's run never interleave their steps
    try:
        guard = lock_mod.take(lock_mod.state_dir(root, name), name, "hold collection update",
                              file=UPDATE_LOCK)
    except lock_mod.Held as e:
        return Outcome("waiting", f"another update pass of {name} runs: {e}", EXIT_WAITING)
    try:
        return _run(ctx, retry=retry)
    finally:
        ctx.release_lock()
        guard.release()


def _run(ctx: _Ctx, *, retry: bool) -> Outcome:
    mk = update_state.read(ctx.root, ctx.name)
    failure = (mk or {}).get("failure")
    if failure and not retry:
        tries = int(failure.get("tries") or 0)
        if tries >= MAX_TRIES:
            return Outcome("refused", f"waits for a human: step {failure.get('step')} failed {tries} times, "
                                      f"last: {failure.get('error')} — `louise hold collection update "
                                      f"{ctx.name} --retry` once it is fixed", EXIT_REFUSED,
                           period=mk.get("period"), run=mk.get("run"), step=mk.get("step"))
        if failure.get("night") == night_of(ctx.now):
            return Outcome("waiting", f"step {failure.get('step')} failed tonight ({failure.get('error')}); "
                                      f"tried again next night ({tries} of {MAX_TRIES})", EXIT_WAITING,
                           period=mk.get("period"), run=mk.get("run"), step=mk.get("step"))
    if failure and retry and mk is not None:
        mk = {**mk, "failure": None}
        update_state.write(ctx.root, ctx.name, mk)
    in_progress = mk is not None and mk.get("step") not in (None, "due", "done")
    if not in_progress:
        try:
            out = _due(ctx, mk)
        except Exception as e:   # noqa: BLE001 — said and counted like any step's failure
            tries = _note_failure(ctx, "due", f"{type(e).__name__}: {e}")
            return Outcome("failed", f"step due failed ({tries} of {MAX_TRIES}): {type(e).__name__}: {e}",
                           EXIT_FAILED, step="due")
        if out is not None:
            return out
        mk = update_state.read(ctx.root, ctx.name)
    while mk.get("step") not in (None, "done"):
        step = mk["step"]
        if ctx.stop.is_set():
            return Outcome("stopped", f"stopped before step {step}; resumes there", EXIT_STOPPED,
                           period=mk.get("period"), run=mk.get("run"), step=step)
        started = _iso()
        try:
            mk = _STEPS[step](ctx, mk, started)
        except _Stopped as e:
            return Outcome("stopped", f"stopped in step {step} ({e}); resumes there", EXIT_STOPPED,
                           period=mk.get("period"), run=mk.get("run"), step=step)
        except _Waiting as e:
            _note_waiting(ctx, step, str(e))
            return Outcome("waiting", str(e), EXIT_WAITING, period=mk.get("period"), run=mk.get("run"),
                           step=step)
        except _Refused as e:
            return Outcome("refused", str(e), EXIT_REFUSED, period=mk.get("period"), run=mk.get("run"),
                           step=step)
        except Exception as e:   # noqa: BLE001 — every failure of a step is said, counted, retried
            said = f"{type(e).__name__}: {e}" if not isinstance(e, _Failed) else str(e)
            tries = _note_failure(ctx, step, said)
            ctx.log(f"step {step} failed ({tries} of {MAX_TRIES}): {said}")
            return Outcome("failed", f"step {step} failed ({tries} of {MAX_TRIES}): {said}", EXIT_FAILED,
                           period=mk.get("period"), run=mk.get("run"), step=step)
    return Outcome("done", describe(ctx.root, ctx.name, ctx.codex)["says"], EXIT_DONE,
                   period=mk.get("period"), run=mk.get("run"), step="done")


def _note_waiting(ctx: _Ctx, step: str, says: str) -> None:
    """What the pass waits for, beside the step (not a step's commit)."""
    mk = update_state.read(ctx.root, ctx.name) or {}
    mk["waiting"] = {"step": step, "says": says, "at": _iso()}
    update_state.write(ctx.root, ctx.name, mk)


def _note_failure(ctx: _Ctx, step: str, error: str) -> int:
    mk = update_state.read(ctx.root, ctx.name) or {}
    prev = mk.get("failure") or {}
    tries = int(prev.get("tries") or 0) + 1 if prev.get("step") == step else 1
    mk["failure"] = {"step": step, "error": error[:1000], "tries": tries, "at": _iso(),
                     "night": night_of(ctx.now)}
    mk.pop("waiting", None)
    update_state.write(ctx.root, ctx.name, mk)
    return tries


def _done(ctx: _Ctx, mk: dict, step: str, started: str, counts: dict, **fields) -> dict:
    """The step's record, written AFTER its durable commit; the next step."""
    nxt = STEPS[STEPS.index(step) + 1] if step != STEPS[-1] else "done"
    steps = dict(mk.get("steps") or {})
    steps[step] = {"start": (steps.get(step) or {}).get("start", started), "end": _iso(), "counts": counts}
    mk = {**mk, **fields, "steps": steps, "step": nxt, "failure": None}
    mk.pop("waiting", None)
    if nxt == "done":
        mk["finished"] = _iso()
    update_state.write(ctx.root, ctx.name, mk)
    ctx.log(f"step {step} done: {counts}")
    return mk


# ---- due --------------------------------------------------------------------

def _due(ctx: _Ctx, mk: dict | None, *, write: bool = True) -> Outcome | None:
    """None when an update is due (the marker then names step `fetch`);
    else what the pass says instead."""
    period = next_period(ctx.root, ctx.name, ctx.codex)
    if period is None:
        return Outcome("not-due", "holds no snapshot yet: its first is built by `hold records build` "
                                  "and `hold snapshot create`, then the update follows", EXIT_DONE)
    newest, nxt, cutoff = period
    if ctx.now < cutoff:
        return Outcome("not-due", f"up to date ({newest}); the next update, {nxt}, after "
                                  f"{cutoff:%Y-%m-%d %H:%M} {ctx.codex.origin.clock}", EXIT_DONE, period=nxt)
    started = _iso()
    try:
        runs = ctx.fetch_runs(ctx.spec)
        run_ = run_before(runs, cutoff)
        if run_ is None:
            raise _Waiting(f"{ctx.hook.origin} offers no run before {cutoff:%Y-%m-%d} yet "
                           f"(of {len(runs)} listed)")
        slices = held_slices(ctx.collection, ctx.spec)
        if not slices:
            return Outcome("refused", "holds no slice its origin delivers whole, so there is nothing to "
                                      "fetch", EXIT_REFUSED, period=nxt)
        recs = ctx.hook.records(run_, slices)
        bodies = ctx.fetch_records(ctx.spec, recs)
        pending = [v for v in (ctx.hook.verdict(r, bodies.get(r.rel_path)) for r in recs) if v]
        if pending:
            raise _Waiting(f"waiting for {ctx.hook.origin}'s {_day(run_)} run to complete: "
                           + "; ".join(pending[:4]) + (f"; and {len(pending) - 4} more" if len(pending) > 4 else ""))
    except _Waiting as e:
        if write:
            _write_due_waiting(ctx, mk, nxt, str(e))
        return Outcome("waiting", str(e), EXIT_WAITING, period=nxt)
    except Exception as e:
        from app.acquire.completion import OriginAway
        if not isinstance(e, OriginAway):
            raise
        if write:
            _write_due_waiting(ctx, mk, nxt, f"the origin did not answer: {e}")
        return Outcome("waiting", f"the origin did not answer: {e}", EXIT_WAITING, period=nxt)
    if not write:
        return Outcome("due", f"due: {nxt} from {ctx.hook.origin}'s {_day(run_)} run", EXIT_DONE,
                       period=nxt, run=run_)
    current = origin_run.current_run(ctx.root, ctx.name, ctx.codex, spec=ctx.spec)
    doc = {"collection_id": ctx.codex.collection_id, "period": nxt, "run": run_,
           "from": {"snapshot": newest, "run": current}, "step": "due", "moved": False, "scan": None,
           "steps": {}, "failure": None, "last": _last_of(mk)}
    _done(ctx, doc, "due", started, {"slices": len(slices), "runs_offered": len(runs),
                                     "complete": len(recs)})
    return None


def _write_due_waiting(ctx: _Ctx, mk: dict | None, period: str, says: str) -> None:
    update_state.write(ctx.root, ctx.name, {
        "collection_id": ctx.codex.collection_id, "period": period, "step": "due",
        "moved": False, "waiting": {"step": "due", "says": says, "at": _iso()},
        "last": _last_of(mk)})


def _last_of(mk: dict | None) -> dict | None:
    """The finished update a new period's marker keeps a line of."""
    if mk is None:
        return None
    if mk.get("step") == "done":
        return {"period": mk.get("period"), "run": mk.get("run"), "finished": mk.get("finished"),
                "counts": ((mk.get("steps") or {}).get("snapshot") or {}).get("counts")}
    return mk.get("last")


# ---- fetch ------------------------------------------------------------------

def _at_run(ctx: _Ctx, run_: str):
    return dataclasses.replace(ctx.collection, snapshot=run_)


def _fetch(ctx: _Ctx, mk: dict, started: str) -> dict:
    from app import db
    from app.acquire import engine
    from app.acquire.spec import DiskPreflightError, LicenseAckRequiredError
    c = _at_run(ctx, mk["run"])
    profile = c.profile
    if not profile:
        raise _Refused(f"{ctx.name}: no profile resolves its slices, so nothing can be fetched")
    con = db.connect(c)
    try:
        try:
            got = engine.run_acquisition(con, c, ctx.spec, profile, stop=ctx.stop)
        except LicenseAckRequiredError as e:
            raise _Refused(str(e)) from None
        except DiskPreflightError as e:
            raise _Failed(f"not enough free space for run {mk['run']}: {e}") from None
        if ctx.stop.is_set() or got.get("stopped") or got.get("aborted"):
            raise _Stopped(str(got.get("stopped") or "the fetch was interrupted, partial files kept"))
        if got.get("failed"):
            raise _Failed(f"{got['failed']} of {got['total']} file(s) of run {mk['run']} failed to fetch; "
                          f"each is tried again after its backoff")
        plan = engine.plan_acquisition(con, c, ctx.spec, profile)
    finally:
        con.close()
    if plan.files_pending:
        raise _Waiting(f"{plan.files_pending} file(s) of run {mk['run']} still to fetch "
                       f"({got.get('waiting', 0)} waiting out a backoff)")
    return _done(ctx, mk, "fetch", started, {
        "files": plan.files_total, "fetched": got.get("fetched", 0), "adopted": got.get("adopted", 0),
        "bytes": plan.bytes_total})


# ---- run --------------------------------------------------------------------

def _run_moves(ctx: _Ctx, mk: dict, started: str) -> dict:
    from app import collections as collections_mod
    from app import db, discovery

    from . import lock as lock_mod
    run_ = mk["run"]
    if not mk.get("moved"):
        # THE CURRENT RUN MOVES (origin_run.current_run): written first, a
        # commit of its own — from here discovery, acquisition and the next
        # snapshot read the new run, and a crash resumes with it moved
        mk = {**mk, "moved": True,
              "steps": {**(mk.get("steps") or {}), "run": {"start": started}}}
        update_state.write(ctx.root, ctx.name, mk)
        ctx.log(f"the collection reads run {run_} now")
    if not _scanned(mk, run_):
        try:
            ctx.take_lock("hold collection update (the scan of the new run)")
        except lock_mod.Held as e:
            raise _Waiting(f"the node scans run {run_} itself (it holds {ctx.name}: {e})") from None
        c = collections_mod.by_name(ctx.name)
        if c is None or c.snapshot != run_:
            raise _Failed(f"the collection does not read run {run_} after the move "
                          f"(it reads {getattr(c, 'snapshot', None)})")
        con = db.connect(c)
        try:
            stats = discovery.sync_collection(con, c, stop=ctx.stop)
        finally:
            con.close()
        if stats.get("interrupted"):
            raise _Stopped("the scan of the new run, committed batch by batch")
        if stats.get("error"):
            raise _Failed(f"the scan of run {run_}: {stats['error']}")
        mk = update_state.read(ctx.root, ctx.name) or mk
    if not _scanned(mk, run_):
        scan = mk.get("scan") or {}
        raise _Failed(f"the scan of run {run_} met {((scan.get('counts') or {}).get('container_errors') or '?')} "
                      f"file(s) it could not enumerate (the collection's log names them); nothing "
                      f"is built from a run read in part")
    return _done(ctx, mk, "run", started, dict((mk.get("scan") or {}).get("counts") or {}))


def _scanned(mk: dict, run_: str) -> bool:
    scan = mk.get("scan") or {}
    return scan.get("run") == run_ and bool(scan.get("complete"))


def note_scan(collection, stats: dict) -> None:
    """Discovery's last word on a scan (app/discovery.py `sync_collection`,
    after its last commit): when the collection is in an update whose run
    has moved and this scan read that run whole, the marker says so — by
    whichever process ran it, this pass or the node's worker. One stat of
    the marker for every other scan."""
    run_ = getattr(collection, "snapshot", None)
    if not run_ or stats.get("interrupted") or stats.get("error"):
        return
    p = Path(collection.manifest_path).parent / update_state.FILE
    if not p.exists():
        return
    mk = update_state.read_at(p)
    if mk is None or mk.get("step") != "run" or not mk.get("moved") or mk.get("run") != run_:
        return
    errors = int(stats.get("container_errors") or 0)
    update_state.write_at(p, {**mk, "scan": {
        "run": run_, "complete": errors == 0, "at": _iso(),
        "counts": {k: stats.get(k, 0) for k in ("seen", "added", "changed", "missing_txt", "relocated",
                                                 "withdrawn", "container_errors")}}})


# ---- records, snapshot --------------------------------------------------------

def _locked(ctx: _Ctx, what: str) -> None:
    from . import lock as lock_mod
    try:
        ctx.take_lock(f"hold collection update ({what})")
    except lock_mod.Held as e:
        raise _Waiting(f"{what} needs the collection's writers, and {e}; the worker's update keeper "
                       f"pauses it itself, by hand `louise node pause {ctx.name}`") from None


def _records(ctx: _Ctx, mk: dict, started: str) -> dict:
    from . import equivalence
    from . import records as rec
    _locked(ctx, "the records build")
    try:
        equivalence.require_reference(ctx.root, ctx.name, ctx.codex, use="build its records")
    except equivalence.NotEquivalent as e:
        raise _Refused(str(e)) from None
    got = rec.build(ctx.root, ctx.name, corpus_dir=ctx.collection.corpus_dir, snapshot_id=mk["period"],
                    records_from=ctx.name, stop=ctx.stop, log=ctx.log)
    if got.interrupted:
        raise _Stopped("the records build, staged and resumable")
    return _done(ctx, mk, "records", started, {"records": got.records, "parts": got.shards,
                                               "skipped": got.skipped})


def _snapshot(ctx: _Ctx, mk: dict, started: str) -> dict:
    from . import snapshot as sn
    _locked(ctx, "the snapshot")
    snap = sn.create(ctx.root, ctx.name, snapshot_id=mk["period"], log=ctx.log)
    if snap.origin_run != mk["run"]:
        raise _Failed(f"snapshot {snap.snapshot_id} names run {snap.origin_run}, the update {mk['run']}")
    prev = sn._previous(ctx.root / ctx.name / "shard", snap.snapshot_id)
    signed = 0
    try:
        from app.bundles import keys as node_keys
        key = node_keys.load_signing_key()
    except Exception as e:   # noqa: BLE001 — no key: held unsigned (09 §9.4), and never published
        ctx.log(f"snapshot {snap.snapshot_id} unsigned: {e}")
    else:
        signed = len(sn.sign_snapshot(ctx.root, ctx.name, snap, key, now=_iso())["signatures"])
    d = snap.delta
    out = _done(ctx, mk, "snapshot", started, {
        "records": snap.records, "added": len(d.added), "changed": len(d.changed),
        "tombstoned": len(d.tombstoned), "parts": len(snap.shards),
        "reissued": len(sn.reissued(prev, snap)), "signed": signed, "hash": snap.hash})
    ctx.release_lock()                    # the embedding is the node's, which needs it back
    return out


# ---- embed ------------------------------------------------------------------

#: the four (rdf_done, fts_done) a document waiting for its vectors can be at
_WAITING_AT = ((0, 0), (0, 1), (1, 0), (1, 1))
#: the first document still waiting for its vectors from a rowid on: each
#: probe a full equality prefix of idx_documents_scope with its rowid tail,
#: so the walk starts AT the cursor and stops at the first that is not
#: errored (scheduling.md §5 pitfall 2: the rowid bound is a range only when
#: every column before it is constrained by equality)
EMBED_FIRST_SQL = ("SELECT rowid FROM documents INDEXED BY idx_documents_scope "
                   "WHERE in_scope=1 AND rdf_done=? AND fts_done=? AND embed_done=0 AND rowid>=? "
                   "AND error IS NULL ORDER BY rowid LIMIT 1")
EMBED_LEFT_SQL = ("SELECT COUNT(*) FROM (SELECT 1 FROM documents INDEXED BY idx_documents_scope "
                  "WHERE in_scope=1 AND rdf_done=? AND fts_done=? AND embed_done=0 AND rowid>=? "
                  "AND error IS NULL LIMIT ?)")


def embed_waiting(con, cursor: int = 0) -> int | None:
    """The rowid of the first document from `cursor` on still waiting for
    its vectors, or None."""
    found = [r[0] for r in (con.execute(EMBED_FIRST_SQL, (rdf, fts, cursor)).fetchone()
                            for rdf, fts in _WAITING_AT) if r is not None]
    return min(found) if found else None


def embed_left(con, cursor: int = 0, cap: int = EMBED_COUNT_CAP) -> int:
    return sum(con.execute(EMBED_LEFT_SQL, (rdf, fts, cursor, cap)).fetchone()[0]
               for rdf, fts in _WAITING_AT)


def _embed(ctx: _Ctx, mk: dict, started: str) -> dict:
    from app import collections as collections_mod
    from app import db
    c = collections_mod.by_name(ctx.name) or ctx.collection
    con = db.connect(c)
    try:
        mode = str(db.get_scope(con).get("embed_mode") or c.embed_mode_default or "").lower()
        if mode != "all":
            return _done(ctx, mk, "embed", started, {"embedded_here": False, "mode": mode or "unset"})
        cursor = int(mk.get("embed_cursor") or 0)
        first = embed_waiting(con, cursor)
        if first is not None:
            left = embed_left(con, first)
            if first != cursor:
                mk = {**mk, "embed_cursor": first}
                update_state.write(ctx.root, ctx.name, mk)
            raise _Waiting(f"embedding what changed: {_count(left)}{'+' if left >= EMBED_COUNT_CAP else ''} "
                           f"document(s) to go on the node's encoder")
    finally:
        con.close()
    scan = (mk.get("scan") or {}).get("counts") or {}
    return _done(ctx, mk, "embed", started, {"embedded_here": True,
                                             "reopened": int(scan.get("changed", 0)) + int(scan.get("added", 0))})


# ---- old run ------------------------------------------------------------------

def old_run_files(ctx: _Ctx, prev: str) -> list[str]:
    """The previous run's files the collection held: the spec's files of
    that run, under the collection's profile (no listing asked of the
    origin: a dated run names its files)."""
    from app.acquire import engine
    files = engine._list_and_filter(ctx.spec, ctx.collection.profile, prev)
    return [f.rel_path for f in files if f"/{prev}/" in f"/{f.rel_path}"]


def _old_run(ctx: _Ctx, mk: dict, started: str) -> dict:
    from app import db

    from . import cascade
    prev, run_ = (mk.get("from") or {}).get("run"), mk["run"]
    if not prev or prev >= run_:
        return _done(ctx, mk, "old-run", started, {"files": 0, "bytes": 0, "run": prev})
    gate = cascade.old_run_gate(ctx.root, ctx.name, ctx.codex, mk["period"])
    if not gate.open:
        raise _Waiting(gate.says)
    corpus = Path(ctx.collection.corpus_dir)
    rels = old_run_files(ctx, prev)
    deleted, freed = 0, 0
    from app import derived
    for rel in rels:
        if ctx.stop.is_set():
            raise _Stopped(f"deleting run {prev}, {deleted} of {len(rels)} file(s) gone")
        f = corpus / rel
        # the file, a partial fetch of it, and what was DERIVED from it
        # beside it (a pack under `.pack/`, re-creatable, invariant 6); a
        # derived copy on the fast drive is the cache's to age out
        beside = sorted((f.parent / derived.BESIDE).glob(f.name + "*")) \
            if (f.parent / derived.BESIDE).is_dir() else []
        for p in (f, f.with_name(f.name + ".part"), *beside):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            p.unlink()
            if p.parent == f.parent:
                deleted += 1
            freed += size
    for d in sorted({(corpus / rel).parent for rel in rels}, reverse=True):
        for empty in (d / derived.BESIDE, d):
            try:
                empty.rmdir()
            except OSError:
                pass
    con = db.connect(ctx.collection)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.executemany("DELETE FROM fetch_files WHERE source_id=? AND rel_path=?",
                        [(ctx.spec.source_id, r) for r in rels])
        con.execute("COMMIT")
    finally:
        con.close()
    ctx.log(f"run {prev} left the corpus: {deleted} file(s), {_gb(freed)} GB ({gate.says})")
    return _done(ctx, mk, "old-run", started, {"files": deleted, "bytes": freed, "run": prev})


_STEPS: dict[str, Callable] = {"fetch": _fetch, "run": _run_moves, "records": _records,
                               "snapshot": _snapshot, "embed": _embed, "old-run": _old_run}


# --------------------------------------------------------------------------
# the dry run

def _dry_run(ctx: _Ctx) -> Outcome:
    """What the pass would do now, and what it would fetch: no byte moved,
    nothing written (the origin is asked whether its run is complete)."""
    import shutil

    from app import db
    from app.acquire import engine
    from app.acquire import spec as acquire_spec
    mk = update_state.read(ctx.root, ctx.name)
    in_progress = mk is not None and mk.get("step") not in (None, "due", "done")
    if in_progress:
        period, run_, step = mk["period"], mk["run"], mk["step"]
    else:
        out = _due(ctx, mk, write=False)
        if out.state != "due":
            return dataclasses.replace(out, state="dry-run" if out.exit == EXIT_DONE else out.state)
        period, run_, step = out.period, out.run, "fetch"
    c = _at_run(ctx, run_)
    con = db.connect(c)
    try:
        plan = engine.plan_acquisition(con, c, ctx.spec, c.profile)
    finally:
        con.close()
    declared = acquire_spec.estimated_bytes_for(ctx.spec, c.profile)
    need = int((declared if declared is not None else plan.bytes_pending) * engine._DISK_HEADROOM)
    try:
        free = shutil.disk_usage(c.corpus_dir).free
    except OSError:
        free = None
    slices = held_slices(ctx.collection, ctx.spec)
    fetch = {"files": plan.files_total, "files_pending": plan.files_pending,
             "bytes_total": plan.bytes_total, "bytes_pending": plan.bytes_pending,
             "bytes_estimated": declared, "free": free, "need": need,
             "slices": slices, "profile": c.profile}
    size = plan.bytes_pending or declared
    room = "" if free is None else (f"; {_gb(free)} GB free at {c.corpus_dir}"
                                    + ("" if free >= need else f" — NOT ENOUGH, the fetch needs {_gb(need)} GB"))
    says = (f"update {period} from {ctx.hook.origin}'s {_day(run_)} run"
            + (f", resuming at step {step}" if in_progress else "")
            + f": fetch {plan.files_pending} of {plan.files_total} file(s) of {len(slices)} slice(s), "
              f"{_gb(size)} GB{room}")
    return Outcome("dry-run", says, EXIT_DONE, period=period, run=run_, step=step,
                   plan={"fetch": fetch})


# --------------------------------------------------------------------------
# shown: `hold collection show`, the Library card

def describe(root, name: str, codex=None) -> dict[str, Any]:
    """The update's state in plain words, from the marker and what is on
    disk — never the origin, never a long query."""
    from . import collection as col
    root = Path(root)
    if codex is None:
        name, codex = col.resolve(root, name)
    why = not_updated(codex)
    if why is not None:
        return {"state": "not-updated", "says": f"not updated by cadence: {why}"}
    if origin_names_run(codex):
        return {"state": "waits-recreation", "says": origin_form_wait(root, name, codex)}
    mk = update_state.read(root, name)
    period = next_period(root, name, codex)
    if mk is None:
        if period is None:
            return {"state": "no-snapshot", "says": "no update yet: the first snapshot is built by hand"}
        return {"state": "idle", "says": f"next update {period[1]} after "
                                         f"{period[2]:%Y-%m-%d %H:%M} {codex.origin.clock}"}
    step = mk.get("step")
    out = {"state": step, "period": mk.get("period"), "run": mk.get("run"), "step": step,
           "failure": mk.get("failure"), "waiting": (mk.get("waiting") or {}).get("says")}
    head = f"update {mk.get('period')}"
    failure = mk.get("failure")
    if failure:
        tries = int(failure.get("tries") or 0)
        tail = ("waits for a human (`hold collection update --retry`)" if tries >= MAX_TRIES
                else f"tried again next night ({tries} of {MAX_TRIES})")
        out["says"] = f"{head}: step {failure.get('step')} failed — {failure.get('error')}; {tail}"
        return out
    if step == "done":
        counts = ((mk.get("steps") or {}).get("snapshot") or {}).get("counts") or {}
        changed = sum(int(counts.get(k) or 0) for k in ("added", "changed", "tombstoned"))
        fin = _local_hhmm(mk.get("finished"))
        nxt = (f"; next update {period[1]} after {period[2]:%Y-%m-%d}" if period is not None
               and period[0] == mk.get("period") else "")
        out["says"] = f"{head}: done {fin}, {_count(changed)} records changed{nxt}"
        return out
    if step == "due":
        out["says"] = f"{head}: {(mk.get('waiting') or {}).get('says') or 'due: asking the origin'}"
        return out
    if mk.get("waiting"):
        out["says"] = f"{head}: {mk['waiting']['says']}"
        if step == "fetch":
            out["says"] += "; " + _fetch_progress(root, name, codex, mk)
        return out
    words = {"fetch": lambda: f"fetching {_fetch_progress(root, name, codex, mk)}",
             "run": lambda: f"reading run {mk.get('run')} (what changed is re-opened, what it no longer "
                            f"holds withdrawn)",
             "records": lambda: f"building the records of {mk.get('period')} from run {mk.get('run')}",
             "snapshot": lambda: f"freezing snapshot {mk.get('period')}",
             "embed": lambda: "embedding what changed",
             "old-run": lambda: f"deleting run {(mk.get('from') or {}).get('run')} from the corpus"}
    out["says"] = f"{head}: " + words.get(step, lambda: str(step))()
    return out


def _local_hhmm(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone() \
            .strftime("%H:%M")
    except ValueError:
        return iso


def _fetch_progress(root: Path, name: str, codex, mk: dict) -> str:
    """"7 of 11 languages, 41 of 62 GB": the held slices whose every file
    of the run is on disk whole, and the bytes on disk (a `.part` counted
    as far as it has come) of what the profile is estimated at."""
    from app import collections as collections_mod
    from app.acquire import engine
    from app.acquire import spec as acquire_spec
    try:
        c = collections_mod.by_name(name)
        spec = origin_run.spec_of(codex)
        run_ = mk.get("run")
        slices = held_slices(c, spec)
        corpus = Path(c.corpus_dir)
        done_slices, have = 0, 0
        for s in slices:
            prof = spec.slice_profiles.get(s)
            files = engine._list_and_filter(spec, prof, run_) if prof else []
            whole = bool(files)
            for f in files:
                p = corpus / f.rel_path
                part = p.with_name(p.name + ".part")
                if p.is_file():
                    have += p.stat().st_size
                else:
                    whole = False
                    if part.is_file():
                        have += part.stat().st_size
            done_slices += whole
        total = acquire_spec.estimated_bytes_for(spec, c.profile)
        word = "languages" if all(s.startswith("lang=") for s in slices) else "slices"
        return f"{done_slices} of {len(slices)} {word}, {_gb(have)} of {_gb(total)} GB"
    except Exception as e:   # noqa: BLE001 — a show never fails on progress
        return f"progress unknown ({type(e).__name__})"


# --------------------------------------------------------------------------
# the worker's keeper: what to run next for a collection, and the child

def window_open(now: datetime | None = None) -> tuple[bool, str]:
    """Is it the node's night (config.UPDATE_PROFILES / UPDATE_HOURS)? And
    why, in words. The node's profile in force when it declares one
    (app/cpu_limit.py's cached reading, the same every gate reads); its
    hours otherwise, in `[bandwidth] schedule`'s grammar."""
    from app import config, cpu_limit
    from app.transfer import schedule as schedule_mod
    lim = cpu_limit.current()
    if lim.profile is not None:
        ok = lim.profile in config.UPDATE_PROFILES
        return ok, (f"profile {lim.profile}" + ("" if ok else
                    f": updates run in profile {', '.join(config.UPDATE_PROFILES)} only"))
    local = (now or datetime.now(timezone.utc)).astimezone()
    sched = schedule_mod.parse_schedule(f"{config.UPDATE_HOURS} full; else off")
    ok = sched.factor_at(local.hour * 60 + local.minute) > 0
    return ok, f"{'inside' if ok else 'outside'} the update hours {config.UPDATE_HOURS}"


def step_allowed(step: str | None, state: str) -> bool:
    """The node's mode and profile run what `step` does
    (`node_state.runs`, the one table every gate asks)."""
    from app import node_state
    activity = ACTIVITY.get(step or "")
    return activity is None or node_state.runs(state, activity)


@dataclass(frozen=True)
class Next:
    step: str | None                 # None: nothing to run now
    why: str
    asks_origin: bool = False


def next_step(root, name: str, codex, *, now: datetime | None = None) -> Next:
    """What the keeper would run for this collection now — local reads
    only (the marker, the newest snapshot's id, the clock)."""
    now = now or datetime.now(timezone.utc)
    why = not_updated(codex)
    if why is not None:
        return Next(None, why)
    if origin_names_run(codex):
        return Next(None, f"re-create at parser {current_parser(codex)} first (each record's origin names "
                          f"the run)")
    mk = update_state.read(root, name)
    failure = (mk or {}).get("failure")
    if failure:
        if int(failure.get("tries") or 0) >= MAX_TRIES:
            return Next(None, "waits for a human")
        if failure.get("night") == night_of(now):
            return Next(None, "failed tonight; tried again next night")
    if mk is not None and mk.get("step") not in (None, "due", "done"):
        return Next(mk["step"], f"resumes {mk['period']} at {mk['step']}")
    period = next_period(root, name, codex)
    if period is None:
        return Next(None, "no snapshot yet")
    if now < period[2]:
        return Next(None, f"next after {period[2]:%Y-%m-%d}")
    return Next("due", f"{period[1]} may be due", asks_origin=True)


def run_child(name: str, *, stop: Callable[[], bool], grace_s: float = 300.0, poll_s: float = 1.0,
              argv_extra: tuple[str, ...] = ()) -> tuple[int, dict | None]:
    """`louise hold collection update <name> --json` in a process of its
    own (what deploy/supervise-pass.sh runs too): its stderr is the
    worker's log, its last stdout line the outcome. `stop()` true: SIGTERM —
    it ends its unit and exits 130 — then SIGKILL after `grace_s`. The child
    goes first in line for the kernel's OOM killer (`oom_score_adj`), so an
    out-of-memory takes the pass and never the worker."""
    import json
    import subprocess
    env = {**os.environ, "LOUISE_OOM_SCORE_ADJ": os.environ.get("LOUISE_OOM_SCORE_ADJ", "500")}
    proc = subprocess.Popen([sys.executable, "-m", "app.louise", "hold", "collection", "update", name,
                             "--json", *argv_extra], cwd=str(Path(__file__).resolve().parents[2]),
                            stdout=subprocess.PIPE, stderr=None, text=True, env=env)
    signalled_at = None
    while True:
        try:
            out, _ = proc.communicate(timeout=poll_s)
            break
        except subprocess.TimeoutExpired:
            if signalled_at is None and stop():
                proc.terminate()
                signalled_at = time.monotonic()
            elif signalled_at is not None and time.monotonic() - signalled_at > grace_s:
                proc.kill()
    last = [ln for ln in (out or "").splitlines() if ln.strip()]
    try:
        doc = json.loads(last[-1]) if last else None
    except ValueError:
        doc = None
    return proc.returncode, doc if isinstance(doc, dict) else None


def raise_oom_score() -> None:
    """The pass first in line for the OOM killer when the keeper asked for
    it (`run_child`): raising one's own score needs no privilege."""
    adj = os.environ.get("LOUISE_OOM_SCORE_ADJ")
    if not adj:
        return
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write(str(int(adj)))
    except (OSError, ValueError):
        pass
