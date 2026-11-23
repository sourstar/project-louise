"""One wiki's `page_props` table, from the same dump run as its articles
(wikipedia parser 5; docs/notes/wikipedia-qids.md).

Each run publishes `<wiki>-<date>-page_props.sql.gz` beside the articles:
MediaWiki's per-page properties, `wikibase_item` among them — the page's
Wikidata item — with the sitelink badges, the lead image and the
disambiguation flag. The QIDs come from here, from the source, and never
from another collection (operator, 2026-10-18).

THE UNIT. The table's rows are dumped in primary-key order (`pp_page`,
`pp_propname`), so one page's rows are contiguous: its unit is the bytes of
the decompressed SQL from the opening parenthesis of its first tuple to the
closing one of its last — `[start, end)`, the member its locator names. A
page whose rows straddle two INSERT statements takes the statement break
with them, as the dump wrote it.

TWO STATEMENT LAYOUTS, read alike. Runs up to 20260901 write each INSERT on
one line, `INSERT INTO `t` VALUES (…),(…);`; from 20261001 dumps.wikimedia.org
writes `INSERT INTO `t` VALUES` alone on its line and then one tuple a line,
each ending in `,` and the statement's last in `;` (read from the runs'
own files by Range, 2026-11-23). The old reader matched only the first
layout and read a new-layout file as a table with no row — silently: every
page of such a run would have lost its QID. PACK_VERSION 2 rebuilds any
pack it wrote.

THE PACK. gzip is not seekable, so a page is never read from the file: a
derived Parquet (`page`, `start`, `end`, `rows`), built once per file by one
streaming pass and rebuilt when the file's (size, mtime) or PACK_VERSION
moves, the way pubmed's pack is (app/plugins/pubmed/pack.py). It sits in a
hidden `.pack/` beside the file — never published, never enumerated — or, on
an origin that cannot be written (a read-only mount at check time), in the
system's temporary directory. The `.sql.gz` stays the origin.

STRICT. Every byte between two tuples of a statement must be the comma the
dump writes (and, in the second layout, the line break after it), every
statement must end in `);`, and a `CREATE TABLE` naming other columns than
the reader's fails the file. A row this reader does not recognise fails the
file, loudly, rather than being skipped: a skipped row would be a property
the record silently lacks.

THE SAME READER for any MediaWiki table dumped the same way (`iter_rows`,
`iter_units`, `PageTable`): Wikisource's categorylinks, linktarget and
page_props (app/plugins/wikisource/tables.py), each with its own tuple form
and its own pack.
"""
from __future__ import annotations

import base64
import gzip
import re
import tempfile
import threading
from bisect import bisect_right
from pathlib import Path
from typing import Callable, Iterator

from app import derived
from app.backend import read_cache
from app.container_pack import ContainerPack, signature

#: 2 (2026-11-23): the one-tuple-a-line layout read; a pack 1 wrote from such
#: a file holds no row
PACK_VERSION = 2
ROW_GROUP = 20_000
SUFFIX = "-page_props.sql.gz"
PACK_SUFFIX = ".pack.parquet"
TABLE = b"page_props"
#: one row: (pp_page, 'pp_propname', 'pp_value', pp_sortkey)
_TUPLE = re.compile(
    rb"\((\d+),'((?:[^'\\]|\\.)*)','((?:[^'\\]|\\.)*)',"
    rb"(NULL|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\)", re.S)
COLUMNS = ("pp_page", "pp_propname", "pp_value", "pp_sortkey")
#: mysqldump's escapes; any other `\x` is `x` (MySQL's own rule)
_ESCAPES = {b"0": b"\0", b"'": b"'", b'"': b'"', b"b": b"\b", b"n": b"\n",
            b"r": b"\r", b"t": b"\t", b"Z": b"\x1a", b"\\": b"\\"}
_ESCAPED = re.compile(rb"\\(.)", re.S)
#: a column of a CREATE TABLE: its name, backquoted, first on its line
_COLUMN = re.compile(rb"^\s+`([^`]+)`")
#: the property whose home is `extra.wikidata`
WIKIBASE_ITEM = "wikibase_item"

_META_VERSION = b"louise.page_props_pack_version"
_META_SIZE = b"louise.source_size"
_META_MTIME = b"louise.source_mtime_ns"


class TableError(ValueError):
    """The file holds bytes this reader does not recognise as its table's rows."""


#: the name page_props' callers know
PagePropsError = TableError


def props_path_of(dump: Path) -> Path:
    """The page_props file of the dump run `dump` belongs to: beside it, the
    run's own prefix (`enwiki-20260801`)."""
    name = dump.name
    i = name.find("-pages-articles")
    prefix = name[:i] if i > 0 else name.split(".")[0]
    return dump.with_name(prefix + SUFFIX)


def is_props_locator(segment: str) -> bool:
    return segment.rsplit("::", 2)[0].endswith(SUFFIX)


def _unescape(raw: bytes) -> bytes:
    return _ESCAPED.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), raw)


def _text(raw: bytes):
    """A value as the record holds it: text, or `{"base64": …}` for bytes
    that are not UTF-8 (MediaWiki stores some properties compressed)."""
    b = _unescape(raw)
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(b).decode("ascii")}


#: a dumped value as a record holds it — Wikisource's tables read theirs alike
value = _text


def parse_rows(unit: bytes) -> list[tuple[str, object]]:
    """`[(pp_propname, pp_value), …]` of one page's unit, in the dump's order.
    `pp_page` is the page itself; `pp_sortkey` is MediaWiki's float copy of a
    numeric value, kept for its index, and adds nothing the value does not
    say (docs/notes/wikipedia-qids.md §2)."""
    out = []
    for m in _TUPLE.finditer(unit):
        name = _unescape(m.group(2)).decode("utf-8", errors="strict")
        out.append((name, _text(m.group(3))))
    return out


def fields_of(unit: bytes | None) -> dict:
    """What a record holds from its page's unit: `wikidata` (the QID) and
    `page_props` (every other property, by name). `{}` for no unit."""
    if not unit:
        return {}
    rows = parse_rows(unit)
    qid = next((v for k, v in rows if k == WIKIBASE_ITEM), None)
    rest = {k: v for k, v in rows if k != WIKIBASE_ITEM}
    return {k: v for k, v in {"wikidata": qid, "page_props": rest}.items() if v}


def iter_rows(f, *, table: bytes = TABLE, row: re.Pattern = _TUPLE,
              columns: tuple[str, ...] | None = COLUMNS) -> Iterator[tuple[re.Match, int]]:
    """`(match, offset of the match's line)` for every tuple of every
    `INSERT INTO `table`` statement of the decompressed SQL `f` (a binary
    file object, read line by line), in the dump's order, in either layout
    (the module's docstring). TableError on a byte that is not a tuple of
    `row`, its separating comma, a line break after one, or the `;` that
    ends a statement; on a statement the file leaves open; on a `CREATE
    TABLE` of `table` whose columns are not `columns`."""
    head = b"INSERT INTO `" + table + b"` VALUES"
    create = b"CREATE TABLE `" + table + b"` ("
    pos = 0
    open_ = False                 # inside a statement: the next tuple may start a line
    named: list[str] | None = None
    for line in f:
        at = 0
        if not open_:
            if named is not None:                                # inside CREATE TABLE
                m = _COLUMN.match(line)
                if m:
                    named.append(m.group(1).decode("utf-8", errors="replace"))
                elif line.lstrip().startswith(b")"):
                    if columns is not None and tuple(named) != tuple(columns):
                        raise TableError(f"`{table.decode()}` has the columns {tuple(named)}, this reader "
                                         f"reads {tuple(columns)}")
                    named = None
                pos += len(line)
                continue
            if line.startswith(create):
                named = []
                pos += len(line)
                continue
            if not line.startswith(head):
                if line.startswith(b"("):
                    raise TableError(f"a tuple outside any statement at offset {pos}: {line[:60]!r}")
                pos += len(line)
                continue
            at = len(head)
            open_ = True
            if line[at:at + 1] == b" ":
                at += 1
            elif line[at:].rstrip(b"\r\n") == b"":
                pos += len(line)
                continue                                         # its tuples start on the next line
            else:
                raise TableError(f"statement at offset {pos} begins {line[:len(head) + 20]!r}")
        while True:
            m = row.match(line, at)
            if m is None:
                raise TableError(f"unparsed bytes at offset {pos + at}: {line[at:at + 60]!r}")
            yield m, pos
            at = m.end()
            sep = line[at:at + 1]
            if sep == b",":
                at += 1
                if _line_ends(line, at):
                    break                                        # the next tuple starts the next line
                continue
            if sep == b";" and _line_ends(line, at + 1):
                open_ = False
                break
            raise TableError(f"statement at offset {pos} ends in {line[at - 1:at + 20]!r}")
        pos += len(line)
    if open_:
        raise TableError(f"the file ends inside a `{table.decode()}` statement")


def _line_ends(line: bytes, at: int) -> bool:
    """Nothing but the line break from `at` on — in constant time: a
    first-layout statement is one line of a megabyte, read tuple by tuple."""
    c = line[at:at + 1]
    return c in (b"", b"\n") or (c == b"\r" and line[at + 1:at + 2] in (b"", b"\n"))


def iter_units(f, *, table: bytes = TABLE, row: re.Pattern = _TUPLE,
               columns: tuple[str, ...] | None = COLUMNS) -> Iterator[tuple[int, int, int, bytes]]:
    """`(page, start, end, unit bytes)` per page — the first column of `row`
    — streaming the decompressed SQL `f` (`iter_rows`). Pages strictly
    ascending, or TableError: the rows of a table whose primary key leads
    with the page are one run per page."""
    seen: list[tuple[int, bytes]] = []     # every line from the open unit's first to the one read now

    def lines():
        pos = 0
        for line in f:
            seen.append((pos, line))
            pos += len(line)
            yield line
    cur: list | None = None                # [page, start, end]
    last_page = -1
    for m, line_pos in iter_rows(lines(), table=table, row=row, columns=columns):
        page = int(m.group(1))
        if cur is not None and cur[0] == page:
            cur[2] = line_pos + m.end()
            continue
        if cur is not None:
            yield _close(cur, seen)
        del seen[:-1]                      # the new unit starts on the line read now
        if page <= last_page:
            raise TableError(f"page {page} after {last_page}: the rows are not in primary-key order")
        last_page = page
        cur = [page, line_pos + m.start(), line_pos + m.end()]
    if cur is not None:
        yield _close(cur, seen)


def _close(cur: list, seen: list[tuple[int, bytes]]) -> tuple[int, int, int, bytes]:
    """The unit `[start, end)` cut from the lines that hold it: from its own
    line when it fits in one (a long first-layout statement is never copied
    per page), from the lines joined when it spans several — the statement
    break between them included, as the dump wrote it."""
    page, start, end = cur
    p, line = seen[0]
    if end <= p + len(line):
        return page, start, end, line[start - p:end - p]
    return page, start, end, b"".join(b for _, b in seen)[start - p:end - p]


# ---- the pack ---------------------------------------------------------------

_SCHEMA = None


def _schema():
    import pyarrow as pa
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = pa.schema([pa.field("page", pa.int64()), pa.field("start", pa.int64()),
                             pa.field("end", pa.int64()), pa.field("rows", pa.binary())])
    return _SCHEMA


def _tables(props: Path, units: Callable = iter_units):
    """`units(file)`'s units of the gzip `props`, as tables of ROW_GROUP rows."""
    import pyarrow as pa
    cols: dict[str, list] = {"page": [], "start": [], "end": [], "rows": []}
    with gzip.open(props, "rb") as f:
        for page, start, end, unit in units(f):
            cols["page"].append(page)
            cols["start"].append(start)
            cols["end"].append(end)
            cols["rows"].append(unit)
            if len(cols["page"]) >= ROW_GROUP:
                yield pa.table(cols, schema=_schema())
                cols = {k: [] for k in cols}
    if cols["page"]:
        yield pa.table(cols, schema=_schema())


_lock = threading.Lock()
_bounds: dict[str, list[int]] = {}             # pack path -> first page of each row group


class PageTable:
    """A dumped table packed by its leading key — the CONTAINER_PACK template
    (app/container_pack.py `pack`: freshness, one builder, the pack written
    whole) over `units` (`iter_units` here). Wikipedia's page_props is one
    (this module's functions); Wikisource's categorylinks, linktarget and
    page_props are others, each placed under its own kind
    (app/plugins/wikisource/tables.py). `dirs(origin)`: the fast drive's
    place or `.pack/` beside the origin, then the temporary directory;
    `where(origin)`: what decides them, cheap (`derived.place_kept`)."""

    def __init__(self, pack: ContainerPack, version: int, *, dirs: Callable[[Path], list[Path]],
                 where: Callable[[Path], object], units: Callable = iter_units):
        self.pack, self.version, self.dirs, self.where, self.units = pack, version, dirs, where, units
        #: (pack path, group) -> {page: (start, end, rows)}, in the process's one
        #: read budget (app/backend.py `READ_CACHE`)
        self.groups = read_cache(pack.label)

    def candidates(self, origin: Path) -> list[Path]:
        # the temporary one is keyed by the origin's full path: two corpora can
        # hold a file of the same name (caches key on physical identity)
        first, second = self.dirs(origin)
        beside = origin.parent / derived.BESIDE / (origin.name + PACK_SUFFIX)
        if first != beside.parent:
            derived.adopt(beside, first / (origin.name + PACK_SUFFIX))   # a pack built beside, carried once
        return [first / (origin.name + PACK_SUFFIX),
                second / (derived.tag(origin) + "-" + origin.name + PACK_SUFFIX)]

    def is_fresh(self, origin: Path, pack: Path) -> bool:
        return self.pack.is_fresh(origin, pack, self.version)

    def build(self, origin: Path, dest: Path) -> int:
        """One streaming pass over `origin` into `dest`, written whole."""
        return self.pack.write(origin, dest, _schema(), _tables(origin, self.units), self.version)

    def ensure(self, origin: Path) -> Path:
        """The pack of `origin`, built if missing or stale; one builder across
        threads and processes."""
        return self.pack.ensure(origin, lambda: self.candidates(origin), version=self.version,
                                build=lambda o, d: self.build(o, d), ready=self._ready,
                                where=self.where(origin))

    def _ready(self, pack: Path) -> None:
        """A pack newly confirmed or built: what was cached from an older one goes."""
        with _lock:
            _bounds.pop(str(pack), None)
        self.groups.forget(lambda k: k[0] == str(pack))

    def unit_of(self, origin: Path, page: int) -> tuple[int, int, bytes] | None:
        """`(start, end, unit)` of `page` in `origin`, or None when the table
        has no row for it."""
        return _lookup(self.ensure(origin), page, self.groups)

    def reader(self, origin: Path):
        """`unit_of` over one file for a whole scan: the pack confirmed once,
        not stat-ed per page."""
        pack = self.ensure(origin)
        return lambda page: _lookup(pack, page, self.groups)


def _signature(path: Path) -> tuple[int, int]:
    return signature(path)


def _pack_dirs(props: Path) -> list[Path]:
    """The fast drive's `wikipedia-page-props/<tag of the props' folder>/`
    when the node names one (LOUISE_CACHE_DIR, app/derived.py), else
    `.pack/` beside the props; then the temporary directory."""
    first = derived.place("wikipedia-page-props", props.parent) or props.parent / derived.BESIDE
    return [first, Path(tempfile.gettempdir()) / "louise-page-props"]


#: the CONTAINER_PACK template's machinery (app/container_pack.py): freshness,
#: one builder across processes, the pack written whole
_PACK = ContainerPack("wikipedia page_props", "pages", _META_VERSION)
_TABLE = PageTable(_PACK, PACK_VERSION, dirs=lambda p: _pack_dirs(p),
                   where=lambda p: derived.place_kept("wikipedia-page-props", p.parent))
#: (props, where) -> (signature, version, pack path) of the pack found fresh
_fresh = _PACK.fresh
#: wikipedia's read cache, "wikipedia page_props" (it held 4 groups, then 64 MiB of its own)
_groups = _TABLE.groups


def _candidates(props: Path) -> list[Path]:
    return _TABLE.candidates(props)


def _is_fresh(props: Path, pack: Path) -> bool:
    return _TABLE.is_fresh(props, pack)


def build(props: Path, dest: Path) -> int:
    """One streaming pass over `props` into `dest`, written whole
    (app/container_pack.py)."""
    return _TABLE.build(props, dest)


def ensure(props: Path) -> Path:
    """The pack of `props`, built if missing or stale; one builder across
    threads and processes (app/container_pack.py)."""
    return _TABLE.ensure(props)


def unit_of(props: Path, page: int) -> tuple[int, int, bytes] | None:
    """`(start, end, unit)` of `page` in `props`, or None when the table has
    no row for it."""
    return _TABLE.unit_of(props, page)


def reader(props: Path):
    """`unit_of` over one file for a whole scan: the pack confirmed once,
    not stat-ed per page."""
    return _TABLE.reader(props)


def _lookup(pack: Path, page: int, groups=None) -> tuple[int, int, bytes] | None:
    import pyarrow.parquet as pq
    groups = _groups if groups is None else groups
    pk = str(pack)
    with _lock:
        bounds = _bounds.get(pk)
    if bounds is None:
        md = pq.read_metadata(pack)
        bounds = []
        for g in range(md.num_row_groups):
            st = md.row_group(g).column(0).statistics
            bounds.append(int(st.min))
        with _lock:
            _bounds[pk] = bounds
    g = bisect_right(bounds, page) - 1
    if g < 0:
        return None
    group = groups.get((pk, g))
    if group is None:
        with pq.ParquetFile(pack) as pf:
            t = pf.read_row_group(g)
        group = groups.put((pk, g), {p: (s, e, r) for p, s, e, r in zip(
            t.column("page").to_pylist(), t.column("start").to_pylist(),
            t.column("end").to_pylist(), t.column("rows").to_pylist())}, t.nbytes)
    return group.get(page)
