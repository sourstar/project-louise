"""SISTERS SHARE ONE TEXT INDEX (operator, 2026-11-10: choice A, "one shared
text index, written by one sister, read by the others";
docs/notes/sister-shared-index.md, 02 §2.7 amended the same day).

Two held collections whose text index would be the same byte for byte —
the same records, layers, update, held slices and index schema: their
INDEX IDENTITY (`identity`) — keep ONE copy of it, under
`collections/.text-index/<identity>/`, instead of one each. One of them, the
group's WRITER, indexes as every collection does; the others, its READERS,
never write it: their index stage copies the writer's marks (`copy_marks`),
so a document is indexed once on the node however many sisters hold it.

WHERE A COLLECTION'S TEXT INDEX IS (`base`). `Collection.fts_dir` stays the
collection's own `<name>/index/` — its catalogue (`_catalog`, keyed on its
manifest's rowids, app/catalog.py) and its facets (app/ask/facets.py) stay
there, per collection, whatever is shared. Its language-group indexes are at
`base(fts_dir)`: that same directory for a collection on its own (every
collection that has no sister on the node: unchanged, byte for byte), or the
group's shared directory, named by the collection's PLACEMENT,
`<name>/state/text-index.json`. Every process resolves it per use, from that
file (cached on its mtime), so the web follows a move without a restart, and
every cache keyed on the path (fts.py's indexes and writers,
language_plan's) is keyed on the shared directory: one tantivy writer per
directory, whichever sister asks.

WHO WRITES (`role`): the member `writer.json` names in the shared
directory, sticky — it changes only when that member is no longer held
there (forgotten), to the next member by name. A paused writer stays the
writer; its readers' indexing waits on it.

WHAT A READER COPIES: a document's marks (`fts_done`, `fts_version`,
`entity_version`, `chunks`, `tokens`) and its entity tags, from the
writer's manifest, where the writer holds the SAME RECORD (`same_record`:
the same `content_hash`, else `txt_hash`, else discovery signature) indexed. Driven by the writer's commits (`indexed`, pushed after
each fts commit) and by the reader's own rdf commits (`parsed`), plus one
walk of the reader's pending rows when its thread starts (the pushes are in
memory; a restart loses them). A document the writer has not indexed yet is
DEFERRED, never an error, and costs nothing per round.

MOVES ONLY AT THE WORKER'S BOOT (`settle`), before any stage and before the
worker opens an index: a group is formed (the member with the most indexed
documents renames its group directories into the shared one; the others
reset their marks where they differ from the writer's, then their own
copies go), joined, or left (a member whose identity changed takes a
hard-linked copy of the group's index and becomes its own writer). A
`hold collection new --sister-of` sister JOINS at once when her sister's
index is already shared (`join_at_carry`): her copied manifest holds the
writer's marks, so there is nothing to reset and nothing to copy.

THE STANDARD ALTERNATIVE, NOT ADOPTED: tantivy has no multi-tenant index.
The usual shape is one index per tenant group with a tenant field filtered
at search (a `slice` field here): sisters holding different slices could
then share one index too. It pays when several sisters per group hold
different slices; none does today (München, 2026-11-10: no sister group on
the node).
"""
from __future__ import annotations

import collections as _collections
import errno
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

#: the shared indexes' folder, under the collections root
SHARED = ".text-index"
#: a collection's placement, under its `state/`
PLACEMENT = "text-index.json"
#: the writer of a shared directory, inside it
WRITER = "writer.json"
#: tantivy's lock files: never hard-linked (one inode would be one lock for
#: two directories), created empty in a linked copy
LOCK_SUFFIX = ".lock"
#: a reader's queue of uids to copy: beyond this, the queue is dropped and
#: a walk of its pending rows is due instead (memory stays bounded)
QUEUE_MAX = 500_000
#: documents copied per round, and rowids per walk window
COPY_BATCH = 2_000
WALK_WINDOW = 50_000
#: how long a member list is trusted (a forget or a placement changes it)
MEMBERS_TTL_S = 10.0


# ---- identity -------------------------------------------------------------

def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def identity(collection) -> str | None:
    """The index identity: BLAKE2b-128 over the canonical JSON of what
    decides the text index's bytes — the records' lineage (02 §2.7's fields
    a sister keeps: plugin, origin, platform image, parser, cleaning,
    slicing), the layers whole, `update`, the slices held, and the schema's
    inputs (validity declared, names). None where there is nothing to share:
    no Codex (a test's, a legacy shape) or an annotations collection (no
    text index)."""
    codex = getattr(collection, "codex", None)
    if codex is None or getattr(codex, "space", None) is None:
        return None
    from . import fts, slice_scope, validity
    try:
        held = slice_scope.held_slices(collection)
    except Exception:   # noqa: BLE001 — an unreadable spec: never shared
        return None
    body = {
        "plugin": codex.plugin,
        "origin": _plain(codex.origin),
        "platform_image": getattr(codex.toolchain, "platform_image", None),
        "parser": _plain(codex.parser),
        "cleaning_version": codex.cleaning_version,
        "slicing": _plain(codex.slicing),
        "layers": _plain(codex.layers),
        "update": _plain(codex.update),
        "slices": list(held) if held is not None else getattr(collection, "slices", "all"),
        "validity": validity.declared(collection) is not None,
        "names": fts.names_capable(collection),
    }
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


# ---- placement ------------------------------------------------------------

def placement_file(root: Path, name: str) -> Path:
    return Path(root) / name / "state" / PLACEMENT


#: str(placement file) -> (mtime_ns, its content)
_placements: dict[str, tuple[int, dict | None]] = {}


def placement(root: Path, name: str) -> dict | None:
    """`{"dir": ".text-index/<identity>", "identity": …}`, or None: the
    collection's index is its own `<name>/index`. Read once per change of
    the file (its mtime)."""
    pf = placement_file(root, name)
    try:
        mtime = pf.stat().st_mtime_ns
    except OSError:
        return None
    hit = _placements.get(str(pf))
    if hit is not None and hit[0] == mtime:
        return dict(hit[1]) if hit[1] is not None else None
    try:
        got = json.loads(pf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = None
    got = got if isinstance(got, dict) and isinstance(got.get("dir"), str) else None
    if len(_placements) > 4096:
        _placements.clear()
    _placements[str(pf)] = (mtime, got)
    return dict(got) if got is not None else None


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_placement(root: Path, name: str, rel: str, ident: str) -> None:
    _write_json(placement_file(root, name), {"dir": rel, "identity": ident})
    invalidate()


#: str(fts_dir) -> (placement mtime_ns or None, base)
_bases: dict[str, tuple[int | None, Path]] = {}


def base(fts_dir) -> Path:
    """Where the language-group indexes of the collection whose own index
    directory is `fts_dir` (`<root>/<name>/index`) are: the shared directory
    its placement names, or `fts_dir` itself. One stat a call."""
    fts_dir = Path(fts_dir)
    pf = fts_dir.parent / "state" / PLACEMENT
    try:
        mtime = pf.stat().st_mtime_ns
    except OSError:
        mtime = None
    key = str(fts_dir)
    hit = _bases.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    out = fts_dir
    if mtime is not None:
        try:
            got = json.loads(pf.read_text(encoding="utf-8"))
            if isinstance(got, dict) and isinstance(got.get("dir"), str):
                out = fts_dir.parent.parent / got["dir"]
        except (OSError, ValueError):
            pass
    if len(_bases) > 4096:
        _bases.clear()
    _bases[key] = (mtime, out)
    return out


def group_dirs(index_dir: Path) -> list[Path]:
    """The language-group indexes in a directory (a tantivy `meta.json`;
    `_catalog` and the facets are the collection's own, never shared)."""
    index_dir = Path(index_dir)
    if not index_dir.is_dir():
        return []
    return sorted(p for p in index_dir.iterdir()
                  if p.is_dir() and not p.name.startswith("_") and (p / "meta.json").is_file())


# ---- members and the writer -----------------------------------------------

def _held(root: Path, name: str) -> bool:
    from .hold import deposit
    try:
        return deposit.held_file(root, name).is_file()
    except Exception:   # noqa: BLE001
        return (Path(root) / f"{name}.toml").is_file()


_members_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
_members_lock = threading.Lock()


def invalidate() -> None:
    with _members_lock:
        _members_cache.clear()
    _placements.clear()
    _bases.clear()


def members(root: Path, rel: str) -> list[str]:
    """The held collections placed at `rel`, by name."""
    key = (str(root), rel)
    now = time.monotonic()
    with _members_lock:
        hit = _members_cache.get(key)
        if hit is not None and now - hit[0] < MEMBERS_TTL_S:
            return list(hit[1])
    out: list[str] = []
    root = Path(root)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.name.startswith(".") or not d.is_dir():
            continue
        pl = placement(root, d.name)
        if pl is not None and pl["dir"] == rel and _held(root, d.name):
            out.append(d.name)
    with _members_lock:
        _members_cache[key] = (now, out)
    return list(out)


def writer(root: Path, rel: str, names: list[str] | None = None) -> str | None:
    """The writer of the shared directory `rel`: the member `writer.json`
    names while it is still placed there, else the first member by name
    (written back, so the choice is sticky from then on)."""
    names = sorted(members(root, rel) if names is None else names)
    if not names:
        return None
    wf = Path(root) / rel / WRITER
    try:
        named = json.loads(wf.read_text(encoding="utf-8")).get("writer")
    except (OSError, ValueError, AttributeError):
        named = None
    if named in names:
        return named
    try:
        _write_json(wf, {"writer": names[0], "since": int(time.time())})
    except OSError:
        pass                    # a read-only process: the worker writes it
    return names[0]


@dataclass(frozen=True)
class Role:
    reader: bool
    #: the writer's name and manifest, for a reader; None otherwise
    writer: str | None = None
    writer_manifest: Path | None = None
    #: the shared directory, relative to the root; None for an own index
    shared: str | None = None
    members: tuple[str, ...] = ()


ALONE = Role(reader=False)


def _root_of(collection) -> Path | None:
    fts_dir = getattr(collection, "fts_dir", None)
    if fts_dir is None:
        return None
    return Path(fts_dir).parent.parent


def role(collection) -> Role:
    """Whether `collection` reads a shared index another member writes."""
    root = _root_of(collection)
    if root is None:
        return ALONE
    name = collection.name
    pl = placement(root, name)
    if pl is None:
        return ALONE
    names = members(root, pl["dir"])
    w = writer(root, pl["dir"], names)
    if w is None or w == name or name not in names:
        return Role(reader=False, shared=pl["dir"], members=tuple(names))
    return Role(reader=True, writer=w, writer_manifest=root / w / "state" / "manifest.sqlite",
                shared=pl["dir"], members=tuple(names))


def writes(collection) -> bool:
    """False for a reader: it never writes the shared index (its writer
    makes the same writes from the same records)."""
    return not role(collection).reader


# ---- what a reader copies --------------------------------------------------

def same_record(mine, theirs) -> bool:
    """The two manifests' rows describe the same record version, by the
    strongest evidence both hold: the same `content_hash` (09 §9.5, the
    cleaned text's) where both have one; else the same `txt_hash` (the
    origin unit's bytes); else the same discovery signature — `path`,
    `mtime`, `size` — which is what discovery compares to decide a document
    changed (app/discovery.py), and which sisters reading one origin
    share. Nothing less is copied: a document that is not known to be the
    same waits for its writer's next commit of it."""
    a, b = mine["content_hash"], theirs["content_hash"]
    if a and b:
        return a == b
    ta, tb = mine["txt_hash"], theirs["txt_hash"]
    if ta and tb:
        return ta == tb
    sig = ("path", "mtime", "size")
    return (all(mine[k] is not None for k in sig)
            and all(mine[k] == theirs[k] for k in sig))


_MARKS = ("fts_version", "entity_version", "chunks", "tokens")


def same_marks(mine, theirs) -> bool:
    return bool(mine["fts_done"]) and all(mine[k] == theirs[k] for k in _MARKS)


class _Queue:
    """A reader's uids to copy, pushed by its writer's commits and its own
    rdf commits; and whether a walk of its pending rows is due."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.uids: _collections.deque[str] = _collections.deque()
        self.seen: set[str] = set()
        self.walk_due = True        # a thread's first round walks (the pushes are in memory)
        self.cursor = 0

    def push(self, uids: Iterable[str]) -> None:
        with self.lock:
            for u in uids:
                if u in self.seen:
                    continue
                if len(self.uids) >= QUEUE_MAX:
                    self.uids.clear()
                    self.seen.clear()
                    self.walk_due, self.cursor = True, 0
                    return
                self.uids.append(u)
                self.seen.add(u)

    def take(self, n: int) -> list[str]:
        with self.lock:
            out = []
            while self.uids and len(out) < n:
                u = self.uids.popleft()
                self.seen.discard(u)
                out.append(u)
            return out


_queues: dict[str, _Queue] = {}
_queues_lock = threading.Lock()


def queue_of(manifest_path) -> _Queue:
    key = str(manifest_path)
    with _queues_lock:
        q = _queues.get(key)
        if q is None:
            q = _queues[key] = _Queue()
        return q


def reset_queues() -> None:
    with _queues_lock:
        _queues.clear()


def walk_again(collection) -> None:
    """A walk of the reader's pending rows is due (a thread starts, a
    writer changes)."""
    q = queue_of(collection.manifest_path)
    with q.lock:
        q.walk_due, q.cursor = True, 0


def indexed(collection, uids: Iterable[str]) -> None:
    """The writer committed these documents to the shared index: each
    reader copies their marks (after the commit: a reader reads committed
    rows only)."""
    r = role(collection)
    if r.reader or r.shared is None or len(r.members) < 2:
        return
    uids = list(uids)
    if not uids:
        return
    root = _root_of(collection)
    for m in r.members:
        if m != collection.name:
            queue_of(root / m / "state" / "manifest.sqlite").push(uids)


def parsed(collection, uids: Iterable[str]) -> None:
    """A reader's own rdf commit: these documents may be copyable now (the
    writer may have indexed them before the reader read them)."""
    if role(collection).reader:
        queue_of(collection.manifest_path).push(list(uids))


_W_ROW = ("SELECT uid, content_hash, txt_hash, path, mtime, size, fts_done, fts_version, "
          "entity_version, chunks, tokens FROM documents WHERE uid=?")
_R_COLS = ("rowid AS rid, uid, native_id, content_hash, txt_hash, path, mtime, size, fts_done, "
           "fts_version, entity_version, chunks, tokens")
#: one row of the reader by uid, only if it is in its index stage's scope
_R_ROW = (f"SELECT {_R_COLS} FROM documents WHERE uid=? AND in_scope=1 AND rdf_done=1 "
          f"AND error IS NULL")
#: a walk window: a rowid RANGE on the integer key (the `+`s keep the scope
#: index out of the plan, as worker._FTS_REINDEX_WINDOWED's)
_R_WALK = (f"SELECT {_R_COLS} FROM documents WHERE rowid>? AND rowid<=? AND +in_scope=1 "
           f"AND +rdf_done=1 AND error IS NULL AND (fts_done=0 OR fts_version<? OR entity_version<?) "
           f"ORDER BY rowid LIMIT ?")
#: guarded on the reader's row being the one read: the same record version
#: (its hashes and discovery signature) and no error since
_COPY = ("UPDATE documents SET fts_done=1, fts_version=?, entity_version=?, chunks=?, tokens=?, "
         "updated_at=? WHERE rowid=? AND +uid=? AND content_hash IS ? AND txt_hash IS ? "
         "AND path IS ? AND mtime IS ? AND size IS ? AND error IS NULL")

_wcons = threading.local()


def writer_connection(path: Path) -> sqlite3.Connection:
    """A read-only connection on the writer's manifest, one per thread."""
    got = getattr(_wcons, "by", None)
    if got is None:
        got = _wcons.by = {}
    con = got.get(str(path))
    if con is None:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30,
                              check_same_thread=False)
        con.row_factory = sqlite3.Row
        got[str(path)] = con
    return con


def close_writer_connections() -> None:
    for con in (getattr(_wcons, "by", None) or {}).values():
        try:
            con.close()
        except sqlite3.Error:
            pass
    _wcons.by = {}


def pending_rows(con, collection, *, fts_version: int, entity_version: int) -> tuple[list, bool]:
    """This round's candidates for a reader: queued uids first, else one
    window of the walk. `(rows, walked)`: `walked` says a walk window was
    read (progress through the manifest, even when it copied nothing)."""
    q = queue_of(collection.manifest_path)
    uids = q.take(COPY_BATCH)
    if uids:
        rows = [con.execute(_R_ROW, (u,)).fetchone() for u in uids]
        return [r for r in rows if r is not None], False
    with q.lock:
        due, start = q.walk_due, q.cursor
    if not due:
        return [], False
    top = con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0
    end = min(start + WALK_WINDOW, top)
    rows = (con.execute(_R_WALK, (start, end, fts_version, entity_version, COPY_BATCH)).fetchall()
            if end > start else [])
    # a full batch stops at its last row: the next round goes on from there
    reached = int(rows[-1]["rid"]) if len(rows) >= COPY_BATCH else end
    with q.lock:
        q.cursor = reached
        if reached >= top:
            q.walk_due = False
    return list(rows), True


def tags_of(wcon, uids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {u: [] for u in uids}
    for u in uids:
        out[u] = sorted(r[0] for r in wcon.execute(
            "SELECT entity_id FROM doc_keys WHERE uid=?", (u,)).fetchall())
    return out


def copy_marks(con, collection, r: Role, rows: list, *, now: int | None = None,
               after: Callable[[list], None] | None = None) -> list:
    """Copy the writer's marks and tags onto `rows` where the writer holds
    the same record indexed and the marks differ. The writer's rows are
    read BEFORE the reader's write lock (another file, long reads stay out
    of the transaction); every write is guarded on the reader's row being
    the one read. `after(copied_rows)` runs inside the transaction (the
    genesis demand). Returns the rows copied."""
    if not rows or r.writer_manifest is None:
        return []
    wcon = writer_connection(r.writer_manifest)
    plan = []
    for row in rows:
        w = wcon.execute(_W_ROW, (row["uid"],)).fetchone()
        if w is None or not w["fts_done"] or not same_record(row, w) or same_marks(row, w):
            continue
        plan.append((row, w))
    if not plan:
        return []
    from . import entities
    tags = tags_of(wcon, [row["uid"] for row, _ in plan])
    now = int(time.time()) if now is None else now
    copied: list = []
    own = not con.in_transaction
    # one short write lock per COPY_BATCH documents, as the index stage's
    # batch holds it (marks, tags and the genesis demand together)
    for i in range(0, len(plan), COPY_BATCH):
        part = plan[i:i + COPY_BATCH]
        done = []
        if own:
            con.execute("BEGIN IMMEDIATE")
        try:
            for row, w in part:
                cur = con.execute(_COPY, (w["fts_version"], w["entity_version"], w["chunks"], w["tokens"],
                                          now, int(row["rid"]), row["uid"], row["content_hash"],
                                          row["txt_hash"], row["path"], row["mtime"], row["size"]))
                if cur.rowcount:
                    done.append(row)
            if done:
                entities.write_tags_many(con, {row["uid"]: tags[row["uid"]] for row in done})
                if after is not None:
                    after(done)
            if own:
                con.execute("COMMIT")
        except BaseException:
            if own and con.in_transaction:
                con.execute("ROLLBACK")
            raise
        copied += done
    return copied


# ---- boot: forming, joining and leaving groups ------------------------------

def _indexed_count(manifest: Path) -> int:
    try:
        con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True, timeout=30)
        try:
            return int(con.execute("SELECT COUNT(*) FROM documents WHERE fts_done=1").fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def link_tree(src: Path, dst: Path) -> int:
    """A hard-linked copy of a tantivy directory (tantivy never rewrites a
    file: segments are written once, `meta.json` and `.managed.json` are
    replaced by rename, so the two diverge safely). Lock files are created
    empty, never linked. Raises OSError(EXDEV) across filesystems. Returns
    the files linked."""
    n = 0
    for base_, dirs, files in os.walk(src):
        rel = Path(base_).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            s, d = Path(base_) / f, dst / rel / f
            if d.exists():
                continue
            if f.endswith(LOCK_SUFFIX):
                d.touch()
                continue
            os.link(s, d)
            n += 1
    return n


def _diff_window(reader: Path, wman: Path, lo: int, hi: int) -> list[tuple[int, str]]:
    """The reader's indexed rows in (lo, hi] whose marks the writer's row
    does not equal (or that the writer does not hold as the same record)."""
    rcon = sqlite3.connect(f"file:{reader}?mode=ro", uri=True, timeout=60)
    wcon = sqlite3.connect(f"file:{wman}?mode=ro", uri=True, timeout=60)
    rcon.row_factory = wcon.row_factory = sqlite3.Row
    try:
        out = []
        for row in rcon.execute(f"SELECT {_R_COLS} FROM documents WHERE rowid>? AND rowid<=? "
                                f"AND +fts_done=1", (lo, hi)):
            w = wcon.execute(_W_ROW, (row["uid"],)).fetchone()
            if w is None or not w["fts_done"] or not same_record(row, w) or not same_marks(row, w):
                out.append((int(row["rid"]), row["uid"]))
        return out
    finally:
        rcon.close()
        wcon.close()


def reset_differing(reader: Path, wman: Path, *, threads: int | None = None,
                    window: int = 100_000, log: Callable[[str], None] = print) -> int:
    """A joining member's index marks reset (`fts_done = 0`) wherever they
    differ from the writer's: its copy then reads the writer's index, and
    what the writer has not indexed as this member holds it is indexed
    again — by the writer, copied here. Windows compared on threads (the
    cores the node allows, CLAUDE.md), each window's resets one short
    `BEGIN IMMEDIATE`; idempotent, so a stop resumes by running it again."""
    from concurrent.futures import ThreadPoolExecutor
    from . import db, parallel
    rcon = sqlite3.connect(reader, timeout=60, isolation_level=None)
    db.wal(rcon)
    try:
        top = rcon.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0
        bounds = [(lo, min(lo + window, top)) for lo in range(0, top, window)]
        n = threads or max(2, parallel.processes())
        reset = 0
        with ThreadPoolExecutor(max_workers=n) as ex:
            for got in ex.map(lambda b: _diff_window(reader, wman, *b), bounds):
                if not got:
                    continue
                rcon.execute("BEGIN IMMEDIATE")
                try:
                    rcon.executemany("UPDATE documents SET fts_done=0 WHERE rowid=? AND +uid=?", got)
                    rcon.execute("COMMIT")
                except BaseException:
                    rcon.execute("ROLLBACK")
                    raise
                reset += len(got)
        return reset
    finally:
        rcon.close()


def _remove_groups(index_dir: Path) -> int:
    """The language-group indexes under a collection's own directory
    removed (its catalogue and facets stay). Returns the bytes freed."""
    freed = 0
    for g in group_dirs(index_dir):
        freed += sum(f.stat().st_size for f in g.rglob("*") if f.is_file())
        shutil.rmtree(g)
    return freed


def _manifest(root: Path, name: str) -> Path:
    return Path(root) / name / "state" / "manifest.sqlite"


def _source(root: Path, names: list[str]) -> str | None:
    """The member whose index the group keeps: the one whose manifest marks
    the most documents indexed, first by name on a tie; None when no member
    holds an index yet."""
    have = [n for n in names if group_dirs(base(Path(root) / n / "index"))]
    if not have:
        return None
    return sorted(have, key=lambda n: (-_indexed_count(_manifest(root, n)), n))[0]


def _same_fs(a: Path, b: Path) -> None:
    """Raise OSError(EXDEV) before anything moves when `a` and `b` are on two
    filesystems (a rename or a link would fail half-way otherwise)."""
    while not b.exists():
        b = b.parent
    if os.stat(a).st_dev != os.stat(b).st_dev:
        raise OSError(errno.EXDEV, f"{a} and {b} are on two filesystems")


def _move_into(root: Path, name: str, shared: Path) -> str:
    """`name`'s index into `shared` (which must not exist yet): its own
    group directories renamed (one filesystem: free), or, placed in another
    shared directory, that directory renamed when nobody else is placed
    there, a hard-linked copy of it otherwise."""
    root = Path(root)
    pl = placement(root, name)
    src = root / name / "index" if pl is None else root / pl["dir"]
    _same_fs(src, shared.parent)
    shared.parent.mkdir(parents=True, exist_ok=True)
    tmp = shared.with_name(f".{shared.name}.forming")
    if tmp.exists():
        shutil.rmtree(tmp)
    if pl is None:
        tmp.mkdir(parents=True)
        for g in group_dirs(src):
            os.rename(g, tmp / g.name)
        os.rename(tmp, shared)
        return "renamed"
    if [m for m in members(root, pl["dir"]) if m != name]:
        try:
            for g in group_dirs(src):
                link_tree(g, tmp / g.name)
            tmp.mkdir(parents=True, exist_ok=True)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        os.rename(tmp, shared)
        return "linked"
    os.rename(src, shared)
    (shared / WRITER).unlink(missing_ok=True)
    return "renamed"


def _leave_shared(root: Path, name: str, log: Callable[[str], None]) -> str:
    """A member now alone with its identity: back to its own
    `<name>/index`, a hard-linked copy of the shared index it read (its
    marks are that index's: nothing to reset), its placement removed. The
    shared directory goes if nobody is placed there any more (every file is
    linked: nothing is lost)."""
    root = Path(root)
    pl = placement(root, name)
    old = root / pl["dir"]
    own = root / name / "index"
    _same_fs(old, own)
    for g in group_dirs(old):
        tmp = own / f".{g.name}.leaving"
        if tmp.exists():
            shutil.rmtree(tmp)
        if (own / g.name).exists():
            continue
        try:
            link_tree(g, tmp)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        os.rename(tmp, own / g.name)
    placement_file(root, name).unlink()
    invalidate()
    if not members(root, pl["dir"]) and old.is_dir():
        shutil.rmtree(old)
    log(f"text index: {name} has no sister with its identity any more; it left {pl['dir']} "
        f"for its own index (hard-linked, nothing re-indexed)")
    return "left"


def _set_aside(root: Path, rel: str, log: Callable[[str], None]) -> None:
    """A shared directory that exists with nobody placed in it holds an index
    no manifest's marks describe: never adopted. Moved aside (kept, named in
    `orphans`), never deleted here."""
    d = Path(root) / rel
    aside = d.with_name(f".stale-{d.name}-{int(time.time())}")
    os.rename(d, aside)
    log(f"text index: {rel} existed with no member placed in it; set aside as {aside.name}")


def settle(root: Path, collections: list, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Place every held collection's text index by its identity — the
    worker's boot, before any stage (module docstring). Idempotent; each
    step is complete or redone: the marks are reset before the placement
    is written, and a member's own copy is removed only after it.

    - ONE member of an identity: its own `<name>/index`, as every collection
      without a sister (a member that was in a group leaves it, linked).
    - TWO OR MORE: `.text-index/<identity>`. Formed from the member holding
      the most (renamed, or linked out of the group it was in); every other
      member's marks reset where they differ from the writer's, its placement
      written, then its own copy removed."""
    root = Path(root)
    report: dict[str, Any] = {"groups": [], "freed": 0, "skipped": []}
    by_ident: dict[str, list] = {}
    for c in collections:
        try:
            ident = identity(c)
        except Exception as e:   # noqa: BLE001 — one collection's, not the boot's
            report["skipped"].append((c.name, f"{type(e).__name__}: {e}"))
            continue
        if ident is not None:
            by_ident.setdefault(ident, []).append(c.name)
    invalidate()
    for ident, names in sorted(by_ident.items()):
        names = sorted(names)
        rel = f"{SHARED}/{ident}"
        shared = root / rel
        try:
            if len(names) == 1:
                pl = placement(root, names[0])
                if pl is not None:
                    how = _leave_shared(root, names[0], log)
                    report["groups"].append({"identity": ident, "members": names, "how": how})
                continue
            placed = [n for n in names if (placement(root, n) or {}).get("dir") == rel]
            how = "kept"
            if shared.exists() and not placed:
                _set_aside(root, rel, log)
            if not shared.exists():
                src = _source(root, names)
                if src is None:
                    shared.mkdir(parents=True, exist_ok=True)
                    how, src = "new", names[0]
                else:
                    how = _move_into(root, src, shared)
                write_placement(root, src, rel, ident)
                _write_json(shared / WRITER, {"writer": src, "since": int(time.time())})
                placed = [src]
            w = writer(root, rel, placed)
            for n in names:
                pl = placement(root, n)
                if pl is None or pl["dir"] != rel:
                    reset = reset_differing(_manifest(root, n), _manifest(root, w), log=log)
                    write_placement(root, n, rel, ident)
                    log(f"text index: {n} joins {rel} (written by {w}); {reset} document(s) "
                        f"whose marks differed from {w}'s reset, to be copied as {w} indexes them")
                    if pl is not None and not members(root, pl["dir"]):
                        old = root / pl["dir"]
                        freed = sum(f.stat().st_size for f in old.rglob("*") if f.is_file())
                        shutil.rmtree(old)
                        report["freed"] += freed
                        log(f"text index: {pl['dir']}, read by nobody now, removed "
                            f"({freed / 2**30:.2f} GiB)")
                freed = _remove_groups(root / n / "index")
                if freed:
                    report["freed"] += freed
                    log(f"text index: {n}'s own copy removed, {freed / 2**30:.2f} GiB freed")
            invalidate()
            report["groups"].append({"identity": ident, "members": names,
                                     "writer": writer(root, rel, names), "how": how})
        except OSError as e:
            if e.errno == errno.EXDEV:
                report["skipped"].append((",".join(names), "another filesystem than the shared folder"))
                log(f"text index: {', '.join(names)} not shared: {e}")
                continue
            raise
    invalidate()
    report["orphans"] = orphans(root)
    for o in report["orphans"]:
        log(f"text index: {o['dir']} is placed by no held collection ({o['bytes'] / 2**30:.2f} GiB); "
            f"`hold collection forget` of a group's last member removes its directory")
    return report


def orphans(root: Path) -> list[dict[str, Any]]:
    """Shared directories no held collection is placed at."""
    root = Path(root)
    out = []
    shared_root = root / SHARED
    for d in sorted(shared_root.iterdir()) if shared_root.is_dir() else []:
        if not d.is_dir() or d.name.endswith(".forming"):
            continue
        rel = f"{SHARED}/{d.name}"
        if d.name.startswith(".stale-") or not members(root, rel):
            out.append({"dir": rel, "bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file())})
    return out


def join_at_carry(root: Path, from_name: str, to_name: str, ident_from: str | None,
                  ident_to: str | None) -> str | None:
    """A sister carried from `from_name` (`hold collection new --sister-of`,
    case J): when her sister's index is shared already and their identities
    are equal, she joins it now — her manifest is a copy of her sister's, so
    every mark she holds is the writer's, and nothing is copied or reset.
    Returns the shared directory joined, or None (her index is then linked
    beside her, and the worker's next boot groups them)."""
    pl = placement(root, from_name)
    if pl is None or ident_from is None or ident_from != ident_to:
        return None
    write_placement(root, to_name, pl["dir"], ident_to)
    return pl["dir"]


def leave_at_forget(root: Path, name: str, *, dry_run: bool = False) -> dict[str, Any] | None:
    """What a forget of `name` does to a shared index: nothing while another
    held member is placed there (the next member by name becomes its writer
    if `name` wrote it); removed with its last member. Returns
    `{dir, bytes, removed|kept}` or None (no placement)."""
    pl = placement(root, name)
    if pl is None:
        return None
    rel = pl["dir"]
    d = Path(root) / rel
    invalidate()
    others = [m for m in members(root, rel) if m != name]
    size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.is_dir() else 0
    if others:
        return {"dir": str(d), "bytes": size, "kept": others}
    if not dry_run and d.is_dir():
        shutil.rmtree(d)
    return {"dir": str(d), "bytes": size, "removed": not dry_run}


def index_digest(root: Path, name: str) -> dict[str, Any]:
    """`{files, bytes, sha256}` of a collection's text index AS IT IS READ:
    its own `<name>/index` with its language groups taken from where its
    placement says they are — `recreate.tree_digest`'s lines over that view,
    so two collections with one digest search the same bytes whether either
    shares its index or not (the sister carry's proof)."""
    from .hold import recreate
    own = Path(root) / name / "index"
    groups_at = base(own)
    group_names = {g.name for g in group_dirs(groups_at)}
    files: list[tuple[str, Path]] = []
    roots = [(own, lambda rel: rel.split("/", 1)[0] not in group_names)]
    roots.append((groups_at, lambda rel: rel.split("/", 1)[0] in group_names))
    seen: set[str] = set()
    for top, keep in roots:
        if not top.is_dir():
            continue
        for b, dirs, names in os.walk(top):
            dirs.sort()
            for nm in sorted(names):
                p = Path(b) / nm
                rel = p.relative_to(top).as_posix()
                if not keep(rel) or rel in seen or p.is_symlink() or not p.is_file():
                    continue
                seen.add(rel)
                files.append((rel, p))
    sums = [recreate._file_sha256(p) for _, p in files]
    sizes = [p.stat().st_size for _, p in files]
    lines = sorted(f"{rel} {size} {h}" for (rel, _), size, h in zip(files, sizes, sums))
    return {"files": len(files), "bytes": int(sum(sizes)),
            "sha256": ("sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest())
            if files else None}


def describe(root: Path, name: str) -> dict[str, Any]:
    """What `hold collection show` says of a collection's text index: its
    own, or the shared one — who writes it, who reads it, and a reader's
    wait on a paused writer, in a sentence."""
    pl = placement(root, name)
    if pl is None:
        return {"shared": None, "says": "its own (no sister with the same text index on this node)"}
    invalidate()
    names = members(root, pl["dir"])
    w = writer(root, pl["dir"], names)
    paused = False
    try:
        from . import node_state
        paused = w in node_state.paused_collections()
    except Exception:   # noqa: BLE001 — the sentence goes without it
        pass
    others = [n for n in names if n != name]
    if w == name:
        says = (f"shared with {', '.join(others)}, written by this collection" if others else
                "shared, written by this collection (no other member held)")
    else:
        says = (f"shared, written by {w}" + (", paused: this collection's indexing waits on it"
                                             if paused else "") + f"; read by {', '.join(others)}")
    return {"shared": pl["dir"], "identity": pl.get("identity"), "writer": w, "members": names,
            "writer_paused": paused, "says": says}
