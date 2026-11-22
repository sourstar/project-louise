"""The Library card's p2p line, computed in ONE CHILD PROCESS of the web's
own (2026-11-22).

MEASURED (München, the web container, load ~12): a warm Library took 10.56 s
with the cards' p2p refreshes running in the web process, and 1.27 s with
them off. They are pure Python (the coverage over every part, the
possession of each), so the threads that run them hold the GIL every
request thread waits for: every helper of every card was slower together
(`_cascade_view` 0.142 s, then 1.724 s, with six refreshes alive). Moving
them off the request THREAD (2026-10-27) was not moving them off the
request PROCESS.

THE STANDARD: process isolation for CPU-bound work beside a server, a
`concurrent.futures.ProcessPoolExecutor`, as app/lance_child.py and
app/parallel.py use it: SPAWNED, never forked (the web holds threads and
SQLite connections), watching its parent (`parallel.watch_parent`). ONE
long-lived worker, so the possession views it builds stay warm between
refreshes (`possession._part_views`: ~0.4 s for 1,600 parts warm, minutes
cold), and the refreshes of every collection queue in it rather than six at
once. The web's refresh thread waits for its answer, and a thread waiting on
a pipe holds no GIL.

A CHILD THAT DIES IS NOT STARTED AGAIN by this process (CLAUDE.md: an OOM
kill is not relaunched until its cause is known): the cards say so, and the
log names the collection it was counting and the peak of its last answer,
the diagnostic that names it the next time. The web's next start starts a
new one. A child that cannot be started (EMFILE, EAGAIN) computes here, as
before: the work is done and the GIL shared. `LIBRARY_P2P_CHILD=0` computes
here always.

BOUNDED (CLAUDE.md: a thread never waits without a bound on an optional
subsystem): an answer not back in `ANSWER_S` stops that child (not a memory
kill, so a new one counts the next line) and the card goes without the line
this time.
"""
from __future__ import annotations

import multiprocessing
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from . import collections as collections_mod
from . import config

_ex: ProcessPoolExecutor | None = None
_lock = threading.Lock()
#: why this process counts no more p2p lines, once its child died
LOST: dict[str, Any] | None = None
#: the child's peak resident set, in KiB, as its last answer said
_last_peak_kib: int | None = None
#: executors stopped for not answering: their other callers' broken futures
#: are that stop, never a death of the child
_retired: set = set()
#: how long a refresh waits for the child's answer: a cold count of the
#: biggest collection's every part, with room
ANSWER_S = 600.0


def compute(collection) -> dict | None:
    """The card's p2p line for `collection`: computed in the child, or here
    where none can be had; `{"lost": …}` once the child died."""
    global _last_peak_kib
    if not config.LIBRARY_P2P_CHILD:
        return compute_here(collection)
    if LOST is not None:
        return {"lost": LOST}
    ex = _executor()
    if ex is None:
        return compute_here(collection)
    try:
        fut = ex.submit(_in_child, collection.name, str(collection.manifest_path))
    except BrokenProcessPool:
        return _lost(collection.name)
    except (OSError, RuntimeError):
        return compute_here(collection)
    try:
        out, peak = fut.result(timeout=ANSWER_S)
    except BrokenProcessPool:
        return None if ex in _retired else _lost(collection.name)
    except TimeoutError:
        _retire(ex, collection.name)
        return None
    _last_peak_kib = peak
    return out


def _retire(ex: ProcessPoolExecutor, name: str) -> None:
    """A child that did not answer in `ANSWER_S`: stopped, and the next
    count starts a new one."""
    global _ex
    with _lock:
        if ex in _retired:
            return
        _retired.add(ex)
        if _ex is ex:
            _ex = None
    print(f"[library] {name}: the Library's p2p count did not answer in {ANSWER_S:.0f} s; its process "
          f"is stopped, and a new one counts the next line", file=sys.stderr, flush=True)
    for proc in list(getattr(ex, "_processes", {}).values()):
        proc.terminate()
    ex.shutdown(wait=False, cancel_futures=True)


def _executor() -> ProcessPoolExecutor | None:
    global _ex
    with _lock:
        if _ex is None:
            from . import parallel
            try:
                _ex = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"),
                                          initializer=parallel.watch_parent)
            except OSError:
                return None
        return _ex


def _lost(name: str) -> dict[str, Any]:
    """The child died while counting `name`: said once in the log, and on
    every card from now on; never started again in this process."""
    global LOST
    with _lock:
        if LOST is None:
            peak = f"{_last_peak_kib / 1024:.0f} MiB" if _last_peak_kib else "not known"
            LOST = {"collection": name, "peak": peak, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            print(f"[library] {name}: the process counting the Library's p2p lines died while counting "
                  f"{name} (its last answer's peak: {peak}); not started again until the web restarts",
                  file=sys.stderr, flush=True)
    return {"lost": LOST}


def _in_child(name: str, manifest_path: str) -> tuple[dict | None, int]:
    """In the child: the collection read again by its manifest's path (the
    physical identity; its name where none matches), its line, and this
    process's peak resident set in KiB."""
    import resource
    c = next((c for c in collections_mod.load_all() if str(c.manifest_path) == manifest_path), None) \
        or collections_mod.by_name(name)
    out = compute_here(c) if c is not None else None
    return out, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def compute_here(collection) -> dict | None:
    """The line, computed in this process (~0.4 s for 1,600 parts warm; the
    coverage over every part after a bundle lands)."""
    from contextlib import closing
    codex = collection.codex
    now = time.time()
    out: dict | None = None
    try:
        from app.hold import collection as col
        from app.share import contribute, counting, peers, seeder
        from app.share import coverage as cov
        root = collections_mod.louise_root() / "collections"
        snap = seeder.cached_snapshot(col.shard_dir_of(root, collection.name))
        if snap is not None and (root / collection.name / "state" / "manifest.sqlite").is_file():
            with closing(cov.manifest(root, collection.name)) as mcon:
                rows = cov.rows(mcon)
                view = cov.shards_view(codex, snap, rows, now=now,
                                       **counting.view_kwargs(root, collection.name, codex, mcon, rows))
                chunks = sum(v["chunks"] for v in view)
                covered = sum(v["covered"] for v in view)
                accepted = [r for r in rows if r["accepted_at"]]
                out = {"parts": len(view), "open": sum(1 for v in view if v["open"]),
                       "covered_pct": round(100.0 * covered / chunks, 1) if chunks else 0.0,
                       "from_here": sum(r["chunks"] for r in accepted if r["source"] == cov.LOCAL),
                       "from_peers": sum(r["chunks"] for r in accepted if r["source"] != cov.LOCAL),
                       "leads": None, "left": None, "peers": 0}
                try:
                    from app import identity
                    own = identity.own()
                except Exception:   # noqa: BLE001 — the web may hold no key: the bounty line stays
                    own = None
                if own:
                    with closing(peers.connect()) as ncon:
                        ncon.execute("PRAGMA busy_timeout = 250")
                        live = cov.live_peers(codex, ncon, now=now, own_key=own,
                                              own_benchmarks=cov.own_benchmarks(root, collection.name, codex, own))
                    out["peers"] = len(live)
                    if live and out["open"]:
                        lead = contribute.leaders(codex, snap, mcon, live=live, now=now)
                        out["leads"] = sum(1 for k in lead.values() if k == own)
                        out["left"] = sum(1 for k in lead.values() if k is not None and k != own)
    except Exception:   # noqa: BLE001 — a card line, never a failed page
        out = None
    return out
