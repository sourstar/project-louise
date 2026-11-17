"""WHAT A VERIFIER READS of wikipedia's origin to answer for one record —
`records-vs-origin`'s bound (app/hold/check.py; 03 §3.2 amended 2026-11-17).

Until 2026-11-17 a record's unit was weighed as the whole FILES its pieces
name (plugin_io.origin_unit): a dump-layer record named its run's
multistream dump, 26 GB for enwiki, and page_props.sql.gz, 458 MB, far over
the check's 256 MiB. Such a unit was taken only when the seed ranked it
first, so the dump layer — about 30 M records — was in practice never
compared with its origin (München, 2026-11-17: 2 records drawn, both from
the archive's fa/001.parquet). What a verifier reads, piece by piece:

  * a multistream page: ITS STREAM, by Range — the byte range the run's
    index gives (stream_index.py's table, built once per run, one lookup a
    record; never a pass over the index per record). That range is the
    UNIT: the pages of one stream are read together;
  * the run's multistream index, to find that stream: bz2, not seekable, so
    read whole — SHARED by every dump page of the wiki in that run (a
    parser-5 origin names the bytes itself and needs no index);
  * page_props.sql.gz: gzip, read whole — SHARED by every record of the wiki
    in that run, archive or dump;
  * an archive article (wikivec's parquet at a pinned revision): its row
    groups' column chunks of the four columns the parse reads, by Range,
    beside the file's footer, SHARED by every article of that file.

A check reads a shared piece ONCE however many units need it (app/hold/
check.py `_origin_units_taken`). Each record's GROUP — the layer it is read
from and its wiki — is what the report breaks the reach down by.

THE STANDARD, NOT ADOPTED: HTTP Range requests (RFC 9110 §14) are what a
verifier would send; this weighs the bytes they would return, from the
local copy's own indexes. Weighing by a real ranged fetch is the network
origin's business (app/acquire/member_origin.py), and wikipedia has none.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from app.plugin_io import OriginReads

from . import archive, locate, stream_index

DUMP, ARCHIVE = "dump", "archive"
#: what a piece is read from: the archive's parquet, `data/<lang>/<NNN>.parquet`
_ARCHIVE_REL = re.compile(r"(?:^|/)data/(?P<lang>[a-z][a-z0-9_]*)/[^/]+\.parquet$")


def weigher(base: Path, run: str | None, generic: Callable[[str], Path]) -> Callable[[str], OriginReads]:
    """`origin -> OriginReads` for one check. `generic(piece)` is the
    platform's reading of a piece that names no page (plugin_io's
    `generic_local`). What it looks up — a file's size, a run's index table,
    a parquet's footer — is kept for the check it serves, never longer: a
    new check sees a new run.

    ONE LOOKUP A RECORD, NOTHING ELSE PER RECORD: everything about a FILE —
    its path, its name relative to the base, its size, its index's table —
    is worked out once a check and kept by the piece's file part, so a record
    costs its pieces' regular expressions, a dictionary read and the table's
    lookup (pathlib per record was 145 µs a record; measured 2026-11-17)."""
    base = Path(base)
    files: dict[str, tuple] = {}
    layouts: dict[str, tuple | None] = {}

    def rel(p: Path) -> str:
        try:
            return p.relative_to(base).as_posix()
        except ValueError:
            return p.as_posix()

    def size(p: Path) -> int | None:
        try:
            return p.stat().st_size
        except OSError:
            return None

    def dump_file(wiki: str) -> tuple:
        """(name, size, (index name, index size), the index's table) of the
        run's dump of `wiki`."""
        key = f"d:{wiki}"
        if key not in files:
            f = locate.run_file(base, wiki, run or "", locate.DUMP_UNIT)
            index = stream_index.index_of(f)
            pack = None
            if size(f) is not None and index.is_file():
                try:
                    pack = stream_index.ensure(index)
                except (OSError, ValueError):
                    pack = None
            files[key] = (rel(f), size(f), (rel(index), size(index)), pack)
        return files[key]

    def whole(key: str, path: Callable[[], Path]) -> tuple[str, int | None]:
        if key not in files:
            p = path()
            files[key] = (rel(p), size(p))
        return files[key]

    def layout(head: str, piece: str):
        """(name, footer bytes, [(first row, rows, bytes of the columns read)]
        per row group) of an archive parquet; (name, None, None) when it
        cannot be read, and None when the piece is no archive row range."""
        if head not in layouts:
            path = Path(generic(piece))
            name = rel(path)
            m = _ARCHIVE_REL.search(name)
            if m is None:
                layouts[head] = None
                return None
            try:
                import pyarrow.parquet as pq
                md = pq.read_metadata(path)
                groups, at = [], 0
                for g in range(md.num_row_groups):
                    rg = md.row_group(g)
                    read = sum(rg.column(i).total_compressed_size for i in range(rg.num_columns)
                               if rg.column(i).path_in_schema in archive.READ_COLUMNS)
                    groups.append((at, rg.num_rows, read))
                    at += rg.num_rows
                # the footer, its 4-byte length and the 4-byte magic
                layouts[head] = (name, m["lang"], md.serialized_size + 8, groups)
            except Exception:                           # noqa: BLE001 — unreadable: unweighed
                layouts[head] = (name, m["lang"], None, None)
        return layouts[head]

    def ranged(name: str, total: int | None, start: int, end: int) -> tuple[str, int | None]:
        """A multistream stream: `end` 0 runs to the end of the file."""
        if total is None:
            return f"{name}#{start}::{end}", None
        return f"{name}#{start}::{end}", (end or total) - start

    def weigh(origin: str) -> OriginReads:
        own: list[tuple[str, int | None]] = []
        shared: list[tuple[str, int | None]] = []
        group = None
        for piece in str(origin).split(locate.SEG_SEP):
            m = locate._PAGE.match(piece)
            if m is not None:
                wiki, unit = m["wiki"], m["unit"]
                if not run:
                    own.append((piece, None))
                    continue
                if unit == locate.DUMP_UNIT:
                    group = (DUMP, wiki)
                    name, total, index, pack = dump_file(wiki)
                    shared.append(index)
                    got = stream_index.lookup(pack, int(m["page"])) if pack is not None else None
                    own.append(ranged(name, total, *got) if got is not None else (name, None))
                else:
                    shared.append(whole(f"p:{wiki}", lambda w=wiki, u=unit: locate.run_file(base, w, run, u)))
                continue
            m = locate._DATED.match(piece)
            if m is not None:                            # parser 5: the bytes named, no index needed
                head = piece.partition("#")[0]
                name, total = whole(f"f:{head}", lambda h=head: base / h)
                if m["unit"] == locate.DUMP_UNIT:
                    group = (DUMP, m["wiki"])
                    own.append(ranged(name, total, int(m["start"]), int(m["end"])))
                else:
                    shared.append((name, total))
                continue
            head, _, member = piece.rpartition("#")
            lay = layout(head, piece) if "::" in member else None
            if lay is None:                              # anything else: its file, whole
                own.append(whole(f"g:{piece.partition('#')[0]}", lambda x=piece: Path(generic(x))))
                continue
            name, lang, footer, groups = lay
            group = group or (ARCHIVE, f"{lang}wiki")
            if groups is None:
                own.append(whole(f"g:{head}", lambda x=piece: Path(generic(x))))
                continue
            shared.append((f"{name}#footer", footer))
            start, _, end = member.partition("::")
            lo, hi = int(start), int(end)
            for g, (first, rows, read) in enumerate(groups):
                if first + rows > lo and first < hi:
                    own.append((f"{name}#rg{g}", read))
        sizes_ = [s for _, s in own]
        total = None if any(s is None for s in sizes_) else sum(sizes_)
        return OriginReads("+".join(k for k, _ in own), total, tuple(dict.fromkeys(shared)), group)

    return weigh
