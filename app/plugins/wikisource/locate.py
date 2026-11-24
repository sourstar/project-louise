"""A WIKISOURCE RECORD'S ORIGIN, BY ITS PAGE (09 §9.5 amended 2026-11-17;
app/keyed_origin.py; docs/notes/wikisource-design.md §1).

A record is a work — its page, its subpages and the scan pages they
transclude — or an author's page, each with its rows of the run's three
tables. Its `origin` names the ONE page id that identifies it (the work's
page, the author's) in the run-free name of each of the four files its unit
is read from, joined by `|`, in the order `frame` writes them:

    <wiki>/pages-articles-multistream.xml.bz2#<page id>
    <wiki>/page_props.sql.gz#<page id>
    <wiki>/categorylinks.sql.gz#<page id>
    <wiki>/linktarget.sql.gz#<page id>

RESOLVED IN THE RUN THE SNAPSHOT NAMES (09 §9.4 `origin_run`, else the
Codex's): each file is Wikimedia's own `<wiki>/<run>/<wiki>-<run>-<file>`
under the origin's base. Which pages the unit holds is not in the name: it
is what the run's dump says of that page — its subpages by title, the scan
pages its `<pages>` tags name — found through the works index the node
builds of the dump (index.py), exactly as discovery found them. So a line
moves only when its work does, and a verifier needs the run and nothing of
this node's.

All four files are always named: a page with no category and no property
is still a statement of those tables ("none"), and `origin_hash` covers
what they hold of the unit's pages.

Wikipedia's module of the same name (app/plugins/wikipedia/locate.py) is
the model. Its patterns are not reused: they read `<lang>wiki`, and a
Wikisource wiki is `<lang>wikisource`.
"""
from __future__ import annotations

import re
from pathlib import Path

DUMP_UNIT = "pages-articles-multistream.xml.bz2"
#: the run's tables, in `frame`'s order (source.py)
TABLE_UNITS = ("page_props.sql.gz", "categorylinks.sql.gz", "linktarget.sql.gz")
UNITS = (DUMP_UNIT, *TABLE_UNITS)
SEG_SEP = "|"
_WIKI = r"[a-z][a-z0-9_]*wikisource"
_PIECE = re.compile(rf"^(?P<wiki>{_WIKI})/(?P<unit>[a-z_.0-9-]+)#(?P<page>\d+)$")
#: the dump of a run, in a manifest locator's path
_RUN_DUMP = re.compile(rf"(?:^|/)(?P<wiki>{_WIKI})/(?P<run>\d{{8}})/(?P=wiki)-(?P=run)-{re.escape(DUMP_UNIT)}$")


class PageNotInRun(LookupError):
    """The run the origin is resolved in does not hold the page."""


def origin(wiki: str, page_id: int) -> str:
    """The record's `origin`: its page, in each of its unit's four files."""
    return SEG_SEP.join(f"{wiki}/{unit}#{int(page_id)}" for unit in UNITS)


def read(text: str) -> tuple[str, int]:
    """`(wiki, page id)` of an origin `origin` wrote; ValueError for anything
    else — another wiki or page in one piece, a piece missing or out of
    order, a dated form."""
    pieces = str(text).split(SEG_SEP)
    got = [_PIECE.match(p) for p in pieces]
    if len(pieces) != len(UNITS) or any(m is None for m in got):
        raise ValueError(f"{text!r}: not a wikisource origin (its page in {', '.join(UNITS)}, joined by "
                         f"{SEG_SEP!r})")
    wiki, page = got[0]["wiki"], got[0]["page"]
    for m, unit in zip(got, UNITS):
        if (m["wiki"], m["unit"], m["page"]) != (wiki, unit, page):
            raise ValueError(f"{text!r}: its pieces name one page of one wiki, in the order "
                             f"{', '.join(UNITS)}")
    return wiki, int(page)


def run_file(base: Path, wiki: str, run: str, unit: str) -> Path:
    """Wikimedia's own path of a job's file in a run, under the origin base."""
    return Path(base) / wiki / run / f"{wiki}-{run}-{unit}"


def need_run(run: str | None, text: str) -> str:
    if not run:
        raise ValueError(f"{text}: an origin naming a page is read in a run (09 §9.4 `origin_run`, else the "
                         f"Codex's), and none was given")
    return str(run)


def run_of_locator(locator: str) -> str | None:
    """The run a manifest locator (`<dump path>::<page id>`) reads, or None
    when its path is not a dated run's dump."""
    path = str(locator).rpartition("::")[0] or str(locator)
    m = _RUN_DUMP.search(path.replace("\\", "/"))
    return m["run"] if m is not None else None
