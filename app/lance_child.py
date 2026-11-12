"""The Lance calls that leave index files open, each run in a child process
that ends with it (München, 2026-11-12).

MEASURED. The worker's fd census, two readings on one process six hours
apart: index files open 6 -> 52, and 38 of them deleted, every one
`chunks.lance/_indices/<uuid>/{index,auxiliary}.idx` of normattiva-nemo:
4,499 MiB of deleted bytes pinned on ssd3 until the worker restarts, more at
every refresh. Reproduced with lance 9.0.1 (tests/test_lance_child.py):
`optimize_indices` (a delta, and a merge of deltas) and `compact_files`
keep open, for the life of the process, the vector-index files they read,
through the worker's shared Session OR a fresh one, the dataset dropped and
collected. The prune then deletes the files merged away, and the process
pins them. The scalar (BTREE) refresh does not.

THE STANDARD: a native resource a library does not give back is released by
ending the process that holds it: process-per-task isolation, what
`multiprocessing.Pool(maxtasksperchild=…)` exists for ("to enable unused
resources to be freed"). Each call is ONE task in a single-use child,
SPAWNED and never forked (the worker holds threads and SQLite connections,
app/parallel.py), watching its parent (`parallel.watch_parent`); it reopens
the dataset by its URI, makes the one commit, and exits. The commit is
Lance's own, atomic as before; the caller waits for it as it waited for the
call in-process, and re-reads the table after it (the callers' `forget`).
The cost: a process start and an import of lance, ~1-2 s, beside a refresh
of seconds to minutes. Upstream: no issue found for this on 2026-11-12. When
Lance closes these readers, LANCE_CHILD=0 is the same behaviour without the
child.

A child that cannot be started (EMFILE, EAGAIN: the exhaustion this guards
against) runs the call here, so the work is done and the leak taken. A child
the kernel kills raises `ChildLost`: the caller defers (the worker's
maintenance logs "deferred" and asks again on its next beat), and the table
is as the last commit left it.
"""
from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from types import SimpleNamespace
from typing import Any

from . import config

#: what `compact_files`' metrics say, carried back from the child
METRICS = ("fragments_removed", "fragments_added", "files_removed", "files_added")


class ChildLost(RuntimeError):
    """The child running a Lance call died before it answered (an OOM, a
    SIGKILL): a deferral, never the caller's data."""


def _run(op: str, uri: str, kw: dict[str, Any]) -> dict[str, Any]:
    """In the child: the one call, on the dataset as it is now."""
    import lance
    ds = lance.dataset(uri)
    if op == "optimize_indices":
        ds.optimize.optimize_indices(**kw)
        return {}
    if op == "compact_files":
        m = ds.optimize.compact_files(**kw)
        return {k: getattr(m, k, None) for k in METRICS}
    raise ValueError(f"no such Lance call here: {op}")


def _here(op: str, ds, kw: dict[str, Any]):
    """The call in this process, on the caller's own dataset: what the code
    did before (LANCE_CHILD=0, or no child to be had)."""
    return getattr(ds.optimize, op)(**kw)


def _call(op: str, ds, kw: dict[str, Any]):
    if not config.LANCE_CHILD:
        return _here(op, ds, kw)
    from . import parallel
    uri = str(ds.uri)
    try:
        ex = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"),
                                 initializer=parallel.watch_parent)
    except OSError:
        return _here(op, ds, kw)                # no child to be had: here, leak and all
    try:
        try:
            fut = ex.submit(_run, op, uri, kw)
        except (OSError, BrokenProcessPool):
            return _here(op, ds, kw)            # the child could not start
        try:
            return SimpleNamespace(**fut.result())
        except BrokenProcessPool as e:
            raise ChildLost(f"the process running Lance's {op} on {uri} died before it "
                            f"answered; asked again later") from e
    finally:
        ex.shutdown(wait=True, cancel_futures=True)


def optimize_indices(ds, **kw) -> None:
    """`ds.optimize.optimize_indices(**kw)`, in a child that reopens the
    dataset by its URI and ends with the call."""
    _call("optimize_indices", ds, kw)


def compact_files(ds, **kw):
    """`ds.optimize.compact_files(**kw)`, in a child that reopens the dataset
    by its URI and ends with the call; its metrics (`METRICS`) as attributes."""
    return _call("compact_files", ds, kw)
