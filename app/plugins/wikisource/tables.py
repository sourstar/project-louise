"""A Wikisource run's own SQL tables: what the origin states about each page
beside its text (docs/notes/wikisource-design.md §2, bulk first) — its
categories and its page properties, the Wikidata item among them — from the
SAME run as the pages, and never from another collection (CLAUDE.md: a
collection holds only what its corpus states).

THE FILES, beside the run's dump (dumps.wikimedia.org, every run):
- `<wiki>-<run>-categorylinks.sql.gz`: a page's category links. MediaWiki
  1.45's form, the only one the runs served now publish (read from each
  language's 20261101 file, and it.wikisource's 20260701 and 20260901, by
  Range, 2026-11-23): `(cl_from, cl_sortkey, cl_timestamp, cl_sortkey_prefix,
  cl_type, cl_collation_id, cl_target_id)`, primary key `(cl_from,
  cl_target_id)`. The category is no longer named in the row (`cl_to` is
  gone): `cl_target_id` points into
- `<wiki>-<run>-linktarget.sql.gz`: `(lt_id, lt_namespace, lt_title)`, the
  title as the database keys it (underscores). Dumped in (namespace, title)
  order, NOT by id; a category is namespace 14.
- `<wiki>-<run>-page_props.sql.gz`: Wikipedia's parser-5 table, read by
  Wikipedia's own reader (app/plugins/wikipedia/page_props.py).
Any other column list fails the file (the reader is strict), so a schema
change is said, never read as an empty table.

THE PACKS (the CONTAINER_PACK template, Wikipedia's `PageTable` over its
strict reader): gzip is not seekable, so each file is packed once by one
streaming pass — categorylinks and page_props by page (`cl_from`,
`pp_page`: their primary keys lead with it, one run of rows per page), the
categories of linktarget by `lt_id` (sorted here; the other namespaces are
read, strictly, and not packed: categorylinks names categories only) —
under `LOUISE_CACHE_DIR` (app/derived.py) keyed on the file's folder, else
in `.pack/` beside it, else the temporary directory; fresh by the file's
size, mtime and PACK_VERSION.

A RECORD'S UNIT takes, for each page it reads, the page's rows of
page_props and of categorylinks as the decompressed table holds them, then
the linktarget rows those category links name, each once in the order first
named (source.py frames them), so `origin_hash` covers every byte a record
keeps from the tables.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Iterator

from app import derived
from app.container_pack import ContainerPack

from ..wikipedia import page_props as pp

#: 1 (2026-11-23): the first
PACK_VERSION = 1
_VERSION_KEY = b"louise.wikisource_table_pack_version"
CATEGORYLINKS_SUFFIX = "-categorylinks.sql.gz"
LINKTARGET_SUFFIX = "-linktarget.sql.gz"
PAGE_PROPS_SUFFIX = pp.SUFFIX
#: MediaWiki's Category namespace, the same key on every wiki
CATEGORY_NS = 14

_Q = rb"'((?:[^'\\]|\\.)*)'"
#: one categorylinks row: (cl_from, 'cl_sortkey', 'cl_timestamp',
#: 'cl_sortkey_prefix', 'cl_type', cl_collation_id, cl_target_id)
CL_ROW = re.compile(rb"\((\d+)," + _Q + rb"," + _Q + rb"," + _Q + rb",'(page|subcat|file)',(\d+),(\d+)\)", re.S)
CL_COLUMNS = ("cl_from", "cl_sortkey", "cl_timestamp", "cl_sortkey_prefix", "cl_type", "cl_collation_id",
              "cl_target_id")
#: one linktarget row: (lt_id, lt_namespace, 'lt_title')
LT_ROW = re.compile(rb"\((\d+),(-?\d+)," + _Q + rb"\)", re.S)
LT_COLUMNS = ("lt_id", "lt_namespace", "lt_title")


@dataclass(frozen=True)
class RunTables:
    """The three table files of the run a dump belongs to."""
    categorylinks: Path
    linktarget: Path
    page_props: Path

    def files(self) -> tuple[Path, ...]:
        return (self.categorylinks, self.linktarget, self.page_props)


def tables_of(dump: Path) -> RunTables:
    """`<wiki>-<run>-….sql.gz` beside `dump`, the run's own prefix."""
    props = pp.props_path_of(Path(dump))
    prefix = props.name[: -len(PAGE_PROPS_SUFFIX)]
    return RunTables(props.with_name(prefix + CATEGORYLINKS_SUFFIX), props.with_name(prefix + LINKTARGET_SUFFIX),
                     props)


def _category_links(f) -> Iterator[tuple[int, int, int, bytes]]:
    return pp.iter_units(f, table=b"categorylinks", row=CL_ROW, columns=CL_COLUMNS)


def _category_targets(f) -> Iterator[tuple[int, int, int, bytes]]:
    """linktarget's categories, each row its own unit keyed by `lt_id`,
    ascending (the table is dumped by namespace and title)."""
    rows = [(int(m.group(1)), pos + m.start(), pos + m.end(), m.group(0))
            for m, pos in pp.iter_rows(f, table=b"linktarget", row=LT_ROW, columns=LT_COLUMNS)
            if int(m.group(2)) == CATEGORY_NS]
    rows.sort()
    for a, b in pairwise(rows):
        if a[0] == b[0]:
            raise pp.TableError(f"linktarget holds id {a[0]} twice")
    yield from rows


def _dirs(first: Path | None, origin: Path) -> list[Path]:
    return [first or origin.parent / derived.BESIDE, Path(tempfile.gettempdir()) / "louise-wikisource-tables"]


CATEGORYLINKS = pp.PageTable(
    ContainerPack("wikisource categorylinks", "pages", _VERSION_KEY), PACK_VERSION, units=_category_links,
    dirs=lambda o: _dirs(derived.place("wikisource-categorylinks", o.parent), o),
    where=lambda o: derived.place_kept("wikisource-categorylinks", o.parent))
LINKTARGET = pp.PageTable(
    ContainerPack("wikisource linktarget", "categories", _VERSION_KEY), PACK_VERSION, units=_category_targets,
    dirs=lambda o: _dirs(derived.place("wikisource-linktarget", o.parent), o),
    where=lambda o: derived.place_kept("wikisource-linktarget", o.parent))
PAGE_PROPS = pp.PageTable(
    ContainerPack("wikisource page_props", "pages", _VERSION_KEY), PACK_VERSION, units=pp.iter_units,
    dirs=lambda o: _dirs(derived.place("wikisource-page-props", o.parent), o),
    where=lambda o: derived.place_kept("wikisource-page-props", o.parent))


TABLES = {"categorylinks": CATEGORYLINKS, "linktarget": LINKTARGET, "page_props": PAGE_PROPS}


def _build(name: str, origin: str) -> str:
    """One table's pack, in a child of the pass's pool (`ensure`)."""
    return str(TABLES[name].ensure(Path(origin)))


def ensure(tables: RunTables) -> None:
    """Every pack of the run, built where missing or stale — discovery's, so
    a table that cannot be read is a container's report, never an error on
    each of its documents. The stale ones are built at once, one process a
    file (a gzip is one stream: its pass cannot be split; the node's parse
    pool, `parallel.each_part`, in-process where it allows one). The child's
    error is raised here: TableError for a file the reader refuses."""
    from app import parallel
    named = (("categorylinks", tables.categorylinks), ("linktarget", tables.linktarget),
             ("page_props", tables.page_props))
    stale = [(name, str(origin)) for name, origin in named
             if not any(TABLES[name].is_fresh(origin, c) for c in TABLES[name].candidates(origin))]
    for _pack in parallel.each_part(_build, stale):
        pass
    for name, origin in named:
        TABLES[name].ensure(origin)


@dataclass(frozen=True)
class PageRows:
    """One record's rows of the tables, as the decompressed tables hold them."""
    #: page id -> its page_props unit, its categorylinks unit (pages with rows only)
    props: dict[int, bytes]
    links: dict[int, bytes]
    #: the linktarget rows the links name, each once in the order first named
    targets: list[bytes]


def rows_for(tables: RunTables, page_ids: list[int]) -> PageRows:
    """The rows of `page_ids` (in their order) in the run's tables. A target
    linktarget does not hold is left out here and said by `parse`."""
    props_of, links_of, target_of = (PAGE_PROPS.reader(tables.page_props),
                                     CATEGORYLINKS.reader(tables.categorylinks), LINKTARGET.reader(tables.linktarget))
    props: dict[int, bytes] = {}
    links: dict[int, bytes] = {}
    named: dict[int, None] = {}
    for pid in page_ids:
        if (u := props_of(pid)) is not None:
            props[pid] = u[2]
        if (u := links_of(pid)) is not None:
            links[pid] = u[2]
            for m in CL_ROW.finditer(u[2]):
                named.setdefault(int(m.group(7)))
    targets = [u[2] for t in named if (u := target_of(t)) is not None]
    return PageRows(props, links, targets)


def page_of_unit(unit: bytes) -> int:
    """The page a page_props or categorylinks unit is of: its first column."""
    m = re.match(rb"\((\d+),", unit)
    if m is None:
        raise ValueError(f"not a table unit: {unit[:40]!r}")
    return int(m.group(1))


def titles_of(targets: list[bytes]) -> dict[int, object]:
    """`{lt_id: title as the table states it}` of linktarget rows."""
    out: dict[int, object] = {}
    for row in targets:
        m = LT_ROW.fullmatch(row)
        if m is None or int(m.group(2)) != CATEGORY_NS:
            raise ValueError(f"not a category of linktarget: {row[:60]!r}")
        out[int(m.group(1))] = pp.value(m.group(3))
    return out


def category_rows(unit: bytes, titles: dict[int, object]) -> list[dict]:
    """A page's categorylinks rows as the record keeps them, in the table's
    order: every column but `cl_from` (the page itself), each as the origin
    states it, with the category's `title` (linktarget's, underscores and
    all) where the run's linktarget holds its target."""
    out = []
    for m in CL_ROW.finditer(unit):
        target = int(m.group(7))
        row = {"target_id": target, "sortkey": pp.value(m.group(2)), "timestamp": pp.value(m.group(3)),
               "sortkey_prefix": pp.value(m.group(4)), "type": m.group(5).decode("ascii"),
               "collation_id": int(m.group(6))}
        if target in titles:
            row["title"] = titles[target]
        out.append(row)
    return out


def category_name(title: object) -> str | None:
    """A category's name as MediaWiki shows it (`Title::getText`): its
    database key with underscores as spaces — the same title, losslessly
    (a title cannot hold both). None for a title that is not text."""
    return title.replace("_", " ") if isinstance(title, str) else None


def props_of(unit: bytes) -> dict:
    """A page's properties by name, as the origin states them (Wikipedia's
    parser, `wikibase_item` included)."""
    return dict(pp.parse_rows(unit))
