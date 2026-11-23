"""A run's WORKS INDEX: every page of a Wikisource multistream dump, by
namespace and title, with the bz2 stream that holds it — so a work's pages
(its page, its subpages, the scan pages it transcludes) are found and read
without reading the dump (docs/notes/wikisource-design.md §1, §3).

DERIVED and re-creatable (invariant 6), the DUMP_PACK template
(app/backend.py): built in one pass over the run's streams on the parse pool
(`parallel.each_part`: multi-threading is a must), under `LOUISE_CACHE_DIR`
(app/derived.py) keyed on the dump's physical identity, else beside the
dump in `.pack/`; fresh while the dump's size and mtime and INDEX_VERSION
stand, rebuilt otherwise. ONE WRITER (`derived.writing`), never waited for:
a second gets `derived.Busy` at once and discovery defers. Written whole
into a temporary file, then renamed.

Why SQLite and not the CONTAINER_PACK template's Parquet (app/container_pack.py,
which Wikipedia's stream index takes): a work is read by TITLE PREFIX
(`Vita/…`) and by exact title (`Pagina:Vita.djvu/5`), which a B-tree on
(ns, title) answers in one probe each; the Parquet KeyTable bisects one
integer key. The standard taken is SQLite's own index.

The multistream index (`offset:page_id:title`) gives the streams; a stream
runs to the next distinct offset and the last to the end of the file. The
dump's first stream is its <siteinfo> header: the namespaces are read there.
"""
from __future__ import annotations

import bz2
import json
import os
import re
import sqlite3
from pathlib import Path

from app import derived
from . import dump as dump_mod, proofread

INDEX_VERSION = 1
KIND = "wikisource-works"
DUMP_SUFFIX = "-pages-articles-multistream.xml.bz2"
INDEX_SUFFIX = "-pages-articles-multistream-index.txt.bz2"
_LINE = re.compile(rb"^(\d+):(\d+):")
#: streams per task of the pass (~100 pages a stream)
STREAMS_PER_TASK = 64

#: a page that only disambiguates is no record (design note §3): its
#: template, by each wiki's name (lowercased)
DISAMBIGUATION = ("disambigua", "disambiguation", "dab", "homonymie", "begriffsklärung", "desambiguación")
_DISAMBIG = re.compile(r"\{\{\s*(?:" + "|".join(DISAMBIGUATION) + r")\s*[|}]", re.I)

_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE pages (id INTEGER PRIMARY KEY, ns INTEGER NOT NULL, title TEXT NOT NULL, "
    " rev_id INTEGER NOT NULL, sha1 TEXT NOT NULL, model TEXT NOT NULL, redirect TEXT, "
    " start INTEGER NOT NULL, end INTEGER NOT NULL, tags TEXT, disambiguation INTEGER NOT NULL DEFAULT 0)",
    "CREATE UNIQUE INDEX idx_pages_title ON pages(ns, title)",
)


def index_of(dump: Path) -> Path:
    return Path(dump).with_name(Path(dump).name.replace(DUMP_SUFFIX, INDEX_SUFFIX))


def streams(dump: Path) -> list[tuple[int, int]]:
    """`(start, end)` of every page stream, in dump order (the header's,
    before the first, apart: `header`)."""
    offsets: set[int] = set()
    with bz2.open(index_of(dump), "rb") as f:
        for line in f:
            m = _LINE.match(line)
            if m:
                offsets.add(int(m.group(1)))
    starts = sorted(offsets)
    size = Path(dump).stat().st_size
    return [(s, starts[i + 1] if i + 1 < len(starts) else size) for i, s in enumerate(starts)]


def header(dump: Path, first: int) -> str:
    with open(dump, "rb") as f:
        return bz2.decompress(f.read(first)).decode("utf-8", errors="replace")


def _rows(dump: str, ranges: list[tuple[int, int]]) -> list[tuple]:
    """The pass's task: every page of the streams `ranges`, as rows."""
    out = []
    with open(dump, "rb") as f:
        for start, end in ranges:
            f.seek(start)
            data = bz2.decompress(f.read(end - start))
            for p in dump_mod.pages_of_stream(data):
                tags, dis = None, 0
                if p.ns == 0 and p.redirect is None:
                    found = [t.raw for t in proofread.tags(p.text)]
                    tags = json.dumps(found, ensure_ascii=False) if found else None
                    dis = 1 if _DISAMBIG.search(p.text) else 0
                out.append((p.id, p.ns, p.title, p.rev_id, p.sha1, p.model, p.redirect, start, end, tags, dis))
    return out


def _signature(dump: Path) -> dict[str, str]:
    st = Path(dump).stat()
    return {"version": str(INDEX_VERSION), "size": str(st.st_size), "mtime_ns": str(st.st_mtime_ns)}


def _places(dump: Path) -> list[Path]:
    out = []
    d = derived.place("wikisource-works", dump)
    if d is not None:
        out.append(d / "works.sqlite")
    out.append(Path(dump).parent / ".pack" / (Path(dump).name + ".works.sqlite"))
    return out


def _fresh(path: Path, dump: Path) -> bool:
    if not path.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False
    want = _signature(dump)
    return all(meta.get(k) == v for k, v in want.items()) and meta.get("complete") == "1"


def build(dump: Path, dest: Path, *, log=None) -> int:
    """The index of `dump` written whole at `dest`; returns the pages."""
    from app import parallel
    say = log or (lambda _m: None)
    ranges = streams(dump)
    if not ranges:
        raise ValueError(f"{index_of(dump)} lists no stream")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    n = 0
    try:
        for stmt in _SCHEMA:
            con.execute(stmt)
        con.execute("INSERT INTO meta VALUES ('namespaces', ?)",
                    (json.dumps(dump_mod.namespaces(header(dump, ranges[0][0]))),))
        tasks = [(str(dump), ranges[i:i + STREAMS_PER_TASK]) for i in range(0, len(ranges), STREAMS_PER_TASK)]
        for rows in parallel.each_part(_rows, tasks):
            con.executemany("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
        con.executemany("INSERT INTO meta VALUES (?, ?)", list(_signature(dump).items()) + [("complete", "1")])
        con.commit()
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(tmp, dest)
    say(f"[wikisource] {Path(dump).name}: works index of {n:,} pages, {len(ranges):,} streams")
    return n


def ensure(dump: Path, *, log=None) -> Path:
    """The fresh index of `dump`, built where it may be when there is none.
    `derived.Busy` when another process is building it (the caller defers)."""
    places = _places(dump)
    for p in places:
        if _fresh(p, dump):
            return p
    err: OSError | None = None
    for p in places:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with derived.writing(p.with_name(p.name + ".lock"), f"the works index of {Path(dump).name}"):
                if not _fresh(p, dump):
                    build(dump, p, log=log)
            return p
        except derived.Busy:
            raise
        except OSError as e:
            err = e
    raise err if err else OSError(f"no place to build the works index of {dump}")


class Works:
    """The index, read: a run's pages by namespace and title."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.namespaces = {int(k): v for k, v in
                           json.loads(self._meta("namespaces") or "{}").items()}
        self._last: dict[str, int | None] = {}

    def _meta(self, key: str) -> str | None:
        row = self._con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._con.close()

    def ns_named(self, name: str) -> int | None:
        return next((k for k, v in self.namespaces.items() if v == name), None)

    def main_titles(self) -> list[str]:
        """Every main-namespace page that is neither a redirect nor a
        disambiguation, by title."""
        return [r[0] for r in self._con.execute(
            "SELECT title FROM pages WHERE ns = 0 AND redirect IS NULL AND disambiguation = 0 ORDER BY title")]

    def page(self, ns: int, title: str) -> dict | None:
        row = self._con.execute("SELECT id, ns, title, rev_id, sha1, model, redirect, start, end, tags "
                                "FROM pages WHERE ns = ? AND title = ?", (ns, title)).fetchone()
        if row is None:
            return None
        keys = ("id", "ns", "title", "rev_id", "sha1", "model", "redirect", "start", "end", "tags")
        return dict(zip(keys, row))

    def by_id(self, page_id: int) -> dict | None:
        row = self._con.execute("SELECT ns, title FROM pages WHERE id = ?", (page_id,)).fetchone()
        return self.page(row[0], row[1]) if row else None

    def subpages(self, root: str) -> list[str]:
        lo, hi = root + "/", root + "/\U0010ffff"
        return [r[0] for r in self._con.execute(
            "SELECT title FROM pages WHERE ns = 0 AND redirect IS NULL AND title >= ? AND title < ? "
            "ORDER BY title", (lo, hi))]

    def last_page(self, ns: int, prefix: str) -> int | None:
        """The highest page number `<prefix>/<n>` held in namespace `ns`."""
        key = f"{ns}:{prefix}"
        if key not in self._last:
            best = None
            lo, hi = prefix + "/", prefix + "/\U0010ffff"
            for (title,) in self._con.execute("SELECT title FROM pages WHERE ns = ? AND title >= ? AND title < ?",
                                              (ns, lo, hi)):
                tail = title[len(lo):]
                if tail.isdigit():
                    best = max(best or 0, int(tail))
            self._last[key] = best
        return self._last[key]
