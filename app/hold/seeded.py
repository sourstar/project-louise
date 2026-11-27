"""SEEDED PARTS, READ WHERE THEY LIE (operator, 2026-11-27: "By path";
"Never use hard links, always consciously load from a path").

A SHARD seeded from another collection's byte-identical parts (hold/seed.py,
`snapshot create --shards-from`; a case-J sister's carry) holds each part as
a RELATIVE symbolic link to her sister's part — RULE 1: provably identical
bytes are not copied (gutenberg's SHARD is 95 GB) — and `.seeded.json` at
the SHARD's root names each one: `{rel: {root, bytes, identity, from}}`, the
pieces root her snapshot names, and the file the link reached when it was
made. Hidden: no snapshot lists it and no torrent serves it.

HER SISTER RE-ISSUES A PART AT ITS PATH (the superseded version renamed
aside, shard.keep_superseded): the link then reaches other bytes. So every
read of a seeded part — a record or figure read (app/pack/records.py,
hold/figures.py), a snapshot made or checked, a part shared — calls `check`
first. One `lstat` for a part that is no link. For a link: the file it
reaches is compared with the recorded identity, and where it differs, its
pieces root with the recorded one, once a process for each new file. A part
whose bytes are no longer her snapshot's REFUSES (`Reissued`), naming it and
her sister's newer snapshot: never a wrong answer.

HER SISTER FORGOTTEN: her parts are handed over to the collection that reads
them (hold/forget.py `hand_over_parts`, the mechanism the origin directory
uses), never removed with her; and she is not renamed while read
(hold/rename.py)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .codex import CodexError

#: the record of the parts read where they lie, at the SHARD's root
RECORD = ".seeded.json"
#: how far above a part its SHARD's root may be (records/lang=xx/part)
_DEPTH = 6

_lock = threading.Lock()
#: str(record) -> (mtime_ns, its entries)
_records: dict[str, tuple[int, dict[str, dict]]] = {}
#: str(a part's directory) -> its SHARD's root, or None
_roots: dict[str, Path | None] = {}
#: (str(part), identity) -> pieces root found
_verified: dict[tuple[str, tuple], str] = {}


class Reissued(CodexError):
    """A seeded part whose sister re-issued it: not the bytes the snapshot names."""


def record_path(shard_dir: Path) -> Path:
    return Path(shard_dir) / RECORD


def load(shard_dir: Path) -> dict[str, dict]:
    """The SHARD's seeded parts, `{rel: entry}`; {} for none."""
    path = record_path(shard_dir)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _lock:
        hit = _records.get(str(path))
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = {}
    got = got if isinstance(got, dict) else {}
    with _lock:
        if len(_records) > 1024:
            _records.clear()
        _records[str(path)] = (mtime, got)
    return got


def write(shard_dir: Path, entries: dict[str, dict]) -> None:
    path = record_path(shard_dir)
    if not entries:
        path.unlink(missing_ok=True)
    else:
        tmp = path.with_name(f".{RECORD}.tmp")
        tmp.write_text(json.dumps(entries, sort_keys=True, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    with _lock:
        _records.pop(str(path), None)


def relative_link(target: Path, link: Path) -> None:
    """`link` made a RELATIVE symbolic link to `target`, computed between
    their physical directories (a SHARD is usually a link to another mount):
    it reaches the same file wherever the two SHARDs' common root is
    mounted. Atomic: a hidden name, renamed into place."""
    link.parent.mkdir(parents=True, exist_ok=True)
    rel = os.path.relpath(Path(target).resolve(), link.parent.resolve())
    tmp = link.with_name(f".{link.name}.seeding")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(rel)
    os.replace(tmp, link)


def entry(link: Path, root: str, size: int, from_name: str) -> dict[str, Any]:
    from ..file_copy import identity
    return {"root": str(root), "bytes": int(size), "identity": list(identity(link)), "from": from_name}


def _shard_root(part: Path) -> Path | None:
    """The SHARD a part is in: the nearest directory above it that holds a
    `RECORD`."""
    key = str(part.parent)
    with _lock:
        if key in _roots:
            return _roots[key]
    found = None
    d = part.parent
    for _ in range(_DEPTH):
        if (d / RECORD).is_file():
            found = d
            break
        if d == d.parent:
            break
        d = d.parent
    with _lock:
        if len(_roots) > 4096:
            _roots.clear()
        _roots[key] = found
    return found


def _rel(shard: Path, part: Path) -> str:
    rel = Path(os.path.relpath(part, shard)).as_posix()
    if rel.startswith(".discarded/"):               # a discarded snapshot's part (hold/discard.py)
        rel = rel.split("/", 2)[2] if rel.count("/") >= 2 else rel
    return rel


def _newer(part: Path, rel: str) -> str:
    """Her sister's latest snapshot, read from the SHARD the link reaches."""
    try:
        target = part.resolve()
        sister_shard = target.parents[len(Path(rel).parts) - 1]
        from app.share import seeder
        snap = seeder.current_snapshot(sister_shard)
        return snap.snapshot_id if snap is not None else "none"
    except Exception:   # noqa: BLE001 — the refusal stands without it
        return "unknown"


def check(path: Path | str) -> None:
    """Raise `Reissued` when `path` is a seeded part whose bytes are no
    longer the ones its SHARD's snapshot names. Nothing for any other file."""
    part = Path(path)
    if not os.path.islink(part):
        return
    shard = _shard_root(part)
    if shard is None:
        return
    rel = _rel(shard, part)
    e = load(shard).get(rel)
    if e is None:
        return
    from ..file_copy import identity
    from . import shard as shard_mod
    try:
        got = identity(part)
    except OSError:
        got = None
    if got is not None and list(got) == list(e.get("identity") or ()):
        return
    if got is not None:
        with _lock:
            seen = _verified.get((str(part), tuple(got)))
        if seen is None:
            seen = shard_mod.pieces_root_of(part)
            with _lock:
                if len(_verified) > 4096:
                    _verified.clear()
                _verified[(str(part), tuple(got))] = seen
        if seen == e.get("root"):
            return
    owner = shard.parent.name if shard.name == "shard" else shard.name
    sister = e.get("from") or "her sister"
    raise Reissued(
        f"{owner}: its part {rel} is read where {sister} holds it, and {sister} has re-issued it "
        f"since (its latest snapshot: {_newer(part, rel)}): " +
        ("the file is gone" if got is None else f"its pieces root is no longer {str(e.get('root'))[:23]}…, "
                                                 f"the one {owner}'s snapshot names") +
        f". Nothing was read from it. Take {sister}'s newer parts: `louise hold snapshot create {owner} "
        f"--shards-from {sister}`")


def readers(root: Path, name: str) -> dict[str, list[str]]:
    """The held collections whose SHARD reads parts of `name`'s where they
    lie: `{reader: [rel, …]}`, by name."""
    from . import collection as col, deposit
    root = Path(root)
    out: dict[str, list[str]] = {}
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.name == name or d.name.startswith(".") or not d.is_dir():
            continue
        try:
            if not deposit.held_file(root, d.name).is_file():
                continue
            shard = col.shard_dir_of(root, d.name)
        except Exception:   # noqa: BLE001 — not a held collection's directory
            continue
        rels = sorted(rel for rel, e in load(shard).items() if e.get("from") == name)
        if rels:
            out[d.name] = rels
    return out
