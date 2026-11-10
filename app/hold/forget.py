"""`hold collection forget` — stop holding a collection on this peer (07 §7.3,
§7.7; 02 §2.9).

What goes: the held file (`collections/<name>.toml`, 07 §7.8 — the node
stops serving the collection at its next round and starts no thread for
it); then, for a never-share collection, every deposit erased leaving no
trace (02 §2.9: "erasing every deposit followed by removing the
collection's directory"); then the directory whole — SHARD, state, indexes,
vector table, origin. With `--kinds vectors` only the vector-side members
of the SHARD and the vector table go, and the collection stays held. The
Codex as published, this peer's attestations as published and what other
peers hold are untouched: a peer may stop holding (01, promise 7 is about
the network). Nothing is announced yet, so nothing is withdrawn.

On a running node this runs IN THE WORKER (app/jobs.py, the DEPOSIT kind's
op "forget"): the worker is the one writer of a collection's indexes, and
it retires the collection's threads first (`worker.retire_collection`).
Idempotent: a member is gone entire or still there (07 §7.7), and
forgetting what is not held is nothing to do.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable

from .codex import CodexError

#: 09 §9.16's vector-side members of a SHARD, by their prefixes
VECTOR_MEMBER_PREFIXES = ("vectors-", "buckets-", "bundle-", "centres-")
#: the `--kinds` taken apart today; everything else goes with the copy whole
KINDS = ("vectors",)


#: the vector store renamed aside, under the vector write lock, before it is
#: removed: `lance.forgotten-<ns>` (no `.lance` suffix: never listed as a table)
ASIDE = "lance.forgotten-"


def next_after_vectors(name: str, mode: str | None) -> str:
    """What the worker does with documents whose vectors are gone, under the
    collection's embed setting — the sentence `forget --kinds vectors`
    prints. The command never changes the setting itself: `--kinds vectors`
    says "stop holding vectors here", and only `embed = off` keeps it so."""
    from app import collections as collections_mod
    label = collections_mod.EMBED_MODE_LABELS.get(mode or "", mode or "unknown")
    off = f"`louise hold config apply {name} --set embed=off`"
    if mode == "off":
        return (f"embed is off ({label}): they wait, counted as waiting, not done — peers' bundles "
                f"may still bring vectors in; `louise hold config apply {name} --set embed=all` "
                f"embeds them here again")
    if mode == "all":
        return (f"embed is all ({label}): the worker embeds every one of them again. To stop "
                f"holding vectors here, {off} — then they wait, counted as waiting, not done")
    if mode == "p2p":
        return (f"embed is p2p ({label}): the worker embeds again the parts rendezvous gives this "
                f"node, peers' bundles bring the rest. To stop holding vectors here, {off}")
    if mode == "lazy":
        return (f"embed is lazy ({label}): the worker embeds what a search needs, the rest wait. "
                f"To stop holding vectors here, {off}")
    return (f"embed is {label}: what the worker embeds again is that setting's; to stop holding "
            f"vectors here, {off}")


def _embed_mode(name: str) -> str | None:
    """The collection's embed mode as the worker resolves it (invariant 10:
    `effective_config`, the node's own resolution), None when not loaded."""
    try:
        from app import collections as collections_mod, effective_config
        c = collections_mod.by_name(name)
        return None if c is None else str(effective_config._embed_mode(c)["value"])
    except Exception:                                   # noqa: BLE001 — the sentence says "unknown"
        return None


def _forget_vectors(root: Path, name: str, codex, base: Path, *, kinds, dry_run: bool) -> dict[str, Any]:
    """`--kinds vectors`: the SHARD's vector-side members and the vector store
    go, and the collection stays held — and EVERY DOCUMENT MARKED EMBEDDED IS
    NOT ANY MORE (2026-11-07): still `embed_done = 1` with its vectors gone it
    would never be embedded again and dense search would miss it without a
    word. So, in order:

    1. the members;
    2. the store renamed aside UNDER THE VECTOR WRITE LOCK (the manifest's,
       with the writer registration, `embeddings._write_lock`): an embed
       commit in flight finishes first and none lands half in a directory
       being removed; removed after, outside the lock (a large store takes
       minutes to delete);
    3. AFTER the store is gone, every document not embedded and version 0
       (`embed_marks.unmark_manifest`, rowid windows) — the declined keep
       theirs. A run stopped before this finishes it: the marks are reset on
       every run, a store already gone or not.

    What the worker does next is the collection's embed setting, which this
    never changes: under `all` it embeds every document again, under `p2p`
    the parts rendezvous gives the node, under `lazy` what a search needs,
    under `off` nothing — the documents wait, counted as waiting, not done.
    The output says which (`embed`, `next`) and the command that keeps the
    collection without vectors. A document the worker embeds while this runs
    may be marked not embedded though it holds rows: embedded again, its
    rows reused — the safe direction."""
    from . import records as rec, shard as shard_mod
    shard_tree = shard_mod.shard_dir(base)
    members = [p for p in (sorted(shard_tree.iterdir()) if shard_tree.is_dir() else [])
               if p.name.startswith(VECTOR_MEMBER_PREFIXES)]
    from app import vector_place
    store = vector_place.table_dir(root, name)
    aside = sorted(base.glob(f"{ASIDE}*"))              # an earlier run's, stopped before its removal
    mode = _embed_mode(name)
    manifest = (rec._local_collection(root, name, codex) if codex is not None else None)
    marks = manifest is not None and Path(manifest.manifest_path).is_file()
    if dry_run:
        # a table in the build area goes with its folder there (app/vector_place.py)
        stores = [p for p in [store, *aside] if p.exists() or p.is_symlink()]
        behind = [vector_place.physical(p) for p in stores if p.is_symlink()]
        return {"collection": name, "dry_run": True,
                "would_remove": [str(p) for p in members + stores + behind],
                "would_reset": ("every document marked embedded here: embed_done 0, embed_version 0"
                                if marks else "nothing: no state database"),
                "embed": mode, "next": next_after_vectors(name, mode), "removed": []}
    removed: list[str] = []
    for p in members:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        removed.append(str(p))
    con = None
    try:
        if marks:
            from app import db
            con = db.connect(manifest)
        if store.exists() or store.is_symlink():
            aside.append(_set_aside(store, manifest, con))
            removed.append(str(store))
        # a move of the table begun and not finished goes with it
        removed += vector_place.discard_move(root, name)
        for p in aside:
            # a link into the build area: its folder there first, then the link
            # (shutil.rmtree refuses a link, and ignoring that left the folder)
            vector_place.remove_table(p)
        reset = 0
        if con is not None:
            from app import embed_marks
            reset = embed_marks.unmark_manifest(con)
    finally:
        if con is not None:
            con.close()
    return {"collection": name, "kinds": list(kinds), "removed": removed, "documents_reset": reset,
            "embed": mode, "next": next_after_vectors(name, mode)}


def _set_aside(store: Path, collection, con) -> Path:
    """`store` renamed to `lance.forgotten-<ns>` beside it, under the vector
    write lock when there is a manifest to hold it on, and every cached
    connection and index state of it dropped. Returns the new path."""
    import time
    from contextlib import nullcontext
    target = store.with_name(f"{ASIDE}{time.time_ns()}")
    if collection is None:
        os.replace(store, target)
        return target
    from app import embeddings, vector_index, vector_parts
    parts = vector_parts.views(collection) if vector_parts.is_split(collection) else []
    lock = (embeddings._write_lock(con, collection, parts=parts) if con is not None else nullcontext())
    with lock:
        os.replace(store, target)
    embeddings.forget(collection)
    for c in [collection, *parts]:
        vector_index.forget(c)
    return target


def _linked_by_others(root: Path, name: str, target: Path) -> list[str]:
    from .rename import _origin_shared
    return _origin_shared(Path(root), name, target)


def shared_folders(root: Path, name: str) -> tuple[list[Path], list[Path]]:
    """`(removed, kept)`: the folders under the shared roots (one mount per
    kind, a folder per collection, 2026-10-12) that `<root>/<name>`'s links
    point at and a forget removes — its SHARD, origin and bundles folders —
    and the origin folders it keeps because another held collection links
    them (both Normattiva collections read /corpora/normattiva). A link to a
    folder not named after the collection (a custom origin, linked by hand)
    is not ours and is in neither list. One list for the forget and its dry
    run, so the two cannot disagree."""
    from . import shard as shard_mod
    base = Path(root) / name
    removed: list[Path] = []
    kept: list[Path] = []
    for link_name, env in ((shard_mod.SHARD_DIR if hasattr(shard_mod, "SHARD_DIR") else "shard",
                            "SHARDS_ROOT"), ("origin", "ORIGINS_ROOT"), ("state/bundles", "BUNDLES_ROOT")):
        link = base / link_name
        shared_root = os.getenv(env, "").strip()
        if not (shared_root and link.is_symlink()):
            continue
        target = Path(os.readlink(link))
        if target.name != name or os.path.normpath(target.parent) != os.path.normpath(shared_root):
            continue                         # a custom origin, linked by hand: not ours
        if link_name == "origin" and _linked_by_others(root, name, target):
            kept.append(target)
            continue
        if target.is_dir():
            removed.append(target)
    # the vector table built in the build area (app/vector_place.py): its
    # folder there, and a begun move's, go with the collection
    from app import vector_place
    removed += [t for t in vector_place.held_in_area(root, name) if t not in removed]
    return removed, kept


def forget(root: Path, name: str, *, kinds=None, dry_run: bool = False,
           retire: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Remove the copy at `<root>/<name>` and its held file — or, with
    `kinds`, only those members — and say what went. `retire` is the
    worker's hook that stops the collection's threads before anything under
    its directory is removed; None outside the worker."""
    from . import collection as col, deposit, shard as shard_mod
    root = Path(root)
    base = root / name
    held = deposit.held_file(root, name)
    kinds = tuple(kinds or ())
    unknown = sorted(set(kinds) - set(KINDS))
    if unknown:
        raise CodexError(f"--kinds {','.join(unknown)}: forget removes the copy whole, or "
                         f"its vectors (`--kinds vectors`); no other member is taken "
                         f"apart yet (07 §7.3)")
    if not base.exists() and not held.exists():
        return {"collection": name, "gone": True, "removed": []}
    try:
        codex = col.read(root, name)
    except (CodexError, FileNotFoundError, OSError):
        codex = None                        # half gone already: finish the job
    if kinds:
        return _forget_vectors(root, name, codex, base, kinds=kinds, dry_run=dry_run)
    plan = [p for p in (held, base) if p.exists()]
    from app import text_index
    if dry_run:
        out: dict[str, Any] = {"collection": name, "dry_run": True,
                               "would_remove": [str(p) for p in plan], "removed": []}
        # a text index shared with sisters stays while one of them is held,
        # and goes with its last member (app/text_index.py)
        shared = text_index.leave_at_forget(root, name, dry_run=True)
        if shared is not None:
            out["text_index"] = shared
        if not kinds:
            # the folders under the shared roots too (step 4 below): a dry run
            # naming the directory alone under-said what goes, the SHARD,
            # origin and bundles folders its links point at (2026-11-07,
            # found sizing normattiva's forget for its re-creation)
            gone, kept = shared_folders(root, name)
            out["would_remove"] += [str(p) for p in gone]
            out["kept"] = [str(p) for p in kept]
        return out
    removed: list[str] = []
    # 1. the held file: not held from here on, and no round starts a thread
    #    for it (07 §7.8)
    if held.exists():
        held.unlink()
        removed.append(str(held))
    # 2. its threads, when this runs in the worker
    if retire is not None:
        retire(name)
    # 3. a never-share collection: every deposit erased, no trace (02 §2.9)
    erased = 0
    if codex is not None and deposit.is_deposit(codex) and base.exists():
        erased = deposit.erase_all(root, name, codex)
    # 4. its folders under the shared roots (one mount per kind, a folder per
    #    collection, 2026-10-12): the directory's links name them and rmtree
    #    removes only the links, so the SHARD folder was left behind under
    #    the name — and a collection renamed to that name found it taken
    #    (2026-10-13). The SHARD folder is this collection's copy and goes;
    #    an origin folder goes too unless another held collection links it
    #    (both Normattiva collections read /corpora/normattiva)
    for target in shared_folders(root, name)[0]:
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        removed.append(str(target))
    # 5. a text index shared with sisters (app/text_index.py): kept while
    #    another held member reads it — the next by name writes it if this
    #    one did — and removed with its last member. Before the directory,
    #    whose placement file says where the shared index is
    shared = text_index.leave_at_forget(root, name)
    if shared is not None and shared.get("removed"):
        removed.append(shared["dir"])
    # 6. the directory, whole
    if base.exists():
        shutil.rmtree(base)
        removed.append(str(base))
    text_index.invalidate()
    out = {"collection": name, "erased_deposits": erased, "removed": removed}
    if shared is not None:
        out["text_index"] = shared
    return out
