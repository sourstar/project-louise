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


def build(dump: Path, *, workers: int | None = None, mode: str = "threads") -> Path:
    """The table of `dump`'s pack: one pass over its chunk files on the pool,
    written whole under the pack's writer. Raises `derived.Busy` when
    another process writes the pack (deferred, as a pack build is)."""
    import numpy as np
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
        keys, values = [], []
        files = [str(p) for p in pack.chunk_files(dump)]
        if mode == "processes":
            got = parallel.each_part(columns_of, [(f,) for f in files], limit=workers)
        else:
            # THREADS, measured (2026-11-17, 12 M entities in 120 chunk files):
            # the pool's processes ship every chunk's arrays back pickled and
            # lost to one process (4.4 s in-process, 5.1 s on 2 processes,
            # 4.8 s on 3); a chunk's read is Arrow's, which lets the GIL go
            from concurrent.futures import ThreadPoolExecutor
            n = max(1, int(workers or parallel.processes()))
            with ThreadPoolExecutor(max_workers=n, thread_name_prefix="wikidata-entities") as ex:
                got = list(ex.map(columns_of, files))
        for nid, idx in got:
            keys.append(nid)
            values.append(idx)
        k = np.concatenate(keys) if keys else np.zeros(0, np.int64)
        v = np.concatenate(values) if values else np.zeros(0, np.int64)
        schema = TABLE.schema().with_metadata(_want(dump))
        out = path_of(dump)
        tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
        w = pq.ParquetWriter(tmp, schema, compression="zstd", compression_level=3)
        try:
            try:
                for t in TABLE.tables(k, {"index": v}, where=Path(dump).name, row_group=ROW_GROUP):
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


def ensure(dump: Path) -> Path:
    """The table of `dump`'s pack, built when missing or stale."""
    key, want = str(dump), _want(dump)
    with _lock:
        hit = _fresh.get(key)
    if hit is not None and hit[0] == want and hit[1].is_file():
        return hit[1]
    p = path_of(dump) if is_fresh(dump) else build(dump)
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
