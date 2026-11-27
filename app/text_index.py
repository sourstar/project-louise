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

MOVES WITH NO WRITER OPEN: at the worker's boot (`settle`), before any
stage and before the worker opens an index, a group is formed (the member
with the most indexed documents renames its group directories into the
shared one; the others reset their marks where they differ from the
writer's, then their own copies go) or joined. A `hold collection new
--sister-of` sister FORMS OR JOINS HER SISTER'S GROUP AT ONCE
(`form_at_carry`): her sister's directories renamed into the shared one
where they are her sister's own, both placed — her copied manifest holds
the writer's marks, so nothing is reset or copied. The rename needs her
sister's index writers closed: a writer another process holds (the running
worker keeps its writers for its life) refuses in words, asking for
`louise node stop` (CLAUDE.md RULE 1: "a tool that needs the worker's
writers asks for a stop, not for code that works around one").

A FOLDER STAYS ITS WRITER'S. A member ALONE in its folder stays placed
(no copy back to `<name>/index`: 54 GB for gutenberg); the directory goes
with the last member's forget. A member whose identity diverges from its
writer's (its slices changed, say) — or a writer whose content key changed
— LEAVES, never sharing that writer again: EVERY mark of it reset, said in
one line, and the worker indexes it again, into its own folder (or as a
reader of its new identity's group). NOTHING IS COPIED OR PRUNED (the
coordinator's default of 2026-11-27, after round 5 found the copy-on-leave
losing data): a member leaves the moment its identity changes, before it
indexes anything under it, so the folder it leaves holds exactly its
group's identity. A placement naming a directory that is gone is dropped
at the boot, the member's marks reset, said. A collection another process
holds, or one with no manifest yet (a sister made, her carry not done), is
not settled at that boot. The journal is for FORMING BY RENAME only (a
sister's creation, the boot); while one names a collection its fts stage
is held and no group is made for it.

NO HARD LINKS (operator, 2026-11-27: "Never use hard links, always
consciously load from a path. It already happened that you deleted important
files because you forgot the hard links"; app/file_copy.py). A shared index
is one directory read by its path; where two owners must each hold one, it
is copied. Nothing here makes a second name for a file.

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
#: tantivy's lock files: never carried into a copy (a copy's locks are its
#: own writer's)
LOCK_SUFFIX = ".lock"
#: a placement's key: what the group's index CONTENT was built for (the
#: identity without the slices held and `update`, which add or drop
#: documents but never change how one is indexed), so a member whose content
#: changed is never given another's copy (`content_key`, `_plan`)
CONTENT = "content"
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


def _layer_key(layer: Any) -> Any:
    """A layer as its index reads it: a text input stated (Codex version 5
    and later) or absent (below 5, read as text, codex.Layer.form) is the
    same cut, the same chunks and the same index. Absent is kept, so a
    collection below version 5 hashes as it did (gutenberg-nemo, 2026-11-27:
    a sister written at version 11 of an older sister would otherwise never
    share her index)."""
    plain = _plain(layer)
    if isinstance(plain, dict) and plain.get("input") == {"form": "text"}:
        plain = {**plain, "input": None}
    return plain


def _slicing_key(slicing: Any) -> Any:
    """The slicing as its index reads it: `container = "jsonl"` stated (Codex
    version 9 and later) or absent (below 9, which means JSONL) is the same
    parts — `codex.parts_slicing`, the one answer the carry uses too."""
    from .hold import codex as cx
    return cx.parts_slicing(_plain(slicing))


def _identity_body(collection) -> dict[str, Any] | None:
    codex = getattr(collection, "codex", None)
    if codex is None or getattr(codex, "space", None) is None:
        return None
    from . import fts, slice_scope, validity
    try:
        held = slice_scope.held_slices(collection)
    except Exception:   # noqa: BLE001 — an unreadable spec: never shared
        return None
    return {
        "plugin": codex.plugin,
        "origin": _plain(codex.origin),
        "platform_image": getattr(codex.toolchain, "platform_image", None),
        "parser": _plain(codex.parser),
        "cleaning_version": codex.cleaning_version,
        "slicing": _slicing_key(codex.slicing),
        "layers": [_layer_key(l) for l in codex.layers],
        "update": _plain(codex.update),
        "slices": list(held) if held is not None else getattr(collection, "slices", "all"),
        "validity": validity.declared(collection) is not None,
        "names": fts.names_capable(collection),
    }


def _digest(body: dict[str, Any]) -> str:
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def content_key(collection) -> str | None:
    """What decides HOW a document is indexed — the identity without the
    slices held and `update` (which decide which documents are): two
    collections with one content key index a document alike."""
    body = _identity_body(collection)
    if body is None:
        return None
    return _digest({k: v for k, v in body.items() if k not in ("slices", "update")})


def identity(collection) -> str | None:
    """The index identity: BLAKE2b-128 over the canonical JSON of what
    decides the text index's bytes — the records' lineage (02 §2.7's fields
    a sister keeps: plugin, origin, platform image, parser, cleaning,
    slicing), the layers whole, `update`, the slices held, and the schema's
    inputs (validity declared, names). None where there is nothing to share:
    no Codex (a test's, a legacy shape) or an annotations collection (no
    text index)."""
    body = _identity_body(collection)
    return None if body is None else _digest(body)


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


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, value: dict, *, durable: bool = False) -> None:
    """`value` written whole: to a hidden name, renamed into place.
    `durable`: the file synced, then its directory — a group folder's record
    and a forming's journal survive a power cut (the fourth read of
    cf91b9ac)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(value, sort_keys=True))
        if durable:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, path)
    if durable:
        _fsync_dir(path.parent)


def write_placement(root: Path, name: str, rel: str, ident: str, *, content: str | None = None) -> None:
    """Synced, the file then its directory: a forming's journal is removed
    only once every placement it made is on disk (round 5, item 4)."""
    _write_json(placement_file(root, name), {"dir": rel, "identity": ident,
                                             **({CONTENT: content} if content else {})}, durable=True)
    invalidate()


def is_shared(rel: str) -> bool:
    """A shared directory (`.text-index/<identity>`), not a collection's own."""
    return str(rel).startswith(SHARED + "/")


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
    return sorted(p for p in index_dir.iterdir()       # a hidden one is a copy under way
                  if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "meta.json").is_file())


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
    if not is_shared(rel):
        return None                 # a collection's own index: its owner writes it
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


def copy_tree(src: Path, dst: Path, what: str = "a text index") -> int:
    """A COPY of a tantivy directory (never hard-linked: app/file_copy.py),
    after a preflight of its bytes where it lands: to a hidden name, renamed
    into place; `dst` must not exist. Its lock files are not carried (a
    copy's locks are its own writer's). Returns the files."""
    from .file_copy import copy_tree as _copy, preflight, tree_bytes
    preflight(tree_bytes(src), Path(dst).parent, what)
    n = _copy(src, dst)
    for lock in [q for q in Path(dst).rglob(f"*{LOCK_SUFFIX}") if q.is_file()]:
        lock.unlink()
        n -= 1
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


def _rw(manifest: Path) -> sqlite3.Connection:
    """A manifest opened to write and NEVER CREATED (`mode=rw`): a collection
    made and not carried yet has none, and an empty one made here would be
    taken for hers (the fourth read of cf91b9ac)."""
    return sqlite3.connect(f"file:{manifest}?mode=rw", uri=True, timeout=60, isolation_level=None)


def reset_differing(reader: Path, wman: Path, *, limit: int | None = None,
                    window: int = 100_000, log: Callable[[str], None] = print) -> int:
    """A joining member's index marks reset (`fts_done = 0`) wherever they
    differ from the writer's: its copy then reads the writer's index, and
    what the writer has not indexed as this member holds it is indexed
    again — by the writer, copied here. The windows are compared in the
    parse pool's processes (`parallel.each_part`, the cores the node allows:
    a thread pool gave nothing, the per-row work holding the GIL — 11 µs a
    row on 1, 4 and 8 threads alike, measured); each window's resets are
    one short `BEGIN IMMEDIATE` here, the one writer. Idempotent, so a stop
    resumes by running it again."""
    from . import db, parallel
    rcon = _rw(reader)
    db.wal(rcon)
    try:
        top = rcon.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0
        tasks = [(Path(reader), Path(wman), lo, min(lo + window, top)) for lo in range(0, top, window)]
        reset = 0
        for got in parallel.each_part(_diff_window, tasks, limit=limit):
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


def _content_valid(root: Path, name: str, content: str | None) -> bool:
    """Whether `name`'s index — its own, or the group's it is placed in —
    describes how it indexes NOW: the content key its placement recorded is
    its current one (a placement written before 2026-11-27 recorded none: a
    collection's Codex, and so its layers, does not change in its life)."""
    pl = placement(root, name)
    had = (pl or {}).get(CONTENT)
    return had is None or content is None or had == content


def _source(root: Path, names: list[str], contents: dict[str, str | None] | None = None) -> str | None:
    """The member whose index the group keeps: the one whose manifest marks
    the most documents indexed, first by name on a tie, among those whose
    index still describes how they index (`_content_valid`); None when no
    such member holds an index yet."""
    contents = contents or {}
    have = [n for n in names if _content_valid(root, n, contents.get(n))
            and group_dirs(base(Path(root) / n / "index"))]
    if not have:
        return None
    return sorted(have, key=lambda n: (-_indexed_count(_manifest(root, n)), n))[0]


def _reset_all(manifest: Path, *, window: int = 100_000) -> int:
    """Every index mark of a manifest reset (`fts_done = 0`), in rowid
    windows: its index no longer describes it, so it is indexed again —
    never given another's copy. The documents reset."""
    con = _rw(manifest)
    try:
        from . import db
        db.wal(con)
        top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM documents").fetchone()[0]
        n = 0
        for lo in range(0, int(top), window):
            con.execute("BEGIN IMMEDIATE")
            try:
                cur = con.execute("UPDATE documents SET fts_done=0 WHERE rowid>? AND rowid<=? AND fts_done=1",
                                  (lo, lo + window))
                n += cur.rowcount
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return n
    finally:
        con.close()


class WriterHeld(RuntimeError):
    """An index writer another process holds (the running worker keeps its
    writers for its life): nothing is renamed from under it."""


def writer_held(index_dir: Path) -> str | None:
    """The first language group under `index_dir` whose tantivy writer lock
    another process holds (an flock, as tantivy takes it), or None. Taken
    and let go at once: a probe, never kept."""
    import fcntl
    for g in group_dirs(index_dir):
        lock = g / ".tantivy-writer.lock"
        if not lock.is_file():
            continue
        fd = os.open(lock, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return g.name
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return None


def _same_fs(a: Path, b: Path) -> None:
    """Raise OSError(EXDEV) before anything moves when `a` and `b` are on two
    filesystems (a rename or a link would fail half-way otherwise)."""
    while not b.exists():
        b = b.parent
    if os.stat(a).st_dev != os.stat(b).st_dev:
        raise OSError(errno.EXDEV, f"{a} and {b} are on two filesystems")


#: a group folder's own record: the identity and content key it holds
GROUP = "group.json"


def new_rel() -> str:
    """A NEW group folder's name: a unique id made once, never an identity
    key, so a folder's name is never reused for another group (the third
    read of 74c95825, finding 1)."""
    import uuid
    return f"{SHARED}/{uuid.uuid4().hex}"


def folder_identity(root: Path, rel: str) -> str | None:
    """The identity a group folder holds (`GROUP`), or — a folder made
    before 2026-11-27, named by its identity — its name."""
    try:
        got = json.loads((Path(root) / rel / GROUP).read_text(encoding="utf-8"))
        return got.get("identity")
    except (OSError, ValueError, AttributeError):
        return Path(rel).name if is_shared(rel) else None


def _tag(root: Path, rel: str, ident: str, content: str | None) -> None:
    _write_json(Path(root) / rel / GROUP, {"identity": ident, "content": content}, durable=True)


def _place(root: Path, name: str, rel: str, ident: str, content: str | None, w: str,
           log: Callable[[str], None]) -> int:
    """`name` placed in `rel`, written by `w`, through THE ONE
    RECONCILIATION (a group formed at boot or at a carry, joined, alike):
    its marks reset where they differ from the writer's — all of them where
    its content key changed — then the placement written, then its own
    copy removed, and a folder it leaves that nobody reads any more removed.
    Placed already: its record refreshed (a folder retagged with another
    identity) and its own copy, never read, removed (the fourth read of
    cf91b9ac: it leaked). The documents reset."""
    root = Path(root)
    pl = placement(root, name)
    if pl is not None and pl["dir"] == rel:
        had = pl.get(CONTENT)
        if pl.get("identity") != ident or (had is None and content):
            write_placement(root, name, rel, ident, content=had or content)
        _remove_groups(root / name / "index")
        return 0
    if name == w:
        reset = 0 if _content_valid(root, name, content) else _reset_all(_manifest(root, name))
    elif not _content_valid(root, name, content):
        reset = _reset_all(_manifest(root, name))
    else:
        reset = reset_differing(_manifest(root, name), _manifest(root, w), log=log)
    write_placement(root, name, rel, ident, content=content)
    log(f"text index: {name} placed in {rel} (written by {w}); {reset} document(s) reset")
    if pl is not None and is_shared(pl["dir"]) and not members(root, pl["dir"]):
        old = root / pl["dir"]
        if old.is_dir():
            shutil.rmtree(old)
            log(f"text index: {pl['dir']}, read by nobody now, removed")
    _remove_groups(root / name / "index")
    return reset


# ---- leaving: to an empty folder, every mark reset ---------------------------------

def _leave(root: Path, name: str, log: Callable[[str], None]) -> int:
    """`name`'s identity is no longer its group's: ALL its index marks reset,
    said in one line; it is indexed again by the worker, into its own folder
    (or as a reader of the group of its new identity). NOTHING IS COPIED OR
    PRUNED (the coordinator's default of 2026-11-27, replacing a94c0177's
    copy-on-leave, which round 5 found losing data three ways): a member
    leaves the moment its identity changes, before it indexes anything
    under the new one, so the folder it leaves holds exactly its group's
    identity, and those who stay need nothing done."""
    pl = placement(root, name)
    n = _reset_all(_manifest(root, name))
    log(f"text index: {name} leaves {pl['dir'] if pl else 'its index'} (its identity is no longer its "
        f"group's): its {n} indexed document(s) reset, to be indexed again; nothing copied")
    return n


def _empty_group(root: Path, ident: str, contents: dict[str, str | None], names: list[str],
                 log: Callable[[str], None]) -> str:
    """A NEW empty folder for `names`, each one's marks reset first (none of
    them holds an index for this identity), written by the first by name. A
    cut before the placements leaves an empty folder `orphans` names; the
    next boot does it again."""
    root = Path(root)
    names = sorted(names)
    for n in names:
        _reset_all(_manifest(root, n))
    rel = new_rel()
    (root / rel).mkdir(parents=True)
    _tag(root, rel, ident, contents.get(names[0]))
    _write_json(root / rel / WRITER, {"writer": names[0], "since": int(time.time())}, durable=True)
    for n in names:
        _place(root, n, rel, ident, contents.get(n), names[0], log)
    return rel


# ---- forming by rename, journaled ----------------------------------------------------

def _journal_dir(root: Path) -> Path:
    return Path(root) / SHARED / ".forming"


def _members_of(j: dict) -> set[str]:
    return {j["writer"], *j["place"]}


def open_journals(root: Path) -> list[set[str]]:
    """The members each unfinished forming names (an unreadable journal
    names nobody it can be sure of: it is said by `resume_forming`)."""
    jd = _journal_dir(root)
    out = []
    for jf in sorted(jd.glob("*.json")) if jd.is_dir() else []:
        try:
            out.append(_members_of(json.loads(jf.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def forming(collection) -> bool:
    """Whether an unfinished forming names `collection`: its index groups
    are being renamed into a shared folder. Its fts stage is held and no
    group is made for it until that is finished (`fts._for_write`,
    `fts.get_index`; round 5, item 3). One stat while no forming is open."""
    root = _root_of(collection)
    if root is None or not _journal_dir(root).is_dir():
        return False
    return any(collection.name in ms for ms in open_journals(root))


def _form(root: Path, *, source: Path, ident: str, content: str | None, writer_name: str,
          place: list[str], log: Callable[[str], None]) -> str:
    """A NEW group folder (`new_rel`) formed BY RENAME of `source`'s groups —
    the ONLY forming (a sister's creation, the node stopped; the boot) —
    JOURNALED and synced before the first rename (the groups, the folder,
    the members to place), then completed by `_finish`, which a retry or the
    boot runs again from the journal, forward, never deleting what moved."""
    root = Path(root)
    rel = new_rel()
    _same_fs(source, root / SHARED if (root / SHARED).exists() else root)
    jd = _journal_dir(root)
    jd.mkdir(parents=True, exist_ok=True)
    journal = jd / f"{Path(rel).name}.json"
    _write_json(journal, {"rel": rel, "source": str(source), "groups": [g.name for g in group_dirs(source)],
                          "identity": ident, "content": content, "writer": writer_name,
                          "place": list(place)}, durable=True)
    return _finish(root, journal, log)


def _finish(root: Path, journal: Path, log: Callable[[str], None]) -> str:
    """A forming completed from its journal: each group not yet in the
    folder renamed into it, the folder tagged, its writer named, every
    member placed (`_place`; each placement synced) — and only then the
    journal removed. Repeatable at any cut."""
    root = Path(root)
    j = json.loads(journal.read_text(encoding="utf-8"))
    rel, target, src = j["rel"], root / j["rel"], Path(j["source"])
    target.mkdir(parents=True, exist_ok=True)
    for g in j["groups"]:
        if (target / g).exists() or not (src / g).exists():
            continue
        os.rename(src / g, target / g)
    _tag(root, rel, j["identity"], j.get("content"))
    if not (target / WRITER).is_file():
        _write_json(target / WRITER, {"writer": j["writer"], "since": int(time.time())}, durable=True)
    w = writer(root, rel, None) if members(root, rel) else j["writer"]
    for n in [j["writer"], *[m for m in j["place"] if m != j["writer"]]]:
        _place(root, n, rel, j["identity"], j.get("content"), w, log)
    invalidate()
    journal.unlink(missing_ok=True)
    _fsync_dir(journal.parent)
    return rel


def resume_forming(root: Path, log: Callable[[str], None] = print, *,
                   own: Callable[[set[str]], bool] | None = None,
                   report: dict[str, Any] | None = None) -> list[str]:
    """The formings a cut left (their journals), finished forward — only
    the CALLER'S OWN (`own(members)`; every journal when None): the worker's
    boot finishes those all of whose members it settles, a sister's carry
    those naming her or her sister and no collection another process runs,
    so a stale journal of someone else's blocks neither. With `report`, one
    that fails is said and left for the next pass, the others go on;
    without, it raises (the caller's own)."""
    jd = _journal_dir(root)
    done = []
    for jf in sorted(jd.glob("*.json")) if jd.is_dir() else []:
        try:
            members_ = _members_of(json.loads(jf.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError) as e:
            if report is not None:
                report["skipped"].append((jf.name, f"an unreadable forming journal: {e}"))
                log(f"text index: the forming journal {jf} cannot be read ({e}): left as it is")
            continue
        if own is not None and not own(members_):
            continue
        try:
            done.append(_finish(root, jf, log))
        except Exception as e:   # noqa: BLE001 — said, the next journal goes on
            if report is None:
                raise
            report["skipped"].append((",".join(sorted(members_)), f"its forming: {type(e).__name__}: {e}"))
            log(f"text index: the forming of {', '.join(sorted(members_))} could not be finished "
                f"({type(e).__name__}: {e}); left for the next boot")
    return done


# ---- the boot -----------------------------------------------------------------------

def _drop_gone(root: Path, collections: list, log: Callable[[str], None], report: dict[str, Any]) -> None:
    """A placement naming a directory that is gone: dropped, the member's
    marks reset (nothing describes them now: it is indexed again), said.
    Never aborts the boot."""
    for c in collections:
        pl = placement(root, c.name)
        if pl is None or (Path(root) / pl["dir"]).is_dir():
            continue
        try:
            reset = _reset_all(_manifest(root, c.name))
            placement_file(root, c.name).unlink(missing_ok=True)
            invalidate()
            report.setdefault("dropped", []).append({"collection": c.name, "dir": pl["dir"], "reset": reset})
            log(f"text index: {c.name}'s placement named {pl['dir']}, which is gone: dropped, its "
                f"{reset} indexed document(s) reset, to be indexed again")
        except Exception as e:   # noqa: BLE001 — one collection's, never the boot's
            report["skipped"].append((c.name, f"its gone placement {pl['dir']}: {type(e).__name__}: {e}"))
            log(f"text index: {c.name}'s placement names {pl['dir']}, which is gone, and it could not "
                f"be dropped: {e}")


def _busy(root: Path, name: str) -> dict | None:
    """Who else holds `name`'s write lock (`lock.holder`: the worker, a
    `hold` command, a sister being made and carried), or None."""
    from .hold import lock as lock_mod
    try:
        return lock_mod.holder(lock_mod.state_dir(Path(root), name))
    except OSError:
        return None


def _homes(root: Path, ident: dict[str, str], contents: dict[str, str | None]
           ) -> tuple[dict[str, tuple[str, str]], set[str], set[str]]:
    """Which folder each identity KEEPS, who LEAVES, and the folders nothing
    moves into or out of this boot.

    A folder stays its WRITER's: the members of the writer's identity stay;
    every other member leaves (`_leave`). A writer whose index no longer
    describes how it indexes (its content key) leaves too, and the next
    member by name writes, as `writer()` falls back — leaving in turn if its
    own no longer does. `{identity: (folder, writer)}`, the fullest folder
    where two hold one identity. A folder one of whose members this boot
    does not settle (another process holds it, it has no manifest yet) is
    FROZEN: its writer may be at work."""
    home: dict[str, tuple[str, str]] = {}
    frozen: set[str] = set()
    leaving: set[str] = set()
    cands = []
    folders = sorted({pl["dir"] for n in ident if (pl := placement(root, n)) is not None
                      and is_shared(pl["dir"]) and (Path(root) / pl["dir"]).is_dir()})
    for rel in folders:
        ms = sorted(members(root, rel))
        if any(m not in ident for m in ms):
            frozen.add(rel)
            continue
        was = writer(root, rel, ms)
        if was is None:
            continue
        w = None
        for m in [was, *[m for m in ms if m != was]]:
            if _content_valid(root, m, contents.get(m)):
                w = m
                break
            leaving.add(m)
        if w is None:
            continue
        leaving.update(m for m in ms if m not in leaving and ident[m] != ident[w])
        cands.append((rel, w))
    for rel, w in sorted(cands, key=lambda t: (-_indexed_count(_manifest(root, t[1])), t[0])):
        home.setdefault(ident[w], (rel, w))
    return home, frozen, leaving


def settle(root: Path, collections: list, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Place every held collection's text index by its identity — the
    worker's boot, before any stage (module docstring). Idempotent; a
    forming by rename is journaled and finished forward (`resume_forming`).

    - A collection another process holds (`lock.holder`), or with no
      manifest yet (made, its carry not done), is not settled this boot,
      said; nothing moves in or out of a folder it is placed in.
    - A placement naming a directory that is gone: dropped, marks reset.
    - A folder in use STAYS ITS WRITER'S (`_homes`): the members of the
      writer's identity stay, retagged; a newcomer of that identity joins
      through `_place`'s reconciliation.
    - A member whose identity diverges from its writer's — a writer whose
      content key changed among them — LEAVES (`_leave`): every mark reset,
      said; nothing copied. It is placed with its new identity's group
      where there is one (a reader: its writer's marks are copied back),
      else in a new folder — formed by rename of a newcomer's own index
      where one has groups, otherwise EMPTY, indexed again by the worker.
    - An identity of two or more with no folder: the same.
    - One member on its own `<name>/index`: untouched.

    Each identity, and each journal, that fails is said and the rest goes
    on. A folder some held collection is placed in is never set aside."""
    root = Path(root)
    report: dict[str, Any] = {"groups": [], "freed": 0, "skipped": [], "left": []}
    mine = []
    for c in collections:
        who = _busy(root, c.name)
        if who is not None:
            report["skipped"].append((c.name, f"another process holds it ({who.get('what', '?')})"))
            log(f"text index: {c.name} not settled this boot: another process holds it "
                f"({who.get('what', '?')}, pid {who.get('pid', '?')})")
        elif not _manifest(root, c.name).is_file():
            report["skipped"].append((c.name, "no manifest yet (made, its carry not done)"))
            log(f"text index: {c.name} not settled this boot: it has no manifest yet (made, its carry "
                f"not done)")
        else:
            mine.append(c)
    names = {c.name for c in mine}
    resume_forming(root, log, own=lambda ms: ms <= names, report=report)
    _drop_gone(root, mine, log, report)
    ident: dict[str, str] = {}
    contents: dict[str, str | None] = {}
    for c in mine:
        try:
            i = identity(c)
            contents[c.name] = content_key(c)
        except Exception as e:   # noqa: BLE001 — one collection's, not the boot's
            report["skipped"].append((c.name, f"{type(e).__name__}: {e}"))
            continue
        if i is not None:
            ident[c.name] = i
    invalidate()
    by_ident: dict[str, list[str]] = {}
    for n, i in ident.items():
        by_ident.setdefault(i, []).append(n)
    home, frozen, leaving = _homes(root, ident, contents)
    for i, ns in sorted(by_ident.items()):
        ns = sorted(ns)
        stuck = sorted({d for n in ns if (d := (placement(root, n) or {}).get("dir")) in frozen})
        if stuck:
            report["skipped"].append((",".join(ns), f"placed in {', '.join(stuck)}, which a member this "
                                                    f"boot does not settle is placed in"))
            log(f"text index: {', '.join(ns)} not settled this boot: {', '.join(stuck)} has a member "
                f"another process holds or that has no manifest yet")
            continue
        placed = [n for n in ns if is_shared((placement(root, n) or {}).get("dir", ""))]
        if i not in home and len(ns) == 1 and not placed:
            continue                                  # its own index, untouched
        try:
            for n in sorted(set(ns) & leaving):
                _leave(root, n, log)
                report["left"].append(n)
            if i in home:
                how, (rel, w) = "keep", home[i]
                if writer(root, rel) != w:
                    _write_json(root / rel / WRITER, {"writer": w, "since": int(time.time())}, durable=True)
                _tag(root, rel, i, contents.get(w))
                for n in [w, *[m for m in ns if m != w]]:
                    _place(root, n, rel, i, contents.get(n), w, log)
            else:
                own = [n for n in ns if n not in placed and group_dirs(Path(root) / n / "index")]
                src = _fullest(root, own)
                if src is not None:
                    how = "rename"
                    rel = _form(root, source=root / src / "index", ident=i, content=contents.get(src),
                                writer_name=src, place=ns, log=log)
                else:
                    how = "new"
                    rel = _empty_group(root, i, contents, ns, log)
            invalidate()
            report["groups"].append({"identity": i, "members": ns, "dir": rel,
                                     "writer": writer(root, rel), "how": how})
        except Exception as e:   # noqa: BLE001 — one identity's, said; the rest goes on
            invalidate()
            report["skipped"].append((",".join(ns), f"{type(e).__name__}: {e}"))
            log(f"text index: {', '.join(ns)} not settled this boot ({type(e).__name__}: {e})")
    invalidate()
    report["orphans"] = orphans(root)
    for o in report["orphans"]:
        log(f"text index: {o['dir']} is placed by no held collection ({o['bytes'] / 2**30:.2f} GiB); "
            f"`hold collection forget` of a group's last member removes its directory")
    return report


def _fullest(root: Path, names: Iterable[str]) -> str | None:
    names = sorted(names)
    return min(names, key=lambda m: (-_indexed_count(_manifest(root, m)), m)) if names else None


def orphans(root: Path) -> list[dict[str, Any]]:
    """Shared directories no held collection is placed at."""
    root = Path(root)
    out = []
    shared_root = root / SHARED
    for d in sorted(shared_root.iterdir()) if shared_root.is_dir() else []:
        if not d.is_dir() or d.name.endswith(".forming") or d.name == ".forming":
            continue
        rel = f"{SHARED}/{d.name}"
        if d.name.startswith(".stale-") or not members(root, rel):
            out.append({"dir": rel, "bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file())})
    return out


def form_at_carry(root: Path, from_name: str, to_name: str, ident_from: str | None,
                  ident_to: str | None, *, content: str | None = None) -> str | None:
    """A sister carried from `from_name` (`hold collection new --sister-of`,
    case J), their identities equal, joins her sister's index NOW. Her
    sister placed in a group already: she JOINS it through `_place`'s
    reconciliation (her marks against its writer's; its writer kept). Her
    sister on her own: a NEW group is formed (`_form`, journaled): her
    sister's group directories RENAMED in, both placed, her sister its
    writer. A forming of theirs a cut left is finished first, unless it
    names a collection another process runs (the worker): then nothing is
    done and WriterHeld says so. Another's journal is not hers and is left
    alone. Raises WriterHeld, renaming nothing, when another process holds
    one of her sister's index writers (the running worker: `louise node
    stop` first). Returns the folder, or None: their identities differ."""
    if ident_from is None or ident_from != ident_to:
        return None
    root = Path(root)
    invalidate()
    ours = {from_name, to_name}
    for ms in open_journals(root):
        run = sorted(m for m in ms - ours if _busy(root, m) is not None) if ms & ours else []
        if run:
            raise WriterHeld(f"an unfinished forming of {from_name}'s text index also names {', '.join(run)}, "
                             f"which another process runs (the worker): it is not finished from here, and "
                             f"nothing was renamed. `louise node stop`, then run this again (CLAUDE.md RULE 1)")
    resume_forming(root, lambda m: None, own=lambda ms: bool(ms & ours))
    pl = placement(root, from_name)
    if pl is not None and (root / pl["dir"]).is_dir():
        w = writer(root, pl["dir"]) or from_name
        _place(root, to_name, pl["dir"], ident_to, content, w, lambda m: None)
        return pl["dir"]
    held = writer_held(root / from_name / "index")
    if held is not None:
        raise WriterHeld(f"{from_name}'s text index ({held}) has its writer held by another process (the "
                         f"running worker keeps its index writers for its life): forming {from_name} and "
                         f"{to_name}'s shared index renames it, so nothing was renamed. `louise node "
                         f"stop`, then run this again (CLAUDE.md RULE 1)")
    return _form(root, source=root / from_name / "index", ident=ident_from, content=content,
                 writer_name=from_name, place=[from_name, to_name], log=lambda m: None)


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
    if not is_shared(rel):
        return {"dir": str(d), "bytes": 0, "kept": []}    # not a shared directory: never removed here
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
