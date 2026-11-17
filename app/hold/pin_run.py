"""`louise dev pin-run <collection> [--run R] [--yes]` — an UNPUBLISHED
collection whose Codex should have named its run, given the Codex that
does, in place (RULE 1; 09 §9.17; München, 2026-11-17).

THE CASE. wikidata's parser-3 re-creation (`wikidata.next`) was made from
the live Codex, whose `origin.spec` is `{"files": null}`: no `delta.mode`,
no run. Its records name each entity by its id, read in a run (09 §9.5),
and its snapshot named none — `records-vs-origin` could not run. Its
parts are right: their origins are run-free, and every byte of them is the
by-the-book creation's. Only the Codex, and so the collection_id, the
snapshot and what names them, are wrong. By the book the corrected Codex is
a new collection whose parts are carried again (`records carry`, 4,009
parts, every byte rewritten to the same bytes) and whose snapshot is frozen
again (a first snapshot's delta re-reads every part: ~11 GB of uids).

WHAT THIS DOES INSTEAD, under the collection's write lock, each step
repeatable (invariant 2; `state/pin-run.json` names the step reached):

  1. the corrected Codex: this one with `origin.spec` pinned to the run its
     records were read in (`origin_run.pinned_spec`, the creation path's own
     function — `create --run` writes the same bytes);
  2. the snapshot again, the same id over the same parts and members: the
     old file's bytes with `collection_id` replaced and `origin_run` written
     in its canonical place, spliced as a stream (no delta parsed), held to
     the build database's parts and to the members the new Codex names;
  3. `codex.json` and the collection file's `codex` swapped, the build
     staging's claim, the carry markers (`carry.json`, `line-carry.json`)
     and the manifest's cursors that name the old id re-pointed to the new
     one; the old snapshot's sidecar replaced by this peer's signature of
     the new one (its published marker with it), and the old id's local
     attestations removed — they attest a Codex no longer held;
  4. a sweep of the collection's small files: none may still name the old
     id.

tests/hold/test_pin_run.py holds the result to a by-the-book creation with
the corrected Codex, byte for byte: the SHARD, its snapshot and sidecar, the
markers and the collection file.

WHAT IT REFUSES (nothing written): a Codex that names a run already; a
plugin whose origin publishes no dated dumps; a run that is not the one its
records were read in (the manifest's locators, the carry's source); and
ANYTHING PUBLISHED OR SHARED — `share = true` in its file, a snapshot the
seeder served (`share_served`), coverage rows under its id, an attestation
of it this node published, a certifier's pointer, or another held
collection naming its id. A collection a peer may hold is superseded, never
rewritten (01 promise 8).

AMENDED (RULE 1, named): 07 §7.8's "a name holds one collection for its
life" yields here for a `.next`-style collection nothing official was
published from — its id never left this node, so no reference to the old
id exists to break.
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .codex import Codex, CodexError

MARKER = "pin-run.json"
#: the steps, in order (`state/pin-run.json` `step`)
STEPS = ("begun", "snapshot", "codex", "state", "signed", "done")
#: what the manifest sample reads to tell the run its records were read in
SAMPLE_ROWS = 1000
#: the small files the sweep reads under `state/` (larger ones are a pass's data)
_SWEEP_MAX = 1 << 20


class PinRefused(CodexError):
    """A rule stands in the way; nothing was written (exit 2)."""
    exit_code = 2


def corrected(codex: Codex, run: str) -> Codex:
    """`codex` with its origin pinned to `run` (template.pin_dated: the
    creation path's own pin), validated and read back from its bytes."""
    from . import codex as cx
    from . import template
    pinned = template.pin_dated(codex.plugin, codex.origin, run)
    d = json.loads(codex.canonical())
    d.pop("collection_id", None)
    d["origin"] = {**d["origin"], "spec": pinned.spec}
    out = cx.from_dict(d)
    cx.validate(out)
    return cx.loads(out.canonical())


# ---- what may be pinned -------------------------------------------------------

def _published_attestations(collection_id: str) -> int:
    try:
        from app import node_state
        con = node_state.connect()
    except Exception:   # noqa: BLE001 — no node database: nothing published from here
        return 0
    try:
        return int(con.execute("SELECT COUNT(*) FROM attestations_published WHERE collection_id=?",
                               (collection_id,)).fetchone()[0])
    except sqlite3.OperationalError:            # no attestation ever published here
        return 0
    finally:
        con.close()


def _manifest_facts(manifest: Path, codex: Codex) -> dict[str, Any]:
    """Read-only: whether the seeder served it, the coverage rows under its
    id, and a sample of the documents' locators."""
    from app.share import coverage as cov
    out: dict[str, Any] = {"served": None, "coverage": 0, "locators": [], "documents": 0}
    if not manifest.is_file():
        return out
    con = sqlite3.connect(f"file:{manifest.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "control" in tables:
            from app.share import seeder
            row = con.execute("SELECT value FROM control WHERE key=?", (seeder.CONTROL_KEY,)).fetchone()
            out["served"] = row["value"] if row else None
        like = cov.shard_id(codex, "").replace("%", r"\%").replace("_", r"\_") + "%"
        for t in ("coverage", "coverage_shards"):
            if t in tables:
                out["coverage"] += con.execute(f"SELECT COUNT(*) FROM {t} WHERE shard LIKE ? ESCAPE '\\'",
                                               (like,)).fetchone()[0]
        if "documents" in tables:
            top = con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0
            out["documents"] = int(con.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            step = max(1, top // SAMPLE_ROWS)
            ids = list(range(1, top + 1, step))[:SAMPLE_ROWS]
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                out["locators"] += [r[0] for r in con.execute(
                    f"SELECT path FROM documents WHERE rowid IN ({','.join('?' * len(part))})", part)]
    finally:
        con.close()
    return out


def _naming(root: Path, name: str, collection_id: str) -> list[str]:
    """The other held collections whose Codex names `collection_id` (an
    annotations collection's target, a daughter's mother, a sister's)."""
    from . import collection as col
    hexid = collection_id.split(":", 1)[-1]
    out = []
    for other, _c in col._held(root):
        if other == name:
            continue
        try:
            raw = (col.shard_dir_of(root, other) / col.CODEX_FILE).read_bytes()
        except OSError:
            continue
        if hexid.encode() in raw:
            out.append(other)
    return out


def _run_evidence(root: Path, name: str, codex: Codex, facts: dict[str, Any]) -> dict[str, Any]:
    """The runs that say which run the records were read in: the manifest's
    locators (a sample), the line carry's source as this node resolves it,
    and the collection's own file."""
    from app import collections as collections_mod

    from . import collection as col
    from . import origin_run
    plugin = collections_mod.plugin_at(collections_mod.PLUGIN_REGISTRY.get(codex.plugin), codex.parser.version)
    run_of = getattr(plugin, "run_of_locator", None)
    seen: dict[str, int] = {}
    for loc in facts["locators"]:
        got = run_of(str(loc)) if callable(run_of) else None
        if got:
            seen[got] = seen.get(got, 0) + 1
    out: dict[str, Any] = {"manifest": seen, "sampled": len(facts["locators"])}
    try:
        marker = json.loads((root / name / "state" / "line-carry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marker = None
    if marker and marker.get("to") == codex.collection_id:
        try:
            src_name, src = col.resolve(root, str(marker.get("from")))
        except CodexError:
            src_name = src = None
        if src is not None:
            out["carried_from"] = {"name": src_name, "run": origin_run.held_run(root, src_name, src)}
    out["file"] = origin_run.stated_run(name)
    return out


def _decide_run(given: str | None, evidence: dict[str, Any]) -> tuple[str, list[str]]:
    """The run, and why — or a refusal where the evidence disagrees or says
    nothing."""
    said: dict[str, str] = {}
    manifest = evidence.get("manifest") or {}
    if len(manifest) > 1:
        raise PinRefused(f"pin-run: the manifest's locators name {len(manifest)} runs "
                         f"({', '.join(sorted(manifest))}): its records were not read in one run")
    if manifest:
        said["the manifest's locators"] = next(iter(manifest))
    carried = (evidence.get("carried_from") or {}).get("run")
    if carried:
        said[f"the run {evidence['carried_from']['name']}'s records were read in (the line carry's source)"] = carried
    if evidence.get("file"):
        said["the collection file's `snapshot`"] = evidence["file"]
    if given:
        said["--run"] = given
    runs = set(said.values())
    if len(runs) > 1:
        raise PinRefused("pin-run: the run is said differently — "
                         + "; ".join(f"{k}: {v}" for k, v in said.items())
                         + ". A Codex names the run its records were read in, and nothing is pinned")
    if not runs:
        raise PinRefused("pin-run: nothing says which run its records were read in — no locator of its "
                         "manifest names a run, no line carry names a source, its file has no `snapshot`. "
                         "Give --run <YYYYMMDD>")
    return runs.pop(), [f"{k}: {v}" for k, v in said.items()]


def plan(root, ref: str, *, run: str | None = None) -> dict[str, Any]:
    """Everything checked, nothing written: what `apply` would do."""
    from app.acquire.specs import ACQUIRE_REGISTRY

    from . import collection as col
    from . import origin_run
    from . import shard as shard_mod
    from .seed import latest_snapshot
    root = Path(root)
    name, codex = col.resolve(root, ref)
    if origin_run.named_run(codex) is not None:
        raise PinRefused(f"pin-run: {name}'s Codex names run {origin_run.named_run(codex)} already")
    if not origin_run.spec_dated(ACQUIRE_REGISTRY.get(codex.plugin)):
        raise PinRefused(f"pin-run: {codex.plugin}'s origin publishes no dated dumps; there is no run to name")
    # ANYTHING PUBLISHED OR SHARED IS SUPERSEDED, NEVER REWRITTEN (01 promise 8)
    from app import collections as collections_mod
    why: list[str] = []
    try:
        import tomllib
        raw = tomllib.loads((collections_mod.collections_dir() / f"{name}.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PinRefused(f"pin-run: {name}'s collection file cannot be read ({e}): whether it is shared "
                         f"is not known") from None
    if raw.get("codex") != codex.collection_id:
        raise PinRefused(f"pin-run: {name}'s file names {raw.get('codex')}, its SHARD {codex.collection_id}")
    if raw.get("share"):
        why.append("its file says share = true")
    state = root / name / "state"
    facts = _manifest_facts(state / "manifest.sqlite", codex)
    if facts["served"]:
        why.append(f"the seeder served it ({facts['served'][:80]})")
    if facts["coverage"]:
        why.append(f"{facts['coverage']} coverage row(s) under its id")
    n = _published_attestations(codex.collection_id)
    if n:
        why.append(f"this node published {n} attestation(s) of it")
    from .certify import pointer_path
    if pointer_path(root, name, codex).is_file():
        why.append("a certifier's pointer names it")
    named = _naming(root, name, codex.collection_id)
    if named:
        why.append(f"{', '.join(named)} name(s) its id")
    if why:
        raise PinRefused(f"pin-run: {name} may be held elsewhere — {'; '.join(why)}. A collection a peer "
                         f"may hold is superseded (a new collection, carried), never rewritten in place")
    evidence = _run_evidence(root, name, codex, facts)
    run, said = _decide_run(run, evidence)
    new = corrected(codex, run)
    shard_dir = col.shard_dir_of(root, name)
    snap = latest_snapshot(root, name)
    snaps = sorted(p for p in (shard_dir / "snapshots").glob("*.json") if not p.name.endswith(".sig.json"))
    if len(snaps) != 1:
        raise PinRefused(f"pin-run: {name} holds {len(snaps)} snapshots; only a first, alone, is frozen "
                         f"again here")
    if snap.previous is not None or snap.origin_run is not None or snap.collection_id != codex.collection_id:
        raise PinRefused(f"pin-run: {name}'s snapshot {snap.snapshot_id} is not its Codex's first, naming no run")
    attest = root / name / "state" / "attestations" / shard_mod._hex(codex.collection_id)
    sidecar = shard_dir / shard_mod.snapshot_sig_path(snap.snapshot_id)
    dump = _dump_of(root, name, new, run)
    return {"collection": name, "from": codex.collection_id, "to": new.collection_id, "run": run,
            "run_said_by": said, "snapshot": {"id": snap.snapshot_id, "hash": snap.hash,
                                              "bytes": snaps[0].stat().st_size,
                                              "parts": len(snap.shards), "records": snap.records},
            "space_id": {"from": codex.space_id, "to": new.space_id},
            "removed": {"sidecar": str(sidecar) if sidecar.is_file() else None,
                        "attestations": sorted(str(p) for p in attest.glob("*.json")) if attest.is_dir() else []},
            "dump": dump, "documents": facts["documents"],
            "evidence": {k: v for k, v in evidence.items() if k != "locators"}}


def _dump_of(root: Path, name: str, codex: Codex, run: str) -> dict[str, Any]:
    """Where the run's files are in the corpus (what records-vs-origin reads)."""
    from app.acquire.specs import ACQUIRE_REGISTRY

    from . import origin_run
    base = root / name / "origin"
    files = [f["rel_path"] for f in origin_run.run_files(ACQUIRE_REGISTRY[codex.plugin], run)]
    held = {f: (base / f).is_file() for f in files}
    return {"files": files, "held": sorted(f for f, ok in held.items() if ok),
            "missing": sorted(f for f, ok in held.items() if not ok)}


# ---- the snapshot, spliced ---------------------------------------------------

_KIND = b'"kind":"louise-snapshot"'
_CHUNK = 8 << 20


def splice(src: Path, dst: Path, *, old_id: str, new_id: str, run: str) -> str:
    """`src`'s bytes with `collection_id` replaced and `origin_run` written
    in canonical JSON's place for it (after `kind`, before `previous`),
    streamed to `dst`; its hash. Refused unless `src` is canonical and
    names `old_id` and no run."""
    head = b'{"collection_id":' + json.dumps(old_id).encode() + b","
    with open(src, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        if raw[:len(head)] != head or raw[-1:] != b"}":
            raise PinRefused(f"pin-run: {src.name} is not a canonical snapshot of {old_id}")
        at = raw.rfind(b',"previous":')
        if at < 0 or raw[max(0, at - len(_KIND)):at] != _KIND:
            raise PinRefused(f"pin-run: {src.name} names a run already, or is not canonical")
        h = hashlib.sha256()
        tmp = dst.with_name(dst.name + ".tmp")
        with open(tmp, "wb") as out:
            def put(b: bytes) -> None:
                h.update(b)
                out.write(b)
            put(b'{"collection_id":' + json.dumps(new_id).encode() + b",")
            for i in range(len(head), at, _CHUNK):
                put(raw[i:min(i + _CHUNK, at)])
            put(b',"origin_run":' + json.dumps(str(run)).encode())
            for i in range(at, len(raw), _CHUNK):
                put(raw[i:min(i + _CHUNK, len(raw))])
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, dst)
    return "sha256:" + h.hexdigest()


def _held_to(root: Path, name: str, new: Codex, path: Path, run: str, old_hash: str) -> None:
    """The spliced snapshot is the one `snapshot create` makes for the new
    Codex: its parts the build database's, its members the new Codex's,
    its run, its predecessor none."""
    from . import snapshot as sn
    from .codex import container_of
    got = sn.load(path)
    shard_dir = root / name / "shard"
    files = sn._files(shard_dir, new, got.snapshot_id)
    con = sqlite3.connect(f"file:{(root / name / 'state' / 'build' / '.build' / 'build.db').as_posix()}?mode=ro",
                          uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM shards ORDER BY slice, n")]
    finally:
        con.close()
    want = sorted((r["slice"], r["n"], r["root"], r["records_root"], r["chunks_root"], r["records"],
                   r["chunks"], r["bytes"], r["first_native_id"], r["last_native_id"]) for r in rows)
    have = sorted((s.slice, s.n, s.root, s.records_root, s.chunks_root, s.records, s.chunks, s.bytes,
                   s.first_id, s.last_id) for s in got.shards)
    bad = []
    if got.collection_id != new.collection_id or got.origin_run != run or got.previous is not None:
        bad.append("its identity, run or predecessor")
    if want != have:
        bad.append("its parts are not the build database's")
    if files != got.files:
        bad.append("its members are not the ones the new Codex names")
    if any(s.container != container_of(new) for s in got.shards):
        bad.append("its parts' container")
    if bad:
        path.unlink(missing_ok=True)
        raise PinRefused(f"pin-run: the snapshot again is not {name}'s for the new Codex ({'; '.join(bad)}); "
                         f"withdrawn, nothing else changed")


# ---- the pass -----------------------------------------------------------------

def _write_json(p: Path, body: dict[str, Any]) -> None:
    """As the markers are written: sorted keys, whole, renamed into place."""
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def _repoint_json(p: Path, old: str, new: str, extra: Callable[[dict], dict] | None = None) -> bool:
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(body, dict):
        return False
    out = {k: (new if v == old else v) for k, v in body.items()}
    if extra is not None:
        out = extra(out)
    if out == body:
        return False
    _write_json(p, out)
    return True


def _repoint_manifest(manifest: Path, old: str, new: str) -> list[str]:
    """The manifest's cursors that name `old` (a carry's pair) name `new`."""
    if not manifest.is_file():
        return []
    con = sqlite3.connect(manifest, timeout=60, isolation_level=None)
    try:
        from app import db
        db.wal(con)
        con.execute("BEGIN IMMEDIATE")
        moved = []
        for key, value in con.execute("SELECT key, value FROM control WHERE value LIKE ?",
                                      (f"%{old}%",)).fetchall():
            con.execute("UPDATE control SET value=? WHERE key=?", (str(value).replace(old, new), key))
            moved.append(key)
        con.execute("COMMIT")
        return moved
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def _repoint_file(name: str, old: str, new: str) -> None:
    """The collection file's `codex = "<old>"` line, the only line naming it."""
    from app import collections as collections_mod
    p = collections_mod.collections_dir() / f"{name}.toml"
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)

    def naming(cid: str) -> list[int]:
        return [i for i, ln in enumerate(lines) if ln.replace(" ", "").startswith(f'codex="{cid}"')]
    hits = naming(old)
    if not hits and len(naming(new)) == 1:
        return                                          # a step done before
    if len(hits) != 1:
        raise PinRefused(f"pin-run: {p} does not name {old} on one `codex` line")
    lines[hits[0]] = lines[hits[0]].replace(old, new)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, p)


def _sweep(root: Path, name: str, old: str) -> list[str]:
    """Every small file of the collection that still names `old`."""
    hexid = old.split(":", 1)[-1].encode()
    out = []
    base = root / name
    for sub in ("shard", "state"):
        d = base / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            rel = p.relative_to(base).parts
            if not p.is_file() or "records" in rel[:2] or p.name == MARKER \
                    or p.name.endswith((".sqlite", ".db", "-wal", "-shm")):
                continue
            try:
                if p.stat().st_size > _SWEEP_MAX:
                    continue
                if hexid in p.read_bytes():
                    out.append(str(p.relative_to(base)))
            except OSError:
                continue
    return sorted(out)


def apply(root, ref: str, *, run: str | None = None, key=None, now: str,
          log: Callable[[str], None] = print) -> dict[str, Any]:
    """`plan`, then each step (module docstring). The caller holds the
    collection's write lock."""
    from . import cascade
    from . import collection as col
    from . import records as rec
    from . import shard as shard_mod
    from . import snapshot as sn
    root = Path(root)
    name, held = col.resolve(root, ref)
    marker = root / name / "state" / MARKER
    body = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None
    if body is not None and body.get("step") == "done":
        log(f"[pin-run] {name}: pinned to run {body['run']} already ({body['to'][:23]}…)")
        return body
    if body is None:
        p = plan(root, ref, run=run)
        body = {"collection": name, "from": p["from"], "to": p["to"], "run": p["run"],
                "snapshot_id": p["snapshot"]["id"], "from_snapshot": p["snapshot"]["hash"],
                "removed": p["removed"], "step": "begun"}
        _write_json(marker, body)
        log(f"[pin-run] {name}: {p['from'][:23]}… -> {p['to'][:23]}…, run {p['run']} "
            f"({'; '.join(p['run_said_by'])})")
    old, run = body["from"], body["run"]
    if held.collection_id not in (old, body["to"]):
        raise PinRefused(f"pin-run: {name} holds {held.collection_id}, and {marker} names {old}")
    old_codex = held if held.collection_id == old else None
    shard_dir = col.shard_dir_of(root, name)
    sid = body["snapshot_id"]
    snap_p = shard_dir / shard_mod.snapshot_path(sid)
    sig_p = shard_dir / shard_mod.snapshot_sig_path(sid)
    pinned = shard_dir / "snapshots" / f"{sid}.json.pinned"

    def step(s: str) -> None:
        body["step"] = s
        _write_json(marker, body)

    new = corrected(old_codex, run) if old_codex is not None else held
    if new.collection_id != body["to"]:
        raise PinRefused(f"pin-run: the corrected Codex is {new.collection_id}, {marker} names {body['to']}")
    # 2. THE SNAPSHOT AGAIN, beside the old one until the swap
    if STEPS.index(body["step"]) < STEPS.index("snapshot"):
        h = splice(snap_p, pinned, old_id=old, new_id=new.collection_id, run=run)
        _held_to(root, name, new, pinned, run, body["from_snapshot"])
        body["to_snapshot"] = h
        step("snapshot")
        log(f"[pin-run] {name}: snapshot {sid} written again for the new Codex, run {run} ({h[:23]}…)")
    # 3a. THE CODEX AND THE FILE: from here the name holds the new collection
    if STEPS.index(body["step"]) < STEPS.index("codex"):
        cp = shard_dir / col.CODEX_FILE
        tmp = cp.with_name(cp.name + ".tmp")
        tmp.write_bytes(new.canonical())
        os.replace(tmp, cp)
        _repoint_file(name, old, new.collection_id)
        step("codex")
        log(f"[pin-run] {name}: codex.json and collections/{name}.toml name {new.collection_id}")
    # 3b. EVERYTHING THAT NAMED THE OLD ONE
    if STEPS.index(body["step"]) < STEPS.index("state"):
        if pinned.is_file():
            os.replace(pinned, snap_p)
        sig_p.unlink(missing_ok=True)
        cascade.published_path(root, name, sid).unlink(missing_ok=True)
        state = root / name / "state"
        claim = state / rec.BUILD / rec.STAGING_CLAIM
        moved = [str(claim.relative_to(root / name))] if _repoint_json(claim, old, new.collection_id) else []
        from . import carry as carry_mod
        from . import line_carry
        if _repoint_json(state / carry_mod.MARKER, old, new.collection_id):
            moved.append(f"state/{carry_mod.MARKER}")

        def to_snapshot(b: dict) -> dict:
            if isinstance(b.get("to_snapshot"), dict) and b["to_snapshot"].get("id") == sid:
                return {**b, "to_snapshot": {**b["to_snapshot"], "hash": body["to_snapshot"]}}
            return b
        if _repoint_json(state / line_carry.MARKER, old, new.collection_id, to_snapshot):
            moved.append(f"state/{line_carry.MARKER}")
        moved += [f"manifest control {k}" for k in _repoint_manifest(state / "manifest.sqlite", old,
                                                                     new.collection_id)]
        attest = state / "attestations" / shard_mod._hex(old)
        gone = 0
        if attest.is_dir():
            for p in attest.glob("*.json"):
                p.unlink()
                gone += 1
            attest.rmdir()
        body["moved"], body["attestations_removed"] = moved, gone
        step("state")
        log(f"[pin-run] {name}: re-pointed {', '.join(moved) or 'nothing'}; {gone} attestation(s) of the "
            f"old id removed (they attest a Codex no longer held)")
    # 3c. SIGNED, and its published marker beside it (sign_snapshot writes it)
    if STEPS.index(body["step"]) < STEPS.index("signed"):
        mine = sn.load(snap_p)
        signed = 0
        if key is not None:
            sn.sign_snapshot(root, name, _CountedSnapshot(mine, _delta_counts(mine)),
                             key, now=now)
            signed = 1
        else:
            log(f"[pin-run] {name}: no node key — snapshot {sid} unsigned")
        body["signatures"] = signed
        step("signed")
    # 4. NOTHING NAMES THE OLD ONE
    left = _sweep(root, name, old)
    if left:
        raise PinRefused(f"pin-run: {name}: {', '.join(left[:5])} still name {old}; the pass stops here "
                         f"({marker} says where) — nothing else names an id this tool does not know")
    step("done")
    log(f"[pin-run] {name}: done — {new.collection_id}, snapshot {sid} names run {run}. Before use: "
        f"`hold check {name} --checks roots,records,chunks,records-vs-origin,vectors-sample`")
    return body


def _delta_counts(snap) -> tuple[int, int, int] | None:
    """The delta's counts for the published marker, as `snapshot create`'s
    in-memory snapshot gives them: a first snapshot adds every record and
    changes none. None for any other (a loaded snapshot's delta is left
    in its file)."""
    if snap.previous is None:
        return (snap.records, 0, 0)
    return None


class _CountedSnapshot:
    """A loaded snapshot whose delta answers its counts without being read
    (`cascade._delta_counts` takes `len` of each list)."""

    def __init__(self, snap, counts: tuple[int, int, int] | None):
        self._snap = snap
        self.delta = (SimpleNamespace(added=range(counts[0]), changed=range(counts[1]),
                                      tombstoned=range(counts[2])) if counts else snap.delta)

    def __getattr__(self, k: str):
        return getattr(self._snap, k)
