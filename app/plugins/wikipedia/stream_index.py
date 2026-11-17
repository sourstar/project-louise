"""One run's MULTISTREAM INDEX as a lookup table: a page id to the byte
range of the bz2 stream that holds the page (wikipedia parser 6; 09 §9.5
amended 2026-11-17, operator: "Choice B").

Since parser 6 a record's `origin` names its PAGE, never its bytes
(`itwiki/pages-articles-multistream.xml.bz2#<page id>`, app/plugins/
wikipedia/locate.py), and it is read back in the run its snapshot names.
The origin's own index says where: `<wiki>-<run>-pages-articles-multistream-
index.txt.bz2`, one line `offset:page_id:title` per page in dump order, the
offset being that of the stream the page is in. A stream runs to the next
distinct offset of the index, and the last one to the end of the file —
written `end = 0`, exactly as discovery writes a page's locator
(source.py `_flush`), so a page read through this table is read from the
very bytes a scan of the run names, and hashes to the same `origin_hash`.

THE TABLE. bz2 is not seekable by line, and a lookup must not read the
index (enwiki's is ~24 M lines): a derived Parquet (`key` — the page —,
`start`, `end`), sorted by page (app/keyed_origin.py's KeyTable, the format
every keyed origin shares), built in ONE streaming pass over the index and rebuilt when
the index's (size, mtime) or PACK_VERSION moves — the CONTAINER_PACK
template's machinery (app/container_pack.py: one builder across processes,
written whole, fresh by its own metadata), the way page_props' pack is
built. It is placed under the fast drive (`LOUISE_CACHE_DIR`,
app/derived.py, invariant 6) keyed on the run directory's PHYSICAL identity
— its resolved path — never a name (two corpora can hold a file of one
name), else in `.pack/` beside the index, else the system's temporary
directory. A lookup bisects the row groups' first pages (the pack's
footer, read once) and searches one row group, kept in the process's one
read cache (app/backend.py): no scan per read.

LENIENT AS DISCOVERY IS. A line whose offset or page id is not a number is
skipped, as discovery skips it, and ends no stream. A page listed twice is
refused: it would locate one record in two streams.

THE STANDARD, NOT ADOPTED: an SQLite table keyed on the page (one B-tree
probe per lookup) would answer the same; the pack is the template every
CONTAINER_PACK form already shares (freshness, one builder, eviction by the
cache), so the table takes it rather than a second machinery.
"""
from __future__ import annotations

import bz2
import re
import tempfile
from pathlib import Path

from app.container_pack import ContainerPack
from app.keyed_origin import KeyTable, KeyTableError

PACK_VERSION = 1
#: pages per row group: ~1.5 MiB of three int64 columns, one read per group
ROW_GROUP = 65_536
#: the derived kind (app/derived.py), declared in source.py's `backend`
KIND = "wikipedia-stream-index"
PACK_SUFFIX = ".pages.parquet"
INDEX_SUFFIX = "-pages-articles-multistream-index.txt.bz2"
#: decompressed bytes read per step of the pass
_BLOCK = 1 << 24
#: one index line's two numbers, as discovery reads them (`offset:page_id`,
#: then the title after a colon, or the end of the line)
_LINE = re.compile(rb"^(\d+):(\d+)(?::|$)", re.MULTILINE)

_META_VERSION = b"louise.stream_index_version"
_PACK = ContainerPack("wikipedia stream index", "pages", _META_VERSION)


class StreamIndexError(KeyTableError):
    """The index cannot be read as a lookup table (a page listed twice)."""


def index_of(dump: Path) -> Path:
    """The multistream index beside a run's dump."""
    name = Path(dump).name
    return Path(dump).with_name(name.replace("-pages-articles-multistream.xml.bz2", INDEX_SUFFIX))


def read_columns(index: Path):
    """`(page, start, end)` as int64 arrays in the INDEX'S order — one
    streaming pass. `end` is the next distinct offset, 0 for the last stream."""
    import numpy as np
    offs: list = []
    pages: list = []
    tail = b""
    with bz2.open(index, "rb") as f:
        while True:
            block = f.read(_BLOCK)
            if not block:
                break
            block = tail + block
            cut = block.rfind(b"\n") + 1
            if cut == 0:
                tail = block
                continue
            tail = block[cut:]
            got = _LINE.findall(block, 0, cut)
            if got:
                pair = np.array(got, dtype=np.bytes_).astype(np.int64)
                offs.append(pair[:, 0])
                pages.append(pair[:, 1])
    if tail:
        got = _LINE.findall(tail)
        if got:
            pair = np.array(got, dtype=np.bytes_).astype(np.int64)
            offs.append(pair[:, 0])
            pages.append(pair[:, 1])
    if not offs:
        z = np.zeros(0, dtype=np.int64)
        return z, z, z
    off = np.concatenate(offs)
    page = np.concatenate(pages)
    # a stream ends where the next line's offset differs (discovery's rule)
    new = np.empty(len(off), dtype=bool)
    new[0] = True
    new[1:] = off[1:] != off[:-1]
    group = np.cumsum(new) - 1
    starts = off[new]
    ends_of_group = np.append(starts[1:], 0)
    return page, off, ends_of_group[group]



#: the table's format and lookup, shared with every keyed origin (app/keyed_origin.py)
TABLE = KeyTable("wikipedia stream index", ("start", "end"))


def build(index: Path, dest: Path) -> int:
    """One pass over `index` into `dest`, sorted by page, written whole."""
    return _PACK.write(index, dest, TABLE.schema(), _tables(index), PACK_VERSION)


def _tables(index: Path):
    page, start, end = read_columns(index)
    try:
        yield from TABLE.tables(page, {"start": start, "end": end}, where=index.name, row_group=ROW_GROUP)
    except KeyTableError as e:
        raise StreamIndexError(f"{e} (a page is in one stream)") from None


def _candidates(index: Path) -> list[Path]:
    """The fast drive's `wikipedia-stream-index/<tag of the run's folder>/`
    when the node names one, else `.pack/` beside the index; then the
    temporary directory, keyed by the index's full path."""
    from app import derived
    first = derived.place("wikipedia-stream-index", index.parent) or index.parent / derived.BESIDE
    second = Path(tempfile.gettempdir()) / "louise-stream-index"
    return [first / (index.name + PACK_SUFFIX), second / (derived.tag(index) + "-" + index.name + PACK_SUFFIX)]


def ensure(index: Path) -> Path:
    """The pack of `index`, built if missing or stale; one builder across
    threads and processes (app/container_pack.py)."""
    from app import derived
    index = Path(index)
    return _PACK.ensure(index, lambda: _candidates(index), version=PACK_VERSION,
                        build=lambda o, d: build(o, d), ready=TABLE.forget,
                        where=derived.place_kept("wikipedia-stream-index", index.parent))


def stream_of(index: Path, page: int) -> tuple[int, int] | None:
    """`(start, end)` of the stream holding `page` in the dump `index`
    describes, or None when the index does not list the page."""
    return lookup(ensure(index), int(page))


def lookup(pack: Path, page: int) -> tuple[int, int] | None:
    got = TABLE.lookup(pack, page)
    return None if got is None else (got[0], got[1])
