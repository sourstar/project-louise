"""`louise hold annotations retarget <name>` — an annotations collection
whose target was replaced, re-created against the collection the target
became, by ONE command (07 §7.3, amended 2026-11-17: "a re-created target's
annotations collection is created from [its claims] against the new
target"; docs/notes/manual-steps.md: two commands chained by hand are a
hand step).

The case: gutenberg's refit in place (amendment A1l, 2026-11-27) gives the
collection a new `collection_id`; gutenberg-book-editions names the old one
in its Codex (`annotates`). Its claims apply meanwhile, as a twin's
(hold/carry.py `records_twins_of`); retargeted, they name the new id.

The steps, resumable from `state/retarget.json` (invariant 2), the existing
code reused:

  claims   the latest snapshot's claims, one per line, each pinned as its
           record holds it (`annotations_export.export`, the parse pool);
  create   `<name>.retarget`, an annotations collection from them against
           each target's holder at its latest snapshot (`annotations.create`:
           every end read there and every pin checked; `--repin` where the
           target's text moved), its container and selection rules the old
           one's; its file the old one's, its `codex` line the new id
           (`collection_file.recodex`);
  swap     the old one renamed `<name>.retargeted-<stamp>` and its file set
           aside in its own state, so the node no longer loads it (nothing
           deleted: its forget is the operator's), then `<name>.retarget`
           renamed `<name>` (`hold collection rename`'s function, which moves
           the held file, the pause and the stored paths).

A target already its holder's id is nothing to do, said."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .codex import CodexError

MARKER = "retarget.json"
INTERIM = ".retarget"
ASIDE = ".retargeted-"
RETIRED_FILE = "retired-collection.toml"


def _marker(root: Path, name: str) -> tuple[Path | None, dict | None]:
    """Where a retarget of `name` in progress records itself: in the old
    collection's state, before its rename, then in its renamed folder."""
    p = Path(root) / name / "state" / MARKER
    if p.is_file():
        return p, json.loads(p.read_text(encoding="utf-8"))
    for d in sorted(Path(root).glob(f"{name}{ASIDE}*")):
        q = d / "state" / MARKER
        if q.is_file():
            m = json.loads(q.read_text(encoding="utf-8"))
            if not m.get("done"):
                return q, m
    return None, None


def _save(p: Path, m: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(m, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def plan(root, name: str) -> dict[str, Any]:
    """The targets and the holder each now answers by, nothing written."""
    from . import annotations as ann, collection as col, seed
    from .codex import targets_of
    root = Path(root)
    _, codex = col.resolve(root, name)
    if getattr(codex, "annotates", None) is None:
        raise CodexError(f"retarget: {name} is not an annotations collection")
    out = []
    for t in targets_of(codex):
        holder, hcodex = ann._target_holder(root, t.collection_id)
        snap = seed.latest_snapshot(root, holder)
        out.append({"from": t.collection_id, "holder": holder, "to": hcodex.collection_id,
                    "from_snapshot": t.from_snapshot, "snapshot": snap.snapshot_id,
                    "moves": hcodex.collection_id != t.collection_id})
    return {"name": name, "collection_id": codex.collection_id, "targets": out,
            "due": any(t["moves"] for t in out)}


def retarget(root, name: str, *, platform_image: str, key=None, now: str, repin: bool = False,
             log: Callable[[str], None] = print) -> dict[str, Any]:
    """The three steps (module docstring), from where the marker says. The
    caller holds the write lock of `name` and has paused it."""
    from app import collection_file
    from . import annotations as ann, annotations_export, collection as col, deposit, rename as rename_mod
    from .codex import container_of, targets_of
    root = Path(root)
    mp, m = _marker(root, name)
    if m is None:
        p = plan(root, name)
        if not p["due"]:
            log(f"[retarget] {name}: every target is its holder's own id; nothing to do")
            return {**p, "done": True, "nothing": True}
        _, old = col.resolve(root, name)
        mp = root / name / "state" / MARKER
        m = {"name": name, "from": old.collection_id, "targets": p["targets"],
             "interim": f"{name}{INTERIM}", "aside": f"{name}{ASIDE}{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}",
             "steps": {}, "done": False}
        _save(mp, m)
    steps = m["steps"]
    interim, aside = m["interim"], m["aside"]
    claims = (root / name / "state" / "retarget-claims.jsonl") if (root / name).is_dir() else \
        (root / aside / "state" / "retarget-claims.jsonl")
    if not steps.get("claims"):
        got = annotations_export.export(root, name, claims, log=log)
        steps["claims"] = {"path": str(claims.relative_to(root)), **{k: v for k, v in got.items()
                                                                     if isinstance(v, (int, str))}}
        _save(mp, m)
    if not steps.get("create"):
        _, old = col.resolve(root, name)
        tgs = targets_of(old)
        sels = [t.select for t in tgs]
        shard = col.shard_dir_of(root, name)
        rules = ann.select_rules([shard / ann.select_member(s) for s in sels if s is not None])
        annotator = next((c.producer for c in old.credits if c.kind == "corpus"), None) or "annotator"
        if not (root / interim / "shard" / "codex.json").is_file():
            codex, _snap = ann.create(
                root, interim, targets=[(t["holder"], t["snapshot"]) for t in m["targets"]],
                container=container_of(old), selects=sels if any(sels) else None, select_rules=rules,
                claims=root / steps["claims"]["path"], platform_image=platform_image,
                title=old.descriptive.title, summary=old.descriptive.summary, annotator=annotator,
                key=key, now=now, log=lambda s: log(f"[retarget] {s}"), repin=repin)
        else:
            codex = col.read(root, interim)
        # its file: the old one's, every key the operator set (method, kinds,
        # share), its codex line the new id
        old_file, new_file = deposit.held_file(root, name), deposit.held_file(root, interim)
        if old_file.is_file():
            tmp = new_file.with_name(new_file.name + ".retarget")
            tmp.write_bytes(old_file.read_bytes())
            os.replace(tmp, new_file)
            collection_file.recodex(new_file, codex.collection_id)
        steps["create"] = {"collection_id": codex.collection_id}
        _save(mp, m)
        log(f"[retarget] {interim}: {codex.collection_id[:23]}… made from {name}'s claims against "
            + ", ".join(f"{t['holder']} ({t['to'][:23]}…, {t['snapshot']})" for t in m["targets"]))
    # the swap: the old one aside (renamed, its file retired into its state), the new one named
    if not steps.get("old_aside"):
        if (root / name).is_dir() and col.read(root, name).collection_id == m["from"]:
            rename_mod.rename(root, name, aside)
        mp = root / aside / "state" / MARKER
        held = deposit.held_file(root, aside)
        if held.is_file():
            os.replace(held, root / aside / "state" / RETIRED_FILE)
        steps["old_aside"] = True
        _save(mp, m)
        log(f"[retarget] {name}: the old collection kept as {aside}, its file set aside in its state "
            f"(the node no longer loads it; `louise hold collection forget {aside}` is the operator's)")
    if not steps.get("named"):
        if (root / interim).is_dir():
            rename_mod.rename(root, interim, name)
        steps["named"] = True
        m["done"] = True
        _save(mp, m)
        log(f"[retarget] {name}: now {steps['create']['collection_id'][:23]}…, its claims naming "
            + ", ".join(t["to"][:23] + "…" for t in m["targets"]))
    return m
