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
# A DATED-DUMP CODEX NAMES ITS RUN (09 §9.17; München, 2026-11-17)
#
# wikidata's Codex was created from a recipe whose `[origin]` block had no
# `spec`, so it carried `origin.spec = {"files": null}`: no `delta.mode`, no
# run. `is_dated` was False for it and `current_run` None, and parser 3's
# snapshot, whose records name each ENTITY to be read in a run (09 §9.5),
# named no run at all: permanently unverifiable against the source
# (invariant 11). The acquisition spec was dated; only the Codex was not.
# So: the creation writes, for a plugin whose spec is `dated-dumps`, EVERY
# key of the identifying half the recipe states none for (09 §9.17: "in the
# Codex: yes" for source_id, transport, url, layout, listing, files, filters,
# checksums, delta), from the plugin's AcquisitionSpec, in the form
# wikipedia's Codex already states them (`identifying_half`): `files` naming
# the one run (`pinned_snapshot` reads it back), `filters` (what it and the
# engine select by), `delta` (`is_dated`), and the rest as the spec says
# them. A key the recipe states is never touched, so a Codex already naming
# its run and its half (wikipedia's) is written as before, byte for byte. It
# refuses where it has no run to write; and nothing creates, carries into or
# freezes a Codex whose records name a keyed unit without a run.
#
# FOR A DATED-DUMP PLUGIN ONLY. The other plugins' recipes state their half
# themselves, as template._origin says, and its serialisation from a spec is
# not settled for them (pubmed's Codex writes `url` without a scheme,
# wikipedia's with one): written down here so the choice can be revisited; a
# Codex is inside `collection_id`, so filling it for them is a new collection
# for every plugin whose recipe states none.

def spec_dated(spec) -> bool:
    """The plugin's acquisition spec publishes dated dumps a Codex pins."""
    return (spec is not None and getattr(getattr(spec, "delta", None), "mode", None) == "dated-dumps"
            and getattr(spec, "snapshot_files", None) is not None)


def run_files(spec, run: str, filters=None) -> list[dict]:
    """09 §9.17's `files` for run `run`: every file the spec's dated listing
    names for it that the filters admit, `{rel_path, size: null, checksum:
    null}` — what `pinned_snapshot` reads back as `run`."""
    from app.acquire import spec as acquire_spec
    if not RUN_RE.match(str(run or "")):
        raise ValueError(f"{run!r} is not a run of a dated-dump origin (YYYYMMDD, as Wikimedia dates it)")
    flt = tuple(filters) if filters else tuple(spec.filters)
    out = [{"rel_path": f.rel_path, "size": None, "checksum": None}
           for f in spec.snapshot_files(str(run)) if acquire_spec.admitted(f.rel_path, flt)]
    if not out:
        raise ValueError(f"{spec.source_id}: run {run} lists no file its filters admit")
    return out


def identifying_half(spec) -> dict:
    """09 §9.17's identifying half of a dated-dump plugin's spec but its
    `files`, as wikipedia's Codex states it: the origin's own base (the last
    endpoint: mirrors come first), `listing` "snapshot-index" for an origin
    of dated dumps it indexes, checksums `{kind, suffix, algo}` ("" for a
    kind of none), `delta` `{mode, subdir}`."""
    cs = spec.checksums
    listing = ("snapshot-index" if spec.snapshot_index is not None
               else "static" if any(e.static_files for e in spec.endpoints) else "directory")
    return {"source_id": spec.source_id,
            "transport": getattr(spec.transport, "value", spec.transport),
            "url": spec.endpoints[-1].url,
            "layout": spec.layout,
            "listing": listing,
            "filters": list(spec.filters),
            "checksums": ({"kind": "none", "suffix": "", "algo": ""} if cs.kind == "none"
                          else {"kind": cs.kind, "suffix": cs.sidecar_suffix, "algo": cs.sidecar_algo}),
            "delta": {"mode": spec.delta.mode, "subdir": spec.delta.updatefiles_subdir}}


def pinned_spec(origin_spec: dict | None, spec, run: str) -> dict:
    """`origin_spec` (a Codex's or a recipe's `origin.spec`) pinning `run`:
    `files` the run's, and every other key of the identifying half the spec
    states none for from the plugin's (`identifying_half`); every key it
    states as it was, and files it already states for `run` kept as they are
    (their sizes and checksums too: the same recipe is the same id). A spec
    pinning another run, or stating another delta mode, is refused — never
    rewritten."""
    out = dict(origin_spec or {})
    delta = out.get("delta")
    if delta not in (None, {}) and (not isinstance(delta, dict) or delta.get("mode") != "dated-dumps"):
        raise ValueError(f"{spec.source_id}: the origin's delta is {delta!r}, and its acquisition spec "
                         f"publishes dated dumps (09 §9.17 `delta.mode` \"dated-dumps\")")
    half = identifying_half(spec)
    for k, v in half.items():
        if k not in out or out[k] in (None, {}, []):
            out[k] = v
    from app.acquire import spec as acquire_spec
    had = acquire_spec.pinned_snapshot(spec, out) if out.get("files") else None
    if had is not None and had != str(run):
        raise ValueError(f"{spec.source_id}: the origin already names run {had}, not {run}")
    if had is None:
        out["files"] = run_files(spec, run, out["filters"])
    return out


def stated_in(origin_spec: dict | None, spec) -> str | None:
    """The run an `origin.spec` already names in its `files` (the dated
    listing's, as `pinned_snapshot` reads it), or None; a list naming no run
    of the plugin is refused (ValueError)."""
    from app.acquire import spec as acquire_spec
    if not (origin_spec or {}).get("files"):
        return None
    return acquire_spec.pinned_snapshot(spec, {**dict(origin_spec or {}),
                                               "filters": (origin_spec or {}).get("filters") or list(spec.filters)})


def _plugin_at(codex):
    from app import collections as collections_mod
    plugin = collections_mod.PLUGIN_REGISTRY.get(getattr(codex, "plugin", None))
    version = getattr(getattr(codex, "parser", None), "version", None)
    if plugin is None or version is None:
        return None
    return collections_mod.plugin_at(plugin, int(version))


def keyed(codex):
    """The plugin's `origin_key` where it parses at this Codex's parser and
    names its units by the origin's own key there (app/keyed_origin.py);
    None otherwise."""
    from app import keyed_origin
    plugin = _plugin_at(codex)
    return keyed_origin.declared(plugin) if plugin is not None else None


def named_run(codex) -> str | None:
    """The run the Codex itself names — dated and pinned — or None, never a
    refusal: a Codex pinning files no dated listing of its plugin lists
    names no run either."""
    try:
        return codex_run(codex) if is_dated(codex) else None
    except ValueError:
        return None


def why_no_run(codex) -> str | None:
    """Why a Codex whose records name a unit by the origin's own key could
    never be verified against its source — it names no run to read them in
    (09 §9.5, §9.17) — in plain words; None when it names one, or when its
    records name no keyed unit."""
    k = keyed(codex)
    if k is None or named_run(codex) is not None:
        return None
    delta = ((getattr(codex.origin, "spec", None) or {}).get("delta") or {}).get("mode")
    has = "no `origin.spec.delta.mode`" if not delta else f"delta.mode {delta!r}"
    files = (getattr(codex.origin, "spec", None) or {}).get("files")
    return (f"{codex.plugin} at parser {codex.parser.version} names each record's origin by the "
            f"{k.key}, and a key is read in a RUN of the origin (09 §9.5); this Codex names no run "
            f"({has}, origin.spec.files {'null' if not files else 'naming no dated run of the plugin'}), "
            f"so no record of it could ever be checked against the source (invariant 11). A dated-dump "
            f"Codex names the first run its records are built from: `origin.spec.delta.mode` "
            f"\"dated-dumps\" and `origin.spec.files` that run's files (09 §9.17) — `hold collection "
            f"create --run <YYYYMMDD>` writes them; a collection made so and never published is given "
            f"them in place by `louise dev pin-run <name>`")


def refuse_without_run(codex, what: str) -> None:
    """`why_no_run`, as a refusal of `what`."""
    why = why_no_run(codex)
    if why:
        from .codex import CodexError
        raise CodexError(f"{what}: {why}")


def stated_run(name: str) -> str | None:
    """The collection file's `snapshot` (07 §7.8's older key): for a Codex
    that pins no run, the run its acquisition and discovery read (the live
    wikidata's `snapshot = "20261012"`, added by hand on 2026-10-20). None
    where the file states none, or none is readable."""
    import tomllib

    from app import collections as collections_mod
    try:
        raw = tomllib.loads((collections_mod.collections_dir() / f"{name}.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    got = str(raw.get("snapshot") or "")
    return got if RUN_RE.match(got) else None


def held_run(root, name: str, codex) -> str | None:
    """The run `name`'s records were read in, as this node resolves it: the
    current run of a dated Codex (`current_run`); for a Codex that pins none,
    the collection file's `snapshot` — what discovery read the dump of
    (wikidata's `find_dumps(corpus, snapshot)`). None where neither says."""
    run = current_run(root, name, codex) if is_dated(codex) else None
    if run:
        return run
    from app import collections as collections_mod
    try:
        same = Path(root).resolve() == (collections_mod.louise_root() / "collections").resolve()
    except OSError:
        same = False
    return stated_run(name) if same else None


def pinned_over(src, to) -> str | None:
    """The run `to`'s origin pins where `src`'s pins none and the two
    origins are otherwise one — `to.origin` is `src.origin` with its spec
    given `pinned_spec(…, run)` (the creation path's own pin). None
    otherwise."""
    a, b = src.origin.to_dict(), to.origin.to_dict()
    sa, sb = dict(a.pop("spec") or {}), dict(b.pop("spec") or {})
    if a != b or named_run(src) is not None:
        return None
    run = named_run(to)
    spec = spec_of(to)
    if run is None or spec is None or getattr(src, "plugin", None) != getattr(to, "plugin", None):
        return None
    try:
        return run if pinned_spec(sa, spec, run) == sb else None
    except ValueError:
        return None


def admit_run_pin(root, from_name: str, src, to, differences: list[str], what: str) -> list[str]:
    """`differences` (seed.record_differences) without "origin" where the
    target's origin is the source's with the run pinned (`pinned_over`) —
    THE RUN THE SOURCE'S RECORDS WERE READ IN, `held_run`: then every record
    byte is the same (a run-free origin names no run; a dated one names that
    run), and the target only says which run they are read in. A pin to
    another run, or to one the source's run cannot be told for, is refused."""
    if "origin" not in differences:
        return differences
    run = pinned_over(src, to)
    if run is None:
        return differences
    had = held_run(root, from_name, src)
    if had != run:
        from .codex import CodexError
        raise CodexError(
            f"{what}: the target's Codex pins run {run}, and {from_name}'s records were read in "
            + (f"run {had}" if had else "a run this node cannot tell (its Codex names none and its "
                                        "collection file no `snapshot`)")
            + " — its records are not the target's, and nothing is carried")
    return [d for d in differences if d != "origin"]


def run_of(codex, snapshot) -> str | None:
    """The run a snapshot's records were built from: its `origin_run`, else
    — a snapshot made before the field — the Codex's run (09 §9.4). What an
    origin naming a unit by the origin's own key is resolved in (09 §9.5,
    amended 2026-11-17: wikipedia's page, parser 6). None for an origin of
    no dated dumps."""
    run = getattr(snapshot, "origin_run", None) if snapshot is not None else None
    if run:
        return str(run)
    try:
        return codex_run(codex)
    except Exception:   # noqa: BLE001 — a Codex whose origin names no run: none
        return None


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
