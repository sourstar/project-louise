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

import contextlib
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
    if data.get("shape") != SHAPE:
        return None             # an older format (review of 13743848, 2): paged once, kept anew
    if data.get("n") != n or data.get("last") != last or len(data.get("rows") or []) > n:
        return None
    return data["rows"]


@contextlib.contextmanager
def _locked(path: Path):
    """One writer of a window at a time across processes (`keep`, `vouched`):
    `vouched`'s read-modify-write must not put old rows back under a fresh
    `at` (review of 13743848, 4)."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(f".{path.name}.lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def keep(endpoint: str, year: int, languages: tuple[str, ...], n: int, last: str | None, rows: list, *,
         force: bool = False, at: float | None = None) -> None:
    """The window as listed. `at`: when its COUNT was asked — what the rows
    are known whole as of (review of 7eb82ba1, 3a: the last page's time is
    later than what the count vouched for)."""
    path = _path(endpoint, year, languages, force)
    with _locked(path):
        _keep(path, year, languages, n, last, rows, force=force, endpoint=endpoint, at=at)


def _keep(path: Path, year: int, languages: tuple[str, ...], n: int, last: str | None, rows: list, *,
          force: bool, endpoint: str, at: float | None) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    import time
    tmp.write_bytes(gzip.compress(json.dumps({"n": n, "last": last, "rows": rows, "year": year,
                                              "languages": list(languages), "force": force,
                                              "endpoint": endpoint.rstrip("/"), "shape": SHAPE,
                                              "at": time.time() if at is None else at},
                                             separators=(",", ":")).encode(), 6))
    os.replace(tmp, path)



def vouched(endpoint: str, year: int, languages: tuple[str, ...], n: int, last: str | None, *,
            force: bool = False, at: float | None = None) -> None:
    """The window's fingerprint was asked NOW and still holds (cellar
    `_window_files`): its `at` — when it was last known whole, which
    `coverage` reads — is set to now, the file rewritten whole (review of
    a783fd1c, 5: a file's mtime is not a statement). A window kept before
    `at` was stored is left as it is: it vouches for nothing, and is listed."""
    import time
    path = _path(endpoint, year, languages, force)
    with _locked(path):
        try:
            data = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, ValueError, EOFError):
            return
        if data.get("shape") != SHAPE or data.get("n") != n or data.get("last") != last:
            return              # another format, or another pass kept other rows meanwhile
        data["at"] = time.time() if at is None else at
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 6))
        os.replace(tmp, path)


def coverage(wanted: set[tuple[str, str]], after: dict[str, float],
             endpoint: str) -> tuple[dict[tuple[str, str], int], dict[tuple[int, str], float]]:
    """WHAT THE KEPT LISTING VOUCHES FOR SINCE THE DUMP (architecture's decision,
    2026-11-26, on EUR-Lex5.next's 122,317 units asked first): a window is a
    whole enumeration of a (CELEX year, language set) with each unit's
    modified date, so one known whole (`at`) after the stamped dump's
    `created` (`after`, `{lang: epoch}`) says for each of its units whether it
    changed since. ONLY a window of this endpoint and this SHAPE counts: the
    file must sit at the path its own (year, languages, force) name for
    `endpoint` (review of a783fd1c, blocking: windows of the format before
    11-19, dated by the work alone, still lay beside them and would vouch a
    unit whose manifestation Cellar replaced). Anything else is not counted,
    and its window is listed again. Returns the wanted units' dates from such
    windows, and the (year, language) pairs they cover with the `at` each is
    known whole as of. Read-only.

    THE NEWEST WHOLE WINDOW SPEAKS FOR ITS UNITS (review of 556d3a15, N2): a
    unit's date is read only from the newest window covering its (year,
    language) — a one-language window asked again, or the several-language
    one prepare listed. A unit that window lacks is ABSENT (`_bulk_of`:
    Cellar's tree, never the bulk's on silence), whatever an older window
    said: one Cellar no longer lists in a FORMS manifestation of that
    language (`cellar._window`) has a tree that is not the dump's. A change
    still shows as a later date in the newest window (cellar_listing's
    note), so nothing a merge of every window saw is lost."""
    cover: dict[tuple[int, str], float] = {}
    # per file kept: its (year, fresh languages, at) and only the wanted rows
    # (`wanted` is the units due, not the corpus: memory stays the pass's)
    seen: list[tuple[int, set[str], float, list[tuple[tuple[str, str], int]]]] = []
    for p in sorted(_base().glob("*/*.json.gz")):
        try:
            data = json.loads(gzip.decompress(p.read_bytes()))
            year, langs = int(data["year"]), tuple(data["languages"])
            at, force = float(data["at"]), bool(data.get("force"))
        except (OSError, ValueError, EOFError, KeyError, TypeError):
            continue
        if data.get("shape") != SHAPE or p != _path(endpoint, year, langs, force):
            continue
        fresh = {l for l in langs if at > after.get(l, float("inf"))}
        if not fresh:
            continue
        for l in fresh:
            cover[(year, l)] = max(at, cover.get((year, l), 0.0))
        rows = []
        for row in data.get("rows") or []:
            k = (str(row[0]), str(row[1]))
            if k[1] in fresh and k in wanted and isinstance(row[2], int):
                rows.append((k, row[2]))
        seen.append((year, fresh, at, rows))
    dates: dict[tuple[str, str], int] = {}
    for year, fresh, at, rows in seen:
        for k, when in rows:
            # the newest window of this unit's (year, language) alone; two of
            # the same `at` both count (the later date wins, as before)
            if at == cover.get((year, k[1])) and when > dates.get(k, -1):
                dates[k] = when
    return dates, cover
