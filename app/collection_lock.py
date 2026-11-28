"""A LOCKED collection (operator, 2026-11-27; 07 §7.8 `locked`;
docs/notes/collection-lock.md).

The operator, verbatim: "add a "lock" checkbox per collection, with it the
collection is not searchable or shareable, no processing or fetching. It
simply stays frozen as it is. Its embedders/reranker are not loaded (unless
there are other collections using them). It is to keep an unused collection
for whatever reason. It can only be renamed, exported or removed."

THE ONE RULE: `locked = true` in the collection's file is desired state
(invariant 10), and `collections.load_all` refuses a locked file as it
refuses one that names no encoder. Every reader of that listing — the
worker's rounds, a search, sharing, the model keeper — leaves it out by that
one line, and a reader added tomorrow does too; `collections.load_locked`
lists what is held so, for the Library's card and "does this node hold it".

This module is the lock's two writes and the refusals around them:

  * `lock` refuses a collection whose text index a sister READS
    (docs/notes/sister-shared-index.md): the writer locked, her index stops
    following her records, and her text leg misses what she takes in. She
    is unshared first (`hold collection unshare <sister>`), or locked too.
  * `refusal(name, what)` is what every other act on a locked collection
    says — a command (`louise.py`), a value set in its file
    (`collection_file.set_values`) — and None where the act is allowed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import collections as collections_mod

#: what a locked collection still allows, by what it is (operator: "It can
#: only be renamed, exported or removed"), and the lock's own two writes
ALLOWED = ("rename", "export", "remove", "lock", "unlock", "read")


class LockRefused(ValueError):
    """The lock could not be taken, or an act refused on a locked collection.
    Nothing was written (07 §7.7: exit 2)."""


def refusal(name: str, what: str) -> str | None:
    """Why `what` — a few words naming the act — may not be done to `name`
    now, or None: it is not locked."""
    if not collections_mod.is_locked(name):
        return None
    return (f"{name} is locked, so it is not {what}: a locked collection is kept as it is and "
            f"can only be renamed, exported or removed. Untick its lock box in the Library, or "
            f"run `louise hold collection unlock {name}`, first")


def _held_root() -> Path:
    return collections_mod.louise_root() / "collections"


def sisters_reading(name: str, root: Path | None = None) -> list[str]:
    """The held collections that read `name`'s own text index and are not
    locked themselves, by name."""
    from . import text_index
    root = Path(root) if root is not None else _held_root()
    try:
        readers = text_index.readers(root, name)
    except OSError:
        return []
    return [r for r in readers if r != name and not collections_mod.is_locked(r)]


def lock(name: str, *, root: Path | None = None) -> dict[str, Any]:
    """Lock collection `name`: its file says `locked = true`. The worker lets
    it go at its next round (its current unit finished and committed,
    invariant 2), a search and sharing leave it out at once, the keeper
    unloads what only it used. LockRefused, nothing written, where a sister
    reads its text index."""
    from . import collection_file
    if collections_mod.is_locked(name):
        return {"collection": name, "locked": True, "says": f"{name} was already locked"}
    readers = sisters_reading(name, root)
    if readers:
        who = ", ".join(readers)
        raise LockRefused(
            f"{name}'s text index is read by {who} (one index for sisters with the same documents, "
            f"docs/notes/sister-shared-index.md). Locked, {name} would stop indexing for "
            f"{'them' if len(readers) > 1 else 'her'} too, and the text leg would miss every document "
            f"taken in after the lock. `louise hold collection unshare {readers[0]}` first (an index "
            f"of her own, built again by the worker), or lock {who} as well")
    try:
        written = collection_file.set_values(name, {"locked": "true"})
    except collection_file.SetRefused as e:
        raise LockRefused(str(e)) from None
    return {"collection": name, "locked": True, "file": written["file"],
            "says": collections_mod.locked_says(name)}


def index_writer(name: str, root: Path | None = None) -> str | None:
    """The sister whose text index `name` reads, or None: her own."""
    from . import text_index
    root = Path(root) if root is not None else _held_root()
    try:
        rel = (text_index.placement(root, name) or {}).get("dir") or ""
    except (OSError, ValueError):
        return None
    parts = Path(rel).parts
    w = parts[0] if len(parts) == 2 and parts[1] == "index" else None
    return w if w and w != name else None


def unlock(name: str, *, root: Path | None = None) -> dict[str, Any]:
    """Unlock collection `name`: the line removed from its file. Served again
    at the worker's next round, as at a boot — its statements are kept, so a
    run already gated is not gated again."""
    from . import collection_file
    if not collections_mod.config_toml_path(name).is_file():
        raise LockRefused(f"{name}: no such collection on this node "
                          f"({collections_mod.config_toml_path(name)} does not exist)")
    if not collections_mod.is_locked(name):
        return {"collection": name, "locked": False, "says": f"{name} was not locked"}
    writer = index_writer(name, root)
    if writer is not None and collections_mod.is_locked(writer):
        # served, she would search an index nobody writes: the worker's settle
        # withholds her text leg (text_index.settle), and the reason stays in
        # her placement until the next boot
        raise LockRefused(
            f"{name} reads {writer}'s text index, and {writer} is locked: unlock {writer} first, "
            f"then {name}")
    try:
        written = collection_file.set_values(name, {"locked": "false"})
    except collection_file.SetRefused as e:
        raise LockRefused(str(e)) from None
    return {"collection": name, "locked": False, "file": written["file"],
            "says": f"{name} is served again from the worker's next round: searched, shared as "
                    f"its file says, fetched for and processed, its models loaded when it needs them"}
