"""app/vector_place.py — WHERE A COLLECTION'S VECTOR TABLE LIVES: beside the
collection on the node's data pool ("hot", where searches read it), or in
the BUILD AREA, `LOUISE_BUILD_DIR` (config.BUILD_DIR, identity tier, 07
§7.8), which this module alone resolves (invariant 6, as app/derived.py
resolves the cache).

The decision (operator, 2026-11-07, docs/notes/storage-tiering.md §8, "B,
ssd3 as the build area"): a cold dense search is ×3.8 slower on ssd3 (p50
352–384 ms against 96–101 on ssd4), so the SEARCHED tables stay on ssd4; a
table being BUILT — a collection `hold collection new` makes (cases A and
F), not yet the one people search — is written on ssd3, which costs nothing
(embedding writes ~0.1 MB/s, ssd3 sustains 158 MiB/s), and moves to ssd4
when it takes over: case F's swap, or `hold vectors move <c> --to hot`.

THE MECHANISM: A SYMBOLIC LINK. Every reader and writer opens the one path
`table_dir(root, name)` = `<root>/<name>/lance`, as it always has; a table in
the build area is that path made a link to `<LOUISE_BUILD_DIR>/lance/<name>`
— one mount per kind, a folder per collection, the pattern of SHARDS_ROOT,
ORIGINS_ROOT and BUNDLES_ROOT (07 §7.8), which `rename` and `forget` follow.
Chosen over a placement recorded in the collection's state because:

- the path every process opens, and every cache keys on, never changes:
  `Collection.lance_dir`, the LanceDB connections (`embeddings._dbs`), the
  index state (`vector_index`), the writer registrations — keyed on
  `str(lance_dir)` — need nothing re-read when a table moves, in this
  process or in the web's;
- LanceDB keeps the path it was given and opens its files through it at
  each use (MEASURED, lancedb 0.40.0 / pylance 9.0.1: a connection and a
  table opened before a link was switched to a copy and the original
  deleted answered the same rows and the same nearest neighbours after it,
  and a write after it landed behind the new link);
- the locks are not under `lance/` (`state/lock`, the manifest's vector
  write lock), so a move does not move them;
- unset, nothing is a link and every path is today's, byte for byte.

A CACHE KEYED ON THE PATH, ACROSS A MOVE: its key is the same before and
after, and what it holds — a connection, a table at a version, whether an
index exists — describes bytes that are the same before and after (the copy
is proven by a tree digest and its rows before the switch), so it stays
right. A reader holding a file open keeps reading the old inode until it
lets go; the source's bytes are freed then (a web restart frees them at
once).

THE MOVE (`move`): under the collection's write lock, the collection paused
(the worker's writers stopped, as for every other table swap), a COPY into
a staging name on the destination — a file a unit, each hashed as it is
read and recorded in a journal after it is in place, so a stop resumes at
the next file and nothing is read twice — then a VERIFICATION (every
destination file read back and hashed: the tree digest of
`recreate.tree_digest`, both sides, and the rows Lance counts), then, under
the vector write lock, the source re-checked against the journal and an
ATOMIC SWITCH, then the source removed. The switch is one
`renameat2(RENAME_EXCHANGE)`, the Linux kernel's atomic exchange of two
names — a link and a directory included (Linux ≥ 3.15; OpenZFS implements
it on Linux) — so `lance` names the old table or the new, never neither;
where a filesystem refuses it, three renames, and a stop between them is
finished by the next run (`settle`). Every step is resumable; a stop leaves
one valid table under the name.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import config
from .hold.codex import CodexError

#: the vector table's directory under a collection (07 §7.8's `lance/`)
TABLE_DIR = "lance"
#: the build area's kind folder: `<LOUISE_BUILD_DIR>/lance/<name>`
KIND = "lance"
HOT, BUILD = "hot", "build"
TIERS = (HOT, BUILD)
#: beside `lance`: a move's staging copy into the data pool, its new link
#: into the build area, and, after the switch, what the switch let go of
MOVING = "lance.moving"
#: `place_new`'s link, before it takes the name
PLACE = "lance.place"
#: the three-rename exchange's middle name: `lance.xchg-<the other name>`
XCHG = "lance.xchg-"
#: a move's record and its per-file journal, in the collection's state/
STATE = "vectors-move.json"
JOURNAL = "vectors-move.journal"
#: a file being copied, beside where it lands
PART = ".vmove-part"
BLOCK = 8 << 20
#: copy and verify rounds before a source that keeps changing is refused
ROUNDS = 3

#: MiB/s MEASURED on the node, for the dry run's estimate (storage-tiering.md
#: §8): ssd4 (the data pool) — sequential read 1,401–1,609 (fio 1d), a 355 GiB
#: table copy at ~11 GiB/min (2026-10-31); ssd3 (the build area) — sequential
#: read 460–473 (1d, SATA's ceiling), sustained write 158 after an 18 GiB
#: cache written at 287 (1e). Where the node's pools are others, these are an
#: estimate of nothing; the move reports what it took.
RATES: dict[str, dict[str, float]] = {
    HOT: {"read": 1400.0, "write": 187.0},
    BUILD: {"read": 465.0, "write": 158.0, "cache_write": 287.0, "cache_gib": 18.0},
}
MIB = 1 << 20
GIB = 1 << 30

#: a test's stand-in for a kill: called with each step's name
_hook: Callable[[str], None] | None = None


def _at(step: str) -> None:
    if _hook is not None:
        _hook(step)


class PlaceError(CodexError):
    """A placement or a move refused before anything was done (07 §7.7, exit 2)."""
    exit_code = 2


class MoveMismatch(CodexError):
    """The copy is not the source's, byte for byte or by rows: nothing was
    switched, the table stays where it was (exit 1)."""
    exit_code = 1


# ---- where -------------------------------------------------------------------

def area() -> Path | None:
    """`LOUISE_BUILD_DIR`, or None when this node names no build area."""
    return Path(config.BUILD_DIR) if config.BUILD_DIR is not None else None


def table_dir(root, name: str) -> Path:
    """THE PATH OF A COLLECTION'S VECTOR TABLE, `<root>/<name>/lance`: what
    every reader and writer opens, wherever the bytes are (a link into the
    build area, or the directory itself). Every place that names a
    collection's table asks here."""
    return Path(root) / name / TABLE_DIR


def physical(path) -> Path:
    """The directory behind `path`: the link's target, or `path` itself."""
    p = Path(path)
    if not p.is_symlink():
        return p
    t = Path(os.readlink(p))
    return t if t.is_absolute() else Path(os.path.normpath(p.parent / t))


def tier(path) -> str:
    """`build` when the table is behind a link (the build area), else `hot`."""
    return BUILD if Path(path).is_symlink() else HOT


def home(name: str) -> Path | None:
    """`<LOUISE_BUILD_DIR>/lance/<name>`, or None without a build area."""
    a = area()
    return a / KIND / name if a is not None else None


def applies(root) -> bool:
    """Whether `new` places a collection under `root` in the build area: a
    build area is named, and `root` is the node's own (`<LOUISE_ROOT>/
    collections`) — a `--root` elsewhere is untouched, as the shared roots'
    links are (app/hold/collection.py `link_shared_roots`)."""
    if config.BUILD_DIR is None:
        return False
    try:
        return Path(root).resolve() == (config.louise_root() / "collections").resolve()
    except OSError:
        return False


def in_area(path) -> bool:
    """`path` is a table folder of the build area, `<LOUISE_BUILD_DIR>/lance/<x>`."""
    a = area()
    return a is not None and os.path.dirname(os.path.normpath(path)) == os.path.normpath(a / KIND)


def mounted() -> Path:
    """The build area, refused when none is named or it is not a directory:
    inside a container an unmounted path is the container's own storage, and
    nothing is built there (EXPORT_DIR's rule, 07 §7.8)."""
    a = area()
    if a is None:
        raise PlaceError("LOUISE_BUILD_DIR is not set: this node names no build area (07 §7.8)")
    if not a.is_dir():
        raise PlaceError(f"LOUISE_BUILD_DIR {a} is not a directory — is its storage mounted? Nothing "
                         f"is built in a container's own storage (07 §7.8)")
    return a


def unreachable(path) -> str | None:
    """Why the table at `path` cannot be reached, or None: a link whose
    target is not there — the build area not mounted, or its folder gone."""
    p = Path(path)
    if p.is_symlink() and not p.exists():
        return (f"its vector table is in the build area at {physical(p)}, which is not there — is "
                f"LOUISE_BUILD_DIR's storage mounted? (07 §7.8)")
    return None


def ensure(path) -> None:
    """The table's directory made where it is missing (`db.connect`): for a
    table in the build area, refused when the area is not there — a link
    whose target is gone is never papered over with a new empty table."""
    why = unreachable(path)
    if why:
        raise PlaceError(f"{path}: {why}")
    Path(path).mkdir(parents=True, exist_ok=True)


def destination_for_new(root, name: str) -> Path:
    """Where a collection `new` is about to make will hold its table's bytes
    (the dry run's room): the build area when it applies, else beside it."""
    return home(name) if applies(root) else table_dir(root, name)  # type: ignore[return-value]


# ---- the atomic exchange -----------------------------------------------------

_AT_FDCWD = -100
_RENAME_EXCHANGE = 2
_UNSUPPORTED = {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}


def _renameat2_exchange(a: Path, b: Path) -> bool:
    """`renameat2(a, b, RENAME_EXCHANGE)`; False where the C library or the
    filesystem does not offer it."""
    try:
        fn = ctypes.CDLL(None, use_errno=True).renameat2
    except (OSError, AttributeError):
        return False
    fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    fn.restype = ctypes.c_int
    if fn(_AT_FDCWD, os.fsencode(str(a)), _AT_FDCWD, os.fsencode(str(b)), _RENAME_EXCHANGE) == 0:
        return True
    err = ctypes.get_errno()
    if err in _UNSUPPORTED:
        return False
    raise OSError(err, os.strerror(err), str(a), None, str(b))


def exchange(a, b) -> str:
    """`a` and `b`, siblings, swap names: atomically where the kernel and the
    filesystem can (`exchanged`), else by three renames through
    `lance.xchg-<a's name>` (`renamed`), which `settle` finishes after a
    stop. THE STANDARD: renameat2(2)'s RENAME_EXCHANGE."""
    a, b = Path(a), Path(b)
    if _renameat2_exchange(a, b):
        return "exchanged"
    mid = b.with_name(XCHG + a.name)
    os.rename(b, mid)
    _at("exchange-1")
    os.rename(a, b)
    _at("exchange-2")
    os.rename(mid, a)
    return "renamed"


def settle(base) -> bool:
    """A three-rename exchange a stop interrupted, finished: True when one was."""
    base = Path(base)
    if not base.is_dir():
        return False
    done = False
    for mid in sorted(base.glob(XCHG + "*")):
        a, b = base / mid.name[len(XCHG):], base / TABLE_DIR
        if _empty_dir(b) and not _gone(a):
            b.rmdir()                       # made meanwhile by a reader (db.connect): not a table
        if _gone(b):
            os.rename(a, b)
        os.rename(mid, a)
        done = True
    return done


# ---- a new collection's table in the build area --------------------------------

def _gone(p: Path) -> bool:
    return not (p.exists() or p.is_symlink())


def _empty_dir(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink() and not any(p.iterdir())


def place_new(root, name: str) -> Path | None:
    """`new` (cases A and F): the collection's table built in the build area —
    `<root>/<name>/lance` a link to `<LOUISE_BUILD_DIR>/lance/<name>`, made
    BEFORE the collection's file exists, so no writer can have begun a table
    elsewhere. Returns the folder, or None where no build area applies.
    Repeatable: the link already made is kept. Refused (nothing made) when
    the area is not mounted, or when either side already holds a table."""
    if not applies(root):
        return None
    a = mounted()
    link, target = table_dir(root, name), a / KIND / name
    settle(link.parent)
    if link.is_symlink():
        if physical(link) != target:
            raise PlaceError(f"{link} links to {physical(link)}, not {target}: nothing is placed over it")
        target.mkdir(parents=True, exist_ok=True)
        return target
    if link.exists() and not _empty_dir(link):
        raise PlaceError(f"{link} already holds a vector table: a new collection's table is placed "
                         f"before it holds one")
    if (target.exists() or target.is_symlink()) and not _empty_dir(target):
        raise PlaceError(f"{target} already holds files — the table of a collection of this name that "
                         f"was not forgotten? Nothing is placed over it: remove it, then run this again")
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(PLACE)
    if tmp.is_symlink():
        tmp.unlink()
    elif _empty_dir(tmp):
        tmp.rmdir()
    tmp.symlink_to(target, target_is_directory=True)
    if link.is_dir():
        # an empty directory a reader made (db.connect): exchanged out, then removed
        exchange(tmp, link)
        try:
            tmp.rmdir()
        except OSError:
            exchange(tmp, link)                     # it holds something now: put back, refuse
            tmp.unlink()
            raise PlaceError(f"{link} was written while it was placed: it stays where it is") from None
    else:
        os.replace(tmp, link)
    return target


def unplace_new(root, name: str) -> None:
    """`place_new` undone when the collection was not made after all: the
    link and its folder go while the folder is empty, and the collection's
    directory if nothing else is in it."""
    link = table_dir(root, name)
    if not link.is_symlink() or physical(link) != home(name):
        return
    target = physical(link)
    if target.is_dir() and any(target.iterdir()):
        return
    link.unlink()
    if target.is_dir():
        target.rmdir()
    try:
        link.parent.rmdir()
    except OSError:
        pass


# ---- what a forget or a rename follows ------------------------------------------

def _linked_by_others(root, name: str, target: Path) -> list[str]:
    out = []
    root = Path(root)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.name == name or not d.is_dir():
            continue
        link = d / TABLE_DIR
        if link.is_symlink() and os.path.normpath(physical(link)) == os.path.normpath(target):
            out.append(d.name)
    return out


def held_in_area(root, name: str) -> list[Path]:
    """The build area's folders that are this collection's and go with it: the
    target of its table's link and of every other link beside it (a move's,
    a vectors forget's), and `<area>/lance/<name>` (a move's staging) — any
    that another collection's table links is left."""
    base = Path(root) / name
    out: list[Path] = []
    links = [base / TABLE_DIR, base / MOVING, base / PLACE, *sorted(base.glob("lance.*"))]
    for link in links:
        if link.is_symlink():
            t = physical(link)
            if in_area(t) and t not in out:
                out.append(t)
    h = home(name)
    if h is not None and (h.exists() or h.is_symlink()) and h not in out:
        out.append(h)
    return [t for t in out if not _linked_by_others(root, name, t)]


def remove_table(path) -> None:
    """A table at `path` removed: the directory, or the link and the build
    area's folder behind it (the folder first, so a stop leaves the link to
    remove it again)."""
    p = Path(path)
    if p.is_symlink():
        t = physical(p)
        if t.is_dir() and not t.is_symlink():
            shutil.rmtree(t)
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def begun(state_dir) -> dict | None:
    """The move begun for this collection, as its record says, or None."""
    return _load(Path(state_dir) / STATE)


def discard_move(root, name: str) -> list[str]:
    """A begun move dropped with the table it was moving (`forget --kinds
    vectors`): its staging, its links, its record. Returns what went."""
    base = Path(root) / name
    gone: list[str] = []
    state = begun(base / "state")
    for p in (base / MOVING, base / PLACE):
        if not _gone(p):
            remove_table(p)
            gone.append(str(p))
    h = home(name)
    if state and state.get("to") == BUILD and h is not None and not _gone(h):
        if not (table_dir(root, name).is_symlink() and physical(table_dir(root, name)) == h):
            remove_table(h)
            gone.append(str(h))
    for f in (STATE, JOURNAL):
        (base / "state" / f).unlink(missing_ok=True)
    return gone


# ---- the move ---------------------------------------------------------------------

def _load(path: Path) -> dict | None:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _listing(top: Path) -> tuple[dict[str, tuple[int, int]], dict[str, str], list[str]]:
    """`({rel: (size, mtime_ns)}, {rel: link target}, [dirs])` under `top`,
    the top followed, nothing below it; the copy's own partial files left out."""
    files: dict[str, tuple[int, int]] = {}
    links: dict[str, str] = {}
    dirs: list[str] = []
    top = Path(top)
    if not top.is_dir():
        return files, links, dirs
    for base, ds, names in os.walk(top):
        here = Path(base)
        for d in list(ds):
            rel = (here / d).relative_to(top).as_posix()
            if (here / d).is_symlink():
                links[rel] = os.readlink(here / d)
                ds.remove(d)
            else:
                dirs.append(rel)
        for nm in names:
            if nm.endswith(PART):
                continue
            p = here / nm
            rel = p.relative_to(top).as_posix()
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                links[rel] = os.readlink(p)
            elif stat.S_ISREG(st.st_mode):
                files[rel] = (st.st_size, st.st_mtime_ns)
    return files, links, sorted(dirs)


def _read_journal(path: Path) -> dict[str, tuple[int, int, str]]:
    out: dict[str, tuple[int, int, str]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
            out[r["rel"]] = (int(r["size"]), int(r["mtime_ns"]), str(r["sha256"]))
        except (ValueError, KeyError, TypeError):
            continue                                # a line a kill cut short: that file again
    return out


def _write_journal(path: Path, entries: dict[str, tuple[int, int, str]]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps({"rel": r, "size": s, "mtime_ns": m, "sha256": h}, sort_keys=True) + "\n"
                           for r, (s, m, h) in sorted(entries.items())), encoding="utf-8")
    os.replace(tmp, path)


def _copy_one(src: Path, dst: Path) -> tuple[int, int, str]:
    """`src` copied to `dst` through `<dst>.vmove-part`, hashed as it is read,
    its times and mode kept (`shutil.copystat`); `(size, mtime_ns, sha256)`
    of what was read."""
    st = os.stat(src)
    tmp = dst.with_name(dst.name + PART)
    h = hashlib.sha256()
    n = 0
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        while True:
            block = fi.read(BLOCK)
            if not block:
                break
            h.update(block)
            fo.write(block)
            n += len(block)
    shutil.copystat(src, tmp)
    os.replace(tmp, dst)
    return n, st.st_mtime_ns, h.hexdigest()


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(BLOCK), b""):
            h.update(block)
    return h.hexdigest()


def digest(entries: dict[str, tuple[int, str]], links: dict[str, str]) -> dict[str, Any]:
    """`recreate.tree_digest`'s answer from per-file `(size, sha256)`: the
    sha256 of the sorted lines `<rel> <size> <sha256>` and `<rel> -> <target>`."""
    lines = sorted([f"{rel} {size} {h}" for rel, (size, h) in entries.items()]
                   + [f"{rel} -> {t}" for rel, t in links.items()])
    return {"files": len(entries), "bytes": int(sum(s for s, _ in entries.values())),
            "sha256": "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()}


def _threads(threads: int | None) -> int:
    from . import parallel
    return threads or max(4, parallel.processes())


def _free(path: Path) -> tuple[int, int]:
    """`(free, floor)` on the filesystem that holds (or will hold) `path`."""
    from . import disk_guard
    where = Path(path)
    while not where.exists() and where != where.parent:
        where = where.parent
    u = shutil.disk_usage(where)
    return u.free, disk_guard.floor_bytes(u.total)


def estimate(nbytes: int, src: str, dst: str) -> dict[str, Any]:
    """Seconds to copy `nbytes` from tier `src` to tier `dst` and read them
    back, at the RATES measured: the copy at the slower of the source's
    read and the destination's write (the build area's first 18 GiB at its
    cache's speed), the verification at the destination's read. The source
    is hashed as it is copied, never read twice."""
    gib = nbytes / GIB
    read = RATES[src]["read"]
    w = RATES[dst]
    if dst == BUILD:
        cached = min(gib, w["cache_gib"])
        copy_s = cached * 1024 / min(read, w["cache_write"]) + (gib - cached) * 1024 / min(read, w["write"])
    else:
        copy_s = gib * 1024 / min(read, w["write"])
    verify_s = gib * 1024 / RATES[dst]["read"]
    return {"copy_s": round(copy_s), "verify_s": round(verify_s), "total_s": round(copy_s + verify_s),
            "rates_mib_s": {"read": read, "write": w["write"], "verify_read": RATES[dst]["read"],
                            **({"first_gib_write": w["cache_write"], "first_gib": w["cache_gib"]}
                               if dst == BUILD else {})},
            "measured": "docs/notes/storage-tiering.md §8 (2026-11-07)"}


def _human(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, size in (("TiB", 1 << 40), ("GiB", GIB), ("MiB", MIB), ("KiB", 1 << 10)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def _minutes(s: float) -> str:
    return f"{s / 60:.0f} min" if s >= 90 else f"{s:.0f} s"


def _where(root, name: str, to: str) -> tuple[Path, Path, Path]:
    """`(link, source directory, staging)` of a move of `name`'s table to `to`.
    Into the build area the staging IS its folder, `<area>/lance/<name>` (a
    rename into it would cross nothing); into the data pool it is
    `<name>/lance.moving`, beside the name it takes."""
    link = table_dir(root, name)
    src = physical(link)
    if to == BUILD:
        stage = mounted() / KIND / name
        if not applies(root):
            raise PlaceError(f"{root} is not this node's root ({config.louise_root() / 'collections'}): "
                             f"the build area holds the node's own collections, a folder per name")
    else:
        stage = link.with_name(MOVING)
    return link, src, stage


def plan(root, name: str, to: str) -> dict[str, Any]:
    """What `move` would do, reading only: the bytes, the room on the
    destination, the estimated time at the measured rates, and the bytes the
    source's removal frees."""
    if to not in TIERS:
        raise PlaceError(f"--to is {' or '.join(TIERS)}, got {to!r}")
    base = Path(root) / name
    link = table_dir(root, name)
    state = begun(base / "state")
    now = tier(link)
    out: dict[str, Any] = {"collection": name, "dry_run": True, "begun": state}
    if state and state.get("to") != to:
        raise PlaceError(f"{name}: a move to {state.get('to')} is begun: finish it "
                         f"(`louise hold vectors move {name} --to {state.get('to')} --apply`) first")
    if now == to and not state:
        out.update(nothing="the table is already there", tier=now, path=str(physical(link)))
        return out
    if now == to:
        old = base / MOVING
        files, _l, _d = _listing(physical(old)) if not _gone(old) else ({}, {}, [])
        out.update(switched=True, tier=now, path=str(physical(link)),
                   frees=sum(s for s, _ in files.values()),
                   next=f"louise hold vectors move {name} --to {to} --apply: removes the source")
        return out
    why = unreachable(link)
    if why:
        raise PlaceError(f"{name}: {why}")
    if not link.is_dir():
        raise PlaceError(f"{name} holds no vector table at {link}: nothing to move")
    _link, src, stage = _where(root, name, to)
    files, links, _dirs = _listing(src)
    total = sum(s for s, _ in files.values())
    journal = _read_journal(base / "state" / JOURNAL) if state else {}
    done = {r for r, (s, m, _h) in journal.items() if files.get(r) == (s, m)}
    left = total - sum(files[r][0] for r in done)
    free, floor = _free(stage)
    t = estimate(left, now, to)
    out.update({
        "from": {"tier": now, "path": str(src)}, "to": {"tier": to, "path": str(stage if to == BUILD else link)},
        "files": len(files), "links": len(links), "bytes": total, "to_copy": left,
        "room": {"free": free, "floor": floor, "enough": free - left >= floor},
        "estimate": t, "frees": {"bytes": total, "on": now, "path": str(src)},
        "next": f"louise node pause {name}; louise hold vectors move {name} --to {to} --apply",
    })
    if not out["room"]["enough"]:
        out["refusal"] = no_room(name, to, left, free, floor, stage)
    out["say"] = (f"{name}: {_human(total)} in {len(files)} file(s) from {now} ({src}) to {to}"
                  + (f", {_human(left)} still to copy" if left != total else "")
                  + f"; {_human(free)} free there, floor {_human(floor)}: "
                  + ("enough" if out["room"]["enough"] else "SHORT")
                  + f"; about {_minutes(t['total_s'])} ({_minutes(t['copy_s'])} copying, "
                  + f"{_minutes(t['verify_s'])} verifying) at the measured rates; the source's removal "
                  + f"frees {_human(total)} on {now}")
    return out


def no_room(name: str, to: str, need: int, free: int, floor: int, where: Path) -> str:
    return (f"{name}: moving its vector table to {to} needs {_human(need)} and {_human(free)} is free at "
            f"{where}, which must keep {_human(floor)} free (the vectors' floor, app/disk_guard.py): "
            f"nothing was copied. Free the space, then run this again")


def move(root, name: str, to: str, *, collection=None, stop: threading.Event | None = None,
         log: Callable[[str], None] | None = None, threads: int | None = None) -> dict[str, Any]:
    """`name`'s vector table moved to tier `to`, from wherever a stopped run
    left it: copy, verify, switch, remove (the module's docstring). The
    caller holds the collection's write lock with the collection paused;
    `collection` (its local Collection) gives the manifest whose vector write
    lock the switch takes and the caches this process drops. Returns what it
    did; `{"interrupted": True}` when `stop` was set between two files."""
    say = log or (lambda _m: None)
    if to not in TIERS:
        raise PlaceError(f"--to is {' or '.join(TIERS)}, got {to!r}")
    t0 = time.monotonic()
    base = Path(root) / name
    state_dir = base / "state"
    settle(base)
    link = table_dir(root, name)
    state = begun(state_dir)
    if state and state.get("to") != to:
        raise PlaceError(f"{name}: a move to {state.get('to')} is begun: finish it "
                         f"(`louise hold vectors move {name} --to {state.get('to')} --apply`) first")
    if tier(link) == to:
        if not state:
            return {"collection": name, "nothing": "the table is already there", "tier": to,
                    "path": str(physical(link))}
        return _finish(root, name, to, state, say, t0)
    why = unreachable(link)
    if why:
        raise PlaceError(f"{name}: {why}")
    if not link.is_dir():
        raise PlaceError(f"{name} holds no vector table at {link}: nothing to move")
    link, src, stage = _where(root, name, to)
    if not state:
        if not _gone(base / MOVING):
            raise PlaceError(f"{base / MOVING} is there and no move is recorded: remove it, then run this again")
        if not _gone(stage) and not _empty_dir(stage):
            raise PlaceError(f"{stage} already holds files and no move of {name} is recorded: remove it, "
                             f"then run this again")
    # the room, before a byte is copied: what is still to copy, against what
    # the destination may give and keep its vectors' floor
    files, _links, _dirs = _listing(src)
    journal = _read_journal(state_dir / JOURNAL) if state else {}
    left = sum(s for r, (s, m) in files.items() if journal.get(r, (None, None, None))[:2] != (s, m))
    free, floor = _free(stage)
    if free - left < floor:
        raise PlaceError(no_room(name, to, left, free, floor, stage))
    if not state:
        state = {"to": to, "from": tier(link), "began": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "stage": "copy"}
        _save(state_dir / STATE, state)
    jpath = state_dir / JOURNAL
    for rnd in range(ROUNDS):
        copied = _copy(src, stage, jpath, name=name, stop=stop, say=say, threads=threads)
        if copied.get("interrupted"):
            return {"collection": name, "interrupted": True, **copied}
        if stop is not None and stop.is_set():
            return {"collection": name, "interrupted": True, "stage": "copy"}
        checked = _verify(src, stage, jpath, stop=stop, threads=threads)
        if checked.get("interrupted"):
            return {"collection": name, "interrupted": True, "stage": "verify"}
        if checked["bad"]:
            say(f"[vectors move] {name}: {len(checked['bad'])} file(s) differ from the source after the copy "
                f"(round {rnd + 1}): copied again")
            continue
        _at("verified")
        _sync(stage)
        switched = _switch(root, name, to, link, src, stage, jpath, collection)
        if switched is None:
            say(f"[vectors move] {name}: the source changed since it was copied: copied again")
            continue
        state = {**state, "stage": "switched", "switch": switched, "digest": checked["digest"],
                 "rows": checked["rows"]}
        _save(state_dir / STATE, state)
        _at("recorded")
        return _finish(root, name, to, state, say, t0, copied=copied)
    raise MoveMismatch(f"{name}: after {ROUNDS} rounds the copy still differs from the source (another "
                       f"writer?): nothing was switched; the table stays at {src}. Pause the collection "
                       f"and run this again")


def _copy(src: Path, stage: Path, jpath: Path, *, name: str, stop, say, threads) -> dict[str, Any]:
    """Every file of `src` not yet in `stage` as the journal records it,
    copied (a file a unit, in threads); what `stage` holds that `src` does
    not, removed."""
    files, links, dirs = _listing(src)
    journal = _read_journal(jpath)
    have, have_links, _hd = _listing(stage)
    todo = sorted(r for r, (size, mtime) in files.items()
                  if journal.get(r, (None, None, None))[:2] != (size, mtime) or have.get(r, (None,))[0] != size)
    # what a killed copy left half-written, and what the source no longer holds
    if stage.is_dir():
        for p in stage.rglob("*" + PART):
            p.unlink(missing_ok=True)
    for r in set(have) - set(files):
        (stage / r).unlink(missing_ok=True)
    for r in set(have_links) - set(links):
        (stage / r).unlink(missing_ok=True)
    stale = set(journal) - set(files)
    if stale or any(r in journal for r in todo):
        _write_journal(jpath, {r: e for r, e in journal.items() if r in files and r not in todo})
    stage.mkdir(parents=True, exist_ok=True)
    shutil.copystat(src, stage)
    for d in dirs:
        (stage / d).mkdir(parents=True, exist_ok=True)
    for r, t in links.items():
        p = stage / r
        if p.is_symlink() and os.readlink(p) == t:
            continue
        if p.is_symlink() or p.exists():
            p.unlink()
        p.symlink_to(t)
    need = sum(files[r][0] for r in todo)
    total = sum(s for s, _ in files.values())
    if todo:
        say(f"[vectors move] {name}: copying {len(todo)} of {len(files)} file(s), {_human(need)} of "
            f"{_human(total)}, to {stage}")
    lock = threading.Lock()
    done = {"files": 0, "bytes": 0}
    t0 = time.monotonic()
    last = [t0]
    with open(jpath, "a", encoding="utf-8") as jf:
        def one(rel: str) -> None:
            if stop is not None and stop.is_set():
                return
            _at("copying")
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            size, mtime, sha = _copy_one(src / rel, dst)
            with lock:
                jf.write(json.dumps({"rel": rel, "size": size, "mtime_ns": mtime, "sha256": sha},
                                    sort_keys=True) + "\n")
                jf.flush()
                done["files"] += 1
                done["bytes"] += size
                now = time.monotonic()
                if now - last[0] >= 60:
                    last[0] = now
                    rate = done["bytes"] / max(now - t0, 1e-9) / MIB
                    say(f"[vectors move] {name}: {_human(done['bytes'])} of {_human(need)} copied, "
                        f"{rate:.0f} MiB/s")
            _at("copied")
        ex = ThreadPoolExecutor(max_workers=_threads(threads))
        try:
            for f in [ex.submit(one, r) for r in todo]:
                f.result()
        except BaseException:
            ex.shutdown(wait=True, cancel_futures=True)     # what is queued never starts
            raise
        ex.shutdown(wait=True)
    took = time.monotonic() - t0
    out = {"files": len(files), "bytes": total, "copied_files": done["files"], "copied_bytes": done["bytes"],
           "copy_s": round(took, 1)}
    if stop is not None and stop.is_set() and done["files"] < len(todo):
        out["interrupted"] = True
        out["stage"] = "copy"
    return out


def _verify(src: Path, stage: Path, jpath: Path, *, stop, threads) -> dict[str, Any]:
    """Every staged file read back and hashed against the source's hash the
    journal holds (the tree digest of both sides), and the rows Lance counts
    on both. A file that differs leaves the journal and is copied again."""
    from . import spaces
    files, links, _d = _listing(src)
    have, have_links, _hd = _listing(stage)
    journal = _read_journal(jpath)
    names = sorted(have)
    cancelled = threading.Event()

    def one(rel: str) -> str | None:
        if stop is not None and stop.is_set():
            cancelled.set()
            return None
        return _sha(stage / rel)
    with ThreadPoolExecutor(max_workers=_threads(threads)) as ex:
        sums = dict(zip(names, ex.map(one, names)))
    if cancelled.is_set():
        return {"interrupted": True}
    bad = sorted({r for r in files if r not in journal or journal[r][:2] != files[r]
                  or sums.get(r) != journal[r][2] or have.get(r, (None,))[0] != files[r][0]}
                 | (set(have) - set(files)))
    if bad:
        _write_journal(jpath, {r: e for r, e in journal.items() if r not in bad})
        return {"bad": bad}
    if links != have_links:
        raise MoveMismatch(f"the links under {stage} are not the source's: nothing was switched")
    a = digest({r: (journal[r][0], journal[r][2]) for r in files}, links)
    b = digest({r: (have[r][0], sums[r]) for r in have}, have_links)
    rows = {"source": spaces.known_lance_rows(src), "copy": spaces.known_lance_rows(stage)}
    if a != b or rows["source"] != rows["copy"] or rows["source"] is None:
        raise MoveMismatch(f"the copy at {stage} is not the source's ({a['sha256']} {rows['source']} rows "
                           f"against {b['sha256']} {rows['copy']} rows): nothing was switched; the table "
                           f"stays at {src}")
    return {"bad": [], "digest": b, "rows": rows["copy"]}


def _sync(path: Path) -> None:
    """The copy on the disk before the switch: `syncfs` of its filesystem
    (the C library's), else `sync`."""
    try:
        fn = ctypes.CDLL(None, use_errno=True).syncfs
        fd = os.open(path, os.O_RDONLY)
        try:
            if fn(fd) == 0:
                return
        finally:
            os.close(fd)
    except (OSError, AttributeError):
        pass
    os.sync()


def _switch(root, name: str, to: str, link: Path, src: Path, stage: Path, jpath: Path,
            collection) -> str | None:
    """Under the vector write lock: the source re-read as stat against the
    journal (a write since the copy → None: copy again), then the name
    switched in one exchange. Returns how ('exchanged' or 'renamed')."""
    from contextlib import nullcontext
    con = None
    lock = nullcontext()
    if collection is not None:
        from . import db, embeddings
        con = db.connect(collection)
        lock = embeddings._write_lock(con, collection)
    try:
        with lock:
            files, links, _d = _listing(src)
            journal = _read_journal(jpath)
            if {r: e[:2] for r, e in journal.items()} != files:
                return None
            new = stage
            if to == BUILD:
                new = link.with_name(MOVING)
                if new.is_symlink() and physical(new) != stage:
                    new.unlink()
                if not new.is_symlink():
                    new.symlink_to(stage, target_is_directory=True)
                _at("linked")
            how = exchange(new, link)
            _at("exchanged")
    finally:
        if con is not None:
            con.close()
    if collection is not None:
        from . import embeddings, vector_index
        embeddings.forget(collection)
        vector_index.forget(collection)
    return how


def _finish(root, name: str, to: str, state: dict, say, t0: float, copied: dict | None = None) -> dict[str, Any]:
    """After the switch: the source removed — the old directory, or the old
    link and the build area's folder behind it — and the record cleared."""
    base = Path(root) / name
    old = base / MOVING
    freed = 0
    where = None
    if not _gone(old):
        where = str(physical(old))
        files, _l, _d = _listing(physical(old))
        freed = sum(s for s, _ in files.values())
        remove_table(old)
        _at("removed")
    for f in (STATE, JOURNAL):
        (base / "state" / f).unlink(missing_ok=True)
    link = table_dir(root, name)
    out = {"collection": name, "moved": True, "tier": to, "path": str(physical(link)),
           "digest": state.get("digest"), "rows": state.get("rows"), "switch": state.get("switch"),
           "freed": freed, "freed_at": where, "seconds": round(time.monotonic() - t0, 1)}
    if copied:
        out["copied"] = copied
    say(f"[vectors move] {name}: its vector table is in {to} now ({out['path']}), "
        f"{state.get('rows')} rows, {str((state.get('digest') or {}).get('sha256'))[:23]}… both sides; "
        f"the source removed, {_human(freed)} freed" + (f" at {where}" if where else ""))
    return out
