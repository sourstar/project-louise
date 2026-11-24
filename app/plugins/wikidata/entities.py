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


#: THE DIRECT-ADDRESS TABLE (2026-11-17, the operator's performance rule: a
#: known speed-up is built, not written down). A COUNTING SORT: an item's
#: number indexes a uint32 array of ordinals, so the table is written by
#: walking the array in order — no sort, and 4 bytes for every number up to
#: the largest item (Q141 M: ~0.57 GB) where the packed sort held ~14 bytes an
#: entity (~1.9 GB at 118 M). Properties (P1 … P13 k, ids.py's PROPERTY_BASE +
#: n) are a small array of their own, so the large one spans items only. An
#: entity listed twice is found as it is placed (its slot already holds an
#: ordinal, or its chunk lists it twice). The table is the same bytes as the
#: generic sort's (tests/test_wikidata_entity_table.py), which still takes
#: a value the arrays cannot hold (a number past `_ITEM_LIMIT`, an ordinal
#: past `_NONE`). MEASURED (2026-11-17, a fresh process's VmHWM, 3 threads, a
#: synthetic pack of dense ids, its meta naming the largest item as a
#: version-3 pack's does): 227 / 241 / 287 MiB in 1.1 / 1.9 / 3.5 s at 4 / 8 /
#: 16 M entities, against the packed sort's 274 / 331 / 444 MiB in 1.1 / 1.9 /
#: 4.0 s — ~0.8 GB modelled at 118 M (Q141 M), against ~1.9 GB. A sort of each
#: chunk to find an entity twice in it (`np.unique`) was half the time; the
#: read-back below costs a gather (docs/notes/wikidata-entity-origin.md, "The
#: check's memory").
#:
#: THE ALTERNATIVES, WRITTEN DOWN. The PACKED SORT (until today): an entity
#: as one int64 — its id in the high half, its ordinal in the low — sorted in
#: place, ~14 bytes an entity at the peak (274 / 331 / 444 MiB at 4 / 8 / 16
#: M; 1.1 / 1.9 / 4.0 s); it pays over this table where the ids are sparse
#: (fewer entities than a quarter of the largest number). The generic
#: `KeyTable.tables` (before it): ~54 bytes an entity. An external merge
#: sort of sorted runs bounds the memory whatever N, and would pay at a few
#: billion entities.
_NONE = 0xFFFF_FFFF
#: an item's number below this is a slot of the large array; a property's
#: id is PROPERTY_BASE + n, its n a slot of the small one
_ITEM_LIMIT = 1 << 31
#: chunk files read at once, per thread (each read is Arrow's, on threads):
#: what is held of the chunks beside the arrays
_WINDOW = 2
#: slots walked at a time when the table is written (well under 1 MB of transients)
_WALK = 1 << 16
#: how long a lookup waits for another process building the table or the
#: pack (`derived.writing` refuses at once): every pool child of a check
#: reaches its first lookup together, and one builds while the rest wait
#: (bounded, CLAUDE.md: then deferred — `derived.Busy` — never a failure)
TABLE_WAIT_S = 600.0


class _Unfit(Exception):
    """A value the direct-address arrays cannot hold."""


class _Slots:
    """A growable uint32 array of ordinals, `_NONE` where no entity is."""

    def __init__(self, size: int = 0):
        import numpy as np
        self.a = np.full(max(0, int(size)), _NONE, dtype=np.uint32)

    def put(self, slots, ordinals, *, base: int, where: str) -> None:
        import numpy as np
        if not len(slots):
            return
        top = int(slots.max())
        if top >= len(self.a):
            grown = np.full(max(top + 1, len(self.a) + len(self.a) // 2), _NONE, dtype=np.uint32)
            grown[:len(self.a)] = self.a
            self.a = grown
        held = self.a[slots] != _NONE
        if held.any():
            raise KeyTableError(f"{where}: key {base + int(slots[np.flatnonzero(held)[0]])} is listed twice; "
                                f"a key names one unit")
        o = ordinals.astype(np.uint32)
        self.a[slots] = o
        # TWICE IN ONE CHUNK: a chunk's ordinals are distinct lines, so a slot
        # written twice reads back another's ordinal (one gather; a sort of
        # each chunk, `np.unique`, was half the build's time)
        if (self.a[slots] != o).any():
            srt = np.sort(slots)
            k = int(srt[np.flatnonzero(srt[1:] == srt[:-1])[0]])
            raise KeyTableError(f"{where}: key {base + k} is listed twice; a key names one unit")

    def walk(self, base: int):
        """(keys, ordinals) in key order, `_WALK` slots at a time."""
        import numpy as np
        for i in range(0, len(self.a), _WALK):
            block = self.a[i:i + _WALK]
            nz = np.flatnonzero(block != _NONE)
            if len(nz):
                yield nz.astype(np.int64) + np.int64(base + i), block[nz].astype(np.int64)


def _direct_tables(chunks, *, where: str, row_group: int, items_hint: int = 0):
    """`KeyTable.tables`' tables, the same rows in the same row groups, from
    each chunk's (native ids, ordinals) placed in the arrays. Raises
    `_Unfit` (before a table is yielded) where a value does not fit."""
    import numpy as np
    import pyarrow as pa

    from .ids import PROPERTY_BASE
    items, props = _Slots(items_hint), _Slots()
    for nid, idx in chunks:
        if not len(nid):
            continue
        if int(nid.min()) < 0 or int(idx.min()) < 0 or int(idx.max()) >= _NONE:
            raise _Unfit()
        is_item = nid < PROPERTY_BASE
        if (int(nid[is_item].max(initial=0)) >= _ITEM_LIMIT
                or int((nid[~is_item] - PROPERTY_BASE).max(initial=0)) >= _ITEM_LIMIT):
            raise _Unfit()
        items.put(nid[is_item], idx[is_item], base=0, where=where)
        props.put(nid[~is_item] - PROPERTY_BASE, idx[~is_item], base=PROPERTY_BASE, where=where)
    schema = TABLE.schema()

    def rows():
        yield from items.walk(0)
        yield from props.walk(PROPERTY_BASE)

    def tables():
        # each row group filled in its own two buffers, exactly `row_group`
        # rows but the last, as `KeyTable.tables` cuts them
        kb, vb = np.empty(row_group, np.int64), np.empty(row_group, np.int64)
        fill, emitted = 0, False
        for k, v in rows():
            at = 0
            while at < len(k):
                take = min(row_group - fill, len(k) - at)
                kb[fill:fill + take] = k[at:at + take]
                vb[fill:fill + take] = v[at:at + take]
                fill, at = fill + take, at + take
                if fill == row_group:
                    yield pa.table({"key": kb, "index": vb}, schema=schema)
                    emitted = True
                    kb, vb, fill = np.empty(row_group, np.int64), np.empty(row_group, np.int64), 0
        if fill or not emitted:
            yield pa.table({"key": kb[:fill], "index": vb[:fill]}, schema=schema)
    return tables()


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

        def read():
            """Each chunk file's (native ids, ordinals), in order, a window of
            files at a time — never every chunk held at once."""
            if mode == "processes":
                yield from parallel.each_part(columns_of, [(f,) for f in files], limit=workers)
                return
            # THREADS, measured (2026-11-17, 12 M entities in 120 chunk files):
            # the pool's processes ship every chunk's arrays back pickled and
            # lost to one process (4.4 s in-process, 5.1 s on 2 processes,
            # 4.8 s on 3); a chunk's read is Arrow's, which lets the GIL go
            from concurrent.futures import ThreadPoolExecutor
            n = max(1, int(workers or parallel.processes()))
            from collections import deque
            with ThreadPoolExecutor(max_workers=n, thread_name_prefix="wikidata-entities") as ex:
                # a SLIDING window: the next reads run while a chunk is placed,
                # at most n × _WINDOW chunks held
                ahead: deque = deque()
                nxt = 0
                while nxt < len(files) or ahead:
                    while nxt < len(files) and len(ahead) < n * _WINDOW:
                        ahead.append(ex.submit(columns_of, files[nxt]))
                        nxt += 1
                    yield ahead.popleft().result()
        where = Path(dump).name
        try:
            try:
                tables = _direct_tables(read(), where=where, row_group=ROW_GROUP,
                                        items_hint=int(meta.get("max_item") or 0) + 1)
            except _Unfit:
                tables = _generic_tables(list(read()), where=where)
        except KeyTableError as e:
            raise EntityTableError(f"{e} (an entity is one line of the dump)") from None
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
    the direct-address arrays cannot hold."""
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
