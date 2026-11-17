"""THE ANNOTATIONS CASCADE, as the update by cadence reads it — read-only, a
small module the cascade itself (docs/notes/collection-updates.md §4, being
built separately) will extend.

What is here is the one question the update asks before it deletes the
origin's previous run from the corpus (§3; 06 §6.2; CLAUDE.md invariant 11,
the operator's words of 2026-11-17: "Delete the monthly snapshots once
used"): has the snapshot built from the next run been PUBLISHED, and has the
cascade it triggers STARTED? Never before both.

- PUBLISHED: `<shard>/snapshots/<id>.json` and its signatures'
  sidecar `<id>.sig.json` both exist (09 §9.4: a snapshot without a
  verified signature is held in staging by every peer, not promoted).
- STARTED: for every annotations collection held here whose Codex
  `annotates` names this collection's `collection_id` (02 §2.10,
  `codex.targets_of`), its `state/woken.json` has
  `sources[<collection_id>] == <snapshot_id>` and `state` in
  ("running", "done", "failed"). Vacuously true when none declares it.

`woken.json` (the cascade's, §4): `{"sources": {"<collection_id>":
"<snapshot_id>", …}, "state": "waiting|running|done|failed", "started":
iso|null, "finished": iso|null, "error": str|null, "tries": int}`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WOKEN = "woken.json"
#: the states in which the cascade has STARTED for a source snapshot
STARTED = ("running", "done", "failed")


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
        got = json.loads((Path(root) / name / "state" / WOKEN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and isinstance(got.get("sources"), dict) else None


def annotators(root, collection_id: str) -> list[str]:
    """The annotations collections held under `root` whose Codex
    `annotates` names `collection_id`, by name."""
    from . import collection as col
    from .codex import is_annotations, targets_of
    return sorted(name for name, codex in col._held(Path(root))
                  if is_annotations(codex)
                  and any(t.collection_id == collection_id for t in targets_of(codex)))


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
