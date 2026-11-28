"""SISTERS SHARE ONE TEXT INDEX (operator, 2026-11-10: choice A, "one shared
text index, written by one sister, read by the others";
docs/notes/sister-shared-index.md, 02 §2.7 amended the same day). THE SHARE
IS STATIC (operator's default B, 2026-11-27, after six reads found the
automatic groups losing data): set once, at the sister's creation, and
never moved by the system after.

A sister whose INDEX IDENTITY (`identity`: the same records, layers,
update, held slices and index schema) equals her sister's, made by `hold
collection new --sister-of` (no node stop: it moves nothing), READS HER SISTER'S OWN
INDEX: her PLACEMENT, `<name>/state/text-index.json`, names `<sister>/index`
by path (`share`). Her sister indexes as every collection does and is its
only writer; she never writes it: her index stage copies her sister's marks
(`copy_marks`), so a document is indexed once on the node however many
sisters hold it. Her marks are reconciled once, at the share, against her
sister's (`reset_differing`).

WHERE A COLLECTION'S TEXT INDEX IS (`base`). `Collection.fts_dir` stays the
collection's own `<name>/index/` — its catalogue (`_catalog`, keyed on its
manifest's rowids, app/catalog.py) and its facets (app/ask/facets.py) stay
there, per collection, whatever is shared. Its language-group indexes are at
`base(fts_dir)`: that same directory, or the one its placement names. Every
process resolves it per use, from that file (cached on its mtime), and every
cache keyed on the path (fts.py's indexes and writers) is keyed on her
sister's directory: one tantivy writer per directory, whoever asks.

WHAT A READER COPIES: a document's marks (`fts_done`, `fts_version`,
`entity_version`, `chunks`, `tokens`) and its entity tags, from the
writer's manifest, where the writer holds the SAME RECORD (`same_record`:
the same `content_hash`, else `txt_hash`, else discovery signature) indexed. Driven by the writer's commits (`indexed`, pushed after
each fts commit) and by the reader's own rdf commits (`parsed`), plus one
walk of the reader's pending rows when its thread starts (the pushes are in
memory; a restart loses them). A document the writer has not indexed yet is
DEFERRED, never an error, and costs nothing per round.

NOTHING AUTOMATIC AT BOOT (`settle`): each placement is only CHECKED. Her
identity still equal to her sister's, her sister held: her text leg is
served from it. Otherwise it is WITHHELD, in words (`hold collection show`,
the search's withheld note): nothing is moved, copied or reset; `louise hold
collection unshare <name>` (her lock only) gives her an empty index of her
own, her marks reset, and the worker indexes her again. A forget of a
sister another reads hands its index to her when she is its only reader (a
RENAME: she writes it from then on), else is refused. A group folder an
earlier version made (`.text-index/`) is refused in words, never migrated.

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

#: a collection's placement, under its `state/`
PLACEMENT = "text-index.json"
#: the group folders an earlier version made, under the collections root:
#: refused in words, never migrated
EARLIER = ".text-index"
#: tantivy's lock files: never carried into a copy (a copy's locks are its
#: own writer's)
LOCK_SUFFIX = ".lock"
#: a reader's queue of uids to copy: beyond this, the queue is dropped and
#: a walk of its pending rows is due instead (memory stays bounded)
QUEUE_MAX = 500_000
#: documents copied per round, and rowids per walk window
COPY_BATCH = 2_000
WALK_WINDOW = 50_000
#: how long a reader list is trusted (a forget or a placement changes it)
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
        # the dated-dump run it holds (`collection.snapshot`, the one answer of
        # app/hold/origin_run.py): a writer moving to a new run withholds her
        # until her own update reaches it (the read of eef52262, 2)
        "run": getattr(collection, "snapshot", None),
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
    """`{"dir": "<sister>/index", "identity": …, "writer": <sister>,
    "withheld"?: why}`, or None: the collection's index is its own
    `<name>/index`. Read once per change of the file (its mtime)."""
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


def write_placement(root: Path, name: str, rel: str, ident: str | None, *, writer: str,
                    withheld: str | None = None) -> None:
    """Her placement, synced (the file, then its directory): `rel` is her
    sister's own index, `<writer>/index`; `withheld` the reason her text leg
    is withheld, as the boot's check found it."""
    _write_json(placement_file(root, name), {"dir": rel, "identity": ident, "writer": writer,
                                             **({"withheld": withheld} if withheld else {})},
                durable=True)
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
    return sorted(p for p in index_dir.iterdir()       # a hidden one is a copy under way
                  if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "meta.json").is_file())


# ---- readers and the writer -----------------------------------------------

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
    """The writer of `rel`, `<sister>/index`: her sister, while held. Static:
    it never passes to a reader (a forget hands the folder over, `hand_over`)."""
    w = Path(rel).parts[0] if rel and not str(rel).startswith(EARLIER + "/") else None
    return w if w is not None and _held(root, w) else None


def readers(root: Path, name: str) -> list[str]:
    """The held collections that read `name`'s own index, by name."""
    return members(root, f"{name}/index")


@dataclass(frozen=True)
class Role:
    reader: bool
    #: the writer's name and manifest, for a reader; None otherwise
    writer: str | None = None
    writer_manifest: Path | None = None
    #: the index directory she reads, relative to the root; None for her own
    shared: str | None = None
    members: tuple[str, ...] = ()
    #: why her text leg is withheld (the boot's check), or None
    withheld: str | None = None


ALONE = Role(reader=False)


def _root_of(collection) -> Path | None:
    fts_dir = getattr(collection, "fts_dir", None)
    if fts_dir is None:
        return None
    return Path(fts_dir).parent.parent


def _withheld_of(name: str, pl: dict) -> str | None:
    if str(pl["dir"]).startswith(EARLIER + "/"):
        return (f"its text index is a group made by an earlier version ({pl['dir']}): not migrated. "
                f"`louise hold collection unshare {name}`")
    return pl.get("withheld")


def withheld_at(root: Path, name: str) -> str | None:
    """Why `name`'s text leg is withheld (its placement no longer holds, as
    the boot's check found), or None. One stat."""
    pl = placement(root, name)
    return _withheld_of(name, pl) if pl is not None else None


def withheld(collection) -> str | None:
    root = _root_of(collection)
    return withheld_at(root, collection.name) if root is not None else None


def unread(collection) -> str | None:
    """Why her text leg answers nothing, in words, or None: withheld (the
    check's reason), or the folder she reads gone — never a silent empty
    leg (the read of eef52262, 4)."""
    why = withheld(collection)
    root = _root_of(collection)
    pl = placement(root, collection.name) if root is not None and why is None else None
    if pl is not None and not (root / pl["dir"]).is_dir():
        why = f"{pl['dir']}, the index she reads, is gone: `louise hold collection unshare {collection.name}`"
    return why


def role(collection) -> Role:
    """Whether `collection` reads her sister's index (a placement)."""
    root = _root_of(collection)
    if root is None:
        return ALONE
    name = collection.name
    pl = placement(root, name)
    if pl is None:
        return ALONE
    w = pl.get("writer") or writer(root, pl["dir"]) or "?"
    return Role(reader=True, writer=w, writer_manifest=root / w / "state" / "manifest.sqlite",
                shared=pl["dir"], members=(w, *readers(root, w)), withheld=_withheld_of(name, pl))


def writes(collection) -> bool:
    """False for a reader: it never writes her sister's index (her sister
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
    """The writer committed these documents to its index: each sister that
    reads it copies their marks (after the commit: a reader reads committed
    rows only)."""
    if role(collection).reader:
        return
    root = _root_of(collection)
    rs = readers(root, collection.name) if root is not None else []
    uids = list(uids)
    if not rs or not uids:
        return
    for m in rs:
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


# ---- the share, made once; the boot's check --------------------------------

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
    from . import db
    con = sqlite3.connect(f"file:{manifest}?mode=rw", uri=True, timeout=60, isolation_level=None)
    db.wal(con)                       # invariant 6: every write-open of a manifest sets WAL
    return con


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


def share(root: Path, sister: str, name: str, ident: str, log: Callable[[str], None] = print) -> str:
    """`name`, carried from `sister` with the same index identity, READS her
    sister's own index from now on: her marks reconciled once against her
    sister's (`reset_differing`), any language group of her own removed (an
    earlier carry's copy, never read again), then her placement written,
    naming `<sister>/index`. Her sister reading another's, she reads that
    one's: a reader is never a writer. Nothing is moved and her sister's
    writer is never taken: no node stop; the caller holds HER lock."""
    root = Path(root)
    pl = placement(root, sister)
    w = (pl.get("writer") or writer(root, pl["dir"])) if pl is not None else sister
    reset = reset_differing(_manifest(root, name), _manifest(root, w), log=log)
    _remove_groups(root / name / "index")
    rel = f"{w}/index"
    write_placement(root, name, rel, ident, writer=w)
    log(f"text index: {name} reads {rel} (written by {w}); {reset} document(s) reset")
    return rel


def unshare(root: Path, name: str, log: Callable[[str], None] = print) -> dict[str, Any]:
    """`name` reads no other's index from now on: her placement dropped, an
    EMPTY `<name>/index` of her own (her catalogue kept), every fts mark of
    hers reset, said — the worker indexes her again. The caller holds HER
    lock; no node stop."""
    root = Path(root)
    pl = placement(root, name)
    if pl is None:
        return {"collection": name, "unshared": False, "says": "it reads no other collection's index"}
    reset = _reset_all(_manifest(root, name))
    own = root / name / "index"
    own.mkdir(parents=True, exist_ok=True)
    _remove_groups(own)
    placement_file(root, name).unlink()
    _fsync_dir(placement_file(root, name).parent)
    invalidate()
    says = (f"{name} no longer reads {pl['dir']}: its own empty index, its {reset} indexed document(s) "
            f"reset; the worker indexes it again")
    log(f"text index: {says}")
    return {"collection": name, "unshared": True, "was": pl["dir"], "reset": reset, "says": says}


def hand_over(root: Path, name: str, *, dry_run: bool = False) -> dict[str, Any] | None:
    """`name` forgotten while ONE sister reads its index (the forget refuses
    more, or a reader whose placement is withheld): its language groups
    RENAMED into hers, `<reader>/index`, and her placement dropped — she
    writes it from now on, her marks already its writer's. Repeatable at any
    cut (a group already moved is skipped). None: nobody reads it."""
    root = Path(root)
    invalidate()
    rs = readers(root, name)
    if not rs:
        return None
    r = rs[0]
    src, dst = root / name / "index", root / r / "index"
    groups = [g.name for g in group_dirs(src)]
    out: dict[str, Any] = {"to": r, "groups": groups, **({"dry_run": True} if dry_run else {})}
    if dry_run:
        return out
    dst.mkdir(parents=True, exist_ok=True)
    for g in groups:
        if (dst / g).exists() and not (dst / g / "meta.json").is_file():
            # a directory of that name that is no index (an empty one a read
            # made, a copy cut short): moved aside, never in the group's way
            os.rename(dst / g, dst / f".{g}.aside-{time.time_ns()}")
        if not (dst / g).exists():
            os.rename(src / g, dst / g)
    # what her marks claim must be in the folder she now writes: a group they
    # name that is not there (moved from under her by a carry, say) and every
    # mark is reset, said — the worker indexes her again
    claimed = _marked_groups(_manifest(root, r))
    missing = sorted(claimed - {g.name for g in group_dirs(dst)})
    if missing:
        with _her_lock(root, r, "forget: the text index handed to her") as mine:
            if not mine:
                from .hold.lock import Held
                raise Held(r, {"what": "another process"}, exit_code=3)
            out["reset"] = _reset_all(_manifest(root, r))
        out["says"] = (f"{r}'s marks name {', '.join(missing)}, not in the index it receives: its "
                       f"{out['reset']} indexed document(s) reset, to be indexed again")
    placement_file(root, r).unlink(missing_ok=True)
    _fsync_dir(placement_file(root, r).parent)
    invalidate()
    return out


def _marked_groups(manifest: Path) -> set[str]:
    """The language groups a manifest's indexed documents of one language
    are in (a multilingual record's sections may be in several: it claims
    none for certain)."""
    from . import fts
    try:
        con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True, timeout=30)
        try:
            langs = [row[0] for row in con.execute("SELECT DISTINCT lang FROM documents WHERE fts_done=1")]
        finally:
            con.close()
    except sqlite3.Error:
        return set()
    return {fts.group_for(lang) for lang in langs if (lang or "") != fts.MULTILINGUAL}


def settle(root: Path, collections: list, *, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The worker's boot: NOTHING AUTOMATIC — each placement only CHECKED.
    Her identity still her sister's and her sister held: served. Otherwise
    WITHHELD, the reason written into her placement for every process to
    read (`withheld`) and said; nothing is moved, copied or reset. A group
    folder an earlier version made is refused in words, never migrated."""
    root = Path(root)
    report: dict[str, Any] = {"served": [], "withheld": []}
    earlier = root / EARLIER
    if earlier.is_dir() and any(d.is_dir() for d in earlier.iterdir()):
        report["earlier"] = str(earlier)
        log(f"text index: {earlier} holds groups made by an earlier version: not migrated; each "
            f"collection placed there is withheld until `louise hold collection unshare <name>`")
    by_name = {c.name: c for c in collections}
    for c in collections:
        if placement(root, c.name) is None:
            continue
        try:
            # HER LOCK while her placement is read and written: a concurrent
            # `unshare` (which holds it) is never undone; held by another
            # process, she is not re-checked now, said
            with _her_lock(root, c.name, "text index: the placement check") as mine:
                if not mine:
                    report.setdefault("skipped", []).append(c.name)
                    log(f"text index: {c.name}'s placement not re-checked now: another process holds it")
                    continue
                invalidate()
                pl = placement(root, c.name)
                if pl is None:
                    continue
                why = _why(root, c, pl, by_name)
                if why != pl.get("withheld") and not str(pl["dir"]).startswith(EARLIER + "/"):
                    write_placement(root, c.name, pl["dir"], pl.get("identity"),
                                    writer=pl.get("writer") or Path(pl["dir"]).parts[0], withheld=why)
        except Exception as e:   # noqa: BLE001 — that collection's, said; never the boot's
            why = f"its placement could not be checked ({type(e).__name__}: {e})"
        if why:
            report["withheld"].append((c.name, why))
            log(f"text index: {c.name}'s text leg withheld: {why}")
        else:
            report["served"].append(c.name)
    return report


class _her_lock:
    """`name`'s write lock for a short read-and-write of her state: taken
    when free, kept as it is when THIS process holds it (the worker runs
    her); `as` gives False when another process holds it (skip her)."""

    def __init__(self, root: Path, name: str, what: str) -> None:
        self.root, self.name, self.what, self.lock = Path(root), name, what, None

    def __enter__(self) -> bool:
        from .hold import lock as lock_mod
        state = lock_mod.state_dir(self.root, self.name)
        who = lock_mod.holder(state)
        if who is not None:
            return who.get("pid") == os.getpid()
        try:
            self.lock = lock_mod.take(state, self.name, self.what)
        except lock_mod.Held:
            return False
        return True

    def __exit__(self, *exc) -> None:
        if self.lock is not None:
            self.lock.release()


def not_held(why: str | None, sister: str) -> bool:
    """Whether `why` is the check's "her sister is not held" (a forget cut
    short), the one withheld reason a forget run again completes over."""
    return bool(why) and why.startswith(f"her sister {sister}, whose index she reads, is not held")


def _why(root: Path, c, pl: dict, by_name: dict) -> str | None:
    """Why her placement no longer holds, or None."""
    name = c.name
    if str(pl["dir"]).startswith(EARLIER + "/"):
        return _withheld_of(name, pl)
    w = pl.get("writer") or Path(pl["dir"]).parts[0]
    sister = by_name.get(w)
    if sister is None or not _held(root, w):
        return (f"her sister {w}, whose index she reads, is not held or not served here: `louise hold "
                f"collection unshare {name}`")
    if not (Path(root) / pl["dir"]).is_dir():
        return f"{pl['dir']}, the index she reads, is gone: `louise hold collection unshare {name}`"
    mine = identity(c)
    if mine is None or mine != identity(sister):
        return f"her index identity no longer matches {w}'s: `louise hold collection unshare {name}`"
    return None


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
    own (and who reads it), or her sister's — who writes it, a withheld
    text leg and why, a reader's wait on a paused writer, in a sentence."""
    invalidate()
    pl = placement(root, name)
    rs = readers(root, name)
    if pl is None:
        return {"shared": None, "readers": rs,
                "says": (f"its own, read by {', '.join(rs)}" if rs else
                         "its own (no sister with the same text index on this node)")}
    w = pl.get("writer") or writer(root, pl["dir"])
    why = _withheld_of(name, pl)
    paused = False
    try:
        from . import node_state
        paused = w in node_state.paused_collections()
    except Exception:   # noqa: BLE001 — the sentence goes without it
        pass
    says = (f"WITHHELD: {why}" if why else
            f"reads {pl['dir']}, written by {w}" + (", paused: this collection's indexing waits on it"
                                                    if paused else ""))
    return {"shared": pl["dir"], "identity": pl.get("identity"), "writer": w,
            "members": [w, *readers(root, w)] if w else [], "withheld": why, "writer_paused": paused,
            "says": says}
