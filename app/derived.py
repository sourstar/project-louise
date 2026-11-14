"""app/derived.py — where DERIVED data lives: the caches a plugin builds from
its origin and seeks in while it parses (pubmed's per-container pack,
wikipedia's page-props pack, wikidata's pack and label table), re-creatable
from the origin, never exchanged.

Operator, 2026-10-20: "a cache folder on a fast drive, like nvme or ssd, to
be used for such operations". Measured on the node (München): wikidata's
parse is ~95 % label lookups in labels.sqlite — 133–179 entities/s a core
with the table on the HDD pool while the scan writes it, 430 warmed in the
ARC, 1,763 from RAM. `LOUISE_CACHE_DIR` (config.CACHE_DIR, identity tier,
07 §7.8) names the fast drive; unset, every cache stays beside its origin,
as before, so a node that did not ask sees nothing move. A cache is keyed
on the ORIGIN'S PHYSICAL IDENTITY — its resolved path — never a name
(CLAUDE.md: two objects can share a name and point at different files).

TWO CACHES (operator, 2026-11-14: "You need 2 caches, one for fast mmap
operations (like vectors caching) and one for things like files caching (a
decompressed corpus archive to be parsed)"; storage-tiering.md §10). A kind
is placed by how it is READ: `FILES` — a decompressed archive a parse
streams through, a row group a record in the order it was written — under
`LOUISE_FILES_CACHE_DIR` (config.FILES_CACHE_DIR: ssd3 on the node) when
the node names one; every other kind — memory-mapped, or read at random by a
key: label and facts tables, the claim index, the part caches, the speech
clips — under `LOUISE_CACHE_DIR` (ssd5). The tag does not depend on the
root, so a cache moves between the two as bytes: the same `<kind>/<tag>`
under the other root.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app import config


#: the directory BESIDE an origin that holds what is derived from it when no
#: fast drive is named — one name for every template (docs/notes/
#: collection-backend.md); an origin walk never enters it (app/plugins/walk.py)
BESIDE = ".pack"


def tag(origin: Path) -> str:
    """16 hex of the origin's resolved path — the key page_props has used
    for its temporary pack since 2026-09."""
    return key(Path(origin).resolve())


def resolved(path: Path) -> Path:
    """`path` resolved, or — gone — its parent resolved and its name kept."""
    path = Path(path)
    return path.resolve() if path.exists() else path.parent.resolve() / path.name


def key(resolved_path: Path) -> str:
    """`tag` of a path already resolved: a rename keys a whole walked tree
    from one resolve of its root (app/hold/rename.py)."""
    return hashlib.blake2b(str(resolved_path).encode("utf-8"), digest_size=8).hexdigest()


#: the kinds that are a DECOMPRESSED ARCHIVE A PARSE STREAMS THROUGH: the files
#: cache's (module docstring). Measured on ssd3 against ssd4 (storage-tiering.md
#: §8, 2b): 180 against 296 random row groups/s at the parse pool's width, above
#: what a records build reads (~4.1 k records/s, ~64 row groups/s on wikidata).
#: Wikidata's LABEL TABLE is read at random, ~15 lookups a document, and is its
#: own kind, `wikidata-labels`, on the fast cache (app/plugins/wikidata/pack.py
#: `labels_path`).
FILES = frozenset({"pubmed-pack", "legi-pack", "wikipedia-page-props", "wikidata-pack"})


def root(kind: str) -> Path | None:
    """The cache `kind` lives under: the files cache for a `FILES` kind when
    the node names one, else the fast one; None when neither applies."""
    if kind in FILES and config.FILES_CACHE_DIR is not None:
        return Path(config.FILES_CACHE_DIR)
    return Path(config.CACHE_DIR) if config.CACHE_DIR is not None else None


def apart(a: str, b: str) -> bool:
    """Kinds `a` and `b` live under two different named caches."""
    ra, rb = root(a), root(b)
    return ra is not None and rb is not None and ra != rb


def place(kind: str, origin: Path) -> Path | None:
    """`<root of kind>/<kind>/<tag of origin>/` — the directory for the
    caches derived from `origin` (a file or a folder) — or None when no
    cache is named for the kind. The directory is not created here."""
    base = root(kind)
    return None if base is None else base / kind / tag(origin)


#: (kind, origin as named, the fast drive) -> `place`'s answer
_places: dict[tuple[str, str, str], Path | None] = {}


def places_key() -> str:
    """What `place_kept`'s answers are keyed on besides the origin — the
    fast drive — for a caller that keeps its own answers per process
    (wikidata's `pack_dir`): both caches."""
    return f"{config.CACHE_DIR}|{config.FILES_CACHE_DIR}"


def place_kept(kind: str, origin: Path) -> Path | None:
    """`place`, kept per process: its `tag` resolves the origin through
    every link (a realpath), and a caller that asks once per document —
    wikidata's pack, ~75.9 M times in a records build, ~7 % of a child
    (München's py-spy, 2026-10-30) — pays it each time. An origin's place
    does not move under a running process; keyed on both caches too."""
    key = (kind, str(origin), places_key())
    if key not in _places:
        _places[key] = place(kind, origin)
    return _places[key]


def adopt(src: Path, dst: Path) -> bool:
    """An existing cache beside its origin, carried into the fast drive once:
    `src` (a file or a directory) copied to `dst` when `dst` does not exist
    — into a sibling first, renamed whole (invariant 2), sizes and times
    kept — and left where it was for the operator to delete. A stat when
    `dst` exists; False when nothing was copied. RULE 1: the pubmed pack of
    a 40 M-record corpus costs hours to rebuild and is identical either
    way. A directory's `*.tmp` files — a pass's uncommitted work — are not
    copied; a copy stopped half-way leaves only the sibling, which the next
    call removes and redoes: `dst` is whole or absent, never half (the
    node's wikidata pack is ~60 GB, minutes of copying — München)."""
    src, dst = Path(src), Path(dst)
    if dst.exists() or not src.exists():
        return False
    # ONE ADOPTER ACROSS PROCESSES: pubmed adopts from `ensure`, in every
    # parse-pool child, and two copies into one fixed temporary name could
    # rename a half-written pack into place (the 2026-10-30 inventory)
    with building(dst):
        if dst.exists():
            return False
        for stale in _named(dst.parent, f".{dst.name}.", "adopting"):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
            else:
                stale.unlink(missing_ok=True)
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.adopting")
        if src.is_dir():
            shutil.copytree(src, tmp, copy_function=shutil.copy2, ignore=shutil.ignore_patterns("*.tmp", "*.lock"))
        else:
            shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    return True


def _named(parent: Path, prefix: str, suffix: str) -> list[Path]:
    """The entries of `parent` named `prefix` + anything + `suffix`: what
    `parent.glob(glob.escape(prefix) + "*" + suffix)` gives, without
    compiling a pattern per name. glob keeps each compiled pattern
    (`functools.lru_cache(maxsize=512)` on `glob._compile_pattern`), one per
    pack name here: ~750 B a pack in every process, the per-file growth
    tests/test_pubmed_updatefiles_memory.py measured (2026-11-12)."""
    try:
        with os.scandir(parent) as entries:
            return [Path(e.path) for e in entries
                    if len(e.name) >= len(prefix) + len(suffix)
                    and e.name.startswith(prefix) and e.name.endswith(suffix)]
    except FileNotFoundError:
        return []


@contextmanager
def building(dest: Path) -> Iterator[None]:
    """One builder of `dest` at a time, ACROSS PROCESSES: an exclusive
    `flock` on `<dest>.lock` beside it, held while the caller re-checks
    freshness and builds. A thread lock serialised one process only, and
    the records build's plan runs in the pool's children (#80): two of them
    built the same pubmed pack into the same temporary file, and a third
    read it truncated — two documents skipped by the plan, "recorded, never
    fatal" (München, 2026-10-30). The standard: an advisory lock on the
    file, then a temporary name of the writer's own (`<dest>.<pid>.tmp`);
    what a killed writer left there goes when the next one takes the lock.
    The kernel drops the lock with its holder; the empty lock file stays."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest.with_name(dest.name + ".lock"), "a+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            for stale in _named(dest.parent, dest.name + ".", "tmp"):
                stale.unlink(missing_ok=True)
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Busy(RuntimeError):
    """A derived structure another process (or thread) is writing: the
    caller defers — a scan says "resumes next scan", a build waits for its
    next run — and never errors a document (CLAUDE.md, deferral is not
    failure). `holder` is what the lock file says."""

    def __init__(self, path: Path, holder: dict):
        self.path, self.holder = Path(path), holder
        super().__init__(f"{self.path.parent.name} is being written by {holder.get('what') or 'another writer'} "
                         f"(pid {holder.get('pid', '?')}, since {holder.get('since', '?')}); this one waits for it")


@contextmanager
def writing(lock_file: Path, what: str) -> Iterator[None]:
    """THE ONE WRITER of a derived structure built by long passes (wikidata's
    pack: its build, its codes pass, its regroup), across processes and
    threads — an exclusive `flock`, NOT waited for: a pass of hours would
    hold a scan thread without a bound (CLAUDE.md), so a second writer gets
    `Busy` at once. The pattern of the collection's own lock
    (app/hold/lock.py): the holder's words in the file, dropped by the
    kernel with the holder."""
    lock_file = Path(lock_file)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_file, "a+b")              # never truncated by an opener that may lose the race
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.seek(0)
        raw = f.read()
        f.close()
        try:
            who = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            who = {}
        raise Busy(lock_file, who if isinstance(who, dict) else {}) from None
    try:
        f.seek(0)
        f.truncate(0)
        f.write(json.dumps({"what": what, "pid": os.getpid(),
                            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}).encode("utf-8"))
        f.flush()
        yield
    finally:
        try:
            f.seek(0)
            f.truncate(0)
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def held() -> list[Path]:
    """Every cache the two caches hold, `<root>/<kind>/<tag>`, sorted; none
    when no cache is named (a rename carries them, app/hold/rename.py). A
    kind is listed under its own root only: a FILES kind left under the fast
    cache after the files cache was named is a copy nothing reads, the
    operator's to delete, never carried. A dot-name is a copy in progress
    (`adopt`)."""
    found: set[Path] = set()
    for base in {Path(b) for b in (config.CACHE_DIR, config.FILES_CACHE_DIR) if b is not None}:
        if not base.is_dir():
            continue
        found.update(t for k in base.iterdir() if k.is_dir() and root(k.name) == base
                     for t in k.iterdir() if not t.name.startswith("."))
    return sorted(found)


# ---- the packs, upon need ------------------------------------------------------------------
#
# Operator, 2026-11-14: the source downloads stay on tank, and "don't permanently
# use the solid state caching for them, only upon need". A FILES kind's UNIT — a
# container pack file (pubmed's, legi's, page-props'), a wikidata pack directory
# with its label table — is stamped when it is used and goes once it has been
# idle for IDLE_S; its reader builds it again the next time it is needed. The
# standard: a time-to-live cache keyed on last use, the policy of
# systemd-tmpfiles' `Age=` and tmpreaper. Neither is used itself: both work file
# by file (a wikidata pack is ~1,300 files and its table, which go together) and
# read the access time, which the node's datasets turn off (atime=off).

#: a unit unused this long goes (seconds)
IDLE_S = 24 * 3600
#: how often a process restamps one unit: a stamp is a write
TOUCH_EVERY_S = 3600.0
#: how often the worker looks for idle units
EVICT_EVERY_S = 3600.0
#: a container pack's file name ends so (pubmed, legi, page-props)
PACK_FILE = ".pack.parquet"
#: a directory unit's stamp, and its writer lock (wikidata pack.py `WRITER`)
USED, DIR_LOCK = ".used", "writer.lock"
#: a directory unit a reader asked for while it was gone (wikidata pack.py `want`)
WANTED = ".wanted"
_touched: dict[str, float] = {}
_last_evict = 0.0


def _roots() -> list[Path]:
    return [Path(b) for b in {str(x) for x in (config.CACHE_DIR, config.FILES_CACHE_DIR,
                                               getattr(config, "PUBMED_PACK_DIR", None)) if x is not None}]


def cached(path: Path) -> bool:
    """`path` lies under a cache this node names (the fast, the files, the
    pubmed pack's): a pack beside its origin, on tank, is not one."""
    p = str(Path(path))
    return any(p == str(r) or p.startswith(str(r).rstrip("/") + "/") for r in _roots())


def stamp_of(unit: Path) -> Path:
    unit = Path(unit)
    return unit / USED if unit.suffix != ".parquet" else unit.with_name(f".{unit.name}{USED}")


def used(unit: Path, *, now: float | None = None) -> None:
    """`unit` used now: its stamp written at most once an hour a process,
    and only for a unit in a cache; never fails (a read-only mount, the web's,
    simply does not stamp)."""
    now = time.time() if now is None else now
    key = str(unit)
    if now - _touched.get(key, 0.0) < TOUCH_EVERY_S or not cached(Path(unit)):
        return
    _touched[key] = now
    s = stamp_of(Path(unit))
    try:
        s.touch()
        os.utime(s, (now, now))
    except OSError:
        pass


def last_used(unit: Path) -> float:
    """When `unit` was last used or written: its stamp, its own time, and
    for a directory its meta.json (a build in progress rewrites it)."""
    unit = Path(unit)
    times = []
    for p in (stamp_of(unit), unit, unit / "meta.json"):
        try:
            times.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(times, default=0.0)


def units() -> list[tuple[str, Path]]:
    """Every unit of a FILES kind the caches hold, as (kind, path): the
    packs of each kind under its root, and PUBMED_PACK_DIR's files."""
    out: list[tuple[str, Path]] = []

    def files_in(kind: str, d: Path) -> None:
        with contextlib.suppress(OSError):
            out.extend((kind, p) for p in sorted(d.iterdir())
                       if p.name.endswith(PACK_FILE) and not p.name.startswith(".") and p.is_file())

    for kind in sorted(FILES):
        base = root(kind)
        if base is None or not (base / kind).is_dir():
            continue
        for tag_dir in sorted((base / kind).iterdir()):
            if tag_dir.name.startswith(".") or not tag_dir.is_dir():
                continue
            if kind == "wikidata-pack":
                out.extend((kind, d) for d in sorted(tag_dir.iterdir())
                           if d.is_dir() and not d.name.startswith("."))
            else:
                files_in(kind, tag_dir)
    pp = getattr(config, "PUBMED_PACK_DIR", None)
    if pp is not None and Path(pp).is_dir():
        files_in("pubmed-pack", Path(pp))
    return out


def _size(unit: Path) -> int:
    if unit.is_file():
        return unit.stat().st_size
    return sum(p.stat().st_size for p in unit.rglob("*") if p.is_file())


def _companions(kind: str, unit: Path) -> list[Path]:
    """What goes with a unit: a wikidata pack's label table, on the fast
    cache when the two caches are apart (its tag is the pack's: both are
    keyed on the dump, app/plugins/wikidata/pack.py `labels_path`)."""
    if kind != "wikidata-pack" or not apart("wikidata-pack", "wikidata-labels"):
        return []
    d = root("wikidata-labels") / "wikidata-labels" / unit.parent.name
    return [d] if d.exists() else []


def evict_idle(idle_s: float = IDLE_S, *, now: float | None = None, dry_run: bool = False,
               log=None) -> list[dict]:
    """Every unit idle `idle_s` or more, removed — never one a writer holds
    (its lock is tried, not waited for) nor one a reader asked for
    (`WANTED`). A pack file goes under its builder's lock (`building`'s),
    a directory renamed whole first (a reader sees it whole or not at all)
    and its companions before it. Returns what went (or would go): kind,
    path, bytes, idle hours."""
    now = time.time() if now is None else now
    gone: list[dict] = []
    for base in _roots():
        for kind_dir in (k for k in (base.iterdir() if base.is_dir() else ()) if k.is_dir()):
            for p in kind_dir.iterdir():                             # a killed evictor's leftovers
                if p.name.endswith(".evicting"):
                    shutil.rmtree(p, ignore_errors=True)
                elif p.is_dir():
                    for q in _named(p, ".", ".evicting"):
                        shutil.rmtree(q, ignore_errors=True)
    for kind, unit in units():
        idle = now - last_used(unit)
        if idle < idle_s or (unit.is_dir() and (unit / WANTED).exists()):
            continue
        row = {"kind": kind, "path": str(unit), "bytes": _size(unit), "idle_h": round(idle / 3600, 1)}
        if dry_run:
            gone.append(row)
            continue
        if unit.is_dir():
            try:
                with writing(unit / DIR_LOCK, "the cache evictor"):
                    if (unit / WANTED).exists() or now - last_used(unit) < idle_s:
                        continue
                    for c in _companions(kind, unit):
                        tmp = c.with_name(f".{c.name}.{os.getpid()}.evicting")
                        os.replace(c, tmp)
                        shutil.rmtree(tmp, ignore_errors=True)
                    tmp = unit.with_name(f".{unit.name}.{os.getpid()}.evicting")
                    os.replace(unit, tmp)
            except Busy:
                continue
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            with open(unit.with_name(unit.name + ".lock"), "a+b") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue                                       # being built or rebuilt
                try:
                    if now - last_used(unit) < idle_s:
                        continue
                    unit.unlink(missing_ok=True)
                    stamp_of(unit).unlink(missing_ok=True)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        gone.append(row)
        if log is not None:
            log(f"[cache] {kind}: {unit} removed, idle {row['idle_h']} h, {row['bytes'] / 2**30:.2f} GiB "
                f"(built again upon need)")
    return gone


def evict_due(*, now: float | None = None, log=None) -> list[dict]:
    """`evict_idle`, at most once every EVICT_EVERY_S a process: the
    worker's maintainer asks each minute."""
    global _last_evict
    now = time.time() if now is None else now
    if now - _last_evict < EVICT_EVERY_S:
        return []
    _last_evict = now
    return evict_idle(now=now, log=log)
