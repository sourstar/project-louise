"""THE RUN A COLLECTION HOLDS NOW, for an origin of dated dumps — one
answer, asked everywhere (02 §2.7 "A collection keeps its identity as its
origin moves", *amended 2026-11-17*, operator: B; 09 §9.4 `origin_run`,
§9.17 `files`).

A dated-dump Codex's `origin.spec.files` names the FIRST run the collection
was built from, and never moves (it is inside `collection_id`). Each
snapshot names the run its own records came from (`origin_run`). So the run
the collection holds now is, of what this peer knows, the LATEST of:

  1. the Codex's run (`files`, `acquire.spec.pinned_snapshot`);
  2. the newest snapshot's `origin_run` (absent on a snapshot made before
     the field, which read the Codex's run);
  3. while an update is in progress and has MOVED the run (the update
     marker, `state/update.json`, app/hold/update_state.py): the marker's run
     — the one the next snapshot is being built from, which discovery reads
     before any snapshot names it.

Runs only move forward (Wikimedia's are dated `YYYYMMDD`), so "the latest"
is the maximum, and an old marker or an older snapshot can never move a
collection back. Discovery (the plugin's `DiscoverState.snapshot`),
acquisition, the collection file's `snapshot` check, `stale_unit` and a new
snapshot's `origin_run` all read `current_run`, through
`collections.codex_defaults` or `records.local_collection` — no second
answer.

COST. The loader asks this on every `load_all` (the worker's every round),
for a dated-dump collection only: a listing of `snapshots/`, a stat of the
newest file and of the marker. A snapshot file's `origin_run` is read once
per physical identity (device, inode, size, mtime), from the file's TAIL:
canonical JSON sorts its keys, so `origin_run` sits just before
`"previous"`, and after it come only `relations`, `shards`, `snapshot_id` and
`version` — ~20 MB of wikipedia-v8's 2 GB snapshot, never its delta.
"""
from __future__ import annotations

import json
import mmap
import os
import re
import threading
from pathlib import Path
from typing import Any

#: a run's id as Wikimedia dates it; nothing else is ever compared as a run
RUN_RE = re.compile(r"^\d{8}$")


def spec_of(codex) -> Any:
    from app.acquire.specs import ACQUIRE_REGISTRY
    return ACQUIRE_REGISTRY.get(getattr(codex, "plugin", None))


def is_dated(codex, spec=None) -> bool:
    """The Codex's origin publishes dated dumps (09 §9.17 `delta.mode`)."""
    origin = getattr(codex, "origin", None)
    delta = ((getattr(origin, "spec", None) or {}).get("delta") or {})
    if delta.get("mode") != "dated-dumps":
        return False
    spec = spec if spec is not None else spec_of(codex)
    return spec is not None and spec.snapshot_files is not None


def codex_run(codex, spec=None) -> str | None:
    """The run the Codex names in `origin.spec.files`: its FIRST (09 §9.17,
    amended 2026-11-17). None when the origin publishes no dated dumps or
    the Codex pins none."""
    from app.acquire import spec as acquire_spec
    spec = spec if spec is not None else spec_of(codex)
    if spec is None:
        return None
    return acquire_spec.pinned_snapshot(spec, getattr(codex.origin, "spec", None))


# --------------------------------------------------------------------------
# a snapshot's own run, from its tail

#: snapshot file -> (its identity, its run); bounded, the oldest out first
_RUNS: dict[str, tuple[tuple[int, ...], str | None]] = {}
_RUNS_MAX = 256
_lock = threading.Lock()
#: what canonical JSON writes just before `"previous"`: the kind, then the
#: run where there is one
_KIND = b'"kind":"louise-snapshot"'
_TAIL = re.compile(rb',"origin_run":("(?:[^"\\]|\\.)*")$')
#: how far before `"previous"` the run can sit: its key and a short string
_BEFORE = 4096


def _ident(p: Path) -> tuple[int, ...]:
    st = p.stat()
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def snapshot_run(path) -> str | None:
    """`origin_run` of the snapshot file at `path`, or None where it has
    none. Read from the tail and kept while the file is the same file; a
    file not in canonical form is loaded whole (`snapshot.load`)."""
    p = Path(path)
    ident = _ident(p)
    key = str(p)
    with _lock:
        hit = _RUNS.get(key)
    if hit is not None and hit[0] == ident:
        return hit[1]
    run = _tail_run(p)
    with _lock:
        if len(_RUNS) >= _RUNS_MAX:
            _RUNS.pop(next(iter(_RUNS)))
        _RUNS[key] = (ident, run)
    return run


def _tail_run(p: Path) -> str | None:
    with open(p, "rb") as f:
        if os.fstat(f.fileno()).st_size == 0:
            return _whole(p)
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as raw:
            if raw[:1] != b"{" or raw[-1:] != b"}":
                return _whole(p)
            at = raw.rfind(b',"previous":')
            if at < 0:
                return _whole(p)
            before = raw[max(0, at - _BEFORE):at]
    if before.endswith(_KIND):
        return None
    m = _TAIL.search(before)
    if m is None or not before[:m.start()].endswith(_KIND):
        return _whole(p)
    return json.loads(m.group(1))


def _whole(p: Path) -> str | None:
    from . import snapshot as sn
    return sn.load(p).origin_run


def newest_snapshot(shard_dir) -> Path | None:
    """The newest snapshot file of a SHARD, by id — a listing, never a load."""
    d = Path(shard_dir) / "snapshots"
    try:
        ids = sorted(p.name[:-5] for p in d.glob("*.json") if not p.name.endswith(".sig.json"))
    except OSError:
        return None
    return d / f"{ids[-1]}.json" if ids else None


# --------------------------------------------------------------------------
# the one answer

def current_run(root, name: str, codex, *, spec=None) -> str | None:
    """The run of its dated-dump origin `name` holds now (the module's
    docstring); None for any other origin, or a dated one that names no run
    at all (a collection on the origin's moving `latest`)."""
    spec = spec if spec is not None else spec_of(codex)
    if not is_dated(codex, spec):
        return None
    runs = [codex_run(codex, spec)]
    shard_dir = Path(root) / name / "shard"
    newest = newest_snapshot(shard_dir)
    newest_id = newest.name[:-5] if newest is not None else None
    if newest is not None:
        try:
            runs.append(snapshot_run(newest))
        except (OSError, ValueError) as e:
            # a snapshot this peer holds and cannot read is not a run to guess at
            from .codex import CodexError
            raise CodexError(f"{name}: its newest snapshot {newest.name} names no readable "
                             f"origin_run ({type(e).__name__}: {e})") from None
    from . import update_state
    mk = update_state.read(root, name)
    if mk is not None and mk.get("moved") and RUN_RE.match(str(mk.get("run") or "")) \
            and (newest_id is None or str(mk.get("period") or "") > newest_id):
        runs.append(str(mk["run"]))
    held = [r for r in runs if r]
    return max(held) if held else None
