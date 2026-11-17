"""A WIKIPEDIA RECORD'S ORIGIN, BY PAGE (parser 6; 09 §9.5 amended
2026-11-17, operator: "Choice B"; docs/notes/collection-updates.md §6).

Up to parser 5 a record's `origin` named the dated dump file and the byte
range of its stream (`itwiki/20260801/itwiki-20260801-pages-articles-
multistream.xml.bz2#4414568085::4414664693|itwiki/20260801/itwiki-20260801-
page_props.sql.gz#265993916::265994007`), so every monthly run moved every
line and re-issued every part to every peer. From parser 6 each piece names
the PAGE in the run-free name of the job's file:

    <wiki>/pages-articles-multistream.xml.bz2#<page id>
    <wiki>/page_props.sql.gz#<page id>

joined by `|` as before (the page's own bytes first, its page_props rows
last, where it has any), and RESOLVED IN THE RUN THE SNAPSHOT NAMES (09 §9.4
`origin_run`, else the Codex's `files` run): the file is Wikimedia's own
`<wiki>/<run>/<wiki>-<run>-<file>` under the origin's base, the article's
stream is found through the run's multistream index (`offset:page_id:title`,
stream_index.py), the page_props rows through the run's table (pp_page,
page_props.py). A line then changes only when its page does.

WHY THIS FORM. Unambiguous per wiki: a page id is unique within one wiki
only, and the first segment is the wiki's dump name, the one the run's
files and `extra.wiki` carry. Unambiguous per unit: the article and its
page_props rows are two units, named by the two job files' own names, and
the member is the key the origin's index and table are keyed on — the page
id, which a page keeps through a move, where the title (the index's third
field) changes. Distinct from parser 5's form by construction: no run
segment, one member field where parser 5 wrote `start::end`. A verifier
needs only the run, which the snapshot states.

A row range of the pre-embedded archive (layer 1, a parquet at a pinned
revision) is already stable and is left exactly as it was.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import page_props, stream_index

DUMP_UNIT = "pages-articles-multistream.xml.bz2"
PROPS_UNIT = "page_props.sql.gz"
SEG_SEP = "|"
_UNITS = "|".join(re.escape(u) for u in (DUMP_UNIT, PROPS_UNIT))
_WIKI = r"[a-z][a-z0-9_]*wiki"
#: parser 5's piece: the dated file and the byte range
_DATED = re.compile(rf"^(?P<wiki>{_WIKI})/(?P<run>\d{{8}}|latest)/(?P=wiki)-(?P=run)-(?P<unit>{_UNITS})"
                    rf"#(?P<start>\d+)::(?P<end>\d+)$")
#: parser 6's piece: the page, in a run-free name
_PAGE = re.compile(rf"^(?P<wiki>{_WIKI})/(?P<unit>{_UNITS})#(?P<page>\d+)$")
#: the archive's row range (09 §9.5: a URL into a credited archive)
_ARCHIVE = re.compile(r"^.+\.parquet#\d+::\d+$")
#: a run's directory in a manifest locator's path
_RUN_DIR = re.compile(rf"(?:^|/)(?P<wiki>{_WIKI})/(?P<run>\d{{8}})/(?P=wiki)-(?P=run)-(?:{_UNITS})$")


class PageNotInRun(LookupError):
    """The run the origin is resolved in does not hold the page."""


def page_piece(wiki: str, unit: str, page_id: int) -> str:
    return f"{wiki}/{unit}#{int(page_id)}"


def is_page_form(origin: str) -> bool:
    """Every dump and page_props piece of `origin` names its page."""
    pieces = str(origin).split(SEG_SEP)
    return any(_PAGE.match(p) for p in pieces) and not any(_DATED.match(p) for p in pieces)


def page_origin(origin: str, page_id: int) -> str:
    """Parser 6's `origin` for a record parser 5 wrote with `origin`: each
    dated piece named by the page in its run-free name, an archive piece
    kept as it is — a pure function of the line's own `origin` and its page
    id. A piece already in parser 6's form is kept, if it names this page."""
    out = []
    for piece in str(origin).split(SEG_SEP):
        m = _DATED.match(piece)
        if m is not None:
            out.append(page_piece(m["wiki"], m["unit"], page_id))
            continue
        m = _PAGE.match(piece)
        if m is not None:
            if int(m["page"]) != int(page_id):
                raise ValueError(f"{piece}: names page {m['page']}, the record is page {page_id}")
            out.append(piece)
            continue
        if _ARCHIVE.match(piece):
            out.append(piece)
            continue
        raise ValueError(f"{piece!r}: not a piece of a wikipedia origin (a dump's stream, a page_props "
                         f"unit or an archive's row range)")
    return SEG_SEP.join(out)


def run_file(base: Path, wiki: str, run: str, unit: str) -> Path:
    """Wikimedia's own path of a job's file in a run, under the origin base."""
    return Path(base) / wiki / run / f"{wiki}-{run}-{unit}"


def _need_run(run: str | None, piece: str) -> str:
    if not run:
        raise ValueError(f"{piece}: an origin naming a page is read in a run (09 §9.4 `origin_run`, "
                         f"else the Codex's), and none was given")
    return str(run)


def resolve(origin: str, base: Path, run: str | None, generic) -> str:
    """The manifest locator (`<file>::<start>::<end>`, pieces joined by `|`)
    `origin` names in `run` under `base` — what `fetch` reads. A page-form
    piece is found through the run's index (the article's stream) or its
    page_props table (the page's rows); every other piece — parser 5's byte
    range, an archive's row range — through `generic(piece)`, the platform's
    reading of 09 §9.5. Raises FileNotFoundError when the run's file is not
    under `base`, PageNotInRun when the run does not hold the page."""
    out = []
    for piece in str(origin).split(SEG_SEP):
        m = _PAGE.match(piece)
        if m is None:
            out.append(generic(piece))
            continue
        run_ = _need_run(run, piece)
        f = run_file(base, m["wiki"], run_, m["unit"])
        page = int(m["page"])
        if m["unit"] == DUMP_UNIT:
            got = stream_index.stream_of(stream_index.index_of(f), page)
            if got is None:
                raise PageNotInRun(f"{piece}: run {run_}'s index does not list page {page}")
            start, end = got
        else:
            unit = page_props.unit_of(f, page)
            if unit is None:
                raise PageNotInRun(f"{piece}: run {run_}'s page_props holds no row of page {page}")
            start, end = unit[0], unit[1]
        out.append(f"{f}::{start}::{end}")
    return SEG_SEP.join(out)


def files(origin: str, base: Path, run: str | None, generic) -> list[Path]:
    """The files `origin`'s pieces are read from in `run` — no index read:
    `records-vs-origin` weighs a unit by its files (app/hold/check.py)."""
    out = []
    for piece in str(origin).split(SEG_SEP):
        m = _PAGE.match(piece)
        out.append(generic(piece) if m is None else run_file(base, m["wiki"], _need_run(run, piece), m["unit"]))
    return out


def run_of_locator(locator: str) -> str | None:
    """The run a manifest locator's dump and page_props pieces are read from
    (`<wiki>/<run>/<wiki>-<run>-…`), or None when it names no dated file
    (an archive row range alone, a `latest/` dump) or names two runs."""
    runs = set()
    for piece in str(locator).split(SEG_SEP):
        path = piece.split("::", 1)[0]
        m = _RUN_DIR.search(path.replace("\\", "/"))
        if m is not None:
            runs.add(m["run"])
    return runs.pop() if len(runs) == 1 else None
