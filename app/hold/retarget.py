"""`louise hold annotations retarget <name> [--undo]` — an annotations
collection whose target was replaced, re-created against the collection the
target became, by ONE command (07 §7.3, amended 2026-11-17: "a re-created
target's annotations collection is created from [its claims] against the new
target"; docs/notes/manual-steps.md: two commands chained by hand are a hand
step).

The case: gutenberg's refit in place (amendment A1l, 2026-11-27) gives the
collection a new `collection_id`; gutenberg-book-editions names the old one
in its Codex (`annotates`). Its claims apply meanwhile, as a twin's
(hold/carry.py `records_twins_of`); retargeted, they name the new id.

Refused while the target's refit is not finished and gated: the claims are
pinned against the new id's snapshot once it is the collection's for good.

The steps, resumable from `<root>/.retarget/<name>.json` (one place, beside
the collections, that no rename moves), the existing code reused:

  claims   the latest snapshot's claims, one per line, pinned as their
           records hold them (`annotations_export.export`, the parse pool);
  create   `<name>.retarget` from them against each target's holder at its
           latest snapshot — a refit's successor first (`annotations.create`,
           CALLED AGAIN until its snapshot is there: it resumes by design, and
           a stop in its parts left a Codex with no snapshot); its parts'
           roots and records checked and attested for the new id, as the refit
           does (quarantine's test 1); its file the old one's, its `codex` line
           the new id (`collection_file.recodex`);
  out      the old one renamed `<name>.retargeted-<time>` (`hold collection
           rename`'s function, RE-RUN while its `rename.json` is in either
           folder: it moves the folder first and the file last), its file set
           aside IN ITS OWN DIRECTORY (`collection_file.set_aside`: no move
           across filesystems — /config is not /data on the node);
  in       `<name>.retarget` renamed `<name>`, the same way;
  tuck     the old one's folder moved under the new one's own state
           (`<name>/state/.replaced/<old id>/collection`, the same
           filesystem): no longer a held collection — no listing, no cascade,
           no update reads it — and never deleted (its folder and the file set
           aside are the operator's to remove).

`--undo` is the same swap backwards: the folder tucked away comes back, its
file restored, and the retargeted one is tucked away in its turn. It is the
way back a refit's rollback names (a rollback is refused while an
annotations collection names the new id)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .codex import CodexError

MARKER_DIR = ".retarget"
INTERIM = ".retarget"
ASIDE = ".retargeted-"
UNDONE = ".retarget-undone-"
TUCKED = "collection"
#: at most this many parts of the new collection checked and attested
ATTEST_PARTS = 8


def marker_path(root, name: str) -> Path:
    return Path(root) / MARKER_DIR / f"{name}.json"


def _read(p: Path) -> dict | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _save(p: Path, m: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(m, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _hex(cid: str) -> str:
    return cid.split(":", 1)[-1]


def holder_of(root, collection_id: str) -> tuple[str, Any]:
    """The collection held here that the target `collection_id` became: a
    refit's successor first (`refit.is_predecessor`: it IS the target, under
    a new Codex), else the annotations' own resolution (`_target_holder`)."""
    from . import annotations as ann, collection as col, refit
    for n, c in col._held(Path(root)):
        if getattr(c, "annotates", None) is None and refit.is_predecessor(Path(root) / n / "state", collection_id):
            return n, c
    return ann._target_holder(Path(root), collection_id)


def refit_unfinished(root, holder: str, target_id: str) -> str | None:
    """Why a retarget against `holder` must wait, or None: `holder`'s refit
    from `target_id` is under way, or done and not gated."""
    from . import refit
    m = refit.read_marker(Path(root), holder)
    if m is None or m.get("rolled_back") or m["predecessor"]["collection_id"] != target_id:
        return None
    if not m.get("done"):
        return f"{holder}'s refit from {target_id[:23]}… is not finished: `louise hold collection refit {holder}` first"
    if not refit.gated(m):
        return (f"{holder}'s refit from {target_id[:23]}… is done and its gate has not passed: the same "
                f"`louise hold collection refit {holder}` runs its CPU reference and gate")
    return None


def plan(root, name: str) -> dict[str, Any]:
    """The targets and the holder each now answers by, nothing written."""
    from . import collection as col, seed
    from .codex import targets_of
    root = Path(root)
    _, codex = col.resolve(root, name)
    if getattr(codex, "annotates", None) is None:
        raise CodexError(f"retarget: {name} is not an annotations collection")
    out = []
    for t in targets_of(codex):
        holder, hcodex = holder_of(root, t.collection_id)
        snap = seed.latest_snapshot(root, holder)
        out.append({"from": t.collection_id, "holder": holder, "to": hcodex.collection_id,
                    "from_snapshot": t.from_snapshot, "snapshot": snap.snapshot_id,
                    "moves": hcodex.collection_id != t.collection_id,
                    "waits": refit_unfinished(root, holder, t.collection_id)})
    return {"name": name, "collection_id": codex.collection_id, "targets": out,
            "due": any(t["moves"] for t in out)}


def _holds_codex(root: Path, name: str) -> bool:
    return (root / name / "shard" / "codex.json").is_file()


def rename_resumed(root: Path, old: str, new: str) -> str:
    """`old` renamed `new` by `hold collection rename`'s function — RE-RUN
    while its `rename.json` is in either folder (it writes it first, moves
    the folder, the held file last, and removes it): a stop anywhere inside
    is finished here, never refused as "taken"."""
    from . import rename as rename_mod
    for d in (root / new, root / old):
        mk = _read(d / "state" / rename_mod.MARKER)
        if mk is not None and mk.get("from") == old and mk.get("to") == new:
            rename_mod.rename(root, old, new)
            return "resumed"
    if _holds_codex(root, old) and not (root / new).exists():
        rename_mod.rename(root, old, new)
        return "renamed"
    if _holds_codex(root, new) and not (root / old).exists():
        return "done"
    raise CodexError(f"retarget: {old} → {new}: neither is where a rename leaves it, and no rename of it is "
                     f"recorded; nothing more was moved")


def _out(root: Path, name: str, retire_as: str, s: dict, save) -> None:
    """The collection named `name` renamed `retire_as`, its file set aside in
    its own directory. Each half decided by what lies where."""
    from app import collection_file
    from . import deposit
    if not s.get("renamed"):
        s["rename"] = rename_resumed(root, name, retire_as)
        s["renamed"] = True
        save()
    if not s.get("file"):
        held = deposit.held_file(root, retire_as)
        s["file"] = str(collection_file.set_aside(held).name) if held.is_file() else s.get("file") or "none"
        save()


def _tuck(root: Path, name: str, retired: str, retired_id: str) -> Path:
    """The retired folder moved under `name`'s own state — no longer held,
    never deleted; the same filesystem as the collections' folders."""
    dst = root / name / "state" / ".replaced" / _hex(retired_id) / TUCKED
    src = root / retired
    if src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
    elif src.exists():
        raise CodexError(f"retarget: {src} and {dst} both exist; nothing is moved over another")
    return dst


def _attest(root: Path, name: str, codex, log) -> dict[str, Any]:
    """Quarantine's first test for the new annotations collection, as the
    refit runs its own: `roots` and `records` over its parts (a seeded sample
    of `ATTEST_PARTS` where it has more), signed with this node's key."""
    import random
    from . import check as chk, equivalence, seed
    snap = seed.latest_snapshot(root, name)
    parts = [s.path for s in snap.shards]
    if len(parts) > ATTEST_PARTS:
        parts = random.Random(codex.collection_id).sample(parts, ATTEST_PARTS)
    try:
        from app.bundles import keys as node_keys
        key = node_keys.load_signing_key()
    except Exception:   # noqa: BLE001 — checked, not attested; said
        key = None
    got = chk.attest_landed(root, name, codex, snap, parts, key=key, platform_image=equivalence.running_image())
    log(f"[retarget] {name}: " + ", ".join(f"{k} {v.upper()}" for k, v in got["results"].items())
        + (f"; attested ({len(got['attestations'])})" if got["attestations"] else f"; NOT attested — {got['why']}"))
    failed = sorted(c for c, r in got["results"].items() if r == "fail")
    if failed:
        raise CodexError(f"retarget: {name}: {', '.join(failed)} failed over its new parts; the old collection "
                         f"is untouched")
    return {"parts": got["parts"], "results": got["results"], "attestations": got["attestations"],
            "why": got["why"]}


def retarget(root, name: str, *, platform_image: str, key=None, now: str, repin: bool = False,
             log: Callable[[str], None] = print) -> dict[str, Any]:
    """The steps (module docstring), from where the marker says."""
    from app import collection_file
    from . import annotations as ann, annotations_export, collection as col, deposit, seed
    from .codex import container_of, targets_of
    root = Path(root)
    mp = marker_path(root, name)
    m = _read(mp)
    if m is not None and m.get("done"):
        log(f"[retarget] {name}: done already ({m['steps']['create']['collection_id'][:23]}…)")
        return m
    if m is None:
        p = plan(root, name)
        waits = [t["waits"] for t in p["targets"] if t["waits"]]
        if waits:
            raise CodexError(f"retarget: {'; '.join(waits)}; nothing was written")
        if not p["due"]:
            log(f"[retarget] {name}: every target is its holder's own id; nothing to do")
            return {**p, "done": True, "nothing": True}
        _, old = col.resolve(root, name)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        m = {"name": name, "from": old.collection_id, "targets": p["targets"], "interim": f"{name}{INTERIM}",
             "aside": f"{name}{ASIDE}{stamp}", "steps": {}, "done": False}
        _save(mp, m)
    steps, interim, aside = m["steps"], m["interim"], m["aside"]

    def save() -> None:
        _save(mp, m)
    claims = root / MARKER_DIR / f"{name}.claims.jsonl"
    if not steps.get("claims"):
        got = annotations_export.export(root, name, claims, log=log)
        steps["claims"] = {k: v for k, v in got.items() if isinstance(v, (int, str))}
        save()
    if not steps.get("create"):
        _, old = col.resolve(root, name)
        sels = [t.select for t in targets_of(old)]
        rules = ann.select_rules([col.shard_dir_of(root, name) / ann.select_member(s) for s in sels if s is not None])
        annotator = next((c.producer for c in old.credits if c.kind == "corpus"), None) or "annotator"
        # CALLED AGAIN until its snapshot is there: it resumes by design, and
        # a stop in its parts left a Codex and no snapshot
        codex, _snap = ann.create(
            root, interim, targets=[(t["holder"], t["snapshot"]) for t in m["targets"]],
            container=container_of(old), selects=sels if any(sels) else None, select_rules=rules,
            claims=claims, platform_image=platform_image, title=old.descriptive.title,
            summary=old.descriptive.summary, annotator=annotator, key=key, now=now,
            log=lambda s: log(f"[retarget] {s}"), repin=repin)
        snap = seed.latest_snapshot(root, interim)
        if snap.collection_id != codex.collection_id:
            raise CodexError(f"retarget: {interim} holds no snapshot of {codex.collection_id}; nothing more was moved")
        attested = _attest(root, interim, codex, log)
        old_file, new_file = deposit.held_file(root, name), deposit.held_file(root, interim)
        if old_file.is_file():
            tmp = new_file.with_name(new_file.name + ".retarget")
            tmp.write_bytes(old_file.read_bytes())
            os.replace(tmp, new_file)
            collection_file.recodex(new_file, codex.collection_id)
        steps["create"] = {"collection_id": codex.collection_id, "snapshot": snap.snapshot_id, "attest": attested}
        save()
        log(f"[retarget] {interim}: {codex.collection_id[:23]}… made from {name}'s claims against "
            + ", ".join(f"{t['holder']} ({t['to'][:23]}…, {t['snapshot']})" for t in m["targets"]))
    _out(root, name, aside, steps.setdefault("out", {}), save)
    if not steps.get("in"):
        steps["in"] = rename_resumed(root, interim, name)
        save()
    if not steps.get("tuck"):
        steps["tuck"] = str(_tuck(root, name, aside, m["from"]).relative_to(root))
        m["done"] = True
        save()
        log(f"[retarget] {name}: now {steps['create']['collection_id'][:23]}…; the old one kept in "
            f"{steps['tuck']} (its file set aside as {steps['out'].get('file')}), no longer held")
    return m


def undo(root, name: str, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The swap backwards (module docstring): the way back a refit's rollback
    names. Resumable from the same marker (`undo`)."""
    from app import collection_file
    from . import collection as col, deposit
    root = Path(root)
    mp = marker_path(root, name)
    m = _read(mp)
    if m is None or not m.get("done") or m.get("nothing"):
        raise CodexError(f"retarget --undo: {name} records no finished retarget ({mp})")
    u = m.setdefault("undo", {})
    new_id = m["steps"]["create"]["collection_id"]
    back = m["aside"]
    undone = u.setdefault("as", f"{name}{UNDONE}{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}")

    def save() -> None:
        _save(mp, m)
    if not u.get("untucked"):
        src = root / m["steps"]["tuck"]
        if src.exists() and not (root / back).exists():
            os.replace(src, root / back)
        u["untucked"] = True
        save()
    if not u.get("file"):
        hidden = m["steps"]["out"].get("file")
        if hidden and hidden != "none":
            collection_file.restore_aside(deposit.held_file(root, back).parent / hidden,
                                          deposit.held_file(root, back))
        u["file"] = True
        save()
    if u.get("out") or (_holds_codex(root, name) and col.read(root, name).collection_id == new_id):
        _out(root, name, undone, u.setdefault("out", {}), save)
    if not u.get("in"):
        u["in"] = rename_resumed(root, back, name)
        save()
    if not u.get("tuck"):
        u["tuck"] = str(_tuck(root, name, undone, new_id).relative_to(root))
        save()
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    os.replace(mp, mp.with_name(f"{name}.undone-{stamp}.json"))
    log(f"[retarget] {name}: undone — it names {m['from'][:23]}… again; the retargeted one kept in {u['tuck']}")
    return m


def naming(root, collection_id: str) -> list[str]:
    """The held annotations collections whose targets name `collection_id`."""
    from . import collection as col
    from .codex import targets_of
    return sorted(n for n, c in col._held(Path(root)) if getattr(c, "annotates", None) is not None
                  and any(t.collection_id == collection_id for t in targets_of(c)))
