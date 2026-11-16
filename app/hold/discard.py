"""Discarding a snapshot that nothing published names (RULE 1).

A snapshot is immutable once written (02 §2.2), and one a peer may hold is
never recalled — it is superseded (01 promise 8). A snapshot this node
NEVER SERVED and attested nothing of is held by nobody: superseding it would
only write its mistake into the chain as history. wikipedia-v8's 2026-09
(c6e71a2b, 2026-10-29) listed 21.9 M dump-layer twins of archive pages
(app/plugins/walk.py); a successor would have tombstoned every one of them,
a record of a defect no peer ever saw. It is DISCARDED instead, and the
snapshot made again is the one a build by the book makes.

What moves: the snapshot file, its signatures, and every part it lists
(records, relations, figures) — under `<SHARD>/.discarded/<snapshot id>/`,
at their paths inside the SHARD. INSIDE the SHARD, on its own filesystem:
a SHARD is usually a link to another mount (/shards/<name> on the node,
tank, while state/ is on the SSD), and a rename cannot cross a filesystem
(EXDEV — München, 2026-10-30, before the first apply; a copy would have
put 96 GB of parts on the SSD). Nothing reads a SHARD but its `snapshots/`
and `records/` and what a snapshot names, and the torrent leaves out what
is hidden. Moved, not deleted: their record lines are what the rebuild
takes (`hold records build --records-from-discarded`), none parsed. What is
derived from it goes: `index/facets/` (the facets, their postings, the
marker) — never the rest of `index/`, which is the collection's text index
and catalogue — and the edges its relation parts put into the manifest; the
index maintainer derives them again from the snapshot that replaces it.

Refused when it is not the latest snapshot (a later one's `previous` and
delta name it), when this node served it (the seeding control names it),
when this node published an attestation of the collection, or when the
discarded directory already holds that id.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import collection as col, shard as shard_mod, snapshot as sn
from .codex import CodexError

DISCARDED = ".discarded"
#: the collection's index directory: its fts_dir (text index, `_catalog`)
#: and, under it, the facets derived from a snapshot (app/ask/facets.py)
INDEX = "index"


def discarded_dir(root: Path, name: str, snapshot_id: str) -> Path:
    """Inside the SHARD, through its link: the parts' own filesystem."""
    return col.shard_dir_of(Path(root), name) / DISCARDED / snapshot_id


def _device(path: Path) -> int:
    return os.stat(path).st_dev


def held_records(root: Path, name: str, snapshot_id: str):
    """The record lines a discarded snapshot listed, for a build to take
    (`pack.build.HeldRecords`): the parts where the discard put them."""
    from app.pack import build as pack_build
    d = discarded_dir(root, name, snapshot_id)
    path = d / shard_mod.snapshot_path(snapshot_id)
    if not path.is_file():
        raise CodexError(f"{name}: no discarded snapshot {snapshot_id!r} under {d.parent}")
    snap = sn.load(path, container=sn.container_beside(col.shard_dir_of(Path(root), name)
                                                        / shard_mod.snapshot_path(snapshot_id)))
    missing = [s.path for s in snap.shards if not (d / s.path).is_file()]
    if missing:
        raise CodexError(f"{name}: discarded snapshot {snapshot_id} lists {len(missing)} part(s) "
                         f"not held under {d} (first: {missing[0]})")
    return pack_build.HeldRecords(d, tuple(s.path for s in snap.shards))


def _members(snap) -> list[str]:
    return ([s.path for s in snap.shards] + [r.path for r in snap.relations]
            + [f.path for f in getattr(snap, "figures", ())])


def _entries(snap) -> list[tuple[str, str, int]]:
    """`(path, root, bytes)` of every part the snapshot lists."""
    return [(e.path, e.root, int(e.bytes))
            for e in (*snap.shards, *snap.relations, *getattr(snap, "figures", ()))]


def _built_since(root: Path, name: str, snap) -> dict[str, str] | None:
    """The parts a records build made SINCE the snapshot lists, `{path:
    sha256}` — the build's manifest (state/build/manifest.json) where it
    names other parts than the snapshot — or None: no build since, or the
    snapshot's own."""
    import json
    path = Path(root) / name / "state" / "build" / "manifest.json"
    try:
        listed = {str(s["path"]): str(s.get("sha256") or "") for s in json.loads(path.read_text())["shards"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    own = {e.path: str(e.root).replace("sha256:", "") for e in snap.shards}
    return None if listed == own else listed


def version_of(shard_dir: Path, rel: str, snapshot_id: str, size: int) -> Path | None:
    """WHERE THE SNAPSHOT'S OWN VERSION of `rel` is (München, 2026-11-16,
    NeuRIS5): under its superseded name for `snapshot_id` where a build has
    since put a newer version at the path (`shard.keep_superseded`), else at
    the path when it is that version's size — never the newer version at the
    path, which is another snapshot's to come. None where it is not held."""
    here = Path(shard_dir) / rel
    kept = shard_mod.superseded_name(here, snapshot_id)
    if kept.is_file():
        return kept if kept.stat().st_size == size else None
    if here.is_file() and here.stat().st_size == size:
        return here
    return None


def _published(con, node_con, codex, snapshot_id: str) -> str | None:
    """Why the snapshot may be held elsewhere, or None."""
    from app.share import seeder
    got = seeder.served(con) or {}
    if got.get("snapshot_id") == snapshot_id:
        return f"this node served it (seeding control: {got.get('snapshot_id')})"
    if node_con is not None:
        try:
            n = node_con.execute("SELECT COUNT(*) FROM attestations_published WHERE collection_id=?",
                                 (codex.collection_id,)).fetchone()[0]
        except sqlite3.OperationalError:        # no attestation ever published here
            n = 0
        if n:
            return f"this node published {n} attestation(s) of the collection"
    return None


def discard(root: Path, name: str, snapshot_id: str, *, con, node_con=None,
            dry_run: bool = False) -> dict[str, Any]:
    """Move the snapshot and its parts aside and drop what was derived from
    it; `con` is the collection's manifest. Returns what moved."""
    root = Path(root)
    codex = col.read(root, name)
    shard_dir = col.shard_dir_of(root, name)
    path = shard_dir / shard_mod.snapshot_path(snapshot_id)
    if not path.is_file():
        raise CodexError(f"{name}: no snapshot {snapshot_id!r} in {shard_dir / 'snapshots'}")
    from app.share import seeder
    latest = seeder.current_snapshot(shard_dir)
    snap = sn.load(path)
    if latest is None or latest.snapshot_id != snapshot_id:
        raise CodexError(f"{name}: {snapshot_id} is not the latest snapshot "
                         f"({latest.snapshot_id if latest else 'none'} is): a later one names it")
    why = _published(con, node_con, codex, snapshot_id)
    if why:
        raise CodexError(f"{name}: {snapshot_id} is not discarded — {why}; a snapshot a peer "
                         f"may hold is superseded, never recalled (01 promise 8)")
    dest = discarded_dir(root, name, snapshot_id)
    if (dest / shard_mod.snapshot_path(snapshot_id)).exists():
        raise CodexError(f"{name}: {dest} holds a discarded {snapshot_id} already; move it aside first")
    members = _members(snap)
    sig = shard_dir / shard_mod.snapshot_sig_path(snapshot_id)
    # each part's OWN version, wherever it is: a build since may have kept it
    # under its superseded name and put the next version at the path; a part
    # that build left as it was is both's, linked aside and kept in place
    where = [(rel, version_of(shard_dir, rel, snapshot_id, size)) for rel, _root, size in _entries(snap)]
    since = _built_since(root, name, snap) or {}
    shared = {rel for rel, root_, _size in _entries(snap)
              if since.get(rel) and since[rel] == str(root_).replace("sha256:", "")}
    out = {"collection": name, "snapshot": snapshot_id, "hash": snap.hash, "parts": len(members),
           "at_path": sum(1 for rel, p in where if p is not None and p == shard_dir / rel),
           "superseded_name": sum(1 for rel, p in where if p is not None and p != shard_dir / rel),
           "not_held": sum(1 for _rel, p in where if p is None),
           "kept_for_the_build_since": len(shared),
           "to": str(dest), "discarded": not dry_run}
    if dry_run:
        return out
    dest.mkdir(parents=True, exist_ok=True)
    if _device(path.parent) != _device(dest):
        try:
            dest.rmdir()
        except OSError:
            pass
        raise CodexError(f"{name}: {dest} is not on the SHARD's filesystem ({path.parent}); a rename "
                         f"cannot cross one (EXDEV) and nothing was moved")
    # the parts first, the snapshot file last: an interrupted discard still
    # names its snapshot in the SHARD, and a re-run moves what is left
    for rel, src in where:
        if src is not None and src.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            if rel in shared and src == shard_dir / rel:
                os.link(src, dest / rel)
            else:
                os.replace(src, dest / rel)
    relation_paths = [r.path for r in snap.relations]
    if relation_paths:
        has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='relations'").fetchone()
        if has:
            con.execute("BEGIN IMMEDIATE")
            try:
                for i in range(0, len(relation_paths), 900):
                    part = relation_paths[i:i + 900]
                    marks = ",".join("?" * len(part))
                    con.execute(f"DELETE FROM relations WHERE shard IN ({marks})", part)
                    con.execute(f"DELETE FROM relations_indexed WHERE shard IN ({marks})", part)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
    # ONLY the facets: `index/` is the collection's fts_dir — its text index
    # and catalogue, kept whole (82 GB on wikipedia-v8; München, 2026-10-30,
    # before the first --yes). The facets, their postings and their marker
    # are what the maintainer derives from a snapshot (app/ask/facets.py).
    from app.ask import facets as facets_mod
    derived = root / name / INDEX / facets_mod.CACHE
    if derived.is_dir():
        shutil.rmtree(derived)
    if sig.is_file():
        (dest / shard_mod.snapshot_sig_path(snapshot_id)).parent.mkdir(parents=True, exist_ok=True)
        os.replace(sig, dest / shard_mod.snapshot_sig_path(snapshot_id))
    (dest / shard_mod.snapshot_path(snapshot_id)).parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest / shard_mod.snapshot_path(snapshot_id))
    return out


def repair(root: Path, name: str, snapshot_id: str, *, dry_run: bool = True) -> dict[str, Any]:
    """After a discard made before `version_of` (NeuRIS5, 2026-11-16): the
    discard moved the parts at their PATHS — the next build's versions —
    and left its own snapshot's under their superseded names. Each part the
    LATEST snapshot lists and does not find at its path comes back from the
    discarded folder where its size is the latest's (moved where the
    discarded snapshot listed other bytes, linked where the same); then each
    part of the discarded snapshot still in the SHARD under its superseded
    name goes into the discarded folder. Nothing is deleted; a dry run says
    what would move. Idempotent."""
    from app.share import seeder
    root = Path(root)
    shard_dir = col.shard_dir_of(root, name)
    dest = discarded_dir(root, name, snapshot_id)
    old_path = dest / shard_mod.snapshot_path(snapshot_id)
    if not old_path.is_file():
        raise CodexError(f"{name}: no discarded snapshot {snapshot_id!r} under {dest.parent}")
    latest = seeder.current_snapshot(shard_dir)
    if latest is None:
        raise CodexError(f"{name}: no snapshot in {shard_dir / 'snapshots'} to repair the parts of")
    old = {rel: (root_, size) for rel, root_, size in _entries(sn.load(old_path))}
    back, linked, aside, missing = [], [], [], []
    for rel, root_, size in _entries(latest):
        here = shard_dir / rel
        if here.is_file():
            continue
        there = dest / rel
        if there.is_file() and there.stat().st_size == size:
            (linked if old.get(rel, (None,))[0] == root_ else back).append(rel)
        else:
            missing.append(rel)
    for rel, (root_, size) in old.items():
        kept = shard_mod.superseded_name(shard_dir / rel, snapshot_id)
        if kept.is_file() and kept.stat().st_size == size and (rel in back or not (dest / rel).is_file()):
            aside.append(rel)
    out = {"collection": name, "latest": latest.snapshot_id, "discarded": snapshot_id,
           "back": len(back), "linked": len(linked), "aside": len(aside), "not_found": len(missing),
           **({"first_not_found": missing[0]} if missing else {}), "applied": not dry_run}
    if dry_run:
        return out
    for rel in back:
        (shard_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        os.replace(dest / rel, shard_dir / rel)
    for rel in linked:
        (shard_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        os.link(dest / rel, shard_dir / rel)
    for rel in aside:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        os.replace(shard_mod.superseded_name(shard_dir / rel, snapshot_id), dest / rel)
    return out
