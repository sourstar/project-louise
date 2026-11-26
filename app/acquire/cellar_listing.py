"""Cellar's listing WINDOWS kept between passes (2026-11-14: the Italian
listing took 40 min and more, one COUNT and its pages for each CELEX year from
1952; a disconnect at 1954 threw the whole of it away, and every delta round
of `full-resync` lists everything again).

A window is one CELEX year in a set of languages (cellar._window_files). Each
pass still asks every window — the listing is enumerated per pass (06 §6.2) —
but asks its FINGERPRINT in place of its count: the number of (CELEX,
language) rows and the latest `cmr:lastModificationDate` among their works,
one query of the count's own shape. Where both equal the window held from the
pass before, its rows are that pass's, and its pages are not asked again. A
work Cellar changed carries a later date (operator, 2026-11-14: "Assume that
cellar always updates the date when a file is changed"), so it raises the
window's maximum; a row added or removed moves the count. Anything else, and a
window never held, is paged as before and kept when its pages add up to its
count.

WHERE: derived, re-creatable from Cellar (invariant 6): under the cache drive
(`derived.place_kept`), keyed on the node's root, beside it in `.pack/` when no
drive is named. One gzip'd JSON per (endpoint, year, languages).

THE STANDARD, NOT ADOPTED: Cellar's ATOM/RSS notification feeds would give the
changes themselves (cellar.py's note); they are a second origin form to record
when adopted. The fingerprint needs nothing but the SPARQL endpoint already
asked.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
from pathlib import Path

from app import config, derived

KIND = "eurlex-listing"


def _base() -> Path:
    root = Path(config.louise_root())
    return derived.place_kept(KIND, root) or (root / derived.BESIDE / KIND)


#: the rows' shape: each row's date is the unit's latest (work, expression,
#: manifestations — cellar.MODIFIED, 2026-11-19), no longer the work's alone.
#: A window kept in the old shape is never read for the new one: its rows'
#: dates would hide a manifestation replaced under an unchanged window maximum
SHAPE = "unit-modified"


def _path(endpoint: str, year: int, languages: tuple[str, ...], force: bool = False) -> Path:
    # parser 4's rows carry each work's in-force (cellar.page_query `force`):
    # kept apart, never read for a listing of the other shape
    key = hashlib.sha256((f"{endpoint.rstrip('/')}|{year}|{','.join(languages)}|{SHAPE}"
                          + ("|force" if force else "")).encode()).hexdigest()[:24]
    return _base() / f"{year:04d}" / f"{key}.json.gz"


def held(endpoint: str, year: int, languages: tuple[str, ...], n: int, last: str | None, *,
         force: bool = False) -> list | None:
    """The window's rows `[[celex, lang, mtime]…]` as the last pass listed
    them, where its fingerprint is still `(n, last)`; None otherwise."""
    try:
        data = json.loads(gzip.decompress(_path(endpoint, year, languages, force).read_bytes()))
    except (OSError, ValueError, EOFError):
        return None
    if data.get("n") != n or data.get("last") != last or len(data.get("rows") or []) > n:
        return None
    return data["rows"]


def keep(endpoint: str, year: int, languages: tuple[str, ...], n: int, last: str | None, rows: list, *,
         force: bool = False) -> None:
    path = _path(endpoint, year, languages, force)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_bytes(gzip.compress(json.dumps({"n": n, "last": last, "rows": rows, "year": year,
                                              "languages": list(languages)},
                                             separators=(",", ":")).encode(), 6))
    os.replace(tmp, path)



def vouched(endpoint: str, year: int, languages: tuple[str, ...], *, force: bool = False) -> None:
    """The window's fingerprint was asked NOW and still holds (cellar
    `_window_files`): its file's time says when it was last known whole, which
    `coverage` reads (architecture's decision of 2026-11-26)."""
    try:
        os.utime(_path(endpoint, year, languages, force))
    except OSError:
        pass


def coverage(wanted: set[tuple[str, str]], after: dict[str, float]) -> tuple[dict[tuple[str, str], int],
                                                                           set[tuple[int, str]]]:
    """WHAT THE KEPT LISTING VOUCHES FOR SINCE THE DUMP (architecture's decision,
    2026-11-26, on EUR-Lex5.next's 122,317 units asked first): a window is a
    whole enumeration of a (CELEX year, language set) with each unit's
    modified date, so one known whole after the stamped dump's `created`
    (`after`, `{lang: epoch}`; its file's time: kept, or its fingerprint asked
    and holding) says for every unit of it whether it changed since.
    Returns the wanted units' dates from such windows, and the (year,
    language) pairs they cover. A window kept before `languages` was stored
    covers the languages its rows name. Read-only, one pass over the kept files
    (0.1 s on the node's 112)."""
    dates: dict[tuple[str, str], int] = {}
    cover: set[tuple[int, str]] = set()
    for p in sorted(_base().glob("*/*.json.gz")):
        try:
            kept_at = p.stat().st_mtime
            data = json.loads(gzip.decompress(p.read_bytes()))
            year = int(data.get("year") or p.parent.name)
        except (OSError, ValueError, EOFError):
            continue
        rows = data.get("rows") or []
        langs = data.get("languages") or sorted({str(r[1]) for r in rows})
        fresh = {l for l in langs if kept_at > after.get(l, float("inf"))}
        cover |= {(year, l) for l in fresh}
        for row in rows:
            k = (str(row[0]), str(row[1]))
            if k[1] in fresh and k in wanted and isinstance(row[2], int) and row[2] > dates.get(k, -1):
                dates[k] = row[2]
    return dates, cover
