"""One run's ENTITY TABLE: an entity to its line in the run's pack
(wikidata parser 3; 09 §9.5 amended 2026-11-17, the operator's "Choice B"
and "Do the wikidata step as well"; app/keyed_origin.py).

Since parser 3 a record's `origin` names its ENTITY,
`wikidatawiki/entities/all.json.bz2#Q42`, read in the run its snapshot
names. Wikimedia publishes no index of the JSON dump (one bz2 stream of
~100 GB, one entity a line, seekable nowhere), so the node BUILDS one. The
run's pack (pack.py) already holds every line in dump order under its
ordinal, the manifest locator's member. This table maps each entity's
native id (ids.py: an item's number, a property's PROPERTY_BASE + n) to
that ordinal.

ONE PASS, ON THE CORES THE PROFILE ALLOWS: each chunk file's `native_id`
and `index` columns (no line decoded), read on threads — Arrow's reads let
the GIL go, and the parse pool's processes measured slower, shipping every
chunk's arrays back pickled (12 M entities in 120 chunk files: 4.2 / 4.0 /
4.0 s on 1 / 2 / 3 processes, 3.1 / 2.9 / 2.8 s on threads; the sort and
the write, one each, are most of it) — then sorted and written (app/keyed_origin.py `KeyTable`: int64 keys, row groups bisected,
one row group read per lookup). The table is a side table of the pack,
written whole into the pack's own directory under the pack's one writer
(`derived.writing`, the DUMP_PACK template), and so placed by
app/derived.py, keyed on the dump file's physical identity (`pack.pack_dir`).
It is fresh by its own metadata: the dump's size and mtime, the pack's
version and chunk count, TABLE_VERSION. Anything else rebuilds it.

NOT A BYTE RANGE. A bz2 stream cannot be entered at a byte, so a range
would be into the decompressed JSON (~1.5 TB), which no node keeps. The
pack is the run's lines kept by ordinal, so the ordinal is the place.
"""
from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

from app import derived
from app.keyed_origin import KeyTable, KeyTableError

FILE = "entities.parquet"
TABLE_VERSION = 1
ROW_GROUP = 65_536
TABLE = KeyTable("wikidata entity table", ("index",))
_lock = threading.Lock()
#: str(dump) -> (what the table must say, its path), the table found fresh:
#: a lookup pays a stat of the dump and a read of the pack's meta (both
#: memoised by pack.py), never the table's footer
_fresh: dict[str, tuple[dict, Path]] = {}


class EntityTableError(KeyTableError):
    """The run's pack lists an entity twice, or is not complete."""


def path_of(dump: Path) -> Path:
    from . import pack
    return pack.pack_dir(dump) / FILE


def _want(dump: Path) -> dict[bytes, bytes]:
    from . import pack
    meta = pack.read_meta(dump) or {}
    size, mtime = pack._signature(dump)
    return {b"louise.entity_table_version": str(TABLE_VERSION).encode(),
            b"louise.source_size": str(size).encode(), b"louise.source_mtime_ns": str(mtime).encode(),
            b"louise.pack_version": str(meta.get("pack_version")).encode(),
            b"louise.pack_chunks": str(meta.get("chunks")).encode()}


def is_fresh(dump: Path) -> bool:
    import pyarrow.parquet as pq
    p = path_of(dump)
    if not p.is_file():
        return False
    try:
        got = pq.read_metadata(p).metadata or {}
    except Exception:   # noqa: BLE001 — a truncated table is simply stale
        return False
    return all(got.get(k) == v for k, v in _want(dump).items())


def columns_of(path: str):
    """One chunk file's (native ids, ordinals) — a pool task."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=["native_id", "index"])
    return (np.asarray(pc.cast(t.column("native_id"), pa.int64()).to_numpy(), dtype=np.int64),
            np.asarray(t.column("index").to_numpy(), dtype=np.int64))


#: THE PACKED SORT (2026-11-17, measured): an entity as ONE int64, its native
#: id in the high half and its ordinal in the low, sorted in place. The
#: generic `KeyTable.tables` kept every chunk's two arrays beside their
#: concatenation, then the sort's order and both sorted copies: ~60 bytes an
#: entity at the peak (a fresh process's VmHWM at 4 / 8 / 16 M entities:
#: 451 / 701 / 1,174 MB), ~7.2 GB for 118 M, held by whichever process made
#: the first lookup — a pool child of `hold check`, beside the worker's own
#: memory when run by `docker exec` (München, 2026-11-17: the memcg OOM).
#: Packed: 260 / 308 / 492 MB, ~23 bytes an entity, ~2.8 GB for 118 M, and
#: 4.2 s against 6.4 s at 16 M. The table is the same bytes
#: (tests/test_wikidata_entity_table.py). Keys are unique, so sorting the
#: packed values sorts by key. The standard, not adopted: an external merge
#: sort of sorted runs bounds the memory whatever N, and would pay at a few
#: billion entities; at 118 M one array fits. Nor a DIRECT-ADDRESS table (a
#: counting sort: an item's number indexes a uint32 array of ordinals, 4 bytes
#: for every number to the largest item, ~0.57 GB at Q141 M, no sort), which
#: would bound the build at a quarter of this one's — worth it when the build
#: must share a process with a pool. Re-measured 2026-11-17 (a fresh
#: process, 3 threads, 4 / 8 / 16 M entities): packed 274 / 331 / 444 MiB in
#: 1.1 / 1.9 / 4.0 s, generic 395 / 617 / 1,049 MiB in 1.4 / 2.9 / 5.1 s — a
#: marginal 14.2 against 54 bytes an entity, ~1.9 against ~6.6 GB at 118 M
#: (docs/notes/wikidata-entity-origin.md, "The check's memory").
_SHIFT = 32
_LOW = (1 << _SHIFT) - 1
#: a native id below this fits the high half (an item's number, a property's
#: PROPERTY_BASE + n, ids.py): 2^31 keeps the packed value positive
_KEY_LIMIT = 1 << 31
#: the duplicate check's window: two shifted copies of this many values
_BLOCK = 1 << 22
#: how long a lookup waits for another process building the table or the
#: pack (`derived.writing` refuses at once): every pool child of a check
#: reaches its first lookup together, and one builds while the rest wait
#: (bounded, CLAUDE.md: then deferred — `derived.Busy` — never a failure)
TABLE_WAIT_S = 600.0


def packed_of(path: str):
    """One chunk file's entities packed (`_SHIFT`), or None where a value
    does not fit — the generic sort takes the table then. A pool task."""
    import numpy as np
    nid, idx = columns_of(path)
    if len(nid) and (int(nid.min()) < 0 or int(nid.max()) >= _KEY_LIMIT
                     or int(idx.min()) < 0 or int(idx.max()) > _LOW):
        return None
    return (nid << np.int64(_SHIFT)) | idx


def _packed_tables(packed, *, where: str, row_group: int):
    """`KeyTable.tables`' tables from the packed values, sorted in place."""
    import numpy as np
    import pyarrow as pa
    c = np.concatenate(packed) if packed else np.zeros(0, np.int64)
    packed.clear()
    c.sort()
    for i in range(0, max(len(c) - 1, 0), _BLOCK):
        hi = c[i:i + _BLOCK + 1] >> np.int64(_SHIFT)
        twice = np.flatnonzero(hi[1:] == hi[:-1])
        if len(twice):
            raise KeyTableError(f"{where}: key {int(hi[twice[0]])} is listed twice; a key names one unit")
    schema = TABLE.schema()
    for i in range(0, max(len(c), 1), row_group):
        g = c[i:i + row_group]
        yield pa.table({"key": g >> np.int64(_SHIFT), "index": g & np.int64(_LOW)}, schema=schema)


def build(dump: Path, *, workers: int | None = None, mode: str = "threads") -> Path:
    """The table of `dump`'s pack: one pass over its chunk files on the pool,
    written whole under the pack's writer. Raises `derived.Busy` when
    another process writes the pack (deferred, as a pack build is)."""
    import pyarrow.parquet as pq

    from app import parallel

    from . import pack
    meta = pack.read_meta(dump) or {}
    if not meta.get("complete"):
        raise EntityTableError(f"{Path(dump).name}: its pack is not complete; the entity table is built from it")
    d = pack.pack_dir(dump)
    with derived.writing(d / pack.WRITER, "the wikidata entity table"):
        if is_fresh(dump):
            return path_of(dump)
        files = [str(p) for p in pack.chunk_files(dump)]

        def read(fn):
            if mode == "processes":
                return list(parallel.each_part(fn, [(f,) for f in files], limit=workers))
            # THREADS, measured (2026-11-17, 12 M entities in 120 chunk files):
            # the pool's processes ship every chunk's arrays back pickled and
            # lost to one process (4.4 s in-process, 5.1 s on 2 processes,
            # 4.8 s on 3); a chunk's read is Arrow's, which lets the GIL go
            from concurrent.futures import ThreadPoolExecutor
            n = max(1, int(workers or parallel.processes()))
            with ThreadPoolExecutor(max_workers=n, thread_name_prefix="wikidata-entities") as ex:
                return list(ex.map(fn, files))
        packed = read(packed_of)
        if any(p is None for p in packed):
            packed = None
            tables = _generic_tables(read(columns_of), where=Path(dump).name)
        else:
            tables = _packed_tables(packed, where=Path(dump).name, row_group=ROW_GROUP)
        schema = TABLE.schema().with_metadata(_want(dump))
        out = path_of(dump)
        tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
        w = pq.ParquetWriter(tmp, schema, compression="zstd", compression_level=3)
        try:
            try:
                for t in tables:
                    w.write_table(t.replace_schema_metadata(schema.metadata))
            except KeyTableError as e:
                raise EntityTableError(f"{e} (an entity is one line of the dump)") from None
            w.close()
            os.replace(tmp, out)
        except BaseException:
            with contextlib.suppress(Exception):   # the writer's own failure is not the one raised
                w.close()
            tmp.unlink(missing_ok=True)
            raise
        TABLE.forget(out)
        return out


def _generic_tables(got, *, where: str):
    """`KeyTable.tables` over the chunks' (native ids, ordinals): a value
    the packed sort cannot hold."""
    import numpy as np
    k = np.concatenate([a for a, _ in got]) if got else np.zeros(0, np.int64)
    v = np.concatenate([b for _, b in got]) if got else np.zeros(0, np.int64)
    got.clear()
    return TABLE.tables(k, {"index": v}, where=where, row_group=ROW_GROUP)


def ensure(dump: Path, *, wait_s: float | None = None) -> Path:
    """The table of `dump`'s pack, built when missing or stale — or, while
    another process writes the pack or the table, waited for at most
    `wait_s` (TABLE_WAIT_S), then deferred (`derived.Busy`)."""
    import time
    key, want = str(dump), _want(dump)
    with _lock:
        hit = _fresh.get(key)
    if hit is not None and hit[0] == want and hit[1].is_file():
        return hit[1]
    deadline = time.monotonic() + (TABLE_WAIT_S if wait_s is None else wait_s)
    while True:
        if is_fresh(dump):
            p = path_of(dump)
            break
        try:
            p = build(dump)
            break
        except derived.Busy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    with _lock:
        _fresh[key] = (want, p)
    return p


def reset() -> None:
    with _lock:
        _fresh.clear()
    TABLE._groups.clear()
    with TABLE._lock:
        TABLE._bounds.clear()


def index_of(dump: Path, native_id) -> int | None:
    """The ordinal of entity `native_id` in `dump`'s pack, or None when the
    run does not hold it."""
    got = TABLE.lookup(ensure(Path(dump)), int(native_id))
    return None if got is None else got[0]
