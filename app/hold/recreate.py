"""Case F of the collection workflow: A RE-CREATION (docs/notes/new-collection.md;
CLAUDE.md RULE 1: "re-create a collection nothing official was published
from, instead of continuing it with a daughter — carrying what is
identical").

The new collection is made by case A's steps under an interim name
(`<name>.next`), held to its predecessor, and takes the predecessor's name
at the end. What it may change is what lies outside its records and its
space: the ENGINE above all (02 §2.4: "Not the engine either" — inside
`collection_id`, outside `space_id`), and with it the calibration the
engine's run measures. Normattiva, 2026-11-07, is the case: its Codex named
a backend whose files were lost (09 §9.3 keeps a Codex's engine for its
life), and the operator chose "B: re-create normattiva with a Codex naming
the archived current engine, and carry its vectors over unchanged".

What this module gives the workflow's two steps of its own:

  carry  `seed_parts` (the predecessor's parts, linked and checked against
         their roots, the snapshot frozen again: hold/seed.py's G3),
         `share_origin` (the origin read where it lies), `sample` (N chunks
         of the records re-encoded by the new engine's run and held to the
         stored vectors at the space's tolerance — BEFORE a byte is copied),
         then hold/carry.py's `carry` (manifest, text index, vector table,
         bundles: copied, the source left whole), `prove_copy` (every file of
         the vector table and the text index the source's, byte for byte, by
         digest, and the counts), `carry_ack` (this peer's record of the same
         terms text).
  swap   `forget_plan` (what `hold collection forget` would remove and free:
         the operator's yes, with the size, is asked in the session that
         deletes, and this step never deletes), then `carried_file` (the
         predecessor's collection file for the new collection: its codex and
         embed mode excepted).

Vectors are ADMISSIBLE in a space from any engine whose run passed the gate
(invariant 1, 03 §3.3), so carrying the space's vectors is copying them,
never re-encoding; the sample shows the new engine reproduces them, and the
gate that follows validates the run before any vector is written.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

from .codex import Codex, CodexError

#: the local name a re-created collection is made under until its swap
INTERIM = ".next"
#: the sample by default: the size of München's hand check of normattiva's
#: engine (2026-11-07: 196 of 200 byte-identical, 4 within cos 0.99999)
SAMPLE = 200
#: invariant 12's bound: an answer within it of the stored vector is near-exact
NEAR = 0.99999


class SampleFailed(CodexError):
    """The new engine's run does not reproduce the vectors to be carried:
    a failed check (07 §7.7, exit 1). Nothing was carried."""
    exit_code = 1


class CopyMismatch(CodexError):
    """A carried tree is not its source's, byte for byte (exit 1)."""
    exit_code = 1


def interim_name(name: str) -> str:
    return f"{name}{INTERIM}"


# ---- check: what a re-creation may change -----------------------------------

#: what a re-creation never compares: the descriptive block (outside
#: `collection_id`) and what `create` computes from the engine's run
_COMPUTED = ("space.calibration.vectors", "space.calibration.query_vectors", "space.envelope")


def _paths(a: Any, b: Any, at: str, depth: int) -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict) and depth > 0:
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            out += _paths(a.get(k), b.get(k), f"{at}.{k}" if at else k, depth - 1)
        return out
    return [] if a == b else [at]


def differences(pred: Codex, new: Codex) -> dict[str, list[str]]:
    """How `new` (the Codex the recipe makes, its reference hashes not yet
    computed) differs from its predecessor:

      `space`    what makes the function (hold/carry.py `space_differences`,
                 and the layers where `space_id` moves for them alone) —
                 a re-creation keeps it, or its vectors are another
                 function's;
      `records`  what decides a record byte (hold/seed.py
                 `record_differences`) — the parts are carried only when
                 they are the same parts;
      `changed`  every Codex field that differs, by its dotted path, the
                 descriptive block and the computed reference hashes left
                 out: `space.encoder.engine` for an engine's re-creation;
                 `[]` is the predecessor itself, nothing to re-create."""
    from .carry import space_differences
    from .seed import record_differences
    space = space_differences(pred, new)
    if pred.space_id != new.space_id and not space:
        space = ["layers (their chunkers, parameters, rules or input forms)"]
    a, b = pred.to_dict(), new.to_dict()
    for d in (a, b):
        d.pop("descriptive", None)
    changed = [p for p in _paths(a, b, "", 3) if not any(p == c or p.startswith(c + ".")
                                                         for c in _COMPUTED)]
    return {"space": space, "records": record_differences(pred, new), "changed": changed}


def refusal(pred_name: str, pred: Codex, new: Codex) -> str | None:
    """Why the recipe cannot re-create `pred_name`, or None."""
    d = differences(pred, new)
    if d["space"]:
        return (f"the recipe's space is not {pred_name}'s: it differs in {', '.join(d['space'])}. A "
                f"re-creation keeps the space (02 §2.4) — its vectors are carried, and in another "
                f"space they would be another function's. A new space is case A, under another name")
    if d["records"]:
        return (f"the recipe's records are not {pred_name}'s: they differ in {', '.join(d['records'])} "
                f"(02 §2.7). Case F carries the same records' parts, chunks and vectors; a "
                f"re-creation that moves records is not built in the workflow yet (`dev carry "
                f"--rebuilt` is its hand route, A1c)")
    if not d["changed"]:
        return (f"the recipe makes {pred_name} itself ({pred.collection_id[:23]}…): nothing differs "
                f"but what its engine's run computes, so there is nothing to re-create")
    return None


# ---- carry ------------------------------------------------------------------

def seed_parts(root: Path, pred_name: str, name: str, *, now: str,
               log: Callable[[str], None]) -> dict[str, Any]:
    """`name`'s SHARD seeded from the predecessor's latest snapshot (hold/
    seed.py's G3, what `hold snapshot create --shards-from` does): its parts
    hard-linked and checked against their roots, its build database a
    build's, the snapshot frozen again and its parts held to the source's
    (`same_parts_or_withdraw`), signed where this peer has a key. A target
    already holding a snapshot of the same parts is nothing to do. The
    caller holds `name`'s write lock."""
    from . import seed, snapshot as sn
    src = seed.latest_snapshot(root, pred_name)
    try:
        mine = seed.latest_snapshot(root, name, src.snapshot_id)
    except CodexError:
        mine = None
    if mine is not None and sorted(seed.parts_of(mine)) == sorted(seed.parts_of(src)):
        return {"snapshot_id": mine.snapshot_id, "snapshot": mine.hash, "parts": len(seed.parts_of(mine)),
                "seeded": "present"}
    from . import records as rec
    out = seed.seed_shards(root, name, pred_name, from_snapshot=src.snapshot_id, log=log)
    # the Codex's validation block as the SHARD member every snapshot lists
    # (09 §9.4), `hold records validation`'s — this Codex's, never copied
    rec.write_validation(root, name)
    snap = sn.create(root, name, snapshot_id=out["snapshot_id"], log=log)
    bad = seed.same_parts_or_withdraw(root, name, snap, pred_name, src.snapshot_id)
    if bad:
        raise CopyMismatch(f"{name}: the seeded snapshot does not reproduce {pred_name}'s parts under "
                           f"this image, so it is withdrawn: {'; '.join(bad[:5])}")
    signed = 0
    try:
        from app.bundles import keys as node_keys
        key = node_keys.load_signing_key()
    except Exception as e:                              # noqa: BLE001 — an unsigned peer still works
        log(f"[carry] {name}: snapshot {snap.snapshot_id} unsigned — {e}")
    else:
        signed = len(sn.sign_snapshot(root, name, snap, key, now=now)["signatures"])
    return {"snapshot_id": snap.snapshot_id, "snapshot": snap.hash, "parts": out["parts"],
            "build_db": out["build_db"], "signatures": signed, "seeded": "seeded"}


def share_origin(root: Path, pred_name: str, name: str) -> str:
    """The re-created collection reads its predecessor's origin where it
    lies (RULE 1: no byte fetched again). A predecessor whose `origin` is a
    link — the node's one mount per kind, `/corpora/normattiva` — gives its
    target to `name`'s link, so `forget` keeps the folder (another held
    collection links it) and the manifest's stored paths resolve after the
    swap; `name`'s own empty folder there goes. An origin that is a directory
    inside the predecessor is hard-linked file by file (hold/carry.py
    `_link_tree`), so its forget frees none of it."""
    from .carry import _link_tree
    src, dst = Path(root) / pred_name / "origin", Path(root) / name / "origin"
    if src.is_symlink():
        target = os.readlink(src)
        if dst.is_symlink() and os.readlink(dst) == target:
            return f"linked to {target} (already)"
        if dst.is_symlink():
            own = dst.resolve()
            if own.is_dir() and any(own.iterdir()):
                raise CodexError(f"{dst} links to {own}, which holds files: a re-created collection "
                                 f"reads its predecessor's origin, and nothing is linked over it")
            dst.unlink()
            if own.is_dir():
                own.rmdir()
        elif dst.exists():
            if not (dst.is_dir() and not any(dst.iterdir())):
                raise CodexError(f"{dst} holds files: a re-created collection reads its "
                                 f"predecessor's origin, and nothing is linked over it")
            dst.rmdir()
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name("origin.recreate")
        if tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(target, target_is_directory=True)
        os.replace(tmp, dst)
        return f"linked to {target}"
    if src.is_dir():
        got = _link_tree(src, dst)
        return f"hard-linked ({got['linked']} linked, {got['copied']} copied, {got['present']} present)"
    return "none (the predecessor holds no origin)"


def sample(codex: Codex, snapshot, shard_tree: Path, *, n: int, seed: bytes,
           vectors_for: Callable[[list[str]], dict[str, list]],
           embed: Callable[[list], list[list[float]]]) -> dict[str, Any]:
    """N chunks drawn from the records, re-encoded with the new engine's run
    and held to the vectors stored for them — what München did by hand for
    normattiva (200 chunks: 196 byte-identical, 4 within cos 0.99999).

    The draw is 03 §3.2's seeded one, as `vectors-sample` draws it
    (hold/vectors_check.py): ⌈N / quorum.n⌉ parts of the snapshot, each cut
    in the parse pool (`picked_inputs`: the chunk's text, or a pair layer's
    `fields`), the chunks within each drawn by the same seed. Every cosine is
    in f32-or-wider, both sides unit-normed (03 §3.1); an answer is EXACT
    when it is stored as the very bytes held (the writer's cast, the space's
    dtype), NEAR within cos 0.99999 (invariant 12). It passes when at least
    one chunk was checked and the worst reaches the space's tolerance: a
    carry vouches for every vector it takes, so a quorum is not enough."""
    import numpy as np
    from app import parallel
    from . import codex as cx, vectors_check
    tol = float(codex.space.tolerance)
    names = {f"{s.slice}/{s.n:05d}": s for s in snapshot.shards}
    if not names or n <= 0:
        return {"verdict": "inconclusive", "why": "no part to draw from", "asked": n, "checked": 0}
    k = min(len(names), max(1, math.ceil(n / int(codex.space.calibration.n or 16))))
    # every part in the draw's order (`cx.sample` returns the lowest scores
    # first, so its first k ARE the draw of k): parts too small to give
    # their share are followed by the next, until N chunks are checked
    order = [names[x] for x in cx.sample(seed, len(names), iter(sorted(names)))]
    per = math.ceil(n / k)
    prepared = parallel.each_part(vectors_check.picked_inputs,
                                  [(str(shard_tree), codex, e.path, seed, per) for e in order])
    ids: list[str] = []
    inputs: list[Any] = []
    stored: list[list[float]] = []
    drawn: list = []
    unembedded = 0
    try:
        for entry, (picked, got) in zip(order, prepared):
            if len(ids) >= n:
                break
            drawn.append(entry)
            held = vectors_for(list(picked)) if picked else {}
            for cid in picked:
                if len(ids) >= n:
                    break
                if cid not in held or got.get(cid) is None:
                    unembedded += 1
                    continue
                ids.append(cid)
                inputs.append(got[cid])
                stored.append(held[cid])
    finally:
        close = getattr(prepared, "close", None)
        if close:
            close()
    out: dict[str, Any] = {"asked": n, "checked": len(ids), "parts": sorted(f"{e.slice}/{e.n:05d}" for e in drawn),
                           "seed": seed.hex(), "tolerance": codex.space.tolerance,
                           "unembedded": unembedded, "dtype": codex.space.dtype}
    if not ids:
        out.update(verdict="inconclusive", why="not one drawn chunk is held with a vector: there is "
                                                "nothing to reproduce, which is not a pass")
        return out
    answers = embed(inputs)
    runs = sorted(r or "" for r in (getattr(embed, "runs", None) or ()))
    dt = np.float16 if codex.space.dtype == "f16" else np.float32
    mine = np.asarray(answers, dtype=np.float64)
    held_v = np.asarray(stored, dtype=np.float64)
    if mine.shape != held_v.shape:
        raise SampleFailed(f"the new engine answered {mine.shape} vectors for the {held_v.shape} held")
    unit = lambda m: m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-12, None)  # noqa: E731
    cos = np.sum(unit(mine) * unit(held_v), axis=1)
    exact = [np.asarray(a, dtype=dt).tobytes() == np.asarray(s, dtype=dt).tobytes()
             for a, s in zip(answers, stored)]
    below = [cid for cid, c in zip(ids, cos) if not c >= tol]
    near = sum(1 for e, c in zip(exact, cos) if not e and c >= NEAR)
    out.update(exact=int(sum(exact)), near=near, within=int(len(ids) - sum(exact) - near - len(below)),
               below=len(below), below_ids=below[:10], worst=float(np.min(cos)),
               median=float(np.median(cos)), runs=runs,
               verdict="pass" if not below else "fail",
               why=(f"{len(ids)} chunk(s) reproduce at tolerance {codex.space.tolerance}" if not below else
                    f"{len(below)} of {len(ids)} chunk(s) below tolerance {codex.space.tolerance} "
                    f"(worst {float(np.min(cos)):.10f})"))
    return out


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_digest(path: Path, *, threads: int | None = None) -> dict[str, Any]:
    """`{files, bytes, sha256}` of every file under `path`: the sha256 of
    the sorted lines `<relative path> <size> <sha256 of its bytes>` (a link
    as `<relative path> -> <target>`). Two trees with one digest hold the
    same bytes at the same paths — how a copy is proven its source's, as
    `collection.adopt_bundles_root` proves its copy by size and SHA-256.
    Threads, since it is I/O and hashlib lets go of the GIL (the cores the
    node allows, CLAUDE.md: multi-threading is a must). An absent tree is
    `{files: 0, bytes: 0, sha256: None}`.

    THE STANDARD, NOT ADOPTED: a Merkle tree over the files (BitTorrent v2's
    piece layers, 09 §9.16's members) would prove a part of the tree without
    reading the rest; here both whole trees are read once anyway, and one
    flat digest is the whole proof."""
    from app import parallel
    path = Path(path)
    if not path.exists():
        return {"files": 0, "bytes": 0, "sha256": None}
    files: list[tuple[str, Path]] = []
    links: list[str] = []
    for base, dirs, names in os.walk(path):
        dirs.sort()
        for d in list(dirs):
            if (Path(base) / d).is_symlink():
                links.append(f"{(Path(base) / d).relative_to(path).as_posix()} -> {os.readlink(Path(base) / d)}")
                dirs.remove(d)
        for nm in sorted(names):
            p = Path(base) / nm
            rel = p.relative_to(path).as_posix()
            if p.is_symlink():
                links.append(f"{rel} -> {os.readlink(p)}")
            elif p.is_file():
                files.append((rel, p))
    n = threads or max(4, parallel.processes())
    with ThreadPoolExecutor(max_workers=n) as ex:
        sums = list(ex.map(lambda rp: _file_sha256(rp[1]), files))
    sizes = [p.stat().st_size for _, p in files]
    lines = sorted([f"{rel} {size} {h}" for (rel, _), size, h in zip(files, sizes, sums)] + links)
    return {"files": len(files), "bytes": int(sum(sizes)),
            "sha256": "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()}


def _bytes_under(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for base, _dirs, names in os.walk(path):
        for nm in names:
            try:
                st = os.lstat(Path(base) / nm)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


#: what the carry copies and `room` counts, by its path in the collection
COPIED = ("lance", "index", "state/manifest.sqlite")


def sample_seed(request_digest: str, runs) -> bytes:
    """The carry's sample seed: THE SAME REQUEST ON THE SAME RUN DRAWS THE
    SAME CHUNKS, so a sample that failed is not drawn away by running again,
    and a re-calibrated run (the operator fixed the engine) draws anew. Its
    own check, no adversary to out-guess, so it needs no secrecy and is
    recorded either way. `runs`: the runs calibrate measured."""
    return hashlib.sha256("\0".join(("louise recreate sample", request_digest,
                                     *sorted(runs))).encode("utf-8")).digest()


def to_copy(root: Path, pred_name: str, name: str) -> dict[str, int]:
    """The bytes of each of `COPIED` the target does not hold yet (a resumed
    carry counts only what is left), counted from the predecessor's files by
    their size."""
    src, dst = Path(root) / pred_name, Path(root) / name
    out: dict[str, int] = {}
    for rel in COPIED:
        there = dst / rel
        if not there.exists() or (there.is_dir() and not any(there.iterdir())):
            out[rel] = _bytes_under(src / rel) if (src / rel).exists() else 0
    return out


def room(root: Path, pred_name: str, name: str, *, make: bool = True) -> tuple[int, int]:
    """`(bytes the carry's copy needs, bytes free where it writes)`: the
    predecessor's vector table, text index and manifest, each counted only
    where the target does not hold it yet (a resumed carry). A copy-on-write
    filesystem may need none of it (`cp --reflink`); it is not counted on.
    `make=False` (the dry run) makes nothing: the free space is read where
    the target would be made."""
    import shutil
    dst = Path(root) / name
    need = sum(to_copy(root, pred_name, name).values())
    if make:
        dst.mkdir(parents=True, exist_ok=True)
    where = dst
    while not where.exists() and where != where.parent:
        where = where.parent
    return need, shutil.disk_usage(where).free


def no_room(pred_name: str, name: str, need: int, free: int) -> str:
    """The carry's refusal when the copy does not fit (the run's and the dry
    run's one sentence)."""
    return (f"carry: copying {pred_name}'s vector table, text index and manifest needs {human(need)} "
            f"and {human(free)} is free where {name} lives: nothing was copied. Free the space, then "
            f"run this again")


def _documents_write_free(manifest: Path) -> int | None:
    """`carry._documents`, writing nothing: a read-only SQLite open of a WAL
    database at rest CREATES its -wal and -shm files (and cannot remove
    them), so where neither exists — no connection is open, every commit is
    in the main file — it is read `immutable=1` (no -shm, no lock). Where
    they exist, a connection has it open: read `mode=ro`, whose only write
    is the read mark every reader takes in the -shm."""
    import sqlite3
    if not manifest.is_file():
        return None
    at_rest = not any(Path(f"{manifest}{s}").exists() for s in ("-wal", "-shm"))
    con = sqlite3.connect(f"file:{manifest.as_posix()}?mode=ro{'&immutable=1' if at_rest else ''}", uri=True)
    try:
        return int(con.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    except sqlite3.OperationalError:
        return 0                                    # no documents table: nothing held
    except sqlite3.DatabaseError:
        return None                                 # a checkpoint under an immutable read
    finally:
        con.close()


def counts(root: Path, name: str, *, write_free: bool = False) -> dict[str, int | None]:
    """The documents a collection's manifest holds and the rows its vector
    table holds (None where it cannot be read). `write_free` (the dry run):
    the manifest read so that not a file changes."""
    from app import spaces
    from .carry import _documents
    base = Path(root) / name
    manifest = base / "state" / "manifest.sqlite"
    return {"documents": _documents_write_free(manifest) if write_free else _documents(manifest),
            "vectors": spaces.known_lance_rows(base / "lance")}


def prove_copy(root: Path, pred_name: str, name: str) -> dict[str, Any]:
    """After the carry: the vector table and the text index are the
    predecessor's byte for byte (`tree_digest`, both sides), and the two
    manifests hold as many documents. The manifest itself is re-pinned by
    the carry and is held by its count, as hold/carry.py holds it. Raises
    `CopyMismatch` naming what differs."""
    out: dict[str, Any] = {"from": counts(root, pred_name), "to": counts(root, name)}
    bad = [k for k in ("documents", "vectors") if out["from"][k] != out["to"][k]]
    for tree in ("lance", "index"):
        a = tree_digest(Path(root) / pred_name / tree)
        b = tree_digest(Path(root) / name / tree)
        out[tree] = b
        if a != b:
            bad.append(f"{tree}/ ({a['files']} files {a['sha256']} against {b['files']} files {b['sha256']})")
    if bad:
        raise CopyMismatch(f"carry: {name} is not {pred_name}'s copy: {'; '.join(bad)}. Nothing is "
                           f"recorded; pause both and run the re-creation again")
    return out


def carry_ack(root: Path, pred_name: str, name: str, codex: Codex) -> str:
    """This peer's acknowledgement of the terms, kept for the re-created
    collection when it pins the very terms text its predecessor's ack
    records (09 §9.13; invariant 9: "one ack covers corpus, pre-embeddings
    and SHARD"). Copied byte for byte — it says when, and for which Codex,
    the operator acknowledged those bytes on this peer — so the record
    outlives the predecessor's forget. Another terms text is acknowledged
    again by the operator (`hold collection ack`), never carried."""
    src = Path(root) / pred_name / "state" / "ack.json"
    dst = Path(root) / name / "state" / "ack.json"
    want = getattr(codex.license, "terms_sha256", None)
    if not src.is_file():
        return "none held"
    try:
        body = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"not carried: {src} does not read"
    if not want or body.get("terms_sha256") != want:
        return (f"not carried: it acknowledges {str(body.get('terms_sha256'))[:23]}…, the Codex pins "
                f"{str(want)[:23]}… — `louise hold collection ack {name}`")
    if dst.is_file() and dst.read_bytes() == src.read_bytes():
        return "present"
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".json.tmp")
    tmp.write_bytes(src.read_bytes())
    os.replace(tmp, dst)
    return "carried"


# ---- swap -------------------------------------------------------------------

def human(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, size in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def forget_plan(root: Path, name: str) -> dict[str, Any]:
    """What `hold collection forget <name>` would remove (its own dry run,
    app/hold/forget.py) and its size: every inode counted once, and FREED
    only where every link to it is among what goes — the parts and bundles
    the re-created collection hard-linked stay on disk."""
    from .forget import forget
    plan = forget(Path(root), name, dry_run=True)
    seen: dict[tuple[int, int], list[int]] = {}
    for p in plan.get("would_remove") or ():
        p = Path(p)
        walk = [(str(p.parent), [], [p.name])] if not p.is_dir() or p.is_symlink() else os.walk(p)
        for base, _dirs, names in walk:
            for nm in names:
                try:
                    st = os.lstat(Path(base) / nm)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    seen[key][2] += 1
                else:
                    seen[key] = [st.st_size, st.st_nlink, 1]
    total = sum(v[0] for v in seen.values())
    freed = sum(v[0] for v in seen.values() if v[2] >= v[1])
    return {"would_remove": list(plan.get("would_remove") or ()), "kept": list(plan.get("kept") or ()),
            "files": len(seen), "bytes": total, "freed": freed}


_CODEX_LINE = re.compile(r'^codex\s*=.*$', re.MULTILINE)
_EMBED_LINE = re.compile(r'^(embed|embed_mode)\s*=.*$', re.MULTILINE)


def carried_file(text: str, collection_id: str) -> tuple[str, str | None]:
    """`(the file, the predecessor's embed mode)`: the predecessor's
    collection file (07 §7.8) for the re-created collection, every key as
    the operator left it — its slices, kinds, sharing, encoder, reranker —
    but `codex`, the new collection's id, and `embed`, "off": embedding
    stays off until the operator turns it on (docs/notes/new-collection.md).
    Only the top-level lines before the first table are edited; the result
    is read back and must say exactly that, or nothing is written."""
    head_end = re.search(r'^\[', text, re.MULTILINE)
    head, tail = (text[:head_end.start()], text[head_end.start():]) if head_end else (text, "")
    if len(_CODEX_LINE.findall(head)) != 1:
        raise CodexError("the predecessor's collection file names its codex other than once on a "
                         "line of its own: it is carried by hand")
    before = tomllib.loads(text)
    mode = before.get("embed", before.get("embed_mode"))
    head = _EMBED_LINE.sub("", head)
    head = _CODEX_LINE.sub(f'codex = "{collection_id}"\nembed = "off"', head, count=1)
    head = re.sub(r'\n{3,}', "\n\n", head)
    out = head + tail
    after = tomllib.loads(out)
    want = {k: v for k, v in before.items() if k not in ("codex", "embed", "embed_mode")}
    if after.get("codex") != collection_id or after.get("embed") != "off" or \
            {k: v for k, v in after.items() if k not in ("codex", "embed")} != want:
        raise CodexError("the predecessor's collection file does not carry by its two lines: it is "
                         "carried by hand")
    return out, (str(mode) if mode is not None else None)
