"""A LARGE annotations snapshot's claim index, on disk (docs/notes/
wikipedia-one-version.md, "Measured: the size": wikipedia-articles-cache holds
12.3 M claims; the in-memory `annotations.ClaimIndex` holds every claim as a
Python object in the web process — fine for gutenberg's thousands, not for
millions).

The SAME interface as `ClaimIndex` — `snapshot_id`, and `by_target` /
`graph` answering `.get(uid)` with the `Claim`s naming that record — so its
users (app/ask/works.py, app/ask/annotations.py) do not change. Built once per
snapshot (`build`), DERIVED and re-creatable from the held parts, so it lives
under LOUISE_CACHE_DIR (app/derived.py; invariant 6), keyed on the shard
tree's path and the snapshot's hash:

- `end_hash.npy`: every claim end (`annotations.record_ends`), as the first 8
  bytes of its blake2b, sorted;
- `end_kind.npy`: 0 a work claim (`WORK_TYPES`), 1 a graph claim;
- `end_part.npy`, `end_frame.npy`, `end_line.npy`: where that claim's record
  sits in the snapshot's parts (09 §9.5's pack index: part, frame, line);
- `parts.json`: the parts' paths, in `end_part`'s numbering; `meta.json`: the
  snapshot hash, the counts;
- THE SEEK TABLE (2026-11-22, the operator's choice B): every part's frame
  table as fixed-width arrays, `frame_offset`, `frame_csize`, `frame_rsize`
  (int64) and `frame_lines` (int32), all parts end to end, `part_frame_start`
  (int64, a part's first row, one past the last at the end) and `part_bytes`
  (int64, the part's size, checked before it is read); `seek.json` written
  last. A lookup finds its frame there and reads it (`pack_records.
  read_frame_at`): no part's pack index is decoded in the process.

A lookup hashes the uid, finds its run in `end_hash` (a binary search on a
memory-mapped array), reads each claim's frame (one pread + one decompress,
cached), and keeps a claim only if the uid IS one of its ends — so a hash
collision can never put a claim on a record it does not name.

THE SEEK TABLE'S STANDARD: the zstd seekable format's seek table
(contrib/seekable_format: fixed-width little-endian entries of compressed and
decompressed sizes, read without decoding), here with absolute offsets so a
row is found without a sum, and Parquet's OffsetIndex for the same purpose.
It replaces what LevelDB does (each open table's whole index held in memory,
bounded by a file count) by what RocksDB's partitioned index does (only the
slice a lookup needs is touched). It is DERIVED, so it lives where the rest
of this index lives, under LOUISE_CACHE_DIR (ssd5 on the node, the fast
cache, app/derived.py; invariant 6), mmapped: its pages sit in the page
cache, reclaimable, never in the web's heap (the operator, 2026-11-22: ssd5
for what would otherwise be held in RAM). A part's index was kept decoded
before: +485 MiB a question (München, the same day).

STANDARD NOT ADOPTED, written down (CLAUDE.md): a key-value store (LMDB,
RocksDB) would serve the same lookups; sorted NumPy arrays memory-mapped are
what this code base already uses for its derived maps (app/embed_select.py,
the vector packs), need no new dependency, and suffice for a read-only
snapshot. Worth revisiting if annotations become writable in place.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from app import derived

KIND = "annotation-claim-index"
#: a snapshot with more claims than this is searched through the disk index;
#: one at or under it keeps the in-memory `ClaimIndex` (gutenberg's thousands)
LARGE = 250_000
WORK, GRAPH = 0, 1
ARRAYS = ("end_hash", "end_kind", "end_part", "end_frame", "end_line")
#: the seek table's arrays (module docstring); `seek.json` says they are whole
SEEK = ("frame_offset", "frame_csize", "frame_rsize", "frame_lines", "part_frame_start", "part_bytes")
SEEK_VERSION = 1
#: how often an index without its seek table looks for the worker's, seconds
SEEK_LOOK_S = 60.0
#: FRAMES KEPT DECOMPRESSED, per process, by the bytes their lines hold: 256
#: frames of 1,000 claims each held ~88 MiB in the web (München, 2026-11-22,
#: tracemalloc between two questions; operator: the web "should be less than
#: 1GB"). A frame read again costs one pread + one decompress (~1 ms)
FRAME_CACHE_BYTES = 32 << 20
#: PARTS WHOSE FRAME TABLE IS KEPT, per process, least recently used first out.
#: The table alone: a part's uid map is never kept (`_frame_table`)
INDEX_CACHE = 1024


def uid_hash(uid: str) -> int:
    return int.from_bytes(hashlib.blake2b(uid.encode("utf-8"), digest_size=8).digest(), "little",
                          signed=True)


def place(shard_tree: Path, snapshot_hash: str) -> Path:
    base = derived.place(KIND, Path(shard_tree))
    if base is None:
        base = Path(shard_tree).parent / f".{Path(shard_tree).name}.{KIND}"
    return base / snapshot_hash


def is_large(snap) -> bool:
    return snap is not None and int(getattr(snap, "records", 0) or 0) > LARGE


def index_part(path: str, gone: frozenset) -> dict[str, Any]:
    """A task of `build`: one part's claim ends — each end's hash and kind,
    and where its claim's record sits (frame, line) — as bytes, with the
    count of its claims; `gone`'s uids left out."""
    from app.hold import annotations as ann
    from app.pack import records as pack_records
    from array import array
    p = Path(path)
    index = pack_records.read_index(p)
    seek = _part_seek(p, index)
    h, k, fr, ln = array("q"), array("b"), array("i"), array("i")
    claims = 0
    for f in range(len(index.frames)):
        for line_no, raw in enumerate(pack_records.read_frame(p, index, f)):
            r = json.loads(raw)
            if r["uid"] in gone:
                continue
            c = ann.claim_of(r)
            kind = WORK if c["type"] in ann.WORK_TYPES else GRAPH
            claims += 1
            for end in dict.fromkeys(ann.record_ends(c)):
                h.append(uid_hash(end))
                k.append(kind)
                fr.append(f)
                ln.append(line_no)
    return {"hash": h.tobytes(), "kind": k.tobytes(), "frame": fr.tobytes(), "line": ln.tobytes(),
            "claims": claims, "seek": seek}


def _part_seek(p: Path, index) -> dict[str, Any]:
    """One part's rows of the seek table: each frame's offset, compressed and
    raw size and lines, and the part's size. A Parquet part's row groups have
    no byte offsets here: their sizes are -1, and such a part is read through
    its own index (`DiskClaimIndex._lines`)."""
    from array import array
    off, cs, rs, n = array("q"), array("q"), array("q"), array("i")
    parquet = str(p).endswith(".parquet")
    for frame in index.frames:
        off.append(-1 if parquet else int(frame[0]))
        cs.append(-1 if parquet else int(frame[1]))
        rs.append(-1 if parquet else int(frame[2]))
        n.append(int(frame[3]))
    return {"offset": off.tobytes(), "csize": cs.tobytes(), "rsize": rs.tobytes(), "lines": n.tobytes(),
            "bytes": int(p.stat().st_size)}


def part_seek(path: str) -> dict[str, Any]:
    """A task of `add_seek`: one part's rows of the seek table."""
    from app.pack import records as pack_records
    p = Path(path)
    return _part_seek(p, pack_records.read_index(p))


def _write_seek(dest: Path, seeks: list[dict[str, Any]]) -> None:
    """The seek table's arrays, then `seek.json`, each written whole."""
    cat = (lambda key, t: np.concatenate([np.frombuffer(s[key], dtype=t) for s in seeks])
           if seeks else np.zeros(0, dtype=t))
    counts = [len(s["lines"]) // 4 for s in seeks]
    got = {"frame_offset": cat("offset", np.int64), "frame_csize": cat("csize", np.int64),
           "frame_rsize": cat("rsize", np.int64), "frame_lines": cat("lines", np.int32),
           "part_frame_start": np.concatenate([[0], np.cumsum(counts, dtype=np.int64)]).astype(np.int64),
           "part_bytes": np.asarray([s["bytes"] for s in seeks], dtype=np.int64)}
    for name in SEEK:
        tmp = dest / f"{name}.tmp.npy"
        np.save(tmp, got[name])
        tmp.replace(dest / f"{name}.npy")
    tmp = dest / "seek.json.tmp"
    tmp.write_text(json.dumps({"version": SEEK_VERSION, "parts": len(seeks), "frames": int(sum(counts))}))
    tmp.replace(dest / "seek.json")


def has_seek(dest: Path) -> bool:
    try:
        return json.loads((Path(dest) / "seek.json").read_text()).get("version") == SEEK_VERSION
    except (OSError, ValueError):
        return False


def add_seek(shard_tree: Path, dest: Path, *, stop=None) -> dict[str, Any]:
    """The seek table for an index built without one (before 2026-11-22):
    one pack index read a part, on the parse pool, the claims untouched
    (RULE 1: the arrays already built are kept). `stop()` between parts
    ends it with nothing written."""
    from app import parallel
    dest = Path(dest)
    if has_seek(dest):
        return {"skipped": "has its seek table", "place": str(dest)}
    parts = json.loads((dest / "parts.json").read_text())
    seeks: list[dict[str, Any]] = []
    results = parallel.each_part(part_seek, [(str(Path(shard_tree) / p),) for p in parts])
    for i, got in enumerate(results):
        if stop is not None and stop():
            results.close()
            return {"stopped": f"after {i} of {len(parts)} part(s)", "place": str(dest)}
        seeks.append(got)
    with derived.building(dest / "seek"):
        if not has_seek(dest):
            _write_seek(dest, seeks)
    return {"seek": len(parts), "place": str(dest)}


def build(shard_tree: Path, snap=None, *, stop=None, dest: Path | None = None) -> dict[str, Any]:
    """Write the index of `shard_tree`'s current snapshot (or `snap`) if it is
    not there yet. Returns what it did. Streams the parts, a part a task on
    the parse pool (`index_part`; CLAUDE.md, every pass over a collection
    uses the cores): memory is the arrays (~21 bytes an end), never the
    claims. `stop()`, asked between parts, ends it with nothing written
    (`{"stopped": …}`): the worker's stop (app/worker.py)."""
    from app import parallel
    from app.hold import snapshot as sn
    from app.share import seeder
    shard_tree = Path(shard_tree)
    snap = snap if snap is not None else seeder.cached_snapshot(shard_tree)
    if snap is None:
        return {"skipped": "no snapshot held"}
    dest = Path(dest) if dest is not None else place(shard_tree, snap.hash)
    if (dest / "meta.json").is_file():
        return {"skipped": "built", "place": str(dest)}
    # a FIRST snapshot tombstones nothing (`compute_delta(None, …)`), and its
    # delta lists every claim as added: 12.3 M uids, ~1 GB as strings, read
    # into the SHARED snapshot `cached_snapshot` gives, for good, had it been
    # asked. A later one's delta is read in a view of its own
    delta = snap.delta.fresh() if isinstance(snap.delta, sn.LazyDelta) else snap.delta
    gone = frozenset() if snap.previous is None else frozenset(t.uid for t in delta.tombstoned)
    del delta
    held = [s.path for s in snap.shards if (shard_tree / s.path).is_file()]   # a part not held: skipped
    tasks = [(str(shard_tree / path), gone) for path in held]
    hashes: list[np.ndarray] = []
    kinds: list[np.ndarray] = []
    parts_n: list[np.ndarray] = []
    frames_n: list[np.ndarray] = []
    lines_n: list[np.ndarray] = []
    claims = 0
    seeks: list[dict[str, Any]] = []
    results = parallel.each_part(index_part, tasks)
    for part_i, got in enumerate(results):
        if stop is not None and stop():
            results.close()                  # the pool's queued parts cancelled, its running ones awaited
            return {"stopped": f"after {part_i} of {len(tasks)} part(s)", "place": str(dest)}
        h = np.frombuffer(got["hash"], dtype=np.int64)
        hashes.append(h)
        kinds.append(np.frombuffer(got["kind"], dtype=np.int8))
        parts_n.append(np.full(len(h), part_i, dtype=np.int32))
        frames_n.append(np.frombuffer(got["frame"], dtype=np.int32))
        lines_n.append(np.frombuffer(got["line"], dtype=np.int32))
        seeks.append(got["seek"])
        claims += got["claims"]
    parts = held
    cat = (lambda xs, t: np.concatenate(xs) if xs else np.zeros(0, dtype=t))
    end_hash = cat(hashes, np.int64)
    order = np.argsort(end_hash, kind="stable")
    got = {"end_hash": end_hash[order], "end_kind": cat(kinds, np.int8)[order],
           "end_part": cat(parts_n, np.int32)[order], "end_frame": cat(frames_n, np.int32)[order],
           "end_line": cat(lines_n, np.int32)[order]}
    with derived.building(dest / "index"):
        if (dest / "meta.json").is_file():
            return {"skipped": "built", "place": str(dest)}
        dest.mkdir(parents=True, exist_ok=True)
        for name in ARRAYS:
            tmp = dest / f"{name}.tmp.npy"
            np.save(tmp, got[name])
            tmp.replace(dest / f"{name}.npy")
        (dest / "parts.json").write_text(json.dumps(parts))
        _write_seek(dest, seeks)
        meta = {"snapshot_id": snap.snapshot_id, "snapshot_hash": snap.hash, "claims": claims,
                "ends": int(len(end_hash)), "parts": len(parts)}
        tmp = dest / "meta.json.tmp"
        tmp.write_text(json.dumps(meta))
        tmp.replace(dest / "meta.json")
    return {**meta, "place": str(dest)}


def _build_child(tree: str, dest: str) -> None:
    """`build_isolated`'s process: the parent watched (a worker gone ends
    it), the build into the place the PARENT resolved (a child resolves
    nothing of its own: its configuration is the environment's, not the
    parent's), and an exit code that says whether it wrote or found one."""
    import sys
    from app import parallel
    parallel.watch_parent()
    got = build(Path(tree), dest=Path(dest))
    sys.exit(0 if ("ends" in got or got.get("skipped") == "built") else 3)


def build_isolated(shard_tree: Path, snap, *, stop=None, poll_s: float = 1.0) -> dict[str, Any]:
    """`build` in a PROCESS OF ITS OWN (2026-11-10, before the first deploy):
    the merge of the parts' arrays and its sort peak at about three copies of
    ~21 bytes an end — ~1.5 GB for wikipedia-articles-cache's 12.3 M claims — and in
    the worker's own process that would sit on top of its 1.6–3.8 GB under
    the container's 12 GiB cap, at every boot that finds an index missing.
    Here the peak lives and dies with a child; the worker keeps its size.
    `stop()`, asked every `poll_s`, terminates the child: nothing is
    published without its `meta.json`, which is written last, and the build
    lock (`derived.building`, a flock) goes with its holder."""
    import multiprocessing
    shard_tree = Path(shard_tree)
    dest = place(shard_tree, snap.hash)
    p = multiprocessing.get_context("spawn").Process(target=_build_child, args=(str(shard_tree), str(dest)),
                                                     name="claim-index-build", daemon=False)
    p.start()
    while True:
        p.join(poll_s)
        if p.exitcode is not None:
            break
        if stop is not None and stop():
            p.terminate()
            p.join(10)
            return {"stopped": "the worker stopped during the build", "place": str(dest)}
    if p.exitcode != 0 or not (dest / "meta.json").is_file():
        raise RuntimeError(f"the build's process exited {p.exitcode} and wrote no index")
    return {**json.loads((dest / "meta.json").read_text()), "place": str(dest)}


def maintain(root: Path, *, stop=None, log=print, failed: dict[str, str] | None = None,
             isolated: bool = False) -> list[dict[str, Any]]:
    """The index of every annotations collection held under `root` whose
    current snapshot is LARGE and has none yet — one adopted from a peer, or
    made before `create` and `update` built it (2026-11-10): without it its
    claims group nothing at search (`annotations.index_of`). Idempotent (a
    built one is skipped, by `build` too); bounded — one pass, a collection
    at a time, `stop()` asked between parts, and a snapshot whose build
    failed is said once in `failed` and left until it changes. The worker's
    keeper runs it at boot and when the files change (app/worker.py), each
    build `isolated` in a process of its own (`build_isolated`)."""
    from app import collections as collections_mod
    from app.hold import collection as col
    from app.share import seeder
    failed = {} if failed is None else failed
    out: list[dict[str, Any]] = []
    for c in collections_mod.load_annotations():
        if stop is not None and stop():
            break
        tree = col.shard_dir_of(Path(root), c.name)
        try:
            snap = seeder.cached_snapshot(tree)
        except Exception:   # noqa: BLE001 — an unreadable snapshot is its own check's to name
            continue
        if not is_large(snap):
            continue
        dest = place(tree, snap.hash)
        if (dest / "meta.json").is_file():
            # built before the seek table (2026-11-22): the table added, the
            # claims' arrays kept (RULE 1)
            if not has_seek(dest) and f"{tree}@{snap.hash}#seek" not in failed:
                try:
                    got = add_seek(tree, dest, stop=stop)
                except Exception as e:   # noqa: BLE001 — said once; the parts' own indexes serve meanwhile
                    failed[f"{tree}@{snap.hash}#seek"] = f"{type(e).__name__}: {e}"
                    log(f"[annotations] {c.name}: the claim index's seek table could not be written "
                        f"({e}); its parts are read through their own indexes")
                    continue
                if "stopped" in got:
                    break
                if "seek" in got:
                    log(f"[annotations] {c.name}: the claim index's seek table written ({got['seek']} parts): "
                        f"no part's index is decoded at search")
            continue
        key = f"{tree}@{snap.hash}"
        if key in failed:
            continue
        try:
            got = build_isolated(tree, snap, stop=stop) if isolated else build(tree, snap, stop=stop)
        except Exception as e:   # noqa: BLE001 — said once; the snapshot's next version is tried
            failed[key] = f"{type(e).__name__}: {e}"
            log(f"[annotations] {c.name}: the claim index of {snap.snapshot_id} could not be built "
                f"({failed[key]}); its claims group nothing at search until "
                f"`louise hold annotations index {c.name}` builds it")
            continue
        if "stopped" in got:
            break
        log(f"[annotations] {c.name}: claim index of {snap.snapshot_id} built ({got.get('claims', 0):,} claims, "
            f"{got.get('ends', 0):,} ends): searched from disk")
        out.append({"collection": c.name, **got})
    return out


class _Ends:
    """`by_target` / `graph` of a `DiskClaimIndex`: `.get(uid)` → its claims."""

    def __init__(self, index: "DiskClaimIndex", kind: int) -> None:
        self.index, self.kind = index, kind

    def get(self, uid: str, default: Iterable = ()) -> list:
        got = self.index.claims_of(uid, self.kind)
        return got if got else list(default)

    def __contains__(self, uid: str) -> bool:
        return bool(self.index.claims_of(uid, self.kind))


class DiskClaimIndex:
    """A built index, memory-mapped (module docstring)."""

    def __init__(self, shard_tree: Path, dest: Path, snapshot_id: str | None) -> None:
        self.shard_tree, self.dest, self.snapshot_id = Path(shard_tree), Path(dest), snapshot_id
        self.arrays = {name: np.load(dest / f"{name}.npy", mmap_mode="r") for name in ARRAYS}
        self.parts = json.loads((dest / "parts.json").read_text())
        #: the seek table, mmapped, where the index has one (else every part
        #: is read through its own pack index, the bounded caches below)
        self.seek = ({name: np.load(dest / f"{name}.npy", mmap_mode="r") for name in SEEK}
                     if has_seek(dest) else None)
        #: parts whose size was checked against the seek table: True when it
        #: matches, False when the part is read through its own index instead
        self._sized: dict[int, bool] = {}
        self._seek_looked = 0.0
        self.by_target = _Ends(self, WORK)
        self.graph = _Ends(self, GRAPH)
        self._lock = threading.Lock()
        #: (part, frame) -> (its lines, their bytes), within FRAME_CACHE_BYTES
        self._frames: OrderedDict = OrderedDict()
        self._frame_bytes = 0
        #: part -> its frame table, within INDEX_CACHE parts
        self._indexes: OrderedDict = OrderedDict()

    def _lines(self, part: int, frame: int) -> list[bytes]:
        """The lines of one frame of one part. BOUNDED (2026-11-22, München:
        five questions took the web to 4,060 MiB anon, 3,470 after a trim):
        every part's WHOLE pack index was kept for the process's life, its uid
        map of every record in the part included, +485 MiB a question decoded
        from JSON (`pack_records.read_index`). Now a part keeps its frame table
        alone, the least recently used parts go past INDEX_CACHE, and the
        frames kept are bounded by their bytes."""
        from app.pack import records as pack_records
        key = (part, frame)
        with self._lock:
            hit = self._frames.get(key)
            if hit is not None:
                self._frames.move_to_end(key)
                return hit[0]
            index = self._indexes.get(part)
            if index is not None:
                self._indexes.move_to_end(part)
        p = self.shard_tree / self.parts[part]
        row = self._seek_row(part, frame, p) if index is None else None
        if row is not None:
            lines = pack_records.read_frame_at(p, *row, k=frame)
            index = False                    # read through the seek table: no part index held
        elif index is None:
            index = _frame_table(pack_records.read_index(p))
            with self._lock:
                self._indexes[part] = index
                while len(self._indexes) > INDEX_CACHE:
                    self._indexes.popitem(last=False)
        if index is not False:
            lines = pack_records.read_frame(p, index, frame)
        size = _held_bytes(lines)
        with self._lock:
            if key not in self._frames:
                self._frames[key] = (lines, size)
                self._frame_bytes += size
            while self._frame_bytes > FRAME_CACHE_BYTES and len(self._frames) > 1:
                _, (_, gone) = self._frames.popitem(last=False)
                self._frame_bytes -= gone
        return lines

    def _seek_row(self, part: int, frame: int, p: Path) -> tuple[int, int, int, int] | None:
        """(offset, csize, rsize, lines) of the frame from the seek table, or
        None: no table, a Parquet part, or a part whose size is not the one
        the table was made from (read through its own index instead)."""
        s = self.seek
        if s is None:
            s = self._seek_now()
            if s is None:
                return None
        sized = self._sized.get(part)
        if sized is None:
            try:
                sized = int(p.stat().st_size) == int(s["part_bytes"][part])
            except OSError:
                sized = False
            self._sized[part] = sized
        if not sized:
            return None
        i = int(s["part_frame_start"][part]) + frame
        if i >= int(s["part_frame_start"][part + 1]) or int(s["frame_csize"][i]) < 0:
            return None
        return (int(s["frame_offset"][i]), int(s["frame_csize"][i]), int(s["frame_rsize"][i]),
                int(s["frame_lines"][i]))

    def _seek_now(self):
        """The seek table once the worker has added it (an index built before
        it): looked for at most every SEEK_LOOK_S, then kept."""
        import time
        now = time.monotonic()
        if now - self._seek_looked < SEEK_LOOK_S:
            return None
        self._seek_looked = now
        if not has_seek(self.dest):
            return None
        try:
            self.seek = {name: np.load(self.dest / f"{name}.npy", mmap_mode="r") for name in SEEK}
        except (OSError, ValueError):
            return None
        return self.seek

    def claims_of(self, uid: str, kind: int) -> list:
        from app.hold import annotations as ann
        a = self.arrays
        h = uid_hash(uid)
        lo = int(np.searchsorted(a["end_hash"], h, side="left"))
        hi = int(np.searchsorted(a["end_hash"], h, side="right"))
        out = []
        seen: set[tuple[int, int, int]] = set()
        for i in range(lo, hi):
            if int(a["end_kind"][i]) != kind:
                continue
            ref = (int(a["end_part"][i]), int(a["end_frame"][i]), int(a["end_line"][i]))
            if ref in seen:
                continue
            seen.add(ref)
            r = json.loads(self._lines(ref[0], ref[1])[ref[2]])
            c = ann.claim_of(r)
            if uid in ann.record_ends(c):        # a hash collision never names a record
                out.append(ann.Claim(r["uid"], c))
        return out


def _frame_table(index):
    """A part's pack index as a lookup needs it: its frames (offset, sizes,
    count, first uid), its uid map dropped. A lookup finds the frame from the
    built index's arrays, never from the part's uids; the map is every record
    of the part, the bulk of the index (wikipedia-articles: 12.3 M claims)."""
    import dataclasses
    return dataclasses.replace(index, uids={})


def _held_bytes(lines: list[bytes]) -> int:
    """What a frame's lines hold in memory: their bytes and each object's
    own overhead (CPython: 33 bytes a bytes object, 8 a list slot)."""
    return sum(len(line) for line in lines) + 41 * len(lines) + 56


def load(shard_tree: Path, snap) -> DiskClaimIndex | None:
    """The built index of `snap`, or None when it is not built yet."""
    dest = place(shard_tree, snap.hash)
    if not (dest / "meta.json").is_file():
        return None
    try:
        return DiskClaimIndex(shard_tree, dest, snap.snapshot_id)
    except (OSError, ValueError):
        return None
